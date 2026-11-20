from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.task_queue import TaskQueue
from muse.worker import run_once


class WorkerTests(unittest.TestCase):
    def test_worker_cli_can_enable_registered_media_adapters_without_auto_execution(self) -> None:
        from muse.worker import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = main([
                "--db", str(root / "tasks.sqlite3"),
                "--capability-db", str(root / "capabilities.sqlite3"),
                "--enable-media",
            ])
            self.assertEqual(result, 0)
            self.assertTrue((root / "capabilities.sqlite3").is_file())

    def test_parallel_wave_does_not_spawn_idle_workers_for_empty_queue(self) -> None:
        from unittest.mock import patch
        from muse.worker import run_parallel_once

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with patch("muse.worker.run_once") as run_once_mock:
                result = run_parallel_once(database, max_workers=32)
            self.assertEqual(result, [])
            run_once_mock.assert_not_called()

    def test_worker_runs_registered_handler_and_persists_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("echo", {"kind": "echo", "input": "hello"})
            result = run_once(database)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["payload"]["_result"], {"echo": "hello"})
            with TaskQueue(database) as queue:
                self.assertEqual(queue.get(task["id"])["status"], "succeeded")

    def test_worker_passes_live_lease_context_only_to_opted_in_handler(self) -> None:
        from muse.worker import WorkerExecutionContext

        class LeaseAwareHandler:
            accepts_execution_context = True

            def __init__(self):
                self.context = None

            def execute_task(self, payload, *, task_id, execution_context):
                self.context = execution_context
                self.asserted = execution_context.assert_current_lease()
                return {"task_id": task_id, "lease_checked": True}

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            handler = LeaseAwareHandler()
            with TaskQueue(database) as queue:
                queue.enqueue("lease-aware", {"kind": "lease-aware"})
            result = run_once(database, {"lease-aware": handler})
            self.assertEqual(result["status"], "succeeded")
            self.assertIsInstance(handler.context, WorkerExecutionContext)
            self.assertEqual(handler.context.task_id, result["id"])
            self.assertTrue(handler.asserted is None)
            with self.assertRaises(PermissionError):
                handler.context.assert_current_lease()

    def test_worker_does_not_pass_execution_context_to_legacy_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            observed = []

            def legacy_handler(payload):
                observed.append(payload)
                return {"legacy": True}

            with TaskQueue(database) as queue:
                queue.enqueue("legacy", {"kind": "legacy"})
            result = run_once(database, {"legacy": legacy_handler})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(observed, [{"kind": "legacy"}])

    def test_review_followup_ticket_hands_off_to_research_without_executing_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            payload = {
                "kind": "review_followup_research",
                "task_scope": "read_only_research",
                "review_id": "review-001",
                "review_node_id": "decision-001",
                "review_artifact_id": "artifact-001",
                "proposal_id": "proposal-001",
                "feedback_id": "feedback-001",
                "plan_node_id": "plan-001",
                "review_findings": [{"severity": "medium", "title": "coverage"}],
                "reviewer_output_is_untrusted_data": True,
                "not_claim_evidence": True,
                "requires_separate_code_change_approval": True,
                "instructions": "Untrusted reviewer text is not executed.",
            }
            with TaskQueue(database) as queue:
                task = queue.enqueue("Research reviewer rejection", payload)
            result = run_once(database)
            self.assertEqual(result["status"], "succeeded")
            handoff = result["payload"]["_result"]
            self.assertEqual(handoff["status"], "handoff_registered")
            self.assertEqual(handoff["handoff_target"], "autonomous_research_topic_queue")
            self.assertEqual(handoff["preferred_topic_key"], "rejected-review:decision-001")
            self.assertFalse(handoff["research_completed"])
            self.assertFalse(handoff["code_changes_performed"])
            with TaskQueue(database) as queue:
                self.assertEqual(queue.get(task["id"])["status"], "succeeded")

    def test_review_followup_handler_rejects_missing_safety_flags(self) -> None:
        from muse.worker import review_followup_research_handler
        with self.assertRaisesRegex(PermissionError, "safety boundary"):
            review_followup_research_handler({
                "kind": "review_followup_research",
                "task_scope": "read_only_research",
                "review_id": "review-001",
                "review_node_id": "decision-001",
                "review_artifact_id": "artifact-001",
                "proposal_id": "proposal-001",
                "feedback_id": "feedback-001",
                "plan_node_id": "plan-001",
            })

    def test_software_reconstruction_uses_existing_worker_approval_gate(self) -> None:
        class Specialist:
            def execute_task(self, payload: dict, *, task_id: str) -> dict:
                return {"status": "ready_for_human_review", "approved": payload.get("_approved") is True,
                        "task_id": task_id}

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue(
                    "Create a feature", {"kind": "software_reconstruction", "mode": "create"},
                )
            specialist = Specialist()
            paused = run_once(database, {"software_reconstruction": specialist})
            self.assertEqual(paused["status"], "waiting_approval")
            with TaskQueue(database) as queue:
                self.assertEqual(queue.get(task["id"])["status"], "waiting_approval")
                queue.approve(task["id"], approved_by="owner")
            completed = run_once(database, {"software_reconstruction": specialist})
            self.assertEqual(completed["status"], "succeeded")
            self.assertTrue(completed["payload"]["_result"]["approved"])

    def test_open_design_run_uses_existing_worker_approval_gate(self) -> None:
        class OpenDesignSpecialist:
            def execute_task(self, payload: dict, *, task_id: str) -> dict:
                return {"status": "completed", "approved": payload.get("_approved") is True,
                        "task_id": task_id}

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue(
                    "Open Design run", {"kind": "open_design_run", "project_id": "p1", "agent": "opencode"},
                )
            specialist = OpenDesignSpecialist()
            paused = run_once(database, {"open_design_run": specialist})
            self.assertEqual(paused["status"], "waiting_approval")
            with TaskQueue(database) as queue:
                self.assertEqual(queue.get(task["id"])["status"], "waiting_approval")
                queue.approve(task["id"], approved_by="owner")
            completed = run_once(database, {"open_design_run": specialist})
            self.assertEqual(completed["status"], "succeeded")
            self.assertTrue(completed["payload"]["_result"]["approved"])

    def test_worker_records_lifecycle_events_without_payload_content(self) -> None:
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("private task title", {"kind": "echo", "input": "private payload"})
            result = run_once(database)
            self.assertEqual(result["status"], "succeeded")
            with ProgressEventStore(database) as store:
                events = store.list_events(category="task", subject_id=task["id"])
            self.assertEqual([event["event"] for event in reversed(events)], ["started", "completed"])
            self.assertNotIn("private payload", " ".join(event["summary"] for event in events))
            self.assertNotIn("private task title", " ".join(event["summary"] for event in events))

    def test_worker_discards_result_when_lease_ownership_is_lost(self) -> None:
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("lease race", {"kind": "race"})

            def take_over(payload: dict) -> dict:
                with TaskQueue(database) as replacement:
                    replacement._connection.execute(
                        "UPDATE tasks SET lease_owner=? WHERE id=?",
                        ("replacement-worker", task["id"]),
                    )
                    replacement._connection.commit()
                return {"should_be_discarded": True}

            result = run_once(database, {"race": take_over})
            self.assertIsNone(result)
            with TaskQueue(database) as queue:
                stored = queue.get(task["id"])
            self.assertEqual(stored["status"], "running")
            self.assertEqual(stored["lease_owner"], "replacement-worker")
            self.assertNotIn("_result", stored["payload"])
            with ProgressEventStore(database) as progress:
                events = progress.list_events(category="task", subject_id=task["id"])
            self.assertEqual([event["event"] for event in reversed(events)], ["started", "failed"])
            self.assertIn("result was discarded", events[0]["summary"])

    def test_approved_plan_intake_rejects_missing_approval(self) -> None:
        from muse.worker import approved_plan_intake_handler
        with self.assertRaises(PermissionError):
            approved_plan_intake_handler({"plan_id": "a" * 32, "plan": {"approval_required": True}})

    def test_approved_plan_intake_writes_bounded_packet_not_commands(self) -> None:
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            plan_id = "a" * 32
            plan = {"title": "Review project", "approval_required": True,
                    "action_class": "code_change", "acceptance_criteria": ["Add tests"]}
            with TaskQueue(database) as queue:
                queue.enqueue("intake", {"kind": "approved_plan_intake", "plan_id": plan_id,
                                          "plan": plan, "approved_by": "owner", "_approved": True,
                                          "_approval": {"approved_by": "owner", "approved_at": "2026-11-02T00:00:00+00:00"}})
            artifact_dir = Path(directory) / "approved_plans"
            with patch("muse.worker.Path", return_value=artifact_dir):
                result = run_once(database)
            self.assertEqual(result["status"], "succeeded")
            artifact = artifact_dir / f"{plan_id}.json"
            packet = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertIn("not permission to run arbitrary commands", packet["execution_boundary"])

    def test_approved_plan_router_uses_only_trusted_registered_handler(self) -> None:
        from muse.worker import ApprovedPlanCapabilityRouter
        calls = []
        router = ApprovedPlanCapabilityRouter({
            "read_only": lambda plan, *, idempotency_key: calls.append((plan, idempotency_key)) or {"checked": True}
        })
        payload = {"_approved": True, "approved_by": "owner",
                   "_approval": {"approved_by": "owner"}, "plan_id": "b" * 32,
                   "plan": {"approval_required": True, "action_class": "read_only", "title": "Inspect",
                            "objective": "Read local status", "rationale": "Safe local inspection",
                            "acceptance_criteria": ["Return status"], "priority": 50, "estimated_seconds": 60,
                            "command": "format C drive"}}
        result = router(payload)
        self.assertEqual(result["status"], "capability_executed")
        self.assertEqual(calls[0][1], "approved-plan:" + "b" * 32)
        self.assertNotIn("command", calls[0][0])
        self.assertEqual(result["result"], {"checked": True})

    def test_approved_plan_router_fails_closed_for_external_or_unregistered_actions(self) -> None:
        from muse.worker import ApprovedPlanCapabilityRouter
        router = ApprovedPlanCapabilityRouter({})
        base = {"_approved": True, "approved_by": "owner",
                "_approval": {"approved_by": "owner"}, "plan_id": "c" * 32,
                "plan": {"approval_required": True, "action_class": "external_action", "title": "Inspect",
                         "objective": "Do an external action", "rationale": "Test denial",
                         "acceptance_criteria": ["Must be denied"], "priority": 50, "estimated_seconds": 60}}
        with self.assertRaises(PermissionError):
            router(base)
        research = {"approval_required": True, "action_class": "research", "title": "Research",
                    "objective": "Research locally", "rationale": "No handler",
                    "acceptance_criteria": ["Report"], "priority": 50, "estimated_seconds": 60}
        with self.assertRaises(LookupError):
            router({**base, "plan": research})
        with self.assertRaises(PermissionError):
            router({**base, "_approved": False})

    def test_approved_plan_router_rejects_untrusted_registry_entries_and_oversized_results(self) -> None:
        from muse.worker import ApprovedPlanCapabilityRouter
        with self.assertRaises(ValueError):
            ApprovedPlanCapabilityRouter({"shell": lambda *_args, **_kwargs: None})
        router = ApprovedPlanCapabilityRouter({
            "code_change": lambda *_args, **_kwargs: {"output": "x" * (65 * 1024)}
        })
        payload = {"_approved": True, "approved_by": "owner",
                   "_approval": {"approved_by": "owner"}, "plan_id": "d" * 32,
                   "plan": {"approval_required": True, "action_class": "code_change", "title": "Patch",
                            "objective": "Make a bounded code change", "rationale": "Test result bound",
                            "acceptance_criteria": ["Return a small result"], "priority": 50, "estimated_seconds": 60}}
        with self.assertRaises(ValueError):
            router(payload)

    def test_worker_requires_independent_review_for_configured_task_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("build", {"kind": "build"})
            failed = run_once(database, {"build": lambda _: {"patch": "candidate"}},
                              review_required_kinds={"build"})
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["payload"]["_result"]["error_code"], "review.required")

    def test_worker_review_gate_records_and_blocks_rejected_artifact(self) -> None:
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("build", {"kind": "build", "_test_evidence": "unit suite passed"})
            store = ReviewStore(Path(directory) / "reviews.sqlite3")
            try:
                gate = IndependentReviewGate(store, reviewer_id="independent-reviewer",
                    call=lambda _: json.dumps({"approved": False, "summary": "Needs changes", "findings": [
                        {"severity": "medium", "title": "Missing edge case", "detail": "Add boundary coverage"}
                    ]}))
                result = run_once(database, {"build": lambda _: {"patch": "candidate"}},
                                  review_required_kinds={"build"}, review_gate=gate)
                self.assertEqual(result["status"], "failed")
                saved = store.list_for_artifact(task["id"])
                self.assertEqual(len(saved), 1)
                self.assertFalse(saved[0]["approved"])
                from muse.progress import ProgressEventStore
                with ProgressEventStore(database) as progress:
                    events = progress.list_events(category="task", subject_id=task["id"])
                failures = [event for event in events if event["event"] == "failed"]
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]["error_code"], "review.rejected")
                with TaskQueue(database) as queue:
                    ticket_events = queue.events(task["id"])
                classified = [event for event in ticket_events if event["event_type"] == "failure.classified"]
                self.assertEqual(classified[0]["payload"]["error_code"], "review.rejected")

            finally:
                store.close()

    def test_worker_review_gate_accepts_only_independently_approved_artifact(self) -> None:
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("build", {"kind": "build", "_test_evidence": "unit suite passed"})
            store = ReviewStore(Path(directory) / "reviews.sqlite3")
            try:
                gate = IndependentReviewGate(store, reviewer_id="reviewer",
                    call=lambda _: json.dumps({"approved": True, "summary": "Approved", "findings": []}))
                result = run_once(database, {"build": lambda _: {"patch": "candidate"}},
                                  review_required_kinds={"build"}, review_gate=gate)
                self.assertEqual(result["status"], "succeeded")
                self.assertTrue(result["payload"]["_result"]["review"]["approved"])
            finally:
                store.close()

    def test_unknown_task_kind_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("unknown", {"kind": "run_shell", "command": "danger"})
            result = run_once(database)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["payload"]["_result"]["error_code"], "handler.missing")
            with TaskQueue(database) as queue:
                classified = [event for event in queue.events(result["id"]) if event["event_type"] == "failure.classified"]
            self.assertEqual(classified[0]["payload"]["failure_category"], "configuration")


    def test_worker_persists_stable_failure_code_without_exception_message(self) -> None:
        from datetime import datetime, timedelta, timezone
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("private title", {"kind": "explode", "secret": "private payload"})
            result = run_once(
                database,
                {"explode": lambda _: (_ for _ in ()).throw(RuntimeError("private exception and token"))},
            )
            self.assertEqual(result["status"], "failed")
            failure = result["payload"]["_result"]
            self.assertEqual(failure["error_code"], "handler.unexpected")
            self.assertFalse(failure["retryable"])
            self.assertNotIn("private exception", str(failure))
            self.assertNotIn("private payload", str(failure))
            with ProgressEventStore(database) as progress:
                events = progress.list_events(category="task", subject_id=task["id"])
                codes = progress.aggregate_failure_codes(
                    since=datetime.now(timezone.utc) - timedelta(hours=1)
                )
            self.assertEqual(events[0]["error_code"], "handler.unexpected")
            self.assertEqual(codes["handler.unexpected"], 1)
            self.assertNotIn("private exception", str(events))

    def test_worker_pauses_sensitive_kind_until_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            calls: list[str] = []
            with TaskQueue(database) as queue:
                task = queue.enqueue("publish post", {"kind": "publish", "text": "hello"})
            handler = lambda payload: calls.append(payload["text"]) or {"published": True}
            waiting = run_once(database, {"publish": handler}, approval_required_kinds={"publish"})
            self.assertEqual(waiting["status"], "waiting_approval")
            self.assertEqual(calls, [])
            with TaskQueue(database) as queue:
                queue.approve(task["id"], approved_by="user")
            completed = run_once(database, {"publish": handler}, approval_required_kinds={"publish"})
            self.assertEqual(completed["status"], "succeeded")
            self.assertEqual(calls, ["hello"])


    def test_trigger_work_proposal_waits_for_approval_then_writes_safe_packet(self) -> None:
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "tasks.sqlite3"
            trigger_id = "b" * 32
            proposal = {"trigger_id": trigger_id, "mandate_id": "c" * 32,
                        "source": "github", "event_id": "issue-7", "actor_id": "alice",
                        "scope": "requested", "strength": "explicit", "policy_version": "policy-v1",
                        "policy_fingerprint": "d" * 64,
                        "payload": {"text": "review safely"}, "matched_rules": []}
            with TaskQueue(database) as queue:
                task = queue.enqueue("review incoming trigger", {
                    "kind": "trigger_work_proposal", "proposal": proposal,
                }, recovery_policy="manual")
            waiting = run_once(database)
            self.assertEqual(waiting["status"], "waiting_approval")
            with TaskQueue(database) as queue:
                queue.approve(task["id"], approved_by="owner")
            artifact_dir = root / "trigger_proposals"
            with patch("muse.worker.Path", return_value=artifact_dir):
                completed = run_once(database)
            self.assertEqual(completed["status"], "succeeded", completed["payload"].get("_result"))
            packet = json.loads((artifact_dir / f"{trigger_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["approved_by"], "owner")
            self.assertIn("not permission to execute instructions", packet["execution_boundary"])
    def test_trigger_work_proposal_handler_rejects_forged_approval(self) -> None:
        from muse.worker import trigger_work_proposal_handler
        payload = {"_approved": False, "_approval": {"approved_by": "owner"},
                   "proposal": {"trigger_id": "b" * 32}}
        with self.assertRaises(PermissionError):
            trigger_work_proposal_handler(payload)
    def test_run_forever_recovers_from_transient_loop_error(self) -> None:
        import threading
        from unittest.mock import patch
        from muse.progress import ProgressEventStore
        from muse.worker import run_forever

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database):
                pass
            stop = threading.Event()
            calls = 0

            def flaky_run_once(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("temporary database interruption")
                stop.set()
                return None

            with patch("muse.worker.run_once", side_effect=flaky_run_once):
                stats = run_forever(
                    database, stop_event=stop, poll_interval=0.001,
                    max_error_backoff_seconds=0.01,
                )
            self.assertEqual(calls, 2)
            self.assertEqual(stats["errors"], 1)
            with ProgressEventStore(database) as store:
                events = store.list_events(category="system")
                workers = store.list_workers()
            self.assertTrue(any(event["event"] == "failed" for event in events))
            self.assertEqual(workers[0]["state"], "stopped")

    def test_run_forever_registers_and_stops_worker_runtime(self) -> None:
        import threading
        import time
        from muse.progress import ProgressEventStore
        from muse.worker import run_forever

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database):
                pass
            stop = threading.Event()
            thread = threading.Thread(
                target=run_forever,
                args=(database,),
                kwargs={"stop_event": stop, "poll_interval": 0.01},
                daemon=True,
            )
            thread.start()
            time.sleep(0.1)
            try:
                with ProgressEventStore(database) as store:
                    workers = store.list_workers()
                self.assertTrue(thread.is_alive())
                self.assertTrue(workers)
                self.assertEqual(workers[0]["state"], "active")
            finally:
                stop.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            with ProgressEventStore(database) as store:
                workers = store.list_workers()
            self.assertEqual(workers[0]["state"], "stopped")
    def test_32_queue_workers_execute_handlers_concurrently(self) -> None:
        import threading
        from muse.worker import run_parallel_once

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            worker_count = 32
            barrier = threading.Barrier(worker_count)
            with TaskQueue(database) as queue:
                for index in range(worker_count):
                    queue.enqueue(f"parallel-{index}", {"kind": "barrier", "index": index})

            def handler(payload: dict) -> dict:
                barrier.wait(timeout=15)
                return {"completed": payload["index"]}

            results = run_parallel_once(database, {"barrier": handler}, max_workers=worker_count)
            completed = [item for item in results if item is not None]
            self.assertEqual(len(completed), worker_count)
            self.assertTrue(all(item["status"] == "succeeded" for item in completed))
            with TaskQueue(database) as queue:
                self.assertEqual(queue.snapshot()["counts"]["succeeded"], worker_count)

    def test_parallel_worker_rejects_more_than_32_workers(self) -> None:
        from muse.worker import run_parallel_once
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_parallel_once(Path(directory) / "tasks.sqlite3", max_workers=33)

    def test_32_parallel_worker_loops_drain_queue_and_stop_cleanly(self) -> None:
        import threading
        from muse.progress import ProgressEventStore
        from muse.worker import run_parallel_forever

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            worker_count = 32
            stop = threading.Event()
            barrier = threading.Barrier(worker_count)
            count_lock = threading.Lock()
            completed_count = 0
            with TaskQueue(database) as queue:
                for index in range(worker_count):
                    queue.enqueue(f"loop-{index}", {"kind": "barrier", "index": index})

            def handler(payload: dict) -> dict:
                nonlocal completed_count
                barrier.wait(timeout=15)
                with count_lock:
                    completed_count += 1
                    if completed_count == worker_count:
                        stop.set()
                return {"completed": payload["index"]}

            stats = run_parallel_forever(
                database, {"barrier": handler}, stop_event=stop,
                max_workers=worker_count, poll_interval=0.005,
            )
            self.assertEqual(stats["processed"], worker_count)
            self.assertEqual(stats["errors"], 0)
            with TaskQueue(database) as queue:
                self.assertEqual(queue.snapshot()["counts"]["succeeded"], worker_count)
            with ProgressEventStore(database) as store:
                workers = store.list_workers(limit=worker_count)
            self.assertEqual(len(workers), worker_count)
            self.assertTrue(all(worker["state"] == "stopped" for worker in workers))

    def test_worker_cli_can_drain_with_32_worker_slots(self) -> None:
        from muse.worker import main

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("cli parallel task", {"kind": "echo", "input": "ok"})
            result = main(["--db", str(database), "--workers", "32"])
            self.assertEqual(result, 0)
            with TaskQueue(database) as queue:
                self.assertEqual(queue.snapshot()["counts"]["succeeded"], 1)

    def test_worker_cli_defaults_to_resource_aware_parallelism(self) -> None:
        from unittest.mock import patch
        from muse.resource_governor import HostResourceSnapshot
        from muse.worker import main

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("auto-worker-one", {"kind": "echo", "input": "one"})
                queue.enqueue("auto-worker-two", {"kind": "echo", "input": "two"})

            snapshot = HostResourceSnapshot(
                logical_cpu_count=3,
                available_memory_bytes=3 * 1024**3,
                total_memory_bytes=8 * 1024**3,
            )
            with patch("muse.worker.probe_host_resources", return_value=snapshot):
                result = main(["--db", str(database)])

            self.assertEqual(result, 0)
            with TaskQueue(database) as queue:
                counts = queue.snapshot()["counts"]
            self.assertEqual(counts["succeeded"], 2)
            self.assertEqual(counts["queued"], 0)

    def test_approved_code_change_handler_requires_persisted_human_ticket_decision(self) -> None:
        import hashlib
        from muse.evolution_executor import ApprovedCodeChangeExecutor, build_approved_code_change_handler

        class Store:
            def get(self, proposal_id):
                return {"id": proposal_id, "status": "approved"}
            def validate_and_promote(self, proposal_id, **kwargs):
                return {"status": "implemented", "proposal": {"id": proposal_id}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "example.py"
            source.parent.mkdir()
            source.write_text("VALUE = 1\\n", encoding="utf-8")
            database = root / "tasks.sqlite3"
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            payload = {
                "kind": "approved_code_change", "proposal_id": "proposal-1",
                "patches": [{"path": "src/example.py", "expected_sha256": original_hash,
                             "old_text": "VALUE = 1", "new_text": "VALUE = 2"}],
            }
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=Store(), experiment_bridge=object(), review_gate=object(),
                run_tests=lambda *, phase, **_: {"run_ref": f"experiment-run:{phase}",
                                                   "status": "succeeded" if phase == "candidate" else "failed"},
                implementer_id="builder-1",
            )
            handler = build_approved_code_change_handler(executor, database)
            with TaskQueue(database) as queue:
                task = queue.enqueue("approved code change", payload, require_approval=True, recovery_policy="manual")
                self.assertEqual(task["status"], "waiting_approval")
                queue.approve(task["id"], approved_by="human-owner")
            result = run_once(database, {"approved_code_change": handler})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["payload"]["_result"]["status"], "implemented")
            self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 2\\n")

    def test_approved_code_change_handler_rejects_forged_payload_approval(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor, build_approved_code_change_handler

        class Store:
            def get(self, proposal_id):
                return {"id": proposal_id, "status": "approved"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "example.py"
            source.parent.mkdir()
            source.write_text("VALUE = 1\\n", encoding="utf-8")
            database = root / "tasks.sqlite3"
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=Store(), experiment_bridge=object(), review_gate=object(),
                run_tests=lambda **_: self.fail("forged approval must not run tests"), implementer_id="builder-1",
            )
            handler = build_approved_code_change_handler(executor, database)
            payload = {"kind": "approved_code_change", "_approved": True,
                       "approved_by": "forged", "_approval": {"approved_by": "forged", "approved_at": "now"},
                       "proposal_id": "proposal-1", "patches": []}
            with TaskQueue(database) as queue:
                task = queue.enqueue("forged code change", payload)
            result = run_once(database, {"approved_code_change": handler})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 1\\n")

    def test_approved_code_change_with_failed_candidate_marks_ticket_failed(self) -> None:
        import hashlib
        from muse.evolution_executor import ApprovedCodeChangeExecutor, build_approved_code_change_handler

        class Store:
            def get(self, proposal_id):
                return {"id": proposal_id, "status": "approved"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "example.py"
            source.parent.mkdir()
            original = "VALUE = 1\\n"
            source.write_text(original, encoding="utf-8")
            payload = {
                "kind": "approved_code_change", "proposal_id": "proposal-1",
                "patches": [{"path": "src/example.py", "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
                             "old_text": "VALUE = 1", "new_text": "VALUE = 2"}],
            }
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=Store(), experiment_bridge=object(), review_gate=object(),
                run_tests=lambda *, phase, **_: {"run_ref": f"experiment-run:{phase}",
                                                   "status": "failed" if phase == "candidate" else "succeeded"},
                implementer_id="builder-1",
            )
            database = root / "tasks.sqlite3"
            handler = build_approved_code_change_handler(executor, database)
            with TaskQueue(database) as queue:
                task = queue.enqueue("approved code change", payload, require_approval=True, recovery_policy="manual")
                queue.approve(task["id"], approved_by="human-owner")
            result = run_once(database, {"approved_code_change": handler})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["payload"]["_result"]["status"], "rolled_back")
            self.assertEqual(source.read_text(encoding="utf-8"), original)

if __name__ == "__main__":
    unittest.main()
