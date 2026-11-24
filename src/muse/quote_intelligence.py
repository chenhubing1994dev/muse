"""Offline, source-grounded supplier quotation comparison for Muse.

The core accepts normalized JSON only. Document extraction adapters (PDF/XLSX/OCR)
are deliberately separate so they can be reviewed and tested without weakening
the deterministic calculation and provenance rules.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Sequence


class QuoteAuditError(ValueError):
    """Raised when quote input is incomplete, ambiguous, or unsafe to compare."""


CURRENCY_EXPONENTS = {
    "AUD": 2, "BHD": 3, "CAD": 2, "CHF": 2, "CNY": 2, "EUR": 2,
    "GBP": 2, "HKD": 2, "INR": 2, "JPY": 0, "KRW": 0, "KWD": 3,
    "MYR": 2, "NZD": 2, "SGD": 2, "THB": 2, "TWD": 2, "USD": 2,
}
TAX_STATUSES = frozenset({"included", "excluded", "not_applicable", "unknown"})
ADJUSTMENT_KINDS = frozenset({"fee", "tax", "discount"})
MAX_QUOTES = 20
MAX_LINE_ITEMS = 200
MAX_ADJUSTMENTS = 100


def _text(value: Any, name: str, *, limit: int = 500, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise QuoteAuditError(f"{name} must be non-empty text of at most {limit} characters")
    return value.strip()


def _integer(value: Any, name: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise QuoteAuditError(f"{name} must be an integer >= {minimum} or null")
    return value


def _decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise QuoteAuditError(f"{name} must be a decimal string")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise QuoteAuditError(f"{name} is not a valid decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (not positive and result < 0):
        comparator = "greater than zero" if positive else "non-negative"
        raise QuoteAuditError(f"{name} must be finite and {comparator}")
    return result


def _money(value: Any, name: str, exponent: int) -> Decimal:
    amount = _decimal(value, name)
    quantum = Decimal(1).scaleb(-exponent)
    rounded = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded != amount:
        raise QuoteAuditError(f"{name} has more fractional digits than its currency allows")
    return rounded


def _format_money(value: Decimal, exponent: int) -> str:
    return f"{value:.{exponent}f}"


def _source(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise QuoteAuditError(f"{name}.source is required for audit provenance")
    return {
        "file": _text(value.get("file"), f"{name}.source.file", limit=256),
        "locator": _text(value.get("locator"), f"{name}.source.locator", limit=256),
        "excerpt": _text(value.get("excerpt"), f"{name}.source.excerpt", limit=1200),
    }


def _normalise_quote(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise QuoteAuditError("each quote must be an object")
    quote_id = _text(raw.get("quote_id"), "quote_id", limit=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", quote_id):
        raise QuoteAuditError("quote_id contains unsupported characters")
    supplier = _text(raw.get("supplier"), f"{quote_id}.supplier", limit=200)
    currency = _text(raw.get("currency"), f"{quote_id}.currency", limit=3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency) or currency not in CURRENCY_EXPONENTS:
        raise QuoteAuditError(f"{quote_id}.currency is unsupported; provide a supported ISO currency code")
    exponent = CURRENCY_EXPONENTS[currency]
    tax_status = raw.get("tax_status")
    if tax_status not in TAX_STATUSES:
        raise QuoteAuditError(f"{quote_id}.tax_status must be one of {sorted(TAX_STATUSES)}")
    total_complete = raw.get("total_complete")
    if not isinstance(total_complete, bool):
        raise QuoteAuditError(f"{quote_id}.total_complete must be an explicit boolean")

    raw_items = raw.get("line_items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_LINE_ITEMS:
        raise QuoteAuditError(f"{quote_id}.line_items must contain 1..{MAX_LINE_ITEMS} items")
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    subtotal = Decimal(0)
    for index, item in enumerate(raw_items):
        label = f"{quote_id}.line_items[{index}]"
        if not isinstance(item, dict):
            raise QuoteAuditError(f"{label} must be an object")
        item_key = _text(item.get("item_key"), f"{label}.item_key", limit=80)
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", item_key):
            raise QuoteAuditError(f"{label}.item_key contains unsupported characters")
        if item_key in seen_keys:
            raise QuoteAuditError(f"{quote_id} contains duplicate item_key: {item_key}")
        seen_keys.add(item_key)
        description = _text(item.get("description"), f"{label}.description", limit=500)
        quantity = _decimal(item.get("quantity"), f"{label}.quantity", positive=True)
        unit = _text(item.get("unit"), f"{label}.unit", limit=40)
        unit_price = _money(item.get("unit_price"), f"{label}.unit_price", exponent)
        line_total = (quantity * unit_price).quantize(
            Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP
        )
        subtotal += line_total
        items.append({
            "item_key": item_key,
            "description": description,
            "quantity": str(quantity),
            "unit": unit,
            "unit_price": _format_money(unit_price, exponent),
            "line_total": _format_money(line_total, exponent),
            "source": _source(item.get("source"), label),
        })

    raw_adjustments = raw.get("adjustments", [])
    if not isinstance(raw_adjustments, list) or len(raw_adjustments) > MAX_ADJUSTMENTS:
        raise QuoteAuditError(f"{quote_id}.adjustments must be a list of at most {MAX_ADJUSTMENTS} items")
    adjustments: list[dict[str, Any]] = []
    adjustment_total = Decimal(0)
    for index, adjustment in enumerate(raw_adjustments):
        label = f"{quote_id}.adjustments[{index}]"
        if not isinstance(adjustment, dict):
            raise QuoteAuditError(f"{label} must be an object")
        kind = adjustment.get("kind")
        if kind not in ADJUSTMENT_KINDS:
            raise QuoteAuditError(f"{label}.kind must be one of {sorted(ADJUSTMENT_KINDS)}")
        name = _text(adjustment.get("name"), f"{label}.name", limit=200)
        amount = _money(adjustment.get("amount"), f"{label}.amount", exponent)
        signed = -amount if kind == "discount" else amount
        adjustment_total += signed
        adjustments.append({
            "kind": kind,
            "name": name,
            "amount": _format_money(amount, exponent),
            "source": _source(adjustment.get("source"), label),
        })

    terms = raw.get("terms", {})
    if not isinstance(terms, dict):
        raise QuoteAuditError(f"{quote_id}.terms must be an object")
    warranty_months = _integer(terms.get("warranty_months"), f"{quote_id}.warranty_months")
    delivery_days = _integer(terms.get("delivery_days"), f"{quote_id}.delivery_days")
    payment_terms = terms.get("payment_terms")
    if payment_terms is not None:
        payment_terms = _text(payment_terms, f"{quote_id}.payment_terms", limit=500)
    exclusions = terms.get("exclusions", [])
    if not isinstance(exclusions, list) or len(exclusions) > 100:
        raise QuoteAuditError(f"{quote_id}.exclusions must be a list of at most 100 entries")
    exclusions = [_text(value, f"{quote_id}.exclusions[{i}]", limit=500) for i, value in enumerate(exclusions)]

    risk_flags: list[str] = []
    if tax_status == "unknown":
        risk_flags.append("tax_status_unknown")
    if not total_complete:
        risk_flags.append("quoted_total_incomplete")
    if warranty_months is None:
        risk_flags.append("warranty_not_stated")
    if delivery_days is None:
        risk_flags.append("delivery_time_not_stated")
    if payment_terms is None:
        risk_flags.append("payment_terms_not_stated")
    if exclusions:
        risk_flags.append("scope_exclusions_listed")

    quoted_total = subtotal + adjustment_total if total_complete else None
    return {
        "quote_id": quote_id,
        "supplier": supplier,
        "currency": currency,
        "tax_status": tax_status,
        "total_complete": total_complete,
        "subtotal": _format_money(subtotal, exponent),
        "adjustments_total": _format_money(adjustment_total, exponent),
        "quoted_total": _format_money(quoted_total, exponent) if quoted_total is not None else None,
        "line_items": items,
        "item_keys": sorted(seen_keys),
        "adjustments": adjustments,
        "terms": {
            "warranty_months": warranty_months,
            "delivery_days": delivery_days,
            "payment_terms": payment_terms,
            "exclusions": exclusions,
        },
        "missing_scope_items": [],
        "risk_flags": risk_flags,
    }


def audit_quotes(quotes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Validate and compare normalized quotes without guessing missing costs or scope."""
    if not isinstance(quotes, (list, tuple)) or not 1 <= len(quotes) <= MAX_QUOTES:
        raise QuoteAuditError(f"quotes must contain 1..{MAX_QUOTES} quote objects")
    normalised = [_normalise_quote(item) for item in quotes]
    ids = [item["quote_id"] for item in normalised]
    if len(ids) != len(set(ids)):
        raise QuoteAuditError("quote_id values must be unique")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in normalised:
        grouped[(item["currency"], item["tax_status"])].append(item)

    groups: list[dict[str, Any]] = []
    for (currency, tax_status), members in sorted(grouped.items()):
        scope_union = set().union(*(set(item["item_keys"]) for item in members))
        for item in members:
            missing = sorted(scope_union - set(item["item_keys"]))
            item["missing_scope_items"] = missing
            if missing:
                item["risk_flags"].append("scope_items_missing")
                item["quoted_total"] = None

        reasons: list[str] = []
        if len(members) < 2:
            reasons.append("fewer_than_two_quotes")
        if tax_status == "unknown":
            reasons.append("tax_status_unknown")
        if any(not item["total_complete"] for item in members):
            reasons.append("one_or_more_totals_incomplete")
        if any(item["missing_scope_items"] for item in members):
            reasons.append("scope_mismatch")
        comparable = not reasons
        ranking = []
        if comparable:
            ranking = [
                {"rank": index, "quote_id": item["quote_id"], "supplier": item["supplier"], "quoted_total": item["quoted_total"]}
                for index, item in enumerate(
                    sorted(members, key=lambda row: (Decimal(row["quoted_total"]), row["supplier"].casefold())),
                    start=1,
                )
            ]
        groups.append({
            "currency": currency,
            "tax_status": tax_status,
            "quote_ids": sorted(item["quote_id"] for item in members),
            "comparable": comparable,
            "comparison_reasons": reasons,
            "ranking": ranking,
        })

    normalised.sort(key=lambda item: item["quote_id"])
    return {
        "schema_version": 1,
        "quote_count": len(normalised),
        "quotes": normalised,
        "groups": groups,
        "human_review_required": True,
        "automatic_selection": False,
        "policy": {
            "missing_prices_are_unknown_not_zero": True,
            "currency_conversion_performed": False,
            "fuzzy_scope_matching_performed": False,
            "source_provenance_required": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise human-review report with source traceability."""
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise QuoteAuditError("unsupported quote audit report")
    lines = [
        "# Muse Quote Intelligence",
        "",
        "- Human review required: **yes**",
        "- No automatic supplier selection.",
        "- Missing prices and scope items are treated as unknown, never as zero.",
        "",
        "## Comparison groups",
        "",
    ]
    for group in report.get("groups", []):
        lines.extend([
            f"### {group['currency']} · tax status: {group['tax_status']}",
            "",
            f"- Comparable: **{'yes' if group['comparable'] else 'no'}**",
            f"- Reasons: {', '.join(group['comparison_reasons']) or 'none'}",
        ])
        if group["ranking"]:
            lines.extend(["", "| Rank | Supplier | Quote ID | Quoted total |", "|---:|---|---|---:|"])
            quote_map = {item["quote_id"]: item for item in report["quotes"]}
            for row in group["ranking"]:
                supplier = quote_map[row["quote_id"]]["supplier"].replace("|", "\\|")
                lines.append(f"| {row['rank']} | {supplier} | {row['quote_id']} | {row['quoted_total']} {group['currency']} |")
        lines.append("")

    lines.extend(["## Quote details", ""])
    for item in report.get("quotes", []):
        lines.extend([
            f"### {item['supplier']} ({item['quote_id']})",
            "",
            f"- Currency / tax: {item['currency']} / {item['tax_status']}",
            f"- Subtotal: {item['subtotal']} {item['currency']}",
            f"- Adjustments: {item['adjustments_total']} {item['currency']}",
            f"- Quoted total: {item['quoted_total'] if item['quoted_total'] is not None else 'unknown / not comparable'}",
            f"- Missing scope items: {', '.join(item['missing_scope_items']) or 'none identified'}",
            f"- Risk flags: {', '.join(item['risk_flags']) or 'none identified'}",
            "",
            "Price source references:",
        ])
        for line_item in item["line_items"]:
            ref = line_item["source"]
            excerpt = ref["excerpt"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"- {line_item['item_key']}: {ref['file']} · {ref['locator']} — {excerpt}")
        for adjustment in item["adjustments"]:
            ref = adjustment["source"]
            excerpt = ref["excerpt"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"- {adjustment['kind']} {adjustment['name']}: {ref['file']} · {ref['locator']} — {excerpt}")
        lines.append("")
    lines.extend([
        "## Decision boundary",
        "",
        "This report is a comparison aid, not procurement approval. Confirm original documents, scope, tax treatment, exclusions, supplier credentials, and contract terms before making a decision.",
        "",
    ])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit normalized supplier quotes offline.")
    parser.add_argument("input", help="UTF-8 JSON file containing a top-level quotes array")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", help="Optional output path; stdout is used when omitted")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("quotes"), list):
            raise QuoteAuditError("input JSON must contain a top-level quotes array")
        report = audit_quotes(raw["quotes"])
        rendered = (
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
            if args.format == "json" else render_markdown(report)
        )
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0
    except (OSError, json.JSONDecodeError, QuoteAuditError) as exc:
        print(f"quote audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
