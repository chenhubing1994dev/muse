"""Bounded HTTP adapter for explicitly configured loopback Agent services."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_REQUEST_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class LocalAgentError(RuntimeError):
    """Raised when a configured local Agent cannot complete a bounded call."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_invoke_url(url: object) -> str:
    if not isinstance(url, str) or len(url) > 512:
        raise ValueError("invoke_url must be text of at most 512 characters")
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invoke_url contains an invalid port") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or port is None or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path != "/api/v1/invoke"):
        raise ValueError("invoke_url must be an explicit loopback HTTP /api/v1/invoke endpoint with a port")
    return url


class LocalHTTPAgentAdapter:
    """Callable adapter for one fixed local Agent endpoint; no URL is accepted per call."""

    def __init__(self, agent_id: str, invoke_url: str, *, timeout_seconds: float = 20.0) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id.strip()) > 160:
            raise ValueError("agent_id must be non-empty text of at most 160 characters")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 0.1 <= timeout_seconds <= 30.0):
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        self.agent_id = agent_id.strip()
        self.invoke_url = _validate_invoke_url(invoke_url)
        self.timeout_seconds = float(timeout_seconds)
        self._opener = build_opener(ProxyHandler({}), _NoRedirect)

    def __call__(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty text")
        body = json.dumps({"agent_id": self.agent_id, "prompt": prompt}, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("Agent request exceeds 32 KiB")
        request = Request(self.invoke_url, data=body, headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "Muse-Local-Agent/1",
        }, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise LocalAgentError("Agent endpoint returned a non-200 response")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            raise LocalAgentError("Local Agent request failed: HTTPError") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise LocalAgentError(f"Local Agent request failed: {type(exc).__name__}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LocalAgentError("Agent response exceeds 64 KiB")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalAgentError("Agent response must be valid UTF-8 JSON") from exc
        if (not isinstance(payload, dict) or payload.get("agent_id") != self.agent_id
                or not isinstance(payload.get("output"), str) or not payload["output"].strip()):
            raise LocalAgentError("Agent response identity or output is invalid")
        return payload["output"]
