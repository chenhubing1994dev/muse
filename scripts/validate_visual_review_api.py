from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_video_human_review import VisualHumanReviewTests
from test_status_api import StatusApiTests

suite = unittest.TestSuite()
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(VisualHumanReviewTests))
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(StatusApiTests))
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
