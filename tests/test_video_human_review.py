from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.video_human_review import (
    CHECKLIST_KEYS, VisualReviewError, VisualReviewLedger,
    validate_visual_review_payload, verify_visual_review_evidence,
)


def _payload(decision="approved"):
    return {
        "decision": decision, "reviewer": "Local Editor",
        "reviewer_role": "video_editor",
        "rationale": "Reviewed the complete video and the contact sheet carefully.",
        "checklist": {key: True for key in CHECKLIST_KEYS},
        "confirmation": "RECORD-VISUAL-REVIEW",
        "findings": ([{
            "timecode_ms": 12500, "shot_id": "shot-03", "severity": "major",
            "category": "continuity", "description": "镜头切换时人物动作出现明显跳变",
            "expected_fix": "重新衔接该镜头前后动作并复核切点",
        }] if decision == "changes_requested" else []),
    }


class VisualHumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.product = self.root / "product"
        self.release = self.product / "publish-package-v1"
        self.release.mkdir(parents=True)
        self.video = self.product / "video.mp4"
        self.video.write_bytes(b"video-master-v1")
        self.sheet = self.release / "contact-sheet.png"
        self.sheet.write_bytes(
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (800).to_bytes(4, "big") + (600).to_bytes(4, "big") + b"fixture"
        )
        video_hash = hashlib.sha256(self.video.read_bytes()).hexdigest()
        sheet_hash = hashlib.sha256(self.sheet.read_bytes()).hexdigest()
        report = {"can_publish": False, "checks": {"visual_anomaly_review": {
            "contact_sheet": {"status": "available", "sha256": sheet_hash}
        }}}
        report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        (self.release / "release-quality-report.json").write_bytes(report_bytes)
        manifest = {
            "package_type": "muse_manual_platform_publish_pack",
            "initiative_id": "a" * 32, "version_id": "b" * 20,
            "release_state": "draft_only", "can_publish": False,
            "source_video_sha256": video_hash,
            "files": {
                "video.mp4": video_hash, "contact-sheet.png": sheet_hash,
                "release-quality-report.json": hashlib.sha256(report_bytes).hexdigest(),
            },
        }
        (self.release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.evidence = verify_visual_review_evidence(
            self.product, "a" * 32, "b" * 20, self.video,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_payload_requires_all_checks_and_explicit_confirmation(self):
        accepted = validate_visual_review_payload(_payload())
        self.assertEqual(accepted["decision"], "approved")
        incomplete = _payload()
        incomplete["checklist"][CHECKLIST_KEYS[0]] = False
        with self.assertRaisesRegex(VisualReviewError, "complete every"):
            validate_visual_review_payload(incomplete)
        unconfirmed = _payload()
        unconfirmed["confirmation"] = "yes"
        with self.assertRaisesRegex(VisualReviewError, "confirmation"):
            validate_visual_review_payload(unconfirmed)

    def test_changes_requested_requires_bounded_timecoded_findings(self):
        accepted = validate_visual_review_payload(_payload("changes_requested"))
        self.assertEqual(accepted["findings"][0]["timecode_ms"], 12500)
        missing = _payload("changes_requested")
        missing["findings"] = []
        with self.assertRaisesRegex(VisualReviewError, "at least one structured finding"):
            validate_visual_review_payload(missing)
        malformed = _payload("changes_requested")
        malformed["findings"][0]["timecode_ms"] = -1
        with self.assertRaisesRegex(VisualReviewError, "timecode_ms"):
            validate_visual_review_payload(malformed)
        approved = _payload("approved")
        approved["findings"] = _payload("changes_requested")["findings"]
        with self.assertRaisesRegex(VisualReviewError, "approved review"):
            validate_visual_review_payload(approved)

    def test_revision_brief_is_deterministic_and_version_bound(self):
        from muse.video_human_review import build_visual_revision_brief
        record = {
            "record_id": "r" * 32, "review_sha256": "a" * 64, "decision": "changes_requested",
            "reviewed_at": "2026-11-21T12:00:00+00:00",
            "subject": {"initiative_id": "a" * 32, "version_id": "b" * 20, "video_sha256": "c" * 64},
            "findings": _payload("changes_requested")["findings"],
        }
        first = build_visual_revision_brief(record)
        self.assertEqual(first, build_visual_revision_brief(record))
        self.assertEqual(first["source_version"], "b" * 20)
        self.assertEqual(first["tasks"][0]["timecode_ms"], 12500)
        self.assertEqual(first["tasks"][0]["status"], "open")
        self.assertIsNone(build_visual_revision_brief({**record, "decision": "approved"}))

    def test_evidence_verification_binds_video_and_contact_sheet(self):
        self.assertEqual(set(self.evidence["artifact_sha256"]), {"video.mp4", "contact-sheet.png"})
        self.sheet.write_bytes(self.sheet.read_bytes() + b"tampered")
        with self.assertRaisesRegex(VisualReviewError, "hash-mismatched"):
            verify_visual_review_evidence(self.product, "a" * 32, "b" * 20, self.video)

    def test_ledger_appends_and_preserves_history(self):
        path = self.root / "reviews.sqlite3"
        with VisualReviewLedger(path) as ledger:
            first = ledger.append("a" * 32, "b" * 20, validate_visual_review_payload(_payload()), self.evidence)
            second = ledger.append("a" * 32, "b" * 20, validate_visual_review_payload(_payload("changes_requested")), self.evidence)
            history = ledger.list_reviews("a" * 32, "b" * 20)
            self.assertEqual([item["record_id"] for item in history], [first["record_id"], second["record_id"]])
            self.assertEqual(history[-1]["decision"], "changes_requested")
            self.assertEqual(history[-1]["findings"][0]["timecode_ms"], 12500)
            self.assertEqual(history[-1]["findings"][0]["shot_id"], "shot-03")
            self.assertEqual(history[-1]["reviewer"]["identity_assurance"], "self_asserted_local_session")
            self.assertEqual(history[-1]["subject"]["artifact_sha256"], self.evidence["artifact_sha256"])
        connection = sqlite3.connect(path)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            connection.execute("UPDATE visual_content_reviews SET decision='approved'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            connection.execute("DELETE FROM visual_content_reviews")
        connection.close()

    def test_ledger_detects_out_of_band_record_tampering(self):
        path = self.root / "tampered.sqlite3"
        with VisualReviewLedger(path) as ledger:
            ledger.append("a" * 32, "b" * 20, validate_visual_review_payload(_payload()), self.evidence)
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER visual_reviews_no_update")
        row = connection.execute("SELECT record_id, record_json FROM visual_content_reviews").fetchone()
        record = json.loads(row[1])
        record["rationale"] = "altered outside the append-only API"
        connection.execute(
            "UPDATE visual_content_reviews SET record_json=? WHERE record_id=?",
            (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", row[0]),
        )
        connection.commit()
        connection.close()
        with VisualReviewLedger(path) as ledger:
            with self.assertRaisesRegex(VisualReviewError, "digest is invalid"):
                ledger.list_reviews("a" * 32, "b" * 20)

    def test_ledger_path_rejects_symlinked_workspace_directories(self):
        from muse.video_human_review import VisualReviewError
        workspace = self.root / "workspace"
        workspace.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (workspace / "data").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable on this Windows account")
        with self.assertRaisesRegex(VisualReviewError, "cannot be symlinks"):
            VisualReviewLedger("data/reviews/visual-content-reviews.sqlite3", workspace_root=workspace)

    def test_release_manifest_hash_is_part_of_review_scope(self):
        from muse.video_publish_package import _review_record_status
        with VisualReviewLedger(self.root / "manifest-bound.sqlite3") as ledger:
            record = ledger.append("a" * 32, "b" * 20, validate_visual_review_payload(_payload()), self.evidence)
        kwargs = dict(
            category="visual_content", initiative_id="a" * 32, version_id="b" * 20,
            video_sha256=self.evidence["video_sha256"],
            required_artifacts=self.evidence["artifact_sha256"],
        )
        self.assertEqual(
            _review_record_status(record, **kwargs, release_manifest_sha256=self.evidence["release_manifest_sha256"]),
            "verified_human_review_digest_scope_bound",
        )
        self.assertEqual(
            _review_record_status(record, **kwargs, release_manifest_sha256="0" * 64),
            "evidence_mismatch_or_incomplete",
        )

    def test_ledger_record_digest_is_compatible_with_release_gate(self):
        from muse.video_publish_package import _review_record_status
        with VisualReviewLedger(self.root / "reviews.sqlite3") as ledger:
            record = ledger.append("a" * 32, "b" * 20, validate_visual_review_payload(_payload()), self.evidence)
        self.assertEqual(
            _review_record_status(
                record, category="visual_content", initiative_id="a" * 32,
                version_id="b" * 20, video_sha256=self.evidence["video_sha256"],
                required_artifacts=self.evidence["artifact_sha256"],
                release_manifest_sha256=self.evidence["release_manifest_sha256"],
            ),
            "verified_human_review_digest_scope_bound",
        )
        rejected = {**record, "decision": "rejected"}
        rejected.pop("review_sha256")
        rejected["review_sha256"] = hashlib.sha256((json.dumps(rejected, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")).hexdigest()
        self.assertEqual(
            _review_record_status(
                rejected, category="visual_content",
                initiative_id="a" * 32, version_id="b" * 20,
                video_sha256=self.evidence["video_sha256"],
                required_artifacts=self.evidence["artifact_sha256"],
                release_manifest_sha256=self.evidence["release_manifest_sha256"],
            ),
            "human_review_rejected",
        )


if __name__ == "__main__":
    unittest.main()
