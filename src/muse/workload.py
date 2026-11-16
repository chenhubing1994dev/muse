"""Deterministic hourly workload estimation and capacity planning."""

from __future__ import annotations

from typing import Any, Iterable


def plan_hourly_work(
    tasks: Iterable[dict[str, Any]],
    *,
    window_seconds: int = 3600,
    reserve_seconds: int = 300,
    default_estimate_seconds: int = 900,
) -> dict[str, Any]:
    """Pack highest-priority work into an hour, preserving a health-check reserve.

    Unknown estimates use a conservative default. Tasks that do not fit are
    deferred while smaller lower-priority tasks may still be selected. This is
    planning only: it does not enqueue, approve, execute, or discard work.
    """
    for name, value in (("window_seconds", window_seconds),
                        ("default_estimate_seconds", default_estimate_seconds)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if (isinstance(reserve_seconds, bool) or not isinstance(reserve_seconds, int)
            or reserve_seconds < 0 or reserve_seconds >= window_seconds):
        raise ValueError("reserve_seconds must be between zero and window_seconds")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("every task must have a non-empty id")
        if task_id in seen:
            raise ValueError("task ids must be unique")
        seen.add(task_id)
        priority = task.get("priority", 50)
        estimate = task.get("estimated_seconds", default_estimate_seconds)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be an integer from 0 to 100")
        if isinstance(estimate, bool) or not isinstance(estimate, int) or estimate <= 0:
            raise ValueError("estimated_seconds must be a positive integer")
        normalized.append({"id": task_id, "priority": priority, "estimated_seconds": estimate})

    normalized.sort(key=lambda item: (-item["priority"], item["estimated_seconds"], item["id"]))
    available = window_seconds - reserve_seconds
    remaining = available
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for task in normalized:
        if task["estimated_seconds"] <= remaining:
            selected.append(task)
            remaining -= task["estimated_seconds"]
        else:
            deferred.append(task)

    if not selected and deferred:
        capacity_state = "no_fit"
    elif remaining >= default_estimate_seconds:
        capacity_state = "underfilled"
    else:
        capacity_state = "balanced"
    return {
        "window_seconds": window_seconds,
        "reserve_seconds": reserve_seconds,
        "available_work_seconds": available,
        "estimated_work_seconds": available - remaining,
        "remaining_work_budget_seconds": remaining,
        "capacity_state": capacity_state,
        "selected_ids": [item["id"] for item in selected],
        "deferred_ids": [item["id"] for item in deferred],
        "selected": selected,
        "deferred": deferred,
        "task_count": len(normalized),
    }
