from __future__ import annotations

import tempfile
import os
import threading

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.autonomous_runtime import (
    AutonomousSupervisor, RuntimeStateStore, enqueue_local_workflow,
)
from muse.task_queue import TaskQueue


class AutonomousRuntimeTests(unittest.TestCase):
    def test_pid_alive_checks_current_and_invalid_process_ids(self):
        from muse.autonomous_runtime import _pid_alive
        self.assertTrue(_pid_alive(os.getpid()))
        self.assertFalse(_pid_alive(None))
        self.assertFalse(_pid_alive(0))
        self.assertFalse(_pid_alive(2147483647))

    def make_supervisor(self, root: Path) -> AutonomousSupervisor:
        return AutonomousSupervisor(
            task_db=root / "tasks.sqlite3",
            routine_db=root / "routines.sqlite3",
            state_db=root / "runtime.sqlite3",
            improvement_db=root / "improvements.sqlite3",
            knowledge_db=root / "knowledge.sqlite3",
            reminder_db=root / "reminders.sqlite3",
            memory_db=root / "memory.sqlite3",
            conversation_db=root / "conversations.sqlite3",
            workspace=root,
        )

    def test_local_supervisor_claims_only_registered_review_research_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            payload = {
                "kind": "review_followup_research",
                "task_scope": "read_only_research",
                "review_id": "review-001",
                "review_node_id": "decision-001",
                "review_artifact_id": "artifact-001",
                "proposal_id": "proposal-001",
                "feedback_id": "feedback-001",
                "plan_node_id": "plan-001",
                "review_findings": [],
                "reviewer_output_is_untrusted_data": True,
                "not_claim_evidence": True,
                "requires_separate_code_change_approval": True,
            }
            with TaskQueue(root / "tasks.sqlite3") as queue:
                task = queue.enqueue("Review follow-up", payload)
            self.assertEqual(supervisor._next_local_workflow_id(), task["id"])

    def test_review_followup_worker_flows_into_autonomous_research_without_forced_topic(self):
        import hashlib
        from muse.autonomous_research import AutonomousResearchEngine
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.worker import run_once

        class FakeSearch:
            def __init__(self):
                self.queries = []

            def search(self, query, *, limit=8):
                self.queries.append(query)
                counter = "counterexamples" in query or "limitations" in query
                host = "8.8.8.8" if counter else "1.1.1.1"
                return [{
                    "title": "Counter-evidence source" if counter else "Supporting research source",
                    "url": f"https://{host}/public-research",
                    "snippet": "Public research result",
                    "engine": "controlled-smoke",
                    "published_at": "",
                }]

        class FakeReader:
            def read(self, url):
                body = (
                    "Public source text with methods, limitations, sample boundaries "
                    "and reproducible details. " * 8
                )
                return {
                    "url": url,
                    "content_type": "text/plain",
                    "text": body,
                    "sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "fetched_at": "2026-11-19T00:00:00+00:00",
                    "truncated": False,
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                decision = knowledge.upsert_node(
                    "decision", "Rejected independent engineering review",
                    summary="A failed candidate was rejected by independent review.",
                    external_ref="feedback-research-review:runtime-integration",
                    metadata={
                        "decision_kind": "independent_engineering_experiment_review",
                        "improvement_proposal_id": "proposal-runtime-integration",
                        "source_feedback_id": "feedback-runtime-integration",
                        "approved": False,
                        "epistemic_status": "engineering_review_rejected_not_external_evidence",
                    },
                    status="rejected",
                )

            supervisor = self.make_supervisor(root)
            payload = {
                "kind": "review_followup_research",
                "task_scope": "read_only_research",
                "review_id": "review-runtime-integration",
                "review_node_id": decision["id"],
                "review_artifact_id": "artifact-runtime-integration",
                "proposal_id": "proposal-runtime-integration",
                "feedback_id": "feedback-runtime-integration",
                "plan_node_id": "plan-runtime-integration",
                "review_findings": [{
                    "severity": "high", "title": "Controlled finding",
                    "detail": "Untrusted reviewer text remains data.",
                }],
                "reviewer_output_is_untrusted_data": True,
                "not_claim_evidence": True,
                "requires_separate_code_change_approval": True,
            }
            with TaskQueue(root / "tasks.sqlite3") as queue:
                ticket = queue.enqueue(
                    "Research rejected review", payload,
                    idempotency_key="review-followup:review-runtime-integration",
                )
            worker_result = run_once(
                root / "tasks.sqlite3", handlers=supervisor.handlers(),
                task_id=ticket["id"],
            )
            with TaskQueue(root / "tasks.sqlite3") as queue:
                finished = queue.get(ticket["id"])

            search = FakeSearch()
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                improvement_db=root / "improvements.sqlite3",
                memory_db=root / "memory.sqlite3",
                conversation_db=root / "conversations.sqlite3",
                workspace=root, search=search, reader=FakeReader(),
                resource_aware=False, max_sources=2,
            )
            result = engine.run_once()
            report = result.get("report", {})
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                evidence = knowledge.list_claim_evidence(report["claim_id"], limit=20)
            directions = {item.get("direction") for item in evidence}
            next_topic = engine._next_topic()

            self.assertEqual(worker_result["status"], "succeeded")
            self.assertEqual(finished["status"], "succeeded")
            self.assertEqual(
                finished["payload"]["_result"]["status"], "handoff_registered"
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(report["topic_origin"], "rejected_review_followup")
            self.assertEqual(report["topic_key"], "rejected-review:" + decision["id"])
            self.assertEqual(len(search.queries), 2)
            self.assertEqual(report["read_source_count"], 2)
            self.assertEqual(
                directions, {"support_search_candidate", "counter_search_candidate"}
            )
            self.assertEqual(report["epistemic_status"], "hypothesis_only_not_fact")
            self.assertNotEqual(next_topic["key"], report["topic_key"])

    def test_commander_video_worker_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            with patch.dict(os.environ, {}, clear=True):
                result = supervisor._video_worker({})
            self.assertEqual(result["status"], "disabled")
            self.assertFalse(result.get("claimed", False))

    def test_commander_video_worker_dispatches_one_task_when_opted_in(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            expected = {"status": "idle", "claimed": False}
            with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
                with patch("muse.video_task_worker.VideoTaskWorker.run_once", return_value=expected) as run_once:
                    result = supervisor._video_worker({})
            self.assertEqual(result, expected)
            run_once.assert_called_once_with()

    def test_autonomous_research_receives_improvement_store_for_proposal_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with patch("muse.autonomous_runtime.AutonomousResearchEngine") as engine_type:
                engine_type.return_value.run_once.return_value = {
                    "status": "completed", "report": {"topic": "Evidence traceability"},
                }
                result = supervisor._autonomous_research({})
            kwargs = engine_type.call_args.kwargs
            self.assertEqual(kwargs["improvement_db"], root / "improvements.sqlite3")
            self.assertEqual(kwargs["knowledge_db"], root / "knowledge.sqlite3")
            self.assertEqual(kwargs["memory_db"], root / "memory.sqlite3")
            self.assertEqual(kwargs["conversation_db"], root / "conversations.sqlite3")
            self.assertIsNone(kwargs["feedback_experiment_reviewer"])
            self.assertEqual(kwargs["feedback_reviewer_status"]["reason"], "MUSE_REVIEWER_MODEL_not_configured")
            self.assertEqual(result["report_topic"], "Evidence traceability")

    def test_watchdog_and_capability_scan_surface_redacted_failure_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with TaskQueue(root / "tasks.sqlite3") as queue:
                for index in range(2):
                    task = queue.enqueue(f"PRIVATE TASK {index}", {"secret": "PRIVATE PAYLOAD"})
                    queue.claim_next(worker_id=f"worker-{index}", task_id=task["id"])
                    queue.finish(task["id"], status="failed", worker_id=f"worker-{index}", result={
                        "error_code": "provider.unavailable", "failure_category": "provider",
                        "retryable": True, "recovery_guidance": "Check the configured endpoint.",
                    })
            watchdog = supervisor._watchdog_check({})
            self.assertEqual(watchdog["failure_classification"]["total_failures"], 2)
            self.assertEqual(watchdog["failure_classification"]["by_category"], {"provider": 2})
            radar = supervisor._capability_scan({})
            self.assertIn("repeated_failure_class", [item["key"] for item in radar["signals_seen"]])
            self.assertTrue(radar["approval_required"])
            self.assertNotIn("PRIVATE TASK", str(watchdog))
            self.assertNotIn("PRIVATE PAYLOAD", str(radar))

    def test_local_workflow_runs_allowlisted_deterministic_actions_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            task = enqueue_local_workflow(
                root / "tasks.sqlite3", "Inspect local queue",
                [{"action": "queue_snapshot"}, {"action": "knowledge_lint"}],
            )
            result = supervisor.run_tick()
            self.assertEqual(result["task"]["status"], "succeeded")
            with TaskQueue(root / "tasks.sqlite3") as queue:
                finished = queue.get(task["id"])
            self.assertIs(finished["payload"]["_result"]["model_used"], False)
            self.assertEqual(finished["payload"]["_result"]["execution_mode"], "deterministic_local")
            self.assertEqual(len(finished["payload"]["_result"]["steps"]), 2)

    def test_offline_supervisor_does_not_claim_other_queued_agent_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with TaskQueue(root / "tasks.sqlite3") as queue:
                other = queue.enqueue("Requires ChatGPT", {"kind": "commander_ticket", "goal": "new reasoning"})
                local = enqueue_local_workflow(
                    root / "tasks.sqlite3", "Local check", [{"action": "queue_snapshot"}]
                )
            result = supervisor.run_tick()
            self.assertEqual(result["task"]["id"], local["id"])
            with TaskQueue(root / "tasks.sqlite3") as queue:
                self.assertEqual(queue.get(other["id"])["status"], "queued")

    def test_enqueue_rejects_arbitrary_shell_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with self.assertRaisesRegex(ValueError, "unsupported"):
                enqueue_local_workflow(database, "unsafe", [{"action": "shell", "command": "whoami"}])
            with self.assertRaisesRegex(ValueError, "unsupported"):
                enqueue_local_workflow(database, "unsafe", [{"action": "queue_snapshot", "command": "whoami"}])

    def test_local_workflow_requires_bounded_steps_and_query(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with self.assertRaises(ValueError):
                enqueue_local_workflow(database, "empty", [])
            with self.assertRaises(ValueError):
                enqueue_local_workflow(database, "search", [{"action": "knowledge_search"}])

    def test_default_routines_are_idempotent_and_model_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            first = supervisor.ensure_default_routines()
            second = supervisor.ensure_default_routines()
            self.assertEqual(len(first), 6)
            self.assertEqual(second, [])
            status = supervisor.status()
            self.assertFalse(status["architecture"]["local_model_required"])
            self.assertFalse(status["architecture"]["third_party_ai_api_required"])
            self.assertEqual(len(status["routines"]), 6)

        from muse.improvement import ImprovementStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                researching = improvements.create_proposal(
                    "Research candidate", "Problem", "Hypothesis"
                )
                improvements.transition(researching["id"], "researching", actor="test")
                review = improvements.create_proposal(
                    "Review candidate", "Problem", "Hypothesis"
                )
                improvements.transition(review["id"], "researching", actor="test")
                improvements.transition(review["id"], "ready_for_review", actor="test")

            snapshot = supervisor.status()["self_improvement"]
            self.assertEqual(snapshot["status"], "available")
            self.assertEqual(snapshot["pending_research"], 1)
            self.assertEqual(snapshot["pending_human_review"], 1)
            self.assertEqual(snapshot["next_action"], "human_review_required")
            self.assertTrue(snapshot["approval_required"])
            self.assertFalse(snapshot["automatic_code_changes"])
            self.assertEqual(snapshot["proposal_counts"]["ready_for_review"], 1)

    def test_control_watcher_renews_lease_during_a_long_blocking_tick(self):
        from muse.autonomous_runtime import _ControlWatcher
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            owner = "c" * 32
            stop_event = threading.Event()
            with RuntimeStateStore(database) as store:
                store.acquire(owner)
                watcher = _ControlWatcher(
                    database, owner, stop_event,
                    interval_seconds=0.01, heartbeat_interval_seconds=0.02,
                )
                watcher.start()
                try:
                    time.sleep(0.09)
                    status = store.status()
                    self.assertTrue(status["running"])
                    self.assertLessEqual(status["heartbeat_age_seconds"], 1)
                    self.assertFalse(stop_event.is_set())
                finally:
                    watcher.stop()
                    store.release(owner)


    def test_runtime_single_instance_lease_and_cross_process_pause_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            owner = "a" * 32
            with RuntimeStateStore(database) as store:
                store.acquire(owner)
                with self.assertRaisesRegex(RuntimeError, "already has an active owner"):
                    store.acquire("b" * 32)
                self.assertEqual(store.set_control("paused")["state"], "paused")
                self.assertEqual(store.status()["state"], "paused")
                store.set_control("running")
                self.assertEqual(store.status()["state"], "running")
                store.set_control("stopping")
                self.assertEqual(store.status()["state"], "stopping")
                store.release(owner)
                self.assertEqual(store.status()["state"], "stopped")

    def test_run_loop_observes_stop_request_and_releases_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            results = []
            # Isolate the lifecycle contract from first-tick network/model research.
            # Routine execution and stop-event propagation are covered separately.
            with patch.object(supervisor, "run_tick", return_value={
                "routines": {"processed": 0, "outcomes": []}, "task": None,
            }):
                thread = threading.Thread(target=lambda: results.append(supervisor.run_forever(poll_seconds=1)))
                thread.start()
                deadline = time.time() + 5
                while time.time() < deadline:
                    status = supervisor.status()["runtime"]
                    if status.get("running"):
                        break
                    time.sleep(0.02)
                with RuntimeStateStore(root / "runtime.sqlite3") as state:
                    state.set_control("stopping")
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(supervisor.status()["runtime"]["state"], "stopped")
            self.assertEqual(len(results), 1)

    def test_run_loop_stops_before_work_when_runtime_lease_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            with patch("muse.autonomous_runtime.RuntimeStateStore.heartbeat", return_value=False):
                with patch.object(supervisor, "run_tick") as run_tick:
                    result = supervisor.run_forever(poll_seconds=1)
            run_tick.assert_not_called()
            self.assertEqual(result["ticks"], 0)
            self.assertEqual(result["tasks_processed"], 0)
            self.assertEqual(supervisor.status()["runtime"]["state"], "stopped")

    def test_cli_enqueue_and_status_are_usable(self):
        from muse.autonomous_runtime import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = [
                "--task-db", str(root / "tasks.sqlite3"),
                "--routine-db", str(root / "routines.sqlite3"),
                "--state-db", str(root / "runtime.sqlite3"),
                "--improvement-db", str(root / "improvements.sqlite3"),
                "--knowledge-db", str(root / "knowledge.sqlite3"),
                "--reminder-db", str(root / "reminders.sqlite3"),
            ]
            self.assertEqual(main(args + ["enqueue", "--title", "Queue check",
                                          "--steps-json", '[{"action":"queue_snapshot"}]']), 0)
            self.assertEqual(main(args + ["status"]), 0)


    def test_feedback_reviewer_stays_pending_without_second_model_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with patch.dict(os.environ, {}, clear=True):
                gate, scope, status, store = supervisor._build_feedback_reviewer(root)
            self.assertIsNone(gate)
            self.assertIsNone(scope)
            self.assertIsNone(store)
            self.assertEqual(status["status"], "pending")
            self.assertEqual(status["reason"], "MUSE_REVIEWER_MODEL_not_configured")

    def test_feedback_reviewer_requires_second_model_exposed_by_local_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            (config / "muse.toml").write_text(
                '[models]\nprovider = "llama_cpp"\nbase_url = "http://127.0.0.1:1235/v1"\nmodel = "builder-model"\n',
                encoding="utf-8",
            )
            supervisor = self.make_supervisor(root)
            env = {
                "MUSE_REVIEWER_MODEL": "reviewer-model",
                "MUSE_RUFLO_PROVIDER": "llama_cpp",
                "MUSE_LLAMA_CPP_MODEL": "builder-model",
                "MUSE_LLAMA_CPP_URL": "http://127.0.0.1:1235/v1",
            }
            with patch.dict(os.environ, env, clear=True), patch("muse.model_client.LMStudioClient.list_models", return_value=[{"id": "builder-model"}]):
                gate, scope, status, store = supervisor._build_feedback_reviewer(root)
            self.assertIsNone(gate)
            self.assertIsNone(scope)
            self.assertIsNone(store)
            self.assertEqual(status["reason"], "reviewer_model_not_available_on_loopback_endpoint")

    def test_feedback_reviewer_uses_distinct_local_model_and_marks_same_endpoint_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            (config / "muse.toml").write_text(
                '[models]\nprovider = "llama_cpp"\nbase_url = "http://127.0.0.1:1235/v1"\nmodel = "builder-model"\n',
                encoding="utf-8",
            )
            supervisor = self.make_supervisor(root)
            env = {
                "MUSE_REVIEWER_MODEL": "reviewer-model",
                "MUSE_RUFLO_PROVIDER": "llama_cpp",
                "MUSE_LLAMA_CPP_MODEL": "builder-model",
                "MUSE_LLAMA_CPP_URL": "http://127.0.0.1:1235/v1",
            }
            with patch.dict(os.environ, env, clear=True), patch("muse.model_client.LMStudioClient.list_models", return_value=[{"id": "builder-model"}, {"id": "reviewer-model"}]):
                gate, scope, status, store = supervisor._build_feedback_reviewer(root)
            try:
                self.assertIsNotNone(gate)
                self.assertNotEqual(gate.reviewer_id, "builder-model")
                self.assertEqual(scope, "distinct_model_same_local_endpoint")
                self.assertEqual(status["status"], "ready")
                self.assertEqual(status["model_id"], "reviewer-model")
            finally:
                if store is not None:
                    store.close()


if __name__ == "__main__":
    unittest.main()
