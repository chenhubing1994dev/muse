#!/usr/bin/env python3
"""Review legacy_unverified User Track memories (only a user:<name> reviewer is accepted).

Usage:  python scripts/review_legacy_memory.py list                                   # write the review checklist
        python scripts/review_legacy_memory.py confirm --id <memory_id> --by user:<name> [--tag fact]
        python scripts/review_legacy_memory.py revert --review-id <review_id> --by user:<name>
Confirming backs up the store first, changes only the epistemic stamp (original text untouched) and is reversible.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from muse.legacy_memory_review import ReviewerNotHuman, confirm, revert, write_checklist  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "memory" / "muse.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="write the checklist of memories still legacy_unverified")
    c = sub.add_parser("confirm", help="confirm one legacy memory as user_confirmed (user:<name> only)")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--by", required=True)
    c.add_argument("--tag", default="fact", help="fact|observation|experience|interpretation|hypothesis")
    r = sub.add_parser("revert", help="undo one confirmation (user:<name> only)")
    r.add_argument("--review-id", required=True)
    r.add_argument("--by", required=True)
    args = parser.parse_args(argv)
    reports = ROOT / "reports" / "memory_migration"
    try:
        if args.command == "list":
            out = write_checklist(args.db, reports)
        elif args.command == "confirm":
            out = confirm(args.db, args.id, reviewer=args.by, tag=args.tag, backup_dir=ROOT / "backups" / "memory",
                          report_dir=reports)
            out = {k: out.get(k) for k in ("action", "memory_id", "review_id", "result", "report_path", "backup_path")}
        else:
            out = revert(args.db, args.review_id, reviewer=args.by, report_dir=reports)
            out = {k: out.get(k) for k in ("action", "memory_id", "review_id", "result", "report_path")}
    except ReviewerNotHuman as exc:
        print(json.dumps({"error": "reviewer_not_user", "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
