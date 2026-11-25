"""B395: ConsciousnessCore ten-dimension state machine + dual-track memory lifecycle."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import gc
except ImportError:  # restricted sandboxes
    gc = None

from muse.consciousness_core import DIMENSIONS, HONESTY_LABEL, ConsciousnessCore, Telemetry, collect_telemetry
from muse.memory import MemoryStore
from muse.memory_lifecycle import FORGET_PHRASE, MemoryLifecycle


def _task_db(path: Path, rows):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE tasks(id TEXT, status TEXT, payload_json TEXT, updated_at TEXT)")
        db.executemany("INSERT INTO tasks VALUES(?,?,?,?)", rows)


class ConsciousnessCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)

    def tearDown(self):
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def test_real_sqlite_telemetry_drives_all_ten_dimensions(self):
        recent = self.now.isoformat()
        _task_db(self.root / "tasks.sqlite3", [
            ("a", "running", '{"kind":"research_topic"}', recent), ("b", "queued", '{"kind":"research"}', recent),
            ("c", "waiting_approval", "{}", recent), ("d", "failed", "{}", recent), ("e", "succeeded", "{}", recent)])
        with MemoryStore(self.root / "memory.sqlite3") as store:
            store.add("Creator prefers Cantonese-first narration", source="chat")
            store.propose("Agent learned: shorter hooks retain better", source="agent", track="agent")
        telemetry = collect_telemetry(task_db=self.root / "tasks.sqlite3", memory_db=self.root / "memory.sqlite3",
                                      resource_probe=lambda: {"cpu_percent": 40, "memory_percent": 50, "gpu_free_mib": 8000},
                                      now=self.now)
        self.assertEqual((telemetry.running, telemetry.queued, telemetry.waiting_approval), (1, 1, 1))
        self.assertEqual((telemetry.failed_recent, telemetry.memories_active, telemetry.memory_candidates_pending), (1, 1, 1))
        self.assertEqual(telemetry.research_tasks_open, 2)
        with ConsciousnessCore(self.root / "core.sqlite3", clock=lambda: 1.0) as core:
            snapshot = core.evaluate(telemetry)
            self.assertEqual(snapshot["label"], HONESTY_LABEL)
            self.assertEqual(set(snapshot["dimensions"]), set(DIMENSIONS))
            for dim in snapshot["dimensions"].values():
                self.assertTrue(0 <= dim["value"] <= 1)
                self.assertTrue(dim["drivers"])
            self.assertEqual(snapshot["phase"], "strained")   # 1 of 2 recent tasks failed → affect < 0.35
            self.assertEqual(snapshot["dimensions"]["energy_budget"]["value"], 0.5)
            self.assertEqual(len(core.timeline()), 1)
            with self.assertRaises(sqlite3.DatabaseError):
                core.db.execute("UPDATE consciousness_snapshots SET phase='x'")

    def test_missing_sources_degrade_honestly(self):
        telemetry = collect_telemetry(task_db=self.root / "none.sqlite3", memory_db=self.root / "none2.sqlite3")
        self.assertEqual(telemetry.sources, {"tasks": "missing", "memory": "missing"})
        snapshot = ConsciousnessCore().evaluate(telemetry, persist=False)
        self.assertEqual(snapshot["phase"], "dormant")
        self.assertEqual(snapshot["dimensions"]["self_model"]["value"], 0.0)

    def test_state_machine_halt_requires_explicit_resume_and_hysteresis(self):
        core = ConsciousnessCore()
        halted = core.evaluate(Telemetry(running=2, safety_mode="killed"), persist=False)
        self.assertEqual(halted["phase"], "halted")
        self.assertEqual(halted["dimensions"]["agency"]["value"], 0.0)
        resumed = core.evaluate(Telemetry(running=2, safety_mode="running"), previous_phase="halted", persist=False)
        self.assertEqual(resumed["phase"], "dormant")        # never jumps halted → focused
        nxt = core.evaluate(Telemetry(running=2), previous_phase="dormant", persist=False)
        self.assertEqual(nxt["phase"], "focused")
        jitter = core.evaluate(Telemetry(running=2, queued=1), previous_phase="focused",
                               previous_dimensions=nxt["dimensions"], persist=False)
        self.assertEqual(jitter["dimensions"]["attention"]["value"], nxt["dimensions"]["attention"]["value"])
        strained = core.evaluate(Telemetry(failed_recent=9, succeeded_recent=1), previous_phase="focused", persist=False)
        self.assertEqual(strained["phase"], "strained")


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.store = MemoryStore(self.root / "memory.sqlite3")
        self.now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        self.life = MemoryLifecycle(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def test_expiry_reminder_then_archive_not_delete(self):
        memory = self.store.add("GPU credit offer ends soon", source="radar")
        self.life.set_expiry(memory["id"], expires_at=self.now + timedelta(hours=12), remind_before=timedelta(days=1))
        due = self.life.due_reminders()
        self.assertEqual((len(due), due[0]["expired"]), (1, False))
        self.assertEqual(self.life.due_reminders(), [])             # reminded once
        self.now += timedelta(hours=13)
        self.assertEqual(self.life.sweep_expired(), [memory["id"]])
        self.assertIsNotNone(self.store.get(memory["id"]))         # archived, still auditable
        self.assertEqual(self.life.ranked(), [])

    def test_demotion_ranking_and_revision_diff(self):
        a = self.store.add("Hook A works", source="analytics", confidence=0.9)
        b = self.store.add("Hook B works", source="analytics", confidence=0.8)
        self.life.demote(a["id"], factor=0.5)
        self.assertEqual([m["id"] for m in self.life.ranked()], [b["id"], a["id"]])
        self.store.revise(b["id"], "Hook B works for Cantonese audience", confidence=0.95)
        diff = self.life.diff(b["id"], 1, 2)
        self.assertIn("+Hook B works for Cantonese audience", diff["diff"])
        self.assertEqual(diff["metadata_changes"]["confidence"], (0.8, 0.95))

    def test_auditable_forget_purges_content_everywhere(self):
        memory = self.store.add("private phone 555-0100", source="chat")
        self.store.revise(memory["id"], "private phone 555-0199")
        self.life.add_anchor(memory["id"], modality="image", uri="file:///x.png", sha256="a" * 64)
        with self.assertRaises(PermissionError):
            self.life.forget(memory["id"], actor="creator", reason="privacy", confirmation="yes")
        result = self.life.forget(memory["id"], actor="creator", reason="privacy", confirmation=FORGET_PHRASE)
        self.assertEqual(result["content_sha256"], hashlib.sha256(b"private phone 555-0199").hexdigest())
        dump = "\n".join(self.store._connection.iterdump())
        self.assertNotIn("555-01", dump)
        self.assertIsNone(self.store.get(memory["id"]))
        self.assertEqual(self.life.find_by_anchor(sha256="a" * 64), [])
        audit = self.life.forget_audit()
        self.assertEqual((audit[0]["actor"], audit[0]["revisions_purged"]), ("creator", 1))
        with self.assertRaises(sqlite3.DatabaseError):
            self.store._connection.execute("DELETE FROM memory_forget_audit")

    def test_cross_project_causal_timeline_refuses_cycles(self):
        a = self.store.add("Ran 3-second hook test", source="lab", project="video")
        b = self.store.add("Retention rose 12%", source="analytics", project="growth")
        c = self.store.add("Adopted hook matrix as default", source="decision", project="video")
        self.life.link(a["id"], b["id"], relation="caused", created_by="creator")
        self.life.link(b["id"], c["id"], relation="enabled", created_by="creator")
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.life.link(c["id"], a["id"], relation="caused", created_by="creator")
        timeline = self.life.causal_timeline(b["id"])
        self.assertEqual([e["id"] for e in timeline["events"]], [a["id"], b["id"], c["id"]])
        self.assertEqual(timeline["projects"], ["growth", "video"])
        self.assertEqual(len(timeline["edges"]), 2)

    def test_multimodal_anchor_index(self):
        memory = self.store.add("Storyboard frame 12 lighting note", source="studio")
        digest = hashlib.sha256(b"frame").hexdigest()
        self.life.add_anchor(memory["id"], modality="video", uri="file:///shot.mp4", sha256=digest, span="00:03-00:05")
        self.assertEqual(self.life.find_by_anchor(sha256=digest)[0]["span"], "00:03-00:05")
        with self.assertRaises(ValueError):
            self.life.add_anchor(memory["id"], modality="hologram", uri="x")

    def test_obsidian_one_way_mirror_detects_conflicts(self):
        vault = self.root / "vault"
        vault.mkdir()
        memory = self.store.add("Muse mission: help the creator ship", source="charter")
        first = self.life.mirror_to_obsidian(vault)
        self.assertEqual(first["written"], [memory["id"]])
        note = next((vault / "Muse Memory").glob("*.md"))
        self.assertIn("muse_mirror: one_way_read_only", note.read_text(encoding="utf-8"))
        self.assertEqual(self.life.mirror_to_obsidian(vault)["unchanged"], [memory["id"]])
        note.write_text("creator edited this in Obsidian", encoding="utf-8")
        self.store.revise(memory["id"], "Muse mission v2")
        result = self.life.mirror_to_obsidian(vault)
        self.assertEqual(result["conflicts"][0]["reason"], "edited_in_obsidian")
        self.assertEqual(note.read_text(encoding="utf-8"), "creator edited this in Obsidian")  # never overwritten
        with self.assertRaises(ValueError):
            self.life.mirror_to_obsidian(vault, folder="../escape")

    def test_agent_track_receipt_chain(self):
        c1 = self.store.propose("Agent: batch renders at night", source="muse-agent", track="agent")
        c2 = self.store.propose("Agent: cache LUFS analysis", source="muse-agent", track="agent")
        with self.assertRaises(PermissionError):
            self.life.approve_agent_track(c1["id"], approved_by="muse-agent")
        r1 = self.life.approve_agent_track(c1["id"], approved_by="creator")
        r2 = self.life.approve_agent_track(c2["id"], approved_by="creator")
        self.assertEqual(r2["prev_hash"], r1["receipt_hash"])
        self.assertEqual(self.life.verify_receipt_chain(), {"ok": True, "receipts": 2, "head": r2["receipt_hash"]})
        user = self.store.propose("User note", source="chat")
        with self.assertRaises(ValueError):
            self.life.approve_agent_track(user["id"], approved_by="creator")


if __name__ == "__main__":
    unittest.main()
