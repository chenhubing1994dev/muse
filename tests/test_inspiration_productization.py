from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.inspiration_productization import (
    InspirationProductizationConflict,
    InspirationProductizationError,
    build_offline_inspiration_product,
)
from muse.product_builder import LocalProductBuilder


class OfflineInspirationProductizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.blueprint = self.root / "data" / "blueprints" / "inspiration.md"
        self.blueprint.parent.mkdir(parents=True)
        self.blueprint.write_text("# Offline blueprint\n", encoding="utf-8")
        self.report = {
            "synthesis_status": "local_offline_only",
            "synthesis": {
                "executive_summary": "本地资料只提供背景，象数内容仍是待检验假设。",
                "counter_evidence": ["当前本地资料不足以支持该假设。"],
                "testable_experiments": ["比较原型使用前后的任务完成率。"],
                "uncertainties": ["尚无独立外部来源核验。"],
            },
        }
        self.kwargs = {
            "workspace": self.root,
            "inspiration_id": "idea-ziwei-001",
            "title": "以结构化记忆启发创作工具",
            "summary": "探索如何把灵感转化为可复盘的实验。",
            "blueprint_path": "data/blueprints/inspiration.md",
            "report": self.report,
            "hypotheses": ["【象征启发／待验证，非证据】分层记录可能降低记忆污染。"],
            "symbolic_framework_id": "ziwei",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_verified_offline_product_without_dispatch_or_publication(self):
        result = build_offline_inspiration_product(**self.kwargs)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["initiative_status"], "proposed")
        self.assertTrue(result["verified"])
        self.assertEqual(result["package_integrity"], "passed")
        self.assertFalse(result["validated"])
        self.assertFalse(result["publishable"])
        self.assertFalse(result["published"])
        self.assertFalse(result["dispatch_started"])
        self.assertFalse(result["gpu_work_started"])
        self.assertTrue((self.root / result["product"]["package_path"]).is_file())
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            initiative = store.get_by_id(result["initiative_id"])
            verified = LocalProductBuilder(self.root).verify(store, result["initiative_id"])
        self.assertEqual(initiative["status"], "proposed")
        self.assertEqual(initiative["autonomy_level"], "draft")
        self.assertFalse(verified["validated"])
        self.assertEqual(verified["package_integrity"], "passed")
        concept = json.loads(initiative["concept"])
        epistemic = concept["inspiration_productization"]
        self.assertEqual(epistemic["epistemic_status"], "symbolic_hypothesis_only")
        self.assertEqual(epistemic["symbolic_framework_id"], "ziwei")
        self.assertEqual(epistemic["symbolic_framework_status"], "recognized_framework")
        self.assertFalse(epistemic["symbolic_inspiration_is_evidence"])
        self.assertFalse(epistemic["external_network_called"])
        self.assertIn("当前本地资料不足", concept["key_discoveries"]["counter_evidence"][0])
        self.assertIn("分层记录", concept["key_discoveries"]["creator_inspired_hypotheses"][0])

    def test_repeated_build_is_idempotent_for_unchanged_inspiration(self):
        first = build_offline_inspiration_product(**self.kwargs)
        second = build_offline_inspiration_product(**self.kwargs)
        self.assertEqual(first["initiative_id"], second["initiative_id"])
        self.assertEqual(first["product"]["input_sha256"], second["product"]["input_sha256"])
        self.assertEqual(first["product"]["package_sha256"], second["product"]["package_sha256"])
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            self.assertEqual(len(store.list(limit=20)), 1)

    def test_bounded_rows_stops_consuming_an_unbounded_generator(self):
        consumed = []

        def endless_rows():
            index = 0
            while True:
                consumed.append(index)
                yield f"hypothesis-{index}"
                index += 1

        with self.assertRaisesRegex(InspirationProductizationError, "exceeds 40 items"):
            build_offline_inspiration_product(**{**self.kwargs, "hypotheses": endless_rows()})
        self.assertEqual(len(consumed), 41)

    def test_rejects_external_research_or_non_offline_report(self):
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**{**self.kwargs, "allow_external_research": True})
        invalid = {**self.kwargs, "report": {**self.report, "synthesis_status": "completed"}}
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**invalid)

    def test_rejects_inconsistent_or_malformed_offline_provenance_flags(self):
        for field, value in (("external_network_called", True), ("allow_external_research", True)):
            invalid = {**self.kwargs, "report": {**self.report, field: value}}
            with self.subTest(field=field, value=value), self.assertRaises(InspirationProductizationError):
                build_offline_inspiration_product(**invalid)
        for field, value in (("external_network_called", "false"), ("allow_external_research", 0)):
            invalid = {**self.kwargs, "report": {**self.report, field: value}}
            with self.subTest(field=field, value=value), self.assertRaises(InspirationProductizationError):
                build_offline_inspiration_product(**invalid)

    def test_rejects_path_traversal_and_missing_blueprint(self):
        invalid = {**self.kwargs, "blueprint_path": "../outside.md"}
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**invalid)
        invalid = {**self.kwargs, "blueprint_path": "data/blueprints/missing.md"}
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**invalid)

    def test_rejects_conflicting_existing_deterministic_initiative(self):
        import hashlib
        inspiration_key = self.kwargs["inspiration_id"]
        suffix = hashlib.sha256(inspiration_key.encode("utf-8")).hexdigest()[:12]
        title = f"离线灵感原型｜{suffix}"
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            store.propose(
                title, kind="tool", creator_value="不同内容", concept="{}",
                first_step="先审查", expected_output="冲突记录",
                inspiration_refs=["other"], capability_gap="不同", priority=65,
                autonomy_level="draft",
            )
        with self.assertRaises(InspirationProductizationConflict):
            build_offline_inspiration_product(**self.kwargs)

    def test_preserves_unknown_symbolic_framework_without_misattribution(self):
        result = build_offline_inspiration_product(**{**self.kwargs, "symbolic_framework_id": "unknown-lens"})
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            initiative = store.get_by_id(result["initiative_id"])
        metadata = json.loads(initiative["concept"])["inspiration_productization"]
        self.assertEqual(metadata["symbolic_framework_id"], "unknown-lens")
        self.assertEqual(metadata["symbolic_framework_status"], "unrecognized_source_metadata")

    def test_preserves_all_four_symbolic_frameworks_as_hypotheses(self):
        frameworks = ["iching", "qimen", "ziwei", "liu_ren"]
        result = build_offline_inspiration_product(
            **{**self.kwargs, "symbolic_framework_id": None, "symbolic_framework_ids": frameworks}
        )
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            initiative = store.get_by_id(result["initiative_id"])
        metadata = json.loads(initiative["concept"])["inspiration_productization"]
        self.assertIsNone(metadata["symbolic_framework_id"])
        self.assertEqual(metadata["symbolic_framework_ids"], frameworks)
        self.assertEqual(metadata["symbolic_framework_status"], "recognized_frameworks")
        self.assertEqual(metadata["epistemic_status"], "symbolic_hypothesis_only")
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(
                **{**self.kwargs, "symbolic_framework_ids": ["iching", "iching"]}
            )

    def test_rejects_oversized_or_malformed_hypothesis_payload(self):
        invalid = {**self.kwargs, "hypotheses": ["x" * 2001]}
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**invalid)
        invalid = {**self.kwargs, "hypotheses": "not-a-list"}
        with self.assertRaises(InspirationProductizationError):
            build_offline_inspiration_product(**invalid)


    def test_rejects_product_without_a_testable_hypothesis(self):
        invalid = {**self.kwargs, "hypotheses": []}
        with self.assertRaisesRegex(InspirationProductizationError, "at least one testable hypothesis"):
            build_offline_inspiration_product(**invalid)

    def test_rejects_product_without_a_testable_experiment(self):
        report = {**self.report, "synthesis": {**self.report["synthesis"], "testable_experiments": []}}
        invalid = {**self.kwargs, "report": report, "experiments": None}
        with self.assertRaisesRegex(InspirationProductizationError, "at least one experiment"):
            build_offline_inspiration_product(**invalid)


if __name__ == "__main__":
    unittest.main()
