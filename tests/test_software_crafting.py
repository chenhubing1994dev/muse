from __future__ import annotations

import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from muse.software_crafting import MCPStdioClient, SoftwareCraftingAdapter
from muse.task_queue import TaskQueue


class MCPStdioClientTests(unittest.TestCase):
    def make_client(self, root: Path) -> MCPStdioClient:
        return MCPStdioClient(
            command=("python", "fake-mcp.py"),
            workspace=root,
            allowed_tools={"safe_read", "approved_capture"},
        )

    @patch("muse.software_crafting.subprocess.run")
    def test_calls_allowlisted_tool_using_stdio_without_shell(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = self.make_client(root)
            run.return_value = __import__("subprocess").CompletedProcess(
                args=[],
                returncode=0,
                stdout="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "fixture", "version": "1.0"},
                    }}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                        {"name": "safe_read", "annotations": {"readOnlyHint": True}},
                    ]}}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": [
                        {"type": "text", "text": "evidence-ready"},
                    ]}}),
                ]),
                stderr="",
            )
            result = client.call_tool("safe_read", {"target": "fixture"}, timeout_seconds=45)
            self.assertEqual(result["content"][0]["text"], "evidence-ready")
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["python", "fake-mcp.py"])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["cwd"], str(root.resolve()))
            self.assertEqual(kwargs["timeout"], 45)
            sent = [json.loads(line) for line in kwargs["input"].splitlines()]
            self.assertEqual(sent[0]["method"], "initialize")
            self.assertEqual(sent[1]["method"], "notifications/initialized")
            self.assertEqual(sent[-1]["method"], "tools/call")

    @patch("muse.software_crafting.subprocess.run")
    def test_mcp_child_environment_overrides_are_applied_before_secret_scrubbing(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"MUSE_TEST_TOKEN": "ambient-secret"}, clear=False,
        ):
            client = MCPStdioClient(
                command=("node.exe", "daemon-cli.mjs"), workspace=Path(directory),
                allowed_tools={"safe_read"},
                environment={
                    "ELECTRON_RUN_AS_NODE": "1",
                    "MUSE_OPEN_DESIGN_API_KEY": "must-not-be-forwarded",
                },
            )
            run.return_value = __import__("subprocess").CompletedProcess(
                args=[], returncode=0, stdout="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                        "protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "1"},
                    }}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                        {"name": "safe_read", "annotations": {"readOnlyHint": True}},
                    ]}}),
                ]), stderr="",
            )
            client.list_tools()
            child_env = run.call_args.kwargs["env"]
            self.assertEqual(child_env["ELECTRON_RUN_AS_NODE"], "1")
            self.assertNotIn("MUSE_TEST_TOKEN", child_env)
            self.assertNotIn("MUSE_OPEN_DESIGN_API_KEY", child_env)


    def test_multi_tool_batch_runs_sequentially_in_one_mcp_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "fake_mcp.py"
            script.write_text(
                """\
import json, sys
opened = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        response = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "1"}}
    elif method == "tools/list":
        response = {"tools": [
            {"name": "web_site_open", "annotations": {"readOnlyHint": False}},
            {"name": "web_site_capture", "annotations": {"readOnlyHint": False}},
        ]}
    elif method == "tools/call":
        name = request["params"]["name"]
        if name == "web_site_open":
            opened = True
            response = {"content": [{"type": "text", "text": "opened"}]}
        else:
            response = {
                "isError": not opened,
                "content": [{"type": "text", "text": "captured" if opened else "no website is open"}],
            }
    else:
        continue
    if "id" in request:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": response}), flush=True)
""",
                encoding="utf-8",
            )
            client = MCPStdioClient(
                command=(sys.executable, str(script)), workspace=root,
                allowed_tools={'web_site_open', 'web_site_capture'},
            )
            results = client.call_tools([
                {'name':'web_site_open','arguments':{'url':'https://example.com/'}},
                {'name':'web_site_capture','arguments':{'output_directory':str(root / 'capture')}},
            ], approved=True, timeout_seconds=15)
            self.assertEqual([item['content'][0]['text'] for item in results], ['opened', 'captured'])
            client = MCPStdioClient(
                command=(sys.executable, str(script)), workspace=root,
                allowed_tools={'web_site_open', 'web_site_capture'},
            )
            results = client.call_tools([
                {'name':'web_site_open','arguments':{'url':'https://example.com/'}},
                {'name':'web_site_capture','arguments':{'output_directory':str(root / 'capture')}},
            ], approved=True, timeout_seconds=15)
            self.assertEqual([item['content'][0]['text'] for item in results], ['opened', 'captured'])

    @patch("muse.software_crafting.subprocess.run")
    def test_non_read_only_tool_requires_per_call_approval(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            run.return_value = __import__("subprocess").CompletedProcess(
                args=[], returncode=0, stdout="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                        "protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "1"},
                    }}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                        {"name": "approved_capture", "annotations": {"readOnlyHint": False, "destructiveHint": False}},
                    ]}}),
                ]), stderr="",
            )
            with self.assertRaises(PermissionError):
                client.call_tool("approved_capture", {"url": "http://127.0.0.1/"})
            sent = [json.loads(line) for line in run.call_args.kwargs["input"].splitlines()]
            self.assertFalse(any(item.get("method") == "tools/call" for item in sent))
            run.reset_mock()

    @patch("muse.software_crafting.subprocess.run")
    def test_approved_tool_still_requires_static_allowlist(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            with self.assertRaises(PermissionError):
                client.call_tool("arbitrary_shell", {"command": "whoami"}, approved=True)
            run.assert_not_called()

    @patch("muse.software_crafting.subprocess.run")
    def test_tool_failure_includes_tool_name_and_mcp_content(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            run.return_value = __import__("subprocess").CompletedProcess(
                args=[], returncode=0, stdout="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                        "protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "1"},
                    }}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                        {"name": "approved_capture", "annotations": {"readOnlyHint": False}},
                    ]}}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "result": {
                        "isError": True, "content": [{"type": "text", "text": "browser provider cannot bind target"}],
                    }}),
                ]), stderr="",
            )
            with self.assertRaisesRegex(RuntimeError, "approved_capture.*browser provider cannot bind target"):
                client.call_tool("approved_capture", {"url": "https://example.com"}, approved=True)

    @patch("muse.software_crafting.subprocess.run")
    def test_missing_tool_response_includes_bounded_process_diagnostics(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            run.return_value = __import__("subprocess").CompletedProcess(
                args=[], returncode=0, stdout="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                        "protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "1"},
                    }}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                        {"name": "safe_read", "annotations": {"readOnlyHint": True}},
                    ]}}),
                ]), stderr="worker crashed during analysis",
            )
            with self.assertRaisesRegex(RuntimeError, "safe_read.*worker crashed during analysis"):
                client.call_tool("safe_read", {"target": "fixture"}, approved=True)


class SoftwareCraftingAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.database = self.workspace / "data" / "tasks" / "muse.sqlite3"
        self.adapter = SoftwareCraftingAdapter(
            workspace=self.workspace,
            database=self.database,
            craft_cli=None,
            rea_command=None,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_craft_mcp_is_scoped_to_persistent_project_and_person_approval_inbox(self) -> None:
        cli = self.workspace / "craft-cli.exe"
        cli.write_bytes(b"fixture")
        project = self.workspace / "data" / "software_crafts" / "muse-engine.cany"
        adapter = SoftwareCraftingAdapter(
            workspace=self.workspace, database=self.database, craft_cli=cli,
            rea_command=None, craft_project=project,
        )
        command = adapter.craft.command
        self.assertIn("--project", command)
        self.assertEqual(command[command.index("--project") + 1], str(project.resolve()))
        self.assertIn("--inbox", command)
        expected_inbox = (self.workspace / "data" / "software_crafts" / "muse-craft-project" / "inbox").resolve()
        self.assertEqual(command[command.index("--inbox") + 1], str(expected_inbox))
        self.assertNotIn("--allow-run", command)

    def test_recreation_requires_captured_evidence_before_commander_enqueue(self) -> None:
        with self.assertRaisesRegex(ValueError, "study evidence"):
            self.adapter.enqueue_task(
                mode="recreate", target="https://example.com",
                request="Recreate the public landing page", stack="React + Vite",
                target_kind="website", authorization_confirmed=True,
            )
        with TaskQueue(self.database) as queue:
            self.assertEqual(queue.snapshot()["counts"].get("total", 0), 0)

    def test_recreation_task_enters_existing_commander_queue_waiting_for_approval(self) -> None:
        evidence = self.workspace / "data" / "software_crafts" / "demo" / "study"
        evidence.mkdir(parents=True)
        (evidence / "capture.json").write_text("{}", encoding="utf-8")
        task = self.adapter.enqueue_task(
            mode="recreate", target="https://example.com",
            request="Recreate the public landing page", stack="React + Vite",
            evidence_directory=evidence, target_kind="website", authorization_confirmed=True,
        )
        self.assertEqual(task["status"], "waiting_approval")
        self.assertEqual(task["payload"]["kind"], "software_reconstruction")
        self.assertTrue(task["payload"]["approval_required"])
        self.assertEqual(task["payload"]["mode"], "recreate")
        self.assertTrue(task["payload"]["recreation_plan"]["authorization_confirmed"])
        self.assertEqual(task["payload"]["stack"], "React + TypeScript (Vite)")
        self.assertTrue(task["payload"]["evolution_cycle_id"].startswith("cycle_"))
        self.assertTrue(Path(task["payload"]["output_directory"]).resolve().is_relative_to(self.workspace.resolve()))

    def test_create_mode_accepts_no_reference_and_uses_same_commander_queue(self) -> None:
        task = self.adapter.enqueue_task(
            mode="create", target=None,
            request="Create a local Markdown knowledge editor with graph view",
            stack="Rust + egui",
        )
        self.assertEqual(task["status"], "waiting_approval")
        self.assertIsNone(task["payload"]["target"])
        self.assertEqual(task["payload"]["kind"], "software_reconstruction")

    def test_rejects_target_and_output_paths_outside_supported_scope(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.enqueue_task(
                mode="recreate", target="file:///C:/Windows/win.ini",
                request="Recreate", stack="Rust", target_kind="file", authorization_confirmed=True,
            )
        with self.assertRaises(ValueError):
            self.adapter.enqueue_task(
                mode="create", target=None, request="Create a tool", stack="Python",
                output_directory=self.workspace.parent / "outside-muse",
            )

    def test_website_study_requires_explicit_approval_before_tool_transport(self) -> None:
        fake = Mock()
        self.adapter.craft = fake
        with self.assertRaises(PermissionError):
            self.adapter.study_website("https://example.com", approved=False, authorization_confirmed=True)
        fake.call_tools.assert_not_called()

    def test_craft_study_prepares_project_approval_inbox(self) -> None:
        output = self.workspace / "data" / "software_crafts" / "demo" / "study"
        fake = Mock()
        fake.call_tools.return_value = [{"tool": "web_site_open"}, {"tool": "web_site_capture"}]
        self.adapter.craft = fake
        self.adapter.ego_browser = Mock(spec=["probe_runtime", "doctor"])
        self.adapter.ego_browser.probe_runtime.return_value = {"runtime_ready": False, "available": False}
        self.adapter.craft_project = self.workspace / "data" / "software_crafts" / "muse-engine.cany"
        self.adapter.craft_inbox = self.adapter.craft_project / "inbox"
        self.adapter.study_website(
            "https://example.com", output_directory=output, approved=True,
            authorization_confirmed=True, provider="craft-anything",
        )
        self.assertTrue(self.adapter.craft_inbox.is_dir())

    def test_website_study_keeps_open_and_capture_in_one_mcp_session(self) -> None:
        output = self.workspace / "data" / "software_crafts" / "demo" / "study"
        fake = Mock()
        fake.call_tools.return_value = [{"tool": "web_site_open"}, {"tool": "web_site_capture"}]
        self.adapter.craft = fake
        with patch.object(
            self.adapter.ego_browser, "probe_runtime",
            return_value={"available": False, "runtime_ready": False},
        ):
            result = self.adapter.study_website(
                "https://example.com", output_directory=output, approved=True, authorization_confirmed=True,
            )
        calls = fake.call_tools.call_args.args[0]
        kwargs = fake.call_tools.call_args.kwargs
        self.assertEqual([item["name"] for item in calls], ["web_site_open", "web_site_capture"])
        self.assertTrue(kwargs["approved"])
        self.assertEqual(kwargs["timeout_seconds"], 600.0)
        self.assertEqual(len(result), 2)

    def test_auto_study_prefers_ego_lite_when_runtime_is_available(self) -> None:
        output = self.workspace / "data" / "software_crafts" / "ego-study"
        ego = Mock(spec=["probe_runtime", "capture_url"])
        ego.probe_runtime.return_value = {"runtime_ready": True}
        def capture_url(url, **kwargs):
            target = Path(kwargs["output_directory"])
            target.mkdir(parents=True)
            (target / "capture.json").write_text("{}", encoding="utf-8")
            return {"provider": "ego-lite", "evidence_directory": str(target)}
        ego.capture_url.side_effect = capture_url
        self.adapter.ego_browser = ego
        self.adapter.craft = Mock()
        result = self.adapter.study_website(
            "https://example.com", output_directory=output,
            approved=True, authorization_confirmed=True,
        )
        self.assertEqual(result[0]["provider"], "ego-lite")
        ego.capture_url.assert_called_once()
        self.adapter.craft.call_tools.assert_not_called()

    def test_explicit_ego_provider_fails_closed_when_app_is_not_ready(self) -> None:
        self.adapter.ego_browser = Mock(spec=["probe_runtime", "capture_url"])
        self.adapter.ego_browser.probe_runtime.return_value = {"runtime_ready": False}
        with self.assertRaisesRegex(RuntimeError, "ego-lite"):
            self.adapter.study_website(
                "https://example.com", output_directory="data/ego-study",
                approved=True, authorization_confirmed=True, provider="ego-lite",
            )
        self.adapter.ego_browser.capture_url.assert_not_called()

    def test_study_rejects_unknown_browser_provider(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.study_website(
                "https://example.com", approved=True, authorization_confirmed=True,
                provider="unknown",
            )

    def test_doctor_reports_ego_lite_browser_readiness(self) -> None:
        self.adapter.ego_browser = Mock()
        self.adapter.ego_browser.probe_runtime.return_value = {"provider": "ego-lite", "available": True, "runtime_ready": True}
        result = self.adapter.doctor()
        self.adapter.ego_browser.probe_runtime.assert_called_once_with()
        self.assertEqual(result["ego_lite"]["provider"], "ego-lite")
        self.assertTrue(result["ego_lite"]["runtime_ready"])

    def test_tool_schema_returns_exact_allowlisted_upstream_schema(self) -> None:
        self.adapter.rea = Mock()
        self.adapter.rea.list_tools.return_value = [
            {"name": "analyze_javascript_application", "description": "Analyze a JS app",
             "inputSchema": {"type": "object", "properties": {"input_path": {"type": "string"}}},
             "annotations": {"readOnlyHint": True}},
        ]
        result = self.adapter.tool_schema("rea", "analyze_javascript_application")
        self.assertEqual(result["inputSchema"]["properties"]["input_path"]["type"], "string")
        self.assertTrue(result["annotations"]["readOnlyHint"])

    def test_tool_schema_rejects_names_outside_static_allowlist(self) -> None:
        self.adapter.rea = Mock()
        with self.assertRaises(PermissionError):
            self.adapter.tool_schema("rea", "run_arbitrary_shell")
        self.adapter.rea.list_tools.assert_not_called()

    def test_study_refuses_existing_output_folder_to_preserve_prior_evidence(self) -> None:
        output = self.workspace / "data" / "software_crafts" / "demo" / "study"
        output.mkdir(parents=True)
        self.adapter.craft = Mock()
        with self.assertRaises(FileExistsError):
            self.adapter.study_website(
                "https://example.com", output_directory=output, approved=True, authorization_confirmed=True,
            )
        self.adapter.craft.call_tools.assert_not_called()


    def test_website_study_requires_target_authorization_even_when_approved(self) -> None:
        fake = Mock()
        self.adapter.craft = fake
        with self.assertRaises(PermissionError):
            self.adapter.study_website("https://example.com", approved=True, authorization_confirmed=False)
        fake.call_tools.assert_not_called()

    def test_deep_recreation_requires_an_explicit_authorization_scope(self) -> None:
        evidence = self.workspace / "data" / "software_crafts" / "demo" / "study"
        evidence.mkdir(parents=True)
        (evidence / "rea-evidence.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.adapter.enqueue_task(
                mode="recreate", target="D:/apps/owned-app.exe", target_kind="binary",
                request="Recreate an owned app feature", authorization_confirmed=True,
                study_mode="deep", evidence_directory=evidence,
            )

    def test_recreation_requires_authorization_before_study_or_queue(self) -> None:
        with self.assertRaises(PermissionError):
            self.adapter.enqueue_task(
                mode="recreate", target="https://example.com", target_kind="website",
                request="Recreate a page", authorization_confirmed=False,
            )

    def test_execute_rebuild_requires_recorded_task_approval(self) -> None:
        with self.assertRaises(PermissionError):
            self.adapter.execute_rebuild({"kind": "software_reconstruction", "_approved": False})

    @patch("muse.software_crafting.subprocess.run")
    def test_execute_rebuild_uses_clean_room_unattended_local_backend_and_copies_evidence(self, run: Mock) -> None:
        cli = self.workspace / "craft-cli.exe"
        cli.write_bytes(b"fixture")
        project = self.workspace / "data" / "software_crafts" / "muse-engine.cany"
        project.mkdir(parents=True)
        evidence = self.workspace / "data" / "software_crafts" / "demo" / "study"
        evidence.mkdir(parents=True)
        (evidence / "capture.json").write_text("{}", encoding="utf-8")
        output = self.workspace / "data" / "software_crafts" / "demo" / "build"
        self.adapter.craft_cli = cli
        self.adapter.craft_project = project
        payload = {
            "kind": "software_reconstruction", "mode": "recreate", "target": "https://example.com",
            "request": "Recreate the observed demo", "stack": "Plain HTML and CSS",
            "evidence_directory": str(evidence), "output_directory": str(output),
            "study_mode": "clean_room", "max_refine_rounds": 1, "authorization_confirmed": True,
            "_approved": True, "_approval": {"approved_by": "test-user"},
        }
        def fake_run(command, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertEqual(Path(kwargs["cwd"]).resolve(), self.workspace.resolve())
            if "ai" in command and "list" in command:
                return __import__("subprocess").CompletedProcess(command, 0, stdout=json.dumps([{"name": "Muse Local", "enabled": True}]), stderr="")
            self.assertIn("--clean-room", command)
            self.assertIn("--unattended", command)
            self.assertIn("--existing", command)
            self.assertIn("start-from", command)
            self.assertNotIn("--allow-run", command)
            self.assertIn("Muse Local", command)
            app = output / "app"
            app.mkdir(parents=True)
            (app / "index.html").write_text("<!doctype html><html><body>ok</body></html>", encoding="utf-8")
            (output / "craft.json").write_text(json.dumps({"status": "ready", "checks": [{"passed": True}]}), encoding="utf-8")
            return __import__("subprocess").CompletedProcess(command, 0, stdout="Ready", stderr="")
        run.side_effect = fake_run
        result = self.adapter.execute_rebuild(payload, task_id="task-123", approved=True)
        self.assertTrue(result["artifact_validated"])
        self.assertFalse(result["functional_tests_passed"])
        self.assertTrue(result["static_validation"]["passed"])
        self.assertEqual(result["build_status"], "ready")
        self.assertTrue((output / "evidence" / "capture.json").is_file())
        self.assertTrue((output / "muse-build-manifest.json").is_file())

    def test_inspect_with_rea_requires_authorization_and_approval(self) -> None:
        fake = Mock()
        self.adapter.rea = fake
        with self.assertRaises(PermissionError):
            self.adapter.inspect_with_rea(
                "inspect_managed_artifact", {"path": "D:/owned.exe"},
                authorization_confirmed=True, approved=False,
                evidence_directory=self.workspace / "data" / "rea-study",
            )
        fake.call_tool.assert_not_called()

    def test_rea_input_path_accepts_existing_absolute_project_directory(self) -> None:
        target = self.workspace / "owned-source"
        target.mkdir()
        output = self.workspace / "data" / "rea-evidence"
        fake = Mock()
        fake.call_tool.return_value = {"content": [{"type": "text", "text": "summary"}]}
        self.adapter.rea = fake
        result = self.adapter.inspect_with_rea(
            "analyze_javascript_application", {"input_path": str(target), "detail": "summary"},
            authorization_confirmed=True, approved=True, evidence_directory=output,
        )
        normalized = fake.call_tool.call_args.args[1]
        self.assertEqual(Path(normalized["input_path"]).resolve(), target.resolve())
        self.assertTrue((output / "rea-evidence.json").is_file())
        self.assertEqual(result["tool"], "analyze_javascript_application")

    def test_rea_javascript_analysis_uses_bounded_direct_cli_and_saves_evidence(self) -> None:
        target = self.workspace / "owned-source"
        target.mkdir()
        output = self.workspace / "data" / "rea-cli-evidence"
        self.adapter.rea = Mock()
        self.adapter.rea_command = ("node.exe", "rea.mjs")
        analysis = {"status": "complete", "application_graph": {"nodes": 2}}
        with patch.object(self.adapter, "_run_cli", return_value=json.dumps(analysis)) as run:
            result = self.adapter.inspect_with_rea(
                "analyze_javascript_application",
                {"input_path": str(target), "format": "directory", "source_paths": ["index.html"],
                 "max_semantic_nodes": 10000, "max_heap_mb": 512, "detail": "summary"},
                authorization_confirmed=True, approved=True, evidence_directory=output,
            )
        command = run.call_args.args[0]
        self.assertIn("analyze-javascript-application", command)
        self.assertIn("--source-path", command)
        self.assertIn("index.html", command)
        self.assertIn("--max-heap-mb", command)
        stored = json.loads((output / "rea-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["execution_mode"], "direct_cli")
        self.assertEqual(stored["result"]["status"], "complete")
        manifest = json.loads((output / "evidence-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], result["evidence_sha256"])
        self.assertEqual(Path(result["evidence_manifest_path"]).resolve(), (output / "evidence-manifest.json").resolve())
        self.assertEqual(result["result"]["application_graph"]["nodes"], 2)
        self.adapter.rea.call_tool.assert_not_called()

    def test_rea_javascript_analysis_rejects_source_path_traversal(self) -> None:
        target = self.workspace / "owned-source"
        target.mkdir()
        self.adapter.rea = Mock()
        self.adapter.rea_command = ("node.exe", "rea.mjs")
        with patch.object(self.adapter, "_run_cli") as run:
            with self.assertRaises(ValueError):
                self.adapter.inspect_with_rea(
                    "analyze_javascript_application",
                    {"input_path": str(target), "source_paths": ["../outside.js"]},
                    authorization_confirmed=True, approved=True,
                    evidence_directory=self.workspace / "data" / "rea-cli-invalid",
                )
            run.assert_not_called()

    def test_rea_rejects_relative_or_missing_input_path(self) -> None:
        self.adapter.rea = Mock()
        for index, value in enumerate(("relative/project", str(self.workspace / "missing-project"))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.adapter.inspect_with_rea(
                    "analyze_javascript_application", {"input_path": value},
                    authorization_confirmed=True, approved=True,
                    evidence_directory=self.workspace / "data" / f"rea-evidence-{index}",
                )
        self.adapter.rea.call_tool.assert_not_called()

    @patch("muse.software_crafting.SoftwareCraftingAdapter._run_cli")
    def test_configure_local_backend_uses_muse_loopback_endpoint(self, run: Mock) -> None:
        cli = self.workspace / "craft-cli.exe"
        cli.write_bytes(b"fixture")
        config = self.workspace / "config" / "muse.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[models]\nprovider = "llama_cpp"\nmodel = "Local-Coder-9B"\nbase_url = "http://127.0.0.1:1235/v1"\n', encoding="utf-8")
        self.adapter.craft_cli = cli
        run.side_effect = ["[]", "", "", "connected"]
        result = self.adapter.configure_local_backend()
        self.assertTrue(result["configured"])
        self.assertEqual(result["base_url"], "http://127.0.0.1:1235/v1")
        add_call = run.call_args_list[1].args[0]
        self.assertIn("http://127.0.0.1:1235/v1", add_call)

    @patch("muse.software_crafting.SoftwareCraftingAdapter._run_cli")
    def test_configure_local_backend_refuses_cloud_endpoints(self, run: Mock) -> None:
        cli = self.workspace / "craft-cli.exe"
        cli.write_bytes(b"fixture")
        config = self.workspace / "config" / "muse.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[model]\nprovider = "openai_compatible"\nmodel = "remote"\nbase_url = "https://api.openai.com/v1"\n', encoding="utf-8")
        self.adapter.craft_cli = cli
        with self.assertRaises(PermissionError):
            self.adapter.configure_local_backend()
        run.assert_not_called()

    def test_static_validator_detects_markdown_disguised_as_html(self) -> None:
        app = self.workspace / "app"
        app.mkdir()
        (app / "self-check.html").write_text("# Self Check\\n\\n- [ ] add a task", encoding="utf-8")
        result = SoftwareCraftingAdapter.validate_static_artifacts(app)
        self.assertFalse(result["passed"])
        self.assertTrue(any("doctype" in issue for issue in result["issues"]))

    def test_static_validator_accepts_html_css_and_javascript_syntax(self) -> None:
        app = self.workspace / "app"
        app.mkdir()
        (app / "index.html").write_text("<!doctype html><html><body><h1>ok</h1></body></html>", encoding="utf-8")
        (app / "styles.css").write_text("body { margin: 0; }", encoding="utf-8")
        (app / "app.js").write_text("const answer = 42;\n", encoding="utf-8")
        result = SoftwareCraftingAdapter.validate_static_artifacts(app)
        self.assertTrue(result["passed"], result["issues"])

if __name__ == "__main__":
    unittest.main()
