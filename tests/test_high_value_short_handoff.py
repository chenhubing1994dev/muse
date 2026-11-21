from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "muse-high-value-short.py"
SPEC = importlib.util.spec_from_file_location("muse_high_value_short_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HighValueShortHandoffTests(unittest.TestCase):
    def test_handoff_runs_only_after_full_decode_and_persists_package_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "short-v1"
            output.mkdir()
            generation = {
                "status": "completed",
                "mp4": {"path": "short-v1/master.mp4", "full_decode": "passed"},
                "product": {"relative_path": "data/products/short-v1"},
            }
            package = {
                "status": "draft_only", "can_publish": False,
                "contact_sheet_generation": {"status": "unavailable", "reason": "ffmpeg missing"},
            }
            with patch.object(
                MODULE, "build_publish_package_with_optional_generated_contact_sheet",
                return_value=package,
            ) as build_package:
                returned = MODULE.attach_visual_review_handoff(workspace, output, generation)

            build_package.assert_called_once_with(workspace, output)
            self.assertEqual(returned, package)
            saved = json.loads((output / "generation.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["publish_package"], package)
            self.assertEqual(saved["mp4"]["full_decode"], "passed")

    def test_handoff_rejects_video_without_full_decode_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "short-v1"
            output.mkdir()
            generation = {"status": "completed", "mp4": {"full_decode": "failed"}}
            with patch.object(MODULE, "build_publish_package_with_optional_generated_contact_sheet") as build_package:
                with self.assertRaisesRegex(RuntimeError, "full-decode-passed"):
                    MODULE.attach_visual_review_handoff(workspace, output, generation)
            build_package.assert_not_called()
            self.assertFalse((output / "generation.json").exists())


if __name__ == "__main__":
    unittest.main()
