"""Read-only lease diagnostics and additive, backup-first lease schema migration.

This module deliberately does not start the Supervisor or take over leases.
It only adds compatible lease metadata columns and reports observed process
identity. All database mutations require an explicit ``apply=True`` call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class MigrationError(RuntimeError):
    """A lease schema migration could not safely proceed."""


class ProcessIdentityError(RuntimeError):
    """The current process identity could not be established reliably."""


@dataclass(frozen=True)
class MigrationPlan:
    database: str
    table: str
    add_columns: tuple[tuple[str, str], ...]
    create_tables: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "table": self.table,
            "add_columns": [{"name": name, "ddl": ddl} for name, ddl in self.add_columns],
            "create_tables": list(self.create_tables),
            "changed_columns": len(self.add_columns),
        }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if exists is None:
        raise MigrationError(f"required table is missing: {table}")
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def build_migration_plan(routine_db: str | Path, runtime_db: str | Path) -> list[MigrationPlan]:
    """Inspect legacy schemas without opening a write connection."""
    specs = (
        (Path(routine_db), "routines", (
            ("heartbeat_at", "TEXT"),
            ("ttl_seconds", "INTEGER NOT NULL DEFAULT 300"),
            ("pid", "INTEGER"),
            ("fencing_epoch", "INTEGER NOT NULL DEFAULT 0"),
            ("desired_state", "TEXT NOT NULL DEFAULT 'running'"),
            ("process_identity", "TEXT"),
        )),
        (Path(runtime_db), "autonomous_runtime", (
            ("ttl_seconds", "INTEGER NOT NULL DEFAULT 30"),
            ("fencing_epoch", "INTEGER NOT NULL DEFAULT 0"),
            ("desired_state", "TEXT NOT NULL DEFAULT 'stopped'"),
            ("process_identity", "TEXT"),
        )),
    )
    plans: list[MigrationPlan] = []
    for path, table, definitions in specs:
        with closing(_connect_readonly(path)) as connection:
            existing = _columns(connection, table)
            missing = tuple((name, ddl) for name, ddl in definitions if name not in existing)
        plans.append(MigrationPlan(str(path), table, missing))
    return plans


def _integrity(path: Path) -> str:
    with closing(_connect_readonly(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return str(result)

def _backup_database(source: Path, destination: Path) -> None:
    """Create a SQLite-consistent backup; never overwrite an existing snapshot."""
    if destination.exists():
        raise MigrationError(f"backup already exists; refusing to overwrite: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = _connect_readonly(source)
    target_connection: sqlite3.Connection | None = None
    try:
        target_connection = sqlite3.connect(destination, timeout=10)
        target_connection.execute("PRAGMA busy_timeout=10000")
        source_connection.backup(target_connection)
        target_connection.commit()
    except Exception:
        if target_connection is not None:
            target_connection.close()
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        source_connection.close()
        if target_connection is not None:
            target_connection.close()
    if _integrity(destination) != "ok":
        raise MigrationError(f"backup integrity check failed: {destination}")


def migrate_lease_schema(
    routine_db: str | Path,
    runtime_db: str | Path,
    *,
    apply: bool = False,
    handoff_dir: str | Path = "data/handoff",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan or apply additive lease columns, backing up every touched DB first.

    ``apply=False`` is strictly read-only. For apply, all backup destinations
    are checked before the first snapshot is created. Migrations are serialized
    with BEGIN IMMEDIATE and are safe to execute repeatedly.
    """
    routine_path, runtime_path = Path(routine_db), Path(runtime_db)
    plans = build_migration_plan(routine_path, runtime_path)
    preview = {"apply": apply, "plans": [plan.to_dict() for plan in plans]}
    if apply and not any(plan.add_columns for plan in plans):
        return {"status": "already_current", **preview, "backups": []}
    if not apply:
        return {"status": "dry_run", **preview, "backups": []}
    if _integrity(routine_path) != "ok" or _integrity(runtime_path) != "ok":
        raise MigrationError("source database integrity check failed; no migration attempted")

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d")
    handoff = Path(handoff_dir)
    backup_pairs = [
        (routine_path, handoff / f"lease-migration-{stamp}-routines.sqlite3"),
        (runtime_path, handoff / f"lease-migration-{stamp}-autonomy.sqlite3"),
    ]
    collisions = [str(dst) for _, dst in backup_pairs if dst.exists()]
    if collisions:
        raise MigrationError("backup destination already exists: " + ", ".join(collisions))

    backups: list[str] = []
    try:
        for source, destination in backup_pairs:
            _backup_database(source, destination)
            backups.append(str(destination))
        for plan in plans:
            path = Path(plan.database)
            connection = sqlite3.connect(path, timeout=10)
            connection.execute("PRAGMA busy_timeout=10000")
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = _columns(connection, plan.table)
                routine_desired_state_missing = plan.table == "routines" and "desired_state" not in current
                for name, ddl in plan.add_columns:
                    if name not in current:
                        connection.execute(f'ALTER TABLE "{plan.table}" ADD COLUMN "{name}" {ddl}')
                if routine_desired_state_missing:
                    connection.execute("UPDATE routines SET desired_state='stopped' WHERE enabled=0")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        checks = {str(path): _integrity(path) for path in (routine_path, runtime_path)}
        if any(value != "ok" for value in checks.values()):
            raise MigrationError(f"post-migration integrity check failed: {checks}")
    except Exception:
        # Never restore automatically: a caller may need the exact backup for
        # forensic review. The operation fails closed and reports all snapshots.
        raise
    return {"status": "migrated", **preview, "backups": backups, "integrity": checks}

