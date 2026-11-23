from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_recipe_trace import build_video_recipe_trace
from muse.status_api import create_status_server


def _brief_digest(brief):
    return hashlib.sha256(json.dumps(brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class VideoRecipeTraceTests(unittest.TestCase):
    def _brief(self):
        return {
            "candidate_id": "candidate-01-abcdef123456",
            "recipe_sha256": "a" * 64,
            "title": "Traceable preview",
            "prompt": "A quiet mountain at dawn",
            "mapping_report": {"status": "ready", "mapped": [{"source": "scenes", "target": "scenes"}]},
        }

    def _seed(self, root, *, corrupt_digest=False):
        initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
        tasks_db = root / "data" / "tasks" / "muse.sqlite3"
        brief = self._brief()
        with InitiativeStore(initiatives_db) as store:
            initiative = store.propose(
                "Trace initiative", kind="creative_output", creator_value="trace",
                concept=json.dumps({"creator_brief": {"video": brief}}),
                first_step="trace", expected_output="trace",
            )
        payload = {
            "kind": "business_opportunity_video_approval",
            "initiative_id": initiative["id"],
            "attempt_id": "trace-attempt",
            "approval_scope": "one_video_attempt_only",
            "video_brief": brief,
            "video_brief_sha256": ("0" * 64) if corrupt_digest else _brief_digest(brief),
        }
        with TaskQueue(tasks_db) as queue:
            ticket = queue.enqueue("Approve preview", payload, require_approval=True)
            queue.approve(ticket["id"], approved_by="user")
        with InitiativeStore(initiatives_db) as store:
            store.start_video_attempt(
                "trace-attempt", mode="prompt_to_video", initiative_id=initiative["id"],
                approval_task_id=ticket["id"], recipe_sha256=brief["recipe_sha256"],
            )
            events_before = store.list_video_attempt_events("trace-attempt")
        return initiatives_db, ticket["id"], events_before, brief

    def test_trace_is_read_only_and_verifies_persisted_approval_and_brief(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiatives_db, ticket_id, events_before, brief = self._seed(root)
            trace = build_video_recipe_trace(root, "trace-attempt")
            self.assertTrue(trace["read_only"])
            self.assertTrue(trace["chain_consistent"])
            self.assertEqual(trace["candidate"]["candidate_id"], brief["candidate_id"])
            self.assertEqual(trace["candidate"]["recipe_sha256"], brief["recipe_sha256"])
            self.assertEqual(trace["approval"]["ticket_id"], ticket_id)
            self.assertTrue(trace["approval"]["human_decision_verified"])
            self.assertEqual(trace["product"]["status"], "not_built")
            self.assertFalse(trace["release_review"]["can_publish"])
            with InitiativeStore(initiatives_db) as store:
                self.assertEqual(store.list_video_attempt_events("trace-attempt"), events_before)
            with closing(sqlite3.connect(initiatives_db)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM initiative_video_attempts WHERE attempt_id='trace-attempt'"
                    ).fetchone()[0],
                    "queued",
                )

    def test_trace_fails_closed_when_approval_brief_hash_does_not_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._seed(root, corrupt_digest=True)
            trace = build_video_recipe_trace(root, "trace-attempt")
            self.assertFalse(trace["approval"]["human_decision_verified"])
            self.assertFalse(trace["chain_consistent"])

    def test_trace_fails_closed_when_approval_timestamp_is_not_bound_to_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, ticket_id, _, _ = self._seed(root)
            tasks_db = root / "data" / "tasks" / "muse.sqlite3"
            with closing(sqlite3.connect(tasks_db)) as connection:
                row = connection.execute("SELECT payload_json FROM tasks WHERE id=?", (ticket_id,)).fetchone()
                payload = json.loads(row[0])
                payload["_approval"]["approved_at"] = "2000-01-01T00:00:00+00:00"
                connection.execute(
                    "UPDATE tasks SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), ticket_id),
                )
                connection.commit()
            trace = build_video_recipe_trace(root, "trace-attempt")
            self.assertFalse(trace["approval"]["human_decision_verified"])
            self.assertFalse(trace["chain_consistent"])

    def test_trace_rejects_approval_ticket_bound_to_a_different_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiatives_db, ticket_id, _, _ = self._seed(root)
            tasks_db = root / "data" / "tasks" / "muse.sqlite3"
            with closing(sqlite3.connect(tasks_db)) as connection:
                row = connection.execute(
                    "SELECT payload_json FROM tasks WHERE id=?", (ticket_id,)
                ).fetchone()
                payload = json.loads(row[0])
                payload["attempt_id"] = "some-other-attempt"
                connection.execute(
                    "UPDATE tasks SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), ticket_id),
                )
                connection.commit()
            trace = build_video_recipe_trace(root, "trace-attempt")
            self.assertFalse(trace["approval"]["human_decision_verified"])
            self.assertFalse(trace["chain_consistent"])

    def test_trace_returns_none_for_unknown_attempt_and_rejects_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "initiatives").mkdir(parents=True)
            with InitiativeStore(root / "data" / "initiatives" / "muse.sqlite3"):
                pass
            self.assertIsNone(build_video_recipe_trace(root, "missing-attempt"))
            with self.assertRaisesRegex(ValueError, "safe identifier"):
                build_video_recipe_trace(root, "../unsafe")

    def test_authenticated_status_api_exposes_readonly_recipe_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
            products_dir = root / "data" / "products"
            products_dir.mkdir(parents=True)
            brief = self._brief()
            with InitiativeStore(initiatives_db) as store:
                initiative = store.propose(
                    "API trace initiative", kind="creative_output", creator_value="trace",
                    concept=json.dumps({"creator_brief": {"video": brief}}),
                    first_step="trace", expected_output="trace",
                )
                store.start_video_attempt(
                    "api-trace", mode="prompt_to_video", initiative_id=initiative["id"],
                    recipe_sha256=brief["recipe_sha256"],
                )
            task_db = root / "data" / "tasks" / "muse.sqlite3"
            server, token = create_status_server(
                task_db=task_db, progress_db=root / "data" / "progress.sqlite3",
                capability_db=root / "data" / "capabilities.sqlite3", products_dir=products_dir,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/v1/video-recipe-traces/api-trace",
                    headers={"Authorization": "Bearer " + token},
                )
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["trace"]["read_only"])
                self.assertEqual(payload["trace"]["candidate"]["recipe_sha256"], brief["recipe_sha256"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_succeeded_product_and_publish_pack_are_linked_but_never_authorize_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
            products_root = root / "data" / "products"
            product_id = "b" * 32
            version_id = "c" * 20
            manifest_hash = "d" * 64
            video_bytes = b"visual review master fixture"
            mp4_hash = hashlib.sha256(video_bytes).hexdigest()
            package_hash = "f" * 64
            recipe_hash = "a" * 64
            brief = self._brief()
            with InitiativeStore(initiatives_db) as store:
                initiative = store.propose(
                    "Completed trace initiative", kind="creative_output", creator_value="trace",
                    concept=json.dumps({"creator_brief": {"video": brief}}),
                    first_step="trace", expected_output="trace",
                )
                store.start_video_attempt(
                    "completed-trace", mode="prompt_to_video", initiative_id=initiative["id"],
                    recipe_sha256=recipe_hash,
                )
                store.mark_video_attempt_running("completed-trace")
                store.complete_video_attempt(
                    "completed-trace", initiative_id=initiative["id"], product_id=product_id,
                    version_id=version_id, mp4_sha256=mp4_hash, package_sha256=package_hash,
                    manifest_sha256=manifest_hash,
                )
            product_dir = products_root / product_id / version_id
            release_dir = product_dir / "publish-package-v1"
            release_dir.mkdir(parents=True)
            (product_dir / "video.mp4").write_bytes(video_bytes)
            contact_sheet = (
                b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
            )
            (release_dir / "contact-sheet.png").write_bytes(contact_sheet)
            sheet_hash = hashlib.sha256(contact_sheet).hexdigest()
            quality_report = {
                "schema_version": 1, "overall_status": "blocked_pending_human_review",
                "can_publish": False, "blocking_items": ["editorial_and_fact_review", "visual_anomaly_review"],
                "checks": {
                    "editorial_and_fact_review": {"status": "pending"},
                    "visual_content_review": {"status": "not_recorded"},
                    "visual_anomaly_review": {"status": "heuristics_only_review_required", "contact_sheet": {
                        "status": "available", "sha256": sheet_hash, "human_review_required": True,
                    }},
                },
            }
            quality_report_bytes = json.dumps(quality_report, sort_keys=True).encode("utf-8")
            quality_report_hash = hashlib.sha256(quality_report_bytes).hexdigest()
            (release_dir / "release-quality-report.json").write_bytes(quality_report_bytes)
            (release_dir / "release-checklist.json").write_text(json.dumps({
                "release_state": "draft_only", "can_publish": False,
                "editorial_and_fact_review": "pending",
                "visual_and_model_rights_review": "pending",
                "privacy_and_person_release_review": "pending",
                "account_authorization": "not_connected", "official_publish_api": "not_called",
            }), encoding="utf-8")
            (release_dir / "manifest.json").write_text(json.dumps({
                "package_type": "muse_manual_platform_publish_pack",
                "initiative_id": product_id, "version_id": version_id,
                "source_video_sha256": mp4_hash, "muse_product_zip_sha256": package_hash,
                "release_state": "draft_only", "can_publish": False,
                "release_quality_report": quality_report,
                "files": {
                    "video.mp4": mp4_hash, "contact-sheet.png": sheet_hash,
                    "release-quality-report.json": quality_report_hash,
                },
            }), encoding="utf-8")
            from muse.video_human_review import (
                CHECKLIST_KEYS, VisualReviewLedger, validate_visual_review_payload,
                verify_visual_review_evidence,
            )
            visual_evidence = verify_visual_review_evidence(
                product_dir, product_id, version_id, product_dir / "video.mp4",
            )
            review_payload = validate_visual_review_payload({
                "decision": "approved", "reviewer": "Trace Test Editor",
                "reviewer_role": "video_editor",
                "rationale": "Reviewed the complete video and all contact sheet keyframes.",
                "checklist": {key: True for key in CHECKLIST_KEYS},
                "confirmation": "RECORD-VISUAL-REVIEW",
            })
            with VisualReviewLedger(root / "data" / "reviews" / "visual-content-reviews.sqlite3") as ledger:
                visual_record = ledger.append(product_id, version_id, review_payload, visual_evidence)
            verified = {
                "validated": True, "recipe_sha256": recipe_hash,
                "manifest_sha256": manifest_hash, "mp4_sha256": mp4_hash,
                "package_sha256": package_hash, "quality_gate": {"status": "passed"},
            }
            with patch("muse.video_product_builder.VideoProductBuilder.verify_path", return_value=verified):
                trace = build_video_recipe_trace(root, "completed-trace")
            self.assertTrue(trace["chain_consistent"], msg=json.dumps(trace, sort_keys=True))
            self.assertEqual(trace["product"]["status"], "verified")
            self.assertEqual(trace["release_review"]["status"], "draft_only")
            self.assertTrue(trace["release_review"]["quality_report_matches_package"])
            self.assertEqual(trace["release_review"]["overall_status"], "blocked_pending_human_review")
            self.assertEqual(trace["release_review"]["blocking_items"], ["editorial_and_fact_review"])
            self.assertEqual(trace["release_review"]["quality_report_sha256"], quality_report_hash)
            self.assertTrue(trace["release_review"]["visual_content_review_live_overlay"])
            self.assertEqual(trace["release_review"]["checks"]["visual_content_review"]["record_id"], visual_record["record_id"])
            self.assertEqual(trace["release_review"]["checks"]["visual_content_review"]["status"], "verified_human_review_digest_scope_bound")
            self.assertEqual(trace["release_review"]["official_publish_api"], "not_called")
            self.assertFalse(trace["release_review"]["can_publish"])


    def test_generic_visual_evidence_overlay_never_claims_release_ready(self):
        import hashlib
        from muse.video_human_review import (
            CHECKLIST_KEYS, VisualReviewLedger, validate_visual_review_payload,
            verify_visual_review_evidence,
        )
        from muse.video_visual_evidence import create_visual_review_evidence_pack

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiatives_db = root / "data" / "initiatives" / "muse.sqlite3"
            products_root = root / "data" / "products"
            product_id, version_id = "e" * 32, "f" * 20
            recipe_hash, manifest_hash, package_hash = "a" * 64, "d" * 64, "f" * 64
            video_bytes = bytes.fromhex("000000186674797069736f6d66697874757265")
            video_hash = hashlib.sha256(video_bytes).hexdigest()
            brief = self._brief()
            with InitiativeStore(initiatives_db) as store:
                initiative = store.propose(
                    "Generic evidence trace", kind="creative_output", creator_value="trace",
                    concept=json.dumps({"creator_brief": {"video": brief}}),
                    first_step="trace", expected_output="trace",
                )
                store.start_video_attempt(
                    "generic-evidence-trace", mode="prompt_to_video",
                    initiative_id=initiative["id"], recipe_sha256=recipe_hash,
                )
                store.mark_video_attempt_running("generic-evidence-trace")
                store.complete_video_attempt(
                    "generic-evidence-trace", initiative_id=initiative["id"],
                    product_id=product_id, version_id=version_id,
                    mp4_sha256=video_hash, package_sha256=package_hash,
                    manifest_sha256=manifest_hash,
                )

            product_dir = products_root / product_id / version_id
            product_dir.mkdir(parents=True)
            video = product_dir / "video.mp4"
            video.write_bytes(video_bytes)
            qa = {"schema_version": 1, "status": "passed", "path": "video.mp4",
                  "artifact_sha256": video_hash}
            qa_path = product_dir / "qa-report.json"
            qa_path.write_text(json.dumps(qa), encoding="utf-8")
            product_manifest = {
                "product_kind": "video_creator", "initiative_id": product_id,
                "version_id": version_id, "status": "testing",
                "quality_gate": {"status": "passed", "passed": True, "ready_to_publish": False},
                "files": {
                    "video.mp4": video_hash,
                    "qa-report.json": hashlib.sha256(qa_path.read_bytes()).hexdigest(),
                },
            }
            (product_dir / "manifest.json").write_text(json.dumps(product_manifest), encoding="utf-8")

            def fake_contact_sheet(source, target, **kwargs):
                target.write_bytes(
                    b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                    + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"fixture"
                )
                return {"status": "completed", "width": 640, "height": 480, "image_decode": "passed"}

            verified = {
                "validated": True, "recipe_sha256": recipe_hash,
                "manifest_sha256": manifest_hash, "mp4_sha256": video_hash,
                "package_sha256": package_hash,
                "quality_gate": {"status": "passed", "passed": True, "ready_to_publish": False},
            }
            with patch("muse.video_product_builder.VideoProductBuilder.verify_path", return_value=verified):
                pack = create_visual_review_evidence_pack(
                    products_root, product_id, version_id, contact_sheet_factory=fake_contact_sheet,
                )
                self.assertEqual(pack["status"], "available")
                evidence = verify_visual_review_evidence(product_dir, product_id, version_id, video)
                payload = validate_visual_review_payload({
                    "decision": "approved", "reviewer": "Trace Editor",
                    "reviewer_role": "video_editor",
                    "rationale": "Reviewed the complete video and its contact sheet carefully.",
                    "checklist": {key: True for key in CHECKLIST_KEYS},
                    "confirmation": "RECORD-VISUAL-REVIEW",
                })
                with VisualReviewLedger(
                    "data/reviews/visual-content-reviews.sqlite3", workspace_root=root,
                ) as ledger:
                    ledger.append(product_id, version_id, payload, evidence)
                self.assertTrue((root / "data" / "reviews" / "visual-content-reviews.sqlite3").is_file())
                self.assertTrue((product_dir / "visual-review-evidence-v1").is_dir())
                trace = build_video_recipe_trace(root, "generic-evidence-trace")
                trace = build_video_recipe_trace(root, "generic-evidence-trace")

            self.assertTrue(trace["chain_consistent"])
            self.assertEqual(trace["release_review"]["status"], "not_prepared")
            self.assertTrue(trace["release_review"].get("visual_content_review_live_overlay"), trace["release_review"])
            self.assertEqual(trace["release_review"]["overall_status"], "blocked_pending_release_package")
            self.assertEqual(trace["release_review"]["blocking_items"], ["release_package_not_prepared"])
            self.assertEqual(
                trace["release_review"]["checks"]["visual_content_review"]["status"],
                "verified_human_review_digest_scope_bound",
            )
            self.assertFalse(trace["release_review"]["can_publish"])

if __name__ == "__main__":
    unittest.main()
