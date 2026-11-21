from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_usage_ledger
import test_usage_observer
import test_conversations_and_model_client
import test_providers
import test_lmstudio_usage_adapter

suite = unittest.TestSuite()
for module in (
    test_usage_ledger,
    test_usage_observer,
    test_conversations_and_model_client,
    test_providers,
    test_lmstudio_usage_adapter,
):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
