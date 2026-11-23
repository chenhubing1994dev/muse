from __future__ import annotations

import argparse
import hmac
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MAX_SOURCE_DATABASES = 16
BACKUP_PAGE_COUNT = 256


class PortalMaintenanceError(ValueError):
    pass


def _regular_database(path: str | Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise PortalMaintenanceError(f"{label} must be an existing regular non-symlink file")
    return candidate.resolve(strict=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_snapshot(source: Path, destination: Path) -> dict[str, object]:
    if destination.is_symlink() or destination.exists():
        raise PortalMaintenanceError("destination already exists or is a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    except FileExistsError as exc:
        raise PortalMaintenanceError("destination already exists") from exc

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_uri = source.resolve(strict=True).as_uri() + "?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=10.0)
        destination_connection = sqlite3.connect(destination, timeout=10.0)
        source_connection.backup(destination_connection, pages=BACKUP_PAGE_COUNT, sleep=0.05)
        integrity = destination_connection.execute("PRAGMA quick_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise PortalMaintenanceError("SQLite backup failed PRAGMA quick_check")
        destination_connection.commit()
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        return {
            "backup_file": str(destination),
            "sha256": _sha256_file(destination),
            "size_bytes": destination.stat().st_size,
        }
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        for candidate in (destination, Path(str(destination) + "-wal"), Path(str(destination) + "-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def backup_databases(
    sources: Iterable[str | Path],
    backup_dir: str | Path,
) -> dict[str, object]:
    source_paths = [_regular_database(item, "source database") for item in sources]
    if not source_paths or len(source_paths) > MAX_SOURCE_DATABASES:
        raise PortalMaintenanceError(f"provide between 1 and {MAX_SOURCE_DATABASES} source databases")
    resolved = [str(item) for item in source_paths]
    if len(set(resolved)) != len(resolved):
        raise PortalMaintenanceError("duplicate source database paths are not allowed")

    target_dir = Path(backup_dir).expanduser()
    if target_dir.is_symlink():
        raise PortalMaintenanceError("backup directory must not be a symlink")
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target_dir.is_dir():
        raise PortalMaintenanceError("backup directory must be a directory")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    records: list[dict[str, object]] = []
    created: list[Path] = []
    manifest_path = target_dir / f"manifest-{stamp}.json"
    temporary_manifest: Path | None = None
    try:
        for source in source_paths:
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-_") or "database"
            source_tag = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
            destination = target_dir / f"{safe_stem}-{source_tag}-{stamp}.sqlite3"
            copied = _copy_snapshot(source, destination)
            created.append(destination)
            records.append({
                "source_name": source.name,
                **copied,
            })

        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backups": records,
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=".muse-backup-manifest-",
            suffix=".tmp", dir=target_dir, delete=False,
        ) as stream:
            temporary_manifest = Path(stream.name)
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_manifest, manifest_path)
        temporary_manifest = None
        return {"manifest": str(manifest_path), "backups": records}
    except Exception:
        for path in created:
            for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
        if temporary_manifest is not None:
            temporary_manifest.unlink(missing_ok=True)
        raise


def restore_database(
    backup_file: str | Path,
    destination: str | Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, object]:

    source = _regular_database(backup_file, "backup file")
    target = Path(destination).expanduser()
    if target.is_symlink() or target.exists():
        raise PortalMaintenanceError("restore destination must not already exist")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise PortalMaintenanceError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    actual_sha256 = _sha256_file(source)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise PortalMaintenanceError("backup SHA-256 does not match expected SHA-256")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise PortalMaintenanceError("restore destination parent must not be a symlink")
    copied = _copy_snapshot(source, target)
    return {
        "source_name": source.name,
        "destination": str(target),
        **copied,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and verify SQLite backups for Muse self-hosted services")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="create consistent online backups")
    backup_parser.add_argument("--source-db", action="append", required=True, type=Path)
    backup_parser.add_argument("--backup-dir", required=True, type=Path)

    restore_parser = subparsers.add_parser("restore", help="restore one verified backup to a new path")
    restore_parser.add_argument("--backup-file", required=True, type=Path)
    restore_parser.add_argument("--expected-sha256", required=True)
    restore_parser.add_argument("--destination", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_databases(args.source_db, args.backup_dir)
        else:
            result = restore_database(
                args.backup_file, args.destination, expected_sha256=args.expected_sha256,
            )
    except (OSError, sqlite3.Error, PortalMaintenanceError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
