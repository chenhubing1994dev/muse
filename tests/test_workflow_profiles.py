import tempfile
import unittest
from pathlib import Path

from muse.commander_planner import CommanderPlanner
from muse.task_queue import TaskQueue
from muse.workflow_profiles import VerificationPolicy, WorkflowController, WorkflowProfile


class WorkflowProfileTests(unittest.TestCase):
    def test_profile_round_trip_and_bounded_policy(self):
        profile = WorkflowProfile.team(max_attempts=3, max_runtime_seconds=900)
        self.assertEqual(WorkflowProfile.from_dict(profile.to_dict()), profile)
        with self.assertRaises(ValueError):
            VerificationPolicy(max_attempts=4)
        with self.assertRaises(ValueError):
            WorkflowProfile.from_dict({**profile.to_dict(), "schema_version": 99})

    def test_profile_rejects_duplicate_or_invalid_stages(self):
        with self.assertRaises(ValueError):
            WorkflowProfile("team", ("plan", "plan"))
        with self.assertRaises(ValueError):
            WorkflowProfile("team", ("plan", "Bad Stage"))

    def test_bind_plan_maps_every_step_to_existing_unique_ticket(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                workflow_id = controller.bind_plan(plan, ids)
                self.assertEqual(workflow_id, plan.plan_id)
                snapshot = controller.snapshot(ids["research"])
                self.assertEqual(snapshot["workflow_id"], plan.plan_id)
                self.assertEqual(snapshot["profile"]["schema_version"], 1)
                self.assertEqual(snapshot["status"], "active")
                self.assertEqual(snapshot["revision"], 1)

    def test_binding_rejects_incomplete_mapping_and_nonqueued_ticket(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                with self.assertRaises(ValueError):
                    WorkflowController(queue).bind_plan(plan, {"research": ids["research"]})
                queue.claim_next(task_id=ids["research"])
                with self.assertRaises(ValueError):
                    WorkflowController(queue).bind_plan(plan, ids)

    def test_pause_resume_and_cancel_use_existing_ticket_event_log(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                ticket = ids["research"]
                paused = controller.pause(ticket, actor="user", expected_revision=1)
                self.assertEqual(paused["event_type"], "workflow.paused")
                self.assertEqual(controller.snapshot(ticket)["status"], "paused")
                resumed = controller.resume(ticket, actor="user", expected_revision=2)
                self.assertEqual(resumed["event_type"], "workflow.resumed")
                self.assertEqual(controller.snapshot(ticket)["status"], "active")
                cancelled = controller.cancel(ticket, actor="user", reason="stop", expected_revision=3)
                self.assertEqual(cancelled["event_type"], "workflow.cancelled")
                self.assertEqual(queue.get(ticket)["status"], "cancelled")

    def test_pause_blocks_claim_until_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                ticket = ids["research"]
                controller.pause(ticket, actor="user", expected_revision=1)
                self.assertIsNone(queue.claim_next(task_id=ticket))
                controller.resume(ticket, actor="user", expected_revision=2)
                self.assertEqual(queue.claim_next(task_id=ticket)["id"], ticket)

    def test_stage_checkpoint_requires_current_lease_and_cas_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                ticket = ids["research"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                result = controller.record_stage(
                    ticket, worker_id="worker-a", stage="plan", attempt=1,
                    expected_revision=1, checkpoint={"cursor": "phase-1"},
                    evidence_refs=("test://evidence/1",), next_action="architect",
                )
                self.assertEqual(result["event_type"], "workflow.stage_changed")
                self.assertEqual(controller.snapshot(ticket)["checkpoint"], {"cursor": "phase-1"})
                with self.assertRaises(PermissionError):
                    controller.record_stage(
                        ticket, worker_id="worker-b", stage="verify", attempt=1,
                        expected_revision=2,
                    )
                with self.assertRaises(RuntimeError):
                    controller.record_stage(
                        ticket, worker_id="worker-a", stage="verify", attempt=1,
                        expected_revision=1,
                    )

    def test_stage_attempt_and_checkpoint_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids, profile=WorkflowProfile.team(max_attempts=1))
                ticket = ids["research"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                with self.assertRaises(ValueError):
                    controller.record_stage(ticket, worker_id="worker-a", stage="fix", attempt=2, expected_revision=1)
                with self.assertRaises(ValueError):
                    controller.record_stage(ticket, worker_id="worker-a", stage="fix", attempt=1, expected_revision=1, checkpoint={"large": "x" * 5000})



    def test_binding_preflight_prevents_partial_plan_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                queue.record_workflow_event(
                    ids["research"], "workflow.bound", actor="test",
                    payload={"workflow_id": "other", "profile": WorkflowProfile.team().to_dict()},
                )
                with self.assertRaises(ValueError):
                    WorkflowController(queue).bind_plan(plan, ids)
                self.assertEqual(queue.workflow_revision(ids["architecture"]), 0)

    def test_cancel_revision_conflict_leaves_ticket_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                ticket = ids["research"]
                with self.assertRaises(RuntimeError):
                    controller.cancel(ticket, actor="user", reason="stale", expected_revision=0)
                self.assertEqual(queue.get(ticket)["status"], "queued")
                self.assertEqual(controller.snapshot(ticket)["status"], "active")

    def test_expired_and_reclaimed_worker_cannot_append_stage_event(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                ticket = ids["research"]
                queue.claim_next(worker_id="worker-a", task_id=ticket, lease_seconds=1)
                queue.recover_expired(now="2999-01-01T00:00:00+00:00")
                queue.claim_next(worker_id="worker-b", task_id=ticket, lease_seconds=60)
                with self.assertRaises(PermissionError):
                    controller.record_stage(
                        ticket, worker_id="worker-a", stage="verify", attempt=1,
                        expected_revision=1,
                    )
                self.assertEqual(queue.workflow_revision(ticket), 1)


    def test_profile_rejects_boolean_schema_version_and_invalid_ticket_ids(self):
        with self.assertRaises(ValueError):
            WorkflowProfile("team", ("plan", "execute"), schema_version=True)
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                ids["research"] = ""
                with self.assertRaises(ValueError):
                    WorkflowController(queue).bind_plan(plan, ids)

    def test_workflow_deadline_prevents_late_stage_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids, profile=WorkflowProfile.team(max_runtime_seconds=1))
                ticket = ids["research"]
                queue._connection.execute(
                    "UPDATE task_events SET created_at=? WHERE task_id=? AND event_type='workflow.bound'",
                    ("2000-01-01T00:00:00+00:00", ticket),
                )
                queue._connection.commit()
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                with self.assertRaises(TimeoutError):
                    controller.record_stage(
                        ticket, worker_id="worker-a", stage="plan", attempt=1,
                        expected_revision=1,
                    )


    def test_ticket_backed_verify_fix_reverify_persists_each_bounded_attempt(self):
        from muse.verification import VerificationResult

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a")
                reviews = iter((
                    VerificationResult(False, True, ("missing test evidence",), ("evidence:review-1",)),
                    VerificationResult(True, False, (), ("evidence:review-2",)),
                ))
                outcome = controller.verify_and_fix(
                    ticket, worker_id="worker-a", artifact="draft",
                    verify=lambda _artifact, _attempt: next(reviews),
                    fix=lambda artifact, findings, _attempt: artifact + "-fixed",
                    idempotent=True,
                )
                self.assertEqual(outcome.artifact, "draft-fixed")
                self.assertEqual(outcome.attempts, 2)
                stage_events = [
                    event["payload"]["stage"] for event in queue.workflow_events(ticket)
                    if event["event_type"] == "workflow.stage_changed"
                ]
                self.assertEqual(stage_events, ["verify", "fix", "verify"])
                self.assertEqual(controller.snapshot(ticket)["next_action"], "complete")

    def test_ticket_backed_verify_fix_never_retries_non_idempotent_work(self):
        from muse.verification import VerificationBlocked, VerificationResult

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a")
                with self.assertRaises(VerificationBlocked):
                    controller.verify_and_fix(
                        ticket, worker_id="worker-a", artifact="draft",
                        verify=lambda _artifact, _attempt: VerificationResult(
                            False, True, ("side effect cannot be replayed",)
                        ),
                        fix=lambda *_args: self.fail("non-idempotent work must not be retried"),
                        idempotent=False,
                    )
                self.assertEqual(
                    [event["payload"]["stage"] for event in queue.workflow_events(ticket)
                     if event["event_type"] == "workflow.stage_changed"],
                    ["verify"],
                )

if __name__ == "__main__":
    unittest.main()
