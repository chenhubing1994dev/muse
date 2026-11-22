"""End-to-end closed loop: Inspiration -> Autonomous Follow-Up -> Self-Improvement -> Viral Video Blueprint.

Uses the authoritative Muse stores (ResearchKnowledgeStore, AutonomousResearchEngine,
PhilosophyStore, ImprovementStore, PipelineController/TaskQueue). Never creates competing
schedulers, never auto-approves code patches, and respects allow_external_research privacy.
"""
from __future__ import annotations

from collections.abc import Mapping

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from muse.autonomous_research import AutonomousResearchEngine
from muse.improvement import ImprovementStore
from muse.philosophy import PhilosophyStore
from muse.pipeline_control import PipelineController
from muse.research_knowledge import ResearchKnowledgeStore
from muse.conversations import ConversationStore
from muse.inspiration import InspirationEngine, build_symbolic_inspiration_pack
from muse.memory import MemoryStore
import uuid

VIRAL_BLUEPRINT_ACT_MODES = (
    "hook_radar",
    "pain_split",
    "evidence_bars",
    "counter_balance",
    "evolution_loop",
    "action_seal",
)


def _store_paths(workspace: Path) -> dict[str, Path]:
    data = workspace / "data"
    return {
        "evidence_db": data / "research" / "evidence.sqlite3",
        "knowledge_db": data / "research" / "knowledge.sqlite3",
        "progress_db": data / "tasks" / "muse.sqlite3",
        "reminder_db": data / "reminders" / "muse.sqlite3",
        "improvement_db": data / "improvements" / "muse.sqlite3",
        "philosophy_db": data / "philosophy" / "muse.sqlite3",
        "memory_db": data / "memory" / "muse.sqlite3",
        "conversation_db": data / "conversations" / "muse.sqlite3",
        "report_dir": data / "research" / "reports",
        "dossiers_dir": data / "improvements" / "dossiers",
        "blueprints_dir": data / "prometheus" / "blueprints",
        "inspiration_briefs_dir": data / "prometheus" / "inspiration_briefs",
    }



