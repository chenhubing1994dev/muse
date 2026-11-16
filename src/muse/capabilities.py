"""Persistent registry of verified local tools and Muse capabilities."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUSES = frozenset({"ready", "missing", "unverified", "disabled", "needs_approval"})


class CapabilityRegistry:
    """Store capability metadata; registration never launches an executable."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS capabilities (
                capability_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                executable TEXT,
                capabilities_json TEXT NOT NULL,
                requirements_json TEXT NOT NULL,
                trust TEXT NOT NULL,
                license TEXT NOT NULL,
                status TEXT NOT NULL,
                version TEXT,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_capabilities_domain ON capabilities(domain, status)"
        )
        self._connection.commit()

    def __enter__(self) -> CapabilityRegistry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def register(
        self, *, capability_id: str, domain: str, name: str, description: str,
        status: str = "unverified", executable: str | None = None,
        capabilities: Iterable[str] = (), requirements: Iterable[str] = (),
        trust: str = "unverified", license: str = "unknown", version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = (capability_id, domain, name, description, trust, license)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("capability_id, domain, name, description, trust and license must be non-empty text")
        if status not in STATUSES:
            raise ValueError(f"unsupported capability status: {status!r}")
        if executable is not None and (not isinstance(executable, str) or not executable.strip()):
            raise ValueError("executable must be non-empty text when provided")
        capability_list = sorted(set(capabilities))
        requirement_list = sorted(set(requirements))
        if any(not isinstance(item, str) or not item.strip() for item in capability_list + requirement_list):
            raise ValueError("capabilities and requirements must contain non-empty text")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        updated_at = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """INSERT INTO capabilities (
                capability_id, domain, name, description, executable, capabilities_json,
                requirements_json, trust, license, status, version, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(capability_id) DO UPDATE SET
                domain=excluded.domain, name=excluded.name, description=excluded.description,
                executable=excluded.executable, capabilities_json=excluded.capabilities_json,
                requirements_json=excluded.requirements_json, trust=excluded.trust,
                license=excluded.license, status=excluded.status, version=excluded.version,
                metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (capability_id.strip(), domain.strip(), name.strip(), description.strip(), executable,
             json.dumps(capability_list), json.dumps(requirement_list), trust.strip(), license.strip(),
             status, version, json.dumps(metadata or {}, ensure_ascii=False), updated_at),
        )
        self._connection.commit()
        return self.get(capability_id)

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["capabilities"] = json.loads(item.pop("capabilities_json"))
        item["requirements"] = json.loads(item.pop("requirements_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def get(self, capability_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM capabilities WHERE capability_id = ?", (capability_id,)
        ).fetchone()
        return self._decode(row)

    def find_by_capability(self, capability: str, *, status: str | None = None) -> list[dict[str, Any]]:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability must be non-empty text")
        items = self.list_all(status=status)
        return [item for item in items if capability in item["capabilities"]]

    def list_all(self, *, domain: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise ValueError(f"unsupported capability status: {status!r}")
        clauses: list[str] = []
        parameters: list[str] = []
        if domain is not None:
            clauses.append("domain = ?")
            parameters.append(domain)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT * FROM capabilities" + where + " ORDER BY domain, capability_id", parameters
        ).fetchall()
        return [self._decode(row) for row in rows]


def sync_media_capabilities(registry: CapabilityRegistry, inventory: Any) -> dict[str, int]:
    """Synchronize only tools already confirmed by media.discover_media_tools()."""
    registered = 0
    updated = 0
    for tool in inventory.tools:
        capability_id = f"media.{tool.tool_id}"
        existed = registry.get(capability_id) is not None
        has_audio = any(value.startswith("audio_") or value in {"speech_synthesis", "voiceover_generation"} for value in tool.capabilities)
        has_video = any(value.startswith("video_") for value in tool.capabilities)
        domain = "media" if has_audio and has_video else ("audio" if has_audio else "video")
        version = inventory.ffmpeg_version if tool.tool_id == "ffmpeg" else None
        registry.register(
            capability_id=capability_id,
            domain=domain,
            name=tool.name,
            description=f"Discovered local executable for: {', '.join(tool.capabilities)}.",
            executable=tool.executable,
            capabilities=tool.capabilities,
            requirements=(),
            trust="local_path_discovered",
            license="not_applicable",
            status="ready",
            version=version,
            metadata={"hardware_encoders": list(inventory.hardware_encoders) if tool.tool_id == "ffmpeg" else []},
        )
        if existed:
            updated += 1
        else:
            registered += 1
    return {"registered": registered, "updated": updated, "total": len(inventory.tools)}
