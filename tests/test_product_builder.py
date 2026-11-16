from __future__ import annotations

import json
import tempfile
import unittest
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
                self.assertTrue(result["validated"])
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
                self.assertEqual(manifest["builder_version"], 2)
                self.assertFalse(manifest["execution"]["network_access"])
                self.assertFalse(manifest["execution"]["arbitrary_code_executed"])
                self.assertTrue(manifest["execution"]["browser_local_storage_only"])
                with zipfile.ZipFile(package) as archive:
                    self.assertEqual(set(archive.namelist()), {"index.html", "README.md"})
                    self.assertEqual(archive.testzip(), None)
                self.assertEqual(store.list(status="testing")[0]["id"], initiative["id"])
                self.assertEqual(store.list(status="shipped"), [])
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
                self.assertTrue(second["validated"])
                self.assertEqual(first["input_sha256"], second["input_sha256"])
                self.assertEqual(first["package_sha256"], second["package_sha256"])
                artifact_events = [
                    event for event in store.list_events(initiative_id=initiative["id"])
                    if event["event_type"] == "product_built"
                ]
                self.assertEqual(len(artifact_events), 1)
                self.assertEqual(artifact_events[0]["details"]["package_sha256"], first["package_sha256"])

                verified = builder.verify(store, initiative["id"])
                self.assertTrue(verified["validated"])
                self.assertEqual(verified["package_sha256"], first["package_sha256"])
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
