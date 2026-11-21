from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from muse.video_human_review import VisualReviewEvidenceError
from muse.video_product_builder import VideoProductBuilder


EVIDENCE_DIRNAME = "visual-review-evidence-v1"
EVIDENCE_TYPE = "visual-review-evidence-v1"


class VisualEvidencePackError(VisualReviewEvidenceError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, *, limit: int = 1024 * 1024) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise VisualEvidencePackError("visual evidence metadata path is missing, unsafe or oversized")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualEvidencePackError("visual evidence metadata is invalid") from exc
    if not isinstance(value, dict):
        raise VisualEvidencePackError("visual evidence metadata must be a JSON object")
    return value


def _png_is_valid(path: Path) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 24 or path.stat().st_size > 10 * 1024 * 1024:
        return False
    data = path.read_bytes()
    return (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        and data[12:16] == b"IHDR"
        and int.from_bytes(data[16:20], "big") > 0
        and int.from_bytes(data[20:24], "big") > 0
    )


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    if not _png_is_valid(path):
        return None
    data = path.read_bytes()
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def verify_visual_review_evidence_pack(
    product_dir: str | Path, initiative_id: str, version_id: str, video_path: str | Path,
) -> dict[str, Any]:
    product_lexical = Path(product_dir)
    if product_lexical.is_symlink() or product_lexical.parent.is_symlink() or product_lexical.parent.parent.is_symlink():
        raise VisualEvidencePackError("video product path is unsafe")
    root = product_lexical.resolve(strict=True)
    evidence_dir = root / EVIDENCE_DIRNAME
    manifest_path = evidence_dir / "manifest.json"
    sheet_path = evidence_dir / "contact-sheet.png"
    product_manifest_path = root / "manifest.json"
    qa_path = root / "qa-report.json"
    video_lexical = Path(video_path)
    for path in (evidence_dir, manifest_path, sheet_path, product_manifest_path, qa_path, video_lexical):
        if path.is_symlink():
            raise VisualEvidencePackError("visual review evidence path is unsafe")
    if not evidence_dir.is_dir() or evidence_dir.resolve().parent != root:
        raise FileNotFoundError("visual review evidence pack is required")
    manifest = _json_object(manifest_path)
    product_manifest = _json_object(product_manifest_path)
    qa_report = _json_object(qa_path)
    product_files = product_manifest.get("files")
    product_quality_gate = product_manifest.get("quality_gate")
    if not isinstance(product_files, dict) or not isinstance(product_quality_gate, dict):
        raise VisualEvidencePackError("source product manifest is incomplete")
    video = video_lexical.resolve(strict=True)
    if not video.is_file() or video.resolve().parent != root:
        raise VisualEvidencePackError("source video path is unsafe")
    video_hash = _sha256(video)
    sheet_hash = _sha256(sheet_path) if sheet_path.is_file() else None
    sheet_dimensions = _png_dimensions(sheet_path)
    contact_sheet = manifest.get("contact_sheet")
    product_manifest_hash = _sha256(product_manifest_path)
    qa_hash = _sha256(qa_path)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("evidence_type") != EVIDENCE_TYPE
        or manifest.get("initiative_id") != initiative_id
        or manifest.get("version_id") != version_id
        or manifest.get("review_state") != "human_review_required"
        or manifest.get("can_publish") is not False
        or manifest.get("source_product_manifest_sha256") != product_manifest_hash
        or manifest.get("source_video_sha256") != video_hash
        or manifest.get("source_qa_report_sha256") != qa_hash
        or product_manifest.get("product_kind") != "video_creator"
        or product_manifest.get("initiative_id") != initiative_id
        or product_manifest.get("version_id") != version_id
        or product_manifest.get("status") != "testing"
        or product_files.get("video.mp4") != video_hash
        or product_files.get("qa-report.json") != qa_hash
        or product_quality_gate.get("status") != "passed"
        or product_quality_gate.get("passed") is not True
        or product_quality_gate.get("ready_to_publish") is not False
        or qa_report.get("schema_version") != 1
        or qa_report.get("status") != "passed"
        or qa_report.get("path") != "video.mp4"
        or qa_report.get("artifact_sha256") != video_hash
        or not isinstance(sheet_hash, str)
        or manifest.get("files") != {"contact-sheet.png": sheet_hash}
        or sheet_dimensions is None
        or not isinstance(contact_sheet, dict)
        or contact_sheet.get("image_decode") != "passed"
        or contact_sheet.get("width") != sheet_dimensions[0]
        or contact_sheet.get("height") != sheet_dimensions[1]
    ):
        raise VisualEvidencePackError("visual review evidence is missing or hash-mismatched")
    return {
        "evidence_type": EVIDENCE_TYPE,
        "video_sha256": video_hash,
        "artifact_sha256": {"video.mp4": video_hash, "contact-sheet.png": sheet_hash},
        "contact_sheet_path": str(sheet_path),
        "release_manifest_sha256": _sha256(manifest_path),
    }


