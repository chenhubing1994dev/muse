"""Durable, foreground routine scheduler with leases and explicit handlers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass

import argparse
import os
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from muse.model_call_context import current_model_call_context, model_call_scope

from muse.lease_recovery import _backup_database, _process_identity
RoutineHandler = Callable[[dict[str, Any]], Any]


class RoutineCancelled(Exception):
    """A cooperative routine handler observed a supervisor stop request."""


class RoutineLeaseLost(Exception):
    """A cooperative routine handler observed that its lease was lost."""


@dataclass(frozen=True)
class RoutineExecutionContext:
    """Opt-in cooperative cancellation and lease-health signal for handlers."""

    stop_event: Any
    lease_lost_event: threading.Event

    def is_cancelled(self) -> bool:
        return self.stop_event.is_set() or self.lease_lost_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.lease_lost_event.is_set():
            raise RoutineLeaseLost("routine lease was lost during handler execution")
        if self.stop_event.is_set():
            raise RoutineCancelled("routine observed supervisor stop request")


def routine_handler_with_context(handler: RoutineHandler) -> RoutineHandler:
    """Mark one handler as explicitly accepting ``execution_context``."""
    setattr(handler, "accepts_execution_context", True)
    return handler


class RoutineStore:
    """Persist interval routines; lease claims prevent duplicate concurrent runs."""

    def __init__(self, database: str | Path) -> None:
        import sqlite3
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # A concurrent first-opener may be changing the journal mode. The
            # busy_timeout does not reliably serialize this PRAGMA on Windows;
            # continue with SQLite's current safe journal mode, then serialize
            # schema migration below with BEGIN IMMEDIATE.
            if "locked" not in str(exc).casefold():
                raise
        existing_tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        existing_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")} if "routines" in existing_tables else set()
        if self.path.stat().st_size > 0 and ("routines" not in existing_tables or "lease_token" not in existing_columns):
            self._snapshot()
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS routines (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL, payload_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT NOT NULL,
                last_run_at TEXT, last_status TEXT NOT NULL DEFAULT 'never',
                lease_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        self.connection.commit()

        # Serialize schema inspection and migration: concurrent first-openers must
        # not both observe the legacy schema and race the same ALTER TABLE.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
            if "lease_token" not in columns:
                self.connection.execute("ALTER TABLE routines ADD COLUMN lease_token TEXT")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _snapshot(self) -> Path:
        stamp = self._now().strftime("%Y%m%dT%H%M%S%fZ")
        handoff = self.path.parent.parent / "handoff" if self.path.parent.name == "routines" else self.path.parent / "handoff"
        target = handoff / f"routine-op-{stamp}-{uuid.uuid4().hex[:10]}.sqlite3"
        if target.exists():
            raise RuntimeError(f"routine backup already exists; refusing overwrite: {target}")
        _backup_database(self.path, target)
        return target

    def close(self) -> None:
        self.connection.close()
    def __enter__(self) -> "RoutineStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def add(self, name: str, kind: str, *, interval_seconds: int,
            payload: dict[str, Any] | None = None, run_immediately: bool = True) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("name must be 1-120 characters")
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 80:
            raise ValueError("kind must be 1-80 characters")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds < 60:
            raise ValueError("interval_seconds must be an integer of at least 60")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        now = self._now()
        next_run = now if run_immediately else now + timedelta(seconds=interval_seconds)
        routine_id = uuid.uuid4().hex
        self._snapshot()
        self.connection.execute(
            "INSERT INTO routines (id,name,kind,interval_seconds,payload_json,enabled,next_run_at,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?,?)",
            (routine_id, name.strip(), kind.strip(), interval_seconds,
             json.dumps(payload or {}, ensure_ascii=False), next_run.isoformat(), now.isoformat(), now.isoformat()),
        )
        self.connection.commit()
        return self.get(routine_id)

    def get(self, routine_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        value["enabled"] = bool(value["enabled"])
        return value

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute("SELECT * FROM routines ORDER BY name LIMIT ?", (limit,)).fetchall()
        return [self._decode(row) for row in rows]

    def set_enabled(self, routine_id: str, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        self._snapshot()
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
        if "desired_state" in columns:
            cursor = self.connection.execute(
                "UPDATE routines SET enabled=?,desired_state=?,updated_at=? WHERE id=?",
                (int(enabled), "running" if enabled else "stopped", self._now().isoformat(), routine_id),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE routines SET enabled=?,updated_at=? WHERE id=?",
                (int(enabled), self._now().isoformat(), routine_id),
            )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(routine_id)
        return self.get(routine_id)

    def trigger(self, name: str) -> bool:
        """Make an enabled routine due without stealing its lease."""
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("name must be 1-120 characters")
        stamp = self._now().isoformat()
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
        desired_filter = " AND desired_state='running'" if "desired_state" in columns else ""
        eligible = self.connection.execute(
            "SELECT 1 FROM routines WHERE name=? AND enabled=1" + desired_filter, (name.strip(),)
        ).fetchone()
        if eligible is None:
            return False
        self._snapshot()
        cursor = self.connection.execute(            "UPDATE routines SET next_run_at=CASE WHEN next_run_at>? THEN ? ELSE next_run_at END, "
            "updated_at=? WHERE name=? AND enabled=1" + desired_filter,
            (stamp, stamp, stamp, name.strip()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def claim_due(self, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")
        now = self._now()
        stamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        token = uuid.uuid4().hex
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
        fenced = {"heartbeat_at", "ttl_seconds", "pid", "fencing_epoch", "desired_state", "process_identity"}.issubset(columns)
        desired_filter = " AND desired_state='running'" if fenced else ""
        if fenced:
            due = self.connection.execute(
                "SELECT 1 FROM routines WHERE enabled=1 AND desired_state='running' AND next_run_at<=? "
                "AND lease_until IS NULL AND lease_token IS NULL AND pid IS NULL LIMIT 1", (stamp,),
            ).fetchone()
        else:
            due = self.connection.execute(
                "SELECT 1 FROM routines WHERE enabled=1 AND next_run_at<=? AND (lease_until IS NULL OR lease_until<=?) LIMIT 1",
                (stamp, stamp),
            ).fetchone()
        if due is None:
            return None
        self._snapshot()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if fenced:
                claim_sql = (
                    "SELECT * FROM routines WHERE enabled=1" + desired_filter
                    + " AND next_run_at<=? AND lease_until IS NULL AND lease_token IS NULL AND pid IS NULL "
                    "ORDER BY next_run_at,name LIMIT 1"
                )
                claim_params = (stamp,)
            else:
                claim_sql = (
                    "SELECT * FROM routines WHERE enabled=1 AND next_run_at<=? "
                    "AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_run_at,name LIMIT 1"
                )
                claim_params = (stamp, stamp)
            row = self.connection.execute(claim_sql, claim_params).fetchone()
            if row is None:
                self.connection.commit()
                return None
            if fenced:
                identity = _process_identity(os.getpid())
                if not identity:
                    raise RuntimeError("cannot verify routine worker process identity")
                old_epoch = int(row["fencing_epoch"] or 0)
                epoch = old_epoch + 1
                cursor = self.connection.execute(
                    "UPDATE routines SET lease_until=?,lease_token=?,last_status='running',updated_at=?,"
                    "heartbeat_at=?,ttl_seconds=?,pid=?,process_identity=?,fencing_epoch=? "
                    "WHERE id=? AND fencing_epoch=? AND desired_state='running' "
                    "AND lease_until IS NULL AND lease_token IS NULL AND pid IS NULL",
                    (expires, token, stamp, stamp, lease_seconds, os.getpid(), identity,
                     epoch, row["id"], old_epoch),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("routine claim compare-and-swap failed")
            else:
                self.connection.execute(
                    "UPDATE routines SET lease_until=?,lease_token=?,last_status='running',updated_at=? WHERE id=?",
                    (expires, token, stamp, row["id"]),
                )
            self.connection.commit()
            return self.get(row["id"])
        except Exception:
            self.connection.rollback()
            raise

    def renew_lease(
        self, routine_id: str, *, lease_token: str, lease_seconds: int = 300,
        fencing_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("lease_token is required to renew a routine")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")
        now = self._now()
        stamp = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
        fenced = "fencing_epoch" in columns
        self._snapshot()
        if fenced and (isinstance(fencing_epoch, bool) or not isinstance(fencing_epoch, int)):
            raise ValueError("fencing_epoch is required for a migrated routine lease")
        if fenced:
            cursor = self.connection.execute(                "UPDATE routines SET lease_until=?,heartbeat_at=?,ttl_seconds=?,updated_at=? "
                "WHERE id=? AND lease_token=? AND fencing_epoch=? AND lease_until>? AND desired_state='running'",
                (expires, stamp, lease_seconds, stamp, routine_id, lease_token, fencing_epoch, stamp),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE routines SET lease_until=?,updated_at=? WHERE id=? AND lease_token=? AND lease_until>?",
                (expires, stamp, routine_id, lease_token, stamp),
            )
        self.connection.commit()

        return self.get(routine_id) if cursor.rowcount == 1 else None

    def finish(
        self, routine_id: str, *, status: str, lease_token: str,
        fencing_epoch: int | None = None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("status must be succeeded, failed, or cancelled")
        if not isinstance(lease_token, str) or not lease_token:
            raise ValueError("lease_token is required to finish a routine")
        now = self._now()
        row = self.connection.execute("SELECT interval_seconds FROM routines WHERE id=?", (routine_id,)).fetchone()
        if row is None:
            raise KeyError(routine_id)
        next_run = now if status == "cancelled" else now + timedelta(seconds=row["interval_seconds"])
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(routines)")}
        fenced = "fencing_epoch" in columns
        if fenced and (isinstance(fencing_epoch, bool) or not isinstance(fencing_epoch, int)):
            raise ValueError("fencing_epoch is required to finish a migrated routine lease")
        self._snapshot()
        if fenced:
            cursor = self.connection.execute(
                "UPDATE routines SET last_run_at=?,last_status=?,next_run_at=?,lease_until=NULL,"
                "lease_token=NULL,updated_at=?,heartbeat_at=NULL,pid=NULL,process_identity=NULL "
                "WHERE id=? AND lease_token=? AND fencing_epoch=? AND lease_until>? AND desired_state='running'",
                (now.isoformat(), status, next_run.isoformat(), now.isoformat(),
                 routine_id, lease_token, fencing_epoch, now.isoformat()),
            )
        else:
            cursor = self.connection.execute(
                "UPDATE routines SET last_run_at=?,last_status=?,next_run_at=?,lease_until=NULL,lease_token=NULL,updated_at=? "
                "WHERE id=? AND lease_token=? AND lease_until>?",
                (now.isoformat(), status, next_run.isoformat(), now.isoformat(), routine_id, lease_token, now.isoformat()),
            )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("routine lease was lost; stale worker cannot finish it")
        return self.get(routine_id)


def run_due_once(
    database: str | Path, handlers: dict[str, RoutineHandler], *,
    max_routines: int = 10, lease_seconds: int = 300,
    stop_event: Any | None = None, max_workers: int = 1,
) -> dict[str, Any]:
    """Claim a bounded batch, then execute independent routines concurrently."""
    if isinstance(max_routines, bool) or not isinstance(max_routines, int) or not 1 <= max_routines <= 100:
        raise ValueError("max_routines must be between 1 and 100")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 3:
        raise ValueError("lease_seconds must be an integer of at least 3")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= max_routines:
        raise ValueError("max_workers must be between 1 and max_routines")

    cooperative_stop_event = stop_event if stop_event is not None else threading.Event()
    claimed = []
    for _ in range(max_routines):
        if stop_event is not None and stop_event.is_set():
            break
        with RoutineStore(database) as store:
            routine = store.claim_due(lease_seconds=lease_seconds)
        if routine is None:
            break
        claimed.append(routine)

    def execute(routine: dict[str, Any]) -> dict[str, Any]:
        handler = handlers.get(routine["kind"])
        heartbeat_stop = threading.Event()
        heartbeat_lost = threading.Event()
        heartbeat_thread = None
        finish_allowed = True
        if handler is None:
            status, error = "failed", "no handler registered for routine kind"
        elif cooperative_stop_event.is_set():
            status, error = "cancelled", "supervisor stop requested before routine started"
        else:
            def keep_lease_alive() -> None:
                interval = max(1.0, lease_seconds / 3)
                while not heartbeat_stop.wait(interval):
                    try:
                        with RoutineStore(database) as store:
                            renewed = store.renew_lease(
                                routine["id"], lease_token=routine["lease_token"],
                                lease_seconds=lease_seconds,
                                fencing_epoch=routine.get("fencing_epoch"),
                            )
                        if renewed is None:
                            heartbeat_lost.set()
                            return
                    except Exception:
                        heartbeat_lost.set()
                        return

            heartbeat_thread = threading.Thread(target=keep_lease_alive, daemon=True)
            heartbeat_thread.start()
            execution_context = RoutineExecutionContext(
                stop_event=cooperative_stop_event, lease_lost_event=heartbeat_lost,
            )
            try:
                accepts_context = getattr(
                    getattr(handler, "__func__", handler), "accepts_execution_context", False,
                ) is True
                if accepts_context:
                    handler(routine["payload"], execution_context=execution_context)
                else:
                    handler(routine["payload"])
                status, error = "succeeded", ""
            except RoutineCancelled as exc:
                status, error = "cancelled", str(exc)
            except RoutineLeaseLost as exc:
                status, error, finish_allowed = "lease_lost", str(exc), False
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=max(1.0, lease_seconds))
        if finish_allowed:
            try:
                with RoutineStore(database) as store:
                    finished = store.finish(
                        routine["id"], status=status, lease_token=routine["lease_token"],
                        fencing_epoch=routine.get("fencing_epoch"),
                    )
                outcome_status = status
                next_run_at = finished["next_run_at"]
            except RuntimeError as exc:
                outcome_status = "lease_lost"
                error = str(exc)
                next_run_at = None
        else:
            outcome_status, next_run_at = status, None
        return {
            "id": routine["id"], "name": routine["name"],
            "status": outcome_status, "error": error,
            "heartbeat_lost": heartbeat_lost.is_set(),
            "next_run_at": next_run_at,
        }
    def execute_batch() -> list[dict[str, Any]]:
        if len(claimed) <= 1 or max_workers == 1:
            return [execute(item) for item in claimed]
        indexed = {}
        with ThreadPoolExecutor(max_workers=min(max_workers, len(claimed)),
                                thread_name_prefix="muse-routine") as pool:
            futures = {
                pool.submit(copy_context().run, execute, item): index
                for index, item in enumerate(claimed)
            }
            for future in as_completed(futures):
                indexed[futures[future]] = future.result()
        return [indexed[index] for index in range(len(claimed))]

    usage_databases = {
        str(Path((routine.get("payload") or {}).get("task_db") or "data/tasks/muse.sqlite3").resolve())
        for routine in claimed if routine.get("kind") == "autonomy_plan"
    }
    if claimed and current_model_call_context() is None and len(usage_databases) == 1:
        # One shared scope/ledger per batch serializes concurrent SQLite appends via ModelCallContext's lock.
        with model_call_scope(next(iter(usage_databases)), task_id=None, agent_id=None, project_id="muse"):
            outcomes = execute_batch()
    else:
        outcomes = execute_batch()
    return {"processed": len(outcomes), "outcomes": outcomes}


def run_forever(database: str | Path, handlers: dict[str, RoutineHandler], *,
                stop_event: Any | None = None, poll_seconds: int = 15) -> dict[str, int]:
    if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int) or poll_seconds < 1:
        raise ValueError("poll_seconds must be a positive integer")
    stop = stop_event if stop_event is not None else threading.Event()
    processed = errors = 0
    while not stop.is_set():
        try:
            result = run_due_once(database, handlers, stop_event=stop)
            processed += result["processed"]
        except Exception:
            errors += 1
        if stop.wait(poll_seconds):
            break
    return {"processed": processed, "errors": errors}


def _autonomy_handler(payload: dict[str, Any]) -> dict[str, Any]:
    from muse.autonomy import propose_next_step
    from muse.model_client import LMStudioClient

    task_database = payload.get("task_db") or "data/tasks/muse.sqlite3"
    client = LMStudioClient(payload.get("base_url", "http://127.0.0.1:1234/v1"), model=payload.get("model"), timeout=90)

    def run_plan() -> dict[str, Any]:
        return propose_next_step(
            payload.get("backlog", "docs/CAPABILITY_BACKLOG.md"),
            task_database,
            payload.get("plan_db", "data/autonomy/plans.sqlite3"),
            lambda messages: client.chat(messages, temperature=0.2, max_tokens=900),
        )

    if current_model_call_context() is not None:
        return run_plan()
    with model_call_scope(task_database, task_id=None, agent_id=None, project_id="muse"):
        return run_plan()



def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage durable Muse routines; loop runs in the foreground")
    parser.add_argument("--db", default="data/routines/muse.sqlite3")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=15)
    sub = parser.add_subparsers(dest="command")
    add = sub.add_parser("add", help="add a persistent interval routine")
    add.add_argument("name")
    add.add_argument("kind", choices=["autonomy_plan"])
    add.add_argument("--interval-seconds", type=int, default=1800)
    add.add_argument("--backlog", default="docs/CAPABILITY_BACKLOG.md")
    add.add_argument("--task-db", default="data/tasks/muse.sqlite3")
    add.add_argument("--plan-db", default="data/autonomy/plans.sqlite3")
    add.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    add.add_argument("--model")
    enable = sub.add_parser("enable")
    enable.add_argument("routine_id")
    disable = sub.add_parser("disable")
    disable.add_argument("routine_id")
    args = parser.parse_args(argv)
    with RoutineStore(args.db) as store:
        if args.command == "add":
            payload = {"backlog": args.backlog, "task_db": args.task_db, "plan_db": args.plan_db,
                       "base_url": args.base_url, "model": args.model}
            print(json.dumps(store.add(args.name, args.kind, interval_seconds=args.interval_seconds, payload=payload), ensure_ascii=False, indent=2))
            return 0
        if args.command in {"enable", "disable"}:
            print(json.dumps(store.set_enabled(args.routine_id, args.command == "enable"), ensure_ascii=False, indent=2))
            return 0
        if args.list:
            print(json.dumps(store.list(), ensure_ascii=False, indent=2))
            return 0
    handlers = {"autonomy_plan": _autonomy_handler}
    try:
        if args.loop:
            stats = run_forever(args.db, handlers, poll_seconds=args.poll_seconds)
            print(json.dumps({"stopped": True, **stats}))
        else:
            print(json.dumps(run_due_once(args.db, handlers), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("Muse routine scheduler stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
