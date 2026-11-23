from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from muse.autonomous_research import (
    AutonomousResearchEngine, RESEARCH_TOPICS, _build_research_reminder_message,
    _creator_initiative_title, _creator_video_attempt_id, _queue_creator_video_approval,
)
from muse.reminders import ReminderStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.philosophy import PhilosophyStore
from muse.initiative_engine import InitiativeStore
from muse.improvement import ImprovementStore

class FakeSearch:
    def __init__(self, *, shared_domain=False):
        self.shared_domain = shared_domain
        self.queries = []

    def search(self, query, *, limit=8):
        self.queries.append(query)
        slug = "counter" if "counterexamples" in query or "limitations" in query else "support"
        host = "1.1.1.1" if self.shared_domain or slug == "support" else "8.8.8.8"
        return [{"title": slug + " evidence", "url": f"https://{host}/{slug}",
                 "snippet": "public research result", "engine": "fake", "published_at": ""}]


class FakeReader:
    def read(self, url):
        text = "Public evidence text " * 20
        return {"url": url, "content_type": "text/plain", "text": text,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "fetched_at": "2026-11-04T00:00:00+00:00", "truncated": False}


class AutonomousResearchTests(unittest.TestCase):
    def setUp(self):
        self._bookmark_patch = patch("muse.autonomous_research.load_creator_bookmarks", return_value=[])
        self._bookmark_patch.start()

    def tearDown(self):
        self._bookmark_patch.stop()

    def test_run_once_records_project_usage_and_preserves_outer_task_context(self):
        from muse.model_call_context import (
            current_model_call_context, model_call_scope, record_current_model_usage,
        )
        from muse.usage_ledger import UsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = Mock(spec=["doctor"])
            browser.doctor.return_value = {"available": False, "status": "missing"}
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(),
                ego_browser=browser, resource_aware=False,
            )
            expected = {"task_id": None, "agent_id": None}

            def run_cycle(**_kwargs):
                context = current_model_call_context()
                self.assertIsNotNone(context)
                self.assertEqual(context.task_id, expected["task_id"])
                self.assertEqual(context.agent_id, expected["agent_id"])
                self.assertEqual(context.project_id, "muse")
                self.assertTrue(record_current_model_usage(
                    "lm-studio-local", "research-local-model",
                    {"prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40},
                ))
                return {"status": "completed", "report": {}}

            with patch.object(engine, "_run_once_with_cycle", side_effect=run_cycle):
                first = engine.run_once()
                self.assertEqual(first["status"], "completed")
                self.assertIsNone(current_model_call_context())

                expected.update({"task_id": "outer-research-task", "agent_id": "outer-research-agent"})
                with model_call_scope(
                    root / "tasks.sqlite3", task_id=expected["task_id"],
                    agent_id=expected["agent_id"], project_id="muse",
                ):
                    second = engine.run_once()
                    self.assertEqual(second["status"], "completed")
                    self.assertEqual(current_model_call_context().task_id, expected["task_id"])
                self.assertIsNone(current_model_call_context())

            with UsageLedger(root / "model_usage.sqlite3") as ledger:
                events = ledger.list_events(limit=10)
            self.assertEqual(len(events), 2)
            unscoped, scoped = sorted(events, key=lambda event: event["task_id"] is not None)
            self.assertIsNone(unscoped["task_id"])
            self.assertIsNone(unscoped["agent_id"])
            self.assertEqual(unscoped["attribution_status"], "partial")
            self.assertEqual(unscoped["project_id"], "muse")
            self.assertEqual(scoped["task_id"], "outer-research-task")
            self.assertEqual(scoped["agent_id"], "outer-research-agent")
            self.assertEqual(scoped["attribution_status"], "complete")
            self.assertEqual(scoped["project_id"], "muse")
            self.assertTrue(all(event["input_tokens"] == 31 for event in events))
            self.assertTrue(all(event["output_tokens"] == 9 for event in events))

    def test_ego_lite_auto_attaches_to_existing_public_research_pipeline(self):
        from muse.browser_research import HybridSourceReader

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = Mock(spec=["doctor"])
            browser.doctor.return_value = {"available": True, "status": "ready"}
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, ego_browser=browser, resource_aware=False,
            )
            self.assertTrue(engine.browser_search_enabled)
            self.assertIsInstance(engine.reader, HybridSourceReader)
            self.assertIs(engine.ego_browser, browser)

    def test_ego_lite_installed_after_engine_start_is_detected_on_next_cycle(self):
        from muse.browser_research import HybridSourceReader

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = Mock(spec=["doctor"])
            browser.doctor.side_effect = [
                {"available": False, "status": "missing"},
                {"available": True, "status": "ready"},
            ]
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, ego_browser=browser, resource_aware=False,
            )
            self.assertNotIsInstance(engine.reader, HybridSourceReader)
            engine._refresh_browser_capabilities()
            self.assertIsInstance(engine.reader, HybridSourceReader)
            self.assertTrue(engine.browser_search_enabled)

    def test_autonomous_cycle_includes_local_creator_memory_and_dialogue_as_inspiration_only(self):
        from muse.conversations import ConversationStore
        from muse.memory import MemoryStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_db = root / "memory.sqlite3"
            conversation_db = root / "conversations.sqlite3"
            with MemoryStore(memory_db) as memory:
                memory.add(
                    "多智能体调度研究经验：先确定单一 Commander，再按能力分配 Agent。CREATOR_PRIVATE_SECRET_XYZ",
                    project="muse", source="creator-approved-memory",
                    kind="case", track="user",
                )
            with ConversationStore(conversation_db) as conversations:
                conversations.save("creator-thread", [
                    {"role": "user", "content": "我希望 Muse 能主动研究量子理论和跨领域灵感"},
                    {"role": "assistant", "content": "每个新假设都要同时寻找支持证据与反证"},
                ])
            search = FakeSearch()
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                memory_db=memory_db, conversation_db=conversation_db,
                workspace=root, search=search, reader=FakeReader(),
                resource_aware=False,
            )
            result = engine.run_once()
            report = result["report"]
            self.assertRegex(result["evolution_cycle_id"], r"^cycle_[0-9a-f]{32}$")
            self.assertEqual(result["evolution_cycle_id"], result["report"]["evolution_cycle_id"])
            self.assertEqual(result["evolution_cycle_id"], result["report"]["inspiration_context"]["evolution_cycle_id"])
            from muse.progress import ProgressEventStore
            with ProgressEventStore(root / "tasks.sqlite3") as progress:                cycle_events = list(reversed(progress.list_events(
                    category="evolution", subject_id=result["evolution_cycle_id"], limit=10,
                )))
            self.assertEqual([item["event"] for item in cycle_events], ["started", "progress", "completed"])

            self.assertEqual(report["inspiration_context_status"], "loaded")
            self.assertGreaterEqual(report["inspiration_context_counts"]["creator_memories"], 1)
            self.assertGreaterEqual(report["inspiration_context_counts"]["dialogue_records"], 1)
            context = report["inspiration_context"]
            self.assertFalse(context["epistemic_contract"]["personal_history_is_external_evidence"])
            self.assertTrue(any(
                item["source_type"] == "dialogue" for item in context["multi_source_context"]
            ))
            self.assertTrue(any(
                item["source_type"] == "creator_history" for item in context["memory_context"]
            ))
            self.assertTrue(all("CREATOR_PRIVATE_SECRET_XYZ" not in query for query in search.queries))
            report_text = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("创造者经验与知识背景", report_text)
            self.assertIn("不是证据", report_text)

    def test_autonomous_cycle_records_failure_when_unexpected_error_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            with patch.object(engine, "_sync_initiative_feedback", side_effect=RuntimeError("private detail")):
                with self.assertRaisesRegex(RuntimeError, "private detail"):
                    engine.run_once()
            from muse.progress import ProgressEventStore
            with ProgressEventStore(root / "tasks.sqlite3") as progress:
                events = list(reversed(progress.list_events(category="evolution", limit=10)))
            self.assertEqual([item["event"] for item in events], ["started", "failed"])
            self.assertEqual(events[-1]["error_code"], "research.unexpected_error")
            self.assertNotIn("private detail", events[-1]["summary"])

    def test_creator_inspired_hypotheses_are_persisted_idempotently_with_provenance(self):
        synthesis = {
            "executive_summary": "两份研究材料都强调反馈与复盘 [S1][S2]。",
            "cross_source_agreements": [], "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [
                "假设：结合创造者强调的失败复盘经验，来源关系图能减少重复研究；检验：通过重复任务实验比较重复率；证伪条件：若重复率没有下降则否定假设 [S1][S2]。"
            ],
            "testable_experiments": [], "uncertainties": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root, auto_build_products=True,
                synthesizer=lambda topic, sources: synthesis,
            )
            result = engine.run_once()
            report = result["report"]
            self.assertEqual(report["creator_inspired_hypothesis_persistence_status"], "completed")
            self.assertEqual(report["value_forge"]["epistemic_status"], "hypothesis_only")
            self.assertGreaterEqual(len(report["value_forge"]["candidates"]), 1)
            self.assertTrue(all(item["evaluation"]["ratings"]["recurrence"] == 1
                                for item in report["value_forge"]["candidates"]))
            self.assertFalse(report["value_forge"]["dispatch_started"])
            self.assertEqual(report["creator_brief"]["value_opportunities_epistemic_status"], "hypothesis_only")
            self.assertGreaterEqual(len(report["creator_brief"]["value_opportunities"]), 1)


            product = report["creator_initiatives"][0]["product"]
            self.assertEqual(product["status"], "testing")
            self.assertTrue(product["validated"])
            self.assertTrue((root / product["package_path"]).is_file())
            self.assertEqual(len(report["creator_inspired_hypothesis_nodes"]), 1)
            first_id = report["creator_inspired_hypothesis_nodes"][0]["id"]
            self.assertEqual(report["creator_inspired_hypothesis_nodes"][0]["novelty_status"],
                             "no_exact_duplicate_found_not_a_novelty_claim")
            report["synthesis"]["creator_inspired_hypotheses"] = [
                synthesis["creator_inspired_hypotheses"][0].replace("[S1][S2]", "[S2]")
            ]
            repeated = engine._persist_creator_inspired_hypotheses(
                {"key": report["topic_key"]}, report, idea_id=report["idea_id"],
                report_path=result["report_path"],
            )
            self.assertEqual(repeated[0]["id"], first_id)
            with ResearchKnowledgeStore(root / "k.sqlite3") as knowledge:
                node = knowledge.get_node(first_id)
                self.assertEqual(node["status"], "proposed")
                self.assertEqual(node["metadata"]["epistemic_status"], "creator_inspired_hypothesis_only")
                self.assertFalse(node["metadata"]["creator_context_is_evidence"])
                self.assertTrue(node["metadata"]["requires_support_and_counter_evidence"])
                self.assertEqual(len(node["provenance"]), 2)
                self.assertTrue(all(url.startswith("https://") for url in node["provenance"]))
                idea = knowledge.get_node(report["idea_id"])
                self.assertIn(first_id, idea["metadata"]["creator_inspired_hypothesis_node_ids"])
                edge = knowledge.connection.execute(
                    "SELECT metadata_json FROM knowledge_edges WHERE source_id=? AND target_id=? AND edge_type='inspired_by'",
                    (report["idea_id"], first_id),
                ).fetchone()
                self.assertIsNotNone(edge)
                self.assertIn("relation_is_not_support_or_refutation", edge[0])

    def test_synthesis_accepts_uncited_structured_creator_hypothesis_as_hypothesis_only(self):
        payload = {
            "executive_summary": "The sources describe distinct research topics [S1][S2].",
            "cross_source_agreements": [], "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [{
                "Hypothesis": "A structured role review may expose overlooked assumptions.",
                "Test": "Compare the number of newly identified assumptions across matched reviews.",
                "Falsifier": "Reject the hypothesis if the structured group identifies no more assumptions.",
            }],
            "testable_experiments": [], "uncertainties": [],
        }
        result = AutonomousResearchEngine._validate_synthesis(payload, 2)
        hypothesis = result["creator_inspired_hypotheses"][0]
        self.assertIn("Hypothesis:", hypothesis)
        self.assertIn("Test:", hypothesis)
        self.assertIn("Falsifier:", hypothesis)

    def test_synthesis_allows_longer_structured_creator_hypothesis(self):
        payload = {
            "executive_summary": "The sources cover separate topics [S1][S2].",
            "cross_source_agreements": [], "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [{
                "Hypothesis": "A structured review may expose overlooked assumptions. " + "This is a proposed hypothesis. " * 20,
                "Test": "Compare the count of newly identified assumptions across matched reviews.",
                "Falsifier": "Reject if the structured review does not increase the count.",
            }],
            "testable_experiments": [], "uncertainties": [],
        }
        result = AutonomousResearchEngine._validate_synthesis(payload, 2)
        self.assertGreater(len(result["creator_inspired_hypotheses"][0]), 500)

    def test_synthesis_still_requires_citations_for_factual_arrays(self):
        payload = {
            "executive_summary": "The source discusses a method [S1].",
            "cross_source_agreements": [], "differences": [], "counter_evidence": ["This factual counter-evidence has no citation."],
            "design_implications_for_muse": [], "testable_experiments": [], "uncertainties": [],        }
        with self.assertRaisesRegex(ValueError, "missing source references"):
            AutonomousResearchEngine._validate_synthesis(payload, 1)

    def test_synthesis_rejects_creator_hypothesis_without_explicit_falsifier(self):
        invalid = {
            "executive_summary": "Sources discuss experimental standards [S1].",
            "cross_source_agreements": [], "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [
                "Hypothesis: the system improves reliability through standardized testing protocols [S1]."
            ],
            "testable_experiments": [], "uncertainties": [],
        }
        with self.assertRaisesRegex(ValueError, "Test and Falsifier"):
            AutonomousResearchEngine._validate_synthesis(invalid, 1)

    def test_creator_inspired_hypothesis_enters_its_own_support_and_counter_research_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hypothesis = (
                "结合创造者的失败复盘经验，假设来源关系图能减少重复研究；"
                "通过重复任务实验比较启用前后的重复率，并允许结果证伪该假设。"
            )
            with ResearchKnowledgeStore(root / "k.sqlite3") as knowledge:
                candidate = knowledge.upsert_node(
                    "claim", "Creator-inspired test hypothesis", summary=hypothesis,
                    external_ref="creator-inspired-test-seed",
                    metadata={
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "not_researched",
                        "allow_external_research": True,
                        "creator_context_is_evidence": False,
                        "requires_support_and_counter_evidence": True,
                        "symbolic_framework_id": "qimen",
                    },
                    status="proposed",
                )
            synthesis = {
                "executive_summary": "来源强调实验可复现性 [S1]。",
                "cross_source_agreements": [], "differences": [], "counter_evidence": [],
                "design_implications_for_muse": [], "creator_inspired_hypotheses": [],
                "testable_experiments": [], "uncertainties": [],
            }
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                synthesizer=lambda topic, sources: synthesis,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["topic_origin"], "creator_inspired_hypothesis_backlog")
            self.assertEqual(topic["creator_inspired_hypothesis_node_id"], candidate["id"])
            self.assertEqual(topic["symbolic_lens"], "qimen")
            self.assertEqual(topic["symbolic_framework_id"], "qimen")
            self.assertEqual(topic["symbolic_framework_attribution"], "source_metadata")
            self.assertIn("independent evidence", topic["support_query"])
            self.assertIn("counterevidence", topic["counter_query"])
            result = engine.run_once()
            self.assertEqual(result["report"]["symbolic_framework_id"], "qimen")
            self.assertEqual(result["report"]["symbolic_framework_attribution"], "source_metadata")
            self.assertEqual(result["report"]["creator_inspired_hypothesis_review_state"],
                             "researched_needs_independent_review")
            self.assertEqual(result["report"]["creator_inspired_hypothesis_persistence_status"],
                             "skipped_for_hypothesis_review_cycle")
            with ResearchKnowledgeStore(root / "k.sqlite3") as knowledge:
                reviewed = knowledge.get_node(candidate["id"])
                self.assertEqual(reviewed["status"], "proposed")
                self.assertEqual(reviewed["metadata"]["review_state"], "researched_needs_independent_review")
                self.assertTrue(reviewed["metadata"]["requires_support_and_counter_evidence"])
                idea_node = knowledge.get_node(result["report"]["idea_id"])
                self.assertEqual(idea_node["metadata"]["symbolic_framework_id"], "qimen")
                self.assertEqual(idea_node["metadata"]["symbolic_framework_attribution"], "source_metadata")
                cross_metadata = idea_node["metadata"]["symbolic_cross_system"]
                self.assertEqual(cross_metadata["epistemic_status"], "symbolic_hypothesis_only")
                self.assertNotIn("systems", cross_metadata)
                self.assertLess(len(json.dumps(idea_node["metadata"], ensure_ascii=False).encode("utf-8")), 16000)
                self.assertEqual(
                    len([node for node in knowledge.list_nodes(node_type="claim", limit=100)
                         if node.get("metadata", {}).get("epistemic_status") == "symbolic_hypothesis_only"]),
                    0,
                )

    def test_creator_candidate_preserves_each_core_symbolic_framework_provenance(self):
        frameworks = ("iching", "qimen", "ziwei", "liu_ren")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = {}
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                for framework_id in (*frameworks, "unknown-tradition"):
                    candidate = knowledge.upsert_node(
                        "claim", f"Symbolic creator candidate: {framework_id}",
                        summary="A symbolic research hypothesis requiring independent support and counter-evidence.",
                        external_ref=f"creator-symbolic-framework:{framework_id}",
                        metadata={
                            "epistemic_status": "creator_inspired_hypothesis_only",
                            "review_state": "not_researched",
                            "allow_external_research": True,
                            "creator_context_is_evidence": False,
                            "requires_support_and_counter_evidence": True,
                            "symbolic_framework_id": framework_id,
                        },
                        status="proposed",
                    )
                    candidates[framework_id] = candidate["id"]
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "progress.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            for framework_id in frameworks:
                topic = engine._next_topic(preferred_key="creator-hypothesis-review:" + candidates[framework_id])
                self.assertEqual(topic["symbolic_lens"], framework_id)
                self.assertEqual(topic["symbolic_framework_id"], framework_id)
                self.assertEqual(topic["symbolic_framework_source_value"], framework_id)
                self.assertEqual(topic["symbolic_framework_attribution"], "source_metadata")
            unknown = engine._next_topic(preferred_key="creator-hypothesis-review:" + candidates["unknown-tradition"])
            self.assertEqual(unknown["symbolic_lens"], "iching")
            self.assertIsNone(unknown["symbolic_framework_id"])
            self.assertEqual(unknown["symbolic_framework_source_value"], "unknown-tradition")
            self.assertEqual(unknown["symbolic_framework_attribution"], "unrecognized_source_metadata")

    def test_frontier_research_topics_have_support_and_counter_queries(self):
        topics = {item["key"]: item for item in RESEARCH_TOPICS}
        self.assertIn("quantum-theory-frontier", topics)
        self.assertIn("contemporary-conspiracy-claims", topics)
        quantum = topics["quantum-theory-frontier"]
        conspiracy = topics["contemporary-conspiracy-claims"]
        self.assertIn("arxiv.org/abs", quantum["support_query"])
        self.assertIn("replication", quantum["counter_query"])
        self.assertIn("original source", conspiracy["support_query"])
        self.assertIn("fact check", conspiracy["counter_query"])
        self.assertIn("factcheck.org", conspiracy["counter_query"])
        self.assertIn("传播热度不是证据", conspiracy["hypothesis"])


    def test_claim_review_recovers_fenced_preamble_and_duplicate_json(self):
        payload = {"verdict": "insufficient", "rationale": "原文不足以直接支持该主张。", "source_refs": ["S1"]}
        raw = "审查结果如下：\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n" + json.dumps(payload, ensure_ascii=False)
        result = AutonomousResearchEngine._validate_claim_review(raw, 1)
        self.assertEqual(result, payload)

    def test_claim_review_rejects_unknown_source_after_json_recovery(self):
        payload = {"verdict": "supported", "rationale": "证据充分。", "source_refs": ["S2"]}
        with self.assertRaises(ValueError):
            AutonomousResearchEngine._validate_claim_review("结果：" + json.dumps(payload), 1)

    def test_claim_review_downgrades_partial_support_to_insufficient(self):
        result = AutonomousResearchEngine._validate_claim_review({
            "verdict": "supported",
            "rationale": "S1 supports only the structural description; S2 and S3 do not directly address measurable software performance and do not contradict the claim.",
            "source_refs": ["S1", "S2", "S3"],
        }, 3)
        self.assertEqual(result["verdict"], "insufficient")
        self.assertIn("未被反驳不等于得到支持", result["rationale"])

    def test_llama_cpp_research_calls_disable_reasoning_only_mode(self):
        synthesis = {
            "executive_summary": "来源共同指出反证检索能减少确认偏误 [S1]。",
            "cross_source_agreements": ["都要求主动寻找反例 [S1]。"],
            "differences": [], "counter_evidence": [],
            "design_implications_for_muse": ["把反证查询设为必经步骤 [S1]。"],
            "creator_inspired_hypotheses": ["假设：结合创造者的失败复盘经验，来源反证图谱能减少重复研究；检验：通过重复任务实验比较重复率；证伪条件：若重复率没有下降则否定假设 [S1]。"],
            "testable_experiments": [], "uncertainties": [],
        }
        review = {"verdict": "insufficient", "rationale": "来源未直接证明主张。", "source_refs": ["S1"]}
        source = [{"title": "来源", "url": "https://example.org/a", "query": "evidence", "text": "原文摘录"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                workspace=root, use_local_synthesis=True,
            )
            with patch("muse.autonomous_research.load_muse_model_config", return_value={
                "provider": "llama_cpp", "base_url": "http://127.0.0.1:1235/v1", "model": "test-model"
            }), patch("muse.autonomous_research.LMStudioClient") as client_type:
                client = client_type.return_value
                client.chat.return_value = json.dumps(synthesis, ensure_ascii=False)
                inspiration = {
                    "creator_intent": "创造者希望研究 Muse 的自主研究能力",
                    "memory_context": [{"content": "历史经验：先验证再升级", "source": "user-memory"}],
                    "multi_source_context": [{"source_type": "dialogue", "content": "对话要求寻找反证", "source": "local-thread"}],
                    "source_fusion_strategy": ["经验只作灵感，不是证据"],
                    "symbolic_lenses": [], "research_frontier_lenses": [],
                }
                result, status = engine._synthesize_report(
                    {"question": "研究问题"}, source, inspiration_brief=inspiration
                )
                sent_prompt = client.chat.call_args.args[0][1]["content"]
                self.assertIn("创造者希望研究 Muse", sent_prompt)
                self.assertIn("经验只作灵感，不是证据", sent_prompt)
                self.assertIn("MUST NOT support factual claims", sent_prompt)
                self.assertIn("creator_inspired_hypotheses", sent_prompt)
                self.assertEqual(status, "completed")
                self.assertEqual(result["executive_summary"], synthesis["executive_summary"])
                self.assertEqual(result["creator_inspired_hypotheses"], synthesis["creator_inspired_hypotheses"])
                self.assertEqual(client_type.call_args_list[0].kwargs["timeout"], 240.0)
                self.assertEqual(client_type.call_args.kwargs["chat_template_kwargs"], {"enable_thinking": False})
                client.chat.return_value = json.dumps(review, ensure_ascii=False)
                result, status = engine._review_claim(
                    {"question": "研究问题"}, {"summary": "待验证主张"}, source,
                )
                self.assertEqual(status, "completed")
                self.assertEqual(result["verdict"], "insufficient")
                self.assertEqual(client_type.call_args_list[1].kwargs["timeout"], 240.0)
                self.assertEqual(client_type.call_args.kwargs["chat_template_kwargs"], {"enable_thinking": False})

    def test_local_claim_review_retries_once_after_malformed_json(self):
        payload = {"verdict": "insufficient", "rationale": "来源没有直接证明该主张。", "source_refs": ["S1"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                workspace=root, use_local_synthesis=True,
            )
            with patch("muse.autonomous_research.load_muse_model_config", return_value={
                "provider": "lmstudio", "base_url": "http://127.0.0.1:1234"
            }), patch("muse.autonomous_research.LMStudioClient") as client_type:
                client = client_type.return_value
                client.chat.side_effect = ["不是 JSON", "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"]
                result, status = engine._review_claim(
                    {"question": "研究问题"}, {"summary": "待验证主张"},
                    [{"title": "来源", "url": "https://example.org/a", "query": "evidence", "text": "原文摘录"}],
                )
            self.assertEqual(result, payload)
            self.assertEqual(status, "completed_after_bounded_retry")
            self.assertEqual(client.chat.call_count, 2)

    def test_local_claim_review_stops_after_one_invalid_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                workspace=root, use_local_synthesis=True,
            )
            with patch("muse.autonomous_research.load_muse_model_config", return_value={
                "provider": "lmstudio", "base_url": "http://127.0.0.1:1234"
            }), patch("muse.autonomous_research.LMStudioClient") as client_type:
                client = client_type.return_value
                client.chat.side_effect = ["bad", "still bad"]
                result, status = engine._review_claim(
                    {"question": "研究问题"}, {"summary": "待验证主张"},
                    [{"title": "来源", "url": "https://example.org/a", "query": "evidence", "text": "原文摘录"}],
                )
            self.assertIsNone(result)
            self.assertEqual(status, "failed:ValueError")
            self.assertEqual(client.chat.call_count, 2)

    def test_local_synthesis_retry_requires_two_distinct_cross_source_refs(self):
        invalid = {
            "executive_summary": "两份来源都涉及状态建模 [S1][S2]。",
            "cross_source_agreements": ["两份来源都提到状态建模 [S1]。"],
            "differences": [], "counter_evidence": [], "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [], "testable_experiments": [], "uncertainties": [],
        }
        valid = {
            **invalid,
            "cross_source_agreements": ["两份来源都提到状态建模 [S1][S2]。"],
        }
        sources = [
            {"title": "来源一", "url": "https://example.org/a", "text": "状态建模方法一"},
            {"title": "来源二", "url": "https://example.net/b", "text": "状态建模方法二"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                workspace=root, use_local_synthesis=True,
            )
            with patch("muse.autonomous_research.load_muse_model_config", return_value={
                "provider": "lmstudio", "base_url": "http://127.0.0.1:1234"
            }), patch("muse.autonomous_research.LMStudioClient") as client_type:
                client = client_type.return_value
                client.chat.side_effect = [
                    json.dumps(invalid, ensure_ascii=False),
                    json.dumps(valid, ensure_ascii=False),
                ]
                result, status = engine._synthesize_report({"question": "状态建模研究"}, sources)
            self.assertEqual(status, "completed_after_bounded_retry")
            self.assertEqual(result["cross_source_agreements"], valid["cross_source_agreements"])
            retry_prompt = client.chat.call_args_list[1].args[0][1]["content"]
            self.assertGreaterEqual(retry_prompt.count("at least two distinct"), 2)



    def test_source_lineage_diagram_distinguishes_reviewed_edges_from_pending_candidates(self):
        lineage = {
            "sources": [
                {"source_node_id": "a", "title": "Original", "url": "https://a.example"},
                {"source_node_id": "b", "title": "Derivative", "url": "https://b.example"},
                {"source_node_id": "c", "title": "Mirror", "url": "https://c.example"},
            ],
            "reviewed_relationships": [{
                "source_node_id": "a", "related_source_node_id": "b",
                "relationship": "derived_from",
                "source_title": "Original", "source_ref": "https://a.example",
                "related_source_title": "Derivative", "related_source_ref": "https://b.example",
            }],
            "identity_candidates": [
                {
                    "source_node_id": "a", "related_source_node_id": "b",
                    "candidate_type": "derived_from", "status": "accepted",
                    "source_title": "Original", "source_ref": "https://a.example",
                    "related_source_title": "Derivative", "related_source_ref": "https://b.example",
                },
                {
                    "source_node_id": "b", "related_source_node_id": "c",
                    "candidate_type": "canonical_equivalent", "status": "pending",
                    "source_title": "Derivative", "source_ref": "https://b.example",
                    "related_source_title": "Mirror", "related_source_ref": "https://c.example",
                },
            ],
        }
        diagram = AutonomousResearchEngine._source_lineage_mermaid(lineage)
        self.assertIn("flowchart LR", diagram)
        self.assertIn("已审 derived_from", diagram)
        self.assertIn("待审 canonical_equivalent", diagram)
        self.assertEqual(diagram.count("已审 derived_from"), 1)
        self.assertIn("-.->", diagram)

    def test_fact_citation_matrix_maps_each_factual_item_to_source_snapshot(self):
        synthesis = {
            "executive_summary": "Finding one [S1]. Finding two [S2]",
            "cross_source_agreements": ["Agreement [S1][S2]"],
            "differences": ["Difference [S1][S2]"],
            "counter_evidence": ["Counterpoint [S2]"],
            "design_implications_for_muse": ["Design idea [S1]"],
            "testable_experiments": ["Experiment [S2]"],
            "creator_inspired_hypotheses": ["Hypothesis: uncited speculative idea"],
        }
        sources = [
            {"title": "Primary", "url": "https://one.example/paper", "citation_id": "cite-one", "sha256": "a" * 64},
            {"title": "Counter", "url": "https://two.example/report", "citation_id": "cite-two", "sha256": "b" * 64},
        ]
        rows = AutonomousResearchEngine._build_fact_citation_matrix(synthesis, sources)
        self.assertEqual(len(rows), 6)
        summary = rows[0]
        self.assertEqual(summary["section"], "executive_summary")
        self.assertEqual(summary["source_ids"], ["S1", "S2"])
        self.assertEqual(summary["citations"][0]["citation_id"], "cite-one")
        self.assertEqual(summary["citations"][1]["sha256"], "b" * 64)
        self.assertEqual(rows[1]["source_ids"], ["S1", "S2"])
        self.assertEqual(rows[2]["section"], "differences")
        self.assertEqual(rows[3]["section"], "counter_evidence")
        self.assertTrue(all(item["epistemic_status"] == "model_synthesized_factual_statement_needs_human_verification" for item in rows))
        self.assertFalse(any("creator_inspired" in item["section"] for item in rows))
        self.assertEqual(AutonomousResearchEngine._build_fact_citation_matrix(None, sources), [])

    def test_second_stage_claim_review_is_audited_without_promoting_claim(self):
        review = {"verdict": "insufficient", "rationale": "现有摘录未直接证明该假设。", "source_refs": ["S1", "S2"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                claim_reviewer=lambda topic, claim, sources: review,
            )
            result = engine.run_once()
            self.assertEqual(result["report"]["claim_review_status"], "completed")
            self.assertEqual(result["report"]["claim_review_state"], "separate_review_pending_human_confirmation")
            self.assertEqual(result["report"]["claim_status_after_review"], "proposed")
            self.assertEqual(result["report"]["claim_reviewer_independence"], "role_separated_not_independent_model")
            dossier = result["report"]["claim_dossier"]
            self.assertEqual(dossier["original_claim"], result["report"]["symbolic_hypothesis"])
            self.assertEqual(dossier["claim_status"], "proposed")
            self.assertEqual(dossier["evidence_counts"]["support_search_candidate"], 1)
            self.assertEqual(dossier["evidence_counts"]["counter_search_candidate"], 1)
            self.assertTrue(all(item["quality"] == "unassessed" for item in dossier["evidence"]))
            self.assertTrue(all(item["independence"] == "unassessed" for item in dossier["evidence"]))
            self.assertEqual(dossier["confidence"]["level"], "unassessed")
            self.assertFalse(dossier["confidence"]["automatic_scoring"])
            self.assertEqual(result["report"]["source_lineage"]["source_count"], len(result["report"]["sources"]))
            self.assertEqual(result["report"]["source_lineage"]["independence_assessment"], "unassessed")
            self.assertEqual(dossier["evidence_coverage"]["source_count"], len(result["report"]["sources"]))
            self.assertEqual(result["report"]["evidence_coverage"]["overall_quality"], "not_automatically_scored")
            self.assertTrue(result["report"]["evidence_coverage"]["retrieval_direction_is_not_evidence_verdict"])


            report_text = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("第二阶段主张审查", report_text)
            self.assertIn("insufficient", report_text)
            self.assertIn("主张证据档案", report_text)
            self.assertIn("事实级引用索引", report_text)
            self.assertIn("模型引用映射，仍需人工核对原文", report_text)
            self.assertIn("证据覆盖与资料完整性", report_text)
            self.assertIn("检索方向不是证据判断", report_text)
            self.assertIn("来源谱系与独立出处核对", report_text)
            self.assertIn("没有观察到依赖关系不等于已证明来源独立", report_text) 
            self.assertIn("页面自报规范出处", report_text)
            self.assertIn("页面自报出版方", report_text)
            self.assertIn("明确审查为独立的来源数", report_text)
            self.assertIn("检索覆盖启发式指数", report_text)
            self.assertIn("flowchart LR", report_text)

            self.assertIn("不等于事实可信度", report_text)




            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                claim = knowledge.get_node(result["report"]["claim_id"])
                self.assertEqual(claim["status"], "proposed")
                self.assertEqual(claim["metadata"]["claim_review"]["reviewer_id"], "muse-claim-review-pass")
                events = knowledge.list_events(entity_id=claim["id"])
                self.assertTrue(any(item["event_type"] == "claim_review_recorded" for item in events))
                evidence = knowledge.list_claim_evidence(claim["id"])
                self.assertEqual(len(evidence), 2)
                self.assertEqual(
                    {item["direction"] for item in evidence},
                    {"support_search_candidate", "counter_search_candidate"},
                )
                self.assertEqual(
                    {item["edge_type"] for item in knowledge.list_edges(node_id=claim["id"])},
                    {"relates"},
                )



    def test_claim_review_rejects_unknown_source_reference(self):
        with self.assertRaises(ValueError):
            AutonomousResearchEngine._validate_claim_review(
                {"verdict": "supported", "rationale": "not grounded", "source_refs": ["S2"]}, 1
            )


    def test_synthesis_requires_valid_source_references_and_is_written_to_report(self):
        synthesis = {
            "executive_summary": "两份来源提供了不同角度的设计信息 [S1] [S2]。",
            "cross_source_agreements": ["都强调任务状态可追踪 [S1] [S2]。"],
            "differences": ["一个侧重调度，一个侧重验证 [S1] [S2]。"],
            "counter_evidence": ["单一调度器也可能形成瓶颈 [S2]。"],
            "design_implications_for_muse": ["为调度任务增加租约和审计事件 [S1]。"],
            "creator_inspired_hypotheses": ["假设：结合创造者的失败复盘经验，研究图谱能减少重复实验；检验：通过重复任务实验比较重复率；证伪条件：若重复率没有下降则否定假设 [S1]。"],
            "testable_experiments": ["模拟调度器崩溃并检查租约回收 [S2]。"],
            "uncertainties": ["来源未提供同一基准下的量化比较。"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                synthesizer=lambda topic, sources: synthesis,
            )
            result = engine.run_once()
            self.assertEqual(result["report"]["synthesis_status"], "completed")
            self.assertEqual(result["report"]["synthesis"]["executive_summary"], synthesis["executive_summary"])
            report_text = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("本机模型证据综合", report_text)
            self.assertIn("单一调度器也可能形成瓶颈 [S2]", report_text)
            self.assertIn("结合创造者经验的跨领域新假设", report_text)

    def test_fallback_creator_synthesis_produces_actionable_testable_hypotheses(self):
        topic = {"question": "如何改善 Muse 的自主进化？"}
        sources = [
            {"title": "Bookmark source", "url": "https://example.org/a"},
            {"title": "Research source", "url": "https://example.org/b"},
            {"title": "Counter source", "url": "https://example.org/c"},
        ]
        result = AutonomousResearchEngine._fallback_creator_synthesis(topic, sources)
        self.assertIn("[S1]", result["executive_summary"])
        self.assertEqual(len(result["creator_inspired_hypotheses"]), 3)
        self.assertTrue(all("Test:" in item and "Falsifier:" in item for item in result["creator_inspired_hypotheses"]))
        self.assertEqual(result["cross_source_agreements"], [])
        self.assertEqual(result["counter_evidence"], [])

    def test_fallback_synthesis_adapts_to_symbolic_lens_and_source_availability(self):
        tarot = AutonomousResearchEngine._fallback_creator_synthesis(
            {"title": "研究 Agent 角色分工", "question": "怎样减少 Agent 的角色盲点？", "symbolic_lens": "tarot"}, []
        )
        vedic = AutonomousResearchEngine._fallback_creator_synthesis(
            {"title": "长期能力建设", "question": "如何安排短中长期能力建设？", "symbolic_lens": "vedic_astrology"}, []
        )
        tarot_text = " ".join(tarot["creator_inspired_hypotheses"])
        vedic_text = " ".join(vedic["creator_inspired_hypotheses"])
        self.assertIn("角色视角矩阵", tarot_text)
        self.assertIn("情境分支树", tarot_text)
        self.assertIn("多时间尺度路线图", vedic_text)
        self.assertNotEqual(tarot_text, vedic_text)
        self.assertNotIn("[S1]", tarot["executive_summary"])
        self.assertTrue(all("Test:" in item and "Falsifier:" in item for item in tarot["creator_inspired_hypotheses"]))

    def test_fallback_synthesis_contextualizes_every_hypothesis_with_topic_and_sources(self):
        question = "怎样让 Muse 从玄学灵感自动制作可测试原型？"
        topic = {"title": "Muse 自主创造原型", "question": question, "symbolic_lens": "iching"}
        sources = [
            {"title": "Bookmark source: symbolic systems", "url": "https://example.org/bookmark", "text": "A charting interface with date selector and state transitions."},
            {"title": "Research source: prototype evaluation", "url": "https://example.org/research"},
        ]
        result = AutonomousResearchEngine._fallback_creator_synthesis(topic, sources)
        hypotheses = result["creator_inspired_hypotheses"]
        self.assertEqual(len(hypotheses), 3)
        self.assertTrue(all(question in item for item in hypotheses))
        self.assertTrue(all("Bookmark source: symbolic systems" in item for item in hypotheses))
        self.assertTrue(all("date selector" in item for item in hypotheses))
        self.assertTrue(all("Test:" in item and "Falsifier:" in item for item in hypotheses))
        self.assertTrue(all("[S" not in item for item in hypotheses))


    def test_synthesis_recovers_duplicate_json_and_object_items(self):


        payload = {
            "executive_summary": "Both sources discuss provenance [S1][S2].",
            "cross_source_agreements": [{"agreement": "Both discuss provenance [S1][S2]."}],
            "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [{"idea": "Track source hashes [S1]."}],
            "testable_experiments": [], "uncertainties": [],
        }
        raw = json.dumps(payload) + chr(10) + json.dumps(payload)
        result = AutonomousResearchEngine._validate_synthesis(raw, 2)
        self.assertIn("Both discuss provenance [S1][S2]", result["cross_source_agreements"][0])
        self.assertIn("Track source hashes [S1]", result["design_implications_for_muse"][0])




    def test_synthesis_rejects_unknown_source_reference(self):
        invalid = {
            "executive_summary": "结论来自第三个来源 [S3]。",
            "cross_source_agreements": [], "differences": [], "counter_evidence": [],
            "design_implications_for_muse": [], "testable_experiments": [], "uncertainties": [],
        }
        with self.assertRaises(ValueError):
            AutonomousResearchEngine._validate_synthesis(invalid, 2)

    def test_research_reminder_message_is_bounded_and_keeps_actionable_fields(self):
        message = _build_research_reminder_message(
            "初步成果，证据待补充", 80, 2,
            "问题" * 1200,
            "data/research/reports/creator-review.md",
            "假设" * 3000,
        )
        self.assertLessEqual(len(message), 2000)
        self.assertIn("研究问题：", message)
        self.assertIn("报告文件：data/research/reports/creator-review.md", message)
        self.assertIn("象征性灵感（待验证假设）：", message)
        self.assertIn("未审核前不要当作事实", message)
        self.assertIn("…", message)

    def test_idle_cycle_researches_both_sides_persists_hypothesis_and_notifies_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), max_sources=4, resource_aware=False,
                workspace=root,            )
            result = engine.run_once()
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["reminder_created"])
            self.assertEqual(result["read_source_count"], 2)
            self.assertEqual(result["report"]["epistemic_status"], "hypothesis_only_not_fact")
            self.assertEqual(result["report"]["evidence_coverage_score"], 80)
            self.assertTrue(result["report"]["high_value"])
            report_path = Path(result["report_path"])
            self.assertTrue(report_path.is_file())
            report_text = report_path.read_text(encoding="utf-8")
            self.assertIn("支持性查询", report_text)
            self.assertIn("SHA-256", report_text)
            self.assertIn("来源快照 ID", report_text)
            self.assertTrue(all(source.get("citation_id") for source in result["report"]["sources"]))
            self.assertTrue(all("status" in source["publication_date"] for source in result["report"]["sources"]))
            self.assertIn("技术灵感（待验证，不是结论）", report_text)
            self.assertIn("多体系象征灵感", report_text)
            self.assertIn("吠陀占星术", report_text)
            self.assertIn("hypothesis_only_not_fact", report_text)
            self.assertEqual(len(result["report"]["symbolic_inspiration_pack"]), 10)
            cross_system = result["report"]["symbolic_cross_system"]
            self.assertEqual(cross_system["framework_ids"], ["iching", "qimen", "ziwei", "liu_ren"])
            self.assertEqual(cross_system["agreement_status"], "not_assessed_without_case_and_rule_sources")
            self.assertEqual(cross_system["calculation_status"], "not_performed")
            self.assertFalse(cross_system["is_evidence"])
            self.assertEqual(result["report"]["inspiration_context"]["symbolic_cross_system"]["framework_ids"],
                             ["iching", "qimen", "ziwei", "liu_ren"])
            self.assertIn("四大核心体系交叉视角（未形成共识）", report_text)
            self.assertTrue(all(item.get("hypothesis_seed") for item in result["report"]["symbolic_inspiration_pack"]))
            self.assertTrue(all(item.get("falsification_rule") for item in result["report"]["symbolic_inspiration_pack"]))
            self.assertIn("待验证假设", report_text)

            self.assertEqual(result["report"]["high_value_gates"], {
                "two_or_more_domains": True, "two_or_more_publisher_groups": True,
                "support_and_counter_search": True,
                "nontrivial_source_text": True, "sha256_present": True,
            })
            next_topic = engine._next_topic()
            self.assertTrue(next_topic["key"].startswith("symbolic-hypothesis-review:"))
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [node for node in knowledge.list_nodes(node_type="idea", limit=100)
                         if node.get("metadata", {}).get("autonomous_topic_key")]
                claims = [node for node in knowledge.list_nodes(node_type="claim", limit=100)
                          if node.get("metadata", {}).get("epistemic_status") == "hypothesis"]
                self.assertEqual(len(ideas), 1)
                self.assertEqual(len(ideas[0]["metadata"]["symbolic_inspiration_pack"]), 10)
                self.assertEqual(ideas[0]["metadata"]["symbolic_cross_system"]["framework_ids"],
                                 ["iching", "qimen", "ziwei", "liu_ren"])
                research_run_id = result["report"]["research_run_experiment_id"]
                research_run = knowledge.get_node(research_run_id)
                self.assertEqual(research_run["metadata"]["execution_class"], "research_source_retrieval")
                self.assertFalse(research_run["metadata"]["verdict_assigned"])
                self.assertEqual(ideas[0]["metadata"]["research_run_experiment_id"], research_run_id)

                symbolic_claims = [node for node in knowledge.list_nodes(node_type="claim", limit=100)
                                   if node.get("metadata", {}).get("epistemic_status") == "symbolic_hypothesis_only"]
                self.assertEqual(len(symbolic_claims), 10)
                self.assertTrue(all(node["status"] == "proposed" for node in symbolic_claims))
                self.assertTrue(all(node["provenance"] == [] for node in symbolic_claims))
                self.assertTrue(all(node["metadata"]["requires_support_and_counter_evidence"] for node in symbolic_claims))


                self.assertEqual(len(claims), 1)
                self.assertEqual(claims[0]["status"], "proposed")
                self.assertTrue(claims[0]["metadata"]["requires_independent_review"])
                self.assertEqual(claims[0]["metadata"]["research_run_experiment_id"], research_run_id)
                idea_links = knowledge.list_edges(node_id=ideas[0]["id"])
                claim_links = knowledge.list_edges(node_id=claims[0]["id"])
                self.assertTrue(any(edge["target_id"] == research_run_id and edge["edge_type"] == "relates"
                                    for edge in idea_links))
                self.assertTrue(any(edge["target_id"] == research_run_id and edge["edge_type"] == "relates"
                                    for edge in claim_links))

                claim_edges = [edge for edge in knowledge.connection.execute(
                    "SELECT e.metadata_json FROM knowledge_edges e "
                    "JOIN knowledge_nodes s ON s.id=e.source_id "
                    "WHERE e.target_id=? AND e.edge_type='relates' AND s.node_type='source'",
                    (claims[0]["id"],)
                ).fetchall()]
                self.assertEqual(len(claim_edges), 2)
                self.assertTrue(all('relation_is_not_support_or_refutation' in edge[0] for edge in claim_edges))
                self.assertEqual(knowledge.stats()["edges"], 20)
            with ReminderStore(root / "reminders.sqlite3") as reminders:
                pending = reminders.list_pending()
                self.assertEqual(len(pending), 1)
                self.assertIn("待验证假设", pending[0]["message"])
            with PhilosophyStore(root / "data/philosophy/muse.sqlite3") as philosophy:
                experiences = philosophy.connection.execute("SELECT kind,epistemic_status FROM philosophy_experiences").fetchall()
                kinds = {row["kind"] for row in experiences}
                self.assertTrue({"research", "inspiration", "reflection"}.issubset(kinds))
                symbolic = philosophy.connection.execute("""SELECT COUNT(*) FROM philosophy_experiences WHERE kind=? AND epistemic_status=?""", ("inspiration", "symbolic_only")).fetchone()[0]
                self.assertGreaterEqual(symbolic, 1)
                self.assertEqual(result["report"]["philosophy_reflection"]["status"], "captured")
                candidates = philosophy.list_principle_candidates()
                self.assertEqual(len(candidates), len(result["report"]["philosophy_reflection"]["principle_candidate_ids"]))
            with InitiativeStore(root / "data/initiatives/muse.sqlite3") as initiatives:
                proposals = initiatives.list()
                self.assertGreaterEqual(len(proposals), 1)
                self.assertTrue(proposals[0]["title"].startswith("创意机会："))
                concept = json.loads(proposals[0]["concept"])
                brief = concept["creator_brief"]
                self.assertEqual(brief["schema_version"], 1)
                self.assertEqual(brief["source_evolution_cycle_id"], result["evolution_cycle_id"])
                self.assertIn("video", brief["deliverables"])
                self.assertIn("offline_product", brief["deliverables"])
                attempts = initiatives.list_video_attempts(limit=10)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(attempts[0]["status"], "queued")
                self.assertEqual(attempts[0]["mode"], "prompt_to_video")
                self.assertEqual(attempts[0]["initiative_id"], proposals[0]["id"])
                self.assertEqual(attempts[0]["evolution_cycle_id"], result["evolution_cycle_id"])
    def test_repeated_model_output_failures_accumulate_as_research_knowledge(self):
        valid_review = {"verdict": "insufficient", "rationale": "来源不足以证明该主张。", "source_refs": ["S1", "S2"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                synthesizer=lambda topic, sources: "invalid model output",
                claim_reviewer=lambda topic, claim, sources: valid_review,
            )
            first = engine.run_once()
            second = engine.run_once()
            self.assertEqual(first["report"]["synthesis_status"], "failed:ValueError")
            report_text = Path(first["report_path"]).read_text(encoding="utf-8")
            self.assertIn("模型输出校验失败详情：", report_text)
            self.assertEqual(second["report"]["synthesis_status"], "failed:ValueError")
            self.assertEqual(len(first["report"]["model_failure_ids"]), 1)
            with ResearchKnowledgeStore(root / "k.sqlite3") as knowledge:
                failures = knowledge.search_failures("local model output schema", limit=10)
                matching = [item for item in failures if item["metadata"].get("signature") == "autonomous-research:synthesis:ValueError"]
                self.assertEqual(len(matching), 1)
                self.assertEqual(len(matching[0]["metadata"]["task_ids"]), 2)
                signals = knowledge.aggregate_improvement_signals(since=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc))
                self.assertEqual(signals["repeated_failure_groups"], 1)

    def test_low_coverage_research_still_creates_preliminary_output_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(shared_domain=True), reader=FakeReader(), resource_aware=False,
                workspace=root,
            )
            result = engine.run_once()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["report"]["evidence_coverage_score"], 60)
            self.assertFalse(result["report"]["high_value"])
            self.assertEqual(result["report"]["creator_video_task"]["status"], "not_queued_low_evidence")
            with InitiativeStore(root / "data/initiatives/muse.sqlite3") as initiatives:
                self.assertEqual(initiatives.list_video_attempts(limit=10), [])
            self.assertTrue(Path(result["report_path"]).is_file())
            self.assertTrue(result["reminder_created"])
            with ReminderStore(root / "reminders.sqlite3") as reminders:
                pending = reminders.list_pending()
                self.assertEqual(len(pending), 1)
                self.assertIn("初步研究成果待阅", pending[0]["title"])
                self.assertIn("证据待补充", pending[0]["message"])
                self.assertIn(result["report_path"], pending[0]["message"])

    def test_research_search_angles_run_concurrently_with_resource_cap(self):
        import threading
        barrier = threading.Barrier(2)
        started = []
        lock = threading.Lock()

        class ParallelSearch(FakeSearch):
            def search(self, query, *, limit=8):
                with lock:
                    started.append(threading.current_thread().name)
                barrier.wait(timeout=3)
                return super().search(query, limit=limit)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=ParallelSearch(), reader=FakeReader(), max_sources=4,
                max_workers=2, resource_aware=False,
                workspace=root,
            )
            result = engine.run_once()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(started), 2)
            self.assertEqual(len(set(started)), 2)
            self.assertTrue(all(name.startswith("ThreadPoolExecutor") for name in started))

    def test_engine_rejects_unbounded_worker_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                AutonomousResearchEngine(
                    evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                    progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                    max_workers=9,
                )

    def test_engine_rejects_unbounded_source_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                AutonomousResearchEngine(
                    evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                    progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                    max_sources=9,
                )


    def test_high_value_research_creates_deduplicated_proposals_with_pending_source_review(self):
        from muse.improvement import ImprovementStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                improvement_db=root / "improvements.sqlite3", workspace=root,
            )
            topic = {"key": "research-topic-1", "title": "Evidence provenance", "question": "How to trace evidence?"}
            report = {
                "high_value": True, "evidence_coverage_score": 80, "epistemic_status": "hypothesis_only_not_fact",
                "synthesis_status": "completed",
                "synthesis": {"design_implications_for_muse": ["Keep source lineage on each proposal [S1][S2]"]},
                "claim_review_status": "completed", "claim_review": {"verdict": "insufficient"},
                "sources": [
                    {"title": "Research guide A", "url": "https://example.org/a", "query": "support", "sha256": "a" * 64, "citation_id": "S1"},
                    {"title": "Research guide B", "url": "https://example.net/b", "query": "counter", "sha256": "b" * 64, "citation_id": "S2"},
                ],
            }
            created = engine._create_improvement_proposals(topic, report, "reports/research.md")
            self.assertEqual(len(created), 1)
            with ImprovementStore(root / "improvements.sqlite3") as store:
                proposal = store.get(created[0])
                self.assertEqual(proposal["status"], "proposed")
                self.assertEqual(len(proposal["sources"]), 2)
                self.assertTrue(all(item["license_status"] == "unknown" for item in proposal["sources"]))
                self.assertTrue(all(item["security_status"] == "needs_review" for item in proposal["sources"]))
                self.assertTrue(proposal["metadata"]["requires_human_source_review"])
            self.assertEqual(engine._create_improvement_proposals(topic, report, "reports/research.md"), [])

    def test_research_proposal_generation_requires_high_value_cited_synthesis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "e.sqlite3", knowledge_db=root / "k.sqlite3",
                progress_db=root / "p.sqlite3", reminder_db=root / "r.sqlite3",
                improvement_db=root / "improvements.sqlite3", workspace=root,
            )
            topic = {"key": "low", "title": "Low coverage", "question": "Question"}
            report = {"high_value": False, "synthesis_status": "completed",
                      "synthesis": {"design_implications_for_muse": ["Uncited idea"]}, "sources": []}
            self.assertEqual(engine._create_improvement_proposals(topic, report, None), [])
            report["high_value"] = True
            self.assertEqual(engine._create_improvement_proposals(topic, report, None), [])

    def test_dynamic_topic_is_discovered_from_repeated_research_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                knowledge.record_failure(
                    "Research fetch failure", summary="Repeated public-source fetch failure.",
                    signature="research:fetch:TimeoutError", reason_to_avoid="Do not blindly retry.",
                    task_id="ticket-a",
                )
                knowledge.record_failure(
                    "Research fetch failure", summary="Repeated public-source fetch failure.",
                    signature="research:fetch:TimeoutError", reason_to_avoid="Do not blindly retry.",
                    task_id="ticket-b",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["key"], "research-gap:repeated-failure")
            self.assertEqual(topic["topic_origin"], "knowledge_gap_signal")
            self.assertNotIn("ticket-a", topic["support_query"])
            self.assertNotIn("TimeoutError", topic["support_query"])

    def test_initiative_review_feedback_syncs_into_improvement_queue_idempotently(self):
        from muse.improvement import ImprovementStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initiative_db = root / "data" / "initiatives" / "muse.sqlite3"
            with InitiativeStore(initiative_db) as initiatives:
                item = initiatives.propose(
                    "Feedback loop target", kind="feature", creator_value="value",
                    concept="concept", first_step="step", expected_output="output",
                )
                initiatives.record_feedback(
                    item["id"], "需要补充失败恢复测试。",
                    decision="revise", source="prototype_review",
                )
                initiatives.record_feedback(
                    item["id"], "当前方案不可接受，缺少证据边界。",
                    decision="reject", source="prototype_review",
                )
                initiatives.record_feedback(
                    item["id"], "整体方向正确。", decision="accept",
                    source="manual",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                improvement_db=root / "improvements.sqlite3", workspace=root,
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            first = engine._sync_initiative_feedback()
            second = engine._sync_initiative_feedback()
            self.assertEqual(first["status"], "completed")
            self.assertEqual(first["created"], 2)
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["existing"], 2)
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                proposals = improvements.list_proposals()
                self.assertEqual(len(proposals), 2)
                self.assertTrue(all(item["status"] == "proposed" for item in proposals))
                self.assertEqual(
                    {item["metadata"]["decision"] for item in proposals},
                    {"revise", "reject"},
                )
                self.assertTrue(all(
                    item["metadata"]["epistemic_status"] == "unverified_user_feedback_signal"
                    for item in proposals
                ))

    def test_dynamic_topic_targets_claims_missing_counter_search_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                claim = knowledge.upsert_node(
                    "claim", "A testable claim", summary="A claim with support search only.",
                    external_ref="test-claim:missing-counter", status="proposed",
                )
                source = knowledge.upsert_node(
                    "source", "Support source", external_ref="https://example.org/support",
                )
                knowledge.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate",
                    evidence_type="research_article", search_query="support only",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(preferred_key="research-gap:missing-counter-evidence:" + claim["id"])
            self.assertEqual(topic["topic_origin"], "research_wiki_evidence_gap")
            self.assertEqual(topic["evidence_gap_kind"], "missing-counter-evidence")
            self.assertEqual(topic["source_claim_id"], claim["id"])
            self.assertEqual(topic["source_node_ids"], [source["id"]])
            self.assertIn("counterexamples", topic["counter_query"])
            self.assertTrue(topic["candidate_only"])
            self.assertTrue(topic["claim_status_unchanged"])
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                self.assertEqual(knowledge.get_node(claim["id"])["status"], "proposed")

    def test_creator_inbox_counter_gap_retains_domain_specific_research_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domains = [
                "粤语语音学与韵律", "停顿与话语分段", "证据不确定性传播",
                "听觉记忆与多媒体理解", "文本转语音声学控制", "人机信任校准",
            ]
            tags = ["粤语旁白", "停顿控制", "证据语气校准"]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                claim = knowledge.upsert_node(
                    "claim", "证据语气校准", summary="A creator hypothesis awaiting counter-evidence.",
                    external_ref="creator-inbox:counter-gap-domain-context", status="proposed",
                    metadata={
                        "origin": "creator_inspiration_inbox",
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "researched_needs_independent_review",
                        "allow_external_research": True,
                        "research_domains": domains,
                        "tags": tags,
                    },
                )
                source = knowledge.upsert_node(
                    "source", "Support source", external_ref="https://example.org/cantonese-support",
                )
                knowledge.record_claim_evidence(
                    claim["id"], source["id"], direction="support_search_candidate",
                    evidence_type="research_article", search_query="support only",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(
                preferred_key="research-gap:missing-counter-evidence:" + claim["id"],
            )
            self.assertEqual(topic["creator_inspiration_node_id"], claim["id"])
            self.assertEqual(topic["creator_tags"], tags)
            self.assertEqual(topic["creator_research_domains"], domains)
            self.assertEqual(topic["creator_hypothesis_summary"], "A creator hypothesis awaiting counter-evidence.")
            self.assertEqual(topic["creator_epistemic_status"], "creator_inspired_hypothesis_only")
            self.assertEqual(topic["creator_review_state"], "researched_needs_independent_review")

            self.assertEqual(
                topic["support_query"],
                "Cantonese prosody pause duration epistemic qualifiers speech perception delayed recall TTS",
            )
            self.assertEqual(
                topic["counter_query"],
                "speech pause duration disfluency fluency naturalness cognitive load listener comprehension counterexamples null effects",
            )

    def test_creator_initiative_and_video_approval_are_idempotent_across_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            topic = {
                "key": "creator-hypothesis-review:" + "a" * 32,
                "title": "粤语证据语气校准",
                "creator_inspiration_node_id": "a" * 32,
            }
            title = _creator_initiative_title(topic)
            self.assertEqual(title, _creator_initiative_title({**topic, "cycle_id": "cycle_" + "d" * 32}))
            video_brief = {
                "title": "粤语证据语气校准预览", "prompt": "A source-grounded Cantonese narration explainer.",
                "negative_prompt": "fabricated facts", "aspect_ratio": "9:16", "duration_seconds": 2,
            }
            recipe_sha256 = "b" * 64
            with InitiativeStore(root / "initiatives.sqlite3") as initiatives:
                args = dict(
                    kind="tool", creator_value="value", concept=json.dumps({"creator_brief": {"video": video_brief}}),
                    first_step="step", expected_output="output",
                )
                first_initiative = initiatives.propose(title, **args)
                second_initiative = initiatives.propose(_creator_initiative_title(topic), **args)
                self.assertEqual(first_initiative["id"], second_initiative["id"])
                first = _queue_creator_video_approval(
                    initiatives, root / "tasks.sqlite3", initiative=first_initiative, topic=topic,
                    video_brief=video_brief, recipe_sha256=recipe_sha256,
                    evolution_cycle_id="cycle_" + "c" * 32,
                )
                second = _queue_creator_video_approval(
                    initiatives, root / "tasks.sqlite3", initiative=second_initiative, topic=topic,
                    video_brief=video_brief, recipe_sha256=recipe_sha256,
                    evolution_cycle_id="cycle_" + "d" * 32,
                )
                self.assertEqual(first["attempt_id"], second["attempt_id"])
                self.assertEqual(first["ticket"]["id"], second["ticket"]["id"])
                self.assertEqual(first["ticket"]["status"], "waiting_approval")
                self.assertEqual(first["attempt"]["approval_task_id"], first["ticket"]["id"])
                self.assertEqual(second["attempt"]["approval_task_id"], first["ticket"]["id"])
                self.assertEqual(second["attempt"]["status"], "queued")
                self.assertIsNot(second["ticket"]["payload"].get("_approved"), True)
                self.assertEqual(len(initiatives.list_video_attempts(limit=20)), 1)
            self.assertEqual(
                _creator_video_attempt_id(topic["key"], first_initiative["id"], recipe_sha256, "c" * 64),
                _creator_video_attempt_id(topic["key"], first_initiative["id"], recipe_sha256, "c" * 64),
            )
            self.assertNotEqual(
                _creator_video_attempt_id(topic["key"], first_initiative["id"], recipe_sha256, "c" * 64),
                _creator_video_attempt_id(topic["key"], first_initiative["id"], recipe_sha256, "d" * 64),
            )

    def test_creator_inbox_no_evidence_gap_requests_both_search_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domains = ["粤语语音学与韵律", "停顿与话语分段", "证据不确定性传播"]
            tags = ["粤语旁白", "证据语气校准"]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                claim = knowledge.upsert_node(
                    "claim", "证据语气校准：无来源重试", summary="A creator hypothesis with no retrieved sources.",
                    external_ref="creator-inbox:no-evidence-retry-fixture", status="proposed",
                    metadata={
                        "origin": "creator_inspiration_inbox",
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "researched_needs_independent_review",
                        "allow_external_research": True,
                        "research_domains": domains,
                        "tags": tags,
                    },
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(
                preferred_key="research-gap:missing-support-and-counter-evidence:" + claim["id"],
            )
            self.assertEqual(topic["key"], "research-gap:missing-support-and-counter-evidence:" + claim["id"])
            self.assertEqual(topic["evidence_gap_kind"], "missing-support-and-counter-evidence")
            self.assertEqual(topic["creator_inspiration_node_id"], claim["id"])
            self.assertEqual(topic["creator_tags"], tags)
            self.assertEqual(topic["creator_research_domains"], domains)
            self.assertEqual(topic["creator_hypothesis_summary"], "A creator hypothesis with no retrieved sources.")

            self.assertEqual(
                topic["support_query"],
                "Cantonese prosody pause duration epistemic qualifiers speech perception delayed recall TTS",
            )
            self.assertIn("counterexamples", topic["counter_query"])

    def test_next_topic_prefers_a_new_support_counter_query_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            first = engine._next_topic()
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                knowledge.upsert_node(
                    "idea", "Previously researched topic", summary="Prior run",
                    external_ref="test-prior-query-pair",
                    metadata={"research_queries": [first["support_query"], first["counter_query"]]},
                )
            second = engine._next_topic()
            self.assertNotEqual(
                (second["support_query"], second["counter_query"]),
                (first["support_query"], first["counter_query"]),
            )
            self.assertFalse(second["research_query_pair_seen"])

    def test_failed_philosophy_experiment_generates_read_only_followup_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            philosophy_db = root / "philosophy.sqlite3"
            with PhilosophyStore(philosophy_db) as philosophy:
                experiment = philosophy.propose_experiment(
                    "reversible_progress", "Small reversible changes reduce regression risk",
                    metric="regression count", baseline="baseline suite", target="no increase",
                    stop_condition="any data loss",
                )
                philosophy.complete_experiment(
                    experiment["id"], outcome="refuted", result="Observed regression count increased in this trial.",
                )
                before = philosophy.get_experiment(experiment["id"])
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                philosophy_db=philosophy_db, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(preferred_key="philosophy-experiment-followup:" + experiment["id"])
            self.assertEqual(topic["topic_origin"], "philosophy_experiment_followup")
            self.assertEqual(topic["source_experiment_id"], experiment["id"])
            self.assertEqual(topic["experiment_outcome"], "refuted")
            self.assertTrue(topic["candidate_only"])
            self.assertTrue(topic["experiment_record_unchanged"])
            with PhilosophyStore(philosophy_db) as philosophy:
                after = philosophy.get_experiment(experiment["id"])
                self.assertEqual(after["outcome"], before["outcome"])
                self.assertEqual(after["result"], before["result"])

    def test_dynamic_topic_is_discovered_from_unreviewed_claim_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                for index in range(5):
                    knowledge.upsert_node(
                        "claim", f"Proposed claim {index}", summary="Needs independent evidence review.",
                        external_ref=f"test-claim:{index}",
                        metadata={"author_id": "researcher", "review_state": "proposed_not_fact_checked"},
                    )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["key"], "research-gap:claim-review-backlog")
            self.assertEqual(topic["topic_origin"], "knowledge_gap_signal")

    def test_creator_hypothesis_queries_prefer_explicit_research_question_and_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = (
                "假设：研究驱动的交互作品可能提升学习效果。"
                "研究问题：与仅提供文字摘要相比，证据链驱动的微型作品是否提升用户理解、延迟记忆与任务完成率？"
                "验证方法：对照实验并测量理解正确率。"
                "反证条件：若作品组没有改善则拒绝假设。"
            )
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                knowledge.upsert_node(
                    "claim", "Evidence-led interactive learning",
                    summary=summary, external_ref="creator-query-compaction-fixture",
                    metadata={
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "not_researched",
                        "allow_external_research": True,
                        "research_domains": ["多模态创作", "学习科学"],
                    },
                    status="proposed",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "progress.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["topic_origin"], "creator_inspired_hypothesis_backlog")
            self.assertEqual(topic["support_query"], "interactive video lecture learning comprehension retention")
            self.assertEqual(topic["counter_query"], "multimedia learning cognitive load limitations")
            self.assertNotIn("研究问题", topic["support_query"])
            self.assertNotIn("验证方法", topic["support_query"])
            self.assertNotIn("反证条件", topic["counter_query"])

    def test_creator_inbox_preserves_full_brief_and_builds_domain_specific_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = (
                "明确问题：Muse 是否能通过单变量标题实验提升长视频观看时长份额？"
                "可测试假设：对 12 条视频并发比较具体承诺型与好奇缺口型标题。"
                "潜在价值：减少混杂因素并形成可复用的创作规律。"
                "研究领域：数字内容实验设计、因果推断、在线平台A/B测试、创作者分析、贝叶斯决策与统计功效。"
                "分类标签：creative-experiment、youtube-ab、watch-time-share。"
                "支持证据搜索方向：官方实验文档与随机对照研究。"
                "反证搜索方向：低功效、受众变化、CTR 与观看时长背离。"
                "可执行实验：每个变体至少 1000 次有效展示，最长 14 天。"
                "明确证伪条件：胜出不超过 5/12 或中位提升不大于 0。"
                + "补充说明：" + ("每个实验均保存视频 ID、版本、分流、展示量、观察窗口、指标和混杂因素。" * 8)
            )
            tags = ["creative-experiment", "youtube-ab", "watch-time-share"]
            domains = ["数字内容实验设计", "因果推断", "在线平台A/B测试", "创作者分析", "贝叶斯决策与统计功效"]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                candidate = knowledge.upsert_node(
                    "claim", "单变量创作实验卡", summary=summary,
                    external_ref="creator-inbox-structured-query-fixture",
                    metadata={
                        "origin": "creator_inspiration_inbox",
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "not_researched",
                        "allow_external_research": True,
                        "research_question": "Muse 是否能通过 YouTube 长视频单变量标题框架实验提升观看时长份额？",
                        "research_domains": domains,
                        "tags": tags,
                    },
                    status="proposed",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "progress.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(preferred_key="creator-hypothesis-review:" + candidate["id"])
            self.assertEqual(topic["creator_inspiration_node_id"], candidate["id"])
            self.assertEqual(topic["hypothesis"], summary)
            self.assertEqual(topic["creator_hypothesis_summary"], summary[:3000])

            self.assertEqual(topic["creator_tags"], tags)
            self.assertEqual(topic["creator_research_domains"], domains)
            self.assertIn("YouTube title A/B testing", topic["support_query"])
            self.assertIn("watch-time share", topic["support_query"])
            self.assertIn("digital content experimentation", topic["support_query"])
            self.assertIn("counterevidence", topic["counter_query"])
            self.assertIn("statistical power", topic["counter_query"])

    def test_creator_inbox_cantonese_prosody_domains_build_targeted_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = (
                "明确问题：限定语后的停顿能否提升粤语听众对证据不确定性的延迟回忆？"
                "可测试假设：在限定分句后加入 250–450ms pause_after，可提高限定语延迟回忆。"
                "潜在价值：让旁白确信程度不越过证据。"
                "研究领域：粤语语音学与韵律、停顿与话语分段、证据不确定性传播、听觉记忆与多媒体理解、文本转语音声学控制、人机信任校准。"
                "分类标签：粤语旁白、停顿控制、证据语气校准。"
                "支持证据搜索方向：粤语韵律、停顿时长、限定语注意和延迟回忆。"
                "反证搜索方向：停顿导致不流利、认知负荷或没有显著增益。"
                "可执行实验：相同文案下比较默认停顿与 250–450ms pause_after。"
                "明确证伪条件：回忆提升不足 10 个百分点或自然度下降超过 0.3 SD。"
            )
            domains = [
                "粤语语音学与韵律", "停顿与话语分段", "证据不确定性传播",
                "听觉记忆与多媒体理解", "文本转语音声学控制", "人机信任校准",
            ]
            tags = ["粤语旁白", "停顿控制", "证据语气校准"]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                candidate = knowledge.upsert_node(
                    "claim", "证据语气校准", summary=summary,
                    external_ref="creator-inbox-cantonese-prosody-query-fixture",
                    metadata={
                        "origin": "creator_inspiration_inbox",
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "not_researched",
                        "allow_external_research": True,
                        "research_domains": domains,
                        "tags": tags,
                    },
                    status="proposed",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "progress.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(preferred_key="creator-hypothesis-review:" + candidate["id"])
            self.assertEqual(topic["creator_inspiration_node_id"], candidate["id"])
            self.assertEqual(topic["creator_tags"], tags)
            self.assertEqual(topic["creator_research_domains"], domains)
            self.assertEqual(topic["creator_hypothesis_summary"], summary[:3000])

            self.assertEqual(
                topic["support_query"],
                "Cantonese prosody pause duration epistemic qualifiers speech perception delayed recall TTS",
            )
            self.assertEqual(
                topic["counter_query"],
                "speech pause duration disfluency fluency naturalness cognitive load listener comprehension counterexamples null effects",
            )

    def test_creator_research_cycle_links_directional_sources_to_original_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = (
                "明确问题：限定语后的停顿能否提升粤语听众对证据不确定性的延迟回忆？"
                "可测试假设：在限定分句后加入 250–450ms pause_after，可提高限定语延迟回忆。"
                "潜在价值：让旁白确信程度不越过证据。"
                "研究领域：粤语语音学与韵律、停顿与话语分段、证据不确定性传播。"
                "分类标签：粤语旁白、停顿控制、证据语气校准。"
                "支持证据搜索方向：粤语韵律与停顿位置。"
                "反证搜索方向：停顿对流利度、自然度和认知负荷的负面影响。"
                "可执行实验：相同文案下比较默认停顿与 250–450ms pause_after。"
                "明确证伪条件：回忆提升不足 10 个百分点或自然度下降超过 0.3 SD。"
            )
            domains = ["粤语语音学与韵律", "停顿与话语分段", "证据不确定性传播"]
            tags = ["粤语旁白", "停顿控制", "证据语气校准"]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                candidate = knowledge.upsert_node(
                    "claim", "证据语气校准", summary=summary,
                    external_ref="creator-inbox:directional-evidence-link-fixture",
                    metadata={
                        "origin": "creator_inspiration_inbox",
                        "epistemic_status": "creator_inspired_hypothesis_only",
                        "review_state": "not_researched",
                        "allow_external_research": True,
                        "research_domains": domains,
                        "tags": tags,
                    },
                    status="proposed",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3",
                knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "progress.sqlite3",
                reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
            )
            result = engine.run_once(
                preferred_topic_key="creator-hypothesis-review:" + candidate["id"],
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["topic_key"], "creator-hypothesis-review:" + candidate["id"])
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                persisted = knowledge.get_node(candidate["id"])
                coverage = knowledge.claim_evidence_coverage(candidate["id"])
            self.assertEqual(persisted["status"], "proposed")
            self.assertEqual(persisted["metadata"]["review_state"], "researched_needs_independent_review")
            self.assertEqual(coverage["direction_counts"]["support_search_candidate"], 1)
            self.assertEqual(coverage["direction_counts"]["counter_search_candidate"], 1)
            self.assertTrue(coverage["both_support_and_counter_present"])

    def test_symbolic_candidate_is_researched_individually_and_kept_unpromoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                candidate = knowledge.upsert_node(
                    "claim", "Symbolic candidate: scenario branches",
                    summary="A symbolic prompt about scenario branches; test with counterexamples.",
                    external_ref="symbolic-hypothesis:fixture:tarot",
                    metadata={
                        "epistemic_status": "symbolic_hypothesis_only", "review_state": "not_researched",
                        "symbolic_framework_id": "tarot",
                        "application_question": "Which scenario branches are testable?",
                        "falsification_rule": "Reject if no repeatable effect is measured.",
                    },
                    status="proposed",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
            )
            result = engine.run_once()
            self.assertEqual(result["topic_key"], "symbolic-hypothesis-review:" + candidate["id"])
            self.assertEqual(result["report"]["symbolic_hypothesis_node_id"], candidate["id"])
            self.assertEqual(result["report"]["symbolic_framework_id"], "tarot")
            self.assertEqual(result["report"]["symbolic_framework_attribution"], "source_metadata")
            self.assertIn("Which scenario branches", result["report"]["question"])
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                stored = next(item for item in knowledge.list_nodes(node_type="claim", limit=100)
                              if item["id"] == candidate["id"])
                idea_node = knowledge.get_node(result["report"]["idea_id"])
                self.assertEqual(idea_node["metadata"]["symbolic_framework_id"], "tarot")
                self.assertEqual(idea_node["metadata"]["symbolic_framework_attribution"], "source_metadata")
                self.assertEqual(stored["status"], "proposed")
                self.assertEqual(stored["metadata"]["review_state"], "researched_needs_independent_review")
                self.assertEqual(stored["provenance"], [])
                self.assertGreaterEqual(len(stored["metadata"]["research_source_urls"]), 1)
                edges = knowledge.list_edges(node_id=candidate["id"], limit=50)
                source_edges = [edge for edge in edges if edge["edge_type"] == "relates"]
                self.assertEqual(len(source_edges), len(stored["metadata"]["research_source_urls"]))
                self.assertTrue(all(edge["metadata"]["relation_is_not_support_or_refutation"] for edge in source_edges))
            with patch("muse.autonomous_research.load_creator_bookmarks", return_value=[{"host": "qimen.live", "title": "Qimen", "url": "https://qimen.live/"}]):
                self.assertEqual(engine._next_topic()["topic_origin"], "creator_chrome_bookmarks")

    def test_symbolic_hypotheses_trigger_their_own_research_gap_not_claim_review_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                knowledge.upsert_node(
                    "claim", "Symbolic hypothesis candidate", summary="A symbolic prompt, not evidence.",
                    external_ref="symbolic-hypothesis:test:tarot",
                    metadata={"epistemic_status": "symbolic_hypothesis_only", "review_state": "not_researched"},
                )
                signals = knowledge.aggregate_improvement_signals(
                    since=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)
                )
                self.assertEqual(signals["proposed_claim_backlog"], 0)
                self.assertEqual(signals["unreviewed_symbolic_hypotheses"], 1)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["key"], "symbolic-hypothesis-review:" + topic["symbolic_hypothesis_node_id"])
            self.assertEqual(topic["topic_origin"], "symbolic_hypothesis_backlog")
            self.assertIn("A symbolic prompt", topic["support_query"])


    def test_feedback_proposal_creates_private_linked_research_topic_without_leaking_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_text = "PRIVATE_REVIEW_SECRET_XYZ 原型缺少失败恢复路径"
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                proposal = improvements.create_feedback_proposal({
                    "id": "feedback-private-001", "initiative_id": "initiative-private-001",
                    "feedback": private_text, "decision": "revise", "reviewer": "creator",
                    "source": "prototype_review", "created_at": "2026-11-09T00:00:00+00:00",
                })
            experiment_calls = []
            def fake_feedback_experiment(**kwargs):
                experiment_calls.append(kwargs)
                return {"status": "succeeded", "run_ref": "experiment-run:feedback-test-run",
                        "metrics": {"tests_run": 7}, "reused": False,
                        "epistemic_status": "engineering_validation_not_external_evidence",
                        "independent_review_state": "pending_distinct_reviewer"}

            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                improvement_db=root / "improvements.sqlite3", workspace=root,
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                feedback_experiment_runner=fake_feedback_experiment,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["topic_origin"], "feedback_driven_improvement")
            self.assertEqual(topic["improvement_proposal_id"], proposal["id"])
            self.assertEqual(topic["source_feedback_id"], "feedback-private-001")
            self.assertNotIn("PRIVATE_REVIEW_SECRET_XYZ", json.dumps(topic, ensure_ascii=False))
            self.assertNotIn(private_text, topic["support_query"])
            self.assertNotIn(private_text, topic["counter_query"])
            self.assertIn("independent", topic["support_query"])
            self.assertIn("counterexamples", topic["counter_query"])
            result = engine.run_once()
            self.assertEqual(result["topic_key"], topic["key"])
            self.assertTrue(all("PRIVATE_REVIEW_SECRET_XYZ" not in query for query in engine.search.queries))
            self.assertEqual(result["report"]["epistemic_status"], "hypothesis_only_not_fact")
            self.assertEqual(result["report"]["feedback_proposal_lifecycle"]["status"], "ready_for_review")
            self.assertEqual(result["report"]["feedback_proposal_lifecycle"]["sources_added"], 2)
            self.assertTrue(result["report"]["research_plan_experiment_id"])
            self.assertEqual(result["report"]["feedback_experiment_execution"]["status"], "succeeded")
            self.assertEqual(result["report"]["feedback_experiment_execution"]["epistemic_status"], "engineering_validation_not_external_evidence")
            self.assertEqual(result["report"]["feedback_experiment_execution"]["independent_review_state"], "pending_distinct_reviewer")
            self.assertEqual(len(experiment_calls), 1)
            self.assertEqual(experiment_calls[0]["proposal_id"], proposal["id"])
            self.assertEqual(experiment_calls[0]["feedback_id"], "feedback-private-001")
            self.assertEqual(experiment_calls[0]["evolution_cycle_id"], result["evolution_cycle_id"])
            self.assertEqual(result["report"]["feedback_experiment_execution"]["evolution_cycle_id"], result["evolution_cycle_id"])
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [item for item in knowledge.list_nodes(node_type="idea", limit=50)
                         if item["metadata"].get("improvement_proposal_id") == proposal["id"]]
                plans = [item for item in knowledge.list_nodes(node_type="experiment", limit=50)
                         if item["metadata"].get("improvement_proposal_id") == proposal["id"]]
                self.assertEqual(len(ideas), 1)
                self.assertEqual(len(plans), 1)
                self.assertEqual(plans[0]["metadata"]["status"], "planned")
                self.assertEqual(plans[0]["metadata"]["epistemic_status"], "research_plan_not_result")
                self.assertNotIn("PRIVATE_REVIEW_SECRET_XYZ", json.dumps(ideas[0], ensure_ascii=False))
                self.assertEqual(ideas[0]["metadata"]["feedback_research_state"], "ready_for_review")
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                reviewed_proposal = improvements.get(proposal["id"])
                self.assertEqual(reviewed_proposal["status"], "ready_for_review")
                self.assertEqual(len(reviewed_proposal["sources"]), 2)
                self.assertTrue(all(source["license_status"] == "unknown" and source["security_status"] == "needs_review" for source in reviewed_proposal["sources"]))
                with self.assertRaisesRegex(ValueError, "acceptable license review"):
                    improvements.approve(proposal["id"], approved_by="user")


    def test_incomplete_feedback_research_retries_without_duplicate_dossier_sources(self):
        class SupportOnlySearch(FakeSearch):
            def search(self, query, *, limit=8):
                if "counterexamples" in query:
                    self.queries.append(query)
                    return []
                return super().search(query, limit=limit)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                proposal = improvements.create_feedback_proposal({
                    "id": "feedback-retry-001", "initiative_id": "initiative-retry-001",
                    "feedback": "private retry feedback", "decision": "revise",
                })
            search = SupportOnlySearch()
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                improvement_db=root / "improvements.sqlite3", workspace=root,
                search=search, reader=FakeReader(), resource_aware=False,
            )
            first = engine.run_once()
            self.assertEqual(first["report"]["feedback_proposal_lifecycle"]["status"], "evidence_incomplete")
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                self.assertEqual(improvements.get(proposal["id"])["status"], "researching")
                self.assertEqual(len(improvements.get(proposal["id"])["sources"]), 1)
            engine.search = FakeSearch()
            second = engine.run_once()
            self.assertEqual(second["topic_key"], first["topic_key"])
            self.assertEqual(second["report"]["feedback_proposal_lifecycle"]["status"], "ready_for_review")
            with ImprovementStore(root / "improvements.sqlite3") as improvements:
                final = improvements.get(proposal["id"])
                self.assertEqual(final["status"], "ready_for_review")
                self.assertEqual(len(final["sources"]), 2)


    def test_source_snapshot_integrity_blocks_tampered_inputs(self):
        valid_text = "A verified public source snapshot."
        valid_hash = hashlib.sha256(valid_text.encode("utf-8")).hexdigest()
        valid_url = "https://example.org/source"
        citation_id = hashlib.sha256((valid_url + "\n" + valid_hash).encode("utf-8")).hexdigest()[:20]
        sources = [
            {"url": valid_url, "text": valid_text, "sha256": valid_hash, "citation_id": citation_id},
            {"url": "https://example.org/tampered", "text": "changed text",
             "sha256": hashlib.sha256(b"original text").hexdigest()},
        ]
        result = AutonomousResearchEngine._verify_source_snapshots(sources)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["items"][0]["status"], "verified")
        self.assertEqual(result["items"][1]["status"], "hash_mismatch")
        self.assertFalse(result["factual_synthesis_allowed"])

    def test_cross_source_differences_are_candidates_not_confirmed_contradictions(self):
        sources = [
            {"title": "Source A", "url": "https://a.example/report", "sha256": "a" * 64,
             "citation_id": "citation-a"},
            {"title": "Source B", "url": "https://b.example/report", "sha256": "b" * 64,
             "citation_id": "citation-b"},
        ]
        synthesis = {
            "differences": [
                "Source A reports a 2024 baseline while Source B reports a 2025 baseline [S1][S2].",
                "A single-source statement [S1].",
            ]
        }
        candidates = AutonomousResearchEngine._build_cross_source_discrepancy_candidates(synthesis, sources)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["candidate_kind"], "reported_cross_source_discrepancy")
        self.assertEqual(candidate["review_state"], "pending_human_review")
        self.assertFalse(candidate["contradiction_confirmed"])
        self.assertFalse(candidate["claim_status_changed"])
        self.assertEqual(candidate["source_ids"], ["S1", "S2"])
        self.assertIn("different_time_scope_population_or_method", candidate["possible_explanations"])

    def test_cross_source_discrepancy_is_persisted_as_proposed_idea_not_contradiction(self):
        synthesis = {
            "executive_summary": "The sources describe different reference periods [S1][S2].",
            "cross_source_agreements": [],
            "differences": ["Source A describes a 2024 baseline while Source B describes a 2025 baseline [S1][S2]."],
            "counter_evidence": ["A counter-source records a different limitation [S1]."],
            "design_implications_for_muse": [],
            "creator_inspired_hypotheses": [], "testable_experiments": [],
            "uncertainties": ["The different reference periods may explain the apparent discrepancy."],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                synthesizer=lambda _topic, _sources: synthesis,
            )
            result = engine.run_once()
            report = result["report"]
            self.assertEqual(report["source_snapshot_integrity"]["status"], "verified")
            self.assertEqual(len(report["cross_source_discrepancy_candidates"]), 1)
            self.assertEqual(report["cross_source_discrepancy_persistence"]["status"], "completed")
            self.assertEqual(report["cross_source_discrepancy_persistence"]["alternative_nodes"], 2)
            self.assertEqual(report["cross_source_discrepancy_persistence"]["counter_evidence_nodes"], 1)
            self.assertEqual(len(report["counter_evidence_candidates"]), 1)
            candidate = report["cross_source_discrepancy_candidates"][0]
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                node = knowledge.get_node(
                    hashlib.sha256(("idea:research-discrepancy:" + candidate["candidate_id"]).encode("utf-8")).hexdigest()[:32]
                )
                self.assertIsNotNone(node)
                self.assertEqual(node["node_type"], "idea")
                self.assertEqual(node["status"], "proposed")
                self.assertEqual(node["metadata"]["review_state"], "pending_human_review")
                self.assertFalse(node["metadata"]["contradiction_confirmed"])
                edges = knowledge.list_edges(node_id=node["id"], limit=20)
                self.assertTrue(edges)
                self.assertIn("alternative", {edge["edge_type"] for edge in edges})
                self.assertIn("relates", {edge["edge_type"] for edge in edges})
                self.assertTrue(all(edge["metadata"].get("relation_is_not_support_or_refutation",
                                                    edge["metadata"].get("not_a_factual_verdict", False))
                                    for edge in edges))
                alternatives = [item for item in knowledge.list_nodes(node_type="idea", limit=500)
                                if str(item.get("external_ref", "")).startswith("research-alternative:")]
                self.assertEqual(len(alternatives), 2)
                self.assertTrue(all(item["status"] == "proposed" for item in alternatives))
                self.assertTrue(all(item["metadata"]["is_evidence"] is False for item in alternatives))
                counter_candidate = report["counter_evidence_candidates"][0]
                counter_id = hashlib.sha256(
                    ("idea:research-counter-evidence:" + counter_candidate["candidate_id"]).encode("utf-8")
                ).hexdigest()[:32]
                counter_node = knowledge.get_node(counter_id)
                self.assertIsNotNone(counter_node)
                self.assertFalse(counter_node["metadata"]["is_refutation_verdict"])
                self.assertEqual(counter_node["metadata"]["review_state"], "pending_human_review")
                counter_edges = knowledge.list_edges(node_id=counter_id, limit=20)
                self.assertTrue(counter_edges)
                self.assertTrue(all(edge["edge_type"] == "relates" for edge in counter_edges))


    def test_snapshot_integrity_failure_prevents_synthesis_and_claim_review(self):
        synthesis_calls = []
        review_calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                search=FakeSearch(), reader=FakeReader(), resource_aware=False,
                workspace=root,
                synthesizer=lambda *_args: synthesis_calls.append(True),
                claim_reviewer=lambda *_args: review_calls.append(True),
            )
            with patch.object(
                AutonomousResearchEngine, "_verify_source_snapshots",
                return_value={"status": "failed", "source_count": 2, "verified_count": 1,
                              "failed_count": 1, "items": [], "factual_synthesis_allowed": False},
            ):
                result = engine.run_once()
            report = result["report"]
            self.assertEqual(report["synthesis_status"], "source_snapshot_integrity_failed")
            self.assertEqual(report["claim_review_status"], "source_snapshot_integrity_failed")
            self.assertIsNone(report["synthesis"])
            self.assertIsNone(report["claim_review"])
            self.assertEqual(synthesis_calls, [])
            self.assertEqual(review_calls, [])
            self.assertEqual(report["cross_source_discrepancy_candidates"], [])

    def test_failed_research_run_returns_to_topic_queue_without_leaking_query_or_failure_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge_db = root / "knowledge.sqlite3"
            private_query = "PRIVATE_RESEARCH_QUERY_42"
            private_error = "PRIVATE_NETWORK_EXCEPTION_42"
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                record = knowledge.record_research_run(
                    private_query, run_id="failed-run-001", source_refs=[], status="failed",
                    failures=[{"stage": "search", "error": private_error}],
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=knowledge_db,
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic()
            self.assertEqual(topic["topic_origin"], "research_run_followup")
            self.assertEqual(topic["key"], "research-run-followup:failed-run-001")
            self.assertEqual(topic["source_experiment_id"], record["experiment_id"])
            self.assertEqual(topic["source_run_id"], "failed-run-001")
            self.assertEqual(topic["research_run_status"], "failed")
            self.assertTrue(topic["candidate_only"])
            self.assertNotIn(private_query, json.dumps(topic, ensure_ascii=False))
            self.assertNotIn(private_error, json.dumps(topic, ensure_ascii=False))
            with ResearchKnowledgeStore(knowledge_db) as knowledge:
                original = knowledge.get_node(record["experiment_id"])
            self.assertEqual(original["metadata"]["status"], "failed")

    def test_rejected_independent_review_decision_returns_to_research_topic_queue_without_leaking_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_finding = "PRIVATE_REVIEW_FINDING_9f2a"
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                prior_decision = knowledge.upsert_node(
                    "decision", "Rejected candidate with private finding",
                    summary="An independent engineering review rejected the candidate.",
                    external_ref="improvement-review:review-private-001",
                    metadata={
                        "decision_kind": "improvement_validation_review",
                        "improvement_proposal_id": "proposal-private-001",
                        "source_feedback_id": "feedback-private-001",
                        "review_id": "review-private-001", "approved": False,
                        "findings": [{"severity": "high", "title": private_finding}],
                        "epistemic_status": "engineering_review_rejected_not_external_evidence",
                    },
                    status="rejected",
                )
                independent_decision = knowledge.upsert_node(
                    "decision", "Rejected independent engineering experiment review",
                    summary="A distinct engineering experiment review rejected the candidate.",
                    external_ref="feedback-research-review:review-independent-private-002",
                    metadata={
                        "decision_kind": "independent_engineering_experiment_review",
                        "improvement_proposal_id": "proposal-independent-002",
                        "source_feedback_id": "feedback-independent-002",
                        "review_id": "review-independent-private-002", "approved": False,
                        "findings": [{"severity": "medium", "title": private_finding}],
                        "epistemic_status": "engineering_review_rejected_not_external_evidence",
                    },
                    status="rejected",
                )
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            topic = engine._next_topic(preferred_key="rejected-review:" + independent_decision["id"])
            self.assertEqual(topic["topic_origin"], "rejected_review_followup")
            self.assertEqual(topic["rejected_review_node_id"], independent_decision["id"])
            self.assertEqual(topic["improvement_proposal_id"], "proposal-independent-002")
            self.assertEqual(topic["epistemic_status"], "engineering_review_rejected_not_external_evidence")
            self.assertNotIn(private_finding, json.dumps(topic, ensure_ascii=False))
            self.assertNotIn("feedback-independent-002", topic["support_query"])
            self.assertIn("independent engineering code review", topic["support_query"])
            self.assertIn("counterexamples", topic["counter_query"])
            prior_topic = engine._next_topic(preferred_key="rejected-review:" + prior_decision["id"])
            self.assertEqual(prior_topic["improvement_proposal_id"], "proposal-private-001")
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                knowledge.upsert_node(
                    "idea", "Completed rejected review follow-up", summary="A research cycle already covered this query pair.",
                    external_ref="autonomous-idea:" + topic["key"],
                    metadata={
                        "autonomous_topic_key": topic["key"],
                        "research_queries": [topic["support_query"], topic["counter_query"]],
                        "last_researched_at": "2026-11-19T14:00:00+08:00",
                        "topic_origin": "rejected_review_followup",
                    },
                )
            next_topic = engine._next_topic()
            self.assertNotEqual(next_topic["key"], topic["key"])

    def test_interrupted_evolution_cycle_resumes_same_cycle_and_topic_checkpoint(self):
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_db = root / "tasks.sqlite3"
            cycle_id = "cycle_" + "a" * 32
            with ProgressEventStore(progress_db) as progress:
                progress.record("evolution", cycle_id, "started", summary="Autonomous research cycle started")
                progress.record("evolution", cycle_id, "progress", summary="topic_key:rejected-review:decision-001")
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=progress_db, reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            captured = {}
            def resume_cycle(**kwargs):
                captured.update(kwargs)
                return {"status": "completed", "report": {}}
            with patch.object(engine, "_run_once_with_cycle", side_effect=resume_cycle):
                result = engine.run_once()
            self.assertEqual(result["evolution_cycle_id"], cycle_id)
            self.assertEqual(captured["cycle_id"], cycle_id)
            self.assertEqual(captured["resume_topic_key"], "rejected-review:decision-001")
            self.assertEqual(engine._interrupted_cycle_checkpoint(), (None, None))


    def test_interrupted_cycle_event_resumes_same_cycle_and_topic_checkpoint(self):
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_db = root / "tasks.sqlite3"
            cycle_id = "cycle_" + "c" * 32
            with ProgressEventStore(progress_db) as progress:
                progress.record("evolution", cycle_id, "started", summary="Autonomous research cycle started")
                progress.record("evolution", cycle_id, "progress", summary="topic_key:resume-this-topic")
                progress.record("evolution", cycle_id, "interrupted", summary="Autonomous research cycle interrupted and resumable")
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=progress_db, reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            captured = {}
            def resume_cycle(**kwargs):
                captured.update(kwargs)
                return {"status": "completed", "report": {}}
            with patch.object(engine, "_run_once_with_cycle", side_effect=resume_cycle):
                result = engine.run_once()
            self.assertEqual(result["evolution_cycle_id"], cycle_id)
            self.assertEqual(captured["cycle_id"], cycle_id)
            self.assertEqual(captured["resume_topic_key"], "resume-this-topic")
            self.assertEqual(engine._interrupted_cycle_checkpoint(), (None, None))

    def test_stop_event_records_resumable_interrupted_cycle_not_cancelled(self):
        from threading import Event
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_db = root / "tasks.sqlite3"
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=progress_db, reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            stop_event = Event()
            stop_event.set()
            result = engine.run_once(stop_event=stop_event)
            cycle_id = result["evolution_cycle_id"]
            topic_key = result["topic_key"]
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(engine._interrupted_cycle_checkpoint(), (cycle_id, topic_key))
            with ProgressEventStore(progress_db) as progress:
                events = list(reversed(progress.list_events(category="evolution", subject_id=cycle_id, limit=10)))
            self.assertEqual(events[-1]["event"], "interrupted")
            self.assertNotIn("cancelled", [event["event"] for event in events])
            resumed = engine.run_once()
            self.assertEqual(resumed["evolution_cycle_id"], cycle_id)
            self.assertEqual(resumed["topic_key"], topic_key)
            self.assertEqual(engine._interrupted_cycle_checkpoint(), (None, None))

    def test_stop_after_research_pipeline_preserves_topic_and_defers_knowledge_writes(self):
        from threading import Event
        from muse.progress import ProgressEventStore
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.research_pipeline import ResearchPipeline

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_db = root / "tasks.sqlite3"
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=progress_db, reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            stop_event = Event()
            original_run = ResearchPipeline.run
            def run_then_request_stop(pipeline, *args, **kwargs):
                result = original_run(pipeline, *args, **kwargs)
                stop_event.set()
                return result
            with patch.object(ResearchPipeline, "run", new=run_then_request_stop):
                interrupted = engine.run_once(stop_event=stop_event)
            self.assertEqual(interrupted["status"], "interrupted")
            cycle_id, topic_key = engine._interrupted_cycle_checkpoint()
            self.assertEqual(cycle_id, interrupted["evolution_cycle_id"])
            self.assertEqual(topic_key, interrupted["topic_key"])
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [item for item in knowledge.list_nodes(node_type="idea", limit=50)
                         if item.get("metadata", {}).get("autonomous_topic_key") == topic_key]
                self.assertEqual(ideas, [])
            resumed = engine.run_once()
            self.assertEqual(resumed["evolution_cycle_id"], cycle_id)
            self.assertEqual(resumed["topic_key"], topic_key)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [item for item in knowledge.list_nodes(node_type="idea", limit=50)
                         if item.get("metadata", {}).get("autonomous_topic_key") == topic_key]
                self.assertEqual(len(ideas), 1)
            with ProgressEventStore(progress_db) as progress:
                events = list(reversed(progress.list_events(category="evolution", subject_id=cycle_id, limit=20)))
            self.assertEqual(events[-1]["event"], "completed")

    def test_stop_after_report_checkpoint_defers_later_persistence_until_resume(self):
        from threading import Event
        from muse.research_knowledge import ResearchKnowledgeStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            stop_event = Event()
            original_persist = engine._persist_report
            persist_calls = []

            def persist_then_stop(*args, **kwargs):
                path = original_persist(*args, **kwargs)
                persist_calls.append(path)
                stop_event.set()
                return path

            with patch.object(engine, "_persist_report", side_effect=persist_then_stop), \
                 patch.object(engine, "_persist_creator_inspired_hypotheses", wraps=engine._persist_creator_inspired_hypotheses) as persist_hypotheses:
                interrupted = engine.run_once(stop_event=stop_event)
                self.assertEqual(interrupted["status"], "interrupted")
                self.assertEqual(len(persist_calls), 1)
                persist_hypotheses.assert_not_called()
                cycle_id, topic_key = engine._interrupted_cycle_checkpoint()
                self.assertEqual(cycle_id, interrupted["evolution_cycle_id"])
                self.assertEqual(topic_key, interrupted["topic_key"])

            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [item for item in knowledge.list_nodes(node_type="idea", limit=50)
                         if item.get("metadata", {}).get("autonomous_topic_key") == topic_key]
                self.assertEqual(len(ideas), 1)
                inspired = [item for item in knowledge.list_nodes(node_type="claim", limit=100)
                            if item.get("external_ref", "").startswith("creator-inspired-hypothesis:")]
                self.assertEqual(inspired, [])
            resumed = engine.run_once()
            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(resumed["evolution_cycle_id"], cycle_id)
            self.assertEqual(resumed["topic_key"], topic_key)

    def test_two_completed_research_cycles_start_fresh_topics_and_persist_terminal_events(self):
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=root / "tasks.sqlite3", reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )
            first = engine.run_once()
            second = engine.run_once()

            self.assertEqual(first["status"], "completed")
            self.assertEqual(second["status"], "completed")
            self.assertNotEqual(first["evolution_cycle_id"], second["evolution_cycle_id"])
            self.assertNotEqual(first["topic_key"], second["topic_key"])
            with ProgressEventStore(root / "tasks.sqlite3") as progress:
                for cycle_id in (first["evolution_cycle_id"], second["evolution_cycle_id"]):
                    events = list(reversed(progress.list_events(
                        category="evolution", subject_id=cycle_id, limit=50,
                    )))
                    self.assertEqual(events[0]["event"], "started")
                    self.assertEqual(events[-1]["event"], "completed")
                    self.assertEqual(sum(
                        1 for event in events
                        if str(event.get("summary", "")).startswith("topic_key:")
                    ), 1)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                ideas = [item for item in knowledge.list_nodes(node_type="idea", limit=500)
                         if item.get("metadata", {}).get("autonomous_topic_key")]
                self.assertGreaterEqual(len({item["metadata"]["autonomous_topic_key"] for item in ideas}), 2)

    def test_crash_after_research_persistence_resumes_same_topic_without_duplicate_idea(self):
        from muse.progress import ProgressEventStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_db = root / "tasks.sqlite3"
            cycle_id = "cycle_" + "b" * 32
            engine = AutonomousResearchEngine(
                evidence_db=root / "evidence.sqlite3", knowledge_db=root / "knowledge.sqlite3",
                progress_db=progress_db, reminder_db=root / "reminders.sqlite3",
                workspace=root, search=FakeSearch(), reader=FakeReader(), resource_aware=False,
            )

            # Simulate a process dying after durable research writes/checkpoints but
            # before it can append the terminal cycle event.
            interrupted = engine._run_once_with_cycle(cycle_id=cycle_id)
            self.assertEqual(interrupted["status"], "completed")
            original_topic = interrupted["topic_key"]
            with ProgressEventStore(progress_db) as progress:
                self.assertEqual(progress.list_events(
                    category="evolution", subject_id=cycle_id, limit=20,
                )[0]["event"], "progress")

            resumed = engine.run_once()
            self.assertEqual(resumed["evolution_cycle_id"], cycle_id)
            self.assertEqual(resumed["topic_key"], original_topic)
            with ResearchKnowledgeStore(root / "knowledge.sqlite3") as knowledge:
                matching = [item for item in knowledge.list_nodes(node_type="idea", limit=500)
                            if item.get("metadata", {}).get("autonomous_topic_key") == original_topic]
                self.assertEqual(len(matching), 1)
            with ProgressEventStore(progress_db) as progress:
                events = list(reversed(progress.list_events(
                    category="evolution", subject_id=cycle_id, limit=50,
                )))
            self.assertEqual(events[-1]["event"], "completed")

if __name__ == "__main__":
    unittest.main()
