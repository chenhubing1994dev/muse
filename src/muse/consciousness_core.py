"""B395 ConsciousnessCore: a ten-dimension, *observable* functional state model.

This is a telemetry model, not a claim of sentience. Every dimension is a number in [0, 1]
computed from real local SQLite telemetry (task queue, memory store, safety controller) plus an
optional resource probe, and every value ships with the evidence ("drivers") that produced it so
the dashboard can show *why* Muse is in a given state.

Dimensions: Attention, WorkingMemory, LongTermMemory, Curiosity, EnergyBudget, AffectiveUIState,
Metacognition, Intent, Agency, SelfModel.

A small explicit state machine (``dormant → focused / exploring / consolidating / strained → halted``)
sits on top with hysteresis and a whitelist of legal transitions; snapshots are appended to an
append-only table for the timeline.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

DIMENSIONS = ("attention", "working_memory", "long_term_memory", "curiosity", "energy_budget",
              "affective_ui_state", "metacognition", "intent", "agency", "self_model")
PHASES = ("dormant", "focused", "exploring", "consolidating", "strained", "halted")
LEGAL_TRANSITIONS = {
    "dormant": {"dormant", "focused", "exploring", "consolidating", "strained", "halted"},
    "focused": {"focused", "exploring", "consolidating", "strained", "dormant", "halted"},
    "exploring": {"exploring", "focused", "consolidating", "strained", "dormant", "halted"},
    "consolidating": {"consolidating", "focused", "exploring", "dormant", "strained", "halted"},
    "strained": {"strained", "focused", "consolidating", "dormant", "halted"},
    "halted": {"halted", "dormant"},  # only an explicit resume (dormant) leaves halted
}
HONESTY_LABEL = "functional_telemetry_model_not_sentience"


@dataclass
class Telemetry:
    queued: int = 0
    running: int = 0
    waiting_approval: int = 0
    succeeded_recent: int = 0
    failed_recent: int = 0
    memories_active: int = 0
    memories_recent: int = 0
    memory_candidates_pending: int = 0
    memory_revisions_recent: int = 0
    research_tasks_open: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    gpu_free_mib: float | None = None
    safety_mode: str = "running"
    breaker_failures: int = 0
    sources: dict[str, str] = field(default_factory=dict)


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = connection.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.DatabaseError:
        return 0


def collect_telemetry(*, task_db: str | Path, memory_db: str | Path, safety_db: str | Path | None = None,
                      resource_probe: Callable[[], dict[str, Any]] | None = None,
                      now: datetime | None = None, window_hours: int = 24) -> Telemetry:
    """Read-only telemetry collection; missing databases degrade to zeros with ``sources`` notes."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=window_hours)).isoformat()
    telemetry = Telemetry()
    tasks = _ro(Path(task_db))
    if tasks is None:
        telemetry.sources["tasks"] = "missing"
    else:
        try:
            for status, count in tasks.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status"):
                if status in {"queued", "running", "waiting_approval"}:
                    setattr(telemetry, status, int(count))
            telemetry.succeeded_recent = _scalar(tasks, "SELECT COUNT(*) FROM tasks WHERE status='succeeded' AND updated_at>=?", (since,))
            telemetry.failed_recent = _scalar(tasks, "SELECT COUNT(*) FROM tasks WHERE status='failed' AND updated_at>=?", (since,))
            telemetry.research_tasks_open = _scalar(
                tasks, "SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running') AND payload_json LIKE '%research%'")
            telemetry.sources["tasks"] = "ok"
        finally:
            tasks.close()
    memory = _ro(Path(memory_db))
    if memory is None:
        telemetry.sources["memory"] = "missing"
    else:
        try:
            telemetry.memories_active = _scalar(memory, "SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL")
            telemetry.memories_recent = _scalar(memory, "SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL AND created_at>=?", (since,))
            telemetry.memory_candidates_pending = _scalar(memory, "SELECT COUNT(*) FROM memory_candidates WHERE status='pending'")
            telemetry.memory_revisions_recent = _scalar(memory, "SELECT COUNT(*) FROM memory_revisions WHERE changed_at>=?", (since,))
            telemetry.sources["memory"] = "ok"
        finally:
            memory.close()
    if safety_db is not None:
        safety = _ro(Path(safety_db))
        if safety is None:
            telemetry.sources["safety"] = "missing"
        else:
            try:
                row = safety.execute("SELECT mode, consecutive_failures FROM supervisor_safety WHERE singleton=1").fetchone()
                if row is not None:
                    telemetry.safety_mode, telemetry.breaker_failures = str(row[0]), int(row[1])
                telemetry.sources["safety"] = "ok"
            except sqlite3.DatabaseError:
                telemetry.sources["safety"] = "unreadable"
            finally:
                safety.close()
    if resource_probe is not None:
        try:
            sample = resource_probe() or {}
            telemetry.cpu_percent = float(sample.get("cpu_percent", 0.0))
            telemetry.memory_percent = float(sample.get("memory_percent", 0.0))
            telemetry.gpu_free_mib = float(sample["gpu_free_mib"]) if "gpu_free_mib" in sample else None
            telemetry.sources["resources"] = "ok"
        except Exception as exc:
            telemetry.sources["resources"] = f"probe_failed:{type(exc).__name__}"
    return telemetry


