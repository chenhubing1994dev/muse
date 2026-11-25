from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.video_capabilities import probe_ffmpeg_encoder, probe_video_services


class VideoCapabilityTests(unittest.TestCase):
    def test_encoder_probe_requires_real_encode_probe_and_full_decode(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ffmpeg" and "-frames:v" in command:
                Path(command[-1]).write_bytes(b"mp4 fixture")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if command[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "format": {"duration": "1.0"},
                    "streams": [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 320}],
                }), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = probe_ffmpeg_encoder("ffmpeg", "ffprobe", "libx264", runner=runner)
        self.assertEqual(result["health"]["status"], "healthy")
        self.assertEqual(result["capability"]["status"], "available")
        self.assertEqual(len(calls), 3)
        self.assertIn("libx264", calls[0])
        self.assertIn("-show_entries", calls[1])
        self.assertEqual(calls[2][-3:], ["-f", "null", "-"])

    def test_encoder_probe_rejects_advertised_but_nonfunctional_encoder(self):
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="encoder init failed")
        result = probe_ffmpeg_encoder("ffmpeg", "ffprobe", "h264_amf", runner=runner)
        self.assertEqual(result["health"]["status"], "healthy")
        self.assertEqual(result["capability"]["status"], "unavailable")
        self.assertEqual(result["capability"]["stage"], "encode")

    def test_service_health_and_capability_are_independent(self):
        from muse import video_engines
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workflows").mkdir()
            (root / "workflows" / "wan21_t2v_smoke.json").write_text(json.dumps({
                "1": {"class_type": "KSampler", "inputs": {"model": "wan.safetensors"}},
            }), encoding="utf-8")
            models = root / "models"
            models.mkdir()
            (models / "wan.safetensors").write_bytes(b"model fixture")
            fake = type("Comfy", (), {
                "health": lambda self: {"status": "ready", "running_jobs": 0, "pending_jobs": 0},
                "object_info_summary": lambda self, nodes: {
                    "available_required_nodes": ["KSampler"],
                    "missing_required_nodes": ["VAEDecode"] if "VAEDecode" in nodes else [],
                },
            })()
            with patch.object(video_engines, "ComfyUIClient", return_value=fake), \
                 patch("muse.video_capabilities.shutil.which", return_value=None), \
                 patch("muse.video_capabilities.importlib.util.find_spec", return_value=None), \
                 patch.dict(os.environ, {"MUSE_COMFYUI_MODELS_DIR": str(models)}):
                result = probe_video_services(root)
        self.assertEqual(result["comfyui"]["health"]["status"], "healthy")
        self.assertEqual(result["comfyui"]["capability"]["status"], "unavailable")
        self.assertFalse(result["comfyui"]["ready"])
        self.assertEqual(result["wan"]["health"]["status"], "healthy")
        self.assertEqual(result["wan"]["capability"]["status"], "available")
        self.assertEqual(result["tts"]["health"]["status"], "unavailable")
        self.assertEqual(result["asr"]["health"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
