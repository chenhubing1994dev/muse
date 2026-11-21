"""Append-only, source-aware model usage ledger.

This module stores measurements and estimates separately. It deliberately does
not infer money from tokens, requests, latency, or workload duration.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


_TOKEN_SOURCES = {"provider_reported", "estimated", "not_reported"}
_COST_KINDS = {"actual", "estimated"}
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _optional_identifier(value: str | None, field: str, *, max_bytes: int = 256) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string when supplied")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} exceeds the {max_bytes}-byte limit")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in normalized):
        raise ValueError(f"{field} must not contain control characters")
    return normalized


def _token_count(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or None")
    return value


def _timestamp(value: str | datetime | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError("observed_at must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _cost_fields(
    amount: str | int | Decimal | None,
    currency: str | None,
    kind: str | None,
    source: str | None,
    price_version: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if amount is None and currency is None and kind is None and source is None and price_version is None:
        return None, None, None, None, None
    if amount is None or currency is None or kind is None or source is None:
        raise ValueError("cost amount, currency, kind and source must be supplied together")
    if isinstance(amount, float):
        raise ValueError("cost_amount must use a decimal string, integer or Decimal; floats are not accepted")
    try:
        parsed = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("cost_amount must be a finite non-negative decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("cost_amount must be a finite non-negative decimal")
    normalized_currency = currency.strip().upper() if isinstance(currency, str) else ""
    if not _CURRENCY_RE.fullmatch(normalized_currency):
        raise ValueError("cost_currency must be a three-letter ISO currency code")
    if not isinstance(kind, str) or kind not in _COST_KINDS:
        raise ValueError("cost_kind must be actual or estimated")
    normalized_source = _optional_identifier(source, "cost_source")
    normalized_version = _optional_identifier(price_version, "price_version", max_bytes=64)
    if kind == "estimated" and normalized_version is None:
        raise ValueError("estimated cost requires an explicit price_version")
    return format(parsed.normalize(), "f"), normalized_currency, kind, normalized_source, normalized_version


class UsageLedger:
    """SQLite append-only ledger for provider-reported and estimated model usage.

    Prompts, completions, credentials, and arbitrary provider response bodies are
    never accepted or stored by this API.
    """

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database, timeout=15, check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=15000")
        self._lock = threading.RLock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_usage_events (
                event_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model TEXT NOT NULL,
                request_id TEXT,
                task_id TEXT,
                agent_id TEXT,
                project_id TEXT,
                attribution_status TEXT NOT NULL
                    CHECK (attribution_status IN ('unattributed', 'partial', 'complete')),
                token_source TEXT NOT NULL
                    CHECK (token_source IN ('provider_reported', 'estimated', 'not_reported')),
                input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
                output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
                total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
                cost_amount TEXT,
                cost_currency TEXT,
                cost_kind TEXT CHECK (cost_kind IS NULL OR cost_kind IN ('actual', 'estimated')),
                cost_source TEXT,
                price_version TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_model_usage_observed
                ON model_usage_events(observed_at);
            CREATE INDEX IF NOT EXISTS idx_model_usage_agent_observed
                ON model_usage_events(agent_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_model_usage_task_observed
                ON model_usage_events(task_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_model_usage_project_observed
                ON model_usage_events(project_id, observed_at);
            CREATE TRIGGER IF NOT EXISTS model_usage_events_no_update
            BEFORE UPDATE ON model_usage_events
            BEGIN
                SELECT RAISE(ABORT, 'model usage ledger is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS model_usage_events_no_delete
            BEFORE DELETE ON model_usage_events
            BEGIN
                SELECT RAISE(ABORT, 'model usage ledger is append-only');
            END;
            """
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(model_usage_events)")}
        if "price_version" not in columns:
            self._connection.execute("ALTER TABLE model_usage_events ADD COLUMN price_version TEXT")

    def record_usage(
        self,
        provider_id: str,
        model: str,
        *,
        token_source: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_amount: str | int | Decimal | None = None,
        cost_currency: str | None = None,
        cost_kind: str | None = None,
        cost_source: str | None = None,
        price_version: str | None = None,
        request_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        observed_at: str | datetime | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        provider = _optional_identifier(provider_id, "provider_id")
        model_name = _optional_identifier(model, "model")
        if provider is None or model_name is None:
            raise ValueError("provider_id and model are required")
        if not isinstance(token_source, str) or token_source not in _TOKEN_SOURCES:
            raise ValueError("token_source must be provider_reported, estimated or not_reported")
        input_count = _token_count(input_tokens, "input_tokens")
        output_count = _token_count(output_tokens, "output_tokens")
        total_count = _token_count(total_tokens, "total_tokens")
        counts = (input_count, output_count, total_count)
        if token_source == "not_reported" and any(value is not None for value in counts):
            raise ValueError("not_reported usage must leave all token counts unknown")
        if token_source != "not_reported" and all(value is None for value in counts):
            raise ValueError("reported or estimated usage must include at least one token count")
        if input_count is not None and output_count is not None and total_count is not None:
            if total_count < input_count + output_count:
                raise ValueError("total_tokens cannot be less than input_tokens + output_tokens")

        cost = _cost_fields(cost_amount, cost_currency, cost_kind, cost_source, price_version)
        task = _optional_identifier(task_id, "task_id")
        agent = _optional_identifier(agent_id, "agent_id")
        project_name = _optional_identifier(project_id, "project_id")
        request = _optional_identifier(request_id, "request_id")
        attribution_count = sum(value is not None for value in (task, agent, project_name))
        attribution_status = (
            "unattributed" if attribution_count == 0
            else "complete" if attribution_count == 3
            else "partial"
        )
        observed = _timestamp(observed_at) if observed_at is not None else None
        created = datetime.now(timezone.utc).isoformat()
        stable_event_id = _optional_identifier(event_id, "event_id") or uuid.uuid4().hex
        payload = (
            provider, model_name, request, task, agent, project_name,
            attribution_status, token_source, input_count, output_count,
            total_count, *cost,
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT * FROM model_usage_events WHERE event_id=?", (stable_event_id,)
                ).fetchone()
                if existing is not None:
                    existing_payload = (
                        existing["provider_id"], existing["model"], existing["request_id"],
                        existing["task_id"], existing["agent_id"], existing["project_id"],
                        existing["attribution_status"], existing["token_source"],
                        existing["input_tokens"], existing["output_tokens"],
                        existing["total_tokens"], existing["cost_amount"],
                        existing["cost_currency"], existing["cost_kind"], existing["cost_source"],
                        existing["price_version"],
                    )
                    timestamp_matches = observed is None or existing["observed_at"] == observed
                    if existing_payload != payload or not timestamp_matches:
                        raise ValueError("event_id already exists with a different usage payload")
                    self._connection.execute("COMMIT")
                    return dict(existing)

                self._connection.execute(
                    """INSERT INTO model_usage_events (
                        event_id, observed_at, provider_id, model, request_id,
                        task_id, agent_id, project_id, attribution_status,
                        token_source, input_tokens, output_tokens, total_tokens,
                        cost_amount, cost_currency, cost_kind, cost_source, price_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
                    (
                        stable_event_id, observed or created, provider, model_name, request,
                        task, agent, project_name, attribution_status,
                        token_source, input_count, output_count, total_count,
                        *cost, created,
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM model_usage_events WHERE event_id=?", (stable_event_id,)
                ).fetchone()
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return dict(row)
    def list_events(
        self,
        *,
        provider_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        token_source: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        filters = (
            ("provider_id", _optional_identifier(provider_id, "provider_id")),
            ("task_id", _optional_identifier(task_id, "task_id")),
            ("agent_id", _optional_identifier(agent_id, "agent_id")),
            ("project_id", _optional_identifier(project_id, "project_id")),
        )
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in filters:
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        if token_source is not None:
            if token_source not in _TOKEN_SOURCES:
                raise ValueError("invalid token_source filter")
            clauses.append("token_source=?")
            values.append(token_source)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM model_usage_events{where} ORDER BY observed_at DESC, event_id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "UsageLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
