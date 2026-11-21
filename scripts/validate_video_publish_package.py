from __future__ import annotations

import sys
import py_compile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PROJECT_ROOT / "tests"
for entry in (PROJECT_ROOT, PROJECT_ROOT / "src", TEST_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

for source in (
    PROJECT_ROOT / "src" / "muse" / "video_publish_package.py",
    PROJECT_ROOT / "src" / "muse" / "video_human_review.py",
    PROJECT_ROOT / "scripts" / "muse-inspiration-flagship-video.py",
):
    py_compile.compile(str(source), doraise=True)
print("MUSE_NEONCLIPPING_PIPELINE_COMPILE_OK")

suite = unittest.defaultTestLoader.discover(
    str(TEST_ROOT), pattern="test_video_publish_package.py", top_level_dir=str(TEST_ROOT),
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
