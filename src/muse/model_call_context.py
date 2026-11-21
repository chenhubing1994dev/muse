"""Task-scoped model-call attribution for local provider telemetry."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import threading
import uuid
from typing import Iterator

from muse.usage_ledger import UsageLedger

_LOG = logging.getLogger(__name__)


class ModelCallContext:
    """Non-secret attribution metadata active while a task handler is executing."""

    __slots__ = ("ledger_path", "task_id", "agent_id", "project_id", "_ledger", "_lock")

    def __init__(
        self,
        ledger_path: Path,
        *,
        task_id: str | None,
        agent_id: str | None,
        project_id: str | None,
    ) -> None:
        self.ledger_path = ledger_path
        self.task_id = task_id
        self.agent_id = agent_id
        self.project_id = project_id
        self._ledger: UsageLedger | None = None
        self._lock = threading.RLock()

    def record_usage(self, provider_id: str, model: str, **fields: object) -> None:
        with self._lock:
            if self._ledger is None:
                self._ledger = UsageLedger(self.ledger_path)
            self._ledger.record_usage(provider_id, model, **fields)

    def close(self) -> None:
        with self._lock:
            if self._ledger is not None:
                self._ledger.close()
                self._ledger = None

_CURRENT: ContextVar[ModelCallContext | None] = ContextVar(
    "muse_model_call_context", default=None
)


def current_model_call_context() -> ModelCallContext | None:
    return _CURRENT.get()


@contextmanager
def model_call_scope(
    database: str | Path,
    *,
    task_id: str | None,
    agent_id: str | None = None,
    project_id: str | None = "muse",
) -> Iterator[ModelCallContext]:
    ledger_path = Path(database).with_name("model_usage.sqlite3")
    context = ModelCallContext(
        ledger_path, task_id=task_id, agent_id=agent_id, project_id=project_id
    )
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
        context.close()


def record_current_model_usage(
    provider_id: str,
    model: str,
    usage: dict[str, int],
    *,
    event_id: str | None = None,
) -> bool:
    """Append safe token metadata for the active task; never raise into inference."""
    context = _CURRENT.get()
    if context is None:
        return False

    def safe_count(key: str) -> int | None:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    input_tokens = safe_count("prompt_tokens")
    output_tokens = safe_count("completion_tokens")
    total_tokens = safe_count("total_tokens")
    token_source = (
        "provider_reported"
        if any(value is not None for value in (input_tokens, output_tokens, total_tokens))
        else "not_reported"
    )
    try:
        context.record_usage(
            provider_id,
            model,
            token_source=token_source,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            task_id=context.task_id,
            agent_id=context.agent_id,
            project_id=context.project_id,
            request_id=event_id or uuid.uuid4().hex,
            event_id=event_id,
        )
        return True
    except Exception as exc:
        _LOG.error(
            "model usage event could not be persisted (provider=%s, error=%s)",
            provider_id,
            type(exc).__name__,
        )
        return False
