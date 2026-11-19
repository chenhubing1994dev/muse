from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from muse.media_compositor import (
    MediaAssetStore,
    compose_timeline,
    style_filter,
)
from muse.media_search import (
    MediaAsset,
    MediaSearchBlocked,
    MediaSearchEngine,
    _safe_media_url,
    download_asset,
)
from muse.smart_crop import ken_burns_filter, smart_crop, to_ken_burns

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment-dependent optional dependency
    Image = None


def _results():
    return [
        {"title": "HK retro neon street", "url": "https://a.example/retro.jpg",
         "snippet": "retro neon", "engine": "demo", "width": 1920, "height": 1080,
         "license": "CC0", "source_ref": "seed-1"},
        {"title": "Pixel skyline", "url": "https://b.example/pixel.png",
         "snippet": "pixel art city", "engine": "demo", "width": 1200, "height": 800,
         "license": "CC-BY", "source_ref": "seed-2"},
        {"title": "Cosmic nebula", "url": "https://c.example/cosmic.webp",
         "snippet": "deep space cosmic", "engine": "demo", "width": 1000, "height": 1000,
         "license": "CC0", "source_ref": "seed-3"},
        {"title": "HK retro wallpaper", "url": "https://d.example/plain.jpg",
         "snippet": "retro texture", "engine": "demo", "width": 1920, "height": 1080,
         "license": "unknown", "source_ref": "seed-4"},
    ]


class MediaSearchTests(unittest.TestCase):
    def test_search_scores_sorts_and_records_provenance(self):
        engine = MediaSearchEngine(search_callable=lambda q, *, limit: _results()[:limit])
        assets = engine.search_images("city", "hk-retro", limit=4)
        self.assertEqual(assets[0].url, "https://a.example/retro.jpg")
        self.assertEqual(assets[1].url, "https://d.example/plain.jpg")
        self.assertNotIn("https://b.example/pixel.png", [asset.url for asset in assets])
        pixel_assets = engine.search_images("city", "pixel", limit=4)
        self.assertIn("https://b.example/pixel.png", [asset.url for asset in pixel_assets])
        self.assertTrue(all(isinstance(asset, MediaAsset) for asset in assets))
        self.assertEqual(assets[0].license, "CC0")
        self.assertEqual(assets[0].source_ref, "seed-1")
        self.assertEqual(assets[0].width, 1920)
        self.assertEqual(assets[0].height, 1080)

    def test_search_filters_resolution_and_aspect_ratio(self):
        engine = MediaSearchEngine(search_callable=lambda q, *, limit: _results())
        assets = engine.search_images(
            "city", "hk-retro", limit=20, min_width=1500, target_ratio=16 / 9,
        )
        self.assertEqual([asset.url for asset in assets], [
            "https://a.example/retro.jpg", "https://d.example/plain.jpg",
        ])

    def test_search_returns_blocked_without_endpoint_or_callable(self):
        engine = MediaSearchEngine()
        with self.assertRaises(MediaSearchBlocked) as ctx:
            engine.search_images("city", "hk-retro")
        self.assertIn("blocked", str(ctx.exception))

    def test_search_rejects_credentials_and_non_http_urls(self):
        for url in (
            "file:///etc/passwd",
            "https://user:pass@example.com/a.jpg",
            "javascript:alert(1)",
        ):
            self.assertIsNone(_safe_media_url(url))
        engine = MediaSearchEngine(search_callable=lambda q, *, limit: [
            {"title": "bad", "url": "file:///etc/passwd"},
            {"title": "good retro neon", "url": "https://a.example/retro.jpg"},
        ])
        assets = engine.search_images("city", "hk-retro", limit=10)
        self.assertEqual([asset.url for asset in assets], ["https://a.example/retro.jpg"])

    def test_download_rejects_credentials_and_outside_host_allowlist(self):
        asset = MediaAsset(title="x", url="https://user:pass@example.com/a.jpg")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download_asset(asset, directory, max_bytes=1024, allowed_hosts=None)
        safe = MediaAsset(title="x", url="https://cdn.example/a.jpg")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download_asset(safe, directory, max_bytes=1024, allowed_hosts=["other.example"])

    def test_download_enforces_content_length_and_written_size_limit(self):
        class Response:
            def __init__(self, headers=None, body=b"", url="https://cdn.example/a.jpg"):
                self.headers = headers or {}
                self._body = body
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, size=-1):
                return self._body if size < 0 else self._body[:size]

        asset = MediaAsset(title="x", url="https://cdn.example/a.jpg")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as ctx:
                download_asset(
                    asset, directory, max_bytes=10, allowed_hosts=None,
                    opener=lambda *_a, **_k: Response({"Content-Length": "11"}),
                )
            self.assertIn("size limit", str(ctx.exception))
            self.assertEqual(list(Path(directory).glob("*.download")), [])
            with self.assertRaises(ValueError) as ctx:
                download_asset(
                    asset, directory, max_bytes=10, allowed_hosts=None,
                    opener=lambda *_a, **_k: Response({}, b"x" * 11),
                )
            self.assertIn("size limit", str(ctx.exception))
            self.assertEqual(list(Path(directory).glob("*.download")), [])

    def test_download_writes_verified_bytes_without_executing_them(self):
        body = b"not executable"
        class Response:
            headers = {"Content-Length": str(len(body))}
            url = "https://cdn.example/a.jpg"

            def __init__(self):
                self._offset = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, size=-1):
                if size < 0 or size >= len(body) - self._offset:
                    chunk = body[self._offset:]
                    self._offset = len(body)
                else:
                    chunk = body[self._offset:self._offset + size]
                    self._offset += len(chunk)
                return chunk

        asset = MediaAsset(title="x", url="https://cdn.example/a.jpg", license="CC0")
        with tempfile.TemporaryDirectory() as directory:
            result = download_asset(
                asset, directory, max_bytes=100, allowed_hosts=None,
                opener=lambda *_a, **_k: Response(),
            )
            target = Path(result["path"])
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(result["bytes"], len(body))


