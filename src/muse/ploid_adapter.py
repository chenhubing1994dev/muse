"""Optional Ploid MCP adapter for evidence-backed professional people research.

The adapter uses an injected MCP transport or Muse's bounded local stdio client.
Ploid's device-login flow owns credentials; this module never stores secrets.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit


PLOID_MCP_ENDPOINT = "https://api.ploid.com/mcp"
PLOID_TOOL_ALLOWLIST = frozenset({
    "search_people", "research_person", "find_people", "find_decision_makers",
    "get_research_status", "cancel_research",
})
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
_PERSON_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
_CYCLE_ID = re.compile(r"cycle_[0-9a-f]{32}")


class PloidAdapterError(RuntimeError):
    """Ploid is unavailable, incompatible, unauthorized, or outside its safe budget."""


class PloidStdioTransport:
    """Run the official local Ploid MCP server through Muse's existing bounded stdio client."""

    def __init__(
        self, command: Sequence[str], workspace: str | Path,
        *, timeout_seconds: float = 90.0,
    ) -> None:
        from muse.software_crafting import MCPStdioClient

        self.timeout_seconds = timeout_seconds
        self.client = MCPStdioClient(
            command=command, workspace=workspace,
            allowed_tools=set(PLOID_TOOL_ALLOWLIST),
            timeout_seconds=timeout_seconds,
        )

    def list_tools(self) -> dict[str, Any]:
        return {"tools": self.client.list_tools(timeout_seconds=self.timeout_seconds)}

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False,
    ) -> dict[str, Any]:
        if name not in PLOID_TOOL_ALLOWLIST:
            raise PermissionError("tool is outside the Ploid MCP allowlist")
        return self.client.call_tool(
            name, arguments, approved=approved, timeout_seconds=self.timeout_seconds,
        )




