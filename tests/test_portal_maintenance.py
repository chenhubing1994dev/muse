from __future__ import annotations

import io
from contextlib import redirect_stdout
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.portal_maintenance import (
    PortalMaintenanceError,
    backup_databases,
    restore_database,
    main as maintenance_main,
)


class PortalMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_online_backup_captures_wal_snapshot_and_writes_manifest(self) -> None:
        source = self.root / "sessions.sqlite3"
        writer = sqlite3.connect(source)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE records(value TEXT NOT NULL)")
            writer.execute("INSERT INTO records(value) VALUES('before-backup')")
            writer.commit()

            result = backup_databases([source], self.root / "backups")
            record = result["backups"][0]
            backup = Path(record["backup_file"])
            self.assertTrue(backup.is_file())
            self.assertEqual(len(record["sha256"]), 64)
            restored = sqlite3.connect(backup)
            try:
                values = [row[0] for row in restored.execute("SELECT value FROM records")]
                self.assertEqual(values, ["before-backup"])
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                restored.close()

            writer.execute("INSERT INTO records(value) VALUES('after-backup')")
            writer.commit()
            snapshot = sqlite3.connect(backup)
            try:
                values = [row[0] for row in snapshot.execute("SELECT value FROM records")]
                self.assertEqual(values, ["before-backup"])
            finally:
                snapshot.close()

            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["backups"][0]["sha256"], record["sha256"])
        finally:
            writer.close()

    def test_restore_creates_new_verified_database_and_refuses_overwrite(self) -> None:
        source = self.root / "taskqueue.sqlite3"
        source_connection = sqlite3.connect(source)
        try:
            source_connection.execute("CREATE TABLE tasks(title TEXT NOT NULL)")
            source_connection.execute("INSERT INTO tasks(title) VALUES('review')")
            source_connection.commit()
        finally:
            source_connection.close()
        result = backup_databases([source], self.root / "backups")
        record = result["backups"][0]
        destination = self.root / "restored" / "taskqueue.sqlite3"
        with self.assertRaisesRegex(PortalMaintenanceError, "SHA-256"):
            restore_database(record["backup_file"], destination, expected_sha256="0" * 64)
        self.assertFalse(destination.exists())
        restored = restore_database(record["backup_file"], destination, expected_sha256=record["sha256"])
        self.assertEqual(restored["sha256"], record["sha256"])
        connection = sqlite3.connect(destination)
        try:
            self.assertEqual(connection.execute("SELECT title FROM tasks").fetchone()[0], "review")
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            connection.close()
        with self.assertRaises(PortalMaintenanceError):
            restore_database(record["backup_file"], destination, expected_sha256=record["sha256"])

    def test_cli_backup_and_restore_require_and_use_manifest_sha256(self) -> None:
        source = self.root / "cli-source.sqlite3"
        connection = sqlite3.connect(source)
        try:
            connection.execute("CREATE TABLE facts(value TEXT)")
            connection.execute("INSERT INTO facts(value) VALUES('verified')")
            connection.commit()
        finally:
            connection.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(maintenance_main([
                "backup", "--source-db", str(source), "--backup-dir", str(self.root / "cli-backups"),
            ]), 0)
        backup_result = json.loads(output.getvalue())
        record = backup_result["backups"][0]
        destination = self.root / "cli-restore" / "restored.sqlite3"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(maintenance_main([
                "restore", "--backup-file", record["backup_file"], "--destination", str(destination),
                "--expected-sha256", record["sha256"],
            ]), 0)
        self.assertTrue(destination.is_file())

    def test_backup_rejects_missing_duplicate_and_corrupt_sources(self) -> None:
        with self.assertRaises(PortalMaintenanceError):
            backup_databases([self.root / "missing.sqlite3"], self.root / "backups")
        source = self.root / "duplicate.sqlite3"
        source_connection = sqlite3.connect(source)
        try:
            source_connection.execute("CREATE TABLE data(value INTEGER)")
        finally:
            source_connection.close()
        with self.assertRaises(PortalMaintenanceError):
            backup_databases([source, source], self.root / "backups")
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        with self.assertRaises(sqlite3.DatabaseError):
            backup_databases([corrupt], self.root / "backups")


if __name__ == "__main__":
    unittest.main()
