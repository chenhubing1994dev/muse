from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from muse.adapters import FFmpegTranscodeAdapter


class FakeProcess:
    next_code = 0

    def __init__(self, command, **kwargs):
        self.command = command
        self.returncode = None
        self.terminated = False
        Path(command[-1]).write_bytes(b"verified-media-output")

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        self.returncode = 0 if self.returncode is None else self.returncode
        return "", ""

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        self.returncode = -15 if self.terminated else 0
        return self.returncode

    def kill(self):
        self.returncode = -9


class AdapterFactoryTests(unittest.TestCase):
    def test_factory_uses_only_ready_registered_ffmpeg_capability(self):
        import tempfile
        from muse.capabilities import CapabilityRegistry
        from muse.adapters import create_adapter

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "caps.sqlite3") as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="video", name="FFmpeg",
                    description="Verified FFmpeg", executable="D:/tools/ffmpeg.exe",
                    capabilities=("video_transcode",), status="ready",
                    trust="local_path_discovered", license="not_applicable",
                    metadata={"hardware_encoders": ["h264_amf"]},
                )
                adapter = create_adapter(registry, "media.ffmpeg")
                self.assertIsInstance(adapter, FFmpegTranscodeAdapter)
                self.assertIn("h264_amf", adapter.available_encoders)

    def test_factory_rejects_unverified_or_unknown_adapter(self):
        import tempfile
        from muse.capabilities import CapabilityRegistry
        from muse.adapters import create_adapter

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "caps.sqlite3") as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="video", name="FFmpeg",
                    description="Not verified", executable="D:/tools/ffmpeg.exe",
                    capabilities=("video_transcode",), status="unverified",
                )
                with self.assertRaises(PermissionError):
                    create_adapter(registry, "media.ffmpeg")
                with self.assertRaises(ValueError):
                    create_adapter(registry, "media.unknown")


class FFmpegAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mov"
        self.source.write_bytes(b"source")
        self.output = self.root / "render.mp4"
        self.calls = []

        def popen(command, **kwargs):
            self.calls.append((command, kwargs))
            return FakeProcess(command, **kwargs)

        self.adapter = FFmpegTranscodeAdapter(
            ffmpeg_path="ffmpeg.exe", available_encoders={"h264_amf"}, popen=popen
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_validates_and_never_starts_a_process(self):
        result = self.adapter.preview({"input": str(self.source), "output": str(self.output)})
        self.assertEqual(result["status"], "planned")
        self.assertEqual(self.calls, [])
        self.assertIn("-n", result["command"])

    def test_execution_is_blocked_until_explicit_approval(self):
        result = self.adapter.start({"input": str(self.source), "output": str(self.output)})
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(self.calls, [])

    def test_approved_execution_verifies_and_collects_output_artifact(self):
        from unittest.mock import patch
        with patch("muse.adapters.no_console_window_kwargs", return_value={"creationflags": 123}):
            started = self.adapter.start(
                {"input": str(self.source), "output": str(self.output)}, approved=True
            )
        import subprocess
        self.assertIs(self.calls[0][1]["stdout"], subprocess.DEVNULL)
        self.assertIs(self.calls[0][1]["stderr"], subprocess.DEVNULL)
        self.assertEqual(self.calls[0][1]["creationflags"], 123)
        self.assertEqual(started["status"], "running")
        result = self.adapter.wait(started["job_id"], timeout=5)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifacts"], [str(self.output)])
        self.assertGreater(self.output.stat().st_size, 0)

    def test_cancel_terminates_running_process(self):
        started = self.adapter.start(
            {"input": str(self.source), "output": str(self.output)}, approved=True
        )
        result = self.adapter.cancel(started["job_id"])
        self.assertEqual(result["status"], "cancelled")

    def test_execute_and_verify_expose_the_common_adapter_contract(self):
        started = self.adapter.execute(
            {"input": str(self.source), "output": str(self.output)}, approved=True
        )
        self.assertEqual(started["status"], "running")
        verified = self.adapter.verify(started["job_id"], timeout=5)
        self.assertEqual(verified["status"], "completed")
        self.assertEqual(self.adapter.collect_artifacts(started["job_id"]), [str(self.output)])

    def test_wait_rejects_unknown_job(self):
        with self.assertRaises(KeyError):
            self.adapter.wait("missing-job", timeout=1)


if __name__ == "__main__":
    unittest.main()
