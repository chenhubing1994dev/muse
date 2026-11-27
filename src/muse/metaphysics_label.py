"""B391 P1: mandatory 「玄学推演／待验证假设」 labelling for every metaphysical output.

Muse treats 易经 / 紫微斗数 (ziwei) / 奇门遁甲 (qimen-rs) / 大六壬 (kinliuren) purely as symbolic
observation, cross-domain analogy and hypothesis generation. This module is the single place that

* defines the human-readable display label required by the Muse charter and the B391 prompt,
* stamps API payloads from metaphysical routes with that label (never removing existing labels,
  and always forcing ``is_evidence`` / ``is_prediction`` to ``False``),
* converts a :func:`muse.xuanshu_fusion.fuse` result into the structured chain
  ``象征观察 → 清晰问题 → 可检验假设与替代解释`` so it can be written to the Research Wiki as
  ``idea`` / ``claim`` nodes that still carry the label.

Nothing here upgrades a symbolic result into evidence, advice or a forecast.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

METAPHYSICS_DISPLAY_LABEL = "玄学推演／待验证假设"
METAPHYSICS_EPISTEMIC_LABELS: tuple[str, str] = ("symbolic_inspiration", "unverified_hypothesis")
METAPHYSICS_USAGE_NOTE = "仅作象征观察、跨域类比与可检验假设生成；不是事实、证据、建议或预测。"

# API routes whose 2xx JSON bodies are metaphysical and must carry the label.
LABELLED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/metaphysics/",
    "/api/v1/omega/xuanshu/",
    "/api/v1/control/xuanshu/",
)

_FORBIDDEN_STATUS_WORDS = ("evidence", "verified", "prediction", "fact", "proven", "guaranteed")


class MetaphysicsLabelError(ValueError):
    """A metaphysical payload is missing its mandatory label or claims to be evidence."""


def needs_label(path: str) -> bool:
    return isinstance(path, str) and path.startswith(LABELLED_PATH_PREFIXES)


def label_fields() -> dict[str, Any]:
    """Return a fresh copy of the mandatory label fields."""
    return {
        "epistemic_display_label": METAPHYSICS_DISPLAY_LABEL,
        "epistemic_labels": list(METAPHYSICS_EPISTEMIC_LABELS),
        "is_evidence": False,
        "is_prediction": False,
        "epistemic_usage_note": METAPHYSICS_USAGE_NOTE,
    }


def label_payload(payload: Any) -> Any:
    """Stamp a JSON object with the label; non-objects are returned unchanged.

    Existing ``epistemic_labels`` (for example the Xuanshu tuple) are preserved, but the display
    label is always present and evidence / prediction flags are always forced to ``False``.
    """
    if not isinstance(payload, Mapping):
        return payload
    stamped = dict(payload)
    existing = stamped.get("epistemic_labels")
    stamped.update(label_fields())
    if isinstance(existing, (list, tuple)) and existing:
        merged = list(dict.fromkeys([*[str(item) for item in existing], *METAPHYSICS_EPISTEMIC_LABELS]))
        stamped["epistemic_labels"] = merged
    return stamped


def require_label(payload: Any, *, path: str = "payload") -> None:
    """Fail closed when a metaphysical object lost its label or was upgraded to evidence."""
    if not isinstance(payload, Mapping):
        raise MetaphysicsLabelError(f"{path} must be an object")
    if payload.get("epistemic_display_label") != METAPHYSICS_DISPLAY_LABEL:
        raise MetaphysicsLabelError(f"{path} is missing the 「{METAPHYSICS_DISPLAY_LABEL}」 label")
    labels = payload.get("epistemic_labels")
    if not isinstance(labels, (list, tuple)) or not set(METAPHYSICS_EPISTEMIC_LABELS) <= {str(x) for x in labels}:
        raise MetaphysicsLabelError(f"{path} lost its epistemic labels")
    if payload.get("is_evidence") is not False or payload.get("is_prediction") is not False:
        raise MetaphysicsLabelError(f"{path} may never be marked as evidence or prediction")
    status_text = json.dumps(payload.get("status", ""), ensure_ascii=False).casefold()
    if any(word in status_text for word in _FORBIDDEN_STATUS_WORDS):
        raise MetaphysicsLabelError(f"{path} status attempts an epistemic upgrade")


def _clean(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def symbolic_to_hypotheses(fuse_result: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a labelled Xuanshu fusion result into observation → question → testable hypotheses.

    The output is deterministic for a given fusion result and every element carries the label.
    Hypotheses are phrased as falsifiable experiments with an explicit alternative explanation.
    """
    if not isinstance(fuse_result, Mapping):
        raise MetaphysicsLabelError("fusion result must be an object")
    labels = fuse_result.get("epistemic_labels")
    if not isinstance(labels, (list, tuple)) or "unverified_hypothesis" not in {str(x) for x in labels}:
        raise MetaphysicsLabelError("fusion result must already carry the unverified_hypothesis label")
    if fuse_result.get("is_evidence") is not False or fuse_result.get("is_prediction") is not False:
        raise MetaphysicsLabelError("fusion result may never be evidence or prediction")
    question = _clean(fuse_result.get("question"), 500)
    if len(question) < 2:
        raise MetaphysicsLabelError("fusion result has no question")
    lenses = fuse_result.get("lenses") if isinstance(fuse_result.get("lenses"), Mapping) else {}
    observations = []
    for name in ("yijing", "ziwei", "qimen", "liuren"):
        lens = lenses.get(name)
        if not isinstance(lens, Mapping):
            continue
        if lens.get("status") not in {"computed", "symbolic_fallback"} and not str(lens.get("status", "")).startswith("symbolic"):
            observations.append(label_payload({
                "lens": name, "status": "unavailable",
                "text": f"{name} 透镜不可用（{_clean(lens.get('reason'), 80) or '未配置'}），未伪造盘面。",
            }))
            continue
        if name == "yijing":
            text = (f"本卦 {_clean(lens.get('primary'), 60)} → 之卦 {_clean(lens.get('relating'), 60)}；"
                    f"意象 {_clean(lens.get('motif'), 40)}")
        else:
            keywords = [str(item)[:40] for item in (lens.get("keywords") or [])][:6]
            text = f"{name} 盘面关键词：{'、'.join(keywords) if keywords else '（无关键词）'}"
        observations.append(label_payload({"lens": name, "status": "observed", "text": text}))

    motif = _clean((lenses.get("yijing") or {}).get("motif"), 40) or "变化"
    clear_question = (f"围绕「{question}」，有哪些可以在 7–14 天内用真实数据检验的具体变量？"
                      "（象征只用于提出问题，不用于回答问题）")
    hypotheses = [
        {
            "id": "h1",
            "statement": f"以「{motif}」为视觉母题的开场，会比当前默认开场带来更高的 3 秒留存。",
            "test": "同一主题制作 A/B 两个开场，各投放给同等规模的真实受众，比较 3 秒留存率。",
            "success_signal": "预先设定的留存差异阈值（例如 ≥5 个百分点）且样本量足够。",
            "alternative_explanation": "差异可能来自画面亮度、字幕或投放时段，而非意象本身；需控制这些变量。",
        },
        {
            "id": "h2",
            "statement": f"把「{question}」拆成「冲突 → 转折 → 解决」三段叙事，会提高完播率。",
            "test": "对比三段式脚本与线性叙述脚本的完播率与平均观看时长。",
            "success_signal": "完播率提升且平均观看时长不下降。",
            "alternative_explanation": "完播率变化可能只是因为时长变短；需保持两版时长一致。",
        },
        {
            "id": "h3",
            "statement": "这个灵感最可能失败的原因是受众对主题本身缺乏需求，而不是表达方式。",
            "test": "在制作前先用标题/封面做低成本需求测试（点击率或问卷），记录真实反馈。",
            "success_signal": "获得至少一项可追溯的真实需求信号。",
            "alternative_explanation": "低点击率也可能是封面设计问题；需至少测试两种封面。",
        },
    ]
    hypotheses = [label_payload({**item, "status": "proposed"}) for item in hypotheses]
    digest = hashlib.sha256(json.dumps(
        {"question": question, "observations": observations, "hypotheses": hypotheses},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    result = label_payload({
        "status": "hypotheses_only",
        "question": question,
        "symbolic_observations": observations,
        "clear_question": clear_question,
        "testable_hypotheses": hypotheses,
        "source_fusion_sha256": digest,
        "next_step": "选择一条假设，写入 Research Wiki 作为 claim（status=proposed），再提交 Commander 审批后研究或制作样片。",
    })
    require_label(result, path="hypotheses")
    for index, item in enumerate(hypotheses):
        require_label(item, path=f"hypotheses[{index}]")
    return result


def write_hypotheses_to_wiki(knowledge_store: Any, hypotheses: Mapping[str, Any]) -> dict[str, Any]:
    """Persist an idea node plus one proposed claim per hypothesis, linked by ``inspired_by``.

    ``knowledge_store`` is a :class:`muse.research_knowledge.ResearchKnowledgeStore`. Claims are
    created as ``proposed`` and keep the label in metadata; they are never marked supported.
    """
    require_label(hypotheses, path="hypotheses")
    digest = str(hypotheses.get("source_fusion_sha256") or "")
    if len(digest) != 64:
        raise MetaphysicsLabelError("hypotheses must carry source_fusion_sha256")
    label_meta = {
        "epistemic_display_label": METAPHYSICS_DISPLAY_LABEL,
        "epistemic_labels": list(METAPHYSICS_EPISTEMIC_LABELS),
        "is_evidence": False,
        "is_prediction": False,
        "origin": "xuanshu_symbolic_lens",
    }
    idea = knowledge_store.upsert_node(
        "idea", f"【{METAPHYSICS_DISPLAY_LABEL}】{hypotheses['question']}"[:500],
        summary=_clean(hypotheses.get("clear_question"), 2000),
        external_ref=f"xuanshu-idea:{digest}",
        metadata={**label_meta, "symbolic_observations": list(hypotheses.get("symbolic_observations") or [])[:8]},
        provenance=[f"xuanshu-fusion:{digest}"],
    )
    claims = []
    for item in hypotheses.get("testable_hypotheses") or []:
        require_label(item, path="hypothesis")
        claim = knowledge_store.upsert_node(
            "claim", f"【{METAPHYSICS_DISPLAY_LABEL}】{item['statement']}"[:500],
            summary=_clean(f"检验方法：{item.get('test')}；替代解释：{item.get('alternative_explanation')}", 2000),
            external_ref=f"xuanshu-claim:{digest}:{item['id']}",
            metadata={**label_meta, "test": item.get("test"), "success_signal": item.get("success_signal"),
                      "alternative_explanation": item.get("alternative_explanation")},
            provenance=[f"xuanshu-fusion:{digest}"],
            status="proposed",
        )
        knowledge_store.add_edge(claim["id"], idea["id"], "inspired_by",
                                 metadata={"epistemic_display_label": METAPHYSICS_DISPLAY_LABEL})
        claims.append({"id": claim["id"], "title": claim.get("title"), "status": claim.get("status")})
    return label_payload({"status": "written_as_proposed", "idea_id": idea["id"], "claims": claims})
