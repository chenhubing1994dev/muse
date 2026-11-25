"""B394 safety controller for the autonomous supervisor: pause/resume, backpressure, kill switch.

The controller is deliberately small and clock-injectable so every transition can be proven
with a fake clock:

* ``pause`` / ``resume`` — persisted, audited; a paused supervisor runs nothing.
* Resource backpressure — an injected probe reports CPU %, RAM %, free GPU MiB and queue depth;
  any breach skips the tick with exponential backoff (capped), and releases automatically once
  the probe is healthy again.
* Circuit breaker — N consecutive tick failures open the breaker for a cooldown, then exactly
  one half-open trial is allowed; success closes it, failure re-opens it with doubled cooldown.
* Emergency kill switch — a latched state (SQLite) *or* the presence of a ``KILL_SWITCH`` file.
  Once engaged it can only be cleared with the explicit phrase ``RESET-KILL-SWITCH`` and only
  after the file is removed. Nothing in Muse clears it automatically.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

KILL_RESET_PHRASE = "RESET-KILL-SWITCH"


@dataclass(frozen=True)
class ResourceLimits:
    max_cpu_percent: float = 90.0
    max_memory_percent: float = 90.0
    min_free_gpu_mib: float = 0.0
    max_queue_depth: int = 500
    base_backoff_seconds: float = 15.0
    max_backoff_seconds: float = 900.0


class SafetyController:
    def __init__(self, database: str | Path, *, clock: Callable[[], float] = time.time,
                 resource_probe: Callable[[], dict[str, Any]] | None = None,
                 limits: ResourceLimits = ResourceLimits(), breaker_threshold: int = 3,
                 breaker_cooldown_seconds: float = 300.0, kill_switch_file: str | Path | None = None) -> None:
        if breaker_threshold < 1 or breaker_cooldown_seconds <= 0:
            raise ValueError("breaker threshold/cooldown must be positive")
        self.clock = clock
        self.resource_probe = resource_probe
        self.limits = limits
        self.breaker_threshold = int(breaker_threshold)
        self.base_cooldown = float(breaker_cooldown_seconds)
        self.kill_switch_file = Path(kill_switch_file) if kill_switch_file else None
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_safety(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), mode TEXT NOT NULL
                CHECK(mode IN ('running','paused','killed')), reason TEXT NOT NULL DEFAULT '',
                consecutive_failures INTEGER NOT NULL DEFAULT 0, breaker_open_until REAL NOT NULL DEFAULT 0,
                breaker_cooldown REAL NOT NULL DEFAULT 0, half_open INTEGER NOT NULL DEFAULT 0,
                backpressure_streak INTEGER NOT NULL DEFAULT 0, backoff_until REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS supervisor_safety_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, actor TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS supervisor_safety_events_no_update BEFORE UPDATE ON supervisor_safety_events
                BEGIN SELECT RAISE(ABORT,'safety events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS supervisor_safety_events_no_delete BEFORE DELETE ON supervisor_safety_events
                BEGIN SELECT RAISE(ABORT,'safety events are append-only'); END;
        """)
        self.db.execute("INSERT OR IGNORE INTO supervisor_safety(singleton,mode,updated_at) VALUES(1,'running',?)",
                        (self.clock(),))
        self.db.commit()

    # ----------------------------------------------------------------- plumbing
    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "SafetyController":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _row(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM supervisor_safety WHERE singleton=1").fetchone()

    def _update(self, **fields: Any) -> None:
        fields["updated_at"] = self.clock()
        assignments = ",".join(f"{key}=?" for key in fields)
        self.db.execute(f"UPDATE supervisor_safety SET {assignments} WHERE singleton=1", tuple(fields.values()))

    def _event(self, event: str, actor: str, **detail: Any) -> None:
        self.db.execute("INSERT INTO supervisor_safety_events(event,actor,detail_json,created_at) VALUES(?,?,?,?)",
                        (event, str(actor)[:120], json.dumps(detail, ensure_ascii=False, default=str)[:4000], self.clock()))

    # ----------------------------------------------------------------- controls
    def pause(self, *, actor: str, reason: str = "") -> dict[str, Any]:
        with self.db:
            if self._row()["mode"] == "killed":
                raise PermissionError("kill switch engaged; pause/resume are disabled until it is reset")
            self._update(mode="paused", reason=str(reason)[:300])
            self._event("paused", actor, reason=reason)
        return self.status()

    def resume(self, *, actor: str) -> dict[str, Any]:
        with self.db:
            if self._row()["mode"] == "killed" or self._kill_file_present():
                raise PermissionError("kill switch engaged; resume refused")
            self._update(mode="running", reason="")
            self._event("resumed", actor)
        return self.status()

    def engage_kill_switch(self, *, actor: str, reason: str) -> dict[str, Any]:
        with self.db:
            self._update(mode="killed", reason=str(reason or "emergency stop")[:300])
            self._event("kill_switch_engaged", actor, reason=reason)
        return self.status()

    def reset_kill_switch(self, *, actor: str, confirmation: str) -> dict[str, Any]:
        if confirmation != KILL_RESET_PHRASE:
            raise PermissionError(f"confirmation phrase must be {KILL_RESET_PHRASE}")
        if self._kill_file_present():
            raise PermissionError("remove the KILL_SWITCH file before resetting")
        with self.db:
            self._update(mode="paused", reason="kill switch reset; explicit resume required",
                         consecutive_failures=0, breaker_open_until=0, half_open=0)
            self._event("kill_switch_reset", actor)
        return self.status()

    def _kill_file_present(self) -> bool:
        return bool(self.kill_switch_file and self.kill_switch_file.exists())

    # ----------------------------------------------------------------- decisions
    def _probe(self) -> tuple[list[str], dict[str, Any]]:
        if self.resource_probe is None:
            return [], {}
        try:
            sample = self.resource_probe() or {}
        except Exception as exc:  # an unreadable probe is itself backpressure (fail closed)
            return [f"resource probe failed: {type(exc).__name__}"], {}
        breaches = []
        limits = self.limits
        if float(sample.get("cpu_percent", 0)) > limits.max_cpu_percent:
            breaches.append("cpu")
        if float(sample.get("memory_percent", 0)) > limits.max_memory_percent:
            breaches.append("memory")
        if "gpu_free_mib" in sample and float(sample["gpu_free_mib"]) < limits.min_free_gpu_mib:
            breaches.append("gpu")
        if int(sample.get("queue_depth", 0)) > limits.max_queue_depth:
            breaches.append("queue")
        return breaches, sample

    def check(self) -> dict[str, Any]:
        now = self.clock()
        with self.db:
            row = self._row()
            if self._kill_file_present() and row["mode"] != "killed":
                self._update(mode="killed", reason="KILL_SWITCH file present")
                self._event("kill_switch_engaged", "kill-switch-file")
                row = self._row()
            if row["mode"] == "killed":
                return {"allowed": False, "reason": "kill_switch", "detail": row["reason"]}
            if row["mode"] == "paused":
                return {"allowed": False, "reason": "paused", "detail": row["reason"]}
            if row["breaker_open_until"] > now:
                return {"allowed": False, "reason": "circuit_open", "retry_at": row["breaker_open_until"]}
            if row["backoff_until"] > now:
                return {"allowed": False, "reason": "backpressure_backoff", "retry_at": row["backoff_until"]}
            breaches, sample = self._probe()
            if breaches:
                streak = row["backpressure_streak"] + 1
                delay = min(self.limits.base_backoff_seconds * (2 ** (streak - 1)), self.limits.max_backoff_seconds)
                self._update(backpressure_streak=streak, backoff_until=now + delay)
                self._event("backpressure", "resource-probe", breaches=breaches, delay=delay)
                return {"allowed": False, "reason": "backpressure", "breaches": breaches, "retry_at": now + delay,
                        "sample": sample}
            if row["backpressure_streak"]:
                self._update(backpressure_streak=0, backoff_until=0)
                self._event("backpressure_released", "resource-probe")
            half_open = row["consecutive_failures"] >= self.breaker_threshold
            if half_open and row["half_open"]:
                return {"allowed": False, "reason": "half_open_trial_in_progress"}
            if half_open and not row["half_open"]:
                self._update(half_open=1)
                self._event("breaker_half_open", "breaker")
            return {"allowed": True, "reason": "half_open_trial" if half_open else "ok"}

    def record_result(self, success: bool, *, error: str = "") -> dict[str, Any]:
        now = self.clock()
        with self.db:
            row = self._row()
            if success:
                if row["consecutive_failures"] or row["half_open"]:
                    self._event("breaker_closed", "breaker")
                self._update(consecutive_failures=0, half_open=0, breaker_open_until=0, breaker_cooldown=0)
            else:
                failures = row["consecutive_failures"] + 1
                fields: dict[str, Any] = {"consecutive_failures": failures}
                if failures >= self.breaker_threshold:
                    cooldown = self.base_cooldown if not row["half_open"] else min(row["breaker_cooldown"] * 2, 86400)
                    fields.update(breaker_open_until=now + cooldown, breaker_cooldown=cooldown, half_open=0)
                    self._event("breaker_opened", "breaker", failures=failures, cooldown=cooldown, error=error[:300])
                self._update(**fields)
        return self.status()

    def status(self) -> dict[str, Any]:
        row = dict(self._row())
        row["kill_switch_file_present"] = self._kill_file_present()
        return row

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM supervisor_safety_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))]


def guarded_tick(tick: Callable[[], Any], controller: SafetyController) -> dict[str, Any]:
    """Run one supervisor tick only if the controller allows it; record the outcome."""
    decision = controller.check()
    if not decision["allowed"]:
        return {"ran": False, "safety": decision}
    try:
        result = tick()
    except Exception as exc:
        controller.record_result(False, error=f"{type(exc).__name__}: {exc}")
        return {"ran": True, "ok": False, "error_type": type(exc).__name__, "safety": decision}
    controller.record_result(True)
    return {"ran": True, "ok": True, "result": result, "safety": decision}
