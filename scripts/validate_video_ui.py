from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui-prototype"


def main() -> int:
    outcomes = []
    for label, script in (
        ("node-tests", "npm.cmd run test"),
        ("svelte-check", "npm.cmd run check"),
        ("vite-build-native-config", "npm.cmd run build"),
    ):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", script],
            cwd=UI,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        print(f"=== {label}: exit={result.returncode} ===")
        print((result.stdout or "")[-12000:])
        print((result.stderr or "")[-12000:])
        outcomes.append(result.returncode)
    return 0 if all(code == 0 for code in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
