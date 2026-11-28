"""Artifact validation and Publish-Ready Bundles (Workflow C), stopping at the human gate.

* ``validate_artifact`` checks real container/structure, not just existence: non-empty,
  magic bytes (WAV/MP4/PNG), parseable JSON/SRT/HTML/Markdown, WAV duration > 0.
* ``build_publish_bundle`` writes a manifest (sha256 per file, validation verdicts, cost vs
  exposure estimates vs *confirmed* revenue kept in separate fields) with
  ``publish_allowed = False`` and status ``awaiting_human_publish_approval``.
* ``approve_bundle_for_manual_publish`` only records a human decision. Muse never uploads,
  posts or charges anything itself.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import wave
from pathlib import Path
from typing import Any, Iterable

HUMAN_PREFIXES = ("user:", "human:")
_SRT_CUE = re.compile(r"^\d+\s*\r?\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", re.M)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve()
    result: dict[str, Any] = {"path": str(p), "ok": False, "kind": p.suffix.lower().lstrip("."), "checks": []}
    if not p.is_file():
        result["checks"].append("missing")
        return result
    size = p.stat().st_size
    result["size"] = size
    if size == 0:
        result["checks"].append("zero_bytes")
        return result
    head = p.read_bytes()[:64]
    kind = result["kind"]
    try:
        if kind == "wav":
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                result["checks"].append("bad_riff_header")
                return result
            with wave.open(str(p), "rb") as wav:
                frames, rate = wav.getnframes(), wav.getframerate()
                result["duration_seconds"] = round(frames / float(rate), 3) if rate else 0.0
                result["channels"] = wav.getnchannels()
            if result["duration_seconds"] <= 0:
                result["checks"].append("zero_duration")
                return result
        elif kind in ("mp4", "mov", "m4a"):
            if head[4:8] != b"ftyp":
                result["checks"].append("missing_ftyp_box")
                return result
        elif kind == "png":
            if head[:8] != b"\x89PNG\r\n\x1a\n":
                result["checks"].append("bad_png_signature")
                return result
        elif kind in ("jpg", "jpeg"):
            if head[:3] != b"\xff\xd8\xff":
                result["checks"].append("bad_jpeg_signature")
                return result
        elif kind == "json":
            json.loads(p.read_text(encoding="utf-8"))
        elif kind == "srt":
            if not _SRT_CUE.search(p.read_text(encoding="utf-8-sig")):
                result["checks"].append("no_srt_cue")
                return result
        elif kind in ("html", "htm"):
            text = p.read_text(encoding="utf-8").casefold()
            if "<html" not in text or "</html>" not in text:
                result["checks"].append("html_not_closed")
                return result
        elif kind in ("md", "txt"):
            text = p.read_text(encoding="utf-8")
            if not text.strip() or "\ufffd" in text:
                result["checks"].append("empty_or_mojibake")
                return result
    except (UnicodeDecodeError, json.JSONDecodeError, wave.Error, EOFError) as exc:
        result["checks"].append(f"parse_error:{type(exc).__name__}")
        return result
    result["ok"] = True
    result["sha256"] = sha256_file(p)
    return result


def build_publish_bundle(out_dir: str | Path, artifacts: Iterable[str | Path], *, title: str,
                         channel: str, estimated_cost_usd: str = "0.00",
                         exposure_estimates: dict[str, Any] | None = None) -> dict[str, Any]:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    validations = [validate_artifact(a) for a in artifacts]
    manifest = {
        "schema": "muse.publish_ready_bundle.v1", "title": title, "channel": channel,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "awaiting_human_publish_approval", "publish_allowed": False,
        "artifacts": validations, "all_artifacts_valid": bool(validations) and all(v["ok"] for v in validations),
        "economics": {"estimated_cost_usd": str(estimated_cost_usd),
                      "exposure_estimates_not_revenue": exposure_estimates or {},
                      "confirmed_net_revenue_usd": "0.00",
                      "note": "曝光/播放量是预估指标，不是收入；只有经验证的结算回执才计入净收益。"},
        "human_checklist": ["核对版权与素材授权", "核对字幕与事实陈述", "确认发布平台与账号", "确认费用与收益口径"],
    }
    path = out / "publish_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "PUBLISH_CHECKLIST.md").write_text(
        "# 发布准备包（停在人工发布门前）\n\n" + "\n".join(f"- [ ] {item}" for item in manifest["human_checklist"]) +
        "\n\n> Muse 不会自动上传、发帖或扣费。\n", encoding="utf-8")
    manifest["manifest_path"] = str(path)
    return manifest


def approve_bundle_for_manual_publish(bundle_dir: str | Path, *, approver: str) -> dict[str, Any]:
    if not isinstance(approver, str) or not approver.startswith(HUMAN_PREFIXES):
        raise PermissionError("publishing requires a human approver (user:/human:)")
    path = Path(bundle_dir).resolve() / "publish_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("all_artifacts_valid"):
        raise ValueError("cannot approve a bundle with invalid artifacts")
    manifest.update({"status": "approved_for_manual_publish", "approved_by": approver,
                     "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                     "publish_allowed": False, "publish_mode": "manual_by_human_only"})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


VIDEO_PACKAGE_FILES = ("video.mp4", "subtitles.srt", "cover.jpg")


def bundle_from_video_package(package_dir: str | Path, out_dir: str | Path, *, title: str,
                              channel: str = "manual_upload", estimated_cost_usd: str = "0.00") -> dict[str, Any]:
    """Bridge a video_publish_package directory into a human-gated Publish-Ready Bundle.

    Required: ``video.mp4`` (ftyp box) and ``subtitles.srt`` (≥1 cue); optional ``cover.jpg``.
    The existing package and its own review checklist are left untouched; missing required
    files make the bundle invalid instead of silently skipping them.
    """
    package = Path(package_dir).resolve()
    if not package.is_dir():
        raise FileNotFoundError(f"video package directory not found: {package}")
    present = [package / name for name in VIDEO_PACKAGE_FILES if (package / name).exists()]
    missing = [name for name in ("video.mp4", "subtitles.srt") if not (package / name).exists()]
    bundle = build_publish_bundle(out_dir, present or [package / "video.mp4"], title=title, channel=channel,
                                  estimated_cost_usd=estimated_cost_usd)
    if missing:
        bundle["all_artifacts_valid"] = False
        bundle["missing_required"] = missing
        Path(bundle["manifest_path"]).write_text(json.dumps({k: v for k, v in bundle.items() if k != "manifest_path"},
                                                            ensure_ascii=False, indent=2), encoding="utf-8")
    bundle["source_video_package"] = str(package)
    return bundle
