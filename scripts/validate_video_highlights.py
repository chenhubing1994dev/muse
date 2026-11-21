from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


def run_real_ffmpeg_smoke() -> bool:
    from muse.media import discover_media_tools
    from muse.video_highlights import analyze_video_audio, main as clips_cli, render_vertical_clip
    from muse.video_face_framing import stabilize_face_keyframes
    from muse.video_contact_sheet import create_contact_sheet
    from muse.video_qa import inspect_video_file
    from muse.initiative_engine import InitiativeStore
    from muse.video_product_builder import VideoProductBuilder

    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
    if not ffmpeg or not ffprobe:
        print("MUSE_FFMPEG_SMOKE_SKIPPED: FFmpeg/FFprobe not detected")
        return False
    with tempfile.TemporaryDirectory(prefix="muse-neonclipping-smoke-") as temporary:
        root = Path(temporary)
        source, output = root / "fixture.mp4", root / "vertical-captioned.mp4"
        created = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=3000:sample_rate=48000",
            "-t", "8", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
            "-shortest", "-n", str(source),
        ], capture_output=True, text=True, timeout=60, check=False)
        if created.returncode != 0 or not source.is_file():
            raise RuntimeError("could not create synthetic FFmpeg fixture: " + (created.stderr or "")[-1000:])
        contact_sheet = root / "contact-sheet.png"
        contact_result = create_contact_sheet(
            source, contact_sheet, samples=12, columns=4, thumbnail_width=160,
            ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
        )
        if (contact_result.get("status") != "completed"
                or contact_result.get("image_decode") != "passed"
                or contact_result.get("samples_requested") != 12
                or not contact_sheet.is_file()):
            raise RuntimeError("real FFmpeg contact sheet generation failed: " + json.dumps(contact_result, ensure_ascii=False))
        print("MUSE_CONTACT_SHEET_REAL_FFMPEG_OK", contact_result["width"], contact_result["height"])
        analysis = analyze_video_audio(source, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
        if analysis["analysis_mode"] != "audio_only" or analysis["window_count"] < 5:
            raise RuntimeError("real FFmpeg audio-only analysis returned incomplete windows")
        spectral_analysis = analyze_video_audio(
            source, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe, spectral_2_4khz=True,
        )
        band_levels = [item["band_energy_2_4khz_db"] for item in spectral_analysis["windows"]]
        if (not spectral_analysis["spectral_feature_2_4khz"]
                or len(band_levels) != analysis["window_count"] or max(band_levels) < -90.0):
            raise RuntimeError("real FFmpeg 2-4 kHz band-pass smoke returned no usable signal")

        subtitle = root / "captions.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:05,000\nMuse 本地切片字幕验证\n", encoding="utf-8-sig")
        rendered = render_vertical_clip(
            source, output, 0, 5, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            subtitles=subtitle, available_encoders=inventory.hardware_encoders,
        )
        if rendered.get("status") != "completed" or not output.is_file():
            raise RuntimeError("real FFmpeg 9:16 subtitle render failed: " + json.dumps(rendered, ensure_ascii=False))
        qa = inspect_video_file(
            output, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
            expected_width=1080, expected_height=1920, require_audio=True,
        )
        if qa.get("status") != "passed":
            raise RuntimeError("real Muse video QA failed: " + json.dumps(qa, ensure_ascii=False))
        analysis_json = root / "analysis.json"
        analysis_json.write_text(json.dumps({
            "analysis_mode": "audio_only",
            "candidates": [{"start": 0.0, "end": 5.0, "score": 0.93, "transcript": "Muse 本地字幕验证。"}],
            "words": [
                {"start": 0.2, "end": 1.2, "text": "Muse"},
                {"start": 1.3, "end": 2.2, "text": "本地"},
                {"start": 2.3, "end": 3.2, "text": "字幕。"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        cli_output = root / "cli-captioned.mp4"
        cli_status = clips_cli([
            "render", str(source), str(analysis_json), str(cli_output),
            "--captions-from-analysis", "--face-aware-crop",
            "--ffmpeg", ffmpeg, "--ffprobe", ffprobe,
        ])
        if cli_status != 0 or not cli_output.is_file():
            raise RuntimeError("ranked-candidate CLI render/QA flow failed")

        dynamic_cli_output = root / "cli-dynamic-face.mp4"
        dynamic_cli_status = clips_cli([
            "render", str(source), str(analysis_json), str(dynamic_cli_output),
            "--dynamic-face-crop", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe,
        ])
        if dynamic_cli_status != 0 or not dynamic_cli_output.is_file():
            raise RuntimeError("dynamic face crop CLI did not safely render/fallback")

        face_centered_output = root / "face-centered.mp4"
        face_centered = render_vertical_clip(
            source, face_centered_output, 0, 5, ffmpeg_path=ffmpeg,
            ffprobe_path=ffprobe, crop_center_x=0.72,
        )
        if (face_centered.get("status") != "completed"
                or face_centered.get("framing", {}).get("mode") != "face_aware_clip_center"):
            raise RuntimeError("real FFmpeg face-aware crop expression failed: " + json.dumps(face_centered, ensure_ascii=False))
        face_centered_qa = inspect_video_file(
            face_centered_output, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
            expected_width=1080, expected_height=1920, require_audio=True,
        )
        if face_centered_qa.get("status") != "passed":
            raise RuntimeError("real FFmpeg face-aware crop output failed Muse full-decode QA")

        dynamic_keyframes, stabilization = stabilize_face_keyframes([
            {"time_seconds": 0.0, "center_x": 0.35},
            {"time_seconds": 2.5, "center_x": 0.65},
            {"time_seconds": 5.0, "center_x": 0.45},
        ])
        if not stabilization.get("applied") or stabilization.get("output_keyframes") != 3:
            raise RuntimeError("face crop stabilization did not produce a bounded trajectory")
        dynamic_output = root / "dynamic-face.mp4"
        dynamic_render = render_vertical_clip(
            source, dynamic_output, 0, 5, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            crop_center_keyframes=dynamic_keyframes,
        )
        if (dynamic_render.get("status") != "completed"
                or dynamic_render.get("framing", {}).get("mode") != "sampled_face_keyframes"
                or dynamic_render.get("framing", {}).get("crop_keyframes") != dynamic_keyframes):
            raise RuntimeError("real FFmpeg dynamic crop expression failed: " + json.dumps(dynamic_render, ensure_ascii=False))
        dynamic_qa = inspect_video_file(
            dynamic_output, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
            expected_width=1080, expected_height=1920, require_audio=True,
        )
        if dynamic_qa.get("status") != "passed":
            raise RuntimeError("real FFmpeg dynamic crop output failed Muse full-decode QA")
        register_status = clips_cli([
            "register", str(source), str(cli_output), str(analysis_json),
            "Muse NeonClipping Adapter Smoke", "--workspace", str(root),
            "--ffmpeg", ffmpeg, "--ffprobe", ffprobe,
        ])
        if register_status != 0:
            raise RuntimeError("clip product registration CLI failed")
        product_dirs = list((root / "data" / "products").glob("*/*"))
        if len(product_dirs) != 1:
            raise RuntimeError("clip product adapter did not create exactly one versioned product")
        product = product_dirs[0]
        verified = VideoProductBuilder.verify_path(root / "data" / "products", product.parent.name, product.name)
        if not verified.get("validated") or not verified.get("clip_provenance"):
            raise RuntimeError("registered clip product failed canonical integrity verification")
        with InitiativeStore(root / "data" / "initiatives" / "muse.sqlite3") as store:
            attempts = store.list_video_attempts(limit=10)
        if len(attempts) != 1 or attempts[0]["status"] != "succeeded":
            raise RuntimeError("clip product did not link exactly one successful canonical video attempt")
        with zipfile.ZipFile(product / "product.zip") as archive:
            if "clip-captions.srt" not in archive.namelist():
                raise RuntimeError("clip product ZIP is missing its caption sidecar")
        print("MUSE_CLIP_PRODUCT_SMOKE_OK: FFmpeg render, canonical product manifest, caption ZIP and InitiativeStore attempt verified")
    return True


def main() -> int:
    for name in ("video_highlights.py", "video_face_framing.py", "clip_product_adapter.py", "video_product_builder.py"):
        compile((SOURCE / "muse" / name).read_text(encoding="utf-8"), f"src/muse/{name}", "exec")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8-sig"))
    entry = project["project"]["scripts"].get("muse-video-clips")
    if entry != "muse.video_highlights:main":
        raise RuntimeError("muse-video-clips entry point is missing or incorrect")

    suite = unittest.TestSuite()
    for module_name, test_name in (
        ("test_video_highlights", "test_video_highlights.py"),
        ("test_video_face_framing", "test_video_face_framing.py"),
        ("test_clip_product_adapter", "test_clip_product_adapter.py"),
    ):
        test_path = ROOT / "tests" / test_name
        spec = importlib.util.spec_from_file_location(module_name, test_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load {test_name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    run_real_ffmpeg_smoke()

    from muse.video_highlights import main as cli_main
    try:
        cli_main(["--help"])
    except SystemExit as exc:
        if exc.code != 0:
            return int(exc.code or 1)
    print("MUSE_NEONCLIPPING_STAGE3_VALIDATION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
