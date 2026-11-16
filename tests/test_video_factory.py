from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from muse.video_factory import assemble_video_from_folder, discover_video_assets


class VideoFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "02-scene.png").write_bytes(b"image")
        (self.assets / "01-opening.mp4").write_bytes(b"video")
        (self.assets / "voice.wav").write_bytes(b"audio")
        (self.assets / "notes.txt").write_text("ignore", encoding="utf-8")
        self.output = self.root / "finished.mp4"

    def tearDown(self):
        self.temp.cleanup()

    def test_discovers_supported_assets_in_natural_order_and_excludes_audio(self):
        result = discover_video_assets(self.assets)
        self.assertEqual([item.name for item in result], ["01-opening.mp4", "02-scene.png"])

    def test_rejects_empty_asset_folder_and_existing_output(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            discover_video_assets(empty)
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            assemble_video_from_folder(self.assets, self.output, ffmpeg_path="ffmpeg.exe")

    def test_builds_normalized_scenes_concat_and_audio_mux_then_verifies(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            if "-show_entries" in command:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"format":{"duration":"3.5"},"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"30/1"},{"codec_type":"audio"}]}), stderr="")
            Path(command[-1]).write_bytes(b"rendered")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = assemble_video_from_folder(
            self.assets, self.output, ffmpeg_path="ffmpeg.exe", available_encoders={"h264_amf"},
            audio_path=self.assets / "voice.wav", audio_mode="narration", runner=runner,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["scene_count"], 2)
        self.assertEqual(result["duration_seconds"], 3.5)
        self.assertTrue(self.output.is_file())
        self.assertEqual(len(calls), 4)  # two scenes, concat/mux, ffprobe
        self.assertIn("h264_amf", calls[0])
        self.assertIn("-stream_loop", calls[2]) if "-stream_loop" in calls[2] else self.assertIn("-af", calls[2])
        self.assertTrue(all(isinstance(call, list) for call in calls))

    def test_fails_closed_when_ffmpeg_scene_render_fails(self):
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="fixture failure")
        result = assemble_video_from_folder(
            self.assets, self.output, ffmpeg_path="ffmpeg.exe", runner=runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.output.exists())


    def test_ffprobe_rejects_wrong_dimensions_and_frame_rate(self):
        def runner(command, **kwargs):
            if "-show_entries" in command:
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "format": {"duration": "1.0"},
                    "streams": [{"codec_type": "video", "width": 1280, "height": 720,
                                 "avg_frame_rate": "24/1"}],
                }), stderr="")
            Path(command[-1]).write_bytes(b"rendered")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = assemble_video_from_folder(
            self.assets, self.output, ffmpeg_path="ffmpeg.exe", runner=runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.output.exists())

    def test_ffprobe_requires_audio_when_audio_was_requested(self):
        def runner(command, **kwargs):
            if "-show_entries" in command:
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "format": {"duration": "1.0"},
                    "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                                 "avg_frame_rate": "30/1"}],
                }), stderr="")
            Path(command[-1]).write_bytes(b"rendered")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = assemble_video_from_folder(
            self.assets, self.output, ffmpeg_path="ffmpeg.exe",
            audio_path=self.assets / "voice.wav", runner=runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.output.exists())

    def test_software_encoder_can_be_selected_even_when_amf_is_available(self):
        def runner(command, **kwargs):
            if "-show_entries" in command:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"format":{"duration":"1.0"},"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"30/1"}]}), stderr="")
            Path(command[-1]).write_bytes(b"rendered")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result = assemble_video_from_folder(
            self.assets, self.output, ffmpeg_path="ffmpeg.exe", available_encoders={"h264_amf"},
            encoder_preference="software", runner=runner,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["encoder"], "libx264")

    def test_amf_mode_rejects_missing_hardware_encoder(self):
        with self.assertRaises(ValueError):
            assemble_video_from_folder(
                self.assets, self.output, ffmpeg_path="ffmpeg.exe",
                encoder_preference="amf", available_encoders=(),
            )

if __name__ == "__main__":
    unittest.main()
