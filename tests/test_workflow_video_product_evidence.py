from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from muse.commander_planner import CommanderPlanner
from muse.task_queue import TaskQueue
from muse.workflow_profiles import WorkflowContract, WorkflowController


class WorkflowVideoProductEvidenceTests(unittest.TestCase):
    def _prepare(self, root: Path):
        initiative_id = "a" * 32
        version_id = "b" * 20
        product_dir = root / "data" / "products" / initiative_id / version_id
        product_dir.mkdir(parents=True)
        payloads = {
            "README.md": b"# Muse video product\n",
            "storyboard.json": b'{"schema_version":1,"scenes":[]}',
            "video.mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00",
        }
        for name, content in payloads.items():
            (product_dir / name).write_bytes(content)
        with zipfile.ZipFile(product_dir / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in payloads.items():
                archive.writestr(name, content)
        names = ("README.md", "storyboard.json", "video.mp4", "product.zip")
        files = {name: hashlib.sha256((product_dir / name).read_bytes()).hexdigest() for name in names}
        manifest = {
            "schema_version": 1, "builder_version": 1, "product_kind": "video_creator",
            "initiative_id": initiative_id, "version_id": version_id,
            "title": "Evidence fixture", "status": "testing", "files": files,
        }
        (product_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        queue = TaskQueue(root / "data" / "tasks.sqlite3")
        plan = CommanderPlanner().plan("verify a local video product")
        ticket = queue.enqueue("Video product evidence workflow", {})["id"]
        queue.claim_next(worker_id="worker-a", task_id=ticket)
        step_ids = [step.step_id for step in plan.steps]
        criterion = f"{step_ids[0]}: MP4 and package files match the verified manifest"
        contract = WorkflowContract(
            goal=plan.goal, desired_outcome="Video product files are verified and linked to the current run",
            acceptance_criteria=(criterion,),
        )
        controller = WorkflowController(queue, artifact_root=root)
        controller.bind_plan_to_ticket(plan, ticket, worker_id="worker-a", contract=contract)
        for step in plan.steps:
            queue.record_event(
                ticket, "report.step_submitted", actor="ruflo",
                payload={"step_id": step.step_id, "agent_id": f"agent-{step.step_id}", "output_chars": 32},
            )
        queue.record_event(
            ticket, "commander.plan_consensus", actor="commander",
            payload={"plan_id": plan.plan_id, "approved": True},
        )
        queue.record_event(
            ticket, "commander.execution_finished", actor="commander",
            payload={
                "plan_id": plan.plan_id, "status": "succeeded",
                "orchestration_status": "succeeded", "review_approved": True,
                "orchestration_run_id": "video-run",
            },
        )
        try:
            result = controller.record_video_product_evidence(
                ticket, worker_id="worker-a", expected_revision=1,
                orchestration_run_id="video-run", initiative_id=initiative_id, version_id=version_id,
            )
        except Exception:
            queue.close()
            raise
        refs = tuple(dict.fromkeys((
            "orchestration-run:video-run",
            *(f"step:{step_id}" for step_id in step_ids),
            "step:independent_review", *result["evidence_refs"],
        )))
        criterion_refs = (
            "orchestration-run:video-run", f"step:{step_ids[0]}",
            "step:independent_review", *result["evidence_refs"],
        )
        return queue, controller, ticket, criterion, refs, criterion_refs, product_dir, result

    def test_video_builder_manifest_registers_all_local_files_with_current_run(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, controller, ticket, criterion, refs, criterion_refs, product_dir, result = self._prepare(Path(directory))
            try:
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["product_status"], "testing")
                self.assertEqual(len(result["artifacts"]), 5)  # manifest + four declared files
                completed = controller.complete(
                    ticket, worker_id="worker-a", expected_revision=result["workflow_revision"],
                    evidence_refs=refs, outcome_verified=True,
                    criteria_evidence={criterion: criterion_refs},
                )
                validation = completed["payload"]["outcome_verification"]["artifact_validation"]
                self.assertEqual(validation["status"], "verified")
                self.assertEqual(len(validation["verified_artifacts"]), 5)
                self.assertTrue(any(item["relative_path"].endswith("/video.mp4") for item in validation["verified_artifacts"]))
                self.assertTrue(any(item["relative_path"].endswith("/product.zip") for item in validation["verified_artifacts"]))
            finally:
                queue.close()

    def test_completion_rejects_video_file_changed_after_manifest_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, controller, ticket, criterion, refs, criterion_refs, product_dir, result = self._prepare(Path(directory))
            try:
                (product_dir / "video.mp4").write_bytes(b"tampered video file")
                with self.assertRaisesRegex(ValueError, "revalidation"):
                    controller.complete(
                        ticket, worker_id="worker-a", expected_revision=result["workflow_revision"],
                        evidence_refs=refs, outcome_verified=True,
                        criteria_evidence={criterion: criterion_refs},
                    )
            finally:
                queue.close()


if __name__ == "__main__":
    unittest.main()
