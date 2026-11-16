from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from muse.improvement_cli import main
from muse.improvement import ImprovementStore


class ImprovementCliTests(unittest.TestCase):
    def test_cli_creates_lists_and_shows_durable_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "add", "--title", "Add reviewer", "--problem", "No reviewer", "--hypothesis", "A separate reviewer catches regressions"]), 0)
            self.assertIn('"status": "proposed"', output.getvalue())
            with ImprovementStore(database) as store:
                proposals = store.list_proposals()
                proposal_id = proposals[0]["id"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "list"]), 0)
            self.assertIn(proposal_id, output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--db", str(database), "show", proposal_id]), 0)
            self.assertIn("Add reviewer", output.getvalue())

    def test_cli_records_research_source_and_approval_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            with ImprovementStore(database) as store:
                proposal = store.create_proposal("Adapter", "No adapter", "Use provider protocol")
            self.assertEqual(main(["--db", str(database), "move", proposal["id"], "researching", "--by", "researcher"]), 0)
            self.assertEqual(main(["--db", str(database), "source", proposal["id"], "--title", "Official docs", "--url", "https://example.org/docs", "--license-status", "not_applicable", "--security-status", "reviewed"]), 0)
            self.assertEqual(main(["--db", str(database), "move", proposal["id"], "ready_for_review", "--by", "reviewer"]), 0)
            self.assertEqual(main(["--db", str(database), "approve", proposal["id"], "--by", "user"]), 0)
            with ImprovementStore(database) as store:
                self.assertEqual(store.get(proposal["id"])["status"], "approved")


if __name__ == "__main__":
    unittest.main()
