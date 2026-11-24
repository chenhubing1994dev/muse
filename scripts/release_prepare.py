#!/usr/bin/env python3
"""Compute the Muse calendar version and optionally stamp it into build manifests.

Used by .github/workflows/scheduled-release.yml.  Import-safe and standard
library only so it can run on every GitHub-hosted runner without dependencies.

Examples
--------
    python scripts/release_prepare.py --date 2026-11-24
    python scripts/release_prepare.py --apply --date 2026-11-24
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MANIFESTS = (
    ROOT / "pyproject.toml",
    ROOT / "desktop" / "src-tauri" / "Cargo.toml",
    ROOT / "desktop" / "src-tauri" / "tauri.conf.json",
)

VERSION_RE = re.compile(
    r'(?P<prefix>^version\s*=\s*["\']|"version"\s*:\s*")'
    r'(?P<version>[0-9][0-9A-Za-z.+_-]*)'
    r'(?P<suffix>["\'])',
    re.MULTILINE,
)


def calendar_version(day: _dt.date) -> str:
    """Return a semver/PEP440 compatible CalVer: YYYY.M.M (no leading zero)."""
    return f"{day.year}.{day.month}.{day.day}"


def tag_for(version: str) -> str:
    return f"v{version}"


def read_current_version() -> str:
    pyproject = MANIFESTS[0]
    text = pyproject.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    return match.group("version") if match else "unknown"


def stamp_version(version: str, *, dry_run: bool = False) -> list[str]:
    """Rewrite the version field of each manifest. Returns touched paths."""
    changed: list[str] = []
    for path in MANIFESTS:
        original = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            return f"{match.group('prefix')}{version}{match.group('suffix')}"

        updated, count = VERSION_RE.subn(replace, original, count=1)
        if count != 1:
            raise RuntimeError(f"No version field found in {path}")
        if updated != original:
            changed.append(str(path.relative_to(ROOT)).replace("\\", "/"))
            if not dry_run:
                path.write_text(updated, encoding="utf-8", newline="\n")
    return changed


def write_github_output(values: dict[str, str]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD (defaults to today, UTC)")
    parser.add_argument("--apply", action="store_true", help="Stamp manifests in place")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(argv)

    day = _dt.date.fromisoformat(args.date) if args.date else _dt.datetime.now(_dt.timezone.utc).date()
    version = calendar_version(day)
    previous = read_current_version()
    changed = stamp_version(version, dry_run=args.dry_run) if (args.apply or args.dry_run) else []

    result = {
        "version": version,
        "tag": tag_for(version),
        "previous_version": previous,
        "short_date": day.strftime("%Y%m%d"),
        "changed": ",".join(changed),
    }
    write_github_output(result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
