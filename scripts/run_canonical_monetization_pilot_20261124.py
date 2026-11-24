"""Run the canonical local Cantonese monetization pilot in an isolated workspace.

This script runs focused tests, copies immutable source assets into the sandbox,
creates the official draft-only video package, performs extended QA, and packages
the digital product. It does not touch production databases or publish anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SANDBOX = ROOT / ".muse-video-pilot-workspace-20261124"
SOURCE = ROOT / "data" / "prometheus" / "high-value-short-b133"
SANDBOX_SOURCE = SANDBOX / "data" / "prometheus" / "high-value-short-b133"
SOURCE_RELATIVE = "data/prometheus/high-value-short-b133"
OUTPUT_RELATIVE = "data/monetization/first-cantonese-pilot-2026-11-24"
OUTPUT = SANDBOX / OUTPUT_RELATIVE
DELIVERABLE = ROOT / "outputs" / "creator_pilot_20261124"
FINAL = DELIVERABLE / "canonical-monetization-pilot-v1"
HANDOFF = DELIVERABLE / "canonical-pilot-handoff.json"
SOURCE_FILES = (
    "ai-verification-three-steps-9x16.mp4",
    "cover.jpg",
    "source-notes.json",
    "storyboard.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_focused_tests() -> dict:
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.test_monetization_pilot",
        "tests.test_social_campaign_tracking",
    ])
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError(
            f"focused regression failed: tests={result.testsRun}, "
            f"failures={len(result.failures)}, errors={len(result.errors)}"
        )
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "status": "passed",
    }


def stage_immutable_sources() -> dict:
    if not SANDBOX.is_dir() or SANDBOX.is_symlink():
        raise FileNotFoundError(f"isolated sandbox missing or unsafe: {SANDBOX}")
    if not SOURCE.is_dir() or SOURCE.is_symlink():
        raise FileNotFoundError(f"source directory missing or unsafe: {SOURCE}")
    if SANDBOX_SOURCE.exists() and not SANDBOX_SOURCE.is_dir():
        raise RuntimeError("sandbox source path exists but is not a directory")
    SANDBOX_SOURCE.mkdir(parents=True, exist_ok=True)
    staged = {}
    for name in SOURCE_FILES:
        src = SOURCE / name
        dst = SANDBOX_SOURCE / name
        if not src.is_file() or src.is_symlink():
            raise FileNotFoundError(f"unsafe/missing source file: {src}")
        source_hash = sha256(src)
        if dst.exists():
            if not dst.is_file() or dst.is_symlink() or sha256(dst) != source_hash:
                raise RuntimeError(f"sandbox source collision or hash mismatch: {dst}")
        else:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        staged[name] = {"sha256": source_hash, "bytes": dst.stat().st_size}
    return staged


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError(f"refusing to overwrite isolated pilot output: {OUTPUT}")
    if FINAL.exists() or FINAL.is_symlink() or HANDOFF.exists() or HANDOFF.is_symlink():
        raise FileExistsError("refusing to overwrite canonical pilot deliverables")

    tests = run_focused_tests()
    staged_sources = stage_immutable_sources()

    from muse.monetization_pilot import create_cantonese_video_package
    result = create_cantonese_video_package(
        SANDBOX,
        source_relative=SOURCE_RELATIVE,
        output_relative=OUTPUT_RELATIVE,
    )
    if result.get("status") != "completed" or result.get("release_state") != "draft_only":
        raise RuntimeError(f"canonical pilot did not complete in draft-only state: {result}")

    sandbox_output = SANDBOX / result["output_dir"]
    if not sandbox_output.is_dir() or sandbox_output.is_symlink():
        raise RuntimeError("canonical pilot output directory missing or unsafe")
    manifest = json.loads((sandbox_output / "manifest.json").read_text(encoding="utf-8"))
    qa = json.loads((sandbox_output / "qa-report.json").read_text(encoding="utf-8"))
    subtitle_review = json.loads((sandbox_output / "subtitle-review.json").read_text(encoding="utf-8"))
    package = sandbox_output / "delivery-package.zip"
    video = sandbox_output / "video.mp4"
    if not all(p.is_file() for p in (package, video, sandbox_output / "voiceover.wav", sandbox_output / "subtitles.srt")):
        raise RuntimeError("canonical pilot omitted a required media artifact")
    if manifest.get("release_state") != "draft_only" or manifest.get("publish_api_called") is not False or manifest.get("auto_publish") is not False:
        raise RuntimeError("canonical pilot crossed publication gate")
    if manifest.get("voiceover", {}).get("asr_gate") != "passed":
        raise RuntimeError(f"canonical Cantonese ASR gate failed: {manifest.get('voiceover')}")
    cer = float(manifest["voiceover"]["asr_cer"])
    if cer > 0.10 or not manifest["voiceover"].get("human_listening_review_required"):
        raise RuntimeError("ASR threshold or human-listening gate was not preserved")
    if qa.get("status") != "passed" or qa.get("full_decode") != "passed" or qa.get("ready_to_publish") is not False:
        raise RuntimeError("canonical technical QA or release gate mismatch")
    if subtitle_review.get("human_review_required") is not True or subtitle_review.get("publish_blocked") is not True:
        raise RuntimeError("subtitle human-review/publish block missing")
    with zipfile.ZipFile(package, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"canonical delivery ZIP CRC failure: {bad}")

    shutil.copytree(sandbox_output, FINAL)
    final_video = FINAL / "video.mp4"
    final_package = FINAL / "delivery-package.zip"
    if sha256(final_video) != manifest["video"]["sha256"] or sha256(final_package) != result["package"]["sha256"]:
        raise RuntimeError("copied canonical deliverables failed SHA-256 verification")

    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    inventory = discover_media_tools()
    tools = {item.tool_id: item.executable for item in inventory.tools}
    extended_qa = inspect_video_file(
        final_video, ffprobe_path=tools["ffprobe"], ffmpeg_path=tools["ffmpeg"],
        expected_width=1080, expected_height=1920, expected_fps=24,
        min_duration_seconds=20, max_duration_seconds=30, require_audio=True,
    )
    try:
        motion = inspect_video_motion(final_video, max_frames=180)
    except Exception as exc:
        motion = {"status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    (FINAL / "video-qa-extended.json").write_text(
        json.dumps({"technical": extended_qa, "motion_diagnostics": motion,
                    "human_review_required": True, "publishable": False},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    package_script = ROOT / "scripts" / "package_creator_kit_v011_20261124.py"
    package_run = subprocess.run(
        [sys.executable, str(package_script)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if package_run.returncode != 0:
        raise RuntimeError(f"digital product packaging failed: {(package_run.stderr or package_run.stdout)[-1500:]}")
    product_package = DELIVERABLE / "AI-Verification-Kit-v0.1.1.zip"
    product_manifest_path = DELIVERABLE / "AI-Verification-Kit-v0.1.1.manifest.json"
    product_manifest = json.loads(product_manifest_path.read_text(encoding="utf-8"))
    if product_manifest.get("zip_crc") != "passed" or product_manifest.get("published") is not False:
        raise RuntimeError("digital product package manifest validation failed")

    handoff = {
        "schema": "muse.canonical-monetization-pilot-handoff.v1",
        "status": "draft_only_review_required",
        "tests": tests,
        "source_files_staged_in_isolated_sandbox": staged_sources,
        "sandbox_output": str(sandbox_output.relative_to(ROOT)).replace("\\", "/"),
        "deliverable_dir": str(FINAL.relative_to(ROOT)).replace("\\", "/"),
        "video": {
            "file": "canonical-monetization-pilot-v1/video.mp4",
            "sha256": sha256(final_video),
            "bytes": final_video.stat().st_size,
            "duration_seconds": manifest["video"]["duration_seconds"],
            "width": manifest["video"]["width"],
            "height": manifest["video"]["height"],
            "asr_cer": cer,
            "asr_status": manifest["voiceover"]["asr_gate"],
            "voice_profile": manifest["voiceover"]["profile"],
            "voice_model_license": manifest["voiceover"]["license"],
            "human_listening_review_required": True,
        },
        "delivery_package": {
            "file": "canonical-monetization-pilot-v1/delivery-package.zip",
            "sha256": sha256(final_package),
            "bytes": final_package.stat().st_size,
            "zip_crc": "passed",
        },
        "extended_video_qa": {
            "status": extended_qa.get("status"),
            "warnings": extended_qa.get("warnings", []),
            "motion_summary": motion.get("summary"),
        },
        "digital_product": {
            "file": product_package.name,
            "sha256": product_manifest["sha256"],
            "bytes": product_manifest["bytes"],
            "version": product_manifest["version"],
            "price_hypothesis_usd": product_manifest["price_hypothesis"]["amount"],
            "price_validated": False,
            "published": False,
        },
        "publish_api_called": False,
        "auto_publish": False,
        "commercial_use_allowed": False,
        "account_authorization": "not_connected",
        "orders": 0,
        "settled_revenue": 0,
        "paid_api_cost_usd": 0,
        "paid_asset_cost_usd": 0,
        "cloud_compute_cost_usd": 0,
        "local_electricity_cost": "not_metered",
        "production_database_touched": False,
        "production_autonomous_supervisor_started": False,
    }
    HANDOFF.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(handoff, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
