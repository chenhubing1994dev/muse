"""End-to-end tests for Inspiration -> Follow-Up -> Self-Improvement -> Video Blueprint & Multi-Platform Publish."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from muse.improvement import ImprovementStore
from muse.inspiration_evolution import evolve_inspiration_closed_loop
from muse.pipeline_control import PipelineController
from muse.research_knowledge import ResearchKnowledgeStore
from muse.status_api import create_status_server
from muse.task_queue import TaskQueue
from muse.video_product_builder import VideoProductBuilder
from muse.video_publish_package import (
    VIRAL_MONETIZATION_PLATFORMS_16X9,
    VIRAL_MONETIZATION_PLATFORMS_9X16,
    build_publish_package,
)


class _StubSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 8) -> list[dict]:
        self.queries.append(query)
        if "counter" in query.lower() or "limitations" in query.lower():
            return [{
                "title": "Multimedia Cognitive Load & Counter-Evidence Limits",
                "url": "https://8.8.8.8/cognitive-load",
                "snippet": "Excessive visual density impairs retention without clear boundaries.",
                "source_kind": "peer_reviewed_research_article",
                "engine": "stub",
                "published_at": "",
            }]
        return [{
            "title": "NIST AI 600-1 & Dual-Channel Source Verification",
            "url": "https://1.1.1.1/nist-verification",
            "snippet": "Structured verification catches confident AI confabulation.",
            "source_kind": "official_standard_guidance",
            "engine": "stub",
            "published_at": "",
        }]


class _StubReader:
    def read(self, url: str) -> dict:
        if "cognitive-load" in url:
            text = (
                "Empirical counter-evidence demonstrates that high visual density and cognitive overload "
                "reduce learning retention unless captions, visual structure, and pacing remain bounded. "
            ) * 3
            return {
                "url": url,
                "content_type": "text/plain",
                "text": text,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "fetched_at": "2026-11-18T00:00:00+00:00",
                "truncated": False,
            }
        text = (
            "Generative AI frequently presents erroneous or fabricated claims in confident prose; "
            "verifying original primary sources alongside explicit counter-evidence improves accuracy and transfer. "
        ) * 3
        return {
            "url": url,
            "content_type": "text/plain",
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "fetched_at": "2026-11-18T00:00:00+00:00",
            "truncated": False,
        }


class InspirationEvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "muse-inspiration-flagship-video.py").write_text("print('{}')", encoding="utf-8")
        remotion_cli = self.root / "video-motion" / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
        remotion_cli.parent.mkdir(parents=True, exist_ok=True)
        remotion_cli.write_text("// stub", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_offline_inspiration_never_calls_network_and_stays_approval_gated(self) -> None:
        class ExplodingSearch:
            def search(self, *_args, **_kwargs):
                raise AssertionError("network search must not run when allow_external_research=False")

        result = evolve_inspiration_closed_loop(
            self.root,
            create_if_missing={
                "title": "私有灵感：双轨记忆分镜法",
                "content": "我想做一条不依赖外部搜索的本地知识整理分镜工作流。",
                "allow_external_research": False,
                "tags": ["本地优先"],
                "research_domains": ["知识图谱"],
            },
            search=ExplodingSearch(),
            enqueue_video_recipes=["inspiration_viral_9x16_v1"],
        )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["allow_external_research"])
        self.assertFalse(result["external_network_called"])
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["automatic_code_changes"])
        self.assertEqual(len(result["improvement_proposal_ids"]), 1)
        self.assertEqual(len(result["queued_video_jobs"]), 1)
        self.assertEqual(result["queued_video_jobs"][0]["recipe_id"], "inspiration_viral_9x16_v1")

        # Verify proposal advanced to ready_for_review (never approved automatically) and dossier exists
        with ImprovementStore(self.root / "data" / "improvements" / "muse.sqlite3") as store:
            proposal = store.get(result["improvement_proposal_ids"][0])
            self.assertEqual(proposal["status"], "ready_for_review")
        dossier_path = self.root / result["improvement_proposals"][0]["dossier_path"]
        self.assertTrue(dossier_path.is_file())
        bp = json.loads((self.root / result["blueprint_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(bp["acts"]), 6)
        self.assertIn("douyin", bp["platform_copy"])
        self.assertIn("wechat_channels", bp["platform_copy"])
        self.assertIn("youtube", bp["platform_copy"])

    def test_online_extractive_fallback_creates_cited_proposals_and_blueprint(self) -> None:
        # Simulate local LLM endpoint unreachable (loopback high unused port)
        search = _StubSearch()
        reader = _StubReader()
        with patch(
            "muse.autonomous_research.load_muse_model_config",
            return_value={"provider": "llama_cpp", "base_url": "http://127.0.0.1:65431/v1", "model": "offline-test"},
        ):
            result = evolve_inspiration_closed_loop(
                self.root,
                create_if_missing={
                    "title": "90%的人用AI没有复利：靠反证进化闭环",
                    "content": "研究为什么反证检索加自我改进比单次问答更有价值。",
                    "allow_external_research": True,
                    "tags": ["AI进化"],
                    "research_domains": ["多模态创作", "学习科学"],
                },
                search=search,
                reader=reader,
                enqueue_video_recipes=["inspiration_viral_9x16_v1", "inspiration_viral_16x9_v1"],
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["external_network_called"])
        self.assertEqual(result["synthesis_status"], "completed_extractive_fallback")
        self.assertGreaterEqual(result["evidence_coverage_score"], 75)
        self.assertEqual(len(result["improvement_proposal_ids"]), 2)
        self.assertEqual(len(result["queued_video_jobs"]), 2)
        for job in result["queued_video_jobs"]:
            with TaskQueue(self.root / "data" / "tasks" / "muse.sqlite3") as q:
                ticket = q.get(job["job_id"])
                self.assertEqual(ticket["payload"]["inspiration_id"], result["inspiration_id"])
                self.assertEqual(ticket["payload"]["blueprint_path"], result["blueprint_path"])
        with ResearchKnowledgeStore(self.root / "data" / "research" / "knowledge.sqlite3") as k:
            node = k.get_node(result["inspiration_id"])
            self.assertEqual(node["metadata"]["review_state"], "researched_needs_independent_review")
            self.assertEqual(node["metadata"]["evolution_stage"], "blueprint_ready")

    def test_status_api_evolves_inspiration_and_enqueues_commander_jobs(self) -> None:
        task_db = self.root / "data" / "tasks" / "muse.sqlite3"
        progress_db = task_db
        cap_db = self.root / "data" / "capabilities" / "muse.sqlite3"
        knowledge_db = self.root / "data" / "research" / "knowledge.sqlite3"
        improvement_db = self.root / "data" / "improvements" / "muse.sqlite3"
        task_db.parent.mkdir(parents=True, exist_ok=True)
        with ResearchKnowledgeStore(knowledge_db) as k:
            node = k.upsert_node(
                "claim",
                "API 灵感进化测试",
                summary="测试通过 Status API 触发本地闭环与视频排队。",
                external_ref="creator-inspiration:sha256:" + "a" * 64,
                metadata={
                    "origin": "creator_inspiration_inbox",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "review_state": "not_researched",
                    "allow_external_research": False,
                },
                status="proposed",
            )
        server, token = create_status_server(
            task_db=task_db,
            progress_db=progress_db,
            capability_db=cap_db,
            improvement_db=improvement_db,
            knowledge_db=knowledge_db,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        try:
            req = Request(
                base + "/api/v1/inspirations/evolve",
                data=json.dumps({
                    "inspiration_id": node["id"],
                    "enqueue_video": True,
                    "aspect_ratios": ["9:16", "16:9"],
                }).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read())
            evo = body["evolution"]
            self.assertEqual(evo["inspiration_id"], node["id"])
            self.assertEqual(len(evo["queued_video_jobs"]), 2)
            self.assertEqual(len(evo["improvement_proposal_ids"]), 1)
            with urlopen(Request(base + "/api/v1/inspirations", headers={"Authorization": "Bearer " + token}), timeout=5) as resp:
                listed = json.loads(resp.read())["inspirations"]
            self.assertEqual(listed[0]["evolution_stage"], "blueprint_ready")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_publish_package_supports_landscape_youtube_and_douyin_wechat_9x16(self) -> None:
        for aspect, w, h, platforms in (
            ("9:16", 1080, 1920, VIRAL_MONETIZATION_PLATFORMS_9X16),
            ("16:9", 1920, 1080, VIRAL_MONETIZATION_PLATFORMS_16X9),
        ):
            src = self.root / f"pack-{w}x{h}"
            src.mkdir(parents=True, exist_ok=True)
            mp4 = src / f"master-{w}x{h}.mp4"
            mp4.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            cov = src / "cover.jpg"
            cov.write_bytes(b"\xff\xd8\xff\xd9")
            (src / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n", encoding="utf-8")
            (src / "storyboard.json").write_text(json.dumps({
                "schema_version": 1,
                "title": f"测试成片 {aspect}",
                "voiceover": "zh-CN-YunxiNeural",
                "audio": "original ambient bed",
            }), encoding="utf-8")
            (src / "source-notes.json").write_text(json.dumps({
                "title": "Test Source", "publisher": "NIST", "published_at": "2024-07-26",
            }), encoding="utf-8")
            (src / "README.md").write_text("# README\n", encoding="utf-8")
            qa = {
                "status": "passed", "missing_tools": [], "warnings": [],
                "checks": {"full_decode": {"passed": True, "detail": "ok"}},
                "metadata": {"width": w, "height": h, "fps": 30},
            }
            with patch("muse.video_product_builder.inspect_video_file", return_value=qa):
                product = VideoProductBuilder(self.root).package_generated_clip(
                    f"测试成片 {aspect}",
                    "Landscape and vertical pack test",
                    mp4,
                    generation_metadata={
                        "status": "completed", "model": "Remotion native",
                        "aspect_ratio": aspect, "width": w, "height": h, "fps": 30,
                        "duration_seconds": 2.0, "has_audio": True,
                    },
                )
            gen = {
                "status": "completed",
                "title": f"测试成片 {aspect}",
                "mp4": {
                    "path": mp4.relative_to(self.root).as_posix(),
                    "sha256": hashlib.sha256(mp4.read_bytes()).hexdigest(),
                    "bytes": mp4.stat().st_size,
                    "width": w, "height": h, "fps": 30,
                    "duration_seconds": 2.0, "codec": "h264", "audio_codec": "aac",
                    "audio_sample_rate": 48000, "full_decode": "passed",
                },
                "cover": {"path": cov.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(cov.read_bytes()).hexdigest()},
                "product": product,
            }
            (src / "generation.json").write_text(json.dumps(gen), encoding="utf-8")
            res = build_publish_package(self.root, src, platforms=platforms)
            out = self.root / res["output_dir"]
            for p in platforms:
                draft = json.loads((out / "platform-drafts" / f"{p}.json").read_text(encoding="utf-8"))
                self.assertEqual(draft["technical_profile"]["master_aspect_ratio"], aspect)
                self.assertEqual(draft["release_state"], "draft_only")
                self.assertFalse(draft["publish_api_called"])
            with zipfile.ZipFile(out / "publish-package.zip") as arc:
                self.assertIsNone(arc.testzip())


if __name__ == "__main__":
    unittest.main()
