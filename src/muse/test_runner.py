"""Run unittest suites and record privacy-minimal lifecycle events."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import signal
import sys
import time
import unittest
import uuid
from pathlib import Path
from typing import TextIO

from muse.progress import ProgressEventStore
from muse.experiment_bridge import ExperimentBridge
from muse.research_knowledge import ResearchKnowledgeStore
from muse.process_limits import ProcessLimitError, WindowsJobLimits

def _record(progress_events: ProgressEventStore | None, subject_id: str, event: str,
            *, summary: str, error_code: str | None = None) -> bool:
    if progress_events is None:
        return False
    try:
        progress_events.record(
            "test", subject_id, event, summary=summary, error_code=error_code
        )
        return True
    except Exception:
        # Telemetry must not prevent a requested test run.
        return False


def run_suite(suite: unittest.TestSuite, *,
              progress_events: ProgressEventStore | None = None,
              stream: TextIO | None = None, verbosity: int = 2,
              experiment_bridge: ExperimentBridge | None = None,
              manifest_id: str | None = None,
              budget_seconds: int = 900) -> unittest.result.TestResult:
    """Run one suite and record only counts/status, never test names or failure text."""
    if not isinstance(suite, unittest.TestSuite):
        raise TypeError("suite must be a unittest.TestSuite")
    if isinstance(verbosity, bool) or not isinstance(verbosity, int) or not 0 <= verbosity <= 2:
        raise ValueError("verbosity must be between 0 and 2")
    if isinstance(budget_seconds, bool) or not isinstance(budget_seconds, int) or not 1 <= budget_seconds <= 3600:
        raise ValueError("budget_seconds must be between 1 and 3600")
    if experiment_bridge is not None and (not isinstance(experiment_bridge, ExperimentBridge)
                                          or not isinstance(manifest_id, str) or not manifest_id.strip()):
        raise ValueError("experiment_bridge requires a registered manifest_id")
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    _record(progress_events, run_id, "started", summary="Unittest suite started.")
    try:
        result = unittest.TextTestRunner(stream=stream, verbosity=verbosity).run(suite)
    except Exception:
        _record(
            progress_events, run_id, "failed", summary="Unittest runner raised an exception.",
            error_code="tests.runner_error",
        )
        if experiment_bridge is not None:
            try:
                experiment_bridge.record_result(
                    manifest_id, status="failed", duration_seconds=time.monotonic() - started,
                    metrics={}, summary="Unittest runner raised an exception.", exit_code=1, run_id=run_id,
                )
            except Exception:
                pass
        raise
    duration = time.monotonic() - started
    failures = len(result.failures) + len(result.errors)
    if duration > budget_seconds:
        _record(
            progress_events, run_id, "failed",
            summary=f"Unittest runtime budget exceeded: {duration:.1f}s > {budget_seconds}s.",
            error_code="tests.budget_exceeded",
        )
    elif result.testsRun == 0:
        _record(
            progress_events, run_id, "failed", summary="Unittest discovery ran zero tests.",
            error_code="tests.zero_tests",
        )
    elif failures:
        _record(
            progress_events, run_id, "failed",
            summary=f"Unittest suite failed: {failures} failures across {result.testsRun} tests.",
            error_code="tests.failed",
        )
    else:
        _record(
            progress_events, run_id, "completed",
            summary=f"Unittest suite passed: {result.testsRun} tests.",
        )
    experiment_status = "budget_exceeded" if duration > budget_seconds else (
        "succeeded" if result.wasSuccessful() and result.testsRun > 0 else "failed"
    )
    setattr(result, "experiment_status", experiment_status)
    if experiment_bridge is not None:
        try:
            experiment_bridge.record_result(
                manifest_id, status=experiment_status, duration_seconds=duration,
                metrics={"tests_run": result.testsRun, "failures": len(result.failures),
                         "errors": len(result.errors), "skipped": len(result.skipped)},
                summary="Unittest suite result recorded; raw output was not persisted.",
                exit_code=0 if result.wasSuccessful() and result.testsRun > 0 else 1,
                run_id=run_id,
            )
        except Exception:
            setattr(result, "experiment_record_error", True)
    return result


def _run_isolated_suite(
    start_directory: Path, pattern: str, *, progress_events: ProgressEventStore | None,
    experiment_bridge: ExperimentBridge | None, manifest_id: str | None,
    budget_seconds: int, verbosity: int, memory_budget_mb: int = 4096,
    working_directory: Path | None = None, import_root: Path | None = None,
    run_id: str | None = None, stop_event: object | None = None,
) -> dict[str, object]:
    """Run a bounded child process; terminate only this runner-owned process tree on stop."""
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 160 or "\x00" in run_id):
        raise ValueError("run_id must be non-empty bounded text")
    if stop_event is not None and not callable(getattr(stop_event, "is_set", None)):
        raise TypeError("stop_event must expose a callable is_set() method")
    run_id = run_id or uuid.uuid4().hex
    started = time.monotonic()
    _record(progress_events, run_id, "started", summary="Isolated unittest process started.")
    source_root = str(import_root.resolve(strict=True)) if import_root is not None else str(Path(__file__).resolve().parent.parent)
    run_cwd = Path(working_directory).resolve(strict=True) if working_directory is not None else Path(source_root)
    if not run_cwd.is_dir():
        raise ValueError("test working_directory must be an existing directory")
    child_script = chr(10).join((
        "import json,sys,unittest",
        "from pathlib import Path",
        "start=Path(sys.argv[1]); pattern=sys.argv[2]; verbosity=int(sys.argv[3]); root=sys.argv[4]",
        "sys.path.insert(0,root)",
        "try:",
        "    suite=unittest.defaultTestLoader.discover(str(start),pattern=pattern,top_level_dir=str(start))",
        "    result=unittest.TextTestRunner(stream=sys.stdout,verbosity=verbosity).run(suite)",
        "    payload={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'success':bool(result.wasSuccessful() and result.testsRun>0)}",
        "    print('MUSE_EXPERIMENT_RESULT='+json.dumps(payload,separators=(',',':')),flush=True)",
        "    sys.exit(0 if payload['success'] else 1)",
        "except Exception as exc:",
        "    print('MUSE_EXPERIMENT_RESULT='+json.dumps({'tests_run':0,'failures':0,'errors':1,'skipped':0,'success':False,'runner_error':type(exc).__name__}),flush=True)",
        "    raise"
    ))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    process = None
    job_limits = None
    stdout = stderr = ""
    status = "failed"
    exit_code = 1

    def stop_requested() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def terminate_owned_process(code: int) -> None:
        nonlocal stdout, stderr
        if process is None:
            return
        if os.name == "nt":
            if job_limits is not None:
                try:
                    job_limits.terminate(code)
                except ProcessLimitError:
                    pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                if process.poll() is None:
                    process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
            stdout, stderr = process.communicate()

    try:
        if stop_requested():
            status, exit_code = "interrupted", 130
        else:
            process = subprocess.Popen(
                [sys.executable, "-c", child_script, str(start_directory), pattern, str(verbosity), source_root],
                cwd=run_cwd, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                start_new_session=(os.name != "nt"),
            )
            if os.name == "nt":
                # Windows experiments fail closed if OS-level limits cannot be attached.
                job_limits = WindowsJobLimits(process, memory_limit_mb=memory_budget_mb)
            deadline = started + budget_seconds
            while True:
                if stop_requested():
                    terminate_owned_process(130)
                    status, exit_code = "interrupted", 130
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, budget_seconds)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    # A descendant may still hold stdout/stderr open after the root exits.
                    # Keep enforcing the same deadline so the process group/Job Object is reaped.
                    continue
                exit_code = process.returncode if process.returncode >= 0 else 1
                status = "succeeded" if exit_code == 0 else "failed"
                break
    except subprocess.TimeoutExpired:
        terminate_owned_process(124)
        status = "budget_exceeded"
        exit_code = 124
    except ProcessLimitError:
        terminate_owned_process(125)
        status = "failed"
        exit_code = 125
    finally:
        if job_limits is not None:
            job_limits.close()
    payload = None
    visible_lines = []
    for line in (stdout or "").splitlines():
        if line.startswith("MUSE_EXPERIMENT_RESULT="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except (TypeError, ValueError):
                payload = None
        else:
            visible_lines.append(line)
    visible_output = chr(10).join(visible_lines)
    if visible_output:
        sys.stdout.write(visible_output[-200000:] + chr(10))
    if stderr:
        sys.stderr.write(stderr[-200000:])
    duration = time.monotonic() - started
    if payload is None:
        payload = {"tests_run": 0, "failures": 0,
                   "errors": 0 if status == "interrupted" else 1,
                   "skipped": 0, "success": False}
    metrics = {key: int(payload.get(key, 0)) for key in ("tests_run", "failures", "errors", "skipped")}
    if status == "interrupted":
        _record(progress_events, run_id, "interrupted", summary="Isolated unittest process interrupted by caller.")
        return {"status": status, "exit_code": exit_code, "duration_seconds": round(duration, 3),
                "metrics": metrics, "run_ref": None, "experiment_record_error": False}
    if status == "succeeded" and not payload.get("success"):
        status = "failed"
        exit_code = 1
    if status == "succeeded":
        _record(progress_events, run_id, "completed", summary=f"Isolated unittest suite passed: {metrics['tests_run']} tests.")
    else:
        _record(progress_events, run_id, "failed", summary=f"Isolated unittest suite ended with status={status}.",
                error_code="tests.budget_exceeded" if status == "budget_exceeded" else "tests.failed")
    experiment_error = False
    run_ref = None
    if experiment_bridge is not None and manifest_id is not None:
        try:
            recorded = experiment_bridge.record_result(
                manifest_id, status=status, duration_seconds=duration, metrics=metrics,
                summary="Isolated unittest result recorded; raw output was not persisted.",
                exit_code=exit_code, run_id=run_id,
            )
            run_ref = recorded.get("run_ref") if isinstance(recorded, dict) else None
        except Exception:
            experiment_error = True
    return {"status": status, "exit_code": exit_code, "duration_seconds": round(duration, 3),
            "metrics": metrics, "run_ref": run_ref, "experiment_record_error": experiment_error}
def _fingerprint_suite_files(root: Path, pattern: str, *, limit: int = 100) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for path in sorted(root.rglob(pattern)):
        if len(fingerprints) >= limit:
            break
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024:
                continue
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            fingerprints[relative] = digest
        except OSError:
            continue
    return fingerprints


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Muse unittest suites with structured progress events")
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--progress-db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3")
    parser.add_argument("--budget-seconds", type=int, default=900, help="hard child-process timeout")
    parser.add_argument("--memory-budget-mb", type=int, default=4096, help="Windows Job Object per-process and aggregate memory limit")
    parser.add_argument("--verbosity", type=int, choices=(0, 1, 2), default=2)
    args = parser.parse_args(argv)
    source_root = str(Path(__file__).resolve().parent.parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        start_directory = Path(args.start_directory).resolve()
        if not 1 <= args.budget_seconds <= 3600:
            raise ValueError("budget-seconds must be between 1 and 3600")
        if not 128 <= args.memory_budget_mb <= 65536:
            raise ValueError("memory-budget-mb must be between 128 and 65536")
        knowledge = None
        bridge = None
        manifest_id = None
        try:
            knowledge = ResearchKnowledgeStore(Path(args.knowledge_db))
            bridge = ExperimentBridge(knowledge)
            manifest = bridge.register_manifest(
                title=f"Unittest suite: {args.pattern}", kind="test",
                argv=("python", "-m", "unittest", "discover", "-s", ".", "-p", args.pattern),
                cwd=".", timeout_seconds=args.budget_seconds,
                memory_budget_mb=args.memory_budget_mb,
                environment_keys=("PYTHONPATH", "PYTHONHASHSEED"),
                environment={"python_version": platform.python_version(),
                             "platform": platform.system(), "machine": platform.machine()},
                code_fingerprints=_fingerprint_suite_files(start_directory, args.pattern),
            )
            manifest_id = manifest["manifest_id"]
        except Exception as exc:
            if knowledge is not None:
                knowledge.close()
            knowledge = None
            bridge = None
            sys.stderr.write("Experiment metadata unavailable; tests will still run (" + type(exc).__name__ + ")." + chr(10))
        try:
            with ProgressEventStore(Path(args.progress_db)) as progress:
                run_result = _run_isolated_suite(
                    start_directory, args.pattern, progress_events=progress,
                    experiment_bridge=bridge, manifest_id=manifest_id,
                    budget_seconds=args.budget_seconds, verbosity=args.verbosity,
                    memory_budget_mb=args.memory_budget_mb,
                )
        finally:
            if knowledge is not None:
                knowledge.close()
    except (ImportError, OSError, ValueError) as exc:
        parser.exit(2, "Muse test runner failed: " + type(exc).__name__ + chr(10))
    return int(run_result["exit_code"])

if __name__ == "__main__":
    raise SystemExit(main())