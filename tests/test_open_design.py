from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from muse.open_design import (
    APPROVED_WRITE_TOOLS,
    NEVER_ALLOW_TOOLS,
    READ_ONLY_TOOLS,
    OpenDesignAdapter,
)
from muse.task_queue import TaskQueue


class FakeMCPClient:
    def __init__(self, state: dict, *, allowed_tools, **_kwargs) -> None:
        self.state = state
        self.allowed_tools = frozenset(allowed_tools)

    def list_tools(self, *, timeout_seconds=None):
        return list(self.state["catalog"])

    def call_tool(self, name, arguments, *, approved=False, timeout_seconds=None):
        if name not in self.allowed_tools:
            raise PermissionError("fake client static allowlist blocked tool")
        self.state["calls"].append((name, dict(arguments), approved))
        if name == "list_projects":
            projects = self.state.get("projects", [])
            payload = {"projects": projects if self.state.get("list_projects_visible") else []}
        elif name == "get_project":
            project = next(
                (item for item in self.state.get("projects", []) if item.get("id") == arguments.get("project")),
                None,
            )
            payload = dict(project or {})
        elif name == "get_active_context":
            payload = {"active": False, "hint": "No active project"}
        elif name == "create_project":
            project = {"id": arguments["id"], "name": arguments["name"]}
            self.state.setdefault("projects", []).append(project)
            self.state["list_projects_visible"] = True
            payload = {"project": project, "conversationId": "conversation-1"}
        elif name == "list_agents":
            payload = {"agents": [{"id": "opencode", "name": "OpenCode", "version": "1.19.3"}]}
        elif name == "list_skills":
            payload = {"skills": [{"id": "web-research"}]}
        elif name == "list_plugins":
            payload = {"plugins": []}
        elif name == "start_run":
            payload = {"runId": "run-123"}
        elif name == "get_run":
            payload = {"status": "succeeded", "previewUrl": "http://127.0.0.1:7456/preview", "artifacts": []}
        else:
            payload = {"ok": True}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    def call_tools(self, calls, *, approved=False, timeout_seconds=None):
        return [
            self.call_tool(item["name"], item["arguments"], approved=approved, timeout_seconds=timeout_seconds)
            for item in calls
        ]


