from __future__ import annotations

import io
import json
import unittest

from muse.openrouter import OpenRouterConfigurationError, OpenRouterFreeAdapter
from muse.providers import ChatRequest, ProviderRegistry


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.responses.pop(0))


class OpenRouterFreeAdapterTests(unittest.TestCase):
    def test_only_explicit_free_model_ids_are_accepted(self):
        for model in ("openai/gpt-4o", "openrouter/free", "", None):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    OpenRouterFreeAdapter(model=model, api_key="test", opener=FakeOpener([]))

    def test_live_quota_is_read_before_chat_and_no_paid_fallback_is_sent(self):
        opener = FakeOpener([
            {"data": {"free_model_daily_requests": {"remaining": 7}, "limit_remaining": None}},
            {"data": {"free_model_daily_requests": {"remaining": 6}, "limit_remaining": None}},
            {"choices": [{"message": {"content": "research complete"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        ])
        adapter = OpenRouterFreeAdapter(
            model="nvidia/nemotron-free:free", api_key="secret-key", opener=opener
        )
        budget = adapter.refresh_budget()
        self.assertEqual(budget.remaining_units, 7)
        self.assertTrue(budget.is_fresh())
        response = adapter.chat(
            ChatRequest(messages=[{"role": "user", "content": "public research"}], max_tokens=128)
        )
        self.assertEqual(response.content, "research complete")
        self.assertEqual(response.usage["total_tokens"], 15)
        post = opener.requests[-1][0]
        body = json.loads(post.data.decode("utf-8"))
        self.assertEqual(body["model"], "nvidia/nemotron-free:free")
        self.assertNotIn("models", body)
        self.assertNotIn("fallback", body)
        self.assertEqual(len(opener.requests), 3)

    def test_provider_registry_refreshes_live_quota_before_routing(self):
        opener = FakeOpener([
            {"data": {"free_model_daily_requests": {"remaining": 4}, "limit_remaining": None}},
            {"choices": [{"message": {"content": "routed"}}], "usage": {"total_tokens": 2}},
        ])
        adapter = OpenRouterFreeAdapter(
            model="nvidia/nemotron-free:free", api_key="secret", opener=opener
        )
        registry = ProviderRegistry()
        registry.register(adapter)
        response, remaining = registry.chat(
            "openrouter-free", ChatRequest(messages=[{"role": "user", "content": "public task"}])
        )
        self.assertEqual(response.content, "routed")
        self.assertEqual(remaining.remaining_units, 3)
        self.assertEqual(len(opener.requests), 2)

    def test_exhausted_or_unavailable_quota_blocks_inference(self):
        for quota in ({"remaining": 0}, {}, None):
            with self.subTest(quota=quota):
                opener = FakeOpener([
                    {"data": {"free_model_daily_requests": quota, "limit_remaining": None}}
                ])
                adapter = OpenRouterFreeAdapter(
                    model="nvidia/nemotron-free:free", api_key="secret", opener=opener
                )
                with self.assertRaises(Exception):
                    adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
                self.assertEqual(len(opener.requests), 1)

    def test_missing_key_sends_no_network_request(self):
        opener = FakeOpener([])
        adapter = OpenRouterFreeAdapter(
            model="nvidia/nemotron-free:free", api_key="", opener=opener
        )
        with self.assertRaises(OpenRouterConfigurationError):
            adapter.refresh_budget()
        self.assertEqual(opener.requests, [])

    def test_provider_errors_are_not_retried_and_do_not_leak_key(self):
        import urllib.error
        response_body = io.BytesIO(b"limited")
        error = urllib.error.HTTPError("https://openrouter.ai/api/v1/key", 429, "limited", {}, response_body)
        class ErrorOpener:
            def __init__(self):
                self.calls = 0
            def __call__(self, request, timeout):
                self.calls += 1
                raise error
        opener = ErrorOpener()
        adapter = OpenRouterFreeAdapter(
            model="nvidia/nemotron-free:free", api_key="do-not-leak", opener=opener
        )
        with self.assertRaises(OpenRouterConfigurationError) as caught:
            adapter.refresh_budget()
        self.assertNotIn("do-not-leak", str(caught.exception))
        self.assertEqual(opener.calls, 1)
        self.assertTrue(response_body.closed)


if __name__ == "__main__":
    unittest.main()
