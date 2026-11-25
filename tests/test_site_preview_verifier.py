from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.verify_site_preview import verify_site_preview

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "site_launch_preview_20261125_v2"


class SitePreviewVerifierTests(unittest.TestCase):
    def test_current_preview_passes_manifest_zip_and_release_gate_checks(self):
        result = verify_site_preview(SOURCE)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["assets_verified"], 12)
        self.assertFalse(result["public_deployment_allowed"])
        self.assertFalse(result["payments_enabled"])

    def test_rejects_tampered_site_asset(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            copy = Path(temp) / "preview"
            shutil.copytree(SOURCE, copy)
            (copy / "products.html").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                verify_site_preview(copy)

    def test_rejects_open_public_deployment_gate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as temp:
            copy = Path(temp) / "preview"
            shutil.copytree(SOURCE, copy)
            manifest_path = copy / "site-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["public_deployment_allowed"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "public_deployment_allowed"):
                verify_site_preview(copy)

    def test_rejects_site_root_outside_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "inside outputs"):
                verify_site_preview(Path(temp))


if __name__ == "__main__":
    unittest.main()
