from __future__ import annotations

import argparse
import getpass
import secrets
from pathlib import Path
from typing import Callable

from muse.web_portal import PortalError, PortalSessionStore


def generate_initial_password() -> str:
    """Generate a high-entropy URL-safe secret for first-run Portal setup."""
    return secrets.token_urlsafe(48)


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
    parser = argparse.ArgumentParser(description="Manage the Muse self-hosted Portal administrator credential")
    parser.add_argument("--session-db", type=Path)
    parser.add_argument(
        "--generate-initial-password", action="store_true",
        help="print a fresh 48-byte cryptographically random URL-safe secret and exit",
    )
    args = parser.parse_args(argv)
    if args.generate_initial_password:
        if args.session_db is not None:
            parser.error("--session-db cannot be combined with --generate-initial-password")
        print(generate_initial_password())
        return 0
    if args.session_db is None:
        parser.error("--session-db is required unless --generate-initial-password is selected")
    try:
        rotate_admin_password(args.session_db)
    except (OSError, PortalError) as exc:
        parser.error(str(exc))
    print("Muse Portal administrator password rotated; all existing browser sessions were revoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
