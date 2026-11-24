from __future__ import annotations

import unittest

from muse.quote_intelligence import QuoteAuditError, audit_quotes, render_markdown


def source(file_name: str, locator: str, excerpt: str) -> dict[str, str]:
    return {"file": file_name, "locator": locator, "excerpt": excerpt}


def quote(
    quote_id: str,
    supplier: str,
    *,
    items: list[dict] | None = None,
    total_complete: bool = True,
    tax_status: str = "included",
    currency: str = "SGD",
    warranty_months: int | None = 12,
    delivery_days: int | None = 2,
    payment_terms: str | None = "30 days",
    exclusions: list[str] | None = None,
) -> dict:
    return {
        "quote_id": quote_id,
        "supplier": supplier,
        "currency": currency,
        "tax_status": tax_status,
        "total_complete": total_complete,
        "line_items": items if items is not None else [
            {
                "item_key": "unit",
                "description": "Supply and install air-conditioning unit",
                "quantity": "1",
                "unit": "job",
                "unit_price": "2800.00",
                "source": source(f"{quote_id}.pdf", "page 1 / row 1", "Air-conditioning unit: SGD 2,800.00"),
            },
            {
                "item_key": "installation",
                "description": "Installation and commissioning",
                "quantity": "1",
                "unit": "job",
                "unit_price": "500.00",
                "source": source(f"{quote_id}.pdf", "page 1 / row 2", "Installation: SGD 500.00"),
            },
        ],
        "adjustments": [],
        "terms": {
            "warranty_months": warranty_months,
            "delivery_days": delivery_days,
            "payment_terms": payment_terms,
            "exclusions": exclusions or [],
        },
    }


class QuoteIntelligenceTests(unittest.TestCase):
    def test_calculates_exact_totals_and_ranks_only_scope_aligned_quotes(self):
        first = quote("q-a", "Supplier A")
        second = quote("q-b", "Supplier B", items=[
            {
                "item_key": "unit",
                "description": "Supply and install air-conditioning unit",
                "quantity": "1",
                "unit": "job",
                "unit_price": "2500.00",
                "source": source("q-b.pdf", "page 1 / row 1", "Air-conditioning unit: SGD 2,500.00"),
            },
            {
                "item_key": "installation",
                "description": "Installation and commissioning",
                "quantity": "1",
                "unit": "job",
                "unit_price": "500.00",
                "source": source("q-b.pdf", "page 1 / row 2", "Installation: SGD 500.00"),
            },
        ],)
        second["adjustments"] = [{
            "kind": "fee",
            "name": "Site visit",
            "amount": "100.00",
            "source": source("q-b.pdf", "page 2 / row 1", "Site visit fee: SGD 100.00"),
        }]
        result = audit_quotes([first, second])
        self.assertEqual(result["quote_count"], 2)
        self.assertEqual(result["quotes"][0]["quoted_total"], "3300.00")
        self.assertEqual(result["quotes"][1]["quoted_total"], "3100.00")
        group = result["groups"][0]
        self.assertTrue(group["comparable"])
        self.assertEqual([row["quote_id"] for row in group["ranking"]], ["q-b", "q-a"])
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["automatic_selection"])

    def test_missing_scope_item_is_unknown_not_zero_and_blocks_ranking(self):
        complete = quote("q-a", "Supplier A")
        partial = quote("q-b", "Supplier B", items=[
            {
                "item_key": "unit",
                "description": "Supply and install air-conditioning unit",
                "quantity": "1",
                "unit": "job",
                "unit_price": "2500.00",
                "source": source("q-b.pdf", "page 1 / row 1", "Air-conditioning unit: SGD 2,500.00"),
            },
        ])
        result = audit_quotes([complete, partial])
        partial_result = next(row for row in result["quotes"] if row["quote_id"] == "q-b")
        self.assertEqual(partial_result["missing_scope_items"], ["installation"])
        self.assertIsNone(partial_result["quoted_total"])
        self.assertFalse(result["groups"][0]["comparable"])
        self.assertEqual(result["groups"][0]["ranking"], [])

    def test_incomplete_total_and_unknown_tax_are_never_ranked(self):
        incomplete = quote("q-a", "Supplier A", total_complete=False)
        unknown_tax = quote("q-b", "Supplier B", tax_status="unknown")
        result = audit_quotes([incomplete, unknown_tax])
        self.assertIsNone(next(row for row in result["quotes"] if row["quote_id"] == "q-a")["quoted_total"])
        self.assertFalse(result["groups"][0]["comparable"])
        self.assertEqual(result["groups"][0]["ranking"], [])

    def test_separates_currencies_instead_of_applying_unverified_fx(self):
        result = audit_quotes([
            quote("q-sgd", "Supplier SGD", currency="SGD"),
            quote("q-usd", "Supplier USD", currency="USD"),
        ])
        self.assertEqual({group["currency"] for group in result["groups"]}, {"SGD", "USD"})
        self.assertTrue(all(not group["comparable"] and group["ranking"] == [] for group in result["groups"]))

    def test_requires_source_provenance_for_every_price(self):
        invalid = quote("q-a", "Supplier A")
        invalid["line_items"][0]["source"] = None
        with self.assertRaises(QuoteAuditError):
            audit_quotes([invalid])

    def test_rejects_boolean_and_invalid_decimal_amounts(self):
        invalid_quantity = quote("q-a", "Supplier A")
        invalid_quantity["line_items"][0]["quantity"] = True
        with self.assertRaises(QuoteAuditError):
            audit_quotes([invalid_quantity])
        invalid_price = quote("q-b", "Supplier B")
        invalid_price["line_items"][0]["unit_price"] = "NaN"
        with self.assertRaises(QuoteAuditError):
            audit_quotes([invalid_price])

    def test_report_keeps_human_decision_boundary_and_source_references(self):
        result = audit_quotes([quote("q-a", "Supplier A")])
        report = render_markdown(result)
        self.assertIn("Human review required", report)
        self.assertIn("No automatic supplier selection", report)
        self.assertIn("q-a.pdf", report)
        self.assertIn("page 1 / row 1", report)


if __name__ == "__main__":
    unittest.main()
