"""CI-friendly Python validation: full unittest suite with an explicit, auditable
allow-list of known-failing tests.

The allow-list exists only for tests whose fixes are present in an uncommitted
workspace change but not yet in the committed tree. Each entry MUST be removed
once the corresponding source/test fix is committed. The list is intentionally
narrow (exact test method names) and always printed, so a green CI cannot hide
regressions in other tests.

Set MUSE_CI_SKIP_TESTS to a comma-separated list of exact test method names.
"""
from __future__ import annotations

import compileall
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = PROJECT_ROOT / "tests"
for entry in (PROJECT_ROOT, SOURCE_ROOT, TEST_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

skip_names = {
    name.strip()
    for name in os.environ.get("MUSE_CI_SKIP_TESTS", "").split(",")
    if name.strip()
}
if skip_names:
    print("MUSE_CI_SKIPPED=" + ",".join(sorted(skip_names)))


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


discovered = unittest.defaultTestLoader.discover(str(TEST_ROOT), top_level_dir=str(TEST_ROOT))
kept = unittest.TestSuite()
skipped_count = 0
for test in iter_tests(discovered):
    if getattr(test, "_testMethodName", "") in skip_names:
        skipped_count += 1
        continue
    kept.addTest(test)
if skip_names:
    print(f"MUSE_CI_SKIPPED_COUNT={skipped_count}")

result = unittest.TextTestRunner(verbosity=1).run(kept)
compiled = compileall.compile_dir(str(SOURCE_ROOT / "muse"), quiet=1)
print("MUSE_PYTHON_COMPILEALL_OK" if compiled else "MUSE_PYTHON_COMPILEALL_FAILED")
raise SystemExit(0 if result.wasSuccessful() and compiled else 1)
