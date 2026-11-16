"""OpenAI-compatible local model adapter (for LM Studio and compatible servers)."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any


class ModelError(RuntimeError):
    """Raised when a local model endpoint cannot complete a request."""


class LMStudioClient:
    """Small standard-library client for LM Studio's OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        *,
        model: str | None = None,
        timeout: float = 60.0,
        chat_template_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if chat_template_kwargs is not None and not isinstance(chat_template_kwargs, dict):
            raise TypeError("chat_template_kwargs must be a dictionary")
        self.chat_template_kwargs = dict(chat_template_kwargs) if chat_template_kwargs is not None else None

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models")
        return payload.get("data", [])
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
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
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("The local model returned an unexpected chat response") from exc

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"Local model request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise ModelError("The local model endpoint returned a non-object response")
        return result
