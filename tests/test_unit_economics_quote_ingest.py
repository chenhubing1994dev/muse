"""WS7 B393: unit economics simulator + stdlib quote extraction feeding quote_intelligence."""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from muse.quote_ingest import QuoteExtractionError, build_quote, extract_csv, extract_document, extract_pdf_text, extract_xlsx
from muse.quote_intelligence import audit_quotes
from muse.unit_economics import UnitEconomicsError, evaluate_kill_criteria, simulate

COUNTER = {
    "free_alternatives": "CapCut templates are free but not Cantonese-specific",
    "switching_friction": "Creators already have a workflow",
    "distribution_difficulty": "No audience yet; relies on organic posts",
    "compliance_risk": "Music licensing must be checked per asset",
}
KILL = [{"metric": "settled_orders", "operator": "<", "threshold": "3", "by_day": 14}]


def _hypothesis(**overrides):
    base = {
        "title": "Cantonese subtitle kit", "currency": "USD", "price_hypothesis": "9",
        "payment_fee_rate": "0.05", "payment_fee_fixed": "0.50", "platform_fee_rate": "0.0",
        "refund_rate": "0.10", "compute_cost_per_unit": "0.20", "build_hours": "6", "hourly_cost": "0",
        "fixed_costs": "20", "scenario_units": [0, 5, 50], "counter_evidence": dict(COUNTER), "kill_criteria": list(KILL),
    }
    base.update(overrides)
    return base


def _xlsx(rows):
    shared, index = [], {}
    def ref(value):
        if value not in index:
            index[value] = len(shared); shared.append(value)
        return index[value]
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            col = chr(65 + c)
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{col}{r}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{col}{r}" t="s"><v>{ref(value)}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buffer.getvalue()


def _pdf(lines, compress=True):
    content = b"BT " + b" ".join(b"(" + line.encode("latin-1") + b") Tj T*" for line in lines) + b" ET"
    if compress:
        content = zlib.compress(content)
        header = b"<< /Length %d /Filter /FlateDecode >>" % len(content)
    else:
        header = b"<< /Length %d >>" % len(content)
    return b"%PDF-1.4\n1 0 obj\n" + header + b"\nstream\n" + content + b"\nendstream\nendobj\n%%EOF"


class UnitEconomicsTests(unittest.TestCase):
    def test_contribution_break_even_and_labels(self):
        result = simulate(_hypothesis())
        # 9 - 0.90 refund - (0.45 + 0.50) fees - 0.20 compute = 6.95
        self.assertEqual(result["contribution_per_unit"], "6.95")
        self.assertEqual(result["break_even_units"], 3)  # ceil(20 / 6.95)
        self.assertEqual(result["scenarios"][0], {"units": 0, "net_contribution": "-20.00"})
        self.assertEqual((result["label"], result["price_label"], result["revenue_recognised"]),
                         ("simulation_not_forecast", "price_hypothesis", "0"))
        self.assertEqual(simulate(_hypothesis())["simulation_sha256"], result["simulation_sha256"])

    def test_loss_making_unit_is_flagged_unviable(self):
        result = simulate(_hypothesis(price_hypothesis="1", compute_cost_per_unit="2"))
        self.assertIsNone(result["break_even_units"])
        self.assertEqual(result["verdict"], "unviable_each_sale_loses_money")

    def test_counter_evidence_and_kill_criteria_are_mandatory(self):
        with self.assertRaisesRegex(UnitEconomicsError, "counter_evidence"):
            simulate(_hypothesis(counter_evidence=None))
        partial = dict(COUNTER); partial.pop("compliance_risk")
        with self.assertRaisesRegex(UnitEconomicsError, "missing"):
            simulate(_hypothesis(counter_evidence=partial))
        with self.assertRaisesRegex(UnitEconomicsError, "kill_criteria"):
            simulate(_hypothesis(kill_criteria=[]))

    def test_floats_and_out_of_range_inputs_are_refused(self):
        for overrides in ({"price_hypothesis": 9.0}, {"refund_rate": "1.5"}, {"scenario_units": [-1]},
                          {"currency": "usd"}, {"price_hypothesis": "NaN"}):
            with self.assertRaises(UnitEconomicsError, msg=overrides):
                simulate(_hypothesis(**overrides))

    def test_kill_criteria_evaluation_and_missing_measurement(self):
        result = simulate(_hypothesis())
        self.assertEqual(evaluate_kill_criteria(result, {"settled_orders": 1}, day=7)["decision"], "continue")
        self.assertEqual(evaluate_kill_criteria(result, {"settled_orders": 1}, day=14)["decision"], "kill")
        self.assertEqual(evaluate_kill_criteria(result, {"settled_orders": 5}, day=14)["decision"], "continue")
        missing = evaluate_kill_criteria(result, {}, day=20)
        self.assertEqual(missing["triggered"][0]["reason"], "metric_not_measured_by_deadline")
        with self.assertRaises(UnitEconomicsError):
            evaluate_kill_criteria(result, {"views": 1_000_000}, day=20)  # views are never a kill/success metric


