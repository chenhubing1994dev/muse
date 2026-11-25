"""Cross-process GPU arbitration for Muse-owned inference workloads.

The coordinator is a resource lease, not a scheduler. SQLite serializes claims
across Muse processes; expired leases are reclaimed after owner crashes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


LEASE_TTL_MIN_SECONDS = 15
LEASE_TTL_MAX_SECONDS = 3600
SUPERVISOR_STALE_SECONDS = 90


def probe_gpu_workloads(workspace: str | Path) -> dict[str, Any]:
    """Report active GPU workloads, not merely whether a local HTTP service is alive.

    ComfyUI may remain healthy while idle after it has unloaded its models. In
    that state AMF encoding can safely use the shared GPU lease, but an active
    queue or resident model allocation blocks it. Service health is returned
    separately so callers never mistake an idle server for a missing service.
    """
    from muse.model_service import probe_configured_model_service
    from muse.video_engines import ComfyUIClient

    try:
        comfy_client = ComfyUIClient(timeout_seconds=1.0)
        comfy = comfy_client.health()
        if comfy.get("status") == "ready":
            queue = comfy_client.queue_status()
            running, pending = queue.get("running_jobs"), queue.get("pending_jobs")
            if (
                isinstance(running, bool) or not isinstance(running, int) or running < 0
                or isinstance(pending, bool) or not isinstance(pending, int) or pending < 0
            ):
                return {
                    "status": "unknown", "service_health": "healthy",
                    "active_service": "comfyui", "gpu_workload": "unknown",
                    "reason": "comfyui_queue_state_unknown",
                }
            if running or pending:
                return {
                    "status": "healthy", "service_health": "healthy",
                    "active_service": "comfyui", "gpu_workload": "active",
                    "queue_running": running, "queue_pending": pending,
                }
            memory = comfy_client.gpu_memory()
            total, free, ratio = (
                memory.get("vram_total_bytes"),
                memory.get("vram_free_bytes"),
                memory.get("vram_free_ratio"),
            )
            if (
                isinstance(total, bool) or not isinstance(total, int) or total <= 0
                or isinstance(free, bool) or not isinstance(free, int) or not 0 <= free <= total
                or isinstance(ratio, bool) or not isinstance(ratio, (int, float))
                or not 0 <= ratio <= 1
            ):
                return {
                    "status": "unknown", "service_health": "healthy",
                    "active_service": "comfyui", "gpu_workload": "unknown",
                    "reason": "comfyui_gpu_memory_telemetry_unknown",
                }
            if ratio >= 0.70 and free >= int(total * 0.70):
                return {
                    "status": "unavailable", "service_health": "healthy",
                    "active_service": "comfyui_idle", "gpu_workload": "idle",
                    "queue_running": 0, "queue_pending": 0,
                    "vram_total_bytes": total, "vram_free_bytes": free,
                    "vram_free_ratio": ratio,
                }
            return {
                "status": "healthy", "service_health": "healthy",
                "active_service": "comfyui", "gpu_workload": "models_resident_or_memory_busy",
                "queue_running": 0, "queue_pending": 0,
                "vram_total_bytes": total, "vram_free_bytes": free,
                "vram_free_ratio": ratio,
            }
        if comfy.get("status") != "unavailable":
            return {
                "status": "unknown", "service_health": str(comfy.get("status", "unknown")),
                "active_service": "comfyui", "gpu_workload": "unknown",
            }
        model = probe_configured_model_service(str(Path(workspace).resolve()))
    except Exception as exc:
        return {"status": "unknown", "gpu_workload": "unknown", "reason": type(exc).__name__}

    if model.get("status") == "healthy":
        provider = str(model.get("provider", "local_model"))
        return {
            "status": "healthy", "service_health": "healthy",
            "active_service": provider, "gpu_workload": "active",
        }
    if model.get("status") == "unavailable":
        return {
            "status": "unavailable", "service_health": "unavailable",
            "active_service": "none", "gpu_workload": "idle",
        }
    return {
        "status": "unknown", "service_health": str(model.get("status", "unknown")),
        "active_service": "unknown", "gpu_workload": "unknown",
    }


class _ClosingConnection(sqlite3.Connection):
    """Make every connection context close its Windows file handle."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class GpuResourceBusy(RuntimeError):
    """The GPU is leased or a model service cannot safely yield its resources."""


