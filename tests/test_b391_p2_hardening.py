"""B391-P2 adversarial regression tests (written as failing reproductions first, then fixed).

Each test names the defect it pins (B391-P2-xx in CHANGELOG.md). Everything runs on temporary
SQLite files that are explicitly closed before the temporary directory is removed (Windows-safe).
"""
from __future__ import annotations

import hashlib
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
from muse.status_api import create_status_server, parse_byte_range
from muse.memory import MemoryStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.task_queue import TaskQueue


def _hex(ch: str) -> str:
    return ch * 32


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        (self.root / "data" / "tasks").mkdir(parents=True)
        self.ledger: list[dict] = []
        self.video_bytes = b"\x00\x00\x00\x18ftypmp42-fake-but-hashed"
        self.ctx = ControlContext(
            workspace_root=self.root,
            task_path=self.root / "data" / "tasks" / "muse.sqlite3",
            memory_path=self.root / "data" / "memory" / "muse.sqlite3",
            knowledge_path=self.root / "data" / "research" / "knowledge.sqlite3",
            products_provider=self._products,
            video_product_resolver=self._resolve_video,
            feedback_ledger_writer=self._write_ledger,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    # fakes for the Product Vault + creator ledger providers wired by status_api
    def _products(self):
        return [{"kind": "video_creator", "initiative_id": _hex("1"), "version_id": "2" * 20,
                 "creator_initiative_id": _hex("3"), "creator_feedback_enabled": True,
                 "video_path": "/api/v1/products/x/y/video", "name": "样片"}]

    def _resolve_video(self, initiative_id: str, version: str):
        if initiative_id != _hex("1") or version != "2" * 20:
            raise FileNotFoundError("video product not found")
        return {"initiative_id": initiative_id, "version_id": version,
                "video_sha256": hashlib.sha256(self.video_bytes).hexdigest(), "title": "样片"}

    def _write_ledger(self, creator_initiative_id: str, *, feedback: str, decision: str, idempotency_key: str):
        for row in self.ledger:
            if row["idempotency_key"] == idempotency_key:
                if row["feedback"] != feedback or row["decision"] != decision:
                    raise PermissionError("idempotency key reused for different feedback")
                return {**row, "deduplicated": True}
        row = {"id": hashlib.sha256(idempotency_key.encode()).hexdigest()[:32], "initiative_id": creator_initiative_id,
               "feedback": feedback, "decision": decision, "idempotency_key": idempotency_key,
               "created_at": "2026-11-27T04:00:00+00:00"}
        self.ledger.append(row)
        return {**row, "deduplicated": False}

    def post(self, path: str, payload) -> tuple[int, dict]:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return handle_control("POST", path, "", raw, self.ctx)

    def get(self, path: str, query: str = "") -> tuple[int, dict]:
        return handle_control("GET", path, query, b"", self.ctx)

    def submit(self, request_id: str, title: str = "研究任务", goal: str = "目标") -> tuple[int, dict]:
        return self.post("/api/v1/control/tasks", {"request_id": request_id, "title": title, "goal": goal,
                                                    "request_kind": "research", "confirmation": "SUBMIT-TO-COMMANDER"})


class JsonBoundaryTests(_Base):
    def test_p2_01_non_finite_numbers_are_rejected(self):
        for literal in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999"):
            status, body = self.post("/api/v1/control/tasks", b'{"request_id": "' + _hex("a").encode() + b'", "x": ' + literal + b"}")
            self.assertEqual(status, 400, literal)
            self.assertIn("non-finite", body["error"])

    def test_p2_02_top_level_non_objects_are_400_not_500(self):
        for raw in (b"[]", b'"text"', b"null", b"1", b"true"):
            status, body = self.post("/api/v1/control/tasks", raw)
            self.assertEqual(status, 400, raw)
            self.assertIn("JSON object", body["error"])

    def test_p2_03_deep_nesting_is_400_not_503(self):
        raw = b'{"title": ' + b"[" * 2000 + b"]" * 2000 + b"}"
        status, body = self.post("/api/v1/control/tasks", raw)
        self.assertEqual(status, 400)
        self.assertIn("nesting", body["error"])

    def test_p2_04_boolean_is_not_accepted_where_text_or_flags_expected(self):
        status, _ = self.post("/api/v1/control/xuanshu/hypotheses", {"question": "测试", "seed": "s", "write_to_wiki": 1})
        self.assertEqual(status, 400)
        status, _ = self.post("/api/v1/control/tasks", {"request_id": True, "title": "t", "goal": "g",
                                                         "confirmation": "SUBMIT-TO-COMMANDER"})
        self.assertEqual(status, 400)

    def test_duplicate_keys_still_rejected(self):
        status, _ = self.post("/api/v1/control/tasks", b'{"title": "a", "title": "b"}')
        self.assertEqual(status, 400)


class IdempotencyTests(_Base):
    def test_p2_05_concurrent_same_request_id_different_payload_conflicts(self):
        rid = _hex("b")
        results: list[tuple[int, dict]] = []
        barrier = threading.Barrier(6)

        def worker(index: int) -> None:
            barrier.wait()
            results.append(self.submit(rid, title=f"并发 {index % 2}", goal=f"目标 {index % 2}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        statuses = sorted(status for status, _ in results)
        self.assertEqual(len(results), 6)
        self.assertNotIn(503, statuses, results)
        self.assertNotIn(500, statuses, results)
        created = [body for status, body in results if status == 201]
        self.assertEqual(len(created), 1, results)
        winner_title = created[0]["task"]["title"]
        for status, body in results:
            if status == 200:
                self.assertTrue(body["replayed"])
                self.assertEqual(body["task"]["title"], winner_title)
            elif status == 409:
                self.assertIn("different request", body["error"])
        with TaskQueue(self.ctx.task_path) as queue:
            self.assertEqual(len(queue.list_tasks(limit=50)), 1)

    def test_p2_06_retry_request_id_reused_for_another_ticket_conflicts(self):
        _, first = self.submit(_hex("c"), title="第一个")
        _, second = self.submit(_hex("d"), title="第二个")
        for created in (first, second):
            self.post(f"/api/v1/control/tasks/{created['task']['id']}/cancel", {"confirmation": "CANCEL-TASK"})
        status, retried = self.post(f"/api/v1/control/tasks/{first['task']['id']}/retry",
                                    {"confirmation": "RETRY-TASK", "request_id": _hex("e")})
        self.assertEqual(status, 201)
        status, replay = self.post(f"/api/v1/control/tasks/{first['task']['id']}/retry",
                                   {"confirmation": "RETRY-TASK", "request_id": _hex("e")})
        self.assertEqual(status, 200)
        self.assertEqual(replay["task"]["id"], retried["task"]["id"])
        status, body = self.post(f"/api/v1/control/tasks/{second['task']['id']}/retry",
                                 {"confirmation": "RETRY-TASK", "request_id": _hex("e")})
        self.assertEqual(status, 409)
        self.assertIn("different Ticket", body["error"])

    def test_p2_07_retry_keeps_business_fields_and_workload_but_drops_internal_state(self):
        with TaskQueue(self.ctx.task_path) as queue:
            task = queue.enqueue("媒体任务", {"goal": "剪辑", "request_kind": "creative", "_approval": {"approved_by": "x"},
                                              "_lease_hint": 1, "source_ref": "research-wiki:abc"},
                                 recovery_policy="manual", require_approval=True, workload_class="media")
            queue.cancel(task["id"], cancelled_by="test", reason="t")
        status, body = self.post(f"/api/v1/control/tasks/{task['id']}/retry", {"confirmation": "RETRY-TASK", "request_id": _hex("f")})
        self.assertEqual(status, 201)
        with TaskQueue(self.ctx.task_path) as queue:
            new = queue.get(body["task"]["id"])
        self.assertEqual(new["payload"]["goal"], "剪辑")
        self.assertEqual(new["payload"]["source_ref"], "research-wiki:abc")
        self.assertFalse(any(k.startswith("_") for k in new["payload"]))
        self.assertEqual(new["status"], "waiting_approval")
        self.assertEqual(new.get("workload_class"), "media")

    def test_p2_08_retry_of_signed_review_ticket_is_refused(self):
        with TaskQueue(self.ctx.task_path) as queue:
            task = queue.enqueue("签名构建", {"goal": "build", "_signed_review": {"status": "rejected"}},
                                 recovery_policy="manual", require_approval=True)
            queue.cancel(task["id"], cancelled_by="test", reason="t")
        status, body = self.post(f"/api/v1/control/tasks/{task['id']}/retry", {"confirmation": "RETRY-TASK", "request_id": _hex("9")})
        self.assertEqual(status, 409)
        self.assertIn("dedicated", body["error"])


class DedicatedGateTests(_Base):
    def test_every_dedicated_gate_kind_is_refused_by_generic_decide(self):
        kinds = ["business_opportunity_video_approval", "video_pipeline", "contextual_inquiry_review"]
        with TaskQueue(self.ctx.task_path) as queue:
            ids = [queue.enqueue(k, {"kind": k}, recovery_policy="manual", require_approval=True)["id"] for k in kinds]
            ids.append(queue.enqueue("signed", {"goal": "x", "_signed_review": {"status": "pending"}},
                                     recovery_policy="manual", require_approval=True)["id"])
        for task_id in ids:
            status, body = self.post(f"/api/v1/control/approvals/{task_id}/decide",
                                     {"decision": "approve", "confirmation": "APPROVE-TICKET"})
            self.assertEqual(status, 409, body)
            self.assertIn("dedicated gate", body["error"])
            with TaskQueue(self.ctx.task_path) as queue:
                self.assertEqual(queue.get(task_id)["status"], "waiting_approval")


class TestedByLoopTests(_Base):
    """B391-P2-10: 玄学观察 → 可检验假设写入 Wiki → 派发票据 → 成片反馈回写 tested_by → 图谱可查。"""

    def _write_hypotheses(self) -> dict:
        status, body = self.post("/api/v1/control/xuanshu/hypotheses", {
            "question": "粤语旁白节奏是否影响完播", "seed": "2026-11-27", "write_to_wiki": True,
            "confirmation": "WRITE-HYPOTHESES"})
        self.assertEqual(status, 201, body)
        return body["written"]

    def feedback(self, claim_id: str, request_id: str, **extra) -> tuple[int, dict]:
        payload = {"initiative_id": _hex("1"), "version": "2" * 20, "claim_id": claim_id,
                   "verdict": "supports", "decision": "accept", "feedback": "完播率上升，但样本只有一条。",
                   "request_id": request_id, "confirmation": "LINK-FEEDBACK-TO-CLAIM"}
        payload.update(extra)
        return self.post("/api/v1/control/creation/feedback", payload)

    def test_full_loop_from_symbolic_observation_to_tested_by(self):
        written = self._write_hypotheses()
        claim_id = written["claims"][0]["id"]
        status, dispatched = self.post("/api/v1/control/xuanshu/dispatch", {
            "claim_id": claim_id, "target": "video", "request_id": _hex("7"), "confirmation": "SUBMIT-TO-COMMANDER"})
        self.assertEqual(status, 201)
        self.assertEqual(dispatched["task"]["status"], "waiting_approval")

        status, linked = self.feedback(claim_id, _hex("8"))
        self.assertEqual(status, 201, linked)
        self.assertEqual(linked["ledger"]["deduplicated"], False)
        record = linked["feedback_record"]
        self.assertEqual(record["metadata"]["video_sha256"], hashlib.sha256(self.video_bytes).hexdigest())
        self.assertEqual(record["metadata"]["creator_verdict"], "supports")
        self.assertTrue(record["metadata"]["empirical_observation"])
        self.assertFalse(record["metadata"]["independent_evidence"])
        self.assertNotIn("epistemic_display_label", record["metadata"])
        self.assertEqual(linked["edge"]["edge_type"], "tested_by")
        self.assertEqual(linked["edge"]["source_id"], claim_id)

        # The claim keeps its symbolic label and its proposed status (no automatic verdict).
        with ResearchKnowledgeStore(self.ctx.knowledge_path) as store:
            claim = store.get_node(claim_id)
            edges = store.list_edges(node_id=claim_id, edge_type="tested_by")
        self.assertEqual(claim["status"], "proposed")
        self.assertEqual(claim["metadata"]["epistemic_display_label"], "玄学推演／待验证假设")
        self.assertEqual(len(edges), 1)

        # Graph drawer endpoint separates origin (symbolic) and empirical feedback records.
        status, chain = self.get(f"/api/v1/control/knowledge/claims/{claim_id}/tested-by")
        self.assertEqual(status, 200)
        self.assertEqual(chain["origin"]["epistemic_display_label"], "玄学推演／待验证假设")
        self.assertFalse(chain["origin"]["is_evidence"])
        self.assertEqual(len(chain["feedback_records"]), 1)
        self.assertEqual(chain["feedback_records"][0]["video_sha256"], hashlib.sha256(self.video_bytes).hexdigest())
        self.assertEqual(chain["claim_status"], "proposed")
        self.assertIn("不会自动改写", chain["policy"])

        # Hypothesis list for the toolbox form.
        status, listing = self.get("/api/v1/control/knowledge/claims")
        self.assertEqual(status, 200)
        self.assertTrue(any(c["id"] == claim_id and c["tested_by_count"] == 1 for c in listing["claims"]))

    def test_feedback_is_idempotent_and_conflicts_on_changed_payload(self):
        claim_id = self._write_hypotheses()["claims"][0]["id"]
        first = self.feedback(claim_id, _hex("a"))
        again = self.feedback(claim_id, _hex("a"))
        self.assertEqual(first[0], 201)
        self.assertEqual(again[0], 200)
        self.assertTrue(again[1]["replayed"])
        self.assertEqual(again[1]["feedback_record"]["id"], first[1]["feedback_record"]["id"])
        status, body = self.feedback(claim_id, _hex("a"), feedback="完全不同的结论")
        self.assertEqual(status, 409)
        with ResearchKnowledgeStore(self.ctx.knowledge_path) as store:
            self.assertEqual(len(store.list_edges(node_id=claim_id, edge_type="tested_by")), 1)
        self.assertEqual(len(self.ledger), 1)

    def test_feedback_requires_phrase_known_claim_and_real_product(self):
        claim_id = self._write_hypotheses()["claims"][0]["id"]
        status, _ = self.feedback(claim_id, _hex("b"), confirmation="link")
        self.assertEqual(status, 409)
        status, _ = self.feedback("missing-claim", _hex("b"))
        self.assertEqual(status, 404)
        status, _ = self.feedback(claim_id, _hex("b"), version="3" * 20)
        self.assertEqual(status, 404)
        status, _ = self.feedback(claim_id, _hex("b"), verdict="proven")
        self.assertEqual(status, 400)
        status, _ = self.feedback(claim_id, _hex("b"), video_sha256="0" * 64)
        self.assertEqual(status, 400)  # client may not supply the hash; it is computed from the vault
        self.assertEqual(self.ledger, [])

    def test_non_claim_nodes_are_rejected(self):
        written = self._write_hypotheses()
        status, _ = self.feedback(written["idea_id"], _hex("c"))
        self.assertEqual(status, 404)


class ObsidianAuditTests(_Base):
    """B391-P2-12/13: counters reach the API; CRLF and BOM copies are not reported as edits."""

    def test_counts_are_exposed_and_line_endings_or_bom_do_not_conflict(self):
        from muse.obsidian_vault import _note_location, _render_note
        vault = self.root / "vault"
        self.ctx.vault_path = vault
        self.ctx.memory_path.parent.mkdir(parents=True, exist_ok=True)
        with MemoryStore(self.ctx.memory_path) as memory:
            memory.add("我住在上海", source="user", track="user")
            record = memory.list_memories(limit=5)[0]
        relative, _ = _note_location(record)
        note = vault / relative
        note.parent.mkdir(parents=True, exist_ok=True)
        body = _render_note(record).encode("utf-8")
        for label, data in (("lf", body), ("crlf", body.replace(b"\n", b"\r\n")), ("bom", b"\xef\xbb\xbf" + body)):
            note.write_bytes(data)
            status, result = self.get("/api/v1/control/memory/obsidian")
            self.assertEqual(status, 200, label)
            self.assertEqual(result["status"], "audited", label)
            self.assertEqual(result["counts"]["unchanged"], 1, (label, result))
            self.assertEqual(result["conflicts"], 0, label)
        note.write_bytes(body.replace("我住在上海".encode(), "我住在北京".encode()))
        status, result = self.get("/api/v1/control/memory/obsidian")
        self.assertEqual(result["conflicts"], 1)


class GatewayStreamingTests(unittest.TestCase):
    """B391-P2-14: the desktop gateway relays large media without buffering and keeps 206 semantics."""

    def setUp(self):
        import http.server
        import muse_sidecar_main as sidecar
        self.payload = bytes(range(256)) * (40 * 1024 + 7)  # ≈10 MiB, above PROXY_BUFFER_LIMIT
        payload = self.payload

        class Inner(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                rng = self.headers.get("Range")
                body, status = payload, 200
                if rng == "bytes=10-19":
                    body, status = payload[10:20], 206
                self.send_response(status)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                if status == 206:
                    self.send_header("Content-Range", f"bytes 10-19/{len(payload)}")
                self.end_headers()
                self.wfile.write(body)

        self.inner = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Inner)
        self.engine = SimpleNamespace(verbose=False, session_token="s" * 64, inner_token="i" * 64,
                                      inner_port=self.inner.server_address[1], ui_dist=None,
                                      data_root=Path(tempfile.gettempdir()), store_health={},
                                      note_lock=threading.Lock(), consume_nonce=lambda nonce: False,
                                      stop=lambda: None)
        self.gateway = sidecar.GatewayServer(("127.0.0.1", 0), sidecar._make_gateway_handler(self.engine))
        port = self.gateway.server_address[1]
        self.engine.allowed_hosts = frozenset({f"127.0.0.1:{port}"})
        self.engine.allowed_origins = frozenset({f"http://127.0.0.1:{port}"})
        self.port = port
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.inner, self.gateway)]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in (self.gateway, self.inner):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=3)

    def _get(self, headers=None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request("GET", "/api/v1/products/x/y/preview", headers={
                "Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer " + "s" * 64, **(headers or {})})
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    def test_large_media_is_relayed_intact_and_ranges_pass_through(self):
        status, headers, body = self._get()
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["content-length"]), len(self.payload))
        self.assertEqual(hashlib.sha256(body).hexdigest(), hashlib.sha256(self.payload).hexdigest())
        status, headers, body = self._get({"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["content-range"], f"bytes 10-19/{len(self.payload)}")
        self.assertEqual(body, self.payload[10:20])


class TestedByHttpEndToEndTests(unittest.TestCase):
    """B391-P2-10 over real HTTP: Status API + Product Vault + creator ledger + Research Wiki."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        source = self.root / "data" / "video_sources" / "demo"
        source.mkdir(parents=True)
        (source / "01.png").write_bytes(b"image fixture")
        (source / "voice.wav").write_bytes(b"audio fixture")
        self.products = self.root / "data" / "products"
        self.products.mkdir(parents=True)
        (self.root / "data" / "research").mkdir(parents=True)
        self.knowledge = self.root / "data" / "research" / "knowledge.sqlite3"
        self.server, self.token = create_status_server(
            task_db=self.root / "data" / "tasks.sqlite3", progress_db=self.root / "data" / "progress.sqlite3",
            capability_db=self.root / "data" / "capabilities.sqlite3", products_dir=self.products,
            knowledge_db=self.knowledge)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def _call(self, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(self.base + path, data=data, method=method, headers=headers), timeout=20) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            body = json.loads(exc.read() or b"{}")
            exc.close()
            return exc.code, body

    def test_symbolic_observation_to_tested_by_over_http(self):
        from muse.initiative_engine import InitiativeStore
        from muse.video_product_builder import VideoProductBuilder
        inventory = SimpleNamespace(tools=(SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg"),
                                           SimpleNamespace(tool_id="ffprobe", executable="ffprobe")),
                                    hardware_encoders=("h264_amf",))
        with patch("muse.video_product_builder.discover_media_tools", return_value=inventory), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=VaultRangeHttpTests._render):
            product = VideoProductBuilder(self.root).build_from_source("demo", "tested_by 闭环样片", audio_name="voice.wav")
        version = product.get("version_id") or product.get("version")
        initiatives_db = self.products.parent / "initiatives" / "muse.sqlite3"
        initiatives_db.parent.mkdir(parents=True, exist_ok=True)
        with InitiativeStore(initiatives_db) as store:
            initiative = store.propose("粤语旁白节奏实验", kind="creative_output", creator_value="验证节奏假设", concept="样片",
                                       first_step="出样片", expected_output="一条样片")
            store.record_product_artifact(initiative["id"], relative_path=f"data/products/{product['initiative_id']}/{version}",
                                          package_sha256="0" * 64, validated=True)

        # 1. symbolic observation → testable hypotheses written to the Wiki (label kept)
        status, body = self._call("POST", "/api/v1/control/xuanshu/hypotheses", {
            "question": "粤语旁白节奏是否影响完播", "seed": "2026-11-27", "write_to_wiki": True, "confirmation": "WRITE-HYPOTHESES"})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["epistemic_display_label"], "玄学推演／待验证假设")
        self.assertIs(body["is_evidence"], False)
        claim_id = body["written"]["claims"][0]["id"]
        # 2. dispatch a sample-verification Ticket (waiting_approval, never executed)
        status, body = self._call("POST", "/api/v1/control/xuanshu/dispatch", {
            "claim_id": claim_id, "target": "video", "request_id": "7" * 32, "confirmation": "SUBMIT-TO-COMMANDER"})
        self.assertEqual((status, body["task"]["status"]), (201, "waiting_approval"))
        # 3. creator feedback on the vault video → ledger + tested_by
        listing = self._call("GET", "/api/v1/products")[1]
        row = next(item for item in listing["products"] if item["kind"] == "video_creator")
        self.assertTrue(row["creator_feedback_enabled"], row)
        status, linked = self._call("POST", "/api/v1/control/creation/feedback", {
            "initiative_id": row["initiative_id"], "version": row["version_id"], "claim_id": claim_id,
            "verdict": "refutes", "decision": "revise", "feedback": "节奏变化后完播没有提升。", "request_id": "8" * 32,
            "confirmation": "LINK-FEEDBACK-TO-CLAIM"})
        self.assertEqual(status, 201, linked)
        video_bytes = (self.products / row["initiative_id"] / row["version_id"] / "video.mp4").read_bytes()
        self.assertEqual(linked["feedback_record"]["metadata"]["video_sha256"], hashlib.sha256(video_bytes).hexdigest())
        self.assertFalse(linked["ledger"]["deduplicated"])
        # 4. the knowledge graph now shows the closed loop
        status, graph = self._call("GET", "/api/v1/knowledge/graph?limit=160")
        self.assertEqual(status, 200)
        self.assertTrue(any(e["edge_type"] == "tested_by" and e["source_id"] == claim_id for e in graph["edges"]), graph["edges"])
        status, chain = self._call("GET", f"/api/v1/control/knowledge/claims/{claim_id}/tested-by")
        self.assertEqual(status, 200)
        self.assertEqual(chain["claim_status"], "proposed")
        self.assertEqual(chain["origin"]["epistemic_display_label"], "玄学推演／待验证假设")
        self.assertEqual(chain["feedback_records"][0]["creator_verdict"], "refutes")
        with InitiativeStore(initiatives_db) as store:
            timeline = store.connection.execute("SELECT count(*) FROM initiative_feedback WHERE initiative_id=?",
                                                (initiative["id"],)).fetchone()[0]
        self.assertEqual(timeline, 1)
        # replay is idempotent end-to-end
        status, again = self._call("POST", "/api/v1/control/creation/feedback", {
            "initiative_id": row["initiative_id"], "version": row["version_id"], "claim_id": claim_id,
            "verdict": "refutes", "decision": "revise", "feedback": "节奏变化后完播没有提升。", "request_id": "8" * 32,
            "confirmation": "LINK-FEEDBACK-TO-CLAIM"})
        self.assertEqual((status, again["replayed"], again["ledger"]["deduplicated"]), (200, True, True))


class ShadowedImportGuardTests(unittest.TestCase):
    """B391-P2-29: a function-local ``from x import Name`` makes ``Name`` local for the WHOLE function.

    ``status_api.do_POST`` re-imports ``TaskQueue`` in two branches (tests rely on that late lookup to mock it),
    so the POST /api/v1/inspirations/evolve branch — which used ``TaskQueue`` without its own import — crashed
    with UnboundLocalError (seen on the workstation as HTTP 502 through the system proxy). This guard fails for
    any use of a function-local imported name that is not preceded by an import in an enclosing block.
    """

    MODULES = ("status_api.py", "control_center_api.py", "omega_api.py", "local_dashboard.py")

    @staticmethod
    def _unsafe_uses(tree):
        import ast
        problems = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nested = {n for f in ast.walk(func) if f is not func and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                      for n in ast.walk(f)}
            local_imports = {}
            for node in ast.walk(func):
                if node in nested or not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for alias in node.names:
                    local_imports.setdefault((alias.asname or alias.name).split(".")[0], []).append(node)
            if not local_imports:
                continue
            parents = {}
            for parent in ast.walk(func):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            for node in ast.walk(func):
                if node in nested or not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                                          and node.id in local_imports):
                    continue
                current, covered = node, False
                while current is not func and not covered:
                    parent = parents[current]
                    for field in ("body", "orelse", "finalbody"):
                        block = getattr(parent, field, None)
                        if isinstance(block, list) and current in block:
                            covered = any(
                                statement in local_imports[node.id]
                                or any(isinstance(sub, ast.Name) and sub.id == node.id and isinstance(sub.ctx, ast.Store)
                                       for sub in ast.walk(statement))
                                for statement in block[:block.index(current)])
                            if covered:
                                break
                    current = parent
                if not covered:
                    problems.append(f"{func.name}():{node.lineno} uses {node.id} before any local import")
        return problems

    def test_guard_detects_the_pattern(self):
        import ast
        sample = ast.parse("from x import Q\ndef f(a):\n    if a:\n        from x import Q\n        return Q\n    return Q\n")
        self.assertEqual(self._unsafe_uses(sample), ["f():6 uses Q before any local import"])

    def test_http_modules_have_no_unbound_local_import_uses(self):
        import ast
        src_root = Path(__file__).resolve().parents[1] / "src" / "muse"
        offenders = []
        for name in self.MODULES:
            path = src_root / name
            if path.is_file():
                offenders += [name + ":" + item for item in self._unsafe_uses(ast.parse(path.read_text(encoding="utf-8")))]
        self.assertEqual(offenders, [])


class StoreProbeClosesConnectionsTests(unittest.TestCase):
    """B391-P2-34: the startup Store probe must close every SQLite handle (Windows WinError 32 on temp cleanup)."""

    def test_probe_closes_every_connection(self):
        import sqlite3
        from muse import store_health
        opened = []
        real_connect = sqlite3.connect

        def tracking(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            with patch("muse.store_health.sqlite3.connect", side_effect=tracking):
                for name in ("feedback.sqlite3", "knowledge.sqlite3", "agents/registry.sqlite3"):
                    store_health._probe_sqlite(root / name)
            self.assertEqual(len(opened), 3)
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")  # a closed connection refuses every statement


class RemotionFailureDetailTests(unittest.TestCase):
    """B391-P2-36: Remotion/FFmpeg failures must report the first error line, not only trailing stack frames."""

    def test_head_and_tail_are_kept_and_ansi_is_stripped(self):
        from muse.remotion_motion import _failure_detail
        cause = "Error: Command failed with exit code 1: ffprobe.exe -v error http://localhost:3000/public/x.mp4"
        frames = "\n".join("\x1b[31m    at frame" + str(i) + " (X:\\PATH\\TO\\Muse\\video-motion\\x.js:1:1)\x1b[39m" for i in range(200))
        detail = _failure_detail(SimpleNamespace(stderr=cause + "\n" + frames, stdout=""), "fallback")
        self.assertTrue(detail.startswith(cause))
        self.assertIn("frame199", detail)
        self.assertIn("characters omitted", detail)
        self.assertNotIn("\x1b[", detail)
        self.assertLess(len(detail), 3200)

    def test_short_output_and_fallback(self):
        from muse.remotion_motion import _failure_detail
        self.assertEqual(_failure_detail(SimpleNamespace(stderr="", stdout="boom"), "fallback"), "boom")
        self.assertEqual(_failure_detail(SimpleNamespace(stderr=None, stdout=None), "fallback"), "fallback")
        self.assertEqual(_failure_detail(SimpleNamespace(stderr=b"\xff bad", stdout=None), "f"), "\ufffd bad")


class ByteRangeParserTests(unittest.TestCase):
    """B391-P2-11: RFC 9110 single byte ranges for Product Vault media."""

    def test_valid_forms(self):
        self.assertEqual(parse_byte_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_byte_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_byte_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_byte_range("bytes=-500", 100), (0, 99))
        self.assertEqual(parse_byte_range("bytes=50-5000", 100), (50, 99))
        self.assertEqual(parse_byte_range(" bytes = 1 - 2 ", 100), (1, 2))

    def test_unsatisfiable_and_ignored(self):
        self.assertEqual(parse_byte_range("bytes=100-", 100), "unsatisfiable")
        self.assertEqual(parse_byte_range("bytes=5-1", 100), "unsatisfiable")
        self.assertEqual(parse_byte_range("bytes=-0", 100), "unsatisfiable")
        self.assertEqual(parse_byte_range("bytes=0-", 0), "unsatisfiable")   # empty file: no division, no crash
        self.assertEqual(parse_byte_range("bytes=-5", 0), "unsatisfiable")
        for ignored in (None, "", "bytes=-", "items=0-1", "bytes=0-1,4-5", "bytes=a-b", "bytes=" + "9" * 30 + "-"):
            self.assertIsNone(parse_byte_range(ignored, 100), ignored)


class VaultRangeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        source = self.root / "data" / "video_sources" / "demo"
        source.mkdir(parents=True)
        (source / "01.png").write_bytes(b"image fixture")
        (source / "voice.wav").write_bytes(b"audio fixture")
        self.products = self.root / "data" / "products"
        self.products.mkdir(parents=True)
        self.server, self.token = create_status_server(
            task_db=self.root / "tasks.sqlite3", progress_db=self.root / "progress.sqlite3",
            capability_db=self.root / "capabilities.sqlite3", products_dir=self.products)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    @staticmethod
    def _render(source, output, **kwargs):
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 4)
        return {"status": "completed", "output": str(output), "scene_count": 1, "duration_seconds": 4.0,
                "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True, "encoder": "h264_amf",
                "bytes": output.stat().st_size, "audio_mode": kwargs.get("audio_mode")}

    def _get(self, path, headers=None):
        request = Request(self.base + path, headers={"Authorization": "Bearer " + self.token, **(headers or {})})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            body = exc.read()
            headers = dict(exc.headers)
            exc.close()
            return exc.code, headers, body

    def test_preview_supports_206_and_416(self):
        from muse.video_product_builder import VideoProductBuilder
        inventory = SimpleNamespace(tools=(SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg"),
                                           SimpleNamespace(tool_id="ffprobe", executable="ffprobe")),
                                    hardware_encoders=("h264_amf",))
        with patch("muse.video_product_builder.discover_media_tools", return_value=inventory), \
             patch("muse.video_product_builder.assemble_video_from_folder", side_effect=self._render):
            product = VideoProductBuilder(self.root).build_from_source("demo", "Range 测试", audio_name="voice.wav")
        path = f"/api/v1/products/{product['initiative_id']}/{product['version_id'] if 'version_id' in product else product['version']}/preview"
        status, headers, full = self._get(path)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        size = len(full)
        status, headers, part = self._get(path, {"Range": "bytes=4-11"})
        self.assertEqual(status, 206)
        self.assertEqual(headers.get("Content-Range"), f"bytes 4-11/{size}")
        self.assertEqual(part, full[4:12])
        status, headers, tail = self._get(path, {"Range": "bytes=-16"})
        self.assertEqual((status, tail), (206, full[-16:]))
        status, headers, _ = self._get(path, {"Range": f"bytes={size}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("Content-Range"), f"bytes */{size}")
        status, _, again = self._get(path, {"Range": "bytes=0-1,4-5"})
        self.assertEqual((status, again), (200, full))


if __name__ == "__main__":
    unittest.main()
