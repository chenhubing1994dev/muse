from __future__ import annotations

import py_compile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

suite = unittest.TestSuite()
loader = unittest.TestLoader()
for pattern in (
    "test_high_value_short_handoff.py",
    "test_video_publish_package.py",
    "test_cantonese_tts.py",
    "test_chat_cli.py",
    "test_chat_cli_privacy.py",
    "test_business_opportunities.py",
    "test_status_api.py",
    "test_creative_studio_api_routes.py",
    "test_cyber_cosmos_api.py",
    "test_local_dashboard.py",
    "test_unified_initiative_ledger.py",
    "test_video_dashboard_integration.py",
    "test_video_product_api.py",
    "test_video_recipe_trace.py",
    "test_video_task_worker.py",
    "test_video_worker_reverification.py",
    "test_initiative_engine.py",
):
    suite.addTests(loader.discover(str(ROOT / "tests"), pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
for relative in (
    "scripts/muse-high-value-short.py",
    "tests/test_high_value_short_handoff.py",
    "src/muse/video_publish_package.py",
    "src/muse/video_contact_sheet.py",
    "src/muse/cantonese_tts.py",
    "src/muse/chat_cli.py",
    "src/muse/status_api.py",
    "src/muse/initiative_engine.py",
    "src/muse/video_task_worker.py",
):
    py_compile.compile(str(ROOT / relative), doraise=True)
print(f"MUSE_HIGH_VALUE_SHORT_HANDOFF_TESTS={result.testsRun}")
print("MUSE_HIGH_VALUE_SHORT_HANDOFF_COMPILE_OK")
if not result.wasSuccessful():
    raise SystemExit(1)