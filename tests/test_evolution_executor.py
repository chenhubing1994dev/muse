from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path


class _ApprovedImprovementStore:
    def __init__(self) -> None:
        self.calls = []
        self.status = "approved"

    def get(self, proposal_id: str):
        return {"id": proposal_id, "status": self.status}

    def validate_and_promote(self, proposal_id: str, **kwargs):
        self.calls.append((proposal_id, kwargs))
        self.status = "implemented"
        return {"status": "implemented", "proposal": {"id": proposal_id}}

    def transition(self, proposal_id: str, status: str, **kwargs):
        self.status = status
        return {"status": status}


class EvolutionExecutorTests(unittest.TestCase):
    def _payload(self, path: Path) -> dict:
        return {
            "kind": "approved_code_change", "_approved": True,
            "approved_by": "owner", "_approval": {"approved_by": "owner"},
            "proposal_id": "proposal-1",
            "patches": [{"path": "src/example.py", "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "old_text": "VALUE = 1", "new_text": "VALUE = 2"}],
        }

    def test_approved_patch_runs_baseline_candidate_and_promotes(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            store = _ApprovedImprovementStore()
            phases = []

            def run_tests(*, phase, fingerprints, task_id):
                phases.append((phase, dict(fingerprints), task_id))
                return {"run_ref": f"experiment-run:{phase}", "status": "succeeded" if phase == "candidate" else "failed"}

            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                run_tests=run_tests, implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "implemented")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\\n")
            self.assertEqual([item[0] for item in phases], ["baseline", "candidate"])
            self.assertNotEqual(phases[0][1], phases[1][1])
            self.assertEqual(store.calls[0][1]["baseline_run_ref"], "experiment-run:baseline")
            self.assertEqual(store.calls[0][1]["candidate_run_ref"], "experiment-run:candidate")

    def test_approved_patch_forwards_explicit_capability_upgrade_target(self) -> None:
        from muse.capabilities import CapabilityRegistry
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            store = _ApprovedImprovementStore()
            with CapabilityRegistry(root / "capabilities.sqlite3") as registry:
                registry.register(
                    capability_id="research.core", domain="research", name="Core research",
                    description="Research workflow capability", status="unverified",
                    capabilities=("research",), trust="local_test", license="not_applicable",
                )
                executor = ApprovedCodeChangeExecutor(
                    root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                    run_tests=lambda *, phase, **_: {
                        "run_ref": f"experiment-run:{phase}",
                        "status": "failed" if phase == "baseline" else "succeeded",
                    }, implementer_id="builder-1", capability_registry=registry,
                )
                payload = self._payload(target)
                payload["capability_id"] = "research.core"
                result = executor.execute(payload, task_id="ticket-capability")
                self.assertEqual(result["status"], "implemented")
                self.assertIs(store.calls[0][1]["capability_registry"], registry)
                self.assertEqual(store.calls[0][1]["capability_id"], "research.core")

    def test_unapproved_or_unapproved_proposal_never_mutates_source(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            store = _ApprovedImprovementStore()
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                run_tests=lambda **_: self.fail("tests must not run"), implementer_id="builder-1",
            )
            payload = self._payload(target)
            payload["_approved"] = False
            with self.assertRaises(PermissionError):
                executor.execute(payload, task_id="ticket-1")
            payload = self._payload(target)
            store.get = lambda _proposal_id: {"id": "proposal-1", "status": "proposed"}
            with self.assertRaises(ValueError):
                executor.execute(payload, task_id="ticket-1")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\\n")

    def test_source_change_during_baseline_blocks_patch_without_overwrite(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            phases = []

            def run_tests(*, phase, **_):
                phases.append(phase)
                if phase == "baseline":
                    target.write_text("VALUE = 99\\n", encoding="utf-8")
                return {"run_ref": f"experiment-run:{phase}", "status": "succeeded"}

            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=_ApprovedImprovementStore(), experiment_bridge=object(),
                review_gate=object(), run_tests=run_tests, implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "blocked_source_changed")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 99\\n")
            self.assertEqual(phases, ["baseline"])

    def test_failed_candidate_is_rolled_back(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            phases = []
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=_ApprovedImprovementStore(), experiment_bridge=object(),
                review_gate=object(),
                run_tests=lambda *, phase, **_: phases.append(phase) or {
                    "run_ref": f"experiment-run:{phase}", "status": "failed" if phase == "candidate" else "succeeded"},
                implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\\n")
            self.assertEqual(phases, ["baseline", "candidate"])

    def test_source_change_after_candidate_blocks_promotion(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            store = _ApprovedImprovementStore()
            def run_tests(*, phase, **_):
                if phase == "candidate":
                    target.write_text("VALUE = 99\\n", encoding="utf-8")
                return {"run_ref": f"experiment-run:{phase}", "status": "succeeded"}
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                run_tests=run_tests, implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "source_changed_after_candidate")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 99\\n")
            self.assertEqual(store.calls, [])

    def test_source_change_during_review_is_not_overwritten(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor
        class ChangingStore(_ApprovedImprovementStore):
            def __init__(self, target):
                super().__init__()
                self.target = target
                self.transitions = []
            def validate_and_promote(self, proposal_id, **kwargs):
                self.target.write_text("VALUE = 99\\n", encoding="utf-8")
                return {"status": "implemented", "proposal": {"id": proposal_id}}
            def transition(self, proposal_id, status, **kwargs):
                self.transitions.append(status)
                return {"status": status}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")
            store = ChangingStore(target)
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                run_tests=lambda *, phase, **_: {"run_ref": f"experiment-run:{phase}", "status": "succeeded"},
                implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "source_changed_after_review")
            self.assertEqual(store.transitions, ["rolled_back"])
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 99\\n")

    def test_rollback_refuses_to_overwrite_concurrent_change(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\\n", encoding="utf-8")

            def run_tests(*, phase, **_):
                if phase == "candidate":
                    target.write_text("VALUE = 99\\n", encoding="utf-8")
                    return {"run_ref": "experiment-run:candidate", "status": "failed"}
                return {"run_ref": "experiment-run:baseline", "status": "succeeded"}

            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=_ApprovedImprovementStore(), experiment_bridge=object(),
                review_gate=object(), run_tests=run_tests, implementer_id="builder-1",
            )
            result = executor.execute(self._payload(target), task_id="ticket-1")
            self.assertEqual(result["status"], "rollback_blocked")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 99\\n")

    def test_evolution_journal_rejects_non_durable_memory_database(self) -> None:
        from muse.evolution_journal import EvolutionJournal
        with self.assertRaises(ValueError):
            EvolutionJournal(":memory:")

    def test_crash_recovery_restores_candidate_after_interrupted_execution(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor
        from muse.evolution_journal import EvolutionJournal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            original = b"VALUE = 1\\n"
            candidate = b"VALUE = 2\\n"
            target.write_bytes(original)
            journal_path = root / "journal.sqlite3"
            journal = EvolutionJournal(journal_path)
            journal.begin(task_id="ticket-crash", proposal_id="proposal-1", files=[{
                "relative_path": "src/example.py", "original_bytes": original,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            }])
            target.write_bytes(candidate)
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=_ApprovedImprovementStore(), experiment_bridge=object(),
                review_gate=object(), run_tests=lambda **_: self.fail("recovery must not run tests"),
                implementer_id="builder-1", journal_database=journal_path,
            )
            results = executor.recover_incomplete_transactions()
            self.assertEqual(results[0]["status"], "rolled_back")
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(journal.list_incomplete(), [])

    def test_crash_recovery_preserves_unrecognized_concurrent_edit(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor
        from muse.evolution_journal import EvolutionJournal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            original, candidate, concurrent = b"VALUE = 1\\n", b"VALUE = 2\\n", b"VALUE = 99\\n"
            target.write_bytes(original)
            journal_path = root / "journal.sqlite3"
            journal = EvolutionJournal(journal_path)
            journal.begin(task_id="ticket-crash", proposal_id="proposal-1", files=[{
                "relative_path": "src/example.py", "original_bytes": original,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            }])
            target.write_bytes(concurrent)
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=_ApprovedImprovementStore(), experiment_bridge=object(),
                review_gate=object(), run_tests=lambda **_: self.fail("blocked recovery must not run tests"),
                implementer_id="builder-1", journal_database=journal_path,
            )
            results = executor.recover_incomplete_transactions()
            self.assertEqual(results[0]["status"], "blocked")
            self.assertEqual(target.read_bytes(), concurrent)

    def test_crash_after_promotion_keeps_candidate_and_commits_journal(self) -> None:
        from muse.evolution_executor import ApprovedCodeChangeExecutor
        from muse.evolution_journal import EvolutionJournal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "src" / "example.py"
            target.parent.mkdir()
            original, candidate = b"VALUE = 1\\n", b"VALUE = 2\\n"
            target.write_bytes(original)
            journal_path = root / "journal.sqlite3"
            journal = EvolutionJournal(journal_path)
            journal.begin(task_id="ticket-crash", proposal_id="proposal-1", files=[{
                "relative_path": "src/example.py", "original_bytes": original,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
            }])
            target.write_bytes(candidate)
            store = _ApprovedImprovementStore()
            store.status = "implemented"
            executor = ApprovedCodeChangeExecutor(
                root, improvement_store=store, experiment_bridge=object(), review_gate=object(),
                run_tests=lambda **_: self.fail("committed recovery must not run tests"),
                implementer_id="builder-1", journal_database=journal_path,
            )
            results = executor.recover_incomplete_transactions()
            self.assertEqual(results[0]["status"], "committed")
            self.assertEqual(target.read_bytes(), candidate)
            self.assertEqual(journal.list_incomplete(), [])

    def test_isolated_test_adapter_persists_comparable_experiment_runs(self) -> None:
        from muse.evolution_executor import build_isolated_unittest_adapter
        from muse.experiment_bridge import ExperimentBridge
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The snapshot runner imports from this isolated src tree.
            (root / "src" / "muse").mkdir(parents=True)
            (root / "src" / "muse" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "muse" / "marker.py").write_text("VALUE = " + repr("sandbox") + chr(10), encoding="utf-8")

            tests = root / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text(
                chr(10).join([
                    "import unittest", "from pathlib import Path", "from muse.marker import VALUE",
                    "class SampleTests(unittest.TestCase):", "    def test_ok(self):",
                    "        Path('runner-cwd-marker').write_text('ok')",
                    "        self.assertEqual(VALUE, 'sandbox')",
                ]),
                encoding="utf-8",
            )
            knowledge = ResearchKnowledgeStore(root / "knowledge.sqlite3")
            try:
                bridge = ExperimentBridge(knowledge)
                adapter = build_isolated_unittest_adapter(
                    root, experiment_bridge=bridge, progress_database=root / "progress.sqlite3",
                    budget_seconds=30, memory_budget_mb=512,
                )
                baseline = adapter(phase="baseline", fingerprints={"src/example.py": "a" * 64}, task_id="ticket-1")
                candidate = adapter(phase="candidate", fingerprints={"src/example.py": "b" * 64}, task_id="ticket-1")
                self.assertFalse((root / "runner-cwd-marker").exists())

                self.assertEqual(baseline["status"], "succeeded")
                self.assertEqual(candidate["status"], "succeeded")
                self.assertTrue(baseline["run_ref"].startswith("experiment-run:"))
                self.assertTrue(candidate["run_ref"].startswith("experiment-run:"))
                base_row = knowledge.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?", (baseline["run_ref"],)
                ).fetchone()
                candidate_row = knowledge.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?", (candidate["run_ref"],)
                ).fetchone()
                base_node, candidate_node = knowledge.get_node(base_row["id"]), knowledge.get_node(candidate_row["id"])
                base_manifest = knowledge.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    (f"experiment-manifest:{base_node['metadata']['manifest_id']}",),
                ).fetchone()
                candidate_manifest = knowledge.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    (f"experiment-manifest:{candidate_node['metadata']['manifest_id']}",),
                ).fetchone()
                base_meta, candidate_meta = knowledge.get_node(base_manifest["id"])["metadata"], knowledge.get_node(candidate_manifest["id"])["metadata"]
                self.assertEqual(base_meta["argv"], candidate_meta["argv"])
                self.assertEqual(base_meta["environment"], candidate_meta["environment"])
                self.assertNotEqual(base_meta["code_fingerprints"], candidate_meta["code_fingerprints"])
            finally:
                knowledge.close()

