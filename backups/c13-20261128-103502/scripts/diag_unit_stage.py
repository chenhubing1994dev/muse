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
from datetime import datetime, timezone
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
# C11 / D-029: every live SQLite store under data/ gets a logical (row-level) fingerprint before and after the
# suite. The only rows allowed to change are those the live half-hourly self-check appends *after* the pre-run
# snapshot (observed on disk: watchdog_checks, system/watchdog|capability-radar progress events, radar
# snapshots). History rows stay hashed, so rewriting, deleting or back-dating them is still caught. Anything
# else that changes fails the stage and the store is restored from the pre-run backup (evidence kept).
RUNTIME_APPEND_RULES: dict[str, dict[str, str]] = {
    "tasks/muse.sqlite3": {
        "watchdog_checks": "checked_at >= :since",
        "progress_events": ("created_at >= :since AND category = 'system' "
                            "AND subject_id IN ('watchdog', 'capability-radar')"),
    },
    "improvements/muse.sqlite3": {"capability_radar_snapshots": "observed_at >= :since"},
}
# handoff/ holds frozen copies made by earlier migrations; memory/ has its own hard guard + restore below.
UNGUARDED_PREFIXES = ("handoff/", "memory/")


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


# ----------------------------------------------------------------------------- C11 all-store guard
def guarded_stores(data: Path | None = None) -> list[Path]:
    base = DATA if data is None else data
    return [p for p in sorted(base.rglob("*.sqlite3"))
            if p.is_file() and not p.relative_to(base).as_posix().startswith(UNGUARDED_PREFIXES)]


def _row_digest(h: "hashlib._Hash", row: Any) -> None:
    h.update(json.dumps([c.hex() if isinstance(c, bytes) else c for c in row], ensure_ascii=False,
                        default=str).encode("utf-8"))
    h.update(b"\n")


def store_fingerprint(db: Path, since: str, rules: dict[str, str] | None = None) -> dict[str, Any]:
    """Row-level fingerprint of one store; rows matching an exemption rule (bound to ``since``) are left out.

    Fails closed: any read problem yields ``__error__`` and the diff reports the store as unreadable.
    """
    rules = rules or {}
    out: dict[str, Any] = {}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            schema = con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
            out["__schema__"] = hashlib.sha256(json.dumps(schema, default=str).encode("utf-8")).hexdigest()
            for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
                pred = rules.get(table)
                # COALESCE: a NULL timestamp must never make a row exempt.
                where = f" WHERE NOT COALESCE(({pred}), 0)" if pred else ""
                params = {"since": since} if pred else {}
                h, n = hashlib.sha256(), 0
                try:
                    cur = con.execute(f'SELECT rowid, * FROM "{table}"{where} ORDER BY rowid', params)
                    rows: Any = cur
                except sqlite3.OperationalError:      # WITHOUT ROWID table: order-independent digest
                    rows = sorted(con.execute(f'SELECT * FROM "{table}"{where}', params).fetchall(), key=repr)
                for row in rows:
                    _row_digest(h, row)
                    n += 1
                out[table] = [n, h.hexdigest()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}
    return out


def snapshot_stores(since: str, data: Path | None = None) -> dict[str, dict[str, Any]]:
    base = DATA if data is None else data
    return {p.relative_to(base).as_posix(): store_fingerprint(p, since, RUNTIME_APPEND_RULES.get(
        p.relative_to(base).as_posix())) for p in guarded_stores(base)}


def diff_store_fingerprints(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    changed: dict[str, list[str]] = {}
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b is None:
            changed[rel] = ["__created__"]
        elif a is None:
            changed[rel] = ["__deleted__"]
        elif "__error__" in b or "__error__" in a:
            changed[rel] = ["__unreadable__"]
        else:
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            if diff:
                changed[rel] = diff
    return changed


def backup_stores(dest: Path, data: Path | None = None) -> dict[str, Any]:
    base = DATA if data is None else data
    out: dict[str, Any] = {}
    for p in guarded_stores(base):
        rel = p.relative_to(base).as_posix()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            src = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            out[rel] = target
        except sqlite3.Error as exc:   # recorded; a store without a backup cannot be auto-restored (reported)
            out[rel] = f"backup failed: {type(exc).__name__}: {exc}"
    return out


def restore_stores(changed: dict[str, list[str]], backups: dict[str, Any], evidence: Path,
                   data: Path | None = None) -> dict[str, str]:
    """Put changed stores back to their pre-run state. Nothing is deleted: the post-run state is kept as evidence."""
    base = DATA if data is None else data
    result: dict[str, str] = {}
    for rel in changed:
        db = base / rel
        keep = evidence / rel
        keep.parent.mkdir(parents=True, exist_ok=True)
        try:
            if db.exists():
                shutil.copy2(db, keep.with_name(keep.name + ".after-tests"))
            backup = backups.get(rel)
            if not isinstance(backup, Path):
                result[rel] = "no pre-run backup (store created by tests or backup failed); left in place, evidence kept"
                continue
            src = sqlite3.connect(backup)
            dst = sqlite3.connect(db, timeout=10)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            result[rel] = "restored"
        except (OSError, sqlite3.Error) as exc:
            result[rel] = f"restore failed: {type(exc).__name__}: {exc}"
    return result


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
    store_guard = Path(tempfile.mkdtemp(prefix="muse store guard ", dir=str(ROOT / "tmp")))
    since = datetime.now(timezone.utc).isoformat()
    stores_before = snapshot_stores(since)
    store_backups = backup_stores(store_guard)
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
    stores_changed = diff_store_fingerprints(stores_before, snapshot_stores(since))
    stores_restore: dict[str, str] = {}
    if stores_changed:
        kept = guard_dir / "stores-pre-run"
        shutil.copytree(store_guard, kept, dirs_exist_ok=True)     # audit copy of the pre-run backups
        stores_restore = restore_stores(stores_changed, {k: kept / k if isinstance(v, Path) else v
                                                          for k, v in store_backups.items()},
                                        guard_dir / "stores-evidence")
        stores_after_restore = diff_store_fingerprints(stores_before, snapshot_stores(since))
        stores_restore["__verified_clean__"] = str(not stores_after_restore)
    shutil.rmtree(store_guard, ignore_errors=True)
    restored = False
    if memory_changed and memory_backup is not None:
        _restore_memory(memory_backup, guard_dir / "evidence")
        restored = _memory_fingerprint(DATA / "memory" / "muse.sqlite3") == before["memory"]
    failing = sorted(set(details) | {r["test"] for r in results if r["result"] in ("failed", "error", "xpass")})
    # Authoritative counts come from unittest's own summary lines; per-test lines are best-effort detail.
    ok = (totals["failures"] == 0 and totals["errors"] == 0 and totals["unexpected_successes"] == 0
          and not failing and not timeouts and not crashed and not memory_changed and not created
          and not stores_changed and ran > 0)
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
                            "other_sqlite_changed_informational": other_changed,
                            "stores_guarded": len(stores_before),
                            "stores_changed_unexplained": stores_changed,
                            "stores_restore": stores_restore},
        "overall": "PASS" if ok else "FAIL",
    }
    (REPORTS / "pytest_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return report
