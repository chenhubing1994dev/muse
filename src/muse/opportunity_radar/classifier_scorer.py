"""Deterministic-first opportunity classification, risk detection and scoring.

The rule layer is standalone and consumes no model/API calls. Semantic enhancement is an
injected Protocol only: the current Provider Policy adapter signature must be wired and
verified by the second-batch integration; this file deliberately invents no Provider import.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable

from .models import (
    AccessTier,
    EvidenceRecord,
    LicenseRisk,
    OpportunityCandidate,
    OpportunityLane,
    RadarConfig,
    TrapFlag,
    VerificationStatus,
    utc_now,
)
from .source_hints import source_group_diversity_hint
from .sanitizer import sanitize_external_payload, sanitize_external_text, validate_public_url

@dataclass(frozen=True, slots=True)
class LicenseAssessment:
    spdx: str
    risk: LicenseRisk
    reason: str


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    candidate: OpportunityCandidate
    rule_reasons: tuple[str, ...]
    semantic_applied: bool
    semantic_status: str


@runtime_checkable
class SemanticProvider(Protocol):
    """Adapter boundary for an already-authorized Muse local/Provider-Policy model."""

    async def classify_opportunity(self, candidate: OpportunityCandidate) -> Mapping[str, Any]:
        """Return a JSON-like mapping; its output is untrusted and schema-validated."""
        raise NotImplementedError("SemanticProvider adapters must implement classify_opportunity")

def _sanitize_metadata(value: Any, *, depth: int = 0) -> Any:
    """Recursively bound metadata before it can be serialized into a model context."""
    if depth > 5:
        return "[METADATA_DEPTH_LIMIT]"
    if isinstance(value, str):
        return sanitize_external_text(value, max_chars=1000).text
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        blocked_keys = {
            "role", "system", "developer", "assistant", "tool", "instructions",
            "prompt", "policy", "authorization", "approved", "approval_granted",
            "verified", "verified_at", "verified_by", "verification_status",
            "independence", "independent_source_count", "distinct_source_count",
            "credibility", "credibility_score", "roi_verified", "license_adoption_blocker",
            "license_review_required", "semantic_provider_used", "rule_filtered",
        }
        for raw_key, raw_value in list(value.items())[:50]:
            key = sanitize_external_text(str(raw_key), max_chars=80).text
            normalized_key = re.sub(r"[^a-z0-9_]", "_", key.casefold())
            if not key or normalized_key in blocked_keys:
                continue
            if normalized_key.startswith(("approval_", "verified_", "semantic_", "execution_")):
                continue
            clean[key] = _sanitize_metadata(raw_value, depth=depth + 1)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata(item, depth=depth + 1) for item in value[:50]]
    return sanitize_external_text(str(value), max_chars=500).text



def _sanitize_evidence_record(raw: EvidenceRecord | Mapping[str, Any]) -> EvidenceRecord:
    """Rebuild evidence with sanitized prose and a URL-derived origin identity."""
    source = raw.to_dict() if isinstance(raw, EvidenceRecord) else raw
    if not isinstance(source, Mapping):
        raise TypeError("evidence item must be an EvidenceRecord or mapping")
    url = validate_public_url(source.get("url", ""))
    source_id = sanitize_external_text(source.get("source_id", ""), max_chars=160).text
    if not source_id:
        source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    source_type = sanitize_external_text(source.get("source_type", "unknown"), max_chars=80).text.casefold()
    if source_type not in {"github", "github_repository", "github_catalog", "rss", "reddit", "community_json"}:
        source_type = "unknown"
    title = sanitize_external_text(source.get("title", ""), max_chars=500).text or "External source"
    snippet = sanitize_external_text(source.get("snippet", ""), max_chars=4000).text
    publisher = sanitize_external_text(source.get("publisher", ""), max_chars=200).text
    domain = source_group_diversity_hint(url)
    canonical = source.get("canonical_url", "")
    canonical_url = validate_public_url(canonical) if canonical else ""
    metadata = source.get("source_metadata", {})
    clean_metadata = _sanitize_metadata(metadata if isinstance(metadata, Mapping) else {})
    independence = "unassessed"
    return EvidenceRecord(
        source_id=source_id,
        source_type=source_type,
        url=url,
        observed_at=source.get("observed_at") or utc_now().isoformat(),
        title=title,
        snippet=snippet,
        canonical_url=canonical_url,
        publisher=publisher,
        registrable_domain=domain,
        content_sha256=str(source.get("content_sha256", "")),
        independence=independence,
        source_metadata=clean_metadata,
    )


_COST_SIGNALS = re.compile(
    r"\b(?:free\s+api|free\s+credits?|cloud\s+credits?|gpu\s+credits?|"
    r"free\s+tier|startup\s+credits?|student\s+credits?|free\s+compute|"
    r"token\s+quota|免费额度|免费算力|云额度|API\s*额度|赠送\s*额度|学生包|"
    r"免费云|免费服务器|免费数据库|免费托管)\b",
    re.IGNORECASE,
)
_TECH_SIGNALS = re.compile(
    r"github\.com/|gitlab\.com/|codeberg\.org/|open[\s-]?source|self[\s-]?host|"
    r"开源|自托管|源码|repository|repo\b|MIT\s+License|Apache-2\.0",
    re.IGNORECASE,
)
_CREATIVE_SIGNALS = re.compile(
    r"comfyui|workflow|checkpoint|lora|controlnet|diffusion|video\s+model|"
    r"sound\s+effect|royalty[\s-]?free|stock\s+footage|font\s+family|"
    r"AI\s*视频|工作流|音效|素材|字体|模型权重|节点包|插件包",
    re.IGNORECASE,
)
_MONETIZATION_SIGNALS = re.compile(
    r"micro[\s-]?saas|saas|pain\s+point|customer\s+request|feature\s+request|"
    r"looking\s+for\s+tool|need\s+a\s+tool|automation\s+business|"
    r"客户痛点|付费需求|求工具|效率工具|自动化需求|微型\s*SaaS|订阅产品",
    re.IGNORECASE,
)
_TRAP_PATTERNS: tuple[tuple[TrapFlag, re.Pattern[str]], ...] = (
    (TrapFlag.REQUIRES_CREDIT_CARD, re.compile(
        r"credit\s*card\s*(?:is\s+)?(?:required|needed)|card\s+on\s+file|"        r"绑卡|信用卡验证|需要信用卡|需提供银行卡", re.IGNORECASE)),
    (TrapFlag.GEO_RESTRICTED, re.compile(
        r"not\s+available\s+in\s+your\s+country|selected\s+countries\s+only|"
        r"geo[\s-]?restrict|地域限制|仅限.{0,12}(?:美国|欧洲|地区)|地区不可用", re.IGNORECASE)),
    (TrapFlag.WAITLIST_ONLY, re.compile(
        r"wait[\s-]?list\s+only|join\s+the\s+waitlist|waitlist|"
        r"候补名单|等待名单|排队申请|仅限候补", re.IGNORECASE)),
    (TrapFlag.REFERRAL_BAIT, re.compile(
        r"invite\s+\d+\s+friends|refer\s+\d+|referral\s+required|"
        r"邀请.{0,8}(?:好友|朋友).{0,8}(?:注册|加入)|邀请返利|拉新后领取", re.IGNORECASE)),
    (TrapFlag.AUTO_RENEWAL, re.compile(
        r"auto[\s-]?renew(?:al)?|renews?\s+automatically|自动续费|到期自动扣费", re.IGNORECASE)),
    (TrapFlag.USAGE_CAP, re.compile(
        r"fair\s+use|usage\s+cap|rate\s+limit|limited\s+quota|"
        r"限速|限额|额度上限|每月仅|每月限", re.IGNORECASE)),
    (TrapFlag.STUDENT_ONLY, re.compile(
        r"student[s]?\s+only|academic\s+verification|学生专享|仅限学生|教育邮箱验证", re.IGNORECASE)),
    (TrapFlag.INVITE_ONLY, re.compile(
        r"invite[\s-]?only|invitation\s+code\s+required|邀请码|仅限邀请|内测资格", re.IGNORECASE)),
    (TrapFlag.PAYMENT_METHOD_REQUIRED, re.compile(
        r"payment\s+method\s+required|billing\s+details\s+required|"
        r"绑定支付方式|需要支付方式|需填写账单信息", re.IGNORECASE)),
)
_FREE_TRIAL = re.compile(r"free\s+trial|trial\s+for\s+\d+|免费试用|试用期|首月免费|免费体验", re.IGNORECASE)
_PROMO_CREDIT = re.compile(r"promo(?:tional)?\s+credits?|credits?\s+grant|free\s+credits?|体验金|赠送额度|代金券|算力券|token\s+grant|免费额度|GPU\s*credits?", re.IGNORECASE)
_CONDITIONAL_FREE = re.compile(r"invite|referral|student|waitlist|apply|approval|报名|邀请|申请|审核|学生|候补|注册.{0,8}(?:领取|才能|后)|signup\s+required|registration\s+required|login\s+required|email\s+verification", re.IGNORECASE)
_TRULY_FREE = re.compile(r"\bfree\b|freebie|free\s+forever|free\s+tier|no\s+credit\s+card|无需绑卡|永久免费|完全免费|开源免费|白嫖|限免|免费领|免费送|零元|0\s*元", re.IGNORECASE)
_PAID_ROI = re.compile(r"high\s+roi|positive\s+roi|payback\s+period|cost[- ]effective|省成本|节省成本|快速回本|投资回报|高回报|高ROI", re.IGNORECASE)
_PAID_SIGNALS = re.compile(r"\bpaid\b|pricing|subscription|monthly\s+plan|年付|月付|付费|订阅|收费|价格", re.IGNORECASE)
_REVOKED_SIGNALS = re.compile(r"已撤销|已结束|活动结束|福利失效|优惠失效|兑换码失效|已下架|不再提供|revoked|expired|no longer available", re.IGNORECASE)
_LOTTERY_SIGNALS = re.compile(r"抽奖|抽\s*\d+\s*(?:份|个|名|位)|随机送|开奖|中奖|摇号|锦鲤|抽选|抽签|赢取|sweepstakes|\bwinners?\b|\braffle\b", re.IGNORECASE)
_PROMO_ONLY_SIGNALS = re.compile(r"招募|代理|加盟|官方旗舰|我们的服务|自营|软广|推广位|商务合作|招商|诚招|带货|优惠码.*分成|affiliate|referral commission", re.IGNORECASE)
_CONDITION_NEGATION = re.compile(r"(?:无需|无须|不用|不必|不需要|no|without|not required)\s*(?:(?:注册(?:账号|账户)?|验证(?:邮箱|手机(?:号)?|短信)|绑定(?:手机(?:号)?|邮箱)|登录|充值|下单|关注|sign\s*up|registration|account|email verification)(?:\s*(?:或|和|及|并|or|and)\s*)?)+", re.IGNORECASE)
_NOISE_TITLE = re.compile(
    r"^(怎样|怎么|如何|哪里|哪个|为什么|为何)|请问|有没有|求推荐|求一个|哪里(有|能)|值得买吗|怎么样[?？]|不够用|用完了|失效了|吐槽|踩坑|后悔|失望|谁有|求个|求购|哪有|多少钱|求助|求带|大佬|老哥|有人吗|有人知道|怎么弄|能白嫖吗|免费更新|内容更新|版本更新|免费升级|更新公告|更新补丁|补丁说明|patch\s*notes|free\s+update|留\s*id|进群|加群|私信|加微|评论区留|扣\s*[1一]|邀请返利|返利链接|中转站|点我(?:的)?链接",
    re.IGNORECASE,
)
_LURE_SIGNALS = re.compile(r"加微|加\s*V|微信|私信|关注(?:后|领|即可|才)|评论区?\s*扣|扣\s*1|返利|返现|\baff\b|扫码进群|进群领|付费解锁", re.IGNORECASE)
_MUSE_FIT = re.compile(
    r"muse|agent|llm|large language model|ai|gpu|comfyui|workflow|"
    r"video generation|video editing|automation|python|rust|open.?source|"
    r"模型|智能体|算力|视频生成|视频剪辑|自动化|开源|工作流|知识库",
    re.IGNORECASE,
)
_LICENSE_PATTERNS: tuple[tuple[str, LicenseRisk, re.Pattern[str]], ...] = (
    ("AGPL-3.0", LicenseRisk.NETWORK_COPYLEFT, re.compile(r"\bAGPL(?:-3\.0(?:-only|-or-later)?)?\b|GNU\s+AFFERO", re.IGNORECASE)),
    ("GPL-3.0", LicenseRisk.STRONG_COPYLEFT, re.compile(r"\bGPL(?:-3\.0(?:-only|-or-later)?)?\b|GNU\s+GENERAL\s+PUBLIC\s+LICENSE\s+VERSION\s+3", re.IGNORECASE)),
    ("GPL-2.0", LicenseRisk.STRONG_COPYLEFT, re.compile(r"\bGPL(?:-2\.0(?:-only|-or-later)?)?\b|GNU\s+GENERAL\s+PUBLIC\s+LICENSE\s+VERSION\s+2", re.IGNORECASE)),
    ("LGPL-3.0", LicenseRisk.WEAK_COPYLEFT, re.compile(r"\bLGPL(?:-3\.0(?:-only|-or-later)?)?\b", re.IGNORECASE)),
    ("LGPL-2.1", LicenseRisk.WEAK_COPYLEFT, re.compile(r"\bLGPL(?:-2\.1(?:-only|-or-later)?)?\b", re.IGNORECASE)),
    ("MPL-2.0", LicenseRisk.WEAK_COPYLEFT, re.compile(r"\bMPL-2\.0\b|MOZILLA\s+PUBLIC\s+LICENSE\s+2\.0", re.IGNORECASE)),
    ("Apache-2.0", LicenseRisk.PERMISSIVE, re.compile(r"\bApache(?:\s+License)?[- ]?2\.0\b", re.IGNORECASE)),
    ("MIT", LicenseRisk.PERMISSIVE, re.compile(r"\bMIT\s+(?:License|license)\b|Permission\s+is\s+hereby\s+granted", re.IGNORECASE)),
    ("BSD-3-Clause", LicenseRisk.PERMISSIVE, re.compile(r"\bBSD(?:\s+3-Clause|-3-Clause)\b", re.IGNORECASE)),
    ("BSD-2-Clause", LicenseRisk.PERMISSIVE, re.compile(r"\bBSD(?:\s+2-Clause|-2-Clause)\b", re.IGNORECASE)),
    ("ISC", LicenseRisk.PERMISSIVE, re.compile(r"\bISC\s+License\b", re.IGNORECASE)),
    ("SSPL-1.0", LicenseRisk.NETWORK_COPYLEFT, re.compile(r"\bSSPL(?:-1\.0)?\b|SERVER\s+SIDE\s+PUBLIC\s+LICENSE", re.IGNORECASE)),
)


def assess_license(license_text: str, explicit_spdx: str = "") -> LicenseAssessment:
    explicit = explicit_spdx.strip()
    explicit_map = {
        "MIT": (LicenseRisk.PERMISSIVE, "MIT is a permissive license."),
        "Apache-2.0": (LicenseRisk.PERMISSIVE, "Apache-2.0 is permissive and includes notice/patent terms."),
        "BSD-2-Clause": (LicenseRisk.PERMISSIVE, "BSD-2-Clause is permissive."),
        "BSD-3-Clause": (LicenseRisk.PERMISSIVE, "BSD-3-Clause is permissive."),
        "ISC": (LicenseRisk.PERMISSIVE, "ISC is permissive."),
        "MPL-2.0": (LicenseRisk.WEAK_COPYLEFT, "MPL-2.0 has file-level copyleft obligations."),
        "LGPL-2.1": (LicenseRisk.WEAK_COPYLEFT, "LGPL-2.1 has library-linking obligations."),
        "LGPL-3.0": (LicenseRisk.WEAK_COPYLEFT, "LGPL-3.0 has library-linking obligations."),
        "GPL-2.0": (LicenseRisk.STRONG_COPYLEFT, "GPL-2.0 is strong copyleft."),
        "GPL-3.0": (LicenseRisk.STRONG_COPYLEFT, "GPL-3.0 is strong copyleft."),
        "AGPL-3.0": (LicenseRisk.NETWORK_COPYLEFT, "AGPL-3.0 adds network-use source obligations."),
        "SSPL-1.0": (LicenseRisk.NETWORK_COPYLEFT, "SSPL-1.0 requires specialist compatibility review."),
    }
    if explicit in explicit_map:
        risk, reason = explicit_map[explicit]
        return LicenseAssessment(explicit, risk, reason)
    text = f"{explicit}\n{license_text}".strip()
    if not text:
        return LicenseAssessment("", LicenseRisk.UNKNOWN, "No license evidence found; do not assume permission.")
    for spdx, risk, pattern in _LICENSE_PATTERNS:
        if pattern.search(text):
            return LicenseAssessment(spdx, risk, f"Detected {spdx}; review compatibility and obligations before adoption.")
    if re.search(r"all\s+rights\s+reserved|proprietary|commercial\s+license\s+only|专有许可|保留所有权利", text, re.IGNORECASE):
        return LicenseAssessment("LicenseRef-Proprietary", LicenseRisk.PROPRIETARY, "Proprietary or reserved-rights wording detected.")
    return LicenseAssessment("", LicenseRisk.UNKNOWN, "License text is missing or not recognized as a supported SPDX family.")

def _infer_lane(title: str, summary: str, url: str, metadata: Mapping[str, Any]) -> OpportunityLane:
    text = f"{title}\n{summary}\n{url}"
    if _CREATIVE_SIGNALS.search(text):
        return OpportunityLane.CREATIVE_ASSET
    if _TECH_SIGNALS.search(text):
        return OpportunityLane.TECH_FUSION
    category_hint = str(metadata.get("category_hint", "")).casefold()
    if category_hint in {"ai", "domain", "edu", "software", "cloud"}:
        return OpportunityLane.COST_SAVING
    if _MONETIZATION_SIGNALS.search(text):
        return OpportunityLane.MONETIZATION
    if _COST_SIGNALS.search(text):
        return OpportunityLane.COST_SAVING
    return OpportunityLane.COST_SAVING


def _guess_acquisition_mode(
    title: str,
    summary: str,
    content: str,
    metadata: Mapping[str, Any],
) -> str:
    """Port ordered obtain rules without treating promotional copy as proof."""
    text = f"{title}\n{summary}\n{content}"
    if _REVOKED_SIGNALS.search(text):
        return "revoked"
    if _LOTTERY_SIGNALS.search(text):
        return "lottery"
    if _PROMO_ONLY_SIGNALS.search(text):
        return "promo"
    if _FREE_TRIAL.search(text):
        return "trial"
    conditions = _CONDITION_NEGATION.sub(" ", text)
    if _CONDITIONAL_FREE.search(conditions):
        return "conditional"
    raw_price = metadata.get("price")
    has_price = isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool) and raw_price > 0
    has_free_claim = bool(_TRULY_FREE.search(text) or _PROMO_CREDIT.search(text))
    if has_price or (_PAID_SIGNALS.search(text) and not has_free_claim):
        return "paid"
    if has_free_claim:
        return "free"
    return "unknown"


def _infer_access_tier(text: str, acquisition_mode: str, metadata: Mapping[str, Any]) -> AccessTier:
    if acquisition_mode == "trial" or _FREE_TRIAL.search(text):
        return AccessTier.FREE_TRIAL
    if _PROMO_CREDIT.search(text):
        return AccessTier.PROMO_CREDIT
    if acquisition_mode == "conditional":
        return AccessTier.CONDITIONAL_FREE
    if acquisition_mode == "free":
        return AccessTier.TRULY_FREE
    if acquisition_mode == "paid" and _PAID_ROI.search(text):
        return AccessTier.PAID_HIGH_ROI
    return AccessTier.UNKNOWN


def _is_noise_title(title: str, summary: str, lane: OpportunityLane, metadata: Mapping[str, Any]) -> bool:
    if re.search(r"无需\s*(?:加群|进群|留\s*id|私信|加微)|不用\s*(?:加群|进群|留\s*id|私信)", title, re.IGNORECASE):
        return False
    if re.match(r"^(如何|怎么|怎样)", title) and re.search(r"教程|攻略|步骤|指南", title):
        if not re.search(r"请问|求助|有没有|失效|[?？]", title) and lane in {
            OpportunityLane.TECH_FUSION, OpportunityLane.CREATIVE_ASSET
        }:
            return False
    return bool(_NOISE_TITLE.search(title))


def _detect_traps(text: str) -> tuple[TrapFlag, ...]:
    return tuple(flag for flag, pattern in _TRAP_PATTERNS if pattern.search(text))




def _source_credibility(evidence: tuple[EvidenceRecord, ...]) -> float:
    if not evidence:
        return 0.35
    # This is a conservative source-type prior, not a factual trust verdict.
    priors = {"github": 0.62, "github_repository": 0.62, "github_catalog": 0.55, "rss": 0.48, "reddit": 0.38, "community_json": 0.4}
    values = [priors.get(item.source_type.casefold(), 0.42) for item in evidence]
    return round(min(0.75, sum(values) / len(values)), 4)

def _value_score(tier: AccessTier, lane: OpportunityLane, text: str) -> float:
    base = {
        AccessTier.TRULY_FREE: 0.96,
        AccessTier.CONDITIONAL_FREE: 0.76,
        AccessTier.FREE_TRIAL: 0.62,
        AccessTier.PROMO_CREDIT: 0.88,
        AccessTier.PAID_HIGH_ROI: 0.52,
        AccessTier.UNKNOWN: 0.35,
    }[tier]
    if lane is OpportunityLane.TECH_FUSION and re.search(r"\bMIT\b|Apache-2\.0|BSD", text, re.IGNORECASE):
        base = max(base, 0.84)
    if lane is OpportunityLane.MONETIZATION and re.search(r"paying customers|paid users|付费用户|真实订单", text, re.IGNORECASE):
        base = min(1.0, base + 0.08)
    return round(base, 4)


def _muse_fit_score(lane: OpportunityLane, text: str) -> float:
    if _MUSE_FIT.search(text):
        return 0.92 if lane in {OpportunityLane.TECH_FUSION, OpportunityLane.CREATIVE_ASSET} else 0.86
    if lane is OpportunityLane.MONETIZATION:
        return 0.7
    return 0.58


def _friction_score(
    tier: AccessTier,
    traps: tuple[TrapFlag, ...],
    acquisition_mode: str = "unknown",
    source_text: str = "",
) -> float:
    penalty = {
        AccessTier.TRULY_FREE: 0.02,
        AccessTier.CONDITIONAL_FREE: 0.12,
        AccessTier.FREE_TRIAL: 0.2,
        AccessTier.PROMO_CREDIT: 0.1,
        AccessTier.PAID_HIGH_ROI: 0.3,
        AccessTier.UNKNOWN: 0.22,
    }[tier]
    if acquisition_mode == "lottery":
        penalty += 0.28
    elif acquisition_mode == "promo":
        penalty += 0.22
    if acquisition_mode == "revoked":
        penalty = 0.95
    if _LURE_SIGNALS.search(source_text):
        penalty += 0.12
    trap_costs = {
        TrapFlag.REQUIRES_CREDIT_CARD: 0.16,
        TrapFlag.GEO_RESTRICTED: 0.14,
        TrapFlag.WAITLIST_ONLY: 0.12,
        TrapFlag.REFERRAL_BAIT: 0.16,
        TrapFlag.AUTO_RENEWAL: 0.1,
        TrapFlag.USAGE_CAP: 0.07,
        TrapFlag.STUDENT_ONLY: 0.08,
        TrapFlag.INVITE_ONLY: 0.1,
        TrapFlag.PAYMENT_METHOD_REQUIRED: 0.14,
    }
    return round(min(0.95, penalty + sum(trap_costs[item] for item in traps)), 4)






def classify_rules(raw: Mapping[str, Any]) -> ClassificationResult:
    """Pure deterministic classifier; no network, database, model or environment access."""
    if not isinstance(raw, Mapping):
        raise TypeError("candidate input must be a mapping")
    sanitized = sanitize_external_payload(raw)
    fields = sanitized.fields
    title, summary, url = fields["title"], fields["summary"], fields["url"]
    content = sanitize_external_text(raw.get("content", ""), max_chars=6000).text
    metadata_raw = raw.get("metadata", {})
    metadata = _sanitize_metadata(metadata_raw if isinstance(metadata_raw, Mapping) else {})
    for internal_key in (
        "verification_status", "verified", "verified_at", "verified_by", "approved",
        "approval_granted", "independent_source_count", "distinct_source_count",
        "credibility", "credibility_score", "license_adoption_blocker",
        "license_review_required", "semantic_lane_suggestion", "semantic_confidence",
        "semantic_reason_untrusted", "semantic_provider_used", "rule_filtered",
        "noise_filter_reason", "acquisition_mode", "paid_roi_claim_unverified",
        "acquisition_mode_requires_manual_review", "sanitizer_trust_boundary",
        "removed_instruction_segments", "secret_redactions", "source_type_prior_is_not_verification",
        "roi_verified",
    ):
        metadata.pop(internal_key, None)

    license_text = sanitize_external_text(
        raw.get("license_text", metadata.get("license_text", "")), max_chars=5000
    ).text
    explicit_license = sanitize_external_text(raw.get("license_spdx", metadata.get("license_spdx", "")), max_chars=80).text
    license_result = assess_license(license_text, explicit_license)
    evidence_raw = raw.get("evidence_chain", ())
    if not isinstance(evidence_raw, (list, tuple)):
        raise TypeError("evidence_chain must be a sequence")
    evidence = tuple(_sanitize_evidence_record(item) for item in evidence_raw)
    text = f"{title}\n{summary}\n{content}\n{license_text}"
    lane = _infer_lane(title, summary, url, metadata)
    acquisition_mode = _guess_acquisition_mode(title, summary, content, metadata)
    access_tier = _infer_access_tier(text, acquisition_mode, metadata)
    noise_filtered = _is_noise_title(title, summary, lane, metadata)
    traps = _detect_traps(text)
    expiry = raw.get("expires_at")
    status = (
        VerificationStatus.EXPIRED if acquisition_mode == "revoked"
        else VerificationStatus.SUSPICIOUS if traps
        else VerificationStatus.UNVERIFIED
    )
    if expiry:
        try:
            expiry_date = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)
            expiry_date = expiry_date.astimezone(timezone.utc)
            expiry = expiry_date.isoformat()
            if expiry_date <= datetime.now(timezone.utc):
                status = VerificationStatus.EXPIRED
        except (TypeError, ValueError, OverflowError):
            metadata["expiry_parse_warning"] = True
            expiry = None
    if lane is OpportunityLane.TECH_FUSION and license_result.risk in {
        LicenseRisk.STRONG_COPYLEFT, LicenseRisk.NETWORK_COPYLEFT, LicenseRisk.UNKNOWN, LicenseRisk.PROPRIETARY
    }:
        metadata["license_adoption_blocker"] = True
    if lane is OpportunityLane.TECH_FUSION and license_result.risk is LicenseRisk.WEAK_COPYLEFT:
        metadata["license_review_required"] = True
    if lane is OpportunityLane.TECH_FUSION and not license_text:
        metadata["license_review_required"] = True
    metadata["license_claim_unverified"] = bool(explicit_license and not license_text)

    value = _value_score(access_tier, lane, text)
    if noise_filtered:
        value = min(value, 0.12)
    credibility = _source_credibility(evidence)
    muse_fit = _muse_fit_score(lane, text)
    friction = _friction_score(access_tier, traps, acquisition_mode, f"{title}\n{summary}\n{content}")
    source_type = sanitize_external_text(raw.get("source_type", "unknown"), max_chars=80).text or "unknown"
    candidate_id = sanitize_external_text(raw.get("candidate_id", ""), max_chars=160).text
    if not candidate_id:
        candidate_id = hashlib.sha256(
            (url.casefold() + "\n" + re.sub(r"\s+", " ", title.casefold())).encode("utf-8")
        ).hexdigest()[:32]
    reasons = [
        f"lane:{lane.value}",
        f"access:{access_tier.value}",
        f"acquisition:{acquisition_mode}",
        f"license:{license_result.spdx or license_result.risk.value}",
        f"source_prior:{source_type}",
    ]
    if traps:
        reasons.extend(f"trap:{item.value}" for item in traps)
    if noise_filtered:
        reasons.append("deterministic_noise_filter")
    if sanitized.removed_instruction_segments:
        reasons.append(f"prompt_injection_segments_removed:{sanitized.removed_instruction_segments}")
    metadata.update({
        "sanitizer_trust_boundary": sanitized.trust_boundary,
        "removed_instruction_segments": sanitized.removed_instruction_segments,
        "secret_redactions": sanitized.redactions,
        "license_assessment_reason": license_result.reason,
        "license_source_text_present": bool(license_text),
        "source_type_prior_is_not_verification": True,
        "acquisition_mode": acquisition_mode,
        "rule_filtered": noise_filtered,
        "noise_filter_reason": "deterministic_title_noise" if noise_filtered else "",
        "paid_roi_claim_unverified": acquisition_mode == "paid" and access_tier is AccessTier.PAID_HIGH_ROI,
        "acquisition_mode_requires_manual_review": acquisition_mode in {"lottery", "promo", "revoked"},
    })
    candidate = OpportunityCandidate(
        candidate_id=candidate_id,
        title=title,
        summary=summary or content[:1000],
        url=url,
        lane=lane,
        access_tier=access_tier,
        verification_status=status,
        expires_at=str(expiry) if expiry else None,
        value=value,
        credibility=credibility,
        muse_fit=muse_fit,
        friction_penalty=friction,
        evidence_chain=evidence,
        traps=traps,
        license_spdx=license_result.spdx,
        license_risk=license_result.risk,
        distinct_source_count=len({item.source_id for item in evidence}),
        score_reasons=tuple(reasons),
        metadata=metadata,
    )
    return ClassificationResult(candidate, tuple(reasons), False, "rules_only")


class RuleFirstClassifier:
    """Rules always run first; model failure, timeout or invalid JSON never blocks results."""

    def __init__(
        self,
        config: RadarConfig,
        semantic_provider: SemanticProvider | None = None,
    ) -> None:
        if not isinstance(config, RadarConfig):
            raise TypeError("config must be a RadarConfig")
        if semantic_provider is not None and not isinstance(semantic_provider, SemanticProvider):
            raise TypeError("semantic_provider must implement classify_opportunity")
        self.config = config
        self.semantic_provider = semantic_provider

    async def classify(self, raw: Mapping[str, Any]) -> ClassificationResult:
        result = classify_rules(raw)
        if result.candidate.metadata.get("rule_filtered") is True:
            return replace(result, semantic_status="deterministic_noise_filter")

        if not self.config.enabled or not self.config.semantic_enabled or self.semantic_provider is None:
            return result
        try:
            semantic = await asyncio.wait_for(
                self.semantic_provider.classify_opportunity(result.candidate),
                timeout=self.config.semantic_timeout_seconds,
            )
            if not isinstance(semantic, Mapping):
                return replace(result, semantic_status="invalid_semantic_response")
            lane_raw = semantic.get("lane")
            if lane_raw is None:
                return replace(result, semantic_status="no_semantic_change")
            lane = OpportunityLane(str(lane_raw))
            confidence = semantic.get("confidence", 0.0)
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
                return replace(result, semantic_status="invalid_semantic_confidence")
            if float(confidence) < 0.8:
                return replace(result, semantic_status="semantic_confidence_below_threshold")
            # The model may suggest a lane only. It cannot certify a free offer, alter a
            # license, remove traps, increase credibility, or grant verification/approval.
            reason = sanitize_external_text(semantic.get("reason", ""), max_chars=240).text
            metadata = {
                **dict(result.candidate.metadata),
                "semantic_lane_suggestion": lane.value,
                "semantic_confidence": round(float(confidence), 3),
                "semantic_reason_untrusted": reason,
                "semantic_provider_used": True,
            }
            candidate = replace(
                result.candidate,
                lane=lane,
                metadata=metadata,
                score_reasons=tuple((*result.candidate.score_reasons, "semantic_lane_reviewed")),
                updated_at=utc_now().isoformat(),
            )
            return ClassificationResult(candidate, result.rule_reasons, True, "semantic_lane_applied")
        except asyncio.TimeoutError:
            return replace(result, semantic_status="semantic_timeout_rules_fallback")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return replace(result, semantic_status=f"semantic_error_rules_fallback:{type(exc).__name__}")
