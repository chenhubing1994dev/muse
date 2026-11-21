from __future__ import annotations

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.evolution_executor import run_feedback_research_experiment
from muse.research_knowledge import ResearchKnowledgeStore


class FeedbackResearchExperimentTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        workspace = root / "workspace"
        (workspace / "src" / "muse").mkdir(parents=True)
        (workspace / "tests").mkdir()
        (workspace / "src" / "muse" / "autonomous_research.py").write_text("# source snapshot\n", encoding="utf-8")
        (workspace / "src" / "muse" / "test_runner.py").write_text("# runner snapshot\n", encoding="utf-8")
        (workspace / "src" / "muse" / "experiment_bridge.py").write_text("# bridge snapshot\n", encoding="utf-8")
        (workspace / "src" / "muse" / "evolution_executor.py").write_text("# executor snapshot\n", encoding="utf-8")
        (workspace / "src" / "muse" / "research_knowledge.py").write_text("# graph snapshot\n", encoding="utf-8")
        (workspace / "tests" / "test_autonomous_research.py").write_text("# fixed allowlisted suite\n", encoding="utf-8")
        (workspace / "data").mkdir()
        (workspace / "data" / "private.txt").write_text("must not enter snapshot", encoding="utf-8")
        return workspace

    def _plan(self, knowledge: ResearchKnowledgeStore) -> str:
        plan = knowledge.upsert_node(
            "experiment", "Feedback research plan", summary="Plan only.",
            external_ref="feedback-research-plan:proposal-1",
            metadata={"improvement_proposal_id": "proposal-1", "source_feedback_id": "feedback-1",
                      "status": "planned", "epistemic_status": "research_plan_not_result"},
            status="planned",
        )
        return plan["id"]

    def test_unknown_experiment_key_is_rejected_without_running_or_registering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                with patch("muse.test_runner._run_isolated_suite") as runner:
                    with self.assertRaisesRegex(ValueError, "allowlisted"):
                        run_feedback_research_experiment(
                            workspace, knowledge, root / "progress.sqlite3",
                            proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                            experiment_key="python -c arbitrary",
                        )
                    runner.assert_not_called()
                self.assertEqual(len(knowledge.list_nodes(node_type="experiment", limit=20)), 1)

    def test_registered_suite_runs_in_source_only_snapshot_and_links_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                calls = []

                def fake_runner(start_directory, pattern, **kwargs):
                    calls.append((start_directory, pattern, kwargs))
                    self.assertEqual(pattern, "test_autonomous_research.py")
                    self.assertNotEqual(start_directory.resolve(), (workspace / "tests").resolve())
                    self.assertFalse((start_directory.parent / "data" / "private.txt").exists())
                    result = kwargs["experiment_bridge"].record_result(
                        kwargs["manifest_id"], status="succeeded", duration_seconds=1.25,
                        metrics={"tests_run": 7, "failures": 0, "errors": 0, "skipped": 0},
                        summary="Allowlisted feedback research regression suite passed.",
                        exit_code=0, run_id=kwargs["run_id"],
                    )
                    return {"status": "succeeded", "exit_code": 0, "duration_seconds": 1.25,
                            "metrics": result["metrics"], "run_ref": result["run_ref"],
                            "experiment_record_error": False}

                with patch("muse.test_runner._run_isolated_suite", side_effect=fake_runner):
                    from muse.progress import new_evolution_cycle_id, ProgressEventStore
                    cycle_id = new_evolution_cycle_id()
                    first = run_feedback_research_experiment(
                        workspace, knowledge, root / "progress.sqlite3",
                        proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                        evolution_cycle_id=cycle_id,
                    )
                    second = run_feedback_research_experiment(
                        workspace, knowledge, root / "progress.sqlite3",
                        proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                        evolution_cycle_id=cycle_id,
                    )

                self.assertEqual(len(calls), 1)
                self.assertEqual(first["run_ref"], second["run_ref"])
                self.assertEqual(first["evolution_cycle_id"], cycle_id)
                self.assertEqual(len(calls), 1)
                self.assertFalse(first["reused"])
                self.assertTrue(second["reused"])
                run_row = knowledge.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=? AND deleted_at IS NULL",
                    (first["run_ref"],),
                ).fetchone()
                self.assertIsNotNone(run_row)
                run = knowledge.get_node(run_row["id"])
                self.assertIsNotNone(run)
                self.assertEqual(run["metadata"]["improvement_proposal_id"], "proposal-1")
                self.assertEqual(run["metadata"]["source_feedback_id"], "feedback-1")
                self.assertEqual(run["metadata"]["epistemic_status"], "engineering_validation_not_external_evidence")
                self.assertEqual(run["metadata"]["evolution_cycle_ids"], [cycle_id])
                with ProgressEventStore(root / "progress.sqlite3") as progress:
                    cycle_events = progress.list_events(category="evolution", subject_id=cycle_id, limit=10)
                self.assertEqual(cycle_events[0]["event"], "progress")
                updated_plan = knowledge.get_node(plan_id)
                self.assertEqual(updated_plan["metadata"]["execution_state"], "succeeded")
                self.assertEqual(updated_plan["metadata"]["independent_review_state"], "pending_distinct_reviewer")

    def test_failed_registered_suite_persists_failure_without_refuting_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)

                def fake_runner(_start_directory, _pattern, **kwargs):
                    result = kwargs["experiment_bridge"].record_result(
                        kwargs["manifest_id"], status="failed", duration_seconds=2.0,
                        metrics={"tests_run": 7, "failures": 1, "errors": 0, "skipped": 0},
                        summary="Allowlisted feedback research regression suite failed.",
                        exit_code=1, run_id=kwargs["run_id"],
                    )
                    return {"status": "failed", "exit_code": 1, "duration_seconds": 2.0,
                            "metrics": result["metrics"], "run_ref": result["run_ref"],
                            "experiment_record_error": False}

                with patch("muse.test_runner._run_isolated_suite", side_effect=fake_runner):
                    result = run_feedback_research_experiment(
                        workspace, knowledge, root / "progress.sqlite3",
                        proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                    )
                self.assertEqual(result["status"], "failed")
                failures = knowledge.list_nodes(node_type="failure", limit=20)
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]["metadata"]["epistemic_status"], "engineering_failure_not_claim_refutation")
                self.assertEqual(knowledge.get_node(plan_id)["metadata"]["execution_state"], "failed")


    def test_interrupted_feedback_experiment_is_resumable_and_not_recorded_as_failure(self):
        import threading

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            stop_event = threading.Event()
            stop_event.set()
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                with patch("muse.test_runner._run_isolated_suite", return_value={
                    "status": "interrupted", "exit_code": 130, "duration_seconds": 0.1,
                    "metrics": {"tests_run": 0, "failures": 0, "errors": 0, "skipped": 0},
                    "run_ref": None, "experiment_record_error": False,
                }) as runner:
                    result = run_feedback_research_experiment(
                        workspace, knowledge, root / "progress.sqlite3",
                        proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                        stop_event=stop_event,
                    )
                self.assertEqual(result["status"], "interrupted")
                self.assertIsNone(result["run_ref"])
                self.assertIs(runner.call_args.kwargs["stop_event"], stop_event)
                self.assertEqual(knowledge.list_nodes(node_type="failure", limit=20), [])
                runs = [node for node in knowledge.list_nodes(node_type="experiment", limit=50)
                        if node["external_ref"].startswith("experiment-run:")]
                self.assertEqual(runs, [])

    def test_real_feedback_experiment_stops_its_launched_child_and_remains_retryable(self):
        import subprocess
        import threading
        import time
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Path(__file__).resolve().parents[1]
            stop_event = threading.Event()
            stopper_threads = []
            original_popen = subprocess.Popen

            def launch_then_schedule_stop(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                stopper = threading.Thread(
                    target=lambda: (time.sleep(0.25), stop_event.set()), daemon=True,
                )
                stopper.start()
                stopper_threads.append(stopper)
                return process

            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                with patch("muse.test_runner.subprocess.Popen", side_effect=launch_then_schedule_stop):
                    result = run_feedback_research_experiment(
                        workspace, knowledge, root / "progress.sqlite3",
                        proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                        stop_event=stop_event,
                    )
                for stopper in stopper_threads:
                    stopper.join(timeout=2)
                self.assertEqual(result["status"], "interrupted")
                self.assertIsNone(result["run_ref"])
                self.assertTrue(result["manifest_id"])
                self.assertEqual(knowledge.list_nodes(node_type="failure", limit=20), [])
                runs = [node for node in knowledge.list_nodes(node_type="experiment", limit=100)
                        if node["external_ref"].startswith("experiment-run:")]
                self.assertEqual(runs, [])

    def test_distinct_reviewer_reviews_engineering_result_once_without_claim_promotion(self):
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        from muse.task_queue import TaskQueue

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                claim = knowledge.upsert_node(
                    "claim", "External hypothesis remains proposed", summary="Not factual yet.",
                    metadata={"author_id": "muse-autonomous-research"}, status="proposed",
                )
                reviews = ReviewStore(root / "reviews.sqlite3")
                try:
                    reviewer_calls = []
                    def reviewer(prompt):
                        reviewer_calls.append(json.loads(prompt))
                        return json.dumps({"approved": True, "summary": "Engineering test evidence reviewed.", "findings": []})
                    gate = IndependentReviewGate(reviews, reviewer_id="local-reviewer:second-model", call=reviewer)

                    def fake_runner(_start_directory, _pattern, **kwargs):
                        result = kwargs["experiment_bridge"].record_result(
                            kwargs["manifest_id"], status="succeeded", duration_seconds=1.0,
                            metrics={"tests_run": 7, "failures": 0, "errors": 0, "skipped": 0},
                            summary="Allowlisted feedback research regression suite passed.",
                            exit_code=0, run_id=kwargs["run_id"],
                        )
                        return {"status": "succeeded", "exit_code": 0, "duration_seconds": 1.0,
                                "metrics": result["metrics"], "run_ref": result["run_ref"],
                                "experiment_record_error": False}

                    with patch("muse.test_runner._run_isolated_suite", side_effect=fake_runner):
                        first = run_feedback_research_experiment(
                            workspace, knowledge, root / "progress.sqlite3",
                            proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                            review_gate=gate, reviewer_scope="distinct_model_same_local_endpoint",
                        )
                        second = run_feedback_research_experiment(
                            workspace, knowledge, root / "progress.sqlite3",
                            proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                            review_gate=gate, reviewer_scope="distinct_model_same_local_endpoint",
                        )

                    self.assertEqual(first["independent_review_state"], "reviewed_approved")
                    self.assertEqual(second["independent_review_state"], "reviewed_approved")
                    self.assertEqual(first["review_id"], second["review_id"])
                    self.assertEqual(first["review_node_id"], second["review_node_id"])
                    self.assertEqual(len(reviewer_calls), 1)
                    self.assertEqual(len(reviews.list_for_artifact(first["review_artifact_id"])), 1)
                    with TaskQueue(root / "progress.sqlite3") as queue:
                        self.assertEqual(queue.list_tasks(kind="review_followup_research", limit=10), [])
                    self.assertEqual(knowledge.get_node(claim["id"])["status"], "proposed")
                    self.assertFalse(reviewer_calls[0]["test_evidence"].find("raw_experiment_output") >= 0)
                    self.assertEqual(knowledge.get_node(plan_id)["metadata"]["independent_review_scope"], "distinct_model_same_local_endpoint")
                    review_nodes = [node for node in knowledge.list_nodes(node_type="decision", limit=20)
                                    if node.get("external_ref") == "feedback-research-review:" + first["review_id"]]
                    self.assertEqual(len(review_nodes), 1)
                    review_node = review_nodes[0]
                    self.assertTrue(review_node["metadata"]["approved"])
                    self.assertTrue(review_node["metadata"]["not_claim_evidence"])
                    self.assertTrue(review_node["metadata"]["not_code_change_approval"])
                    edge_targets = {row["target_id"] for row in knowledge.connection.execute(
                        "SELECT target_id FROM knowledge_edges WHERE source_id=?", (review_node["id"],)
                    ).fetchall()}
                    run_row = knowledge.connection.execute(
                        "SELECT id FROM knowledge_nodes WHERE external_ref=?", (first["run_ref"],)
                    ).fetchone()
                    self.assertIn(run_row["id"], edge_targets)
                    self.assertIn(plan_id, edge_targets)
                    self.assertEqual(knowledge.get_node(plan_id)["metadata"]["independent_review_node_id"], review_node["id"])
                finally:
                    reviews.close()


    def test_rejected_review_queues_one_idempotent_read_only_research_followup(self):
        import json
        from muse.review_gate import IndependentReviewGate, ReviewStore
        from muse.task_queue import TaskQueue

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = self._workspace(root)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                reviews = ReviewStore(root / "reviews.sqlite3")
                try:
                    reviewer_calls = []
                    def reviewer(prompt):
                        reviewer_calls.append(json.loads(prompt))
                        return json.dumps({
                            "approved": False,
                            "summary": "Coverage gap remains unresolved.",
                            "findings": [{
                                "severity": "medium",
                                "title": "Broader regression coverage required",
                                "detail": "The evidence covers only a small fixture.",
                            }],
                        })
                    gate = IndependentReviewGate(
                        reviews, reviewer_id="local-reviewer:second-model", call=reviewer,
                    )

                    def fake_runner(_start_directory, _pattern, **kwargs):
                        result = kwargs["experiment_bridge"].record_result(
                            kwargs["manifest_id"], status="succeeded", duration_seconds=1.0,
                            metrics={"tests_run": 7, "failures": 0, "errors": 0, "skipped": 0},
                            summary="Allowlisted feedback research regression suite passed.",
                            exit_code=0, run_id=kwargs["run_id"],
                        )
                        return {"status": "succeeded", "exit_code": 0, "duration_seconds": 1.0,
                                "metrics": result["metrics"], "run_ref": result["run_ref"],
                                "experiment_record_error": False}

                    with patch("muse.test_runner._run_isolated_suite", side_effect=fake_runner):
                        first = run_feedback_research_experiment(
                            workspace, knowledge, root / "progress.sqlite3",
                            proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                            review_gate=gate, reviewer_scope="distinct_model_other_loopback_endpoint_provider_unverified",
                        )
                        second = run_feedback_research_experiment(
                            workspace, knowledge, root / "progress.sqlite3",
                            proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                            review_gate=gate, reviewer_scope="distinct_model_other_loopback_endpoint_provider_unverified",
                        )

                    self.assertEqual(first["independent_review_state"], "reviewed_rejected")
                    self.assertEqual(second["independent_review_state"], "reviewed_rejected")
                    self.assertEqual(first["review_id"], second["review_id"])
                    self.assertEqual(first["review_followup_task_id"], second["review_followup_task_id"])
                    self.assertTrue(first["review_followup_task_id"])
                    self.assertEqual(len(reviewer_calls), 1)
                    with TaskQueue(root / "progress.sqlite3") as queue:
                        tasks = queue.list_tasks(kind="review_followup_research", limit=10)
                        self.assertEqual(len(tasks), 1)
                        task = tasks[0]
                        self.assertEqual(task["id"], first["review_followup_task_id"])
                        self.assertEqual(task["status"], "queued")
                        self.assertEqual(task["workload_class"], "research")
                        payload = task["payload"]
                        self.assertEqual(payload["task_scope"], "read_only_research")
                        self.assertTrue(payload["reviewer_output_is_untrusted_data"])
                        self.assertTrue(payload["requires_separate_code_change_approval"])
                        self.assertTrue(payload["not_claim_evidence"])
                        self.assertEqual(len(queue.events(task["id"])), 2)
                        self.assertEqual(
                            [event["event_type"] for event in queue.events(task["id"])],
                            ["ticket.created", "review.followup.requested"],
                        )
                    plan = knowledge.get_node(plan_id)
                    self.assertEqual(plan["metadata"]["review_followup_task_id"], first["review_followup_task_id"])
                finally:
                    reviews.close()

    def test_real_test_runner_executes_allowlisted_suite_and_persists_linked_result(self):
        source_workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "immutable-source-snapshot"
            workspace.mkdir()
            ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
            shutil.copytree(source_workspace / "src", workspace / "src", ignore=ignore)
            shutil.copytree(source_workspace / "tests", workspace / "tests", ignore=ignore)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                plan_id = self._plan(knowledge)
                first = run_feedback_research_experiment(
                    workspace, knowledge, root / "progress.sqlite3",
                    proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                )
                second = run_feedback_research_experiment(
                    workspace, knowledge, root / "progress.sqlite3",
                    proposal_id="proposal-1", feedback_id="feedback-1", plan_node_id=plan_id,
                )
                self.assertEqual(first["status"], "succeeded")

                self.assertGreater(first["metrics"]["tests_run"], 0)
                self.assertEqual(first["run_ref"], second["run_ref"])
                self.assertTrue(second["reused"])
                self.assertEqual(knowledge.get_node(plan_id)["metadata"]["execution_state"], "succeeded")
                self.assertFalse(knowledge.get_node(plan_id)["metadata"]["execution_is_claim_evidence"])


if __name__ == "__main__":
    unittest.main()