def create_visual_review_evidence_pack(
    products_root: str | Path,
    initiative_id: str,
    version_id: str,
    *,
    contact_sheet_factory: Callable[..., dict[str, Any]] | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
) -> dict[str, Any]:
    """Create a separate, draft-only visual evidence pack after product technical QA passes."""
    root_lexical = Path(products_root)
    if root_lexical.is_symlink() or root_lexical.parent.is_symlink():
        raise VisualEvidencePackError("video product root is unsafe")
    root = root_lexical.resolve(strict=True)
    product_dir = root / initiative_id / version_id
    if root.is_symlink() or (root / initiative_id).is_symlink() or product_dir.is_symlink():
        raise VisualEvidencePackError("video product path is unsafe")
    verified = VideoProductBuilder.verify_path(root, initiative_id, version_id)
    quality_gate = verified.get("quality_gate") if isinstance(verified, dict) else None
    if not isinstance(quality_gate, dict) or quality_gate.get("status") != "passed" or quality_gate.get("passed") is not True:
        return {"status": "blocked", "reason": "product_technical_qa_not_passed",
                "can_publish": False, "human_review_required": True}
    product_manifest_path = product_dir / "manifest.json"
    qa_path = product_dir / "qa-report.json"
    video_path = product_dir / "video.mp4"
    product_manifest = _json_object(product_manifest_path)
    qa_report = _json_object(qa_path)
    product_files = product_manifest.get("files")
    product_quality_gate = product_manifest.get("quality_gate")
    if not isinstance(product_files, dict) or not isinstance(product_quality_gate, dict):
        return {"status": "blocked", "reason": "product_manifest_incomplete",
                "can_publish": False, "human_review_required": True}
    if (
        product_manifest.get("product_kind") != "video_creator"
        or product_manifest.get("initiative_id") != initiative_id
        or product_manifest.get("version_id") != version_id
        or product_manifest.get("status") != "testing"
        or qa_report.get("status") != "passed"
        or qa_report.get("path") != "video.mp4"
        or product_quality_gate.get("status") != "passed"
        or product_quality_gate.get("passed") is not True
        or product_quality_gate.get("ready_to_publish") is not False
        or product_files.get("qa-report.json") != _sha256(qa_path)
        or product_files.get("video.mp4") != _sha256(video_path)
        or qa_report.get("artifact_sha256") != _sha256(video_path)
    ):
        return {"status": "blocked", "reason": "product_qa_evidence_mismatch",
                "can_publish": False, "human_review_required": True}
    evidence_dir = product_dir / EVIDENCE_DIRNAME
    if evidence_dir.is_symlink():
        raise VisualEvidencePackError("visual evidence directory cannot be a symlink")
    if evidence_dir.exists():
        evidence = verify_visual_review_evidence_pack(product_dir, initiative_id, version_id, video_path)
        return {"status": "available", **evidence, "can_publish": False,
                "human_review_required": True, "idempotent": True}
    evidence_dir.mkdir()
    try:
        sheet_path = evidence_dir / "contact-sheet.png"
        if contact_sheet_factory is None:
            from muse.video_contact_sheet import create_contact_sheet
            contact_sheet_factory = create_contact_sheet
        try:
            contact_result = contact_sheet_factory(
                video_path, sheet_path, ffmpeg_path=ffmpeg_path or "ffmpeg",
                ffprobe_path=ffprobe_path,
            )
        except Exception as exc:
            shutil.rmtree(evidence_dir, ignore_errors=True)
            return {"status": "unavailable", "reason": str(exc)[:240],
                    "can_publish": False, "human_review_required": True}
        dimensions = _png_dimensions(sheet_path)
        if (not isinstance(contact_result, dict) or contact_result.get("status") != "completed"
                or contact_result.get("image_decode") != "passed" or dimensions is None
                or contact_result.get("width") != dimensions[0]
                or contact_result.get("height") != dimensions[1]):
            shutil.rmtree(evidence_dir, ignore_errors=True)
            return {"status": "unavailable", "reason": "contact_sheet_generation_or_decode_failed",
                    "can_publish": False, "human_review_required": True}
        manifest = {
            "schema_version": 1, "evidence_type": EVIDENCE_TYPE,
            "initiative_id": initiative_id, "version_id": version_id,
            "review_state": "human_review_required", "can_publish": False,
            "source_product_manifest_sha256": _sha256(product_manifest_path),
            "source_video_sha256": _sha256(video_path),
            "source_qa_report_sha256": _sha256(qa_path),
            "files": {"contact-sheet.png": _sha256(sheet_path)},
            "contact_sheet": {
                "width": contact_result.get("width"),
                "height": contact_result.get("height"),
                "image_decode": contact_result.get("image_decode"),
            },
        }
        temporary = evidence_dir / ".manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, evidence_dir / "manifest.json")
        evidence = verify_visual_review_evidence_pack(product_dir, initiative_id, version_id, video_path)
        return {"status": "available", **evidence, "can_publish": False,
                "human_review_required": True, "idempotent": False}
    except Exception:
        shutil.rmtree(evidence_dir, ignore_errors=True)
        raise
