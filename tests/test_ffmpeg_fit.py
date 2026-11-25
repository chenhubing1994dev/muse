from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from muse.ffmpeg_skill import build_fit_command, fit_media, plan_fit


class FFmpegFitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "landscape.mp4"
        self.source.write_bytes(b"source-fixture")
        self.output = self.root / "vertical.mp4"
        self._gpu_db_patch = patch.dict("os.environ", {"MUSE_GPU_RESOURCE_DB": str(self.root / "gpu-resources.sqlite3")})
        self._gpu_db_patch.start()
        self.addCleanup(self._gpu_db_patch.stop)
        self._gpu_probe_patch = patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"})
        self._gpu_probe_patch.start()
        self.addCleanup(self._gpu_probe_patch.stop)
        self.source_metadata = {
            "format": {"duration": "2.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }
        self.output_metadata = {
            "format": {"duration": "2.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 180, "height": 320,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def encoder_listing(self, *names):
        return SimpleNamespace(returncode=0, stdout="\n".join(["Encoders:", *[f" V....D {name}" for name in names]]), stderr="")

    def runner(self, command, **kwargs):
        if "-encoders" in command:
            return self.encoder_listing("libx264")
        if Path(command[0]).name.casefold().startswith("ffprobe"):
            payload = self.output_metadata if Path(command[-1]).name == "verified-fit.mp4" else self.source_metadata
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        Path(command[-1]).write_bytes(b"fitted-media")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_cover_uses_manual_focus_and_exact_target_canvas(self):
        command = build_fit_command(self.source, self.output, width=180, height=320,
                                    mode="cover", focus_x=0.65, focus_y=0.35,
                                    fps=25, has_audio=True)
        graph = command[command.index("-vf") + 1]
        self.assertIn("scale=180:320:force_original_aspect_ratio=increase", graph)
        self.assertIn("crop=180:320:x='iw*0.650000-ow/2':y='ih*0.350000-oh/2'", graph)
        self.assertIn("fps=25.000000", graph)
        self.assertIn("-c:a", command)

    def test_contain_adds_letterbox_padding_without_crop(self):
        command = build_fit_command(self.source, self.output, width=180, height=320,
                                    mode="contain", focus_x=0.5, focus_y=0.5,
                                    fps=25, has_audio=False)
        graph = command[command.index("-vf") + 1]
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=180:320", graph)
        self.assertNotIn("crop=", graph)
        self.assertIn("-an", command)

    def test_face_aware_fit_uses_only_confident_detected_center_and_falls_back_safely(self):
        detected = {"status": "detected", "face_center_x": 0.72, "detected_samples": 5, "sampled_frames": 6}
        with patch("muse.video_face_framing.detect_clip_face_center", return_value=detected):
            plan = plan_fit(self.source, self.output, width=180, height=320,
                            focus_x=0.3, face_aware=True, runner=self.runner)
        self.assertEqual(plan["focus"]["x"], 0.72)
        self.assertEqual(plan["requested_focus"]["x"], 0.3)
        self.assertEqual(plan["face_framing"]["status"], "detected")
        graph = plan["command_preview"][plan["command_preview"].index("-vf") + 1]
        self.assertIn("iw*0.720000", graph)

        fallback_output = self.root / "fallback.mp4"
        low_confidence = {"status": "low_confidence", "face_center_x": None, "detected_samples": 1}
        with patch("muse.video_face_framing.detect_clip_face_center", return_value=low_confidence):
            fallback = plan_fit(self.source, fallback_output, width=180, height=320,
                                focus_x=0.3, face_aware=True, runner=self.runner)
        self.assertEqual(fallback["focus"]["x"], 0.3)
        self.assertEqual(fallback["face_framing"]["fallback"], "manual_focus")
        self.assertTrue(any("manual focus retained" in item for item in fallback["warnings"]))

    def test_plan_is_dry_and_marks_visual_review_required(self):
        plan = plan_fit(self.source, self.output, width=180, height=320, runner=self.runner)
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["visual_review_required"])
        self.assertFalse(plan["writes_files"])
        self.assertFalse(self.output.exists())

    def test_vfr_requires_explicit_output_frame_rate(self):
        self.source_metadata["streams"][0]["avg_frame_rate"] = "24/1"
        with self.assertRaisesRegex(ValueError, "explicit target fps"):
            plan_fit(self.source, self.output, width=180, height=320, runner=self.runner)
        plan = plan_fit(self.source, self.output, width=180, height=320, fps=25, runner=self.runner)
        self.assertEqual(plan["fps"], 25)

    def test_hdr_is_blocked_until_color_delivery_is_explicit(self):
        self.source_metadata["streams"][0]["color_transfer"] = "smpte2084"
        with self.assertRaisesRegex(ValueError, "HDR"):
            plan_fit(self.source, self.output, width=180, height=320, runner=self.runner)

    def test_execution_checks_dimensions_duration_and_full_decode_before_publish(self):
        result = fit_media(self.source, self.output, width=180, height=320, runner=self.runner,
                           qa_inspector=lambda path: {"status": "passed"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["width"], result["height"]), (180, 320))
        self.assertTrue(result["visual_review_required"])
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(self.output.read_bytes(), b"fitted-media")

    def test_failed_qa_never_publishes(self):
        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            fit_media(self.source, self.output, width=180, height=320, runner=self.runner,
                      qa_inspector=lambda path: {"status": "failed"})
        self.assertFalse(self.output.exists())

    def test_amf_failure_falls_back_to_software(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("h264_amf", "libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                payload = self.output_metadata if Path(command[-1]).name == "verified-fit.mp4" else self.source_metadata
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            if "h264_amf" in command:
                Path(command[-1]).write_bytes(b"partial")
                return SimpleNamespace(returncode=1, stdout="", stderr="AMF unavailable")
            Path(command[-1]).write_bytes(b"software-fit")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = fit_media(self.source, self.output, width=180, height=320, runner=runner,
                           qa_inspector=lambda path: {"status": "passed"})
        self.assertEqual(result["encoder"], "libx264")
        self.assertTrue(result["encoder_fallback_used"])
        self.assertEqual(self.output.read_bytes(), b"software-fit")


if __name__ == "__main__":
    unittest.main()
