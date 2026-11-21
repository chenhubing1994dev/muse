"""Opt-in, read-only smoke test for an already-running Herdr Core on Windows.

The command verifies a user-supplied release ZIP, extracts it only to a temporary
folder, checks its local version/schema, then connects to an existing named pipe.
It never installs, starts, stops, updates, or sends input to Herdr Core.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .herdr_core_release import PINNED_HERDR_CORE, verify_release_archive
from .herdr_transport import HerdrCoreEventSubscription, resolve_windows_pipe_endpoint


MAX_ARCHIVE_ENTRIES = 512
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_COMMAND_OUTPUT = 1024 * 1024


def extract_verified_archive(
    archive_path: str | Path,
    destination: str | Path,
    *,
    expected_sha256: str = PINNED_HERDR_CORE.archive_sha256,
) -> Path:
    """Verify and safely extract the pinned ZIP into a caller-owned temp folder."""
    verify_release_archive(archive_path, expected_sha256=expected_sha256)
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with zipfile.ZipFile(archive_path, "r") as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Herdr Core archive has an invalid number of entries")
        total_size = sum(entry.file_size for entry in entries)
        if total_size > MAX_UNPACKED_BYTES:
            raise ValueError("Herdr Core archive exceeds the extraction size limit")
        for entry in entries:
            if "\\" in entry.filename or "\x00" in entry.filename:
                raise ValueError("Herdr Core archive contains an invalid path")
            relative = PurePosixPath(entry.filename)
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or any(":" in part for part in relative.parts)
            ):
                raise ValueError("Herdr Core archive contains an unsafe path")
            key = "/".join(relative.parts).casefold()
            if key in seen:
                raise ValueError("Herdr Core archive contains duplicate Windows paths")
            seen.add(key)
            mode = (entry.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Herdr Core archive contains a special file")
            target = root.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("Herdr Core archive path escapes the temporary directory")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as source, target.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
    executable = root / "herdr-core.exe"
    conpty = root / "conpty" / "conpty.dll"
    if not executable.is_file() or not conpty.is_file():
        raise ValueError("Herdr Core ZIP is incomplete: expected herdr-core.exe and conpty/conpty.dll")
    return executable


def inspect_release_binary(
    executable: str | Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[str, tuple[str, ...]]:
    """Run only local, non-server commands on the extracted binary."""
    command = str(Path(executable).resolve())

    def run(args: tuple[str, ...]) -> str:
        completed = runner(
            [command, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout) + len(stderr) > MAX_COMMAND_OUTPUT:
            raise ValueError("Herdr Core local command output exceeds the size limit")
        if completed.returncode != 0:
            raise RuntimeError(f"Herdr Core local command failed: {args[0]}")
        return stdout.strip()

    version = run(("--version",))
    expected_version = f"hc {PINNED_HERDR_CORE.version}"
    if version != expected_version:
        raise ValueError(f"release binary version mismatch: expected {expected_version!r}, got {version!r}")
    try:
        schema = json.loads(run(("schema", "--json")))
    except json.JSONDecodeError as exc:
        raise ValueError("release binary returned invalid local OpenRPC JSON") from exc
    if not isinstance(schema, dict) or not isinstance(schema.get("info"), dict):
        raise ValueError("release binary returned an invalid OpenRPC schema")
    if schema["info"].get("version") != PINNED_HERDR_CORE.version:
        raise ValueError("release binary OpenRPC version does not match the pinned release")
    if schema.get("openrpc") != PINNED_HERDR_CORE.openrpc_version:
        raise ValueError("release binary OpenRPC schema version does not match the pinned schema")
    methods = schema.get("methods")
    if not isinstance(methods, list):
        raise ValueError("release binary OpenRPC schema has no methods list")
    names = tuple(
        item["name"] for item in methods
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    if "events.subscribe" not in names or "rpc.discover" not in names:
        raise ValueError("release binary OpenRPC schema lacks required read-only event methods")
    return version, names


def run_smoke(
    archive_path: str | Path,
    *,
    endpoint: str | None = None,
    heartbeat_timeout: float = 20.0,
) -> dict[str, Any]:
    if os.name != "nt":
        raise OSError("the pinned Herdr Core pipe smoke test is Windows-only")
    if (
        isinstance(heartbeat_timeout, bool)
        or not isinstance(heartbeat_timeout, (int, float))
        or not 1 <= heartbeat_timeout <= 60
    ):
        raise ValueError("heartbeat_timeout must be between 1 and 60 seconds")

    archive_sha256 = verify_release_archive(archive_path)
    with tempfile.TemporaryDirectory(prefix="muse-herdr-core-smoke-") as directory:
        executable = extract_verified_archive(archive_path, directory)
        local_version, local_methods = inspect_release_binary(executable)
        pipe_endpoint = endpoint or resolve_windows_pipe_endpoint()
        subscription = HerdrCoreEventSubscription.connect(
            pipe_endpoint,
            expected_version=PINNED_HERDR_CORE.version,
            expected_openrpc_version=PINNED_HERDR_CORE.openrpc_version,
            timeout_secs=10.0,
            poll_interval=0.1,
        )
        try:
            deadline = time.monotonic() + float(heartbeat_timeout)
            observed = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Herdr Core did not send a heartbeat within the smoke-test window")
                result = subscription.next_event(timeout_secs=remaining, poll_interval=0.1)
                observed += 1
                if result.needs_reconcile:
                    raise RuntimeError(f"Herdr Core sent an event requiring reconciliation: {result.reason}")
                if result.event_type == "heartbeat":
                    break
            return {
                "ok": True,
                "release_version": PINNED_HERDR_CORE.version,
                "source_commit": PINNED_HERDR_CORE.source_commit,
                "protocol_generation": PINNED_HERDR_CORE.protocol_generation,
                "archive_sha256": archive_sha256,
                "local_binary_version": local_version,
                "local_schema_methods": len(local_methods),
                "connected_core_version": subscription.core_version,
                "initial_terminal_count": len(subscription.reducer.statuses),
                "events_before_heartbeat": observed,
                "heartbeat": True,
                "read_only": True,
                "installed": False,
                "core_started": False,
            }
        finally:
            subscription.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an existing Herdr Core v0.1.21 pipe without installing or starting it."
    )
    parser.add_argument("--archive", required=True, help="Previously downloaded v0.1.21 Windows x64 release ZIP")
    parser.add_argument("--endpoint", help="Optional local Windows named-pipe endpoint override")
    parser.add_argument("--heartbeat-timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        result = run_smoke(args.archive, endpoint=args.endpoint, heartbeat_timeout=args.heartbeat_timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:240],
            "read_only": True,
            "installed": False,
            "core_started": False,
        }, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
