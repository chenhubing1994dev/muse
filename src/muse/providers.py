"""Provider-neutral chat contracts, capability routing and adapter registry.

Cloud adapters are fail-closed: callers must supply a matching, fresh, verified
free budget. External providers are preferred; local inference is opt-in only.
"""

from __future__ import annotations

import threading
import time
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from muse.provider_policy import FreeOnlyRouter, ProviderBudget
from muse.model_client import LMStudioClient

@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    display_name: str
    is_local: bool
    supports_chat: bool = True
    supports_streaming: bool = False
    requires_verified_free_quota: bool = True
    capabilities: frozenset[str] = frozenset({"chat"})
    data_policy: str = "unspecified"  # local_only | no_training | may_train | unspecified
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.capabilities or any(not item.strip() for item in self.capabilities):
            raise ValueError("capabilities must contain non-empty values")
        if self.data_policy not in {"local_only", "no_training", "may_train", "unspecified"}:
            raise ValueError("invalid data_policy")
        if isinstance(self.max_concurrency, bool) or not isinstance(self.max_concurrency, int) or self.max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if self.is_local and self.requires_verified_free_quota:
            object.__setattr__(self, "requires_verified_free_quota", False)
        if self.is_local and self.data_policy == "unspecified":
            object.__setattr__(self, "data_policy", "local_only")


@dataclass
class ProviderRuntimeMetrics:
    in_flight: int = 0
    successes: int = 0
    failures: int = 0
    average_latency_ms: float | None = None
    last_error: str | None = None
    circuit_open_until: float = 0.0

    @property
    def healthy(self) -> bool:
        return self.circuit_open_until <= time.monotonic()


@dataclass(frozen=True)
class ChatRequest:
    messages: list[dict[str, str]]
    temperature: float = 0.7
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("messages must not be empty")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")


@dataclass(frozen=True)
class ChatResponse:
    provider_id: str
    model: str
    content: str
    usage: dict[str, int] = field(default_factory=dict)
    usage_recorded: bool = False