@unittest.skipIf(Image is None, "Pillow optional dependency is not installed")
class SmartCropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        image = Image.new("RGB", (1000, 500), (120, 120, 120))
        for x in range(350, 450):
            for y in range(200, 300):
                image.putpixel((x, y), (240, 30, 30))
        image.save(self.source)
        self.dest = self.root / "crop.png"

    def tearDown(self):
        self.temp.cleanup()

    def test_smart_crop_keeps_focus_and_returns_requested_ratio(self):
        result = smart_crop(self.source, self.dest, 16 / 9, focus_x=0.5, focus_y=0.5)
        left, top, right, bottom = result["crop_box"]
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(right, 1000)
        self.assertLessEqual(bottom, 500)
        self.assertAlmostEqual((right - left) / (bottom - top), 16 / 9, delta=0.01)
        self.assertLessEqual(left, 350)
        self.assertGreaterEqual(right, 450)  # saliency/focus patch stays inside the crop
        self.assertEqual(result["output"], str(self.dest))
        with Image.open(self.dest) as cropped:
            self.assertAlmostEqual(cropped.size[0] / cropped.size[1], 16 / 9, delta=0.01)

    def test_smart_crop_does_not_modify_source(self):
        before = self.source.read_bytes()
        smart_crop(self.source, self.dest, 1.0)
        self.assertEqual(self.source.read_bytes(), before)

    def test_to_ken_burns_emits_bounded_zoompan_filter(self):
        asset = MediaAsset(title="x", url="https://cdn.example/a.jpg")
        params = to_ken_burns(asset, 4.0, effect="push_in")
        self.assertEqual(params["effect"], "push_in")
        self.assertEqual(params["duration_seconds"], 4.0)
        self.assertEqual(params["frames"], 120)
        self.assertIn("zoompan", params["filter"])
        self.assertIn("z='min(zoom+", params["filter"])
        with self.assertRaises(ValueError):
            to_ken_burns(asset, 0)
        with self.assertRaises(ValueError):
            to_ken_burns(asset, 4.0, effect="spin")


@unittest.skipIf(Image is None, "Pillow optional dependency is not installed")
class KenBurnsWithoutPillowTests(unittest.TestCase):
    def test_ken_burns_parameter_contract_without_pillow(self):
        asset = MediaAsset(title="x", url="https://cdn.example/a.jpg")
        for effect in ("push_in", "pull_out", "drift_left", "drift_right"):
            params = to_ken_burns(asset, 2.0, effect=effect)
            self.assertIn(effect, params["filter"])


class MediaCompositorTests(unittest.TestCase):
    def setUp(self):
        self.assets = [
            MediaAsset(title="a", url="https://cdn.example/a.jpg", width=1920, height=1080),
            MediaAsset(title="b", url="https://cdn.example/b.jpg", width=1920, height=1080),
        ]

    def test_compose_timeline_aligns_scenes_to_narration_segments(self):
        narration = [
            {"text": "first", "start": 0.0, "end": 4.0},
            {"text": "second", "start": 4.0, "end": 7.0},
        ]
        result = compose_timeline(self.assets, narration, "hk-retro", fps=30)
        self.assertEqual(result["compositionId"], "MuseSceneTimeline")
        self.assertEqual(len(result["props"]["scenes"]), 2)
        scenes = result["props"]["scenes"]
        self.assertEqual([scene["durationInFrames"] for scene in scenes], [120, 90])
        self.assertEqual([scene["startFrame"] for scene in scenes], [0, 120])
        self.assertTrue(all(scene["cameraMove"] for scene in scenes))
        self.assertTrue(all("vhs" in scene["vfx"] for scene in scenes))
        self.assertTrue(all(scene["src"] for scene in scenes))

    def test_style_filter_maps_three_styles(self):
        hk = style_filter("hk-retro")
        pixel = style_filter("pixel")
        cosmic = style_filter("cosmic")
        self.assertIn("warm", hk["css"])
        self.assertIn("vhs", hk["ffmpeg"])
        self.assertIn("pixelated", pixel["css"])
        self.assertIn("neighbor", pixel["ffmpeg"])
        self.assertIn("glow", cosmic["css"])
        self.assertIn("gblur", cosmic["ffmpeg"])
        with self.assertRaises(ValueError):
            style_filter("unknown")

    def test_media_asset_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.sqlite3"
            with MediaAssetStore(path) as store:
                saved = store.save_manifest("city retro", [
                    asdict(asset) for asset in self.assets
                ])
            with MediaAssetStore(path) as store:
                loaded = store.load_manifest(saved["manifest_id"])
            self.assertEqual(loaded["query"], "city retro")
            self.assertEqual(len(loaded["assets"]), 2)
            self.assertEqual(loaded["assets"][0]["url"], self.assets[0].url)


if __name__ == "__main__":
    unittest.main()
