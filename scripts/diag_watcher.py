#!/usr/bin/env python3
"""Optional one-shot-started diagnostic watcher (lets an agent request a diagnostic run).

Started once by the human (``start_diag_watcher.bat``). It polls for
``reports/diag_request.flag``; when the flag appears it deletes it and runs exactly one
fixed command — ``scripts/diag_auto.py`` with the current interpreter. The flag's content is
ignored: no command, argument or path is ever read from it, so the watcher cannot be used to
run arbitrary code. Results land in ``reports/diag_latest.json|md`` and ``diag_last_run.log``.
Stop with Ctrl+C or by closing the window.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAG = ROOT / "reports" / "diag_request.flag"
LOCK = ROOT / "reports" / "diag_watcher.lock"
LOG = ROOT / "reports" / "diag_watcher.log"


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().astimezone().isoformat()} {line}\n")
    print(line, flush=True)


def main() -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"[Muse] watcher already running (lock: {LOCK}); delete the lock if it is stale.")
        return 3
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.close(fd)
    log(f"[Muse] diag watcher started pid={os.getpid()}; waiting for {FLAG}")
    try:
        while True:
            if FLAG.exists():
                FLAG.unlink()
                log("[Muse] request received -> running scripts/diag_auto.py")
                env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "src")}
                rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "diag_auto.py")], cwd=str(ROOT), env=env,
                                    stdin=subprocess.DEVNULL, timeout=1500).returncode
                log(f"[Muse] diagnostic finished rc={rc}")
            time.sleep(3)
    except KeyboardInterrupt:
        log("[Muse] watcher stopped by user")
    finally:
        LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
