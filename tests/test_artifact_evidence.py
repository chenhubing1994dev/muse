from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from muse.artifact_evidence import ArtifactEvidenceError, LocalArtifactEvidenceVerifier


class LocalArtifactEvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "deliverable.txt"
        self.content = b"Muse verified deliverable\n"
        self.file.write_bytes(self.content)
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.verifier = LocalArtifactEvidenceVerifier(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_verifies_regular_file_and_returns_stable_evidence_reference(self):
        result = self.verifier.verify("deliverable.txt", self.digest, expected_bytes=len(self.content))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["sha256"], self.digest)
        self.assertEqual(result["bytes"], len(self.content))
        self.assertEqual(result["evidence_ref"], f"artifact:deliverable.txt#sha256={self.digest}")

    def test_rejects_hash_or_size_mismatch(self):
        with self.assertRaisesRegex(ArtifactEvidenceError, "SHA-256"):
            self.verifier.verify("deliverable.txt", "0" * 64)
        with self.assertRaisesRegex(ArtifactEvidenceError, "byte count"):
            self.verifier.verify("deliverable.txt", self.digest, expected_bytes=len(self.content) + 1)

    def test_rejects_absolute_traversal_and_noncanonical_paths(self):
        for path in ("../outside.txt", "/etc/passwd", "folder/../deliverable.txt", ".\\deliverable.txt", "folder//file"):
            with self.subTest(path=path), self.assertRaises(ArtifactEvidenceError):
                self.verifier.verify(path, self.digest)

    def test_rejects_symlinked_artifact_and_parent(self):
        outside = self.root.parent / (self.root.name + "-outside.txt")
        outside.write_bytes(self.content)
        try:
            link = self.root / "linked.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available in this environment")
            with self.assertRaisesRegex(ArtifactEvidenceError, "symlink"):
                self.verifier.verify("linked.txt", self.digest)
            nested = self.root / "linked-dir"
            nested.symlink_to(outside.parent, target_is_directory=True)
            with self.assertRaisesRegex(ArtifactEvidenceError, "symlink"):
                self.verifier.verify("linked-dir" + "/" + outside.name, self.digest)
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_directories_and_empty_files(self):
        (self.root / "subdir").mkdir()
        (self.root / "empty.txt").write_bytes(b"")
        with self.assertRaises(ArtifactEvidenceError):
            self.verifier.verify("subdir", self.digest)
        with self.assertRaises(ArtifactEvidenceError):
            self.verifier.verify("empty.txt", self.digest)

    def test_rejects_file_larger_than_configured_limit(self):
        small_limit = LocalArtifactEvidenceVerifier(self.root, max_bytes=4)
        with self.assertRaisesRegex(ArtifactEvidenceError, "size"):
            small_limit.verify("deliverable.txt", self.digest)

    def test_rejects_symlinked_root(self):
        link = self.root.parent / (self.root.name + "-root-link")
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available in this environment")
        try:
            with self.assertRaisesRegex(ArtifactEvidenceError, "root"):
                LocalArtifactEvidenceVerifier(link)
        finally:
            link.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