class QuoteIngestTests(unittest.TestCase):
    def test_csv_with_chinese_headers_and_currency_symbols_feeds_audit(self):
        data = "品名,数量,单位,单价\n剪辑服务,2,次,\"¥1,200.00\"\n字幕校对,1,项,300\n".encode("utf-8-sig")
        extraction = extract_csv(data, "quote-a.csv")
        self.assertEqual([item["unit_price"] for item in extraction["line_items"]], ["1200", "300"])
        self.assertEqual(extraction["line_items"][0]["source"]["locator"], "row 2")
        quote = build_quote(extraction, quote_id="A", supplier="Studio A", currency="CNY", tax_status="included",
                            total_complete=True)
        report = audit_quotes([quote])
        self.assertEqual(report["quotes"][0]["quoted_total"], "2700.00")
        self.assertEqual(report["quotes"][0]["line_items"][1]["source"]["locator"], "row 3")
        self.assertEqual(quote["extraction"]["confidence"], "high")

    def test_xlsx_shared_strings_and_numbers_with_cell_anchors(self):
        data = _xlsx([["Quote from B"], ["Item", "Description", "Qty", "Unit", "Unit Price"],
                      ["edit", "Video edit", 2, "job", 1100], ["subs", "Subtitles", 1, "job", 250.5]])
        extraction = extract_xlsx(data, "quote-b.xlsx")
        self.assertEqual(len(extraction["line_items"]), 2)
        self.assertEqual(extraction["line_items"][1]["source"]["locator"], "Sheet1!A4")
        self.assertEqual(extraction["line_items"][1]["unit_price"], "250.5")
        self.assertEqual(extraction["confidence"], "high")

    def test_text_pdf_is_low_confidence_and_scanned_pdf_needs_ocr(self):
        extraction = extract_pdf_text(_pdf(["Quotation", "Video edit 2 job 1100.00", "Subtitles 1 job $250"]), "q.pdf")
        self.assertEqual(extraction["confidence"], "low")
        self.assertEqual([item["quantity"] for item in extraction["line_items"]], ["2", "1"])
        plain = extract_pdf_text(_pdf(["Video edit 2 job 1100.00"], compress=False), "q2.pdf")
        self.assertEqual(len(plain["line_items"]), 1)
        scanned = b"%PDF-1.4\n<< /Subtype /Image /Length 3 >>\nstream\nabc\nendstream\n%%EOF"
        self.assertTrue(extract_pdf_text(scanned, "scan.pdf")["ocr_needed"])

    def test_hostile_documents_are_rejected(self):
        with self.assertRaises(QuoteExtractionError):
            extract_csv(b"a,b,c\n1,2,3\n", "x.csv")  # no recognisable header
        with self.assertRaises(QuoteExtractionError):
            extract_xlsx(b"not a zip", "x.xlsx")
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", b"<a>" + b" " * 5_000_000 + b"</a>")
        with self.assertRaisesRegex(QuoteExtractionError, "bomb"):
            extract_xlsx(bomb.getvalue(), "bomb.xlsx")
        with self.assertRaises(QuoteExtractionError):
            extract_pdf_text(b"<html>", "fake.pdf")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quote.docx"
            path.write_bytes(b"x")
            with self.assertRaises(QuoteExtractionError):
                extract_document(path)
            with self.assertRaises(QuoteExtractionError):
                build_quote({"line_items": []}, quote_id="x", supplier="s", currency="USD")


if __name__ == "__main__":
    unittest.main()
