from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    project = tomllib.load(stream)
assert project["project"]["scripts"]["muse-ffmpeg-skill"] == "muse.ffmpeg_skill:main"
assert project["project"]["scripts"]["muse-video"] == "muse.video_factory:main"
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT / "src")

for args in (["-m", "muse.ffmpeg_skill", "contract"], ["-m", "muse.ffmpeg_skill", "--help"], ["-m", "muse.ffmpeg_skill", "cut", "--help"], ["-m", "muse.ffmpeg_skill", "join", "--help"], ["-m", "muse.ffmpeg_skill", "fit", "--help"], ["-m", "muse.ffmpeg_skill", "contact-sheet", "--help"]):
    result = subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit(f"CLI validation failed: {args}\n{result.stderr[-2000:]}")
    if args[-1] == "contract":
        contract = json.loads(result.stdout)
        assert len(contract["tools"]) == 42
        assert contract["upstream"]["reviewed_version"] == "3.12.1"
        assert contract["requirements"]["ffmpeg"] == ">=6.1"

for pattern in ("test_ffmpeg_skill.py", "test_ffmpeg_cut.py", "test_ffmpeg_join.py", "test_ffmpeg_fit.py", "test_video_contact_sheet.py", "test_video_face_framing.py"): 
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=240,
    )
    print(tests.stdout)
    print(tests.stderr)
    if tests.returncode != 0:
        raise SystemExit(f"{pattern} failed:\n{tests.stderr[-4000:]}")

doctor = subprocess.run(
    [sys.executable, "-m", "muse.ffmpeg_skill", "doctor"],
    cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
)
if doctor.returncode not in (0, 1, 2):
    raise SystemExit(f"FFmpeg doctor invocation failed: {doctor.stderr[-2000:]}")
doctor_report = json.loads(doctor.stdout)
summary = {
    "ffmpeg_version": doctor_report.get("version"),
    "executables": doctor_report.get("executables"),
    "hardware_encoders": doctor_report.get("hardware_encoders"),
    "tool_readiness": {
        state: sum(1 for row in doctor_report.get("tools", {}).values() if row.get("readiness") == state)
        for state in ("available", "missing", "unknown")
    },
}
print(json.dumps(summary, ensure_ascii=False))

