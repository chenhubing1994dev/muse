"""Read-only audit and backup-verified, non-destructive repair of temp-path ``inspiration_refs``.

Forensics (2026-11-25, read-only immutable open of data/initiatives/muse.sqlite3):
3,954 of 4,073 ``creator_initiatives`` rows carry exactly one inspiration ref that
points into ``C:\\Users\\...\\AppData\\Local\\Temp\\tmp*\\reports\\*.md``. None of those
files exist any more and no basename matches ``data/research/reports``. 3,950 rows
are ``tool/proposed`` and 4 are ``tool/testing``; titles such as
"Creator-inspired test hypothesis" indicate test runs that wrote report files to a
TemporaryDirectory while writing initiatives into the production store.

Repair policy (never deletes anything):
* ``audit`` copies the DB through the SQLite backup API from a ``mode=ro`` connection
  and only reads the copy.
* ``apply`` works on an explicit path. Inside one ``BEGIN IMMEDIATE`` transaction it
  first takes a byte-exact backup (SHA-256 equal to the locked pre-image, integrity_check ok), then rewrites
  each temp ref to either the stable report path (same basename found under the
  reports dir) or the honest marker ``missing-temp-report:<basename>``, and records
  every original value in an append-only ``inspiration_ref_repairs`` table.
* The canonical production DB is refused unless the caller passes
  ``allow_production=True`` *and* the exact current SHA-256 of the DB file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPAIR_SCHEMA = "muse-inspiration-ref-repair-v1"
MISSING_PREFIX = "missing-temp-report:"
_TEMP_PATTERNS = (
    re.compile(r"[\\/]appdata[\\/]local[\\/]temp[\\/]", re.I),
    re.compile(r"^/tmp/", re.I),
    re.compile(r"^/var/folders/[^/]+/[^/]+/t/", re.I),
    re.compile(r"[\\/]tmp[a-z0-9_]{6,}[\\/]", re.I),
)
_TEST_TITLE = re.compile(r"\btest hypothesis\b|\bscenario branch\b", re.I)


class RepairRefused(RuntimeError):
    """The requested repair would violate a safety rule."""


def is_temp_ref(value: Any) -> bool:
    return isinstance(value, str) and any(pattern.search(value) for pattern in _TEMP_PATTERNS)


def ref_basename(value: str) -> str:
    return re.split(r"[\\/]", value.rstrip("\\/"))[-1][:240]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_uri(path: Path) -> str:
    return "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"


def snapshot_copy(source: str | Path, destination: str | Path) -> Path:
    """Consistent copy from a read-only source connection (backup API, else ``VACUUM INTO``)."""
    source_path, destination_path = Path(source), Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(f"database not found: {source_path}")
    if destination_path.exists():
        raise RepairRefused(f"refusing to overwrite existing file: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(_readonly_uri(source_path), uri=True, timeout=10)
    try:
        try:
            writer = sqlite3.connect(destination_path)
            try:
                reader.backup(writer)
            finally:
                writer.close()
        except (TypeError, sqlite3.DatabaseError):
            destination_path.unlink(missing_ok=True)
            reader.execute("VACUUM INTO ?", (str(destination_path),))
    finally:
        reader.close()
    return destination_path


def _byte_backup_under_lock(target: Path, backup_path: Path, expected_sha256: str) -> dict[str, Any]:
    """Byte-exact pre-image copy; caller must already hold a write lock on ``target``."""
    if backup_path.exists():
        raise RepairRefused(f"refusing to overwrite existing backup: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target, backup_path)
    backup_sha = sha256_file(backup_path)
    if backup_sha != expected_sha256:
        backup_path.unlink(missing_ok=True)
        raise RepairRefused("backup bytes do not match the locked pre-image; aborting")
    check = sqlite3.connect(_readonly_uri(backup_path), uri=True)
    try:
        integrity = _integrity(check)
        rows = check.execute("SELECT count(*) FROM creator_initiatives").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        raise RepairRefused(f"backup integrity_check failed: {integrity}")
    return {"backup_path": str(backup_path), "backup_sha256": backup_sha,
            "backup_integrity_check": integrity, "backup_initiative_rows": rows}


def _integrity(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _plan_rows(connection: sqlite3.Connection, reports_dir: Path | None) -> tuple[list[dict], dict]:
    stable = {}
    if reports_dir is not None and reports_dir.is_dir():
        stable = {item.name: item for item in reports_dir.iterdir() if item.is_file()}
    plan: list[dict] = []
    stats = Counter()
    by_status = Counter()
    by_day = Counter()
    test_like = 0
    for initiative_id, title, kind, status, refs_json, created_at in connection.execute(
            "SELECT id,title,kind,status,inspiration_refs_json,created_at FROM creator_initiatives ORDER BY created_at,id"):
        stats["initiatives"] += 1
        try:
            refs = json.loads(refs_json or "[]")
        except json.JSONDecodeError:
            stats["malformed_refs_json"] += 1
            continue
        if not isinstance(refs, list):
            stats["malformed_refs_json"] += 1
            continue
        stats["refs"] += len(refs)
        changes = []
        new_refs = []
        for ref in refs:
            if is_temp_ref(ref):
                basename = ref_basename(ref)
                stats["temp_refs"] += 1
                try:
                    if Path(ref).exists():
                        stats["temp_ref_file_still_exists"] += 1
                except OSError:  # permission-restricted environments: existence unknown, never fatal
                    stats["temp_ref_existence_unknown"] += 1
                if basename in stable:
                    replacement = f"data/research/reports/{basename}"
                    reason = "stable_report_same_basename"
                    stats["mapped_to_stable_report"] += 1
                else:
                    replacement = MISSING_PREFIX + basename
                    reason = "temp_file_missing_marker"
                    stats["marked_missing"] += 1
                changes.append({"old_ref": ref, "new_ref": replacement, "reason": reason})
                new_refs.append(replacement)
            else:
                new_refs.append(ref)
        if changes:
            deduped = list(dict.fromkeys(new_refs))
            plan.append({"initiative_id": initiative_id, "title": title, "status": status, "kind": kind,
                         "old_refs_json": refs_json, "new_refs": deduped, "changes": changes})
            by_status[f"{kind}/{status}"] += 1
            by_day[str(created_at)[:10]] += 1
            if _TEST_TITLE.search(title or ""):
                test_like += 1
    summary = {
        **{key: int(value) for key, value in stats.items()},
        "initiatives_with_temp_refs": len(plan),
        "by_kind_status": dict(by_status.most_common()),
        "by_created_day": dict(sorted(by_day.items())),
        "test_like_titles": test_like,
        "suspected_cause": ("test or sandbox runs wrote report files to a TemporaryDirectory while writing "
                            "initiatives into this store" if plan else "none"),
    }
    return plan, summary


def audit(database: str | Path, *, reports_dir: str | Path | None = None, work_dir: str | Path | None = None,
          sample: int = 5) -> dict[str, Any]:
    """Read-only audit; the source database is never opened for writing."""
    source = Path(database)
    before = sha256_file(source)
    scratch = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="muse-ref-audit-"))
    copy_path = scratch / f"audit-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.sqlite3"
    snapshot_copy(source, copy_path)
    try:
        connection = sqlite3.connect(_readonly_uri(copy_path), uri=True)
        try:
            integrity = _integrity(connection)
            plan, summary = _plan_rows(connection, Path(reports_dir) if reports_dir else None)
        finally:
            connection.close()
    finally:
        copy_path.unlink(missing_ok=True)
        try:
            scratch.rmdir()
        except OSError:
            pass
    after = sha256_file(source)
    return {
        "schema": REPAIR_SCHEMA, "mode": "audit", "database": str(source),
        "source_sha256_before": before, "source_sha256_after": after,
        "source_unchanged": before == after, "integrity_check": integrity,
        "summary": summary,
        "sample": [{"initiative_id": item["initiative_id"], "title": item["title"][:120],
                    "changes": item["changes"]} for item in plan[:max(0, sample)]],
    }


def _ensure_repair_table(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS inspiration_ref_repairs(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        repair_run_id TEXT NOT NULL,
        initiative_id TEXT NOT NULL,
        old_ref TEXT NOT NULL,
        new_ref TEXT NOT NULL,
        reason TEXT NOT NULL,
        old_refs_json_sha256 TEXT NOT NULL,
        db_sha256_before TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(initiative_id, old_ref)
    )""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS inspiration_ref_repairs_no_update
        BEFORE UPDATE ON inspiration_ref_repairs
        BEGIN SELECT RAISE(ABORT,'inspiration ref repairs are append-only'); END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS inspiration_ref_repairs_no_delete
        BEFORE DELETE ON inspiration_ref_repairs
        BEGIN SELECT RAISE(ABORT,'inspiration ref repairs are append-only'); END""")


