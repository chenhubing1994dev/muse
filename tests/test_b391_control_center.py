"""B391 P1 regression tests: control-plane API, metaphysics labelling and Status API wiring.

All tests use isolated temporary workspaces; the formal ``data/`` directory is never touched.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse.control_center_api import ControlContext, handle_control
from muse.memory import MemoryStore
from muse.metaphysics_label import (
    METAPHYSICS_DISPLAY_LABEL, MetaphysicsLabelError, label_payload, needs_label, require_label,
    symbolic_to_hypotheses,
)
from muse.research_knowledge import ResearchKnowledgeStore
from muse.task_queue import TaskQueue
from muse.xuanshu_fusion import fuse


class _Tool(SimpleNamespace):
    pass


def _inventory():
    return SimpleNamespace(tools=[_Tool(tool_id="ffmpeg", executable="ffmpeg"), _Tool(tool_id="ffprobe", executable="ffprobe")])


class ControlCenterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: no WinError 32 on teardown
        self.root = Path(self._tmp.name)
        self.ctx = ControlContext(
            workspace_root=self.root, task_path=self.root / "tasks.sqlite3",
            memory_path=self.root / "memory.sqlite3", knowledge_path=self.root / "knowledge.sqlite3",
            gpu_status_provider=lambda: {"status": "idle"},
            video_engines_provider=lambda: {"comfyui": {"status": "offline"}, "commander_planner": {"status": "unavailable"}},
            products_provider=lambda: [],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def post(self, path, body):
        return handle_control("POST", path, "", json.dumps(body, ensure_ascii=False).encode("utf-8"), self.ctx)

    def get(self, path, query=""):
        return handle_control("GET", path, query, b"", self.ctx)

    def submit(self, request_id="a" * 32, title="研究 X", goal="调查 X", kind="research"):
        return self.post("/api/v1/control/tasks", {
            "request_id": request_id, "title": title, "goal": goal, "request_kind": kind,
            "confirmation": "SUBMIT-TO-COMMANDER",
        })

    def test_submit_requires_confirmation_and_never_executes(self):
        status, body = self.post("/api/v1/control/tasks", {"request_id": "a" * 32, "title": "t", "goal": "g"})
        self.assertEqual(status, 409)
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(body["task"]["status"], "waiting_approval")
        self.assertEqual(body["execution"], "not_started")
        replay_status, replay = self.submit()
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["task"]["id"], body["task"]["id"])
        conflict_status, _ = self.submit(title="不同的请求")
        self.assertEqual(conflict_status, 409)

    def test_strict_json_rejects_duplicate_keys_and_unknown_fields(self):
        status, _ = handle_control("POST", "/api/v1/control/tasks", "", b'{"title":"a","title":"b"}', self.ctx)
        self.assertEqual(status, 400)
        status, body = self.post("/api/v1/control/tasks", {"request_id": "b" * 32, "title": "t", "goal": "g",
                                                           "confirmation": "SUBMIT-TO-COMMANDER", "kind": "echo"})
        self.assertEqual(status, 400)
        self.assertIn("unsupported", body["error"])

    def test_task_list_is_payload_free(self):
        self.submit(goal="SECRET GOAL TEXT")
        status, body = self.get("/api/v1/control/tasks", "status=waiting_approval")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertNotIn("SECRET GOAL TEXT", json.dumps(body, ensure_ascii=False))

    def test_generic_ticket_approval_records_human_decision_without_dispatch(self):
        _, created = self.submit()
        task_id = created["task"]["id"]
        status, _ = self.post(f"/api/v1/control/approvals/{task_id}/decide", {"decision": "approve", "confirmation": "nope"})
        self.assertEqual(status, 409)
        status, body = self.post(f"/api/v1/control/approvals/{task_id}/decide", {"decision": "approve", "confirmation": "APPROVE-TICKET"})
        self.assertEqual(status, 200)
        self.assertEqual(body["task"]["status"], "queued")
        self.assertEqual(body["execution"], "not_started")
        with TaskQueue(self.ctx.task_path) as queue:
            self.assertEqual(queue.get(task_id)["attempts"], 0)
            self.assertTrue(any(event["event_type"] == "human.decision" for event in queue.events(task_id, limit=50)))

    def test_dedicated_gates_cannot_be_bypassed_from_the_approval_center(self):
        with TaskQueue(self.ctx.task_path) as queue:
            video = queue.enqueue("视频审批", {"kind": "business_opportunity_video_approval",
                                            "approval_scope": "one_video_attempt_only"},
                                  require_approval=True, recovery_policy="manual")
        status, body = self.post(f"/api/v1/control/approvals/{video['id']}/decide",
                                 {"decision": "approve", "confirmation": "APPROVE-TICKET"})
        self.assertEqual(status, 409)
        self.assertIn("dedicated", body["error"])
        with TaskQueue(self.ctx.task_path) as queue:
            self.assertEqual(queue.get(video["id"])["status"], "waiting_approval")
        _, approvals = self.get("/api/v1/control/approvals")
        row = next(item for item in approvals["commander_tickets"] if item["id"] == video["id"])
        self.assertFalse(row["can_decide_here"])

    def test_reject_retry_and_cancel(self):
        _, created = self.submit(request_id="c" * 32, kind="creative")
        task_id = created["task"]["id"]
        status, body = self.post(f"/api/v1/control/approvals/{task_id}/decide",
                                 {"decision": "reject", "confirmation": "REJECT-TICKET", "reason": "不需要"})
        self.assertEqual(status, 200)
        self.assertEqual(body["task"]["status"], "cancelled")
        status, retried = self.post(f"/api/v1/control/tasks/{task_id}/retry", {"confirmation": "RETRY-TASK", "request_id": "d" * 32})
        self.assertEqual(status, 201)
        new_id = retried["task"]["id"]
        self.assertNotEqual(new_id, task_id)
        with TaskQueue(self.ctx.task_path) as queue:
            payload = queue.get(new_id)["payload"]
        self.assertFalse(any(key.startswith("_") for key in payload))
        self.assertEqual(payload["retry_of"], task_id)
        status, _ = self.post(f"/api/v1/control/tasks/{new_id}/cancel", {"confirmation": "CANCEL-TASK"})
        self.assertEqual(status, 200)
        status, _ = self.post("/api/v1/control/tasks/" + "f" * 32 + "/cancel", {"confirmation": "CANCEL-TASK"})
        self.assertEqual(status, 404)

    def test_dual_track_memory_history_and_candidate_approval(self):
        with MemoryStore(self.ctx.memory_path) as memory:
            fact = memory.add("我住在上海", source="user", track="user")
            memory.revise(fact["id"], "我住在上海浦东", source="user")
            memory.add("AMF 编码更快", source="agent-run", track="agent")
            candidate = memory.propose("我喜欢粤语旁白", source="chat", track="user")
        status, body = self.get("/api/v1/control/memory", "track=user")
        self.assertEqual(status, 200)
        self.assertEqual(body["counts"], {"user": 1, "agent": 1})
        self.assertEqual(body["pending_candidates"], 1)
        status, _ = self.get("/api/v1/control/memory", "track=everything")
        self.assertEqual(status, 400)
        _, history = self.get(f"/api/v1/control/memory/{fact['id']}/history")
        self.assertGreaterEqual(len(history["revisions"]), 2)
        status, decided = self.post(f"/api/v1/control/memory/candidates/{candidate['id']}/decide",
                                    {"decision": "approve", "confirmation": "APPROVE-MEMORY"})
        self.assertEqual(status, 200)
        self.assertEqual(decided["candidate"]["status"], "approved")
        _, obsidian = self.get("/api/v1/control/memory/obsidian")
        self.assertEqual(obsidian["status"], "not_configured")

    def test_telemetry_reports_real_host_values_and_hard_limit(self):
        status, body = self.get("/api/v1/control/telemetry")
        self.assertEqual(status, 200)
        self.assertEqual(body["concurrency"]["hard_limit"], 32)
        self.assertEqual(body["gpu"]["status"], "idle")
        self.assertIn("total_bytes", body["storage"])

    def test_creation_readiness_selects_path_from_actual_capabilities(self):
        for folder in ("data/video_sources/demo", "video-motion/node_modules/@remotion", "video-motion/src",
                       "data/models/canto-tts-nano", ".muse-canto-tts-py314", "models/whisper-small-cantonese/cts"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "data/models/canto-tts-nano/browser_poc_manifest.json").write_text("{}", encoding="utf-8")
        with patch("muse.media.discover_media_tools", _inventory):
            _, blocked = self.get("/api/v1/control/creation/readiness")
            self.assertEqual(blocked["chosen_path"], "blocked")
            self.assertIn("cantonese_asr_model_files", blocked["paths"]["local_motion"]["missing"])
            (self.root / "models/whisper-small-cantonese/cts/model.bin").write_bytes(b"x")
            _, local = self.get("/api/v1/control/creation/readiness")
            self.assertEqual(local["chosen_path"], "local_motion")
            self.assertIn("comfyui_ready", local["paths"]["ai_shot_generation"]["missing"])
            self.ctx.video_engines_provider = lambda: {"comfyui": {"status": "ready"}, "commander_planner": {"status": "ready"}}
            _, ai = self.get("/api/v1/control/creation/readiness")
            self.assertEqual(ai["chosen_path"], "ai_shot_generation")
            self.ctx.gpu_status_provider = lambda: {"status": "reserved"}
            _, reserved = self.get("/api/v1/control/creation/readiness")
            self.assertEqual(reserved["chosen_path"], "local_motion")

    def test_xuanshu_hypotheses_are_labelled_written_as_proposed_and_dispatchable(self):
        status, preview = self.post("/api/v1/control/xuanshu/hypotheses", {"question": "如何做一条 AI 自动化短片", "seed": "2026-11-27"})
        self.assertEqual(status, 200)
        self.assertEqual(preview["hypotheses"]["epistemic_display_label"], METAPHYSICS_DISPLAY_LABEL)
        self.assertIsNone(preview["written"])
        status, _ = self.post("/api/v1/control/xuanshu/hypotheses",
                              {"question": "如何做一条 AI 自动化短片", "seed": "2026-11-27", "write_to_wiki": True})
        self.assertEqual(status, 409)
        status, written = self.post("/api/v1/control/xuanshu/hypotheses", {
            "question": "如何做一条 AI 自动化短片", "seed": "2026-11-27", "write_to_wiki": True,
            "confirmation": "WRITE-HYPOTHESES",
        })
        self.assertEqual(status, 201)
        claims = written["written"]["claims"]
        self.assertEqual(len(claims), 3)
        self.assertTrue(all(claim["status"] == "proposed" for claim in claims))
        with ResearchKnowledgeStore(self.ctx.knowledge_path) as store:
            node = store.get_node(claims[0]["id"])
            edges = store.list_edges(node_id=claims[0]["id"])
            plain = store.upsert_node("claim", "普通断言", external_ref="plain:1")
        self.assertEqual(node["metadata"]["epistemic_display_label"], METAPHYSICS_DISPLAY_LABEL)
        self.assertIs(node["metadata"]["is_evidence"], False)
        self.assertTrue(any(edge["edge_type"] == "inspired_by" for edge in edges))
        status, dispatched = self.post("/api/v1/control/xuanshu/dispatch", {
            "claim_id": claims[0]["id"], "target": "research", "request_id": "9" * 32,
            "confirmation": "SUBMIT-TO-COMMANDER",
        })
        self.assertEqual(status, 201)
        self.assertEqual(dispatched["task"]["status"], "waiting_approval")
        with TaskQueue(self.ctx.task_path) as queue:
            goal = queue.get(dispatched["task"]["id"])["payload"]["goal"]
        self.assertTrue(goal.startswith("【" + METAPHYSICS_DISPLAY_LABEL + "】"))
        status, _ = self.post("/api/v1/control/xuanshu/dispatch", {
            "claim_id": plain["id"], "target": "video", "request_id": "8" * 32, "confirmation": "SUBMIT-TO-COMMANDER",
        })
        self.assertEqual(status, 409)


class MetaphysicsLabelTests(unittest.TestCase):
    def test_label_payload_forces_non_evidence_and_merges_labels(self):
        stamped = label_payload({"status": "ok", "is_evidence": True, "epistemic_labels": ["x"]})
        self.assertIs(stamped["is_evidence"], False)
        self.assertIs(stamped["is_prediction"], False)
        self.assertEqual(stamped["epistemic_display_label"], METAPHYSICS_DISPLAY_LABEL)
        self.assertEqual(stamped["epistemic_labels"], ["x", "symbolic_inspiration", "unverified_hypothesis"])
        self.assertEqual(label_payload([1, 2]), [1, 2])

    def test_paths_that_must_be_labelled(self):
        for path in ("/api/v1/metaphysics/yijing", "/api/v1/metaphysics/celestial", "/api/v1/omega/xuanshu/fuse",
                     "/api/v1/control/xuanshu/hypotheses"):
            self.assertTrue(needs_label(path), path)
        self.assertFalse(needs_label("/api/v1/control/tasks"))

    def test_epistemic_upgrades_are_rejected(self):
        with self.assertRaises(MetaphysicsLabelError):
            require_label({**label_payload({}), "status": "verified fact"})
        with self.assertRaises(MetaphysicsLabelError):
            require_label({"status": "ok"})
        with self.assertRaises(MetaphysicsLabelError):
            symbolic_to_hypotheses({"question": "x", "epistemic_labels": [], "is_evidence": False, "is_prediction": False})

    def test_hypotheses_are_deterministic_and_fully_labelled(self):
        fused = fuse("如何讲好一个故事", seed="seed-1", symbolic_fallback=True)
        first = symbolic_to_hypotheses(fused)
        second = symbolic_to_hypotheses(fused)
        self.assertEqual(first["source_fusion_sha256"], second["source_fusion_sha256"])
        require_label(first)
        for item in first["testable_hypotheses"] + first["symbolic_observations"]:
            require_label(item)
        self.assertTrue(all(item["alternative_explanation"] for item in first["testable_hypotheses"]))


class StatusApiControlWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        from muse.status_api import create_status_server
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: no WinError 32 on teardown
        root = Path(self._tmp.name)
        (root / "tasks").mkdir()
        self.server, self.token = create_status_server(
            task_db=root / "tasks" / "muse.sqlite3", progress_db=root / "progress.sqlite3",
            capability_db=root / "capabilities.sqlite3", knowledge_db=root / "knowledge.sqlite3",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self._tmp.cleanup()

    def request(self, path, body=None, *, token=True, content_type="application/json"):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = content_type
        return Request(self.base + path, data=data, headers=headers, method="POST" if body is not None else "GET")

    def test_control_routes_require_bearer_and_json(self):
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(self.request("/api/v1/control/summary", token=False), timeout=5)
        self.assertEqual(unauthorized.exception.code, 401)
        with self.assertRaises(HTTPError) as wrong_type:
            urlopen(self.request("/api/v1/control/tasks", {"x": 1}, content_type="text/plain"), timeout=5)
        self.assertEqual(wrong_type.exception.code, 415)

    def test_summary_submit_and_list_over_http(self):
        with urlopen(self.request("/api/v1/control/summary"), timeout=5) as response:
            summary = json.loads(response.read())
        self.assertEqual(len(summary["workflow_loop"]), 7)
        with urlopen(self.request("/api/v1/control/tasks", {
            "request_id": "e" * 32, "title": "HTTP 提交", "goal": "验证接线", "confirmation": "SUBMIT-TO-COMMANDER",
        }), timeout=5) as response:
            self.assertEqual(response.status, 201)
        with urlopen(self.request("/api/v1/control/tasks?status=waiting_approval"), timeout=5) as response:
            listed = json.loads(response.read())
        self.assertEqual(listed["count"], 1)

    def test_metaphysical_http_responses_carry_the_label(self):
        with urlopen(self.request("/api/v1/control/xuanshu/hypotheses", {"question": "灵感主题", "seed": "s"}), timeout=10) as response:
            body = json.loads(response.read())
        self.assertEqual(body["epistemic_display_label"], METAPHYSICS_DISPLAY_LABEL)
        self.assertIs(body["is_evidence"], False)
        with urlopen(self.request("/api/v1/omega/xuanshu/fuse", {"question": "灵感主题", "seed": "s"}), timeout=10) as response:
            fused = json.loads(response.read())
        self.assertEqual(fused["epistemic_display_label"], METAPHYSICS_DISPLAY_LABEL)
        with urlopen(self.request("/api/v1/control/summary"), timeout=5) as response:
            plain = json.loads(response.read())
        self.assertNotIn("epistemic_display_label", plain)


if __name__ == "__main__":
    unittest.main()
