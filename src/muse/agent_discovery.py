"""Safe discovery of explicitly configured local Agent health endpoints.

This module performs bounded HTTP GETs only. It never scans ports/processes,
launches executables, follows redirects, or trusts endpoint claims as permissions.
"""
from __future__ import annotations

import argparse
import json
from threading import Event
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from muse.agent_registry import AgentRegistry

MAX_CONFIG_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 8 * 1024
MAX_ENDPOINTS = 32


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_endpoint(url: object) -> str:
    if not isinstance(url, str) or len(url) > 512:
        raise ValueError("health_url must be text of at most 512 characters")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"/health", "/api/v1/health"}
            or parsed.port is None):
        raise ValueError("health_url must be an explicit loopback HTTP health endpoint with a port")
    return url


def probe_configured_health_endpoints(
    config_path: str | Path, *, timeout_seconds: float = 2.0,
) -> dict[str, object]:
    """Read-only probe of explicitly configured loopback health endpoints.

    This does not register Agents, refresh heartbeats, or write a database.
    Results contain aggregate counts only, never URLs, response bodies or metadata.
    """
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not 0.1 <= timeout_seconds <= 3.0):
        raise ValueError("timeout_seconds must be between 0.1 and 3.0")
    raw = Path(config_path).read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("discovery config exceeds 32 KiB")
    config = json.loads(raw.decode("utf-8"))
    if not isinstance(config, dict) or set(config) != {"agents"} or not isinstance(config["agents"], list):
        raise ValueError("discovery config must contain only an agents array")
    if len(config["agents"]) > MAX_ENDPOINTS:
        raise ValueError("discovery config may contain at most 32 agents")

    opener = build_opener(ProxyHandler({}), _NoRedirect)
    seen: set[str] = set()
    healthy = unavailable = disabled = 0
    for item in config["agents"]:
        if not isinstance(item, dict) or set(item) - {"agent_id", "health_url", "enabled"}:
            raise ValueError("each configured agent must contain only agent_id, health_url and optional enabled")
        agent_id = item.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 160 or agent_id in seen:
            raise ValueError("agent_id must be unique non-empty text of at most 160 characters")
        seen.add(agent_id)
        url = _validate_endpoint(item.get("health_url"))
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        if not enabled:
            disabled += 1
            continue
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Muse-Watchdog/1"}, method="GET")
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise ValueError("health endpoint returned a non-200 response")
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("health response exceeds 8 KiB")
            payload = json.loads(body.decode("utf-8"))
            if (not isinstance(payload, dict) or payload.get("status") != "ok"
                    or payload.get("agent_id") != agent_id or not isinstance(payload.get("role"), str)
                    or not isinstance(payload.get("capabilities", []), list)
                    or not all(isinstance(value, str) for value in payload.get("capabilities", []))):
                raise ValueError("health endpoint identity/status mismatch")
            healthy += 1

        except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            unavailable += 1
    return {
        "status": "healthy" if unavailable == 0 else "warning",
        "configured": len(config["agents"]), "probed": healthy + unavailable,
        "healthy": healthy, "unavailable": unavailable, "disabled": disabled,
        "executed_programs": 0,
    }


def discover_configured_agents(config_path: str | Path, registry: AgentRegistry,
                               *, timeout_seconds: float = 2.0) -> dict[str, object]:
    """Probe only configured loopback health URLs and register valid responders."""
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 3.0:
        raise ValueError("timeout_seconds must be between 0.1 and 3.0")
    path = Path(config_path)
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("discovery config exceeds 32 KiB")
    config = json.loads(raw.decode("utf-8"))
    if not isinstance(config, dict) or set(config) != {"agents"} or not isinstance(config["agents"], list):
        raise ValueError("discovery config must contain only an agents array")
    if len(config["agents"]) > MAX_ENDPOINTS:
        raise ValueError("discovery config may contain at most 32 agents")
    opener = build_opener(ProxyHandler({}), _NoRedirect)
    discovered: list[str] = []
    unavailable: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in config["agents"]:
        if not isinstance(item, dict) or set(item) - {"agent_id", "health_url", "enabled"}:
            raise ValueError("each configured agent must contain only agent_id, health_url and optional enabled")
        agent_id = item.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 160 or agent_id in seen:
            raise ValueError("agent_id must be unique non-empty text of at most 160 characters")
        seen.add(agent_id)
        url = _validate_endpoint(item.get("health_url"))
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Muse-Agent-Discovery/1"}, method="GET")
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise ValueError("health endpoint returned a non-200 response")
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("health response exceeds 8 KiB")
            payload = json.loads(body.decode("utf-8"))
            if (not isinstance(payload, dict) or payload.get("status") != "ok"
                    or payload.get("agent_id") != agent_id):
                raise ValueError("health response identity/status mismatch")
            role = payload.get("role")
            capabilities = payload.get("capabilities", [])
            display_name = payload.get("display_name", agent_id)
            if not isinstance(role, str) or not isinstance(capabilities, list) or not all(isinstance(v, str) for v in capabilities):
                raise ValueError("health response metadata is invalid")
            # Endpoint-provided capabilities are descriptive metadata only; they do not grant tool permissions.
            registry.register(agent_id, role=role, capabilities=capabilities,
                              display_name=display_name, source="local:http-health", enabled=enabled)
            if enabled:
                registry.heartbeat(agent_id)
            discovered.append(agent_id)
        except (OSError, HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            unavailable.append({"agent_id": agent_id, "reason": type(exc).__name__})
    return {"discovered": discovered, "unavailable": unavailable,
            "probed": len(config["agents"]), "executed_programs": 0}


def run_discovery_loop(
    config_path: str | Path, registry: AgentRegistry, *, interval_seconds: float = 30.0,
    stop_event: Event | None = None,
) -> dict[str, object]:
    """Refresh configured Agent health until the caller signals shutdown."""
    if (isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float))
            or not 0.1 <= interval_seconds <= 3600.0):
        raise ValueError("interval_seconds must be between 0.1 and 3600")
    stop = stop_event or Event()
    cycles = 0
    last_result: dict[str, object] = {}
    while not stop.is_set():
        last_result = discover_configured_agents(config_path, registry)
        cycles += 1
        if stop.wait(float(interval_seconds)):
            break
    return {"cycles": cycles, "last_result": last_result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe explicitly configured loopback Agent health endpoints")
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", default="data/agents/muse.sqlite3")
    parser.add_argument("--loop", action="store_true", help="repeat health checks until Ctrl+C")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    with AgentRegistry(args.db) as registry:
        if args.loop:
            try:
                result = run_discovery_loop(args.config, registry, interval_seconds=args.interval_seconds)
            except KeyboardInterrupt:
                result = {"stopped": True}
        else:
            result = discover_configured_agents(args.config, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
