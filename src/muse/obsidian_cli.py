"""Read-only adapter for the official Obsidian CLI.

No shell is used. The adapter targets one named Vault and exposes only search/read
operations so Muse cannot accidentally mutate user-authored notes through this path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


class ObsidianCLIError(RuntimeError):
    """The Obsidian CLI could not complete a read-only request."""


Runner = Callable[..., Any]


def resolve_cli_path(explicit: str | Path | None = None) -> str:
    """Resolve an explicitly configured CLI before standard PATH/install locations."""
    if explicit:
        return str(explicit)
    configured = os.environ.get("MUSE_OBSIDIAN_CLI")
    if configured:
        return configured
    on_path = shutil.which("obsidian")
    if on_path:
        return on_path
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Obsidian" / "Obsidian.com",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Obsidian" / "Obsidian.com",
            Path("D:/Program Files/Obsidian/Obsidian.com"),
        ):
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        "Obsidian CLI was not found; set MUSE_OBSIDIAN_CLI or register obsidian on PATH"
    )


def _validate_vault_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 128 or any(ord(char) < 32 for char in name):
        raise ValueError("vault_name must be a non-empty Vault name")
    return name


def _validate_note_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or (len(raw) >= 2 and raw[1] == ":")
        or any(part in {"", ".", ".."} for part in raw.split("/"))
        or any(ord(char) < 32 for char in raw)
    ):
        raise ValueError("note path must be a safe relative path inside the selected Vault")
    return path.as_posix()


class ObsidianCLI:
    """Search and read a specific Obsidian Vault through the official CLI."""

    def __init__(
        self,
        vault_name: str,
        *,
        executable: str | Path | None = None,
        timeout: float = 20.0,
        runner: Runner = subprocess.run,
    ) -> None:
        if isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.vault_name = _validate_vault_name(vault_name)
        self.executable = resolve_cli_path(executable)
        self.timeout = timeout
        self._runner = runner

    def _run(self, command: str, *options: str) -> str:
        argv = [self.executable, f"vault={self.vault_name}", command, *options]
        try:
            result = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ObsidianCLIError(f"Obsidian CLI timed out during {command}") from exc
        except OSError as exc:
            raise ObsidianCLIError(f"Could not execute Obsidian CLI: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "no CLI error detail").strip()
            raise ObsidianCLIError(f"Obsidian CLI {command} failed: {detail[:1000]}")
        return result.stdout or ""

    def version(self) -> str:
        return self._run("version").strip()

    def vault_path(self) -> Path:
        value = self._run("vault", "info=path").strip()
        if not value:
            raise ObsidianCLIError("Obsidian CLI returned an empty Vault path")
        return Path(value)

    def search(self, query: str, *, folder: str | None = None, limit: int = 50) -> list[str]:
        if not query.strip() or len(query) > 1000:
            raise ValueError("query must contain 1 to 1000 characters")
        if isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        options = [f"query={query}", f"limit={limit}", "format=json"]
        if folder is not None:
            options.append(f"path={_validate_note_path(folder)}")
        output = self._run("search", *options)
        try:
            results = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ObsidianCLIError("Obsidian CLI search returned invalid JSON") from exc
        if not isinstance(results, list) or any(not isinstance(item, str) for item in results):
            raise ObsidianCLIError("Obsidian CLI search returned an unexpected result shape")
        return results

    def read(self, note_path: str) -> str:
        safe_path = _validate_note_path(note_path)
        return self._run("read", f"path={safe_path}")
