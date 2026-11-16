from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from muse.philosophy import PhilosophyStore


class PhilosophyStoreTests(unittest.TestCase):
 def test_seed_experience_distillation_and_persistence(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/"philosophy.sqlite3"
   with PhilosophyStore(db) as store:
    self.assertEqual(store.summary()["principles"],12)
    item=store.capture_experience("inspiration","研究灵感提示主动寻找反证",source_ref="report:1",epistemic_status="symbolic_only")
    links=store.distill_principles(item["id"])
    self.assertTrue(any(p["principle_key"]=="plurality_beats_confirmation" for p in links))
   with PhilosophyStore(db) as store:
    self.assertEqual(store.summary()["experiences"],1)
    self.assertEqual(store.summary()["principles"],12)

 def test_experiment_updates_confidence_only_after_outcome(self):
  with tempfile.TemporaryDirectory() as d:
   with PhilosophyStore(Path(d)/"p.sqlite3") as store:
    before=next(p for p in store.list_principles() if p["principle_key"]=="reversible_progress")
    exp=store.propose_experiment("reversible_progress","小步变更能否降低回归风险",metric="regression count",baseline="current test suite",target="no increase",stop_condition="any data loss")
    self.assertEqual(store.get_experiment(exp["id"])["outcome"],"planned")
    store.complete_experiment(exp["id"],outcome="supported",result="fixture test passed")
    after=next(p for p in store.list_principles() if p["principle_key"]=="reversible_progress")
    self.assertGreater(after["confidence"],before["confidence"])
    with self.assertRaises(ValueError): store.complete_experiment(exp["id"],outcome="supported",result="duplicate")

 def test_symbolic_inspiration_keeps_symbolic_status(self):
  with tempfile.TemporaryDirectory() as d:
   with PhilosophyStore(Path(d)/"p.sqlite3") as store:
    item=store.capture_experience("inspiration","易经变化视角提示探索恢复机制",epistemic_status="symbolic_only")
    self.assertEqual(item["epistemic_status"],"symbolic_only")
    self.assertTrue(store.distill_principles(item["id"]))

 def test_research_insight_creates_pending_principle_candidate_not_auto_adoption(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/"p.sqlite3"
   with PhilosophyStore(db) as store:
    experience=store.capture_experience("reflection","奇门九宫结构启发多层状态建模",source_ref="report:metaphysics#reflection")
    candidate=store.propose_principle_candidate(experience["id"],"多层状态显式建模","时间、空间与角色状态可分层表示，但仍需实验验证",suggested_practice="建立可重放状态快照并与基线比较",source_refs=["https://example.org/source"])
    self.assertEqual(candidate["status"],"pending_review")
    self.assertEqual(candidate["epistemic_status"],"hypothesis")
    self.assertEqual(store.summary()["principle_candidates_pending"],1)
    self.assertEqual(len(store.list_principle_candidates()),1)
    self.assertEqual(store.summary()["principles"],12)
   with PhilosophyStore(db) as reopened:
    self.assertEqual(reopened.summary()["principle_candidates_pending"],1)
    self.assertEqual(len(reopened.list_principle_candidates()),1)

 def test_creator_value_and_self_directed_evolution_are_core_principles(self):
  with tempfile.TemporaryDirectory() as d:
   with PhilosophyStore(Path(d)/"p.sqlite3") as store:
    keys={item["principle_key"] for item in store.list_principles()}
    self.assertIn("creator_value_creation",keys)
    self.assertIn("self_directed_evolution",keys)
    experience=store.capture_experience("reflection","从创造者兴趣主动提出产品与原型，持续自我改进")
    linked=store.distill_principles(experience["id"])
    self.assertTrue(any(x["principle_key"]=="creator_value_creation" for x in linked))
    self.assertTrue(any(x["principle_key"]=="self_directed_evolution" for x in linked))

 def test_creator_interest_and_creation_principles_are_seeded(self):
  with tempfile.TemporaryDirectory() as d:
   with PhilosophyStore(Path(d)/"p.sqlite3") as store:
    keys={item["principle_key"] for item in store.list_principles(limit=50)}
    self.assertTrue({"creator_interest_is_a_compass","curiosity_precedes_certainty","ideas_must_become_artifacts","experience_compounds","creator_value_creation","self_directed_evolution"}.issubset(keys))
    item=store.capture_experience("inspiration","从创造者书签主动发现产品原型并开始制作",source_ref="creator:test")
    linked={entry["principle_key"] for entry in store.distill_principles(item["id"])}
    self.assertIn("ideas_must_become_artifacts",linked)


if __name__=="__main__":
 unittest.main()
