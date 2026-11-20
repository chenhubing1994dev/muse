from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from muse.chimera_vocal import ChimeraVocalError, main, render_chimera_song, render_chimera_voice


class ChimeraVocalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cli = self.root / "chimera.exe"
        self.cli.write_bytes(b"test executable placeholder")
        self.output = self.root / "voice.wav"

    def tearDown(self):
        self.temp.cleanup()

    def _write_wav(self, path: Path, *, rate: int = 48_000, frames: int = 480):
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(b"\x00\x00" * frames)

    def test_english_text_invokes_native_cli_without_shell_and_returns_provenance(self):
        def fake_runner(command, **kwargs):
            self._write_wav(self.output)
            return subprocess.CompletedProcess(command, 0, "rendered", "")

        with patch("muse.chimera_vocal.no_console_window_kwargs", return_value={"creationflags": 7}):
            result = render_chimera_voice(
                "Hello, Muse.", self.output, cli_path=self.cli, runner=fake_runner
            )

        self.assertEqual(result["engine"], "chimera-vocal-synth")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["sample_rate"], 48_000)
        self.assertEqual(result["frames"], 480)
        self.assertEqual(result["sha256"], __import__("hashlib").sha256(self.output.read_bytes()).hexdigest())

    def test_ipa_mode_uses_speech_command_and_explicit_female_body(self):
        observed = {}

        def fake_runner(command, **kwargs):
            observed["command"] = command
            observed["kwargs"] = kwargs
            self._write_wav(self.output)
            return subprocess.CompletedProcess(command, 0, "", "")

        result = render_chimera_voice(
            "həˈloʊ", self.output, mode="ipa", body="female",
            cli_path=self.cli, runner=fake_runner,
        )

        self.assertEqual(observed["command"][1:3], ["render", "speech"])
        self.assertEqual(observed["command"][-1], "female")
        self.assertNotIn("shell", observed["kwargs"])
        self.assertFalse(result["cantonese_supported"])

    def test_rejects_missing_cli_and_never_overwrites_existing_output(self):
        with self.assertRaises(ChimeraVocalError):
            render_chimera_voice("hello", self.output, cli_path=self.root / "missing.exe")
        self.output.write_bytes(b"creator asset")
        with self.assertRaises(ChimeraVocalError):
            render_chimera_voice("hello", self.output, cli_path=self.cli)
        self.assertEqual(self.output.read_bytes(), b"creator asset")

    def test_rejects_invalid_body_mode_and_empty_text(self):
        with self.assertRaises(ValueError):
            render_chimera_voice(" ", self.output, cli_path=self.cli)
        with self.assertRaises(ValueError):
            render_chimera_voice("hello", self.output, mode="cantonese", cli_path=self.cli)
        with self.assertRaises(ValueError):
            render_chimera_voice("hello", self.output, body="robot", cli_path=self.cli)

    def test_ipa_warning_about_skipped_phonemes_fails_closed(self):
        def fake_runner(command, **kwargs):
            self._write_wav(self.output)
            return subprocess.CompletedProcess(command, 0, "", "warning: unsupported IPA symbol")

        with self.assertRaises(ChimeraVocalError):
            render_chimera_voice("ɧ", self.output, mode="ipa", cli_path=self.cli, runner=fake_runner)
        self.assertFalse(self.output.exists())

    def test_bad_sample_rate_removes_only_the_new_invalid_output(self):
        def fake_runner(command, **kwargs):
            self._write_wav(self.output, rate=44_100)
            return subprocess.CompletedProcess(command, 0, "", "")

        with self.assertRaises(ChimeraVocalError):
            render_chimera_voice("hello", self.output, cli_path=self.cli, runner=fake_runner)
        self.assertFalse(self.output.exists())

    def test_custom_song_preflights_syllables_and_renders_notes_without_shell(self):
        observed = []
        parsed = [{"text": "hello"}, {"text": "muse"}]

        def fake_runner(command, **kwargs):
            observed.append((command, kwargs))
            if command[1] == "syllables":
                return subprocess.CompletedProcess(command, 0, json.dumps(parsed), "")
            melody_file = Path(command[3])
            payload = json.loads(melody_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["notes"], [[60.0, 1.0], [62.0, 2.0], [0.0, 0.5]])
            self.assertEqual(payload["syllables"], parsed)
            self._write_wav(Path(command[4] + ".wav"))
            return subprocess.CompletedProcess(command, 0, "rendered song", "")

        result = render_chimera_song(
            {"bpm": 120, "lyrics": "hello Muse", "notes": [[60, 1], [62, 2], [0, 0.5]]},
            self.output, body="female", cli_path=self.cli, runner=fake_runner,
        )

        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0][0][1:], ["syllables", "hello Muse"])
        self.assertEqual(observed[1][0][1:3], ["render", "notes"])
        self.assertNotIn("shell", observed[0][1])
        self.assertNotIn("shell", observed[1][1])
        self.assertEqual(result["mode"], "song")
        self.assertEqual(result["note_count"], 3)
        self.assertEqual(result["sounding_notes"], 2)
        self.assertEqual(result["rest_count"], 1)
        self.assertFalse(result["cantonese_supported"])
        self.assertEqual(result["sample_rate"], 48_000)
        self.assertTrue(self.output.is_file())

    def test_custom_song_rejects_syllable_note_mismatch_before_render(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps([{"text": "one"}]), "")

        with self.assertRaises(ChimeraVocalError):
            render_chimera_song(
                {"bpm": 100, "lyrics": "hello world", "notes": [[60, 1], [62, 1]]},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.output.exists())

    def test_custom_song_rejects_unsupported_script_and_invalid_note_ranges(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with self.assertRaises(ValueError):
            render_chimera_song(
                {"bpm": 100, "lyrics": "あいう", "notes": [[60, 1]]},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        with self.assertRaises(ValueError):
            render_chimera_song(
                {"bpm": 100, "lyrics": "hello", "notes": [[128, 1]]},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        self.assertEqual(calls, [])

    def test_custom_song_preserves_output_created_during_render(self):
        parsed = [{"text": "hello"}]

        def fake_runner(command, **kwargs):
            if command[1] == "syllables":
                return subprocess.CompletedProcess(command, 0, json.dumps(parsed), "")
            self._write_wav(Path(command[4] + ".wav"))
            self.output.write_bytes(b"creator asset created concurrently")
            return subprocess.CompletedProcess(command, 0, "rendered", "")

        with self.assertRaises(ChimeraVocalError):
            render_chimera_song(
                {"bpm": 100, "lyrics": "hello", "notes": [[60, 1]]},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        self.assertEqual(self.output.read_bytes(), b"creator asset created concurrently")

    def test_custom_song_duration_limit_includes_initial_offset(self):
        calls = []
        notes = [[60, 30] for _ in range(30)]

        def fake_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with self.assertRaises(ValueError):
            render_chimera_song(
                {"bpm": 30, "lyrics": "hello", "notes": notes, "t0": 1},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        self.assertEqual(calls, [])

    def test_custom_song_rejects_oversized_syllable_preflight_json(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, " " * (1024 * 1024 + 1), "")

        with self.assertRaises(ChimeraVocalError):
            render_chimera_song(
                {"bpm": 100, "lyrics": "hello", "notes": [[60, 1]]},
                self.output, cli_path=self.cli, runner=fake_runner,
            )
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.output.exists())

    def test_module_cli_song_mode_loads_melody_and_routes_to_song_renderer(self):
        melody = {"bpm": 100, "lyrics": "hello", "notes": [[60, 1]]}
        with patch("muse.chimera_vocal._load_melody_file", return_value=melody) as load:
            with patch("muse.chimera_vocal.render_chimera_song", return_value={"status": "completed"}) as render:
                with patch("builtins.print"):
                    result = main([
                        "--mode", "song", "--melody-json", "melody.json",
                        "--output", "song.wav",
                    ])
        self.assertEqual(result, 0)
        load.assert_called_once_with("melody.json")
        render.assert_called_once_with(
            melody, "song.wav", body="female", cli_path=None, timeout=300,
        )

if __name__ == "__main__":
    unittest.main()
