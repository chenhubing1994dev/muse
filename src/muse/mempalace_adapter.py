"""Optional, local-only MemPalace bridge for curated Muse memory recall.

Muse's SQLite stores remain authoritative. This adapter deliberately exposes a
small read allowlist and one explicitly approved checkpoint write; it never
mines arbitrary directories or imports raw conversation transcripts.
"""
from __future__ import annotations

import json
import math
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Sequence

MEM_PALACE_READ_TOOLS = frozenset({
    "mempalace_status",
    "mempalace_list_wings",
    "mempalace_list_rooms",
    "mempalace_get_taxonomy",
    "mempalace_search",
    "mempalace_check_duplicate",
    "mempalace_get_aaak_spec",
    "mempalace_get_drawer",
    "mempalace_get_drawers",
    "mempalace_kg_query",
    "mempalace_kg_timeline",
    "mempalace_kg_stats",
    "mempalace_traverse",
    "mempalace_find_tunnels",
    "mempalace_graph_stats",
    "mempalace_list_tunnels",
    "mempalace_list_hallways",
    "mempalace_follow_tunnels",
    "mempalace_diary_read",
    "mempalace_memories_filed_away",
})
MEM_PALACE_WRITE_TOOLS = frozenset({"mempalace_checkpoint"})
MEM_PALACE_ENVIRONMENT_KEYS = frozenset({
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HOME",
    "USERPROFILE",
    "MEMPALACE_PALACE_PATH",
    "MEMPALACE_EMBEDDING_MODEL",
    "MEMPALACE_EMBEDDING_DEVICE",
})
MAX_CHECKPOINT_ITEMS = 32
MAX_CHECKPOINT_BYTES = 64 * 1024
_CREDENTIAL_PATTERNS = (
    r"(?i)\b(password|passwd|passphrase|api[_ -]?key|access[_ -]?token|secret[_ -]?key|bearer)\b\s*(?::|=|is)\s*\S+",
    r"(?i)\bsk-[a-z0-9_-]{16,}\b",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
)


class MemPalaceStdioTransport:
    """Run an explicitly configured local MemPalace MCP server via Muse's safe client."""

    def __init__(
        self,
        command: Sequence[str],
        workspace: str | Path,
        *,
        timeout_seconds: float = 90.0,
        environment: dict[str, str] | None = None,
    ) -> None:
        if not isinstance(command, (list, tuple)) or not command:
            raise ValueError("MemPalace command must be a fixed, non-empty argv sequence")
        if any(not isinstance(part, str) or not part.strip() for part in command):
            raise ValueError("MemPalace command arguments must be non-empty strings")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        if environment is not None and (
            not isinstance(environment, dict)
            or any(
                not isinstance(name, str)
                or name not in MEM_PALACE_ENVIRONMENT_KEYS
                or not isinstance(value, str)
                or not value
                or len(value) > 4096
                for name, value in environment.items()
            )
        ):
            raise ValueError("MemPalace environment must use approved non-empty string settings")
        from muse.software_crafting import MCPStdioClient

        self.timeout_seconds = float(timeout_seconds)
        self.client = MCPStdioClient(
            command=tuple(command),
            workspace=workspace,
            allowed_tools=set(MEM_PALACE_READ_TOOLS | MEM_PALACE_WRITE_TOOLS),
            timeout_seconds=self.timeout_seconds,
            environment=dict(environment or {}),
        )

    def list_tools(self) -> dict[str, Any]:
        return {"tools": self.client.list_tools(timeout_seconds=self.timeout_seconds)}

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False,
    ) -> dict[str, Any]:
        if name not in MEM_PALACE_READ_TOOLS and name not in MEM_PALACE_WRITE_TOOLS:
            raise PermissionError("tool is outside Muse's MemPalace allowlist")
        if name in MEM_PALACE_WRITE_TOOLS and approved is not True:
            raise PermissionError("MemPalace writes require explicit per-call approval")
        return self.client.call_tool(
            name, arguments, approved=approved, timeout_seconds=self.timeout_seconds,
        )