ffmpeg = doctor_report.get("executables", {}).get("ffmpeg", {}).get("path")
ffprobe = doctor_report.get("executables", {}).get("ffprobe", {}).get("path")
encoders = doctor_report.get("components", {}).get("encoders", {}).get("items", [])
codec = "mpeg4" if "mpeg4" in encoders else ("libx264" if "libx264" in encoders else None)
if ffmpeg and ffprobe and codec:
    sys.path.insert(0, str(ROOT / "src"))
    from muse.ffmpeg_skill import probe_media
    with tempfile.TemporaryDirectory(prefix="muse-ffmpeg-probe-") as directory:
        sample = Path(directory) / "probe-smoke.mp4"
        rendered = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
             "-t", "2", "-an", "-c:v", codec, "-pix_fmt", "yuv420p", "-y", str(sample)],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        if rendered.returncode != 0:
            raise SystemExit(f"synthetic FFmpeg smoke failed: {rendered.stderr[-2000:]}")
        probed = probe_media(sample, ffprobe_path=ffprobe)
        if probed.get("status") != "passed" or not probed.get("video") or probed["video"].get("width") != 160:
            raise SystemExit(f"real ffprobe smoke failed: {probed}")
        print("MUSE_FFMPEG_PROBE_SMOKE_OK")
        dry_output = Path(directory) / "cut-cli-dry-run.mp4"
        cli_plan = subprocess.run(
            [sys.executable, "-m", "muse.ffmpeg_skill", "cut", str(sample), str(dry_output),
             "--start", "0.2", "--end", "1.2", "--dry-run", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        if cli_plan.returncode != 0 or json.loads(cli_plan.stdout).get("status") != "planned" or dry_output.exists():
            raise SystemExit(f"cut CLI dry-run failed: {cli_plan.stderr[-2000:]} {cli_plan.stdout[-1000:]}")
        print("MUSE_FFMPEG_CUT_CLI_DRY_RUN_OK")
        contact_cli_output = Path(directory) / "contact-sheet-cli-dry-run.png"
        contact_cli_plan = subprocess.run(
            [sys.executable, "-m", "muse.ffmpeg_skill", "contact-sheet", str(sample),
             str(contact_cli_output), "--samples", "8", "--columns", "4",
             "--thumbnail-width", "160", "--dry-run", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        if (contact_cli_plan.returncode != 0
                or json.loads(contact_cli_plan.stdout).get("status") != "planned"
                or contact_cli_output.exists()):
            raise SystemExit(f"contact-sheet CLI dry-run failed: {contact_cli_plan.stderr[-2000:]} {contact_cli_plan.stdout[-1000:]}")
        print("MUSE_FFMPEG_CONTACT_SHEET_CLI_DRY_RUN_OK")

        if "libx264" in encoders:
            cut_source = Path(directory) / "cut-source.mp4"
            cut_output = Path(directory) / "cut-output.mp4"
            source_render = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                 "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cut_source)],
                cwd=ROOT, capture_output=True, text=True, timeout=90,
            )
            if source_render.returncode != 0:
                raise SystemExit(f"cut fixture generation failed: {source_render.stderr[-2000:]}")
            from muse.ffmpeg_skill import cut_media
            cut = cut_media(
                cut_source, cut_output, 0.5, 2.5,
                ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            )
            if cut.get("status") != "completed" or cut.get("technical_qa", {}).get("status") != "passed":
                raise SystemExit(f"real FFmpeg cut smoke failed: {cut}")
            print("MUSE_FFMPEG_CUT_SMOKE_OK")
            print(f"MUSE_FFMPEG_CUT_ENCODER={cut.get('encoder')};fallback={cut.get('encoder_fallback_used')}")
            join_source_2 = Path(directory) / "join-source-2.mp4"
            join_output = Path(directory) / "join-output.mp4"
            second_render = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
                 "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(join_source_2)],
                cwd=ROOT, capture_output=True, text=True, timeout=90,
            )
            if second_render.returncode != 0:
                raise SystemExit(f"join fixture generation failed: {second_render.stderr[-2000:]}")
            from muse.ffmpeg_skill import join_media
            joined = join_media([cut_source, join_source_2], join_output,
                                ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
            if joined.get("status") != "completed" or joined.get("technical_qa", {}).get("status") != "passed":
                raise SystemExit(f"real FFmpeg join smoke failed: {joined}")
            print(f"MUSE_FFMPEG_JOIN_SMOKE_OK;encoder={joined.get('encoder')};fallback={joined.get('encoder_fallback_used')}")
            fit_output = Path(directory) / "fit-output-vertical.mp4"
            from muse.ffmpeg_skill import fit_media
            fitted = fit_media(cut_source, fit_output, width=180, height=320,
                               mode="cover", focus_x=0.5, focus_y=0.5,
                               face_aware=True, max_face_samples=3,
                               ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
            if fitted.get("status") != "completed" or fitted.get("width") != 180 or fitted.get("height") != 320:
                raise SystemExit(f"real FFmpeg fit smoke failed: {fitted}")
            if fitted.get("technical_qa", {}).get("status") != "passed" or not fitted.get("visual_review_required"):
                raise SystemExit(f"real FFmpeg fit QA/review gate failed: {fitted}")
            if not isinstance(fitted.get("face_framing"), dict):
                raise SystemExit(f"face-aware fit did not report its detection/fallback decision: {fitted}")
            print(f"MUSE_FFMPEG_FIT_SMOKE_OK;encoder={fitted.get('encoder')};fallback={fitted.get('encoder_fallback_used')};face={fitted.get('face_framing', {}).get('status')}")
            from muse.video_contact_sheet import create_contact_sheet
            contact_output = Path(directory) / "contact-sheet.png"
            contact = create_contact_sheet(
                cut_source, contact_output, samples=8, columns=4,
                thumbnail_width=160, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
            )
            if (contact.get("status") != "completed" or contact.get("width") != 680
                    or contact.get("height") != 204 or contact.get("image_decode") != "passed"
                    or not contact.get("visual_review_required")):
                raise SystemExit(f"real FFmpeg contact sheet smoke failed: {contact}")
            print(f"MUSE_FFMPEG_CONTACT_SHEET_SMOKE_OK;samples={contact.get('samples_requested')};dimensions={contact.get('width')}x{contact.get('height')}")
            if "h264_amf" in encoders:
                amf_output = Path(directory) / "amf-encode-smoke.mp4"
                amf_render = subprocess.run(
                    [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                     "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                     "-t", "2", "-an", "-c:v", "h264_amf", "-pix_fmt", "nv12",
                     "-movflags", "+faststart", str(amf_output)],
                    cwd=ROOT, capture_output=True, text=True, timeout=90,
                )
                if amf_render.returncode == 0 and amf_output.is_file():
                    from muse.video_qa import inspect_video_file
                    amf_qa = inspect_video_file(
                        amf_output, ffprobe_path=ffprobe, ffmpeg_path=ffmpeg,
                        min_duration_seconds=0.1, max_duration_seconds=3.0,
                    )
                    if amf_qa.get("status") == "passed":
                        print("MUSE_AMF_ENCODE_SMOKE_OK")
                    else:
                        print("MUSE_AMF_ENCODE_SMOKE_UNVERIFIED:" + json.dumps(amf_qa, ensure_ascii=False))
                else:
                    print("MUSE_AMF_ENCODE_SMOKE_UNAVAILABLE:" + (amf_render.stderr or "")[-600:])
            else:
                print("MUSE_AMF_ENCODE_SMOKE_SKIPPED_NO_ENCODER")
        else:
            print("MUSE_FFMPEG_CUT_SMOKE_SKIPPED_NO_LIBX264")

else:
    print("MUSE_FFMPEG_PROBE_SMOKE_SKIPPED_NO_SOFTWARE_ENCODER")

print("MUSE_FFMPEG_SKILL_CONTRACT_TOML_CLI_OK")
print("MUSE_FFMPEG_SKILL_TESTS_OK")
print("MUSE_FFMPEG_SKILL_VALIDATION_OK")
