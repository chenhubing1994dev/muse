from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.harness_evolution import (
    ExperienceLesson, ProbeRun, compare_probes, create_candidate,
    create_snapshot, lesson_from_decision,
)


class HarnessEvolutionTests(unittest.TestCase):
    def _runs(self, **candidate_overrides):
        base = ProbeRun("base", "base-candidate", "a" * 64, "b" * 64,
                        10, 2, 0, 1000, isolated=True)
        values = dict(run_id="candidate", candidate_id="cand-1",
                      environment_fingerprint="a" * 64,
                      input_fingerprint="b" * 64, tests_run=10,
                      failures=0, errors=0, duration_ms=800, isolated=True)
        values.update(candidate_overrides)
        return base, ProbeRun(**values)

    def test_snapshot_is_stable_and_hashes_only_explicit_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("print('hello')", encoding="utf-8")
            (root / "ignored.txt").write_text("not captured", encoding="utf-8")
            first = create_snapshot(root, ["src/a.py"], cycle_id="cycle-1",
                                    ticket_id="ticket-1", domain="research")
            second = create_snapshot(root, ["src/a.py"], cycle_id="cycle-1",
                                     ticket_id="ticket-1", domain="research")
            self.assertEqual(first, second)
            self.assertEqual(len(first.files), 1)
            self.assertEqual(first.files[0].size_bytes, 14)

    def test_snapshot_rejects_traversal_sensitive_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            for bad in ("../outside.txt", ".env", "secrets/key.txt", "C:\\temp.txt"):
                with self.subTest(path=bad), self.assertRaises(ValueError):
                    create_snapshot(root, [bad], cycle_id="c", ticket_id="t", domain="d")
            outside = root.parent / "outside-muse-harness-test.txt"
            outside.write_text("private", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(outside)
                with self.assertRaises(ValueError):
                    create_snapshot(root, ["link.txt"], cycle_id="c", ticket_id="t", domain="d")
            finally:
                outside.unlink(missing_ok=True)
            (root / "nested").mkdir()
            (root / "nested-link").symlink_to(root / "nested", target_is_directory=True)
            with self.assertRaises(ValueError):
                create_snapshot(root, ["nested-link/file.txt"], cycle_id="c", ticket_id="t", domain="d")

    def test_candidate_is_limited_to_baseline_paths_and_has_stable_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.md").write_text("baseline", encoding="utf-8")
            snapshot = create_snapshot(root, ["config.md"], cycle_id="c",
                                       ticket_id="t", domain="docs")
            candidate = create_candidate(snapshot, {"config.md": "candidate"})
            self.assertEqual(candidate, create_candidate(snapshot, {"config.md": "candidate"}))
            with self.assertRaisesRegex(ValueError, "not in baseline allowlist"):
                create_candidate(snapshot, {"other.md": "not allowed"})

    def test_probe_composition_adopts_only_comparable_improvement(self):
        baseline, candidate = self._runs()
        decision = compare_probes(baseline, candidate)
    def test_probe_composition_adopts_only_comparable_improvement(self):
        baseline, candidate = self._runs()
        decision = compare_probes(baseline, candidate)
        self.assertEqual(decision.outcome, "helped")
        self.assertTrue(decision.adopted)
        lesson = lesson_from_decision(decision, domain="tests", scope="unit suite")
        self.assertEqual(lesson.outcome, "helped")
        self.assertEqual(len(lesson.evidence_sha256), 64)

    def test_probe_composition_rejects_confounded_evidence(self):
        baseline, candidate = self._runs(environment_fingerprint="c" * 64)
        decision = compare_probes(baseline, candidate)
        self.assertEqual(decision.outcome, "confounded")
        self.assertFalse(decision.adopted)
        baseline, candidate = self._runs(isolated=False)
        self.assertEqual(compare_probes(baseline, candidate).outcome, "confounded")

    def test_probe_composition_rejects_failures_and_side_effects(self):
        baseline, candidate = self._runs(failures=1)
        self.assertEqual(compare_probes(baseline, candidate).outcome, "hurt")
        baseline, candidate = self._runs(side_effects=("wrote-outside-sandbox",))
        self.assertEqual(compare_probes(baseline, candidate).outcome, "confounded")

    def test_lesson_rejects_unverified_outcome_and_bad_confidence(self):
        with self.assertRaises(ValueError):
            ExperienceLesson("d", "maybe", "lesson", "a" * 64, "scope")
        with self.assertRaises(ValueError):
            ExperienceLesson("d", "helped", "lesson", "a" * 64, "scope",
                             confidence=1.5)



    def test_probe_workspace_copies_candidate_without_touching_source_and_cleans_owned_copy(self):
        from muse.harness_evolution import create_probe_workspace, cleanup_probe_workspace
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            temp_root = base / "experiments"
            source.mkdir()
            (source / "note.md").write_text("baseline", encoding="utf-8")
            snapshot = create_snapshot(source, ["note.md"], cycle_id="c", ticket_id="t", domain="docs")
            candidate = create_candidate(snapshot, {"note.md": "candidate"})
            probe = create_probe_workspace(source, temp_root, snapshot, candidate)
            copied = Path(probe.workspace_path)
            self.assertEqual((copied / "note.md").read_text(encoding="utf-8"), "candidate")
            self.assertEqual((source / "note.md").read_text(encoding="utf-8"), "baseline")
            self.assertTrue(Path(probe.marker_path).is_file())
            self.assertTrue(cleanup_probe_workspace(probe, temp_root))
            self.assertFalse(copied.exists())

    def test_probe_workspace_rejects_source_drift(self):
        from muse.harness_evolution import create_probe_workspace
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            item = source / "a.txt"
            item.write_text("before", encoding="utf-8")
            snapshot = create_snapshot(source, ["a.txt"], cycle_id="c", ticket_id="t", domain="d")
            item.write_text("after", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source drift"):
                create_probe_workspace(source, base / "experiments", snapshot)

    def test_probe_workspace_refuses_cleanup_when_marker_tampered(self):
        from muse.harness_evolution import create_probe_workspace, cleanup_probe_workspace
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "a.txt").write_text("safe", encoding="utf-8")
            snapshot = create_snapshot(source, ["a.txt"], cycle_id="c", ticket_id="t", domain="d")
            probe = create_probe_workspace(source, base / "experiments", snapshot)
            Path(probe.marker_path).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "marker mismatch"):
                cleanup_probe_workspace(probe, base / "experiments")
            self.assertTrue(Path(probe.workspace_path).exists())

    def test_probe_workspace_rejects_temp_root_nested_in_source(self):
        from muse.harness_evolution import create_probe_workspace
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "a.txt").write_text("safe", encoding="utf-8")
            snapshot = create_snapshot(source, ["a.txt"], cycle_id="c", ticket_id="t", domain="d")
            with self.assertRaisesRegex(ValueError, "disjoint"):
                create_probe_workspace(source, source / "experiments", snapshot)


    def test_static_probes_parse_only_and_never_execute_candidate_code(self):
        from muse.harness_evolution import create_probe_workspace, run_static_probes
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "safe.py").write_text("x = 1\n", encoding="utf-8")
            snapshot = create_snapshot(source, ["safe.py"], cycle_id="c", ticket_id="t", domain="code")
            candidate = create_candidate(snapshot, {"safe.py": "raise RuntimeError('must not execute')\n"})
            probe = create_probe_workspace(source, base / "experiments", snapshot, candidate)
            result = run_static_probes(probe)
            self.assertTrue(result.passed)
            self.assertEqual(result.checks[0]["validator"], "python-ast-parse")
            self.assertNotIn("execution", result.checks[0])

    def test_static_probes_report_syntax_errors_without_running_code(self):
        from muse.harness_evolution import create_probe_workspace, run_static_probes
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "safe.py").write_text("x = 1\n", encoding="utf-8")
            snapshot = create_snapshot(source, ["safe.py"], cycle_id="c", ticket_id="t", domain="code")
            candidate = create_candidate(snapshot, {"safe.py": "def broken(:\n"})
            probe = create_probe_workspace(source, base / "experiments", snapshot, candidate)
            result = run_static_probes(probe)
            self.assertFalse(result.passed)
            self.assertEqual(result.checks[0]["error_type"], "SyntaxError")


    def test_static_probes_reject_workspace_content_tampering(self):
        from muse.harness_evolution import create_probe_workspace, run_static_probes
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "a.py").write_text("x = 1\n", encoding="utf-8")
            snapshot = create_snapshot(source, ["a.py"], cycle_id="c", ticket_id="t", domain="code")
            probe = create_probe_workspace(source, base / "experiments", snapshot)
            (Path(probe.workspace_path) / "a.py").write_text("x = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content changed"):
                run_static_probes(probe)

    def test_static_probes_reject_unlisted_files(self):
        from muse.harness_evolution import create_probe_workspace, run_static_probes
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (source / "a.py").write_text("x = 1\n", encoding="utf-8")
            snapshot = create_snapshot(source, ["a.py"], cycle_id="c", ticket_id="t", domain="code")
            probe = create_probe_workspace(source, base / "experiments", snapshot)
            (Path(probe.workspace_path) / "extra.py").write_text("x = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                run_static_probes(probe)

    def test_probe_run_defaults_to_not_isolated(self):
        run = ProbeRun("r", "c", "a" * 64, "b" * 64, 1, 0, 0, 1)
        self.assertFalse(run.isolated)


if __name__ == "__main__":
    unittest.main()