def _clip(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _saturate(count: float, half: float) -> float:
    """Smooth 0..1 saturation: ``half`` items → 0.5."""
    return count / (count + half) if count > 0 else 0.0


def _level(value: float) -> str:
    return "high" if value >= 0.66 else "medium" if value >= 0.33 else "low"


class ConsciousnessCore:
    def __init__(self, database: str | Path | None = None, *, clock: Callable[[], float] = time.time,
                 hysteresis: float = 0.08) -> None:
        self.clock = clock
        self.hysteresis = hysteresis
        self.db = None
        if database is not None:
            path = Path(database)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS consciousness_snapshots(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, phase TEXT NOT NULL,
                    dimensions_json TEXT NOT NULL, telemetry_json TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS consciousness_snapshots_no_update BEFORE UPDATE ON consciousness_snapshots
                    BEGIN SELECT RAISE(ABORT,'snapshots are append-only'); END;
            """)
            self.db.commit()

    def close(self) -> None:
        if self.db is not None:
            self.db.close()

    def __enter__(self) -> "ConsciousnessCore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ dimensions
    def dimensions(self, t: Telemetry) -> dict[str, dict[str, Any]]:
        active = t.running + t.queued
        total_recent = t.succeeded_recent + t.failed_recent
        failure_rate = t.failed_recent / total_recent if total_recent else 0.0
        load = max(t.cpu_percent, t.memory_percent) / 100.0
        dims: dict[str, tuple[float, list[str]]] = {
            # focus is high while something runs, fragmented by a long queue behind it
            "attention": ((0.9 if t.running else 0.25) * (1.0 - 0.5 * _saturate(t.queued, 20)),
                          [f"running={t.running}", f"queued={t.queued}", f"active={active}"]),
            "working_memory": (_saturate(t.running + t.waiting_approval, 4), [f"running={t.running}",
                                                                              f"waiting_approval={t.waiting_approval}"]),
            "long_term_memory": (_saturate(t.memories_active, 200), [f"memories_active={t.memories_active}",
                                                                     f"memories_recent={t.memories_recent}"]),
            "curiosity": (_saturate(t.research_tasks_open * 2 + t.memory_candidates_pending, 6),
                          [f"research_open={t.research_tasks_open}", f"candidates_pending={t.memory_candidates_pending}"]),
            "energy_budget": (1.0 - load if t.sources.get("resources") == "ok" else 0.5,
                              [f"cpu={t.cpu_percent}%", f"ram={t.memory_percent}%", f"gpu_free={t.gpu_free_mib}MiB"]),
            "affective_ui_state": (1.0 - min(1.0, failure_rate * 1.5 + _saturate(t.waiting_approval, 10) * 0.3),
                                   [f"failure_rate={failure_rate:.2f}", f"waiting_approval={t.waiting_approval}"]),
            "metacognition": (_saturate(t.memory_revisions_recent + t.failed_recent, 5),
                              [f"revisions_recent={t.memory_revisions_recent}", f"failures_reflected={t.failed_recent}"]),
            "intent": (_saturate(t.queued + t.waiting_approval, 5), [f"queued={t.queued}",
                                                                     f"waiting_approval={t.waiting_approval}"]),
            "agency": (0.0 if t.safety_mode in {"paused", "killed"} else
                       (1.0 - _saturate(t.waiting_approval, 4) * 0.5) * (1.0 - min(1.0, t.breaker_failures / 3)),
                       [f"safety_mode={t.safety_mode}", f"breaker_failures={t.breaker_failures}"]),
            "self_model": (sum(1 for v in t.sources.values() if v == "ok") / max(1, len(t.sources)),
                           [f"{k}={v}" for k, v in sorted(t.sources.items())]),
        }
        return {name: {"value": _clip(value), "level": _level(_clip(value)), "drivers": drivers}
                for name, (value, drivers) in dims.items()}

    # ------------------------------------------------------------------ phase machine
    def _candidate_phase(self, t: Telemetry, d: dict[str, dict[str, Any]]) -> str:
        if t.safety_mode == "killed" or t.safety_mode == "paused":
            return "halted"
        if d["energy_budget"]["value"] < 0.15 or d["affective_ui_state"]["value"] < 0.35:
            return "strained"
        if t.running > 0:
            return "focused"
        if d["curiosity"]["value"] >= 0.4:
            return "exploring"
        if t.memory_candidates_pending or t.memories_recent:
            return "consolidating"
        return "dormant"

    def evaluate(self, telemetry: Telemetry, *, previous_phase: str | None = None,
                 previous_dimensions: dict[str, dict[str, Any]] | None = None, persist: bool = True) -> dict[str, Any]:
        dims = self.dimensions(telemetry)
        if previous_dimensions:  # hysteresis: ignore jitter smaller than the band
            for name in DIMENSIONS:
                old = previous_dimensions.get(name, {}).get("value")
                if isinstance(old, (int, float)) and abs(dims[name]["value"] - old) < self.hysteresis:
                    dims[name]["value"] = old
                    dims[name]["level"] = _level(old)
        candidate = self._candidate_phase(telemetry, dims)
        previous = previous_phase if previous_phase in PHASES else "dormant"
        if candidate not in LEGAL_TRANSITIONS[previous]:
            # e.g. halted → focused is illegal: must pass through dormant (explicit resume) first
            candidate = "dormant" if previous == "halted" and telemetry.safety_mode == "running" else previous
        snapshot = {"api_version": 1, "label": HONESTY_LABEL, "phase": candidate, "previous_phase": previous,
                    "dimensions": dims, "telemetry": asdict(telemetry), "generated_at": self.clock()}
        if persist and self.db is not None:
            with self.db:
                self.db.execute("INSERT INTO consciousness_snapshots(created_at,phase,dimensions_json,telemetry_json)"
                                " VALUES(?,?,?,?)", (snapshot["generated_at"], candidate, json.dumps(dims),
                                                     json.dumps(asdict(telemetry))))
        return snapshot

    def last(self) -> dict[str, Any] | None:
        if self.db is None:
            return None
        row = self.db.execute("SELECT * FROM consciousness_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"phase": row["phase"], "dimensions": json.loads(row["dimensions_json"]), "created_at": row["created_at"]}

    def timeline(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = self.db.execute("SELECT id,created_at,phase FROM consciousness_snapshots ORDER BY id DESC LIMIT ?",
                               (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]


def read_core_status(workspace: str | Path, *, resource_probe: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Status-API entry: collect live telemetry, evaluate against the last snapshot, persist."""
    root = Path(workspace)
    telemetry = collect_telemetry(task_db=root / "data" / "tasks" / "muse.sqlite3",
                                  memory_db=root / "data" / "memory" / "muse.sqlite3",
                                  safety_db=root / "data" / "autonomy" / "safety.sqlite3",
                                  resource_probe=resource_probe)
    with ConsciousnessCore(root / "data" / "consciousness" / "core.sqlite3") as core:
        last = core.last() or {}
        snapshot = core.evaluate(telemetry, previous_phase=last.get("phase"), previous_dimensions=last.get("dimensions"))
        snapshot["timeline"] = core.timeline(limit=24)
    return snapshot
