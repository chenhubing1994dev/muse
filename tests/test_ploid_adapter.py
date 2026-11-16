from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from muse.ploid_adapter import PloidMCPAdapter, PloidAdapterError, PloidStdioTransport
from muse.research_knowledge import ResearchKnowledgeStore


class FakePloidTransport:
    def __init__(self, tools=None):
        self.tools = tools or [
            {"name": name, "inputSchema": {"type": "object", "properties": {}, "required": []}}
            for name in (
                "search_people", "research_person", "find_people",
                "find_decision_makers", "get_research_status", "cancel_research",
            )
        ]
        self.calls = []
        self.approvals = []
        self.next_result = {"status": "queued", "run_id": "person-run-123", "retry_after_seconds": 10}

    def list_tools(self):
        return {"tools": self.tools}

    def call_tool(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments))
        self.approvals.append(approved)
        return self.next_result


class PloidAdapterTests(unittest.TestCase):
    def test_name_search_is_paid_and_returns_candidates_without_auto_selecting(self):
        transport = FakePloidTransport()
        transport.next_result = {"candidates": [{"person_id": "p-1", "name": "Ada Example"}]}
        with tempfile.TemporaryDirectory() as directory:
            adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3")
            with self.assertRaises(PloidAdapterError):
                adapter.search_people("Ada Example", budget_authorized=False, max_acu=1)
            result = adapter.search_people("Ada Example", budget_authorized=True, max_acu=1)
            adapter.close()
        self.assertEqual(result["candidates"][0]["person_id"], "p-1")
        self.assertEqual(transport.calls, [("search_people", {"name": "Ada Example", "limit": 5})])
        self.assertEqual(transport.approvals, [True])

    def test_person_research_requires_selected_identity_and_explicit_25_acu_budget(self):
        transport = FakePloidTransport()
        with tempfile.TemporaryDirectory() as directory:
            adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3")
            with self.assertRaises(PloidAdapterError):
                adapter.research_person(person_id="p-1", budget_authorized=True, max_acu=25)
            with self.assertRaises(PloidAdapterError):
                adapter.research_person(person_id="p-1", selected_by_user=True, budget_authorized=True, max_acu=24)
            result = adapter.research_person(
                person_id="p-1", selected_by_user=True,
                budget_authorized=True, max_acu=25,
            )
            self.assertEqual(result["run_id"], "person-run-123")
            self.assertEqual(adapter.get_run("person-run-123")["estimated_acu"], 25)
            self.assertEqual(transport.approvals, [True])
            adapter.close()

    def test_status_polling_is_durable_and_throttled_to_five_seconds(self):
        transport = FakePloidTransport()
        clock = [100.0]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "ploid.sqlite3"
            adapter = PloidMCPAdapter(transport, db, clock=lambda: clock[0])
            adapter.research_person(
                linkedin_url="https://www.linkedin.com/in/ada-example",
                budget_authorized=True, max_acu=25,
            )
            adapter.get_research_status("person-run-123")
            with self.assertRaises(PloidAdapterError):
                adapter.get_research_status("person-run-123")
            clock[0] += 5
            transport.next_result = {"status": "running", "run_id": "person-run-123"}
            result = adapter.get_research_status("person-run-123")
            self.assertEqual(result["status"], "running")
            self.assertEqual(transport.approvals, [True, False, False])
            adapter.close()
            reopened = PloidMCPAdapter(transport, db, clock=lambda: clock[0] + 1)
            with self.assertRaises(PloidAdapterError):
                reopened.get_research_status("person-run-123")
            reopened.close()

    def test_cancellation_requires_an_explicit_user_stop_request(self):
        transport = FakePloidTransport()
        with tempfile.TemporaryDirectory() as directory:
            adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3")
            adapter.research_person(
                linkedin_url="https://www.linkedin.com/in/ada-example",
                budget_authorized=True, max_acu=25,
            )
            with self.assertRaises(PloidAdapterError):
                adapter.cancel_research("person-run-123")
            adapter.cancel_research("person-run-123", user_requested=True)
            adapter.close()
        self.assertEqual(transport.calls[-1][0], "cancel_research")
        self.assertEqual(transport.approvals, [True, True])

    def test_completed_person_research_ingests_provenance_without_contact_enrichment(self):
        transport = FakePloidTransport()
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3", knowledge_store=knowledge)
                result = adapter.ingest_person_result({
                    "status": "completed", "run_id": "person-run-123", "found": True,
                    "person": {
                        "person_id": "p-1", "name": "Ada Example", "title": "Research Lead",
                        "company": "Example Labs", "location": "Singapore",
                        "email": "must-not-be-stored@example.com", "phone": "+6500000000",
                    },
                    "source_links": [
                        {"url": "https://example.com/profile", "title": "Professional profile"},
                        "https://example.org/interview",
                    ],
                    "provenance": {"title": {"source_url": "https://example.com/profile", "confidence": "high"}},
                    "limitations": ["Current role may have changed"],
                })
                node = knowledge.get_node(result["knowledge_node_id"])
                self.assertEqual(node["node_type"], "idea")
                self.assertEqual(node["status"], "proposed")
                self.assertEqual(node["metadata"]["epistemic_status"], "third_party_research_unverified_by_muse")
                self.assertNotIn("email", str(node["metadata"]))
                self.assertNotIn("phone", str(node["metadata"]))
                self.assertEqual(len(result["source_node_ids"]), 2)
                edges = knowledge.list_edges(node_id=node["id"])
                self.assertEqual(len(edges), 2)
                self.assertTrue(all(edge["edge_type"] == "relates" for edge in edges))
                adapter.close()

    def test_invalid_source_links_do_not_create_partial_research_nodes(self):
        transport = FakePloidTransport()
        with tempfile.TemporaryDirectory() as directory:
            with ResearchKnowledgeStore(Path(directory) / "knowledge.sqlite3") as knowledge:
                adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3", knowledge_store=knowledge)
                with self.assertRaises(ValueError):
                    adapter.ingest_person_result({
                        "status": "completed", "run_id": "person-run-123", "found": True,
                        "person": {"person_id": "p-2", "name": "Grace Example"},
                        "source_links": ["http://insecure.example/profile"],
                    })
                self.assertEqual(knowledge.stats()["nodes"], 0)
                adapter.close()

    def test_local_stdio_transport_reuses_muses_allowlisted_mcp_client(self):
        tools = [{"name": "research_person", "inputSchema": {"type": "object", "properties": {"linkedin_url": {"type": "string"}}}}]
        with tempfile.TemporaryDirectory() as directory:
            with patch("muse.software_crafting.MCPStdioClient") as client_type:
                client = client_type.return_value
                client.list_tools.return_value = tools
                client.call_tool.return_value = {"structuredContent": {"status": "queued"}}
                transport = PloidStdioTransport(
                    ("npx", "-y", "@ploid/mcp"), directory, timeout_seconds=45,
                )
                self.assertEqual(transport.list_tools(), {"tools": tools})
                transport.call_tool(
                    "research_person", {"linkedin_url": "https://www.linkedin.com/in/ada-example"},
                    approved=True,
                )
                self.assertEqual(client_type.call_args.kwargs["command"], ("npx", "-y", "@ploid/mcp"))
                self.assertEqual(client_type.call_args.kwargs["timeout_seconds"], 45)
                self.assertIn("research_person", client_type.call_args.kwargs["allowed_tools"])
                client.call_tool.assert_called_once_with(
                    "research_person", {"linkedin_url": "https://www.linkedin.com/in/ada-example"},
                    approved=True, timeout_seconds=45,
                )

    def test_missing_or_stale_mcp_tool_fails_closed(self):
        transport = FakePloidTransport(tools=[{"name": "research_person", "inputSchema": {"type": "object", "properties": {"person_id": {"type": "string"}}}}])
        with tempfile.TemporaryDirectory() as directory:
            adapter = PloidMCPAdapter(transport, Path(directory) / "ploid.sqlite3")
            with self.assertRaises(PloidAdapterError):
                adapter.search_people("Ada Example", budget_authorized=True, max_acu=1)
            self.assertEqual(transport.calls, [])
            adapter.close()


if __name__ == "__main__":
    unittest.main()
