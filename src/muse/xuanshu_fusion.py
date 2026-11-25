"""B396 WS5 玄枢超融合实验室 (Xuanshu hyper-fusion lab).

Fuses four symbolic systems — 易经 (I Ching), 紫微斗数 (Zi Wei Dou Shu), 奇门遁甲 (Qimen Dunjia) and
大六壬 (Da Liu Ren) — into *creative prompts*. Every result, every lens and every derived prompt
carries the immutable epistemic labels ``symbolic_inspiration`` and ``unverified_hypothesis``.

* The I Ching lens is always available (deterministic, seeded, in-repo calculation).
* Zi Wei / Qimen / Liu Ren run only through injected providers (the pinned isolated adapters in
  ``ziwei_provider`` / ``qimen_provider`` / ``kinliuren_provider``); a missing or failing provider is
  reported as ``unavailable`` — never fabricated.
* :func:`validate_labels` is used by every consumer (API, feedback bridge, memory) and rejects any
  payload whose labels were stripped, reordered or "upgraded" (e.g. ``evidence`` / ``prediction``).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from muse.yijing import seeded_three_coin_cast

EPISTEMIC_LABELS: tuple[str, str] = ("symbolic_inspiration", "unverified_hypothesis")
FORBIDDEN_UPGRADES = frozenset({"evidence", "verified", "prediction", "fact", "proven", "guaranteed"})
LENSES = ("yijing", "ziwei", "qimen", "liuren")
# immutable source of truth (tuple of pairs); a fresh dict is built for every payload
_LABEL_ITEMS: tuple[tuple[str, Any], ...] = (("epistemic_labels", EPISTEMIC_LABELS), ("is_evidence", False),
                                             ("is_prediction", False))


class XuanshuLabelError(ValueError):
    """A symbolic payload lost or altered its mandatory epistemic labels."""


def _labelled(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, **dict(_LABEL_ITEMS)}


def validate_labels(payload: Any, *, path: str = "result") -> None:
    """Recursively require the exact label tuple on the payload and on every lens / prompt object."""
    if not isinstance(payload, Mapping):
        raise XuanshuLabelError(f"{path} must be an object")
    labels = payload.get("epistemic_labels")
    if tuple(labels or ()) != EPISTEMIC_LABELS:
        raise XuanshuLabelError(f"{path} is missing the immutable labels {EPISTEMIC_LABELS}")
    if payload.get("is_evidence") is not False or payload.get("is_prediction") is not False:
        raise XuanshuLabelError(f"{path} may never be marked as evidence or prediction")
    text = json.dumps(payload.get("status", ""), ensure_ascii=False).casefold()
    if any(word in text for word in FORBIDDEN_UPGRADES):
        raise XuanshuLabelError(f"{path} status attempts an epistemic upgrade")
    for key in ("lenses",):
        for name, lens in (payload.get(key) or {}).items():
            validate_labels(lens, path=f"{path}.{key}.{name}")
    for index, prompt in enumerate(payload.get("prompts") or []):
        validate_labels(prompt, path=f"{path}.prompts[{index}]")


def _yijing_lens(seed: str) -> dict[str, Any]:
    cast = seeded_three_coin_cast(seed)
    primary, relating = cast["primary"], cast["relating"]
    return _labelled({
        "status": "computed", "engine": "muse.yijing", "seed_sha256": cast.get("seed_sha256"),
        "primary": f"{primary['unicode_symbol']} {primary['king_wen_number']} {primary['king_wen_name']}",
        "relating": f"{relating['unicode_symbol']} {relating['king_wen_number']} {relating['king_wen_name']}",
        "moving_lines": cast.get("moving_lines", []),
        "trigrams": [primary["lower_trigram"]["name"], primary["upper_trigram"]["name"]],
        "motif": f"{primary['lower_trigram']['name']}下{primary['upper_trigram']['name']}上",
    })


def _provider_lens(name: str, provider: Any, context: Any) -> dict[str, Any]:
    if provider is None or context is None:
        return _labelled({"status": "unavailable", "reason": "provider_not_configured", "engine": name})
    try:
        raw = provider.calculate(context)
    except Exception as exc:
        return _labelled({"status": "unavailable", "reason": f"provider_error:{type(exc).__name__}", "engine": name})
    if not isinstance(raw, Mapping):
        return _labelled({"status": "unavailable", "reason": "provider_returned_non_object", "engine": name})
    digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    keywords = [str(v)[:40] for k, v in raw.items() if isinstance(v, (str, int)) and k not in {"version", "engine"}][:6]
    return _labelled({"status": "computed", "engine": name, "raw_sha256": digest, "keywords": keywords})


def fuse(question: str, *, seed: str, providers: Mapping[str, Any] | None = None, context: Any = None,
         clock: Callable[[], float] | None = None) -> dict[str, Any]:
    """Run every lens and derive labelled creative prompts. Never returns advice or forecasts."""
    if not isinstance(question, str) or not 2 <= len(question.strip()) <= 500:
        raise ValueError("question must be 2-500 characters")
    if not isinstance(seed, str) or not 1 <= len(seed) <= 200:
        raise ValueError("seed must be 1-200 characters")
    providers = providers or {}
    lenses = {"yijing": _yijing_lens(seed)}
    for name in ("ziwei", "qimen", "liuren"):
        lenses[name] = _provider_lens(name, providers.get(name), context)
    computed = [name for name, lens in lenses.items() if lens["status"] == "computed"]
    motif = lenses["yijing"]["motif"]
    prompts = [
        _labelled({"angle": "visual_metaphor",
                   "text": f"以「{motif}」的意象为视觉母题，为『{question.strip()}』设计一个开场镜头（仅作灵感）"}),
        _labelled({"angle": "tension",
                   "text": f"从「{lenses['yijing']['primary']}」→「{lenses['yijing']['relating']}」的变化，构思一个反转情节"}),
        _labelled({"angle": "counterfactual",
                   "text": "列出这个灵感最可能出错的 3 个方向，并为每个方向设计一个可测量的小实验"}),
    ]
    for name in computed[1:]:
        keywords = "、".join(lenses[name].get("keywords", [])[:3]) or name
        prompts.append(_labelled({"angle": f"{name}_texture", "text": f"借用 {name} 盘面关键词（{keywords}）作为色彩/节奏灵感"}))
    result = _labelled({
        "status": "inspiration_only", "question": question.strip(), "lenses": lenses, "computed_lenses": computed,
        "prompts": prompts,
        "usage_policy": "creative ideation only; not advice, not forecasting, not evidence; test ideas with real experiments",
        "next_step": "convert a prompt into a falsifiable experiment and record the result through the feedback bridge",
    })
    if clock is not None:
        result["generated_at"] = clock()
    validate_labels(result)
    return result
