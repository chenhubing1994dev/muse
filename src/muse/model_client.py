"""OpenAI-compatible local model adapter (for LM Studio and compatible servers)."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib import request as urllib_request
from urllib.parse import urlsplit
from typing import Any

from muse.model_call_context import record_current_model_usage


class ModelError(RuntimeError):
    """Raised when a local model endpoint cannot complete a request."""


class _RejectRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Never forward a local-model request or its prompt to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ModelChatResult:
    """Safe model response metadata; never contains the prompt or raw payload."""

    content: str
    model: str
    usage: dict[str, int]
    usage_recorded: bool = False


class LMStudioClient:
    """Small standard-library client for LM Studio's OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        *,
        model: str | None = None,
        timeout: float = 60.0,
        chat_template_kwargs: dict[str, Any] | None = None,
        bypass_proxies: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if chat_template_kwargs is not None and not isinstance(chat_template_kwargs, dict):
            raise TypeError("chat_template_kwargs must be a dictionary")
        self.chat_template_kwargs = dict(chat_template_kwargs) if chat_template_kwargs is not None else None
        if not isinstance(bypass_proxies, bool):
            raise TypeError("bypass_proxies must be a boolean")
        self._opener = None
        if bypass_proxies:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname
            try:
                is_loopback = host is not None and (
                    host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
                )
            except ValueError:
                is_loopback = False
            if (
                parsed.scheme != "http" or not is_loopback
                or parsed.username or parsed.password or parsed.query or parsed.fragment
            ):
                raise ValueError("proxy bypass is restricted to credential-free HTTP loopback model URLs")
            self._opener = urllib_request.build_opener(
                urllib_request.ProxyHandler({}), _RejectRedirectHandler(),
            )

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models")
        return payload.get("data", [])
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        usage_event_id: str | None = None,
    ) -> str:
        return self.chat_with_usage(
            messages, temperature=temperature, max_tokens=max_tokens,
            usage_event_id=usage_event_id,
        ).content

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        usage_event_id: str | None = None,
    ) -> ModelChatResult:
        """Return content plus provider-reported usage when the endpoint supplies it."""
        if not messages:
            raise ValueError("messages must not be empty")
        if not self.model:
            models = self.list_models()
            if not models:
                raise ModelError("No model is currently exposed by the local endpoint")
            model = models[0]["id"]
        else:
            model = self.model
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self.chat_template_kwargs is not None:
            body["chat_template_kwargs"] = self.chat_template_kwargs
        payload = self._request("POST", "/chat/completions", body)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("The local model returned an unexpected chat response") from exc
        if not isinstance(content, str):
            raise ModelError("The local model returned non-text chat content")
        response_model = payload.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = model
        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        safe_usage = {
            key: value for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        usage_recorded = record_current_model_usage(
            "lm-studio-local", response_model, safe_usage, event_id=usage_event_id
        )
        return ModelChatResult(
            content=content, model=response_model, usage=safe_usage,
            usage_recorded=usage_recorded,
        )
    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib_request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            opener = self._opener.open if self._opener is not None else urllib_request.urlopen
            with opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"Local model request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise ModelError("The local model endpoint returned a non-object response")
        return result
