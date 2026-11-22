from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Callable

from muse.web_portal import PortalError, PortalSessionStore


def rotate_admin_password(
    session_db: str | Path,
    *,
    prompt: Callable[[str], str] | None = None,
) -> None:
    """Interactively rotate the self-hosted Portal administrator password."""
    database = Path(session_db).expanduser()
    if database.is_symlink() or not database.is_file():
        raise PortalError("session database must be an existing regular non-symlink file")

    read_secret = prompt or getpass.getpass
    current = read_secret("Current Muse Portal administrator password: ")
    replacement = read_secret("New Muse Portal administrator password (32-1024 UTF-8 bytes): ")
    confirmation = read_secret("Confirm new administrator password: ")
    if replacement != confirmation:
        raise PortalError("new administrator password confirmation does not match")

    store = PortalSessionStore(database, current)
    try:
        store.rotate_password(current, replacement)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate the Muse self-hosted Portal administrator password")
    parser.add_argument("--session-db", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        rotate_admin_password(args.session_db)
    except (OSError, PortalError) as exc:
        parser.error(str(exc))
    print("Muse Portal administrator password rotated; all existing browser sessions were revoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
