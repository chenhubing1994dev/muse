from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PAGES = {"index.html", "showcase.html", "products.html", "portfolio.html"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_site_preview(site_root: Path) -> dict:
    root = site_root.resolve(strict=True)
    outputs = (ROOT / "outputs").resolve(strict=True)
    if root == outputs or outputs not in root.parents or root.is_symlink():
        raise ValueError("site root must be a real child directory inside outputs/")

    manifest_path = root / "site-manifest.json"
    archive_path = root / "site-preview.zip"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("product") != "muse-public-site-preview":
        raise RuntimeError("unexpected site manifest product")
    if set(manifest.get("pages", [])) != EXPECTED_PAGES:
        raise RuntimeError("site manifest page set mismatch")
    if manifest.get("status") != "preview_only":
        raise RuntimeError("site is not marked preview_only")
    for field in ("public_deployment_allowed", "crawl_allowed", "contact_channel_configured", "payments_enabled"):
        if manifest.get(field) is not False:
            raise RuntimeError(f"site release gate must remain false: {field}")
    if manifest.get("commercial_rights_review_required") is not True:
        raise RuntimeError("commercial rights review gate is missing")

    assets = manifest.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError("site manifest assets must be a non-empty object")
    expected_members = set(assets) | {"site-manifest.json"}
    for relative, expected_digest in assets.items():
        path = (root / relative).resolve(strict=True)
        if root not in path.parents or path.is_symlink() or not path.is_file():
            raise RuntimeError(f"manifest asset is not a regular file inside the site root: {relative}")
        if sha256(path) != expected_digest:
            raise RuntimeError(f"site asset SHA-256 mismatch: {relative}")

    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("site ZIP CRC check failed")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("site ZIP contains duplicate member names")
        if set(names) != expected_members:
            raise RuntimeError("site ZIP member set differs from the site manifest")
        embedded = json.loads(archive.read("site-manifest.json"))
        if embedded != manifest:
            raise RuntimeError("embedded site manifest differs from the external manifest")
        for relative, expected_digest in assets.items():
            if hashlib.sha256(archive.read(relative)).hexdigest() != expected_digest:
                raise RuntimeError(f"site ZIP asset SHA-256 mismatch: {relative}")

    robots = (root / "robots.txt").read_text(encoding="utf-8")
    if "Disallow: /" not in robots:
        raise RuntimeError("preview crawl block is missing")
    commerce = (root / "commerce.js").read_text(encoding="utf-8")
    if "/api/leads" not in commerce or "fetch(" not in commerce:
        raise RuntimeError("consented local lead submission path is missing")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for required in ("loopback-only", "90-day retention", "No analytics", "Payments are not enabled"):
        if required not in readme:
            raise RuntimeError(f"preview README disclosure is missing: {required}")

    return {
        "status": "passed",
        "site_root": root.relative_to(ROOT).as_posix(),
        "pages": sorted(EXPECTED_PAGES),
        "assets_verified": len(assets),
        "zip_crc": "passed",
        "zip_sha256": sha256(archive_path),
        "lead_intake": "local consented POST path present; runtime E2E must be checked separately",
        "public_deployment_allowed": False,
        "payments_enabled": False,
        "commercial_rights_review_required": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a Muse static-site preview without opening release gates.")
    parser.add_argument("--site-root", default="outputs/site_launch_preview_20261125_v2")
    args = parser.parse_args()
    result = verify_site_preview(ROOT / args.site_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