class PloidMCPAdapter:
    """Thin, dynamically discovered Ploid MCP adapter with cost and run controls."""
    @classmethod
    def from_stdio(
        cls, *, command: Sequence[str], workspace: str | Path,
        run_database: str | Path, knowledge_store: Any | None = None,
        timeout_seconds: float = 90.0,
        clock: Callable[[], float] = time.time,
    ) -> "PloidMCPAdapter":
        transport = PloidStdioTransport(command, workspace, timeout_seconds=timeout_seconds)
        return cls(transport, run_database, knowledge_store=knowledge_store, clock=clock)


    def __init__(
        self,
        transport: Any,
        run_database: str | Path,
        *,
        knowledge_store: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(getattr(transport, "list_tools", None)) or not callable(getattr(transport, "call_tool", None)):
            raise TypeError("transport must provide list_tools() and call_tool(name, arguments)")
        self.transport = transport
        self.knowledge_store = knowledge_store
        self.clock = clock
        self.database = Path(run_database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS ploid_research_runs (
                run_id TEXT PRIMARY KEY,
                run_type TEXT NOT NULL CHECK(run_type IN ('person','people_set')),
                status TEXT NOT NULL,
                estimated_acu INTEGER NOT NULL CHECK(estimated_acu >= 0),
                created_at REAL NOT NULL,
                last_polled_at REAL
            )"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _budget(cost_acu: int, *, budget_authorized: bool, max_acu: int | None) -> None:
        if budget_authorized is not True:
            raise PloidAdapterError("paid Ploid operation requires explicit budget authorization")
        if isinstance(max_acu, bool) or not isinstance(max_acu, int) or max_acu < cost_acu:
            raise PloidAdapterError(f"operation requires up to {cost_acu} ACU; supplied budget is insufficient")

    def _tool_catalog(self) -> dict[str, dict[str, Any]]:
        try:
            response = self.transport.list_tools()
        except Exception as exc:
            raise PloidAdapterError("Ploid MCP tool discovery failed: " + type(exc).__name__) from exc
        tools = response.get("tools") if isinstance(response, dict) else None
        if tools is None and isinstance(response, dict) and isinstance(response.get("result"), dict):
            tools = response["result"].get("tools")
        if not isinstance(tools, list):
            raise PloidAdapterError("Ploid MCP returned an invalid tools/list catalog")
        catalog: dict[str, dict[str, Any]] = {}
        for item in tools:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                catalog[item["name"]] = item
        return catalog

    def _call(self, name: str, arguments: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        # Refresh tools/list on every operation: Ploid has removed/renamed tools before.
        catalog = self._tool_catalog()
        tool = catalog.get(name)
        if tool is None:
            raise PloidAdapterError("Ploid MCP tool is unavailable in the current schema: " + name)
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise PloidAdapterError("Ploid MCP tool has no valid object input schema: " + name)
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise PloidAdapterError("Ploid MCP tool schema is malformed: " + name)
        if any(not isinstance(field, str) or field not in arguments for field in required):
            raise PloidAdapterError("Ploid MCP tool arguments do not satisfy the current required schema: " + name)
        if schema.get("additionalProperties") is False and any(field not in properties for field in arguments):
            raise PloidAdapterError("Ploid MCP tool arguments exceed the current schema: " + name)
        try:
            raw = self.transport.call_tool(name, arguments, approved=approved)
        except Exception as exc:
            raise PloidAdapterError("Ploid MCP call failed: " + type(exc).__name__) from exc
        if not isinstance(raw, dict) or raw.get("isError") is True:
            raise PloidAdapterError("Ploid MCP tool returned an error: " + name)
        if isinstance(raw.get("structuredContent"), dict):
            return raw["structuredContent"]
        if isinstance(raw.get("result"), dict):
            return raw["result"]
        return raw

    @staticmethod
    def _linkedin_url(value: str) -> str:
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError("linkedin_url must be a bounded HTTPS LinkedIn profile URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or parsed.hostname not in {"linkedin.com", "www.linkedin.com"}
            or not parsed.path.startswith("/in/") or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError("linkedin_url must be https://www.linkedin.com/in/<slug>")
        return value

    def search_people(
        self, name: str, *, company: str | None = None, location: str | None = None,
        title: str | None = None, limit: int = 5,
        budget_authorized: bool = False, max_acu: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("name must contain 1-200 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        self._budget(1, budget_authorized=budget_authorized, max_acu=max_acu)
        arguments: dict[str, Any] = {"name": name.strip(), "limit": limit}
        for key, value in (("company", company), ("location", location), ("title", title)):
            if value is not None:
                if not isinstance(value, str) or not value.strip() or len(value) > 200:
                    raise ValueError(f"{key} must contain 1-200 characters")
                arguments[key] = value.strip()
        # This returns candidates only. It never selects a person automatically.
        return self._call("search_people", arguments, approved=True)

    def research_person(
        self, *, person_id: str | None = None, linkedin_url: str | None = None,
        selected_by_user: bool = False, budget_authorized: bool = False,
        max_acu: int | None = None,
    ) -> dict[str, Any]:
        if (person_id is None) == (linkedin_url is None):
            raise ValueError("supply exactly one of person_id or linkedin_url")
        arguments: dict[str, Any]
        if person_id is not None:
            if not isinstance(person_id, str) or not _PERSON_ID.fullmatch(person_id):
                raise ValueError("person_id is invalid")
            if selected_by_user is not True:
                raise PloidAdapterError("a candidate returned by search_people must be selected by the user")
            arguments = {"person_id": person_id}
        else:
            arguments = {"linkedin_url": self._linkedin_url(linkedin_url)}
        self._budget(25, budget_authorized=budget_authorized, max_acu=max_acu)
        result = self._call("research_person", arguments, approved=True)
        self._record_run(result, run_type="person", estimated_acu=25)
        return result

    def find_people(
        self, query: str, *, count: int = 10,
        budget_authorized: bool = False, max_acu: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("query must contain 1-2000 characters")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 25:
            raise ValueError("count must be between 1 and 25")
        cost = math.ceil(count / 10)
        self._budget(cost, budget_authorized=budget_authorized, max_acu=max_acu)
        result = self._call("find_people", {"query": query.strip(), "count": count}, approved=True)
        self._record_run(result, run_type="people_set", estimated_acu=cost)
        return result

    def find_decision_makers(
        self, company: str, product: str, *, titles: list[str] | None = None,
        count: int = 10, budget_authorized: bool = False,
        max_acu: int | None = None,
    ) -> dict[str, Any]:
        for value, label in ((company, "company"), (product, "product")):
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError(f"{label} must contain 1-500 characters")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
            raise ValueError("count must be between 1 and 20")
        if titles is not None and (
            not isinstance(titles, list) or len(titles) > 20
            or any(not isinstance(item, str) or not item.strip() or len(item) > 120 for item in titles)
        ):
            raise ValueError("titles must be a list of at most 20 bounded strings")
        cost = math.ceil(count / 10)
        self._budget(cost, budget_authorized=budget_authorized, max_acu=max_acu)
        arguments: dict[str, Any] = {"company": company.strip(), "product": product.strip(), "count": count}
        if titles:
            arguments["titles"] = [item.strip() for item in titles]
        result = self._call("find_decision_makers", arguments, approved=True)
        self._record_run(result, run_type="people_set", estimated_acu=cost)
        return result

    def _record_run(self, result: dict[str, Any], *, run_type: str, estimated_acu: int) -> None:
        run_id = result.get("run_id")
        status = result.get("status")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            # Completed cached snapshots may not need a run id; only persist durable runs.
            if status == "completed":
                return
            raise PloidAdapterError("Ploid accepted work without a valid durable run_id")
        if not isinstance(status, str) or status not in {"queued", "running", "completed", "cancelled"}:
            raise PloidAdapterError("Ploid returned an unsupported research run status")
        with self.connection:
            self.connection.execute(
                """INSERT INTO ploid_research_runs(run_id,run_type,status,estimated_acu,created_at,last_polled_at)
                   VALUES(?,?,?,?,?,NULL)
                   ON CONFLICT(run_id) DO UPDATE SET status=excluded.status""",
                (run_id, run_type, status, estimated_acu, self.clock()),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id is invalid")
        row = self.connection.execute(
            "SELECT run_id,run_type,status,estimated_acu,created_at,last_polled_at FROM ploid_research_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_research_status(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise PloidAdapterError("run_id is not registered in this Muse workspace")
        if run["status"] in {"completed", "cancelled"}:
            raise PloidAdapterError("research run is already terminal; do not poll it again")
        now = self.clock()
        last = run["last_polled_at"]
        if last is not None and now - float(last) < 5.0:
            raise PloidAdapterError("Ploid status polling is limited to one request every five seconds")
        with self.connection:
            self.connection.execute(
                "UPDATE ploid_research_runs SET last_polled_at=? WHERE run_id=?", (now, run_id)
            )
        result = self._call("get_research_status", {"run_id": run_id})
        status = result.get("status")
        if status not in {"queued", "running", "completed", "cancelled"}:
            raise PloidAdapterError("Ploid returned an unsupported research run status")
        with self.connection:
            self.connection.execute(
                "UPDATE ploid_research_runs SET status=? WHERE run_id=?", (status, run_id)
            )
        return result

    def cancel_research(self, run_id: str, *, user_requested: bool = False) -> dict[str, Any]:
        if user_requested is not True:
            raise PloidAdapterError("cancellation is allowed only after an explicit user stop request")
        run = self.get_run(run_id)
        if run is None:
            raise PloidAdapterError("run_id is not registered in this Muse workspace")
        if run["status"] in {"completed", "cancelled"}:
            raise PloidAdapterError("terminal research runs cannot be cancelled")
        result = self._call("cancel_research", {"run_id": run_id}, approved=True)
        status = result.get("status")
        if status in {"cancelled", "cancel_requested"}:
            with self.connection:
                self.connection.execute(
                    "UPDATE ploid_research_runs SET status=? WHERE run_id=?",
                    ("cancelled" if status == "cancelled" else "running", run_id),
                )
        return result


    def ingest_person_result(
        self, result: dict[str, Any], *, evolution_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        if self.knowledge_store is None:
            raise PloidAdapterError("Research Wiki store is not configured")
        if not isinstance(result, dict) or result.get("status") != "completed" or result.get("found") is not True:
            raise PloidAdapterError("only a completed, identity-resolved Ploid result can be ingested")
        if evolution_cycle_id is not None and not _CYCLE_ID.fullmatch(evolution_cycle_id):
            raise ValueError("evolution_cycle_id is invalid")
        person = result.get("person")
        if not isinstance(person, dict):
            raise ValueError("Ploid result is missing its person record")
        name = person.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Ploid person record requires a bounded professional name")
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("Ploid result requires a durable run_id")
        person_id = person.get("person_id")
        linkedin_url = person.get("linkedin_url")
        if person_id is not None and (not isinstance(person_id, str) or not _PERSON_ID.fullmatch(person_id)):
            raise ValueError("Ploid person_id is invalid")
        if linkedin_url is not None:
            linkedin_url = self._linkedin_url(linkedin_url)
        profile: dict[str, str] = {}
        for key in ("name", "title", "company", "location", "linkedin_url"):
            value = linkedin_url if key == "linkedin_url" else person.get(key)
            if isinstance(value, str) and value.strip() and len(value) <= 500:
                profile[key] = value.strip()
        source_links = result.get("source_links")
        if not isinstance(source_links, list) or not 1 <= len(source_links) <= 32:
            raise ValueError("Ploid result must contain 1-32 source links")
        limitations = result.get("limitations", [])
        if not isinstance(limitations, list) or len(limitations) > 20:
            raise ValueError("limitations must be a list of at most 20 items")
        clean_limitations = [
            item.strip()[:500] for item in limitations
            if isinstance(item, str) and item.strip()
        ]
        provenance = result.get("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
        safe_provenance: dict[str, dict[str, str]] = {}
        for field, detail in list(provenance.items())[:32]:
            if not isinstance(field, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field):
                continue
            if not isinstance(detail, dict):
                continue
            source_url = detail.get("source_url")
            confidence = detail.get("confidence")
            if isinstance(source_url, str) and isinstance(confidence, str):
                parsed_source = urlsplit(source_url)
                if parsed_source.scheme == "https" and parsed_source.hostname and not parsed_source.username and not parsed_source.password:
                    safe_provenance[field] = {
                        "source_url": source_url[:1000], "confidence": confidence[:40],
                    }
        identity_key = person_id or hashlib.sha256((linkedin_url or name).encode("utf-8")).hexdigest()
        normalized_sources: list[tuple[str, str]] = []
        for item in source_links:
            if isinstance(item, str):
                url, title = item, "Ploid research source"
            elif isinstance(item, dict):
                url, title = item.get("url"), item.get("title", "Ploid research source")
            else:
                continue
            if not isinstance(url, str) or len(url) > 2000:
                continue
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                continue
            if not isinstance(title, str) or not title.strip():
                title = "Ploid research source"
            normalized_sources.append((url, title.strip()[:500]))
        if not normalized_sources:
            raise ValueError("Ploid result contained no valid HTTPS source links")
        node = self.knowledge_store.upsert_node(
            "idea", "Professional research: " + name.strip(),
            summary="Ploid source-reviewed professional profile; not independently verified by Muse.",
            external_ref="ploid:person:" + identity_key,
            status="proposed",
            metadata={
                "provider": "ploid", "run_id": run_id,
                "epistemic_status": "third_party_research_unverified_by_muse",
                "identity_resolution": "provider_found_true",
                "professional_profile": profile,
                "field_provenance": safe_provenance,
                "source_refs": [],
                "limitations": clean_limitations,
                "independent_verification": "not_performed",
                "evolution_cycle_id": evolution_cycle_id,
            },
            provenance=[],
        )
        source_ids: list[str] = []
        source_refs: list[str] = []
        for url, title in normalized_sources:
            if not isinstance(title, str) or not title.strip():
                title = "Ploid research source"
            source = self.knowledge_store.upsert_node(
                "source", title.strip()[:500],
                summary="Source reference returned by Ploid; original page not independently fetched by Muse.",
                external_ref="ploid:source:" + hashlib.sha256(url.encode("utf-8")).hexdigest(),
                metadata={
                    "provider": "ploid", "run_id": run_id,
                    "source_url": url, "evolution_cycle_id": evolution_cycle_id,
                    "source_reviewed_by_provider": True,
                    "independent_fetch_by_muse": False,
                },
                provenance=[url],
            )
            self.knowledge_store.add_edge(
                source["id"], node["id"], "relates",
                metadata={
                    "provider": "ploid", "run_id": run_id,
                    "evidence_role": "provenance_reference_not_verdict",
                    "evolution_cycle_id": evolution_cycle_id,
                },
            )
            source_ids.append(source["id"])
            source_refs.append(url)
        if not source_ids:
            raise ValueError("Ploid result contained no valid HTTPS source links")
        # Update the existing idea with the final bounded source set after validation.
        node = self.knowledge_store.upsert_node(
            "idea", "Professional research: " + name.strip(),
            summary="Ploid source-reviewed professional profile; not independently verified by Muse.",
            external_ref="ploid:person:" + identity_key,
            status="proposed",
            metadata={"source_refs": source_refs},
            provenance=source_refs,
        )
        return {"knowledge_node_id": node["id"], "source_node_ids": list(dict.fromkeys(source_ids))}
