#!/usr/bin/env python3
"""Roll back one legacy-memory tag migration: removes exactly the tag rows it added; memories untouched."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from muse.memory_migration import rollback  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("migration_id")
    parser.add_argument("--db", default=str(ROOT / "data" / "memory" / "muse.sqlite3"))
    args = parser.parse_args(argv)
    report = rollback(args.db, args.migration_id, report_dir=ROOT / "reports" / "memory_migration")
    print(json.dumps({k: report.get(k) for k in ("migration_id", "rows_removed", "result", "report_path")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