class GpuResourceCoordinator:
    """SQLite-backed exclusive GPU lease and model-supervisor coordination."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        if self.database.is_symlink():
            raise ValueError("GPU resource database must not be a symlink")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10, isolation_level=None, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS gpu_lease ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), lease_id TEXT NOT NULL UNIQUE, "
                "owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, owner_pid INTEGER NOT NULL, "
                "acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL);"
                "CREATE TABLE IF NOT EXISTS gpu_supervisor_state ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), supervisor_id TEXT NOT NULL, "
                "pid INTEGER NOT NULL, heartbeat_at REAL NOT NULL, action TEXT NOT NULL, "
                "acknowledged_lease_id TEXT, child_pid INTEGER, restore_pending INTEGER NOT NULL DEFAULT 0, "
                "detail TEXT NOT NULL DEFAULT '{}');"
                "CREATE TABLE IF NOT EXISTS gpu_resource_events ("
                "event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
                "owner_kind TEXT, created_at REAL NOT NULL, detail TEXT NOT NULL DEFAULT '{}');"
            )

    @staticmethod
    def _ttl(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not LEASE_TTL_MIN_SECONDS <= value <= LEASE_TTL_MAX_SECONDS:
            raise ValueError(f"ttl_seconds must be between {LEASE_TTL_MIN_SECONDS} and {LEASE_TTL_MAX_SECONDS}")
        return value

    @staticmethod
    def _record_event(connection: sqlite3.Connection, event_type: str, owner_kind: str | None, now: float, detail: dict[str, Any] | None = None) -> None:
        connection.execute(
            "INSERT INTO gpu_resource_events(event_type,owner_kind,created_at,detail) VALUES(?,?,?,?)",
            (event_type, owner_kind, now, json.dumps(detail or {}, separators=(",", ":"))),
        )
        connection.execute(
            "DELETE FROM gpu_resource_events WHERE event_id NOT IN "
            "(SELECT event_id FROM gpu_resource_events ORDER BY event_id DESC LIMIT 200)")

    def _reap_expired(self, connection: sqlite3.Connection, now: float) -> None:
        row = connection.execute("SELECT lease_id,owner_kind FROM gpu_lease WHERE singleton=1 AND expires_at<=?", (now,)).fetchone()
        if row is not None:
            self._record_event(connection, "lease_expired", str(row["owner_kind"]), now)
            connection.execute("DELETE FROM gpu_lease WHERE singleton=1 AND lease_id=?", (row["lease_id"],))

    def acquire(self, owner_kind: str, owner_id: str, *, ttl_seconds: int = 120, now: float | None = None) -> str:
        if owner_kind not in {"video_generation", "video_encoding", "comfyui_generation", "model_inference"}:
            raise ValueError("unsupported GPU lease owner_kind")
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("owner_id must contain 1-128 characters")
        ttl = self._ttl(ttl_seconds)
        current = time.time() if now is None else float(now)
        lease_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._reap_expired(connection, current)
                existing = connection.execute("SELECT owner_kind,expires_at FROM gpu_lease WHERE singleton=1").fetchone()
                if existing is not None:
                    raise GpuResourceBusy(
                        f"GPU is reserved for {existing['owner_kind']} "
                        f"({max(0, int(existing['expires_at'] - current))}s lease remaining)"
                    )
                connection.execute(
                    "INSERT INTO gpu_lease(singleton,lease_id,owner_kind,owner_id,owner_pid,acquired_at,heartbeat_at,expires_at) "
                    "VALUES(1,?,?,?,?,?,?,?)",
                    (lease_id, owner_kind, owner_id, os.getpid(), current, current, current + ttl),
                )
                self._record_event(connection, "lease_acquired", owner_kind, current)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return lease_id

    def renew(self, lease_id: str, *, ttl_seconds: int = 120, now: float | None = None) -> bool:
        ttl = self._ttl(ttl_seconds)
        current = time.time() if now is None else float(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired(connection, current)
            cursor = connection.execute(
                "UPDATE gpu_lease SET heartbeat_at=?,expires_at=? WHERE singleton=1 AND lease_id=? AND expires_at>?",
                (current, current + ttl, lease_id, current),
            )
            connection.commit()
            return cursor.rowcount == 1

    def release(self, lease_id: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT owner_kind FROM gpu_lease WHERE singleton=1 AND lease_id=?", (lease_id,)).fetchone()
            if row is None:
                connection.commit()
                return False
            self._record_event(connection, "lease_released", str(row["owner_kind"]), current)
            connection.execute("DELETE FROM gpu_lease WHERE singleton=1 AND lease_id=?", (lease_id,))
            connection.commit()
            return True

    def active_lease(self, *, now: float | None = None) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired(connection, current)
            row = connection.execute(
                "SELECT lease_id,owner_kind,owner_id,owner_pid,acquired_at,heartbeat_at,expires_at FROM gpu_lease WHERE singleton=1"
            ).fetchone()
            connection.commit()
        return dict(row) if row is not None else None

    def record_supervisor(
        self, *, supervisor_id: str, pid: int, action: str, acknowledged_lease_id: str | None = None,
        child_pid: int | None = None, restore_pending: bool = False,
        detail: dict[str, Any] | None = None, now: float | None = None,
    ) -> None:
        allowed = {"starting", "healthy", "model_route_attention_required", "paused_for_video",
                   "external_model_active", "pause_failed", "restart_limit_reached", "start_failed",
                   "startup_timeout", "started", "already_healthy", "not_started", "gpu_reserved_for_video",
                   "gpu_memory_insufficient", "gpu_memory_unknown"}
        if action not in allowed:
            raise ValueError("unsupported model supervisor action")
        current = time.time() if now is None else float(now)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO gpu_supervisor_state(singleton,supervisor_id,pid,heartbeat_at,action,acknowledged_lease_id,child_pid,restore_pending,detail) "
                "VALUES(1,?,?,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                "supervisor_id=excluded.supervisor_id,pid=excluded.pid,heartbeat_at=excluded.heartbeat_at,"
                "action=excluded.action,acknowledged_lease_id=excluded.acknowledged_lease_id,"
                "child_pid=excluded.child_pid,restore_pending=excluded.restore_pending,detail=excluded.detail",
                (supervisor_id, pid, current, action, acknowledged_lease_id, child_pid, int(restore_pending),
                 json.dumps(detail or {}, separators=(",", ":"))),
            )

    def record_comfyui_memory_release(
        self, result: dict[str, Any], *, now: float | None = None,
    ) -> None:
        status = result.get("status")
        if status not in {"released", "deferred_queue_busy", "unconfirmed", "unavailable"}:
            raise ValueError("unsupported ComfyUI memory release status")
        detail: dict[str, Any] = {"status": status}
        for key in ("vram_free_bytes", "vram_total_bytes", "vram_free_ratio", "queue_running", "queue_pending"):
            value = result.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                detail[key] = value
        current = time.time() if now is None else float(now)
        with self._connect() as connection:
            self._record_event(connection, "comfyui_memory_release", "video_generation", current, detail)

    def supervisor_state(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT supervisor_id,pid,heartbeat_at,action,acknowledged_lease_id,child_pid,restore_pending,detail "
                "FROM gpu_supervisor_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["detail"] = json.loads(result["detail"])
        except (TypeError, json.JSONDecodeError):
            result["detail"] = {}
        result["restore_pending"] = bool(result["restore_pending"])
        return result

    def wait_for_video_access(
        self, lease_id: str, model_probe: Callable[[], dict[str, Any]], *, timeout_seconds: float = 75,
        poll_seconds: float = 1, now_fn: Callable[[], float] = time.time,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0, min(float(timeout_seconds), 180))
        last_reason = "model supervisor has not acknowledged the GPU lease"
        first_attempt = True
        while first_attempt or time.monotonic() <= deadline:
            first_attempt = False
            lease = self.active_lease(now=now_fn())
            if lease is None or lease["lease_id"] != lease_id:
                raise GpuResourceBusy("GPU lease was lost before video generation started")
            state = self.supervisor_state()
            health = model_probe()
            model_status = health.get("status")
            now = now_fn()
            fresh_supervisor = bool(state and now - float(state["heartbeat_at"]) <= SUPERVISOR_STALE_SECONDS)
            if fresh_supervisor:
                if state.get("acknowledged_lease_id") == lease_id and state.get("action") == "paused_for_video":
                    if model_status == "unavailable":
                        return {"mode": "supervisor_paused_owned_model", "supervisor_id": state["supervisor_id"]}
                    last_reason = "supervisor acknowledgement received but the model endpoint is not offline"
                elif state.get("action") == "external_model_active" and model_status == "unavailable":
                    # The previous heartbeat may predate this lease. If the configured
                    # Muse endpoint is offline, let the live supervisor observe and
                    # acknowledge this exact lease; external processes remain untouched.
                    last_reason = "waiting for the Muse model supervisor to reconcile its external-model observation with the video lease"
                elif state.get("action") in {"external_model_active", "pause_failed"}:
                    raise GpuResourceBusy("Muse model service could not safely yield GPU resources")
                else:
                    last_reason = "waiting for the Muse model supervisor to pause its managed model process"
            elif model_status == "unavailable":
                return {"mode": "model_offline_supervisor_not_running", "restore_managed": False}
            elif model_status == "healthy":
                raise GpuResourceBusy("configured model endpoint is active without a live Muse supervisor; refusing to stop an unowned process")
            else:
                raise GpuResourceBusy("configured model service state is uncertain; GPU generation is blocked")
            time.sleep(max(0.1, min(float(poll_seconds), 5)))
        raise GpuResourceBusy(last_reason)

    def status_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        lease = self.active_lease(now=current)
        state = self.supervisor_state()
        supervisor_age = max(0, int(current - float(state["heartbeat_at"]))) if state else None
        supervisor_fresh = bool(state and supervisor_age is not None and supervisor_age <= SUPERVISOR_STALE_SECONDS)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at,detail FROM gpu_resource_events "
                "WHERE event_type='comfyui_memory_release' ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        release_state: dict[str, Any]
        if row is None:
            release_state = {"status": "not_observed", "updated_age_seconds": None}
        else:
            try:
                release_detail = json.loads(row["detail"])
            except (TypeError, json.JSONDecodeError):
                release_detail = {}
            release_state = {
                "status": str(release_detail.get("status", "unknown")),
                "updated_age_seconds": max(0, int(current - float(row["created_at"]))),
            }
            for key in ("vram_free_bytes", "vram_total_bytes", "vram_free_ratio", "queue_running", "queue_pending"):
                value = release_detail.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    release_state[key] = value
        return {
            "status": "reserved" if lease else "idle",
            "lease": ({
                "owner_kind": lease["owner_kind"], "expires_in_seconds": max(0, int(lease["expires_at"] - current)),
                "heartbeat_age_seconds": max(0, int(current - lease["heartbeat_at"])),
            } if lease else None),
            "model_supervisor": ({
                "status": "online" if supervisor_fresh else "stale",
                "action": state["action"], "heartbeat_age_seconds": supervisor_age,
                "restore_pending": bool(state["restore_pending"]),
                "managed_child": state["child_pid"] is not None,
            } if state else {"status": "not_observed", "action": "unknown", "heartbeat_age_seconds": None,
                             "restore_pending": False, "managed_child": False}),
            "comfyui_memory_release": release_state,
            "policy": "exclusive_gpu_lease; unknown_vram_is_not_guessed; external_processes_are_never_stopped",
        }


class GpuLeaseSession:
    """A renewable exclusive GPU lease context shared by inference and encoding."""

    def __init__(
        self, coordinator: GpuResourceCoordinator, owner_id: str, *, ttl_seconds: int = 120,
        model_probe: Callable[[], dict[str, Any]] | None = None, wait_timeout_seconds: float = 75,
        owner_kind: str = "video_generation",
    ) -> None:
        if owner_kind not in {"video_generation", "video_encoding", "comfyui_generation", "model_inference"}:
            raise ValueError("unsupported GPU lease owner_kind")
        self.coordinator = coordinator
        self.owner_id = owner_id
        self.owner_kind = owner_kind
        self.ttl_seconds = GpuResourceCoordinator._ttl(ttl_seconds)
        self.model_probe = model_probe
        self.wait_timeout_seconds = wait_timeout_seconds
        self.lease_id: str | None = None
        self.coordination: dict[str, Any] = {}
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "GpuLeaseSession":
        self.lease_id = self.coordinator.acquire(self.owner_kind, self.owner_id, ttl_seconds=self.ttl_seconds)
        self._thread = threading.Thread(target=self._renew_loop, daemon=True, name="MuseGpuLeaseHeartbeat")
        self._thread.start()
        try:
            if self.model_probe is not None:
                self.coordination = self.coordinator.wait_for_video_access(
                    self.lease_id, self.model_probe, timeout_seconds=self.wait_timeout_seconds,
                )
            return self
        except Exception:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=2)
            self.coordinator.release(self.lease_id)
            raise

    def _renew_loop(self) -> None:
        interval = max(5, self.ttl_seconds // 3)
        while not self._stop.wait(interval):
            if not self.lease_id or not self.coordinator.renew(self.lease_id, ttl_seconds=self.ttl_seconds):
                self._lost.set()
                return

    def assert_valid(self) -> None:
        lease = self.coordinator.active_lease()
        if self._lost.is_set() or not self.lease_id or lease is None or lease["lease_id"] != self.lease_id:
            raise GpuResourceBusy("GPU resource lease expired or was lost during generation")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.lease_id:
            self.coordinator.release(self.lease_id)
        if exc is None and self._lost.is_set():
            raise GpuResourceBusy("GPU resource lease heartbeat was lost during generation")
        return False


def gpu_video_encoding_session(
    workspace: str | Path, owner_id: str, *, database: str | Path | None = None,
    wait_timeout_seconds: float = 75,
) -> GpuLeaseSession:
    """Create the shared, renewable GPU lease for any Muse AMF encode operation."""
    root = Path(workspace).resolve()
    configured_database = database or os.environ.get("MUSE_GPU_RESOURCE_DB")
    resource_database = (
        Path(configured_database).expanduser()
        if configured_database else root / "data" / "gpu-resources.sqlite3"
    )
    coordinator = GpuResourceCoordinator(resource_database)
    return GpuLeaseSession(
        coordinator, owner_id, owner_kind="video_encoding",
        model_probe=lambda: probe_gpu_workloads(root),
        wait_timeout_seconds=wait_timeout_seconds,
    )
