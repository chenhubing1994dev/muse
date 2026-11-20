from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any


MAX_REQUEST_BYTES = 256 * 1024
MAX_SCRIPT_CHARS = 128 * 1024
MAX_OUTPUT_CHARS = 32 * 1024
DEFAULT_SESSION = "muse-dev"
DEFAULT_PROJECT_ROOT = r"D:\AI AGENT\Muse"


class PsmuxError(RuntimeError):
    pass


def psmux_executable() -> str:
    configured = os.environ.get("PSMUX_EXE")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("psmux.exe") or shutil.which("psmux")
    if found:
        return found
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidate = local_app_data / "Microsoft" / "WinGet" / "Links" / "psmux.exe"
    if candidate.is_file():
        return str(candidate)
    raise PsmuxError("psmux.exe_not_found")


def powershell_executable() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _job_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "Muse" / "psmux-jobs"


def _active_path(session: str) -> Path:
    return _job_root() / f"{session}.active.json"


def _manifest_path(job_id: str) -> Path:
    return _job_root() / job_id / "job.json"


def _write_job_manifest(job_id: str, manifest: dict[str, Any]) -> None:
    path = _manifest_path(job_id)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read_job_manifest(job_id: str) -> dict[str, Any] | None:
    try:
        value = json.loads(_manifest_path(job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _active_job_id(session: str) -> str | None:
    path = _active_path(session)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        job_id = value.get("job_id") if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    return job_id if isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id) else "unreadable"


def _write_active_job(session: str, job_id: str, window_name: str) -> None:
    path = _active_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"session": session, "job_id": job_id, "window_name": window_name}),
        encoding="utf-8",
    )
    temporary.replace(path)


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o666)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(descriptor)
        return None
    return descriptor


def _release_lock(handle) -> None:
    if handle is None:
        return
    descriptor = handle if isinstance(handle, int) else handle.fileno()
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        if isinstance(handle, int):
            os.close(handle)
        else:
            handle.close()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _script_payload_extension(language: str) -> str:
    return {"powershell": ".ps1", "python": ".py", "bash": ".sh", "batch": ".bat"}[language]


def _windows_to_wsl_path(value: str) -> str:
    path = PureWindowsPath(value)
    if not re.fullmatch(r"[A-Za-z]:", path.drive):
        raise PsmuxError("bash_wsl_requires_a_drive_letter_path")
    return f"/mnt/{path.drive[0].lower()}/" + "/".join(path.parts[1:])


