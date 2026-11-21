from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from muse.usage_ledger import UsageLedger


class UsageLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "usage.sqlite3"
        self.ledger = UsageLedger(self.database)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def test_records_provider_reported_usage_with_attribution_and_persists(self) -> None:
        event = self.ledger.record_usage(
            "lm-studio-local", "gemma-4",
            token_source="provider_reported",
            input_tokens=120, output_tokens=35, total_tokens=155,
            task_id="task-1", agent_id="researcher", project_id="muse",
            request_id="req-1",
        )
        self.assertEqual(event["token_source"], "provider_reported")
        self.assertEqual(event["attribution_status"], "complete")
        self.assertEqual(event["total_tokens"], 155)
        self.assertNotIn("messages", event)
        self.ledger.close()
        with UsageLedger(self.database) as reopened:
            rows = reopened.list_events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], event["event_id"])
        self.assertEqual(rows[0]["agent_id"], "researcher")

    def test_unknown_usage_is_recorded_as_unknown_not_zero(self) -> None:
        event = self.ledger.record_usage(
            "lm-studio-local", "unknown",
            token_source="not_reported",
            task_id="task-2",
        )
        self.assertIsNone(event["input_tokens"])
        self.assertIsNone(event["output_tokens"])
        self.assertIsNone(event["total_tokens"])
        self.assertEqual(event["attribution_status"], "partial")

    def test_estimates_are_explicitly_distinguished_from_provider_measurements(self) -> None:
        event = self.ledger.record_usage(
            "local-estimator", "heuristic",
            token_source="estimated",
            input_tokens=100, output_tokens=20,
        )
        self.assertEqual(event["token_source"], "estimated")

    def test_actual_cost_requires_amount_currency_and_source(self) -> None:
        event = self.ledger.record_usage(
            "provider-x", "model-y",
            token_source="provider_reported",
            input_tokens=10, output_tokens=5, total_tokens=15,
            cost_amount="0.001250", cost_currency="USD",
            cost_kind="actual", cost_source="provider_response",
        )
        self.assertEqual(event["cost_amount"], "0.00125")
        self.assertEqual(event["cost_currency"], "USD")
        self.assertEqual(event["cost_kind"], "actual")

    def test_estimated_cost_is_explicit_and_never_confused_with_actual(self) -> None:
        event = self.ledger.record_usage(
            "provider-x", "model-y",
            token_source="estimated",
            input_tokens=10, output_tokens=5,
            cost_amount="0.002", cost_currency="EUR",
            cost_kind="estimated", cost_source="provider-price-catalog",
            price_version="v3",
        )
        self.assertEqual(event["cost_kind"], "estimated")
        self.assertEqual(event["cost_currency"], "EUR")
        self.assertEqual(event["price_version"], "v3")

    def test_rejects_invalid_tokens_and_costs(self) -> None:
        with self.assertRaises(ValueError):
            self.ledger.record_usage("p", "m", token_source="provider_reported", input_tokens=True)
        with self.assertRaises(ValueError):
            self.ledger.record_usage("p", "m", token_source="provider_reported", total_tokens=-1)
        with self.assertRaises(ValueError):
            self.ledger.record_usage("p", "m", token_source="not_reported", total_tokens=0)
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="provider_reported",
                cost_amount="1.0", cost_currency="USD", cost_kind="actual",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="provider_reported",
                cost_amount="-0.1", cost_currency="USD",
                cost_kind="actual", cost_source="provider_response",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="provider_reported",
                cost_amount="1.0", cost_currency="US",
                cost_kind="actual", cost_source="provider_response",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="provider_reported",
                cost_amount=0.1, cost_currency="USD",
                cost_kind="actual", cost_source="provider_response",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="estimated",
                cost_amount="0.1", cost_currency="USD",
                cost_kind="estimated", cost_source="catalog",
            )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p" * 257, "m", token_source="not_reported",
            )

    def test_rejects_naive_observation_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", token_source="not_reported",
                observed_at="2026-11-21T12:00:00",
            )

    def test_ledger_is_append_only_at_database_boundary(self) -> None:
        event = self.ledger.record_usage("p", "m", token_source="not_reported")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._connection.execute(
                "UPDATE model_usage_events SET model='tampered' WHERE event_id=?",
                (event["event_id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._connection.execute(
                "DELETE FROM model_usage_events WHERE event_id=?",
                (event["event_id"],),
            )

    def test_stable_event_id_is_idempotent_and_conflicts_on_changed_payload(self) -> None:
        first = self.ledger.record_usage(
            "provider-x", "model-y", event_id="stable-event-1",
            token_source="provider_reported", total_tokens=42,
        )
        replay = self.ledger.record_usage(
            "provider-x", "model-y", event_id="stable-event-1",
            token_source="provider_reported", total_tokens=42,
        )
        self.assertEqual(first, replay)
        self.assertEqual(len(self.ledger.list_events()), 1)
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "provider-x", "model-y", event_id="stable-event-1",
                token_source="provider_reported", total_tokens=43,
            )
        self.assertEqual(len(self.ledger.list_events()), 1)

    def test_stable_event_id_checks_explicit_observation_timestamp(self) -> None:
        self.ledger.record_usage(
            "p", "m", event_id="stable-event-2", token_source="not_reported",
            observed_at="2026-11-21T12:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            self.ledger.record_usage(
                "p", "m", event_id="stable-event-2", token_source="not_reported",
                observed_at="2026-11-21T13:00:00+00:00",
            )

    def test_filters_and_limit_are_bounded(self) -> None:
        self.ledger.record_usage("p1", "m1", token_source="not_reported", agent_id="a1")
        self.ledger.record_usage("p2", "m2", token_source="not_reported", agent_id="a2")
        self.assertEqual(len(self.ledger.list_events(agent_id="a1")), 1)
        self.assertEqual(len(self.ledger.list_events(limit=1)), 1)
        with self.assertRaises(ValueError):
            self.ledger.list_events(limit=0)


if __name__ == "__main__":
    unittest.main()
