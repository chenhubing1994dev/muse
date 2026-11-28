"""Muse expert fleet — real registry entries, permission boundaries and executor bindings.

Each specialist has: capability declaration, an explicit tool-permission allow-list, a risk
ceiling (anything above it must go through the Commander approval gate), and an executor
binding (``module:attribute``) that is health-checked *statically* via ``importlib.util``
(no import side effects). Profiles are installed into the existing SpecialistRegistry and
AgentRegistry so routing and historical effectiveness/cost stats use the stores of record.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExpertSpec:
    expert_id: str
    name_zh: str
    role: str
    capabilities: tuple[str, ...]
    tool_permissions: tuple[str, ...]
    risk_ceiling: str  # low | medium — high is always human-gated
    executor: str      # "module:attribute" of the real implementation
    keywords: tuple[str, ...] = field(default_factory=tuple)


FLEET: tuple[ExpertSpec, ...] = (
    ExpertSpec("researcher", "研究员", "research", ("search", "read_source", "dossier"), ("web_read", "knowledge_write"), "low", "muse.research_pipeline:ResearchPipeline", ("研究", "research", "资料")),
    ExpertSpec("counter_researcher", "反证研究员", "research", ("counter_search", "alternative_explanations"), ("web_read", "knowledge_write"), "low", "muse.research_closed_loop:ResearchClosedLoop", ("反证", "反驳", "counter")),
    ExpertSpec("architect", "架构师", "architecture", ("plan", "topology", "contracts"), ("repo_read",), "low", "muse.commander_planner:CommanderPlanner", ("架构", "设计方案", "architecture")),
    ExpertSpec("programmer", "程序员", "developer", ("patch_draft", "code_generation"), ("repo_read", "sandbox_write"), "medium", "muse.improvement_patch_planner:ImprovementPatchPlanner", ("代码", "实现", "code")),
    ExpertSpec("test_engineer", "测试工程师", "reviewer", ("unit_tests", "regression"), ("repo_read", "sandbox_exec"), "low", "muse.test_runner:run_suite", ("测试", "test")),
    ExpertSpec("security_auditor", "安全审计员", "reviewer", ("threat_review", "secret_scan", "url_policy"), ("repo_read",), "low", "muse.security_guards:scan_untrusted_text", ("安全", "审计", "security")),
    ExpertSpec("designer", "设计师", "writer", ("ui_design", "landing_page"), ("sandbox_write",), "low", "muse.prototype_builder:StaticPrototypeBuilder", ("设计", "界面", "ui")),
    ExpertSpec("video_director", "视频导演", "writer", ("storyboard", "shot_list"), ("sandbox_write",), "low", "muse.video_workflow:validate_storyboard", ("分镜", "导演", "storyboard")),
    ExpertSpec("video_editor", "剪辑师", "operator", ("cut", "join", "fit"), ("media_exec",), "medium", "muse.ffmpeg_skill:plan_cut", ("剪辑", "合成", "edit")),
    ExpertSpec("voice_artist", "配音师", "operator", ("tts", "narration"), ("media_exec",), "medium", "muse.piper_tts:synthesize_to_wav", ("配音", "旁白", "tts")),
    ExpertSpec("market_analyst", "市场分析师", "analyst", ("opportunity_scoring", "demand_signals"), ("knowledge_read",), "low", "muse.business_opportunities:score_opportunity", ("市场", "机会", "market")),
    ExpertSpec("operator", "运营者", "operator", ("publish_bundle", "campaign_tracking"), ("sandbox_write",), "medium", "muse.social_campaign_tracking:build_social_tracking_manifest", ("运营", "发布", "campaign")),
    ExpertSpec("finance_analyst", "财务分析师", "analyst", ("unit_economics", "net_revenue"), ("ledger_read",), "low", "muse.unit_economics:simulate", ("成本", "收益", "财务")),
    ExpertSpec("product_manager", "产品经理", "planner", ("prd", "prioritisation"), ("knowledge_read",), "low", "muse.initiative_engine:InitiativeStore", ("产品", "需求", "prd")),
    ExpertSpec("teacher", "教师", "writer", ("explain", "user_guide"), ("docs_write",), "low", "muse.kinetic_explainer:build", ("教学", "讲解", "teach")),
)


def _executor_present(executor: str) -> tuple[bool, str]:
    module, _, attribute = executor.partition(":")
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError) as exc:
        return False, f"find_spec failed: {type(exc).__name__}: {exc}"
    if spec is None or not spec.origin:
        return False, "module not found"
    try:
        source = Path(spec.origin).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"unreadable: {exc}"
    marker_def, marker_class = f"def {attribute}(", f"class {attribute}"
    if marker_def in source or marker_class in source:
        return True, spec.origin
    return False, f"{attribute} not defined in {module}"


def fleet_health() -> list[dict[str, Any]]:
    rows = []
    for spec in FLEET:
        ok, detail = _executor_present(spec.executor)
        rows.append({"expert_id": spec.expert_id, "name_zh": spec.name_zh, "executor": spec.executor,
                     "executor_bound": ok, "detail": detail, "risk_ceiling": spec.risk_ceiling,
                     "tool_permissions": list(spec.tool_permissions)})
    return rows


def permission_allows(expert_id: str, permission: str) -> bool:
    for spec in FLEET:
        if spec.expert_id == expert_id:
            return permission in spec.tool_permissions
    return False


def install_fleet(*, specialist_db: str | Path, agent_db: str | Path) -> dict[str, Any]:
    """Register every expert into the SpecialistRegistry and AgentRegistry (idempotent)."""
    from muse.agent_registry import AgentRegistry
    from muse.specialist_router import SpecialistProfile, SpecialistRegistry

    registered = []
    specialists = SpecialistRegistry(Path(specialist_db).resolve())
    agents = AgentRegistry(Path(agent_db).resolve())
    try:
        for index, spec in enumerate(FLEET):
            specialists.register(SpecialistProfile(
                specialist_id=spec.expert_id, role=spec.role, domains=(spec.role,),
                keywords=spec.keywords, capabilities=spec.capabilities,
                description=f"{spec.name_zh} · executor={spec.executor} · risk_ceiling={spec.risk_ceiling}",
                priority=100 - index))
            agents.register(spec.expert_id, role=spec.role, capabilities=list(spec.capabilities),
                            display_name=spec.name_zh, source="muse.expert_fleet")
            registered.append(spec.expert_id)
        listed = {item.specialist_id if hasattr(item, "specialist_id") else item.get("specialist_id")
                  for item in specialists.list_all()}
    finally:
        specialists.close()
        agents.close()
    return {"registered": registered, "listed_count": len(listed & set(registered))}
