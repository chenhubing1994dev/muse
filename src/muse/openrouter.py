"""OpenRouter free-model adapter with a live, fail-closed quota check.

Only model IDs ending in ':free' are accepted. No paid model fallback list is
sent. Credentials are read from the process environment and never persisted.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from muse.provider_policy import NoFreeProviderAvailable, ProviderBudget
from muse.providers import ChatRequest, ChatResponse, ProviderDescriptor


class OpenRouterConfigurationError(RuntimeError):
    """The adapter cannot safely authenticate or call the provider."""


class OpenRouterFreeAdapter:
    BASE_URL = "https://openrouter.ai/api/v1"
    KEY_ENV = "OPENROUTER_API_KEY"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        opener: Callable = urllib.request.urlopen,
        timeout_seconds: int = 20,
    ) -> None:
        if not isinstance(model, str) or not model.strip() or not model.endswith(":free"):
            raise ValueError("OpenRouter adapter accepts only explicit :free model IDs")
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.model = model.strip()
        self._api_key = api_key if api_key is not None else os.environ.get(self.KEY_ENV, "")
        self._opener = opener
        self.timeout_seconds = timeout_seconds
        self._descriptor = ProviderDescriptor(
            provider_id="openrouter-free",
            display_name="OpenRouter free-model route",
            is_local=False,
            supports_chat=True,
            supports_streaming=False,
            requires_verified_free_quota=True,
            capabilities=frozenset({"chat", "reasoning", "coding", "research"}),
            data_policy="unspecified",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def _request_json(self, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
        if not self._api_key:
            raise OpenRouterConfigurationError(
                f"{self.KEY_ENV} is not configured; no provider request was sent"
            )
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.BASE_URL}{path}",
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://muse.local",
                "X-Title": "Muse Personal Agent",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise OpenRouterConfigurationError(
                f"OpenRouter request failed with HTTP {exc.code}; request was not retried"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OpenRouterConfigurationError(
                "OpenRouter connection failed; request was not retried"
            ) from None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenRouterConfigurationError("OpenRouter returned invalid JSON") from None
        if not isinstance(parsed, dict):
            raise OpenRouterConfigurationError("OpenRouter returned an unexpected response shape")
        return parsed

    def refresh_budget(self) -> ProviderBudget:
        """Read the account's live free-model request balance before inference."""
        data = self._request_json("/key").get("data")
        if not isinstance(data, dict):
            raise NoFreeProviderAvailable("OpenRouter quota response did not include account data")
        free_limits = data.get("free_model_daily_requests")
        if not isinstance(free_limits, dict):
            raise NoFreeProviderAvailable("OpenRouter did not expose a verifiable free-model daily quota")
        remaining = free_limits.get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
            raise NoFreeProviderAvailable("OpenRouter free-model remaining quota is unknown")
        credit_remaining = data.get("limit_remaining")
        if credit_remaining is not None and (
            not isinstance(credit_remaining, (int, float)) or credit_remaining < 0
        ):
            raise NoFreeProviderAvailable("OpenRouter account credit state blocks safe free-model routing")
        if remaining <= 0:
            raise NoFreeProviderAvailable("OpenRouter free-model daily quota is exhausted")
        return ProviderBudget(
            provider_id=self.descriptor.provider_id,
            enabled=True,
            free_tier_confirmed=True,
            quota_verified=True,
            remaining_units=remaining,
            unit_name="free_model_requests_per_utc_day",
            quota_checked_at=datetime.now(timezone.utc).isoformat(),
            quota_source=f"{self.BASE_URL}/key",
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        return self.chat_with_budget(request, self.refresh_budget())

    def chat_with_budget(self, request: ChatRequest, budget: ProviderBudget) -> ChatResponse:
        """Send one :free request using the just-verified quota snapshot."""
        if budget.provider_id != self.descriptor.provider_id or not budget.is_fresh():
            raise NoFreeProviderAvailable("OpenRouter quota snapshot is stale or belongs to another provider")
        if budget.remaining_units is None or budget.remaining_units < 1:
            raise NoFreeProviderAvailable("OpenRouter free-model daily quota is exhausted")
        body = {
            "model": self.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        result = self._request_json("/chat/completions", method="POST", body=body)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise OpenRouterConfigurationError("OpenRouter returned no chat choice")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OpenRouterConfigurationError("OpenRouter returned an unsupported message shape")
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        safe_usage = {
            key: value for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        return ChatResponse(
            provider_id=self.descriptor.provider_id,
            model=self.model,
            content=content,
            usage=safe_usage,
        )
