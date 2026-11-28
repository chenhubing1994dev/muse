"""Tamper-evident audit trail for Muse Commander (backlog C).

* :func:`append_chained` — append-only JSONL where every line carries ``seq``, ``prev_sha256`` and
  ``entry_sha256`` (sha256 of the canonical line body); used for bypass, safety and Ruflo audit logs
  and for the cost chain.
* :func:`verify` — checks, for one Commander root: the completion-receipt chain, every chained JSONL
  audit file, and that every row of ``usage.sqlite3`` matches its cost-chain entry (row digest) and
  vice versa. Any edited, deleted, reordered or unchained line is reported; ``ok`` is False then.

CLI: ``python -m muse.audit verify [--root data/commander]`` (exit code 0 only when everything verifies).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

CHAINED_FILES = ("bypass_audit.jsonl", "safety_actions.jsonl", "ruflo_calls.jsonl", "cost_chain.jsonl")
GENESIS = "0" * 64
_LOCK = threading.Lock()


def _canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _last(path: Path) -> tuple[int, str]:
    if not path.is_file():
        return 0, GENESIS
    last = ""
    with path.open("rb") as handle:
        for raw in handle:
            if raw.strip():
                last = raw.decode("utf-8")
    if not last:
        return 0, GENESIS
    entry = json.loads(last)
    return int(entry.get("seq", 0)), str(entry.get("entry_sha256", GENESIS))


def append_chained(path: str | Path, entry: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        seq, prev = _last(target)
        body = {k: v for k, v in entry.items() if k not in ("seq", "prev_sha256", "entry_sha256")}
        body.update({"seq": seq + 1, "prev_sha256": prev})
        body["entry_sha256"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(body) + "\n")
    return body


def verify_chained_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"file": path.name, "ok": True, "count": 0}
    prev, expected_seq, errors = GENESIS, 1, []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f"line {lineno}: not JSON")
            break
        claimed = entry.pop("entry_sha256", None)
        if claimed is None:
            errors.append(f"line {lineno}: unchained entry")
            break
        if entry.get("seq") != expected_seq or entry.get("prev_sha256") != prev:
            errors.append(f"line {lineno}: sequence/prev mismatch")
            break
        if hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest() != claimed:
            errors.append(f"line {lineno}: content hash mismatch")
            break
        prev, expected_seq = claimed, expected_seq + 1
    return {"file": path.name, "ok": not errors, "count": expected_seq - 1, "errors": errors}


def usage_row_digest(row: dict[str, Any]) -> str:
    keys = ("event_id", "provider_id", "model", "task_id", "agent_id", "project_id", "token_source",
            "input_tokens", "output_tokens", "total_tokens", "cost_amount", "cost_currency", "cost_kind")
    return hashlib.sha256(_canonical({k: row.get(k) for k in keys}).encode("utf-8")).hexdigest()


def _verify_receipts(root: Path) -> dict[str, Any]:
    path = root / "receipts.jsonl"
    if not path.is_file():
        return {"file": "receipts.jsonl", "ok": True, "count": 0}
    previous, count = "", 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        body = json.loads(raw)
        claimed = body.pop("receipt_sha256", None)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if body.get("prev_receipt_sha256") != previous or digest != claimed:
            return {"file": "receipts.jsonl", "ok": False, "count": count, "errors": [f"receipt {count + 1} broken"]}
        previous, count = claimed, count + 1
    return {"file": "receipts.jsonl", "ok": True, "count": count}


def _verify_costs(root: Path) -> dict[str, Any]:
    db = root / "usage.sqlite3"
    chain_path = root / "cost_chain.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    if db.is_file():
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            rows = {r["event_id"]: dict(r) for r in con.execute("SELECT * FROM model_usage_events")}
        finally:
            con.close()
    chained: dict[str, str] = {}
    if chain_path.is_file():
        for raw in chain_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                entry = json.loads(raw)
                chained[str(entry.get("event_id"))] = str(entry.get("row_sha256"))
    errors = [f"usage row {k} has no cost-chain entry" for k in sorted(set(rows) - set(chained))]
    errors += [f"cost-chain entry {k} has no usage row (deleted?)" for k in sorted(set(chained) - set(rows))]
    errors += [f"usage row {k} was modified" for k in sorted(set(rows) & set(chained))
               if usage_row_digest(rows[k]) != chained[k]]
    return {"file": "usage.sqlite3", "ok": not errors, "count": len(rows), "errors": errors[:20]}


def verify(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    checks = [_verify_receipts(base)] + [verify_chained_file(base / name) for name in CHAINED_FILES] + [_verify_costs(base)]
    return {"root": str(base), "ok": all(c["ok"] for c in checks), "checks": checks,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Verify Muse Commander audit chains (receipts, audit logs, cost ledger)")
    parser.add_argument("command", choices=("verify",))
    parser.add_argument("--root", default=None, help="Commander root (default: data/commander of this project)")
    args = parser.parse_args(argv)
    if args.root:
        root = Path(args.root)
    else:
        from muse.commander_gate import commander_root
        root = commander_root()
    report = verify(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
