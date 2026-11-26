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

    def test_commander_dispatches_only_review_only_authorized_contextual_ticket(self):
        import hashlib
        import json
        from unittest.mock import patch
        from muse.contextual_inquiry import ContextualInquiry, Hypothesis, Observation, SourceKind
        from muse.contextual_inquiry_bridge import build_workflow_contract
        from muse.contextual_inquiry_security import _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "tasks.sqlite3"
            inquiry = ContextualInquiry(
                "commander-review-case", "Review one bounded case.",
                observations=(Observation("obs-1", SourceKind.USER_QUOTE, "Private case text.", "source:1"),),
                hypotheses=(Hypothesis(
                    "hyp-1", "A tentative claim.", ("obs-1",), confidence=0.4,
                    falsification_condition="Independent evidence contradicts it.",
                ),),
            )
            case_envelope = {
                "version": 1, "protection": "windows-dpapi-current-user",
                "ciphertext_b64": "Y2FzZS1jaXBoZXJ0ZXh0",
            }
            result_plaintext = json.dumps(
                {
                    "schema_version": 1, "inquiry_id": inquiry.inquiry_id,
                    "summary": "The result remains provisional.",
                    "findings": [{
                        "finding_id": "finding-1", "statement": "Candidate only.",
                        "observation_refs": ["obs-1"], "hypothesis_refs": ["hyp-1"],
                        "assessment": "unresolved", "rationale": "Needs independent corroboration.",
                    }],
                    "unknowns": ["Independent corroboration is unavailable."],
                },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            result_envelope = {
                "version": 1, "protection": "windows-dpapi-current-user",
                "ciphertext_b64": "cmV2aWV3LXJlc3VsdC1jaXBoZXJ0ZXh0",
            }
            case_plaintext = _serialize_contextual_inquiry(inquiry)
            with TaskQueue(database) as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "workflow_contract": build_workflow_contract(inquiry).to_dict(),
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": case_envelope,
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True, recovery_policy="manual",
                    idempotency_key=(
                        "contextual-inquiry-review:"
                        + hashlib.sha256(inquiry.inquiry_id.encode("utf-8")).hexdigest()
                    ),
                )
                queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator", expected_updated_at=task["updated_at"],
                )

            class ReviewHandler:
                accepts_execution_context = True

                def execute_review(self, reviewed_inquiry, *, execution_context):
                    self.reviewed_inquiry = reviewed_inquiry
                    self.execution_context = execution_context
                    return result_envelope

            handler = ReviewHandler()
            supervisor = AutonomousSupervisor(
                task_db=database, routine_db=root / "routines.sqlite3",
                state_db=root / "runtime.sqlite3", improvement_db=root / "improvements.sqlite3",
                knowledge_db=root / "knowledge.sqlite3", reminder_db=root / "reminders.sqlite3",
                memory_db=root / "memory.sqlite3", conversation_db=root / "conversations.sqlite3",
                workspace=root, contextual_review_handler=handler,
            )

            def decrypt(envelope):
                if envelope == case_envelope:
                    return case_plaintext
                if envelope == result_envelope:
                    return result_plaintext
                raise ValueError("unexpected test envelope")

            with (
                patch("muse.autonomous_runtime.run_due_once", return_value={"processed": 0, "outcomes": []}),
                patch("muse.contextual_inquiry_bridge.unprotect_case_bytes", side_effect=decrypt),
                patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt),
            ):
                result = supervisor.run_tick()

            self.assertEqual(result["contextual_review"]["status"], "waiting_approval")
            self.assertEqual(result["contextual_review"]["id"], task["id"])
            self.assertIsInstance(handler.reviewed_inquiry, ContextualInquiry)
            with TaskQueue(database) as queue:
                completed = queue.get(task["id"])
            self.assertEqual(completed["status"], "waiting_approval")
            self.assertEqual(completed["payload"]["review_result_envelope"], result_envelope)
            self.assertNotIn("_result", completed["payload"])
            self.assertNotIn("_approved", completed["payload"])

    def test_supervisor_does_not_dispatch_review_only_ticket_without_injected_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with TaskQueue(root / "tasks.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": {
                            "version": 1, "protection": "windows-dpapi-current-user",
                            "ciphertext_b64": "Y2FzZS1jaXBoZXJ0ZXh0",
                        },
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True, recovery_policy="manual",
                    idempotency_key="contextual-inquiry-review:manual-no-handler-test",
                )
                queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator", expected_updated_at=task["updated_at"],
                )
            with patch("muse.autonomous_runtime.run_due_once", return_value={"processed": 0, "outcomes": []}):
                result = supervisor.run_tick()
            self.assertNotIn("contextual_review", result)
            with TaskQueue(root / "tasks.sqlite3") as queue:
                self.assertEqual(queue.get(task["id"])["status"], "waiting_approval")

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

    def test_review_followup_handoff_wakes_research_routine_on_next_tick(self):
        from muse.routines import RoutineStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            with RoutineStore(root / "routines.sqlite3") as store:
                store.add(
                    "idle-autonomous-research", "autonomous_research",
                    interval_seconds=3600, run_immediately=False,
                )
            payload = {
                "kind": "review_followup_research",
                "task_scope": "read_only_research",
                "review_id": "review-wakeup",
                "review_node_id": "decision-wakeup",
                "review_artifact_id": "artifact-wakeup",
                "proposal_id": "proposal-wakeup",
                "feedback_id": "feedback-wakeup",
                "plan_node_id": "plan-wakeup",
                "review_findings": [],
                "reviewer_output_is_untrusted_data": True,
                "not_claim_evidence": True,
                "requires_separate_code_change_approval": True,
            }
            with TaskQueue(root / "tasks.sqlite3") as queue:
                ticket = queue.enqueue(
                    "Wake research after rejection", payload,
                    idempotency_key="review-followup:review-wakeup",
                )
            with patch(
                "muse.autonomous_runtime.run_due_once",
                return_value={"processed": 0, "outcomes": []},
            ):
                result = supervisor.run_tick()

            self.assertEqual(result["task"]["id"], ticket["id"])
            self.assertEqual(result["task"]["status"], "succeeded")
            self.assertEqual(result["research_wakeup"]["status"], "scheduled")
            self.assertEqual(result["research_wakeup"]["topic_key"], "rejected-review:decision-wakeup")
            with RoutineStore(root / "routines.sqlite3") as store:
                routine = next(item for item in store.list() if item["name"] == "idle-autonomous-research")
                self.assertLessEqual(routine["next_run_at"], store._now().isoformat())

            research_calls = []
            supervisor._routine_handlers["autonomous_research"] = (
                lambda routine_payload: research_calls.append(routine_payload)
            )
            next_tick = supervisor.run_tick()
            self.assertEqual(next_tick["routines"]["processed"], 1)
            self.assertEqual(research_calls, [{}])
            with RoutineStore(root / "routines.sqlite3") as store:
                routine = next(item for item in store.list() if item["name"] == "idle-autonomous-research")
                self.assertEqual(routine["last_status"], "succeeded")


    def test_commander_video_worker_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            with patch.dict(os.environ, {}, clear=True):
                result = supervisor._video_worker({})
            self.assertEqual(result["status"], "disabled")
            self.assertFalse(result.get("claimed", False))

    def test_legacy_video_pipeline_task_handler_is_not_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            handlers = supervisor.handlers()
        self.assertNotIn("video_pipeline", handlers)


    def test_commander_video_worker_dispatches_queued_video_pipeline_via_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            provider = type("Provider", (), {
                "issue_video_admission_grant": lambda self, store, request: {},
                "readiness": lambda self: {"status": "ready"},
            })()
            request_factory = lambda **kwargs: kwargs
            supervisor.video_admission_provider = provider
            supervisor.video_admission_request_factory = request_factory
            with TaskQueue(supervisor.task_db) as queue:
                queue.enqueue("ordinary task", {"kind": "general_task"})
                execution = queue.enqueue(
                    "approved video execution", {"kind": "video_pipeline"},
                    recovery_policy="manual",
                )
            expected = {"status": "succeeded", "task_id": execution["id"]}
            with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
                with patch("muse.unified_runtime.MuseRuntime") as runtime_type:
                    with patch("muse.video_task_worker.VideoTaskWorker") as worker_type:
                        runtime_type.return_value.dispatch_video_pipeline_once.return_value = expected
                        result = supervisor._video_worker({})
            self.assertEqual(result, expected)
            runtime_type.assert_called_once_with(supervisor.task_db.resolve(), workspace=supervisor.workspace)
            worker_kwargs = worker_type.call_args.kwargs
            self.assertEqual(worker_kwargs["worker_id"], "commander-video-worker")
            self.assertIs(worker_kwargs["admission_provider"], provider)
            self.assertIs(worker_kwargs["admission_request_factory"], request_factory)
            self.assertTrue(callable(worker_kwargs["pause_check"]))
            dispatch = runtime_type.return_value.dispatch_video_pipeline_once
            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.kwargs["task_id"], execution["id"])
            self.assertIs(dispatch.call_args.kwargs["approved"], True)
            self.assertEqual(dispatch.call_args.kwargs["worker_id"], "commander-video-worker")
            self.assertIs(dispatch.call_args.kwargs["video_worker"], worker_type.return_value)

    def test_commander_video_worker_is_idle_without_queued_video_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            provider = type("Provider", (), {
                "issue_video_admission_grant": lambda self, store, request: {},
                "readiness": lambda self: {"status": "ready"},
            })()
            supervisor.video_admission_provider = provider
            supervisor.video_admission_request_factory = lambda **kwargs: kwargs
            with TaskQueue(supervisor.task_db) as queue:
                queue.enqueue("ordinary task", {"kind": "general_task"})
            with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
                with patch("muse.unified_runtime.MuseRuntime") as runtime_type:
                    with patch("muse.video_task_worker.VideoTaskWorker") as worker_type:
                        result = supervisor._video_worker({})
            self.assertEqual(result["status"], "idle")
            self.assertFalse(result["claimed"])
            runtime_type.assert_not_called()
            worker_type.assert_not_called()

    def test_commander_video_worker_does_not_claim_when_admission_readiness_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            provider = type("Provider", (), {
                "issue_video_admission_grant": lambda self, store, request: {},
                "readiness": lambda self: {"status": "blocked", "reason": "ComfyUI offline"},
            })()
            supervisor.video_admission_provider = provider
            supervisor.video_admission_request_factory = lambda **kwargs: kwargs
            with TaskQueue(supervisor.task_db) as queue:
                execution = queue.enqueue(
                    "approved video execution", {"kind": "video_pipeline"},
                    recovery_policy="manual",
                )
            with patch.dict(os.environ, {"MUSE_ENABLE_VIDEO_WORKER": "1"}):
                with patch("muse.unified_runtime.MuseRuntime") as runtime_type:
                    result = supervisor._video_worker({})
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["claimed"])
            self.assertEqual(result["reason"], "ComfyUI offline")
            runtime_type.assert_not_called()
            with TaskQueue(supervisor.task_db) as queue:
                self.assertEqual(queue.get(execution["id"])["status"], "queued")


    def test_supervisor_rejects_partial_video_admission_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "injected together"):
                AutonomousSupervisor(task_db=root / "tasks.sqlite3", workspace=root,
                                     video_admission_provider=object())
            with self.assertRaisesRegex(ValueError, "injected together"):
                AutonomousSupervisor(task_db=root / "tasks.sqlite3", workspace=root,
                                     video_admission_request_factory=lambda **kwargs: kwargs)


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

    def test_autonomous_research_feeds_sanitized_local_value_radar(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "reports"
            report_dir.mkdir()
            source_report = report_dir / "autonomous-research-sample-20261124.md"
            source_report.write_text("# public research report\n", encoding="utf-8")
            report = {
                "topic": "Public workflow research",
                "evidence_coverage_score": 72,
                "sources": [
                    {"url": "https://example.com/source-a", "title": "Source A"},
                    {"url": "mailto:invalid@example.com", "title": "Invalid source"},
                ],
                "synthesis": {
                    "executive_summary": "Public sources describe a repeatable workflow problem.",
                    "testable_experiments": ["Compare manual and templated production time."],
                    "counter_evidence": ["A small sample cannot establish broad demand."],
                    "uncertainties": ["Willingness to pay remains unknown."],
                    "creator_inspired_hypotheses": ["creator-context-sentinel"],
                },
                "inspiration_context": {
                    "multi_source_context": [{"title": "memory-context-sentinel"}],
                },
            }
            supervisor = self.make_supervisor(root)
            with (
                patch.object(supervisor, "_build_feedback_reviewer", return_value=(None, None, {"status": "disabled"}, None)),
                patch(
                    "muse.autonomous_runtime.AutonomousResearchEngine.run_once",
                    return_value={"status": "completed", "report": report, "report_path": str(source_report)},
                ),
            ):
                result = supervisor._autonomous_research({})

            self.assertEqual(result["value_radar_input"]["status"], "persisted")
            sidecar_path = root / result["value_radar_input"]["path"]
            self.assertTrue(sidecar_path.is_file())
            sidecar_text = sidecar_path.read_text(encoding="utf-8")
            self.assertNotIn("memory-context-sentinel", sidecar_text)
            self.assertNotIn("creator-context-sentinel", sidecar_text)

            loaded = supervisor._load_value_radar_reports(20)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["report"]["synthesis"]["executive_summary"],
                             "Public sources describe a repeatable workflow problem.")
            self.assertEqual(len(loaded[0]["report"]["sources"]), 1)
            self.assertEqual(loaded[0]["report"]["sources"][0]["url"], "https://example.com/source-a")

            handler = supervisor._routine_handlers["autonomous_value_radar"]
            class Context:
                def raise_if_cancelled(self):
                    return None
            radar_result = handler({}, execution_context=Context())
            self.assertEqual(radar_result["status"], "ranked_hypotheses")
            self.assertGreaterEqual(radar_result["candidate_count"], 1)
            self.assertFalse(radar_result["external_actions_performed"])
            self.assertTrue(radar_result["approval_required"])
            radar_path = root / radar_result["report_path"]
            self.assertTrue(radar_path.is_file())
            radar = json.loads(radar_path.read_text(encoding="utf-8"))
            self.assertEqual(radar["candidates"][0]["evidence_state"], "hypothesis_only")
            self.assertIsNone(radar["candidates"][0]["revenue_forecast"])

    def test_value_radar_loader_rejects_absolute_report_paths(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "reports"
            input_dir = report_dir / "value-radar-inputs"
            input_dir.mkdir(parents=True)
            outside_report = root / "outside.md"
            outside_report.write_text("# outside", encoding="utf-8")
            envelope = {
                "schema_version": 1,
                "source_kind": "muse_public_research_value_radar_input",
                "report_path": str(outside_report.resolve()),
                "report": {
                    "evidence_coverage_score": 80,
                    "sources": [{"url": "https://example.com/a", "title": "A"}],
                    "synthesis": {"executive_summary": "Candidate only."},
                },
            }
            (input_dir / "outside.json").write_text(json.dumps(envelope), encoding="utf-8")
            supervisor = self.make_supervisor(root)
            self.assertEqual(supervisor._load_value_radar_reports(20), [])

    def test_default_routines_are_idempotent_and_model_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            first = supervisor.ensure_default_routines()
            second = supervisor.ensure_default_routines()
            self.assertEqual(len(first), 7)
            self.assertEqual(second, [])
            status = supervisor.status()
            self.assertFalse(status["architecture"]["local_model_required"])
            self.assertFalse(status["architecture"]["third_party_ai_api_required"])
            self.assertEqual(len(status["routines"]), 7)

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

    def test_autonomous_research_records_project_usage_without_shadowing_active_task_context(self):
        from muse.model_call_context import (
            current_model_call_context, model_call_scope, record_current_model_usage,
        )
        from muse.usage_ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = self.make_supervisor(root)
            expected = {"task_id": None, "agent_id": None}
            test_case = self

            class FakeResearchEngine:
                def __init__(self, **_kwargs):
                    pass

                def run_once(self, *, stop_event=None):
                    context = current_model_call_context()
                    self.assert_context(context)
                    test_case.assertTrue(record_current_model_usage(                        "lm-studio-local", "research-local-model",
                        {"prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40},
                    ))
                    return {
                        "status": "completed",
                        "report": {"topic": {"question": "bounded test topic"}},
                        "feedback_reviewer_configuration": {"status": "pending"},
                    }

                @staticmethod
                def assert_context(context):
                    if context is None:
                        raise AssertionError("routine research must install or preserve a model usage context")
                    if context.task_id != expected["task_id"] or context.agent_id != expected["agent_id"]:
                        raise AssertionError("routine research replaced the caller's attribution context")
                    if context.project_id != "muse":
                        raise AssertionError("routine research must retain Muse project attribution")

            with patch("muse.autonomous_runtime.AutonomousResearchEngine", FakeResearchEngine), \
                 patch.dict(os.environ, {}, clear=True):
                first = supervisor._autonomous_research({})
                self.assertEqual(first["status"], "completed")
                self.assertIsNone(current_model_call_context())

                expected.update({"task_id": "outer-task-123", "agent_id": "outer-agent-456"})
                with model_call_scope(
                    supervisor.task_db, task_id=expected["task_id"],
                    agent_id=expected["agent_id"], project_id="muse",
                ):
                    second = supervisor._autonomous_research({})
                    self.assertEqual(second["status"], "completed")
                    self.assertEqual(current_model_call_context().task_id, expected["task_id"])
                self.assertIsNone(current_model_call_context())

            with UsageLedger(root / "model_usage.sqlite3") as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 2)
            unscoped, scoped = sorted(events, key=lambda event: event["task_id"] is not None)
            self.assertIsNone(unscoped["task_id"])
            self.assertIsNone(unscoped["agent_id"])
            self.assertEqual(unscoped["attribution_status"], "partial")
            self.assertEqual(unscoped["project_id"], "muse")
            self.assertEqual(scoped["task_id"], "outer-task-123")
            self.assertEqual(scoped["agent_id"], "outer-agent-456")
            self.assertEqual(scoped["attribution_status"], "complete")
            self.assertEqual(scoped["project_id"], "muse")
            self.assertTrue(all(event["input_tokens"] == 31 for event in events))
            self.assertTrue(all(event["output_tokens"] == 9 for event in events))

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

    def test_stopped_runtime_does_not_report_inactive_heartbeat_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            with RuntimeStateStore(database) as store:
                store.acquire("stopped-owner")
                store.release("stopped-owner")
                status = store.status()
            self.assertEqual(status["state"], "stopped")
            self.assertFalse(status["running"])
            self.assertFalse(status["stale"])
            self.assertEqual(status["heartbeat_status"], "inactive")

    def test_active_runtime_with_old_heartbeat_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            with RuntimeStateStore(database) as store:
                store.acquire("stale-owner")
                store.db.execute(
                    "UPDATE autonomous_runtime SET heartbeat_at=? WHERE singleton=1",
                    ("2000-01-01T00:00:00+00:00",),
                )
                store.db.commit()
                status = store.status(stale_after_seconds=30)
                store.release("stale-owner")
            self.assertEqual(status["state"], "running")
            self.assertTrue(status["stale"])
            self.assertFalse(status["running"])
            self.assertEqual(status["heartbeat_status"], "stale")


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

    def test_run_tick_does_not_claim_local_task_after_routine_stop_request(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(Path(directory))
            stop_event = threading.Event()

            def stop_during_routines(*_args, **_kwargs):
                stop_event.set()
                return {"processed": 1, "outcomes": [{"status": "cancelled"}]}

            with patch("muse.autonomous_runtime.run_due_once", side_effect=stop_during_routines):
                with patch.object(supervisor, "_next_local_workflow_id") as next_task:
                    result = supervisor.run_tick(stop_event=stop_event)

            next_task.assert_not_called()
            self.assertIsNone(result["task"])
            self.assertEqual(result["routines"]["outcomes"][0]["status"], "cancelled")


    def test_cli_local_contextual_review_requires_explicit_opt_in(self):
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
            with (
                patch("muse.autonomous_runtime.AutonomousSupervisor") as supervisor_class,
                patch("muse.contextual_inquiry_local_review.build_local_contextual_review_handler") as factory,
            ):
                supervisor = supervisor_class.return_value
                supervisor.run_tick.return_value = {"task": None}
                factory.return_value = object()

                self.assertEqual(main(args + ["once"]), 0)
                factory.assert_not_called()
                self.assertIsNone(supervisor_class.call_args.kwargs.get("contextual_review_handler"))

                supervisor_class.reset_mock()
                self.assertEqual(main(args + ["once", "--enable-contextual-review"]), 0)
                project_root = Path(__import__("muse.autonomous_runtime", fromlist=["main"]).__file__).resolve().parents[2]
                factory.assert_called_once_with(project_root, timeout_seconds=90.0)
                self.assertIs(
                    supervisor_class.call_args.kwargs["contextual_review_handler"],
                    factory.return_value,
                )

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


    def test_cli_defaults_are_project_root_anchored_outside_project_cwd(self):
        from muse.autonomous_runtime import main
        with tempfile.TemporaryDirectory() as directory:
            with patch("muse.autonomous_runtime.Path.cwd", return_value=Path(directory)):
                with patch("muse.autonomous_runtime.AutonomousSupervisor") as supervisor_class:
                    supervisor_class.return_value.status.return_value = {"runtime": {"running": False}}
                    self.assertEqual(main(["status"]), 0)
            kwargs = supervisor_class.call_args.kwargs
            project_root = Path(__import__("muse.autonomous_runtime", fromlist=["main"]).__file__).resolve().parents[2]
            expected = {
                "task_db": project_root / "data/tasks/muse.sqlite3",
                "routine_db": project_root / "data/routines/muse.sqlite3",
                "state_db": project_root / "data/autonomy/runtime.sqlite3",
                "improvement_db": project_root / "data/improvements/muse.sqlite3",
                "knowledge_db": project_root / "data/research/knowledge.sqlite3",
                "reminder_db": project_root / "data/reminders/muse.sqlite3",
            }
            for key, path in expected.items():
                self.assertEqual(Path(kwargs[key]), path)
                self.assertTrue(Path(kwargs[key]).is_absolute())
            self.assertEqual(kwargs["workspace"], project_root)

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
