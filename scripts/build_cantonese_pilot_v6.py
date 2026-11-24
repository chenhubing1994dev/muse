"""Package the existing ASR-passing Cantonese voice preview as v6.

Local review deliverable only. It does not publish, spend, or change source assets.
It embeds the corrected SRT as a soft subtitle track and creates a 16:9 derivative.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "outputs" / "creator_pilot_20261124"
INPUT_VIDEO = SOURCE_DIR / "ai-verification-three-steps-9x16-voice-review-only.mp4"
INPUT_AUDIO = SOURCE_DIR / "voiceover-preview.wav"
INPUT_SRT = SOURCE_DIR / "voiceover-preview-edited.srt"
VOICE_MANIFEST = SOURCE_DIR / "voiceover-preview-manifest.json"
VOICE_REVIEW = SOURCE_DIR / "voiceover-preview-subtitle-review.json"
VOICE_RIGHTS = SOURCE_DIR / "voiceover-preview-rights-addendum.json"
RIGHTS = SOURCE_DIR / "rights-manifest.json"
COSTS = SOURCE_DIR / "cost-ledger.json"
SOURCE_NOTES = SOURCE_DIR / "source-notes.json"
STORYBOARD = SOURCE_DIR / "storyboard.json"
PLATFORM_COPY = SOURCE_DIR / "platform-copy.md"
V2_DIR = ROOT / "data" / "monetization" / "first-cantonese-pilot-2026-11-24-v2"
OUT = ROOT / "data" / "monetization" / "first-cantonese-pilot-2026-11-24-v6"
HANDOFF = SOURCE_DIR / "canonical-pilot-handoff-v6.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], *, timeout: int = 900) -> None:
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2500:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command[:10])}\n{detail}")


def probe(path: Path, ffprobe: str) -> dict[str, Any]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed for {path.name}: {(result.stderr or result.stdout)[-1200:]}")
    return json.loads(result.stdout)


def main() -> int:
    if OUT.exists() or OUT.is_symlink() or HANDOFF.exists() or HANDOFF.is_symlink():
        raise FileExistsError("v6 output or handoff already exists; refusing to overwrite")

    required = (
        INPUT_VIDEO, INPUT_AUDIO, INPUT_SRT, VOICE_MANIFEST, VOICE_REVIEW,
        VOICE_RIGHTS, RIGHTS, COSTS, SOURCE_NOTES, STORYBOARD, PLATFORM_COPY,
        V2_DIR / "release-checklist.json", V2_DIR / "platform-drafts.json",
    )
    for path in required:
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"required source missing or unsafe: {path}")

    voice = read_json(VOICE_MANIFEST)
    review = read_json(VOICE_REVIEW)
    if voice.get("status") != "review_only" or voice.get("publishable") is not False:
        raise RuntimeError("voice preview is not a review-only source")
    if voice.get("asr", {}).get("status") != "passed":
        raise RuntimeError("standalone Cantonese ASR gate did not pass")
    cer = float(voice["asr"]["cer"])
    if cer > 0.10 or float(review.get("asr_cer", 1.0)) > 0.10:
        raise RuntimeError("standalone Cantonese ASR exceeds the unchanged 10% threshold")
    if sha256(INPUT_VIDEO) != voice.get("video_sha256"):
        raise RuntimeError("voice-preview video SHA-256 does not match its manifest")
    if sha256(INPUT_AUDIO) != voice.get("audio_sha256"):
        raise RuntimeError("voice-preview audio SHA-256 does not match its manifest")

    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion

    tools = {item.tool_id: item.executable for item in discover_media_tools().tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg/FFprobe unavailable")

    encoder_listing = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    ).stdout
    encoder = "h264_amf" if "h264_amf" in encoder_listing else "libx264"

    OUT.mkdir(parents=True)
    copy_map = {
        "voiceover-preview.wav": INPUT_AUDIO,
        "voiceover-preview-edited.srt": INPUT_SRT,
        "voiceover-preview.srt": SOURCE_DIR / "voiceover-preview.srt",
        "voiceover-preview-manifest.json": VOICE_MANIFEST,
        "voiceover-preview-subtitle-review.json": VOICE_REVIEW,
        "voiceover-preview-rights-addendum.json": VOICE_RIGHTS,
        "rights-manifest.json": RIGHTS,
        "cost-ledger.json": COSTS,
        "source-notes.json": SOURCE_NOTES,
        "storyboard.json": STORYBOARD,
        "platform-copy.md": PLATFORM_COPY,
        "platform-drafts.json": V2_DIR / "platform-drafts.json",
        "release-checklist.json": V2_DIR / "release-checklist.json",
        "cover.jpg": SOURCE_DIR / "cover.jpg",
    }
    for destination, source in copy_map.items():
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, OUT / destination)

    release = read_json(OUT / "release-checklist.json")
    release.update({
        "release_state": "draft_only",
        "can_publish": False,
        "human_editorial_review": "pending",
        "human_voice_listening_review": "pending",
        "human_subtitle_review": "pending",
        "visual_and_model_rights_review": "pending",
        "source_and_fact_review": "pending",
        "account_authorization": "not_connected",
        "official_publish_api": "not_called",
    })
    write_json(OUT / "release-checklist.json", release)

    rights = read_json(OUT / "rights-manifest.json")
    rights["status"] = "review_required"
    rights["publishable"] = False
    rights["human_review_required"] = True
    rights["commercial_use_allowed"] = False
    write_json(OUT / "rights-manifest.json", rights)

    costs = read_json(OUT / "cost-ledger.json")
    costs["build_id"] = "first-cantonese-pilot-2026-11-24-v6"
    costs["v6_hardware_encode"] = {
        "encoder": encoder,
        "gpu_energy_cost": "not_metered",
        "cpu_energy_cost": "not_metered",
        "paid_api_cost": 0,
        "paid_asset_cost": 0,
    }
    costs["settled_revenue"] = 0
    costs["revenue_status"] = "no_sales_or_settlements_recorded"
    write_json(OUT / "cost-ledger.json", costs)

    vertical = OUT / "video-9x16.mp4"
    horizontal = OUT / "video-16x9.mp4"
    # No -shortest: the SRT ends before the 24.6s video and must not truncate it.
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-n",
        "-i", str(INPUT_VIDEO), "-i", str(INPUT_SRT),
        "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=chi", "-movflags", "+faststart",
        str(vertical),
    ])

    filter_graph = (
        "[0:v]split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,boxblur=28:10[bg];"
        "[fgsrc]scale=608:1080:flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=24,setsar=1,format=yuv420p[v]"
    )
    common = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-n",
        "-i", str(INPUT_VIDEO), "-i", str(INPUT_SRT),
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a:0", "-map", "1:0",
    ]
    if encoder == "h264_amf":
        encode_args = [
            "-c:v", "h264_amf", "-quality", "quality", "-b:v", "12M",
            "-maxrate", "16M", "-bufsize", "24M", "-pix_fmt", "yuv420p",
        ]
    else:
        encode_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]
    command = common + encode_args + [
        "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=chi",
        "-movflags", "+faststart", str(horizontal),
    ]
    try:
        run(command, timeout=1200)
    except RuntimeError:
        if encoder != "h264_amf":
            raise
        horizontal.unlink(missing_ok=True)
        encoder = "libx264_fallback"
        fallback = common + [
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=chi",
            "-movflags", "+faststart", str(horizontal),
        ]
        run(fallback, timeout=1800)

    technical_vertical = inspect_video_file(
        vertical, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
        expected_width=1080, expected_height=1920, expected_fps=24,
        min_duration_seconds=24, max_duration_seconds=25, require_audio=True,
    )
    technical_horizontal = inspect_video_file(
        horizontal, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
        expected_width=1920, expected_height=1080, expected_fps=24,
        min_duration_seconds=24, max_duration_seconds=25, require_audio=True,
    )

    output_probes = {}
    for output in (vertical, horizontal):
        metadata = probe(output, ffprobe)
        streams = metadata.get("streams", [])
        if not any(
            item.get("codec_type") == "subtitle" and item.get("codec_name") == "mov_text"
            for item in streams
        ):
            raise RuntimeError(f"{output.name} is missing the embedded mov_text subtitle track")
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", str(output), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        if decoded.returncode:
            raise RuntimeError(f"full decode failed for {output.name}: {(decoded.stderr or decoded.stdout)[-1500:]}")
        output_probes[output.name] = metadata

    try:
        motion_vertical = inspect_video_motion(vertical, max_frames=24)
    except Exception as exc:
        motion_vertical = {"status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    try:
        motion_horizontal = inspect_video_motion(horizontal, max_frames=24)
    except Exception as exc:
        motion_horizontal = {"status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    asr_summary = {
        "schema": "muse.cantonese-asr-review.v1",
        "standalone_voiceover": {
            "status": voice["asr"]["status"],
            "cer": cer,
            "max_cer": float(voice["asr"]["max_cer"]),
            "recognized_text": voice["asr"]["transcript"],
            "reference_text": voice["voiceover_text"],
        },
        "known_mistranscription": "揾反正 -> 搵反證",
        "corrected_subtitles": "voiceover-preview-edited.srt",
        "mixed_audio_asr": {
            "status": "not_run",
            "note": "ASR gate is measured on the standalone voiceover WAV; final-mix human listening review remains mandatory.",
        },
        "human_listening_review_required": True,
        "human_subtitle_review_required": True,
    }
    write_json(OUT / "asr-summary.json", asr_summary)

    storyboard = read_json(OUT / "storyboard.json")
    storyboard["voiceover"] = {
        "status": "used_in_video",
        "source": "voiceover-preview.wav",
        "muxed_into_video": True,
        "asr_cer_standalone": cer,
        "voice_profile": voice.get("tts", {}).get("voice_profile"),
        "human_listening_review_required": True,
    }
    storyboard["subtitles"] = {
        "file": "voiceover-preview-edited.srt",
        "embedded_track": "mov_text",
        "burned_in": False,
        "human_review_required": True,
    }
    write_json(OUT / "storyboard.json", storyboard)

    qa = {
        "schema": "muse.cantonese-pilot-v6-qa.v1",
        "status": "passed" if all(
            item.get("status") == "passed" for item in (technical_vertical, technical_horizontal)
        ) else "needs_review",
        "vertical": technical_vertical,
        "horizontal": technical_horizontal,
        "vertical_motion": motion_vertical,
        "horizontal_motion": motion_horizontal,
        "standalone_voiceover_asr_cer": cer,
        "standalone_voiceover_asr_gate": "passed",
        "voiceover_muxed": True,
        "soft_subtitle_track_embedded": True,
        "human_visual_review_required": True,
        "human_listening_review_required": True,
        "human_subtitle_review_required": True,
        "rights_review_required": True,
        "commercial_use_allowed": False,
        "publishable": False,
    }
    write_json(OUT / "video-qa.json", qa)

    readme = f"""# Cantonese Pilot v6 — narrated review package

