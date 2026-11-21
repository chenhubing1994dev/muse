from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from muse.video_contact_sheet import build_contact_sheet_command, create_contact_sheet


class ContactSheetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.output = self.root / "review.png"

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, command, **kwargs):
        if Path(command[0]).name.startswith("ffprobe"):
            if str(command[-1]).endswith(".mp4"):
                return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [
                    {"codec_type": "video", "duration": "4.0", "width": 640, "height": 360}
                ]}), stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [
                {"codec_type": "video", "width": 992, "height": 584}
            ]}), stderr="")
        if "-f" in command and command[-1] == "-":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_command_uses_fixed_bounded_tile_filter(self):
        command = build_contact_sheet_command(self.source, self.output,
                                              duration_seconds=4, samples=12, columns=4)
        graph = command[command.index("-vf") + 1]
        self.assertIn("fps=3.000000000", graph)
        self.assertIn("tile=4x3", graph)
        self.assertIn("-nostdin", command)

    def test_rejects_unbounded_parameters(self):
        with self.assertRaises(ValueError):
            build_contact_sheet_command(self.source, self.output, duration_seconds=4, samples=99)

    def test_dry_run_does_not_write(self):
        result = create_contact_sheet(self.source, self.output, runner=self.runner, dry_run=True)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(self.output.exists())

    def test_render_verifies_png_and_publishes_atomically(self):
        result = create_contact_sheet(self.source, self.output, runner=self.runner)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["width"], result["height"]), (992, 584))
        self.assertTrue(result["visual_review_required"])
        self.assertEqual(result["image_decode"], "passed")
        self.assertTrue(self.output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_failed_render_does_not_publish(self):
        def failed(command, **kwargs):
            if Path(command[0]).name.startswith("ffprobe"):
                return self.runner(command, **kwargs)
            return SimpleNamespace(returncode=1, stdout="", stderr="render failed")
        with self.assertRaisesRegex(RuntimeError, "contact sheet failed"):
            create_contact_sheet(self.source, self.output, runner=failed)
        self.assertFalse(self.output.exists())


    def test_failed_full_decode_never_publishes(self):
        def decode_failed(command, **kwargs):
            if "-f" in command and command[-1] == "-":
                return SimpleNamespace(returncode=1, stdout="", stderr="decode failed")
            return self.runner(command, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "full image decode"):
            create_contact_sheet(self.source, self.output, runner=decode_failed)
        self.assertFalse(self.output.exists())

    def test_source_change_during_probe_fails_closed(self):
        def source_mutating(command, **kwargs):
            if Path(command[0]).name.startswith("ffprobe") and Path(command[-1]) == self.source:
                self.source.write_bytes(b"changed-source")
            return self.runner(command, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "changed during contact sheet probe"):
            create_contact_sheet(self.source, self.output, runner=source_mutating)
        self.assertFalse(self.output.exists())

if __name__ == "__main__":
    unittest.main()
