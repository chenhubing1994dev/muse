"""Read-only CLI for recent Muse progress events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from muse.progress import CATEGORIES, ProgressEventStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect structured Muse progress")
    parser.add_argument("--db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--category", choices=sorted(CATEGORIES))
    parser.add_argument("--subject")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        with ProgressEventStore(Path(args.db)) as store:
            events = store.list_events(
                category=args.category, subject_id=args.subject, limit=args.limit
            )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
