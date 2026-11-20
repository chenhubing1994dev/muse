"""Provider-neutral, read-only terminal runtime discovery.

This layer does not execute tasks or schedule Agents. Muse's Commander and
TaskQueue remain the sole authorities for assignment and execution.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence, runtime_checkable


MAX_OUTPUT_CHARS = 1024 * 1024


@dataclass(frozen=True)
class TerminalRuntimeProbe:
    provider_id: str
    available: bool
    executable: str | None = None
    version: str | None = None
    schema_valid: bool | None = None
    schema_methods: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class TerminalSessionListing:
    provider_id: str
    available: bool
    payload: dict[str, Any] | list[Any] | None = None
    error_code: str | None = None


@runtime_checkable
class TerminalRuntimeProvider(Protocol):
    """Small discovery contract shared by terminal runtime adapters."""

    @property
    def provider_id(self) -> str: ...

    def probe(self) -> TerminalRuntimeProbe: ...

    def list_sessions(self) -> TerminalSessionListing: ...


class PsmuxProvider:
    """Read-only view over Muse's existing psmux Job/session adapter.

    Callers inject the established observers so this module does not create a
    second psmux executor or import the script-layer adapter into the package.
    """

    provider_id = "psmux"

    def __init__(
        self,
        *,
        inspect_session: Callable[[dict[str, Any]], dict[str, Any]],
        list_jobs: Callable[[dict[str, Any]], dict[str, Any]],
        session: str = "muse-dev",
        job_limit: int = 20,
    ) -> None:
        if not callable(inspect_session) or not callable(list_jobs):
            raise ValueError("psmux observers must be callable")
        if not isinstance(session, str) or not session or len(session) > 64:
            raise ValueError("invalid psmux session")
        if isinstance(job_limit, bool) or not isinstance(job_limit, int) or not 1 <= job_limit <= 100:
            raise ValueError("job_limit must be 1..100")
        self._inspect_session = inspect_session
        self._list_jobs = list_jobs
        self.session = session
        self.job_limit = job_limit

    def probe(self) -> TerminalRuntimeProbe:
        result = self._inspect_session({"session": self.session})
        available = isinstance(result, dict) and result.get("status") == "completed"
        return TerminalRuntimeProbe(
            provider_id=self.provider_id,
            available=available,
            error_code=None if available else _result_error(result, "psmux_inspection_failed"),
        )

    def list_sessions(self) -> TerminalSessionListing:
        inspected = self._inspect_session({"session": self.session})
        jobs = self._list_jobs({"session": self.session, "limit": self.job_limit})
        available = (
            isinstance(inspected, dict) and inspected.get("status") == "completed"
            and isinstance(jobs, dict) and jobs.get("status") == "completed"
        )
        payload = {"psmux": _safe_psmux_inspection(inspected), "jobs": _safe_psmux_jobs(jobs)}
        return TerminalSessionListing(
            provider_id=self.provider_id,
            available=available,
            payload=payload,
            error_code=None if available else _result_error(jobs if not isinstance(jobs, dict) or jobs.get("status") != "completed" else inspected, "psmux_listing_failed"),
        )


class HerdrCoreProvider:
    """Optional `hc` read-only discovery adapter; never launches or steers agents."""

    provider_id = "herdr-core"

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        executable_lookup: Callable[[str], str | None] = shutil.which,
        timeout_secs: float = 8.0,
        max_output_chars: int = MAX_OUTPUT_CHARS,
    ) -> None:
        if command is not None:
            if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
                raise ValueError("command must be a non-empty argv sequence, not a shell string")
            if not command or any(not isinstance(part, str) or not part or "\x00" in part for part in command):
                raise ValueError("command must contain non-empty argv strings")
            self._command = tuple(command)
        else:
            self._command = None
        if not callable(executable_lookup):
            raise ValueError("executable_lookup must be callable")
        if isinstance(timeout_secs, bool) or not isinstance(timeout_secs, (int, float)) or not 0.1 <= timeout_secs <= 30:
            raise ValueError("timeout_secs must be between 0.1 and 30")
        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int) or not 1024 <= max_output_chars <= MAX_OUTPUT_CHARS:
            raise ValueError("max_output_chars must be between 1024 and 1048576")
        self._executable_lookup = executable_lookup
        self.timeout_secs = float(timeout_secs)
        self.max_output_chars = max_output_chars

    def _resolve_command(self) -> tuple[str, ...] | None:
        if self._command is not None:
            return self._command
        executable = self._executable_lookup("hc.exe") or self._executable_lookup("hc")
        return (executable,) if executable else None

    def _run_json_command(self, command: tuple[str, ...], args: tuple[str, ...]) -> tuple[bool, Any, str | None]:
        try:
            completed = subprocess.run(
                [*command, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_secs,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return False, None, "hc_timeout"
        except OSError as exc:
            return False, None, f"hc_launch_failed:{type(exc).__name__}"
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout) + len(stderr) > self.max_output_chars:
            return False, None, "hc_output_too_large"
        if completed.returncode != 0:
            return False, None, f"hc_exit_{completed.returncode}"
        try:
            return True, json.loads(stdout), None
        except (json.JSONDecodeError, TypeError):
            return False, None, "hc_invalid_json"

    def _run_text_command(self, command: tuple[str, ...], args: tuple[str, ...]) -> tuple[str | None, str | None]:
        try:
            completed = subprocess.run(
                [*command, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_secs,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return None, "hc_timeout"
        except OSError as exc:
            return None, f"hc_launch_failed:{type(exc).__name__}"
        output = (completed.stdout or "").strip()
        if len(output) + len(completed.stderr or "") > self.max_output_chars:
            return None, "hc_output_too_large"
        if completed.returncode != 0:
            return None, f"hc_exit_{completed.returncode}"
        return (output.splitlines()[0][:160] if output else None), None

    def probe(self) -> TerminalRuntimeProbe:
        command = self._resolve_command()
        if command is None:
            return TerminalRuntimeProbe(self.provider_id, False, error_code="hc_not_found")
        version, version_error = self._run_text_command(command, ("--version",))
        if version_error:
            return TerminalRuntimeProbe(self.provider_id, False, executable=command[0], error_code=version_error)
        schema_ok, schema, schema_error = self._run_json_command(command, ("schema", "--json"))
        if not schema_ok or not isinstance(schema, dict) or not isinstance(schema.get("methods"), list):
            normalized_error = "hc_schema_invalid" if schema_error == "hc_invalid_json" else schema_error
            return TerminalRuntimeProbe(
                self.provider_id, False, executable=command[0], version=version,
                schema_valid=False, error_code=normalized_error or "hc_schema_invalid",
            )
        methods = tuple(
            item["name"] for item in schema["methods"]
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
        )
        return TerminalRuntimeProbe(
            self.provider_id, True, executable=command[0], version=version,
            schema_valid=True, schema_methods=methods,
        )

    def list_sessions(self) -> TerminalSessionListing:
        command = self._resolve_command()
        if command is None:
            return TerminalSessionListing(self.provider_id, False, error_code="hc_not_found")
        ok, payload, error = self._run_json_command(command, ("ls", "--json"))
        if not ok or not isinstance(payload, (dict, list)):
            return TerminalSessionListing(self.provider_id, False, error_code=error or "hc_list_invalid_shape")
        return TerminalSessionListing(self.provider_id, True, payload=payload)


def _safe_psmux_inspection(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key] for key in ("session", "active_job_id", "windows")
        if key in result and isinstance(result[key], (str, type(None), list))
    }


def _safe_psmux_jobs(result: dict[str, Any]) -> dict[str, Any]:
    allowed = ("job_id", "active", "session", "language", "status", "exit_code", "created_at", "finished_at", "updated_at", "next_action")
    rows = result.get("jobs")
    safe_rows = [
        {key: row[key] for key in allowed if key in row}
        for row in rows if isinstance(row, dict)
    ] if isinstance(rows, list) else []
    return {"count": result.get("count", len(safe_rows)), "jobs": safe_rows, "read_only": True}


def _result_error(result: Any, fallback: str) -> str:
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, str) and error:
            return error[:160]
    return fallback
