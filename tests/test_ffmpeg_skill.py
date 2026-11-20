from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from muse.ffmpeg_skill import (
    TOOL_CATALOG,
    build_contract,
    inspect_ffmpeg_capabilities,
    probe_media,
)


class FFmpegSkillContractTests(unittest.TestCase):
    def test_catalog_covers_upstream_42_tools_without_claiming_all_are_native(self):
        contract = build_contract()
        self.assertEqual(len(contract["tools"]), 42)
        self.assertEqual(len({item["name"] for item in contract["tools"]}), 42)
        self.assertEqual(contract["upstream"]["license"], "MIT")
        self.assertTrue(any(item["muse_support"] == "planned" for item in contract["tools"]))
        self.assertTrue(all(item["mutates_input"] is False for item in contract["tools"]))

    def test_failed_capability_listing_is_unknown_not_missing(self):
        def runner(args, **kwargs):
            if "-version" in args:
                return SimpleNamespace(returncode=0, stdout="ffmpeg version 8.1.1", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="unsupported listing")
        report = inspect_ffmpeg_capabilities(
            ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", runner=runner,
        )
        self.assertEqual(report["components"]["filters"]["status"], "unknown")
        self.assertEqual(report["tools"]["caption"]["readiness"], "unknown")
        self.assertEqual(report["executables"]["ffmpeg"]["status"], "available")

    def test_capability_detection_reports_real_missing_filter(self):
        def runner(args, **kwargs):
            if "-version" in args:
                return SimpleNamespace(returncode=0, stdout="ffmpeg version 8.1.1", stderr="")
            flag = args[1]
            if flag == "-filters":
                text = "Filters:\n ... scale\n ... crop\n"
            elif flag == "-encoders":
                text = "Encoders:\n V....D h264_amf\n V....D libx264\n"
            elif flag == "-decoders":
                text = "Decoders:\n V....D h264\n"
            else:
                text = "Bitstream filters:\naac_adtstoasc\n"
            return SimpleNamespace(returncode=0, stdout=text, stderr="")
        report = inspect_ffmpeg_capabilities(
            ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", runner=runner,
        )
        self.assertEqual(report["components"]["filters"]["status"], "available")
        self.assertEqual(report["tools"]["fit"]["readiness"], "available")
        self.assertEqual(report["tools"]["caption"]["readiness"], "missing")
        self.assertIn("filter:subtitles", report["tools"]["caption"]["missing"])
        self.assertIn("h264_amf", report["hardware_encoders"])
        self.assertEqual(report["components"]["bsfs"]["items"], ["aac_adtstoasc"])
    def test_missing_ffmpeg_is_missing_not_unknown(self):
        report = inspect_ffmpeg_capabilities(which=lambda name: None)
        self.assertEqual(report["executables"]["ffmpeg"]["status"], "missing")
        self.assertEqual(report["tools"]["cut"]["readiness"], "missing")


class FFmpegProbeTests(unittest.TestCase):
    def test_probe_returns_measured_media_facts_and_vfr_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"fixture")
            payload = {
                "format": {"duration": "12.5", "format_name": "mov,mp4"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                     "avg_frame_rate": "25/1", "r_frame_rate": "30/1",
                     "pix_fmt": "yuv420p", "color_transfer": "smpte2084"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                     "channels": 2, "channel_layout": "stereo"},
                ],
                "chapters": [],
            }
            calls = []
            def runner(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            result = probe_media(source, ffprobe_path="ffprobe", runner=runner)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["duration_seconds"], 12.5)
            self.assertTrue(result["video"]["variable_frame_rate_suspected"])
            self.assertTrue(result["editing_guidance"]["hdr_source_requires_explicit_delivery_decision"])
            self.assertEqual(result["audio"][0]["channel_layout"], "stereo")
            self.assertIn("-show_streams", calls[0])

    def test_probe_blocks_missing_ffprobe_and_rejects_symlinks_or_empty_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"fixture")
            self.assertEqual(probe_media(source, which=lambda name: None)["status"], "blocked")
            empty = Path(directory) / "empty.mp4"
            empty.touch()
            with self.assertRaisesRegex(ValueError, "non-empty"):
                probe_media(empty, ffprobe_path="ffprobe")


if __name__ == "__main__":
    unittest.main()
