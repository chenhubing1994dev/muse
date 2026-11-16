from pathlib import Path
import tempfile
import unittest

from muse.memory import MemoryStore
from muse.skillify import SkillifyBlocked, SkillifyService


class SkillifyTests(unittest.TestCase):
    def _candidate(self, service):
        return service.propose_completed_run(
            run_id="run-001", agent_id="agent-coder", name="safe-test-first",
            summary="Run focused tests before changing code.",
            steps=("Inspect the failing test.", "Make the smallest change.", "Run the focused test suite."),
            test_cases=(
                {"input": "fix a regression", "expected_contains": "focused tests"},
                {"input": "change an API", "expected_contains": "smallest change"},
            ),
            evidence_refs=("task-event:123", "test-run:456"), run_status="succeeded",
        )

    def test_skill_stays_pending_until_evaluated_and_human_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                service = SkillifyService(memory)
                candidate = self._candidate(service)
                self.assertEqual(candidate["status"], "pending")
                self.assertEqual(service.search_active_skills("focused tests"), [])
                report = service.evaluate_candidate(
                    candidate["id"], evaluator_id="skill-evaluator-v1",
                    runner=lambda _skill, case: (
                        "Run focused tests and make the smallest change."
                        if case["input"] else ""
                    ),
                )
                self.assertTrue(report["passed"])
                approved = service.approve_candidate(candidate["id"], approved_by="user")
                self.assertEqual(approved["status"], "approved")
                self.assertEqual(approved["track"], "agent")
                self.assertEqual(approved["kind"], "skill")
                self.assertEqual(len(service.search_active_skills("focused tests")), 1)

    def test_failed_evaluation_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                service = SkillifyService(memory)
                candidate = self._candidate(service)
                report = service.evaluate_candidate(
                    candidate["id"], evaluator_id="skill-evaluator-v1",
                    runner=lambda _skill, _case: "no match",
                )
                self.assertFalse(report["passed"])
                with self.assertRaises(SkillifyBlocked):
                    service.approve_candidate(candidate["id"], approved_by="user")
                with self.assertRaisesRegex(ValueError, "passing evaluation"):
                    memory.approve_candidate(candidate["id"], approved_by="user")
                self.assertEqual(memory.search("safe-test-first"), [])

    def test_only_successful_evidence_backed_runs_can_propose_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                service = SkillifyService(memory)
                with self.assertRaises(SkillifyBlocked):
                    service.propose_completed_run(
                        run_id="run-002", agent_id="agent-coder", name="unsafe-shortcut",
                        summary="shortcut", steps=("skip tests",),
                        test_cases=({"input": "x", "expected_contains": "x"},),
                        evidence_refs=("task-event:1",), run_status="failed",
                    )
                with self.assertRaises(ValueError):
                    service.propose_completed_run(
                        run_id="run-002", agent_id="agent-coder", name="unsafe-shortcut",
                        summary="shortcut", steps=("skip tests",),
                        test_cases=({"input": "x", "expected_contains": "x"},),
                        evidence_refs=(), run_status="succeeded",
                    )

    def test_skill_has_no_permission_grants_and_evaluation_is_auditable(self):
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(Path(directory) / "memory.sqlite3") as memory:
                service = SkillifyService(memory)
                candidate = self._candidate(service)
                report = service.evaluate_candidate(
                    candidate["id"], evaluator_id="review-suite",
                    runner=lambda _skill, _case: "focused tests; smallest change",
                )
                self.assertTrue(report["passed"])
                stored = service.evaluation(candidate["id"])
                self.assertEqual(stored["evaluator_id"], "review-suite")
                document = __import__("json").loads(candidate["content"])
                self.assertEqual(document["permissions"], [])
                self.assertIn("grants no tools", document["notice"])


if __name__ == "__main__":
    unittest.main()
