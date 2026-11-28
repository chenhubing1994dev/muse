#!/usr/bin/env python3
"""Unit-test stage for diag_auto (P0-1) plus the sync fence / source manifest helpers (P0-2).

* pytest is not installed on this machine and installing packages is forbidden, so the stage runs the
  project's existing unittest suite with a fixed argv: ``[sys.executable, "-m", "unittest", "-v", <modules>]``
  (no shell, module names come only from ``tests/test_*.py`` file names validated by regex).
* Batches run in parallel worker processes, cwd=``tests`` (the layout the suite was written for),
  per-batch timeout 600 s, stage budget 1200 s (the watcher hard-kills diag_auto at 1500 s). Any
  timeout counts as a failure, never as a pass.
* Real-data guard: logical fingerprints of the real stores are taken before and after. A change of the
  memory store (User Track) is restored from the pre-run backup immediately; changes elsewhere are
  reported (a live Muse runtime may legitimately write those files) and fail the stage only for files
  that tests must never create (commander / workflow / prototype trees).
Results: ``reports/pytest_latest.json`` (name kept for the requested contract; ``runner`` says unittest).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TESTS = ROOT / "tests"
DATA = ROOT / "data"
BATCH_TIMEOUT = 600
STAGE_BUDGET = 1200
WORKERS = 3
_MODULE = re.compile(r"^test_[A-Za-z0-9_]+$")
_RESULT = re.compile(r"^(?P<name>\w+) \((?P<where>[\w.]+)\)(?:\n.*?)?\s\.\.\.\s(?P<status>ok|FAIL|ERROR|skipped(?: '(?P<reason>.*)')?|expected failure|unexpected success)\s*$", re.M)
MEMORY_TABLES = ("memories", "memory_revisions", "memory_candidates", "memory_epistemics", "memory_migrations")
NEVER_CREATED_BY_TESTS = ("commander", "workflow_a", "workflow_c", "prototypes")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- P0-2 helpers
def read_fence() -> str | None:
    path = REPORTS / "sync_fence.txt"
    if not path.is_file():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token if re.fullmatch(r"[0-9a-f]{16,64}", token) else "INVALID"


def source_manifest() -> dict[str, Any]:
    files: dict[str, str] = {}
    for top in ("src", "scripts", "tests"):
        for path in sorted((ROOT / top).rglob("*")):
            if path.suffix in (".py", ".bat") and path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = _sha256(path)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    return {"file_count": len(files), "manifest_sha256": digest, "files": files}


# ----------------------------------------------------------------------------- data guard
def _memory_fingerprint(db: Path) -> dict[str, Any]:
    if not db.is_file():
        return {"exists": False}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        out: dict[str, Any] = {"exists": True}
        for table in MEMORY_TABLES:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            rows = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            out[table] = {"rows": len(rows), "sha256": hashlib.sha256(
                json.dumps([list(r) for r in rows], ensure_ascii=False, default=str).encode("utf-8")).hexdigest()}
        return out
    finally:
        con.close()


def _tree_listing(name: str) -> list[str]:
    base = DATA / name
    return sorted(p.relative_to(DATA).as_posix() for p in base.rglob("*")) if base.exists() else []


def _sqlite_files() -> dict[str, list]:
    out = {}
    for p in sorted(DATA.rglob("*.sqlite3")):
        try:
            st = p.stat()
            out[p.relative_to(DATA).as_posix()] = [st.st_size, round(st.st_mtime, 3)]
        except OSError:
            continue
    return out


def guard_snapshot() -> dict[str, Any]:
    return {"memory": _memory_fingerprint(DATA / "memory" / "muse.sqlite3"),
            "never_created": {n: _tree_listing(n) for n in NEVER_CREATED_BY_TESTS},
            "sqlite_files": _sqlite_files()}


def _backup_memory(dest: Path) -> Path | None:
    db = DATA / "memory" / "muse.sqlite3"
    if not db.is_file():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "memory.muse.sqlite3.bak"
    src = sqlite3.connect(db, timeout=10)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def _restore_memory(backup: Path, evidence_dir: Path) -> None:
    db = DATA / "memory" / "muse.sqlite3"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    keep = sqlite3.connect(db, timeout=10)
    out = sqlite3.connect(evidence_dir / "memory.after-tests.sqlite3")
    try:
        keep.backup(out)            # keep the polluted state as evidence (nothing is deleted)
    finally:
        out.close()
        keep.close()
    src = sqlite3.connect(backup)
    dst = sqlite3.connect(db, timeout=10)
    try:
        src.backup(dst)             # online restore of the pre-run state
    finally:
        dst.close()
        src.close()


# ----------------------------------------------------------------------------- runner
def discover_modules() -> list[str]:
    mods = sorted(p.stem for p in TESTS.glob("test_*.py") if _MODULE.fullmatch(p.stem))
    return mods


def _batches(mods: list[str], n: int) -> list[list[str]]:
    size = max(1, -(-len(mods) // n))
    return [mods[i:i + size] for i in range(0, len(mods), size)]


def _run_batch(index: int, mods: list[str], env: dict[str, str], deadline: float) -> dict[str, Any]:
    remaining = deadline - time.time()
    timeout = max(1.0, min(BATCH_TIMEOUT, remaining))
    argv = [sys.executable, "-m", "unittest", "-v", *mods]
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=str(TESTS), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        rc, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        rc, timed_out = 124, True
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    return {"index": index, "modules": mods, "rc": rc, "timed_out": timed_out, "seconds": round(time.time() - started, 1),
            "stdout": out, "stderr": err}


def _parse(text: str) -> dict[str, Any]:
    results = []
    for m in _RESULT.finditer(text):
        status = m.group("status")
        kind = ("passed" if status == "ok" else "failed" if status == "FAIL" else "error" if status == "ERROR"
                else "skipped" if status.startswith("skipped") else "xfail" if status == "expected failure" else "xpass")
        where = m.group("where")
        test_id = where if where.endswith("." + m.group("name")) else f"{where}.{m.group('name')}"
        results.append({"test": test_id, "result": kind, "reason": m.group("reason")})
    ran = re.findall(r"^Ran (\d+) tests? in", text, re.M)
    blocks = re.split(r"^={50,}$", text, flags=re.M)[1:]
    details = {}
    for b in blocks:
        head = re.match(r"\s*(FAIL|ERROR): (\w+) \(([\w.]+)\)", b)
        if head:
            where, name = head.group(3), head.group(2)
            details[where if where.endswith("." + name) else f"{where}.{name}"] = b.strip()[-2500:]
    summary = {"failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0}
    verdicts = re.findall(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", text, re.M)
    for _verdict, inner in verdicts:
        for key, value in re.findall(r"(\w+)=(\d+)", inner or ""):
            if key in summary:
                summary[key] += int(value)
    return {"results": results, "ran": sum(int(x) for x in ran), "details": details, "summary": summary,
            "summary_found": len(ran) >= 1 and len(verdicts) >= 1}


def run_unit_stage() -> dict[str, Any]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    guard_dir = ROOT / "backups" / f"unit-guard-{stamp}"
    # ASCII-only (OpenCV cannot write non-ASCII Windows paths) but keeps a space to exercise quoting.
    work = Path(tempfile.mkdtemp(prefix="muse unit work ", dir=str(ROOT / "tmp")))
    before = guard_snapshot()
    memory_backup = _backup_memory(guard_dir)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           # ROOT first: the suite imports scripts.*, tools.* and tests.* packages.
           "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "src"), str(TESTS)]),
           "MUSE_ERROR_JOURNAL": str(work / "suppressed.jsonl"),
           "MUSE_DIAG_NONINTERACTIVE": "1", "TMP": str(work), "TEMP": str(work)}
    mods = discover_modules()
    deadline = time.time() + STAGE_BUDGET
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        batches = list(pool.map(lambda ib: _run_batch(ib[0], ib[1], env, deadline), enumerate(_batches(mods, 12))))
    results, details, ran, timeouts, crashed = [], {}, 0, [], []
    totals = {"failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0, "unexpected_successes": 0}
    log_parts = []
    for b in batches:
        parsed = _parse(b["stderr"] + "\n" + b["stdout"])
        results += parsed["results"]
        details.update(parsed["details"])
        ran += parsed["ran"]
        for key in totals:
            totals[key] += parsed["summary"][key]
        if b["timed_out"]:
            timeouts.append(b["modules"])
        elif not parsed["summary_found"] or b["rc"] not in (0, 1):
            crashed.append({"modules": b["modules"][:5], "rc": b["rc"], "tail": (b["stderr"] or b["stdout"])[-1500:]})
        log_parts.append(f"===== batch {b['index']} rc={b['rc']} {b['seconds']}s timed_out={b['timed_out']} modules={b['modules']}\n"
                         f"{b['stderr']}\n{b['stdout']}\n")
    (REPORTS / "pytest_last_run.log").write_text("".join(log_parts), encoding="utf-8")
    counts = {k: sum(1 for r in results if r["result"] == k) for k in ("passed", "failed", "error", "skipped", "xfail", "xpass")}
    after = guard_snapshot()
    memory_changed = before["memory"] != after["memory"]
    created = {n: sorted(set(after["never_created"][n]) - set(before["never_created"][n])) for n in NEVER_CREATED_BY_TESTS}
    created = {k: v for k, v in created.items() if v}
    other_changed = sorted(k for k in set(before["sqlite_files"]) | set(after["sqlite_files"])
                           if before["sqlite_files"].get(k) != after["sqlite_files"].get(k))
    restored = False
    if memory_changed and memory_backup is not None:
        _restore_memory(memory_backup, guard_dir / "evidence")
        restored = _memory_fingerprint(DATA / "memory" / "muse.sqlite3") == before["memory"]
    failing = sorted(set(details) | {r["test"] for r in results if r["result"] in ("failed", "error", "xpass")})
    # Authoritative counts come from unittest's own summary lines; per-test lines are best-effort detail.
    ok = (totals["failures"] == 0 and totals["errors"] == 0 and totals["unexpected_successes"] == 0
          and not failing and not timeouts and not crashed and not memory_changed and not created and ran > 0)
    report = {
        "schema": "muse.unit_stage.v1", "runner": "unittest (pytest not installed; installs forbidden)",
        "generated_at": datetime.now().astimezone().isoformat(), "seconds": round(time.time() - started, 1),
        "modules": len(mods), "tests_ran": ran, "parsed_results": len(results), "counts": counts,
        "unittest_summary": totals, "passed": ran - totals["failures"] - totals["errors"] - totals["skipped"]
        - totals["expected_failures"] - totals["unexpected_successes"],
        "failing": failing, "failure_details": {k: details.get(k, "") for k in failing[:200]},
        "skipped": [{"test": r["test"], "reason": r["reason"]} for r in results if r["result"] == "skipped"],
        "timeouts": timeouts, "crashed_batches": crashed,
        "real_data_guard": {"memory_changed": memory_changed, "memory_restored": restored,
                            "memory_backup": str(memory_backup) if memory_backup else None,
                            "created_in_protected_trees": created,
                            "other_sqlite_changed_informational": other_changed},
        "overall": "PASS" if ok else "FAIL",
    }
    (REPORTS / "pytest_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return report
