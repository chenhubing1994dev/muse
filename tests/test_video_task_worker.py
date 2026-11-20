from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_task_worker import VideoTaskWorker
from muse.video_recipe_trace import build_video_recipe_trace


class FakeVideoBuilder:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "validated": True, "initiative_id": "a" * 32, "version_id": "b" * 20,
            "mp4_sha256": "c" * 64, "package_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
            "relative_path": "data/products/example/version", "quality_gate": {"status": "passed"},
        }
        self.error = error
        self.calls = []
        self.verify_calls = []
        self.verify_overrides = {}

    def build_from_prompt(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return dict(self.result)

    def build_from_scenes(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return dict(self.result)


    def verify_path(self, products_root, initiative_id, version_id):
        self.verify_calls.append((Path(products_root), initiative_id, version_id))
        verified = {
            "validated": True,
            "initiative_id": self.result.get("initiative_id"),
            "version_id": self.result.get("version_id"),
            "relative_path": self.result.get("relative_path"),
            "mp4_sha256": self.result.get("mp4_sha256"),
            "package_sha256": self.result.get("package_sha256"),
            "manifest_sha256": self.result.get("manifest_sha256"),
        }
        verified.update(self.verify_overrides)
        return verified


class VideoTaskWorkerTests(unittest.TestCase):
    def _queue_approved_attempt(self, root, initiative_id, attempt_id, video_brief):
        task_database = root / "data" / "tasks" / "muse.sqlite3"
        digest = hashlib.sha256(json.dumps(video_brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        payload = {
            "kind": "business_opportunity_video_approval",
            "opportunity_id": "test-opportunity",
            "initiative_id": initiative_id,
            "attempt_id": attempt_id,
            "video_brief_sha256": digest,
            "video_brief": video_brief,
            "approval_scope": "one_video_attempt_only",
        }
        with TaskQueue(task_database) as queue:
            ticket = queue.enqueue(
                "Approve test video", payload, require_approval=True,
                idempotency_key=f"test-video-approval:{attempt_id}", workload_class="media",
            )
            queue.approve(ticket["id"], approved_by="test-operator")
        with InitiativeStore(root / "data" / "initiatives" / "muse.sqlite3") as store:
            store.start_video_attempt(
                attempt_id, mode="prompt_to_video", initiative_id=initiative_id,
                approval_task_id=ticket["id"],
                recipe_sha256=video_brief.get("recipe_sha256"),
            )
        return ticket["id"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "data" / "initiatives" / "muse.sqlite3"
        self.database.parent.mkdir(parents=True)
        self.initiative_id = None
        video_brief = {
            "title": "测试影片", "prompt": "source grounded story", "negative_prompt": "fake facts",
            "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
        }
        with InitiativeStore(self.database) as store:
            item = store.propose(
                "研究创作短片", kind="creative_output", creator_value="研究变成作品",
                concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                first_step="生成预览", expected_output="本地视频产品",
            )
            self.initiative_id = item["id"]
        self.approval_task_id = self._queue_approved_attempt(
            self.root, self.initiative_id, "worker-test", video_brief,
        )

    def tearDown(self):
        self.temp.cleanup()


    def test_worker_routes_approved_scene_brief_to_multi_scene_builder_with_stable_seeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            video_brief = {
                "title": "多镜头测试", "prompt": "A concise summary of the story",
                "aspect_ratio": "16:9", "duration_seconds": 4, "steps": 8,
                "scenes": [
                    {"prompt": "Opening: ancient observatory above clouds", "duration_seconds": 2},
                    {"prompt": "Close-up: bronze armillary sphere at sunrise", "duration_seconds": 2},
                ],
            }
            with InitiativeStore(database) as store:
                item = store.propose(
                    "多镜头 Worker", kind="creative_output", creator_value="测试多镜头接线",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成镜头", expected_output="多镜头 MP4",
                )
            self._queue_approved_attempt(root, item["id"], "scene-worker-test", video_brief)
            builder = FakeVideoBuilder()
            result = VideoTaskWorker(root, builder=builder, worker_id="scene-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(builder.calls), 1)
            args, kwargs = builder.calls[0]
            self.assertEqual(args[0], "多镜头测试")
            self.assertEqual(len(kwargs["scenes"]), 2)
            self.assertNotEqual(kwargs["scenes"][0]["seed"], kwargs["scenes"][1]["seed"])
            self.assertTrue(all(isinstance(scene["seed"], int) for scene in kwargs["scenes"]))
            self.assertEqual(kwargs["scenes"][0]["duration_seconds"], 2.0)

    def test_worker_builds_one_queued_video_and_records_verified_artifact(self):
        builder = FakeVideoBuilder()
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker")
        result = worker.run_once()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["attempt_id"], "worker-test")
        self.assertEqual(len(builder.calls), 1)
        args, kwargs = builder.calls[0]
        self.assertEqual(args[0], "测试影片")
        self.assertEqual(args[1], "source grounded story")
        self.assertEqual(kwargs["creator_initiative_id"], self.initiative_id)
        self.assertEqual(kwargs["aspect_ratio"], "9:16")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "succeeded")
            self.assertEqual(attempt["product_id"], "a" * 32)
            self.assertEqual(attempt["manifest_sha256"], "e" * 64)
            self.assertEqual(builder.verify_calls, [((self.root / "data" / "products").resolve(), "a" * 32, "b" * 20)])

    def test_approved_creator_brief_recipe_fingerprint_survives_worker_and_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            recipe_hash = "a" * 64
            video_brief = {
                "candidate_id": "candidate-01-abcdef123456",
                "recipe_sha256": recipe_hash,
                "title": "获批配方追溯测试",
                "prompt": "A restrained cinematic dawn over an ancient observatory",
                "negative_prompt": "unverified claims, text artifacts",
                "aspect_ratio": "16:9",
                "duration_seconds": 2,
                "steps": 8,
                "mapping_report": {"status": "ready", "mapped": [{"source": "scenes", "target": "scenes"}]},
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "已审批配方端到端测试", kind="creative_output", creator_value="配方指纹贯通",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成测试预览", expected_output="可追溯视频产品",
                )
            self._queue_approved_attempt(root, initiative["id"], "recipe-e2e-attempt", video_brief)
            builder = FakeVideoBuilder()
            result = VideoTaskWorker(root, builder=builder, worker_id="recipe-e2e-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(len(builder.calls), 1)
            with InitiativeStore(database) as store:
                attempt = store.get_video_attempt("recipe-e2e-attempt")
                self.assertEqual(attempt["status"], "succeeded")
                self.assertEqual(attempt["recipe_sha256"], recipe_hash)
                self.assertEqual(attempt["manifest_sha256"], "e" * 64)
            verified_product = {
                "validated": True,
                "recipe_sha256": recipe_hash,
                "manifest_sha256": "e" * 64,
                "mp4_sha256": "c" * 64,
                "package_sha256": "d" * 64,
                "quality_gate": {"status": "passed"},
            }
            with patch("muse.video_product_builder.VideoProductBuilder.verify_path", return_value=verified_product):
                trace = build_video_recipe_trace(root, "recipe-e2e-attempt")
            self.assertTrue(trace["approval"]["human_decision_verified"])
            self.assertEqual(trace["approval"]["decision_actor"], "test-operator")
            self.assertTrue(trace["candidate"]["brief_fingerprint_matches_attempt"])
            self.assertTrue(trace["product"]["manifest_matches_attempt"])
            self.assertTrue(trace["product"]["recipe_matches_attempt"])
            self.assertEqual(trace["product"]["status"], "verified")
            self.assertTrue(trace["chain_consistent"])
            self.assertFalse(trace["release_review"]["can_publish"])
            self.assertEqual(trace["publishing"], "not_performed")

    def test_worker_fails_closed_when_builder_omits_manifest_digest(self):
        builder = FakeVideoBuilder()
        builder.result.pop("manifest_sha256")
        result = VideoTaskWorker(self.root, builder=builder, worker_id="missing-manifest").run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")

    def test_worker_fails_closed_when_builder_returns_invalid_manifest_digest(self):
        builder = FakeVideoBuilder()
        builder.result["manifest_sha256"] = "invalid"
        result = VideoTaskWorker(self.root, builder=builder, worker_id="invalid-manifest").run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")

    def test_claim_grounded_worker_blocks_unreviewed_claim_before_gpu_or_builder(self):
        from muse.narration_sync import NarrationSyncStore
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            claim_id = "a" * 32
            with ResearchKnowledgeStore(root / "data" / "research" / "knowledge.sqlite3") as research:
                claim = research.upsert_node(
                    "claim", "未审核事实主张", summary="候选 Claim",
                    external_ref="test:claim-grounded-worker",
                )
                claim_id = claim["id"]
            concept = {"creator_brief": {"video": {
                "title": "证据门禁测试", "prompt": "A simple evidence gate test",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "这是一项未经审核的主张。它仍然需要核实。不能直接作为事实发布。",
                    "claim_grounded": True,
                    "claim_ids_by_segment": [[claim_id], [claim_id], [claim_id]],
                    "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Claim-grounded worker", kind="creative_output", creator_value="测试旁白证据门禁",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="验证门禁", expected_output="阻断记录",
                )
            self._queue_approved_attempt(root, item["id"], "claim-grounded-worker", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}):
                result = VideoTaskWorker(root, builder=builder, worker_id="claim-grounded-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertEqual(builder.calls, [])
            with NarrationSyncStore(root / "data" / "narration" / "narration.sqlite3") as sync:
                runs = sync.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertFalse(runs[0]["evidence_contract"]["publishable"])
            self.assertTrue(all(
                "no_verified_support" in segment["blocking_reasons"]
                for segment in runs[0]["evidence_contract"]["segments"]
            ))

    def test_claim_grounded_worker_passes_publishable_evidence_contract_to_builder(self):
        from muse.narration_evidence_contract import build_narration_evidence_contract
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            with ResearchKnowledgeStore(root / "data" / "research" / "knowledge.sqlite3"):
                pass
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            claim_id = "a" * 32
            evidence_by_claim = {
                claim_id: [{
                    "evidence_id": "b" * 32,
                    "verdict": "supports",
                    "citation_anchor_verified": True,
                    "source_current": True,
                    "source_ref": "https://example.org/verified",
                }],
            }
            concept = {"creator_brief": {"video": {
                "title": "Claim-grounded pass test", "prompt": "Explain a verified test result",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "经核验的来源支持这项结论。旁白必须逐句关联主张。第三句也必须有证据支持。",
                    "claim_grounded": True,
                    "claim_ids_by_segment": [[claim_id], [claim_id], [claim_id]],
                    "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Claim-grounded pass", kind="creative_output",
                    creator_value="验证已审核旁白的 Worker 接线",
                    concept=json.dumps(concept, ensure_ascii=False),
                    first_step="验证证据契约传递", expected_output="Builder 收到可发布证据契约",
                )
            self._queue_approved_attempt(
                root, item["id"], "claim-grounded-ready-worker", concept["creator_brief"]["video"],
            )
            builder = FakeVideoBuilder()

            def persist_ready_contract(generated_script, research_store, narration_store):
                contract = build_narration_evidence_contract(
                    generated_script["segments"], evidence_by_claim,
                )
                self.assertTrue(contract["publishable"])
                return {"evidence_contract": contract}

            with (
                patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}),
                patch(
                    "muse.narration_evidence_pipeline.persist_claim_grounded_narration_run",
                    side_effect=persist_ready_contract,
                ) as persist,
            ):
                result = VideoTaskWorker(
                    root, builder=builder, worker_id="claim-grounded-ready-worker",
                ).run_once()

            self.assertEqual(result["status"], "succeeded")
            persist.assert_called_once()
            self.assertEqual(len(builder.calls), 1)
            _, kwargs = builder.calls[0]
            contract = kwargs["narration_evidence_contract"]
            self.assertTrue(contract["publishable"])
            self.assertGreaterEqual(len(contract["segments"]), 3)
            self.assertTrue(all(segment["evidence"] for segment in contract["segments"]))

    def test_worker_generates_cantonese_script_and_passes_local_tts_paths_after_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            tts_model = root / "data" / "models" / "canto-tts-nano"
            tts_model.mkdir(parents=True)
            (tts_model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            concept = {"creator_brief": {"video": {
                "title": "粵語測試片", "prompt": "Explain a verified local service pilot",
                "aspect_ratio": "9:16", "duration_seconds": 2, "steps": 8,
                "narration": {
                    "enabled": True, "language": "zh-HK", "style": "yujie_canto", "segments": 3,
                    "research_text": "客戶需要清晰介紹。方案仍在試點驗證。成功信號是取得真實預約。",
                    "inspiration": "沉穩知性，清晰可信。", "asr_enabled": False,
                },
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "粵語測試", kind="creative_output", creator_value="測試旁白接線",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="測試", expected_output="測試產品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-worker-test", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            worker = VideoTaskWorker(root, builder=builder, worker_id="canto-worker")
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", return_value={"status": "ready"}) as preflight:
                result = worker.run_once()
            preflight.assert_called_once()
            self.assertTrue(Path(preflight.call_args.args[0]).samefile(tts_packages.parent))
            self.assertTrue(Path(preflight.call_args.args[1]).samefile(tts_model))
            self.assertEqual(result["status"], "succeeded")
            _, kwargs = builder.calls[0]
            self.assertTrue(Path(kwargs["narration_model_dir"]).samefile(tts_model))
            self.assertIsNone(kwargs["narration_asr_model_dir"])
            self.assertIn("係㗎", kwargs["narration_script"])
            self.assertGreaterEqual(kwargs["narration_script"].count("。"), 2)

    def test_cantonese_tts_preflight_failure_stops_before_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            (root / ".muse-canto-tts-py314" / "canto_tts").mkdir(parents=True)
            model = root / "data" / "models" / "canto-tts-nano"
            model.mkdir(parents=True)
            (model / "browser_poc_manifest.json").write_text("{}", encoding="utf-8")
            concept = {"creator_brief": {"video": {
                "title": "Runtime gate", "prompt": "A grounded test prompt",
                "narration": {"enabled": True, "research_text": "只做测试。仍需验证。"},
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Runtime gate", kind="creative_output", creator_value="测试 Python 版本门禁",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="测试", expected_output="测试产品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-runtime-gate", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime", side_effect=RuntimeError("Python 3.14 TTS helper unavailable")):
                result = VideoTaskWorker(root, builder=builder, worker_id="canto-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertEqual(builder.calls, [])

    def test_requested_cantonese_narration_fails_closed_before_builder_when_model_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            tts_packages = root / ".muse-canto-tts-py314" / "canto_tts"
            tts_packages.mkdir(parents=True)
            concept = {"creator_brief": {"video": {
                "title": "缺少模型测试", "prompt": "A grounded test prompt",
                "narration": {"enabled": True, "research_text": "只做测试。仍需验证。"},
            }}}
            with InitiativeStore(database) as store:
                item = store.propose(
                    "缺少模型", kind="creative_output", creator_value="测试失败关闭",
                    concept=json.dumps(concept, ensure_ascii=False), first_step="测试", expected_output="测试产品",
                )
            self._queue_approved_attempt(root, item["id"], "canto-missing-model", concept["creator_brief"]["video"])
            builder = FakeVideoBuilder()
            result = VideoTaskWorker(root, builder=builder, worker_id="canto-worker").run_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(builder.calls, [])
            with InitiativeStore(database) as store:
                self.assertEqual(store.get_video_attempt("canto-missing-model")["status"], "failed")

    def test_worker_ignores_legacy_unapproved_research_queue_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            with InitiativeStore(database) as store:
                item = store.propose(
                    "自主研究候选", kind="creative_output", creator_value="未批准不应出片",
                    concept=json.dumps({"creator_brief": {"video": {"title": "候选", "prompt": "research candidate"}}}),
                    first_step="等待审核", expected_output="候选视频",
                )
                store.start_video_attempt("research-unapproved", mode="prompt_to_video", initiative_id=item["id"])
            builder = FakeVideoBuilder()
            result = VideoTaskWorker(root, builder=builder).run_once()
            self.assertEqual(result["status"], "idle")
            self.assertTrue(result["approval_required"])
            self.assertEqual(builder.calls, [])
            with InitiativeStore(database) as store:
                self.assertEqual(store.get_video_attempt("research-unapproved")["status"], "queued")

    def test_paused_worker_does_not_claim_or_start_gpu_work(self):
        builder = FakeVideoBuilder()
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker", pause_check=lambda: True)
        result = worker.run_once()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            self.assertEqual(store.get_video_attempt("worker-test")["status"], "queued")

    def test_pause_after_claim_releases_task_without_invoking_builder(self):
        builder = FakeVideoBuilder()
        checks = iter([False, True])
        worker = VideoTaskWorker(
            self.root, builder=builder, worker_id="test-worker",
            pause_check=lambda: next(checks),
        )
        result = worker.run_once()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(builder.calls, [])
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "queued")
            self.assertIsNone(attempt["worker_id"])

    def test_builder_failure_is_durable_and_does_not_mark_product_succeeded(self):
        builder = FakeVideoBuilder(error=RuntimeError("ComfyUI unavailable"))
        worker = VideoTaskWorker(self.root, builder=builder, worker_id="test-worker")
        result = worker.run_once()
        self.assertEqual(result["status"], "failed")
        with InitiativeStore(self.database) as store:
            attempt = store.get_video_attempt("worker-test")
            self.assertEqual(attempt["status"], "failed")
            self.assertEqual(attempt["error_type"], "RuntimeError")


    def test_worker_output_is_traceable_to_exact_approved_creator_brief_and_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "data" / "initiatives" / "muse.sqlite3"
            database.parent.mkdir(parents=True)
            recipe_sha256 = "a" * 64
            video_brief = {
                "candidate_id": "candidate-integration-01",
                "recipe_sha256": recipe_sha256,
                "title": "端到端追溯测试", "prompt": "A grounded integration-test scene",
                "negative_prompt": "fabricated claims", "aspect_ratio": "9:16",
                "duration_seconds": 2, "steps": 8,
            }
            with InitiativeStore(database) as store:
                initiative = store.propose(
                    "Worker trace integration", kind="creative_output",
                    creator_value="验证审批到产品的追溯闭环",
                    concept=json.dumps({"creator_brief": {"video": video_brief}}, ensure_ascii=False),
                    first_step="生成模拟预览", expected_output="可追溯的视频产品",
                )
            attempt_id = "worker-trace-integration"
            ticket_id = self._queue_approved_attempt(root, initiative["id"], attempt_id, video_brief)
            builder = FakeVideoBuilder()
            result = VideoTaskWorker(root, builder=builder, worker_id="trace-worker").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["attempt_id"], attempt_id)
            self.assertEqual(len(builder.calls), 1)
            from muse.video_recipe_trace import build_video_recipe_trace
            verified_product = {
                "validated": True, "recipe_sha256": recipe_sha256,
                "manifest_sha256": builder.result["manifest_sha256"],
                "mp4_sha256": builder.result["mp4_sha256"],
                "package_sha256": builder.result["package_sha256"],
                "quality_gate": {"status": "passed"},
            }
            with patch(
                "muse.video_product_builder.VideoProductBuilder.verify_path",
                return_value=verified_product,
            ):
                trace = build_video_recipe_trace(root, attempt_id)
            self.assertTrue(trace["chain_consistent"])
            self.assertEqual(trace["candidate"]["candidate_id"], "candidate-integration-01")
            self.assertEqual(trace["candidate"]["recipe_sha256"], recipe_sha256)
            self.assertTrue(trace["approval"]["human_decision_verified"])
            self.assertEqual(trace["approval"]["ticket_id"], ticket_id)
            self.assertEqual(trace["attempt"]["status"], "succeeded")
            self.assertEqual(trace["product"]["status"], "verified")
            self.assertFalse(trace["release_review"]["can_publish"])

if __name__ == "__main__":
    unittest.main()
