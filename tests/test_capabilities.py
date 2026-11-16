from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.capabilities import CapabilityRegistry, sync_media_capabilities
from muse.media import MediaInventory, MediaTool


class CapabilityRegistryTests(unittest.TestCase):
    def test_registers_and_persists_verified_media_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "capabilities.sqlite3"
            inventory = MediaInventory(
                tools=(MediaTool("ffmpeg", "FFmpeg", "D:/tools/ffmpeg.exe", ("video_transcode", "audio_convert")),),
                ffmpeg_version="8.1.1",
                hardware_encoders=("h264_amf",),
            )
            with CapabilityRegistry(database) as registry:
                result = sync_media_capabilities(registry, inventory)
                self.assertEqual(result["registered"], 1)
                item = registry.get("media.ffmpeg")
                self.assertEqual(item["status"], "ready")
                self.assertEqual(item["domain"], "media")
                self.assertEqual(item["executable"], "D:/tools/ffmpeg.exe")
                self.assertIn("video_transcode", item["capabilities"])
            with CapabilityRegistry(database) as reopened:
                self.assertEqual(reopened.get("media.ffmpeg")["version"], "8.1.1")

    def test_registry_does_not_mark_unverified_tools_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as registry:
                registry.register(
                    capability_id="image.local_generator",
                    domain="images",
                    name="Local image generator",
                    description="Not yet connected to a verified executable.",
                    status="unverified",
                    capabilities=("image_generation",),
                )
                self.assertEqual(registry.get("image.local_generator")["status"], "unverified")

    def test_rejects_invalid_status_and_empty_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as registry:
                with self.assertRaises(ValueError):
                    registry.register(
                        capability_id="", domain="video", name="Bad", description="Bad", status="ready"
                    )
                with self.assertRaises(ValueError):
                    registry.register(
                        capability_id="video.bad", domain="video", name="Bad", description="Bad", status="magic"
                    )

    def test_media_sync_updates_existing_capability_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as registry:
                inventory = MediaInventory(
                    tools=(MediaTool("ffprobe", "FFprobe", "D:/tools/ffprobe.exe", ("media_probe",)),),
                    ffmpeg_version=None,
                    hardware_encoders=(),
                )
                first = sync_media_capabilities(registry, inventory)
                second = sync_media_capabilities(registry, inventory)
                self.assertEqual(first["registered"], 1)
                self.assertEqual(second["updated"], 1)
                self.assertEqual(len(registry.list_all()), 1)


if __name__ == "__main__":
    unittest.main()
