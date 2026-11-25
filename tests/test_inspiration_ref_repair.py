"""WS0: read-only audit + backup-verified non-destructive repair of temp-path inspiration_refs."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.inspiration_ref_repair import (
    MISSING_PREFIX,
    RepairRefused,
    apply_repair,
    audit,
    is_temp_ref,
)

# Mirrors the production creator_initiatives DDL from muse.initiative_engine.InitiativeStore.
INITIATIVES_DDL = """
CREATE TABLE creator_initiatives(
    id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, creator_value TEXT NOT NULL,
    concept TEXT NOT NULL, first_step TEXT NOT NULL, expected_output TEXT NOT NULL,
    interest_signals_json TEXT NOT NULL DEFAULT '[]', inspiration_refs_json TEXT NOT NULL DEFAULT '[]',
    capability_gap TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 50,
    autonomy_level TEXT NOT NULL DEFAULT 'prototype', status TEXT NOT NULL DEFAULT 'proposed',
    outcome TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(title,kind)
);
"""
TEMP_A = r"C:\Users\example\AppData\Local\Temp\tmpz5l8lj2e\reports\autonomous-research-agent-orchestration-20261108T121509214717_0000.md"
TEMP_B = r"C:\Users\example\AppData\Local\Temp\tmp0_q64wbb\reports\autonomous-research-symbolic-hypothesis-review-46-20261108T121729477889_0000.md"
STABLE_NAME = "autonomous-research-stable-20261120.md"
TEMP_C = r"C:\Users\example\AppData\Local\Temp\tmpabcdef12\reports" + "\\" + STABLE_NAME


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InspirationRefRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "initiatives.sqlite3"
        self.reports = self.root / "reports"
        self.reports.mkdir()
        (self.reports / STABLE_NAME).write_text("# stable report\n", encoding="utf-8")
        connection = sqlite3.connect(self.db)
        connection.executescript(INITIATIVES_DDL)
        rows = [
            ("i1", "创意机会：Creator-inspired test hypothesis｜1a", [TEMP_A], "proposed"),
            ("i2", "创意机会：Symbolic candidate: scenario branch", [TEMP_B, "research:topic-x"], "testing"),
            ("i3", "Stable research initiative", ["data\\research\\reports\\autonomous-research-ok.md"], "proposed"),
            ("i4", "Stable mapping candidate", [TEMP_C], "proposed"),
        ]
        for initiative_id, title, refs, status in rows:
            connection.execute(
                "INSERT INTO creator_initiatives(id,title,kind,creator_value,concept,first_step,expected_output,"
                "inspiration_refs_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (initiative_id, title, "tool", "v", "{}", "s", "o", json.dumps(refs), status,
                 "2026-11-21T00:00:00+00:00", "2026-11-21T00:00:00+00:00"),
            )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _refs(self):
        connection = sqlite3.connect(self.db)
        try:
            return {row[0]: json.loads(row[1]) for row in connection.execute(
                "SELECT id,inspiration_refs_json FROM creator_initiatives")}
        finally:
            connection.close()

    def test_temp_ref_detection(self):
        self.assertTrue(is_temp_ref(TEMP_A))
        self.assertTrue(is_temp_ref("/tmp/pytest-1/reports/a.md"))
        self.assertFalse(is_temp_ref("data\\research\\reports\\autonomous-research-ok.md"))
        self.assertFalse(is_temp_ref("research:topic-x"))
        self.assertFalse(is_temp_ref(None))

    def test_audit_is_read_only_and_reports_exact_counts(self):
        before = _sha(self.db)
        report = audit(self.db, reports_dir=self.reports, work_dir=self.root / "scratch")
        self.assertEqual(_sha(self.db), before)
        self.assertTrue(report["source_unchanged"])
        self.assertEqual(report["integrity_check"], "ok")
        summary = report["summary"]
        self.assertEqual(summary["temp_refs"], 3)
        self.assertEqual(summary["initiatives_with_temp_refs"], 3)
        self.assertEqual(summary["mapped_to_stable_report"], 1)
        self.assertEqual(summary["marked_missing"], 2)
        self.assertEqual(summary["test_like_titles"], 2)
        self.assertEqual(self._refs()["i1"], [TEMP_A])  # untouched

    def test_apply_on_disposable_copy_rewrites_records_originals_and_backs_up(self):
        report = apply_repair(self.db, reports_dir=self.reports, backup_dir=self.root / "backups")
        self.assertEqual(report["rows_rewritten"], 3)
        self.assertEqual(report["integrity_check_after"], "ok")
        self.assertNotEqual(report["db_sha256_before"], report["db_sha256_after"])
        backup = Path(report["backup_path"])
        self.assertEqual(_sha(backup), report["backup_sha256"])
        self.assertEqual(report["backup_sha256"], report["db_sha256_before"])  # backup is a faithful pre-image
        refs = self._refs()
        self.assertEqual(refs["i1"], [MISSING_PREFIX + TEMP_A.split("\\")[-1]])
        self.assertEqual(refs["i2"], [MISSING_PREFIX + TEMP_B.split("\\")[-1], "research:topic-x"])
        self.assertEqual(refs["i3"], ["data\\research\\reports\\autonomous-research-ok.md"])
        self.assertEqual(refs["i4"], [f"data/research/reports/{STABLE_NAME}"])
        connection = sqlite3.connect(self.db)
        try:
            originals = {row[0] for row in connection.execute("SELECT old_ref FROM inspiration_ref_repairs")}
            self.assertEqual(originals, {TEMP_A, TEMP_B, TEMP_C})
            self.assertEqual(connection.execute("SELECT count(*) FROM creator_initiatives").fetchone()[0], 4)
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM inspiration_ref_repairs")
        finally:
            connection.close()

    def test_apply_is_idempotent(self):
        apply_repair(self.db, reports_dir=self.reports, backup_dir=self.root / "b1")
        first = self._refs()
        second = apply_repair(self.db, reports_dir=self.reports, backup_dir=self.root / "b2")
        self.assertEqual(second["rows_rewritten"], 0)
        self.assertEqual(self._refs(), first)

    def test_dry_run_writes_nothing(self):
        before = _sha(self.db)
        report = apply_repair(self.db, reports_dir=self.reports, dry_run=True)
        self.assertEqual(report["planned_rows"], 3)
        self.assertEqual(_sha(self.db), before)
        self.assertNotIn("backup_path", report)

    def test_production_db_requires_explicit_flag_and_exact_sha(self):
        with self.assertRaisesRegex(RepairRefused, "live production DB"):
            apply_repair(self.db, production_database=self.db)
        with self.assertRaisesRegex(RepairRefused, "does not match"):
            apply_repair(self.db, production_database=self.db, allow_production=True, confirm_sha256="0" * 64)
        self.assertEqual(self._refs()["i1"], [TEMP_A])
        report = apply_repair(self.db, production_database=self.db, allow_production=True,
                              confirm_sha256=_sha(self.db), reports_dir=self.reports, backup_dir=self.root / "pb")
        self.assertEqual(report["rows_rewritten"], 3)

    def test_non_empty_wal_sidecar_refuses_repair(self):
        Path(str(self.db) + "-wal").write_bytes(b"pending frames")
        with self.assertRaisesRegex(RepairRefused, "non-empty"):
            apply_repair(self.db, reports_dir=self.reports)
        self.assertEqual(self._refs()["i1"], [TEMP_A])

    def test_mid_transaction_failure_rolls_back_every_row_and_the_repair_table(self):
        connection = sqlite3.connect(self.db)
        connection.execute("""CREATE TRIGGER crash_on_last BEFORE UPDATE ON creator_initiatives
                              WHEN old.id='i4' BEGIN SELECT RAISE(ABORT,'simulated crash'); END""")
        connection.commit()
        connection.close()
        with self.assertRaises(sqlite3.DatabaseError):
            apply_repair(self.db, reports_dir=self.reports, backup_dir=self.root / "crash")
        refs = self._refs()
        self.assertEqual(refs["i1"], [TEMP_A])  # earlier rows rolled back atomically
        self.assertEqual(refs["i2"][0], TEMP_B)
        connection = sqlite3.connect(self.db)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertNotIn("inspiration_ref_repairs", tables)
        self.assertEqual(len(list((self.root / "crash").iterdir())), 1)  # verified backup still kept


if __name__ == "__main__":
    unittest.main()
