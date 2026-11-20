from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from muse.video_audio_mix import build_bgm_envelope_expression

ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    print("MUSE_BGM_VIDEO_MUX_FFMPEG_SKIPPED_NO_FFMPEG")
    raise SystemExit(0)

expression = build_bgm_envelope_expression(
    [(0.5, 1.2), (1.3, 2.4)],
    3.0,
    base_gain=0.22,
    duck_gain=0.10,
    attack_ms=80,
    release_ms=450,
    fade_out_seconds=1.5,
)
filter_graph = (
    "[1:a]aresample=48000,apad,volume=2.0,asplit=2[voice_sc][voice_mix];"
    f"[2:a]aresample=48000,volume='{expression}':eval=frame[music];"
    "[music][voice_sc]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=450[ducked];"
    "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2[mixed];"
    "[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
)
with tempfile.TemporaryDirectory(prefix="muse-bgm-video-smoke-") as temporary_dir:
    output = Path(temporary_dir) / "voice-aware-mix.mp4"
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-n",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=24:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
            "-filter_complex", filter_graph, "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 12:
        raise SystemExit(f"FFmpeg voice-aware MP4 mux failed: {result.stderr[-2000:]}")
    with output.open("rb") as stream:
        if stream.read(12)[4:8] != b"ftyp":
            raise SystemExit("FFmpeg voice-aware mix output is not an MP4 container")
print("MUSE_BGM_VIDEO_MUX_FFMPEG_OK")
