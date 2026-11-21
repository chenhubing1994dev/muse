from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.open_design import OpenDesignAdapter
from muse.software_crafting import MCPStdioClient


class OpenDesignMcpRuntimeTests(unittest.TestCase):
    def _write_fake_server(self, root: Path) -> Path:
        script = root / "resources" / "app" / "prebundled" / "daemon" / "daemon-cli.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            """import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture", "version": "0.27.1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "safe_read", "annotations": {"readOnlyHint": True, "destructiveHint": False}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "persistent-tool-ok"}]}
    elif method == "resources/list":
        result = {"resources": [{"uri": "od://design-systems/muse/DESIGN.md", "name": "Muse design system", "mimeType": "text/markdown"}]}
    elif method == "resources/read":
        result = {"contents": [{"uri": request["params"]["uri"], "mimeType": "text/markdown", "text": "# Muse design system"}]}
    else:
        continue
    if "id" in request:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""",
            encoding="utf-8",
        )
        return script

    def test_open_design_tool_catalog_uses_sequential_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._write_fake_server(root)
            client = MCPStdioClient(
                command=(sys.executable, str(script)), workspace=root,
                allowed_tools={"safe_read"},
            )
            with patch.object(client, "_exchange_sequential", wraps=client._exchange_sequential) as sequential:
                tools = client.list_tools(timeout_seconds=10)
        self.assertEqual(tools[0]["name"], "safe_read")
        sequential.assert_called_once_with([], timeout_seconds=10.0)

    def test_single_open_design_tool_call_uses_persistent_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._write_fake_server(root)
            client = MCPStdioClient(
                command=(sys.executable, str(script)), workspace=root,
                allowed_tools={"safe_read"},
            )
            result = client.call_tool("safe_read", {}, timeout_seconds=10)
        self.assertEqual(result["content"][0]["text"], "persistent-tool-ok")

    def test_open_design_resources_list_and_read_use_persistent_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = self._write_fake_server(root)
            client = MCPStdioClient(
                command=(sys.executable, str(script)), workspace=root,
                allowed_tools={"safe_read"},
            )
            resources = client.list_resources(timeout_seconds=10)
            result = client.read_resource("od://design-systems/muse/DESIGN.md", timeout_seconds=10)
        self.assertEqual(resources[0]["uri"], "od://design-systems/muse/DESIGN.md")
        self.assertEqual(result["contents"][0]["text"], "# Muse design system")

    def test_open_design_adapter_disables_remote_telemetry_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = OpenDesignAdapter(
                workspace=root,
                database=root / "data" / "tasks.sqlite3",
                command=(sys.executable, str(self._write_fake_server(root)), "mcp"),
                daemon_url="http://127.0.0.1:7456",
                allowed_agents=(),
            )
        self.assertEqual(adapter.environment["OPEN_DESIGN_VELA_TELEMETRY"], "off")
        self.assertEqual(adapter.environment["OD_NEXT_TASK_OBSERVABILITY_MODE"], "off")
        self.assertEqual(adapter.allowed_agents, frozenset())


if __name__ == "__main__":
    unittest.main()
