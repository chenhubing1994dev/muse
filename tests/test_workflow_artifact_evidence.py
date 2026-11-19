from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from muse.commander_planner import CommanderPlanner
from muse.task_queue import TaskQueue
from muse.workflow_profiles import WorkflowContract, WorkflowController


class WorkflowArtifactEvidenceTests(unittest.TestCase):
    def _prepare(self, root: Path):
        artifact_root = root / "artifacts"
        artifact_root.mkdir()
        artifact = artifact_root / "release-notes.txt"
        content = b"Muse release artifact v1\n"
        artifact.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        queue = TaskQueue(root / "data" / "tasks.sqlite3")
        plan = CommanderPlanner().plan("verify a local deliverable")
        ticket = queue.enqueue("Artifact-backed workflow", {})["id"]
        queue.claim_next(worker_id="worker-a", task_id=ticket)
        step_ids = [step.step_id for step in plan.steps]
        criterion = f"{step_ids[0]}: release artifact is present and verified"
        contract = WorkflowContract(
            goal=plan.goal, desired_outcome="A local release artifact is verified",
            acceptance_criteria=(criterion,),
        )
        controller = WorkflowController(queue, artifact_root=artifact_root)
        controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
        for step in plan.steps:
            queue.record_event(
                ticket, "report.step_submitted", actor="ruflo",
                payload={"step_id": step.step_id, "agent_id": f"agent-{step.step_id}", "output_chars": 24},
            )
        queue.record_event(
            ticket, "commander.plan_consensus", actor="commander",
            payload={"plan_id": plan.plan_id, "approved": True},
        )
        queue.record_event(
            ticket, "commander.execution_finished", actor="commander",
            payload={
                "plan_id": plan.plan_id, "status": "succeeded",
                "orchestration_status": "succeeded", "review_approved": True,
                "orchestration_run_id": "artifact-run",
            },
        )
        recorded = controller.record_artifact_evidence(
            ticket, worker_id="worker-a", expected_revision=1,
            orchestration_run_id="artifact-run", relative_path="release-notes.txt", expected_sha256=digest,
            expected_bytes=len(content),
        )
        artifact_ref = recorded["payload"]["evidence_ref"]
        refs = tuple(dict.fromkeys((
            "orchestration-run:artifact-run",
            *(f"step:{step_id}" for step_id in step_ids),
            "step:independent_review", artifact_ref,
        )))
        criterion_refs = (
            "orchestration-run:artifact-run", f"step:{step_ids[0]}",
            "step:independent_review", artifact_ref,
        )
        return queue, controller, ticket, criterion, refs, criterion_refs, artifact

    def test_completion_revalidates_and_persists_local_artifact_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, controller, ticket, criterion, refs, criterion_refs, artifact = self._prepare(Path(directory))
            try:
                completed = controller.complete(
                    ticket, worker_id="worker-a", expected_revision=2,
                    evidence_refs=refs, outcome_verified=True,
                    criteria_evidence={criterion: criterion_refs},
                )
                validation = completed["payload"]["outcome_verification"]["artifact_validation"]
                self.assertEqual(validation["status"], "verified")
                self.assertEqual(validation["verified_artifacts"][0]["relative_path"], "release-notes.txt")
                self.assertEqual(
                    validation["verified_artifacts"][0]["sha256"],
                    hashlib.sha256(artifact.read_bytes()).hexdigest(),
                )
            finally:
                queue.close()

    def test_completion_rejects_artifact_mutated_after_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, controller, ticket, criterion, refs, criterion_refs, artifact = self._prepare(Path(directory))
            try:
                artifact.write_bytes(b"tampered after verification\n")
                with self.assertRaisesRegex(ValueError, "revalidation"):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=2,
                        evidence_refs=refs, outcome_verified=True,
                        criteria_evidence={criterion: criterion_refs},
                    )
            finally:
                queue.close()

    def test_completion_rejects_forged_artifact_reference_without_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            queue = TaskQueue(root / "data" / "tasks.sqlite3")
            try:
                plan = CommanderPlanner().plan("verify a local deliverable")
                ticket = queue.enqueue("Artifact-backed workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                step_ids = [step.step_id for step in plan.steps]
                criterion = f"{step_ids[0]}: deliverable verified"
                contract = WorkflowContract(
                    goal=plan.goal, desired_outcome="Deliverable is verified",
                    acceptance_criteria=(criterion,),
                )
                controller = WorkflowController(queue, artifact_root=artifact_root)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
                for step in plan.steps:
                    queue.record_event(
                        ticket, "report.step_submitted", actor="ruflo",
                        payload={"step_id": step.step_id, "agent_id": f"agent-{step.step_id}", "output_chars": 24},
                    )
                queue.record_event(
                    ticket, "commander.plan_consensus", actor="commander",
                    payload={"plan_id": plan.plan_id, "approved": True},
                )
                queue.record_event(
                    ticket, "commander.execution_finished", actor="commander",
                    payload={
                        "plan_id": plan.plan_id, "status": "succeeded",
                        "orchestration_status": "succeeded", "review_approved": True,
                        "orchestration_run_id": "artifact-run",
                    },
                )
                artifact_ref = "artifact:release-notes.txt#sha256=" + "a" * 64
                refs = tuple(dict.fromkeys((
                    "orchestration-run:artifact-run",
                    *(f"step:{step_id}" for step_id in step_ids),
                    "step:independent_review", artifact_ref,
                )))
                with self.assertRaisesRegex(ValueError, "no verified event"):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=refs, outcome_verified=True,
                        criteria_evidence={
                            criterion: (
                                "orchestration-run:artifact-run", f"step:{step_ids[0]}",
                                "step:independent_review", artifact_ref,
                            ),
                        },
                    )
            finally:
                queue.close()


if __name__ == "__main__":
    unittest.main()
