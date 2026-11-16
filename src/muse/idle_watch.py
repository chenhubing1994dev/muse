"""Adaptive local inactivity watcher for Muse progress (not ChatGPT turn telemetry)."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from muse.handoff import write_resume_prompt
from muse.progress import ProgressEventStore

MEANINGFUL_CATEGORIES = ("task", "agent", "research", "test", "review")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def latest_event_timestamp(events: list[dict[str, object]]) -> datetime | None:
    """Return the chronologically latest valid event time, normalized to UTC."""
    timestamps = [
        parsed for event in events
        if (parsed := _parse_timestamp(str(event.get("created_at", "")))) is not None
    ]
    return max(timestamps) if timestamps else None


def adaptive_idle_threshold(
    events: list[dict[str, object]],
    *,
    fallback_seconds: int = 900,
    minimum_seconds: int = 300,
    maximum_seconds: int = 1800,
) -> int:
    """Estimate a conservative idle threshold from recent meaningful event gaps."""
    if minimum_seconds < 1 or maximum_seconds < minimum_seconds or fallback_seconds < 1:
        raise ValueError("invalid idle threshold bounds")
    timestamps = sorted(
        (parsed for event in events if (parsed := _parse_timestamp(str(event.get("created_at", ""))))),
    )
    if len(timestamps) < 3:
        return min(max(fallback_seconds, minimum_seconds), maximum_seconds)
    recent = timestamps[-12:]
    gaps = [
        (right - left).total_seconds()
        for left, right in zip(recent, recent[1:])
        if (right - left).total_seconds() > 0
    ]
    if len(gaps) < 2:
        estimate = fallback_seconds
    else:
        estimate = int(statistics.median(gaps) * 1.5)
    return min(max(estimate, minimum_seconds), maximum_seconds)


def check_idle(
    database: str | Path,
    *,
    progress_path: str | Path,
    architecture_path: str | Path,
    output_path: str | Path,
    now: datetime | None = None,
    fallback_seconds: int = 900,
    minimum_seconds: int = 300,
    maximum_seconds: int = 1800,
    cooldown_seconds: int = 1800,
) -> dict[str, object]:
    """Generate a continuation prompt after Muse's own progress has gone quiet."""
    database_path = Path(database)
    if not database_path.is_file():
        return {"status": "not_initialized", "threshold_seconds": None,
                "last_progress_at": None, "prompt": None}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    with ProgressEventStore(database) as store:
        events: list[dict[str, object]] = []
        for category in MEANINGFUL_CATEGORIES:
            events.extend(store.list_events(category=category, limit=40))
        latest = latest_event_timestamp(events)
        threshold = adaptive_idle_threshold(            events, fallback_seconds=fallback_seconds,
            minimum_seconds=minimum_seconds, maximum_seconds=maximum_seconds,
        )

    output = Path(output_path)
    idle_seconds = (current - latest).total_seconds() if latest else None
    if idle_seconds is None:
        return {"status": "no_progress_history", "threshold_seconds": threshold,
                "last_progress_at": None, "prompt": None}
    if idle_seconds < threshold:
        return {"status": "active", "threshold_seconds": threshold,
                "idle_seconds": int(idle_seconds), "last_progress_at": latest.isoformat(),
                "prompt": None}

    if output.is_file():
        age = (current.timestamp() - output.stat().st_mtime)
        if age < cooldown_seconds:
            return {"status": "cooldown", "threshold_seconds": threshold,
                    "idle_seconds": int(idle_seconds), "last_progress_at": latest.isoformat(),
                    "prompt": str(output)}

    write_resume_prompt(output, progress_path, architecture_path)
    with ProgressEventStore(database) as store:
        store.record(
            "system", "idle-watch", "progress",
            summary=f"Generated continuation prompt after {int(idle_seconds)}s without Muse progress.",
        )
    return {"status": "prompt_generated", "threshold_seconds": threshold,
            "idle_seconds": int(idle_seconds), "last_progress_at": latest.isoformat(),
            "prompt": str(output)}


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Watch Muse progress and generate a resume prompt when idle")
    parser.add_argument("--db", default=str(root / "data" / "tasks" / "muse.sqlite3"))
    parser.add_argument("--progress", default=str(root / "docs" / "PROGRESS.md"))
    parser.add_argument("--architecture", default=str(root / "docs" / "ARCHITECTURE.md"))
    parser.add_argument("--output", default=str(root / "data" / "handoff" / "continue_prompt.md"))
    parser.add_argument("--interval", type=int, default=60, help="poll interval in seconds")
    parser.add_argument("--once", action="store_true", help="check once (the default)")
    parser.add_argument("--loop", action="store_true", help="poll until interrupted")
    parser.add_argument("--fallback-seconds", type=int, default=900)
    args = parser.parse_args(argv)

    def check() -> dict[str, object]:
        return check_idle(
            args.db, progress_path=args.progress, architecture_path=args.architecture,
            output_path=args.output, fallback_seconds=args.fallback_seconds,
        )

    if args.loop:
        if args.interval < 5:
            parser.error("--interval must be at least 5 seconds")
        try:
            while True:
                print(json.dumps(check(), ensure_ascii=False))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
    print(json.dumps(check(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
