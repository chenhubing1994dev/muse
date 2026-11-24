"""Package the digital checklist kit and validate the first Muse creator handoff."""
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "creator_pilot_20261124"
KIT = OUT / "digital-product"
ZIP = OUT / "AI-Verification-Kit-v0.1.zip"
MANIFEST = OUT / "AI-Verification-Kit-v0.1.manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if ZIP.exists() or MANIFEST.exists():
        raise FileExistsError("refusing to overwrite existing digital-product bundle")
    if not KIT.is_dir():
        raise FileNotFoundError(KIT)

    generation = json.loads((OUT / "generation.json").read_text(encoding="utf-8"))
    production = json.loads((OUT / "production-manifest.json").read_text(encoding="utf-8"))
    voice = json.loads((OUT / "voiceover-preview-manifest.json").read_text(encoding="utf-8"))
    rights = json.loads((OUT / "rights-manifest.json").read_text(encoding="utf-8"))
    cost = json.loads((OUT / "cost-ledger.json").read_text(encoding="utf-8"))
    kit_manifest = json.loads((KIT / "product-manifest.json").read_text(encoding="utf-8"))

    mp4 = ROOT / generation["mp4"]["path"]
    cover = ROOT / generation["cover"]["path"]
    delivery_bundle = ROOT / generation["bundle"]["path"]
    product_root = ROOT / generation["product"]["relative_path"]
    publish_package = ROOT / generation["publish_package"]["package_path"]
    preview_mp4 = OUT / voice["video_file"]
    if not all(p.is_file() for p in (mp4, cover, delivery_bundle, publish_package, preview_mp4)):
        raise FileNotFoundError("one or more generation.json paths do not resolve to a real artifact")
    if sha256(mp4) != generation["mp4"]["sha256"]:
        raise ValueError("base MP4 hash mismatch")
    if sha256(preview_mp4) != voice["video_sha256"]:
        raise ValueError("voice preview MP4 hash mismatch")
    if voice["asr"]["status"] != "passed" or float(voice["asr"]["cer"]) > 0.10:
        raise ValueError("voice preview did not pass the pinned ASR threshold")
    if production["publish_api_called"] or production["auto_publish"] or production["commercial_use_allowed"]:
        raise ValueError("base product crossed a publication or commercial-use gate")
    if voice["publish_api_called"] or voice["auto_publish"] or voice["commercial_use_allowed"]:
        raise ValueError("voice preview crossed a publication or commercial-use gate")
    if rights["publishable"] or any(asset["commercial_use_allowed"] for asset in rights["assets"]):
        raise ValueError("unreviewed source assets must remain non-commercial")
    if cost["settled_revenue"] != 0 or kit_manifest["settled_revenue"] != 0:
        raise ValueError("no settlement has been recorded in this pilot")

    claim_csv = KIT / "templates" / "claim-register.csv"
    with claim_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        claim_headers = next(csv.reader(stream))
    required_headers = {"claim_id", "exact_claim", "source_url", "counter_evidence_url", "status"}
    if not required_headers.issubset(set(claim_headers)):
        raise ValueError("claim register missing required columns")

    files = sorted(p for p in KIT.rglob("*") if p.is_file())
    if not files:
        raise RuntimeError("digital product pack is empty")
    with zipfile.ZipFile(ZIP, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(KIT).as_posix())
    with zipfile.ZipFile(ZIP, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"digital product ZIP CRC failure: {bad_member}")
        entries = archive.namelist()

    manifest = {
        "schema": "muse.digital-product-package.v1",
        "status": "draft_not_published",
        "file": ZIP.name,
        "sha256": sha256(ZIP),
        "bytes": ZIP.stat().st_size,
        "entry_count": len(entries),
        "entries": entries,
        "price_hypothesis_usd": kit_manifest["price_hypothesis"]["amount"],
        "price_validated": False,
        "sales_channel": kit_manifest["sales_channel"],
        "published": False,
        "orders": 0,
        "settled_revenue": 0,
        "commercial_use_allowed": False,
        "validation": {
            "generation_json_valid": True,
            "base_video_hash": "passed",
            "voice_preview_hash": "passed",
            "voice_asr_cer": voice["asr"]["cer"],
            "rights_gate": "preserved_blocked_pending_review",
            "publish_gate": "preserved_draft_only",
            "digital_product_zip_crc": "passed",
            "production_database": "isolated sandbox only",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "validated_draft_bundle",
        "video": str(mp4.relative_to(ROOT)).replace("\\", "/"),
        "voice_preview": str(preview_mp4.relative_to(ROOT)).replace("\\", "/"),
        "digital_product_zip": str(ZIP.relative_to(ROOT)).replace("\\", "/"),
        "digital_product_bytes": ZIP.stat().st_size,
        "digital_product_files": len(entries),
        "digital_product_sha256": manifest["sha256"],
        "voice_asr_cer": voice["asr"]["cer"],
        "publishable": False,
        "commercial_use_allowed": False,
        "settled_revenue": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
