"""Verified, offline backup and no-overwrite restore for a local MemPalace directory.

The caller must stop/quiesce the MemPalace writer before backup or restore.
This module never touches Muse's canonical SQLite stores and never starts a
MemPalace process or rebuilds an index.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = ".muse-mempalace-backup.json"
MANIFEST_FORMAT = "muse-mempalace-palace-backup"
MANIFEST_VERSION = 1
_CHUNK_SIZE = 1024 * 1024


def _absolute(path: str | Path, field: str) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise ValueError(f"{field} must be an absolute local path")
    return Path(os.path.abspath(value))


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink path components are not allowed: {current.name}")


def _assert_disjoint(source: Path, destination: Path) -> None:
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be separate, non-nested directories")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_entries(root: Path, *, allow_manifest: bool = False) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            item = current_path / name
            if item.is_symlink():
                raise ValueError(f"symlinks are not allowed in a Palace backup: {item.name}")
            mode = item.lstat().st_mode
            if not stat.S_ISDIR(mode):
                raise ValueError(f"unsupported Palace entry type: {item.name}")
            entries.append({"path": item.relative_to(root).as_posix(), "kind": "dir"})
        for name in files:
            item = current_path / name
            if item.name == MANIFEST_NAME and current_path == root:
                if allow_manifest:
                    continue
                raise ValueError(f"reserved backup manifest name already exists: {MANIFEST_NAME}")
            if item.is_symlink():
                raise ValueError(f"symlinks are not allowed in a Palace backup: {item.name}")
            mode = item.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f"unsupported Palace entry type: {item.name}")
            entries.append({
                "path": item.relative_to(root).as_posix(),
                "kind": "file",
                "size": item.stat().st_size,
                "sha256": _sha256(item),
            })
    return sorted(entries, key=lambda item: (item["path"], item["kind"]))


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"format", "version", "created_at", "source_name", "entries"}:
        raise ValueError("backup manifest has an invalid shape")
    if value["format"] != MANIFEST_FORMAT or value["version"] != MANIFEST_VERSION:
        raise ValueError("unsupported MemPalace backup manifest")
    if not isinstance(value["created_at"], str) or not value["created_at"].strip():
        raise ValueError("backup manifest timestamp is invalid")
    if not isinstance(value["source_name"], str) or not value["source_name"].strip():
        raise ValueError("backup manifest source name is invalid")
    entries = value["entries"]
    if not isinstance(entries, list):
        raise ValueError("backup manifest entries must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") not in {"file", "dir"}:
            raise ValueError("backup manifest contains an invalid entry")
        expected = {"path", "kind", "size", "sha256"} if entry["kind"] == "file" else {"path", "kind"}
        if set(entry) != expected:
            raise ValueError("backup manifest entry fields are invalid")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
            raise ValueError("backup manifest path is invalid")
        relative = PurePosixPath(raw_path)
        if (relative.is_absolute() or relative.as_posix() != raw_path
                or any(part in {"", ".", ".."} for part in relative.parts)):
            raise ValueError("backup manifest path escapes the Palace")
        if raw_path == MANIFEST_NAME or raw_path in seen:
            raise ValueError("backup manifest contains a reserved or duplicate path")
        seen.add(raw_path)
        if entry["kind"] == "file":
            if isinstance(entry["size"], bool) or not isinstance(entry["size"], int) or entry["size"] < 0:
                raise ValueError("backup manifest file size is invalid")
            digest = entry["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("backup manifest SHA-256 is invalid")
    return value


def verify_backup(backup_path: str | Path) -> dict[str, Any]:
    backup = _absolute(backup_path, "backup_path")
    _reject_symlink_components(backup)
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError("backup_path must be an existing local directory")
    manifest_path = backup / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("backup manifest is missing")
    try:
        manifest = _validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest cannot be read") from exc
    actual = _tree_entries(backup, allow_manifest=True)
    if actual != manifest["entries"]:
        raise ValueError("backup content does not match its manifest")
    return {
        "verified": True,
        "file_count": sum(entry["kind"] == "file" for entry in actual),
        "directory_count": sum(entry["kind"] == "dir" for entry in actual),
        "total_bytes": sum(entry.get("size", 0) for entry in actual),
        "created_at": manifest["created_at"],
    }


def backup_palace(
    palace_path: str | Path,
    backup_path: str | Path,
    *,
    quiesced: bool = False,
) -> dict[str, Any]:
    """Create a verified backup; quiesced=True asserts the Palace writer is stopped."""
    if quiesced is not True:
        raise PermissionError("stop the MemPalace writer and confirm quiesced=True before backup")
    source = _absolute(palace_path, "palace_path")
    destination = _absolute(backup_path, "backup_path")
    _reject_symlink_components(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("palace_path must be an existing local directory")
    _assert_disjoint(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination)
    if os.path.lexists(destination):
        raise FileExistsError("backup destination already exists; backups are never overwritten")
    entries = _tree_entries(source)
    staging = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, staging, symlinks=True)
        manifest = {
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_name": source.name,
            "entries": entries,
        }
        manifest_path = staging / MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        actual = _tree_entries(staging, allow_manifest=True)
        if actual != entries:
            raise ValueError("backup verification failed after copy")
        os.replace(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_backup(destination) | {"backup_path": str(destination)}


def restore_palace(
    backup_path: str | Path,
    destination_path: str | Path,
    *,
    quiesced: bool = False,
) -> dict[str, Any]:
    """Restore to a new directory only; never overwrite an existing Palace."""
    if quiesced is not True:
        raise PermissionError("stop the MemPalace writer and confirm quiesced=True before restore")
    backup = _absolute(backup_path, "backup_path")
    destination = _absolute(destination_path, "destination_path")
    _reject_symlink_components(backup)
    _assert_disjoint(backup, destination)
    verified = verify_backup(backup)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination)
    if os.path.lexists(destination):
        raise FileExistsError("restore destination already exists; live Palaces are never overwritten")
    staging = destination.parent / f".{destination.name}.restore-{uuid.uuid4().hex}"
    try:
        shutil.copytree(
            backup,
            staging,
            symlinks=True,
            ignore=lambda directory, names: (
                {MANIFEST_NAME}
                if os.path.normcase(os.path.abspath(directory)) == os.path.normcase(str(backup))
                and MANIFEST_NAME in names else set()
            ),
        )
        manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
        if _tree_entries(staging) != manifest["entries"]:
            raise ValueError("restored Palace failed content verification")
        os.replace(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return verified | {"restored_path": str(destination)}
