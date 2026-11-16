from __future__ import annotations

import io
import unittest

from muse.test_runner import run_suite, _run_isolated_suite, main


class FakeProgress:
    def __init__(self):
        self.events = []

    def record(self, category, subject_id, event, *, summary="", error_code=None):
        self.events.append({
            "category": category, "subject_id": subject_id, "event": event,
            "summary": summary, "error_code": error_code,
        })


class TestRunnerTests(unittest.TestCase):
    def test_records_started_and_completed_without_test_payload(self):
        progress = FakeProgress()
        result = run_suite(
            unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]),
            progress_events=progress, stream=io.StringIO(), verbosity=0,
        )
        self.assertTrue(result.wasSuccessful())
        self.assertEqual([event["event"] for event in progress.events], ["started", "completed"])
        self.assertEqual(progress.events[0]["subject_id"], progress.events[1]["subject_id"])
        self.assertNotIn("test_passes", repr(progress.events))

    def test_records_failed_suite_with_bounded_error_code(self):
        progress = FakeProgress()
        result = run_suite(
            unittest.TestSuite([unittest.FunctionTestCase(lambda: (_ for _ in ()).throw(AssertionError("expected failure")))]),
            progress_events=progress, stream=io.StringIO(), verbosity=0,
        )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(progress.events[-1]["event"], "failed")
        self.assertEqual(progress.events[-1]["error_code"], "tests.failed")

    def test_test_runner_records_reproducible_experiment_result_without_raw_output(self):
        import tempfile
        from pathlib import Path
        from muse.experiment_bridge import ExperimentBridge
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Test runner integration", kind="test",
                    argv=("python", "-m", "unittest", "discover"),
                    environment={"python_version": "3.14"},
                    code_fingerprints={"tests/test_runner.py": "b" * 64},
                )
                result = run_suite(
                    unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]),
                    stream=io.StringIO(), verbosity=0, experiment_bridge=bridge,
                    manifest_id=manifest["manifest_id"], budget_seconds=30,
                )
                self.assertTrue(result.wasSuccessful())
                self.assertEqual(result.experiment_status, "succeeded")
                runs = [node for node in knowledge.list_nodes(node_type="experiment")
                        if node["external_ref"].startswith("experiment-run:")]
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["metadata"]["metrics"]["tests_run"], 1)
                self.assertNotIn("test_passes", str(runs[0]))

    def test_isolated_runner_records_manifest_result_and_enforces_runtime_budget(self):
        import tempfile
        from pathlib import Path
        from muse.experiment_bridge import ExperimentBridge
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_fast.py").write_text(
                chr(10).join(("import unittest", "class Fast(unittest.TestCase):", "    def test_ok(self): self.assertTrue(True)", "")),
                encoding="utf-8",
            )
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Isolated test suite", kind="test",
                    argv=("python", "-m", "unittest", "discover"),
                )
                result = _run_isolated_suite(
                    root, "test_fast.py", progress_events=None, experiment_bridge=bridge,
                    manifest_id=manifest["manifest_id"], budget_seconds=30, verbosity=0,
                )
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["metrics"]["tests_run"], 1)

    def test_isolated_runner_terminates_over_budget_child_process(self):
        import tempfile
        from pathlib import Path
        from muse.experiment_bridge import ExperimentBridge
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_slow.py").write_text(
                chr(10).join(("import time,unittest", "class Slow(unittest.TestCase):", "    def test_sleep(self): time.sleep(5)", "")),
                encoding="utf-8",
            )
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                bridge = ExperimentBridge(knowledge)
                manifest = bridge.register_manifest(
                    title="Over-budget test suite", kind="test",
                    argv=("python", "-m", "unittest", "discover"), timeout_seconds=1,
                )
                result = _run_isolated_suite(
                    root, "test_slow.py", progress_events=None, experiment_bridge=bridge,
                    manifest_id=manifest["manifest_id"], budget_seconds=1, verbosity=0,
                )
                self.assertEqual(result["status"], "budget_exceeded")
                self.assertEqual(result["exit_code"], 124)
                runs = [node for node in knowledge.list_nodes(node_type="experiment")
                        if node["external_ref"].startswith("experiment-run:")]
                self.assertEqual(runs[0]["metadata"]["status"], "budget_exceeded")

    def test_cli_main_records_isolated_experiment_manifest_and_result(self):
        import tempfile
        from pathlib import Path
        from muse.research_knowledge import ResearchKnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_cli.py").write_text(
                chr(10).join(("import unittest", "class Cli(unittest.TestCase):", "    def test_ok(self): self.assertTrue(True)", "")),
                encoding="utf-8",
            )
            result = main([
                "--start-directory", str(root), "--pattern", "test_cli.py",
                "--progress-db", str(root / "tasks.sqlite3"),
                "--knowledge-db", str(root / "knowledge.sqlite3"),
                "--budget-seconds", "30", "--memory-budget-mb", "512", "--verbosity", "0",
            ])
            self.assertEqual(result, 0)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                nodes = knowledge.list_nodes(node_type="experiment")
                self.assertTrue(any(node["external_ref"].startswith("experiment-manifest:") for node in nodes))
                self.assertTrue(any(node["external_ref"].startswith("experiment-run:") for node in nodes))

    def test_progress_store_failure_does_not_interrupt_test_execution(self):
        class BrokenProgress:
            def record(self, *_args, **_kwargs):
                raise OSError("telemetry unavailable")
        result = run_suite(
            unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]),
            progress_events=BrokenProgress(), stream=io.StringIO(), verbosity=0,
        )
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
