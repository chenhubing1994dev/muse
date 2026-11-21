"""Evidence-aware value discovery patterns and bridges for Muse."""
from __future__ import annotations

import argparse
import copy
import json
from typing import Any, Mapping, Sequence

SOURCE_REPOSITORY = "https://github.com/jaxxchen003/muse-use-cases"

# Abstract workflow patterns independently phrased by Muse; no case text is copied.
_PATTERN_CATALOG: dict[str, dict[str, Any]] = {
    "passive_watchtower": {
        "title": "被动监测与主动提醒", "category": "automation",
        "problem": "重要价格、续费、网页、内容或业务状态变化容易被忽略。",
        "first_step": "选定一个只读数据源、监测频率、变化阈值和通知渠道，先做影子运行。",
        "expected_output": "带来源、时间戳、变化摘要和误报反馈的监测报告。",
        "creator_value": "减少错过重要变化的风险与重复检查时间。",
        "hypothesis": "监测用户明确订阅的来源，只在出现重要变化时提醒并附带差异。",
        "initiative_kind": "workflow", "capability_gap": "增量监测、去重、阈值判断和主动提醒。",
        "guardrails": ["默认只读", "不自动购买、续费、取消或外发消息", "遵守来源规则并限制频率"],
    },
    "personal_context_to_action": {
        "title": "个人资料转化为可执行行动", "category": "productivity",
        "problem": "邮件、收据、文件、收藏和笔记中的信息没有转化为下一步行动。",
        "first_step": "从用户主动选择的资料夹或样本文件开始，建立字段白名单和人工核对流程。",
        "expected_output": "可追溯的行动清单与可编辑草稿，不自动提交或发送。",
        "creator_value": "减少信息整理时间，让已有资料产生实际用途。",
        "hypothesis": "在授权范围内提取最少必要字段，形成待办、日程、摘要或草稿。",
        "initiative_kind": "tool", "capability_gap": "隐私最小化提取、来源追踪和结构化行动生成。",
        "guardrails": ["只处理明确授权的资料", "敏感字段最小化", "外发、付款、提交前必须人工确认"],
    },
    "paperwork_and_negotiation": {
        "title": "账单审查与办事谈判助手", "category": "consumer_value",
        "problem": "账单、合同条款、保险理赔或服务方案复杂，难以快速发现差异。",
        "first_step": "选一份用户提供的非高风险样本，建立字段核对表和引用定位。",
        "expected_output": "逐项差异清单、证据出处和可编辑沟通草稿。",
        "creator_value": "节省审查时间并帮助用户提出有依据的问题。",
        "hypothesis": "对用户提供的文件做条款对照、异常标注和证据整理。",
        "initiative_kind": "workflow", "capability_gap": "文档结构化、条款对照和来源定位。",
        "guardrails": ["不保证节省金额或理赔结果", "不提供法律结论", "不得擅自联系机构或签署文件"],
    },
    "workflow_to_microapp": {
        "title": "重复流程变成轻量工具", "category": "software",
        "problem": "重复的多步骤工作依赖手工复制、核对和跨工具切换。",
        "first_step": "记录一次完整流程，识别输入、输出、失败分支和人工决策点。",
        "expected_output": "一个可测试的本地原型及其操作说明。",
        "creator_value": "把重复劳动转化为可复用的生产力能力。",
        "hypothesis": "把稳定流程抽象成小型本地工具或仪表板，保留人工检查点。",
        "initiative_kind": "tool", "capability_gap": "流程建模、表单化输入、验证和审计日志。",
        "guardrails": ["先做本地原型", "不绕过现有权限和审批", "不新增重复调度器或数据账本"],
    },
    "research_to_video": {
        "title": "研究洞察转为高质量视频", "category": "content",
        "problem": "研究成果停留在长文或资料堆中，没有形成可传播、可评估的作品。",
        "first_step": "选一个来源充分且适合视觉解释的主题，先制作带引用的短视频样片。",
        "expected_output": "脚本、粤语旁白、视频预览、质量检查和表现指标方案。",
        "creator_value": "让研究成果成为可观看、可分享和可迭代的内容资产。",
        "hypothesis": "将已审查的主张、反证和不确定性转成脚本、旁白、分镜和视频 QA。",
        "initiative_kind": "creative_output", "capability_gap": "研究证据到脚本、旁白、画面和视频 QA 的端到端衔接。",
        "guardrails": ["不编造事实、数据或见证", "保留反证与不确定性", "发布必须经过人工审核"],
    },
    "demand_to_business_pilot": {
        "title": "真实需求转为小规模商业试点", "category": "business",
        "problem": "创意看似有价值，却缺少真实需求、付费意愿和交付成本验证。",
        "first_step": "写清目标客户、问题、最小报价假设、验证实验和成功信号。",
        "expected_output": "一份可审核的试点方案和证据记录，不把估算当成收入预测。",
        "creator_value": "减少盲目开发，把时间投入到有人真实需要的产品或服务。",
        "hypothesis": "从明确人群和痛点出发，设计低成本验证实验，记录证据后再决定是否产品化。",
        "initiative_kind": "product", "capability_gap": "客户需求验证、付费意愿测试和交付成本核算。",
        "guardrails": ["不伪造客户反馈或收入", "价格和成本标注为估算", "交易和对外承诺须人工批准"],
    },
    "capability_gap_to_experiment": {
        "title": "能力缺口转为研究与实验", "category": "research",
        "problem": "反复失败或无法交付的任务暴露出能力缺口，但没有转成可验证改进。",
        "first_step": "选一个重复出现的能力缺口，建立最小复现、基线指标和验收测试。",
        "expected_output": "有证据链、反例和回归测试的能力改进提案。",
        "creator_value": "让失败和研究积累变成下一轮可验证的能力提升。",
        "hypothesis": "从脱敏失败类别、用户反馈和研究缺口中提出假设并设计回归实验。",
        "initiative_kind": "research", "capability_gap": "从信号聚类、根因假设到实验和回归验证的闭环。",
        "guardrails": ["只使用获准的脱敏信号", "相似度不等于事实", "升级需审查和测试"],
    },
}

