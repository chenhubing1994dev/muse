from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PROJECT_ROOT / "tests"

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the reviewed, isolated astronomy runtime for integration tests only.
# Muse's default Python environment remains free of optional engine dependencies.
POC_VENV = PROJECT_ROOT / "artifacts" / "ziwei-poc-venv"
OPTIONAL_SITE_PACKAGES = [
    POC_VENV / "Lib" / "site-packages",
    *POC_VENV.glob("lib/python*/site-packages"),
]
for site_packages in OPTIONAL_SITE_PACKAGES:
    if site_packages.is_dir() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))

suite = unittest.defaultTestLoader.discover(
    start_dir=str(TEST_ROOT),
    pattern="test*.py",
    top_level_dir=str(TEST_ROOT),
)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
