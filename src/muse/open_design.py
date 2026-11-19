"""Local Open Design MCP bridge owned and scheduled by Muse Commander.

The default client is read-only. Creative runs are queued in Muse's existing
TaskQueue, require recorded approval, and require an explicitly allowlisted
agent. Cloud/BYOK runtimes and destructive Open Design tools are never selected
implicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence
from urllib.parse import quote, urlsplit

from muse.software_crafting import MCPStdioClient, SoftwareCraftingError
from muse.task_queue import TaskQueue


READ_ONLY_TOOLS = frozenset({
    "list_projects", "get_active_context", "get_project", "get_artifact",
    "get_file", "search_files", "list_files", "list_skills", "list_plugins",
    "list_agents", "get_run",
})
APPROVED_WRITE_TOOLS = frozenset({
    "create_project", "start_run", "create_artifact", "write_file",
})
NEVER_ALLOW_TOOLS = frozenset({
    "delete_file", "delete_project", "cancel_run", "start_vela_login",
    "collect_brief", "confirm_brief",
})
MAX_PROMPT_BYTES = 12 * 1024
MAX_INPUTS_BYTES = 64 * 1024


def _decode_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON text envelope returned by Open Design MCP tools."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            value = block.get("text")
            if not isinstance(value, str):
                continue
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {"text": value}
            if isinstance(decoded, dict):
                return decoded
            return {"data": decoded}
    return dict(result) if isinstance(result, dict) else {"data": result}


def _project_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("Open Design output path must be non-empty relative text")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or ":" in normalized:
        raise ValueError("Open Design output path must remain inside its project")
    return path.as_posix()


class OpenDesignAdapter:
    """Read-only discovery plus approval-gated project-scoped creative runs."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        database: str | Path,
        command: Sequence[str],
        daemon_url: str,
        data_dir: str | Path | None = None,
        environment: dict[str, str] | None = None,
        allowed_agents: Sequence[str] = (),
        timeout_seconds: float = 120.0,
        run_timeout_seconds: float = 1800.0,
        poll_interval_seconds: float = 5.0,
        client_factory: Callable[..., MCPStdioClient] = MCPStdioClient,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        auto_start_daemon: bool = False,
        expected_version: str | None = "0.27.1",
        startup_timeout_seconds: float = 45.0,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("Muse workspace must already exist")
        self.database = Path(database)
        if not self.database.is_absolute():
            self.database = self.workspace / self.database
        self.database = self.database.resolve()
        if not command or any(not isinstance(part, str) or not part.strip() for part in command):
            raise ValueError("Open Design MCP command must be a fixed executable and argv")
        self.command = tuple(command)
        parsed = urlsplit(daemon_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port or parsed.path not in {"", "/"}:
            raise PermissionError("Open Design daemon URL must be a root loopback HTTP endpoint")
        self.daemon_url = daemon_url.rstrip("/")
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.runtime_path = Path(self.command[0])
        self.cli_path = Path(self.command[1]) if len(self.command) > 1 else None
        self.install_dir = self.runtime_path.parent
        self.auto_start_daemon = bool(auto_start_daemon)
        self.expected_version = expected_version
        if isinstance(startup_timeout_seconds, bool) or not isinstance(startup_timeout_seconds, (int, float)) or not 5 <= startup_timeout_seconds <= 180:
            raise ValueError("daemon startup timeout must be between 5 and 180 seconds")
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.daemon_status = "not_managed"
        self.daemon_pid: int | None = None
        self.daemon_log_path: Path | None = None
        self._daemon_ready = False
        self.project_discovery_source = "not_checked"
        self.allowed_agents = frozenset(allowed_agents)
        if any(not isinstance(agent, str) or not agent.strip() for agent in self.allowed_agents):
            raise ValueError("allowed_agents must contain exact non-empty agent ids")
        if isinstance(run_timeout_seconds, bool) or not isinstance(run_timeout_seconds, (int, float)) or not 1 <= run_timeout_seconds <= 86400:
            raise ValueError("run timeout must be between 1 and 86400 seconds")
        if isinstance(poll_interval_seconds, bool) or not isinstance(poll_interval_seconds, (int, float)) or not 0.1 <= poll_interval_seconds <= 60:
            raise ValueError("poll interval must be between 0.1 and 60 seconds")
        self.timeout_seconds = timeout_seconds
        self.run_timeout_seconds = float(run_timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        self._client_factory = client_factory
        self.environment = dict(environment or {})
        if self.data_dir is not None:
            self.environment.setdefault("OD_DATA_DIR", str(self.data_dir))
        self._read_client = client_factory(
            command=self.command, workspace=self.workspace,
            allowed_tools=READ_ONLY_TOOLS, timeout_seconds=timeout_seconds,
            environment=self.environment,
        )
        self._write_client: MCPStdioClient | None = None

    @classmethod
    def from_installed(
        cls,
        *,
        workspace: str | Path,
        database: str | Path = "data/tasks/muse.sqlite3",
        config_path: str | Path | None = None,
        client_factory: Callable[..., MCPStdioClient] = MCPStdioClient,
    ) -> "OpenDesignAdapter":
        root = Path(workspace).resolve()
        config_file = Path(config_path) if config_path else root / "config" / "muse.toml"
        config: dict[str, Any] = {}
        if config_file.is_file():
            config = tomllib.loads(config_file.read_text(encoding="utf-8"))
        section = config.get("open_design", {})
        if not isinstance(section, dict):
            raise ValueError("[open_design] configuration must be a TOML table")

        install_dir = Path(os.environ.get("MUSE_OPEN_DESIGN_INSTALL_DIR", r"D:\Programs\Open Design"))
        cli_value = (
            os.environ.get("MUSE_OPEN_DESIGN_CLI")
            or section.get("cli_path")
            or str(install_dir / "resources" / "app" / "prebundled" / "daemon" / "daemon-cli.mjs")
        )
        runtime_value = (
            os.environ.get("MUSE_OPEN_DESIGN_RUNTIME")
            or section.get("runtime_path")
            or str(install_dir / "Open Design.exe")
            if os.name == "nt" else (shutil.which("node") or "")
        )
        data_value = (
            os.environ.get("MUSE_OPEN_DESIGN_DATA_DIR")
            or section.get("data_dir")
            or (
                str(Path(os.environ["APPDATA"]) / "Open Design" / "namespaces" / "release-stable-win" / "data")
                if os.name == "nt" and os.environ.get("APPDATA") else None
            )
        )
        daemon_url = (
            os.environ.get("MUSE_OPEN_DESIGN_DAEMON_URL")
            or section.get("daemon_url")
            or os.environ.get("OD_DAEMON_URL")
            or "http://127.0.0.1:7456"
        )
        cli_path = Path(str(cli_value)).expanduser()
        runtime_path = Path(str(runtime_value)).expanduser()
        if not cli_path.is_file():
            raise FileNotFoundError(f"Open Design CLI not found: {cli_path}")
        if not runtime_path.is_file() and shutil.which(str(runtime_value)) is None:
            raise FileNotFoundError(f"Open Design Node runtime not found: {runtime_path}")
        data_dir = Path(str(data_value)).expanduser() if data_value else None
        allowed_agents = section.get("allowed_agents", [])
        if not isinstance(allowed_agents, list) or any(not isinstance(item, str) for item in allowed_agents):
            raise ValueError("open_design.allowed_agents must be an array of exact agent ids")
        environment: dict[str, str] = {}
        if data_dir is not None:
            environment["OD_DATA_DIR"] = str(data_dir.resolve())
        if runtime_path.name.casefold() == "open design.exe":
            environment["ELECTRON_RUN_AS_NODE"] = "1"
        opencode_config_value = (
            os.environ.get("MUSE_OPEN_DESIGN_OPENCODE_CONFIG")
            or section.get("opencode_config_path")
        )
        if opencode_config_value:
            opencode_config_path = Path(str(opencode_config_value)).expanduser()
            if not opencode_config_path.is_file():
                raise FileNotFoundError(f"OpenCode local config not found: {opencode_config_path}")
            environment["OPENCODE_CONFIG"] = str(opencode_config_path.resolve())
            environment["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "true"
            environment["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        return cls(
            workspace=root, database=database,
            command=(str(runtime_path), str(cli_path), "mcp", "--daemon-url", str(daemon_url)),
            daemon_url=str(daemon_url), data_dir=data_dir,
            environment=environment, allowed_agents=allowed_agents,
            timeout_seconds=float(section.get("timeout_seconds", 120)),
            run_timeout_seconds=float(section.get("run_timeout_seconds", 1800)),
            poll_interval_seconds=float(section.get("poll_interval_seconds", 5)),
            client_factory=client_factory,
            auto_start_daemon=bool(section.get("auto_start_daemon", False)),
            expected_version=str(section.get("expected_version", "0.27.1")),
            startup_timeout_seconds=float(section.get("startup_timeout_seconds", 45)),
        )

    def _read_daemon_health(self) -> dict[str, Any]:
        with urllib.request.urlopen(self.daemon_url + "/api/health", timeout=3) as response:
            if getattr(response, "status", 200) != 200:
                raise SoftwareCraftingError("Open Design daemon health endpoint returned a non-200 response")
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
        if not isinstance(payload, dict):
            raise SoftwareCraftingError("Open Design daemon health response is not a JSON object")
        version = payload.get("version") or payload.get("appVersion")
        if self.expected_version and version != self.expected_version:
            raise PermissionError(
                f"Open Design daemon version mismatch: expected {self.expected_version}, got {version!r}"
            )
        return payload

    def _ensure_daemon(self) -> None:
        if not self.auto_start_daemon:
            self.daemon_status = "externally_managed"
            return
        if self._daemon_ready:
            return
        try:
            self._read_daemon_health()
            self.daemon_status = "already_running"
            self._daemon_ready = True
            return
        except PermissionError:
            raise
        except Exception:
            pass

        parsed = urlsplit(self.daemon_url)
        host, port = str(parsed.hostname), int(parsed.port)
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
        except OSError:
            pass
        else:
            raise PermissionError(
                f"Open Design port {port} is occupied but its health endpoint is unavailable; refusing to replace it"
            )
        if self.data_dir is None or not self.data_dir.is_dir():
            raise FileNotFoundError("Open Design auto-start requires the existing user data directory")
        if self.cli_path is None or not self.cli_path.is_file() or not self.runtime_path.is_file():
            raise FileNotFoundError("Open Design packaged runtime or daemon CLI is missing")

        log_dir = self.data_dir / "logs" / "muse"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "open-design-daemon.log"
        child_env = os.environ.copy()
        child_env.update(self.environment)
        child_env.update({
            "OD_DATA_DIR": str(self.data_dir),
            "OD_PORT": str(port),
            "OD_BIND_HOST": host,
        })
        secret_markers = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD")
        for key in tuple(child_env):
            if any(marker in key.upper() for marker in secret_markers):
                child_env.pop(key, None)
        if self.runtime_path.name.casefold() == "open design.exe":
            child_env["ELECTRON_RUN_AS_NODE"] = "1"

        command = [
            str(self.runtime_path), str(self.cli_path), "--no-open",
            "--port", str(port), "--host", host,
        ]
        kwargs: dict[str, Any] = {
            "cwd": str(self.install_dir), "env": child_env,
            "stdin": subprocess.DEVNULL, "stdout": None,
            "stderr": subprocess.STDOUT, "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        with log_path.open("ab") as log_handle:
            kwargs["stdout"] = log_handle
            process = subprocess.Popen(command, **kwargs)
        self.daemon_pid = process.pid
        self.daemon_log_path = log_path
        deadline = self._monotonic() + self.startup_timeout_seconds
        last_error: Exception | None = None
        while self._monotonic() < deadline:
            try:
                self._read_daemon_health()
                self.daemon_status = "started"
                self._daemon_ready = True
                return
            except PermissionError:
                raise
            except Exception as exc:
                last_error = exc
            if process.poll() is not None:
                raise SoftwareCraftingError(
                    f"Open Design daemon exited with code {process.returncode}; inspect {log_path}"
                ) from last_error
            self._sleep(0.5)
        raise TimeoutError(
            f"Open Design daemon did not become healthy within {self.startup_timeout_seconds:.0f}s; inspect {log_path}"
        ) from last_error

    def _catalog(self) -> list[dict[str, Any]]:
        self._ensure_daemon()
        tools = self._read_client.list_tools(timeout_seconds=self.timeout_seconds)
        if not isinstance(tools, list):
            raise SoftwareCraftingError("Open Design returned an invalid MCP tool catalog")
        seen: set[str] = set()
        for spec in tools:
            if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
                raise SoftwareCraftingError("Open Design MCP catalog contains an invalid tool entry")
            name = spec["name"]
            if name in seen:
                raise SoftwareCraftingError("Open Design MCP catalog contains duplicate tool names")
            seen.add(name)
            annotations = spec.get("annotations") if isinstance(spec.get("annotations"), dict) else {}
            if name in READ_ONLY_TOOLS:
                if annotations.get("readOnlyHint") is not True or annotations.get("destructiveHint") is True:
                    raise PermissionError(f"Open Design read-only tool annotation is unsafe: {name}")
            if name in APPROVED_WRITE_TOOLS:
                if annotations.get("readOnlyHint") is not False or annotations.get("destructiveHint") is True:
                    raise PermissionError(f"Open Design write tool annotation is unsafe: {name}")
        return tools

    def call_read_only(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in READ_ONLY_TOOLS:
            raise PermissionError("Open Design tool is not in Muse's read-only allowlist")
        self._catalog()
        companion = "list_projects" if name == "get_active_context" else "get_active_context"
        results = self._read_client.call_tools([
            {"name": name, "arguments": arguments or {}},
            {"name": companion, "arguments": {}},
        ], approved=False, timeout_seconds=self.timeout_seconds)
        if not results:
            raise SoftwareCraftingError(f"Open Design MCP returned no result for {name}")
        return results[0]

    def _get_write_client(self) -> MCPStdioClient:
        if self._write_client is None:
            self._write_client = self._client_factory(
                command=self.command, workspace=self.workspace,
                allowed_tools=APPROVED_WRITE_TOOLS, timeout_seconds=self.timeout_seconds,
                environment=self.environment,
            )
        return self._write_client

    def call_write(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False,
        overwrite_approved: bool = False,
    ) -> dict[str, Any]:
        if approved is not True:
            raise PermissionError("Open Design write tools require explicit Muse task approval")
        if name not in APPROVED_WRITE_TOOLS or name in NEVER_ALLOW_TOOLS:
            raise PermissionError("Open Design tool is not in Muse's approved write allowlist")
        if name == "create_project":
            project_name, project_id = arguments.get("name"), arguments.get("id")
            if not isinstance(project_name, str) or not project_name.strip() or len(project_name) > 128:
                raise ValueError("Open Design project creation requires a bounded project name")
            if not isinstance(project_id, str) or not 1 <= len(project_id) <= 128 or not all(
                ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char in "._-"
                for char in project_id
            ):
                raise ValueError("Open Design project creation requires an explicit safe project id")
            existing_projects = self.list_projects()
            if any(item.get("id") == project_id for item in existing_projects):
                raise ValueError("Open Design project id already exists; use ensure_project to inspect it")
            if any(str(item.get("name", "")).casefold() == project_name.casefold() for item in existing_projects):
                raise ValueError("Open Design project name already exists; use ensure_project to inspect it")
        if name in {"create_artifact", "write_file"}:
            if name == "write_file" and overwrite_approved is not True:
                raise PermissionError("Open Design write_file requires separate overwrite approval")
            key = "name" if name == "create_artifact" else "path"
            arguments = {**arguments, key: _project_relative_path(arguments.get(key))}
            if not isinstance(arguments.get("project"), str) or not arguments["project"].strip():
                raise ValueError("Open Design writes require an explicit project id")
        if name == "start_run":
            agent = arguments.get("agent")
            if not isinstance(agent, str) or agent not in self.allowed_agents:
                raise PermissionError("Open Design run requires an explicitly allowlisted agent id")
            if not isinstance(arguments.get("project"), str) or not arguments["project"].strip():
                raise ValueError("Open Design runs require an explicit project id")
            try:
                uuid.UUID(str(arguments.get("requestId")))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Open Design start_run requires a stable UUID requestId") from exc
        self._catalog()
        return self._get_write_client().call_tool(
            name, arguments, approved=True, timeout_seconds=self.timeout_seconds,
        )

    def ensure_project(
        self, *, name: str, project_id: str, approved: bool = False,
    ) -> dict[str, Any]:
        """Create a dedicated project once; deterministic id makes recovery inspectable."""
        if approved is not True:
            raise PermissionError("Creating an Open Design project requires explicit Muse task approval")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("Open Design project name must be non-empty and at most 128 characters")
        if not isinstance(project_id, str) or not 1 <= len(project_id) <= 128 or not all(
            ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char in "._-"
            for char in project_id
        ):
            raise ValueError("Open Design project id must match [A-Za-z0-9._-] and be at most 128 characters")
        projects = self.list_projects()
        by_id = next((item for item in projects if item.get("id") == project_id), None)
        if by_id is not None:
            if by_id.get("name") != name:
                raise ValueError("Open Design project id already exists with a different name")
            return {"created": False, "project": by_id}
        by_name = next((item for item in projects if str(item.get("name", "")).casefold() == name.casefold()), None)
        if by_name is not None:
            raise ValueError("Open Design project name already exists under a different id; refusing a duplicate")
        try:
            result = _decode_tool_result(self.call_write(
                "create_project", {"name": name, "id": project_id}, approved=True,
            ))
        except SoftwareCraftingError as exc:
            raise SoftwareCraftingError(
                "Open Design create_project outcome may be unknown; inspect list_projects before retrying"
            ) from exc
        created = result.get("project") if isinstance(result.get("project"), dict) else None
        if created is None:
            created = next((item for item in self.list_projects() if item.get("id") == project_id), None)
        if not isinstance(created, dict) or created.get("id") != project_id:
            raise SoftwareCraftingError("Open Design create_project returned no verifiable project id")
        return {
            "created": True, "project": created,
            "conversation_id": result.get("conversationId"),
        }

    def _read_payload(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return _decode_tool_result(self.call_read_only(name, arguments))

    def _local_project_records(self) -> list[dict[str, Any]]:
        if self.data_dir is None:
            return []
        database = self.data_dir / "app.sqlite"
        if not database.is_file():
            return []
        uri = "file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "projects" not in tables:
                return []
            project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            if not {"id", "name"}.issubset(project_columns):
                raise SoftwareCraftingError("Open Design local projects table has an unsupported schema")
            selected = ["id", "name"] + [
                column for column in ("skill_id", "design_system_id", "created_at", "updated_at")
                if column in project_columns
            ]
            fields = ", ".join("p." + column for column in selected)
            if "workspace_projects" in tables:
                workspace_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(workspace_projects)")
                }
                if "project_id" in workspace_columns:
                    filters = []
                    if "resource_state" in workspace_columns:
                        filters.append("(wp.resource_state IS NULL OR wp.resource_state = 'active')")
                    if "cloud_tombstoned_at" in workspace_columns:
                        filters.append("(wp.cloud_tombstoned_at IS NULL OR wp.cloud_tombstoned_at = '')")
                    where = " WHERE " + " AND ".join(filters) if filters else ""
                    order = " ORDER BY p.updated_at DESC" if "updated_at" in project_columns else ""
                    query = (
                        f"SELECT {fields} FROM projects p LEFT JOIN workspace_projects wp "
                        f"ON wp.project_id = p.id{where}{order}"
                    )
                else:
                    query = f"SELECT {fields} FROM projects p"
            else:
                order = " ORDER BY updated_at DESC" if "updated_at" in project_columns else ""
                query = f"SELECT {fields} FROM projects{order}"
            rows = connection.execute(query).fetchall()
            return [dict(zip(selected, row)) for row in rows]
        finally:
            connection.close()

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._read_payload("list_projects")
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise SoftwareCraftingError("Open Design list_projects returned no project list")
        visible = [item for item in projects if isinstance(item, dict)]
        if visible:
            self.project_discovery_source = "mcp"
            return visible

        # Headless MCP may have no active workspace context even though the local
        # 0.27.x app database contains personal projects. Verify every DB id through
        # the official read-only get_project tool before exposing it to Muse.
        local_records = self._local_project_records()
        if not local_records:
            self.project_discovery_source = "mcp_empty"
            return []
        calls = [
            {"name": "get_project", "arguments": {"project": str(item["id"])}}
            for item in local_records
        ]
        if len(calls) == 1:
            calls.append({"name": "get_active_context", "arguments": {}})
        results = self._read_client.call_tools(
            calls, approved=False, timeout_seconds=self.timeout_seconds,
        )
        verified: list[dict[str, Any]] = []
        expected_ids = {str(item["id"]) for item in local_records}
        for result in results[:len(local_records)]:
            project = _decode_tool_result(result)
            if project.get("id") in expected_ids:
                verified.append(project)
        if len(verified) != len(expected_ids):
            raise SoftwareCraftingError("Open Design local project records could not all be verified through get_project")
        self.project_discovery_source = "local_db_verified"
        return verified

    def list_agents(self) -> list[dict[str, Any]]:
        data = self._read_payload("list_agents")
        agents = data.get("agents")
        if not isinstance(agents, list):
            raise SoftwareCraftingError("Open Design list_agents returned no agent list")
        return [item for item in agents if isinstance(item, dict)]

    def doctor(self) -> dict[str, Any]:
        catalog = self._catalog()
        names = {item["name"] for item in catalog}
        calls = [
            {"name": "list_agents", "arguments": {}},
            {"name": "list_skills", "arguments": {}},
            {"name": "list_plugins", "arguments": {}},
        ]
        if not set(call["name"] for call in calls).issubset(names):
            raise SoftwareCraftingError("Open Design is missing one or more required read-only discovery tools")
        results = self._read_client.call_tools(calls, approved=False, timeout_seconds=self.timeout_seconds)
        decoded = [_decode_tool_result(item) for item in results]
        projects = self.list_projects()
        agents = decoded[0].get("agents", [])
        skills = decoded[1].get("skills", [])
        plugins = decoded[2].get("plugins", [])
        return {
            "status": "connected",
            "daemon_url": self.daemon_url,
            "daemon_status": self.daemon_status,
            "daemon_pid": self.daemon_pid,
            "daemon_log_path": str(self.daemon_log_path) if self.daemon_log_path else None,
            "tool_count": len(catalog),
            "read_only_tools": sorted(name for name in names if name in READ_ONLY_TOOLS),
            "gated_write_tools": sorted(name for name in names if name in APPROVED_WRITE_TOOLS),
            "blocked_destructive_tools": sorted(name for name in names if name in NEVER_ALLOW_TOOLS),
            "unclassified_tools": sorted(name for name in names if name not in READ_ONLY_TOOLS | APPROVED_WRITE_TOOLS | NEVER_ALLOW_TOOLS),
            "project_count": len(projects),
            "projects": projects,
            "project_source": self.project_discovery_source,
            "agents": agents if isinstance(agents, list) else [],
            "skills_count": len(skills) if isinstance(skills, list) else 0,
            "plugins_count": len(plugins) if isinstance(plugins, list) else 0,
            "allowed_agents": sorted(self.allowed_agents),
            "generation_enabled": bool(self.allowed_agents),
        }

    def enqueue_run(
        self,
        *,
        project_id: str,
        prompt: str,
        agent: str,
        skill: str | None = None,
        skills: Sequence[str] = (),
        plugin: str | None = None,
        inputs: dict[str, Any] | None = None,
        priority: int = 70,
    ) -> dict[str, Any]:
        if not isinstance(agent, str) or agent not in self.allowed_agents:
            raise PermissionError("No Open Design agent is enabled until its exact local runtime is explicitly allowlisted")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("Open Design project id is required")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("Open Design prompt must be non-empty and at most 12 KiB")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be an integer from 0 to 100")
        if skill is not None and (not isinstance(skill, str) or not skill.strip()):
            raise ValueError("skill must be a non-empty skill id")
        if not isinstance(skills, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in skills):
            raise ValueError("skills must contain exact non-empty skill ids")
        if plugin is not None and (not isinstance(plugin, str) or not plugin.strip()):
            raise ValueError("plugin must be a non-empty plugin id")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict):
            raise ValueError("Open Design inputs must be a JSON object")
        inputs = dict(inputs)
        if len(json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUTS_BYTES:
            raise ValueError("Open Design plugin inputs exceed 64 KiB")
        projects = self.list_projects()
        if project_id not in {item.get("id") for item in projects}:
            raise ValueError("Open Design project id must match an existing project; create_project is a separate approved action")
        agents = self.list_agents()
        if agent not in {item.get("id") for item in agents}:
            raise ValueError("Selected Open Design agent is not currently available")
        payload = {
            "kind": "open_design_run", "project_id": project_id,
            "prompt": prompt.strip(), "agent": agent, "skill": skill,
            "skills": list(skills), "plugin": plugin, "inputs": inputs,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        with TaskQueue(self.database) as queue:
            task = queue.enqueue(
                "Open Design creative run", payload, priority=priority,
                estimated_seconds=int(self.run_timeout_seconds),
                recovery_policy="manual", idempotency_key="open-design-run:" + fingerprint,
                require_approval=True, workload_class="writing",
            )
        return task

    def execute_task(self, payload: dict[str, Any], *, task_id: str) -> dict[str, Any]:
        approval = payload.get("_approval")
        if payload.get("_approved") is not True or not isinstance(approval, dict):
            raise PermissionError("Open Design execution requires recorded Muse task approval")
        approver = approval.get("approved_by")
        if not isinstance(approver, str) or not approver.strip():
            raise PermissionError("Open Design approval actor is missing or inconsistent")
        if payload.get("kind") != "open_design_run":
            raise ValueError("unsupported Open Design task kind")
        project_id = payload.get("project_id")
        agent = payload.get("agent")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("Open Design task prompt is missing or exceeds 12 KiB")
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, dict) or len(json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUTS_BYTES:
            raise ValueError("Open Design task inputs are invalid or exceed 64 KiB")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("Open Design task has no explicit project id")
        if not isinstance(agent, str) or agent not in self.allowed_agents:
            raise PermissionError("Open Design task agent is not explicitly allowlisted")
        if project_id not in {item.get("id") for item in self.list_projects()}:
            raise ValueError("Open Design task project no longer exists")
        if agent not in {item.get("id") for item in self.list_agents()}:
            raise ValueError("Open Design task agent is no longer available")
        try:
            request_id = str(uuid.UUID(hex=task_id.replace("-", "")))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Muse task id cannot be converted to a stable Open Design request UUID") from exc
        arguments: dict[str, Any] = {
            "project": project_id, "prompt": prompt.strip(),
            "agent": agent, "requestId": request_id,
        }
        for key in ("skill", "skills", "plugin", "inputs"):
            value = payload.get(key)
            if value not in (None, [], {}):
                arguments[key] = value
        started = _decode_tool_result(self.call_write("start_run", arguments, approved=True))
        run_id = started.get("runId") or started.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise SoftwareCraftingError("Open Design start_run returned no runId; inspect by requestId before retrying")
        deadline = self._monotonic() + self.run_timeout_seconds
        latest: dict[str, Any] = {}
        while True:
            latest = self._read_payload("get_run", {"runId": run_id})
            status = latest.get("status")
            if status in {"succeeded", "failed", "canceled"}:
                break
            if self._monotonic() >= deadline:
                raise TimeoutError(f"Open Design run {run_id} did not reach a terminal state; do not resubmit start_run")
            self._sleep(self.poll_interval_seconds)
        if latest.get("status") != "succeeded":
            raise RuntimeError(f"Open Design run {run_id} ended with status {latest.get('status')}")
        return {
            "status": "completed", "run_id": run_id, "project_id": project_id,
            "agent": agent, "preview_url": latest.get("previewUrl") or latest.get("preview_url"),
            "artifacts": latest.get("artifacts", []), "run": latest,
            "request_id": request_id,
        }


class OpenDesignTaskHandler:
    """Lazy worker wrapper; it does not start or configure any cloud agent."""

    def __init__(self, *, workspace: str | Path, database: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.database = Path(database)

    def execute_task(self, payload: dict[str, Any], *, task_id: str) -> dict[str, Any]:
        adapter = OpenDesignAdapter.from_installed(workspace=self.workspace, database=self.database)
        return adapter.execute_task(payload, task_id=task_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse's local Open Design MCP bridge")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--database", default="data/tasks/muse.sqlite3")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="discover local Open Design tools, projects, skills and agents")
    sub.add_parser("projects", help="list existing Open Design projects (read-only)")
    sub.add_parser("agents", help="list available Open Design agent CLIs (read-only)")
    enqueue = sub.add_parser("enqueue-run", help="queue an approved Open Design creative run in Muse")
    enqueue.add_argument("--project", required=True)
    enqueue.add_argument("--agent", required=True)
    enqueue.add_argument("--prompt", required=True)
    enqueue.add_argument("--skill")
    enqueue.add_argument("--plugin")
    enqueue.add_argument("--inputs-json", default="{}")
    enqueue.add_argument("--priority", type=int, default=70)
    args = parser.parse_args(argv)
    try:
        adapter = OpenDesignAdapter.from_installed(workspace=args.workspace, database=args.database)
        if args.action == "doctor":
            result = adapter.doctor()
        elif args.action == "projects":
            result = {"projects": adapter.list_projects()}
        elif args.action == "agents":
            result = {"agents": adapter.list_agents()}
        else:
            inputs = json.loads(args.inputs_json)
            if not isinstance(inputs, dict):
                raise ValueError("--inputs-json must be a JSON object")
            result = adapter.enqueue_run(
                project_id=args.project, agent=args.agent, prompt=args.prompt,
                skill=args.skill, plugin=args.plugin, inputs=inputs, priority=args.priority,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        print("muse-open-design: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
