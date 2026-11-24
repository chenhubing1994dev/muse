"""Render a rights-aware first Muse creator pilot in an isolated workspace.

The production databases remain untouched: all temporary Initiative/Research/Progress
stores are created under the isolated sandbox. Existing locally generated Wan shots
and pinned Cantonese TTS/ASR assets are hard-linked read-only where possible.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SANDBOX = ROOT / ".muse-video-pilot-workspace-20261124"
FINAL = ROOT / "outputs" / "creator_pilot_20261124"
TITLE = "AI講得好肯定，就一定係真？三步查證法"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hardlink_or_copy(source: str, destination: str) -> str:
    src, dst = Path(source), Path(destination)
    if src.is_symlink():
        raise RuntimeError(f"refusing symlinked production input: {src}")
    try:
        os.link(src, dst)
        return str(dst)
    except OSError:
        shutil.copy2(src, dst)
        return str(dst)


def clone_tree(relative_source: str, relative_target: str) -> None:
    source = ROOT / relative_source
    target = SANDBOX / relative_target
    if not source.is_dir() or source.is_symlink():
        raise FileNotFoundError(f"required source directory missing or unsafe: {relative_source}")
    for candidate in source.rglob("*"):
        if candidate.is_symlink() and ".cache" not in candidate.parts and "__pycache__" not in candidate.parts:
            raise RuntimeError(f"refusing symlinked input tree entry: {candidate}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source, target, copy_function=hardlink_or_copy, symlinks=False,
        ignore=shutil.ignore_patterns(".cache", "__pycache__", "*.pyc"),
    )


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if SANDBOX.exists():
        raise FileExistsError(f"refusing to reuse existing isolated workspace: {SANDBOX}")
    if FINAL.exists():
        raise FileExistsError(f"refusing to overwrite existing deliverable: {FINAL}")

    SANDBOX.mkdir(parents=True, exist_ok=False)
    required = [
        ("data/video_sources/muse-cosmos-film", "data/video_sources/muse-cosmos-film"),
        ("data/products/59148fb9f5299ad0292157ed38194996/f0140e0b32facf564f95",
         "data/products/59148fb9f5299ad0292157ed38194996/f0140e0b32facf564f95"),
        ("data/models/canto-tts-nano", "data/models/canto-tts-nano"),
        ("models/whisper-small-cantonese", "models/whisper-small-cantonese"),
        (".muse-canto-tts-py314", ".muse-canto-tts-py314"),
    ]
    for source, target in required:
        clone_tree(source, target)

    source_script = ROOT / "scripts" / "muse-high-value-short.py"
    spec = importlib.util.spec_from_file_location("muse_high_value_short_pilot", source_script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the existing Muse high-value short builder")
    builder_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder_module)

    render_root = SANDBOX / "outputs"
    render_root.mkdir(parents=True, exist_ok=True)
    render_dir = render_root / "creator_pilot_20261124"
    fallback_reason = None
    try:
        result = builder_module.build(
            SANDBOX, render_dir, title=TITLE, recipe_id="cantonese_pilot_v2"
        )
    except Exception as exc:
        fallback_reason = {
            "attempt": "local_cantonese_tts_asr",
            "error_type": type(exc).__name__,
            "message": str(exc)[:1200],
            "note": "No external TTS service was substituted; retrying with original captions and soundtrack only.",
        }
        render_dir = render_root / "creator_pilot_20261124_no_voice"
        result = builder_module.build(
            SANDBOX, render_dir, title=TITLE, recipe_id="high_value_short_v2"
        )

    mp4_info = result.get("mp4") if isinstance(result, dict) else None
    if result.get("status") != "completed" or not isinstance(mp4_info, dict):
        raise RuntimeError("existing Muse builder did not return a completed MP4 product")
    rendered_mp4 = SANDBOX / str(mp4_info.get("path", ""))
    if not rendered_mp4.is_file() or rendered_mp4.is_symlink():
        raise FileNotFoundError("rendered MP4 is missing or unsafe")
    if sha256(rendered_mp4) != mp4_info.get("sha256") or mp4_info.get("full_decode") != "passed":
        raise RuntimeError("rendered MP4 failed hash/full-decode acceptance")

    bundle_path = SANDBOX / str(result["bundle"]["path"])
    if not bundle_path.is_file():
        raise FileNotFoundError("delivery bundle is missing")
    with zipfile.ZipFile(bundle_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("delivery bundle failed ZIP CRC validation")

    FINAL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(render_dir, FINAL, copy_function=shutil.copy2)

    rights_assets = []
    for index in range(1, 7):
        source_file = ROOT / "data" / "video_sources" / "muse-cosmos-film" / f"shot-{index:02d}.mp4"
        if not source_file.is_file():
            raise FileNotFoundError(f"source shot missing: {source_file.name}")
        rights_assets.append({
            "asset_id": f"muse-wan-shot-{index:02d}",
            "source_file": f"data/video_sources/muse-cosmos-film/{source_file.name}",
            "sha256": sha256(source_file),
            "origin": "locally generated Muse Wan 2.1 T2V source shot",
            "third_party_stock_media": False,
            "prompt_provenance": "partial; only retained source metadata is asserted",
            "commercial_use_allowed": False,
            "license_review_status": "pending_model_and_output_rights_review",
            "human_review_required": True,
        })

    voice = result.get("voiceover", {"status": "not_used"})
    cost_ledger = {
        "currency": "USD",
        "paid_api_cost": 0,
        "paid_stock_asset_cost": 0,
        "cloud_compute_cost": 0,
        "local_hardware": {
            "video_encoder": mp4_info.get("encoder", "unknown"),
            "ai_video_generation_reused_existing_shots": True,
            "new_wan_gpu_generation": False,
            "gpu_energy_cost": "not_metered",
            "cpu_energy_cost": "not_metered",
        },
        "voiceover": {
            "status": voice.get("status", "not_used"),
            "engine": voice.get("engine"),
            "license_review_status": "pending",
        },
        "settled_revenue": 0,
        "revenue_status": "no_sales_or_settlements_recorded",
        "note": "Zero paid-service spend in this run; local electricity and labor are not included.",
    }
    platform_copy = f"""# 发布草稿（尚未发布）

