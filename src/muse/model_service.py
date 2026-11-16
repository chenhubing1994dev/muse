"""Safe health probing for Muse's configured local model service."""

from __future__ import annotations

import ipaddress
import time
from typing import Any
from urllib.parse import urlsplit

from muse.model_client import LMStudioClient, ModelError
from muse.ruflo_adapter import load_muse_model_config


def _is_loopback_model_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        _ = parsed.port
        if (parsed.scheme != "http" or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/v1")):
            return False
        return host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def probe_model_service(base_url: str, expected_model: str = "", *, timeout: float = 3.0) -> dict[str, Any]:
    """Probe only an explicit credential-free HTTP loopback OpenAI-compatible endpoint.

    The result intentionally omits endpoint host/port and raw exception text so it can
    safely be included in watchdog reports and reminder summaries.
    """
    if not isinstance(base_url, str) or not _is_loopback_model_url(base_url):
        return {"status": "endpoint_rejected", "provider": "local_openai_compatible",
                "model_configured": bool(expected_model), "model_available": False,
                "model_count": 0, "latency_ms": None}
    if not isinstance(expected_model, str):
        return {"status": "configuration_error", "provider": "local_openai_compatible",
                "model_configured": False, "model_available": False,
                "model_count": 0, "latency_ms": None}
    started = time.monotonic()
    try:
        models = LMStudioClient(base_url, timeout=max(0.1, min(float(timeout), 10.0))).list_models()
    except (ModelError, TypeError, ValueError):
        return {"status": "unavailable", "provider": "local_openai_compatible",
                "model_configured": bool(expected_model.strip()), "model_available": False,
                "model_count": 0, "latency_ms": round((time.monotonic() - started) * 1000, 2)}
    model_ids = [item.get("id") for item in models if isinstance(item, dict)
                 and isinstance(item.get("id"), str) and item.get("id")]
    available = not expected_model.strip() or expected_model in model_ids
    status = "healthy" if model_ids and available else "model_mismatch" if model_ids else "no_models"
    return {"status": status, "provider": "local_openai_compatible",
            "model_configured": bool(expected_model.strip()), "model_available": bool(model_ids) and available,
            "model_count": len(model_ids), "latency_ms": round((time.monotonic() - started) * 1000, 2)}


def probe_configured_model_service(workspace: str) -> dict[str, Any]:
    """Load Muse's non-secret model route and probe it without starting/stopping processes."""
    try:
        config = load_muse_model_config(workspace)
    except (OSError, ValueError):
        return {"status": "configuration_error", "provider": "unknown",
                "model_configured": False, "model_available": False,
                "model_count": 0, "latency_ms": None}
    provider = config.get("provider", "")
    if provider not in {"llama_cpp", "lmstudio"}:
        return {"status": "not_managed", "provider": provider or "unspecified",
                "model_configured": bool(config.get("model")), "model_available": None,
                "model_count": 0, "latency_ms": None}
    result = probe_model_service(config.get("base_url", ""), config.get("model", ""))
    result["provider"] = provider
    return result
