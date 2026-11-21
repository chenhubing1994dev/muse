from pathlib import Path
import tempfile
import sqlite3
import unittest

from muse.task_queue import TaskQueue



class TaskQueueTests(unittest.TestCase):


    def _create_claimed_contextual_review(self, queue: TaskQueue):
        import hashlib
        from muse.contextual_inquiry import ContextualInquiry, Hypothesis, Observation, SourceKind

        inquiry = ContextualInquiry(
            "queue-review-case", "Review a bounded case.",
            observations=(Observation("obs-1", SourceKind.USER_QUOTE, "Private observation.", "source:1"),),
            hypotheses=(Hypothesis(
                "hyp-1", "A tentative hypothesis.", ("obs-1",), confidence=0.4,
                falsification_condition="Independent evidence contradicts it.",
            ),),
        )
        case_envelope = {
            "version": 1, "protection": "windows-dpapi-current-user",
            "ciphertext_b64": "Y2FzZS1jaXBoZXJ0ZXh0",
        }
        task = queue.enqueue(
            "Contextual inquiry review",
            {
                "kind": "contextual_inquiry_review",
                "case_context_attached": False,
                "case_context_envelope_stored": True,
                "case_context_envelope": case_envelope,
                "context_status": "encrypted_pending_handler",
                "requires_context_reentry": True,
                "long_term_storage_performed": False,
            },
            require_approval=True,
            idempotency_key=(
                "contextual-inquiry-review:"
                + hashlib.sha256(inquiry.inquiry_id.encode("utf-8")).hexdigest()
            ),
        )
        authorized = queue.authorize_contextual_inquiry_review_only(
            task["id"], authorized_by="operator", expected_updated_at=task["updated_at"],
        )
        claimed = queue.claim_contextual_inquiry_review_only(
            task["id"], worker_id="review-worker",
            expected_updated_at=authorized["task"]["updated_at"],
        )
        return claimed, inquiry, case_envelope

    def _review_result_envelope_and_plaintext(self, inquiry):
        import json

        result = {
            "schema_version": 1,
            "inquiry_id": inquiry.inquiry_id,
            "summary": "Review remains provisional.",
            "findings": [{
                "finding_id": "finding-1",
                "statement": "The observation is a candidate, not a verdict.",
                "observation_refs": ["obs-1"],
                "hypothesis_refs": ["hyp-1"],
                "assessment": "unresolved",
                "rationale": "Independent corroboration is still required.",
            }],
            "unknowns": ["Whether an independent source corroborates the claim."],
        }
        plaintext = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        envelope = {
            "version": 1, "protection": "windows-dpapi-current-user",
            "ciphertext_b64": "cmV2aWV3LXJlc3VsdC1jaXBoZXJ0ZXh0",
        }
        return envelope, plaintext

    def test_contextual_review_result_is_attached_encrypted_and_returns_to_human_approval(self) -> None:
        import json
        from unittest.mock import patch
        from muse.contextual_inquiry_security import _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, case_envelope = self._create_claimed_contextual_review(queue)
                result_envelope, result_plaintext = self._review_result_envelope_and_plaintext(inquiry)
                case_plaintext = _serialize_contextual_inquiry(inquiry)

                def decrypt(envelope):
                    if envelope == case_envelope:
                        return case_plaintext
                    if envelope == result_envelope:
                        return result_plaintext
                    raise ValueError("unexpected test envelope")

                with patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt):
                    completed = queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="review-worker",
                        expected_updated_at=claimed["updated_at"],
                        encrypted_review_result=result_envelope,
                    )

                self.assertEqual(completed["status"], "waiting_approval")
                self.assertIsNone(completed["lease_owner"])
                self.assertIsNone(completed["lease_expires_at"])
                self.assertEqual(completed["payload"]["review_result_envelope"], result_envelope)
                self.assertTrue(completed["payload"]["case_context_attached"])
                self.assertFalse(completed["payload"]["requires_context_reentry"])
                self.assertEqual(completed["payload"]["context_status"], "validated_attached")
                self.assertNotIn("_result", completed["payload"])
                self.assertNotIn("_approved", completed["payload"])
                self.assertNotIn(result_plaintext.decode("utf-8"), json.dumps(completed["payload"]))
                events = queue.events(claimed["id"])
                attached = [event for event in events if event["event_type"] == "contextual_inquiry.context_attached"]
                self.assertEqual(len(attached), 1)
                self.assertEqual(attached[0]["actor"], "contextual-inquiry-review-handler")
                self.assertEqual(attached[0]["payload"], {"attachment_state": "validated"})
                self.assertNotIn(result_plaintext.decode("utf-8"), json.dumps(events))

                approved = queue.approve(claimed["id"], approved_by="human-operator")
                self.assertEqual(approved["status"], "queued")
                self.assertTrue(approved["payload"]["_approved"])

    def test_contextual_review_result_reader_is_read_only_and_requires_completed_review(self) -> None:
        import json
        from unittest.mock import patch
        from muse.contextual_inquiry_security import _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, case_envelope = self._create_claimed_contextual_review(queue)
                with self.assertRaisesRegex(PermissionError, "not ready for human review"):
                    queue.load_contextual_inquiry_review_result(claimed["id"])

                result_envelope, result_plaintext = self._review_result_envelope_and_plaintext(inquiry)
                case_plaintext = _serialize_contextual_inquiry(inquiry)

                def decrypt(envelope):
                    if envelope == case_envelope:
                        return case_plaintext
                    if envelope == result_envelope:
                        return result_plaintext
                    raise ValueError("unexpected test envelope")

                with patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt):
                    completed = queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="review-worker",
                        expected_updated_at=claimed["updated_at"],
                        encrypted_review_result=result_envelope,
                    )
                    before_events = queue.events(claimed["id"])
                    result = queue.load_contextual_inquiry_review_result(claimed["id"])
                    after = queue.get(claimed["id"])
                    after_events = queue.events(claimed["id"])

                self.assertEqual(result, json.loads(result_plaintext))
                self.assertEqual(after["updated_at"], completed["updated_at"])
                self.assertEqual(after_events, before_events)
                self.assertNotIn(result_plaintext.decode("utf-8"), json.dumps(after["payload"]))
                self.assertNotIn(result_plaintext.decode("utf-8"), json.dumps(after_events))

                queue.approve(claimed["id"], approved_by="human-operator")
                with self.assertRaisesRegex(PermissionError, "not ready for human review"):
                    queue.load_contextual_inquiry_review_result(claimed["id"])

    def test_contextual_review_result_reader_rejects_missing_claim_audit(self) -> None:
        from unittest.mock import patch
        from muse.contextual_inquiry_security import _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, case_envelope = self._create_claimed_contextual_review(queue)
                result_envelope, result_plaintext = self._review_result_envelope_and_plaintext(inquiry)
                case_plaintext = _serialize_contextual_inquiry(inquiry)

                def decrypt(envelope):
                    return case_plaintext if envelope == case_envelope else result_plaintext

                with patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt):
                    queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="review-worker",
                        expected_updated_at=claimed["updated_at"],
                        encrypted_review_result=result_envelope,
                    )
                queue._connection.execute(
                    "DELETE FROM task_events WHERE task_id = ? AND event_type = ?",
                    (claimed["id"], "assignment.contextual_review_only_claimed"),
                )
                queue._connection.commit()
                with patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt):
                    with self.assertRaisesRegex(PermissionError, "claim audit"):
                        queue.load_contextual_inquiry_review_result(claimed["id"])

    def test_contextual_review_result_rejects_wrong_worker_stale_revision_and_expired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, _ = self._create_claimed_contextual_review(queue)
                envelope, _ = self._review_result_envelope_and_plaintext(inquiry)
                with self.assertRaisesRegex(PermissionError, "current Worker lease owner"):
                    queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="other-worker",
                        expected_updated_at=claimed["updated_at"], encrypted_review_result=envelope,
                    )
                with self.assertRaisesRegex(RuntimeError, "revision conflict"):
                    queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="review-worker",
                        expected_updated_at=claimed["updated_at"] + "-stale",
                        encrypted_review_result=envelope,
                    )
                queue._connection.execute(
                    "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00+00:00", claimed["id"]),
                )
                queue._connection.commit()
                expired = queue.get(claimed["id"])
                with self.assertRaisesRegex(PermissionError, "lease has expired"):
                    queue.complete_contextual_inquiry_review_only(
                        claimed["id"], worker_id="review-worker",
                        expected_updated_at=expired["updated_at"], encrypted_review_result=envelope,
                    )
                self.assertEqual(queue.get(claimed["id"])["status"], "running")

    def test_contextual_review_result_rejects_invalid_plaintext_without_partial_attachment(self) -> None:
        from unittest.mock import patch
        from muse.contextual_inquiry_security import _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, case_envelope = self._create_claimed_contextual_review(queue)
                result_envelope, _ = self._review_result_envelope_and_plaintext(inquiry)
                case_plaintext = _serialize_contextual_inquiry(inquiry)
                with patch(
                    "muse.contextual_inquiry_security.unprotect_case_bytes",
                    side_effect=lambda envelope: case_plaintext if envelope == case_envelope else b"not-json",
                ):
                    with self.assertRaisesRegex(Exception, "not valid JSON"):
                        queue.complete_contextual_inquiry_review_only(
                            claimed["id"], worker_id="review-worker",
                            expected_updated_at=claimed["updated_at"],
                            encrypted_review_result=result_envelope,
                        )
                unchanged = queue.get(claimed["id"])
                self.assertEqual(unchanged["status"], "running")
                self.assertNotIn("review_result_envelope", unchanged["payload"])
                self.assertFalse(any(
                    event["event_type"] == "contextual_inquiry.context_attached"
                    for event in queue.events(claimed["id"])
                ))


    def test_contextual_review_result_rejects_verdict_promotion(self) -> None:
        import json
        from unittest.mock import patch
        from muse.contextual_inquiry_security import CaseProtectionError, _serialize_contextual_inquiry

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                claimed, inquiry, case_envelope = self._create_claimed_contextual_review(queue)
                result_envelope, result_plaintext = self._review_result_envelope_and_plaintext(inquiry)
                result = json.loads(result_plaintext)
                result["findings"][0]["assessment"] = "verified"
                invalid_plaintext = json.dumps(
                    result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
                case_plaintext = _serialize_contextual_inquiry(inquiry)

                def decrypt(envelope):
                    return case_plaintext if envelope == case_envelope else invalid_plaintext

                with patch("muse.contextual_inquiry_security.unprotect_case_bytes", side_effect=decrypt):
                    with self.assertRaisesRegex(CaseProtectionError, "cannot promote evidence"):
                        queue.complete_contextual_inquiry_review_only(
                            claimed["id"], worker_id="review-worker",
                            expected_updated_at=claimed["updated_at"],
                            encrypted_review_result=result_envelope,
                        )
                unchanged = queue.get(claimed["id"])
                self.assertEqual(unchanged["status"], "running")
                self.assertNotIn("review_result_envelope", unchanged["payload"])

    def test_task_survives_reopening_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("research", {"topic": "memory"})
                self.assertEqual(task["status"], "queued")
            with TaskQueue(database) as queue:
                restored = queue.get(task["id"])
                self.assertEqual(restored["title"], "research")
                self.assertEqual(restored["payload"], {"topic": "memory"})
                self.assertEqual(restored["status"], "queued")

    def test_enqueue_can_start_waiting_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue("approval gated", {"goal": "bounded"}, require_approval=True)
                self.assertEqual(task["status"], "waiting_approval")
                self.assertEqual(
                    [event["event_type"] for event in queue.events(task["id"])],
                    ["ticket.created", "approval.requested"],
                )
                approved = queue.approve(task["id"], approved_by="operator")
                self.assertEqual(approved["status"], "queued")
                self.assertTrue(approved["payload"]["_approved"])

    def test_ticket_lifecycle_events_persist_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tickets.sqlite3"
            with TaskQueue(database) as queue:
                task = queue.enqueue("ticket lifecycle", {"goal": "build"})
                queue.record_event(task["id"], "mandate.issued", actor="commander", payload={"scope": "bounded"})
                queue.claim_next(worker_id="ruflo-agent-1")
                queue.finish(task["id"], status="succeeded", result={"summary": "done"}, worker_id="ruflo-agent-1")
                timeline = queue.events(task["id"])
                self.assertEqual([e["event_type"] for e in timeline], ["ticket.created", "mandate.issued", "assignment.claimed", "ticket.status_changed", "report.submitted"])
            with TaskQueue(database) as queue:
                self.assertEqual(len(queue.events(task["id"])), 5)

    def test_ticket_event_rejects_unknown_task_and_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                with self.assertRaises(KeyError):
                    queue.record_event("missing", "mandate.issued", actor="commander")
                task = queue.enqueue("bounded event", {})
                with self.assertRaises(ValueError):
                    queue.record_event(task["id"], "report.submitted", actor="worker", payload={"text": "x" * 17000})

    def test_enqueue_idempotency_key_returns_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                first = queue.enqueue("same", {"value": 1}, idempotency_key="plan:123")
                second = queue.enqueue("different title", {"value": 2}, idempotency_key="plan:123")
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(second["payload"], {"value": 1})
                self.assertEqual(queue.get_by_idempotency_key("plan:123")["id"], first["id"])

    def test_claim_moves_one_queued_task_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                first = queue.enqueue("first", {})
                queue.enqueue("second", {})
                claimed = queue.claim_next()
                self.assertEqual(claimed["id"], first["id"])
                self.assertEqual(claimed["status"], "running")
                self.assertEqual(queue.get(first["id"])["status"], "running")

    def test_approval_requeues_waiting_task_and_records_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("send report", {"kind": "send_report"})
                claimed = queue.claim_next()
                self.assertEqual(claimed["id"], task["id"])
                waiting = queue.finish(task["id"], status="waiting_approval", result={"reason": "external action"})
                self.assertEqual(waiting["status"], "waiting_approval")
                approved = queue.approve(task["id"], approved_by="user")
                self.assertEqual(approved["status"], "queued")
                self.assertTrue(approved["payload"]["_approved"])
                self.assertEqual(approved["payload"]["_approval"]["approved_by"], "user")

    def test_cannot_approve_task_not_waiting_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("ordinary task", {})
                with self.assertRaises(ValueError):
                    queue.approve(task["id"], approved_by="user")


    def test_contextual_inquiry_placeholder_cannot_be_approved_before_context_is_attached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review - context required",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "requires_context_reentry": True,
                    },
                    require_approval=True,
                )
                with self.assertRaisesRegex(PermissionError, "case context is attached"):
                    queue.approve(task["id"], approved_by="user")
                unchanged = queue.get(task["id"])
                self.assertEqual(unchanged["status"], "waiting_approval")
                self.assertNotIn("_approved", unchanged["payload"])
                self.assertEqual(
                    [event["event_type"] for event in queue.events(task["id"])],
                    ["ticket.created", "approval.requested"],
                )

    def test_claim_records_lease_owner_expiry_and_attempt_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("leased", {})
                claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
                self.assertEqual(claimed["id"], task["id"])
                self.assertEqual(claimed["lease_owner"], "worker-a")
                self.assertIsNotNone(claimed["lease_expires_at"])
                self.assertEqual(claimed["attempts"], 1)

    def test_heartbeat_extends_only_the_current_workers_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("heartbeat", {})
                queue.claim_next(worker_id="worker-a", lease_seconds=30)
                before = queue.get(task["id"])["lease_expires_at"]
                self.assertIsNone(queue.heartbeat(task["id"], worker_id="worker-b", lease_seconds=60))
                updated = queue.heartbeat(task["id"], worker_id="worker-a", lease_seconds=60)
                self.assertGreater(updated["lease_expires_at"], before)

    def test_expired_running_task_is_requeued_and_can_be_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("recover", {})
                queue.claim_next(worker_id="worker-a", lease_seconds=1)
                recovered = queue.recover_expired(now="2999-01-01T00:00:00+00:00")
                self.assertEqual(recovered, 1)
                queued = queue.get(task["id"])
                self.assertEqual(queued["status"], "queued")
                self.assertIsNone(queued["lease_owner"])
                reclaimed = queue.claim_next(worker_id="worker-b", lease_seconds=60)
                self.assertEqual(reclaimed["attempts"], 2)
                self.assertEqual(reclaimed["lease_owner"], "worker-b")


    def test_previous_worker_cannot_finish_after_task_was_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("fenced", {})
                queue.claim_next(worker_id="worker-a", lease_seconds=1)
                queue.recover_expired(now="2999-01-01T00:00:00+00:00")
                queue.claim_next(worker_id="worker-b", lease_seconds=60)
                stale = queue.finish(
                    task["id"], status="succeeded", result={"wrong": True},
                    worker_id="worker-a",
                )
                self.assertIsNone(stale)
                self.assertEqual(queue.get(task["id"])["lease_owner"], "worker-b")


    def test_existing_queue_database_is_migrated_without_losing_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-id", "legacy task", "{}", "queued", "2026-01-01", "2026-01-01"),
            )
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-media", "legacy media", '{"kind": "ffmpeg_transcode"}', "queued", "2026-01-01", "2026-01-01"),
            )
            connection.commit()
            connection.close()

            with TaskQueue(database) as queue:
                migrated = queue.get("legacy-id")
                self.assertEqual(migrated["title"], "legacy task")
                self.assertEqual(migrated["attempts"], 0)
                self.assertEqual(migrated["recovery_policy"], "retry")
                self.assertEqual(migrated["workload_class"], "general")
                self.assertIsNone(migrated["lease_owner"])
                migrated_media = queue.get("legacy-media")
                self.assertEqual(migrated_media["recovery_policy"], "manual")


    def test_snapshot_reports_queue_counts_without_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                queued = queue.enqueue("queued item", {"secret": "not exposed"})
                running = queue.enqueue("running item", {})
                queue.claim_next(worker_id="status-worker")
                snapshot = queue.snapshot()
                self.assertEqual(snapshot["counts"]["queued"], 1)
                self.assertEqual(snapshot["counts"]["running"], 1)
                self.assertEqual(len(snapshot["active"]), 2)
                self.assertNotIn("payload", snapshot["active"][0])
                self.assertEqual({item["id"] for item in snapshot["active"]}, {queued["id"], running["id"]})


    def test_expired_task_fails_closed_after_maximum_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("bounded retries", {})
                for attempt in range(3):
                    claimed = queue.claim_next(
                        worker_id=f"worker-{attempt}", lease_seconds=1, max_attempts=3
                    )
                    self.assertEqual(claimed["attempts"], attempt + 1)
                    queue.recover_expired(
                        now="2999-01-01T00:00:00+00:00", max_attempts=3
                    )
                self.assertIsNone(queue.claim_next(worker_id="worker-final"))
                failed = queue.get(task["id"])
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["attempts"], 3)
                failure_events = [event for event in queue.events(task["id"]) if event["event_type"] == "failure.classified"]
                self.assertEqual(failure_events[0]["payload"]["error_code"], "worker.attempt_limit")



    def test_cancel_queued_task_records_actor_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("obsolete", {"kind": "echo"})
                cancelled = queue.cancel(task["id"], cancelled_by="user", reason="No longer needed")
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["payload"]["_cancellation"]["cancelled_by"], "user")
                self.assertEqual(cancelled["payload"]["_cancellation"]["reason"], "No longer needed")
                self.assertIsNone(queue.claim_next(worker_id="worker"))
                self.assertEqual(queue.snapshot()["counts"]["cancelled"], 1)

    def test_cancel_waiting_approval_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("sensitive", {"kind": "publish"})
                queue.claim_next(worker_id="worker")
                queue.finish(task["id"], status="waiting_approval", worker_id="worker")
                cancelled = queue.cancel(task["id"], cancelled_by="user")
                self.assertEqual(cancelled["status"], "cancelled")

    def test_running_or_unknown_task_cannot_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                running = queue.enqueue("running", {})
                queue.claim_next(worker_id="worker")
                with self.assertRaises(ValueError):
                    queue.cancel(running["id"], cancelled_by="user")
                with self.assertRaises(ValueError):
                    queue.cancel("missing", cancelled_by="user")

    def test_cancel_requires_actor_and_bounds_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("bounded", {})
                with self.assertRaises(ValueError):
                    queue.cancel(task["id"], cancelled_by=" ")
                with self.assertRaises(ValueError):
                    queue.cancel(task["id"], cancelled_by="user", reason="x" * 501)

    def test_manual_recovery_requires_reapproval_after_worker_lease_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue(
                    "external side effect", {"kind": "publish", "_approved": True},
                    recovery_policy="manual",
                )
                queue.claim_next(worker_id="worker-a", lease_seconds=1)
                self.assertEqual(queue.recover_expired(now="2999-01-01T00:00:00+00:00"), 1)
                paused = queue.get(task["id"])
                self.assertEqual(paused["status"], "waiting_approval")
                self.assertNotIn("_approved", paused["payload"])
                self.assertEqual(paused["payload"]["_recovery"]["reason"], "worker_lease_expired")
                self.assertEqual(paused["recovery_policy"], "manual")
                approved = queue.approve(task["id"], approved_by="user")
                self.assertTrue(approved["payload"]["_approved"])
                reclaimed = queue.claim_next(worker_id="worker-b")
                self.assertEqual(reclaimed["id"], task["id"])

    def test_claim_next_does_not_automatically_retry_expired_manual_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("external", {}, recovery_policy="manual")
                queue.claim_next(worker_id="worker-a", lease_seconds=30)
                queue._connection.execute(
                    "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00+00:00", task["id"]),
                )
                queue._connection.commit()
                reclaimed = queue.claim_next(worker_id="worker-b")
                self.assertIsNone(reclaimed)
                self.assertEqual(queue.get(task["id"])["status"], "waiting_approval")
    def test_recovery_policy_accepts_only_retry_or_manual(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                with self.assertRaises(ValueError):
                    queue.enqueue("invalid", {}, recovery_policy="automatic")

    def test_dependency_tasks_wait_until_all_upstream_tasks_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                first = queue.enqueue("research", {})
                second = queue.enqueue("independent", {})
                dependent = queue.enqueue("implement", {}, depends_on=(first["id"],))
                self.assertEqual(dependent["depends_on"], [first["id"]])

                claimed_first = queue.claim_next(worker_id="worker-a")
                self.assertEqual(claimed_first["id"], first["id"])
                claimed_second = queue.claim_next(worker_id="worker-b")
                self.assertEqual(claimed_second["id"], second["id"])
                self.assertIsNone(queue.claim_next(worker_id="worker-c"))

                queue.finish(first["id"], status="succeeded", worker_id="worker-a")
                claimed_dependent = queue.claim_next(worker_id="worker-c")
                self.assertEqual(claimed_dependent["id"], dependent["id"])

    def test_failed_dependency_fails_downstream_task_without_running_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                upstream = queue.enqueue("upstream", {})
                downstream = queue.enqueue("downstream", {}, depends_on=(upstream["id"],))
                queue.claim_next(worker_id="builder")
                queue.finish(upstream["id"], status="failed", worker_id="builder")

                self.assertIsNone(queue.claim_next(worker_id="next-worker"))
                failed = queue.get(downstream["id"])
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["payload"]["_dependency_failure"]["dependencies"], [upstream["id"]])
                failure_events = [event for event in queue.events(downstream["id"]) if event["event_type"] == "failure.classified"]
                self.assertEqual(failure_events[0]["payload"]["error_code"], "dependency.failed")


    def test_enqueue_rejects_unknown_duplicate_and_malformed_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                with self.assertRaises(ValueError):
                    queue.enqueue("missing", {}, depends_on=("not-a-task",))
                parent = queue.enqueue("parent", {})
                with self.assertRaises(ValueError):
                    queue.enqueue("duplicate", {}, depends_on=(parent["id"], parent["id"]))
                with self.assertRaises(ValueError):
                    queue.enqueue("malformed", {}, depends_on=["not-a-tuple"])

    def test_aggregate_metrics_reports_recent_retry_counts_without_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                first = queue.enqueue("private title one", {"secret": "payload-one"})
                second = queue.enqueue("private title two", {"secret": "payload-two"})
                queue.claim_next(worker_id="worker-a")
                queue.claim_next(worker_id="worker-b")
                queue._connection.execute("UPDATE tasks SET attempts=3 WHERE id=?", (second["id"],))
                queue._connection.commit()
                metrics = queue.aggregate_attempt_metrics(since="2000-01-01T00:00:00+00:00")
            self.assertEqual(metrics["tasks_seen"], 2)
            self.assertEqual(metrics["retried_tasks"], 1)
            self.assertEqual(metrics["retry_attempts"], 2)
            self.assertNotIn("payload", str(metrics))
            self.assertNotIn("private title", str(metrics))

    def test_runtime_estimate_uses_median_of_recent_successful_attempts_only(self) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tasks.sqlite3"
            with TaskQueue(database) as queue:
                task_ids = [queue.enqueue(f"private-{i}", {"secret": f"value-{i}"})["id"] for i in range(4)]
                base = datetime(2026, 11, 1, tzinfo=timezone.utc)
                durations = [100, 200, 300, 90000]
                for index, (task_id, duration) in enumerate(zip(task_ids, durations)):
                    claimed = base + timedelta(hours=index * 2)
                    finished = claimed + timedelta(seconds=duration)
                    for event_type, timestamp, payload in (
                        ("assignment.claimed", claimed, {"attempt": 1}),
                        ("ticket.status_changed", finished, {"status": "succeeded" if index < 3 else "failed"}),
                    ):
                        queue._connection.execute(
                            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?)",
                            (f"{task_id}-{event_type}", task_id, event_type, "worker", json.dumps(payload), timestamp.isoformat()),
                        )
                queue._connection.commit()
                estimate = queue.runtime_estimate_seconds(default_estimate_seconds=900, min_samples=3)
            with TaskQueue(database) as queue:
                reopened = queue.runtime_estimate_seconds(default_estimate_seconds=900, min_samples=3)
                calibrated_task = queue.enqueue("calibrated task", {})
            self.assertEqual(calibrated_task["estimated_seconds"], 200)
            self.assertEqual(estimate, reopened)
            self.assertEqual(estimate["sample_count"], 3)
            self.assertEqual(estimate["median_seconds"], 200)
            self.assertEqual(estimate["estimated_seconds"], 200)
            self.assertEqual(estimate["source"], "historical_median")
            self.assertEqual(estimate["workload_class"], "general")
            self.assertNotIn("private-", str(estimate))
            self.assertNotIn("secret", str(estimate))

    def test_runtime_estimates_are_isolated_by_validated_workload_class(self) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                samples = []
                for workload_class, duration in (("coding", 120), ("coding", 180), ("coding", 240), ("research", 600), ("research", 900), ("research", 1200)):
                    task = queue.enqueue("private task", {}, workload_class=workload_class, estimated_seconds=999)
                    samples.append((task["id"], duration))
                base = datetime(2026, 11, 1, tzinfo=timezone.utc)
                for index, (task_id, duration) in enumerate(samples):
                    started = base + timedelta(hours=index * 2)
                    finished = started + timedelta(seconds=duration)
                    for event_type, stamp, payload in (
                        ("assignment.claimed", started, {}),
                        ("ticket.status_changed", finished, {"status": "succeeded"}),
                    ):
                        queue._connection.execute(
                            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?)",
                            (f"{task_id}-{event_type}", task_id, event_type, "worker", json.dumps(payload), stamp.isoformat()),
                        )
                queue._connection.commit()
                coding = queue.runtime_estimate_seconds(workload_class="coding")
                research = queue.runtime_estimate_seconds(workload_class="research")
                new_coding = queue.enqueue("calibrated coding", {}, workload_class="coding")
                new_research = queue.enqueue("calibrated research", {}, workload_class="research")
            self.assertEqual(coding["median_seconds"], 180)
            self.assertEqual(research["median_seconds"], 900)
            self.assertEqual(new_coding["estimated_seconds"], 180)
            self.assertEqual(new_research["estimated_seconds"], 900)

    def test_enqueue_rejects_unknown_workload_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                with self.assertRaises(ValueError):
                    queue.enqueue("bad class", {}, workload_class="shell")

    def test_runtime_estimate_falls_back_until_minimum_success_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                estimate = queue.runtime_estimate_seconds(default_estimate_seconds=777, min_samples=3)
            self.assertEqual(estimate["estimated_seconds"], 777)
            self.assertEqual(estimate["source"], "configured_default")
            self.assertEqual(estimate["sample_count"], 0)

    def test_failed_ticket_records_sanitized_failure_classification_and_aggregates_it(self) -> None:
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                first = queue.enqueue("private title one", {"kind": "build", "secret": "private payload"})
                queue.claim_next(worker_id="worker-a")
                queue.finish(first["id"], status="failed", worker_id="worker-a", result={
                    "error_code": "provider.unavailable", "failure_category": "provider",
                    "retryable": True, "recovery_guidance": "Check the endpoint before retrying.",
                    "private_detail": "do not copy this into the event",
                })
                second = queue.enqueue("private title two", {})
                queue.claim_next(worker_id="worker-b")
                queue.finish(second["id"], status="failed", worker_id="worker-b", result={
                    "error_code": "authorization.denied", "failure_category": "authorization",
                    "retryable": False, "recovery_guidance": "Review the required approval.",
                })
                events = queue.events(first["id"])
                classified = [event for event in events if event["event_type"] == "failure.classified"]
                metrics = queue.aggregate_failure_metrics(
                    since=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                )

            self.assertEqual(len(classified), 1)
            self.assertEqual(classified[0]["payload"], {
                "error_code": "provider.unavailable", "failure_category": "provider",
                "retryable": True, "recovery_guidance": "Check the endpoint before retrying.",
            })
            self.assertEqual(metrics["total_failures"], 2)
            self.assertEqual(metrics["by_code"], {
                "authorization.denied": 1, "provider.unavailable": 1,
            })
            self.assertEqual(metrics["by_code_detail"], {
                "authorization.denied": {"category": "authorization", "occurrences": 1, "retryable": 0, "non_retryable": 1},
                "provider.unavailable": {"category": "provider", "occurrences": 1, "retryable": 1, "non_retryable": 0},
            })
            self.assertEqual(metrics["by_category"], {"authorization": 1, "provider": 1})
            self.assertEqual(metrics["retryable"], 1)
            self.assertEqual(metrics["non_retryable"], 1)
            self.assertNotIn("private", str(metrics))
            self.assertNotIn("private_detail", str(classified))

    def test_failed_ticket_does_not_record_unvalidated_failure_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("bad metadata", {})
                queue.claim_next(worker_id="worker")
                queue.finish(task["id"], status="failed", worker_id="worker", result={
                    "error_code": "worker.failure", "failure_category": [],
                    "retryable": True, "recovery_guidance": "x" * 241,
                })
                events = queue.events(task["id"])
            self.assertFalse(any(event["event_type"] == "failure.classified" for event in events))

    def test_contextual_review_only_authorization_is_separate_from_downstream_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": {"version": 1, "protection": "windows-dpapi-current-user", "ciphertext_b64": "b3BhcXVl"},
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True,
                )
                decision = queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator",
                    expected_updated_at=task["updated_at"],
                )
                current = queue.get(task["id"])
                self.assertEqual(current["status"], "waiting_approval")
                self.assertNotIn("_approved", current["payload"])
                self.assertEqual(current["payload"]["_review_authorization"]["scope"], "review_only")
                self.assertEqual(decision["event_type"], "contextual_inquiry.review_only_authorized")
                self.assertEqual(decision["payload"], {"scope": "review_only"})
                with self.assertRaisesRegex(PermissionError, "attached and validated"):
                    queue.approve(task["id"], approved_by="operator")

    def test_contextual_review_only_claim_requires_human_authorization_and_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": {
                            "version": 1, "protection": "windows-dpapi-current-user",
                            "ciphertext_b64": "b3BhcXVl",
                        },
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True,
                )
                with self.assertRaisesRegex(PermissionError, "separately authorized"):
                    queue.claim_contextual_inquiry_review_only(
                        task["id"], worker_id="review-worker",
                        expected_updated_at=task["updated_at"],
                    )
                authorization = queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator",
                    expected_updated_at=task["updated_at"],
                )
                claimed = queue.claim_contextual_inquiry_review_only(
                    task["id"], worker_id="review-worker", lease_seconds=120,
                    expected_updated_at=authorization["task"]["updated_at"],
                )
                self.assertEqual(claimed["status"], "running")
                self.assertEqual(claimed["lease_owner"], "review-worker")
                self.assertIsNotNone(claimed["lease_expires_at"])
                self.assertNotIn("_approved", claimed["payload"])
                self.assertEqual(claimed["payload"]["_review_authorization"]["scope"], "review_only")
                events = queue.events(task["id"])
                self.assertTrue(any(
                    event["event_type"] == "assignment.contextual_review_only_claimed"
                    and event["actor"] == "review-worker"
                    and event["payload"]["scope"] == "review_only"
                    for event in events
                ))

    def test_contextual_review_only_claim_rejects_stale_revision_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": {
                            "version": 1, "protection": "windows-dpapi-current-user",
                            "ciphertext_b64": "b3BhcXVl",
                        },
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True,
                )
                authorization = queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator",
                    expected_updated_at=task["updated_at"],
                )
                with self.assertRaisesRegex(RuntimeError, "revision conflict"):
                    queue.claim_contextual_inquiry_review_only(
                        task["id"], worker_id="review-worker",
                        expected_updated_at=task["updated_at"],
                    )
                claimed = queue.claim_contextual_inquiry_review_only(
                    task["id"], worker_id="review-worker",
                    expected_updated_at=authorization["task"]["updated_at"],
                )
                with self.assertRaisesRegex(PermissionError, "waiting_approval"):
                    queue.claim_contextual_inquiry_review_only(
                        task["id"], worker_id="second-worker",
                        expected_updated_at=claimed["updated_at"],
                    )

    def test_contextual_review_only_authorization_rejects_stale_revision_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": False,
                        "case_context_envelope_stored": True,
                        "case_context_envelope": {"version": 1, "protection": "windows-dpapi-current-user", "ciphertext_b64": "b3BhcXVl"},
                        "context_status": "encrypted_pending_handler",
                        "requires_context_reentry": True,
                        "long_term_storage_performed": False,
                    },
                    require_approval=True,
                )
                with self.assertRaisesRegex(RuntimeError, "revision conflict"):
                    queue.authorize_contextual_inquiry_review_only(
                        task["id"], authorized_by="operator",
                        expected_updated_at=task["updated_at"] + "-stale",
                    )
                decision = queue.authorize_contextual_inquiry_review_only(
                    task["id"], authorized_by="operator",
                    expected_updated_at=task["updated_at"],
                )
                with self.assertRaisesRegex(ValueError, "already exists"):
                    queue.authorize_contextual_inquiry_review_only(
                        task["id"], authorized_by="operator",
                        expected_updated_at=decision["task"]["updated_at"],
                    )

    def test_generic_event_api_cannot_forge_review_only_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue("Contextual inquiry review", {"kind": "contextual_inquiry_review"}, require_approval=True)
                with self.assertRaisesRegex(PermissionError, "dedicated queue transition"):
                    queue.record_event(
                        task["id"], "contextual_inquiry.review_only_authorized",
                        actor="operator", payload={"scope": "review_only"},
                    )

    def test_contextual_inquiry_approval_rejects_forged_attachment_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {
                        "kind": "contextual_inquiry_review",
                        "case_context_attached": True,
                        "requires_context_reentry": False,
                        "context_status": "validated_attached",
                    },
                    require_approval=True,
                )
                with self.assertRaisesRegex(PermissionError, "trusted case-context attachment audit record"):
                    queue.approve(task["id"], approved_by="user")
                unchanged = queue.get(task["id"])
                self.assertEqual(unchanged["status"], "waiting_approval")
                self.assertNotIn("_approved", unchanged["payload"])

    def test_generic_event_api_cannot_forge_contextual_attachment_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tickets.sqlite3") as queue:
                task = queue.enqueue(
                    "Contextual inquiry review",
                    {"kind": "contextual_inquiry_review"},
                    require_approval=True,
                )
                with self.assertRaisesRegex(PermissionError, "trusted queue transition"):
                    queue.record_event(
                        task["id"],
                        "contextual_inquiry.context_attached",
                        actor="contextual-inquiry-review-handler",
                        payload={"attachment_state": "validated"},
                    )
                self.assertFalse(any(
                    event["event_type"] == "contextual_inquiry.context_attached"
                    for event in queue.events(task["id"])
                ))

if __name__ == "__main__":
    unittest.main()