class MemPalaceAdapter:
    """Thin optional adapter; no install, auto-sync, cloud transport, or data migration."""

    def __init__(self, transport: Any) -> None:
        if not callable(getattr(transport, "list_tools", None)) or not callable(
            getattr(transport, "call_tool", None)
        ):
            raise TypeError("transport must provide list_tools() and call_tool(name, arguments)")
        self.transport = transport

    @classmethod
    def from_stdio(
        cls,
        *,
        command: Sequence[str],
        workspace: str | Path,
        timeout_seconds: float = 90.0,
        environment: dict[str, str] | None = None,
    ) -> "MemPalaceAdapter":
        return cls(MemPalaceStdioTransport(
            command, workspace, timeout_seconds=timeout_seconds, environment=environment,
        ))

    @staticmethod
    def _text(value: Any, field: str, *, maximum: int = 500) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be non-empty text up to {maximum} characters")
        return value.strip()

    def _read(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in MEM_PALACE_READ_TOOLS:
            raise PermissionError("requested MemPalace operation is not read-allowlisted")
        return self.transport.call_tool(name, arguments or {}, approved=False)

    def status(self) -> dict[str, Any]:
        return self._read("mempalace_status")

    def list_wings(self) -> dict[str, Any]:
        return self._read("mempalace_list_wings")

    def taxonomy(self) -> dict[str, Any]:
        return self._read("mempalace_get_taxonomy")

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        wing: str | None = None,
        room: str | None = None,
        since: str | None = None,
        before: str | None = None,
        max_distance: float | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "query": self._text(query, "query", maximum=2000),
            "limit": limit,
        }
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        for field, value in (("wing", wing), ("room", room), ("since", since), ("before", before)):
            if value is not None:
                arguments[field] = self._text(value, field, maximum=160)
        if max_distance is not None:
            if isinstance(max_distance, bool) or not isinstance(max_distance, (int, float)):
                raise ValueError("max_distance must be a number between 0 and 2")
            if not math.isfinite(float(max_distance)) or not 0 <= max_distance <= 2:
                raise ValueError("max_distance must be a number between 0 and 2")
            arguments["max_distance"] = float(max_distance)
        return self._read("mempalace_search", arguments)

    def kg_query(
        self, entity: str, *, as_of: str | None = None, direction: str = "both",
    ) -> dict[str, Any]:
        arguments = {"entity": self._text(entity, "entity", maximum=200)}
        if not isinstance(direction, str) or direction not in {"outgoing", "incoming", "both"}:
            raise ValueError("direction must be outgoing, incoming, or both")
        if as_of is not None:
            arguments["as_of"] = self._text(as_of, "as_of", maximum=40)
        arguments["direction"] = direction
        return self._read("mempalace_kg_query", arguments)

    def kg_timeline(self, entity: str | None = None) -> dict[str, Any]:
        arguments = {} if entity is None else {"entity": self._text(entity, "entity", maximum=200)}
        return self._read("mempalace_kg_timeline", arguments)

    def traverse(self, start_room: str, *, max_hops: int = 2) -> dict[str, Any]:
        if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be between 1 and 5")
        return self._read("mempalace_traverse", {
            "start_room": self._text(start_room, "start_room", maximum=160),
            "max_hops": max_hops,
        })

    def diary_read(self, agent_name: str, *, last_n: int = 10) -> dict[str, Any]:
        if isinstance(last_n, bool) or not isinstance(last_n, int) or not 1 <= last_n <= 50:
            raise ValueError("last_n must be between 1 and 50")
        return self._read("mempalace_diary_read", {
            "agent_name": self._text(agent_name, "agent_name", maximum=120),
            "last_n": last_n,
        })

    def checkpoint(
        self,
        items: list[dict[str, Any]],
        *,
        approved: bool = False,
        user_data_authorized: bool = False,
        dedup_threshold: float = 0.9,
    ) -> dict[str, Any]:
        """Write curated items only; User Track requires separate explicit opt-in."""
        if approved is not True:
            raise PermissionError("MemPalace checkpoint requires explicit per-call approval")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_CHECKPOINT_ITEMS:
            raise ValueError(f"items must contain 1-{MAX_CHECKPOINT_ITEMS} curated records")
        if isinstance(dedup_threshold, bool) or not isinstance(dedup_threshold, (int, float)):
            raise ValueError("dedup_threshold must be between 0 and 1")
        if not math.isfinite(float(dedup_threshold)) or not 0 <= dedup_threshold <= 1:
            raise ValueError("dedup_threshold must be between 0 and 1")
        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict) or set(item) - {"wing", "room", "content", "track"}:
                raise ValueError("each checkpoint item may contain only wing, room, content and track")
            wing = self._text(item.get("wing"), "wing", maximum=120)
            room = self._text(item.get("room"), "room", maximum=160)
            content = self._text(item.get("content"), "content", maximum=16000)
            track = item.get("track", "agent")
            if not isinstance(track, str) or track not in {"agent", "user"}:
                raise ValueError("track must be agent or user")
            if track == "user":
                if user_data_authorized is not True:
                    raise PermissionError("User Track transfer requires separate explicit data-transfer authorization")
                raise PermissionError("User Track projection is disabled until a separate palace and retrieval scope are implemented")
            if any(re.search(pattern, content) for pattern in _CREDENTIAL_PATTERNS):
                raise ValueError("credential-like content cannot be checkpointed")
            normalized.append({"wing": wing, "room": room, "content": content})
        payload = {
            "items": normalized,
            "dedup_threshold": float(dedup_threshold),
            "added_by": "muse-agent-track",
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError("MemPalace checkpoint exceeds the 64 KiB payload limit")
        return self.transport.call_tool("mempalace_checkpoint", payload, approved=True)


def load_mempalace_adapter(
    workspace: str | Path,
    environ: dict[str, str] | None = None,
) -> MemPalaceAdapter | None:
    """Load an explicitly enabled local MemPalace connection; never installs or starts a daemon."""
    source = os.environ if environ is None else environ
    config_file = Path(workspace).resolve() / "config" / "muse.toml"
    config: dict[str, Any] = {}
    if config_file.is_file():
        try:
            parsed = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("config/muse.toml is unreadable or invalid TOML") from exc
        section = parsed.get("mempalace", {})
        if not isinstance(section, dict):
            raise ValueError("[mempalace] must be a TOML table")
        config = section

    raw_enabled = source.get("MUSE_MEMPALACE_ENABLED")
    if raw_enabled is None:
        enabled = config.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("[mempalace].enabled must be a boolean")
    else:
        normalized = str(raw_enabled).strip().casefold()
        if normalized not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ValueError("MUSE_MEMPALACE_ENABLED must be a boolean value")
        enabled = normalized in {"1", "true", "yes", "on"}
    if not enabled:
        return None

    def configured_path(key: str, env_key: str, *, must_be_file: bool = False) -> Path:
        raw = source.get(env_key) or config.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"[mempalace].{key} must be configured when MemPalace is enabled")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"[mempalace].{key} must be an absolute local path")
        resolved = path.resolve()
        valid = resolved.is_file() if must_be_file else resolved.is_dir()
        if not valid:
            raise ValueError(f"[mempalace].{key} does not point to an existing local path")
        return resolved

    python_path = configured_path("python_path", "MUSE_MEMPALACE_PYTHON", must_be_file=True)
    mcp_workspace = configured_path("mcp_workspace", "MUSE_MEMPALACE_WORKSPACE")
    palace_path = configured_path("palace_path", "MUSE_MEMPALACE_PALACE")
    cache_dir = configured_path("cache_dir", "MUSE_MEMPALACE_CACHE")
    profile_dir = configured_path("profile_dir", "MUSE_MEMPALACE_PROFILE")
    embedding_model = source.get("MUSE_MEMPALACE_EMBEDDING_MODEL") or config.get(
        "embedding_model", "embeddinggemma",
    )
    embedding_device = source.get("MUSE_MEMPALACE_EMBEDDING_DEVICE") or config.get(
        "embedding_device", "cpu",
    )
    if embedding_model not in {"embeddinggemma", "minilm"}:
        raise ValueError("MemPalace embedding_model must be embeddinggemma or minilm")
    if embedding_device not in {"cpu", "dml", "auto"}:
        raise ValueError("MemPalace embedding_device must be cpu, dml or auto")
    timeout = config.get("timeout_seconds", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 30 <= timeout <= 600:
        raise ValueError("[mempalace].timeout_seconds must be between 30 and 600")

    child_environment = {
        "HF_HOME": str(cache_dir),
        "HUGGINGFACE_HUB_CACHE": str(cache_dir / "hub"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HOME": str(profile_dir),
        "USERPROFILE": str(profile_dir),
        "MEMPALACE_PALACE_PATH": str(palace_path),
        "MEMPALACE_EMBEDDING_MODEL": embedding_model,
        "MEMPALACE_EMBEDDING_DEVICE": embedding_device,
    }
    command = (
        str(python_path), "-m", "mempalace.mcp_server",
        "--palace", str(palace_path),
    )
    return MemPalaceAdapter.from_stdio(
        command=command,
        workspace=mcp_workspace,
        timeout_seconds=float(timeout),
        environment=child_environment,
    )

