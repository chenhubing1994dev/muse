from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.clip_product_adapter import register_highlight_clip
from muse.video_product_builder import VideoProductBuilder, VideoProductError


class ClipProductAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"original-source-video")
        self.clip = self.root / "highlight.mp4"
        self.clip.write_bytes(b"\x00\x00\x00\x18ftypisom0000rendered-clip")
        self.captions = self.root / "highlight.srt"
        self.captions.write_text(
            "1\n00:00:00,100 --> 00:00:01,200\n精彩片段。\n", encoding="utf-8"
        )
        self.candidate = {"start": 12.0, "end": 17.0, "score": 0.91, "transcript": "精彩片段。"}

    def tearDown(self):
        self.temp.cleanup()

    def _register(self):
        def qa(path, **kwargs):
            return {
                "schema_version": 1, "status": "passed", "path": "video.mp4",
                "artifact_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "metadata": {
                    "duration_seconds": 5.0, "width": 1080, "height": 1920,
                    "fps": 30.0, "audio_codecs": ["aac"], "has_audio": True,
                },
                "checks": {"full_decode": {"passed": True, "detail": "decoded"}},
                "warnings": [],
            }

        with patch("muse.clip_product_adapter.discover_media_tools", return_value=SimpleNamespace(tools=[])):
            with patch("muse.clip_product_adapter._probe_media_duration_seconds", return_value=60.0):
                with patch("muse.clip_product_adapter._inspect_product_video", side_effect=qa):
                    return register_highlight_clip(
                        self.root, self.source, self.clip, self.candidate,
                        title="本地高光测试", captions_srt=self.captions,
                        ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                        analysis_mode="audio_plus_local_cantonese_asr",
                        transcript_engine="faster-whisper-local",
                        transcript="精彩片段。",
                    )

    def test_registers_versioned_product_caption_sidecar_and_single_ledger_attempt(self):
        result = self._register()
        self.assertTrue(result["validated"])
        self.assertEqual(result["status"], "testing")
        self.assertEqual(result["video_attempt_status"], "succeeded")
        product = self.root / result["relative_path"]
        manifest = json.loads((product / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["clip_provenance"]["start_seconds"], 12.0)
        self.assertEqual(manifest["files"]["clip-captions.srt"], hashlib.sha256(self.captions.read_bytes()).hexdigest())
        with zipfile.ZipFile(product / "product.zip") as archive:
            self.assertIn("clip-captions.srt", archive.namelist())
            self.assertEqual(archive.read("clip-captions.srt"), self.captions.read_bytes())

        repeated = self._register()
        self.assertEqual(repeated["initiative_id"], result["initiative_id"])
        self.assertEqual(repeated["version_id"], result["version_id"])
        self.assertEqual(repeated["video_attempt_id"], result["video_attempt_id"])

        from muse.initiative_engine import InitiativeStore
        with InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3") as store:
            attempt = store.get_video_attempt(result["video_attempt_id"])
        self.assertEqual(attempt["status"], "succeeded")
        self.assertEqual(attempt["product_id"], result["initiative_id"])
        self.assertEqual(attempt["version_id"], result["version_id"])

        (product / "clip-captions.srt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(VideoProductError, "integrity"):
            VideoProductBuilder.verify_path(
                self.root / "data" / "products", result["initiative_id"], result["version_id"]
            )

    def test_rejects_invalid_candidate_before_creating_product_or_ledger(self):
        with self.assertRaisesRegex(ValueError, "candidate interval"):
            register_highlight_clip(
                self.root, self.source, self.clip,
                {"start": 12.0, "end": 17.0, "score": 1.2}, title="invalid",
            )
        self.assertFalse((self.root / "data" / "products").exists())
        self.assertFalse((self.root / "data" / "initiatives").exists())

    def test_failed_canonical_qa_never_registers_a_product_or_attempt(self):
        with patch("muse.clip_product_adapter.discover_media_tools", return_value=SimpleNamespace(tools=[])):
            with patch("muse.clip_product_adapter._probe_media_duration_seconds", return_value=60.0):
                with patch("muse.clip_product_adapter._inspect_product_video", return_value={
                    "schema_version": 1, "status": "failed", "path": "video.mp4",
                    "artifact_sha256": hashlib.sha256(self.clip.read_bytes()).hexdigest(),
                    "metadata": {}, "checks": {}, "warnings": [],
                }):
                    with self.assertRaisesRegex(VideoProductError, "failed canonical"):
                        register_highlight_clip(
                            self.root, self.source, self.clip, self.candidate,
                            title="QA must fail", ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                        )
        products = self.root / "data" / "products"
        self.assertTrue(products.is_dir())
        self.assertEqual(list(products.rglob("manifest.json")), [])
        self.assertFalse((self.root / "data" / "initiatives").exists())


if __name__ == "__main__":
    unittest.main()