def _git_bash_executable() -> str | None:
    system_bash = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "bash.exe"
    candidates = [
        shutil.which("bash.exe"),
        shutil.which("bash"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe"),
        str(Path(os.environ.get("SCOOP", r"D:\Scoop")) / "apps" / "git" / "current" / "bin" / "bash.exe"),
    ]
    system_path = os.path.normcase(os.path.abspath(system_bash))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.path.normcase(os.path.abspath(candidate)) != system_path:
            return candidate
    return None


def _wsl_executable() -> str | None:
    candidate = shutil.which("wsl.exe") or str(
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    )
    return candidate if Path(candidate).is_file() else None


def _build_job_script(
    user_script: str,
    output_path: Path,
    result_path: Path,
    *,
    language: str = "powershell",
    payload_path: Path | None = None,
    working_directory: str = DEFAULT_PROJECT_ROOT,
) -> str:
    output_literal = _ps_quote(output_path.as_posix())
    result_literal = _ps_quote(result_path.as_posix())
    if language == "powershell":
        if payload_path is not None:
            invocation = (
                f"    & {_ps_quote(powershell_executable())} -NoLogo -NoProfile "
                f"-File {_ps_quote(payload_path.as_posix())} *> {output_literal}\n"
            )
        else:
            invocation = (
                "    & {\n"
                f"{user_script}\n"
                f"    }} *> {output_literal}\n"
            )
        return (
            "$ErrorActionPreference = 'Stop'\n"
            "$global:LASTEXITCODE = 0\n"
            "try {\n"
            f"{invocation}"
            "    $museExitCode = if ($null -eq $global:LASTEXITCODE) { 0 } else { [int]$global:LASTEXITCODE }\n"
            "} catch {\n"
            f"    [IO.File]::WriteAllText({output_literal}, ($_ | Out-String), [Text.Encoding]::Unicode)\n"
            "    $museExitCode = 1\n"
            "}\n"
            f"[IO.File]::WriteAllText({result_literal}, [string]$museExitCode, [Text.Encoding]::UTF8)\n"
            "exit $museExitCode\n"
        )

    if payload_path is None:
        raise ValueError("payload_path_required_for_script_language")
    payload_literal = _ps_quote(payload_path.as_posix())
    if language == "python":
        project_root = _ps_quote(Path(working_directory).as_posix())
        source_root = _ps_quote((Path(working_directory) / "src").as_posix())
        invocation = (
            f"    $env:PYTHONPATH = {project_root} + ';' + {source_root} + ';' + [string]$env:PYTHONPATH\n"
            f"    & {_ps_quote(sys.executable)} {payload_literal} *> {output_literal}\n"
        )
    elif language == "bash":
        executable_path = _git_bash_executable()
        if executable_path:
            invocation = f"    & {_ps_quote(executable_path)} {payload_literal} *> {output_literal}\n"
        else:
            wsl = _wsl_executable()
            if not wsl:
                raise PsmuxError("bash_requires_git_bash_or_wsl")
            wsl_command = (
                f"cd {shlex.quote(_windows_to_wsl_path(working_directory))} && "
                f"exec bash {shlex.quote(_windows_to_wsl_path(str(payload_path)))}"
            )
            invocation = f"    & {_ps_quote(wsl)} --exec bash -lc {_ps_quote(wsl_command)} *> {output_literal}\n"
    elif language == "batch":
        comspec = os.environ.get("ComSpec") or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        )
        invocation = (
            f"    & {_ps_quote(comspec)} /d /c "
            f"('call \"' + {payload_literal} + '\"') *> {output_literal}\n"
        )
    else:
        raise ValueError(f"unsupported_language:{language}")
    return (
        "$ErrorActionPreference = 'Continue'\n"
        "$global:LASTEXITCODE = 0\n"
        f"{invocation}"
        "    $museExitCode = if ($null -eq $global:LASTEXITCODE) { 0 } else { [int]$global:LASTEXITCODE }\n"
        f"[IO.File]::WriteAllText({result_literal}, [string]$museExitCode, [Text.Encoding]::UTF8)\n"
        "exit $museExitCode\n"
    )





def build_psmux_argv(
    executable: str,
    session: str,
    window_name: str,
    working_directory: str,
    script_path: str,
) -> list[str]:
    return [
        executable,
        "new-window",
        "-d",
        "-t", session,
        "-n", window_name,
        "-c", working_directory,
        "--",
        powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-File",
        script_path,
    ]


