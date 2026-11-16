from __future__ import annotations

import tempfile
import unittest
from datetime import date
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from muse.research_knowledge import ResearchKnowledgeStore
from muse.ziwei_adapter import sync_ziwei_knowledge_catalog


class CatalogStatus(Enum):
    VERIFIED = "verified"


class CatalogLayer(Enum):
    ORIGINAL = "original"


class CatalogAspect(Enum):
    INTERPRETATION = "interpretation"


class ZiweiKnowledgeAdapterTests(unittest.TestCase):
    def _catalog(self):
        source = SimpleNamespace(
            id="source.classic.volume1",
            title="紫微斗數全書卷一",
            edition="fixed-edition-2026-01",
            url="https://example.org/classic/volume1",
            layer=CatalogLayer.ORIGINAL,
            lineage_id="lineage.wikibase",
            provenance="固定版本录文",
            claims="支持书中原文定位",
            limits="未核纸本底本",
            rights="仅记录必要短引及出处",
            status=CatalogStatus.VERIFIED,
            authors=("古籍作者",),
            checked_on=date(2026, 11, 2),
        )
        citation = SimpleNamespace(
            id="citation.ziwei.nature",
            source_id=source.id,
            locator="诸星问答论/紫微",
            quote="紫微屬土，乃中天之尊星為帝座。",
            layer=CatalogLayer.ORIGINAL,
            aspects=(CatalogAspect.INTERPRETATION,),
            limits="仅记录原文称述，不认证预测效力",
        )
        concept = SimpleNamespace(
            id="concept.star.ziwei",
            name="紫微",
            kind=SimpleNamespace(value="星曜"),
            aliases=("Ziwei",),
            related_ids=(),
            options=(),
        )
        variant = SimpleNamespace(
            id="variant.star.ziwei",
            name="紫微安星规则",
            definition="按具名规则安置紫微星",
            scope="本命盘星曜位置",
            concept_ids=(concept.id,),
            tracking_url="https://example.org/rules/ziwei",
            aliases=(),
            school_ids=("school.classic",),
            conditions=("已给出有效生日",),
            option=None,
            implementation="ziwei.stars",
        )
        entry = SimpleNamespace(
            id="entry.interpretation.ziwei",
            statement="古籍将紫微称为帝座。",
            concept_ids=(concept.id,),
            variant_ids=(variant.id,),
            citation_ids=(citation.id,),
            premises=("仅描述该文本的用语",),
            premise_status=SimpleNamespace(value="complete"),
            show_by_default=False,
            curation=SimpleNamespace(value="curated"),
            limits="不认证现实预测",
            exceptions=(),
            alternatives=("其他流派可能采用不同解释",),
            recheck="对照纸本影像",
            tracking_url="https://example.org/issues/1",
        )
        return SimpleNamespace(
            concepts=(concept,),
            variants=(variant,),
            sources=(source,),
            citations=(citation,),
            entries=(entry,),
        )

    def test_sync_imports_provenance_graph_without_copying_upstream_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                result = sync_ziwei_knowledge_catalog(
                    store, catalog=self._catalog(), catalog_version="0.4.1"
                )
                self.assertEqual(result["catalog_version"], "0.4.1")
                self.assertEqual(result["counts"], {
                    "sources": 1, "citations": 1, "concepts": 1, "variants": 1, "entries": 1,
                })
                self.assertEqual(result["quote_bodies_imported"], 0)

                citation = store.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    ("ziwei-citation:citation.ziwei.nature",),
                ).fetchone()
                source = store.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    ("ziwei-source:source.classic.volume1",),
                ).fetchone()
                claim = store.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    ("ziwei-entry:entry.interpretation.ziwei",),
                ).fetchone()
                self.assertIsNotNone(citation)
                self.assertIsNotNone(source)
                self.assertIsNotNone(claim)
                citation_node = store.get_node(citation["id"])
                claim_node = store.get_node(claim["id"])
                self.assertNotIn("quote", citation_node["metadata"])
                self.assertEqual(len(citation_node["metadata"]["quote_sha256"]), 64)
                self.assertEqual(claim_node["status"], "proposed")
                self.assertEqual(
                    claim_node["metadata"]["epistemic_status"],
                    "traditional_framework_statement_not_empirical",
                )
                self.assertTrue(claim_node["metadata"]["requires_independent_review"])
                self.assertFalse(claim_node["metadata"]["show_by_default"])
                edge_rows = store.connection.execute(
                    "SELECT source_id,target_id FROM knowledge_edges"
                ).fetchall()
                edges = {(row["source_id"], row["target_id"]) for row in edge_rows}
                self.assertIn((citation["id"], source["id"]), edges)
                self.assertIn((claim["id"], citation["id"]), edges)

    def test_sync_is_idempotent_and_preserves_human_review_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                first = sync_ziwei_knowledge_catalog(
                    store, catalog=self._catalog(), catalog_version="0.4.1"
                )
                claim = store.connection.execute(
                    "SELECT id FROM knowledge_nodes WHERE external_ref=?",
                    ("ziwei-entry:entry.interpretation.ziwei",),
                ).fetchone()
                node = store.get_node(claim["id"])
                store.upsert_node(
                    "claim", node["title"], summary=node["summary"],
                    external_ref=node["external_ref"],
                    metadata={"human_review_note": "保留人工复核意见"},
                )
                before = store.stats()
                second = sync_ziwei_knowledge_catalog(
                    store, catalog=self._catalog(), catalog_version="0.4.1"
                )
                after = store.stats()
                self.assertEqual(first["catalog_fingerprint"], second["catalog_fingerprint"])
                self.assertEqual(before["nodes"], after["nodes"])
                self.assertEqual(before["edges"], after["edges"])
                self.assertEqual(
                    store.get_node(claim["id"])["metadata"]["human_review_note"],
                    "保留人工复核意见",
                )

    def test_sync_rejects_dangling_citation_references_before_writing(self) -> None:
        catalog = self._catalog()
        catalog.citations = (
            SimpleNamespace(
                id="citation.orphan", source_id="missing.source", locator="missing",
                quote="", layer=CatalogLayer.ORIGINAL, aspects=(), limits="",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as store:
                with self.assertRaisesRegex(ValueError, "unknown source_id"):
                    sync_ziwei_knowledge_catalog(store, catalog=catalog, catalog_version="0.4.1")
                self.assertEqual(store.stats()["nodes"], 0)


if __name__ == "__main__":
    unittest.main()
