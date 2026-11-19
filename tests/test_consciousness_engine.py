"""Tests for the Phase 5 Muse consciousness/philosophy evolution engine."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.consciousness_engine import ConsciousnessEngine
from muse.philosophy import PhilosophyStore


VIDEO_META = {
    "style": "港风霓虹",
    "narration": "霓虹之下，记忆有迹可循",
    "asset_count": 12,
    "duration": 95.5,
    # privacy / unrelated fields must never reach the durable experience record
    "author_email": "creator@example.org",
    "local_file_path": "C:\\private\\draft.mp4",
    "viewer_user_id": "u-12345",
}


class ConsciousnessEngineTests(unittest.TestCase):
    def test_record_creation_experience_fields_and_no_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                exp = engine.record_creation_experience(VIDEO_META, "success", "开场节奏稳定，转场可复用")
                self.assertEqual(exp["kind"], "success")
                self.assertEqual(exp["epistemic_status"], "experience")
                metadata = json.loads(exp["metadata_json"])
                self.assertEqual(
                    set(metadata), {"style", "narration", "asset_count", "duration"}
                )
                self.assertEqual(metadata["style"], "港风霓虹")
                self.assertEqual(metadata["asset_count"], 12)
                blob = exp["metadata_json"] + exp["summary"]
                for leaked in ("creator@example.org", "draft.mp4", "u-12345"):
                    self.assertNotIn(leaked, blob)

    def test_record_creation_experience_kind_from_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                self.assertEqual(
                    engine.record_creation_experience(VIDEO_META, "failure", "素材缺失")["kind"],
                    "failure",
                )
                self.assertEqual(
                    engine.record_creation_experience(VIDEO_META, "reflection", "复盘节奏")["kind"],
                    "reflection",
                )
                with self.assertRaises(ValueError):
                    engine.record_creation_experience(VIDEO_META, "unknown", "x")

    def test_inject_inspiration_marks_symbolic_lenses_as_hypothesis(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                brief = engine.inject_inspiration(
                    "可逆的失败复盘",
                    symbolic_lenses=(
                        "iching",
                        {"id": "tarot", "name": "塔罗", "epistemic_status": "fact", "is_evidence": True},
                    ),
                )
                self.assertTrue(brief["principles"])
                self.assertEqual(len(brief["symbolic_lenses"]), 2)
                for lens in brief["symbolic_lenses"]:
                    self.assertIn(lens["epistemic_status"], {"hypothesis", "symbolic_only"})
                    self.assertIs(lens["is_evidence"], False)
                    self.assertIs(lens["is_prediction"], False)
                    self.assertTrue(lens["source"])
                forced = next(l for l in brief["symbolic_lenses"] if l["id"] == "tarot")
                self.assertEqual(forced["epistemic_status"], "symbolic_only")
                self.assertTrue(brief["muse_note"])
                self.assertIn("假设", brief["epistemic_status"] + brief["muse_note"])

    def test_inject_inspiration_never_treats_agent_track_as_personal_fact(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                brief = engine.inject_inspiration("创作方向")
                self.assertIs(brief["agent_track_read"], False)
                self.assertIs(brief["agent_track_as_personal_fact"], False)
                self.assertIs(brief["automatic_user_track_write"], False)
                self.assertIs(brief["is_evidence"], False)
                for principle in brief["principles"]:
                    self.assertTrue(principle["source"])
                    self.assertIn("confidence", principle)

    def test_philosophy_quotes_label_source_and_use_active_principles(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                quotes = engine.philosophy_quotes("失败复盘", limit=3)
                self.assertEqual(len(quotes), 3)
                for q in quotes:
                    self.assertIn("Muse 哲学", q["source"])
                    self.assertIn("假设/经验", q["source"])
                    self.assertTrue(q["text"])
                    self.assertTrue(q["title"])
                    self.assertTrue(q["practice"])
                    self.assertEqual(q["status"], "active")

    def test_evolution_snapshot_nodes_edges_timeline_structure(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store)
                exp = engine.record_creation_experience(VIDEO_META, "success", "失败回归经验")
                store.distill_principles(exp["id"])
                snapshot = engine.evolution_snapshot()
                kinds = {n["type"] for n in snapshot["nodes"]}
                self.assertTrue({"principle", "experience"}.issubset(kinds))
                self.assertTrue(all("status" in n and "confidence" in n for n in snapshot["nodes"]))
                relations = {e["relation"] for e in snapshot["edges"]}
                self.assertIn("informs", relations)
                for edge in snapshot["edges"]:
                    self.assertIn(edge["source"], {n["id"] for n in snapshot["nodes"]})
                    self.assertIn(edge["target"], {n["id"] for n in snapshot["nodes"]})
                times = [item["created_at"] for item in snapshot["timeline"]]
                self.assertEqual(times, sorted(times))

                # a pending proposal contributes a proposal node and proposed_from edge
                for _ in range(4):
                    engine.record_creation_experience(VIDEO_META, "failure", "再记一次失败")
                snapshot = engine.evolution_snapshot()
                kinds = {n["type"] for n in snapshot["nodes"]}
                self.assertIn("proposal", kinds)
                self.assertIn("proposed_from", {e["relation"] for e in snapshot["edges"]})

    def test_auto_proposal_at_threshold_is_pending_review_and_does_not_add_principles(self):
        with tempfile.TemporaryDirectory() as d:
            with PhilosophyStore(Path(d) / "p.sqlite3") as store:
                engine = ConsciousnessEngine(store, auto_propose_threshold=5)
                before = store.summary()["principles"]
                for _ in range(4):
                    result = engine.record_creation_experience(VIDEO_META, "failure", "反复出现的失败模式")
                    self.assertNotIn("auto_proposal", result)
                self.assertEqual(store.summary()["principle_candidates_pending"], 0)
                fifth = engine.record_creation_experience(VIDEO_META, "failure", "反复出现的失败模式")
                proposal = fifth["auto_proposal"]
                self.assertEqual(proposal["status"], "pending_review")
                self.assertEqual(proposal["epistemic_status"], "hypothesis")
                self.assertEqual(store.summary()["principle_candidates_pending"], 1)
                self.assertEqual(store.summary()["principles"], before)
                self.assertEqual(len(store.list_principle_candidates()), 1)
                with self.assertRaises(ValueError):
                    engine.record_creation_experience(VIDEO_META, "ghost", "x")

    def test_sqlite_readback_preserves_experiences_and_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "p.sqlite3"
            with PhilosophyStore(db) as store:
                engine = ConsciousnessEngine(store, auto_propose_threshold=1)
                engine.record_creation_experience(VIDEO_META, "success", "可读回的创作经验")
            with PhilosophyStore(db) as reopened:
                engine2 = ConsciousnessEngine(reopened)
                self.assertEqual(reopened.summary()["experiences"], 1)
                self.assertEqual(reopened.summary()["principle_candidates_pending"], 1)
                self.assertEqual(reopened.summary()["principles"], 12)
                snapshot = engine2.evolution_snapshot()
                self.assertTrue(any(n["type"] == "experience" for n in snapshot["nodes"]))
                self.assertTrue(any(n["type"] == "proposal" for n in snapshot["nodes"]))


if __name__ == "__main__":
    unittest.main()
