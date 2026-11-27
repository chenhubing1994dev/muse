"""Fail-closed startup registration and read/write probes for desktop API stores."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class StoreSpec:
    key: str
    display_name: str
    kind: str


_STORE_REGISTRY: dict[str, StoreSpec] = {
    "feedback": StoreSpec("feedback", "Feedback Store", "sqlite"),
    "inspiration": StoreSpec("inspiration", "Inspiration / Knowledge Store", "sqlite"),
    "artifacts": StoreSpec("artifacts", "Artifact Store", "directory"),
    "agents": StoreSpec("agents", "Agent Registry", "sqlite"),
}


def register_store(key: str, display_name: str, kind: str) -> None:
    """Register an additional required Store before startup; duplicate keys fail closed."""
    if not isinstance(key, str) or not key.strip() or key in _STORE_REGISTRY:
        raise ValueError(f"invalid or duplicate Store registration: {key!r}")
    if kind not in {"sqlite", "directory"}:
        raise ValueError(f"unsupported Store kind for {key}: {kind!r}")
    _STORE_REGISTRY[key] = StoreSpec(key, display_name, kind)


def registered_stores() -> tuple[StoreSpec, ...]:
    return tuple(_STORE_REGISTRY.values())


def _probe_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # B391-P2-34: ``with sqlite3.connect(...)`` only commits/rolls back; it never closes the handle, which kept the
    # database file locked on Windows until garbage collection (WinError 32 when deleting temp dirs).
    with closing(sqlite3.connect(str(path), timeout=3.0)) as connection:
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("CREATE TEMP TABLE muse_startup_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO muse_startup_probe(value) VALUES (1)")
        row = connection.execute("SELECT value FROM muse_startup_probe").fetchone()
        if row != (1,):
            raise RuntimeError("SQLite temporary write/read probe returned an unexpected value")
        connection.rollback()
    if not path.is_file():
        raise RuntimeError("SQLite database file was not created")


def _probe_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError("artifact directory is missing, not a directory, or is a symlink")
    fd, name = tempfile.mkstemp(prefix=".muse-store-probe-", dir=str(path))
    probe = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"muse-store-probe")
            stream.flush()
            os.fsync(stream.fileno())
        if probe.read_bytes() != b"muse-store-probe":
            raise RuntimeError("artifact directory write/read probe failed")
    finally:
        probe.unlink(missing_ok=True)


def validate_store_registry(paths: Mapping[str, str | Path]) -> dict[str, dict[str, str]]:
    """Require every registered Store path and prove real filesystem/SQLite read-write access."""
    results: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    for spec in registered_stores():
        raw = paths.get(spec.key)
        if raw is None:
            reason = "path was not injected into MuseEngine.start"
            results[spec.key] = {"name": spec.display_name, "status": "error", "reason": reason}
            failures.append(f"{spec.display_name}: {reason}")
            continue
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise RuntimeError("configured path must be absolute")
            if spec.kind == "sqlite":
                _probe_sqlite(path)
            else:
                _probe_directory(path)
            results[spec.key] = {"name": spec.display_name, "status": "healthy", "path": str(path)}
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            results[spec.key] = {"name": spec.display_name, "status": "error", "reason": reason}
            failures.append(f"{spec.display_name}: {reason}")
    if failures:
        raise RuntimeError("Muse Store startup self-check failed: " + "; ".join(failures))
    return results
