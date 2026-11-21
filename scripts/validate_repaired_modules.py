from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_agent_discovery import AgentDiscoveryTests
from test_specialist_router import SpecialistRouterTests
from test_unified_runtime import UnifiedRuntimeTests
from test_video_recipe_trace import VideoRecipeTraceTests
from test_video_publish_package import VideoPublishPackageTests
from test_video_human_review import VisualHumanReviewTests
from test_status_api import StatusApiTests
from test_worker import WorkerTests

suite = unittest.TestSuite()
for test_case in (
    AgentDiscoveryTests,
    SpecialistRouterTests,
    UnifiedRuntimeTests,
    VideoRecipeTraceTests,
    VideoPublishPackageTests,
    VisualHumanReviewTests,
    StatusApiTests,
    WorkerTests,
):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(test_case))
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
