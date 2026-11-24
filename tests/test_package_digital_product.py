from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_digital_product import package_product


class PackageDigitalProductTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        source = root / "outputs" / "digital_products" / "starter"
        source.mkdir(parents=True)
        (source / "README.md").write_text("review draft\n", encoding="utf-8")
        (source / "FREE_CHECKLIST.md").write_text("three steps\n", encoding="utf-8")
        (source / "product-manifest.json").write_text(json.dumps({
            "status": "internal_review_draft",
            "contents": ["README.md", "FREE_CHECKLIST.md"],
            "release": {"can_publish": False},
        }), encoding="utf-8")
        return source

    def test_packages_declared_files_and_verifies_crc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            result = package_product(
                root, source_relative="outputs/digital_products/starter",
                output_relative="outputs/digital_products/starter-review.zip",
            )
            archive_path = root / result["path"]
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["README.md", "FREE_CHECKLIST.md", "product-manifest.json"])
                self.assertIsNone(archive.testzip())
            self.assertEqual(result["file_count"], 3)
            self.assertFalse(result["can_publish"])
            self.assertEqual(result["sales"], 0)

    def test_refuses_to_overwrite_existing_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            target = root / "outputs" / "digital_products" / "starter-review.zip"
            target.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                package_product(root, source_relative="outputs/digital_products/starter",
                                output_relative="outputs/digital_products/starter-review.zip")
            self.assertEqual(target.read_bytes(), b"keep")

    def test_rejects_publishable_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._fixture(root)
            manifest = json.loads((source / "product-manifest.json").read_text(encoding="utf-8"))
            manifest["release"]["can_publish"] = True
            (source / "product-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                package_product(root, source_relative="outputs/digital_products/starter",
                                output_relative="outputs/digital_products/starter-review.zip")

    def test_rejects_traversal_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._fixture(root)
            manifest = json.loads((source / "product-manifest.json").read_text(encoding="utf-8"))
            manifest["contents"].append("../outside.txt")
            (source / "product-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                package_product(root, source_relative="outputs/digital_products/starter",
                                output_relative="outputs/digital_products/starter-review.zip")

    def test_rejects_symlinked_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._fixture(root)
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                (source / "linked.txt").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this host")
            manifest = json.loads((source / "product-manifest.json").read_text(encoding="utf-8"))
            manifest["contents"].append("linked.txt")
            (source / "product-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                package_product(root, source_relative="outputs/digital_products/starter",
                                output_relative="outputs/digital_products/starter-review.zip")


if __name__ == "__main__":
    unittest.main()
