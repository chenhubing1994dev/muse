"""Local, policy-gated intake of external or proactive agent triggers.

This module records source events and their admission decisions. It does not
connect to sources, call models, enqueue work, or execute tools. The payload is
kept locally for a later bounded assessor; it is never authority by itself.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


MAX_PAYLOAD_BYTES = 16 * 1024
MAX_IDENTIFIER_LENGTH = 200
VALID_MODES = frozenset({"off", "observe", "auto"})


def _required_text(value: str, field: str, *, maximum: int = MAX_IDENTIFIER_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be non-empty text of at most {maximum} characters")
    return value.strip()


@dataclass(frozen=True)
class SourcePolicy:
    """Source-local authorization and the finite scopes/rules it may grant."""

    enabled: bool = True
    allowed_actors: frozenset[str] = frozenset()
    enabled_rules: frozenset[str] = frozenset()
    allowed_scopes: frozenset[str] = frozenset({"requested"})

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("source enabled must be boolean")
        for field, values in (
            ("allowed_actors", self.allowed_actors),
            ("enabled_rules", self.enabled_rules),
            ("allowed_scopes", self.allowed_scopes),
        ):
            if len(values) > 500:
                raise ValueError(f"{field} may contain at most 500 entries")
            if not isinstance(values, (set, frozenset)):
                raise ValueError(f"{field} must be a set of strings")
        for actor in self.allowed_actors:
            _required_text(actor, "allowed actor", maximum=200)
        for rule in self.enabled_rules:
            _required_text(rule, "rule id", maximum=80)
        for scope in self.allowed_scopes:
            _required_text(scope, "scope", maximum=120)


@dataclass(frozen=True)
class AdmissionPolicy:
    """Versioned admission rules. Inferred work defaults to observe-only."""

    version: str
    mode: str
    sources: Mapping[str, SourcePolicy]

    def __post_init__(self) -> None:
        _required_text(self.version, "policy version", maximum=128)
        if not isinstance(self.mode, str) or self.mode not in VALID_MODES:
            raise ValueError("mode must be off, observe, or auto")
        if not isinstance(self.sources, Mapping):
            raise ValueError("sources must be a mapping of source ids to SourcePolicy")
        for source, config in self.sources.items():
            _required_text(source, "source id", maximum=80)
            if not isinstance(config, SourcePolicy):
                raise ValueError("every source policy must be a SourcePolicy")

    def fingerprint(self) -> str:
        snapshot = {
            "version": self.version,
            "mode": self.mode,
            "sources": {
                source: {
                    "enabled": config.enabled,
                    "allowed_actors": sorted(config.allowed_actors),
                    "enabled_rules": sorted(config.enabled_rules),
                    "allowed_scopes": sorted(config.allowed_scopes),
                }
                for source, config in sorted(self.sources.items())
            },
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TriggerInput:
    source: str
    event_id: str
    actor_id: str
    addressed: bool
    payload: dict[str, object]


@dataclass(frozen=True)
class ReceiveResult:
    trigger_id: str
    status: str
    strength: str | None
    reason: str
    duplicate: bool = False


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str
    trigger_id: str
    status: str
    reason: str
    mandate_id: str | None = None


class TriggerStore:
    """SQLite trigger inbox with dedupe, source admission and immutable mandates."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        # Two first-use connections can race while switching a new database to WAL.
        # Retry only this idempotent journal-mode transition; all other errors fail closed.
        for attempt in range(8):
            try:
                journal_mode = self.connection.execute("PRAGMA journal_mode").fetchone()[0]
                if str(journal_mode).lower() != "wal":
                    self.connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(min(0.025 * (2 ** attempt), 0.4))
        self.connection.executescript(            """
            CREATE TABLE IF NOT EXISTS triggers (
                trigger_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                event_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                addressed INTEGER NOT NULL CHECK(addressed IN (0, 1)),
                strength TEXT CHECK(strength IN ('explicit', 'inferred') OR strength IS NULL),
                payload_json TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ignored', 'candidate', 'would_accept', 'accepted', 'unavailable')),
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(source, event_id)
            );
            CREATE TABLE IF NOT EXISTS trigger_decisions (
                decision_id TEXT PRIMARY KEY,
                trigger_id TEXT NOT NULL UNIQUE REFERENCES triggers(trigger_id),
                policy_version TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                assessed_rules_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mandates (
                mandate_id TEXT PRIMARY KEY,
                trigger_id TEXT NOT NULL UNIQUE REFERENCES triggers(trigger_id),
                source TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                strength TEXT NOT NULL CHECK(strength IN ('explicit', 'inferred')),
                policy_version TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                matched_rules_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS mandates_reject_update
            BEFORE UPDATE ON mandates BEGIN
                SELECT RAISE(ABORT, 'mandates are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS mandates_reject_delete
            BEFORE DELETE ON mandates BEGIN
                SELECT RAISE(ABORT, 'mandates are immutable');
            END;
            CREATE INDEX IF NOT EXISTS idx_triggers_status_created
                ON triggers(status, created_at);
            CREATE TABLE IF NOT EXISTS intake_outbox (
                outbox_id TEXT PRIMARY KEY,
                trigger_id TEXT NOT NULL UNIQUE REFERENCES triggers(trigger_id),
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'delivered')),
                task_id TEXT,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_intake_outbox_pending
                ON intake_outbox(status, created_at);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "TriggerStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _payload_json(payload: dict[str, object]) -> str:
        if not isinstance(payload, dict):
            raise ValueError("trigger payload must be a dictionary")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("trigger payload must contain JSON-compatible values") from exc
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"trigger payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        return encoded

    def receive(self, trigger: TriggerInput, policy: AdmissionPolicy) -> ReceiveResult:
        source = _required_text(trigger.source, "source id", maximum=80)
        event_id = _required_text(trigger.event_id, "source event id", maximum=256)
        actor_id = _required_text(trigger.actor_id, "actor id", maximum=200)
        if not isinstance(trigger.addressed, bool):
            raise ValueError("addressed must be boolean")
        payload_json = self._payload_json(trigger.payload)
        policy_fingerprint = policy.fingerprint()
        source_policy = policy.sources.get(source)

        strength: str | None = None
        if source_policy is None:
            status, reason = "ignored", "source_not_configured"
        elif not source_policy.enabled:
            status, reason = "ignored", "source_disabled"
        elif actor_id not in source_policy.allowed_actors:
            status, reason = "ignored", "actor_not_allowed"
        elif trigger.addressed:
            status, strength, reason = "candidate", "explicit", "explicit_request"
        elif policy.mode == "off":
            status, reason = "ignored", "inferred_admission_off"
        else:
            status, strength = "candidate", "inferred"
            reason = "observe_only" if policy.mode == "observe" else "assessment_required"

        if status == "ignored":
            payload_json = "{}"  # Retain the disposition, not content from a rejected source/actor.

        now = self._now()
        trigger_id = uuid.uuid4().hex
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT trigger_id, status, strength, reason FROM triggers WHERE source=? AND event_id=?",
                (source, event_id),
            ).fetchone()
            if existing is not None:
                self.connection.commit()
                return ReceiveResult(
                    existing["trigger_id"], existing["status"], existing["strength"],
                    existing["reason"], duplicate=True,
                )
            self.connection.execute(
                """INSERT INTO triggers
                   (trigger_id, source, event_id, actor_id, addressed, strength,
                    payload_json, policy_version, policy_fingerprint, status, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trigger_id, source, event_id, actor_id, int(trigger.addressed), strength,
                 payload_json, policy.version, policy_fingerprint, status, reason, now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return ReceiveResult(trigger_id, status, strength, reason)

    def get_trigger(self, trigger_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM triggers WHERE trigger_id=?", (trigger_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["addressed"] = bool(result["addressed"])
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def get_mandate(self, trigger_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM mandates WHERE trigger_id=?", (trigger_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["matched_rules"] = json.loads(result.pop("matched_rules_json"))
        return result

    def decide(
        self,
        trigger_id: str,
        policy: AdmissionPolicy,
        *,
        action: str,
        scope: str = "requested",
        assessed_rules: Mapping[str, bool] | None = None,
        reason: str = "",
    ) -> DecisionResult:
        """Record a decision; only explicit or auto-approved matched work gets a Mandate.

        The assessor supplies a boolean judgement for every enabled rule. Its
        prose and the Trigger payload are evidence, never execution authority.
        """
        if not isinstance(action, str) or action not in {"accept", "ignore"}:
            raise ValueError("action must be accept or ignore")
        scope = _required_text(scope, "scope", maximum=120)
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("reason must be text of at most 500 characters")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM triggers WHERE trigger_id=?", (trigger_id,)
            ).fetchone()
            if row is None:
                raise KeyError(trigger_id)
            prior = self.connection.execute(
                "SELECT decision_id, status, reason FROM trigger_decisions WHERE trigger_id=?",
                (trigger_id,),
            ).fetchone()
            if prior is not None:
                raise ValueError("trigger already has a decision")
            if row["status"] != "candidate":
                raise ValueError("only candidate triggers can be decided")

            source_policy = policy.sources.get(row["source"])
            outcome = "accepted" if action == "accept" else "ignored"
            outcome_reason = reason.strip()
            matched_rules: list[str] = []
            assessed: dict[str, bool] = {}

            if policy.version != row["policy_version"]:
                outcome, outcome_reason = "unavailable", "policy_version_changed"
            elif policy.fingerprint() != row["policy_fingerprint"]:
                outcome, outcome_reason = "unavailable", "policy_content_changed"
            elif source_policy is None or not source_policy.enabled or row["actor_id"] not in source_policy.allowed_actors:
                outcome, outcome_reason = "unavailable", "source_authorization_changed"
            elif scope not in source_policy.allowed_scopes:
                raise ValueError("scope is not allowed by the source policy")
            elif row["strength"] == "explicit":
                if assessed_rules is not None:
                    raise ValueError("explicit requests do not require inferred-rule assessment")
            else:
                if policy.mode == "off":
                    outcome, outcome_reason = "unavailable", "inferred_admission_disabled"
                else:
                    if not isinstance(assessed_rules, Mapping) or set(assessed_rules) != set(source_policy.enabled_rules):
                        raise ValueError("every enabled rule must be assessed exactly once")
                    if any(not isinstance(value, bool) for value in assessed_rules.values()):
                        raise ValueError("rule assessments must be boolean")
                    assessed = dict(sorted(assessed_rules.items()))
                    matched_rules = [rule for rule, matched in assessed.items() if matched]
                    if action == "accept" and not matched_rules:
                        outcome, outcome_reason = "ignored", "no_enabled_rule_matched"
                    elif action == "accept" and policy.mode == "observe":
                        outcome, outcome_reason = "would_accept", "observe_mode_no_execution"
                    elif action == "accept" and policy.mode != "auto":
                        outcome, outcome_reason = "unavailable", "unknown_admission_mode"

            now = self._now()
            decision_id = uuid.uuid4().hex
            mandate_id: str | None = None
            if outcome == "accepted":
                mandate_id = uuid.uuid4().hex
                self.connection.execute(
                    """INSERT INTO mandates
                       (mandate_id, trigger_id, source, actor_id, scope, strength,
                        policy_version, policy_fingerprint, matched_rules_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mandate_id, trigger_id, row["source"], row["actor_id"], scope,
                     row["strength"], policy.version, policy.fingerprint(), json.dumps(matched_rules), now),
                )
                outbox_payload = {
                    "trigger_id": trigger_id, "mandate_id": mandate_id,
                    "source": row["source"], "event_id": row["event_id"],
                    "actor_id": row["actor_id"], "scope": scope,
                    "strength": row["strength"], "matched_rules": matched_rules,
                    "payload": json.loads(row["payload_json"]),
                    "policy_version": policy.version,
                    "policy_fingerprint": policy.fingerprint(),
                }
                self.connection.execute(
                    """INSERT INTO intake_outbox
                       (outbox_id, trigger_id, idempotency_key, payload_json, status, created_at)
                       VALUES (?, ?, ?, ?, 'pending', ?)""",
                    (uuid.uuid4().hex, trigger_id, f"trigger-outbox:{trigger_id}",
                     json.dumps(outbox_payload, ensure_ascii=False, sort_keys=True), now),
                )
            self.connection.execute(
                """INSERT INTO trigger_decisions
                   (decision_id, trigger_id, policy_version, policy_fingerprint, status, reason,
                    assessed_rules_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, trigger_id, policy.version, policy.fingerprint(), outcome, outcome_reason,
                 json.dumps(assessed, sort_keys=True), now),
            )
            self.connection.execute(
                "UPDATE triggers SET status=?, reason=? WHERE trigger_id=? AND status='candidate'",
                (outcome, outcome_reason, trigger_id),
            )
            self.connection.commit()
            return DecisionResult(decision_id, trigger_id, outcome, outcome_reason, mandate_id)
        except Exception:
            self.connection.rollback()
            raise

    def list_candidates(self, *, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            "SELECT trigger_id, source, event_id, actor_id, strength, policy_version, reason, created_at "
            "FROM triggers WHERE status='candidate' ORDER BY created_at, trigger_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def pending_outbox(self, *, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            "SELECT outbox_id, trigger_id, idempotency_key, payload_json, created_at "
            "FROM intake_outbox WHERE status='pending' ORDER BY created_at, outbox_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def mark_outbox_delivered(self, outbox_id: str, task_id: str) -> bool:
        _required_text(outbox_id, "outbox id", maximum=64)
        _required_text(task_id, "task id", maximum=64)
        now = self._now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status, task_id FROM intake_outbox WHERE outbox_id=?", (outbox_id,)
            ).fetchone()
            if row is None:
                raise KeyError(outbox_id)
            if row["status"] == "delivered":
                if row["task_id"] != task_id:
                    raise ValueError("outbox item was delivered to a different task")
                self.connection.commit()
                return False
            cursor = self.connection.execute(
                "UPDATE intake_outbox SET status='delivered', task_id=?, delivered_at=? "
                "WHERE outbox_id=? AND status='pending'",
                (task_id, now, outbox_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1
        except Exception:
            self.connection.rollback()
            raise
