from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.mempalace_lifecycle import (
    MANIFEST_NAME,
    backup_palace,
    restore_palace,
    verify_backup,
)


class MemPalaceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.palace = self.root / "synthetic-palace"
        (self.palace / "chroma").mkdir(parents=True)
        (self.palace / "metadata").mkdir()
        (self.palace / "chroma" / "records.sqlite3").write_bytes(b"synthetic sqlite bytes")
        (self.palace / "metadata" / "config.json").write_text(
            json.dumps({"track": "agent", "fixture": True}), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_backup_and_restore_verify_all_files_without_mutating_source(self) -> None:
        backup = self.root / "backups" / "palace-001"
        restored = self.root / "restored-palace"
        source_before = {
            path.relative_to(self.palace).as_posix(): path.read_bytes()
            for path in self.palace.rglob("*") if path.is_file()
        }
        result = backup_palace(self.palace, backup, quiesced=True)
        self.assertTrue(result["verified"])
        self.assertEqual(result["file_count"], 2)
        self.assertTrue((backup / MANIFEST_NAME).is_file())
        restore = restore_palace(backup, restored, quiesced=True)
        self.assertTrue(restore["verified"])
        restored_files = {
            path.relative_to(restored).as_posix(): path.read_bytes()
            for path in restored.rglob("*") if path.is_file()
        }
        self.assertEqual(restored_files, source_before)
        self.assertEqual(
            {path.relative_to(self.palace).as_posix(): path.read_bytes()
             for path in self.palace.rglob("*") if path.is_file()},
            source_before,
        )

    def test_backup_requires_explicit_quiesced_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            backup_palace(self.palace, self.root / "backup", quiesced=False)

    def test_restore_requires_explicit_quiesced_confirmation(self) -> None:
        backup = self.root / "backup"
        backup_palace(self.palace, backup, quiesced=True)
        with self.assertRaises(PermissionError):
            restore_palace(backup, self.root / "restored", quiesced=False)

    def test_backup_never_overwrites_existing_destination(self) -> None:
        backup = self.root / "backup"
        backup.mkdir()
        sentinel = backup / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            backup_palace(self.palace, backup, quiesced=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_restore_never_overwrites_existing_destination(self) -> None:
        backup = self.root / "backup"
        backup_palace(self.palace, backup, quiesced=True)
        destination = self.root / "restored"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            restore_palace(backup, destination, quiesced=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_corrupt_backup_is_rejected_before_restore(self) -> None:
        backup = self.root / "backup"
        backup_palace(self.palace, backup, quiesced=True)
        (backup / "chroma" / "records.sqlite3").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_backup(backup)
        with self.assertRaisesRegex(ValueError, "does not match"):
            restore_palace(backup, self.root / "restored", quiesced=True)
        self.assertFalse((self.root / "restored").exists())

    def test_manifest_path_traversal_is_rejected(self) -> None:
        backup = self.root / "backup"
        backup_palace(self.palace, backup, quiesced=True)
        manifest_path = backup / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"][0]["path"] = "../outside.txt"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes"):
            verify_backup(backup)

    def test_restore_preserves_nested_file_named_like_root_manifest(self) -> None:
        nested = self.palace / "metadata" / MANIFEST_NAME
        nested.write_text("nested user data", encoding="utf-8")
        backup = self.root / "backup-with-nested-manifest-name"
        restored = self.root / "restored-with-nested-manifest-name"
        backup_palace(self.palace, backup, quiesced=True)
        result = restore_palace(backup, restored, quiesced=True)
        self.assertTrue(result["verified"])
        self.assertEqual((restored / "metadata" / MANIFEST_NAME).read_text(encoding="utf-8"), "nested user data")

    def test_symlinks_are_rejected(self) -> None:
        link = self.palace / "external-link"
        try:
            link.symlink_to(self.root / "outside.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            backup_palace(self.palace, self.root / "backup", quiesced=True)


if __name__ == "__main__":
    unittest.main()
