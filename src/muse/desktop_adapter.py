"""Permission-aware bridge to the pinned agent-desktop native CLI.

Muse owns task policy and approvals; agent-desktop supplies native UIA access.
Only named commands are exposed. Arbitrary shell execution is impossible here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Any


_READ_ONLY = frozenset({"status", "permissions", "version", "list-apps", "list-windows", "list-surfaces", "snapshot", "find", "get", "is", "screenshot"})
_APPROVAL_REQUIRED = frozenset({"click", "double-click", "right-click", "set-value", "clear", "select", "toggle", "check", "uncheck", "expand", "collapse", "scroll", "scroll-to", "focus-window", "resize-window", "move-window", "minimize", "maximize", "restore", "launch", "clipboard-set", "clipboard-clear"})
_BLOCKED = frozenset({"close-app", "key-down", "key-up", "mouse-down", "mouse-up", "dismiss-all-notifications", "notification-action", "press", "type", "drag", "mouse-click", "mouse-move", "mouse-wheel", "open-system-surface"})
_COMMAND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_OUTPUT_BYTES = 256 * 1024


class DesktopError(RuntimeError):
    """Native desktop command failed or returned an invalid envelope."""


@dataclass(frozen=True)
class DesktopResult:
    command: str
    data: Any
    raw_output: str


class AgentDesktopAdapter:
    """Call the native executable with argv, never through a shell."""

    @classmethod
    def from_installed(cls, *, executable: str | Path | None = None, timeout_seconds: float = 20.0) -> "AgentDesktopAdapter":
        candidate = executable or os.environ.get("MUSE_AGENT_DESKTOP_EXE")
        if candidate is None:
            candidate = Path(r"D:\Tools\AgentDesktop\node_modules\agent-desktop\bin\agent-desktop-win32-x64.exe")
        path = Path(candidate)
        if not path.is_file():
            raise FileNotFoundError("agent-desktop native executable not found: " + str(path))
        return cls(path, timeout_seconds=timeout_seconds)

    def __init__(self, executable: str | Path, *, timeout_seconds: float = 20.0) -> None:
        path = Path(executable)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        self.executable = path.resolve()
        self.timeout_seconds = float(timeout_seconds)

    def call(self, command: str, args: Sequence[str] = (), *, approved: bool = False) -> DesktopResult:
        if not isinstance(command, str) or not _COMMAND.fullmatch(command):
            raise ValueError("invalid desktop command")
        if command in _BLOCKED or command not in _READ_ONLY | _APPROVAL_REQUIRED:
            raise PermissionError("desktop command is blocked or not allowlisted: " + command)
        if command in _APPROVAL_REQUIRED and approved is not True:
            raise PermissionError("desktop action requires explicit approval: " + command)
        if not isinstance(args, (list, tuple)) or len(args) > 32 or any(not isinstance(x, str) or len(x) > 2048 or "\x00" in x for x in args):
            raise ValueError("desktop arguments must be at most 32 bounded strings")
        if any(x in {"--force", "--headed", "--debug", "--trace-strict"} for x in args):
            raise PermissionError("privileged desktop flags are not exposed by the Muse adapter")
        try:
            completed = subprocess.run([str(self.executable), command, *args], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout_seconds, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DesktopError("agent-desktop process failed: " + type(exc).__name__) from exc
        output = completed.stdout or ""
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise DesktopError("agent-desktop output exceeds 256 KiB")
        try:
            envelope = json.loads(output)
        except json.JSONDecodeError as exc:
            raise DesktopError("agent-desktop returned invalid JSON") from exc
        if not isinstance(envelope, dict) or envelope.get("ok") is not True or "data" not in envelope:
            error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
            code = error.get("code", "UNKNOWN") if isinstance(error, dict) else "UNKNOWN"
            raise DesktopError("agent-desktop command failed: " + str(code))
        if completed.returncode != 0:
            raise DesktopError("agent-desktop exited with code " + str(completed.returncode))
        return DesktopResult(command, envelope["data"], output)

    def status(self) -> DesktopResult:
        return self.call("status")

    def snapshot(self, app: str | None = None, *, interactive_only: bool = True) -> DesktopResult:
        args = []
        if app:
            if not isinstance(app, str) or len(app) > 160 or not app.strip():
                raise ValueError("app must be non-empty text of at most 160 characters")
            args.extend(["--app", app])
        if interactive_only:
            args.append("--interactive-only")
        return self.call("snapshot", args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Permission-aware agent-desktop bridge for Muse")
    parser.add_argument("action", choices=("status", "snapshot", "list-apps", "call"))
    parser.add_argument("command", nargs="?")
    parser.add_argument("--app")
    parser.add_argument("--args-json", default="[]")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args(argv)
    try:
        adapter = AgentDesktopAdapter.from_installed()
        if args.action == "status":
            result = adapter.status().data
        elif args.action == "snapshot":
            result = adapter.snapshot(args.app).data
        elif args.action == "list-apps":
            result = adapter.call("list-apps").data
        else:
            values = json.loads(args.args_json)
            if not isinstance(values, list):
                raise ValueError("--args-json must be a JSON array")
            result = adapter.call(args.command or "", values, approved=args.approve).data
    except (DesktopError, OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        parser.exit(2, "muse-desktop: " + type(exc).__name__ + ": " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
