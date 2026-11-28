"""External agent adapters (T8): one protocol for Super Agent Party and dsh, Commander-gated.

Protocol (every adapter): ``health()`` · ``capabilities()`` · ``invoke(request, commander_ctx=…)`` ·
``timeout_seconds``. Invocation only happens inside a running Commander ticket (handler
``route.external_invoke``) that was approved by a human, because it may send data to third-party
model providers behind the gateway. Outbound requests pass an egress allowlist (fields + size)
and may only target loopback addresses.

Discovery (no guessing):
* dsh / Our Free Model — the vendored plugin README documents a local OpenAI-compatible gateway
  ``127.0.0.1:8326`` (Chat Completions) and the dsh web kernel on ``127.0.0.1:3099``. Health is a
  real loopback probe; nothing is sent anywhere else.
* Super Agent Party — no running instance, address or port was found in this repository; the
  adapter stays dormant (``not_configured``) until ``config/external_agents.json`` names one.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_EGRESS = {"allowed_fields": ["model", "messages", "max_tokens", "temperature"], "max_chars": 8000,
                  "allowed_roles": ["system", "user"]}
DISCOVERED_DEFAULTS: dict[str, dict[str, Any]] = {
    "dsh": {"enabled": False, "base_url": "http://127.0.0.1:8326", "health_path": "/v1/models",
            "invoke_path": "/v1/chat/completions", "timeout_seconds": 60,
            "kernel_url": "http://127.0.0.1:3099", "kernel_health_path": "/api/our-free-model/summary",
            "source": "dsh-our-free-model/README.md (documented local gateway 127.0.0.1:8326, kernel :3099)"},
    "super_agent_party": {"enabled": False, "base_url": "", "health_path": "/v1/models",
                          "invoke_path": "/v1/chat/completions", "timeout_seconds": 60,
                          "source": "no address found in repository; dormant until configured"},
}


class ExternalAgentError(RuntimeError):
    pass


class EgressDenied(PermissionError):
    """Outbound payload violates the egress allowlist."""


def _require_loopback(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK_HOSTS or parts.username or parts.password:
        raise ExternalAgentError(f"external agent URL must be plain http on loopback, got {url!r}")
    return url.rstrip("/")


def load_config(name: str, *, config_path: str | Path | None = None) -> dict[str, Any]:
    if name not in DISCOVERED_DEFAULTS:
        raise ExternalAgentError(f"unknown external agent {name!r}")
    cfg = dict(DISCOVERED_DEFAULTS[name])
    path = Path(config_path) if config_path else PROJECT_ROOT / "config" / "external_agents.json"
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8")).get(name) or {}
        if not isinstance(user, dict):
            raise ExternalAgentError(f"config entry for {name} must be an object")
        cfg.update({k: v for k, v in user.items() if k in cfg or k == "egress"})
        cfg["source"] = str(path)
    cfg["egress"] = {**DEFAULT_EGRESS, **(cfg.get("egress") or {})}
    if cfg.get("base_url"):
        cfg["base_url"] = _require_loopback(str(cfg["base_url"]))
    if cfg.get("kernel_url"):
        cfg["kernel_url"] = _require_loopback(str(cfg["kernel_url"]))
    return cfg


def _http_json(url: str, *, data: dict[str, Any] | None = None, timeout: float = 3.0) -> tuple[int, Any]:
    _require_loopback(url.split("/v1")[0].split("/api")[0])
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route loopback via a proxy
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(256 * 1024)
            return resp.status, json.loads(raw.decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def check_egress(request: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise EgressDenied("request must be an object")
    extra = sorted(set(request) - set(policy["allowed_fields"]))
    if extra:
        raise EgressDenied(f"fields not on the egress allowlist: {extra}")
    messages = request.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise EgressDenied("messages must be a non-empty list")
    total = 0
    for m in messages:
        if not isinstance(m, dict) or set(m) - {"role", "content"} or m.get("role") not in policy["allowed_roles"]:
            raise EgressDenied("each message may only contain an allowed role and text content")
        if not isinstance(m.get("content"), str):
            raise EgressDenied("message content must be text")
        total += len(m["content"])
    if total > int(policy["max_chars"]):
        raise EgressDenied(f"payload has {total} chars > egress limit {policy['max_chars']}")
    return {"fields": sorted(request), "chars": total}


class ExternalAgentAdapter:
    """Uniform adapter; instances differ only in configuration."""

    def __init__(self, name: str, *, config_path: str | Path | None = None) -> None:
        self.name = name
        self.config = load_config(name, config_path=config_path)
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 60)

    def capabilities(self) -> list[str]:
        return ["chat_completions"] if self.config.get("base_url") else []

    def health(self) -> dict[str, Any]:
        cfg = self.config
        if not cfg.get("base_url"):
            return {"adapter": self.name, "state": "not_configured", "detail": cfg["source"], "enabled": False}
        probes = {}
        for label, base, path in (("gateway", cfg.get("base_url"), cfg.get("health_path")),
                                  ("kernel", cfg.get("kernel_url"), cfg.get("kernel_health_path"))):
            if not base or not path:
                continue
            started = time.monotonic()
            try:
                status, _ = _http_json(base + path, timeout=2.5)
                probes[label] = {"url": base + path, "http": status, "ms": round((time.monotonic() - started) * 1000)}
            except (OSError, ValueError, ExternalAgentError) as exc:
                probes[label] = {"url": base + path, "error": f"{type(exc).__name__}: {exc}"[:160]}
        live = any(p.get("http") == 200 for p in probes.values())
        state = "live" if live else "unreachable"
        return {"adapter": self.name, "state": state, "enabled": bool(cfg.get("enabled")), "probes": probes,
                "detail": cfg["source"]}

    def invoke(self, request: dict[str, Any], *, commander_ctx: Any, workspace: str | Path) -> dict[str, Any]:
        from muse.commander_routes import require_commander_ticket
        require_commander_ticket(commander_ctx, handler="route.external_invoke",
                                 function=f"external_agents.{self.name}.invoke", workspace_root=workspace)
        if not self.config.get("enabled"):
            raise ExternalAgentError(f"{self.name} is not enabled in config/external_agents.json")
        if not self.config.get("base_url"):
            raise ExternalAgentError(f"{self.name} is not configured")
        egress = check_egress(request, self.config["egress"])
        status, body = _http_json(self.config["base_url"] + self.config["invoke_path"], data=request,
                                  timeout=self.timeout_seconds)
        if status != 200 or not isinstance(body, dict):
            raise ExternalAgentError(f"{self.name} returned HTTP {status}")
        return {"adapter": self.name, "http": status, "egress": egress, "response": body}


def adapters(*, config_path: str | Path | None = None) -> dict[str, ExternalAgentAdapter]:
    return {name: ExternalAgentAdapter(name, config_path=config_path) for name in DISCOVERED_DEFAULTS}


def request_external_invoke(workspace: str | Path, name: str, request: dict[str, Any], *,
                            request_key: str, config_path: str | Path | None = None) -> dict[str, Any]:
    """Create a high-risk Commander ticket for one external call; it waits for a human approval."""
    import hashlib
    from muse.commander_routes import submit_and_wait
    adapter = ExternalAgentAdapter(name, config_path=config_path)
    check_egress(request, adapter.config["egress"])  # refuse early; re-checked again at execution time
    return submit_and_wait(
        workspace, handler="route.external_invoke",
        route_args={"adapter": name, "request": request, "config_path": str(config_path) if config_path else None},
        title=f"External agent call: {name}", objective=f"向外部智能体 {name} 外发数据（上传到第三方模型）",
        request_key=hashlib.sha256(f"{name}|{request_key}".encode("utf-8")).hexdigest(), risk_override="high")
