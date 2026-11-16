"""Command-line controls for queueing local media work and approving tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from muse.task_queue import TaskQueue


def approve_task(database: str | Path, task_id: str, approved_by: str) -> dict[str, Any]:
    with TaskQueue(database) as queue:
        return queue.approve(task_id, approved_by=approved_by)

def cancel_task(
    database: str | Path, task_id: str, cancelled_by: str, reason: str = ""
) -> dict[str, Any]:
    with TaskQueue(database) as queue:
        return queue.cancel(task_id, cancelled_by=cancelled_by, reason=reason)



def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Queue local media work and inspect Muse task approval controls")
    parser.add_argument("--db", default="data/tasks/muse.sqlite3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    approve = subparsers.add_parser("approve", help="approve a task waiting for approval")
    approve.add_argument("task_id")
    approve.add_argument("--by", required=True, dest="approved_by")
    cancel = subparsers.add_parser("cancel", help="cancel a task that has not started")
    cancel.add_argument("task_id")
    cancel.add_argument("--by", required=True, dest="cancelled_by")
    cancel.add_argument("--reason", default="")
    list_tasks = subparsers.add_parser("list", help="show queue counts and active task summaries")
    list_tasks.add_argument("--limit", type=int, default=20)


    enqueue = subparsers.add_parser("enqueue-transcode", help="preview and queue an AMD AMF FFmpeg transcode")
    enqueue.add_argument("--capability-db", default="data/capabilities/muse.sqlite3")
    enqueue.add_argument("--input", required=True)
    enqueue.add_argument("--output", required=True)
    enqueue.add_argument("--codec", choices=("h264", "hevc", "av1"), default="h264")
    enqueue.add_argument("--bitrate", default="8M")
    enqueue.add_argument("--scale")
    args = parser.parse_args(argv)

    if args.command == "approve":
        try:
            task = approve_task(args.db, args.task_id, args.approved_by)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"{task['id']} {task['status']} approved_by={task['payload']['_approval']['approved_by']}")
        return 0

    if args.command == "cancel":
        try:
            task = cancel_task(args.db, args.task_id, args.cancelled_by, args.reason)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"{task['id']} {task['status']} cancelled_by={task['payload']['_cancellation']['cancelled_by']}")
        return 0

    if args.command == "list":
        try:
            with TaskQueue(args.db) as queue:
                snapshot = queue.snapshot(limit=args.limit)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0

    from muse.adapter_handlers import enqueue_ffmpeg_task
    from muse.capabilities import CapabilityRegistry

    request = {"input": args.input, "output": args.output, "codec": args.codec, "bitrate": args.bitrate}
    if args.scale:
        request["scale"] = args.scale
    try:
        with CapabilityRegistry(args.capability_db) as registry:
            task, preview = enqueue_ffmpeg_task(args.db, registry, request)
    except (ValueError, PermissionError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"task": task, "preview": preview, "approval_required": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())