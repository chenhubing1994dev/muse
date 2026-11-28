"""Structured journal for secondary failures that must not abort the primary path.

Muse forbids ``except Exception: pass``. When a best-effort side effect (telemetry,
cleanup, compensating abort) fails while another error is already being handled, the
failure is recorded here instead of being silently swallowed:

* a ``logging`` WARNING on the ``muse.suppressed`` logger;
* one bounded JSON line appended to ``<journal>`` (UTF-8), where ``<journal>`` is
  ``$MUSE_ERROR_JOURNAL`` or ``<project>/data/diag/suppressed_errors.jsonl``;
* an in-process counter readable through :func:`suppressed_error_stats` so health
  panels and ``scripts/muse_diag.py`` can surface the count.

``record_suppressed_error`` itself never raises: a failing journal write is counted in
``journal_write_failures`` so the problem stays visible without crashing the caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("muse.suppressed")
_LOCK = threading.Lock()
_STATS: dict[str, Any] = {"total": 0, "by_component": {}, "journal_write_failures": 0, "last": None}
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_FIELD_CHARS = 2000


def default_journal_path() -> Path:
    override = os.environ.get("MUSE_ERROR_JOURNAL", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / "data" / "diag" / "suppressed_errors.jsonl").resolve()


def record_suppressed_error(component: str, error: BaseException, **context: Any) -> dict[str, Any]:
    """Record a deliberately non-fatal failure. Returns the stored entry; never raises."""
    try:
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "component": str(component)[:200],
            "error_type": type(error).__name__,
            "error": str(error)[:MAX_FIELD_CHARS],
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-MAX_FIELD_CHARS:],
            "context": {str(k)[:80]: str(v)[:300] for k, v in list(context.items())[:20]},
        }
    except BaseException:  # pragma: no cover - formatting must never break the caller
        entry = {"component": str(component)[:200], "error_type": "unformattable"}
    with _LOCK:
        _STATS["total"] += 1
        by = _STATS["by_component"]
        by[entry["component"]] = by.get(entry["component"], 0) + 1
        _STATS["last"] = {k: entry.get(k) for k in ("at", "component", "error_type", "error")}
    try:
        _LOG.warning("suppressed secondary failure in %s: %s: %s", entry["component"],
                     entry.get("error_type"), entry.get("error", ""))
    except BaseException:  # pragma: no cover
        with _LOCK:  # logging misconfiguration is non-fatal but must stay visible
            _STATS["journal_write_failures"] += 1
    try:
        path = default_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_JOURNAL_BYTES:
            rotated = path.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            path.replace(rotated)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except BaseException:
        with _LOCK:
            _STATS["journal_write_failures"] += 1
    return entry


def suppressed_error_stats() -> dict[str, Any]:
    with _LOCK:
        return {"total": _STATS["total"], "by_component": dict(_STATS["by_component"]),
                "journal_write_failures": _STATS["journal_write_failures"], "last": _STATS["last"],
                "journal_path": str(default_journal_path())}


def read_journal_tail(limit: int = 50) -> list[dict[str, Any]]:
    path = default_journal_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)):]
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"component": "error_journal", "error_type": "corrupt_line", "error": line[:200]})
    return out
