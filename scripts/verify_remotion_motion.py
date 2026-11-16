"""Run an opt-in real Remotion + FFmpeg integration smoke test.

Creates only temporary media under video-motion/out and removes it afterward.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from muse.video_product_builder import VideoProductBuilder
from muse.remotion_motion import render_motion_package, render_scene_timeline


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("FFmpeg and FFprobe are required")
    output_root = workspace / "video-motion" / "out"
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="muse-remotion-verify-", dir=output_root) as temporary:
        work = Path(temporary)
        base = work / "base.mp4"
        generated = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=512x288:rate=12:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(base)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if generated.returncode != 0 or not base.is_file():
            raise RuntimeError("Could not create the temporary audio/video fixture: " + generated.stderr[-800:])
        results = []
        scene_paths = []
        for index, color in enumerate(("0x18304a", "0x493523"), start=1):
            still = work / f"scene-{index}.png"
            still_result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c={color}:s=512x288",
                 "-frames:v", "1", str(still)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if still_result.returncode != 0 or not still.is_file():
                raise RuntimeError("Could not create a temporary still-scene fixture")
            scene_paths.append(still)
        scene_paths.insert(1, base)
        timeline_output = work / "scene-timeline.mp4"
        timeline_motion = render_scene_timeline(
            workspace, scene_paths, base, timeline_output, width=512, height=288,
            fps=12, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            timeout_seconds=900, image_seconds=1.5, transition_seconds=0.3,
        )
        timeline_decode = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", str(timeline_output), "-f", "null", "-"],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if timeline_decode.returncode != 0:
            raise RuntimeError("Scene timeline full decode failed: " + timeline_decode.stderr[-800:])
        timeline_qa = subprocess.run(
            [sys.executable, "-m", "muse.video_qa", str(timeline_output)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if timeline_qa.returncode != 0:
            raise RuntimeError("Scene timeline Muse QA failed to execute: " + timeline_qa.stderr[-800:])
        results.append({
            "stage": "Remotion multi-scene TransitionSeries",
            "motion": timeline_motion,
            "qa_status": json.loads(timeline_qa.stdout).get("status"),
            "full_decode": True,
        })
        for audio_mode in ("music", "narration"):
            output = work / f"{audio_mode}.mp4"
            motion = render_motion_package(
                workspace, base, output, title="Muse 超融合引擎",
                subtitle="RESEARCH / CREATE / EVOLVE",
                width=512, height=288, fps=12, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
                timeout_seconds=900, audio_mode=audio_mode,
            )
            decoded = subprocess.run(
                [ffmpeg, "-hide_banner", "-v", "error", "-i", str(output), "-f", "null", "-"],
                capture_output=True, text=True, timeout=180, check=False,
            )
            if decoded.returncode != 0:
                raise RuntimeError("Full decode failed: " + decoded.stderr[-800:])
            qa = subprocess.run(
                [sys.executable, "-m", "muse.video_qa", str(output)],
                capture_output=True, text=True, timeout=180, check=False,
            )
            if qa.returncode != 0:
                raise RuntimeError("Muse video QA failed to execute: " + qa.stderr[-800:])
            report = json.loads(qa.stdout)
            results.append({
                "audio_mode": audio_mode, "motion": motion,
                "qa_status": report.get("status"),
                "warning_codes": sorted({item.get("code") for item in report.get("warnings", [])}),
                "sha256": report.get("sha256"),
                "full_decode": True,
            })
        class SmokeBuilder(VideoProductBuilder):
            def __init__(self, project_workspace: Path, remotion_workspace: Path) -> None:
                super().__init__(project_workspace, motion_workspace=remotion_workspace)

            def _record_creator_artifact(
                self, result, *, title, prompt, creator_initiative_id, evolution_cycle_id
            ):
                return result
        project_workspace = work / "builder-workspace"
        source_folder = project_workspace / "data" / "video_sources" / "demo"
        source_folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base, source_folder / "01.mp4")
        for index, color in enumerate(("0x223a52", "0x5a3c2b"), start=1):
            still = source_folder / f"{index + 1:02d}.png"
            still_result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c={color}:s=1920x1080",
                 "-frames:v", "1", str(still)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if still_result.returncode != 0 or not still.is_file():
                raise RuntimeError("Could not create source-build still-scene fixture")
        music = source_folder / "music.wav"
        music_result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=3",
             "-c:a", "pcm_s16le", str(music)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if music_result.returncode != 0:
            raise RuntimeError("Could not create the temporary music fixture")
        builder = SmokeBuilder(project_workspace, workspace)
        product = builder.build_from_source(
            "demo", "Muse source-build integration", audio_name="music.wav",
            audio_mode="music", aspect_ratio="16:9", fps=30, image_seconds=4.0,
            encoder="software", motion_profile="brand_scene_transitions", timeout_seconds=900,
        )
        product_root = project_workspace / "data" / "products"
        verified = builder.verify_path(product_root, product["initiative_id"], product["version_id"])
        product_dir = product_root / product["initiative_id"] / product["version_id"]
        manifest = json.loads((product_dir / "manifest.json").read_text(encoding="utf-8"))
        import zipfile
        with zipfile.ZipFile(product_dir / "product.zip") as archive:
            if archive.testzip() is not None:
                raise RuntimeError("The generated product ZIP failed CRC validation")
        if not verified.get("validated") or manifest["video_metadata"]["motion"].get("renderer") != "remotion":
            raise RuntimeError("The Remotion-enhanced VideoProductBuilder output did not pass package verification")
        from unittest.mock import patch

        def fake_wan_generate(prompt, output, **kwargs):
            shutil.copyfile(base, output)
            return {
                "status": "completed", "output": str(output), "prompt_id": "remotion-smoke-fixture",
                "model": "Wan fixture", "seed": kwargs["seed"], "aspect_ratio": kwargs["aspect_ratio"],
                "fps": 12, "duration_seconds": 2.0, "frames": 24, "width": 512, "height": 288,
                "steps": kwargs["steps"], "has_audio": True, "bytes": output.stat().st_size,
            }

        with patch("muse.wan_video.generate_wan21_t2v", side_effect=fake_wan_generate):
            ai_product = builder.build_from_prompt(
                "Muse prompt-build integration", "A fixture prompt for motion integration",
                aspect_ratio="16:9", duration_seconds=2.0, steps=8, seed=42,
                motion_profile="brand_intro_outro", timeout_seconds=900,
            )
        ai_verified = builder.verify_path(product_root, ai_product["initiative_id"], ai_product["version_id"])
        ai_dir = product_root / ai_product["initiative_id"] / ai_product["version_id"]
        ai_manifest = json.loads((ai_dir / "manifest.json").read_text(encoding="utf-8"))
        if not ai_verified.get("validated") or ai_manifest["video_metadata"]["motion"].get("renderer") != "remotion":
            raise RuntimeError("The Remotion-enhanced AI prompt product did not pass package verification")
        results.append({
            "stage": "VideoProductBuilder.build_from_prompt (Wan mocked; post-production real)",
            "status": "passed",
            "product_validated": ai_verified["validated"],
            "motion": ai_manifest["video_metadata"]["motion"],
            "mp4_sha256": ai_verified["mp4_sha256"],
            "package_sha256": ai_verified["package_sha256"],
        })
        recovered_product = builder.package_generated_clip(
            "Muse generated-clip packaging integration", "A previously rendered fixture",
            base,
            generation_metadata={
                "status": "completed", "model": "integration-fixture",
                "width": 512, "height": 288, "fps": 12,
                "duration_seconds": 2.0, "frames": 24, "has_audio": True,
            },
            motion_profile="brand_intro_outro",
        )
        recovered_verified = builder.verify_path(
            product_root, recovered_product["initiative_id"], recovered_product["version_id"]
        )
        recovered_dir = product_root / recovered_product["initiative_id"] / recovered_product["version_id"]
        recovered_manifest = json.loads((recovered_dir / "manifest.json").read_text(encoding="utf-8"))
        if not recovered_verified.get("validated") or recovered_manifest["video_metadata"]["motion"].get("renderer") != "remotion":
            raise RuntimeError("The Remotion-enhanced generated-clip package did not pass verification")
        results.append({
            "stage": "VideoProductBuilder.package_generated_clip",
            "status": "passed",
            "product_validated": recovered_verified["validated"],
            "motion": recovered_manifest["video_metadata"]["motion"],
            "mp4_sha256": recovered_verified["mp4_sha256"],
            "package_sha256": recovered_verified["package_sha256"],
        })
        results.append({
            "stage": "VideoProductBuilder.build_from_source",
            "status": "passed",
            "product_validated": verified["validated"],
            "motion": manifest["video_metadata"]["motion"],
            "mp4_sha256": verified["mp4_sha256"],
            "package_sha256": verified["package_sha256"],
        })
    print(json.dumps({"status": "passed", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
