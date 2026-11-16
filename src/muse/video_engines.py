"""Safe loopback-only discovery clients for ComfyUI and Velorn MCP.

The adapter exposes discovery/health first. It does not install models or nodes,
submit ComfyUI workflows, or perform timeline edits unless a later explicit,
allowlisted and approved call is made.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 1_048_576
MCP_PROTOCOL_VERSION = "2024-11-05"
# Velorn currently omits MCP readOnlyHint annotations from tools/list.
# Only these exact tools are documented as read-only by Velorn; unknown tools
# remain write-capable and require both approval and an explicit allowlist.
VELORN_DOCUMENTED_READ_ONLY_TOOLS = frozenset({
    "get_project", "list_recent_projects", "list_glsl_effects", "get_mcp_recipes",
    "get_timeline", "check_media_health", "analyze_timeline",
    "check_export_readiness", "inspect_visible_shots", "inspect_timeline_range",
    "inspect_clip", "inspect_timeline_frame", "list_velorn_workflows",
    "get_generation_status",
})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_loopback_url(url: str, *, allow_path: bool) -> tuple[str, str]:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("endpoint must be non-empty text")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("video engine endpoint must use HTTP loopback only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint credentials, query and fragment are not allowed")
    if not allow_path and parsed.path not in {"", "/"}:
        raise ValueError("base endpoint path is not allowed")
    origin = f"http://{parsed.netloc}"
    path = parsed.path.rstrip("/") if allow_path else ""
    return origin, path


class LocalHTTPJSONClient:
    """Bounded JSON HTTP client pinned to loopback and with redirects disabled."""

    def __init__(
        self, base_url: str, *, timeout_seconds: float = 3.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        requester: Callable[..., Any] | None = None,
    ) -> None:
        self.origin, _ = _validate_loopback_url(base_url, allow_path=False)
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        if not 1024 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("max_response_bytes is outside the supported range")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._requester = requester

    def request_json(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        if method not in {"GET", "POST"} or not path.startswith("/") or path.startswith("//"):
            raise ValueError("unsupported local HTTP request")
        url = self.origin + path
        request_headers = {"Accept": "application/json, text/event-stream"}
        request_headers.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(data) > self.max_response_bytes:
                raise ValueError("local video engine request exceeded the size limit")
            request_headers["Content-Type"] = "application/json"
        if self._requester is not None:
            status, response_headers, body = self._requester(
                url, method, data, request_headers, self.timeout_seconds, self.max_response_bytes
            )
        else:
            request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
            opener = urllib.request.build_opener(_NoRedirect())
            try:
                with opener.open(request, timeout=self.timeout_seconds) as response:
                    status = response.status
                    response_headers = dict(response.headers.items())
                    body = response.read(self.max_response_bytes + 1)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"local video engine returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise RuntimeError("local video engine is unavailable") from None
        if not 200 <= status < 300:
            raise RuntimeError(f"local video engine returned HTTP {status}")
        if not isinstance(body, (bytes, bytearray)):
            raise RuntimeError("local video engine returned an invalid response body")
        if len(body) > self.max_response_bytes:
            raise RuntimeError("local video engine response exceeded the size limit")
        if not body:
            return status, dict(response_headers), {}
        content_type = next((v for k, v in response_headers.items()
                             if k.casefold() == "content-type"), "")
        try:
            if "text/event-stream" in content_type:
                decoded = None
                for line in bytes(body).decode("utf-8").splitlines():
                    if line.startswith("data:"):
                        candidate = json.loads(line[5:].strip())
                        if isinstance(candidate, dict) and ("id" in candidate or "error" in candidate):
                            decoded = candidate
                if decoded is None:
                    raise ValueError("no JSON-RPC message in event stream")
                result = decoded
            else:
                result = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise RuntimeError("local video engine returned malformed JSON") from None
        if not isinstance(result, dict):
            raise RuntimeError("local video engine returned a non-object JSON response")
        return status, dict(response_headers), result


def _validate_workflow_value(value: Any, *, depth: int = 0) -> None:
    """Bound JSON values embedded in a workflow before any network request."""
    if depth > 12:
        raise ValueError("workflow input nesting exceeds 12 levels")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not (value == value and abs(value) != float("inf")):
            raise ValueError("workflow contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > 32_768:
            raise ValueError("workflow string value exceeds 32 KiB")
        return
    if isinstance(value, list):
        if len(value) > 512:
            raise ValueError("workflow list exceeds 512 items")
        for item in value:
            _validate_workflow_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 128:
            raise ValueError("workflow object exceeds 128 fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("workflow object contains an invalid key")
            _validate_workflow_value(item, depth=depth + 1)
        return
    raise ValueError("workflow contains a non-JSON value")


def validate_workflow_graph(
    workflow: dict[str, Any], *, node_allowlist: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Validate a bounded ComfyUI API graph without contacting the engine."""
    if not isinstance(workflow, dict) or not 1 <= len(workflow) <= 256:
        raise ValueError("workflow must contain between 1 and 256 nodes")
    if (not isinstance(node_allowlist, (set, frozenset)) or not node_allowlist
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", name)
                   for name in node_allowlist)):
        raise ValueError("a non-empty valid explicit node allowlist is required")
    used_nodes: set[str] = set()
    for node_id, node in workflow.items():
        if (not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node_id)
                or not isinstance(node, dict)
                or set(node) - {"class_type", "inputs", "_meta"}
                or not isinstance(node.get("class_type"), str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", node["class_type"])
                or not isinstance(node.get("inputs"), dict)):
            raise ValueError("workflow contains an invalid node or input shape")
        if "_meta" in node and not isinstance(node["_meta"], dict):
            raise ValueError("workflow node _meta must be an object")
        _validate_workflow_value(node["inputs"])
        if "_meta" in node:
            _validate_workflow_value(node["_meta"])
        used_nodes.add(node["class_type"])
    if not used_nodes.issubset(node_allowlist):
        raise PermissionError("workflow contains node types outside the explicit allowlist")
    try:
        encoded_size = len(json.dumps({"prompt": workflow}, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ValueError("workflow is not finite JSON") from None
    if encoded_size > MAX_RESPONSE_BYTES:
        raise ValueError("workflow exceeds the configured request size limit")
    return {"node_count": len(workflow), "used_nodes": sorted(used_nodes), "encoded_bytes": encoded_size}


class ComfyUIClient:
    def __init__(self, endpoint: str = "http://127.0.0.1:8188", **client_options: Any) -> None:
        self.client = LocalHTTPJSONClient(endpoint, **client_options)

    def object_info_summary(self, required_nodes: list[str] | None = None) -> dict[str, Any]:
        """Check named node classes through the bounded ComfyUI per-node endpoint."""
        if required_nodes is None:
            required_nodes = []
        if (not isinstance(required_nodes, list) or len(required_nodes) > 128
                or any(not isinstance(name, str)
                       or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", name)
                       for name in required_nodes)):
            raise ValueError("required_nodes must be a list of at most 128 safe node names")
        available: list[str] = []
        missing: list[str] = []
        for name in dict.fromkeys(required_nodes):
            _, _, info = self.client.request_json("GET", f"/object_info/{name}")
            if name in info and isinstance(info[name], dict):
                available.append(name)
            else:
                missing.append(name)
        return {
            "node_count": None,
            "available_required_nodes": sorted(available),
            "missing_required_nodes": sorted(missing),
        }

    def submit_workflow(
        self, workflow: dict[str, Any], *, node_allowlist: set[str] | frozenset[str],
        approved: bool = False,
    ) -> dict[str, Any]:
        """Submit a validated ComfyUI API graph only after explicit approval."""
        if approved is not True:
            raise PermissionError("ComfyUI generation requires explicit approval")
        summary = validate_workflow_graph(workflow, node_allowlist=node_allowlist)
        used_nodes = set(summary["used_nodes"])
        inventory = self.object_info_summary(sorted(used_nodes))
        if inventory["missing_required_nodes"]:
            raise ValueError(
                "ComfyUI is missing required node types: "
                + ", ".join(inventory["missing_required_nodes"])
            )
        _, _, result = self.client.request_json("POST", "/prompt", payload={"prompt": workflow})
        prompt_id = result.get("prompt_id")
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
            raise RuntimeError("ComfyUI did not return a valid prompt_id")
        number = result.get("number")
        return {
            "status": "queued",
            "prompt_id": prompt_id,
            "queue_number": number if isinstance(number, int) and not isinstance(number, bool) else None,
            "node_errors": result.get("node_errors") if isinstance(result.get("node_errors"), dict) else {},
        }

    def history(self, prompt_id: str) -> dict[str, Any]:
        """Read one ComfyUI prompt history entry; never submits or changes work."""
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
            raise ValueError("prompt_id has an invalid format")
        _, _, history = self.client.request_json("GET", f"/history/{prompt_id}")
        return history

    def queue_status(self) -> dict[str, Any]:
        """Read queue counts without exposing queued workflow payloads."""
        _, _, queue = self.client.request_json("GET", "/queue")
        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])
        return {
            "running_jobs": len(running) if isinstance(running, list) else None,
            "pending_jobs": len(pending) if isinstance(pending, list) else None,
        }

    def free_models(self) -> dict[str, Any]:
        """Ask an idle ComfyUI instance to unload inference models and cached GPU allocations."""
        self.client.request_json(
            "POST", "/free", payload={"unload_models": True, "free_memory": True},
        )
        return {"status": "requested"}

    def gpu_memory(self) -> dict[str, int | float]:
        """Read device VRAM counters from ComfyUI system_stats without guessing missing values."""
        _, _, stats = self.client.request_json("GET", "/system_stats")
        devices = stats.get("devices")
        if not isinstance(devices, list) or not devices:
            raise RuntimeError("ComfyUI did not report GPU memory devices")
        total_bytes = 0
        free_bytes = 0
        torch_total_bytes = 0
        torch_free_bytes = 0
        torch_reported = False
        for device in devices:
            if not isinstance(device, dict):
                continue
            total, free = device.get("vram_total"), device.get("vram_free")
            if (isinstance(total, bool) or not isinstance(total, int) or total <= 0
                    or isinstance(free, bool) or not isinstance(free, int) or not 0 <= free <= total):
                continue
            total_bytes += total
            free_bytes += free
            torch_total = device.get("torch_vram_total")
            value = device.get("torch_vram_free")
            if (isinstance(torch_total, int) and not isinstance(torch_total, bool) and torch_total > 0
                    and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= torch_total):
                torch_total_bytes += torch_total
                torch_free_bytes += value
                torch_reported = True
        if total_bytes <= 0:
            raise RuntimeError("ComfyUI GPU memory counters are missing or invalid")
        result: dict[str, int | float] = {
            "vram_total_bytes": total_bytes, "vram_free_bytes": free_bytes,
            "vram_free_ratio": round(free_bytes / total_bytes, 4),
        }
        if torch_reported:
            result["torch_vram_total_bytes"] = torch_total_bytes
            result["torch_vram_free_bytes"] = torch_free_bytes
        return result

    def health(self) -> dict[str, Any]:
        try:
            _, _, stats = self.client.request_json("GET", "/system_stats")
            _, _, queue = self.client.request_json("GET", "/queue")
            running = queue.get("queue_running", [])
            pending = queue.get("queue_pending", [])
            return {
                "status": "ready", "device_count": len(stats.get("devices", [])),
                "running_jobs": len(running) if isinstance(running, list) else None,
                "pending_jobs": len(pending) if isinstance(pending, list) else None,
            }
        except (RuntimeError, ValueError, TypeError) as exc:
            return {"status": "unavailable", "reason": str(exc)}


