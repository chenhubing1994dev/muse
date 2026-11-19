from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from muse.cantonese_tts import (
    CantoneseTTSError,
    _RESULT_MARKER,
    preflight_cantonese_tts_runtime,
    synthesize_cantonese_with_runtime,
)


class CantoneseTTSRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.packages = self.root / ".muse-canto-tts-py314"
        (self.packages / "canto_tts").mkdir(parents=True)
        self.model = self.root / "data" / "models" / "canto-tts-nano"
        self.model.mkdir(parents=True)
        (self.model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
        self.output_dir = self.root / "out"
        self.output_dir.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_preflight_uses_isolated_python314_child(self):
        completed = subprocess.CompletedProcess(
            args=["py"], returncode=0,
            stdout=_RESULT_MARKER + json.dumps({"status": "ready", "python_version": "3.14"}) + "\n",
            stderr="",
        )
        with patch("muse.cantonese_tts._python314_command", return_value=["py", "-3.14"]), \
             patch("muse.cantonese_tts.subprocess.run", return_value=completed) as run:
            result = preflight_cantonese_tts_runtime(self.packages, self.model)
        self.assertEqual(result["status"], "ready")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["py", "-3.14", "-m", "muse.cantonese_tts"])
        self.assertIn("--preflight", command)
        self.assertIn(str(Path(__file__).resolve().parents[1]), run.call_args.kwargs["env"]["PYTHONPATH"])

    def test_synthesis_uses_child_and_returns_only_after_wav_exists(self):
        def fake_run(command, **kwargs):
            if "--preflight" in command:
                result = {"status": "ready", "python_version": "3.14"}
            else:
                output = Path(command[command.index("--output") + 1])
                with wave.open(str(output), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(48_000)
                    audio.writeframes(b"\x00\x00")
                result = {
                    "status": "completed", "engine": "canto-tts-onnx",
                    "language": "zh-HK", "sample_rate": 48_000, "channels": 1,
                    "frames": 1, "duration_seconds": 1 / 48_000,
                    "bytes": output.stat().st_size, "output": str(output),
                }
            return subprocess.CompletedProcess(
                args=command, returncode=0,
                stdout=_RESULT_MARKER + json.dumps(result) + "\n", stderr="",
            )

        output = self.output_dir / "voiceover.wav"
        with patch("muse.cantonese_tts._python314_command", return_value=["py", "-3.14"]), \
             patch("muse.cantonese_tts.subprocess.run", side_effect=fake_run) as run:
            result = synthesize_cantonese_with_runtime(
                "係㗎，呢段係測試旁白。", output,
                model_dir=self.model, packages_dir=self.packages,
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(output.is_file())
        self.assertEqual(run.call_count, 2)
        self.assertTrue(any("--synthesize" in call.args[0] for call in run.call_args_list))

    def test_successful_preflight_is_cached_for_the_same_pinned_runtime(self):
        completed = subprocess.CompletedProcess(
            args=["py"], returncode=0,
            stdout=_RESULT_MARKER + json.dumps({"status": "ready", "python_version": "3.14", "model_loaded": True}),
            stderr="",
        )
        with (
            patch("muse.cantonese_tts._python314_command", return_value=["py", "-3.14"]),
            patch("muse.cantonese_tts.subprocess.run", return_value=completed) as run,
        ):
            first = preflight_cantonese_tts_runtime(self.packages, self.model)
            second = preflight_cantonese_tts_runtime(self.packages, self.model)
        self.assertEqual(first, second)
        run.assert_called_once()

    def test_preflight_rejects_missing_model_without_spawning_child(self):
        missing = self.root / "missing-model"
        with patch("muse.cantonese_tts.subprocess.run") as run:
            with self.assertRaises(CantoneseTTSError):
                preflight_cantonese_tts_runtime(self.packages, missing)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
