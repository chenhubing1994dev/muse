import tempfile
import unittest
from pathlib import Path

from muse.task_queue import TaskQueue
from muse.trace import FailureTraceRecorder, TraceBlocked


class FailureTraceTests(unittest.TestCase):
    def test_competing_hypotheses_require_existing_evidence_and_can_be_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("trace me", {})
                claimed = queue.claim_next(worker_id="worker-a", task_id=task["id"])
                evidence_a = queue.record_event(task["id"], "diagnostic.provider_probe", actor="test", payload={"result": "timeout"})
                evidence_b = queue.record_event(task["id"], "diagnostic.queue_probe", actor="test", payload={"result": "lease_expired"})
                queue.finish(task["id"], status="failed", result={"error_code": "execution.timeout"}, worker_id="worker-a")
                trace = FailureTraceRecorder(queue)
                trace.record_hypotheses(
                    task["id"], failure_code="execution.timeout",
                    hypotheses=(
                        {"hypothesis_id": "h1", "claim": "Provider response exceeded its timeout.",
                         "evidence_refs": (evidence_a["event_id"],),
                         "falsification_check": "Replay the same request against a healthy endpoint."},
                        {"hypothesis_id": "h2", "claim": "Worker lease expired before the provider completed.",
                         "evidence_refs": (evidence_b["event_id"],),
                         "falsification_check": "Compare lease heartbeat timestamps with completion time."},
                    ),
                )
                resolution = queue.record_event(
                    task["id"], "diagnostic.replay", actor="test", payload={"result": "healthy"},
                )
                trace.resolve(
                    task["id"], hypothesis_id="h1", disposition="rejected",
                    evidence_refs=(resolution["event_id"],),
                    rationale="The same request completed within budget on replay.",
                )
                report = trace.report(task["id"])
                self.assertEqual(report["failure_code"], "execution.timeout")
                self.assertEqual(report["hypotheses"][0]["resolution"], "rejected")
                self.assertEqual(report["hypotheses"][1]["resolution"], "unresolved")

    def test_trace_fails_closed_without_competing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("trace me", {})
                queue.claim_next(worker_id="worker-a", task_id=task["id"])
                queue.finish(task["id"], status="failed", result={}, worker_id="worker-a")
                trace = FailureTraceRecorder(queue)
                with self.assertRaises(TraceBlocked):
                    trace.record_hypotheses(
                        task["id"], failure_code="handler.unexpected",
                        hypotheses=(
                            {"hypothesis_id": "h1", "claim": "one", "evidence_refs": ("missing",), "falsification_check": "test"},
                            {"hypothesis_id": "h2", "claim": "two", "evidence_refs": ("missing",), "falsification_check": "test"},
                        ),
                    )

    def test_trace_rejects_duplicate_hypotheses_and_unresolved_claims_without_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("trace me", {})
                queue.claim_next(worker_id="worker-a", task_id=task["id"])
                evidence = queue.record_event(task["id"], "diagnostic.result", actor="test", payload={})
                queue.finish(task["id"], status="failed", result={}, worker_id="worker-a")
                trace = FailureTraceRecorder(queue)
                duplicate = (
                    {"hypothesis_id": "h1", "claim": "same", "evidence_refs": (evidence["event_id"],), "falsification_check": "check"},
                    {"hypothesis_id": "h2", "claim": "same", "evidence_refs": (evidence["event_id"],), "falsification_check": "check"},
                )
                with self.assertRaises(ValueError):
                    trace.record_hypotheses(task["id"], failure_code="handler.unexpected", hypotheses=duplicate)
                with self.assertRaises(TraceBlocked):
                    trace.resolve(task["id"], hypothesis_id="h1", disposition="supported", evidence_refs=(), rationale="no proof")



    def test_new_trace_does_not_inherit_old_hypothesis_resolutions(self):
        with tempfile.TemporaryDirectory() as directory:
            with TaskQueue(Path(directory) / "tasks.sqlite3") as queue:
                task = queue.enqueue("trace me", {})
                queue.claim_next(worker_id="worker-a", task_id=task["id"])
                evidence = queue.record_event(task["id"], "diagnostic.result", actor="test", payload={})
                queue.finish(task["id"], status="failed", result={}, worker_id="worker-a")
                trace = FailureTraceRecorder(queue)
                first = (
                    {"hypothesis_id": "h1", "claim": "Provider was unavailable.", "evidence_refs": (evidence["event_id"],), "falsification_check": "Retry against a healthy endpoint."},
                    {"hypothesis_id": "h2", "claim": "The Worker lease expired.", "evidence_refs": (evidence["event_id"],), "falsification_check": "Inspect lease heartbeat."},
                )
                trace.record_hypotheses(task["id"], failure_code="provider.unavailable", hypotheses=first)
                trace.resolve(
                    task["id"], hypothesis_id="h1", disposition="supported",
                    evidence_refs=(evidence["event_id"],), rationale="The provider probe failed.",
                )
                second = (
                    {"hypothesis_id": "h1", "claim": "The request payload was invalid.", "evidence_refs": (evidence["event_id"],), "falsification_check": "Validate the request schema."},
                    {"hypothesis_id": "h2", "claim": "The local network disconnected.", "evidence_refs": (evidence["event_id"],), "falsification_check": "Inspect local connectivity."},
                )
                trace.record_hypotheses(task["id"], failure_code="input.invalid", hypotheses=second)
                report = trace.report(task["id"])
                self.assertEqual(report["hypotheses"][0]["resolution"], "unresolved")

if __name__ == "__main__":
    unittest.main()
