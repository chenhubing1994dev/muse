from __future__ import annotations

import unittest
from datetime import datetime, timezone

from muse.providers import (
    ChatRequest, ChatResponse, LMStudioAdapter, ProviderDescriptor, ProviderRegistry,
)
from muse.provider_policy import ProviderBudget


def fresh_budget(provider_id: str, remaining: int = 3) -> ProviderBudget:
    return ProviderBudget(provider_id, True, True, True, remaining, "requests", datetime.now(timezone.utc).isoformat(), "https://quota.example/status")


class FakeAdapter:
    descriptor = ProviderDescriptor("fake-local", "Fake Local", is_local=True, capabilities=frozenset({"chat", "research", "coding"}))

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(self.descriptor.provider_id, "fake-model", f"received {len(request.messages)} messages")


class FakeCloudAdapter:
    descriptor = ProviderDescriptor("verified-cloud", "Verified Cloud", is_local=False, capabilities=frozenset({"chat", "research", "coding"}), data_policy="no_training")

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse("verified-cloud", "cloud-model", "cloud reply")


class FakeUnspecifiedCloudAdapter:
    descriptor = ProviderDescriptor("unspecified-cloud", "Unspecified Cloud", is_local=False, capabilities=frozenset({"chat", "research"}), data_policy="unspecified")

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse("unspecified-cloud", "cloud-model", "cloud reply")


class FakeClient:
    model = "test-model"

    def chat(self, messages, *, temperature=0.7, max_tokens=None):
        self.seen = (messages, temperature, max_tokens)
        return "local reply"


