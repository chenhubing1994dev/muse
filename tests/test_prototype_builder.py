from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.prototype_builder import PrototypeBuildError, StaticPrototypeBuilder


class PrototypeBuilderTests(unittest.TestCase):
    def make_store(self, root: Path) -> tuple[InitiativeStore, dict]:
        store = InitiativeStore(root / "data" / "initiatives.sqlite3")
        initiative = store.propose(
            "Muse 原型 <script>alert(1)</script>",
            kind="tool",
            creator_value="把研究洞察变成可用工具",
            concept=json.dumps({
                "research_question": "如何将书签研究转为可测试原型？",
                "key_discoveries": {
                    "executive_summary": "两份研究资料提供了可追溯的背景线索 [S1][S2]。",
                    "creator_inspired_hypotheses": ["假设：来源关系图能减少重复研究。 Test: 比较重复率。 Falsifier: 重复率没有下降。"],
                    "counter_evidence": ["目前没有独立验证来源关系图的效果。"],
                    "design_implications_for_muse": ["先保存来源谱系与反证关系。"],
                    "uncertainties": ["尚未验证长期收益。"],
                },
                "report_path": "C:\\\\Users\\\\Test\\\\AppData\\\\Local\\\\Temp\\\\report.md",
            }, ensure_ascii=False),
            first_step="显示主题、来源线索和下一步实验",
            expected_output="可预览的本地静态研究卡片",
            interest_signals=["Chrome 书签", "玄学灵感"],
            inspiration_refs=["C:\\\\Users\\\\Test\\\\AppData\\\\Local\\\\Temp\\\\report.md"],
            capability_gap="研究提案到可交付原型",
            priority=90,
        )
        return store, initiative

    def test_builds_escaped_static_prototype_and_moves_only_to_testing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                result = StaticPrototypeBuilder(root).build(store, initiative["id"])
                output = root / result["relative_path"]
                html = (output / "index.html").read_text(encoding="utf-8")
                manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                self.assertTrue(result["validated"])
                self.assertTrue((output / "README.md").is_file())
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
                self.assertIn("如何将书签研究转为可测试原型？", html)
                self.assertIn("两份研究资料提供了可追溯的背景线索 [S1][S2]。", html)
                self.assertIn("来源关系图能减少重复研究", html)
                self.assertIn("report.md", html)
                self.assertNotIn("C:\\\\Users\\\\Test", html)
                self.assertNotIn("<script", html.casefold())
                self.assertFalse(manifest["execution"]["arbitrary_code_executed"])
                self.assertFalse(manifest["execution"]["network_access"])
                self.assertEqual(store.list(status="testing")[0]["id"], initiative["id"])
                self.assertNotEqual(store.list(status="shipped"), [initiative])
            finally:
                store.close()

    def test_build_is_idempotent_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                first = StaticPrototypeBuilder(root).build(store, initiative["id"])
                second = StaticPrototypeBuilder(root).build(store, initiative["id"])
                self.assertEqual(first["relative_path"], second["relative_path"])
                self.assertTrue(first["created"])
                self.assertFalse(second["created"])
            finally:
                store.close()

    def test_tampered_existing_prototype_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                result = StaticPrototypeBuilder(root).build(store, initiative["id"])
                index = root / result["relative_path"] / "index.html"
                index.write_text("tampered", encoding="utf-8")
                with self.assertRaises(PrototypeBuildError):
                    StaticPrototypeBuilder(root).build(store, initiative["id"])
            finally:
                store.close()

    def test_html_validator_rejects_scripts_and_remote_resources(self):
        unsafe = '<html><head><script src="https://example.org/x.js"></script></head><body><main><h1>x</h1></main></body></html>'
        with self.assertRaises(PrototypeBuildError):
            StaticPrototypeBuilder._validate_html(unsafe)

    def test_html_validator_rejects_event_handlers_css_urls_and_meta_refresh(self):
        unsafe_documents = (
            '<html><head></head><body><main><h1 onerror="alert(1)">x</h1></main></body></html>',
            '<html><head><style>body{background:url(https://example.org/a.png)}</style></head><body><main><h1>x</h1></main></body></html>',
            '<html><head><meta http-equiv="refresh" content="0;url=https://example.org"></head><body><main><h1>x</h1></main></body></html>',
            '<html><head></head><body><main><img srcset="https://example.org/a.png 1x"><h1>x</h1></main></body></html>',
        )
        for unsafe in unsafe_documents:
            with self.subTest(document=unsafe):
                with self.assertRaises(PrototypeBuildError):
                    StaticPrototypeBuilder._validate_html(unsafe)

    def test_invalid_initiative_id_and_rejected_initiative_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, initiative = self.make_store(root)
            try:
                with self.assertRaises(ValueError):
                    StaticPrototypeBuilder(root).build(store, "../outside")
                store.update(initiative["id"], status="rejected", outcome="not approved")
                with self.assertRaises(PrototypeBuildError):
                    StaticPrototypeBuilder(root).build(store, initiative["id"])
                self.assertFalse((root / "data" / "prototypes").exists())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