class VelornMCPClient:
    """MCP discovery plus approval-gated calls to explicitly allowlisted write tools."""

    def __init__(
        self, endpoint: str = "http://127.0.0.1:19790/mcp", *,
        write_allowlist: set[str] | frozenset[str] = frozenset(),
        timeout_seconds: float = 3.0, max_response_bytes: int = MAX_RESPONSE_BYTES,
        requester: Callable[..., Any] | None = None,
    ) -> None:
        origin, path = _validate_loopback_url(endpoint, allow_path=True)
        if not path:
            raise ValueError("Velorn MCP endpoint path is required")
        self.path = path
        self.client = LocalHTTPJSONClient(
            origin, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes,
            requester=requester,
        )
        self.write_allowlist = frozenset(write_allowlist)
        self.session_id: str | None = None
        self._request_id = 0
        self._tools: dict[str, dict[str, Any]] = {}

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._request_id += 1
        request_id = self._request_id
        payload["id"] = request_id
        _, _, response = self.client.request_json(
            "POST", self.path, payload=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise RuntimeError("Velorn MCP response ID or JSON-RPC version did not match")
        if response.get("error"):
            raise RuntimeError("Velorn MCP returned a JSON-RPC error")
        return response

    def list_tools(self) -> list[dict[str, Any]]:
        # Velorn documents direct JSON-RPC tools/list without a separate
        # initialize/notifications handshake; keep this compatible with its
        # local MCP endpoint rather than assuming a generic session transport.
        response = self._rpc("tools/list", {})
        result = response.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise RuntimeError("Velorn MCP tools/list response is invalid")
        normalized = []
        for item in tools:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            annotations = item.get("annotations")
            if not isinstance(annotations, dict):
                annotations = {}
            normalized.append({
                "name": item["name"],
                "description": str(item.get("description", ""))[:500],
                "read_only": (
                    annotations.get("readOnlyHint") is True
                    or ("readOnlyHint" not in annotations
                        and item["name"] in VELORN_DOCUMENTED_READ_ONLY_TOOLS)
                ),
            })
        self._tools = {item["name"]: item for item in normalized}
        return normalized

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False,
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise PermissionError("Velorn tool must be discovered before it can be called")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if not tool["read_only"] and (approved is not True or name not in self.write_allowlist):
            raise PermissionError("Velorn write tool requires explicit approval and a named allowlist entry")
        response = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Velorn tools/call response is invalid")
        return result

    def health(self) -> dict[str, Any]:
        try:
            tools = self.list_tools()
            return {
                "status": "ready", "protocol_version": MCP_PROTOCOL_VERSION,
                "tool_count": len(tools), "read_only_tools": sum(1 for item in tools if item["read_only"]),
            }
        except (RuntimeError, ValueError, PermissionError) as exc:
            return {"status": "unavailable", "reason": str(exc)}

def inspect_video_engines(
    *, comfyui_endpoint: str = "http://127.0.0.1:8188",
    velorn_endpoint: str = "http://127.0.0.1:19790/mcp",
    timeout_seconds: float = 1.0,
) -> dict[str, Any]:
    """Read-only engine discovery; never launches either service."""
    return {
        "comfyui": ComfyUIClient(comfyui_endpoint, timeout_seconds=timeout_seconds).health(),
        "velorn": VelornMCPClient(velorn_endpoint, timeout_seconds=timeout_seconds).health(),
    }
