"""Human review of ``legacy_unverified`` User Track memories (backlog K).

T7 tagged every pre-taxonomy User Track memory ``legacy_unverified``: it is *not* evidence of what the user
said. This module is the only way such a stamp can become ``user_confirmed``:

* the reviewer identity must be ``user:<name>`` — ``agent:``, ``human:``, an empty name or no prefix are
  refused before anything is read for writing (an agent can never upgrade a legacy memory);
* the tag must be admissible on the User Track (``muse.epistemics.assert_user_track_admissible``);
* a complete SQLite backup is taken first and its SHA-256 recorded; only the ``memory_epistemics`` stamp
  row changes — ``memories`` / ``memory_revisions`` are hashed inside the same transaction and the
  transaction is rolled back on any difference;
* every review is a row in ``memory_legacy_reviews`` (previous stamp kept verbatim) and can be reverted by a
  ``user:`` reviewer; reviewing an already-confirmed memory is a no-op (idempotent);
* a JSON + Markdown report is written for every confirm / revert, and :func:`write_checklist` produces the
  review checklist for the human.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from muse.epistemics import assert_user_track_admissible
from muse.memory_migration import LEGACY_ORIGIN, LEGACY_TAG, _backup, content_fingerprint

REVIEWER_PREFIX = "user:"
REVIEW_DDL = """CREATE TABLE IF NOT EXISTS memory_legacy_reviews (
    review_id TEXT PRIMARY KEY,
    memory_id INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('confirm')),
    tag TEXT NOT NULL,
    previous_stamp_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    backup_sha256 TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('applied','reverted')),
    reverted_by TEXT NOT NULL DEFAULT '',
    reverted_at TEXT NOT NULL DEFAULT '')"""
_PENDING_SQL = ("SELECT m.id AS id, m.kind AS kind, m.content AS content FROM memories m "
                "JOIN memory_epistemics e ON e.ref_kind='memory' AND e.ref_id=m.id "
                "WHERE m.track='user' AND m.deleted_at IS NULL AND e.origin=? AND e.approval_status=? ORDER BY m.id")


class ReviewerNotHuman(PermissionError):
    """Only a ``user:<name>`` identity may review legacy User Track memories."""


def require_user_reviewer(reviewer: Any) -> str:
    if not isinstance(reviewer, str) or not reviewer.startswith(REVIEWER_PREFIX) or not reviewer[len(REVIEWER_PREFIX):].strip():
        raise ReviewerNotHuman("legacy memory review only accepts a user:<name> reviewer (agent:/human:/empty refused)")
    return reviewer


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def pending_reviews(db_path: str | Path) -> list[dict[str, Any]]:
    """Read-only list of live User Track memories still stamped ``legacy_unverified``."""
    db = Path(db_path).resolve()
    con = _ro(db)
    try:
        if not _has_table(con, "memory_epistemics"):
            return []
        return [{"memory_id": int(r["id"]), "kind": r["kind"], "content": r["content"],
                 "content_sha256": hashlib.sha256(str(r["content"]).encode("utf-8")).hexdigest()}
                for r in con.execute(_PENDING_SQL, (LEGACY_ORIGIN, LEGACY_TAG))]
    finally:
        con.close()


def write_checklist(db_path: str | Path, report_dir: str | Path) -> dict[str, Any]:
    items = pending_reviews(db_path)
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"legacy_review_checklist_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    (out / f"{stem}.json").write_text(json.dumps({"at": _now(), "db": str(Path(db_path).resolve()), "pending": items},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 旧记忆复核清单（legacy_unverified）", "", f"- 生成：{_now()}", f"- 待复核：{len(items)} 条",
             "- 确认命令（只接受 user: 身份）：`python scripts\\review_legacy_memory.py confirm --id <memory_id> --by user:<你的名字>`",
             "- 不确认的条目保持 legacy_unverified（不会被当作证据）；如需修改或删除原文，请用记忆命令行自行操作。", ""]
    lines += [f"- [ ] #{i['memory_id']}（{i['kind']}）{str(i['content'])[:200]}" for i in items]
    (out / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"pending": len(items), "checklist": str(out / f"{stem}.md")}


def _write_report(report_dir: Path, report: dict[str, Any]) -> str:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"legacy_review_{report['action']}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    (report_dir / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# 旧记忆复核：{report['action']}", "", f"- 时间：{report['at']}", f"- 记忆 ID：{report.get('memory_id')}",
          f"- 复核人：{report.get('reviewer')}", f"- 结果：{report['result']}",
          f"- 原文哈希（前/后）：{report['content_before']['sha256'][:16]} / {report['content_after']['sha256'][:16]}",
          f"- 备份：{report.get('backup_path') or '—'}", ""]
    (report_dir / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    return str(report_dir / f"{stem}.json")


def confirm(db_path: str | Path, memory_id: int, *, reviewer: str, tag: str = "fact",
            backup_dir: str | Path, report_dir: str | Path) -> dict[str, Any]:
    reviewer = require_user_reviewer(reviewer)        # identity first: a refused reviewer writes nothing
    resolved = assert_user_track_admissible(tag=tag, origin="user_confirmed")
    db = Path(db_path).resolve()
    con = _ro(db)
    try:
        stamp = con.execute("SELECT e.* FROM memory_epistemics e JOIN memories m ON m.id=e.ref_id "
                            "WHERE e.ref_kind='memory' AND e.ref_id=? AND m.track='user'", (int(memory_id),)).fetchone() \
            if _has_table(con, "memory_epistemics") else None
        done = con.execute("SELECT review_id FROM memory_legacy_reviews WHERE memory_id=? AND status='applied'",
                           (int(memory_id),)).fetchone() if _has_table(con, "memory_legacy_reviews") else None
    finally:
        con.close()
    if stamp is None:
        raise ValueError(f"memory {memory_id} is not a stamped User Track memory")
    if done is not None and stamp["origin"] == "user_confirmed":
        return {"action": "confirm", "memory_id": int(memory_id), "result": "already_confirmed", "review_id": done[0]}
    if stamp["origin"] != LEGACY_ORIGIN or stamp["approval_status"] != LEGACY_TAG:
        raise ValueError(f"memory {memory_id} is not legacy_unverified (origin={stamp['origin']}, status={stamp['approval_status']})")
    backup_path, backup_sha = _backup(db, Path(backup_dir))
    review_id = uuid.uuid4().hex
    con = sqlite3.connect(db, timeout=10, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(REVIEW_DDL)
        before = content_fingerprint(con)
        row = con.execute("SELECT content FROM memories WHERE id=? AND track='user' AND deleted_at IS NULL",
                          (int(memory_id),)).fetchone()
        if row is None:
            raise ValueError(f"memory {memory_id} is deleted or not on the User Track")
        previous = con.execute("SELECT * FROM memory_epistemics WHERE ref_kind='memory' AND ref_id=?", (int(memory_id),)).fetchone()
        cols = [d[0] for d in con.execute("SELECT * FROM memory_epistemics LIMIT 0").description]
        changed = con.execute(
            "UPDATE memory_epistemics SET epistemic_tag=?, origin='user_confirmed', approval_status='user_confirmed', "
            "approved_by=? WHERE ref_kind='memory' AND ref_id=? AND origin=? AND approval_status=?",
            (resolved, reviewer, int(memory_id), LEGACY_ORIGIN, LEGACY_TAG)).rowcount
        if changed != 1:
            raise RuntimeError(f"stamp changed concurrently (updated {changed} rows); rolled back")
        after = content_fingerprint(con)
        if after != before:
            raise RuntimeError("memory content changed during review; rolled back")
        con.execute("INSERT INTO memory_legacy_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (review_id, int(memory_id), reviewer, "confirm", resolved,
                     json.dumps(dict(zip(cols, previous)), ensure_ascii=False),
                     hashlib.sha256(str(row[0]).encode("utf-8")).hexdigest(), str(backup_path), backup_sha,
                     _now(), "applied", "", ""))
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    report = {"action": "confirm", "at": _now(), "db": str(db), "memory_id": int(memory_id), "reviewer": reviewer,
              "tag": resolved, "review_id": review_id, "backup_path": str(backup_path), "backup_sha256": backup_sha,
              "content_before": before, "content_after": after, "result": "confirmed"}
    report["report_path"] = _write_report(Path(report_dir), report)
    return report


def revert(db_path: str | Path, review_id: str, *, reviewer: str, report_dir: str | Path) -> dict[str, Any]:
    """Undo one confirmation: restore the exact previous stamp (only if nobody changed it since)."""
    reviewer = require_user_reviewer(reviewer)
    db = Path(db_path).resolve()
    con = sqlite3.connect(db, timeout=10, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        if not _has_table(con, "memory_legacy_reviews"):
            raise LookupError(f"unknown review {review_id}")
        rev = con.execute("SELECT memory_id, reviewer, tag, previous_stamp_json, status FROM memory_legacy_reviews "
                          "WHERE review_id=?", (review_id,)).fetchone()
        if rev is None:
            raise LookupError(f"unknown review {review_id}")
        if rev[4] != "applied":
            raise ValueError("review already reverted")
        before = content_fingerprint(con)
        prev = json.loads(rev[3])
        changed = con.execute(
            "UPDATE memory_epistemics SET epistemic_tag=?, origin=?, approval_status=?, approved_by=? "
            "WHERE ref_kind='memory' AND ref_id=? AND origin='user_confirmed' AND approved_by=? AND epistemic_tag=?",
            (prev["epistemic_tag"], prev["origin"], prev["approval_status"], prev["approved_by"],
             int(rev[0]), rev[1], rev[2])).rowcount
        if changed != 1:
            raise RuntimeError("stamp no longer matches this review (changed since); refusing to revert")
        con.execute("UPDATE memory_legacy_reviews SET status='reverted', reverted_by=?, reverted_at=? WHERE review_id=?",
                    (reviewer, _now(), review_id))
        after = content_fingerprint(con)
        if after != before:
            raise RuntimeError("memory content changed during revert; rolled back")
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    report = {"action": "revert", "at": _now(), "db": str(db), "memory_id": int(rev[0]), "reviewer": reviewer,
              "review_id": review_id, "content_before": before, "content_after": after, "result": "reverted"}
    report["report_path"] = _write_report(Path(report_dir), report)
    return report
