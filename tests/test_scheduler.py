from __future__ import annotations

import time

import tempfile
import unittest
from pathlib import Path

from muse.task_queue import TaskQueue
from muse.worker import run_forever
from muse.worker import run_once


class StopAfterWait:
    def __init__(self, waits: int = 1) -> None:
        self.waits = waits
        self.waited: list[float] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, timeout: float | None = None) -> bool:
        self.waited.append(timeout)
        if len(self.waited) >= self.waits:
            self.stopped = True
        return self.stopped


class SchedulerTests(unittest.TestCase):
    def test_scheduler_processes_tasks_and_stops_after_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                queue.enqueue("first", {"kind": "echo", "input": "hello"})
            stop = StopAfterWait()

            result = run_forever(database, stop_event=stop, poll_interval=0.25)

            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["idle_polls"], 1)
            self.assertEqual(stop.waited, [0.25])
            with TaskQueue(database) as queue:
                tasks = queue._connection.execute("SELECT status FROM tasks").fetchall()
            self.assertEqual([row["status"] for row in tasks], ["succeeded"])

    def test_scheduler_waits_when_queue_is_empty_instead_of_spinning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stop = StopAfterWait()
            result = run_forever(
                Path(directory) / "tasks.sqlite3", stop_event=stop, poll_interval=2.0
            )
            self.assertEqual(result["processed"], 0)
            self.assertEqual(result["idle_polls"], 1)
            self.assertEqual(stop.waited, [2.0])


    def test_worker_heartbeats_long_running_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("slow", {"kind": "slow"})
            result = run_once(
                database,
                handlers={"slow": lambda _payload: (time.sleep(0.8), {"ok": True})[1]},
                lease_seconds=1,
            )
            self.assertEqual(result["status"], "succeeded")
            with TaskQueue(database) as queue:
                stored = queue.get(task["id"])
            self.assertEqual(stored["attempts"], 1)
            self.assertIsNone(stored["lease_owner"])


if __name__ == "__main__":
    unittest.main()
