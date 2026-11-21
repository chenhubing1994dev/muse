from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from muse.video_product_builder import VideoProductBuilder
from muse.video_publish_package import (
    VideoPublishPackageError,
    build_publish_package,
    build_publish_package_with_generated_contact_sheet,
)


class VideoPublishPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_dir = self.root / "data" / "prometheus" / "sample"
        self.source_dir.mkdir(parents=True)
        self.video = self.source_dir / "clip.mp4"
        self.video.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
        self.cover = self.source_dir / "cover.jpg"
        self.cover.write_bytes(b"\xff\xd8\xff\xd9")
        (self.source_dir / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nAI 说得自信，不代表一定正确。\n",
            encoding="utf-8",
        )
        self.storyboard = {
            "schema_version": 1,
            "title": "三步查证法",
            "voiceover": "not used",
            "audio": "original synthetic ambient bed",
            "research": {"source": "NIST AI 600-1"},
        }
        (self.source_dir / "storyboard.json").write_text(json.dumps(self.storyboard), encoding="utf-8")
        self.source_notes = {
            "title": "NIST AI Risk Management Framework: Generative AI Profile",
            "publisher": "NIST",
            "published_at": "2024-07-26",
            "doi": "https://doi.org/10.6028/NIST.AI.600-1",
            "pdf": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
            "relevant_sections": {"2.2": "confabulation", "MS-2.5-003": "verify sources"},
            "editorial_boundary": "Counter-evidence is editorial advice, not a direct quotation.",
        }
        (self.source_dir / "source-notes.json").write_text(json.dumps(self.source_notes), encoding="utf-8")
        (self.source_dir / "README.md").write_text("# Three-step verification\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_generation(self, qa_status: str = "passed") -> dict:
        qa = {
            "status": qa_status,
            "missing_tools": [],
            "checks": {"full_decode": {"passed": qa_status == "passed", "detail": "test"}},
            "warnings": ([{"code": "sustained_black", "message": "review dark intro", "start_seconds": 0.0, "duration_seconds": 1.2}] if qa_status == "passed" else []),
            "metadata": {"width": 1080, "height": 1920, "fps": 24},
        }
        builder = VideoProductBuilder(self.root)
        with patch("muse.video_product_builder.inspect_video_file", return_value=qa):
            product = builder.package_generated_clip(
                "三步查证法",
                "Research-backed short about checking AI claims.",
                self.video,
                generation_metadata={
                    "status": "completed", "model": "Wan 2.1 T2V 1.3B",
                    "aspect_ratio": "9:16", "width": 1080, "height": 1920,
                    "fps": 24, "duration_seconds": 1.0, "has_audio": True,
                },
            )
        video_hash = hashlib.sha256(self.video.read_bytes()).hexdigest()
        cover_hash = hashlib.sha256(self.cover.read_bytes()).hexdigest()
        generation = {
            "status": "completed",
            "title": "别把 AI 的自信，当成事实｜三步查证法",
            "evolution_cycle_id": "cycle_test",
            "mp4": {
                "path": "data/prometheus/sample/clip.mp4",
                "sha256": video_hash, "bytes": self.video.stat().st_size,
                "duration_seconds": 1.0, "width": 1080, "height": 1920,
                "fps": 24, "codec": "h264", "audio_codec": "aac",
                "audio_sample_rate": 48000, "full_decode": "passed",
            },
            "cover": {"path": "data/prometheus/sample/cover.jpg", "sha256": cover_hash},
            "product": product,
        }
        (self.source_dir / "generation.json").write_text(json.dumps(generation), encoding="utf-8")
        return generation

    def test_builds_four_platform_drafts_and_verified_package(self):
        self._create_generation()
        result = build_publish_package(self.root, self.source_dir)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["release_state"], "draft_only")
        self.assertFalse(result["can_publish"])
        self.assertFalse(result["official_publish_api_called"])
        self.assertEqual(result["review_warning_count"], 1)
        self.assertEqual(result["review_warning_codes"], ["sustained_black"])
        self.assertEqual(result["release_quality_status"], "blocked_pending_human_review")
        self.assertGreater(result["release_blocker_count"], 0)
        out = self.root / result["output_dir"]
        for platform in ("youtube_shorts", "tiktok", "instagram_reels", "bilibili"):
            draft = json.loads((out / "platform-drafts" / f"{platform}.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["release_state"], "draft_only")
            self.assertFalse(draft["publish_api_called"])
            self.assertEqual(draft["compliance_review"]["account_authorization"]["status"], "not_connected")
            self.assertEqual(draft["technical_profile"]["master_aspect_ratio"], "9:16")
            self.assertTrue(draft["source_citations"][0]["doi"])
        with zipfile.ZipFile(out / "publish-package.zip") as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertIn("qa-report.json", names)
            self.assertIn("release-quality-report.json", names)
            self.assertIn("subtitles.srt", names)
            self.assertIn("platform-drafts/youtube_shorts.json", names)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        release_checklist = json.loads((out / "release-checklist.json").read_text(encoding="utf-8"))
        self.assertEqual(release_checklist["visual_anomaly_review"]["warning_count"], 1)
        self.assertEqual(release_checklist["visual_anomaly_review"]["warning_codes"], ["sustained_black"])
        quality_report = json.loads((out / "release-quality-report.json").read_text(encoding="utf-8"))
        self.assertFalse(quality_report["can_publish"])
        self.assertTrue(quality_report["technical_qa_is_not_release_approval"])
        self.assertEqual(quality_report["checks"]["technical_quality"]["status"], "passed")
        self.assertEqual(quality_report["checks"]["subtitle_review"]["status"], "not_recorded")
        self.assertIn("editorial_and_fact_review", quality_report["blocking_items"])
        self.assertIn("visual_and_model_rights_review", quality_report["blocking_items"])
        self.assertIn("account_authorization", quality_report["blocking_items"])
        self.assertFalse(manifest["can_publish"])
        self.assertEqual(set(manifest["platforms"]), {"youtube_shorts", "tiktok", "instagram_reels", "bilibili"})

    def test_contact_sheet_is_included_and_hash_bound_as_visual_review_evidence(self):
        self._create_generation()
        contact = self.root / "data" / "prometheus" / "sample" / "contact.png"
        contact.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (800).to_bytes(4, "big") + (600).to_bytes(4, "big"))
        result = build_publish_package(self.root, self.source_dir, contact_sheet="data/prometheus/sample/contact.png")
        out = self.root / result["output_dir"]
        self.assertTrue((out / "contact-sheet.png").is_file())
        report = json.loads((out / "release-quality-report.json").read_text(encoding="utf-8"))
        visual = report["checks"]["visual_anomaly_review"]["contact_sheet"]
        self.assertEqual(visual["status"], "available")
        self.assertEqual(visual["sha256"], hashlib.sha256(contact.read_bytes()).hexdigest())
        self.assertTrue(visual["human_review_required"])
        self.assertEqual(report["checks"]["visual_content_review"]["status"], "not_recorded")
        self.assertFalse(report["can_publish"])
        with zipfile.ZipFile(out / "publish-package.zip") as archive:
            self.assertIn("contact-sheet.png", archive.namelist())

    def test_prior_package_visual_review_is_not_inherited_by_next_release_package(self):
        from muse.video_human_review import (
            VisualReviewLedger, validate_visual_review_payload,
            verify_visual_review_evidence, CHECKLIST_KEYS,
        )
        generation = self._create_generation()
        contact = self.root / "data" / "prometheus" / "sample" / "contact.png"
        contact.write_bytes(
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (800).to_bytes(4, "big") + (600).to_bytes(4, "big") + b"fixture"
        )
        initiative_id, version_id = generation["product"]["initiative_id"], generation["product"]["version_id"]
        product_dir = self.root / "data" / "products" / initiative_id / version_id
        first = build_publish_package(
            self.root, self.source_dir, output_dir=product_dir / "publish-package-v1",
            contact_sheet="data/prometheus/sample/contact.png",
        )
        evidence = verify_visual_review_evidence(
            product_dir, initiative_id, version_id, self.video,
        )
        payload = validate_visual_review_payload({
            "decision": "approved", "reviewer": "Local Editor", "reviewer_role": "video_editor",
            "rationale": "Reviewed the complete video and every sampled keyframe carefully.",
            "checklist": {key: True for key in CHECKLIST_KEYS},
            "confirmation": "RECORD-VISUAL-REVIEW",
        })
        with VisualReviewLedger(self.root / "data" / "reviews" / "visual-content-reviews.sqlite3") as ledger:
            ledger.append(initiative_id, version_id, payload, evidence)
        second = build_publish_package(
            self.root, self.source_dir, output_dir=product_dir / "publish-package-v2",
            contact_sheet="data/prometheus/sample/contact.png",
        )
        report = json.loads((self.root / second["output_dir"] / "release-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(
            report["checks"]["visual_content_review"]["status"],
            "not_recorded",
        )
        self.assertNotEqual(
            report["checks"]["visual_anomaly_review"]["status"],
            "verified_human_review_digest_scope_bound",
        )
        self.assertFalse(report["can_publish"])
        self.assertIn("account_authorization", report["blocking_items"])

    def test_generated_contact_sheet_is_packaged_and_temp_evidence_is_cleaned(self):
        self._create_generation()
        inventory = type("Inventory", (), {"tools": [
            type("Tool", (), {"tool_id": "ffmpeg", "executable": "ffmpeg.exe"})(),
            type("Tool", (), {"tool_id": "ffprobe", "executable": "ffprobe.exe"})(),
        ]})()
        png = (
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
        )
        def generate(source, output, **kwargs):
            self.assertEqual(Path(source).resolve(), self.video.resolve())
            self.assertEqual(kwargs["ffmpeg_path"], "ffmpeg.exe")
            self.assertEqual(kwargs["ffprobe_path"], "ffprobe.exe")
            Path(output).write_bytes(png)
            return {"status": "completed", "image_decode": "passed"}

        with patch("muse.media.discover_media_tools", return_value=inventory):
            with patch("muse.video_contact_sheet.create_contact_sheet", side_effect=generate):
                result = build_publish_package_with_generated_contact_sheet(self.root, self.source_dir)

        out = self.root / result["output_dir"]
        self.assertTrue((out / "contact-sheet.png").is_file())
        product_relative = json.loads((self.source_dir / "generation.json").read_text(encoding="utf-8"))["product"]["relative_path"]
        self.assertEqual(result["output_dir"], (Path(product_relative) / "publish-package-v1").as_posix())
        self.assertEqual((out / "contact-sheet.png").read_bytes(), png)
        self.assertFalse(any(self.source_dir.glob(".muse-contact-sheet-*")))
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["files"]["contact-sheet.png"],
            hashlib.sha256(png).hexdigest(),
        )
        self.assertFalse(result["can_publish"])

    def test_optional_contact_sheet_failure_is_explicit_and_keeps_draft_non_publishable(self):
        from muse.video_publish_package import build_publish_package_with_optional_generated_contact_sheet

        self._create_generation()
        inventory = type("Inventory", (), {"tools": []})()
        with patch("muse.media.discover_media_tools", return_value=inventory):
            result = build_publish_package_with_optional_generated_contact_sheet(self.root, self.source_dir)

        self.assertEqual(result["contact_sheet_generation"]["status"], "unavailable")
        self.assertIn("FFmpeg and FFprobe", result["contact_sheet_generation"]["reason"])
        self.assertTrue(result["contact_sheet_generation"]["human_review_required"])
        self.assertFalse(result["can_publish"])
        out = self.root / result["output_dir"]
        self.assertFalse((out / "contact-sheet.png").exists())
        quality = json.loads((out / "release-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(quality["checks"]["visual_anomaly_review"]["contact_sheet"]["status"], "not_provided")
    def test_optional_contact_sheet_timeout_is_explicit_and_keeps_draft_non_publishable(self):
        import subprocess
        from muse.video_publish_package import build_publish_package_with_optional_generated_contact_sheet

        self._create_generation()
        inventory = type("Inventory", (), {"tools": [
            type("Tool", (), {"tool_id": "ffmpeg", "executable": "ffmpeg.exe"})(),
            type("Tool", (), {"tool_id": "ffprobe", "executable": "ffprobe.exe"})(),
        ]})()
        with patch("muse.media.discover_media_tools", return_value=inventory):
            with patch("muse.video_contact_sheet.create_contact_sheet",
                       side_effect=subprocess.TimeoutExpired("ffmpeg", 30)):
                result = build_publish_package_with_optional_generated_contact_sheet(self.root, self.source_dir)

        self.assertEqual(result["contact_sheet_generation"]["status"], "unavailable")
        self.assertIn("timed out", result["contact_sheet_generation"]["reason"].lower())
        self.assertTrue(result["contact_sheet_generation"]["human_review_required"])
        self.assertFalse(result["can_publish"])
        self.assertFalse((self.root / result["output_dir"] / "contact-sheet.png").exists())

    def test_optional_contact_sheet_success_records_hash_bound_evidence(self):
        from muse.video_publish_package import build_publish_package_with_optional_generated_contact_sheet

        self._create_generation()
        inventory = type("Inventory", (), {"tools": [
            type("Tool", (), {"tool_id": "ffmpeg", "executable": "ffmpeg.exe"})(),
            type("Tool", (), {"tool_id": "ffprobe", "executable": "ffprobe.exe"})(),
        ]})()
        png = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
               + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture")
        def generate(source, output, **kwargs):
            self.assertEqual(Path(source).resolve(), self.video.resolve())
            Path(output).write_bytes(png)
            return {"status": "completed", "image_decode": "passed"}

        with patch("muse.media.discover_media_tools", return_value=inventory):
            with patch("muse.video_contact_sheet.create_contact_sheet", side_effect=generate):
                result = build_publish_package_with_optional_generated_contact_sheet(self.root, self.source_dir)

        self.assertEqual(result["contact_sheet_generation"]["status"], "completed")
        self.assertEqual(result["contact_sheet_generation"]["sha256"], hashlib.sha256(png).hexdigest())
        self.assertFalse(result["can_publish"])
        out = self.root / result["output_dir"]
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"]["contact-sheet.png"], hashlib.sha256(png).hexdigest())

    def test_contact_sheet_rejects_non_png(self):
        self._create_generation()
        contact = self.root / "data" / "prometheus" / "sample" / "not-image.png"
        contact.write_bytes(b"not a png")
        with self.assertRaisesRegex(VideoPublishPackageError, "valid PNG"):
            build_publish_package(self.root, self.source_dir, contact_sheet="data/prometheus/sample/not-image.png")
    def test_visual_content_review_digest_binds_contact_sheet_hash(self):
        from muse.video_publish_package import _json_bytes, _review_record_status

        video_hash = hashlib.sha256(b"video-v1").hexdigest()
        sheet_hash = hashlib.sha256(b"contact-sheet-v1").hexdigest()
        required = {"video.mp4": video_hash, "contact-sheet.png": sheet_hash}
        manifest_hash = hashlib.sha256(b"release-manifest-v1").hexdigest()
        record = {
            "schema_version": 1, "category": "visual_content", "decision": "approved",
            "reviewer": {"id": "editor-2", "role": "video_editor"},
            "reviewed_at": "2026-11-21T10:00:00+00:00",
            "subject": {"initiative_id": "initiative-1", "version_id": "version-1",
                        "video_sha256": video_hash, "artifact_sha256": required,
                        "release_manifest_sha256": manifest_hash},
            "evidence_refs": ["contact-sheet.png#all-frames"],
            "rationale": "Checked framing, missing subjects, and shot continuity.",
        }
        record["review_sha256"] = hashlib.sha256(_json_bytes(record)).hexdigest()
        args = {"category": "visual_content", "initiative_id": "initiative-1",
                "version_id": "version-1", "video_sha256": video_hash,
                "required_artifacts": required}
        args["release_manifest_sha256"] = manifest_hash
        self.assertEqual(_review_record_status(record, **args), "verified_human_review_digest_scope_bound")
        changed = {**required, "contact-sheet.png": "0" * 64}
        self.assertEqual(_review_record_status(record, **{**args, "required_artifacts": changed}),
                         "evidence_mismatch_or_incomplete")

    def test_release_review_record_is_bound_to_reviewer_scope_and_exact_artifact_hashes(self):
        from muse.video_publish_package import _json_bytes, _review_record_status

        video_hash = hashlib.sha256(b"video-v1").hexdigest()
        subtitle_hash = hashlib.sha256(b"subtitle-v1").hexdigest()
        record = {
            "schema_version": 1, "category": "editorial_and_fact",
            "decision": "approved", "reviewer": {"id": "editor-1", "role": "editor"},
            "reviewed_at": "2026-11-20T12:00:00+00:00",
            "subject": {"initiative_id": "initiative-1", "version_id": "version-1",
                        "video_sha256": video_hash, "artifact_sha256": {"subtitles.srt": subtitle_hash}},
            "evidence_refs": ["source-notes.json#citations"],
            "rationale": "Claims and captions checked against cited source material.",
        }
        record["review_sha256"] = hashlib.sha256(_json_bytes(record)).hexdigest()
        args = {"category": "editorial_and_fact", "initiative_id": "initiative-1",
                "version_id": "version-1", "video_sha256": video_hash,
                "required_artifacts": {"subtitles.srt": subtitle_hash}}
        self.assertEqual(_review_record_status(record, **args), "verified_human_review_digest_scope_bound")
        self.assertEqual(_review_record_status(None, **args), "not_recorded")
        changed = dict(record, rationale="changed after approval")
        self.assertEqual(_review_record_status(changed, **args), "evidence_mismatch_or_incomplete")
        self.assertEqual(_review_record_status(record, **{**args, "version_id": "version-2"}),
                         "evidence_mismatch_or_incomplete")
        self.assertEqual(_review_record_status(record, **{**args, "required_artifacts": {"subtitles.srt": "0" * 64}}),
                         "evidence_mismatch_or_incomplete")
        self.assertEqual(_review_record_status(record, **{**args, "required_artifacts": {"subtitles.srt": ""}}),
                         "evidence_mismatch_or_incomplete")

    def test_release_quality_report_requires_scope_bound_subtitle_human_review_record(self):
        from muse.video_publish_package import _json_bytes, _release_quality_report

        video = self.source_dir / "reviewed.mp4"
        subtitles = self.source_dir / "reviewed.srt"
        video.write_bytes(b"video-v1")
        subtitles.write_bytes(b"subtitle-v1")
        video_hash = hashlib.sha256(video.read_bytes()).hexdigest()
        subtitle_hash = hashlib.sha256(subtitles.read_bytes()).hexdigest()
        review_artifact_hash = hashlib.sha256(b"subtitle-review-document").hexdigest()
        legacy_review = {
            "schema_version": 1, "status": "approved", "human_approved": True,
            "approved_by": "human-editor", "approval_timestamp": "2026-11-20T12:00:00Z",
            "review_sha256": review_artifact_hash, "approved_srt_sha256": subtitle_hash,
            "video_sha256": video_hash,
        }
        product_manifest = {
            "subtitle_review": legacy_review,
            "files": {"subtitle-review.json": review_artifact_hash},
        }
        report = _release_quality_report(
            product_manifest=product_manifest, qa_warnings=[],
            subtitles=subtitles, video=video, initiative_id="initiative-1", version_id="version-1",
        )
        self.assertEqual(report["checks"]["subtitle_review"]["status"], "not_recorded")

        record = {
            "schema_version": 1, "category": "subtitle_review", "decision": "approved",
            "reviewer": {"id": "editor-1", "role": "subtitle_editor"},
            "reviewed_at": "2026-11-20T12:00:00+00:00",
            "subject": {
                "initiative_id": "initiative-1", "version_id": "version-1",
                "video_sha256": video_hash,
                "artifact_sha256": {
                    "subtitles.srt": subtitle_hash,
                    "subtitle-review.json": review_artifact_hash,
                },
            },
            "evidence_refs": ["subtitle-review.json#segments"],
            "rationale": "Every cue was checked against the source narration.",
        }
        record["review_sha256"] = hashlib.sha256(_json_bytes(record)).hexdigest()
        product_manifest["human_reviews"] = {"subtitle_review": record}
        approved = _release_quality_report(
            product_manifest=product_manifest, qa_warnings=[],
            subtitles=subtitles, video=video, initiative_id="initiative-1", version_id="version-1",
        )
        self.assertEqual(
            approved["checks"]["subtitle_review"]["status"],
            "verified_human_review_digest_scope_bound",
        )
        self.assertFalse(approved["can_publish"])

        changed_record = {**record, "review_sha256": "b" * 64}
        tampered = _release_quality_report(
            product_manifest={**product_manifest, "human_reviews": {"subtitle_review": changed_record}},
            qa_warnings=[], subtitles=subtitles, video=video,
            initiative_id="initiative-1", version_id="version-1",
        )
        self.assertEqual(tampered["checks"]["subtitle_review"]["status"], "evidence_mismatch_or_incomplete")
        video.write_bytes(b"video-v2")
        changed = _release_quality_report(
            product_manifest=product_manifest, qa_warnings=[],
            subtitles=subtitles, video=video, initiative_id="initiative-1", version_id="version-1",
        )
        self.assertEqual(changed["checks"]["subtitle_review"]["status"], "evidence_mismatch_or_incomplete")


    def test_blocks_publish_pack_when_technical_qa_did_not_pass(self):
        self._create_generation("failed")
        with self.assertRaisesRegex(VideoPublishPackageError, "passed technical QA"):
            build_publish_package(self.root, self.source_dir)

    def test_rejects_modified_master_video(self):
        self._create_generation()
        self.video.write_bytes(self.video.read_bytes() + b"tampered")
        with self.assertRaisesRegex(VideoPublishPackageError, "SHA-256"):
            build_publish_package(self.root, self.source_dir)

    def test_rejects_unknown_platform_profile(self):
        self._create_generation()
        with self.assertRaisesRegex(VideoPublishPackageError, "supported metadata profiles"):
            build_publish_package(self.root, self.source_dir, platforms=["unknown"])

    def test_refuses_to_overwrite_existing_publish_package(self):
        self._create_generation()
        target = self.source_dir / "already-there"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            build_publish_package(self.root, self.source_dir, output_dir=target)


if __name__ == "__main__":
    unittest.main()
