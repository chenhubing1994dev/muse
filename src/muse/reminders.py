"""Local, persistent reminders with explicit acknowledgement and idempotent creation."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo


def _timestamp(value: str | datetime) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("due_at must be an ISO-8601 timestamp with timezone") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("due_at must be an ISO-8601 timestamp with timezone")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("due_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class ReminderStore:
    """SQLite-backed reminder inbox; storage never sends data outside this machine."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, message TEXT NOT NULL,
                due_at TEXT NOT NULL, source TEXT NOT NULL, dedupe_key TEXT UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('pending','acknowledged')),
                created_at TEXT NOT NULL, acknowledged_at TEXT, notified_at TEXT,
                snoozed_until TEXT
            )"""
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(reminders)")}
        if "notified_at" not in columns:
            self.connection.execute("ALTER TABLE reminders ADD COLUMN notified_at TEXT")
        if "snoozed_until" not in columns:
            self.connection.execute("ALTER TABLE reminders ADD COLUMN snoozed_until TEXT")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ReminderStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add(
        self, title: str, message: str, *, due_at: str | datetime,
        source: str = "user", dedupe_key: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError("title must be 1-120 characters")
        if not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise ValueError("message must be 1-2000 characters")
        if not isinstance(source, str) or not source.strip() or len(source) > 120:
            raise ValueError("source must be 1-120 characters")
        if dedupe_key is not None and (
            not isinstance(dedupe_key, str) or not dedupe_key.strip() or len(dedupe_key) > 200
        ):
            raise ValueError("dedupe_key must be 1-200 characters")
        due = _timestamp(due_at)
        now = datetime.now(timezone.utc).isoformat()
        reminder_id = uuid.uuid4().hex
        try:
            self.connection.execute(
                "INSERT INTO reminders (id,title,message,due_at,source,dedupe_key,status,created_at) "
                "VALUES (?,?,?,?,?,?,'pending',?)",
                (reminder_id, title.strip(), message.strip(), due, source.strip(), dedupe_key, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError:
            if dedupe_key is None:
                raise
            row = self.connection.execute(
                "SELECT * FROM reminders WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone()
            if row is None:
                raise
            return dict(row)
        return self.get(reminder_id)

    def get(self, reminder_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM reminders WHERE id=?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(
        self, *, include_acknowledged: bool = False, limit: int = 100
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        query = "SELECT * FROM reminders"
        if not include_acknowledged:
            query += " WHERE status='pending'"
        rows = self.connection.execute(
            query + " ORDER BY due_at,id LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def list_pending(self, *, limit: int = 100) -> list[dict[str, object]]:
        return self.list(limit=limit)

    def due(
        self, *, now: str | datetime | None = None, limit: int = 100
    ) -> list[dict[str, object]]:
        current = _timestamp(now or datetime.now(timezone.utc))
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            "SELECT * FROM reminders WHERE status='pending' AND notified_at IS NULL "
            "AND (snoozed_until IS NULL OR snoozed_until<=?) AND due_at<=? "
            "ORDER BY due_at,id LIMIT ?",
            (current, current, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, reminder_id: str) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE reminders SET notified_at=? "
            "WHERE id=? AND status='pending' AND notified_at IS NULL",
            (now, reminder_id),
        )
        self.connection.commit()
        reminder = self.get(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        return reminder

    def snooze(self, reminder_id: str, *, until: str | datetime) -> dict[str, object]:
        snoozed = _timestamp(until)
        reminder = self.get(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        if reminder["status"] != "pending":
            raise ValueError("only pending reminders can be snoozed")
        self.connection.execute(
            "UPDATE reminders SET snoozed_until=?, notified_at=NULL "
            "WHERE id=? AND status='pending'",
            (snoozed, reminder_id),
        )
        self.connection.commit()
        return self.get(reminder_id)

    def acknowledge(self, reminder_id: str) -> dict[str, object]:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE reminders SET status='acknowledged', acknowledged_at=? "
            "WHERE id=? AND status='pending'",
            (now, reminder_id),
        )
        self.connection.commit()
        reminder = self.get(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        return reminder


def parse_capture_text(
    text: str, *, now: datetime | None = None, timezone_name: str | None = None,
) -> dict[str, str]:
    """Parse a deliberately small, local-only Chinese/English reminder grammar."""
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise ValueError("reminder text must be 1-500 characters")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else None
    except Exception as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    if zone is not None:
        current = current.astimezone(zone)

    source = text.strip()
    match = re.search(
        r"(?P<num>\d+|半|一|兩|两|二|三|四|五|六|七|八|九|十)\s*"
        r"(?P<unit>個小時|小时|小時|分钟|分鐘|分|天|日)\s*(?:後|后)",
        source,
    )
    if match:
        numbers = {
            "半": 0.5, "一": 1, "兩": 2, "两": 2, "二": 2, "三": 3,
            "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        amount = float(match.group("num")) if match.group("num").isdigit() else numbers[match.group("num")]
        unit = match.group("unit")
        seconds = amount * (
            60 if unit in {"分", "分钟", "分鐘"}
            else 3600 if unit in {"小时", "小時", "個小時"}
            else 86400
        )
        due = current + timedelta(seconds=seconds)
    else:
        match = re.search(
            r"(?i)in\s+(?P<num>\d+)\s*(?P<unit>minutes?|mins?|hours?|days?)",
            source,
        )
        if match:
            amount = int(match.group("num"))
            unit = match.group("unit").lower()
            seconds = amount * (
                60 if unit.startswith("min")
                else 3600 if unit.startswith("hour")
                else 86400
            )
            due = current + timedelta(seconds=seconds)
        else:
            match = re.search(
                r"(?:明天|tomorrow)(?:\s*(?:at)?\s*)"
                r"(?P<hour>\d{1,2}):(?P<minute>\d{2})",
                source,
                re.IGNORECASE,
            )
            if not match:
                raise ValueError(
                    "time not understood; use '20 分鐘後', '半小時後', "
                    "'in 20 minutes', or '明天 09:30'"
                )
            hour, minute = int(match.group("hour")), int(match.group("minute"))
            if hour > 23 or minute > 59:
                raise ValueError("reminder time is outside the valid clock range")
            due = (current + timedelta(days=1)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

    title = re.sub(
        r"(?i)\s*(?:提醒我|提醒|記得|记得|remind me to|remind me|remind)\s*",
        "",
        source,
    )
    title = title.replace(match.group(0), " ")
    title = re.sub(r"^[\s，,。.!！:：]+|[\s，,。.!！:：]+$", "", title).strip()
    if not title:
        title = "定時提醒"
    return {
        "title": title[:120],
        "message": title[:2000],
        "due_at": due.isoformat(),
        "timezone": str(due.tzinfo),
    }


def is_quiet_hours(now: datetime, window: str | None) -> bool:
    if window is None or not window.strip():
        return False
    try:
        start_text, end_text = window.split("-", 1)
        start = datetime.strptime(start_text.strip(), "%H:%M").time()
        end = datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError as exc:
        raise ValueError("quiet hours must use HH:MM-HH:MM") from exc
    current = now.timetz().replace(tzinfo=None)
    if start == end:
        return True
    return start <= current < end if start < end else current >= start or current < end


def create_operational_reminders(
    database: str | Path, *, counts: dict[str, int], expired_leases: int,
    stale_workers: int, dead_workers: int, stalled_tasks: int = 0,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Create redacted, daily-deduplicated reminders from aggregate health signals."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    day = current.astimezone(timezone.utc).date().isoformat()
    signals = []
    for key, title, message, count in (
        ("failed", "Muse 有失败任务待检查",
         "任务队列存在失败任务。请检查失败记录，再决定是否重试。", counts.get("failed", 0)),
        ("approval", "Muse 有任务等待你批准",
         "有任务正在等待人工审批。请查看任务队列及其风险说明。", counts.get("waiting_approval", 0)),
        ("expired", "Muse 有任务租约过期",
         "有运行中任务的租约已过期。请检查 Worker 状态及恢复策略。", expired_leases),
        ("stalled", "Muse 有任务长时间没有进展",
         "有运行中任务超过 15 分钟没有更新。请检查任务和 Worker 进度。", stalled_tasks),
        ("stale-worker", "Muse Worker 心跳停滞",
         "有 Worker 心跳长时间没有更新。请检查进程和运行日志。", stale_workers),
        ("dead-worker", "Muse Worker 进程已退出",
         "有已登记的 Worker 进程不存在。请检查是否需要人工重启。", dead_workers),
    ):
        if count > 0:
            signals.append((key, title, f"{message} 当前检测数量：{count}。"))
    created = []
    with ReminderStore(database) as store:
        for key, title, message in signals:
            created.append(store.add(
                title, message, due_at=current, source="watchdog",
                dedupe_key=f"watchdog:{day}:{key}",
            ))
    return created



def notify_windows_balloon(
    title: str, message: str, *, platform_name: str | None = None,
    powershell_path: str | None = None, runner=subprocess.run,
) -> None:
    """Display a native Windows notification-area balloon without third-party packages."""
    if (platform_name or os.name) != "nt":
        raise RuntimeError("Windows only: native notification-area delivery is unavailable")
    executable = powershell_path or shutil.which("powershell.exe")
    if not executable:
        raise RuntimeError("Windows PowerShell was not found")
    payload = base64.b64encode(
        json.dumps({"title": title[:120], "message": message[:900]}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json; "
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$n=New-Object System.Windows.Forms.NotifyIcon; "
        "try { $n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; "
        "$n.ShowBalloonTip(10000,[string]$p.title,[string]$p.message,[System.Windows.Forms.ToolTipIcon]::Info); "
        "Start-Sleep -Seconds 11 } finally { $n.Dispose() }"
    )
    runner(
        [executable, "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
        check=True, capture_output=True, text=True, timeout=20, shell=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local Muse reminders (no cloud delivery)")
    parser.add_argument("--db", default="data/reminders/muse.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="create a local reminder")
    add.add_argument("--title", required=True)
    add.add_argument("--message", required=True)
    add.add_argument("--at", required=True, help="ISO-8601 timestamp with timezone")
    add.add_argument("--source", default="user")
    add.add_argument("--dedupe-key")

    listing = commands.add_parser("list", help="list pending reminders")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--limit", type=int, default=100)

    due = commands.add_parser("due", help="list reminders whose due time has arrived")
    due.add_argument("--limit", type=int, default=100)

    ack = commands.add_parser("ack", help="acknowledge a reminder")
    ack.add_argument("reminder_id")

    snooze = commands.add_parser("snooze", help="defer a reminder until an explicit timezone-aware time")
    snooze.add_argument("reminder_id")
    snooze.add_argument("--until", required=True)

    capture = commands.add_parser("capture", help="create a reminder from simple local Chinese/English text")
    capture.add_argument("--text", required=True)
    capture.add_argument("--timezone", help="IANA timezone, e.g. Asia/Singapore; defaults to local time")

    notify = commands.add_parser("notify-due", help="show due reminders in the Windows notification area")
    notify.add_argument("--limit", type=int, default=20)
    notify.add_argument("--dry-run", action="store_true", help="preview without displaying or marking reminders")
    notify.add_argument("--quiet-hours", default="22:00-08:00")
    notify.add_argument("--no-quiet-hours", action="store_true")

    args = parser.parse_args(argv)
    try:
        with ReminderStore(args.db) as store:
            if args.command == "add":
                result = store.add(
                    args.title, args.message, due_at=args.at,
                    source=args.source, dedupe_key=args.dedupe_key,
                )
            elif args.command == "list":
                result = store.list(include_acknowledged=args.all, limit=args.limit)
            elif args.command == "due":
                result = store.due(limit=args.limit)
            elif args.command == "ack":
                result = store.acknowledge(args.reminder_id)
            elif args.command == "snooze":
                result = store.snooze(args.reminder_id, until=args.until)
            elif args.command == "capture":
                parsed = parse_capture_text(args.text, timezone_name=args.timezone)
                result = store.add(
                    parsed["title"], parsed["message"], due_at=parsed["due_at"],
                    source="natural_language",
                )
                result["parsed_timezone"] = parsed["timezone"]
            else:
                due_items = store.due(limit=args.limit)
                quiet = None if args.no_quiet_hours else args.quiet_hours
                if not args.dry_run and is_quiet_hours(datetime.now().astimezone(), quiet):
                    result = {
                        "deferred": True, "reason": "quiet_hours",
                        "quiet_hours": quiet, "count": len(due_items),
                    }
                elif args.dry_run:
                    result = {
                        "dry_run": True, "count": len(due_items),
                        "reminders": due_items, "quiet_hours": quiet,
                    }
                elif not due_items:
                    result = {"notified": 0, "reminder_ids": []}
                else:
                    lines = [f"{item['title']}: {item['message']}" for item in due_items[:5]]
                    if len(due_items) > 5:
                        lines.append(f"...and {len(due_items) - 5} more reminder(s)")
                    notify_windows_balloon("Muse 提醒", "\n".join(lines))
                    for item in due_items:
                        store.mark_notified(item["id"])
                    result = {
                        "notified": len(due_items),
                        "reminder_ids": [item["id"] for item in due_items],
                    }
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
