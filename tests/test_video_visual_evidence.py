from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.video_human_review import VisualReviewEvidenceError, verify_visual_review_evidence
from muse.video_visual_evidence import create_visual_review_evidence_pack


class VideoVisualEvidencePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.products = self.root / "products"
        self.product = self.products / ("a" * 32) / ("b" * 20)
        self.product.mkdir(parents=True)
        self.video = self.product / "video.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypisomfixture")
        self.qa = {"schema_version": 1, "status": "passed", "path": "video.mp4",
                   "artifact_sha256": hashlib.sha256(self.video.read_bytes()).hexdigest()}
        self.qa_path = self.product / "qa-report.json"
        self.qa_path.write_text(json.dumps(self.qa), encoding="utf-8")
        self.manifest = {
            "product_kind": "video_creator", "initiative_id": "a" * 32,
            "version_id": "b" * 20, "status": "testing",
            "quality_gate": {"status": "passed", "passed": True, "ready_to_publish": False,
                             "report": "qa-report.json"},
            "files": {"video.mp4": hashlib.sha256(self.video.read_bytes()).hexdigest(),
                      "qa-report.json": hashlib.sha256(self.qa_path.read_bytes()).hexdigest()},
        }
        self.manifest_path = self.product / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _factory(self, source, target, **kwargs):
        target.write_bytes(
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
        )
        return {"status": "completed", "width": 640, "height": 480, "image_decode": "passed", "output": str(target)}

    @patch("muse.video_visual_evidence.VideoProductBuilder.verify_path")
    def test_creates_hash_bound_evidence_pack_and_verifies_it(self, verify_path):
        verify_path.return_value = {"validated": True, "quality_gate": {"status": "passed", "passed": True}}
        result = create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20,
            contact_sheet_factory=self._factory,
        )
        self.assertEqual(result["status"], "available")
        evidence = verify_visual_review_evidence(
            self.product, "a" * 32, "b" * 20, self.video,
        )
        self.assertEqual(evidence["artifact_sha256"]["video.mp4"], self.manifest["files"]["video.mp4"])
        self.assertEqual(evidence["artifact_sha256"]["contact-sheet.png"],
                         hashlib.sha256((self.product / "visual-review-evidence-v1" / "contact-sheet.png").read_bytes()).hexdigest())
        self.assertEqual(evidence["evidence_type"], "visual-review-evidence-v1")
        self.assertFalse(result["can_publish"])
        repeated = create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20,
            contact_sheet_factory=lambda *args, **kwargs: self.fail("existing evidence must be reused"),
        )
        self.assertTrue(repeated["idempotent"])

    @patch("muse.video_visual_evidence.VideoProductBuilder.verify_path")
    def test_refuses_evidence_when_product_qa_did_not_pass(self, verify_path):
        verify_path.return_value = {"validated": True, "quality_gate": {"status": "failed"}}
        called = []
        result = create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20,
            contact_sheet_factory=lambda *args, **kwargs: called.append(True),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(called, [])
        self.assertFalse((self.product / "visual-review-evidence-v1").exists())

    @patch("muse.video_visual_evidence.VideoProductBuilder.verify_path")
    def test_records_unavailable_contact_sheet_without_claiming_review_ready(self, verify_path):
        verify_path.return_value = {"validated": True, "quality_gate": {"status": "passed", "passed": True}}
        def unavailable(*args, **kwargs):
            raise RuntimeError("ffprobe executable is unavailable")
        result = create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20, contact_sheet_factory=unavailable,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["can_publish"])
        self.assertFalse((self.product / "visual-review-evidence-v1").exists())

    @patch("muse.video_visual_evidence.VideoProductBuilder.verify_path")
    def test_verifier_detects_contact_sheet_tampering(self, verify_path):
        verify_path.return_value = {"validated": True, "quality_gate": {"status": "passed", "passed": True}}
        create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20, contact_sheet_factory=self._factory,
        )
        sheet = self.product / "visual-review-evidence-v1" / "contact-sheet.png"
        sheet.write_bytes(sheet.read_bytes() + b"tampered")
        with self.assertRaises(VisualReviewEvidenceError):
            verify_visual_review_evidence(self.product, "a" * 32, "b" * 20, self.video)

    @patch("muse.video_visual_evidence.VideoProductBuilder.verify_path")
    def test_verifier_detects_source_manifest_drift(self, verify_path):
        verify_path.return_value = {"validated": True, "quality_gate": {"status": "passed", "passed": True}}
        create_visual_review_evidence_pack(
            self.products, "a" * 32, "b" * 20, contact_sheet_factory=self._factory,
        )
        self.manifest["title"] = "tampered"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaises(VisualReviewEvidenceError):
            verify_visual_review_evidence(self.product, "a" * 32, "b" * 20, self.video)


if __name__ == "__main__":
    unittest.main()
