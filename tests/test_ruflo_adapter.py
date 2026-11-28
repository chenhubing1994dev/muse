import tempfile
import unittest
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from muse.commander_gate import BypassCommanderError, ruflo_ticket_scope
from muse.ruflo_adapter import RufloMCPAdapter, RufloError, resolve_lmstudio_ruflo_config
from muse.task_queue import TaskQueue


class RufloAdapterTests(unittest.TestCase):
    def make_adapter(self, enabled=()):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        return RufloMCPAdapter(("node", "ruflo.js"), workspace=self.tmp.name, enabled_tools=enabled)

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    @contextmanager
    def ticket(self):
        """Contract since T6: executing Ruflo tools run only inside a running, leased Commander ticket."""
        db = Path(self.tmp.name) / "tasks.sqlite3"
        with TaskQueue(db) as queue:
            task = queue.enqueue("ruflo test", {"kind": "test"})
            queue.claim_next(worker_id="w1", task_id=task["id"], lease_seconds=60)
        with ruflo_ticket_scope(db, task["id"], "w1", self.tmp.name):
            yield

    def test_executing_tool_without_commander_ticket_is_refused(self):
        adapter = self.make_adapter(("agent_execute",))
        with patch("muse.ruflo_adapter.subprocess.run") as run:
            with self.assertRaises(BypassCommanderError):
                adapter.call_tool("agent_execute", {"agentId": "a", "prompt": "p"}, approved=True)
            run.assert_not_called()

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_read_only_status_calls_pinned_cli_without_shell(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'INFO\nResult:\n{"status":"healthy"}', "")
        adapter = self.make_adapter()
        result = adapter.health()
        self.assertEqual(result.result, {"status": "healthy"})
        args, kwargs = run.call_args
        self.assertEqual(args[0][:4], ["node", "ruflo.js", "mcp", "exec"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], str(Path(self.tmp.name).resolve()))

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_agent_execute_preserves_provider_usage_in_mcp_result(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0,
            'INFO\nResult:\n{"success":true,"model":"local-model","messageId":"chatcmpl-123","usage":{"inputTokens":12,"outputTokens":4,"totalTokens":16}}',
            "",
        )
        adapter = self.make_adapter(("agent_execute",))
        with self.ticket():
            result = adapter.call_tool(
                "agent_execute", {"agentId": "agent-test", "prompt": "short"}, approved=True,
            )
        self.assertEqual(result.result["usage"], {
            "inputTokens": 12, "outputTokens": 4, "totalTokens": 16,
        })
        self.assertEqual(result.result["messageId"], "chatcmpl-123")

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_agent_execute_gets_bounded_local_generation_timeout(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'Result:\n{"output":"ok"}', "")
        adapter = self.make_adapter(("agent_execute",))
        with self.ticket():
            adapter.call_tool("agent_execute", {"agentId": "agent-test", "prompt": "short"}, approved=True)
        self.assertEqual(run.call_args.kwargs["timeout"], 180.0)

    @patch("muse.ruflo_adapter.subprocess.run")
    @patch("muse.ruflo_adapter.resolve_lmstudio_ruflo_config", create=True, return_value=("http://127.0.0.1:1235", "llama-model"))
    def test_llama_cpp_agent_execute_extends_ruflo_mcp_timeout(self, resolve, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'Result:\\n{"output":"ok"}', "")
        adapter = self.make_adapter(("agent_execute",))
        with patch.dict("os.environ", {
            "MUSE_RUFLO_PROVIDER": "llama_cpp",
            "MUSE_LLAMA_CPP_URL": "http://127.0.0.1:1235/v1",
        }, clear=True):
            adapter._run(("mcp", "exec", "-t", "agent_execute", "-p", "{}"))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["AUTOPILOT_STEP_TIMEOUT"], "210000")
        self.assertEqual(child_env["MUSE_RUFLO_MCP_TIMEOUT_MS"], "210000")
        self.assertEqual(child_env["MCP_TOOL_TIMEOUT_MS"], "210000")
        self.assertEqual(run.call_args.kwargs["timeout"], 240.0)
        resolve.assert_called_once()

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_control_calls_keep_configured_timeout(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'Result:\n{"status":"healthy"}', "")
        adapter = self.make_adapter()
        adapter.health()
        self.assertEqual(run.call_args.kwargs["timeout"], 60.0)

    def test_mutations_require_allowlist_and_explicit_approval(self):
        adapter = self.make_adapter()
        with self.assertRaises(PermissionError):
            adapter.call_tool("swarm_init", {"topology": "hierarchical"}, approved=True)
        adapter = self.make_adapter(("swarm_init",))
        with self.assertRaises(PermissionError):
            adapter.call_tool("swarm_init", {"topology": "hierarchical"})
        with patch("muse.ruflo_adapter.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, 'Result:\n{"ok":true}', "")
            with self.ticket():
                result = adapter.call_tool("swarm_init", {"topology": "hierarchical"}, approved=True)
        self.assertEqual(result.result, {"ok": True})

    def test_rejects_high_risk_invalid_params_and_oversized_payload(self):
        adapter = self.make_adapter(("system_reset", "terminal_execute"))
        with self.assertRaises(PermissionError):
            adapter.call_tool("system_reset", {}, approved=True)
        with self.assertRaises(PermissionError):
            adapter.call_tool("terminal_execute", {"command": "whoami"}, approved=True)
        with self.assertRaises(ValueError):
            adapter.call_tool("system_status", [])
        with self.assertRaises(ValueError):
            adapter.call_tool("system_status", {"x": "a" * 40000})

    def test_unknown_mutating_tool_requires_explicit_allowlist_and_approval(self):
        adapter = self.make_adapter(("coordination_topology",))
        with self.assertRaises(PermissionError):
            adapter.call_tool("coordination_topology", {"topology": "mesh"})
        with patch("muse.ruflo_adapter.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, 'Result:\n{"ok":true}', "")
            with self.ticket():
                result = adapter.call_tool("coordination_topology", {"topology": "mesh"}, approved=True)
        self.assertEqual(result.result, {"ok": True})

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_process_failure_is_reported(self, run):
        run.side_effect = OSError("not found")
        with self.assertRaises(RufloError):
            self.make_adapter().health()

    def test_from_installed_resolves_explicit_pinned_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "ruflo.js"
            cli.write_text("// pinned CLI", encoding="utf-8")
            adapter = RufloMCPAdapter.from_installed(
                workspace=directory, node_path="node.exe", cli_path=cli,
            )
            self.assertEqual(adapter.command_prefix, ("node.exe", str(cli)))

    def test_invalid_workspace_and_command_are_rejected(self):
        with self.assertRaises(ValueError):
            RufloMCPAdapter((), workspace=".")
        with self.assertRaises(ValueError):
            RufloMCPAdapter(("node",), workspace="missing-path-never-create")


    @patch("muse.ruflo_adapter.subprocess.run")
    def test_chatanywhere_key_is_mapped_to_ollama_child_environment(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        adapter = self.make_adapter()
        with patch.dict("os.environ", {
            "CHATANYWHERE_API_KEY": "chatanywhere-test-secret",
            "CHATANYWHERE_MODEL": "gpt-4o-mini",
        }, clear=True):
            adapter._run(("--version",))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["OLLAMA_API_KEY"], "chatanywhere-test-secret")
        self.assertEqual(child_env["OLLAMA_BASE_URL"], "https://api.chatanywhere.org")
        self.assertEqual(child_env["OLLAMA_DEFAULT_MODEL"], "gpt-4o-mini")
        self.assertNotIn("CHATANYWHERE_API_KEY", child_env)
        self.assertNotIn("CHATANYWHERE_MODEL", child_env)

    @patch("muse.ruflo_adapter.subprocess.run")
    def test_chatanywhere_secret_is_not_forwarded_when_anthropic_is_selected(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        adapter = self.make_adapter()
        with patch.dict("os.environ", {
            "CHATANYWHERE_API_KEY": "chatanywhere-test-secret",
            "ANTHROPIC_API_KEY": "anthropic-test-secret",
        }, clear=True):
            adapter._run(("--version",))
        child_env = run.call_args.kwargs["env"]
        self.assertNotIn("CHATANYWHERE_API_KEY", child_env)
        self.assertNotIn("OLLAMA_API_KEY", child_env)
        self.assertEqual(child_env["ANTHROPIC_API_KEY"], "anthropic-test-secret")

    @patch("muse.ruflo_adapter.subprocess.run")
    @patch("muse.ruflo_adapter.resolve_lmstudio_ruflo_config", create=True, return_value=("http://127.0.0.1:1234", "gemma-local"))
    def test_lmstudio_provider_maps_to_local_ollama_compat_without_cloud_secrets(self, resolve, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        adapter = self.make_adapter()
        with patch.dict("os.environ", {
            "MUSE_RUFLO_PROVIDER": "lmstudio",
            "MUSE_LMSTUDIO_URL": "http://127.0.0.1:1234/v1",
            "ANTHROPIC_API_KEY": "must-not-forward",
            "OPENROUTER_API_KEY": "must-not-forward-either",
            "CHATANYWHERE_API_KEY": "must-not-forward-either",
        }, clear=True):
            adapter._run(("mcp", "exec", "-t", "agent_execute", "-p", "{}"))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["RUFLO_PROVIDER"], "ollama")
        self.assertEqual(child_env["OLLAMA_BASE_URL"], "http://127.0.0.1:1234")
        self.assertEqual(child_env["OLLAMA_DEFAULT_MODEL"], "gemma-local")
        self.assertEqual(child_env["CLAUDE_FLOW_ROUTER_FALLBACK_MAX_RETRIES"], "0")
        self.assertNotIn("OLLAMA_API_KEY", child_env)
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)
        self.assertNotIn("OPENROUTER_API_KEY", child_env)
        self.assertNotIn("CHATANYWHERE_API_KEY", child_env)
        resolve.assert_called_once()

    @patch("muse.ruflo_adapter.subprocess.run")
    @patch("muse.ruflo_adapter.resolve_lmstudio_ruflo_config", create=True, return_value=("http://127.0.0.1:1235", "llama-model"))
    def test_llama_cpp_provider_maps_to_local_ollama_compat(self, resolve, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        adapter = self.make_adapter()
        with patch.dict("os.environ", {
            "MUSE_RUFLO_PROVIDER": "llama_cpp",
            "MUSE_LLAMA_CPP_URL": "http://127.0.0.1:1235/v1",
            "ANTHROPIC_API_KEY": "must-not-forward",
            "OPENROUTER_API_KEY": "must-not-forward-either",
        }, clear=True):
            adapter._run(("mcp", "exec", "-t", "agent_execute", "-p", "{}"))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["RUFLO_PROVIDER"], "ollama")
        self.assertEqual(child_env["OLLAMA_BASE_URL"], "http://127.0.0.1:1235")
        self.assertEqual(child_env["OLLAMA_DEFAULT_MODEL"], "llama-model")
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)
        self.assertNotIn("OPENROUTER_API_KEY", child_env)
        resolve.assert_called_once()


    @patch("muse.ruflo_adapter.subprocess.run")
    @patch("muse.ruflo_adapter.resolve_lmstudio_ruflo_config")
    def test_read_only_ruflo_status_does_not_depend_on_local_model_availability(self, resolve, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        adapter = self.make_adapter()
        with patch.dict("os.environ", {"MUSE_RUFLO_PROVIDER": "lmstudio"}, clear=True):
            adapter._run(("mcp", "exec", "-t", "system_status", "-p", "{}"))
        resolve.assert_not_called()
        self.assertNotIn("OLLAMA_BASE_URL", run.call_args.kwargs["env"])

    @patch("muse.ruflo_adapter.LMStudioClient.list_models", return_value=[{"id": "gemma-local"}, {"id": "qwen-local"}])
    def test_resolves_lmstudio_model_from_loopback_endpoint(self, list_models):
        base_url, model = resolve_lmstudio_ruflo_config({
            "MUSE_LMSTUDIO_URL": "http://127.0.0.1:1234/v1",
            "MUSE_LMSTUDIO_MODEL": "qwen-local",
        })
        self.assertEqual(base_url, "http://127.0.0.1:1234")
        self.assertEqual(model, "qwen-local")
        list_models.assert_called_once()

    @patch("muse.ruflo_adapter.LMStudioClient.list_models")
    def test_lmstudio_config_rejects_remote_endpoint_before_network(self, list_models):
        with self.assertRaisesRegex(ValueError, "loopback"):
            resolve_lmstudio_ruflo_config({"MUSE_LMSTUDIO_URL": "https://example.com/v1"})
        list_models.assert_not_called()

    def test_project_local_config_selects_lmstudio_for_agent_execute_without_environment_variable(self):
        adapter = self.make_adapter()
        config_dir = Path(self.tmp.name) / "config"
        config_dir.mkdir()
        (config_dir / "muse.toml").write_text(
            '[models]\nprovider = "lmstudio"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "gemma-local"\n',
            encoding="utf-8",
        )
        with patch("muse.ruflo_adapter.subprocess.run") as run, \
             patch("muse.ruflo_adapter.resolve_lmstudio_ruflo_config", return_value=("http://127.0.0.1:1234", "gemma-local")):
            run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
            with patch.dict("os.environ", {}, clear=True):
                adapter._run(("mcp", "exec", "-t", "agent_execute", "-p", "{}"))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["RUFLO_PROVIDER"], "ollama")
        self.assertEqual(child_env["OLLAMA_DEFAULT_MODEL"], "gemma-local")
        self.assertEqual(child_env["CLAUDE_FLOW_ROUTER_FALLBACK_MAX_RETRIES"], "0")
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)

    def test_project_model_config_environment_overrides_provider(self):
        self.make_adapter()
        config_dir = Path(self.tmp.name) / "config"
        config_dir.mkdir()
        (config_dir / "muse.toml").write_text('[models]\nprovider = "lmstudio"\n', encoding="utf-8")
        from muse.ruflo_adapter import load_muse_model_config
        settings = load_muse_model_config(self.tmp.name, {"MUSE_RUFLO_PROVIDER": "anthropic"})
        self.assertEqual(settings["provider"], "anthropic")

    def test_llama_cpp_model_config_uses_1235_and_auto_detects_model(self):
        self.make_adapter()
        from muse.ruflo_adapter import load_muse_model_config
        config_dir = Path(self.tmp.name) / "config"
        config_dir.mkdir()
        (config_dir / "muse.toml").write_text(
            '[models]\nprovider = "llama_cpp"\nbase_url = "http://127.0.0.1:1235/v1"\nmodel = ""\n',
            encoding="utf-8",
        )
        settings = load_muse_model_config(self.tmp.name, {})
        self.assertEqual(settings, {"provider": "llama_cpp", "base_url": "http://127.0.0.1:1235/v1", "model": ""})

    def test_llama_cpp_config_ignores_legacy_lmstudio_environment_overrides(self):
        self.make_adapter()
        from muse.ruflo_adapter import load_muse_model_config
        config_dir = Path(self.tmp.name) / "config"
        config_dir.mkdir()
        (config_dir / "muse.toml").write_text(
            '[models]\nprovider = "llama_cpp"\nbase_url = "http://127.0.0.1:1235/v1"\nmodel = "DeepSeek-V4-Pro-Qwen3.5-9B"\n',
            encoding="utf-8",
        )
        settings = load_muse_model_config(self.tmp.name, {
            "MUSE_LMSTUDIO_URL": "http://127.0.0.1:1234/v1",
            "MUSE_LMSTUDIO_MODEL": "old-lmstudio-model",
        })
        self.assertEqual(settings["base_url"], "http://127.0.0.1:1235/v1")
        self.assertEqual(settings["model"], "DeepSeek-V4-Pro-Qwen3.5-9B")

if __name__ == "__main__":
    unittest.main()