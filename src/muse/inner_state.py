"""Persistent inner-state model that drives Muse's autonomous attention (stdlib only).

This is a *functional control model*, not a claim of subjective experience
(docs/VISION.md treats "consciousness" as a brand metaphor). Its numbers decide
concrete behaviour:

- ``valence``   (-1..1) recent outcome quality; low valence lowers risk appetite
- ``energy``    (0..1)  action capacity; spent by work, recovered while idle
- ``curiosity`` (0..1)  drive for idle introspection; raised by novelty
- ``focus``     goals with priorities; the top goal feeds idle thinking
- ``budget``    rolling-hour model-call / token budget (anti runaway loop)
- ``strategy``  self-adjusting ``max_actions`` per cycle (halved on failure,
                grown on success streaks)

Persistence: one JSON document written atomically (temp file + ``os.replace``)
with the previous good copy kept as ``.bak``. A corrupt main file falls back to
the backup, then to defaults; the recovery source is recorded, never hidden.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

SCHEMA = "muse-inner-state-v1"
BASELINE = {"valence": 0.1, "energy": 0.8, "curiosity": 0.5}
HALF_LIFE_SECONDS = {"valence": 6 * 3600.0, "curiosity": 3 * 3600.0}
ENERGY_RECOVERY_PER_HOUR = 0.25
MAX_GOALS = 50
MAX_EVENTS = 200

# event -> (valence delta, energy delta, curiosity delta)
EVENT_EFFECTS: dict[str, tuple[float, float, float]] = {
    "user_message": (0.02, -0.02, 0.05),
    "introspection": (0.0, -0.03, -0.10),
    "novelty": (0.03, 0.0, 0.15),
    "plan_created": (0.02, -0.03, -0.02),
    "task_succeeded": (0.08, -0.04, -0.02),
    "task_failed": (-0.15, -0.06, 0.04),
    "approval_waiting": (0.0, 0.0, 0.0),
    "approval_rejected": (-0.08, 0.0, 0.0),
    "review_approved": (0.06, 0.0, 0.0),
    "review_rejected": (-0.12, -0.02, 0.05),
    "throttled": (-0.02, 0.0, 0.0),
    "rest": (0.0, 0.05, 0.02),
}


def _clamp(value: float, low: float, high: float) -> float:
    if not isinstance(value, (int, float)) or math.isnan(value):
        return low
    return max(low, min(high, float(value)))


def _default_state(now: float) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "created_at": now,
        "updated_at": now,
        "last_tick": now,
        "last_thought_at": 0.0,
        "affect": dict(BASELINE),
        "goals": [],
        "counters": {"ticks": 0, "cycles": 0, "actions": 0, "failures": 0, "throttled": 0, "restarts": 0},
        "strategy": {"max_actions": 4, "success_streak": 0},
        "budget": {"window_start": now, "calls": 0, "tokens": 0},
        "events": [],
        "recovered_from": None,
    }


class InnerStateError(RuntimeError):
    """The inner-state document is invalid and could not be recovered."""


class InnerStateStore:
    """Load, mutate and atomically persist the inner state for one workspace."""

    def __init__(
        self, path: str | Path, *, clock: Callable[[], float] = time.time,
        max_calls_per_hour: int = 60, max_tokens_per_hour: int = 40_000,
        min_thought_interval: float = 300.0, read_only: bool = False,
    ) -> None:
        for name, value in (("max_calls_per_hour", max_calls_per_hour), ("max_tokens_per_hour", max_tokens_per_hour)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(min_thought_interval, (int, float)) or min_thought_interval < 0:
            raise ValueError("min_thought_interval must be non-negative")
        self.path = Path(path)
        self.backup_path = self.path.with_name(self.path.name + ".bak")
        self._clock = clock
        self.max_calls_per_hour = max_calls_per_hour
        self.max_tokens_per_hour = max_tokens_per_hour
        self.min_thought_interval = float(min_thought_interval)
        self.read_only = bool(read_only)
        self._lock = threading.RLock()
        self.state = self._load()

    # ------------------------------------------------------------------ persistence
    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        return data

    def _normalise(self, data: dict[str, Any], now: float) -> dict[str, Any]:
        state = _default_state(now)
        for key in ("created_at", "updated_at", "last_tick", "last_thought_at"):
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                state[key] = float(data[key])
        affect = data.get("affect") if isinstance(data.get("affect"), dict) else {}
        state["affect"] = {
            "valence": _clamp(affect.get("valence", BASELINE["valence"]), -1.0, 1.0),
            "energy": _clamp(affect.get("energy", BASELINE["energy"]), 0.0, 1.0),
            "curiosity": _clamp(affect.get("curiosity", BASELINE["curiosity"]), 0.0, 1.0),
        }
        goals = []
        for goal in data.get("goals", [])[:MAX_GOALS] if isinstance(data.get("goals"), list) else []:
            if isinstance(goal, dict) and isinstance(goal.get("id"), str) and isinstance(goal.get("text"), str):
                goals.append({
                    "id": goal["id"], "text": goal["text"][:1000],
                    "priority": _clamp(goal.get("priority", 0.5), 0.0, 1.0),
                    "status": goal.get("status") if goal.get("status") in {"active", "done", "dropped"} else "active",
                    "attempts": int(goal.get("attempts", 0)) if isinstance(goal.get("attempts"), int) else 0,
                    "created_at": float(goal.get("created_at", now)) if isinstance(goal.get("created_at"), (int, float)) else now,
                })
        state["goals"] = goals
        for section in ("counters", "strategy", "budget"):
            if isinstance(data.get(section), dict):
                for key, value in data[section].items():
                    if key in state[section] and isinstance(value, (int, float)) and not isinstance(value, bool):
                        state[section][key] = value
        state["strategy"]["max_actions"] = int(_clamp(state["strategy"]["max_actions"], 1, 16))
        events = data.get("events") if isinstance(data.get("events"), list) else []
        state["events"] = [e for e in events if isinstance(e, dict)][-MAX_EVENTS:]
        return state

    def _load(self) -> dict[str, Any]:
        now = self._clock()
        data = self._read(self.path)
        source = None
        if data is None:
            # Main file missing (crash between the two replaces) or corrupt: use the backup.
            data = self._read(self.backup_path)
            if data is not None:
                source = "backup"
            elif self.path.exists():
                source = "defaults_after_corruption"
        state = self._normalise(data, now) if data is not None else _default_state(now)
        if data is not None and not self.read_only:
            state["counters"]["restarts"] = int(state["counters"].get("restarts", 0)) + 1
        state["recovered_from"] = source
        return state

    def save(self) -> None:
        if self.read_only:
            raise InnerStateError("read-only inner state cannot be saved")
        with self._lock:
            self.state["updated_at"] = self._clock()
            payload = json.dumps(self.state, ensure_ascii=False, indent=1, sort_keys=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self._read(self.path) is not None:
                try:
                    os.replace(self.path, self.backup_path)
                except OSError:
                    pass
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    # Windows: a reader may hold the file briefly; retry, never leave it half-written.
                    if attempt == 4:
                        temporary.unlink(missing_ok=True)
                        raise
                    time.sleep(0.05 * (attempt + 1))

    # ------------------------------------------------------------------ dynamics
    def _record(self, kind: str, detail: str = "") -> None:
        events = self.state["events"]
        events.append({"at": self._clock(), "kind": kind, "detail": detail[:200]})
        del events[:-MAX_EVENTS]

    def tick(self) -> dict[str, Any]:
        """Advance homeostasis: affect decays toward baseline, energy recovers over time."""
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - float(self.state["last_tick"]))
            affect = self.state["affect"]
            for key, half_life in HALF_LIFE_SECONDS.items():
                factor = 0.5 ** (elapsed / half_life)
                affect[key] = BASELINE[key] + (affect[key] - BASELINE[key]) * factor
            affect["energy"] = _clamp(affect["energy"] + ENERGY_RECOVERY_PER_HOUR * elapsed / 3600.0, 0.0, 1.0)
            affect["curiosity"] = _clamp(affect["curiosity"] + 0.02 * elapsed / 3600.0, 0.0, 1.0)
            self.state["last_tick"] = now
            self.state["counters"]["ticks"] += 1
            self._roll_budget(now)
            return dict(affect)

    def apply_event(self, kind: str, *, intensity: float = 1.0, detail: str = "") -> dict[str, float]:
        if kind not in EVENT_EFFECTS:
            raise ValueError(f"unknown inner-state event: {kind}")
        intensity = _clamp(intensity, 0.0, 3.0)
        with self._lock:
            dv, de, dc = EVENT_EFFECTS[kind]
            affect = self.state["affect"]
            affect["valence"] = _clamp(affect["valence"] + dv * intensity, -1.0, 1.0)
            affect["energy"] = _clamp(affect["energy"] + de * intensity, 0.0, 1.0)
            affect["curiosity"] = _clamp(affect["curiosity"] + dc * intensity, 0.0, 1.0)
            if kind == "task_failed":
                self.state["counters"]["failures"] += 1
            self._record(kind, detail)
            return dict(affect)

    def mood_label(self) -> str:
        a = self.state["affect"]
        if a["energy"] < 0.2:
            return "depleted"
        if a["valence"] < -0.3:
            return "cautious"
        if a["curiosity"] > 0.7:
            return "curious"
        if a["valence"] > 0.4:
            return "confident"
        return "steady"

    def risk_appetite(self) -> str:
        """Lower appetite when outcomes have been bad or energy is low."""
        a = self.state["affect"]
        if a["valence"] < -0.3 or a["energy"] < 0.2:
            return "minimal"
        return "normal"

    # ------------------------------------------------------------------ strategy
    def record_cycle_outcome(self, *, succeeded: int, failed: int) -> dict[str, Any]:
        with self._lock:
            strategy = self.state["strategy"]
            self.state["counters"]["cycles"] += 1
            self.state["counters"]["actions"] += succeeded + failed
            if failed:
                strategy["max_actions"] = max(1, int(strategy["max_actions"]) // 2)
                strategy["success_streak"] = 0
            elif succeeded:
                strategy["success_streak"] = int(strategy["success_streak"]) + 1
                if strategy["success_streak"] >= 3:
                    strategy["max_actions"] = min(16, int(strategy["max_actions"]) + 1)
                    strategy["success_streak"] = 0
            return dict(strategy)

    # ------------------------------------------------------------------ budget
    def _roll_budget(self, now: float) -> None:
        budget = self.state["budget"]
        if now - float(budget["window_start"]) >= 3600.0:
            budget.update(window_start=now, calls=0, tokens=0)

    def budget_remaining(self) -> dict[str, int]:
        with self._lock:
            self._roll_budget(self._clock())
            budget = self.state["budget"]
            return {
                "calls": max(0, self.max_calls_per_hour - int(budget["calls"])),
                "tokens": max(0, self.max_tokens_per_hour - int(budget["tokens"])),
            }

    def try_consume(self, *, tokens: int, calls: int = 1) -> bool:
        """Reserve budget for one model call; False means throttled (nothing consumed)."""
        if tokens < 0 or calls < 0:
            raise ValueError("budget amounts must be non-negative")
        with self._lock:
            self._roll_budget(self._clock())
            budget = self.state["budget"]
            if (int(budget["calls"]) + calls > self.max_calls_per_hour
                    or int(budget["tokens"]) + tokens > self.max_tokens_per_hour):
                self.state["counters"]["throttled"] += 1
                self.apply_event("throttled")
                return False
            budget["calls"] = int(budget["calls"]) + calls
            budget["tokens"] = int(budget["tokens"]) + tokens
            return True

    # ------------------------------------------------------------------ goals
    def add_goal(self, text: str, *, priority: float = 0.5) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("goal text must be 1-1000 characters")
        with self._lock:
            normalised = text.strip()
            for goal in self.state["goals"]:
                if goal["status"] == "active" and goal["text"] == normalised:
                    goal["priority"] = max(goal["priority"], _clamp(priority, 0.0, 1.0))
                    return dict(goal)
            goal = {"id": uuid.uuid4().hex[:12], "text": normalised, "priority": _clamp(priority, 0.0, 1.0),
                    "status": "active", "attempts": 0, "created_at": self._clock()}
            self.state["goals"].append(goal)
            active = [g for g in self.state["goals"] if g["status"] == "active"]
            inactive = [g for g in self.state["goals"] if g["status"] != "active"]
            self.state["goals"] = (sorted(active, key=lambda g: -g["priority"]) + inactive)[:MAX_GOALS]
            return dict(goal)

    def update_goal(self, goal_id: str, *, status: str | None = None, attempted: bool = False,
                    priority_delta: float = 0.0) -> dict[str, Any] | None:
        with self._lock:
            for goal in self.state["goals"]:
                if goal["id"] == goal_id:
                    if status is not None:
                        if status not in {"active", "done", "dropped"}:
                            raise ValueError("invalid goal status")
                        goal["status"] = status
                    if attempted:
                        goal["attempts"] += 1
                    goal["priority"] = _clamp(goal["priority"] + priority_delta, 0.0, 1.0)
                    return dict(goal)
        return None

    def top_goal(self, *, exclude: set[str] | frozenset[str] = frozenset()) -> dict[str, Any] | None:
        """Highest weighted active goal; repeated attempts reduce weight (avoid fixation loops).

        ``exclude`` holds goal IDs that already have work in flight (e.g. waiting for a
        human decision); they are never re-planned until that work resolves.
        """
        with self._lock:
            active = [g for g in self.state["goals"] if g["status"] == "active" and g["id"] not in exclude]
            if not active:
                return None
            curiosity = self.state["affect"]["curiosity"]

            def weight(goal: dict[str, Any]) -> float:
                return goal["priority"] * (0.5 + curiosity) / (1.0 + goal["attempts"])
            return dict(max(active, key=weight))

    # ------------------------------------------------------------------ autonomy gate
    def should_think(self, *, exclude: set[str] | frozenset[str] = frozenset()) -> tuple[bool, str]:
        with self._lock:
            now = self._clock()
            affect = self.state["affect"]
            if now - float(self.state["last_thought_at"]) < self.min_thought_interval:
                return False, "cooldown"
            if affect["energy"] < 0.25:
                return False, "low_energy"
            if self.budget_remaining()["calls"] < 1:
                return False, "budget_exhausted"
            if self.top_goal(exclude=exclude) is None:
                return False, "no_active_goal" if not exclude else "goals_awaiting_resolution"
            if affect["curiosity"] < 0.3:
                return False, "low_curiosity"
            return True, "ready"

    def mark_thought(self) -> None:
        with self._lock:
            self.state["last_thought_at"] = self._clock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "mood": self.mood_label(),
                "risk_appetite": self.risk_appetite(),
                "affect": {k: round(v, 4) for k, v in self.state["affect"].items()},
                "goals": [dict(g) for g in self.state["goals"] if g["status"] == "active"][:10],
                "counters": dict(self.state["counters"]),
                "strategy": dict(self.state["strategy"]),
                "budget_remaining": self.budget_remaining(),
                "recent_events": list(self.state["events"][-10:]),
                "recovered_from": self.state.get("recovered_from"),
                "updated_at": self.state["updated_at"],
            }


def read_snapshot(path: str | Path) -> dict[str, Any]:
    """Read-only snapshot for API/CLI callers; never writes or increments restarts."""
    store_path = Path(path)
    if not store_path.exists() and not store_path.with_name(store_path.name + ".bak").exists():
        return {"status": "not_initialized"}
    store = InnerStateStore(store_path, read_only=True)
    if store.state.get("recovered_from") == "defaults_after_corruption":
        return {"status": "unreadable"}
    return {"status": "ok", **store.snapshot()}


__all__ = ["InnerStateStore", "InnerStateError", "EVENT_EFFECTS", "read_snapshot"]
