from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from muse.research_knowledge import ResearchKnowledgeStore
from muse.source_identity_cli import main


class SourceIdentityCliTests(unittest.TestCase):
    def test_cli_lists_pending_candidates_and_records_explicit_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(database) as store:
                source = store.upsert_node("source", "Mirror", external_ref="https://mirror.example/story")
                original = store.upsert_node("source", "Original", external_ref="https://origin.example/story")
                candidate = store.record_source_identity_candidate(
                    source["id"], original["id"], candidate_type="same_publisher",
                    confidence=0.8, evidence={"rule": "publisher imprint matched"},
                )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "list", "--status", "pending"]), 0)
            listed = json.loads(output.getvalue())
            self.assertEqual(listed[0]["id"], candidate["id"])
            self.assertEqual(listed[0]["status"], "pending")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(database), "review", candidate["id"],
                    "--decision", "accepted", "--by", "human-reviewer",
                    "--rationale", "Publisher imprint confirmed from the source page.",
                ]), 0)
            self.assertEqual(json.loads(output.getvalue())["decision"], "accepted")
            with ResearchKnowledgeStore(database) as store:
                self.assertEqual(len(store.list_source_identity_candidates(status="accepted")), 1)
                self.assertEqual(
                    store.connection.execute("SELECT count(*) FROM source_relationships").fetchone()[0], 1
                )

    def test_cli_records_rejection_without_creating_a_verified_relationship(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            with ResearchKnowledgeStore(database) as store:
                left = store.upsert_node("source", "Left", external_ref="https://left.example")
                right = store.upsert_node("source", "Right", external_ref="https://right.example")
                candidate = store.record_source_identity_candidate(
                    left["id"], right["id"], candidate_type="canonical_equivalent",
                    confidence=0.95, evidence={"rule": "canonical tag"},
                )
            self.assertEqual(main([
                "--db", str(database), "review", candidate["id"],
                "--decision", "rejected", "--by", "reviewer",
                "--rationale", "The canonical declaration is inconsistent.",
            ]), 0)
            with ResearchKnowledgeStore(database) as store:
                self.assertEqual(len(store.list_source_identity_candidates(status="rejected")), 1)
                self.assertEqual(
                    store.connection.execute("SELECT count(*) FROM source_relationships").fetchone()[0], 0
                )


if __name__ == "__main__":
    unittest.main()
