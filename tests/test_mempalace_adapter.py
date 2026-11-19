from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from muse.mempalace_adapter import (
    MEM_PALACE_READ_TOOLS,
    MEM_PALACE_WRITE_TOOLS,
    MemPalaceAdapter,
    MemPalaceStdioTransport,
    load_mempalace_adapter,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, bool]] = []

    def list_tools(self) -> dict:
        return {"tools": [{"name": name} for name in sorted(MEM_PALACE_READ_TOOLS | MEM_PALACE_WRITE_TOOLS)]}

    def call_tool(self, name: str, arguments: dict, *, approved: bool = False) -> dict:
        self.calls.append((name, arguments, approved))
        return {"structuredContent": {"tool": name, "arguments": arguments}}


class MemPalaceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTransport()
        self.adapter = MemPalaceAdapter(self.transport)

    def test_search_uses_scoped_semantic_recall(self) -> None:
        result = self.adapter.search("Muse 的长期记忆架构", limit=7, wing="Muse", room="memory")
        self.assertEqual(result["structuredContent"]["tool"], "mempalace_search")
        self.assertEqual(self.transport.calls[-1], (
            "mempalace_search",
            {"query": "Muse 的长期记忆架构", "limit": 7, "wing": "Muse", "room": "memory"},
            False,
        ))

    def test_search_rejects_invalid_bounds(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.search("x", limit=0)
        with self.assertRaises(ValueError):
            self.adapter.search("x", max_distance=2.1)
        self.assertEqual(self.transport.calls, [])

    def test_temporal_knowledge_graph_queries_are_read_only(self) -> None:
        self.adapter.kg_query("Muse", as_of="2026-11-19", direction="outgoing")
        self.adapter.kg_timeline("Muse")
        self.assertEqual([call[0] for call in self.transport.calls], [
            "mempalace_kg_query", "mempalace_kg_timeline",
        ])
        self.assertTrue(all(call[2] is False for call in self.transport.calls))

    def test_checkpoint_requires_explicit_write_approval(self) -> None:
        items = [{"wing": "Muse", "room": "engineering", "content": "A tested engineering lesson.", "track": "agent"}]
        with self.assertRaises(PermissionError):
            self.adapter.checkpoint(items)
        self.assertEqual(self.transport.calls, [])

    def test_checkpoint_requires_separate_opt_in_for_user_track(self) -> None:
        items = [{"wing": "Muse", "room": "preferences", "content": "A user preference.", "track": "user"}]
        with self.assertRaises(PermissionError):
            self.adapter.checkpoint(items, approved=True)
        with self.assertRaises(PermissionError):
            self.adapter.checkpoint(items, approved=True, user_data_authorized=True)
        self.assertEqual(self.transport.calls, [])

    def test_checkpoint_writes_only_curated_items_when_authorized(self) -> None:
        items = [{"wing": "Muse", "room": "engineering", "content": "A tested engineering lesson.", "track": "agent"}]
        result = self.adapter.checkpoint(items, approved=True)
        self.assertEqual(result["structuredContent"]["tool"], "mempalace_checkpoint")
        self.assertEqual(self.transport.calls[-1], (
            "mempalace_checkpoint",
            {
                "items": [{"wing": "Muse", "room": "engineering", "content": "A tested engineering lesson."}],
                "dedup_threshold": 0.9,
                "added_by": "muse-agent-track",
            },
            True,
        ))

    def test_checkpoint_rejects_unknown_fields_and_oversized_payloads(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.checkpoint([{"wing": "Muse", "room": "x", "content": "x", "raw_transcript": "secret"}], approved=True)
        with self.assertRaises(ValueError):
            self.adapter.checkpoint([{"wing": "Muse", "room": "x", "content": "x" * 70000}], approved=True)
        self.assertEqual(self.transport.calls, [])

    def test_checkpoint_rejects_credential_like_content(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.checkpoint([{"wing": "Muse", "room": "secrets", "content": "api_key=abc123"}], approved=True)
        self.assertEqual(self.transport.calls, [])

    def test_checkpoint_rejects_non_string_track(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.checkpoint([{"wing": "Muse", "room": "x", "content": "lesson", "track": []}], approved=True)
        self.assertEqual(self.transport.calls, [])

    def test_kg_query_rejects_non_string_direction(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.kg_query("Muse", direction=[])
        self.assertEqual(self.transport.calls, [])

    def test_transport_blocks_tools_outside_explicit_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "muse.software_crafting.MCPStdioClient"
        ) as client_type:
            client = client_type.return_value
            transport = MemPalaceStdioTransport(("mempalace-mcp",), directory)
            with self.assertRaises(PermissionError):
                transport.call_tool("mempalace_delete_drawer", {"drawer_id": "x"})
            with self.assertRaises(PermissionError):
                transport.call_tool("mempalace_checkpoint", {"items": []})
            transport.call_tool("mempalace_checkpoint", {"items": []}, approved=True)
            self.assertEqual(client_type.call_args.kwargs["allowed_tools"],
                             set(MEM_PALACE_READ_TOOLS | MEM_PALACE_WRITE_TOOLS))
            client.call_tool.assert_called_once_with(
                "mempalace_checkpoint", {"items": []}, approved=True, timeout_seconds=90.0,
            )

    def test_stdio_transport_uses_fixed_argv_and_existing_bounded_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "muse.software_crafting.MCPStdioClient"
        ) as client_type:
            transport = MemPalaceStdioTransport(
                ("C:/Tools/mempalace-mcp.exe", "--stdio"), directory, timeout_seconds=45,
            )
            transport.list_tools()
            kwargs = client_type.call_args.kwargs
            self.assertEqual(kwargs["command"], ("C:/Tools/mempalace-mcp.exe", "--stdio"))
            self.assertEqual(kwargs["workspace"], directory)
            self.assertEqual(kwargs["timeout_seconds"], 45)
            client_type.return_value.list_tools.assert_called_once_with(timeout_seconds=45)

    def test_stdio_transport_passes_only_approved_environment_overrides(self) -> None:
        environment = {
            "HF_HOME": "D:/MuseData/MemPalace/cache",
            "MEMPALACE_PALACE_PATH": "D:/MuseData/MemPalace/palace",
            "MEMPALACE_EMBEDDING_MODEL": "embeddinggemma",
            "MEMPALACE_EMBEDDING_DEVICE": "cpu",
            "USERPROFILE": "D:/MuseData/MemPalace/profile",
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "muse.software_crafting.MCPStdioClient"
        ) as client_type:
            MemPalaceStdioTransport(("mempalace-mcp",), directory, environment=environment)
            self.assertEqual(client_type.call_args.kwargs["environment"], environment)

    def test_stdio_transport_rejects_unapproved_environment_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "muse.software_crafting.MCPStdioClient"
        ) as client_type:
            with self.assertRaises(ValueError):
                MemPalaceStdioTransport(
                    ("mempalace-mcp",), directory,
                    environment={"OPENAI_API_KEY": "must-not-pass"},
                )
            client_type.assert_not_called()


    def test_config_loader_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "config"
            config_dir.mkdir()
            (config_dir / "muse.toml").write_text("[mempalace]\nenabled = false\n", encoding="utf-8")
            self.assertIsNone(load_mempalace_adapter(directory, environ={}))

    def test_config_loader_builds_fixed_local_offline_stdio_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            python = root / "venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_text("placeholder", encoding="utf-8")
            workspace = root / "workspace"
            palace = root / "palace"
            cache = root / "cache"
            profile = root / "profile"
            for path in (workspace, palace, cache, profile):
                path.mkdir()

            def quote(value):
                return '"' + str(value).replace("\\", "\\\\") + '"'
            config = (
                "[mempalace]\n"
                "enabled = true\n"
                f"python_path = {quote(python)}\n"
                f"mcp_workspace = {quote(workspace)}\n"
                f"palace_path = {quote(palace)}\n"
                f"cache_dir = {quote(cache)}\n"
                f"profile_dir = {quote(profile)}\n"
                'embedding_model = "embeddinggemma"\n'
                'embedding_device = "cpu"\n'
                "timeout_seconds = 120\n"
            )
            (config_dir / "muse.toml").write_text(config, encoding="utf-8")
            with patch("muse.mempalace_adapter.MemPalaceAdapter.from_stdio", return_value=object()) as factory:
                result = load_mempalace_adapter(root, environ={})
            self.assertIsNotNone(result)
            kwargs = factory.call_args.kwargs
            self.assertEqual(kwargs["command"], (
                str(python.resolve()), "-m", "mempalace.mcp_server", "--palace", str(palace.resolve()),
            ))
            self.assertEqual(kwargs["workspace"], workspace.resolve())
            self.assertEqual(kwargs["environment"]["HF_HOME"], str(cache.resolve()))
            self.assertEqual(kwargs["environment"]["HF_HUB_OFFLINE"], "1")
            self.assertEqual(kwargs["environment"]["TRANSFORMERS_OFFLINE"], "1")
            self.assertNotIn("OPENAI_API_KEY", kwargs["environment"])

if __name__ == "__main__":
    unittest.main()
