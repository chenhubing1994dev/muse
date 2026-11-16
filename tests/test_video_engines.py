from __future__ import annotations

import json
import unittest

from muse.video_engines import ComfyUIClient, LocalHTTPJSONClient, VelornMCPClient


class VideoEngineClientTests(unittest.TestCase):
    def test_only_loopback_http_endpoints_are_accepted(self):
        for endpoint in ("https://example.com", "http://192.168.1.20:8188",
                         "http://user:pass@127.0.0.1:8188", "http://127.0.0.1:8188/path"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    LocalHTTPJSONClient(endpoint)

    def test_comfyui_object_info_returns_only_bounded_node_summary(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            self.assertEqual(method, "GET")
            name = url.rsplit("/", 1)[-1]
            body = {name: {}} if name == "KSampler" else {}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = ComfyUIClient(requester=requester)
        self.assertEqual(client.object_info_summary(["KSampler", "MissingNode"]), {
            "node_count": None,
            "available_required_nodes": ["KSampler"],
            "missing_required_nodes": ["MissingNode"],
        })
    def test_comfyui_workflow_requires_approval_before_transport(self):
        calls = []
        client = ComfyUIClient(requester=lambda *args: calls.append(args))
        graph = {"1": {"class_type": "KSampler", "inputs": {}}}
        with self.assertRaises(PermissionError):
            client.submit_workflow(graph, node_allowlist={"KSampler"})
        self.assertEqual(calls, [])

    def test_comfyui_workflow_requires_explicit_node_allowlist(self):
        calls = []
        client = ComfyUIClient(requester=lambda *args: calls.append(args))
        graph = {"1": {"class_type": "KSampler", "inputs": {}}}
        with self.assertRaises(ValueError):
            client.submit_workflow(graph, node_allowlist=set(), approved=True)
        self.assertEqual(calls, [])

    def test_comfyui_workflow_rejects_unapproved_node_types(self):
        calls = []
        client = ComfyUIClient(requester=lambda *args: calls.append(args))
        graph = {"1": {"class_type": "ArbitraryCustomNode", "inputs": {}}}
        with self.assertRaises(PermissionError):
            client.submit_workflow(graph, node_allowlist={"KSampler"}, approved=True)
        self.assertEqual(calls, [])

    def test_comfyui_workflow_rejects_missing_installed_node_before_post(self):
        calls = []
        def requester(url, method, data, headers, timeout, max_bytes):
            calls.append((url, method))
            return 200, {"Content-Type": "application/json"}, b"{}"
        client = ComfyUIClient(requester=requester)
        with self.assertRaisesRegex(ValueError, "missing required node"):
            client.submit_workflow(
                {"1": {"class_type": "MissingCustomNode", "inputs": {}}},
                node_allowlist={"MissingCustomNode"}, approved=True,
            )
        self.assertEqual(calls, [("http://127.0.0.1:8188/object_info/MissingCustomNode", "GET")])
    def test_comfyui_workflow_submits_only_approved_allowlisted_graph(self):
        calls = []
        def requester(url, method, data, headers, timeout, max_bytes):
            calls.append((url, method, json.loads(data) if data else None))
            if url.endswith("/object_info/KSampler"):
                return 200, {"Content-Type": "application/json"}, b'{"KSampler":{}}'
            return 200, {"Content-Type": "application/json"}, b'{"prompt_id":"abc-123","number":7}'
        client = ComfyUIClient(requester=requester)
        graph = {"1": {"class_type": "KSampler", "inputs": {}}}
        result = client.submit_workflow(graph, node_allowlist={"KSampler"}, approved=True)
        self.assertEqual(result, {
            "status": "queued", "prompt_id": "abc-123", "queue_number": 7, "node_errors": {},
        })
        self.assertEqual(calls[0][0], "http://127.0.0.1:8188/object_info/KSampler")
        self.assertEqual(calls[0][1], "GET")
        self.assertEqual(calls[1][0], "http://127.0.0.1:8188/prompt")
        self.assertEqual(calls[1][1], "POST")
        self.assertEqual(calls[1][2], {"prompt": graph})
    def test_comfyui_workflow_rejects_malformed_graph_before_transport(self):
        calls = []
        client = ComfyUIClient(requester=lambda *args: calls.append(args))
        for graph in ({}, {"../x": {"class_type": "KSampler", "inputs": {}}}, {"1": "bad"}):
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    client.submit_workflow(graph, node_allowlist={"KSampler"}, approved=True)
        self.assertEqual(calls, [])

    def test_comfyui_workflow_rejects_invalid_inputs_before_network(self):
        calls = []
        client = ComfyUIClient(requester=lambda *args: calls.append(args))
        deep = 1
        for _ in range(14):
            deep = [deep]
        invalid_graphs = (
            {"1": {"class_type": "KSampler", "inputs": []}},
            {"1": {"class_type": "KSampler", "inputs": {"seed": float("nan")}}},
            {"1": {"class_type": "KSampler", "inputs": {}, "python": "not allowed"}},
            {"1": {"class_type": "KSampler", "inputs": {"deep": deep}}},
        )
        for graph in invalid_graphs:
            with self.subTest(graph=str(graph)[:80]):
                with self.assertRaises(ValueError):
                    client.submit_workflow(graph, node_allowlist={"KSampler"}, approved=True)
        self.assertEqual(calls, [])

    def test_comfyui_workflow_rejects_malformed_prompt_id_response(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            if url.endswith("/object_info/KSampler"):
                return 200, {"Content-Type": "application/json"}, b'{"KSampler":{}}'
            return 200, {"Content-Type": "application/json"}, b'{"prompt_id":"../queue"}'
        client = ComfyUIClient(requester=requester)
        with self.assertRaises(RuntimeError):
            client.submit_workflow(
                {"1": {"class_type": "KSampler", "inputs": {}}},
                node_allowlist={"KSampler"}, approved=True,
            )

    def test_comfyui_history_validates_prompt_id_before_request(self):
        calls = []
        def requester(url, method, data, headers, timeout, max_bytes):
            calls.append(url)
            return 200, {"Content-Type": "application/json"}, b'{"prompt":{}}'
        client = ComfyUIClient(requester=requester)
        self.assertEqual(client.history("abc-123"), {"prompt": {}})
        self.assertTrue(calls[0].endswith("/history/abc-123"))
        for bad in ("../queue", "x/y", "", "a" * 129):
            with self.subTest(prompt_id=bad):
                with self.assertRaises(ValueError):
                    client.history(bad)
        self.assertEqual(len(calls), 1)


    def test_comfyui_object_info_rejects_unbounded_node_lists(self):
        client = ComfyUIClient(requester=lambda *args: self.fail("request must not run"))
        with self.assertRaises(ValueError):
            client.object_info_summary(["KSampler"] * 129)

    def test_comfyui_gpu_memory_reports_real_device_and_torch_pool_counters(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/system_stats"))
            return 200, {"Content-Type": "application/json"}, json.dumps({"devices": [
                {"vram_total": 1200, "vram_free": 900, "torch_vram_total": 1000, "torch_vram_free": 800},
                {"vram_total": 800, "vram_free": 700, "torch_vram_total": 700, "torch_vram_free": 600},
            ]}).encode()
        result = ComfyUIClient(requester=requester).gpu_memory()
        self.assertEqual(result["vram_total_bytes"], 2000)
        self.assertEqual(result["vram_free_bytes"], 1600)
        self.assertEqual(result["vram_free_ratio"], 0.8)
        self.assertEqual(result["torch_vram_total_bytes"], 1700)
        self.assertEqual(result["torch_vram_free_bytes"], 1400)

    def test_comfyui_gpu_memory_rejects_missing_or_invalid_counters(self):
        for devices in ([], [{"vram_total": 0, "vram_free": 0}], [{"vram_total": 100, "vram_free": 101}]):
            with self.subTest(devices=devices):
                client = ComfyUIClient(requester=lambda *args, d=devices: (
                    200, {"Content-Type": "application/json"}, json.dumps({"devices": d}).encode()
                ))
                with self.assertRaises(RuntimeError):
                    client.gpu_memory()

    def test_comfyui_free_models_uses_only_documented_unload_flags(self):
        calls = []
        def requester(url, method, data, headers, timeout, max_bytes):
            calls.append((url, method, json.loads(data)))
            return 200, {"Content-Type": "application/json"}, b""
        result = ComfyUIClient(requester=requester).free_models()
        self.assertEqual(result, {"status": "requested"})
        self.assertEqual(calls, [("http://127.0.0.1:8188/free", "POST", {
            "unload_models": True, "free_memory": True,
        })])

    def test_comfyui_health_reports_device_and_queue_counts(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            if url.endswith("/system_stats"):
                body = {"devices": [{"name": "AMD"}]}
            else:
                body = {"queue_running": [["a"]], "queue_pending": [["b"], ["c"]]}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = ComfyUIClient(requester=requester)
        self.assertEqual(client.health(), {
            "status": "ready", "device_count": 1, "running_jobs": 1, "pending_jobs": 2,
        })

    def test_comfyui_health_degrades_cleanly_when_service_is_down(self):
        def requester(*args):
            raise RuntimeError("local video engine is unavailable")
        self.assertEqual(ComfyUIClient(requester=requester).health()["status"], "unavailable")

    def test_velorn_mcp_uses_documented_direct_tools_list_and_calls_read_only_tool(self):
        calls = []
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            calls.append((url, payload, headers))
            if payload["method"] == "tools/list":
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [
                    {"name": "get_project", "annotations": {"readOnlyHint": True}},
                    {"name": "edit_timeline", "annotations": {"readOnlyHint": False}},
                ]}}
            else:
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"type": "text", "text": "ok"}]}}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()

        client = VelornMCPClient(requester=requester)
        tools = client.list_tools()
        self.assertEqual([item["name"] for item in tools], ["get_project", "edit_timeline"])
        result = client.call_tool("get_project", {})
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(calls[0][1]["method"], "tools/list")
        self.assertNotIn("Mcp-Session-Id", calls[0][2])

    def test_velorn_documented_read_only_tools_without_annotations_are_callable(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            if payload["method"] == "tools/list":
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [
                    {"name": "get_project", "description": "Read project state"},
                    {"name": "list_recent_projects", "description": "List recent projects"},
                ]}}
            else:
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = VelornMCPClient(requester=requester)
        tools = client.list_tools()
        self.assertEqual([item["read_only"] for item in tools], [True, True])
        self.assertEqual(client.call_tool("get_project", {})["ok"], True)

    def test_unknown_unannotated_velorn_tool_remains_approval_gated(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            if payload["method"] == "tools/list":
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [
                    {"name": "unknown_future_tool", "description": "Unknown behavior"},
                ]}}
            else:
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = VelornMCPClient(requester=requester, write_allowlist={"unknown_future_tool"})
        client.list_tools()
        with self.assertRaises(PermissionError):
            client.call_tool("unknown_future_tool", {}, approved=False)
        self.assertEqual(client.call_tool("unknown_future_tool", {}, approved=True)["ok"], True)

    def test_velorn_write_tools_need_both_approval_and_allowlist(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            if payload["method"] == "tools/list":
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [
                    {"name": "edit_timeline", "annotations": {"readOnlyHint": False}},
                ]}}
            else:
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = VelornMCPClient(requester=requester, write_allowlist={"edit_timeline"})
        client.list_tools()
        with self.assertRaises(PermissionError):
            client.call_tool("edit_timeline", {}, approved=False)
        self.assertEqual(client.call_tool("edit_timeline", {}, approved=True)["ok"], True)

    def test_velorn_write_tool_not_in_allowlist_is_rejected_even_when_approved(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            if payload["method"] == "tools/list":
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [
                    {"name": "delete_project", "annotations": {"readOnlyHint": False}},
                ]}}
            else:
                body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
        client = VelornMCPClient(requester=requester)
        client.list_tools()
        with self.assertRaises(PermissionError):
            client.call_tool("delete_project", {}, approved=True)


    def test_non_success_http_status_is_rejected(self):
        client = LocalHTTPJSONClient(
            "http://127.0.0.1:8188",
            requester=lambda *args: (302, {"Content-Type": "application/json"}, b"{}"),
        )
        with self.assertRaises(RuntimeError):
            client.request_json("GET", "/system_stats")

    def test_request_body_is_bounded_before_transport(self):
        called = []
        client = LocalHTTPJSONClient("http://127.0.0.1:8188", requester=lambda *args: called.append(True))
        with self.assertRaises(ValueError):
            client.request_json("POST", "/prompt", payload={"workflow": "x" * 1_100_000})
        self.assertEqual(called, [])

    def test_velorn_mismatched_json_rpc_id_is_rejected(self):
        def requester(url, method, data, headers, timeout, max_bytes):
            payload = json.loads(data)
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "jsonrpc": "2.0", "id": 999, "result": {"tools": []},
            }).encode()
        client = VelornMCPClient(requester=requester)
        with self.assertRaises(RuntimeError):
            client.list_tools()

if __name__ == "__main__":
    unittest.main()
