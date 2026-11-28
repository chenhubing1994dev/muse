"""Deep health check for the local Muse runtime (backlog J-1).

``build_deep_health`` answers "is it safe to keep running unattended?" with a fixed set of
read-only checks. Every check reports ``ok`` / ``warn`` / ``fail`` / ``error``:

* ``commander_tickets`` — ticket backlog (queued + waiting_approval) and pending approvals,
  read from ``<commander>/tasks.sqlite3`` in SQLite read-only mode;
* ``audit_chain``       — :func:`muse.audit.verify` over the Commander root (receipts, chained
  audit logs, cost chain); any broken chain is ``fail``;
* ``ledger_size``       — total bytes of the append-only audit/cost ledgers;
* ``disk_free``         — free bytes on the workspace volume;
* ``kill_switch``       — presence of the Commander ``KILL_SWITCH`` file (``warn``: Muse is stopped);
* ``last_diagnostic``   — ``reports/diag_latest.json``: missing/stale is ``warn``, unreadable or not
  ``PASS`` is ``fail``;
* ``pending_human``     — number of bullet lines in ``docs/PENDING_HUMAN.md`` (informational).

Fail closed: a check that raises is reported as ``error`` and makes the overall status
``unhealthy``. Overall: any fail/error → ``unhealthy``; else any warn → ``degraded``; else ``ok``.
Nothing here writes, creates or repairs anything.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

LEDGER_FILES = ("receipts.jsonl", "bypass_audit.jsonl", "safety_actions.jsonl", "ruflo_calls.jsonl",
                "cost_chain.jsonl")
DEFAULT_LIMITS: dict[str, float] = {
    "backlog_warn": 50,
    "pending_approvals_warn": 20,
    "ledger_bytes_warn": 256 * 1024 * 1024,
    "disk_free_fail_bytes": 1 * 1024 ** 3,
    "disk_free_warn_bytes": 5 * 1024 ** 3,
    "diag_max_age_seconds": 24 * 3600,
}
SEVERITY = {"ok": 0, "warn": 1, "fail": 2, "error": 2}


def _commander_tickets(commander: Path, limits: dict[str, float]) -> dict[str, Any]:
    db = commander / "tasks.sqlite3"
    if not db.is_file():
        return {"status": "ok", "configured": False, "by_status": {}, "backlog": 0, "pending_approvals": 0}
    uri = "file:" + quote(db.resolve().as_posix(), safe="/:") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        rows = con.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
    finally:
        con.close()
    by = {str(s): int(n) for s, n in rows}
    pending = by.get("waiting_approval", 0)
    backlog = pending + by.get("queued", 0)
    status = "warn" if backlog > limits["backlog_warn"] or pending > limits["pending_approvals_warn"] else "ok"
    return {"status": status, "configured": True, "by_status": by, "backlog": backlog, "pending_approvals": pending}


def _audit_chain(commander: Path, _limits: dict[str, float]) -> dict[str, Any]:
    from muse.audit import verify
    report = verify(commander)
    broken = [c for c in report["checks"] if not c.get("ok")]
    return {"status": "ok" if report["ok"] and not broken else "fail",
            "verified_files": len(report["checks"]),
            "broken": [{"file": c.get("file"), "errors": list(c.get("errors") or [])[:3]} for c in broken]}


def _ledger_size(commander: Path, limits: dict[str, float]) -> dict[str, Any]:
    sizes = {name: (commander / name).stat().st_size for name in LEDGER_FILES if (commander / name).is_file()}
    total = sum(sizes.values())
    return {"status": "warn" if total > limits["ledger_bytes_warn"] else "ok", "total_bytes": total, "files": sizes}


def _kill_switch(commander: Path, _limits: dict[str, float]) -> dict[str, Any]:
    engaged = (commander / "KILL_SWITCH").exists()
    return {"status": "warn" if engaged else "ok", "engaged": engaged}


def _last_diagnostic(reports: Path, limits: dict[str, float], now: float) -> dict[str, Any]:
    path = reports / "diag_latest.json"
    if not path.is_file():
        return {"status": "warn", "present": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("diag_latest.json is not a JSON object")
    age = max(0.0, now - path.stat().st_mtime)
    overall = data.get("overall")
    if overall != "PASS" or data.get("exit_code") != 0:
        status = "fail"
    elif age > limits["diag_max_age_seconds"]:
        status = "warn"
    else:
        status = "ok"
    return {"status": status, "present": True, "overall": overall, "exit_code": data.get("exit_code"),
            "age_seconds": round(age, 1), "probe_summary": data.get("probe_summary"),
            "finished_at": data.get("finished_at")}


def _pending_human(docs: Path) -> dict[str, Any]:
    path = docs / "PENDING_HUMAN.md"
    if not path.is_file():
        return {"status": "ok", "present": False, "items": 0}
    items = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.lstrip().startswith("- "))
    return {"status": "ok", "present": True, "items": items}


def build_deep_health(workspace_root: str | Path, *, commander_root: str | Path | None = None,
                      now: float | None = None, limits: dict[str, float] | None = None,
                      disk_usage: Callable[[str], Any] = shutil.disk_usage) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    if commander_root is None:
        from muse.commander_gate import commander_root as _default_commander_root
        commander = _default_commander_root(root)
    else:
        commander = Path(commander_root).resolve()
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    stamp = time.time() if now is None else float(now)

    def disk(_c: Path, l: dict[str, float]) -> dict[str, Any]:
        free = int(disk_usage(str(root)).free)
        status = "fail" if free < l["disk_free_fail_bytes"] else "warn" if free < l["disk_free_warn_bytes"] else "ok"
        return {"status": status, "free_bytes": free}

    plan: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("commander_tickets", lambda: _commander_tickets(commander, lim)),
        ("audit_chain", lambda: _audit_chain(commander, lim)),
        ("ledger_size", lambda: _ledger_size(commander, lim)),
        ("disk_free", lambda: disk(commander, lim)),
        ("kill_switch", lambda: _kill_switch(commander, lim)),
        ("last_diagnostic", lambda: _last_diagnostic(root / "reports", lim, stamp)),
        ("pending_human", lambda: _pending_human(root / "docs")),
    ]
    checks: dict[str, dict[str, Any]] = {}
    for name, fn in plan:
        try:
            result = fn()
            if result.get("status") not in SEVERITY:
                raise ValueError(f"check returned unknown status {result.get('status')!r}")
        except Exception as exc:  # fail closed: a check that cannot run is never "ok"
            result = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        checks[name] = result
    worst = max(SEVERITY[c["status"]] for c in checks.values())
    overall = "unhealthy" if worst >= 2 else "degraded" if worst == 1 else "ok"
    return {"service": "muse-deep-health", "status": overall, "checks": checks,
            "failing": sorted(k for k, c in checks.items() if SEVERITY[c["status"]] >= 2),
            "warnings": sorted(k for k, c in checks.items() if c["status"] == "warn"),
            "commander_root": str(commander), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "read_only": True}
