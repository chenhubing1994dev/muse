from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.status_api import build_initiative_timeline
from muse.video_product_builder import VideoProductBuilder


class UnifiedInitiativeLedgerTests(unittest.TestCase):
    def test_research_counterevidence_failure_feedback_and_video_share_one_initiative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            knowledge_db = root / "data" / "research" / "knowledge.sqlite3"
            cycle_id = "cycle_" + "f" * 32

            with InitiativeStore(initiative_db) as initiatives:
                initiative = initiatives.propose(
                    "统一研究与视频作品", kind="creative_output",
                    creator_value="把研究知识、创作资产和经验沉淀为可验证能力",
                    concept="研究 idea/claim/反面证据、视频产物和改进实验共享一条账本",
                    first_step="将研究图谱节点关联到创作项目",
                    expected_output="MP4、工程 ZIP、证据链和验证记录",
                )
                with ResearchKnowledgeStore(knowledge_db) as knowledge:
                    idea = knowledge.upsert_node(
                        "idea", "古城与星河的创意假设",
                        metadata={"epistemic_status": "hypothesis"},
                    )
                    experiment = knowledge.upsert_node(
                        "experiment", "镜头节奏实验",
                        metadata={"evolution_cycle_ids": [cycle_id], "epistemic_status": "engineering_validation"},
                    )
                    claim = knowledge.upsert_node(
                        "claim", "镜头节奏能提升叙事连贯性",
                        metadata={"epistemic_status": "unverified_claim"},
                    )
                    counter_source = knowledge.upsert_node(
                        "source", "反面证据来源",
                        external_ref="https://example.invalid/counter-evidence",
                    )
                    failure = knowledge.record_failure(
                        "镜头合成失败", summary="素材编码参数不兼容。",
                        signature="video-encode:fixture", reason_to_avoid="不要重复使用未经验证的编码参数。",
                        task_id="research-run-fixture",
                    )
                    for node, relationship in (
                        (idea, "inspired_by"),
                        (experiment, "tested_by"),
                        (claim, "related"),
                        (counter_source, "counter_evidence"),
                        (failure, "addresses_gap"),
                    ):
                        knowledge.link_node_to_initiative(
                            initiatives, initiative["id"], node["id"],
                            relationship=relationship, evolution_cycle_id=cycle_id,
                        )
                initiatives.record_knowledge_link(
                    initiative["id"], "a" * 32, node_type="feedback",
                    relationship="improvement_candidate", evolution_cycle_id=cycle_id,
                )
                initiatives.record_knowledge_link(
                    initiative["id"], "b" * 32, node_type="improvement_proposal",
                    relationship="improvement_candidate", evolution_cycle_id=cycle_id,
                )

            source_mp4 = root / "recovered.mp4"
            source_mp4.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            product = VideoProductBuilder(root).package_generated_clip(
                "统一账本短片", "古城上空的星河缓缓展开", source_mp4,
                generation_metadata={"status": "completed", "model": "Wan 2.1", "seed": 77},
                creator_initiative_id=initiative["id"], evolution_cycle_id=cycle_id,
            )

            timeline = build_initiative_timeline(initiative_db, initiative["id"])
            linked = [event for event in timeline["events"] if event["event_type"] == "knowledge_linked"]
            product_events = [event for event in timeline["events"] if event["event_type"] == "product_built"]
            relationships = {event["details"]["relationship"] for event in linked}

            self.assertTrue(timeline["history_complete"])
            self.assertEqual(len(linked), 8)
            self.assertEqual(product["knowledge_linkage"]["already_linked_cycle_nodes"], 5)
            self.assertEqual(product["knowledge_linkage"]["new_cycle_links"], 0)
            self.assertIn("counter_evidence", relationships)
            self.assertIn("addresses_gap", relationships)
            self.assertIn("improvement_candidate", relationships)
            self.assertEqual(len(product_events), 1)
            metadata = product_events[0]["details"]["artifact_metadata"]
            self.assertEqual(metadata["mp4_sha256"], product["mp4_sha256"])
            self.assertEqual(metadata["manifest_sha256"], product["manifest_sha256"])
            self.assertEqual(metadata["evolution_cycle_id"], cycle_id)
            self.assertEqual(product["creator_initiative_id"], initiative["id"])
            self.assertEqual(product["ledger_event_id"], product_events[0]["id"])


if __name__ == "__main__":
    unittest.main()
