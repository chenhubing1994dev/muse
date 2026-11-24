"""Create a review-only Cantonese voiceover preview for the first creator pilot.

This does not alter the validated silent draft, product databases, or publication state.
It uses the pinned local Apache-2.0 Canto-TTS model and requires the pinned ASR gate.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SANDBOX = ROOT / ".muse-video-pilot-workspace-20261124"
DELIVERABLE = ROOT / "outputs" / "creator_pilot_20261124"
SOURCE_VIDEO = DELIVERABLE / "ai-verification-three-steps-9x16.mp4"
SOURCE_AUDIO = SANDBOX / "outputs" / "voice-diagnostic-02" / "voice-3.wav"
PREVIEW_VIDEO = DELIVERABLE / "ai-verification-three-steps-9x16-voice-review-only.mp4"
PREVIEW_AUDIO = DELIVERABLE / "voiceover-preview.wav"
PREVIEW_SRT = DELIVERABLE / "voiceover-preview.srt"
SCRIPT_FILE = DELIVERABLE / "voiceover-script.txt"
TEXT = "人工智能可以答得好快，但未必答得啱。先查來源，再搵反證。唔肯定，就先唔好信。"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    targets = [
        PREVIEW_VIDEO, PREVIEW_AUDIO, PREVIEW_SRT, SCRIPT_FILE,
        DELIVERABLE / "voiceover-preview-manifest.json",
        DELIVERABLE / "voiceover-preview-cost-addendum.json",
        DELIVERABLE / "voiceover-preview-rights-addendum.json",
    ]
    existing = [str(path) for path in targets if path.exists() or path.is_symlink()]
    if existing:
        raise FileExistsError(f"refusing to overwrite preview files: {existing}")
    if not SOURCE_VIDEO.is_file() or SOURCE_VIDEO.is_symlink():
        raise FileNotFoundError("base silent draft is missing or unsafe")
    if not SOURCE_AUDIO.is_file() or SOURCE_AUDIO.is_symlink():
        raise FileNotFoundError("ASR-passing local Cantonese voice candidate is missing or unsafe")

    from muse.cantonese_asr import transcribe_cantonese
    from muse.media import discover_media_tools

    asr = transcribe_cantonese(
        SOURCE_AUDIO,
        model_dir=SANDBOX / "models" / "whisper-small-cantonese",
        output_srt=PREVIEW_SRT,
        reference_text=TEXT,
        max_cer=0.10,
        device="cpu",
        compute_type="int8",
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    if asr.get("status") != "passed" or float((asr.get("comparison") or {}).get("cer", 1.0)) > 0.10:
        raise RuntimeError("refusing to mux voice preview: pinned Cantonese ASR gate did not pass")

    shutil.copy2(SOURCE_AUDIO, PREVIEW_AUDIO)
    inventory = discover_media_tools()
    executables = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg = executables.get("ffmpeg")
    ffprobe = executables.get("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg/FFprobe unavailable; voice preview not rendered")

    filter_graph = (
        "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
        "[0:a]volume=0.50[amb];"
        "[voice][amb]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,"
        "alimiter=limit=0.89[a]"
    )
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-n",
        "-i", str(SOURCE_VIDEO), "-i", str(PREVIEW_AUDIO),
        "-filter_complex", filter_graph,
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-metadata", "title=AI verification - Cantonese voice review preview",
        str(PREVIEW_VIDEO),
    ]
    rendered = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if rendered.returncode != 0 or not PREVIEW_VIDEO.is_file():
        raise RuntimeError(f"FFmpeg voice preview failed: {(rendered.stderr or rendered.stdout)[-1800:]}")

    decoded = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", "-i", str(PREVIEW_VIDEO), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if decoded.returncode != 0:
        raise RuntimeError(f"voice preview full decode failed: {(decoded.stderr or decoded.stdout)[-1800:]}")

    probed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration,size:stream=codec_name,width,height,avg_frame_rate",
         "-of", "json", str(PREVIEW_VIDEO)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if probed.returncode != 0:
        raise RuntimeError("FFprobe could not inspect the voice preview")
    metadata = json.loads(probed.stdout)
    video_stream = next((item for item in metadata.get("streams", []) if item.get("codec_name") == "h264"), None)
    audio_stream = next((item for item in metadata.get("streams", []) if item.get("codec_name") == "aac"), None)
    if not video_stream or not audio_stream:
        raise RuntimeError("voice preview must contain H.264 video and AAC audio")

    SCRIPT_FILE.write_text(TEXT + "\n", encoding="utf-8")
    comparison = asr.get("comparison") or {}
    voice_meta = {
        "schema": "muse.creator.voice-preview.v1",
        "status": "review_only",
        "video_file": PREVIEW_VIDEO.name,
        "video_sha256": sha256(PREVIEW_VIDEO),
        "video_bytes": PREVIEW_VIDEO.stat().st_size,
        "duration_seconds": float(metadata.get("format", {}).get("duration", 0)),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": video_stream.get("avg_frame_rate"),
        "audio_file": PREVIEW_AUDIO.name,
        "audio_sha256": sha256(PREVIEW_AUDIO),
        "audio_bytes": PREVIEW_AUDIO.stat().st_size,
        "subtitle_file": PREVIEW_SRT.name,
        "script_file": SCRIPT_FILE.name,
        "voiceover_text": TEXT,
        "tts": {
            "engine": "canto-tts-onnx-segmented",
            "model_repo": "typangaa/canto-tts-nano",
            "model_revision": "fe17418a293d9be85685ddf750b0cfef9c88abb4",
            "license": "Apache-2.0",
            "voice_profile": "neutral local Hong Kong Cantonese; not a mature female voice",
            "offline": True,
        },
        "asr": {
            "engine": asr.get("engine"),
            "model_repo": asr.get("model_repo"),
            "model_revision": asr.get("model_revision"),
            "model_sha256": asr.get("model_sha256"),
            "status": asr.get("status"),
            "cer": comparison.get("cer"),
            "max_cer": comparison.get("max_cer"),
            "transcript": asr.get("transcript"),
            "segment_count": asr.get("segment_count"),
            "human_listening_review_required": True,
        },
        "technical_decode": "passed",
        "publishable": False,
        "commercial_use_allowed": False,
        "human_listening_and_editorial_review_required": True,
        "publish_api_called": False,
        "auto_publish": False,
    }
    cost = {
        "currency": "USD",
        "paid_tts_api_cost": 0,
        "paid_stock_asset_cost": 0,
        "cloud_compute_cost": 0,
        "local_tts_model_license": "Apache-2.0",
        "local_cpu_compute_cost": "electricity not metered",
        "settled_revenue": 0,
        "revenue_status": "no_sales_or_settlements_recorded",
        "note": "Review-only voice preview; no external speech service or paid API was used.",
    }
    rights = {
        "schema": "muse.creator.voice-rights.v1",
        "voice_asset": PREVIEW_AUDIO.name,
        "origin": "locally synthesized with pinned Canto-TTS-Nano ONNX",
        "model_license": "Apache-2.0",
        "model_revision": "fe17418a293d9be85685ddf750b0cfef9c88abb4",
        "commercial_use_allowed": False,
        "license_review_status": "model-license-known; complete asset/editorial review pending",
        "human_listening_review_required": True,
        "overall_video_rights_status": "review_required",
    }
    (DELIVERABLE / "voiceover-preview-manifest.json").write_text(
        json.dumps(voice_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (DELIVERABLE / "voiceover-preview-cost-addendum.json").write_text(
        json.dumps(cost, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (DELIVERABLE / "voiceover-preview-rights-addendum.json").write_text(
        json.dumps(rights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "review_only_preview_completed",
        "video": str(PREVIEW_VIDEO.relative_to(ROOT)).replace("\\", "/"),
        "video_bytes": PREVIEW_VIDEO.stat().st_size,
        "video_sha256": sha256(PREVIEW_VIDEO),
        "duration_seconds": voice_meta["duration_seconds"],
        "asr_status": asr.get("status"),
        "cer": comparison.get("cer"),
        "tts_license": "Apache-2.0",
        "voice_profile": voice_meta["tts"]["voice_profile"],
        "commercial_use_allowed": False,
        "publish_api_called": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
