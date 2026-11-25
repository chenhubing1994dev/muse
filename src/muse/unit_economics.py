"""Unit economics & experiment simulator for Muse value hypotheses (WS7).

Pure, deterministic Decimal arithmetic. Nothing here is a forecast: prices are
``price_hypothesis`` values, volumes are scenarios, and every result carries the
label ``simulation_not_forecast``. A hypothesis cannot be scored until mandatory
counter-evidence (free alternatives, switching friction, distribution difficulty,
compliance risk) has been written down, and every experiment must declare kill
criteria *before* it runs so results cannot be rationalised afterwards.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Mapping

COUNTER_EVIDENCE_KEYS = ("free_alternatives", "switching_friction", "distribution_difficulty", "compliance_risk")
KILL_METRICS = frozenset({"visitors", "leads", "settled_orders", "refund_rate", "net_contribution_minor"})
CENT = Decimal("0.01")


class UnitEconomicsError(ValueError):
    """Input is incomplete or would produce a misleading simulation."""


def _decimal(value: Any, name: str, *, minimum: Decimal = Decimal(0), maximum: Decimal | None = None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise UnitEconomicsError(f"{name} must be an integer or decimal string (floats are refused)")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise UnitEconomicsError(f"{name} is not a valid decimal") from exc
    if not result.is_finite() or result < minimum or (maximum is not None and result > maximum):
        raise UnitEconomicsError(f"{name} is outside its allowed range")
    return result


def _text(value: Any, name: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise UnitEconomicsError(f"{name} must be non-empty text of at most {limit} characters")
    return value.strip()


def require_counter_evidence(counter_evidence: Any) -> dict[str, str]:
    """All four counter-evidence dimensions must be answered explicitly (\"none found\" is an answer)."""
    if not isinstance(counter_evidence, Mapping):
        raise UnitEconomicsError("counter_evidence is mandatory before any opportunity is scored")
    missing = [key for key in COUNTER_EVIDENCE_KEYS if key not in counter_evidence]
    extra = sorted(set(counter_evidence) - set(COUNTER_EVIDENCE_KEYS))
    if missing or extra:
        raise UnitEconomicsError(f"counter_evidence must contain exactly {list(COUNTER_EVIDENCE_KEYS)}; "
                                 f"missing={missing} unexpected={extra}")
    return {key: _text(counter_evidence[key], f"counter_evidence.{key}") for key in COUNTER_EVIDENCE_KEYS}


def _kill_criteria(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
        raise UnitEconomicsError("kill_criteria must list 1..10 pre-registered stop rules")
    criteria = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or set(item) != {"metric", "operator", "threshold", "by_day"}:
            raise UnitEconomicsError(f"kill_criteria[{index}] needs exactly metric/operator/threshold/by_day")
        if item["metric"] not in KILL_METRICS or item["operator"] not in {"<", "<=", ">", ">="}:
            raise UnitEconomicsError(f"kill_criteria[{index}] metric/operator unsupported")
        day = item["by_day"]
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 365:
            raise UnitEconomicsError(f"kill_criteria[{index}].by_day must be 1..365")
        criteria.append({"metric": item["metric"], "operator": item["operator"],
                         "threshold": str(_decimal(item["threshold"], f"kill_criteria[{index}].threshold",
                                                   minimum=Decimal("-1000000000"))),
                         "by_day": day})
    return criteria


def simulate(hypothesis: Mapping[str, Any]) -> dict[str, Any]:
    """Per-unit contribution, break-even units and scenario table for one priced hypothesis.

    Required keys: title, price_hypothesis, currency, payment_fee_rate, payment_fee_fixed,
    platform_fee_rate, refund_rate, compute_cost_per_unit, build_hours, hourly_cost,
    fixed_costs, scenario_units (list), counter_evidence, kill_criteria.
    """
    if not isinstance(hypothesis, Mapping):
        raise UnitEconomicsError("hypothesis must be a mapping")
    title = _text(hypothesis.get("title"), "title", 200)
    currency = hypothesis.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise UnitEconomicsError("currency must be an ISO-4217 code")
    counter = require_counter_evidence(hypothesis.get("counter_evidence"))
    kill = _kill_criteria(hypothesis.get("kill_criteria"))
    price = _decimal(hypothesis.get("price_hypothesis"), "price_hypothesis", minimum=Decimal("0.01"))
    pay_rate = _decimal(hypothesis.get("payment_fee_rate"), "payment_fee_rate", maximum=Decimal("0.5"))
    pay_fixed = _decimal(hypothesis.get("payment_fee_fixed"), "payment_fee_fixed")
    platform_rate = _decimal(hypothesis.get("platform_fee_rate"), "platform_fee_rate", maximum=Decimal("0.9"))
    refund_rate = _decimal(hypothesis.get("refund_rate"), "refund_rate", maximum=Decimal("1"))
    compute = _decimal(hypothesis.get("compute_cost_per_unit"), "compute_cost_per_unit")
    build_hours = _decimal(hypothesis.get("build_hours"), "build_hours", maximum=Decimal("10000"))
    hourly = _decimal(hypothesis.get("hourly_cost"), "hourly_cost")
    fixed = _decimal(hypothesis.get("fixed_costs"), "fixed_costs")
    units = hypothesis.get("scenario_units")
    if not isinstance(units, list) or not 1 <= len(units) <= 12:
        raise UnitEconomicsError("scenario_units must list 1..12 integer volumes")
    volumes = []
    for index, value in enumerate(units):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000:
            raise UnitEconomicsError(f"scenario_units[{index}] must be an integer volume")
        volumes.append(value)

    expected_refund = price * refund_rate
    fees = price * pay_rate + pay_fixed + price * platform_rate
    contribution = (price - expected_refund - fees - compute).quantize(CENT, rounding=ROUND_HALF_UP)
    upfront = (build_hours * hourly + fixed).quantize(CENT, rounding=ROUND_HALF_UP)
    if contribution <= 0:
        break_even: int | None = None
        verdict = "unviable_each_sale_loses_money"
    else:
        break_even = int((upfront / contribution).to_integral_value(rounding=ROUND_CEILING)) if upfront > 0 else 0
        verdict = "viable_if_break_even_volume_is_reachable"
    scenarios = [{"units": volume,
                  "net_contribution": str((contribution * volume - upfront).quantize(CENT, rounding=ROUND_HALF_UP))}
                 for volume in volumes]
    body = {
        "title": title, "currency": currency, "price_hypothesis": str(price),
        "contribution_per_unit": str(contribution), "upfront_cost": str(upfront),
        "break_even_units": break_even, "verdict": verdict, "scenarios": scenarios,
        "counter_evidence": counter, "kill_criteria": kill,
    }
    body["simulation_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    body.update({
        "label": "simulation_not_forecast",
        "price_label": "price_hypothesis",
        "revenue_recognised": "0",  # only revenue_ledger settlements count
    })
    return body


def evaluate_kill_criteria(simulation: Mapping[str, Any], observations: Mapping[str, Any], *, day: int) -> dict[str, Any]:
    """Apply the pre-registered stop rules to observed metrics on a given experiment day."""
    if isinstance(day, bool) or not isinstance(day, int) or day < 1:
        raise UnitEconomicsError("day must be a positive integer")
    if not isinstance(observations, Mapping) or set(observations) - KILL_METRICS:
        raise UnitEconomicsError("observations may only contain supported metrics")
    triggered, pending = [], []
    for rule in simulation.get("kill_criteria", []):
        if day < rule["by_day"]:
            pending.append(rule)
            continue
        if rule["metric"] not in observations:
            triggered.append({**rule, "reason": "metric_not_measured_by_deadline"})
            continue
        observed = _decimal(observations[rule["metric"]], f"observations.{rule['metric']}", minimum=Decimal("-1000000000"))
        threshold = Decimal(rule["threshold"])
        hit = {"<": observed < threshold, "<=": observed <= threshold,
               ">": observed > threshold, ">=": observed >= threshold}[rule["operator"]]
        if hit:
            triggered.append({**rule, "observed": str(observed), "reason": "threshold_hit"})
    return {"day": day, "decision": "kill" if triggered else "continue",
            "triggered": triggered, "pending": pending, "requires_human_confirmation": True}
