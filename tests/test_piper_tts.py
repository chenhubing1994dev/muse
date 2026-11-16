from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from muse.piper_tts import PiperTtsError, synthesize_to_wav, synthesize_with_cli


class PiperTtsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "voice.onnx"
        self.config = self.root / "voice.onnx.json"
        self.model.write_bytes(b"fixture model")
        self.config.write_text("{}", encoding="utf-8")
        self.output = self.root / "voice.wav"

    def tearDown(self):
        self.temp.cleanup()

    def test_synthesizes_existing_model_to_valid_pcm_wav(self):
        chunks = [SimpleNamespace(sample_rate=22050, sample_width=2, sample_channels=1,
                                  audio_float_array=[0.0, 0.25, -0.25, 0.5])]
        voice = SimpleNamespace(synthesize=Mock(return_value=iter(chunks)))
        loader = Mock(return_value=voice)
        result = synthesize_to_wav("你好，Muse。", self.model, self.output,
                                   config_path=self.config, voice_loader=loader)
        loader.assert_called_once_with(self.model.resolve(), config_path=self.config.resolve(), use_cuda=False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sample_rate"], 22050)
        with wave.open(str(self.output), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 22050)
            self.assertEqual(wav_file.getnframes(), 4)

    def test_missing_voice_assets_fail_without_downloading(self):
        with self.assertRaisesRegex(PiperTtsError, "No model download"):
            synthesize_to_wav("你好", self.root / "missing.onnx", self.output)
        self.assertFalse(self.output.exists())

    def test_refuses_overwrite(self):
        self.output.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            synthesize_to_wav("你好", self.model, self.output,
                              config_path=self.config, voice_loader=Mock())
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_rejects_empty_text_and_unsupported_audio(self):
        with self.assertRaisesRegex(ValueError, "text"):
            synthesize_to_wav("  ", self.model, self.output, config_path=self.config)
        voice = SimpleNamespace(synthesize=lambda text: iter([
            SimpleNamespace(sample_rate=22050, sample_width=4, sample_channels=1,
                            audio_float_array=[0.1])]))
        with self.assertRaisesRegex(PiperTtsError, "unsupported Piper PCM"):
            synthesize_to_wav("你好", self.model, self.output,
                              config_path=self.config, voice_loader=Mock(return_value=voice))


    def _write_valid_wav(self, path: Path):
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(bytes(2 * 2205))
    def test_cli_runs_literal_argv_and_validates_wav(self):
        def fake_runner(command, **kwargs):
            self._write_valid_wav(self.output)
            self.assertEqual(command[1:7], ["-m", str(self.model.resolve()), "-c", str(self.config.resolve()), "-i", command[6]])
            self.assertEqual(command[7:], ["-f", str(self.output.resolve())])
            self.assertFalse(kwargs.get("shell", False))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = synthesize_with_cli(
            "你好，Muse。", self.model, self.output, executable=r"D:\\Piper\\piper.exe",
            config_path=self.config, runner=fake_runner,
        )
        self.assertEqual(result["engine"], "Piper CLI")
        self.assertEqual(result["duration_seconds"], 0.1)
        self.assertEqual(result["frames"], 2205)
        with wave.open(str(self.output), "rb") as wav_file:
            self.assertEqual(wav_file.getnframes(), 2205)

    def test_cli_nonzero_exit_cleans_partial_output(self):
        def fake_runner(command, **kwargs):
            self.output.write_bytes(b"partial")
            return SimpleNamespace(returncode=2, stdout="", stderr="model load failed")

        with self.assertRaisesRegex(PiperTtsError, "model load failed"):
            synthesize_with_cli("你好", self.model, self.output, executable="piper", runner=fake_runner)
        self.assertFalse(self.output.exists())

    def test_cli_rejects_invalid_wav_and_cleans_it(self):
        def fake_runner(command, **kwargs):
            self.output.write_bytes(b"not a wav")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(PiperTtsError, "not a readable WAV"):
            synthesize_with_cli("你好", self.model, self.output, executable="piper", runner=fake_runner)
        self.assertFalse(self.output.exists())

    def test_cli_missing_assets_does_not_spawn(self):
        runner = Mock()
        with self.assertRaisesRegex(PiperTtsError, "No model download"):
            synthesize_with_cli("你好", self.root / "missing.onnx", self.output, executable="piper", runner=runner)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
