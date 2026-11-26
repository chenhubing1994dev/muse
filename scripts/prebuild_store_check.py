"""Fail-closed pre-build contract for the four required desktop Stores."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "src" / "muse_sidecar_main.py"
STATUS = ROOT / "src" / "muse" / "status_api.py"
HEALTH = ROOT / "src" / "muse" / "store_health.py"
REQUIRED = {
    "feedback": ("improvement_db", "improvement_db"),
    "inspiration": ("knowledge_db", "knowledge_db"),
    "artifacts": ("reports_dir", "report_dir"),
    "agents": ("agent_db", "agent_db"),
}


def fail(message: str) -> None:
    raise SystemExit("[pre-build-check] FAIL: " + message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    for path in (ENTRY, STATUS, HEALTH):
        if not path.is_file():
            fail(f"required source missing: {path}")
    entry_text = ENTRY.read_text(encoding="utf-8")
    status_text = STATUS.read_text(encoding="utf-8")
    health_text = HEALTH.read_text(encoding="utf-8")
    try:
        ast.parse(entry_text, filename=str(ENTRY))
        ast.parse(status_text, filename=str(STATUS))
        ast.parse(health_text, filename=str(HEALTH))
    except SyntaxError as exc:
        fail(f"Python syntax error: {exc}")
    for key in REQUIRED:
        if f'"{key}"' not in health_text:
            fail(f"Store registry does not register required key {key!r}")
        if f'"{key}"' not in entry_text:
            fail(f"MuseEngine.start does not inject Store key {key!r}")
    for path_key, kwarg in REQUIRED.values():
        if f'paths["{path_key}"]' not in entry_text:
            fail(f"startup injection missing paths[{path_key!r}]")
        if f"{kwarg}=" not in entry_text:
            fail(f"status API constructor missing {kwarg}= injection")
    for endpoint in ("/api/v1/health", "/api/v1/store-health"):
        if endpoint not in status_text:
            fail(f"runtime health endpoint missing: {endpoint}")
    if "validate_store_registry" not in entry_text:
        fail("startup Store self-check is not called")
    if "PRAGMA busy_timeout" not in health_text or "INSERT INTO muse_startup_probe" not in health_text:
        fail("SQLite read/write probe implementation is incomplete")
    if "os.fsync" not in health_text or "probe.read_bytes()" not in health_text:
        fail("Artifact directory durable read/write probe implementation is incomplete")
    report = {
        "status": "passed",
        "stores": list(REQUIRED),
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256(path) for path in (ENTRY, STATUS, HEALTH)
        },
    }
    out = ROOT / "build-sidecar" / "pre-build-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