def make_catalog():
    return [
        {"name": name, "annotations": {"readOnlyHint": True, "destructiveHint": False}}
        for name in sorted(READ_ONLY_TOOLS)
    ] + [
        {"name": name, "annotations": {"readOnlyHint": False, "destructiveHint": False}}
        for name in sorted(APPROVED_WRITE_TOOLS)
    ] + [
        {"name": name, "annotations": {"readOnlyHint": False, "destructiveHint": True}}
        for name in sorted(NEVER_ALLOW_TOOLS)
    ] + [
        {"name": "get_vela_login_status", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ]


class OpenDesignAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.database = self.workspace / "data" / "tasks" / "muse.sqlite3"
        self.data_dir = self.workspace / "open-design-data"
        self.data_dir.mkdir()
        db = sqlite3.connect(self.data_dir / "app.sqlite")
        try:
            db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, skill_id TEXT, design_system_id TEXT, created_at INTEGER, updated_at INTEGER)")
            db.execute("CREATE TABLE workspace_projects (project_id TEXT, resource_state TEXT, cloud_tombstoned_at TEXT)")
            db.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, 1, 2)", ("project-1", "Existing project"))
            db.execute("INSERT INTO workspace_projects (project_id, resource_state, cloud_tombstoned_at) VALUES (?, 'active', NULL)", ("project-1",))
            db.commit()
        finally:
            db.close()
        self.state = {
            "catalog": make_catalog(), "calls": [],
            "projects": [{"id": "project-1", "name": "Existing project"}],
            "list_projects_visible": False,
        }

        def factory(**kwargs):
            return FakeMCPClient(self.state, **kwargs)

        self.adapter = OpenDesignAdapter(
            workspace=self.workspace,
            database=self.database,
            command=("Open Design.exe", "daemon-cli.mjs", "mcp"),
            daemon_url="http://127.0.0.1:7456",
            data_dir=self.data_dir,
            allowed_agents=("opencode",),
            client_factory=factory,
            sleep=lambda _seconds: None,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_doctor_discovers_projects_agents_skills_and_tools_read_only(self) -> None:
        result = self.adapter.doctor()
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["tool_count"], len(self.state["catalog"]))
        self.assertEqual(result["project_count"], 1)
        self.assertEqual(result["projects"][0]["id"], "project-1")
        self.assertEqual(result["project_source"], "local_db_verified")
        self.assertEqual(result["agents"][0]["id"], "opencode")
        self.assertEqual(result["skills_count"], 1)
        self.assertTrue(set(result["read_only_tools"]).issubset(READ_ONLY_TOOLS))
        self.assertTrue(set(result["gated_write_tools"]).issubset(APPROVED_WRITE_TOOLS))
        self.assertTrue(set(result["blocked_destructive_tools"]).issubset(NEVER_ALLOW_TOOLS))
        self.assertEqual(result["unclassified_tools"], ["get_vela_login_status"])
        self.assertTrue(all(call[0] not in APPROVED_WRITE_TOOLS | NEVER_ALLOW_TOOLS for call in self.state["calls"]))

    def test_daemon_url_must_be_loopback_http(self) -> None:
        for url in ("https://127.0.0.1:7456", "http://example.com:7456", "http://10.0.0.2:7456"):
            with self.subTest(url=url), self.assertRaises(PermissionError):
                OpenDesignAdapter(
                    workspace=self.workspace, database=self.database,
                    command=("node", "cli.mjs"), daemon_url=url,
                )

    def test_read_only_annotation_mismatch_fails_closed(self) -> None:
        self.state["catalog"] = [
            {**item, "annotations": {"readOnlyHint": False, "destructiveHint": False}}
            if item["name"] == "list_projects" else item
            for item in self.state["catalog"]
        ]
        with self.assertRaises(PermissionError):
            self.adapter.doctor()

    def test_write_requires_approval_and_static_allowlist(self) -> None:
        args = {"project": "project-1", "prompt": "Create a research microsite",
                "agent": "opencode", "requestId": str(uuid.uuid4())}
        with self.assertRaises(PermissionError):
            self.adapter.call_write("start_run", args, approved=False)
        with self.assertRaises(PermissionError):
            self.adapter.call_write("delete_project", {"project": "project-1", "confirm": True}, approved=True)
        self.assertEqual(self.state["calls"], [])

    def test_write_path_traversal_is_rejected_before_mcp_transport(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.call_write(
                "write_file", {"project": "project-1", "path": "../outside.txt", "content": "x"},
                approved=True, overwrite_approved=True,
            )
        self.assertEqual(self.state["calls"], [])

    def test_write_file_requires_separate_overwrite_approval(self) -> None:
        args = {"project": "project-1", "path": "index.html", "content": "<h1>Draft</h1>"}
        with self.assertRaises(PermissionError):
            self.adapter.call_write("write_file", args, approved=True)
        self.adapter.call_write("write_file", args, approved=True, overwrite_approved=True)
        self.assertTrue(any(call[0] == "write_file" for call in self.state["calls"]))

    def test_enqueue_run_uses_muse_queue_and_waits_for_approval(self) -> None:
        task = self.adapter.enqueue_run(
            project_id="project-1", prompt="Create a research showcase",
            agent="opencode", skill="web-research",
        )
        self.assertEqual(task["status"], "waiting_approval")
        self.assertEqual(task["payload"]["kind"], "open_design_run")
        self.assertEqual(task["payload"]["project_id"], "project-1")
        self.assertEqual(task["recovery_policy"], "manual")
        with TaskQueue(self.database) as queue:
            same = self.adapter.enqueue_run(
                project_id="project-1", prompt="Create a research showcase",
                agent="opencode", skill="web-research",
            )
            self.assertEqual(same["id"], task["id"])
            self.assertEqual(queue.get(task["id"])["status"], "waiting_approval")

    def test_enqueue_run_rejects_agent_without_explicit_allowlist(self) -> None:
        adapter = OpenDesignAdapter(
            workspace=self.workspace, database=self.database,
            command=("node", "cli.mjs"), daemon_url="http://127.0.0.1:7456",
            allowed_agents=(), client_factory=lambda **kwargs: FakeMCPClient(self.state, **kwargs),
        )
        with self.assertRaises(PermissionError):
            adapter.enqueue_run(project_id="project-1", prompt="Build a page", agent="opencode")

    def test_ensure_project_is_approval_gated_and_idempotent(self) -> None:
        with self.assertRaises(PermissionError):
            self.adapter.ensure_project(name="Muse Creative Studio", project_id="muse-creative-studio")
        first = self.adapter.ensure_project(
            name="Muse Creative Studio", project_id="muse-creative-studio", approved=True,
        )
        second = self.adapter.ensure_project(
            name="Muse Creative Studio", project_id="muse-creative-studio", approved=True,
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["project"]["id"], "muse-creative-studio")
        self.assertEqual(
            sum(1 for call in self.state["calls"] if call[0] == "create_project"), 1,
        )

    def test_execute_task_requires_recorded_approval(self) -> None:
        payload = {"kind": "open_design_run", "project_id": "project-1", "agent": "opencode",
                   "prompt": "Build a page"}
        with self.assertRaises(PermissionError):
            self.adapter.execute_task(payload, task_id="a" * 32)
        self.assertEqual(self.state["calls"], [])

    def test_execute_task_accepts_real_task_queue_approval_record(self) -> None:
        task = self.adapter.enqueue_run(
            project_id="project-1", prompt="Create a showcase",
            agent="opencode",
        )
        with TaskQueue(self.database) as queue:
            approved = queue.approve(task["id"], approved_by="owner")
        result = self.adapter.execute_task(approved["payload"], task_id=task["id"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any(call[0] == "start_run" for call in self.state["calls"]))

    def test_execute_task_uses_stable_request_id_and_polls_run(self) -> None:
        task_id = "a" * 32
        payload = {
            "kind": "open_design_run", "project_id": "project-1", "agent": "opencode",
            "prompt": "Create a research showcase", "_approved": True,
            "_approval": {"approved_by": "owner"},
        }
        result = self.adapter.execute_task(payload, task_id=task_id)
        start_call = next(call for call in self.state["calls"] if call[0] == "start_run")
        poll_call = next(call for call in self.state["calls"] if call[0] == "get_run")
        self.assertTrue(start_call[2])
        self.assertEqual(start_call[1]["requestId"], str(uuid.UUID(hex=task_id)))
        self.assertEqual(start_call[1]["agent"], "opencode")
        self.assertEqual(poll_call[1]["runId"], "run-123")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:7456/preview")

    def test_from_installed_uses_packaged_electron_node_mode(self) -> None:
        install = self.workspace / "Open Design"
        cli = install / "resources" / "app" / "prebundled" / "daemon" / "daemon-cli.mjs"
        runtime = install / "Open Design.exe"
        data = self.workspace / "Open Design Data"
        cli.parent.mkdir(parents=True)
        data.mkdir()
        cli.write_text("// fixture", encoding="utf-8")
        runtime.write_bytes(b"fixture")
        config = self.workspace / "muse.toml"
        config.write_text(
            "[open_design]\n"
            f'cli_path = "{str(cli).replace(chr(92), chr(92) * 2)}"\n'
            f'runtime_path = "{str(runtime).replace(chr(92), chr(92) * 2)}"\n'
            f'data_dir = "{str(data).replace(chr(92), chr(92) * 2)}"\n'
            'daemon_url = "http://127.0.0.1:7456"\n'
            'allowed_agents = []\n',
            encoding="utf-8",
        )
        adapter = OpenDesignAdapter.from_installed(
            workspace=self.workspace, database=self.database, config_path=config,
            client_factory=lambda **kwargs: FakeMCPClient(self.state, **kwargs),
        )
        self.assertEqual(adapter.command[0], str(runtime))
        self.assertEqual(adapter.environment["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(adapter.environment["OD_DATA_DIR"], str(data.resolve()))
        self.assertEqual(adapter.allowed_agents, frozenset())


    def test_auto_start_spawns_detached_loopback_daemon(self) -> None:
        install = self.workspace / "Open Design Runtime"
        install.mkdir()
        runtime = install / "Open Design.exe"
        cli = install / "daemon-cli.mjs"
        runtime.write_bytes(b"fixture")
        cli.write_text("// fixture", encoding="utf-8")
        adapter = OpenDesignAdapter(
            workspace=self.workspace, database=self.database,
            command=(str(runtime), str(cli), "mcp"),
            daemon_url="http://127.0.0.1:7456", data_dir=self.workspace,
            auto_start_daemon=True, expected_version="0.27.1",
            client_factory=lambda **kwargs: FakeMCPClient(self.state, **kwargs),
            sleep=lambda _seconds: None,
        )

        class Process:
            pid = 4242
            returncode = None
            def poll(self):
                return None

        with patch("muse.open_design.socket.create_connection", side_effect=OSError("port free")):
            with patch("muse.open_design.subprocess.Popen", return_value=Process()) as popen:
                with patch.object(adapter, "_read_daemon_health", side_effect=[ConnectionError("not running"), {"version": "0.27.1"}]):
                    adapter._ensure_daemon()

        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertIn("--no-open", command)
        self.assertIn("--port", command)
        self.assertEqual(environment["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(environment["OD_DATA_DIR"], str(self.workspace.resolve()))
        self.assertEqual(adapter.daemon_status, "started")
        self.assertEqual(adapter.daemon_pid, 4242)
        self.assertTrue(adapter.daemon_log_path.is_file())

    def test_auto_start_refuses_an_unrecognized_listener(self) -> None:
        install = self.workspace / "Open Design Runtime"
        install.mkdir()
        runtime = install / "Open Design.exe"
        cli = install / "daemon-cli.mjs"
        runtime.write_bytes(b"fixture")
        cli.write_text("// fixture", encoding="utf-8")
        adapter = OpenDesignAdapter(
            workspace=self.workspace, database=self.database,
            command=(str(runtime), str(cli), "mcp"),
            daemon_url="http://127.0.0.1:7456", data_dir=self.workspace,
            auto_start_daemon=True, client_factory=lambda **kwargs: FakeMCPClient(self.state, **kwargs),
        )
        with patch.object(adapter, "_read_daemon_health", side_effect=ConnectionError("not Open Design")):
            with patch("muse.open_design.socket.create_connection") as connect:
                with patch("muse.open_design.subprocess.Popen") as popen:
                    connect.return_value.__enter__.return_value = object()
                    with self.assertRaises(PermissionError):
                        adapter._ensure_daemon()
                    popen.assert_not_called()

if __name__ == "__main__":
    unittest.main()
