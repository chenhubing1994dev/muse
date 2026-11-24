from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder, ProductBuildError


class LocalProductBuilderTests(unittest.TestCase):
    def make_store(self, root: Path) -> tuple[InitiativeStore, dict]:
        store = InitiativeStore(root / "data" / "initiatives.sqlite3")
        initiative = store.propose(
            "灵感实验台 <script>alert(1)</script>",
            kind="tool",
            creator_value="把象征灵感转化为可验证的软件工程实验",
            concept=json.dumps({
                "research_question": "如何分离稳定条件、变化事件与未解矛盾？",
                "key_discoveries": {
                    "creator_inspired_hypotheses": [
                        "分层记录稳定约束与变化事件。 Test: 比较重复选题率。 Falsifier: 重复率不下降。",
                        "优先选择最小可逆行动。 Test: 比较回滚成本。 Falsifier: 回滚成本没有下降。",
                    ],
                    "counter_evidence": ["目前没有独立实证证明该方法有效。"],
                    "uncertainties": ["仍需真实运行数据验证。"],
                    "testable_experiments": ["比较默认策略与候选策略的成功率，并记录失败样本。"],
                },
            }, ensure_ascii=False),
            first_step="建立可编辑的灵感与实验工作台",
            expected_output="可离线运行并导出数据的本地产品",
            interest_signals=["易经", "奇门", "紫微"],
            inspiration_refs=["research:fixture"],
            capability_gap="把研究洞察转化为可运行产品",
            priority=90,
        )
        return store, initiative

    def test_build_resolves_exact_initiative_id_without_bounded_list_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            store,initiative=self.make_store(root)
            try:
                with patch.object(InitiativeStore,"list",return_value=[]):
                    result=LocalProductBuilder(root).build(store,initiative["id"])
                self.assertFalse(result["validated"])
                self.assertEqual(result["status"], "draft")
                self.assertTrue(result["content_readiness"]["ready_for_human_review"])
            finally:
                store.close()

    def test_builds_interactive_offline_product_and_downloadable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                from muse.progress import new_evolution_cycle_id, ProgressEventStore
                cycle_id = new_evolution_cycle_id()
                result = LocalProductBuilder(root).build(
                    store, initiative["id"], evolution_cycle_id=cycle_id,
                )
                output = root / result["relative_path"]
                html = (output / "index.html").read_text(encoding="utf-8")
                readme = (output / "README.md").read_text(encoding="utf-8")
                manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                package = output / "product.zip"

                self.assertTrue(result["created"])
                self.assertEqual(result["evolution_cycle_id"], cycle_id)
                with ProgressEventStore(root / "data" / "tasks" / "muse.sqlite3") as progress:
                    cycle_events = progress.list_events(category="evolution", subject_id=cycle_id, limit=10)
                self.assertEqual(cycle_events[0]["event"], "progress")
                self.assertIn(initiative["id"], cycle_events[0]["summary"])
                self.assertFalse(result["validated"])
                self.assertFalse(result["publishable"])
                self.assertEqual(result["status"], "draft")
                self.assertTrue(package.is_file())
                self.assertTrue(result["package_sha256"])
                self.assertIn("稳定条件", html)
                self.assertIn("变化事件", html)
                self.assertIn("未解矛盾", html)
                self.assertIn("localStorage", html)
                self.assertIn("导出 JSON", html)
                self.assertIn("待验证假设", html)
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
                self.assertNotIn("https://", html)
                self.assertIn("双击打开", readme)
                self.assertEqual(manifest["validation"]["offline_product"], "passed")
                self.assertEqual(manifest["title"], initiative["title"])
                self.assertEqual(manifest["status"], "draft")
                self.assertEqual(manifest["builder_version"], 5)
                self.assertFalse(manifest["validated"])
                self.assertFalse(manifest["publishable"])
                self.assertFalse(manifest["execution"]["network_access"])
                self.assertFalse(manifest["execution"]["arbitrary_code_executed"])
                self.assertTrue(manifest["execution"]["browser_local_storage_only"])
                with zipfile.ZipFile(package) as archive:
                    self.assertEqual(set(archive.namelist()), {"index.html", "README.md"})
                    self.assertEqual(archive.testzip(), None)
                self.assertEqual(store.list(status="testing"), [])
                self.assertEqual(store.get_by_id(initiative["id"])["status"], "proposed")
                self.assertEqual(store.list(status="shipped"), [])
            finally:
                store.close()

    def test_product_seeds_unreviewed_sources_counter_candidates_and_experiment_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = InitiativeStore(root / "data" / "initiatives.sqlite3")
            try:
                initiative = store.propose(
                    "粤语证据语气校准实验台", kind="tool",
                    creator_value="用可证伪实验校准粤语旁白中的证据语气",
                    concept=json.dumps({
                        "research_question": "限定语后的停顿能否提升延迟回忆？",
                        "creator_hypothesis_summary": "假设：250–450ms pause_after 可提升限定语延迟回忆；回忆提升不足10个百分点即证伪。",
                        "key_discoveries": {
                            "creator_inspired_hypotheses": ["250–450ms clause-boundary pause improves delayed recall."],
                            "uncertainties": ["All retrieved sources remain unreviewed candidates."],
                            "sources": [
                                {"title": "Support candidate", "url": "https://example.test/support", "direction": "support_search_candidate", "review_state": "unreviewed", "quality": "unassessed", "independence": "unassessed"},
                                {"title": "Counter candidate", "url": "https://example.test/counter", "direction": "counter_search_candidate", "review_state": "unreviewed", "quality": "unassessed", "independence": "unassessed"},
                            ],
                            "counter_evidence": ["Counter candidate: pause duration may reduce perceived naturalness."],
                            "testable_experiments": ["Compare default pauses, 250–450ms, and 600ms after the same clause boundary."],
                            "research_domains": ["粤语语音学与韵律", "停顿与话语分段"],
                            "tags": ["粤语旁白", "证据语气校准"],
                            "epistemic_status": "creator_inspired_hypothesis_only",
                            "evidence_review_state": "unreviewed",
                        },
                    }, ensure_ascii=False),
                    first_step="执行随机对照旁白实验",
                    expected_output="包含支持与反证候选的离线实验台",
                    priority=80,
                )
                result = LocalProductBuilder(root).build(store, initiative["id"])
                output = root / result["relative_path"]
                html = (output / "index.html").read_text(encoding="utf-8")
                readme = (output / "README.md").read_text(encoding="utf-8")
                manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                for value in (
                    "Support candidate", "Counter candidate", "https://example.test/support",
                    "counter_search_candidate", "粤语语音学与韵律", "粤语旁白",
                    "Compare default pauses, 250–450ms, and 600ms", "creator_inspired_hypothesis_only",
                    "未经复核，不构成结论",
                ):
                    self.assertIn(value, html)
                self.assertNotIn('href="https://', html)
                self.assertNotIn('src="https://', html)
                self.assertIn("All source entries are unreviewed candidates", readme)
                self.assertEqual(manifest["source_candidate_count"], 2)
                self.assertEqual(manifest["counter_evidence_candidate_count"], 1)
                self.assertEqual(manifest["epistemic_status"], "creator_inspired_hypothesis_only")
                self.assertEqual(manifest["evidence_review_state"], "unreviewed")
                self.assertFalse(manifest["auto_publish"])
                self.assertTrue(manifest["conclusion_promotion_requires_independent_review"])
                self.assertEqual(manifest["status"], "draft")
                self.assertEqual(manifest["content_readiness"]["status"], "ready_for_human_review")
                self.assertFalse(manifest["validated"])
                self.assertFalse(result["validated"])
            finally:
                store.close()

    def test_build_is_idempotent_and_verifies_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                builder = LocalProductBuilder(root)
                first = builder.build(store, initiative["id"])
                second = builder.build(store, initiative["id"])
                self.assertTrue(first["created"])
                self.assertFalse(second["created"])
                self.assertFalse(second["validated"])
                self.assertEqual(second["status"], "draft")
                self.assertEqual(first["input_sha256"], second["input_sha256"])
                self.assertEqual(first["package_sha256"], second["package_sha256"])
                artifact_events = [
                    event for event in store.list_events(initiative_id=initiative["id"])
                    if event["event_type"] == "product_built"
                ]
                self.assertEqual(artifact_events, [])
                self.assertEqual(store.get_by_id(initiative["id"])["status"], "proposed")

                verified = builder.verify(store, initiative["id"])
                self.assertFalse(verified["validated"])
                self.assertEqual(verified["status"], "draft")
                self.assertEqual(verified["package_sha256"], first["package_sha256"])
            finally:
                store.close()

    def test_incomplete_content_remains_draft_without_validated_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = InitiativeStore(root / "data" / "initiatives.sqlite3")
            initiative = store.propose(
                "缺少实验的产品草稿", kind="tool",
                creator_value="将研究想法转化为可验证产品",
                concept=json.dumps({
                    "research_question": "该方法是否能降低创作返工？",
                    "key_discoveries": {"creator_inspired_hypotheses": ["明确记录失败与回滚成本。"]},
                }, ensure_ascii=False),
                first_step="建立最小实验工作台",
                expected_output="可离线使用的产品原型",
            )
            try:
                result = LocalProductBuilder(root).build(store, initiative["id"])
                output = root / result["relative_path"]
                manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                self.assertFalse(result["validated"])
                self.assertEqual(result["status"], "draft")
                self.assertIn("testable_experiment_or_acceptance_criteria", result["content_readiness"]["missing_requirements"])
                self.assertEqual(manifest["validation"]["package_integrity"], "passed")
                self.assertEqual(manifest["content_readiness"]["status"], "incomplete")
                self.assertEqual(store.get_by_id(initiative["id"])["status"], "proposed")
                artifact_events = [
                    event for event in store.list_events(initiative_id=initiative["id"])
                    if event["event_type"] == "product_built"
                ]
                self.assertEqual(artifact_events, [])
                second = LocalProductBuilder(root).build(store, initiative["id"])
                self.assertFalse(second["validated"])
                self.assertEqual(second["status"], "draft")
            finally:
                store.close()

    def test_refuses_modified_existing_product_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                builder = LocalProductBuilder(root)
                result = builder.build(store, initiative["id"])
                target = root / result["relative_path"] / "index.html"
                target.write_text("user edit", encoding="utf-8")
                with self.assertRaises(ProductBuildError):
                    builder.build(store, initiative["id"])
                self.assertEqual(target.read_text(encoding="utf-8"), "user edit")
            finally:
                store.close()

    def test_rejects_invalid_ids_and_non_buildable_initiatives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                builder = LocalProductBuilder(root)
                with self.assertRaises(ValueError):
                    builder.build(store, "../escape")
                store.update(initiative["id"], status="rejected")
                with self.assertRaises(ProductBuildError):
                    builder.build(store, initiative["id"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