DIMENSIONS = ("impact", "recurrence", "capability_fit", "evidence_quality", "feasibility", "risk")
WEIGHTS = {"impact": 25, "recurrence": 15, "capability_fit": 20, "evidence_quality": 15, "feasibility": 25}


def score_value_opportunity(ratings: Mapping[str, int]) -> dict[str, Any]:
    """Return an explainable heuristic score, never a probability or revenue forecast."""
    if not isinstance(ratings, Mapping) or set(ratings) != set(DIMENSIONS):
        raise ValueError(f"ratings must contain exactly: {', '.join(DIMENSIONS)}")
    normalized: dict[str, int] = {}
    for key in DIMENSIONS:
        value = ratings[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError(f"{key} must be an integer from 1 to 5")
        normalized[key] = value
    base = sum(normalized[key] / 5 * weight for key, weight in WEIGHTS.items())
    penalty = (normalized["risk"] - 1) / 4 * 15
    score = round(max(0.0, min(100.0, base - penalty)), 2)
    band = "exceptional" if score >= 85 else "high" if score >= 70 else "promising" if score >= 55 else "exploratory"
    return {
        "score": score, "band": band, "ratings": normalized,
        "weighted_base": round(base, 2), "risk_penalty": round(penalty, 2),
        "evidence_state": "hypothesis_only", "requires_validation": True,
    }


def _validate_refs(values: Sequence[str], name: str) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > 12:
        raise ValueError(f"{name} must be a list or tuple containing at most 12 references")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise ValueError(f"{name} entries must be non-empty text of at most 2048 characters")
        if any(ord(char) < 32 for char in value):
            raise ValueError(f"{name} entries cannot contain control characters")
        result.append(value.strip())
    return list(dict.fromkeys(result))


class ValueForge:
    """Catalog, score and bridge value hypotheses into Muse's existing ledgers."""

    def list_patterns(self) -> list[dict[str, Any]]:
        return [
            {"id": key, "title": item["title"], "category": item["category"],
             "initiative_kind": item["initiative_kind"], "creator_value": item["creator_value"]}
            for key, item in _PATTERN_CATALOG.items()
        ]

    def get_pattern(self, pattern_id: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(_PATTERN_CATALOG[pattern_id])
        except (KeyError, TypeError) as exc:
            raise KeyError(f"unknown value pattern: {pattern_id}") from exc

    def evaluate(self, pattern_id: str, ratings: Mapping[str, int], *, evidence_refs: Sequence[str] = ()) -> dict[str, Any]:
        pattern = self.get_pattern(pattern_id)
        refs = _validate_refs(evidence_refs, "evidence_refs")
        return {
            "pattern_id": pattern_id, "title": pattern["title"], "category": pattern["category"],
            "evaluation": score_value_opportunity(ratings), "evidence_refs": refs,
            "evidence_note": "References are traceability pointers only; they are not independently verified evidence.",
            "source_refs": [SOURCE_REPOSITORY],
        }

    def evaluate_research_report(self, report: Mapping[str, Any], *, report_path: str = "") -> dict[str, Any]:
        """Rank research-to-value hypotheses using only fields already present in a report.

        A single research cycle is never treated as proof of recurrence or market demand.
        Scores remain heuristic and every result stays hypothesis_only.
        """
        if not isinstance(report, Mapping):
            raise TypeError("report must be a mapping")
        synthesis = report.get("synthesis")
        synthesis = synthesis if isinstance(synthesis, Mapping) else {}
        sources = report.get("sources")
        sources = sources if isinstance(sources, list) else []
        experiments = synthesis.get("testable_experiments")
        experiments = experiments if isinstance(experiments, list) else []
        counter_evidence = synthesis.get("counter_evidence")
        counter_evidence = counter_evidence if isinstance(counter_evidence, list) else []
        coverage = report.get("evidence_coverage_score", 0)
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
            coverage = 0
        coverage_value = max(0.0, min(90.0, float(coverage)))
        coverage_rating = max(1, min(5, round(coverage_value / 18)))
        source_urls: list[str] = []
        for item in sources[:50]:
            if isinstance(item, Mapping):
                url = item.get("url")
                if (isinstance(url, str) and url.startswith(("https://", "http://"))
                        and url not in source_urls):
                    source_urls.append(url)
        source_rating = max(1, min(5, len(source_urls)))
        evidence_rating = min(coverage_rating, source_rating)
        evidence_refs = [report_path] if report_path else []
        evidence_refs.extend(source_urls[:11])
        refs = _validate_refs(evidence_refs, "evidence_refs")
        has_experiments = bool(experiments)
        has_counter_evidence = bool(counter_evidence)
        candidates: list[dict[str, Any]] = []
        candidates.append(self.evaluate(
            "research_to_video",
            {
                "impact": 4 if synthesis.get("executive_summary") else 3,
                "recurrence": 1,  # One cycle cannot establish recurrence.
                "capability_fit": 4,  # Muse already has a research/video bridge.
                "evidence_quality": evidence_rating,
                "feasibility": 4,
                "risk": 3 if has_counter_evidence else 4,
            },
            evidence_refs=refs,
        ))
        if has_experiments or has_counter_evidence:
            candidates.append(self.evaluate(
                "capability_gap_to_experiment",
                {
                    "impact": 4 if has_experiments else 3,
                    "recurrence": 1,  # No cross-cycle recurrence inferred.
                    "capability_fit": 4,
                    "evidence_quality": evidence_rating,
                    "feasibility": 3 if has_experiments else 2,
                    "risk": 3 if has_counter_evidence else 4,
                },
                evidence_refs=refs,
            ))
        candidates.sort(key=lambda item: item["evaluation"]["score"], reverse=True)
        return {
            "schema_version": 1,
            "status": "ranked_hypotheses",
            "epistemic_status": "hypothesis_only",
            "source_count": len(source_urls),
            "experiment_count": len(experiments),
            "counter_evidence_count": len(counter_evidence),
            "recurrence_policy": "single_research_cycle_is_not_recurrence_evidence",
            "candidates": candidates,
            "requires_human_review": True,
            "dispatch_started": False,
        }

    def evaluate_capability_radar_result(self, radar_result: Mapping[str, Any]) -> dict[str, Any]:
        """Score existing Radar proposals using operational recurrence only.

        A prior non-overlapping window is an operational repeat signal, not evidence
        of customer demand, commercial value, or independently verified causality.
        This adapter is read-only: it never creates or updates proposals.
        """
        if not isinstance(radar_result, Mapping):
            raise TypeError("radar_result must be a mapping")
        signals_seen = radar_result.get("signals_seen")
        signals_seen = signals_seen if isinstance(signals_seen, list) else []
        signal_index: dict[str, Mapping[str, Any]] = {}
        for signal in signals_seen[:64]:
            if isinstance(signal, Mapping):
                key = signal.get("key")
                if isinstance(key, str) and key and len(key) <= 64:
                    signal_index[key] = signal

        trend = radar_result.get("trend")
        trend = trend if isinstance(trend, Mapping) else {}
        trend_signals = trend.get("signals")
        trend_signals = trend_signals if isinstance(trend_signals, Mapping) else {}
        has_prior_window = trend.get("status") == "compared"

        proposals: list[Mapping[str, Any]] = []
        seen_ids: set[str] = set()
        for bucket_name in ("created", "existing"):
            bucket = radar_result.get(bucket_name)
            if not isinstance(bucket, list):
                continue
            for proposal in bucket[:128]:
                if not isinstance(proposal, Mapping):
                    continue
                proposal_id = proposal.get("id")
                if not isinstance(proposal_id, str) or not proposal_id or proposal_id in seen_ids:
                    continue
                seen_ids.add(proposal_id)
                proposals.append(proposal)

        evaluations: list[dict[str, Any]] = []
        for proposal in proposals:
            metadata = proposal.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            signal_key = proposal.get("signal_key") or metadata.get("signal_key")
            if not isinstance(signal_key, str) or not signal_key:
                continue
            signal = signal_index.get(signal_key, {})
            window = trend_signals.get(signal_key)
            window = window if isinstance(window, Mapping) else {}
            current = window.get("current")
            previous = window.get("previous")
            valid_window = (
                has_prior_window
                and isinstance(current, int) and not isinstance(current, bool) and current >= 0
                and isinstance(previous, int) and not isinstance(previous, bool) and previous >= 0
            )
            recurrence_observed = bool(valid_window and current > 0 and previous > 0)
            combined_count = (current + previous) if valid_window else 0
            evidence_quality = 3 if valid_window and combined_count >= 3 else 2 if signal else 1
            impact_value = signal.get("impact")
            impact_rating = impact_value if isinstance(impact_value, int) and not isinstance(impact_value, bool) and 1 <= impact_value <= 5 else 3
            proposal_ref = f"capability-radar:proposal:{proposal_id}"
            evaluated = self.evaluate(
                "capability_gap_to_experiment",
                {
                    "impact": impact_rating,
                    "recurrence": 2 if recurrence_observed else 1,
                    "capability_fit": 4,
                    "evidence_quality": evidence_quality,
                    "feasibility": 3,
                    "risk": 3,
                },
                evidence_refs=[proposal_ref],
            )
            evaluations.append({
                "proposal_id": proposal_id,
                "signal_key": signal_key,
                "recurrence_observed": recurrence_observed,
                "current_window_count": current if valid_window else signal.get("count"),
                "previous_window_count": previous if valid_window else None,
                "evaluation": evaluated["evaluation"],
                "evidence_refs": evaluated["evidence_refs"],
            })

        evaluations.sort(key=lambda item: item["proposal_id"])
        return {
            "schema_version": 1,
            "epistemic_status": "hypothesis_only",
            "recurrence_scope": "operational_signal_across_non_overlapping_windows",
            "evaluations": evaluations,
            "market_demand_inferred": False,
            "requires_human_review": True,
            "dispatch_started": False,
        }

    def propose_initiative(
        self, initiative_store: Any, pattern_id: str, ratings: Mapping[str, int], *,
        title: str | None = None, evidence_refs: Sequence[str] = (), capability_gap: str | None = None,
    ) -> dict[str, Any]:
        """Create a draft in the existing InitiativeStore; never dispatch work."""
        pattern = self.get_pattern(pattern_id)
        evaluated = self.evaluate(pattern_id, ratings, evidence_refs=evidence_refs)
        if not callable(getattr(initiative_store, "propose", None)):
            raise TypeError("initiative_store must expose the existing propose() interface")
        concept = {
            "schema_version": 1, "origin": "muse_value_forge", "pattern_id": pattern_id,
            "category": pattern["category"], "problem": pattern["problem"],
            "hypothesis": pattern["hypothesis"], "evaluation": evaluated["evaluation"],
            "epistemic_status": "hypothesis_only", "evidence_refs": evaluated["evidence_refs"],
            "evidence_note": evaluated["evidence_note"], "source_refs": [SOURCE_REPOSITORY],
            "guardrails": pattern["guardrails"],
        }
        concept_text = json.dumps(concept, ensure_ascii=False, sort_keys=True)
        if len(concept_text) > 3900:
            raise ValueError("value proposal concept exceeds InitiativeStore's safe text limit")
        refs = [f"value-pattern:{pattern_id}", SOURCE_REPOSITORY, *evaluated["evidence_refs"]]
        initiative = initiative_store.propose(
            title=(title or f"价值模式试点：{pattern['title']}")[:180],
            kind=pattern["initiative_kind"], creator_value=pattern["creator_value"],
            concept=concept_text, first_step=pattern["first_step"], expected_output=pattern["expected_output"],
            interest_signals=[
                f"value_score:{evaluated['evaluation']['score']:.2f}",
                f"value_band:{evaluated['evaluation']['band']}",
                "evidence_state:hypothesis_only", f"risk:{ratings['risk']}/5",
            ],
            inspiration_refs=refs[:64], capability_gap=(capability_gap or pattern["capability_gap"])[:2000],
            priority=max(0, min(100, round(evaluated["evaluation"]["score"]))), autonomy_level="draft",
        )
        return {"evaluation": evaluated, "initiative": initiative, "dispatch_started": False}

    @staticmethod
    def promote_business_opportunity(business_store: Any, opportunity_id: str, *, initiative_database: str) -> dict[str, Any]:
        """Delegate commercial promotion to the existing evidence-aware business ledger."""
        promote = getattr(business_store, "promote_to_initiative", None)
        if not callable(promote):
            raise TypeError("business_store must expose promote_to_initiative()")
        return promote(opportunity_id, initiative_database=initiative_database)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Muse ValueForge: discover and evaluate value patterns.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("patterns", help="list abstract value patterns")
    evaluate = commands.add_parser("evaluate", help="score a pattern without creating a record")
    evaluate.add_argument("--pattern", required=True, choices=tuple(_PATTERN_CATALOG))
    evaluate.add_argument("--ratings-json", required=True)
    evaluate.add_argument("--evidence-ref", action="append", default=[])
    propose = commands.add_parser("propose", help="create a draft in the existing InitiativeStore")
    propose.add_argument("--pattern", required=True, choices=tuple(_PATTERN_CATALOG))
    propose.add_argument("--ratings-json", required=True)
    propose.add_argument("--initiative-database", required=True)
    propose.add_argument("--evidence-ref", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    forge = ValueForge()
    try:
        if args.command == "patterns":
            result: Any = forge.list_patterns()
        else:
            ratings = json.loads(args.ratings_json)
            if args.command == "evaluate":
                result = forge.evaluate(args.pattern, ratings, evidence_refs=args.evidence_ref)
            else:
                from muse.initiative_engine import InitiativeStore
                with InitiativeStore(args.initiative_database) as store:
                    result = forge.propose_initiative(store, args.pattern, ratings, evidence_refs=args.evidence_ref)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
