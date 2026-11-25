"""Audit (read-only, default) or repair temp-path inspiration_refs in the initiatives store.

Examples (PowerShell, from the Muse project root):
  .venv\\Scripts\\python.exe scripts\\audit_and_repair_inspiration_refs.py
  .venv\\Scripts\\python.exe scripts\\audit_and_repair_inspiration_refs.py --apply-on-db tmp\\clone.sqlite3 --dry-run
  .venv\\Scripts\\python.exe scripts\\audit_and_repair_inspiration_refs.py --apply-on-db data\\initiatives\\muse.sqlite3 ^
      --allow-production --confirm-sha256 <current sha256 printed by the audit>

Implementation and tests: src/muse/inspiration_ref_repair.py, tests/test_inspiration_ref_repair.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muse.inspiration_ref_repair import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