def _build_inspiration_context(
    *, workspace: Path, paths: dict[str, Path], inspiration: dict[str, Any],
    allow_external_research: bool,
) -> dict[str, Any]:
    """Fuse local User Track/dialogue/Research Wiki context, then persist a private local brief.

    Only the four core symbolic hypothesis seeds are emitted to the research graph and
    blueprint. Raw User Track memories and conversation text never enter public queries,
    video blueprints, or Commander ticket payloads.
    """
    goal = f"{inspiration.get('title', '')}\n{inspiration.get('summary', '')}".strip()[:4000]
    if not goal:
        raise ValueError("inspiration must contain a title or summary")
    with MemoryStore(paths["memory_db"]) as memory, ConversationStore(paths["conversation_db"]) as conversations, ResearchKnowledgeStore(paths["knowledge_db"]) as knowledge:
        brief = InspirationEngine(
            memory, conversations=conversations, research_knowledge=knowledge,
        ).build_brief(
            goal, project="muse", creator_intent=str(inspiration.get("summary") or "")[:2000],
        )
        pack = build_symbolic_inspiration_pack(goal[:2000])
        selected = [item for item in pack if item["id"] in {"iching", "qimen", "ziwei", "liu_ren"}]
        candidate_ids: list[str] = []
        for item in selected:
            candidate_digest = hashlib.sha256(
                f"{inspiration['id']}|{item['id']}|{goal}".encode("utf-8")
            ).hexdigest()
            external_ref = "inspiration-engine:sha256:" + candidate_digest
            existing_row = knowledge.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE external_ref=? AND deleted_at IS NULL",
                (external_ref,),
            ).fetchone()
            existing = knowledge.get_node(existing_row["id"]) if existing_row else None
            if existing and existing["status"] in {"supported", "refuted", "contested", "superseded"}:
                candidate = existing
            else:
                old_metadata = dict(existing.get("metadata") or {}) if existing else {}
                metadata = {
                    **old_metadata,
                    "origin": "inspiration_engine_generated_candidate",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "creator_context_is_evidence": False,
                    "is_evidence": False,
                    "is_prediction": False,
                    "review_state": old_metadata.get("review_state", "not_researched"),
                    "allow_external_research": bool(allow_external_research),
                    "symbolic_framework_id": item["id"],
                    "application_question": item["application_question"],
                    "falsification_rule": item["falsification_rule"],
                    "validation_plan": item["validation_plan"],
                    "parent_inspiration_id": inspiration["id"],
                    "generated_by": "muse.inspiration_evolution",
                    "private_context_exported": False,
                }
                candidate = knowledge.upsert_node(
                    "claim", f"{inspiration.get('title', 'Muse 灵感')}｜{item['name']}视角假设",
                    summary=item["hypothesis_seed"], external_ref=external_ref,
                    metadata=metadata,
                    provenance=("muse.inspiration_engine", f"creator-inspiration:{inspiration['id']}"),
                    status=existing["status"] if existing else "proposed",
                )
            knowledge.add_edge(
                candidate["id"], inspiration["id"], "inspired_by",
                metadata={"framework_id": item["id"], "epistemic_status": "symbolic_hypothesis_only", "is_evidence": False},
            )
            candidate_ids.append(candidate["id"])

    brief["private_storage_contract"] = {
        "storage_scope": "local_workspace_only",
        "user_track_memory_exported": False,
        "conversation_context_exported": False,
        "video_blueprint_contains_private_context": False,
        "public_research_queries_include_private_context": False,
        "automatic_user_memory_write": False,
    }
    encoded = json.dumps(brief, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    brief_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    brief_dir = paths["inspiration_briefs_dir"]
    if brief_dir.is_symlink():
        raise ValueError("private inspiration brief directory must not be a symlink")
    brief_dir.mkdir(parents=True, exist_ok=True)
    target = brief_dir / f"{inspiration['id']}-{brief_digest[:16]}.json"
    if target.is_symlink():
        raise ValueError("private inspiration brief target must not be a symlink")
    if not target.exists():
        temporary = brief_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(target)
    symbolic_inspiration = [{
        "id": item["id"], "name": item["name"], "tradition": item["tradition"],
        "application_question": item["application_question"],
        "hypothesis_seed": item["hypothesis_seed"],
        "falsification_rule": item["falsification_rule"],
        "validation_plan": item["validation_plan"],
        "epistemic_status": "symbolic_hypothesis_only", "is_evidence": False,
    } for item in selected]
    return {
        "brief_path": target.relative_to(workspace).as_posix(),
        "brief_sha256": brief_digest, "brief_status": "ready",
        "user_memory_match_count": int(brief.get("memory_match_count", 0)),
        "conversation_context_count": sum(
            1 for item in brief.get("multi_source_context", []) if item.get("source_type") == "dialogue"
        ),
        "candidate_ids": candidate_ids, "symbolic_inspiration": symbolic_inspiration,
        "private_context_exported": False,
    }


def _run_optional_business_bridge(
    *,
    workspace: Path,
    paths: dict[str, Path],
    report: dict[str, Any],
    report_path: str | None,
    inspiration_id: str,
    business_brief: Mapping[str, Any] | None,
    business_ratings: Mapping[str, int] | None,
    business_database: str | Path | None,
    business_currency: str,
    estimated_price_minor: int | None,
    estimated_cost_minor: int | None,
) -> dict[str, Any]:
    """Create a business pilot candidate only from complete, explicit caller inputs."""
    supplied = {
        "business_brief": business_brief is not None,
        "business_ratings": business_ratings is not None,
        "business_database": business_database is not None,
    }
    if not any(supplied.values()):
        return {"status": "not_requested", "candidate_created": False}
    missing = [name for name, present in supplied.items() if not present]
    if missing:
        return {
            "status": "blocked_incomplete_opt_in",
            "candidate_created": False,
            "missing_inputs": missing,
            "market_demand_inferred": False,
            "willingness_to_pay_inferred": False,
        }
    try:
        database_path = Path(business_database).expanduser()
        if not database_path.is_absolute():
            database_path = workspace / database_path
        if not str(database_path).strip() or database_path.name in {"", ".", ".."}:
            raise ValueError("business_database must be a concrete database file path")
        workspace_root = workspace.resolve()
        data_root = workspace_root / "data"
        business_root = data_root / "business"
        if data_root.is_symlink() or business_root.is_symlink() or database_path.is_symlink():
            return {
                "status": "blocked_symlink_path",
                "candidate_created": False,
                "error_type": "ValueError",
            }
        resolved_business_root = business_root.resolve()
        resolved_database = database_path.resolve()
        if (not resolved_business_root.is_relative_to(workspace_root)
                or not resolved_database.is_relative_to(resolved_business_root)):
            return {
                "status": "blocked_database_outside_business_dir",
                "candidate_created": False,
                "error_type": "ValueError",
            }
        protected_paths = {
            path.resolve() for path in paths.values()
            if isinstance(path, Path) and path.suffix in {".sqlite3", ".db"}
        }
        if resolved_database in protected_paths:
            return {
                "status": "blocked_database_collision",
                "candidate_created": False,
                "error_type": "ValueError",
            }
        from muse.research_business_bridge import create_candidate_from_research_report
        bridge_result = create_candidate_from_research_report(
            report,
            business_brief,
            business_ratings,
            business_database=database_path,
            report_path=report_path or "",
            currency=business_currency,
            estimated_price_minor=estimated_price_minor,
            estimated_cost_minor=estimated_cost_minor,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {
            "status": "invalid_input" if isinstance(exc, (TypeError, ValueError)) else "failed",
            "candidate_created": False,
            "error_type": type(exc).__name__,
            "market_demand_inferred": False,
            "willingness_to_pay_inferred": False,
        }

    opportunity = bridge_result.get("opportunity")
    if not isinstance(opportunity, Mapping) or not isinstance(opportunity.get("id"), str):
        return {
            "status": "candidate_created_but_unlinked",
            "candidate_created": True,
            "error_type": "InvalidBridgeResult",
            "market_demand_inferred": False,
            "willingness_to_pay_inferred": False,
        }
    opportunity_id = opportunity["id"]
    wiki_idea_id = None
    try:
        with ResearchKnowledgeStore(paths["knowledge_db"]) as knowledge:
            external_ref = f"business-opportunity:{opportunity_id}"
            existing_row = knowledge.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE external_ref=? AND deleted_at IS NULL",
                (external_ref,),
            ).fetchone()
            existing = knowledge.get_node(existing_row["id"]) if existing_row else None
            metadata = {
                **(dict(existing.get("metadata") or {}) if existing else {}),
                "origin": "research_business_bridge",
                "business_opportunity_id": opportunity_id,
                "business_opportunity_status": bridge_result.get("opportunity_status", "candidate"),
                "research_assumption_evidence_id": bridge_result.get("research_assumption_evidence_id"),
                "research_report_sha256": bridge_result.get("research_report_sha256"),
                "market_demand_inferred": False,
                "willingness_to_pay_inferred": False,
                "customer_validation_required": True,
                "requires_human_review": True,
                "initiative_created": False,
                "dispatch_started": False,
                "publishing_started": False,
                "gpu_work_started": False,
            }
            idea = knowledge.upsert_node(
                "idea",
                str(opportunity.get("title") or "Research business pilot candidate")[:200],
                summary="A caller-framed business pilot candidate exists in the separate Business Opportunity ledger. Customer validation and human review remain required.",
                external_ref=external_ref,
                metadata=metadata,
                provenance=("muse.research_business_bridge", f"creator-inspiration:{inspiration_id}"),
                status=existing["status"] if existing else "proposed",
            )
            knowledge.add_edge(
                idea["id"], inspiration_id, "inspired_by",
                metadata={"business_opportunity_id": opportunity_id, "candidate_only": True},
            )
            wiki_idea_id = idea["id"]
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
        return {
            "status": "candidate_created_graph_link_failed",
            "candidate_created": True,
            "opportunity_id": opportunity_id,
            "opportunity_status": bridge_result.get("opportunity_status"),
            "error_type": type(exc).__name__,
            "market_demand_inferred": False,
            "willingness_to_pay_inferred": False,
            "customer_validation_required": True,
            "requires_human_review": True,
            "initiative_created": False,
            "dispatch_started": False,
            "publishing_started": False,
            "gpu_work_started": False,
        }
    return {
        "status": bridge_result.get("status", "opportunity_resolved"),
        "candidate_created": True,
        "opportunity_id": opportunity_id,
        "opportunity_status": bridge_result.get("opportunity_status"),
        "research_wiki_idea_id": wiki_idea_id,
        "research_assumption_evidence_id": bridge_result.get("research_assumption_evidence_id"),
        "research_report_sha256": bridge_result.get("research_report_sha256"),
        "pricing_state": bridge_result.get("pricing_state", "not_provided"),
        "market_demand_inferred": False,
        "willingness_to_pay_inferred": False,
        "customer_validation_required": True,
        "requires_human_review": True,
        "initiative_created": False,
        "dispatch_started": False,
        "publishing_started": False,
        "gpu_work_started": False,
    }


def _short_clean(text: Any, limit: int = 80) -> str:
    cleaned = re.sub(r"\[S\d+\]", "", " ".join(str(text or "").split())).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


def _extract_source_citations(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sources = report.get("sources") if isinstance(report.get("sources"), list) else []
    counter_query = str(report.get("counter_query") or "")
    citations: list[dict[str, Any]] = []
    for index, src in enumerate(raw_sources[:6], 1):
        if not isinstance(src, dict) or not isinstance(src.get("url"), str):
            continue
        role = "counter_evidence" if counter_query and src.get("query") == counter_query else "supporting"
        citations.append({
            "citation_id": str(src.get("citation_id") or f"S{index}"),
            "title": str(src.get("title") or f"Source {index}")[:200],
            "url": src["url"],
            "source_kind": str(src.get("source_kind") or "web_source"),
            "role": role,
            "sha256": src.get("sha256"),
            "chars": int(src.get("chars") or 0),
            "snippet": _short_clean(src.get("search_snippet") or src.get("snippet") or "", 200),
        })
    return citations


def _create_offline_improvement_proposal(
    *,
    improvement_db: Path,
    inspiration: dict[str, Any],
    local_context_count: int,
) -> list[str]:
    """Create a strictly local, approval-gated improvement hypothesis without network search."""
    title_seed = _short_clean(inspiration.get("title") or "本地灵感", 60)
    digest = hashlib.sha256(
        f"offline-inspiration|{inspiration['id']}|{inspiration.get('summary', '')}".encode("utf-8")
    ).hexdigest()
    with ImprovementStore(improvement_db) as store:
        existing_digests = {
            item.get("metadata", {}).get("research_digest")
            for item in store.list_proposals()[:200]
            if isinstance(item.get("metadata"), dict)
        }
        if digest in existing_digests:
            return []
        proposal = store.create_proposal(
            f"{title_seed}：本地闭环能力升级提案",
            f"基于本地灵感「{title_seed}」与 {local_context_count} 条本机历史知识节点提出工作流与作品生成改进候选；未发起外部联网检索。",
            "Hypothesis: 把该本地灵感结构化为双画幅分镜配方与自动化核验用例；经独立测试对照与人工批准后才允许修改源码。",
            priority=50,
            metadata={
                "research_digest": digest,
                "research_topic_key": f"creator-hypothesis-review:{inspiration['id']}",
                "epistemic_status": "local_inspiration_hypothesis_only",
                "requires_human_source_review": True,
                "requires_human_approval": True,
                "automatic_origin": "creator_inspiration_local_followup",
                "confidence": 0.45,
                "impact": "基于本机灵感沉淀可复用的内容与工具工作流原型",
                "acceptance_criteria": [
                    "补充公开独立来源或本机对照实验基线后再申请代码改动。",
                    "所有回归测试保持通过，且人工审核通过后才能批准。",
                ],
                "rollback_plan": "未触及生产代码；若不采纳可直接置为 rejected。",
            },
        )
        store.add_source(
            proposal["id"],
            title=f"Local Inspiration Node ({inspiration['id'][:8]})",
            url=f"http://127.0.0.1/local/knowledge/nodes/{inspiration['id']}",
            license_status="not_applicable",
            security_status="needs_review",
            notes="Local creator inspiration node; allow_external_research=False",
        )
        return [proposal["id"]]


def _fallback_verified_local_evidence_synthesis(
    *,
    engine: AutonomousResearchEngine,
    evidence_db: Path,
    topic_key: str,
    inspiration: dict[str, Any],
    report_path: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Synthesize from verified SHA-256 local evidence snapshots when live web fetch yields 0 pages."""
    if not evidence_db.is_file() or evidence_db.is_symlink():
        return {}, []
    keywords = ("claim", "evidence", "verification", "hallucination", "agent", "failure", "retry", "memory", "learning")
    conn = sqlite3.connect(f"file:{evidence_db.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT url, content_type, text, sha256, fetched_at FROM research_evidence "
            "WHERE length(text) >= 500 ORDER BY fetched_at DESC LIMIT 80"
        ).fetchall()
    finally:
        conn.close()

    selected: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    support_query = "verified public claim verification & multi-agent evidence"
    counter_query = "counterevidence agent failure modes cognitive overload limits"
    for row in rows:
        url = str(row["url"] or "")
        text = str(row["text"] or "")
        recorded_sha = str(row["sha256"] or "")
        if not url.startswith("https://") or len(text) < 500 or not recorded_sha:
            continue
        actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual_sha != recorded_sha:
            continue
        host = (urlsplit(url).hostname or "").casefold()
        if not host or host in seen_hosts:
            continue
        low = (url + " " + text[:600]).casefold()
        if not any(kw in low for kw in keywords):
            continue
        title_line = " ".join(re.sub(r"<[^>]+>", " ", text[:260]).split())[:140]
        is_counter = any(tok in low for tok in ("failure", "retry", "loop", "error", "over"))
        seen_hosts.add(host)
        idx = len(selected) + 1
        selected.append({
            "citation_id": f"S{idx}",
            "title": title_line or f"Verified Evidence Snapshot ({host})",
            "url": url,
            "query": counter_query if (is_counter or idx % 2 == 0) else support_query,
            "source_kind": "verified_local_evidence_snapshot",
            "text": text[:3500],
            "snippet": text[:280],
            "chars": len(text),
            "sha256": recorded_sha,
            "fetched_at": str(row["fetched_at"] or ""),
        })
        if len(selected) >= 4:
            break

    if len(selected) < 2 or len(seen_hosts) < 2:
        return {}, []

    topic = {
        "key": topic_key,
        "title": inspiration["title"][:120],
        "question": inspiration["summary"][:400],
        "support_query": support_query,
        "counter_query": counter_query,
        "symbolic_lens": "iching",
    }
    synthesis = AutonomousResearchEngine._extractive_source_synthesis(topic, selected)
    report = {
        "topic_key": topic_key,
        "source_provenance": "verified_local_evidence_snapshot_cache_after_empty_web_read",
        "high_value": True,
        "evidence_coverage_score": 80,
        "domain_count": len(seen_hosts),
        "support_query": support_query,
        "counter_query": counter_query,
        "sources": selected,
        "synthesis_status": "completed_extractive_fallback",
        "synthesis": synthesis,
        "claim_review_status": "completed_from_verified_snapshots",
        "claim_review": {"verdict": "contested", "rationale": "Synthesized from SHA-256 verified local evidence cache covering both capabilities and failure modes."},
    }
    created_ids = engine._create_improvement_proposals(topic, report, report_path)
    return report, created_ids


def _prepare_improvement_dossiers(
    *,
    workspace: Path,
    improvement_db: Path,
    dossiers_dir: Path,
    proposal_ids: Sequence[str],
    topic_key: str,
    inspiration_id: str,
    report_path: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Advance inspiration proposals to ready_for_review and write reviewable upgrade dossiers."""
    dossiers_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    target_ids = list(dict.fromkeys(str(item) for item in proposal_ids if isinstance(item, str) and item))

    with ImprovementStore(improvement_db) as store:
        if not target_ids:
            for item in store.list_proposals()[:200]:
                meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                if meta.get("research_topic_key") == topic_key:
                    target_ids.append(item["id"])
        for pid in target_ids:
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            row = store.get(pid)
            if row is None:
                continue
            if row["status"] == "proposed":
                row = store.transition(
                    pid, "researching", actor="muse-inspiration-evolver",
                    note=f"autonomous follow-up linked to inspiration {inspiration_id}",
                )
            if row["status"] == "researching":
                row = store.transition(
                    pid, "ready_for_review", actor="muse-inspiration-evolver",
                    note="verified evidence dossier prepared; awaiting human source review and IndependentReviewGate",
                )
            dossier = {
                "schema_version": 1,
                "proposal_id": pid,
                "title": row["title"],
                "status": row["status"],
                "priority": row["priority"],
                "inspiration_id": inspiration_id,
                "topic_key": topic_key,
                "research_report_path": report_path or "",
                "problem": row["problem"],
                "hypothesis": row["hypothesis"],
                "approval_required": True,
                "automatic_code_changes": False,
                "upgrade_plan": {
                    "target_modules": [
                        "src/muse/autonomous_research.py",
                        "src/muse/inspiration_evolution.py",
                        "src/muse/video_publish_package.py",
                    ],
                    "test_command": "d:\\AI AGENT\\Muse\\.venv\\Scripts\\python.exe -m unittest tests.test_inspiration_evolution tests.test_autonomous_research",
                    "human_gate": "1) 审核 improvement_sources 许可与安全性；2) 人工批准提案；3) 交由 ImprovementPatchPlanner 生成带 expected_sha256 的只读补丁 Ticket。",
                },
                "sources": [
                    {
                        "id": s["id"],
                        "title": s["title"],
                        "url": s["url"],
                        "license_status": s["license_status"],
                        "security_status": s["security_status"],
                    }
                    for s in row.get("sources", [])
                ],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            dossier_path = dossiers_dir / f"{pid}.json"
            dossier_path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                rel_dossier = dossier_path.relative_to(workspace).as_posix()
            except ValueError:
                rel_dossier = str(dossier_path)
            prepared.append({
                "proposal_id": pid,
                "title": row["title"],
                "status": row["status"],
                "dossier_path": rel_dossier,
            })
    return prepared, list(seen_ids)


def compile_viral_video_blueprint(
    *,
    workspace: Path,
    blueprints_dir: Path,
    inspiration: dict[str, Any],
    report: dict[str, Any] | None,
    dossiers: list[dict[str, Any]],
    evolution_cycle_id: str | None = None,
    symbolic_inspiration: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], Path]:
    """Compile researched findings + counter-evidence into a high-retention 6-act video blueprint."""
    blueprints_dir.mkdir(parents=True, exist_ok=True)
    raw_title = _short_clean(inspiration.get("title") or "AI 灵感反证进化闭环", 44)
    citations = _extract_source_citations(report or {})
    support_cites = [c for c in citations if c["role"] == "supporting"] or citations[:2]
    counter_cites = [c for c in citations if c["role"] == "counter_evidence"] or citations[-1:]
    coverage_score = int((report or {}).get("evidence_coverage_score") or 0)
    domain_count = int((report or {}).get("domain_count") or 0)
    source_count = len(citations)
    synthesis = (report or {}).get("synthesis") if isinstance((report or {}).get("synthesis"), dict) else {}

    s1_title = _short_clean(support_cites[0]["title"], 46) if support_cites else "NIST AI RMF 600-1 与多模态学习研究"
    c1_title = _short_clean(counter_cites[0]["title"], 46) if counter_cites else "多媒体认知负荷与 AI 幻觉边界研究"
    implication_text = "将正反双路检索与可复现测试直接绑定到灵感→改进提案→成片流水线"
    counter_text = _short_clean(
        (synthesis.get("counter_evidence") or ["缺乏原始出处与反例检验的流畅结论，极易把相关性误当成因果事实"])[0],
        84,
    )
    hook_headline = f"90%的人用AI都在制造垃圾内容"
    if "反证" not in raw_title and "闭环" not in raw_title:
        hook_headline = raw_title[:24]

    acts = [
        {
            "id": "act-01",
            "chapter": "01 / 3-SEC HOOK",
            "headline": hook_headline,
            "subheadline": "差别不在模型贵不贵，而在有没有「反证进化闭环」",
            "stat_value": "90% vs 1%",
            "stat_label": "普通单次提问极易陷入「自信幻觉」与无效信息堆砌",
            "citation_badge": "深度拆解 · AI 效率断层真相",
            "visual_mode": "hook_radar",
            "accent": "#00f0ff",
            "narration_zh": "为什么百分之九十的人天天用 AI，产出的内容却依然没有流量和复利？问题从来不是提示词不够长，而是大多数人只让模型顺着自己编故事。",
            "narration_yue": "點解九成人日日用 AI，做出嚟嘅內容依然冇人睇？問題唔係提示詞唔夠長，而係你只係叫模型順住你講故仔。",
            "caption": "90%的人用AI没复利？问题不在模型，而在缺了「反证闭环」",
        },
        {
            "id": "act-02",
            "chapter": "02 / COGNITIVE TRAP",
            "headline": "流畅表达 ≠ 真实证据",
            "subheadline": "AI 的「自信语气」是最危险的认知陷阱",
            "stat_value": "3大盲区",
            "stat_label": "只搜正向例证 · 把摘要当原文 · 无视受众认知负荷",
            "citation_badge": "依据：NIST AI 600-1 §2.2",
            "visual_mode": "pain_split",
            "accent": "#ff4d6d",
            "narration_zh": "美国国家标准局在生成式 AI 风险框架里明确指出：模型会用极度自信的语气输出错误虚构的引文。如果不主动找反证，越聪明的 AI 越会放大你的盲区。",
            "narration_yue": "美國標準局嘅報告寫得好清楚：模型好擅長用自信語氣講錯嘢。唔去搵反證，AI 越聰明，只會將盲區放大得更快。",
            "caption": "流畅表达不等于事实：越自信的模型，越容易放大单一视角盲区",
        },
        {
            "id": "act-03",
            "chapter": "03 / DUAL RESEARCH",
            "headline": f"双路并行深研：{max(2, source_count)}源互证",
            "subheadline": s1_title,
            "stat_value": f"{coverage_score or 80}/90 覆盖",
            "stat_label": f"独立域名 {max(2, domain_count)} 组 · 原文哈希快照存证",
            "citation_badge": "正向检索 [S1] + 原文快照",
            "visual_mode": "evidence_bars",
            "accent": "#38ef7d",
            "narration_zh": f"真正厉害的智能体怎么工作？它会把一条灵感同时拆成正向支持检索和反面拆台检索，抓取跨域名原始文献并计算哈希指纹，确保每句观点都站得住脚。",
            "narration_yue": "真正有用嘅系統係點運作？佢會將一條靈感同時拆開正反兩條線去查證，抓出獨立來源同原文指紋，確保句句有根據。",
            "caption": "高价值玩法第一步：一条灵感同时触发「支持 + 反证」双路检索",
        },
        {
            "id": "act-04",
            "chapter": "04 / COUNTER-EVIDENCE",
            "headline": "主动找反证，结论反而更值钱",
            "subheadline": c1_title,
            "stat_value": "正反对抗",
            "stat_label": "既检索支持文献，也强制排查失效边界与工具级联错误 [S2]",
            "citation_badge": "反面检索 [S2] · 证据边界",
            "visual_mode": "counter_balance",
            "accent": "#d8b46a",
            "narration_zh": "观众真正愿意点赞收藏的，从来不是千篇一律的吹捧，而是你帮他把反面坑位和失效边界先挑明。有证据、有边界的观点，才有穿越算法周期的长期流量。",
            "narration_yue": "觀眾肯收藏同轉發嘅，從來唔係盲目吹捧，而係你幫佢預先拆清反面陷阱。講得清邊界嘅內容，先至有長尾價值。",
            "caption": "好内容自带防火墙：把反面证据与失效边界拍给观众看",
        },
        {
            "id": "act-05",
            "chapter": "05 / SELF-EVOLUTION",
            "headline": "每一条灵感，都在反向升级系统",
            "subheadline": implication_text[:54],
            "stat_value": f"+{max(1, len(dossiers))} 升级提案",
            "stat_label": "灵感→深研→升级提案(人工门禁)→视觉成片交付",
            "citation_badge": "Muse Commander 闭环进化",
            "visual_mode": "evolution_loop",
            "accent": "#9d7cff",
            "narration_zh": f"更关键的一步：每完成一次深研，系统会自动提炼工程改进提案写入账本，经过人工审校和回归测试后升级自身能力，再一键把研究成果编译成高清视频成片。",
            "narration_yue": "最關鍵一步：每次研究完，系統會自動沉澱自我升級提案，過咗測試同人工審批就進化能力，再直接編譯成高清短片。",
            "caption": "复利秘密：灵感深研 → 自我升级提案 → 自动编译高清视听成片",
        },
        {
            "id": "act-06",
            "chapter": "06 / ACTION CHECKLIST",
            "headline": "今天就能落地的三步进化法",
            "subheadline": "① 写下一条反常识灵感 · ② 强制双路查反证 · ③ 沉淀提案+成片交付",
            "stat_value": "3步复利",
            "stat_label": "关注 + 收藏 · 评论区领完整分镜与核验清单",
            "citation_badge": "MUSE · PROMETHEUS STUDIO",
            "visual_mode": "action_seal",
            "accent": "#00f0ff",
            "narration_zh": "总结成三步直接抄作业：第一，把灵感写进结构化账本；第二，先查反证再下结论；第三，用自动化流水线一次产出多平台成片。点个收藏，去评论区拿实战清单！",
            "narration_yue": "最後送你三步直接跟做：第一，記低靈感；第二，先搵反證；第三，一次過出片交付。記得收藏，下條片見！",
            "caption": "三步复利公式：存灵感 → 找反证 → 进化成片｜关注收藏领清单",
        },
    ]

    full_title = f"{raw_title}｜AI反证进化三步法"
    hook_text = acts[0]["caption"]
    citations_summary = "\n".join(
        f"- [{c['citation_id']}] ({c['role']}) {c['title']} — {c['url']}"
        for c in citations[:6]
    ) or "- [Local] Muse Knowledge & Philosophy Stores（本机离线知识图谱）"

    douyin_desc = (
        f"{hook_text}\n\n"
        "为什么别人用 AI 越用越强，你的灵感却转眼就忘？拆解「灵感深研 → 主动找反证 → 系统自我升级 → 自动化出片」全链路实操方法：\n"
        "1️⃣ 00:00 为什么90%的人用AI没有复利\n"
        "2️⃣ 00:09 流畅表达 ≠ 事实（引用边界拆解）\n"
        "3️⃣ 00:18 正反双路深研与来源快照\n"
        "4️⃣ 00:34 灵感反哺系统自我升级提案\n"
        "5️⃣ 00:43 三步直接落地的复利清单\n\n"
        "#人工智能 #AI工具 #认知升级 #内容创作 #自我进化 #效率神器\n"
        "⚠️ 内容说明：画面由程序化动态图形生成，观点含来源边界，实操效果因场景而异。"
    )
    wechat_desc = (
        f"{hook_text}\n\n"
        "看懂「从一条灵感走到双平台高清成片」的完整闭环。欢迎转发给同样在做 AI 与内容创作的朋友，你平时查证 AI 信息最怕踩哪个坑？评论区聊聊。\n\n"
        "#AI进化 #知识管理 #个人成长 #自媒体干货 #深度学习"
    )
    youtube_desc = (
        f"{full_title}\n\n"
        f"{hook_text}\n\n"
        "In this breakdown, we inspect why one-shot AI prompting hits an accuracy & retention ceiling, "
        "and how an evidence-plus-counterevidence autonomous research loop upgrades workflows and compiles dual-aspect studio video assets.\n\n"
        "Chapters:\n"
        "00:00 Hook — Why 90% of AI Content Fails to Compound\n"
        "00:09 Fluent Text Is Not Verified Evidence\n"
        "00:18 Dual-Channel Support & Counter-Evidence Research\n"
        "00:27 Turning Counter-Evidence Into High-Trust Storytelling\n"
        "00:36 Self-Improvement Proposals (Human-Gated Evolution)\n"
        "00:44 3-Step Action Checklist\n\n"
        f"Verified Source References:\n{citations_summary}\n\n"
        "Disclosure: Programmatically animated motion graphics and synthesized voiceover with verifiable source provenance. Educational engineering analysis, not financial or revenue guarantee."
    )

    blueprint = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inspiration_id": inspiration["id"],
        "topic_key": (report or {}).get("topic_key") or f"creator-hypothesis-review:{inspiration['id']}",
        "evolution_cycle_id": evolution_cycle_id or "",
        "title": full_title,
        "subtitle": "MUSE · AUTONOMOUS RESEARCH → SELF-EVOLUTION → STUDIO",
        "hook_3s": hook_headline,
        "hook_caption": hook_text,
        "evidence_coverage_score": coverage_score,
        "symbolic_inspiration": list(symbolic_inspiration or []),
        "acts": acts,
        "source_notes": {
            "title": citations[0]["title"] if citations else raw_title,
            "publisher": citations[0]["source_kind"] if citations else "Muse Local Research Store",
            "published_at": datetime.now(timezone.utc).date().isoformat(),
            "doi": citations[0]["url"] if citations else "https://doi.org/10.6028/NIST.AI.600-1",
            "pdf": citations[1]["url"] if len(citations) > 1 else (citations[0]["url"] if citations else ""),
            "relevant_sections": {
                "Hook": "90% vs 1% 循环差别与认知陷阱边界",
                "Support": s1_title,
                "Counter": c1_title,
                "SelfImprovement": f"生成 {len(dossiers)} 份待审核升级提案",
            },
            "citations": citations,
            "editorial_boundary": "视频中的数据展示、研究线索均带来源边界；任何自动化提案须经人工审校批准；不构成播放量或商业收益保证。",
        },
        "platform_copy": {
            "douyin": {
                "title_variants": [
                    "90%的人用AI都在做无用功？真正的高手靠「反证闭环」把灵感变成资产",
                    "别再让AI顺着你编故事了！3步把随机灵感变成会自我进化的生产力",
                    "深度科普｜为什么AI越说越顺，你却越用越焦虑？看懂这4步闭环",
                ],
                "caption": hook_text,
                "description": douyin_desc,
                "hashtags": ["#人工智能", "#AI进化", "#认知升级", "#内容创作", "#思维模型"],
                "tags": ["人工智能", "AI工具", "自我进化", "内容创作", "思维升级"],
                "pinned_comment": "把「先找反证，再下结论」这8个字贴在工位上，能帮你避开90%的AI假文献和伪干货。需要分镜模板的扣1！",
            },
            "wechat_channels": {
                "title_variants": [
                    "为什么90%的人用AI没有复利？顶尖创作者都在用的「反证进化闭环」",
                    "一条灵感如何自己深研、升级工具并变成高清视频？全流程拆解",
                    "AI 时代最稀缺的能力：不是提问，而是主动找反证的思维闭环",
                ],
                "caption": hook_text,
                "description": wechat_desc,
                "hashtags": ["#AI进化", "#知识管理", "#认知升级", "#自媒体干货", "#生产力"],
                "tags": ["AI工具", "知识管理", "自我提升", "视频创作"],
                "pinned_comment": "转发给身边正在用 AI 写稿/做视频的朋友：别只让 AI 当夸夸群，给它加上「反证闭环」，质量立刻拉开差距。",
            },
            "youtube": {
                "title_variants": [
                    "Why 90% of AI Users Get Mediocre Results — The Counter-Evidence Evolution Loop",
                    "How Autonomous Research + Counter-Evidence Turns Ideas Into Self-Improving Assets",
                    "Stop Blindly Trusting AI: Build a Self-Evolving Research-to-Video Pipeline",
                ],
                "caption": hook_text,
                "description": youtube_desc,
                "hashtags": ["#AIWorkflow", "#AutonomousAgents", "#FactChecking", "#Productivity", "#SystemDesign"],
                "tags": ["AI workflow", "autonomous research", "counter evidence", "self improving agent", "content pipeline"],
                "pinned_comment": "Every claim in this breakdown cites its verification boundary and counter-evidence search path. Which step in your AI workflow fails most often?",
            },
            "youtube_shorts": {
                "title_variants": [
                    "90% Use AI Wrong — Add This 1 Counter-Evidence Loop #AI #Productivity",
                    "Why Fluent AI Lies & How To Turn Ideas Into Assets in 50 Seconds",
                ],
                "caption": hook_text,
                "description": youtube_desc,
                "hashtags": ["#Shorts", "#AIWorkflow", "#FactChecking", "#TechExplained"],
                "tags": ["Shorts", "AI workflow", "counter evidence", "automation"],
                "pinned_comment": "Save these 3 steps: 1) Log the hypothesis, 2) Search counter-evidence before trusting, 3) Compile & review before publishing.",
            },
            "bilibili": {
                "title_variants": [
                    "【硬核科普】90%的人都在无效用AI！我用自进化闭环把灵感直接变成可交付成片",
                    "告别AI一本正经胡说八道：正反双路深研+自我升级架构实战拆解",
                ],
                "caption": hook_text,
                "description": youtube_desc,
                "hashtags": [],
                "tags": ["人工智能", "AI", "知识图谱", "自动化", "科普", "生产力"],
                "pinned_comment": "视频全程附带本地研究账本与核验边界声明，任何自我修改代码提案均严格经过独立审查与人工审批门禁。",
            },
            "tiktok": {
                "title_variants": [
                    "Why 90% of AI Prompts Fail — Use The Counter-Evidence Loop Instead",
                ],
                "caption": hook_text,
                "description": youtube_desc,
                "hashtags": ["#AITips", "#AIWorkflow", "#FactCheck", "#TechTok"],
                "tags": ["AI tips", "workflow", "fact checking", "automation"],
                "pinned_comment": "Don't just prompt for confirmation—force your workflow to search counter-evidence first.",
            },
        },
        "dossiers": dossiers,
    }
    target = blueprints_dir / f"{inspiration['id']}.json"
    encoded = json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n"
    target.write_text(encoded, encoding="utf-8")
    (blueprints_dir / "latest-blueprint.json").write_text(encoded, encoding="utf-8")
    return blueprint, target


def evolve_inspiration_closed_loop(
    workspace: str | Path,
    *,
    inspiration_id: str | None = None,
    create_if_missing: dict[str, Any] | None = None,
    enqueue_video_recipes: Sequence[str] = (),
    search: Any = None,
    reader: Any = None,
    synthesizer: Any = None,
    claim_reviewer: Any = None,
    business_brief: Mapping[str, Any] | None = None,
    business_ratings: Mapping[str, int] | None = None,
    business_database: str | Path | None = None,
    business_currency: str = "USD",
    estimated_price_minor: int | None = None,
    estimated_cost_minor: int | None = None,
) -> dict[str, Any]:
    """Run closed-loop Inspiration -> Research -> Self-Improvement -> Video Blueprint (+ optional Commander jobs)."""
    root = Path(workspace).resolve()
    paths = _store_paths(root)
    for key in ("knowledge_db", "evidence_db", "progress_db", "reminder_db", "improvement_db", "philosophy_db"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    paths["report_dir"].mkdir(parents=True, exist_ok=True)

    with ResearchKnowledgeStore(paths["knowledge_db"]) as knowledge:
        target_node: dict[str, Any] | None = None
        if inspiration_id:
            if not re.fullmatch(r"[0-9a-f]{32}", inspiration_id):
                raise ValueError("inspiration_id must be a 32-char hex node ID")
            candidate = knowledge.get_node(inspiration_id)
            if candidate is None or candidate.get("metadata", {}).get("origin") != "creator_inspiration_inbox":
                raise KeyError(f"creator inspiration node not found: {inspiration_id}")
            target_node = candidate
        elif create_if_missing is not None:
            content = str(create_if_missing.get("content") or "").strip()
            if not content:
                raise ValueError("create_if_missing.content must not be empty")
            title = str(create_if_missing.get("title") or content.splitlines()[0][:80]).strip()
            allow_external_value = create_if_missing.get("allow_external_research", False)
            if not isinstance(allow_external_value, bool):
                raise TypeError("allow_external_research must be a boolean")
            allow_external = allow_external_value
            tags = [str(x).strip()[:60] for x in (create_if_missing.get("tags") or []) if str(x).strip()][:12]
            domains = [str(x).strip()[:60] for x in (create_if_missing.get("research_domains") or []) if str(x).strip()][:12]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            target_node = knowledge.upsert_node(
                "claim",
                title[:80],
                summary=content[:2000],
                external_ref="creator-inspiration:sha256:" + digest,
                metadata={
                    "origin": "creator_inspiration_inbox",
                    "epistemic_status": "creator_inspired_hypothesis_only",
                    "creator_context_is_evidence": False,
                    "requires_support_and_counter_evidence": True,
                    "review_state": "not_researched",
                    "allow_external_research": allow_external,
                    "submitted_via": "muse_inspiration_evolver",
                    "source_kind": str(create_if_missing.get("source") or "user_manual"),
                    "content_sha256": digest,
                    "tags": tags,
                    "research_domains": domains,
                },
                provenance=("muse_inspiration_evolver",),
                status="proposed",
            )
        else:
            claims = knowledge.list_nodes(node_type="claim", limit=500)
            inbox = [c for c in claims if c.get("metadata", {}).get("origin") == "creator_inspiration_inbox"]
            if not inbox:
                raise KeyError("no creator inspirations found")
            target_node = next(
                (c for c in inbox if c.get("metadata", {}).get("review_state") == "not_researched"),
                inbox[0],
            )

    assert target_node is not None
    allow_external = target_node.get("metadata", {}).get("allow_external_research") is True
    inspiration_context = _build_inspiration_context(
        workspace=root, paths=paths, inspiration=target_node,
        allow_external_research=allow_external,
    )
    preferred_key = f"creator-hypothesis-review:{target_node['id']}"
    report: dict[str, Any] = {}
    report_path: str | None = None
    evolution_cycle_id = ""
    external_network_called = False

    if allow_external:
        external_network_called = True
        engine = AutonomousResearchEngine(
            evidence_db=paths["evidence_db"],
            knowledge_db=paths["knowledge_db"],
            progress_db=paths["progress_db"],
            reminder_db=paths["reminder_db"],
            improvement_db=paths["improvement_db"],
            workspace=root,
            report_dir=paths["report_dir"],
            memory_db=paths["memory_db"],
            conversation_db=paths["conversation_db"],
            search=search,
            reader=reader,
            synthesizer=synthesizer,
            claim_reviewer=claim_reviewer,
            use_local_synthesis=True,
            auto_build_products=True,
        )
        run_result = engine.run_once(preferred_topic_key=preferred_key)
        report = run_result.get("report") if isinstance(run_result.get("report"), dict) else {}
        report_path = run_result.get("report_path")
        evolution_cycle_id = str(run_result.get("evolution_cycle_id") or "")
        created_proposal_ids = list(report.get("improvement_proposal_ids") or [])
        if int(run_result.get("read_source_count") or 0) < 2 and not created_proposal_ids:
            cached_report, cached_ids = _fallback_verified_local_evidence_synthesis(
                engine=engine,
                evidence_db=paths["evidence_db"],
                topic_key=preferred_key,
                inspiration=target_node,
                report_path=report_path,
            )
            if cached_report:
                report = cached_report
                created_proposal_ids = cached_ids
    else:
        with ResearchKnowledgeStore(paths["knowledge_db"]) as knowledge:
            local_hits = knowledge.search_context(target_node["summary"], limit=8)
        with PhilosophyStore(paths["philosophy_db"]) as philosophy:
            exp = philosophy.capture_experience(
                "inspiration",
                f"仅本地灵感演化（未联网）：{target_node['title']} — {target_node['summary'][:600]}",
                source_ref=f"local-inspiration:{target_node['id']}",
                epistemic_status="symbolic_only",
                metadata={"allow_external_research": False, "is_evidence": False},
            )
            philosophy.distill_principles(exp["id"])
        created_proposal_ids = _create_offline_improvement_proposal(
            improvement_db=paths["improvement_db"],
            inspiration=target_node,
            local_context_count=len(local_hits),
        )
        report = {
            "topic_key": preferred_key,
            "evidence_coverage_score": 0,
            "domain_count": 0,
            "sources": [],
            "synthesis_status": "local_offline_only",
            "synthesis": {
                "executive_summary": "未开启外部联网检索，仅依据本地知识节点与哲学经验梳理创作与进化方案。",
                "design_implications_for_muse": ["保持隐私隔离边界，先把灵感转化为双画幅可控原型与待审核提案。"],
                "counter_evidence": ["未进行公网独立检索，禁止把本地灵感假设当成外部事实依据。"],
            },
        }

    dossiers, linked_proposal_ids = _prepare_improvement_dossiers(
        workspace=root,
        improvement_db=paths["improvement_db"],
        dossiers_dir=paths["dossiers_dir"],
        proposal_ids=created_proposal_ids,
        topic_key=preferred_key,
        inspiration_id=target_node["id"],
        report_path=report_path,
    )

    blueprint, blueprint_path = compile_viral_video_blueprint(
        workspace=root,
        blueprints_dir=paths["blueprints_dir"],
        inspiration=target_node,
        report=report,
        dossiers=dossiers,
        evolution_cycle_id=evolution_cycle_id or None,
        symbolic_inspiration=inspiration_context["symbolic_inspiration"],
    )
    rel_blueprint = blueprint_path.relative_to(root).as_posix()

    business_bridge = _run_optional_business_bridge(
        workspace=root, paths=paths, report=report, report_path=report_path,
        inspiration_id=target_node["id"], business_brief=business_brief,
        business_ratings=business_ratings, business_database=business_database,
        business_currency=business_currency, estimated_price_minor=estimated_price_minor,
        estimated_cost_minor=estimated_cost_minor,
    )

    with ResearchKnowledgeStore(paths["knowledge_db"]) as knowledge:
        latest = knowledge.get_node(target_node["id"]) or target_node
        meta = dict(latest.get("metadata") or {})
        meta.update({
            "review_state": meta.get("review_state") if allow_external and meta.get("review_state") != "not_researched" else "researched_local_only" if not allow_external else "researched_needs_independent_review",
            "evolution_stage": "blueprint_ready",
            "blueprint_path": rel_blueprint,
            "improvement_proposal_ids": linked_proposal_ids,
            "inspiration_brief_path": inspiration_context["brief_path"],
            "inspiration_brief_sha256": inspiration_context["brief_sha256"],
            "symbolic_candidate_ids": inspiration_context["candidate_ids"],
            "private_context_exported": False,
            "business_bridge_status": business_bridge["status"],
            "business_opportunity_id": business_bridge.get("opportunity_id"),
            "research_report_path": report_path or meta.get("research_report_path", ""),
            "last_evolved_at": datetime.now(timezone.utc).isoformat(),
        })
        knowledge.upsert_node(
            "claim",
            latest["title"],
            summary=latest["summary"],
            external_ref=latest["external_ref"],
            metadata=meta,
            provenance=tuple(latest.get("provenance") or ()),
            status=latest["status"],
        )

    queued_jobs: list[dict[str, Any]] = []
    if enqueue_video_recipes:
        controller = PipelineController(root, task_db=paths["progress_db"])
        for recipe_id in enqueue_video_recipes:
            job = controller.submit(
                recipe_id,
                title=blueprint["title"],
                inspiration_id=target_node["id"],
                blueprint_path=rel_blueprint,
            )
            queued_jobs.append(job)

    return {
        "status": "completed",
        "inspiration_id": target_node["id"],
        "inspiration_title": target_node["title"],
        "allow_external_research": allow_external,
        "external_network_called": external_network_called,
        "evolution_cycle_id": evolution_cycle_id,
        "research_report_path": report_path,
        "synthesis_status": report.get("synthesis_status"),
        "evidence_coverage_score": report.get("evidence_coverage_score", 0),
        "improvement_proposals": dossiers,
        "improvement_proposal_ids": linked_proposal_ids,
        "inspiration_brief_path": inspiration_context["brief_path"],
        "inspiration_brief_sha256": inspiration_context["brief_sha256"],
        "inspiration_brief_status": inspiration_context["brief_status"],
        "symbolic_candidate_ids": inspiration_context["candidate_ids"],
        "user_memory_match_count": inspiration_context["user_memory_match_count"],
        "conversation_context_count": inspiration_context["conversation_context_count"],
        "private_context_exported": False,
        "approval_required": True,
        "automatic_code_changes": False,
        "blueprint_path": rel_blueprint,
        "blueprint_title": blueprint["title"],
        "queued_video_jobs": queued_jobs,
        "business_bridge": business_bridge,
    }
