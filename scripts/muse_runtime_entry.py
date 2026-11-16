from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
_LOG = (LOGS / "autonomous-runtime-task.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = _LOG
sys.stderr = _LOG
sys.path.insert(0, str(ROOT / "src"))

from muse.autonomous_runtime import main

raise SystemExit(main(["run", "--poll-seconds", "5"]))
