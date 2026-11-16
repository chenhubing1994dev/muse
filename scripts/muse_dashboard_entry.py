from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
_LOG = (LOGS / "dashboard-task.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = _LOG
sys.stderr = _LOG
sys.path.insert(0, str(ROOT / "src"))

from muse.local_dashboard import main

lan_args = []
address_file = ROOT / "data" / "desktop" / "lan-address.txt"
if address_file.is_file():
    address = address_file.read_text(encoding="ascii").strip()
    if address:
        lan_args = ["--advertise-ip", address]

raise SystemExit(main([
    "--root", str(ROOT),
    "--state-file", str(ROOT / "data" / "desktop" / "dashboard.json"),
    "--port", "8766",
    "--lan", *lan_args,
]))