def apply_repair(database: str | Path, *, reports_dir: str | Path | None = None,
                 production_database: str | Path | None = None, allow_production: bool = False,
                 confirm_sha256: str | None = None, backup_dir: str | Path | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    target = Path(database).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"database not found: {target}")
    before = sha256_file(target)
    if production_database is not None and Path(production_database).resolve() == target:
        if not allow_production:
            raise RepairRefused("target is the live production DB; pass --allow-production and --confirm-sha256 "
                                "(gate G6 requires the creator's explicit action)")
        if confirm_sha256 != before:
            raise RepairRefused("--confirm-sha256 does not match the current production DB SHA-256")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists() and sidecar.stat().st_size > 0:
            raise RepairRefused(f"{sidecar.name} is non-empty: stop Muse writers and checkpoint before repair")
    report: dict[str, Any] = {"schema": REPAIR_SCHEMA, "mode": "dry_run" if dry_run else "apply",
                              "database": str(target), "db_sha256_before": before}
    connection = sqlite3.connect(target, timeout=15, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=15000")
    run_id = f"ref-repair-{int(time.time() * 1000)}"
    try:
        connection.execute("BEGIN IMMEDIATE")  # blocks every other writer from here to COMMIT/ROLLBACK
        if sha256_file(target) != before:
            raise RepairRefused("database changed between hashing and locking; re-run the audit")
        plan, summary = _plan_rows(connection, Path(reports_dir) if reports_dir else None)
        report["summary"] = summary
        if dry_run:
            connection.execute("ROLLBACK")
            report["planned_rows"] = len(plan)
            report["sample"] = plan[:5]
            return report
        folder = Path(backup_dir) if backup_dir else target.parent
        backup_path = folder / f"{target.stem}.pre-ref-repair-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.sqlite3"
        report.update(_byte_backup_under_lock(target, backup_path, before))
        _ensure_repair_table(connection)
        now = datetime.now(timezone.utc).isoformat()
        for item in plan:
            old_sha = hashlib.sha256((item["old_refs_json"] or "").encode("utf-8")).hexdigest()
            for change in item["changes"]:
                connection.execute(
                    "INSERT OR IGNORE INTO inspiration_ref_repairs(repair_run_id,initiative_id,old_ref,new_ref,"
                    "reason,old_refs_json_sha256,db_sha256_before,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, item["initiative_id"], change["old_ref"], change["new_ref"], change["reason"],
                     old_sha, before, now),
                )
            cursor = connection.execute(
                "UPDATE creator_initiatives SET inspiration_refs_json=? WHERE id=? AND inspiration_refs_json=?",
                (json.dumps(item["new_refs"], ensure_ascii=False), item["initiative_id"], item["old_refs_json"]),
            )
            if cursor.rowcount != 1:
                raise RepairRefused(f"initiative {item['initiative_id']} changed during repair; aborting")
        remaining = sum(1 for row in connection.execute("SELECT inspiration_refs_json FROM creator_initiatives")
                        for ref in json.loads(row[0] or "[]") if is_temp_ref(ref))
        if remaining:
            raise RepairRefused(f"{remaining} temp refs remain after rewrite; aborting")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        integrity = _integrity(connection)
        connection.close()
    report.update({"repair_run_id": run_id, "rows_rewritten": len(plan),
                   "refs_rewritten": sum(len(item["changes"]) for item in plan),
                   "integrity_check_after": integrity, "db_sha256_after": sha256_file(target),
                   "rollback_command": f'copy /Y "{report["backup_path"]}" "{target}"'})
    return report


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    production = root / "data" / "initiatives" / "muse.sqlite3"
    parser = argparse.ArgumentParser(description="Audit (read-only, default) or repair temp-path inspiration_refs.")
    parser.add_argument("--db", default=str(production), help="database to audit (default: production, read-only)")
    parser.add_argument("--reports-dir", default=str(root / "data" / "research" / "reports"))
    parser.add_argument("--apply-on-db", help="explicit database path to repair (backup is created first)")
    parser.add_argument("--dry-run", action="store_true", help="with --apply-on-db: plan only, no writes")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--confirm-sha256", help="exact current SHA-256 of the production DB (required with --allow-production)")
    parser.add_argument("--backup-dir")
    parser.add_argument("--out", help="write the JSON report to this file")
    args = parser.parse_args(argv)
    try:
        if args.apply_on_db:
            report = apply_repair(args.apply_on_db, reports_dir=args.reports_dir, production_database=production,
                                  allow_production=args.allow_production, confirm_sha256=args.confirm_sha256,
                                  backup_dir=args.backup_dir, dry_run=args.dry_run)
        else:
            report = audit(args.db, reports_dir=args.reports_dir)
    except (RepairRefused, FileNotFoundError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def make_disposable_clone(source: str | Path, destination_dir: str | Path) -> Path:
    """Convenience for drills: consistent clone of a DB into a scratch directory."""
    destination = Path(destination_dir) / f"{Path(source).stem}.drill-{int(time.time())}.sqlite3"
    return snapshot_copy(source, destination)


__all__ = ["RepairRefused", "audit", "apply_repair", "is_temp_ref", "snapshot_copy", "make_disposable_clone",
           "MISSING_PREFIX", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
