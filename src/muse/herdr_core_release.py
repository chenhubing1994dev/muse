"""Pinned Herdr Core release metadata used by the opt-in Windows smoke test."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HerdrCoreReleasePin:
    version: str
    source_commit: str
    protocol_generation: int
    openrpc_version: str
    schema_blob_sha: str
    target: str
    archive_name: str
    archive_url: str
    archive_sha256: str
    manifest_url: str


PINNED_HERDR_CORE = HerdrCoreReleasePin(
    version="0.1.21",
    source_commit="1b0393f80c35b1dae34f37934d33d0144a8fc8d7",
    protocol_generation=1,
    openrpc_version="1.3.2",
    schema_blob_sha="2048664078012a88f90e49d1819d44f8f1e18b86",
    target="windows-x86_64",
    archive_name="herdr-core-windows-x86_64.zip",
    archive_url="https://releases.herdr.dev/core/0.1.21/herdr-core-windows-x86_64.zip",
    archive_sha256="a8984634c35b58ba82d0aec078d5a5e6004da01d5a25b3daf822ab5e31c7312b",
    manifest_url="https://releases.herdr.dev/core/0.1.21/manifest.json",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release_archive(
    path: str | Path,
    *,
    expected_sha256: str = PINNED_HERDR_CORE.archive_sha256,
) -> str:
    archive = Path(path)
    if not archive.is_file():
        raise FileNotFoundError(f"Herdr Core release archive is not a file: {archive}")
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise ValueError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    actual = sha256_file(archive)
    if not hmac.compare_digest(actual, expected_sha256):
        raise ValueError("Herdr Core release archive SHA-256 does not match the pinned release manifest")
    return actual
