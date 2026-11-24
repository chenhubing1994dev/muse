from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


def validate(root: Path) -> dict:
    root = root.resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    video = root / "muse-ai-fact-check-64s-silent.mp4"
    package = root / "delivery-package.zip"
    if not video.is_file() or not package.is_file():
        raise FileNotFoundError("Video or delivery package is missing")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for full decode")
    subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video), "-f", "null", "-"],
        check=True, timeout=600, capture_output=True,
    )
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC check failed")
        names = set(archive.namelist())
        expected = set(manifest["files"]) | {"manifest.json"}
        if names != expected:
            raise RuntimeError(f"ZIP member set mismatch: {sorted(names ^ expected)}")
        for name, expected_sha in manifest["files"].items():
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected_sha:
                raise RuntimeError(f"SHA-256 mismatch: {name}")
        packaged_manifest = json.loads(archive.read("manifest.json"))
        if packaged_manifest != manifest:
            raise RuntimeError("Packaged manifest differs from the working manifest")
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    recorded_sha = (root / "package.sha256").read_text(encoding="ascii").split()[0]
    if package_sha != recorded_sha:
        raise RuntimeError("Delivery package SHA-256 mismatch")
    if manifest.get("ready_to_publish") is not False:
        raise RuntimeError("Release gate must remain fail-closed pending human review")
    rights = json.loads((root / "rights-manifest.json").read_text(encoding="utf-8"))
    if rights.get("commercial_use_allowed") is not False:
        raise RuntimeError("Rights gate must remain fail-closed pending editorial review")
    font = rights.get("font", {})
    if font.get("family") != "Noto Sans CJK HK" or font.get("license") != "SIL Open Font License 1.1":
        raise RuntimeError("Bundled font metadata is not the verified Noto CJK OFL 1.1 asset")
    if font.get("license_verified") is not True:
        raise RuntimeError("Bundled font license is not marked verified")
    for field in ("font_file", "license_file"):
        relative = font.get(field)
        if not isinstance(relative, str) or relative not in manifest.get("files", {}):
            raise RuntimeError(f"Font package file missing from manifest: {field}")
    license_text = (root / font["license_file"]).read_text(encoding="utf-8")
    if "SIL OPEN FONT LICENSE" not in license_text or "Version 1.1" not in license_text:
        raise RuntimeError("Packaged font license text is not SIL OFL 1.1")
    if hashlib.sha256((root / font["font_file"]).read_bytes()).hexdigest() != font.get("font_sha256"):
        raise RuntimeError("Packaged font SHA-256 does not match rights manifest")
    if hashlib.sha256((root / font["license_file"]).read_bytes()).hexdigest() != font.get("license_sha256"):
        raise RuntimeError("Packaged font license SHA-256 does not match rights manifest")
    return {
        "full_decode": "passed",
        "zip_crc": "passed",
        "manifest_members": "passed",
        "member_sha256": "passed",
        "package_sha256": package_sha,
        "duration_seconds": manifest["duration_seconds"],
        "ready_to_publish": manifest["ready_to_publish"],
        "commercial_use_allowed": rights["commercial_use_allowed"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a Muse kinetic explainer delivery package.")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.directory), ensure_ascii=False, indent=2))
