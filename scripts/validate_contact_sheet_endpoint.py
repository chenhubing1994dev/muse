from __future__ import annotations
import pathlib, sys, unittest
root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))
from test_status_api import StatusApiTests
suite = unittest.TestSuite([StatusApiTests("test_contact_sheet_endpoint_serves_only_hash_verified_release_evidence")])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
