"""Optional adapter decorator that records safe model-usage metadata."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Any

from muse.providers import ChatRequest, ChatResponse, ProviderAdapter, ProviderDescriptor
from muse.usage_ledger import UsageLedger


_LOG = logging.getLogger(__name__)


class UsageRecordingAdapter:
    """Decorate a ProviderAdapter without changing its request/response contract.

    Constructor attribution remains the fallback for calls without a lower-level
    usage receipt. A successful lower-level ModelCallContext write is authoritative
    and suppresses duplicate decorator writes. This decorator never stores
    prompts, completions, credentials, or arbitrary response metadata.
    """

    def __init__(
        self,
        adapter: ProviderAdapter,
        ledger: UsageLedger,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._ledger = ledger
        self._task_id = task_id
        self._agent_id = agent_id
        self._project_id = project_id
        self._lock = threading.Lock()
        self._record_failures = 0
        self._last_record_error: str | None = None

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._adapter.descriptor

    def observer_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "record_failures": self._record_failures,
                "last_record_error": self._last_record_error,
            }

    def chat(self, request: ChatRequest) -> ChatResponse:
        response = self._adapter.chat(request)
        if getattr(response, "usage_recorded", False) is True:
            with self._lock:
                self._last_record_error = None
            return response
        usage = response.usage if isinstance(response.usage, dict) else {}
        def safe_count(value: object) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        input_tokens = safe_count(usage.get("prompt_tokens"))
        output_tokens = safe_count(usage.get("completion_tokens"))
        total_tokens = safe_count(usage.get("total_tokens"))
        token_source = (
            "provider_reported"
            if any(value is not None for value in (input_tokens, output_tokens, total_tokens))
            else "not_reported"
        )
        try:
            self._ledger.record_usage(
                response.provider_id,
                response.model,
                token_source=token_source,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                task_id=self._task_id,
                agent_id=self._agent_id,
                project_id=self._project_id,
            )
            with self._lock:
                self._last_record_error = None
            return replace(response, usage_recorded=True)
        except Exception as exc:
            # The model call already succeeded. Do not turn telemetry failure
            # into a retryable inference failure that could duplicate spend.
            with self._lock:
                self._record_failures += 1
                self._last_record_error = type(exc).__name__
            _LOG.error(
                "model usage event could not be persisted (provider=%s, error=%s)",
                response.provider_id,
                type(exc).__name__,
            )
        return response
