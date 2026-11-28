#!/usr/bin/env python3
"""Tag legacy User Track memories ``legacy_unverified`` (backup first, idempotent, content untouched).

Usage:  python scripts/migrate_legacy_memory.py            # dry-run report only
        python scripts/migrate_legacy_memory.py --apply    # backup + add tag rows
Rollback: python scripts/rollback_legacy_memory.py <migration_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from muse.memory_migration import migrate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "memory" / "muse.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = migrate(args.db, backup_dir=ROOT / "backups" / "memory", report_dir=ROOT / "reports" / "memory_migration",
                     apply=args.apply)
    print(json.dumps({k: report.get(k) for k in ("action", "migration_id", "rows_added", "result", "report_path", "backup_path")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
