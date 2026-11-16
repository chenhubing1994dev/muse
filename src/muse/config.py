"""Configuration loading with Python's standard-library TOML parser."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("rb") as stream:
        data = tomllib.load(stream)

    required_sections = ("app", "memory", "scheduler", "notifications", "tools", "models")
    missing = [section for section in required_sections if section not in data]
    if missing:
        raise ValueError(f"Missing configuration sections: {', '.join(missing)}")

    if data["memory"].get("backend") != "sqlite":
        raise ValueError("Only the sqlite memory backend is supported by this scaffold")

    return data
