from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.ffmpeg_skill import build_join_command, join_media, plan_join


class FFmpegJoinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = [self.root / "one.mp4", self.root / "two.mp4"]
        for source in self.sources:
            source.write_bytes(b"source-fixture")
        self.output = self.root / "joined.mp4"
        self._gpu_db_patch = patch.dict("os.environ", {"MUSE_GPU_RESOURCE_DB": str(self.root / "gpu-resources.sqlite3")})
        self._gpu_db_patch.start()
        self.addCleanup(self._gpu_db_patch.stop)
        self._gpu_probe_patch = patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"})
        self._gpu_probe_patch.start()
        self.addCleanup(self._gpu_probe_patch.stop)
        self.metadata = {
            "format": {"duration": "2.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }
        self.output_metadata = {
            "format": {"duration": "4.0", "format_name": "mov,mp4"},

            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180,
                 "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }

        self.second_metadata = json.loads(json.dumps(self.metadata))

    def tearDown(self):
        self.temp.cleanup()

    def encoder_listing(self, *names):
        return SimpleNamespace(returncode=0, stdout="\n".join(["Encoders:", *[f" V....D {name}" for name in names]]), stderr="")

    def runner(self, command, **kwargs):
        if "-encoders" in command:
            return self.encoder_listing("libx264")
        if Path(command[0]).name.casefold().startswith("ffprobe"):
            if Path(command[-1]).name == "verified-join.mp4":
                payload = self.output_metadata
            elif Path(command[-1]).name == "two.mp4":
                payload = self.second_metadata
            else:
                payload = self.metadata
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        Path(command[-1]).write_bytes(b"joined-media")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_command_builds_fixed_concat_graph_and_maps_audio(self):
        command = build_join_command(self.sources, self.output, has_audio=True)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(graph, "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[vout][aout]")
        self.assertIn("[vout]", command)
        self.assertIn("[aout]", command)
        self.assertIn("-nostdin", command)
        self.assertIn("-n", command)

    def test_plan_is_probe_first_and_dry_run(self):
        plan = plan_join(self.sources, self.output, runner=self.runner)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["source_count"], 2)
        self.assertEqual(plan["expected_duration_seconds"], 4.0)
        self.assertFalse(plan["writes_files"])
        self.assertFalse(self.output.exists())

    def test_rejects_mismatched_dimensions(self):
        self.second_metadata["streams"][0]["width"] = 640
        with self.assertRaisesRegex(ValueError, "match dimensions"):
            plan_join(self.sources, self.output, runner=self.runner)

    def test_execution_publishes_only_after_probe_and_qa(self):
        qa_calls = []
        def qa(path):
            qa_calls.append(Path(path))
            return {"status": "passed"}
        result = join_media(self.sources, self.output, runner=self.runner, qa_inspector=qa)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["duration_seconds"], 4.0)
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(self.output.read_bytes(), b"joined-media")
        self.assertEqual(len(qa_calls), 1)

    def test_failed_qa_never_publishes(self):
        with self.assertRaisesRegex(RuntimeError, "QA failed"):
            join_media(self.sources, self.output, runner=self.runner,
                       qa_inspector=lambda path: {"status": "failed"})
        self.assertFalse(self.output.exists())

    def test_amf_failure_falls_back_to_software(self):
        def runner(command, **kwargs):
            if "-encoders" in command:
                return self.encoder_listing("h264_amf", "libx264")
            if Path(command[0]).name.casefold().startswith("ffprobe"):
                if Path(command[-1]).name == "verified-join.mp4":
                    payload = self.output_metadata
                elif Path(command[-1]).name == "two.mp4":
                    payload = self.second_metadata
                else:
                    payload = self.metadata
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            if "h264_amf" in command:
                Path(command[-1]).write_bytes(b"partial")
                return SimpleNamespace(returncode=1, stdout="", stderr="AMF unavailable")
            Path(command[-1]).write_bytes(b"software-joined")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = join_media(self.sources, self.output, runner=runner,
                            qa_inspector=lambda path: {"status": "passed"})
        self.assertEqual(result["encoder"], "libx264")
        self.assertTrue(result["encoder_fallback_used"])
        self.assertEqual(self.output.read_bytes(), b"software-joined")


if __name__ == "__main__":
    unittest.main()
