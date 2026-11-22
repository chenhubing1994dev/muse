from __future__ import annotations

import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
import socket
from unittest.mock import patch

from muse.capabilities import CapabilityRegistry
from muse.skill_fusion import FusionError, SkillFusionService, _download_repository, _validate_public_host


def make_archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class SkillFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.capability_db = self.root / "data" / "capabilities.sqlite3"
        self.service = SkillFusionService(self.root, self.capability_db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_submission_extracts_capabilities_and_maps_existing_muse_skills(self) -> None:
        with CapabilityRegistry(self.capability_db) as registry:
            registry.register(
                capability_id="research.scrapling", domain="research", name="Scrapling adapter",
                description="Existing web extraction capability", status="ready",
                capabilities=("web_research",), trust="local_verified", license="MIT",
            )
        archive = make_archive({
            "demo/README.md": "# Demo\nA browser research and crawler toolkit.",
            "demo/pyproject.toml": "[project]\nname='demo'\n",
            "demo/src/crawler.py": "class Crawler: pass\n",
        })
        result = self.service.submit(archive_base64=__import__("base64").b64encode(archive).decode(), file_name="demo.zip")
        fusion = result["fusion"]
        self.assertEqual(fusion["status"], "integrated_unverified")
        self.assertEqual(fusion["analysis"]["capabilities"][0]["id"], "web_research")
        self.assertTrue(fusion["analysis"]["integration_edges"])
        self.assertFalse(fusion["safety"]["external_code_executed"])
        self.assertFalse(fusion["safety"]["dependencies_installed"])
        self.assertTrue(fusion["safety"]["archive_retained"])
        self.assertTrue(fusion["registered_capability_ids"])
        with CapabilityRegistry(self.capability_db) as registry:
            fused = [registry.get(item) for item in fusion["registered_capability_ids"]]
        self.assertTrue(all(item["status"] == "unverified" for item in fused))
        self.assertTrue(all(item["executable"] is None for item in fused))
        self.assertTrue(all(item["metadata"]["integration_edges"] is not None for item in fused))
        self.assertEqual(fusion["source"]["sha256"], __import__("hashlib").sha256(archive).hexdigest())

    def test_duplicate_archive_returns_existing_fusion_without_duplicate_record(self) -> None:
        archive = make_archive({"demo/README.md": "# Plain docs"})
        import base64
        encoded = base64.b64encode(archive).decode()
        first = self.service.submit(archive_base64=encoded)
        second = self.service.submit(archive_base64=encoded)
        self.assertEqual(first["fusion"]["fusion_id"], second["fusion"]["fusion_id"])
        self.assertTrue(second["fusion"]["duplicate"])
        self.assertEqual(len(self.service.list_recent()), 1)

    def test_archive_path_traversal_is_rejected(self) -> None:
        archive = make_archive({"../escape.py": "print('unsafe')"})
        import base64
        with self.assertRaisesRegex(FusionError, "unsafe path"):
            self.service.submit(archive_base64=base64.b64encode(archive).decode())

    def test_archive_symlink_is_rejected(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            info = zipfile.ZipInfo("demo/link.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target.py")
        import base64
        with self.assertRaisesRegex(FusionError, "unsafe path"):
            self.service.submit(archive_base64=base64.b64encode(output.getvalue()).decode())

    def test_approval_adds_only_unverified_metadata_to_existing_registry(self) -> None:
        archive = make_archive({"demo/src/video_editor.py": "def edit_video(): pass"})
        import base64
        result = self.service.submit(archive_base64=base64.b64encode(archive).decode())
        fusion_id = result["fusion"]["fusion_id"]
        approved = self.service.approve(fusion_id, approved_by="local-user")["fusion"]
        self.assertEqual(approved["status"], "approved_unverified")
        self.assertEqual(approved["activation"], "blocked_until_tests_and_trust_review")
        with CapabilityRegistry(self.capability_db) as registry:
            entries = [registry.get(item) for item in approved["registered_capability_ids"]]
        self.assertTrue(entries)
        self.assertTrue(all(item["status"] == "unverified" for item in entries))
        self.assertTrue(all(item["executable"] is None for item in entries))
        self.assertTrue(all(item["metadata"]["execution_policy"] == "never_execute_untrusted_source" for item in entries))
        rolled_back = self.service.rollback(fusion_id, rolled_back_by="local-user")["fusion"]
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual(rolled_back["activation"], "rolled_back_disabled")
        with CapabilityRegistry(self.capability_db) as registry:
            disabled = [registry.get(item) for item in approved["registered_capability_ids"]]
        self.assertTrue(all(item["status"] == "disabled" for item in disabled))
        self.assertTrue(all(item["metadata"]["execution_policy"] == "disabled_by_fusion_rollback" for item in disabled))
        self.assertTrue((self.root / "data" / "fusion_sources" / f"{fusion_id}.zip").is_file())

    def test_approval_rejects_unknown_submission(self) -> None:
        with self.assertRaisesRegex(FusionError, "not found"):
            self.service.approve("f" * 32, approved_by="local-user")

    def test_url_requires_https_and_allowlisted_public_host(self) -> None:
        with self.assertRaisesRegex(FusionError, "HTTPS"):
            _download_repository("http://github.com/openai/example")
        with self.assertRaisesRegex(FusionError, "allowlist"):
            _download_repository("https://example.com/owner/repo")

    def test_allowlisted_source_host_is_rejected_when_dns_resolves_private(self) -> None:
        private_record = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("muse.skill_fusion.socket.getaddrinfo", return_value=private_record):
            with self.assertRaisesRegex(FusionError, "non-public"):
                _validate_public_host("github.com")

    def test_github_repository_url_fetches_default_branch_archive_only(self) -> None:
        archive = make_archive({"demo/README.md": "# source"})
        calls = []
        def fake_fetch(url: str, *, max_bytes: int) -> bytes:
            calls.append(url)
            if "api.github.com" in url:
                return json.dumps({"default_branch": "main"}).encode()
            return archive
        with patch("muse.skill_fusion._validate_public_host"), patch("muse.skill_fusion._fetch_https", side_effect=fake_fetch):
            downloaded, label = _download_repository("https://github.com/example/project")
        self.assertEqual(downloaded, archive)
        self.assertEqual(label, "github.com/example/project")
        self.assertIn("refs/heads/main", calls[-1])

    def test_invalid_base64_is_rejected(self) -> None:
        with self.assertRaisesRegex(FusionError, "invalid"):
            self.service.submit(archive_base64="not base64!")

if __name__ == "__main__":
    unittest.main()
