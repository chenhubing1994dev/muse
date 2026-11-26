from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

loader = unittest.defaultTestLoader
suite = unittest.TestSuite()
for pattern in (
    "test_business_opportunities.py",
    "test_video_product_api.py",
    "test_video_product_builder.py",
    "test_autonomous_runtime.py",
    "test_video_admission_runtime.py",
    "test_unified_runtime.py",
):
    suite.addTests(loader.discover(str(ROOT / "tests"), pattern=pattern))
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
