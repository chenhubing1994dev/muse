from __future__ import annotations
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from muse.video_contact_sheet import create_contact_sheet

ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    raise SystemExit("SKIP: ffmpeg/ffprobe unavailable")
with tempfile.TemporaryDirectory(prefix="muse-contact-smoke-") as folder:
    source = pathlib.Path(folder) / "synthetic.mp4"
    output = pathlib.Path(folder) / "contact.png"
    made = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                           "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=12",
                           "-t", "2", "-pix_fmt", "yuv420p", "-y", str(source)],
                          capture_output=True, text=True, timeout=60, check=False)
    if made.returncode or not source.is_file():
        raise SystemExit("synthetic source creation failed: " + made.stderr[-1000:])
    result = create_contact_sheet(source, output, samples=8, columns=4,
                                  ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    if result.get("status") != "completed" or result.get("width", 0) <= 0 or not output.is_file():
        raise SystemExit("contact sheet smoke failed: " + repr(result))
    print("MUSE_CONTACT_SHEET_SMOKE_OK", result)
