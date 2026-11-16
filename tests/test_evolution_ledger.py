from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.evolution_journal import EvolutionJournal
from muse.progress import ProgressEventStore


class EvolutionLedgerTests(unittest.TestCase):
    def test_evolution_progress_category_persists_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "progress.sqlite3"
            with ProgressEventStore(database) as store:
                started = store.record("evolution", "cycle-1", "started", summary="Evolution cycle started")
                store.record("evolution", "cycle-1", "progress", summary="Research and experiment completed")
            with ProgressEventStore(database) as store:
                events = store.list_events(category="evolution", subject_id="cycle-1")
            self.assertEqual(len(events), 2)
            self.assertIn(started["id"], [event["id"] for event in events])
            self.assertEqual({event["event"] for event in events}, {"started", "progress"})

    def test_code_evolution_journal_keeps_append_only_transaction_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "evolution.sqlite3"
            progress_database = Path(directory) / "progress.sqlite3"
            journal = EvolutionJournal(database, progress_database=progress_database)
            transaction_id = journal.begin(
                task_id="task-1",
                proposal_id="proposal-1",
                files=[{
                    "relative_path": "src/example.py",
                    "original_bytes": b"VALUE = 1\n",
                    "original_sha256": "a" * 64,
                    "candidate_sha256": "b" * 64,
                }],
            )
            journal.update_status(transaction_id, "testing")
            journal.update_status(transaction_id, "reviewing")
            journal.update_status(transaction_id, "committed")

            reopened = EvolutionJournal(database)
            events = reopened.list_events(transaction_id=transaction_id)

            self.assertEqual([event["status"] for event in events],
                             ["applying", "testing", "reviewing", "committed"])
            self.assertTrue(all(event["task_id"] == "task-1" for event in events))
            self.assertTrue(all(event["proposal_id"] == "proposal-1" for event in events))
            self.assertEqual(reopened.list_events(transaction_id=transaction_id, limit=2),
                             events[-2:])
            with ProgressEventStore(progress_database) as progress:
                visible_events = progress.list_events(category="evolution", subject_id=transaction_id)
            self.assertEqual({event["event"] for event in visible_events},
                             {"started", "progress", "completed"})

    def test_evolution_journal_rejects_invalid_event_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = EvolutionJournal(Path(directory) / "evolution.sqlite3")
            with self.assertRaises(ValueError):
                journal.list_events(transaction_id="missing", limit=0)


if __name__ == "__main__":
    unittest.main()
