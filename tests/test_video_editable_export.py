from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.video_editable_export import (
    VideoEditableExportError,
    export_verified_video_product,
)
from muse.video_product_builder import VideoProductBuilder


class VideoEditableExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.builder = VideoProductBuilder(self.root)
        self.source = self.root / "wan-final.mp4"
        self.source.write_bytes(bytes.fromhex("000000186674797069736f6d00000200") + b"fixture")
        self.voice = self.root / "voiceover.wav"
        self.voice.write_bytes(b"RIFFvoice-fixture")
        self.music = self.root / "background.wav"
        self.music.write_bytes(b"RIFFmusic-fixture")
        self.subtitles = self.root / "subtitles.srt"
        self.subtitles.write_text(
            "1\n00:00:00,250 --> 00:00:01,500\n第一句粤语旁白。\n\n"
            "2\n00:00:02,000 --> 00:00:03,250\n第二句，继续讲解。\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _inventory(self):
        return SimpleNamespace(tools=(
            SimpleNamespace(tool_id="ffmpeg", executable="ffmpeg"),
            SimpleNamespace(tool_id="ffprobe", executable="ffprobe"),
        ))

    def _build_product(self):
        def mux(arguments, **kwargs):
            Path(arguments[-1]).write_bytes(bytes.fromhex("000000186674797069736f6d00000200") + b"muxed")
            return SimpleNamespace(returncode=0, stderr="")

        def inspect(path, **kwargs):
            return {
                "schema_version": 1,
                "status": "passed",
                "path": "video.mp4",
                "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "warnings": [],
                "metadata": {
                    "duration_seconds": 5.0, "width": 832, "height": 480,
                    "fps": 24.0, "audio_codecs": ["aac"],
                    "file_bytes": Path(path).stat().st_size,
                },
            }

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._probe_media_duration_seconds", side_effect=[5.0, 4.0]),
            patch("muse.video_product_builder.subprocess.run", side_effect=mux),
            patch("muse.video_product_builder._inspect_product_video", side_effect=inspect),
            patch.object(self.builder, "_record_creator_artifact", side_effect=lambda result, **kwargs: result),
        ):
            return self.builder.package_generated_clip(
                "粤语产品演示", "一段有镜头切换的城市夜景讲解",
                self.source,
                generation_metadata={
                    "status": "completed", "model": "Wan 2.1 T2V 1.3B",
                    "width": 832, "height": 480, "fps": 24,
                    "duration_seconds": 5.0, "seed": 1234,
                },
                scene_records=[
                    {"order": 1, "prompt": "城市夜景，缓慢推进", "seed": 1234},
                    {"order": 2, "prompt": "霓虹街道，平稳横移", "seed": 5678},
                ],
                narration_path=self.voice,
                narration_script="第一句粤语旁白。第二句，继续讲解。",
                subtitle_path=self.subtitles,
                background_music_path=self.music,
            )

    def test_exports_integrity_verified_product_as_muted_video_and_separate_tracks(self):
        product = self._build_product()
        verified = VideoProductBuilder.verify_path(
            self.root / "data" / "products", product["initiative_id"], product["version_id"],
        )
        self.assertTrue(verified["validated"])

        output = self.root / "editable" / "openfilm-project"
        result = export_verified_video_product(
            self.root / "data" / "products", product["initiative_id"], product["version_id"], output,
        )

        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["resolution"], "832x480")
        self.assertEqual(result["fps"], 24)
        self.assertEqual(result["subtitle_cue_count"], 2)
        self.assertEqual(result["audio_tracks"], ["narration", "music"])
        self.assertFalse(result["publication_approved"])
        film = Path(result["film_html"]).read_text(encoding="utf-8")
        self.assertIn('<video ', film)
        self.assertIn('volume="0.000"', film)
        self.assertEqual(film.count("<audio "), 2)
        self.assertIn("subtitle-001.html", film)
        self.assertIn("subtitle-002.html", film)

        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_product"]["initiative_id"], product["initiative_id"])
        self.assertEqual(manifest["source_product"]["version_id"], product["version_id"])
        self.assertEqual(manifest["subtitle_cue_count"], 2)
        self.assertIn("does not recreate", manifest["mix_note"])

        with zipfile.ZipFile(result["project_zip"]) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertIn("film.html", names)
            self.assertIn("muse-openfilm-manifest.json", names)
            manifest_assets = json.loads(archive.read("muse-openfilm-manifest.json"))["assets"]
            self.assertEqual({item["role"] for item in manifest_assets}, {"footage", "narration", "music"})
            self.assertIn("pages/subtitle-002.html", names)

    def test_preserves_embedded_audio_when_product_has_no_separate_audio_assets(self):
        def inspect(path, **kwargs):
            return {
                "schema_version": 1, "status": "passed", "path": "video.mp4",
                "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "warnings": [],
                "metadata": {
                    "duration_seconds": 5.0, "width": 832, "height": 480,
                    "fps": 24.0, "audio_codecs": ["aac"],
                    "file_bytes": Path(path).stat().st_size,
                },
            }

        with (
            patch("muse.video_product_builder.discover_media_tools", return_value=self._inventory()),
            patch("muse.video_product_builder._inspect_product_video", side_effect=inspect),
            patch.object(self.builder, "_record_creator_artifact", side_effect=lambda result, **kwargs: result),
        ):
            product = self.builder.package_generated_clip(
                "原生音频视频", "保留原始视频声音", self.source,
                generation_metadata={
                    "status": "completed", "model": "Wan 2.1 T2V 1.3B",
                    "width": 832, "height": 480, "fps": 24,
                    "duration_seconds": 5.0, "has_audio": True,
                },
            )
        result = export_verified_video_product(
            self.root / "data" / "products", product["initiative_id"], product["version_id"],
            self.root / "editable" / "video-only",
        )
        film = Path(result["film_html"]).read_text(encoding="utf-8")
        self.assertEqual(result["audio_tracks"], [])
        self.assertIn('volume="1.000"', film)
        self.assertNotIn("<audio ", film)

    def test_rejects_source_artifact_changed_after_integrity_verification(self):
        product = self._build_product()
        original_verify = VideoProductBuilder.verify_path

        def verify_then_mutate(products_root, initiative_id, version):
            result = original_verify(products_root, initiative_id, version)
            video = Path(products_root) / initiative_id / version / "video.mp4"
            video.write_bytes(video.read_bytes() + b"changed-after-verify")
            return result

        output = self.root / "tamper-attempt"
        with patch.object(VideoProductBuilder, "verify_path", side_effect=verify_then_mutate):
            with self.assertRaisesRegex(VideoEditableExportError, "changed after verification: video.mp4"):
                export_verified_video_product(
                    self.root / "data" / "products", product["initiative_id"], product["version_id"], output,
                )
        self.assertFalse(output.exists())

    def test_rejects_product_that_did_not_pass_integrity_verification(self):
        output = self.root / "not-created"
        with patch("muse.video_editable_export.VideoProductBuilder.verify_path",
                   return_value={"validated": False}):
            with self.assertRaisesRegex(VideoEditableExportError, "integrity verification"):
                export_verified_video_product(self.root / "products", "a" * 32, "b" * 20, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
