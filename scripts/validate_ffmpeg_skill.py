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

for args in (["-m", "muse.ffmpeg_skill", "contract"], ["-m", "muse.ffmpeg_skill", "--help"], ["-m", "muse.ffmpeg_skill", "cut", "--help"]):
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

for pattern in ("test_ffmpeg_skill.py", "test_ffmpeg_cut.py"):
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
        else:
            print("MUSE_FFMPEG_CUT_SMOKE_SKIPPED_NO_LIBX264")

else:
    print("MUSE_FFMPEG_PROBE_SMOKE_SKIPPED_NO_SOFTWARE_ENCODER")

print("MUSE_FFMPEG_SKILL_CONTRACT_TOML_CLI_OK")
print("MUSE_FFMPEG_SKILL_TESTS_OK")
print("MUSE_FFMPEG_SKILL_VALIDATION_OK")