def _process_identity(pid: int) -> str | None:
    """Return a stable process identity where the OS exposes it via stdlib."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                    return None
                created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return hashlib.sha256(f"{created}|{buffer.value}".casefold().encode()).hexdigest()
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError, TypeError, ValueError):
            return None
    proc = Path(f"/proc/{pid}")
    try:
        stat = (proc / "stat").read_text(encoding="utf-8")        # comm may contain spaces/parentheses; fields after the final ')' begin at field 3.
        fields = stat[stat.rfind(")") + 2:].split()
        start_ticks = fields[19]  # Linux proc field 22 (starttime), relative to field 3.
        command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").strip()
        return hashlib.sha256(start_ticks.encode() + b"|" + command).hexdigest()
    except (OSError, IndexError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _process_identity(pid) is not None
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, SystemError):
        return False


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def diagnose_leases(
    routine_db: str | Path,
    runtime_db: str | Path,
    *,
    now: datetime | None = None,
    pid_alive: Callable[[int | None], bool] = _pid_alive,
    process_identity: Callable[[int], str | None] = _process_identity,
) -> dict[str, Any]:
    """Read-only diagnostic of every routine lease and the runtime singleton."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reports: list[dict[str, Any]] = []
    with closing(_connect_readonly(Path(routine_db))) as connection:
        columns = _columns(connection, "routines")
        rows = connection.execute("SELECT * FROM routines ORDER BY name").fetchall()
        for row in rows:
            item = dict(row)
            pid = item.get("pid")
            alive = pid_alive(pid)
            heartbeat = _parse_time(item.get("heartbeat_at"))
            ttl = item.get("ttl_seconds")
            has_modern = {"heartbeat_at", "ttl_seconds", "pid", "fencing_epoch", "desired_state", "process_identity"}.issubset(columns)
            age = max(0.0, (current_time - heartbeat).total_seconds()) if heartbeat else None
            expired = age is not None and isinstance(ttl, int) and ttl > 0 and age > ttl
            expected_identity = item.get("process_identity")
            observed_identity = process_identity(pid) if alive and isinstance(pid, int) else None
            identity_mismatch = bool(expected_identity and observed_identity and expected_identity != observed_identity)
            desired = item.get("desired_state") if has_modern else None
            if desired == "stopped":
                conclusion = "stopped_by_intent"
            elif not has_modern or heartbeat is None or not isinstance(ttl, int) or ttl <= 0:
                conclusion = "legacy_unknown"
            elif alive and identity_mismatch:
                conclusion = "orphaned"
            elif alive and expired:
                conclusion = "suspected_hung"
            elif alive and not expired and (not expected_identity or not observed_identity):
                conclusion = "identity_unverified"
            elif alive and not expired:
                conclusion = "healthy"
            elif expired:
                conclusion = "orphaned" if desired == "running" else "stale"
            elif pid is None and heartbeat is None:
                conclusion = "stale" if item.get("lease_until") else "inactive"
            else:
                conclusion = "stale"
            reports.append({
                "kind": "routine", "id": item.get("id"), "name": item.get("name"),
                "owner": None, "lease_token": item.get("lease_token"), "heartbeat_at": item.get("heartbeat_at"),
                "ttl_seconds": ttl, "expired": expired, "expired_seconds": round(max(0.0, age - ttl), 3) if expired and age is not None else 0.0,
                "pid": pid, "pid_alive": alive, "process_identity_matches": None if not expected_identity or not observed_identity else not identity_mismatch,
                "desired_state": desired, "fencing_epoch": item.get("fencing_epoch"), "conclusion": conclusion,
            })

    with closing(_connect_readonly(Path(runtime_db))) as connection:
        columns = _columns(connection, "autonomous_runtime")
        row = connection.execute("SELECT * FROM autonomous_runtime WHERE singleton=1").fetchone()
        if row is not None:
            item = dict(row)
            pid = item.get("pid")
            alive = pid_alive(pid)
            heartbeat = _parse_time(item.get("heartbeat_at"))
            ttl = item.get("ttl_seconds") if "ttl_seconds" in columns else None
            age = max(0.0, (current_time - heartbeat).total_seconds()) if heartbeat else None
            expired = age is not None and isinstance(ttl, int) and ttl > 0 and age > ttl
            expected_identity = item.get("process_identity") if "process_identity" in columns else None
            observed_identity = process_identity(pid) if alive and isinstance(pid, int) else None
            mismatch = bool(expected_identity and observed_identity and expected_identity != observed_identity)
            desired = item.get("desired_state") if "desired_state" in columns else None
            legacy_stopped = desired is None and item.get("state") == "stopped" and item.get("owner_id") is None and pid is None
            if desired == "stopped" or legacy_stopped:
                conclusion = "stopped_by_intent"
            elif not {"ttl_seconds", "desired_state", "process_identity"}.issubset(columns) or heartbeat is None or not isinstance(ttl, int):
                conclusion = "legacy_unknown"
            elif alive and mismatch:
                conclusion = "orphaned"
            elif alive and expired:
                conclusion = "suspected_hung"
            elif alive and (not expected_identity or not observed_identity):
                conclusion = "identity_unverified"
            elif alive:
                conclusion = "healthy"
            elif expired and desired == "running":
                conclusion = "orphaned"
            else:
                conclusion = "stale"
            reports.append({
                "kind": "runtime", "id": "autonomous_runtime:1", "name": "autonomous_runtime",
                "owner": item.get("owner_id"), "heartbeat_at": item.get("heartbeat_at"),
                "ttl_seconds": ttl, "expired": expired,
                "expired_seconds": round(max(0.0, age - ttl), 3) if expired and age is not None else 0.0,
                "pid": pid, "pid_alive": alive,
                "process_identity_matches": None if not expected_identity or not observed_identity else not mismatch,
                "desired_state": desired or ("stopped (inferred from legacy state)" if legacy_stopped else None),
                "fencing_epoch": item.get("fencing_epoch"), "conclusion": conclusion,
            })
    return {
        "generated_at": current_time.isoformat(),
        "read_only": True,
        "leases": reports,
        "summary": {
            key: sum(1 for item in reports if item["conclusion"] == key)
            for key in (
                "healthy", "stale", "orphaned", "suspected_hung", "stopped_by_intent",
                "legacy_unknown", "inactive", "identity_unverified",
            )
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse lease schema doctor (read-only by default)")
    parser.add_argument("command", choices=("doctor", "migrate"))
    parser.add_argument("--routine-db", default="data/routines/muse.sqlite3")
    parser.add_argument("--runtime-db", default="data/autonomy/runtime.sqlite3")
    parser.add_argument("--handoff-dir", default="data/handoff")
    parser.add_argument("--apply", action="store_true", help="apply additive migration after backups")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = diagnose_leases(args.routine_db, args.runtime_db)
        else:
            result = migrate_lease_schema(args.routine_db, args.runtime_db, apply=args.apply, handoff_dir=args.handoff_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())