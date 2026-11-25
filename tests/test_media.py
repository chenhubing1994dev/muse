from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.media import (
    build_transcode_command,
    discover_media_tools,
    plan_media_task,
    transcode_video,
)


class MediaRegistryTests(unittest.TestCase):
    def test_discovery_reports_installed_tools_and_amf_encoders(self):
        known = {
            "D:/Program Files/obs-studio/bin/64bit/obs64.exe",
            "C:/Program Files/Audacity/audacity.exe",
            "C:/Program Files/PreSonus/Studio One 6/Studio One.exe",
            "D:/software/BoilsoftVideoSplitter/BoilsoftVideoSplitterPortable.exe",
            "D:/software/HDVideoConverterFactoryPro/App/HDVideoConverterFactoryPro/VideoConverterFactoryPro.exe",
            "D:/software/ChatTTS-WebUI/run_webui.bat",            "D:/Tools/MuseTTS/Piper/venv-zh/Scripts/piper.exe",

        }

        def fake_run(args, **kwargs):
            if "-version" in args:
                return SimpleNamespace(stdout="ffmpeg version 8.1.1", stderr="", returncode=0)
            return SimpleNamespace(
                stdout=" V....D h264_amf AMD H.264\n V....D hevc_amf AMD HEVC\n V....D av1_amf AMD AV1\n",
                stderr="",
                returncode=0,
            )

        inventory = discover_media_tools(
            which=lambda name: "D:/ffmpeg/bin/ffmpeg.exe" if name == "ffmpeg" else (
                "D:/ffmpeg/bin/ffprobe.exe" if name == "ffprobe" else None
            ),
            exists=lambda path: str(path).replace("\\", "/") in known,
            runner=fake_run,
        )
        ids = {tool.tool_id for tool in inventory.tools}
        self.assertTrue({"ffmpeg", "ffprobe", "obs", "audacity", "studio_one", "boilsoft", "hd_video_converter", "chattts_webui", "piper_tts"}.issubset(ids))
        self.assertEqual(inventory.ffmpeg_version, "8.1.1")
        self.assertEqual(set(inventory.hardware_encoders), {"h264_amf", "hevc_amf", "av1_amf"})

    def test_task_planner_routes_to_existing_tools_and_marks_missing_nle(self):
        inventory = discover_media_tools(
            which=lambda name: "ffmpeg" if name == "ffmpeg" else None,
            exists=lambda path: str(path).replace("\\", "/") in {
                "D:/Program Files/obs-studio/bin/64bit/obs64.exe",
                "C:/Program Files/Audacity/audacity.exe",
                "C:/Program Files/PreSonus/Studio One 6/Studio One.exe",
                "D:/Tools/MuseTTS/Piper/venv-zh/Scripts/piper.exe",
            },
            runner=lambda args, **kwargs: SimpleNamespace(
                stdout=("ffmpeg version 8.1.1" if "-version" in args else " V....D h264_amf"),
                stderr="", returncode=0,
            ),
        )
        self.assertEqual(plan_media_task("video_capture", inventory)["primary_tool"], "obs")
        self.assertEqual(plan_media_task("audio_edit", inventory)["primary_tool"], "audacity")
        speech = plan_media_task("speech_synthesis", inventory)
        self.assertEqual(speech["primary_tool"], "piper_tts")
        self.assertEqual(speech["status"], "partial")
        self.assertIn("voice model", speech["limitation"])
        nle = plan_media_task("video_timeline_edit", inventory)
        self.assertEqual(nle["status"], "missing")
        self.assertIn("timeline editor", nle["limitation"].lower())


class MediaTranscodeTests(unittest.TestCase):
    def test_builds_amf_command_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"fixture")
            target = root / "output.mp4"
            command = build_transcode_command(
                source, target, ffmpeg_path="ffmpeg.exe",
                codec="h264", available_encoders={"h264_amf"},
            )
            self.assertEqual(command[0], "ffmpeg.exe")
            self.assertIn("-n", command)
            self.assertIn("h264_amf", command)
            self.assertIn("aac", command)
            self.assertEqual(command[-1], str(target))

    def test_dry_run_is_default_and_execute_uses_literal_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"fixture")
            target = root / "output.mp4"
            called = []
            def fake_run(args, **kwargs):
                called.append((args, kwargs))
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            planned = transcode_video(
                source, target, ffmpeg_path="ffmpeg.exe",
                available_encoders={"h264_amf"}, runner=fake_run,
            )
            self.assertEqual(planned["status"], "planned")
            self.assertEqual(called, [])
            with patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"}):
                completed = transcode_video(
                    source, target, ffmpeg_path="ffmpeg.exe",
                    available_encoders={"h264_amf"}, execute=True, runner=fake_run,
                    gpu_resource_db=root / "gpu-resources.sqlite3",
                )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(called), 1)
            self.assertIsInstance(called[0][0], list)
            self.assertFalse(called[0][1].get("shell", False))

    def test_rejects_existing_output_and_unavailable_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"fixture")
            target = root / "output.mp4"
            target.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                build_transcode_command(
                    source, target, ffmpeg_path="ffmpeg.exe",
                    codec="h264", available_encoders={"h264_amf"},
                )
            target.unlink()
            with self.assertRaises(RuntimeError):
                build_transcode_command(
                    source, target, ffmpeg_path="ffmpeg.exe",
                    codec="av1", available_encoders={"h264_amf"},
                )


    def test_execute_amf_transcode_holds_shared_gpu_lease(self):
        from muse.gpu_resources import GpuResourceCoordinator
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mov"
            source.write_bytes(b"fixture")
            target = root / "output.mp4"
            database = root / "gpu-resources.sqlite3"
            def fake_run(args, **kwargs):
                lease = GpuResourceCoordinator(database).active_lease()
                self.assertEqual(lease["owner_kind"], "video_encoding")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"}):
                result = transcode_video(
                    source, target, ffmpeg_path="ffmpeg.exe",
                    available_encoders={"h264_amf"}, execute=True, runner=fake_run,
                    gpu_resource_db=database,
                )
            self.assertEqual(result["status"], "completed")
            self.assertIsNone(GpuResourceCoordinator(database).active_lease())

if __name__ == "__main__":
    unittest.main()
