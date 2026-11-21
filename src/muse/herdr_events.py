"""Pure, fail-closed reducer for herdr-core JSON-RPC event notifications.

This module intentionally contains no socket transport, process launch, or
Commander integration. A caller owns reconnection and authoritative refresh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


_ALLOWED_AGENT_STATES = {"idle", "working", "done", "blocked"}


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("event sequence must be a non-negative integer")
    return value


def _terminal_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("id", record.get("terminal"))
    if not isinstance(value, str) or not value or len(value) > 160:
        return None
    return value


def _reported_state(value: Any, *, reported: Any = True) -> str:
    if reported is not True or not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in _ALLOWED_AGENT_STATES else "unknown"


@dataclass(frozen=True)
class HerdrEventResult:
    accepted: bool
    sequence: int
    event_type: str | None = None
    terminal_id: str | None = None
    agent_state: str | None = None
    snapshot_replaced: bool = False
    needs_reconcile: bool = False
    reason: str | None = None


@dataclass
class HerdrEventStream:
    """Tracks only terminal identity and reported status, never command/cwd data.

    A missed event carries a fresh terminal snapshot and atomically replaces
    local state. Reconnecting must start from a fresh subscription snapshot
    because the Core sequence restarts with each Core process.
    """

    sequence: int
    statuses: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "HerdrEventStream":
        sequence = _sequence(snapshot.get("seq"))
        terminals = snapshot.get("terminals")
        if not isinstance(terminals, list):
            raise ValueError("subscription snapshot must contain a terminals list")
        statuses: dict[str, str] = {}
        for record in terminals:
            if not isinstance(record, dict):
                continue
            terminal_id = _terminal_id(record)
            if terminal_id is not None:
                statuses[terminal_id] = _reported_state(
                    record.get("status"), reported=record.get("reported", False)
                )
        return cls(sequence=sequence, statuses=statuses)

    def apply(self, message: Mapping[str, Any]) -> HerdrEventResult:
        if message.get("jsonrpc") != "2.0":
            return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="invalid_jsonrpc")
        method = message.get("method")
        params = message.get("params")
        if method == "heartbeat":
            if not isinstance(params, dict):
                return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="invalid_heartbeat")
            try:
                sequence = _sequence(params.get("seq"))
            except ValueError:
                return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="invalid_sequence")
            if sequence < self.sequence:
                return HerdrEventResult(False, self.sequence, reason="stale_heartbeat")
            self.sequence = sequence
            return HerdrEventResult(True, self.sequence, event_type="heartbeat")
        # The Core schema emits `missed` as its own notification method,
        # not as an `event` whose params.type is `missed`.
        if method == "missed":
            if not isinstance(params, dict):
                return HerdrEventResult(False, self.sequence, event_type="missed", needs_reconcile=True, reason="invalid_missed")
            try:
                sequence = _sequence(params.get("seq"))
            except ValueError:
                return HerdrEventResult(False, self.sequence, event_type="missed", needs_reconcile=True, reason="invalid_sequence")
            if sequence <= self.sequence:
                return HerdrEventResult(False, self.sequence, event_type="missed", reason="duplicate_or_stale")
            terminals = params.get("terminals")
            if not isinstance(terminals, list):
                return HerdrEventResult(False, self.sequence, event_type="missed", needs_reconcile=True, reason="missed_without_snapshot")
            replacement: dict[str, str] = {}
            for record in terminals:
                if not isinstance(record, dict):
                    continue
                terminal_id = _terminal_id(record)
                if terminal_id is not None:
                    replacement[terminal_id] = _reported_state(
                        record.get("status"), reported=record.get("reported", False)
                    )
            self.statuses = replacement
            self.sequence = sequence
            return HerdrEventResult(True, sequence, event_type="missed", snapshot_replaced=True)

        if method != "event" or not isinstance(params, dict):
            return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="unexpected_message")
        try:
            sequence = _sequence(params.get("seq"))
        except ValueError:
            return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="invalid_sequence")
        event_type = params.get("type")
        if not isinstance(event_type, str) or not event_type:
            return HerdrEventResult(False, self.sequence, needs_reconcile=True, reason="invalid_event_type")
        if sequence <= self.sequence:
            return HerdrEventResult(False, self.sequence, event_type=event_type, reason="duplicate_or_stale")

        if event_type == "missed":
            terminals = params.get("terminals")
            if not isinstance(terminals, list):
                return HerdrEventResult(False, self.sequence, event_type=event_type, needs_reconcile=True, reason="missed_without_snapshot")
            replacement: dict[str, str] = {}
            for record in terminals:
                if not isinstance(record, dict):
                    continue
                terminal_id = _terminal_id(record)
                if terminal_id is not None:
                    replacement[terminal_id] = _reported_state(
                        record.get("status"), reported=record.get("reported", False)
                    )
            self.statuses = replacement
            self.sequence = sequence
            return HerdrEventResult(True, sequence, event_type=event_type, snapshot_replaced=True)

        terminal_id = params.get("terminal")
        if terminal_id is not None and (
            not isinstance(terminal_id, str) or not terminal_id or len(terminal_id) > 160
        ):
            return HerdrEventResult(False, self.sequence, event_type=event_type, needs_reconcile=True, reason="invalid_terminal_id")
        data = params.get("data")
        state = None
        if event_type in {"created", "restarted"}:
            if not isinstance(data, dict):
                return HerdrEventResult(False, self.sequence, event_type=event_type, needs_reconcile=True, reason="missing_terminal_record")
            terminal_id = _terminal_id(data)
            if terminal_id is None:
                return HerdrEventResult(False, self.sequence, event_type=event_type, needs_reconcile=True, reason="missing_terminal_id")
            state = _reported_state(data.get("status"), reported=data.get("reported", False))
            self.statuses[terminal_id] = state
        elif event_type == "status":
            if terminal_id is None or not isinstance(data, dict):
                return HerdrEventResult(False, self.sequence, event_type=event_type, needs_reconcile=True, reason="invalid_status_event")
            state = _reported_state(data.get("status"), reported=data.get("reported", False))
            self.statuses[terminal_id] = state
        elif event_type in {"lost", "finished", "removed"}:
            state = "unknown"
            if terminal_id is not None:
                self.statuses.pop(terminal_id, None)
        else:
            state = self.statuses.get(terminal_id) if terminal_id is not None else None
        self.sequence = sequence
        return HerdrEventResult(True, sequence, event_type, terminal_id, state)


def bound_agent_snapshot_updates(
    stream: HerdrEventStream,
    terminal_to_agent: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    """Project a reconciled snapshot through explicit bindings; unknown is omitted."""
    updates: list[tuple[str, str]] = []
    for terminal_id, state in stream.statuses.items():
        agent_id = terminal_to_agent.get(terminal_id)
        if (state in _ALLOWED_AGENT_STATES and isinstance(agent_id, str)
                and agent_id.strip() and len(agent_id) <= 160
                and sum(1 for value in terminal_to_agent.values() if value == agent_id) == 1):
            updates.append((agent_id, state))
    return tuple(sorted(updates))


def bound_agent_status_updates(
    result: HerdrEventResult,
    terminal_to_agent: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    """Return explicit, pre-authorized terminal→Agent status updates only."""
    if not result.accepted or result.needs_reconcile or result.snapshot_replaced:
        return ()
    if result.terminal_id is None or result.agent_state not in _ALLOWED_AGENT_STATES:
        return ()
    agent_id = terminal_to_agent.get(result.terminal_id)
    if (not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 160
            or sum(1 for value in terminal_to_agent.values() if value == agent_id) != 1):
        return ()
    return ((agent_id, result.agent_state),)
