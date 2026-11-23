from __future__ import annotations

import tempfile
import json
import threading
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.watchdog import run_check


class WatchdogTests(unittest.TestCase):
    def test_check_reports_idle_queue_and_records_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database):
                pass
            report = run_check(database)
            self.assertEqual(report["status"], "healthy")
            self.assertEqual(report["worker_state"], "idle")
            self.assertEqual(report["queue"]["counts"]["queued"], 0)
            self.assertEqual(report["workload"]["capacity_state"], "underfilled")
            with ProgressEventStore(database) as store:
                events = store.list_events(category="system", subject_id="watchdog")
            self.assertEqual(events[0]["event"], "completed")
            self.assertIn("Self-check completed", events[0]["summary"])

    def test_watchdog_records_redacted_history_and_returns_trend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database) as queue:
                failed = queue.enqueue("PRIVATE TITLE", {"secret": "private"})
                queue._connection.execute("UPDATE tasks SET status='failed' WHERE id=?", (failed["id"],))
                queue._connection.commit()
            report = run_check(database)
            history = report["history"]
            self.assertEqual(history["current"]["checks"], 1)
            self.assertEqual(history["current"]["warning_checks"], 1)
            self.assertEqual(history["current"]["failed_tasks"], 1)
            self.assertEqual(history["trend"], "insufficient_data")
            self.assertNotIn("PRIVATE TITLE", str(history))
            self.assertNotIn("private", str(history))

    def test_watchdog_exposes_only_aggregated_classified_failure_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database) as queue:
                for index in range(2):
                    task = queue.enqueue(f"PRIVATE TASK {index}", {"secret": "PRIVATE PAYLOAD"})
                    queue.claim_next(worker_id=f"worker-{index}", task_id=task["id"])
                    queue.finish(task["id"], status="failed", worker_id=f"worker-{index}", result={
                        "error_code": "provider.unavailable", "failure_category": "provider",
                        "retryable": True, "recovery_guidance": "Check the configured endpoint.",
                        "private_detail": "PRIVATE EXCEPTION",
                    })
            report = run_check(database)
            metrics = report["failure_classification"]
            self.assertEqual(metrics["total_failures"], 2)
            self.assertEqual(metrics["by_code"], {"provider.unavailable": 2})
            self.assertEqual(metrics["by_category"], {"provider": 2})
            self.assertEqual(metrics["retryable"], 2)
            self.assertNotIn("PRIVATE TASK", str(report))
            self.assertNotIn("PRIVATE PAYLOAD", str(report))
            self.assertNotIn("PRIVATE EXCEPTION", str(report))

    def test_check_flags_expired_running_lease_without_exposing_task_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("PRIVATE TITLE", {"kind": "echo"})
                queue.claim_next(worker_id="dead-worker", lease_seconds=1)
                expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
                queue._connection.execute(
                    "UPDATE tasks SET lease_expires_at=? WHERE id=?", (expired, task["id"])
                )
                queue._connection.commit()
            report = run_check(database)
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["worker_state"], "active_but_stale")
            self.assertEqual(report["expired_leases"], 1)
            self.assertNotIn("PRIVATE TITLE", str(report))

    def test_watchdog_reports_hourly_workload_capacity_without_task_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue(
                    "private title", {"kind": "echo", "secret": "private"},
                    priority=90, estimated_seconds=1200,
                )
            report = run_check(database)
            workload = report["workload"]
            self.assertEqual(workload["selected_ids"], [task["id"]])
            self.assertEqual(workload["estimated_work_seconds"], 1200)
            self.assertEqual(workload["capacity_state"], "underfilled")
            self.assertNotIn("private", str(workload))

    def test_watchdog_can_write_a_half_hour_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "muse.sqlite3"
            with TaskQueue(database):
                pass
            report = run_check(database, report_dir=root / "reports", window_minutes=30)
            self.assertEqual(report["report"]["status"], "created")
            self.assertTrue(Path(report["report"]["path"]).is_file())

    def test_watchdog_reports_worker_runtime_liveness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database):
                pass
            with ProgressEventStore(database) as store:
                store.register_worker("worker-live", pid=456)
            with patch("muse.watchdog._pid_is_alive", return_value=True):
                report = run_check(database)
            self.assertEqual(report["worker_runtime"]["active"], 1)
            self.assertEqual(report["worker_runtime"]["stale"], 0)
            self.assertEqual(report["worker_runtime"]["workers"][0]["pid"], 456)

    def test_watchdog_warns_when_worker_heartbeat_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database):
                pass
            old = datetime.now(timezone.utc) - timedelta(minutes=5)
            with ProgressEventStore(database) as store:
                store.register_worker("worker-stale", pid=789, now=old)
            with patch("muse.watchdog._pid_is_alive", return_value=True):
                report = run_check(database)
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["worker_runtime"]["stale"], 1)
            self.assertIn("heartbeats are stale", report["issues"][0])

    def test_watchdog_detects_dead_worker_process_even_with_fresh_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database):
                pass
            with ProgressEventStore(database) as store:
                store.register_worker("worker-dead", pid=987654)
            with patch("muse.watchdog._pid_is_alive", return_value=False):
                report = run_check(database)
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["worker_runtime"]["dead"], 1)
            self.assertIn("processes no longer exist", report["issues"][-1])

    def test_watchdog_creates_daily_deduplicated_redacted_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "tasks.sqlite3"
            reminders = root / "reminders.sqlite3"
            with TaskQueue(database) as queue:
                failed = queue.enqueue("PRIVATE TASK TITLE", {"secret": "private payload"})
                queue._connection.execute("UPDATE tasks SET status='failed' WHERE id=?", (failed["id"],))
                queue._connection.commit()
            first = run_check(database, reminder_database=reminders)
            second = run_check(database, reminder_database=reminders)
            self.assertEqual(len(first["reminders_created"]), 1)
            self.assertEqual(first["reminders_created"][0]["source"], "watchdog")
            self.assertEqual(first["reminders_created"][0]["id"], second["reminders_created"][0]["id"])
            self.assertNotIn("PRIVATE TASK TITLE", str(first["reminders_created"]))
            self.assertNotIn("private payload", str(first["reminders_created"]))
            from muse.reminders import ReminderStore
            with ReminderStore(reminders) as store:
                pending = store.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertIn("失败任务", pending[0]["title"])

    def test_watchdog_detects_stalled_running_task_and_creates_redacted_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "tasks.sqlite3"
            reminders = root / "reminders.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("PRIVATE STALLED TASK", {"private": "payload"})
                queue.claim_next(worker_id="worker-stalled", lease_seconds=3600)
                old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
                queue._connection.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (old, old, task["id"]))
                queue._connection.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (old, task["id"]))
                queue._connection.commit()
            report = run_check(database, reminder_database=reminders)
            self.assertEqual(report["stalled_tasks"], 1)
            self.assertIn("no meaningful progress", str(report["issues"]))
            self.assertEqual(len(report["reminders_created"]), 1)
            self.assertNotIn("PRIVATE STALLED TASK", str(report))
            from muse.reminders import ReminderStore
            with ReminderStore(reminders) as store:
                self.assertIn("长时间没有进展", store.list_pending()[0]["title"])

    def test_missing_database_is_reported_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.sqlite3"
            report = run_check(database)
            self.assertEqual(report["status"], "not_initialized")
            self.assertFalse(database.exists())


    def test_watchdog_distinguishes_heartbeat_from_meaningful_task_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
            with TaskQueue(database) as queue:
                stalled = queue.enqueue("stalled", {"kind": "echo"})
                queue.claim_next(worker_id="worker-a", lease_seconds=3600, task_id=stalled["id"])
                queue._connection.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, stalled["id"]))
                queue._connection.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (old, stalled["id"]))
                queue.heartbeat(stalled["id"], worker_id="worker-a", lease_seconds=3600)
                queue._connection.commit()

                progressing = queue.enqueue("progressing", {"kind": "echo"})
                queue.claim_next(worker_id="worker-b", lease_seconds=3600, task_id=progressing["id"])
                queue._connection.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, progressing["id"]))
                queue._connection.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (old, progressing["id"]))
                queue.heartbeat(progressing["id"], worker_id="worker-b", lease_seconds=3600)
                queue._connection.commit()
                queue.record_event(progressing["id"], "workflow.stage_changed", actor="commander", payload={"stage": "research"})
            report = run_check(database)
            self.assertEqual(report["stalled_tasks"], 1)
            self.assertEqual(report["meaningful_progress"]["active_tasks_with_recent_progress"], 1)
            self.assertIn("meaningful progress", str(report["issues"]))

    def test_watchdog_probes_only_explicit_loopback_endpoints_and_redacts_details(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({
                    "status": "ok", "agent_id": "private-agent-id", "role": "private-role",
                    "capabilities": ["private.capability"], "display_name": "private-display-name",
                }).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = root / "muse.sqlite3"
                config = root / "health.json"
                with TaskQueue(database):
                    pass
                config.write_text(json.dumps({"agents": [{
                    "agent_id": "private-agent-id",
                    "health_url": f"http://127.0.0.1:{server.server_port}/health",
                    "enabled": True,
                }]}), encoding="utf-8")
                report = run_check(database, agent_health_config=config)
                self.assertEqual(report["status"], "healthy")
                self.assertEqual(report["configured_endpoints"]["probed"], 1)
                self.assertEqual(report["configured_endpoints"]["healthy"], 1)
                self.assertEqual(report["configured_endpoints"]["executed_programs"], 0)
                serialized = json.dumps(report, ensure_ascii=False)
                for secret in ("private-agent-id", "private-role", "private.capability", "private-display-name", str(server.server_port)):
                    self.assertNotIn(secret, serialized)
                with ProgressEventStore(database) as store:
                    event = store.list_events(category="system", subject_id="watchdog", limit=1)[0]
                self.assertNotIn("private-agent-id", event["summary"])
                self.assertIn("endpoints=1", event["summary"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_watchdog_warns_for_unavailable_configured_endpoint_without_leaking_details(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "muse.sqlite3"
            config = root / "health.json"
            with TaskQueue(database):
                pass
            config.write_text(json.dumps({"agents": [{
                "agent_id": "private-unavailable-agent",
                "health_url": f"http://127.0.0.1:{port}/health",
                "enabled": True,
            }]}), encoding="utf-8")
            report = run_check(database, agent_health_config=config)
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["configured_endpoints"]["unavailable"], 1)
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("private-unavailable-agent", serialized)
            self.assertNotIn(str(port), serialized)

    def test_watchdog_skips_disabled_configured_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "muse.sqlite3"
            config = root / "health.json"
            with TaskQueue(database):
                pass
            config.write_text(json.dumps({"agents": [{
                "agent_id": "disabled-agent",
                "health_url": "http://127.0.0.1:8766/health",
                "enabled": False,
            }]}), encoding="utf-8")
            with patch("muse.agent_discovery.build_opener") as opener:
                report = run_check(database, agent_health_config=config)
            opener.return_value.open.assert_not_called()
            self.assertEqual(report["configured_endpoints"]["disabled"], 1)
            self.assertEqual(report["configured_endpoints"]["probed"], 0)
            self.assertEqual(report["status"], "healthy")

    def test_watchdog_can_probe_configured_local_model_and_warn_when_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "muse.sqlite3"
            with TaskQueue(database):
                pass
            with patch("muse.watchdog.probe_configured_model_service", return_value={
                "status": "unavailable", "provider": "llama_cpp", "model_configured": True,
                "model_available": False, "model_count": 0, "latency_ms": 5.0,
            }) as probe:
                report = run_check(database, model_workspace=root)
            probe.assert_called_once_with(str(root))
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["model_service"]["status"], "unavailable")
            self.assertIn("local model service", report["issues"][-1])
            with ProgressEventStore(database) as store:
                event = store.list_events(category="system", subject_id="watchdog", limit=1)[0]
            self.assertIn("model_service=unavailable", event["summary"])

    def test_watchdog_keeps_model_probe_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "muse.sqlite3"
            with TaskQueue(database):
                pass
            with patch("muse.watchdog.probe_configured_model_service") as probe:
                report = run_check(database)
            probe.assert_not_called()
            self.assertEqual(report["model_service"]["status"], "not_configured")

if __name__ == "__main__":
    unittest.main()