class ProviderAdapterTests(unittest.TestCase):
    def test_registry_registers_and_invokes_a_provider_neutrally(self) -> None:
        registry = ProviderRegistry()
        adapter = FakeAdapter()
        registry.register(adapter)
        result = registry.get("fake-local").chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
        self.assertEqual(result.content, "received 1 messages")
        self.assertEqual(registry.list_descriptors(), [adapter.descriptor])

    def test_duplicate_provider_id_is_rejected(self) -> None:
        registry = ProviderRegistry()
        registry.register(FakeAdapter())
        with self.assertRaises(ValueError):
            registry.register(FakeAdapter())

    def test_cloud_adapter_requires_fresh_verified_free_quota(self) -> None:
        registry = ProviderRegistry()
        registry.register(FakeCloudAdapter())
        request = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        with self.assertRaises(ValueError):
            registry.chat("verified-cloud", request)
        unknown = ProviderBudget("verified-cloud", True, True, False, None)
        from muse.provider_policy import NoFreeProviderAvailable
        with self.assertRaises(NoFreeProviderAvailable):
            registry.chat("verified-cloud", request, budget=unknown)
        response, remaining = registry.chat("verified-cloud", request, budget=fresh_budget("verified-cloud"))
        self.assertEqual(response.content, "cloud reply")
        self.assertEqual(remaining.remaining_units, 2)

    def test_task_router_enforces_capability_and_data_class(self) -> None:
        registry = ProviderRegistry()
        registry.register(FakeAdapter())
        registry.register(FakeCloudAdapter())
        registry.register(FakeUnspecifiedCloudAdapter())
        with self.assertRaises(LookupError):
            registry.select_for_task({"coding"}, data_class="private")
        private = registry.select_for_task({"coding"}, data_class="private", allow_local_inference=True)
        self.assertEqual(private.descriptor.provider_id, "fake-local")
        sanitized = registry.select_for_task({"research"}, data_class="sanitized", budgets={"verified-cloud": fresh_budget("verified-cloud")})
        self.assertEqual(sanitized.descriptor.provider_id, "verified-cloud")
        public = registry.select_for_task({"research"}, data_class="public", budgets={"verified-cloud": fresh_budget("verified-cloud"), "unspecified-cloud": fresh_budget("unspecified-cloud")})
        self.assertEqual(public.descriptor.provider_id, "verified-cloud")
        with self.assertRaises(LookupError):
            registry.select_for_task({"vision"}, data_class="public")
    def test_stale_cloud_quota_is_not_selected(self) -> None:
        from datetime import timedelta
        stale = ProviderBudget("verified-cloud", True, True, True, 3, "requests", (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(), "https://quota.example")
        registry = ProviderRegistry()
        registry.register(FakeCloudAdapter())
        with self.assertRaises(LookupError):
            registry.select_for_task({"coding"}, data_class="public", budgets={"verified-cloud": stale})

    def test_lm_studio_adapter_wraps_existing_local_client(self) -> None:
        class Client:
            model = "test-model"
            def chat(self, messages, *, temperature=0.7, max_tokens=None):
                self.seen = (messages, temperature, max_tokens)
                return "local reply"
        client = Client()
        adapter = LMStudioAdapter(client)
        response = adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=64))
        self.assertEqual(response.provider_id, "lm-studio-local")
        self.assertEqual(response.model, "test-model")
        self.assertEqual(response.content, "local reply")
        self.assertEqual(client.seen[1:], (0.2, 64))


    def test_registry_tracks_latency_and_successful_calls_for_load_routing(self) -> None:
        registry = ProviderRegistry()
        adapter = FakeCloudAdapter()
        registry.register(adapter)
        response, remaining = registry.chat("verified-cloud", ChatRequest(messages=[{"role": "user", "content": "hi"}]), budget=fresh_budget("verified-cloud"))
        metrics = registry.runtime_snapshot()["verified-cloud"]
        self.assertEqual(metrics["successes"], 1)
        self.assertEqual(metrics["failures"], 0)
        self.assertEqual(metrics["in_flight"], 0)
        self.assertGreaterEqual(metrics["average_latency_ms"], 0)

    def test_circuit_breaker_excludes_provider_after_repeated_failures(self) -> None:
        class BrokenCloud:
            descriptor = ProviderDescriptor("broken", "Broken", is_local=False,
                capabilities=frozenset({"chat", "research"}), data_policy="no_training")
            def chat(self, request: ChatRequest) -> ChatResponse:
                raise TimeoutError("provider timeout")
        registry = ProviderRegistry(circuit_breaker_threshold=1, circuit_cooldown_seconds=60)
        registry.register(BrokenCloud())
        with self.assertRaises(TimeoutError):
            registry.chat("broken", ChatRequest(messages=[{"role": "user", "content": "hi"}]), budget=fresh_budget("broken"))
        snapshot = registry.runtime_snapshot()["broken"]
        self.assertFalse(snapshot["healthy"])
        self.assertEqual(snapshot["last_error"], "TimeoutError")
        with self.assertRaises(LookupError):
            registry.select_for_task({"research"}, data_class="public", budgets={"broken": fresh_budget("broken")})

    def test_runtime_metrics_and_open_circuit_survive_registry_restart(self) -> None:
        import tempfile
        from pathlib import Path

        class BrokenCloud:
            descriptor = ProviderDescriptor("persisted-broken", "Broken", is_local=False,
                capabilities=frozenset({"chat", "research"}), data_policy="no_training")
            def chat(self, request: ChatRequest) -> ChatResponse:
                raise TimeoutError("do not persist exception text")

        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "provider-metrics.sqlite3"
            registry = ProviderRegistry(circuit_breaker_threshold=1, circuit_cooldown_seconds=60, metrics_database=database)
            registry.register(BrokenCloud())
            with self.assertRaises(TimeoutError):
                registry.chat("persisted-broken", ChatRequest(messages=[{"role": "user", "content": "hi"}]), budget=fresh_budget("persisted-broken"))
            registry.close()

            restarted = ProviderRegistry(circuit_breaker_threshold=1, circuit_cooldown_seconds=60, metrics_database=database)
            restarted.register(BrokenCloud())
            metrics = restarted.runtime_snapshot()["persisted-broken"]
            self.assertEqual(metrics["failures"], 1)
            self.assertEqual(metrics["last_error"], "TimeoutError")
            self.assertFalse(metrics["healthy"])
            with self.assertRaises(LookupError):
                restarted.select_for_task({"research"}, data_class="public", budgets={"persisted-broken": fresh_budget("persisted-broken")})
            restarted.close()

if __name__ == "__main__":
    unittest.main()
