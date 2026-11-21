from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src", ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

MODULES = (
    "test_task_queue",
    "test_worker",
    "test_contextual_inquiry_security",
    "test_contextual_inquiry_bridge",
    "test_contextual_inquiry_cli",
    "test_autonomous_runtime",
    "test_contextual_inquiry_local_review",
    "test_conversations_and_model_client",
    "test_model_call_context",
)

loader = unittest.defaultTestLoader
suite = unittest.TestSuite()
for module in MODULES:
    suite.addTests(loader.loadTestsFromName(module))

result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