- Vertical: video-9x16.mp4, 1080×1920, H.264/AAC, selectable Chinese soft subtitles.
- Landscape: video-16x9.mp4, 1920×1080, blurred-background layout, H.264/AAC, selectable Chinese soft subtitles.
- Source: existing local voice-preview MP4; corrected subtitle draft embedded as mov_text.
- Standalone voiceover ASR CER: {cer:.2%} (10% threshold passed). The ASR check is on the standalone voiceover WAV, not the final mixed audio.
- Voice: default neutral local Hong Kong Cantonese; not approved as a mature female voice.
- Rights: review required. Wan 2.1 source shots, background audio, TTS terms/voice and ASR model redistribution terms are not all cleared for commercial reuse.
- Human listening, subtitle, visual, source/fact and rights reviews remain pending.
- Release state: draft_only; no public upload, account action, paid API, ad spend, order or settled revenue occurred.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    vertical_meta = output_probes[vertical.name]
    horizontal_meta = output_probes[horizontal.name]
    audio_meta = probe(OUT / "voiceover-preview.wav", ffprobe)
    vertical_duration = float(vertical_meta.get("format", {}).get("duration", 0))
    horizontal_duration = float(horizontal_meta.get("format", {}).get("duration", 0))
    audio_duration = float(audio_meta.get("format", {}).get("duration", 0))

    files = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "delivery-package.zip", "package.sha256"}:
            files[path.relative_to(OUT).as_posix()] = sha256(path)
    manifest = {
        "schema": "muse.canonical-cantonese-pilot.v6",
        "status": "completed_review_only",
        "title": voice.get("voiceover_text", "Cantonese AI verification short"),
        "source_video_sha256": voice["video_sha256"],
        "voiceover": {
            "file": "voiceover-preview.wav",
            "sha256": sha256(OUT / "voiceover-preview.wav"),
            "duration_seconds": audio_duration,
            "asr_cer": cer,
            "asr_gate": "passed",
            "model_repo": voice["tts"]["model_repo"],
            "model_revision": voice["tts"]["model_revision"],
            "model_license_metadata": voice["tts"]["license"],
            "voice_profile": voice["tts"]["voice_profile"],
            "voice_profile_approved": False,
            "human_listening_review_required": True,
        },
        "videos": {
            "vertical": {
                "file": vertical.name, "sha256": sha256(vertical),
                "bytes": vertical.stat().st_size, "width": 1080, "height": 1920,
                "duration_seconds": vertical_duration, "subtitle_track": "mov_text",
                "technical_qa": technical_vertical.get("status"),
            },
            "horizontal": {
                "file": horizontal.name, "sha256": sha256(horizontal),
                "bytes": horizontal.stat().st_size, "width": 1920, "height": 1080,
                "duration_seconds": horizontal_duration, "subtitle_track": "mov_text",
                "encoder": encoder, "technical_qa": technical_horizontal.get("status"),
            },
        },
        "release_state": "draft_only",
        "rights_review_required": True,
        "commercial_use_allowed": False,
        "publishable": False,
        "auto_publish": False,
        "publish_api_called": False,
        "account_authorization": "not_connected",
        "orders": 0,
        "settled_revenue": 0,
        "files": files,
    }
    write_json(OUT / "manifest.json", manifest)

    package = OUT / "delivery-package.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(OUT.rglob("*")):
            if path.is_file() and path not in {package, OUT / "package.sha256"}:
                archive.write(path, arcname=path.relative_to(OUT).as_posix())
    with zipfile.ZipFile(package) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"delivery ZIP CRC failed: {bad}")
    package_hash = sha256(package)
    (OUT / "package.sha256").write_text(f"{package_hash}  delivery-package.zip\n", encoding="ascii")

    handoff = {
        "schema": "muse.canonical-pilot-handoff.v6",
        "status": "draft_only_human_review_required",
        "pilot_dir": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "vertical_video": {
            "file": vertical.name, "sha256": sha256(vertical), "bytes": vertical.stat().st_size,
            "resolution": "1080x1920", "duration_seconds": vertical_duration,
            "technical_qa": technical_vertical.get("status"),
        },
        "horizontal_video": {
            "file": horizontal.name, "sha256": sha256(horizontal), "bytes": horizontal.stat().st_size,
            "resolution": "1920x1080", "duration_seconds": horizontal_duration,
            "encoder": encoder, "technical_qa": technical_horizontal.get("status"),
        },
        "voiceover_muxed": True,
        "standalone_voiceover_asr_cer": cer,
        "standalone_voiceover_asr_gate": "passed",
        "mixed_audio_asr": "not_run",
        "soft_subtitles_embedded": True,
        "voice_profile_approved": False,
        "human_listening_review_required": True,
        "human_subtitle_review_required": True,
        "human_visual_review_required": True,
        "rights_review_required": True,
        "commercial_use_allowed": False,
        "release_state": "draft_only",
        "account_authorization": "not_connected",
        "publish_api_called": False,
        "auto_publish": False,
        "delivery_package": str(package.relative_to(ROOT)).replace("\\", "/"),
        "delivery_package_sha256": package_hash,
        "delivery_package_zip_crc": "passed",
        "paid_api_cost_usd": 0,
        "paid_asset_cost_usd": 0,
        "local_compute_cost": "not_metered",
        "orders": 0,
        "settled_revenue": 0,
        "production_database_touched": False,
    }
    write_json(HANDOFF, handoff)
    print(json.dumps(handoff, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
