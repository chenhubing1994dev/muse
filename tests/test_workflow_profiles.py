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
                    ticket, worker_id="worker-a", stage="plan_ready", attempt=1,
                    expected_revision=1, checkpoint={"cursor": "phase-1"},
                    evidence_refs=("test://evidence/1",), next_action="architect",
                )
                self.assertEqual(result["event_type"], "workflow.stage_changed")
                self.assertEqual(controller.snapshot(ticket)["checkpoint"], {"cursor": "phase-1"})
                self.assertEqual(controller.snapshot(ticket)["stage"], "plan_ready")
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

    def test_workflow_contract_is_versioned_and_round_trips(self):
        from muse.workflow_profiles import WorkflowContract

        contract = WorkflowContract(
            goal="Publish a verified research brief",
            desired_outcome="A cited brief is independently reviewed",
            acceptance_criteria=("Every material claim has a source",),
        )
        self.assertEqual(WorkflowContract.from_dict(contract.to_dict()), contract)
        with self.assertRaises(ValueError):
            WorkflowContract(goal=" ", desired_outcome="Done", acceptance_criteria=("Check",))
        with self.assertRaises(ValueError):
            WorkflowContract(goal="Goal", desired_outcome="Done", acceptance_criteria=())
        with self.assertRaises(ValueError):
            WorkflowContract.from_dict({**contract.to_dict(), "unexpected": True})
        maximum_contract = WorkflowContract(
            goal="A goal", desired_outcome="A verified result",
            acceptance_criteria=tuple(f"Criterion {index}" for index in range(32)),
        )
        self.assertEqual(len(maximum_contract.acceptance_criteria), 32)
        with self.assertRaises(ValueError):
            WorkflowContract(
                goal="A goal", desired_outcome="A verified result",
                acceptance_criteria=tuple(f"Criterion {index}" for index in range(33)),
            )

    def test_bind_plan_persists_contract_in_existing_ticket_event_log(self):
        from muse.workflow_profiles import WorkflowContract

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                contract = WorkflowContract(
                    goal=plan.goal,
                    desired_outcome="The requested feature passes independent verification",
                    acceptance_criteria=("Implementation tests pass", "Reviewer records approval"),
                    exclusions=("No external publication",),
                )
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids, contract=contract)
                snapshot = controller.snapshot(ids["research"])
                self.assertEqual(snapshot["contract"], contract.to_dict())
                self.assertEqual(queue.workflow_events(ids["research"])[0]["payload"]["contract"], contract.to_dict())
                self.assertEqual(queue.workflow_revision(ids["research"]), 1)

    def test_bind_plan_derives_goal_contract_when_none_is_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ids = {step.step_id: queue.enqueue(step.step_id, {})["id"] for step in plan.steps}
                controller = WorkflowController(queue)
                controller.bind_plan(plan, ids)
                contract = controller.snapshot(ids["research"])["contract"]
                self.assertEqual(contract["goal"], plan.goal)
                self.assertTrue(contract["acceptance_criteria"])
                self.assertIn("independent Reviewer", contract["desired_outcome"])

    def test_bind_plan_to_ticket_persists_contract_under_existing_worker_lease(self):
        from muse.workflow_profiles import WorkflowContract

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                contract = WorkflowContract(
                    goal=plan.goal,
                    desired_outcome="The requested work is verified",
                    acceptance_criteria=("All plan steps have evidence",),
                )
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
                self.assertEqual(controller.snapshot(ticket)["contract"], contract.to_dict())

    def test_workflow_completion_rejects_fabricated_commander_evidence(self):
        from muse.workflow_profiles import WorkflowContract

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                step_id = plan.steps[0].step_id
                criterion = f"{step_id}: tests pass"
                contract = WorkflowContract(
                    goal=plan.goal,
                    desired_outcome="All declared steps ran and the independent Reviewer approved",
                    acceptance_criteria=(criterion,),
                )
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)

                # A caller-supplied boolean and plausible-looking strings are not
                # proof unless matching Commander events exist in this Ticket log.
                with self.assertRaises(ValueError):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=(
                            "orchestration-run:forged-run",
                            f"step:{step_id}",
                            "step:independent_review",
                        ),
                        outcome_verified=True,
                        criteria_evidence={
                            criterion: (
                                "orchestration-run:forged-run",
                                f"step:{step_id}",
                                "step:independent_review",
                            ),
                        },
                    )

    def test_workflow_completion_rejects_unapproved_independent_review(self):
        from muse.workflow_profiles import WorkflowContract

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                step_ids = [step.step_id for step in plan.steps]
                criterion = f"{step_ids[0]}: verified output"
                contract = WorkflowContract(
                    goal=plan.goal,
                    desired_outcome="Every step succeeds and an independent Reviewer approves",
                    acceptance_criteria=(criterion,),
                )
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
                queue.record_event(
                    ticket, "commander.plan_consensus", actor="commander",
                    payload={"plan_id": plan.plan_id, "approved": True},
                )
                for step in plan.steps:
                    queue.record_event(
                        ticket, "report.step_submitted", actor="ruflo",
                        payload={"step_id": step.step_id, "agent_id": f"agent-{step.step_id}", "output_chars": 32},
                    )
                queue.record_event(
                    ticket, "commander.execution_finished", actor="commander",
                    payload={
                        "plan_id": plan.plan_id, "status": "failed",
                        "orchestration_status": "succeeded", "review_approved": False,
                        "orchestration_run_id": "review-rejected",
                    },
                )
                run_ref = "orchestration-run:review-rejected"
                refs = tuple(dict.fromkeys((run_ref, *(f"step:{step_id}" for step_id in step_ids), "step:independent_review")))
                with self.assertRaises(ValueError):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=refs, outcome_verified=True,
                        criteria_evidence={
                            criterion: (run_ref, f"step:{step_ids[0]}", "step:independent_review"),
                        },
                    )

    def test_workflow_completion_requires_verified_outcome_and_evidence_for_every_criterion(self):
        from muse.workflow_profiles import WorkflowContract

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                plan = CommanderPlanner().plan("build a research feature")
                ticket = queue.enqueue("Commander workflow", {})["id"]
                queue.claim_next(worker_id="worker-a", task_id=ticket)
                step_ids = [step.step_id for step in plan.steps]
                contract = WorkflowContract(
                    goal=plan.goal,
                    desired_outcome="A tested feature approved by an independent Reviewer",
                    acceptance_criteria=(f"{step_ids[0]}: Tests pass", f"{step_ids[1]}: Reviewer approves"),
                )
                controller = WorkflowController(queue)
                controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
                run_ref = "orchestration-run:run-verified"
                evidence_refs = tuple(dict.fromkeys((run_ref, *(f"step:{step_id}" for step_id in step_ids), "step:independent_review")))
                evidence = {
                    contract.acceptance_criteria[0]: (run_ref, f"step:{step_ids[0]}", "step:independent_review"),
                    contract.acceptance_criteria[1]: (run_ref, f"step:{step_ids[1]}", "step:independent_review"),
                }
                with self.assertRaises(ValueError):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=evidence_refs, outcome_verified=False,
                        criteria_evidence=evidence,
                    )
                with self.assertRaises(ValueError):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=evidence_refs, outcome_verified=True,
                        criteria_evidence={contract.acceptance_criteria[0]: evidence[contract.acceptance_criteria[0]]},
                    )
                with self.assertRaises(ValueError):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=1,
                        evidence_refs=evidence_refs, outcome_verified=True,
                        criteria_evidence={
                            item: ("orchestration-run:other-run",) for item in contract.acceptance_criteria
                        },
                    )

                queue.record_event(
                    ticket, "commander.plan_consensus", actor="commander",
                    payload={"plan_id": plan.plan_id, "approved": True},
                )
                for step in plan.steps:
                    queue.record_event(
                        ticket, "report.step_submitted", actor="ruflo",
                        payload={"step_id": step.step_id, "agent_id": f"agent-{step.step_id}", "output_chars": 64},
                    )
                queue.record_event(
                    ticket, "commander.execution_finished", actor="commander",
                    payload={
                        "plan_id": plan.plan_id, "status": "succeeded",
                        "orchestration_status": "succeeded", "review_approved": True,
                        "orchestration_run_id": "run-verified",
                    },
                )
                completed = controller.complete(
                    ticket, worker_id="worker-a", expected_revision=1,
                    evidence_refs=evidence_refs, outcome_verified=True,
                    criteria_evidence=evidence,
                )
                self.assertTrue(completed["payload"]["outcome_verification"]["verified"])
                self.assertEqual(
                    set(completed["payload"]["outcome_verification"]["criteria_evidence"]),
                    set(contract.acceptance_criteria),
                )
                self.assertEqual(
                    completed["payload"]["outcome_verification"]["evidence_validation"]["verified_steps"],
                    step_ids,
                )
                self.assertTrue(controller.snapshot(ticket)["outcome_verification"]["verified"])

    def test_team_profile_declares_plan_ready_checkpoint_before_execution(self):
        self.assertIn("plan_ready", WorkflowProfile.team().stages)

if __name__ == "__main__":
    unittest.main()
