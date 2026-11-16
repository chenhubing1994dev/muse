"""Local health checks and periodic workload reporting for Muse."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from muse.progress import ProgressEventStore
from muse.agent_discovery import probe_configured_health_endpoints
from muse.model_service import probe_configured_model_service
from muse.reminders import create_operational_reminders
from muse.reporting import create_half_hour_report
from muse.task_queue import TaskQueue
from muse.workload import plan_hourly_work


_MEANINGFUL_TASK_EVENTS = frozenset({
    "workflow.stage_changed", "workflow.completed", "workflow.failed",
    "report.step_submitted", "report.submitted", "research.knowledge_recorded",
    "commander.review_finished", "commander.plan_created",
})


def _parse_timestamp(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) if parsed is not None else None
    except (TypeError, ValueError):
        return None


def _pid_is_alive(pid: int) -> bool:
    """Return whether a local process PID exists; permission denial means alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if getattr(exc, "winerror", None) == 5:
            return True
        if getattr(exc, "winerror", None) == 87:
            return False
        raise
    return True


def run_check(
    database: str | Path, *, report_dir: str | Path | None = None,
    window_minutes: int = 30, reminder_database: str | Path | None = None,
    agent_health_config: str | Path | None = None,
    model_workspace: str | Path | None = None,
) -> dict[str, object]:
    """Inspect operational state, optionally create redacted reminders and a local report."""
    path = Path(database)
    if not path.is_file():
        return {
            "status": "not_initialized", "database_exists": False,

            "worker_state": "unknown", "queue": None, "workload": None,
            "expired_leases": 0, "last_progress_at": None, "report": None,
            "issues": ["Muse database does not exist; no files were created."],
            "reminders_created": [],
        }

    now = datetime.now(timezone.utc)
    with TaskQueue(path) as queue_store:
        snapshot = queue_store.snapshot()
        queued_work = queue_store.queued_work_items()
        failure_classification = queue_store.aggregate_failure_metrics(
            since=(now - timedelta(hours=24)).isoformat()
        )
        task_events = {
            item["id"]: queue_store.events(item["id"], limit=100)
            for item in snapshot["active"] if item["status"] == "running"
        }
    workload = plan_hourly_work(queued_work)
    counts = snapshot["counts"]
    expired = 0
    stalled = 0
    meaningful_progress_count = 0
    last_meaningful_progress_at = None
    for item in snapshot["active"]:
        if item["status"] != "running":
            continue
        lease_expired = False
        if item["lease_expires_at"]:
            expiry = _parse_timestamp(item["lease_expires_at"])
            lease_expired = expiry is None or expiry <= now
            expired += lease_expired
        events = task_events.get(item["id"], [])
        meaningful = [event for event in events if event["event_type"] in _MEANINGFUL_TASK_EVENTS]
        latest_meaningful = _parse_timestamp(meaningful[-1]["created_at"]) if meaningful else None
        latest_event = _parse_timestamp(events[-1]["created_at"]) if events else _parse_timestamp(item.get("created_at"))
        if latest_meaningful is not None:
            if last_meaningful_progress_at is None or latest_meaningful > last_meaningful_progress_at:
                last_meaningful_progress_at = latest_meaningful
            if (now - latest_meaningful).total_seconds() <= 900:
                meaningful_progress_count += 1
        baseline = max(
            (value for value in (latest_event, latest_meaningful) if value is not None),
            default=None,
        )
        if not lease_expired and (baseline is None or (now - baseline).total_seconds() > 900):
            stalled += 1

    if counts["running"]:
        worker_state = "active_but_stale" if expired else "active"
    elif counts["queued"]:
        worker_state = "idle_with_queued_work"
    else:
        worker_state = "idle"

    issues: list[str] = []
    if expired:
        issues.append("One or more running task leases have expired.")
    if stalled:
        issues.append("One or more running tasks have had no meaningful progress for over 15 minutes.")
    if counts["queued"]:
        issues.append("Queued tasks are waiting for a worker.")
    if counts["failed"]:
        issues.append("Failed tasks exist; inspect task records before retrying.")
    if counts["waiting_approval"]:
        issues.append("One or more tasks are waiting for human approval.")

    endpoint_health: dict[str, object] = {
        "status": "not_configured", "configured": 0, "probed": 0,
        "healthy": 0, "unavailable": 0, "disabled": 0, "executed_programs": 0,
    }
    if agent_health_config is not None:
        try:
            endpoint_health = probe_configured_health_endpoints(agent_health_config)
            if endpoint_health["unavailable"]:
                issues.append("One or more configured local tool health endpoints are unavailable.")
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            endpoint_health = {
                "status": "configuration_error", "configured": 0, "probed": 0,
                "healthy": 0, "unavailable": 0, "disabled": 0, "executed_programs": 0,
            }
            issues.append("Configured local tool health checks could not be completed.")

    model_service: dict[str, object] = {"status": "not_configured"}
    if model_workspace is not None:
        model_service = probe_configured_model_service(str(model_workspace))
        if model_service.get("status") not in {"healthy", "not_managed"}:
            issues.append("The configured local model service is unavailable or does not expose the configured model.")
    with ProgressEventStore(path) as progress:
        latest = progress.list_events(limit=1)
        last_progress_at = latest[0]["created_at"] if latest else None
        workers = progress.list_workers(stale_after_seconds=45)
        stale_workers = sum(worker["stale"] for worker in workers)
        dead_workers = [
            worker for worker in workers
            if worker["state"] == "active" and not _pid_is_alive(worker["pid"])
        ]
        if stale_workers:
            issues.append("One or more persistent worker heartbeats are stale.")
        if dead_workers:
            issues.append("One or more registered worker processes no longer exist.")
        status = "warning" if issues else "healthy"
        progress.record_watchdog_snapshot(
            status=status, expired_leases=expired, stalled_tasks=stalled,
            stale_workers=stale_workers, dead_workers=len(dead_workers),
            queued_tasks=counts["queued"], failed_tasks=counts["failed"],
            waiting_approval=counts["waiting_approval"],
            endpoint_unavailable=int(endpoint_health["unavailable"]), now=now,
        )
        history = progress.watchdog_trend(now=now)
        progress.record(
            "system", "watchdog", "completed",
            summary=f"Self-check completed: status={status}; worker={worker_state}; "                    f"queued={counts['queued']}; capacity={workload['capacity_state']}; "
                    f"endpoints={endpoint_health['probed']}; endpoint_unavailable={endpoint_health['unavailable']}; "
                    f"model_service={model_service.get('status', 'not_configured')}.",
        )
    reminders_created = []
    if reminder_database is not None:
        reminders_created = create_operational_reminders(
            reminder_database, counts=counts, expired_leases=expired,
            stale_workers=stale_workers, dead_workers=len(dead_workers),
            stalled_tasks=stalled, now=now,
        )

    report = None
    if report_dir is not None:
        report = create_half_hour_report(path, report_dir, window_minutes=window_minutes)

    return {
        "status": status, "database_exists": True, "checked_at": now.isoformat(),
        "worker_state": worker_state,
        "queue": {"counts": counts, "active_truncated": snapshot["active_truncated"]},
        "workload": workload, "expired_leases": expired, "stalled_tasks": stalled,
        "failure_classification": failure_classification,
        "last_progress_at": last_progress_at,
        "report": report, "issues": issues, "history": history,
        "meaningful_progress": {
            "active_tasks_with_recent_progress": meaningful_progress_count,
            "last_meaningful_progress_at": last_meaningful_progress_at.isoformat() if last_meaningful_progress_at else None,
            "event_types": sorted(_MEANINGFUL_TASK_EVENTS),
        },
        "reminders_created": [
            {"id": item["id"], "title": item["title"], "source": item["source"]}
            for item in reminders_created
        ],
        "worker_runtime": {
            "active": sum(
                w["state"] == "active" and not w["stale"] and _pid_is_alive(w["pid"])
                for w in workers
            ),
            "stale": stale_workers,
            "dead": len(dead_workers),
            "workers": workers,
        },
        "configured_endpoints": endpoint_health,
        "model_service": model_service,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Muse self-check and periodic report")
    parser.add_argument("--db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--once", action="store_true", help="run one check (the default)")
    parser.add_argument("--report-dir", help="write a privacy-conscious Markdown report")
    parser.add_argument("--window-minutes", type=int, default=30)
    parser.add_argument("--agent-health-config", help="optional JSON config of explicit loopback tool health endpoints")
    parser.add_argument("--model-workspace", help="optional Muse workspace whose configured local model endpoint should be probed")
    parser.add_argument(
        "--reminder-db", default="data/reminders/muse.sqlite3",
        help="local SQLite reminder inbox for operational alerts",
    )
    args = parser.parse_args(argv)
    report = run_check(
        Path(args.db),
        report_dir=Path(args.report_dir) if args.report_dir else None,
        window_minutes=args.window_minutes,
        reminder_database=Path(args.reminder_db),
        agent_health_config=Path(args.agent_health_config) if args.agent_health_config else None,
        model_workspace=Path(args.model_workspace) if args.model_workspace else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "warning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
