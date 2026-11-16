"""Explicit structured feedback intake for Muse capability discovery."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from muse.improvement import ImprovementStore, _FEEDBACK_CATEGORIES, _FEEDBACK_TAGS


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record allowlisted user feedback labels for Muse capability discovery"
    )
    parser.add_argument("--db", default="data/improvements/muse.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="record one explicit structured feedback label")
    add.add_argument("--category", required=True, choices=sorted(_FEEDBACK_CATEGORIES))
    add.add_argument("--tag", required=True, choices=sorted(_FEEDBACK_TAGS))
    add.add_argument("--severity", type=int, choices=range(1, 6), default=3)

    listing = commands.add_parser("list", help="list recent structured feedback labels")
    listing.add_argument("--since-hours", type=int, default=24)
    listing.add_argument("--limit", type=int, default=50)

    args = parser.parse_args(argv)
    try:
        with ImprovementStore(Path(args.db)) as store:
            if args.command == "add":
                result = store.record_feedback(
                    args.category, args.tag, severity=args.severity
                )
            else:
                if args.since_hours <= 0:
                    parser.error("--since-hours must be positive")
                if not 1 <= args.limit <= 200:
                    parser.error("--limit must be between 1 and 200")
                since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
                result = store.list_feedback(since=since, limit=args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
