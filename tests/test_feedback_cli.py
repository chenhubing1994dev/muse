from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from muse.feedback_cli import main
from muse.improvement import ImprovementStore


class FeedbackStoreTests(unittest.TestCase):
    def test_feedback_is_allowlisted_and_aggregated_without_free_text(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            with ImprovementStore(database) as store:
                store.record_feedback("reliability", "repeated_manual_fix", severity=4)
                store.record_feedback("reliability", "repeated_manual_fix", severity=5)
                store.record_feedback("capability", "missing_tool", severity=3)
                with self.assertRaises(ValueError):
                    store.record_feedback("private_chat", "repeated_manual_fix", severity=4)
                with self.assertRaises(ValueError):
                    store.record_feedback("reliability", "arbitrary free text", severity=4)
                with self.assertRaises(ValueError):
                    store.record_feedback("reliability", "missing_tool", severity=6)
                aggregates = store.aggregate_feedback(
                    since=datetime.now(timezone.utc) - timedelta(hours=1)
                )
            repeated = next(item for item in aggregates if item["tag"] == "repeated_manual_fix")
            self.assertEqual(repeated["count"], 2)
            self.assertEqual(repeated["max_severity"], 5)
            self.assertNotIn("free text", str(aggregates))

    def test_feedback_cli_adds_and_lists_structured_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "improvements.sqlite3"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main([
                    "--db", str(database), "add", "--category", "usability",
                    "--tag", "workflow_gap", "--severity", "4",
                ])
            saved = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(saved["tag"], "workflow_gap")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["--db", str(database), "list", "--since-hours", "24"])
            listed = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["severity"], 4)


if __name__ == "__main__":
    unittest.main()
