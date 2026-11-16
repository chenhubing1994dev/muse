from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.handoff import build_resume_prompt, write_resume_prompt


class HandoffTests(unittest.TestCase):
    def test_prompt_contains_latest_project_context_and_resume_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress = root / "PROGRESS.md"
            architecture = root / "ARCHITECTURE.md"
            progress.write_text("# Progress\n\nLatest fix: worker retry backoff.", encoding="utf-8")
            architecture.write_text("# Architecture\n\nNext: cooperative cancellation.", encoding="utf-8")

            prompt = build_resume_prompt(progress, architecture)

            self.assertIn("Latest fix: worker retry backoff.", prompt)
            self.assertIn("Next: cooperative cancellation.", prompt)
            self.assertIn("Do not reply with only a plan", prompt)
            self.assertIn("D:", prompt)

    def test_prompt_tail_is_bounded_and_missing_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress = root / "PROGRESS.md"
            progress.write_text("A" * 200 + "LATEST", encoding="utf-8")
            prompt = build_resume_prompt(progress, root / "missing.md", max_chars=20)
            self.assertIn("LATEST", prompt)
            self.assertIn("File not found", prompt)
            self.assertIn("Earlier content omitted", prompt)

    def test_write_resume_prompt_replaces_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress = root / "PROGRESS.md"
            architecture = root / "ARCHITECTURE.md"
            output = root / "nested" / "continue.md"
            progress.write_text("Checkpoint one", encoding="utf-8")
            architecture.write_text("Architecture one", encoding="utf-8")

            result = write_resume_prompt(output, progress, architecture)

            self.assertEqual(result, output)
            self.assertIn("Checkpoint one", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
