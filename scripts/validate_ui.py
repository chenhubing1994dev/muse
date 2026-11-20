from __future__ import annotations

import subprocess

COMMANDS = (
    ("npm.cmd", "run", "check"),
    ("npm.cmd", "test"),
    ("npm.cmd", "run", "build"),
)

def main() -> int:
    for command in COMMANDS:
        print(f"\n$ {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd="ui-prototype", check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
