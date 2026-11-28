"""Legacy User Track memory migration (T7): tag every unstamped User Track memory ``legacy_unverified``.

Guarantees (asserted by scripts/muse_diag.py L3.6):
* complete, consistent backup first (SQLite online-backup API) with its SHA-256 recorded;
* only *adds* rows to the ``memory_epistemics`` side table (+ one ``memory_migrations`` ledger row);
  ``memories`` / ``memory_revisions`` are never written — their content hash is compared before and
  after inside the same transaction and the transaction is rolled back on any difference;
* idempotent: a second run adds 0 rows; ``rollback(migration_id)`` removes exactly the rows that
  migration added and nothing else;
* a JSON + Markdown report is written for every apply / dry-run / rollback.

Deliberately stdlib-only (no ``muse`` imports) so it can run standalone.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

LEGACY_TAG = "legacy_unverified"
LEGACY_ORIGIN = "legacy_import"
SIDE_TABLE_DDL = """CREATE TABLE IF NOT EXISTS memory_epistemics (
    ref_kind TEXT NOT NULL CHECK(ref_kind IN ('memory','candidate')),
    ref_id INTEGER NOT NULL,
    track TEXT NOT NULL CHECK(track IN ('user','agent')),
    epistemic_tag TEXT NOT NULL,
    origin TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    approved_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(ref_kind, ref_id))"""
LEDGER_DDL = """CREATE TABLE IF NOT EXISTS memory_migrations (
    migration_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    rows_added_json TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    backup_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('applied','rolled_back')))"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def content_fingerprint(con: sqlite3.Connection) -> dict[str, Any]:
    """Hash of every column of ``memories`` and ``memory_revisions`` (the user data of record)."""
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    for table in ("memories", "memory_revisions"):
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not exists:
            counts[table] = 0
            continue
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        rows = con.execute(f'SELECT {",".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}" ORDER BY id').fetchall()
        counts[table] = len(rows)
        digest.update(table.encode("utf-8"))
        for row in rows:
            digest.update(json.dumps(list(row), ensure_ascii=False, default=str).encode("utf-8"))
    return {"sha256": digest.hexdigest(), "counts": counts}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(db: Path, backup_dir: Path) -> tuple[Path, str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db.stem}.{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.sqlite3.bak"
    src = sqlite3.connect(db, timeout=10)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target, _sha256_file(target)


def _write_report(report_dir: Path, report: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    # Unique per report: several reports can be written within the same second (never overwrite one).
    stem = f"memory_migration_{report['action']}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    (report_dir / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 记忆迁移报告：{report['action']}", "", f"- 时间：{report['at']}", f"- 数据库：{report['db']}",
             f"- 迁移 ID：{report.get('migration_id') or '—'}", f"- 新增标签行：{report.get('rows_added', 0)}",
             f"- 移除标签行：{report.get('rows_removed', 0)}",
             f"- 原内容哈希（前/后）：{report['content_before']['sha256'][:16]} / {report['content_after']['sha256'][:16]}",
             f"- 条数（前/后）：{report['content_before']['counts']} / {report['content_after']['counts']}",
             f"- 备份：{report.get('backup_path') or '—'}（sha256 {str(report.get('backup_sha256') or '')[:16]}）",
             f"- 结论：{report['result']}", ""]
    (report_dir / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    return report_dir / f"{stem}.json"


def migrate(db_path: str | Path, *, backup_dir: str | Path, report_dir: str | Path, apply: bool) -> dict[str, Any]:
    db = Path(db_path).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"memory database not found: {db}")
    con = sqlite3.connect(db, timeout=10)
    try:
        before = content_fingerprint(con)
        has_table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_epistemics'").fetchone()
        if has_table:
            pending = [r[0] for r in con.execute(
                "SELECT m.id FROM memories m LEFT JOIN memory_epistemics e ON e.ref_kind='memory' AND e.ref_id=m.id "
                "WHERE m.track='user' AND e.ref_id IS NULL ORDER BY m.id")]
        else:
            pending = [r[0] for r in con.execute("SELECT id FROM memories WHERE track='user' ORDER BY id")]
    finally:
        con.close()
    report: dict[str, Any] = {"action": "apply" if apply else "dry_run", "at": _now(), "db": str(db),
                              "pending_user_rows": len(pending), "content_before": before}
    if not apply or not pending:
        report.update({"rows_added": 0, "content_after": before, "result": "no_changes" if apply else "dry_run_only"})
        report["report_path"] = str(_write_report(Path(report_dir), report))
        return report
    backup_path, backup_sha = _backup(db, Path(backup_dir))
    migration_id = uuid.uuid4().hex
    con = sqlite3.connect(db, timeout=10, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(SIDE_TABLE_DDL)
        con.execute(LEDGER_DDL)
        now = _now()
        added = []
        for memory_id in pending:
            cur = con.execute("INSERT OR IGNORE INTO memory_epistemics VALUES (?,?,?,?,?,?,?,?)",
                              ("memory", int(memory_id), "user", LEGACY_TAG, LEGACY_ORIGIN, LEGACY_TAG, "", now))
            if cur.rowcount == 1:
                added.append(int(memory_id))
        after = content_fingerprint(con)
        if after != before:
            con.execute("ROLLBACK")
            raise RuntimeError("memory content changed during migration; rolled back")
        con.execute("INSERT INTO memory_migrations VALUES (?,?,?,?,?,?,?,?)",
                    (migration_id, "legacy_user_track_tags", now, json.dumps(added), str(backup_path), backup_sha,
                     before["sha256"], "applied"))
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    report.update({"migration_id": migration_id, "rows_added": len(added), "added_memory_ids": added,
                   "backup_path": str(backup_path), "backup_sha256": backup_sha, "content_after": after,
                   "result": "applied"})
    report["report_path"] = str(_write_report(Path(report_dir), report))
    return report


def rollback(db_path: str | Path, migration_id: str, *, report_dir: str | Path) -> dict[str, Any]:
    db = Path(db_path).resolve()
    con = sqlite3.connect(db, timeout=10, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT rows_added_json, status FROM memory_migrations WHERE migration_id=?", (migration_id,)).fetchone()
        if row is None:
            raise LookupError(f"unknown migration {migration_id}")
        if row[1] != "applied":
            raise ValueError("migration already rolled back")
        before = content_fingerprint(con)
        removed = 0
        for memory_id in json.loads(row[0]):
            removed += con.execute(
                "DELETE FROM memory_epistemics WHERE ref_kind='memory' AND ref_id=? AND origin=? AND approval_status=?",
                (int(memory_id), LEGACY_ORIGIN, LEGACY_TAG)).rowcount
        con.execute("UPDATE memory_migrations SET status='rolled_back' WHERE migration_id=?", (migration_id,))
        after = content_fingerprint(con)
        if after != before:
            con.execute("ROLLBACK")
            raise RuntimeError("memory content changed during rollback; aborted")
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    report = {"action": "rollback", "at": _now(), "db": str(db), "migration_id": migration_id, "rows_removed": removed,
              "content_before": before, "content_after": after, "result": "rolled_back"}
    report["report_path"] = str(_write_report(Path(report_dir), report))
    return report
