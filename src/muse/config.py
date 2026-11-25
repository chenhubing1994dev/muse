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
    not_tables = [section for section in required_sections if not isinstance(data[section], dict)]
    if not_tables:
        raise ValueError(f"Configuration sections must be tables: {', '.join(not_tables)}")
    for section, key in (("app", "name"), ("scheduler", "enabled")):
        if key not in data[section]:
            raise ValueError(f"Missing configuration key: [{section}].{key}")
    if not isinstance(data["scheduler"]["enabled"], bool):
        raise ValueError("[scheduler].enabled must be true or false")

    if data["memory"].get("backend") != "sqlite":
        raise ValueError("Only the sqlite memory backend is supported by this scaffold")

    return data
