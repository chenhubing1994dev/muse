from __future__ import annotations

import py_compile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for pattern in (
    "test_high_value_short_handoff.py",
    "test_prometheus_teaser_handoff.py",
    "test_prometheus_film_handoff.py",
    "test_video_contact_sheet.py",
    "test_video_human_review.py",
    "test_video_recipe_trace.py",
    "test_video_product_api.py",
    "test_video_publish_package.py",
    "test_status_api.py",
):
    suite.addTests(loader.discover(str(ROOT / "tests"), pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
for relative in (
    "scripts/muse-high-value-short.py",    "scripts/muse-prometheus-teaser.py",
    "scripts/muse-prometheus-film.py",
    "tests/test_high_value_short_handoff.py",
    "tests/test_prometheus_teaser_handoff.py",
    "tests/test_prometheus_film_handoff.py",
    "src/muse/video_publish_package.py",
    "src/muse/video_contact_sheet.py",
    "src/muse/video_human_review.py",
    "src/muse/video_recipe_trace.py",
    "src/muse/status_api.py",
):
    py_compile.compile(str(ROOT / relative), doraise=True)
print(f"MUSE_NEONCLIPPING_HANDOFF_TESTS={result.testsRun}")
print("MUSE_NEONCLIPPING_HANDOFF_COMPILE_OK")
if not result.wasSuccessful():    raise SystemExit(1)