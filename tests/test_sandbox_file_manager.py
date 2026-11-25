from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.sandbox_file_manager import ApprovalAuthority, SandboxFileManager, SandboxPathError


class SandboxFileManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "sandbox"
        self.authority = ApprovalAuthority()
        self.manager = SandboxFileManager(self.root, approval_authority=self.authority)
        self.payload = "Muse sandbox verification\n"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def grant(self, action: str, target: str):
        return self.authority.issue(action, target)

    def test_create_write_read_hash_compare_delete_closed_loop(self) -> None:
        self.assertTrue(self.manager.create_folder("test", approval=self.grant("create_folder", "test"))["success"])
        written = self.manager.write_file("test/hello.txt", self.payload, approval=self.grant("write_file", "test/hello.txt"))
        self.assertTrue(written["success"])
        readback = self.manager.read_file("test/hello.txt")
        self.assertEqual(readback["evidence"]["content"], self.payload)
        digest = hashlib.sha256(self.payload.encode()).hexdigest()
        self.assertEqual(self.manager.sha256("test/hello.txt")["evidence"]["sha256"], digest)
        self.assertTrue(self.manager.compare_content("test/hello.txt", self.payload)["success"])
        deleted = self.manager.delete_file("test/hello.txt", approval=self.grant("delete_file", "test/hello.txt"), second_approval=self.grant("delete_file", "test/hello.txt"))
        self.assertTrue(deleted["success"])
        self.assertFalse((self.root / "test/hello.txt").exists())

    def test_missing_wrong_scope_forged_and_replayed_grants_are_denied(self) -> None:
        missing = self.manager.write_file("denied.txt", "no")
        self.assertFalse(missing["success"])
        self.assertFalse(missing["approved"])
        wrong_scope = self.manager.write_file("denied.txt", "no", approval=self.grant("write_file", "other.txt"))
        self.assertFalse(wrong_scope["success"])
        grant = self.grant("write_file", "once.txt")
        self.assertTrue(self.manager.write_file("once.txt", "one", approval=grant)["success"])
        replay = self.manager.write_file("once-more.txt", "two", approval=grant)
        self.assertFalse(replay["success"])
        self.assertFalse((self.root / "once-more.txt").exists())
        foreign = ApprovalAuthority().issue("write_file", "foreign.txt")
        self.assertFalse(self.manager.write_file("foreign.txt", "no", approval=foreign)["success"])

    def test_traversal_absolute_windows_and_symlink_escape_are_rejected(self) -> None:
        for path in ("../outside.txt", "/etc/passwd", "C:/outside.txt", r"folder\\escape.txt"):
            with self.subTest(path=path), self.assertRaises(SandboxPathError):
                self.manager._target(path)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(SandboxPathError):
            self.manager._target("escape/secret.txt")

    def test_delete_requires_two_distinct_grants_and_keeps_target(self) -> None:
        self.assertTrue(self.manager.write_file("keep.txt", "keep", approval=self.grant("write_file", "keep.txt"))["success"])
        result = self.manager.delete_file("keep.txt", approval=self.grant("delete_file", "keep.txt"))
        self.assertFalse(result["success"])
        self.assertTrue((self.root / "keep.txt").exists())

    def test_overwrite_is_refused_even_with_fresh_grant(self) -> None:
        self.assertTrue(self.manager.write_file("same.txt", "one", approval=self.grant("write_file", "same.txt"))["success"])
        result = self.manager.write_file("same.txt", "two", approval=self.grant("write_file", "same.txt"))
        self.assertFalse(result["success"])
        self.assertEqual((self.root / "same.txt").read_text(encoding="utf-8"), "one")

    def test_missing_file_and_content_mismatch_are_explicit(self) -> None:
        missing = self.manager.read_file("missing.txt")
        self.assertFalse(missing["success"])
        self.assertIn("SandboxPathError", missing["error"])
        self.assertTrue(self.manager.write_file("value.txt", "actual", approval=self.grant("write_file", "value.txt"))["success"])
        mismatch = self.manager.compare_content("value.txt", "expected")
        self.assertFalse(mismatch["success"])
        self.assertFalse(mismatch["evidence"]["matches"])

    def test_failed_readback_rolls_back_new_file(self) -> None:
        with patch.object(self.manager, "read_file", return_value={"success": False, "evidence": {}, "error": "simulated readback failure"}):
            result = self.manager.write_file("rollback.txt", "must be rolled back", approval=self.grant("write_file", "rollback.txt"))
        self.assertFalse(result["success"])
        self.assertIn("VerificationError", result["error"])
        self.assertFalse((self.root / "rollback.txt").exists())

    def test_audit_log_hash_chain_verifies_and_detects_tampering(self) -> None:
        self.assertTrue(self.manager.create_folder("logged", approval=self.grant("create_folder", "logged"))["success"])
        event = json.loads(self.manager.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(event["action"], "create_folder")
        self.assertIn("timestamp", event)
        self.assertTrue(event["approved"])
        self.assertEqual(event["prev_hash"], "0" * 64)
        self.assertTrue(self.manager.verify_audit_log()["valid"])
        event["target"] = "rewritten"
        self.manager.log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        self.assertFalse(self.manager.verify_audit_log()["valid"])
        with self.assertRaises(Exception):
            self.manager.read_file("absent.txt")


if __name__ == "__main__":
    unittest.main()
