from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "muse-prometheus-film.py"
SPEC = importlib.util.spec_from_file_location("muse_prometheus_film_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrometheusFilmHandoffTests(unittest.TestCase):
    def test_landscape_master_gets_product_scoped_review_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "data" / "prometheus" / "film"
            output.mkdir(parents=True)
            video, cover = output / "master.mp4", output / "cover.jpg"
            video.write_bytes(b"verified-master")
            cover.write_bytes(b"cover")
            product = {"initiative_id": "initiative-1", "version_id": "version-2",
                       "relative_path": "data/products/initiative-1/version-2",
                       "video_metadata": {"aspect_ratio": "16:9"}}
            result = {"status": "completed", "title": "PROMETHEUS",
                      "landscape": {"duration_seconds": 90.0, "full_decode": "passed"},
                      "completed_at": "2026-11-21T12:00:00Z"}
            package = {"status": "draft_only", "can_publish": False,
                       "contact_sheet_generation": {"status": "unavailable", "reason": "ffmpeg unavailable"}}
            expected_output = workspace / product["relative_path"] / "publish-package-v1"
            with patch.object(MODULE, "build_publish_package_with_optional_generated_contact_sheet",
                              return_value=package) as build_package:
                returned = MODULE._attach_landscape_visual_review_handoff(
                    workspace, output, result, master_video=video, cover=cover, product=product,
                )
            build_package.assert_called_once_with(
                workspace, output, output_dir=expected_output,
                platforms=("youtube", "wechat_channels", "bilibili"),
            )
            self.assertEqual(returned, package)
            saved = json.loads((output / "generation.json").read_text(encoding="utf-8"))
            notes = json.loads((output / "source-notes.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["product"]["version_id"], "version-2")
            self.assertEqual(saved["publish_package"], package)
            self.assertIn("illustrative", notes["editorial_boundary"])

    def test_landscape_handoff_fails_closed_without_full_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            output = workspace / "film"
            output.mkdir()
            video, cover = output / "master.mp4", output / "cover.jpg"
            video.write_bytes(b"video")
            cover.write_bytes(b"cover")
            result = {"status": "completed", "landscape": {"duration_seconds": 90}}
            with patch.object(MODULE, "build_publish_package_with_optional_generated_contact_sheet") as build_package:
                with self.assertRaisesRegex(RuntimeError, "full-decode-passed"):
                    MODULE._attach_landscape_visual_review_handoff(
                        workspace, output, result, master_video=video, cover=cover, product={},
                    )
            build_package.assert_not_called()
            self.assertFalse((output / "generation.json").exists())


if __name__ == "__main__":
    unittest.main()
