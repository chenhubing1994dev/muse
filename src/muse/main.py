"""Minimal command-line entry point for Muse."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from muse.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Muse personal AI companion")
    parser.add_argument("--check", action="store_true", help="validate local configuration and paths")
    parser.add_argument("--config", default="config/muse.toml", help="configuration TOML path")
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        return 0

    config_path = Path(args.config)
    if not config_path.exists():
        example = Path("config/muse.example.toml")
        print(f"No local config at {config_path}. Copy {example} to config/muse.toml to begin.")
        return 0

    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"Configuration check failed: {exc}", file=sys.stderr)
        return 2

    print(f"Muse {config['app']['name']} configuration is valid.")
    print("Scheduler: disabled" if not config["scheduler"]["enabled"] else "Scheduler: configured (no worker started)")
    print("No background service was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
