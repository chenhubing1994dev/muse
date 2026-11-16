from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore


class VideoAttemptLedgerTests(unittest.TestCase):
    def _initiative(self, store):
        return store.propose(
            "Video attempt initiative", kind="creative_output",
            creator_value="Track real render attempts",
            concept="Every queued render must reconcile with its delivered artifact or failure.",
            first_step="Create an attempt ledger",
            expected_output="Reconciled video attempt metrics",
        )

    def test_video_attempts_reconcile_success_and_failure_without_fake_success_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                initiative = self._initiative(store)
                store.start_video_attempt("attempt-success", mode="prompt_to_video")
                store.start_video_attempt("attempt-failure", mode="prompt_to_video")
                store.complete_video_attempt(
                    "attempt-success", initiative_id=initiative["id"],
                    product_id="product-1", version_id="version-1",
                    mp4_sha256="a" * 64, package_sha256="b" * 64,
                )
                store.fail_video_attempt(
                    "attempt-failure", failure_code="video.product_job_failed",
                    error_type="RuntimeError", initiative_id=initiative["id"],
                )
                summary = store.aggregate_video_attempt_metrics()
                self.assertEqual(summary["total"], 2)
                self.assertEqual(summary["succeeded"], 1)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(summary["pending"], 0)
                self.assertEqual(summary["success_rate"], 0.5)
                attempts = {item["attempt_id"]: item for item in store.list_video_attempts()}
                self.assertEqual(attempts["attempt-success"]["product_id"], "product-1")
                self.assertEqual(attempts["attempt-failure"]["failure_code"], "video.product_job_failed")

    def test_video_attempt_success_is_idempotent_but_conflicting_terminal_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                initiative = self._initiative(store)
                store.start_video_attempt("attempt-1", mode="local_assets")
                first = store.complete_video_attempt(
                    "attempt-1", initiative_id=initiative["id"],
                    product_id="product-1", version_id="version-1",
                    mp4_sha256="a" * 64, package_sha256="b" * 64,
                )
                second = store.complete_video_attempt(
                    "attempt-1", initiative_id=initiative["id"],
                    product_id="product-1", version_id="version-1",
                    mp4_sha256="a" * 64, package_sha256="b" * 64,
                )
                self.assertEqual(first, second)
                with self.assertRaises(ValueError):
                    store.fail_video_attempt("attempt-1", error_type="RuntimeError")

    def test_video_attempt_validation_rejects_unverified_hashes_and_duplicate_start_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("attempt-1", mode="prompt_to_video")
                with self.assertRaises(ValueError):
                    store.complete_video_attempt(
                        "attempt-1", product_id="product-1", version_id="version-1",
                        mp4_sha256="not-a-hash", package_sha256="b" * 64,
                    )
                with self.assertRaises(ValueError):
                    store.start_video_attempt("attempt-1", mode="local_assets")



    def test_attempt_keeps_initiative_and_evolution_cycle_when_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                initiative = self._initiative(store)
                cycle_id = "cycle_" + "c" * 32
                queued = store.start_video_attempt(
                    "attempt-cycle-failure", mode="prompt_to_video",
                    initiative_id=initiative["id"], evolution_cycle_id=cycle_id,
                )
                self.assertEqual(queued["evolution_cycle_id"], cycle_id)
                failed = store.fail_video_attempt(
                    "attempt-cycle-failure", failure_code="video.render_failed",
                    error_type="RuntimeError",
                )
                self.assertEqual(failed["initiative_id"], initiative["id"])
                self.assertEqual(failed["evolution_cycle_id"], cycle_id)
                listed = store.list_video_attempts(limit=10)
                self.assertEqual(listed[0]["evolution_cycle_id"], cycle_id)

    def test_success_attempt_preserves_cycle_from_queue_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                initiative = self._initiative(store)
                cycle_id = "cycle_" + "d" * 32
                store.start_video_attempt(
                    "attempt-cycle-success", mode="local_assets",
                    initiative_id=initiative["id"], evolution_cycle_id=cycle_id,
                )
                result = store.complete_video_attempt(
                    "attempt-cycle-success", initiative_id=initiative["id"],
                    product_id="product-cycle", version_id="version-cycle",
                    mp4_sha256="a" * 64, package_sha256="b" * 64,
                )
                self.assertEqual(result["evolution_cycle_id"], cycle_id)


    def test_existing_video_attempt_table_migrates_cycle_column_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("""
                CREATE TABLE initiative_video_attempts(
                    attempt_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    initiative_id TEXT,
                    product_id TEXT,
                    version_id TEXT,
                    mp4_sha256 TEXT,
                    package_sha256 TEXT,
                    failure_code TEXT,
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
            """)
            connection.execute(
                "INSERT INTO initiative_video_attempts(attempt_id,mode,status,created_at) VALUES(?,?,?,?)",
                ("legacy-attempt", "local_assets", "failed", "2026-11-12T00:00:00+00:00"),
            )
            connection.commit()
            connection.close()
            with InitiativeStore(database) as store:
                columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(initiative_video_attempts)")}
                self.assertIn("evolution_cycle_id", columns)
                legacy = store.connection.execute(
                    "SELECT * FROM initiative_video_attempts WHERE attempt_id='legacy-attempt'"
                ).fetchone()
                self.assertEqual(legacy["status"], "failed")
                self.assertEqual(legacy["evolution_cycle_id"], "")

if __name__ == "__main__":    unittest.main()