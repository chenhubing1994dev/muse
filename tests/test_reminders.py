from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from muse.reminders import ReminderStore


class ReminderStoreTests(unittest.TestCase):
    def test_add_and_list_reminders_persist_without_exposing_acknowledged_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "reminders.sqlite3"
            due_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            with ReminderStore(db) as store:
                item = store.add("Review plan", "Check the next project milestone", due_at=due_at)
                self.assertEqual(item["status"], "pending")
                self.assertEqual(store.list_pending()[0]["id"], item["id"])
                store.acknowledge(item["id"])
                self.assertEqual(store.list_pending(), [])
            with ReminderStore(db) as store:
                self.assertEqual(store.list_pending(), [])
                self.assertEqual(store.list(include_acknowledged=True)[0]["status"], "acknowledged")

    def test_due_returns_only_pending_reminders_at_or_before_now(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                now = datetime.now(timezone.utc)
                store.add("Past", "Already due", due_at=(now - timedelta(seconds=1)).isoformat())
                store.add("Future", "Not yet", due_at=(now + timedelta(hours=1)).isoformat())
                self.assertEqual([item["title"] for item in store.due(now=now)], ["Past"])

    def test_deduplication_key_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                due = datetime.now(timezone.utc).isoformat()
                first = store.add("One", "Message", due_at=due, dedupe_key="task-123")
                second = store.add("Different", "Ignored duplicate", due_at=due, dedupe_key="task-123")
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(len(store.list(include_acknowledged=True)), 1)

    def test_invalid_time_and_text_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                with self.assertRaises(ValueError):
                    store.add("Title", "Message", due_at="tomorrow")
                with self.assertRaises(ValueError):
                    store.add("", "Message", due_at=datetime.now(timezone.utc).isoformat())

    def test_notified_reminder_is_not_delivered_twice_but_stays_pending_until_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                item = store.add("Due", "Reminder body", due_at=datetime.now(timezone.utc).isoformat())
                self.assertEqual(len(store.due()), 1)
                notified = store.mark_notified(item["id"])
                self.assertIsNotNone(notified["notified_at"])
                self.assertEqual(store.due(), [])
                self.assertEqual(store.list_pending()[0]["status"], "pending")
                store.acknowledge(item["id"])
                self.assertEqual(store.list_pending(), [])

    def test_existing_database_migrates_notified_at_column(self) -> None:
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("""CREATE TABLE reminders (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, message TEXT NOT NULL,
                due_at TEXT NOT NULL, source TEXT NOT NULL, dedupe_key TEXT UNIQUE,
                status TEXT NOT NULL, created_at TEXT NOT NULL, acknowledged_at TEXT
            )""")
            connection.commit()
            connection.close()
            with ReminderStore(db) as store:
                columns = {row[1] for row in store.connection.execute("PRAGMA table_info(reminders)")}
                self.assertIn("notified_at", columns)

    def test_windows_balloon_provider_uses_encoded_data_without_shell(self) -> None:
        import base64
        import json
        from muse.reminders import notify_windows_balloon
        captured = {}
        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
        notify_windows_balloon(
            "Muse 提醒", "Check this task", platform_name="nt",
            powershell_path="powershell.exe", runner=fake_run,
        )
        self.assertFalse(captured["kwargs"]["shell"])
        encoded = captured["args"][-1].split("FromBase64String('")[1].split("')")[0]
        self.assertEqual(json.loads(base64.b64decode(encoded).decode("utf-8")),
                         {"title": "Muse 提醒", "message": "Check this task"})

    def test_windows_balloon_provider_refuses_non_windows(self) -> None:
        from muse.reminders import notify_windows_balloon
        with self.assertRaisesRegex(RuntimeError, "Windows only"):
            notify_windows_balloon("Title", "Message", platform_name="posix")

    def test_snooze_defers_due_reminder_until_requested_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                now = datetime.now(timezone.utc)
                item = store.add("Due", "Check later", due_at=now - timedelta(minutes=1))
                self.assertEqual(len(store.due(now=now)), 1)
                store.mark_notified(item["id"])
                store.snooze(item["id"], until=now + timedelta(minutes=10))
                self.assertEqual(store.due(now=now), [])
                self.assertEqual(len(store.due(now=now + timedelta(minutes=11))), 1)

    def test_snooze_rejects_acknowledged_and_unknown_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                item = store.add("Due", "Check later", due_at=datetime.now(timezone.utc))
                store.acknowledge(item["id"])
                with self.assertRaisesRegex(ValueError, "only pending"):
                    store.snooze(item["id"], until=datetime.now(timezone.utc) + timedelta(minutes=5))
                with self.assertRaises(KeyError):
                    store.snooze("missing", until=datetime.now(timezone.utc) + timedelta(minutes=5))

    def test_schema_migrates_both_notification_and_snooze_columns(self) -> None:
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("""CREATE TABLE reminders (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, message TEXT NOT NULL,
                due_at TEXT NOT NULL, source TEXT NOT NULL, dedupe_key TEXT UNIQUE,
                status TEXT NOT NULL, created_at TEXT NOT NULL, acknowledged_at TEXT
            )""")
            connection.commit()
            connection.close()
            with ReminderStore(db) as store:
                columns = {row[1] for row in store.connection.execute("PRAGMA table_info(reminders)")}
                self.assertIn("notified_at", columns)
                self.assertIn("snoozed_until", columns)

    def test_parse_chinese_relative_reminder_and_title(self) -> None:
        from muse.reminders import parse_capture_text
        now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        parsed = parse_capture_text("提醒我半小时后检查报告", now=now)
        self.assertEqual(parsed["title"], "检查报告")
        self.assertEqual(datetime.fromisoformat(parsed["due_at"]), now + timedelta(minutes=30))

    def test_parse_english_relative_reminder_and_explicit_timezone(self) -> None:
        from muse.reminders import parse_capture_text
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            singapore = ZoneInfo("Asia/Singapore")
        except ZoneInfoNotFoundError:
            self.skipTest("IANA timezone database is not installed in this Python environment")
        now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        parsed = parse_capture_text("Remind me to review the build in 20 minutes", now=now, timezone_name="Asia/Singapore")
        self.assertEqual(parsed["title"], "review the build")
        self.assertEqual(datetime.fromisoformat(parsed["due_at"]), datetime(2026, 11, 2, 18, 20, tzinfo=singapore))

    def test_parse_tomorrow_clock_time_and_reject_ambiguous_text(self) -> None:
        from muse.reminders import parse_capture_text
        now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        parsed = parse_capture_text("明天 09:30 提醒我开会", now=now)
        self.assertEqual(parsed["title"], "开会")
        self.assertEqual(datetime.fromisoformat(parsed["due_at"]), datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "time not understood"):
            parse_capture_text("提醒我稍后处理", now=now)

    def test_quiet_hours_support_overnight_windows(self) -> None:
        from muse.reminders import is_quiet_hours
        self.assertTrue(is_quiet_hours(datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc), "22:00-08:00"))
        self.assertTrue(is_quiet_hours(datetime(2026, 11, 2, 7, 59, tzinfo=timezone.utc), "22:00-08:00"))
        self.assertFalse(is_quiet_hours(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc), "22:00-08:00"))
        self.assertFalse(is_quiet_hours(datetime.now(timezone.utc), None))
        with self.assertRaisesRegex(ValueError, "HH:MM-HH:MM"):
            is_quiet_hours(datetime.now(timezone.utc), "bad")

    def test_unknown_reminder_cannot_be_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ReminderStore(Path(directory) / "reminders.sqlite3") as store:
                with self.assertRaises(KeyError):
                    store.acknowledge("missing")


if __name__ == "__main__":
    unittest.main()
