"""Privacy-conscious half-hour operational reports for Muse."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import os
import re

from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.workload import plan_hourly_work


def _latest_progress_note(project_root: Path) -> tuple[str | None, list[str]]:
    """Read only the newest authored project-progress section, never task payloads."""
    path = project_root / "docs" / "PROGRESS.md"
    if not path.is_file():
        return None, []
    lines = path.read_text(encoding="utf-8").splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not headings:
        return None, []
    start = headings[-1]
    bullets = [line.strip() for line in lines[start + 1:] if line.strip().startswith("-")]
    return lines[start][3:].strip(), bullets[:8]


def _next_backlog_item(project_root: Path) -> tuple[str | None, list[str]]:
    """Find the first explicitly incomplete capability and its concise next steps."""
    path = project_root / "docs" / "CAPABILITY_BACKLOG.md"
    if not path.is_file():
        return None, []
    lines = path.read_text(encoding="utf-8").splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("### ")]
    incomplete_markers = ("未實作", "未实现", "未部署", "未完成", "未驗證", "未验证")
    for index, start in enumerate(headings):
        end = headings[index + 1] if index + 1 < len(headings) else len(lines)
        section = lines[start + 1:end]
        if not any(any(marker in line for marker in incomplete_markers) for line in section):
            continue
        details = [line.strip() for line in section if line.strip().startswith("-")]
        return lines[start][4:].strip(), details[:5]
    return None, []


def create_half_hour_report(
    database: str | Path,
    output_dir: str | Path,
    *,
    now: datetime | None = None,
    window_minutes: int = 30,
    reserve_seconds: int = 180,
) -> dict[str, Any]:
    """Write a local Markdown report with operational counts and project milestones."""
    if isinstance(window_minutes, bool) or not isinstance(window_minutes, int) or window_minutes <= 0:
        raise ValueError("window_minutes must be a positive integer")
    if isinstance(reserve_seconds, bool) or not isinstance(reserve_seconds, int) or reserve_seconds < 0:
        raise ValueError("reserve_seconds must be a non-negative integer")

    database_path = Path(database)
    if not database_path.is_file():
        return {"status": "not_initialized", "path": None, "window_minutes": window_minutes}

    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    cutoff = checked_at - timedelta(minutes=window_minutes)

    with TaskQueue(database_path) as queue:
        snapshot = queue.snapshot()
        queued = queue.queued_work_items()
        runtime_calibration = queue.runtime_estimate_seconds()
    workload = plan_hourly_work(
        queued,
        window_seconds=window_minutes * 60,
        reserve_seconds=reserve_seconds,
    )

    with ProgressEventStore(database_path) as store:
        recent = store.list_events(limit=200)
    recent = [
        event for event in recent
        if cutoff <= datetime.fromisoformat(event["created_at"]).astimezone(timezone.utc) <= checked_at
    ]
    activity_counts = dict(sorted(Counter(
        f"{event['category']}.{event['event']}" for event in recent
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", str(event.get("category", "")))
        and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", str(event.get("event", "")))
    ).items()))
    timeline = []
    for event in recent[:8]:
        category, event_name = str(event.get("category", "")), str(event.get("event", ""))
        if not (re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", category)
                and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", event_name)):
            continue
        timeline.append({"at": event["created_at"], "event": f"{category}.{event_name}"})
    timeline.reverse()
    last_progress_at = recent[0]["created_at"] if recent else None
    counts = snapshot["counts"]

    project_root = database_path.parent.parent.parent
    progress_heading, progress_bullets = _latest_progress_note(project_root)
    next_heading, next_bullets = _next_backlog_item(project_root)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = checked_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = output / f"muse-report-{stamp}.md"
    lines = [
        f"# {window_minutes}-minute Muse report",
        "",
        f"- Window: {cutoff.isoformat()} to {checked_at.isoformat()}",
        f"- Health snapshot: queue={counts['queued']} queued, {counts['running']} running, {counts['waiting_approval']} awaiting approval, {counts['failed']} failed",
        f"- Worker state: {'active' if counts['running'] else 'idle'}",
        f"- Workload: {workload['capacity_state']} ({workload['estimated_work_seconds']} / {workload['available_work_seconds']} estimated seconds planned; {workload['remaining_work_budget_seconds']} seconds remaining)",
        f"- Planned queued tasks: {len(workload['selected_ids'])}; deferred: {len(workload['deferred_ids'])}",
        f"- Runtime estimate calibration: {runtime_calibration['estimated_seconds']} seconds ({runtime_calibration['source']}; samples={runtime_calibration['sample_count']})",
        f"- Last recorded progress: {last_progress_at or 'none'}",
        "",
        "## Recent event timeline (metadata only)",
    ]
    if timeline:
        lines.extend(f"- {item['at']}: {item['event']}" for item in timeline)
    else:
        lines.append("- No safe timeline events in this window.")
    lines.extend(["", "## Activity in this window"])
    if activity_counts:
        lines.extend(f"- {name}: {count}" for name, count in activity_counts.items())
    else:
        lines.append("- No recorded progress events in this window.")
    lines.extend(["", "## Latest implemented progress"])
    if progress_heading:
        lines.append(f"- {progress_heading}")
        lines.extend(progress_bullets)
    else:
        lines.append("- No project progress note found.")
    lines.extend(["", "## Next planned capability"])
    if next_heading:
        lines.append(f"- {next_heading}")
        lines.extend(next_bullets)
    else:
        lines.append("- No explicitly incomplete backlog item found.")
    lines.extend([
        "",
        "## Privacy and interpretation",
        "- This report omits task titles, payloads, progress-event summaries, private notes and credentials.",
        "- Project progress is read from the latest section of docs/PROGRESS.md; next work is read from the first incomplete docs/CAPABILITY_BACKLOG.md item.",
        "- Estimated workload is a planning hint, not a hard execution deadline or proof that a worker is running.",
        "- An idle queue means no work was queued; it is not itself a fault.",
        "",
    ])
    temporary = report_path.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, report_path)
    return {
        "status": "created", "path": str(report_path), "window_minutes": window_minutes,
        "activity_counts": activity_counts, "timeline": timeline, "workload": workload,
        "runtime_calibration": runtime_calibration,
        "queue_counts": counts, "last_progress_at": last_progress_at,
        "latest_progress_heading": progress_heading, "next_backlog_heading": next_heading,
    }