## YouTube Shorts
**标题**：{TITLE}

**简介**：
AI 回答得流畅、有自信，不代表每个细节都有证据。记住三步：拆开主张、回到原始来源、主动寻找反证。找不到可靠来源，就清楚标记「未证实」。

本片依据 NIST AI 600-1（2024）关于生成式 AI confabulation 与核验来源/引用的风险管理建议；三步流程属于实用编辑建议，并非 NIST 对准确率的统计结论。

**标签**：#AI #人工智能 #事实核查 #AIAgent #数字素养

## Bilibili / 微信视频号
**标题**：AI讲得好肯定，就一定是真？记住这三步

**简介**：
别把流畅表达当成事实证明。拆主张、查原文、找反证。关注我，继续用简单方法看懂 AI、核验信息。

**置顶评论**：你平时会先看 AI 的答案，还是先找它的来源？留言讲讲。

**发布状态**：仅生成文案草稿；未登录账号、未调用发布 API、未上传。
**AI 内容披露**：画面含本地 AI 生成镜头，旁白（如已生成）为本地合成语音；按平台当期规则如实标记。
**人工检查**：请先完整观看、试听并确认字幕/旁白，再核对平台 AI 内容披露要求。
"""
    rights_manifest = {
        "schema": "muse.creator.rights-manifest.v1",
        "status": "review_required",
        "assets": rights_assets,
        "background_audio": {
            "origin": "algorithmically generated original ambient bed in existing Muse script",
            "third_party_music": False,
            "commercial_use_allowed": False,
            "license_review_status": "pending",
        },
        "voiceover": {
            "engine": voice.get("engine"),
            "origin": "local model output" if voice.get("status") not in (None, "not_used") else "not_used",
            "commercial_use_allowed": False,
            "license_review_status": "pending_model_terms_and_voice_review",
        },
        "publishable": False,
        "human_review_required": True,
    }
    manifest = {
        "schema": "muse.creator.pilot.v1",
        "status": "completed_draft_pending_human_review",
        "title": TITLE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video": {
            "file": "ai-verification-three-steps-9x16.mp4",
            "sha256": mp4_info["sha256"],
            "bytes": mp4_info["bytes"],
            "duration_seconds": mp4_info["duration_seconds"],
            "width": mp4_info["width"],
            "height": mp4_info["height"],
            "fps": mp4_info["fps"],
            "encoder": mp4_info.get("encoder"),
            "full_decode": "passed",
        },
        "voiceover": voice,
        "subtitle_review": result.get("subtitle_review"),
        "source": "NIST AI 600-1, published 2024-07-26, §2.2 and MS-2.5-003",
        "production_workspace": "isolated temporary Muse workspace; production databases untouched",
        "fallback_reason": fallback_reason,
        "publish_api_called": False,
        "auto_publish": False,
        "commercial_use_allowed": False,
    }
    write_json(FINAL / "production-manifest.json", manifest)
    write_json(FINAL / "rights-manifest.json", rights_manifest)
    write_json(FINAL / "cost-ledger.json", cost_ledger)
    (FINAL / "platform-copy.md").write_text(platform_copy, encoding="utf-8")

    final_mp4 = FINAL / "ai-verification-three-steps-9x16.mp4"
    if not final_mp4.is_file():
        # The original builder writes a stable MP4 basename; keep exact observed file if it differs.
        candidates = sorted(FINAL.glob("*.mp4"))
        if len(candidates) != 1:
            raise FileNotFoundError("could not identify one final MP4 in deliverable directory")
        final_mp4 = candidates[0]
    output = {
        "status": "completed",
        "deliverable_dir": str(FINAL.relative_to(ROOT)).replace("\\", "/"),
        "video_file": str(final_mp4.relative_to(ROOT)).replace("\\", "/"),
        "video_bytes": final_mp4.stat().st_size,
        "video_sha256": sha256(final_mp4),
        "duration_seconds": mp4_info["duration_seconds"],
        "voiceover": voice,
        "fallback_reason": fallback_reason,
        "rights_review_required": True,
        "commercial_use_allowed": False,
        "publish_api_called": False,
        "cost_ledger": "cost-ledger.json",
        "delivery_bundle": "delivery-bundle.zip",
        "sandbox": str(SANDBOX.relative_to(ROOT)).replace("\\", "/"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
