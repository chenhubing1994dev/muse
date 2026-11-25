"""WS7: accounting invariants of the settled-revenue / net-contribution ledger."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.revenue_ledger import (
    LedgerError,
    RevenueLedger,
    VerifiedReceipt,
    WebhookRejected,
    WebhookVerifier,
    format_money,
    verify_provider_statement,
)

SECRET = b"whsec_ephemeral_test_secret_0001"  # clearly labelled ephemeral test secret (gate G9)


class RevenueLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = RevenueLedger(self.root / "ledger.sqlite3")
        self.now = 1_800_000_000
        self.verifier = WebhookVerifier("lemonsqueezy-test", SECRET, clock=lambda: self.now)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def _webhook(self, event_id, event_type, amount=None, currency="USD", timestamp=None):
        data = {"product_ref": "kit-v011"}
        if amount is not None:
            data.update({"amount_minor": amount, "currency": currency})
        body = json.dumps({"id": event_id, "type": event_type, "data": data}).encode("utf-8")
        return body, WebhookVerifier.sign(SECRET, body, self.now if timestamp is None else timestamp)

    def test_empty_ledger_reports_exact_zero(self):
        summary = self.ledger.summary()
        self.assertEqual(summary["settled_revenue_display"], "US$0.00")
        self.assertEqual(summary["net_contribution_minor"], 0)
        self.assertEqual(summary["status_label"], "NO_SETTLED_REVENUE_YET")

    def test_signals_and_pending_orders_never_become_revenue(self):
        for index, kind in enumerate(("view", "like", "lead", "inquiry", "cart_checkout", "pending_order")):
            self.ledger.record_signal(kind, source="tiktok-import", external_id=f"sig-{index}", quantity=1000)
        body, header = self._webhook("evt_checkout_1", "checkout.session.completed", amount=900)
        self.ledger.ingest_webhook(self.verifier, body, header)
        summary = self.ledger.summary()
        self.assertEqual(summary["settled_revenue_minor"], 0)
        self.assertEqual(summary["signals_not_revenue"]["view"], 1000)
        self.assertEqual(summary["signals_not_revenue"]["pending_order"], 1001)
        with self.assertRaises(LedgerError):
            self.ledger.record_signal("settlement", source="x", external_id="y")

    def test_settlement_requires_verified_receipt(self):
        with self.assertRaisesRegex(LedgerError, "verified settlement receipt"):
            self.ledger.record_money("settlement", amount_minor=900, currency="USD", source="manual", external_id="s1")
        with self.assertRaises(LedgerError):
            VerifiedReceipt("hmac_webhook", "manual", "s1", "0" * 64, object())
        connection = self.ledger.connection
        with self.assertRaises(sqlite3.IntegrityError):  # even raw SQL cannot bypass the receipt rule
            connection.execute("INSERT INTO ledger_entries(kind,amount_minor,currency,source,external_id,created_at,"
                               "prev_hash,row_hash) VALUES('settlement',900,'USD','raw','r1','t','0','h')")
        self.assertEqual(self.ledger.summary()["settled_revenue_minor"], 0)

    def test_net_contribution_formula_with_verified_webhooks_and_statement(self):
        for event_id, event_type, amount in (("evt_s1", "balance.available", 900), ("evt_s2", "payout.paid", 700),
                                             ("evt_r1", "charge.refunded", 300), ("evt_c1", "dispute.created", 100)):
            body, header = self._webhook(event_id, event_type, amount=amount)
            self.ledger.ingest_webhook(self.verifier, body, header)
        statement = self.root / "statement.csv"
        statement.write_text("date,amount\n2026-11-25,5.00\n", encoding="utf-8")
        digest = hashlib.sha256(statement.read_bytes()).hexdigest()
        receipt = verify_provider_statement(statement, expected_sha256=digest, provider="gumroad", external_id="gr-1")
        self.ledger.record_money("settlement", amount_minor=500, currency="USD", source="gumroad",
                                 external_id="gr-1", receipt=receipt)
        self.ledger.record_money("payment_fee", amount_minor=95, currency="USD", source="gumroad", external_id="fee-1")
        self.ledger.record_money("platform_fee", amount_minor=50, currency="USD", source="gumroad", external_id="fee-2")
        self.ledger.record_money("compute_cost", amount_minor=40, currency="USD", source="local-gpu", external_id="gpu-1")
        self.ledger.record_money("tool_cost", amount_minor=10, currency="USD", source="tools", external_id="tool-1")
        summary = self.ledger.summary()
        self.assertEqual(summary["settled_revenue_minor"], 2100)
        self.assertEqual(summary["net_contribution_minor"], 2100 - 300 - 100 - 145 - 50)
        self.assertEqual(summary["net_contribution_display"], "US$15.05")
        self.assertEqual(summary["verified_settlements"], 3)
        self.assertTrue(summary["chain_intact"])

    def test_forged_stale_malformed_and_unmapped_webhooks_are_rejected(self):
        body, header = self._webhook("evt_x", "balance.available", amount=900)
        forged = WebhookVerifier.sign(b"attacker_secret_attacker_secret", body, self.now)
        with self.assertRaisesRegex(WebhookRejected, "signature"):
            self.ledger.ingest_webhook(self.verifier, body, forged)
        stale_body, stale_header = self._webhook("evt_old", "balance.available", amount=900, timestamp=self.now - 3600)
        with self.assertRaisesRegex(WebhookRejected, "tolerance"):
            self.ledger.ingest_webhook(self.verifier, stale_body, stale_header)
        garbage = b"{not json"
        with self.assertRaisesRegex(WebhookRejected, "JSON"):
            self.ledger.ingest_webhook(self.verifier, garbage, WebhookVerifier.sign(SECRET, garbage, self.now))
        odd_body, odd_header = self._webhook("evt_odd", "customer.updated")
        with self.assertRaisesRegex(WebhookRejected, "unmapped"):
            self.ledger.ingest_webhook(self.verifier, odd_body, odd_header)
        with self.assertRaises(WebhookRejected):
            self.ledger.ingest_webhook(self.verifier, body, body.decode()[:10])
        self.assertEqual(self.ledger.summary()["settled_revenue_minor"], 0)

    def test_webhook_replay_is_idempotent_and_conflicts_are_rejected(self):
        body, header = self._webhook("evt_s1", "balance.available", amount=900)
        first = self.ledger.ingest_webhook(self.verifier, body, header)
        second = self.ledger.ingest_webhook(self.verifier, body, header)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.ledger.summary()["settled_revenue_minor"], 900)
        other_body, other_header = self._webhook("evt_s1", "balance.available", amount=99_900)
        with self.assertRaisesRegex(LedgerError, "idempotency conflict"):
            self.ledger.ingest_webhook(self.verifier, other_body, other_header)

    def test_statement_hash_mismatch_is_rejected(self):
        statement = self.root / "statement.csv"
        statement.write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "does not match"):
            verify_provider_statement(statement, expected_sha256="0" * 64, provider="gumroad", external_id="g")

    def test_ledger_is_append_only_and_hash_chained(self):
        self.ledger.record_signal("view", source="import", external_id="v1", quantity=5)
        with self.assertRaises(sqlite3.DatabaseError):
            self.ledger.connection.execute("UPDATE ledger_entries SET quantity=999")
        with self.assertRaises(sqlite3.DatabaseError):
            self.ledger.connection.execute("DELETE FROM ledger_entries")
        self.assertTrue(self.ledger.verify_chain())

    def test_money_validation_and_formatting(self):
        for bad in (-1, 1.5, True, "900"):
            with self.assertRaises(LedgerError):
                self.ledger.record_money("refund", amount_minor=bad, currency="USD", source="s", external_id=f"b{bad!s}")
        with self.assertRaises(LedgerError):
            self.ledger.record_money("refund", amount_minor=1, currency="usd", source="s", external_id="lower")
        self.assertEqual(format_money(0, "USD"), "US$0.00")
        self.assertEqual(format_money(123456, "HKD"), "HK$1,234.56")
        self.assertEqual(format_money(-5, "USD"), "-US$0.05")


if __name__ == "__main__":
    unittest.main()
