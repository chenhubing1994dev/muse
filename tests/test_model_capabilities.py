from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from muse.model_capabilities import ModelCapabilityStore, run_offline_prompt_benchmark


class ModelCapabilityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "model-capabilities.sqlite3"
        self.store = ModelCapabilityStore(self.database)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_unrecorded_capabilities_are_explicitly_unknown(self) -> None:
        profile = self.store.get_profile("claude-opus", model_family="anthropic")
        self.assertEqual(profile["model_id"], "claude-opus")
        self.assertEqual(profile["model_family"], "anthropic")
        self.assertEqual(profile["capabilities"]["context_window_tokens"]["status"], "unknown")
        self.assertIsNone(profile["capabilities"]["context_window_tokens"]["value"])
        self.assertEqual(profile["capabilities"]["context_window_tokens"]["source"], "not_measured")

    def test_capability_value_keeps_source_timestamp_and_evidence(self) -> None:
        observed = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)
        self.store.set_capability(
            "claude-opus", "anthropic", "context_window_tokens", 200000,
            status="declared", source="provider-documentation",
            evidence_ref="https://example.invalid/model-card", observed_at=observed,
        )
        capability = self.store.get_profile("claude-opus")["capabilities"]["context_window_tokens"]
        self.assertEqual(capability["value"], 200000)
        self.assertEqual(capability["status"], "declared")
        self.assertEqual(capability["source"], "provider-documentation")
        self.assertEqual(capability["evidence_ref"], "https://example.invalid/model-card")
        self.assertEqual(capability["observed_at"], observed.isoformat())

    def test_unknown_can_be_recorded_without_inventing_a_value(self) -> None:
        self.store.set_capability(
            "local-model", "local", "video", None,
            status="unknown", source="not_measured",
        )
        result = self.store.get_profile("local-model")["capabilities"]["video"]
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "unknown")

    def test_capability_types_and_provenance_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "vision", "yes", status="verified", source="test")
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "context_window_tokens", 0, status="verified", source="test")
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "vision", True, status="verified", source=" ")
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "vision", True, status="unknown", source="test")

    def test_profile_survives_store_restart(self) -> None:
        self.store.set_capability("qwen", "qwen", "tool_calling", True, status="verified", source="offline-contract-test", evidence_ref="test://tool-calling")
        self.store.close()
        self.store = ModelCapabilityStore(self.database)
        profile = self.store.get_profile("qwen")
        self.assertTrue(profile["capabilities"]["tool_calling"]["value"])
        self.assertEqual(profile["capabilities"]["tool_calling"]["status"], "verified")

    def test_verified_capability_requires_evidence_reference(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "vision", True, status="verified", source="operator-check")

    def test_zero_cost_is_valid_and_never_inferred(self) -> None:
        self.store.set_capability("free-model", "local", "input_cost_per_million", 0.0,
                                  status="declared", source="operator-config")
        field = self.store.get_profile("free-model")["capabilities"]["input_cost_per_million"]
        self.assertEqual(field["value"], 0.0)
        self.assertEqual(field["status"], "declared")

    def test_store_rejects_benchmark_record_that_claims_a_model_call(self) -> None:
        with self.assertRaises(ValueError):
            self.store.save_benchmark_run({"model_id": "m", "no_model_invoked": False})

    def test_rejects_unknown_capability_name(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_capability("m", "local", "hidden_system_prompt", "x", status="declared", source="test")

    def test_offline_benchmark_is_deterministic_and_persisted(self) -> None:
        first = run_offline_prompt_benchmark(self.store, model_id="local-test", model_family="local")
        second = run_offline_prompt_benchmark(self.store, model_id="local-test", model_family="local")
        self.assertTrue(first["no_model_invoked"])
        self.assertTrue(first["not_a_model_quality_measurement"])
        self.assertEqual(first["passed_cases"], first["total_cases"])
        self.assertEqual(first["score"], 100)
        self.assertEqual(first["case_results"], second["case_results"])
        runs = self.store.list_benchmark_runs(model_id="local-test")
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["benchmark_id"], second["benchmark_id"])
        self.assertEqual(runs[0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