def _window_exists(executable: str, session: str, window_name: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "list-windows", "-t", session, "-F", "#{window_name}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_no_window_flags(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and window_name in result.stdout.splitlines()


def _read_output(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        return "", False
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe"):
        data = data[2:]
        encoding = "utf-16-le"
    elif data.startswith(b"\xfe\xff"):
        data = data[2:]
        encoding = "utf-16-be"
    else:
        encoding = "utf-8-sig"
    truncated = len(data) > MAX_OUTPUT_CHARS * 4
    if truncated:
        data = data[-MAX_OUTPUT_CHARS * 4:]
    decoded = data.decode(encoding, errors="replace")
    if len(decoded) > MAX_OUTPUT_CHARS:
        decoded = decoded[-MAX_OUTPUT_CHARS:]
        truncated = True
    return decoded, truncated


def _clear_active_for_job(job_id: str) -> None:
    for active_path in _job_root().glob("*.active.json"):
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
            if isinstance(active, dict) and active.get("job_id") == job_id:
                active_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            continue


def _result(job_id: str, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "ok": status == "completed",
        "status": status,
        "job_id": job_id,
        "transport": "psmux-detached-window",
        **fields,
    }


def recover_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return _result(job_id, "failed", error="invalid_job_id")
    directory = _job_root() / job_id
    result_path = directory / "result.txt"
    output_path = directory / "output.log"
    if not result_path.is_file():
        return _result(
            job_id, "outcome_unknown",
            error="completion_not_yet_recorded",
            result_file=str(result_path),
            output_file=str(output_path),
        )
    try:
        exit_code = int(result_path.read_text(encoding="utf-8-sig").strip())
    except (OSError, ValueError):
        return _result(job_id, "outcome_unknown", error="result_file_unreadable")
    output, truncated = _read_output(output_path)
    manifest = _read_job_manifest(job_id) or {"job_id": job_id}
    manifest.update({"status": "completed", "exit_code": exit_code, "finished_at": time.time()})
    try:
        _write_job_manifest(job_id, manifest)
    except OSError:
        pass
    for generated in (directory / "job.ps1", directory / "payload.ps1", directory / "payload.py", directory / "payload.sh", directory / "payload.bat"):
        generated.unlink(missing_ok=True)
    _clear_active_for_job(job_id)
    return _result(
        job_id, "completed",
        command_succeeded=(exit_code == 0),
        exit_code=exit_code,
        output=output,
        output_truncated=truncated,
        result_file=str(result_path),
        output_file=str(output_path),
    )


def inspect_session(request: dict[str, Any]) -> dict[str, Any]:
    session = request.get("session", DEFAULT_SESSION)
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", session):
        return _result("", "failed", error="invalid_session_name")
    try:
        executable = psmux_executable()
        result = subprocess.run(
            [executable, "list-windows", "-t", session, "-F", "#{window_index}|#{window_name}|#{window_id}|#{window_panes}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_no_window_flags(),
            check=False,
        )
        if result.returncode != 0:
            return _result("", "failed", error=result.stderr.strip() or f"psmux_exit_{result.returncode}")
        return _result("", "completed", session=session, windows=result.stdout.splitlines(), active_job_id=_active_job_id(session))
    except (OSError, subprocess.TimeoutExpired, PsmuxError) as exc:
        return _result("", "failed", error=f"{type(exc).__name__}:{exc}")


def list_jobs(request: dict[str, Any]) -> dict[str, Any]:
    """Read-only view of retained psmux job folders; never acquires or repairs locks."""
    session = request.get("session", DEFAULT_SESSION)
    limit = request.get("limit", 20)
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", session):
        return _result("", "failed", error="invalid_session_name")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return _result("", "failed", error="limit_must_be_1_to_100")

    root = _job_root()
    active_job_id = _active_job_id(session)
    try:
        root.stat()
    except FileNotFoundError:
        return _result("", "completed", session=session, active_job_id=active_job_id, concurrency={"policy": "one_in_flight_job_per_session", "active_job_id": active_job_id, "lock_probe": "not_performed_read_only"}, jobs=[], count=0, read_only=True, transport="local-read-only")
    except OSError as exc:
        return _result("", "failed", error=f"job_directory_unreadable:{type(exc).__name__}", transport="local-read-only")

    jobs: list[dict[str, Any]] = []
    try:
        directories = sorted(
            (item for item in root.iterdir() if item.is_dir() and re.fullmatch(r"[0-9a-f]{32}", item.name)),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        return _result("", "failed", error=f"job_directory_unreadable:{type(exc).__name__}")

    for directory in directories[:limit]:
        job_id = directory.name
        result_path = directory / "result.txt"
        output_path = directory / "output.log"
        item: dict[str, Any] = {"job_id": job_id, "active": job_id == active_job_id}
        manifest = _read_job_manifest(job_id)
        if manifest is not None:
            item.update({key: manifest[key] for key in ("session", "language", "working_directory", "created_at", "finished_at") if key in manifest})
        else:
            item["session"] = "unknown"
        try:
            item["updated_at"] = directory.stat().st_mtime
            if result_path.is_file():
                try:
                    item["exit_code"] = int(result_path.read_text(encoding="utf-8-sig").strip())
                    item["status"] = "completed"
                except (OSError, ValueError):
                    item["status"] = "result_unreadable"
            elif item["active"]:
                item["status"] = "outcome_unknown"
            else:
                item["status"] = "incomplete_untracked"
            if output_path.is_file():
                output, truncated = _read_output(output_path)
                item["output_tail"] = output[-4000:]
                item["output_truncated"] = truncated or len(output) > 4000
            item["next_action"] = {
                "completed": "review_output",
                "outcome_unknown": "recover_job_after_confirming_process_state",
                "incomplete_untracked": "inspect_before_retry; process_state_unknown",
                "result_unreadable": "inspect_result_file; do_not_retry",
            }.get(item["status"], "inspect_job_record")
        except OSError as exc:
            item["status"] = "unreadable"
            item["error"] = type(exc).__name__
        jobs.append(item)

    return _result(
        "", "completed", session=session, active_job_id=active_job_id,
        jobs=jobs, count=len(jobs), limit=limit, read_only=True,
        concurrency={"policy": "one_in_flight_job_per_session", "active_job_id": active_job_id, "lock_probe": "not_performed_read_only"},
        transport="local-read-only",
    )


def _resolve_script_request(
    request: dict[str, Any],
    working_directory: str,
    project_root: str | None = None,
) -> tuple[str, str, Path | None]:
    source_path = request.get("script_path")
    if source_path is None:
        user_script = request.get("script")
        if not isinstance(user_script, str) or not user_script.strip():
            raise PsmuxError("script_required")
        if len(user_script) > MAX_SCRIPT_CHARS:
            raise PsmuxError("script_too_large")
        return user_script, request.get("language", "powershell"), None

    if not isinstance(source_path, str) or not source_path.strip():
        raise PsmuxError("script_path_must_be_non_empty_text")
    if request.get("script") not in (None, ""):
        raise PsmuxError("script_and_script_path_are_mutually_exclusive")
    root = Path(project_root or working_directory).resolve(strict=True)
    candidate = Path(source_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PsmuxError("script_path_outside_project_root") from exc
    if not candidate.is_file():
        raise PsmuxError("script_path_not_found")
    if candidate.stat().st_size > MAX_SCRIPT_CHARS:
        raise PsmuxError("script_too_large")
    inferred = {
        ".ps1": "powershell",
        ".py": "python",
        ".sh": "bash",
        ".bat": "batch",
        ".cmd": "batch",
    }.get(candidate.suffix.lower(), "powershell")
    return "", request.get("language", inferred), candidate




def _resolve_working_directory(value: Any, project_root: str = DEFAULT_PROJECT_ROOT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PsmuxError("working_directory_not_found")
    root = Path(project_root).resolve(strict=True)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PsmuxError("working_directory_not_found") from exc
    except OSError as exc:
        raise PsmuxError("working_directory_outside_project_root") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PsmuxError("working_directory_outside_project_root") from exc
    if not resolved.is_dir():
        raise PsmuxError("working_directory_not_found")
    return str(resolved)


def execute_request(
    request: dict[str, Any],
    project_root: str = DEFAULT_PROJECT_ROOT,
) -> dict[str, Any]:
    try:
        working_directory = _resolve_working_directory(
            request.get("expected_cwd", project_root), project_root
        )
        user_script, language, source_payload_path = _resolve_script_request(
            request, working_directory, project_root
        )
    except (OSError, PsmuxError) as exc:
        return _result("", "failed", error=str(exc))
    if not isinstance(language, str):
        return _result("", "failed", error="unsupported_language")
    language = {"ps1": "powershell", "py": "python", "sh": "bash", "bat": "batch", "cmd": "batch"}.get(language.lower(), language.lower())
    if language not in {"powershell", "python", "bash", "batch"}:
        return _result("", "failed", error="unsupported_language")
    session = request.get("session", DEFAULT_SESSION)
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", session):
        return _result("", "failed", error="invalid_session_name")
    timeout_secs = request.get("timeout_secs", 120)
    if isinstance(timeout_secs, bool) or not isinstance(timeout_secs, int) or not 1 <= timeout_secs <= 7200:
        return _result("", "failed", error="timeout_secs_must_be_1_to_7200")


    job_id = uuid.uuid4().hex
    window_name = f"musejob_{job_id[:12]}"
    root = _job_root()
    lock = _acquire_lock(root / f"{session}.lock")
    if lock is None:
        return _result(job_id, "busy", error="another_psmux_command_is_in_flight")
    active_job = _active_job_id(session)
    if active_job:
        previous = recover_job(active_job) if active_job != "unreadable" else {"status": "outcome_unknown"}
        if previous.get("status") != "completed":
            _release_lock(lock)
            return _result(job_id, "busy", error="previous_psmux_job_requires_reconciliation", active_job_id=active_job)

    directory = root / job_id
    script_path = directory / "job.ps1"
    output_path = directory / "output.log"
    result_path = directory / "result.txt"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        payload_path = source_payload_path
        if payload_path is None:
            payload_path = directory / f"payload{_script_payload_extension(language)}"
            payload_encoding = "utf-8-sig" if language == "powershell" else "utf-8"
            payload_path.write_text(user_script, encoding=payload_encoding)
        script_path.write_text(
            _build_job_script(
                user_script,
                output_path,
                result_path,
                language=language,
                payload_path=payload_path,
                working_directory=working_directory,
            ),
            encoding="utf-8-sig",
        )
        executable = psmux_executable()
        argv = build_psmux_argv(executable, session, window_name, working_directory, str(script_path))
        _write_job_manifest(job_id, {
            "job_id": job_id,
            "session": session,
            "language": language,
            "working_directory": working_directory,
            "window_name": window_name,
            "status": "launching",
            "created_at": time.time(),
        })
        _write_active_job(session, job_id, window_name)
        try:
            launched = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                creationflags=_no_window_flags(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            if not _window_exists(executable, session, window_name):
                return _result(
                    job_id, "outcome_unknown",
                    error="psmux_new_window_launch_timed_out",
                    window_name=window_name,
                    result_file=str(result_path),
                    output_file=str(output_path),
                )
        else:
            if launched.returncode != 0 and not _window_exists(executable, session, window_name):
                _clear_active_for_job(job_id)
                return _result(
                    job_id, "failed",
                    error=launched.stderr.strip() or launched.stdout.strip() or f"psmux_exit_{launched.returncode}",
                    window_name=window_name,
                )

        deadline = time.monotonic() + timeout_secs
        while time.monotonic() < deadline:
            if result_path.is_file():
                result = recover_job(job_id)
                return {**result, "window_name": window_name, "language": language}
            time.sleep(0.1)
        return _result(
            job_id, "outcome_unknown",
            error="execution_deadline_without_result_marker",
            window_name=window_name,
            language=language,
            result_file=str(result_path),
            output_file=str(output_path),
        )
    except (OSError, PsmuxError, subprocess.TimeoutExpired, ValueError) as exc:
        _clear_active_for_job(job_id)
        return _result(job_id, "failed", error=f"{type(exc).__name__}:{exc}")
    finally:
        _release_lock(lock)



def main() -> int:
    raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        print(json.dumps({"ok": False, "status": "failed", "error": "request_too_large"}))
        return 2
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": f"invalid_json:{exc.msg}"}))
        return 2
    if not isinstance(request, dict):
        print(json.dumps({"ok": False, "status": "failed", "error": "request_must_be_object"}))
        return 2
    action = request.get("action", "execute")
    if action == "recover":
        result = recover_job(str(request.get("job_id", "")))
    elif action == "inspect":
        result = inspect_session(request)
    elif action == "list":
        result = list_jobs(request)
    elif action == "execute":
        result = execute_request(request)
    else:
        result = _result("", "failed", error="unsupported_action")
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
