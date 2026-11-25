"""Fenced lease mutations for Muse's existing Routine and Runtime stores.

This module owns no scheduler or queue. Every mutating call snapshots the
single SQLite database it changes before BEGIN IMMEDIATE. It requires the
additive columns installed by muse.lease_recovery migrate --apply.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from muse.lease_recovery import MigrationError, _backup_database, _pid_alive, _process_identity


class LeaseError(RuntimeError):
    """A lease operation was refused because ownership cannot be proven."""


class StaleFenceError(LeaseError):
    """A stale owner/epoch attempted to renew, release, or mutate a lease."""


@dataclass(frozen=True)
class LeaseResult:
    status: str
    resource_id: str
    owner: str | None
    epoch: int
    heartbeat_at: str | None = None
    ttl_seconds: int | None = None
    pid: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "resource_id": self.resource_id,
            "owner": self.owner, "epoch": self.epoch,
            "heartbeat_at": self.heartbeat_at, "ttl_seconds": self.ttl_seconds,
            "pid": self.pid, "reason": self.reason,
        }


class LeaseManager:
    """CAS/fencing lease manager for routines or autonomous_runtime."""

    TABLES = {
        "routines": ("id", "lease_token"),
        "autonomous_runtime": ("singleton", "owner_id"),
    }

    def __init__(
        self,
        database: str | Path,
        *,
        resource_table: str = "routines",
        resource_id: str | int = "",
        handoff_dir: str | Path = "data/handoff",
        pid_alive: Callable[[int | None], bool] = _pid_alive,
        process_identity: Callable[[int], str | None] = _process_identity,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if resource_table not in self.TABLES:
            raise ValueError("resource_table must be routines or autonomous_runtime")
        if resource_table == "routines" and (not isinstance(resource_id, str) or not resource_id):
            raise ValueError("routine resource_id must be a non-empty string")
        if resource_table == "autonomous_runtime" and resource_id not in (1, "1", ""):
            raise ValueError("autonomous_runtime resource_id must be singleton 1")
        self.path = Path(database)
        self.table = resource_table
        self.key_column, self.owner_column = self.TABLES[resource_table]
        self.resource_id = 1 if resource_table == "autonomous_runtime" else resource_id
        self.handoff_dir = Path(handoff_dir)
        self.pid_alive = pid_alive
        self.process_identity = process_identity
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _stamp(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _read(self) -> dict[str, Any]:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            row = db.execute(
                f'SELECT * FROM "{self.table}" WHERE "{self.key_column}"=?',
                (self.resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(self.resource_id)
            value = dict(row)
            required = {"heartbeat_at", "ttl_seconds", "pid", "fencing_epoch", "desired_state", "process_identity"}
            missing = required - set(value)
            if missing:
                raise MigrationError("lease schema migration required; missing: " + ", ".join(sorted(missing)))
            return value
        finally:
            db.close()

    def _snapshot(self) -> Path:
        stamp = self.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.handoff_dir / f"lease-op-{stamp}-{uuid.uuid4().hex[:10]}-{self.path.stem}.sqlite3"
        if destination.exists():
            raise MigrationError(f"backup already exists; refusing to overwrite: {destination}")
        _backup_database(self.path, destination)
        return destination

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _audit(
        self, db: sqlite3.Connection, *, old_owner: str | None,
        new_owner: str, old_epoch: int, reason: str, stamp: str,
    ) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS lease_audit (
            audit_id TEXT PRIMARY KEY, resource_table TEXT NOT NULL,
            resource_id TEXT NOT NULL, old_owner TEXT, old_epoch INTEGER NOT NULL,
            new_owner TEXT, new_epoch INTEGER NOT NULL, reason TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )""")
        db.execute(
            "INSERT INTO lease_audit (audit_id,resource_table,resource_id,old_owner,old_epoch,new_owner,new_epoch,reason,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, self.table, str(self.resource_id), old_owner, old_epoch,
             new_owner, old_epoch + 1, reason, stamp),
        )

    def _result(self, status: str, row: dict[str, Any], reason: str | None = None) -> LeaseResult:
        return LeaseResult(
            status=status, resource_id=str(self.resource_id),
            owner=row.get(self.owner_column), epoch=int(row.get("fencing_epoch") or 0),
            heartbeat_at=row.get("heartbeat_at"), ttl_seconds=row.get("ttl_seconds"),
            pid=row.get("pid"), reason=reason,
        )

    def acquire(self, owner: str, *, pid: int, ttl_seconds: int = 60) -> LeaseResult:
        """Acquire a free lease; never overrides an explicit stop or live owner."""
        if not owner or len(owner) > 160:
            raise ValueError("owner must be 1-160 characters")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive integer")
        identity = self.process_identity(pid)
        if not identity:
            raise LeaseError("process identity cannot be verified; refusing lease acquisition")
        self._snapshot()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f'SELECT * FROM "{self.table}" WHERE "{self.key_column}"=?',
                (self.resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(self.resource_id)
            current = dict(row)
            if current.get("desired_state") == "stopped":
                raise LeaseError("desired_state=stopped; explicit user resume is required")
            old_owner = current.get(self.owner_column)
            old_epoch = int(current.get("fencing_epoch") or 0)
            if old_owner is not None or current.get("pid") is not None:
                raise LeaseError("lease is not free; use takeover_if_stale after diagnosis")
            stamp = self._stamp()
            expires = (self.now().astimezone(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
            assignments = f'"{self.owner_column}"=?, pid=?, heartbeat_at=?, ttl_seconds=?, process_identity=?, fencing_epoch=?'
            params: list[Any] = [owner, pid, stamp, ttl_seconds, identity, old_epoch + 1]
            if self.table == "routines":
                assignments += ", lease_until=?"
                params.append(expires)
            else:
                assignments += ", state='running', updated_at=?"
                params.append(stamp)
            params.extend([self.resource_id, old_owner, old_epoch])
            cursor = db.execute(
                f'UPDATE "{self.table}" SET {assignments} WHERE "{self.key_column}"=? AND "{self.owner_column}" IS ? AND fencing_epoch=?',
                params,
            )
            if cursor.rowcount != 1:
                raise StaleFenceError("lease acquire compare-and-swap failed")
            db.commit()
            current.update({
                self.owner_column: owner, "pid": pid, "heartbeat_at": stamp,
                "ttl_seconds": ttl_seconds, "process_identity": identity,
                "fencing_epoch": old_epoch + 1,
            })
            return self._result("acquired", current)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def renew(self, owner: str, epoch: int, *, ttl_seconds: int | None = None) -> LeaseResult:
        """Renew only when both owner and fencing epoch still match."""
        if not owner or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ValueError("owner and positive epoch are required")
        self._snapshot()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f'SELECT * FROM "{self.table}" WHERE "{self.key_column}"=?',
                (self.resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(self.resource_id)
            current = dict(row)
            ttl = ttl_seconds if ttl_seconds is not None else int(current["ttl_seconds"])
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
                raise ValueError("ttl_seconds must be a positive integer")
            stamp = self._stamp()
            expires = (self.now().astimezone(timezone.utc) + timedelta(seconds=ttl)).isoformat()
            assignments = "heartbeat_at=?,ttl_seconds=?"
            params: list[Any] = [stamp, ttl]
            if self.table == "routines":
                assignments += ",lease_until=?"
                params.append(expires)
            else:
                assignments += ",updated_at=?"
                params.append(stamp)
            params.extend([self.resource_id, owner, epoch])
            cursor = db.execute(
                f'UPDATE "{self.table}" SET {assignments} WHERE "{self.key_column}"=? AND "{self.owner_column}"=? AND fencing_epoch=? AND desired_state=\'running\'',
                params,
            )
            if cursor.rowcount != 1:
                raise StaleFenceError("old owner or fencing epoch cannot renew this lease")
            db.commit()
            current.update({"heartbeat_at": stamp, "ttl_seconds": ttl})
            return self._result("renewed", current)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def release(self, owner: str, epoch: int, *, desired_state: str | None = None) -> LeaseResult:
        """Release with a fenced CAS; optionally persist an explicit stop intent."""
        if desired_state not in (None, "running", "stopped"):
            raise ValueError("desired_state must be running or stopped")
        self._snapshot()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f'SELECT * FROM "{self.table}" WHERE "{self.key_column}"=?',
                (self.resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(self.resource_id)
            current = dict(row)
            stamp = self._stamp()
            assignments = f'"{self.owner_column}"=NULL,pid=NULL,heartbeat_at=NULL,process_identity=NULL,updated_at=?'
            params: list[Any] = [stamp]
            if self.table == "routines":
                assignments += ",lease_until=NULL,lease_token=NULL"
            if desired_state is not None:
                assignments += ",desired_state=?"
                params.append(desired_state)
            params.extend([self.resource_id, owner, epoch])
            cursor = db.execute(
                f'UPDATE "{self.table}" SET {assignments} WHERE "{self.key_column}"=? AND "{self.owner_column}"=? AND fencing_epoch=?',
                params,
            )
            if cursor.rowcount != 1:
                raise StaleFenceError("old owner or fencing epoch cannot release this lease")
            db.commit()
            current.update({
                self.owner_column: None, "pid": None, "heartbeat_at": None,
                "process_identity": None,
                "desired_state": desired_state or current.get("desired_state"),
            })
            return self._result("released", current)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def takeover_if_stale(
        self, owner: str, *, pid: int, ttl_seconds: int = 60,
        force_takeover: bool = False,
    ) -> LeaseResult:
        """Take over only a running, expired lease whose recorded process is absent.

        A live PID with an expired heartbeat is suspected_hung and requires
        explicit force_takeover. A reused PID is orphaned but is not auto-taken
        over in this conservative implementation.
        """
        current = self._read()
        if current.get("desired_state") == "stopped":
            return self._result("stopped_by_intent", current, "automatic recovery forbidden by desired_state")
        heartbeat = self._parse(current.get("heartbeat_at"))
        ttl = int(current.get("ttl_seconds") or 0)
        expired = (
            heartbeat is not None and ttl > 0
            and (self.now().astimezone(timezone.utc) - heartbeat).total_seconds() > ttl
        )
        if not expired:
            return self._result("not_stale", current, "heartbeat has not exceeded TTL")
        old_pid = current.get("pid")
        alive = self.pid_alive(old_pid)
        expected = current.get("process_identity")
        observed = self.process_identity(old_pid) if alive and isinstance(old_pid, int) else None
        if alive and expected and observed and expected != observed:
            return self._result("orphaned", current, "PID is reused by a different process; manual review required")
        if alive and not force_takeover:
            return self._result("suspected_hung", current, "live PID with expired heartbeat; --force-takeover required")
        if alive and force_takeover and (not expected or not observed or expected != observed):
            raise LeaseError("force takeover requires matching recorded and observed process identity")
        if not alive and current.get("desired_state") != "running":            return self._result("stale", current, "desired_state is not running")
        identity = self.process_identity(pid)
        if not identity:
            raise LeaseError("new owner process identity cannot be verified")
        backup = self._snapshot()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f'SELECT * FROM "{self.table}" WHERE "{self.key_column}"=?',
                (self.resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(self.resource_id)
            locked = dict(row)
            if locked.get("desired_state") != "running":
                raise LeaseError("desired_state changed before takeover")
            old_owner = locked.get(self.owner_column)
            old_epoch = int(locked.get("fencing_epoch") or 0)
            if old_owner != current.get(self.owner_column) or old_epoch != int(current.get("fencing_epoch") or 0):
                raise StaleFenceError("lease changed during takeover; retry diagnosis")
            stamp = self._stamp()
            expires = (self.now().astimezone(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
            reason = "forced_takeover_suspected_hung" if alive else "orphaned_process_absent"
            self._audit(
                db, old_owner=old_owner, new_owner=owner, old_epoch=old_epoch,
                reason=reason, stamp=stamp,
            )
            assignments = f'"{self.owner_column}"=?,pid=?,heartbeat_at=?,ttl_seconds=?,process_identity=?,fencing_epoch=?'
            params: list[Any] = [owner, pid, stamp, ttl_seconds, identity, old_epoch + 1]
            if self.table == "routines":
                assignments += ",lease_until=?"
                params.append(expires)
            else:
                assignments += ",state='running',updated_at=?"
                params.append(stamp)
            params.extend([self.resource_id, old_owner, old_epoch])
            cursor = db.execute(
                f'UPDATE "{self.table}" SET {assignments} WHERE "{self.key_column}"=? AND "{self.owner_column}" IS ? AND fencing_epoch=? AND desired_state=\'running\'',
                params,
            )
            if cursor.rowcount != 1:
                raise StaleFenceError("takeover compare-and-swap failed")
            db.commit()
            locked.update({
                self.owner_column: owner, "pid": pid, "heartbeat_at": stamp,
                "ttl_seconds": ttl_seconds, "process_identity": identity,
                "fencing_epoch": old_epoch + 1,
            })
            return self._result("taken_over", locked, f"{reason}; backup={backup}")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
