"""Show the next hourly work batch from Muse's durable task queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from muse.task_queue import TaskQueue
from muse.workload import plan_hourly_work


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan queued work for one hourly window")
    parser.add_argument("--db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--window-seconds", type=int, default=3600)
    parser.add_argument("--reserve-seconds", type=int, default=300)
    parser.add_argument("--default-estimate-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    with TaskQueue(Path(args.db)) as queue:
        items = queue.queued_work_items()
    plan = plan_hourly_work(
        items,
        window_seconds=args.window_seconds,
        reserve_seconds=args.reserve_seconds,
        default_estimate_seconds=args.default_estimate_seconds,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
