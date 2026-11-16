from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from muse.task_queue import TaskQueue


class _ProposalStore:
    def __init__(self, status="approved"):
        self.proposal = {
            "id": "proposal-1", "title": "Improve parser", "problem": "Parser misses a bounded case",
            "hypothesis": "A narrow validation change closes the gap", "status": status,
            "priority": 70, "updated_at": "2026-11-06T00:00:00+00:00", "metadata": {},
        }

    def get(self, proposal_id):
        return self.proposal if proposal_id == self.proposal["id"] else None


class ImprovementPatchPlannerTests(unittest.TestCase):
    def _setup(self, directory, responses, status="approved"):
        root = Path(directory)
        source = root / "src" / "muse" / "example.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_example.py").write_text("import unittest\n", encoding="utf-8")
        calls = []

        def builder(prompt):
            calls.append(prompt)
            return responses.pop(0)

        queue = TaskQueue(root / "tasks.sqlite3")
        store = _ProposalStore(status)
        from muse.improvement_patch_planner import ImprovementPatchPlanner
        planner = ImprovementPatchPlanner(root, improvement_store=store, task_queue=queue,
                                           builder=builder, builder_model_id="local-builder")
        return planner, queue, source, calls

    def test_prepares_exact_patch_as_waiting_approval_ticket_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, source, calls = self._setup(directory, [
                '{"paths":["src/muse/example.py"]}',
                '{"summary":"Tighten the example validation","patches":[{"path":"src/muse/example.py","old_text":"VALUE = 1","new_text":"VALUE = 2"}]}',
            ])
            try:
                before = source.read_bytes()
                result = planner.prepare_ticket("proposal-1")
                task = queue.get(result["ticket_id"])
                self.assertEqual(result["status"], "waiting_approval")
                self.assertEqual(task["status"], "waiting_approval")
                self.assertEqual(task["payload"]["kind"], "approved_code_change")
                self.assertEqual(task["payload"]["patches"][0]["expected_sha256"], hashlib.sha256(before).hexdigest())
                self.assertNotIn("_approved", task["payload"])
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(len(calls), 2)
            finally:
                queue.close()

    def test_rejects_unapproved_proposal_before_calling_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, source, calls = self._setup(directory, [], status="proposed")
            try:
                with self.assertRaises(PermissionError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(calls, [])
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()

    def test_rejects_model_selected_path_outside_allowlisted_index(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, _source, calls = self._setup(directory, ['{"paths":["pyproject.toml"]}'])
            try:
                with self.assertRaises(ValueError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(len(calls), 1)
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()

    def test_rejects_patch_that_adds_duplicate_top_level_import(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = [
                json.dumps({"paths": ["src/muse/example.py"]}),
                json.dumps({"summary": "Add import", "patches": [{
                    "path": "src/muse/example.py", "old_text": "VALUE = 1",
                    "new_text": "import ipaddress\\nVALUE = 2",
                }]}),
            ]
            planner, queue, source, _calls = self._setup(directory, responses)
            source.write_text("import ipaddress\\nVALUE = 1\\n", encoding="utf-8")
            try:
                with self.assertRaises(ValueError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()

    def test_large_files_are_not_exposed_to_builder_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, _source, calls = self._setup(directory, ['{"paths":["src/muse/large.py"]}'])
            large = Path(directory) / "src" / "muse" / "large.py"
            large.write_text("VALUE = 1\\n" + ("# padding\\n" * 4000), encoding="utf-8")
            try:
                with self.assertRaises(ValueError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(len(calls), 1)
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()

    def test_source_change_during_model_generation_blocks_ticket_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, source, calls = self._setup(directory, [
                '{"paths":["src/muse/example.py"]}',
                '{"summary":"Change value","patches":[{"path":"src/muse/example.py","old_text":"VALUE = 1","new_text":"VALUE = 2"}]}',
            ])
            original_builder = planner.builder

            def changing_builder(prompt):
                response = original_builder(prompt)
                if len(calls) == 2:
                    source.write_text("VALUE = 99\n", encoding="utf-8")
                return response

            planner.builder = changing_builder
            try:
                with self.assertRaises(RuntimeError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 99\n")
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()

    def test_rejects_non_unique_or_stale_patch_context(self):
        with tempfile.TemporaryDirectory() as directory:
            planner, queue, source, _calls = self._setup(directory, [
                '{"paths":["src/muse/example.py"]}',
                '{"summary":"Change value","patches":[{"path":"src/muse/example.py","old_text":"VALUE","new_text":"VALUE = 2"}]}',
            ])
            try:
                source.write_text("VALUE = 1\nVALUE = 3\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    planner.prepare_ticket("proposal-1")
                self.assertEqual(queue.snapshot()["counts"]["waiting_approval"], 0)
            finally:
                queue.close()
