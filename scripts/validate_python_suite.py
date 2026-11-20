from __future__ import annotations

import compileall
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = PROJECT_ROOT / "tests"
for entry in (PROJECT_ROOT, SOURCE_ROOT, TEST_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

suite = unittest.defaultTestLoader.discover(
    str(TEST_ROOT), top_level_dir=str(TEST_ROOT),
)
result = unittest.TextTestRunner(verbosity=1).run(suite)
compiled = compileall.compile_dir(str(SOURCE_ROOT / "muse"), quiet=1)
print("MUSE_PYTHON_COMPILEALL_OK" if compiled else "MUSE_PYTHON_COMPILEALL_FAILED")
raise SystemExit(0 if result.wasSuccessful() and compiled else 1)
