from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse.agent_registry import AgentRegistry
from muse.capabilities import CapabilityRegistry
from muse.memory import MemoryStore
from muse.orchestration import AgentRoleRegistry, OrchestrationStore
from muse.philosophy import PhilosophyStore
from muse.progress import ProgressEventStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.status_api import create_status_server
from muse.task_queue import TaskQueue


class CyberCosmosApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        data = self.root / "data"
        self.task_db = data / "tasks" / "muse.sqlite3"
        self.progress_db = self.task_db
        self.capability_db = data / "capabilities" / "muse.sqlite3"
        self.agent_db = data / "agents" / "muse.sqlite3"
        self.improvement_db = data / "improvements" / "muse.sqlite3"
        self.knowledge_db = data / "research" / "knowledge.sqlite3"
        self.memory_db = data / "memory" / "muse.sqlite3"
        self.philosophy_db = data / "philosophy" / "muse.sqlite3"
        self.reports_dir = data / "research" / "reports"
        self.products_dir = data / "products"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.products_dir.mkdir(parents=True, exist_ok=True)
        (self.reports_dir / "sample-report.md").write_text("# Sample Report\n", encoding="utf-8")

        with TaskQueue(self.task_db) as queue:
            queue.enqueue("Queued item", {"secret": "private-task-payload"})
        with ProgressEventStore(self.progress_db) as progress:
            progress.record("task", "task-1", "started", summary="Started")
        with AgentRoleRegistry(self.task_db) as roles:
            roles.register(
                "research",
                allowed_capabilities=("web.search", "read.doc"),
                trust_tier="standard",
                description="Research specialist",
            )
        with OrchestrationStore(self.task_db) as orch:
            orch.connection.execute(
                "INSERT INTO orchestration_runs(run_id,prompt,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("run-001", "SECRET_PROMPT_DO_NOT_LEAK", "succeeded", "2026-11-18T00:00:00Z", "2026-11-18T00:01:00Z"),
            )
            orch.connection.execute(
                "INSERT INTO agent_runs(run_id,agent_id,role,status,output,error,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("run-001", "researcher", "research", "succeeded", "SECRET_OUTPUT_DO_NOT_LEAK", "", "2026-11-18T00:01:00Z"),
            )
            orch.connection.commit()

        with CapabilityRegistry(self.capability_db) as caps:
            caps.register(
                capability_id="tool.local",
                domain="research",
                name="Local Tool",
                description="Local research tool",
                executable="C:/secret/tool.exe",
                capabilities=("web.search",),
                status="ready",
            )
        with AgentRegistry(self.agent_db):
            pass

        with MemoryStore(self.memory_db) as mem:
            mem.add("SECRET_USER_PRIVATE_MEMORY_TEXT", project="muse", source="manual", track="user", kind="preference")
            mem.add("Agent operational lesson", project="muse", source="runbook", track="agent", kind="skill")

        with PhilosophyStore(self.philosophy_db) as philo:
            exp = philo.capture_experience("inspiration", "记录一次经验复利反馈")
            philo.distill_principles(exp["id"])

        with ResearchKnowledgeStore(self.knowledge_db) as wiki:
            n_src = wiki.upsert_node("source", "Primary Paper", summary="Source summary", external_ref="https://example.org/paper")
            n_claim = wiki.upsert_node(
                "claim",
                "Local Knowledge Graph Hypothesis",
                summary="Claim summary",
                external_ref="creator-inspiration:sha256:" + "a" * 64,
                metadata={"origin": "creator_inspiration_inbox", "review_state": "not_researched"},
                status="proposed",
            )
            wiki.add_edge(n_claim["id"], n_src["id"], "inspired_by")
            self.claim_id = n_claim["id"]
            self.source_id = n_src["id"]

        self.server, self.token = create_status_server(
            task_db=self.task_db,
            progress_db=self.progress_db,
            capability_db=self.capability_db,
            agent_db=self.agent_db,
            improvement_db=self.improvement_db,
            knowledge_db=self.knowledge_db,
            report_dir=self.reports_dir,
            products_dir=self.products_dir,
            port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def _get_json(self, path: str, headers: dict[str, str] | None = None) -> dict:
        req = Request(self.base + path, headers=headers if headers is not None else self.headers)
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        req = Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers if headers is not None else self.headers)},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_auth_and_origin_guards_on_new_endpoints(self) -> None:
        for path in ("/api/v1/prometheus/core", "/api/v1/knowledge/graph", "/api/v1/galaxy", "/api/v1/metaphysics/celestial"):
            with self.assertRaises(HTTPError) as ctx:
                self._get_json(path, headers={})
            self.assertEqual(ctx.exception.code, 401)
            with self.assertRaises(HTTPError) as origin_ctx:
                self._get_json(path, headers={**self.headers, "Origin": "https://evil.example"})
            self.assertEqual(origin_ctx.exception.code, 403)

    def test_prometheus_core_exposes_counts_without_leaking_user_memory_or_guessing_vram(self) -> None:
        data = self._get_json("/api/v1/prometheus/core")
        self.assertEqual(data["api_version"], 1)
        stages = data["stages"]
        self.assertEqual(stages["ignition"]["inspirations_count"], 1)
        self.assertEqual(stages["discovery"]["knowledge_nodes_total"], 2)
        self.assertEqual(stages["synthesis"]["knowledge_edges_total"], 1)
        self.assertEqual(stages["synthesis"]["philosophy_principles_count"], 12)
        self.assertEqual(stages["creation"]["artifacts_count"], 1)
        self.assertEqual(stages["creation"]["orchestration_runs_count"], 1)
        self.assertEqual(stages["transmission"]["user_track_memories_count"], 1)
        self.assertEqual(stages["transmission"]["agent_track_memories_count"], 1)
        self.assertIsNone(data["resource_governor"]["gpu_available_memory_bytes"])
        self.assertEqual(data["resource_governor"]["maximum_workers"], 32)
        self.assertFalse(data["memory_privacy_guard"]["user_track_text_exposed"])
        self.assertNotIn("SECRET_USER_PRIVATE_MEMORY_TEXT", json.dumps(data, ensure_ascii=False))

    def test_knowledge_graph_and_enriched_node_lookup(self) -> None:
        graph = self._get_json("/api/v1/knowledge/graph?limit=50&node_type=claim")
        self.assertTrue(graph["configured"])
        self.assertEqual(graph["totals"]["active_nodes"], 2)
        self.assertEqual(graph["totals"]["active_edges"], 1)
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["nodes"][0]["id"], self.claim_id)
        self.assertIsNotNone(graph["governance"])

        full_graph = self._get_json("/api/v1/knowledge/graph?limit=50")
        self.assertEqual(len(full_graph["nodes"]), 2)
        self.assertEqual(len(full_graph["edges"]), 1)

        node_detail = self._get_json(f"/api/v1/knowledge/nodes/{self.claim_id}")
        self.assertEqual(node_detail["node"]["id"], self.claim_id)
        self.assertEqual(len(node_detail["neighbors"]), 1)
        self.assertEqual(node_detail["neighbors"][0]["id"], self.source_id)
        self.assertEqual(node_detail["evidence_summary"]["total"], 0)

        with self.assertRaises(HTTPError) as bad_req:
            self._get_json("/api/v1/knowledge/graph?limit=999")
        self.assertEqual(bad_req.exception.code, 400)

    def test_agent_galaxy_and_route_preview_stay_redacted_and_honest(self) -> None:
        galaxy = self._get_json("/api/v1/galaxy")
        self.assertTrue(galaxy["commander"]["sole_authority"])
        self.assertEqual(galaxy["commander"]["competing_schedulers"], 0)
        self.assertEqual(galaxy["live_agents"], [])
        self.assertEqual(len(galaxy["specialist_profiles"]), 8)
        self.assertTrue(all(item["live_adapter_connected"] is False for item in galaxy["specialist_profiles"]))
        self.assertTrue(all(item["layer"] == "specialist_template_metadata" for item in galaxy["specialist_profiles"]))
        self.assertEqual(galaxy["orchestration_summary"]["total_runs"], 1)
        serialized = json.dumps(galaxy, ensure_ascii=False)
        self.assertNotIn("SECRET_PROMPT_DO_NOT_LEAK", serialized)
        self.assertNotIn("SECRET_OUTPUT_DO_NOT_LEAK", serialized)
        self.assertNotIn("C:/secret/tool.exe", serialized)

        preview = self._post_json("/api/v1/galaxy/route-preview", {"goal": "研究并开发系统架构与安全审查测试"})
        self.assertTrue(preview["readonly_preview"])
        self.assertFalse(preview["dispatched"])
        self.assertEqual(preview["live_ready_count"], 0)
        self.assertGreaterEqual(len(preview["selected"]), 2)
        # Ensure preview did not enqueue any new task.
        with TaskQueue(self.task_db) as queue:
            self.assertEqual(queue.snapshot(limit=20)["counts"]["queued"], 1)

    def test_metaphysics_celestial_and_yijing_enforce_epistemic_labels(self) -> None:
        celestial = self._get_json("/api/v1/metaphysics/celestial")
        self.assertEqual(celestial["epistemic_status"], "symbolic_inspiration_only")
        self.assertFalse(celestial["is_evidence"])
        self.assertFalse(celestial["is_prediction"])
        self.assertEqual(len(celestial["bagua_trigrams"]), 8)
        self.assertEqual(len(celestial["star_mansions"]), 28)

        cast_res = self._post_json(
            "/api/v1/metaphysics/yijing",
            {"mode": "seeded_cast", "seed": "muse-cyber-test", "goal": "构建赛博知识星图"},
        )
        self.assertEqual(cast_res["epistemic_status"], "symbolic_hypothesis_only")
        self.assertFalse(cast_res["is_evidence"])
        self.assertFalse(cast_res["is_prediction"])
        self.assertFalse(cast_res["automatic_memory_write"])
        self.assertFalse(cast_res["automatic_task_approval"])
        self.assertEqual(cast_res["reading"]["line_order"], "bottom_to_top")
        self.assertIsNotNone(cast_res["cross_system"])
        self.assertEqual(len(cast_res["cross_system"]["framework_ids"]), 4)

        pair_res = self._post_json(
            "/api/v1/metaphysics/yijing",
            {"mode": "trigram_pair", "lower_bits": "111", "upper_bits": "000"},
        )
        self.assertEqual(pair_res["reading"]["king_wen_name"], "泰")
        self.assertFalse(pair_res["is_evidence"])
        self.assertFalse(pair_res["is_prediction"])

        manual_res = self._post_json(
            "/api/v1/metaphysics/yijing",
            {"mode": "manual_lines", "line_values": [9, 7, 8, 6, 7, 8]},
        )
        self.assertEqual(manual_res["reading"]["moving_lines"], [1, 4])
        self.assertFalse(manual_res["is_prediction"])


if __name__ == "__main__":
    unittest.main()