@runtime_checkable
class ProviderAdapter(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def chat(self, request: ChatRequest) -> ChatResponse: ...


class ProviderMetricsStore:
    """SQLite persistence for aggregate provider health metrics (never prompts or responses)."""

    def __init__(self, database: str | Path) -> None:
        import sqlite3
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database, timeout=10, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS provider_runtime_metrics (
                provider_id TEXT PRIMARY KEY,
                successes INTEGER NOT NULL,
                failures INTEGER NOT NULL,
                average_latency_ms REAL,
                last_error TEXT,
                circuit_open_until_epoch REAL NOT NULL DEFAULT 0
            )"""
        )
        self._connection.commit()
        self._lock = threading.RLock()

    def load(self, provider_id: str) -> ProviderRuntimeMetrics:
        with self._lock:
            row = self._connection.execute(
                "SELECT successes, failures, average_latency_ms, last_error, circuit_open_until_epoch FROM provider_runtime_metrics WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
        if row is None:
            return ProviderRuntimeMetrics()
        remaining = max(0.0, float(row[4]) - time.time())
        return ProviderRuntimeMetrics(
            successes=int(row[0]), failures=int(row[1]),
            average_latency_ms=None if row[2] is None else float(row[2]),
            last_error=row[3], circuit_open_until=time.monotonic() + remaining,
        )

    def save(self, provider_id: str, metric: ProviderRuntimeMetrics) -> None:
        # Monotonic deadlines cannot survive process restarts; persist their wall-clock equivalent.
        circuit_epoch = time.time() + max(0.0, metric.circuit_open_until - time.monotonic())
        with self._lock:
            self._connection.execute(
                """INSERT INTO provider_runtime_metrics
                   (provider_id, successes, failures, average_latency_ms, last_error, circuit_open_until_epoch)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                     successes=excluded.successes, failures=excluded.failures,
                     average_latency_ms=excluded.average_latency_ms, last_error=excluded.last_error,
                     circuit_open_until_epoch=excluded.circuit_open_until_epoch""",
                (provider_id, metric.successes, metric.failures, metric.average_latency_ms, metric.last_error, circuit_epoch),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class ProviderRegistry:
    """Explicit adapter registry with measured load-aware selection and optional durable metrics."""
    def __init__(
        self, *, circuit_breaker_threshold: int = 3, circuit_cooldown_seconds: float = 30.0,
        metrics_database: str | Path | None = None,
    ) -> None:
        if circuit_breaker_threshold < 1 or circuit_cooldown_seconds <= 0:
            raise ValueError("invalid circuit breaker configuration")
        self._adapters: dict[str, ProviderAdapter] = {}
        self._metrics: dict[str, ProviderRuntimeMetrics] = {}
        self._lock = threading.RLock()
        self._quota_router = FreeOnlyRouter()
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_cooldown_seconds = circuit_cooldown_seconds
        self._metrics_store = ProviderMetricsStore(metrics_database) if metrics_database is not None else None

    def close(self) -> None:
        if self._metrics_store is not None:
            self._metrics_store.close()

    def register(self, adapter: ProviderAdapter) -> None:
        descriptor = adapter.descriptor
        if not descriptor.supports_chat:
            raise ValueError("adapter does not support chat")
        with self._lock:
            if descriptor.provider_id in self._adapters:
                raise ValueError(f"provider already registered: {descriptor.provider_id}")
            self._adapters[descriptor.provider_id] = adapter
            self._metrics[descriptor.provider_id] = (
                self._metrics_store.load(descriptor.provider_id)
                if self._metrics_store is not None else ProviderRuntimeMetrics()
            )
    def get(self, provider_id: str) -> ProviderAdapter:
        with self._lock:
            try:
                return self._adapters[provider_id]
            except KeyError as exc:
                raise KeyError(f"unknown provider: {provider_id}") from exc

    def list_descriptors(self) -> list[ProviderDescriptor]:
        with self._lock:
            return [adapter.descriptor for adapter in self._adapters.values()]

    def runtime_snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                provider_id: {
                    "in_flight": metric.in_flight,
                    "successes": metric.successes,
                    "failures": metric.failures,
                    "average_latency_ms": metric.average_latency_ms,
                    "healthy": metric.healthy,
                    "last_error": metric.last_error,
                }
                for provider_id, metric in self._metrics.items()
            }

    def select_for_task(
        self,
        required_capabilities: set[str] | frozenset[str],
        *,
        data_class: str = "public",
        budgets: dict[str, ProviderBudget] | None = None,
        now=None,
        allow_local_inference: bool = False,
    ) -> ProviderAdapter:
        """Choose by capability/privacy/quota, then measured load and reliability.

        Metrics are observed only from calls through this registry. No synthetic
        health, latency, capacity or cost values are invented.
        """
        if data_class not in {"private", "sanitized", "public"}:
            raise ValueError("data_class must be private, sanitized or public")
        required = set(required_capabilities)
        if not required:
            raise ValueError("at least one required capability is needed")
        budgets = budgets or {}
        candidates = []
        with self._lock:
            adapters = list(self._adapters.values())
            for adapter in adapters:
                descriptor = adapter.descriptor
                metric = self._metrics[descriptor.provider_id]
                if descriptor.is_local and not allow_local_inference:
                    continue
                if not required.issubset(descriptor.capabilities):
                    continue
                if data_class == "private" and not descriptor.is_local:
                    continue
                if data_class == "sanitized" and not descriptor.is_local and descriptor.data_policy != "no_training":
                    continue
                if metric.in_flight >= descriptor.max_concurrency or not metric.healthy:
                    continue
                if descriptor.requires_verified_free_quota:
                    budget = budgets.get(descriptor.provider_id)
                    if budget is None:
                        continue
                    try:
                        self._quota_router.select([budget], now=now)
                    except Exception:
                        continue
                latency = metric.average_latency_ms if metric.average_latency_ms is not None else float("inf")
                failure_rate = metric.failures / max(1, metric.successes + metric.failures)
                utilization = metric.in_flight / descriptor.max_concurrency
                candidates.append(((utilization, failure_rate, latency, descriptor.is_local), adapter))
        if not candidates:
            raise LookupError("no provider satisfies capability, privacy, free-quota, health and capacity requirements")
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def chat(
        self,
        provider_id: str,
        request: ChatRequest,
        *,
        budget: ProviderBudget | None = None,
    ) -> tuple[ChatResponse, ProviderBudget | None]:
        adapter = self.get(provider_id)
        descriptor = adapter.descriptor
        with self._lock:
            metric = self._metrics[provider_id]
            if metric.in_flight >= descriptor.max_concurrency:
                raise RuntimeError("provider concurrency capacity is exhausted")
            if not metric.healthy:
                raise RuntimeError("provider circuit breaker is open")
            metric.in_flight += 1
        started = time.monotonic()
        try:
            remaining = budget
            selected = budget
            if descriptor.requires_verified_free_quota:
                if budget is None and callable(getattr(adapter, "refresh_budget", None)):
                    budget = adapter.refresh_budget()
                if budget is None or budget.provider_id != provider_id:
                    raise ValueError("cloud adapters require a matching verified free-quota budget")
                selected = self._quota_router.select([budget])
                remaining = self._quota_router.consume(selected, 1)
            quota_chat = getattr(adapter, "chat_with_budget", None)
            if descriptor.requires_verified_free_quota and callable(quota_chat):
                response = quota_chat(request, selected)
            else:
                response = adapter.chat(request)
            if response.provider_id != provider_id:
                raise ValueError("adapter response provider_id does not match registered provider")
            elapsed_ms = (time.monotonic() - started) * 1000
            with self._lock:
                metric = self._metrics[provider_id]
                metric.successes += 1
                metric.last_error = None
                metric.average_latency_ms = elapsed_ms if metric.average_latency_ms is None else (0.2 * elapsed_ms + 0.8 * metric.average_latency_ms)
                metric.circuit_open_until = 0.0
                if self._metrics_store is not None:
                    self._metrics_store.save(provider_id, metric)
            return response, remaining
        except Exception as exc:
            with self._lock:
                metric = self._metrics[provider_id]
                metric.failures += 1
                metric.last_error = type(exc).__name__
                if metric.failures - metric.successes >= self._circuit_breaker_threshold:
                    metric.circuit_open_until = time.monotonic() + self._circuit_cooldown_seconds
                if self._metrics_store is not None:
                    self._metrics_store.save(provider_id, metric)
            raise
        finally:
            with self._lock:
                self._metrics[provider_id].in_flight = max(0, self._metrics[provider_id].in_flight - 1)

class LMStudioAdapter:
    """Expose the existing local LM Studio client through the common contract."""
    def __init__(self, client: LMStudioClient) -> None:
        self.client = client
        self._descriptor = ProviderDescriptor(
            provider_id="lm-studio-local",
            display_name="LM Studio (local)",
            is_local=True,
            supports_chat=True,
            supports_streaming=False,
            requires_verified_free_quota=False,
            capabilities=frozenset({"chat", "reasoning", "coding", "research", "local_inference"}),
            data_policy="local_only",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def chat(self, request: ChatRequest) -> ChatResponse:
        detailed_chat = getattr(self.client, "chat_with_usage", None)
        if callable(detailed_chat):
            result = detailed_chat(
                request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            content, model, usage = result.content, result.model, result.usage
            usage_recorded = getattr(result, "usage_recorded", False) is True
        else:
            # Keep compatibility with injected test clients and legacy adapters.
            content = self.client.chat(
                request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            model, usage = self.client.model or "auto", {}
            usage_recorded = False
        return ChatResponse(
            provider_id=self.descriptor.provider_id,
            model=model,
            content=content,
            usage=usage,
            usage_recorded=usage_recorded,
        )