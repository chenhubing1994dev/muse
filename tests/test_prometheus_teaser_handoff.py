from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "muse-prometheus-teaser.py"
SPEC = importlib.util.spec_from_file_location("muse_prometheus_teaser_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrometheusTeaserHandoffTests(unittest.TestCase):
    def test_full_decode_pass_builds_product_scoped_optional_visual_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "data" / "prometheus" / "teaser"
            output.mkdir(parents=True)
            video = output / "master.mp4"
            cover = output / "cover.jpg"
            video.write_bytes(b"verified-video")
            cover.write_bytes(b"cover-image")
            product = {"initiative_id": "initiative-1", "version_id": "version-1",
                       "relative_path": "data/products/initiative-1/version-1",
                       "video_metadata": {"aspect_ratio": "16:9"}}
            summary = {"status": "completed", "title": "Teaser",
                       "video": {"duration_seconds": 16.0}, "completed_at": "2026-11-21T12:00:00Z",
                       "full_decode": "passed"}
            package = {"status": "draft_only", "can_publish": False,
                       "contact_sheet_generation": {"status": "unavailable", "reason": "ffmpeg unavailable"}}
            with patch.object(MODULE, "build_publish_package_with_optional_generated_contact_sheet",
                              return_value=package) as build_package:
                returned = MODULE._attach_visual_review_handoff(
                    workspace, output, summary, master_video=video, cover=cover, product=product,
                )
            build_package.assert_called_once_with(workspace, output)
            self.assertEqual(returned, package)
            saved = json.loads((output / "generation.json").read_text(encoding="utf-8"))
            notes = json.loads((output / "source-notes.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["mp4"]["full_decode"], "passed")
            self.assertEqual(saved["product"]["version_id"], "version-1")
            self.assertEqual(saved["publish_package"], package)
            self.assertIn("illustrative", notes["editorial_boundary"])

    def test_handoff_fails_closed_without_full_decode_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "teaser"
            output.mkdir()
            video, cover = output / "master.mp4", output / "cover.jpg"
            video.write_bytes(b"video")
            cover.write_bytes(b"cover")
            summary = {"status": "completed", "video": {"duration_seconds": 1}}
            with patch.object(MODULE, "build_publish_package_with_optional_generated_contact_sheet") as build_package:
                with self.assertRaisesRegex(RuntimeError, "full-decode"):
                    MODULE._attach_visual_review_handoff(
                        workspace, output, summary, master_video=video, cover=cover, product={},
                    )
            build_package.assert_not_called()
            self.assertFalse((output / "generation.json").exists())


if __name__ == "__main__":
    unittest.main()
