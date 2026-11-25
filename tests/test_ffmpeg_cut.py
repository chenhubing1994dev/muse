from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.ffmpeg_skill import build_cut_command, cut_media, plan_cut


class FFmpegCutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source-fixture")
        self.output = self.root / "cut.mp4"
        self._gpu_db_patch = patch.dict("os.environ", {"MUSE_GPU_RESOURCE_DB": str(self.root / "gpu-resources.sqlite3")})
        self._gpu_db_patch.start()
        self.addCleanup(self._gpu_db_patch.stop)
        self._gpu_probe_patch = patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"})
        self._gpu_probe_patch.start()
        self.addCleanup(self._gpu_probe_patch.stop)
        self.source_metadata = {
            "format": {"duration": "12.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }
        self.output_metadata = {
            "format": {"duration": "2.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def probe_runner(self, command, **kwargs):
        payload = self.source_metadata if Path(command[-1]).name == self.source.name else self.output_metadata
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    def encoder_listing(self, *names):
        rows = ["Encoders:"] + [f" V....D {name}" for name in names]
        return SimpleNamespace(returncode=0, stdout="\n".join(rows), stderr="")

    def test_accurate_and_copy_commands_use_literal_argv_without_filtergraph(self):
        accurate = build_cut_command(self.source, self.output, 1.25, 3.25, mode="accurate")
        amf = build_cut_command(self.source, self.output, 1.25, 3.25, mode="accurate", video_encoder="h264_amf")
        copy = build_cut_command(self.source, self.output, 1.25, 3.25, mode="copy")
        self.assertEqual(accurate[accurate.index("-i") + 1], str(self.source))
        self.assertGreater(accurate.index("-ss"), accurate.index("-i"))
        self.assertLess(copy.index("-ss"), copy.index("-i"))
        self.assertIn("-c:v", accurate)
        self.assertIn("h264_amf", amf)
        self.assertIn("nv12", amf)
        self.assertIn("copy", copy)
        self.assertFalse(any("filter_complex" in item or item == "-vf" for item in accurate))
        self.assertIn("-nostdin", accurate)
        self.assertIn("-n", accurate)

    def test_auto_prefers_amf_when_encoder_is_advertised(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("h264_amf", "libx264")
            return self.probe_runner(command, **kwargs)

        plan = plan_cut(self.source, self.output, 1, 3, runner=runner)
        self.assertEqual(plan["encoder_requested"], "auto")
        self.assertEqual(plan["encoder_selected"], "h264_amf")
        self.assertIn("h264_amf", plan["command_preview"])

    def test_explicit_amf_requires_encoder_to_be_advertised(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            return self.probe_runner(command, **kwargs)

        with self.assertRaisesRegex(ValueError, "h264_amf was not reported"):
            plan_cut(self.source, self.output, 1, 3, encoder="h264_amf", runner=runner)

    def test_plan_probes_and_returns_dry_run_without_creating_output(self):
        plan = plan_cut(self.source, self.output, 1, 3, runner=self.probe_runner)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["precision"], "frame-accurate-decode-and-reencode")
        self.assertFalse(plan["writes_files"])
        self.assertFalse(self.output.exists())

    def test_plan_warns_that_stream_copy_is_keyframe_aligned(self):
        plan = plan_cut(self.source, self.output, 1, 3, mode="copy", runner=self.probe_runner)
        self.assertIn("stream-copy seeks may snap to a nearby keyframe", plan["warnings"])
        self.assertEqual(plan["precision"], "fast-keyframe-aligned-stream-copy")

    def test_hdr_requires_explicit_stream_copy_and_warns_about_no_tone_mapping(self):
        self.source_metadata["streams"][0]["color_transfer"] = "smpte2084"
        with self.assertRaisesRegex(ValueError, "HDR source"):
            plan_cut(self.source, self.output, 1, 3, runner=self.probe_runner)
        plan = plan_cut(self.source, self.output, 1, 3, mode="copy", runner=self.probe_runner)
        self.assertTrue(any("HDR source is stream-copied" in warning for warning in plan["warnings"]))

    def test_rejects_same_path_existing_output_and_out_of_range_before_ffmpeg(self):
        with self.assertRaisesRegex(ValueError, "different files"):
            plan_cut(self.source, self.source, 1, 3, runner=self.probe_runner)
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            plan_cut(self.source, self.output, 1, 3, runner=self.probe_runner)
        self.output.unlink()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            plan_cut(self.source, self.output, 10, 13, runner=self.probe_runner)

    def test_rejects_boolean_timestamps(self):
        with self.assertRaisesRegex(ValueError, "finite number"):
            plan_cut(self.source, self.output, True, 3, runner=self.probe_runner)

    def test_execution_publishes_only_after_probe_and_qa(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            Path(command[-1]).write_bytes(b"rendered-cut")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        qa_calls = []
        def qa_inspector(path):
            qa_calls.append(Path(path))
            self.assertTrue(Path(path).is_file())
            return {"status": "passed", "checks": {"full_decode": {"passed": True}}}

        original = self.source.read_bytes()
        result = cut_media(
            self.source, self.output, 1, 3, runner=runner, qa_inspector=qa_inspector,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), b"rendered-cut")
        self.assertEqual(len(qa_calls), 1)
        self.assertEqual([Path(item[0]).name.casefold().split(".")[0] for item in calls], ["ffprobe", "ffmpeg", "ffmpeg", "ffprobe"])
        self.assertIn("-encoders", calls[1])
        self.assertIn("libx264", calls[2])
        self.assertFalse(any(path.name.startswith(".muse-cut-") for path in self.root.iterdir()))

    def test_timeout_discards_partial_temp_output_and_never_creates_target(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            Path(command[-1]).write_bytes(b"partial")
            raise subprocess.TimeoutExpired(command, 1)

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            cut_media(self.source, self.output, 1, 3, runner=runner, qa_inspector=lambda path: {"status": "passed"})
        self.assertFalse(self.output.exists())
        self.assertFalse(any(path.name.startswith(".muse-cut-") for path in self.root.iterdir()))

    def test_source_change_during_render_prevents_publication(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            Path(command[-1]).write_bytes(b"rendered-cut")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def qa_inspector(path):
            self.source.write_bytes(b"changed-source")
            return {"status": "passed"}

        with self.assertRaisesRegex(RuntimeError, "source file changed"):
            cut_media(self.source, self.output, 1, 3, runner=runner, qa_inspector=qa_inspector)
        self.assertFalse(self.output.exists())

    def test_failed_qa_never_publishes_output(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            Path(command[-1]).write_bytes(b"rendered-cut")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            cut_media(
                self.source, self.output, 1, 3, runner=runner,
                qa_inspector=lambda path: {"status": "failed"},
            )
        self.assertFalse(self.output.exists())

    def test_auto_falls_back_to_libx264_when_amf_process_fails(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("h264_amf", "libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            if "h264_amf" in command:
                from muse.gpu_resources import GpuResourceCoordinator
                active = GpuResourceCoordinator(self.root / "gpu-resources.sqlite3").active_lease()
                self.assertEqual(active["owner_kind"], "video_encoding")
                Path(command[-1]).write_bytes(b"partial-amf")
                return SimpleNamespace(returncode=1, stdout="", stderr="AMF device unavailable")
            Path(command[-1]).write_bytes(b"software-cut")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = cut_media(
            self.source, self.output, 1, 3, runner=runner,
            qa_inspector=lambda path: {"status": "passed"},
        )
        self.assertEqual(result["encoder"], "libx264")
        self.assertTrue(result["encoder_fallback_used"])
        self.assertEqual(self.output.read_bytes(), b"software-cut")

    def test_destination_created_during_qa_is_never_overwritten(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                return self.probe_runner(command, **kwargs)
            Path(command[-1]).write_bytes(b"rendered-cut")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def qa_inspector(path):
            self.output.write_bytes(b"racing-writer")
            return {"status": "passed"}

        with self.assertRaises(FileExistsError):
            cut_media(self.source, self.output, 1, 3, runner=runner, qa_inspector=qa_inspector)
        self.assertEqual(self.output.read_bytes(), b"racing-writer")


if __name__ == "__main__":
    unittest.main()
