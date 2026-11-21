from __future__ import annotations

import unittest

from muse.model_client import ModelChatResult
from muse.providers import ChatRequest, LMStudioAdapter


class FakeDetailedClient:
    model = "configured-model"

    def chat_with_usage(self, messages, *, temperature=0.7, max_tokens=None):
        return ModelChatResult(
            content="local reply",
            model="actual-served-model",
            usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
        )


class LMStudioUsageAdapterTests(unittest.TestCase):
    def test_adapter_preserves_provider_reported_usage_and_actual_model(self) -> None:
        adapter = LMStudioAdapter(FakeDetailedClient())
        response = adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
        self.assertEqual(response.provider_id, "lm-studio-local")
        self.assertEqual(response.model, "actual-served-model")
        self.assertEqual(response.usage, {
            "prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15,
        })
        self.assertFalse(response.usage_recorded)

    def test_adapter_propagates_low_level_usage_record_receipt(self) -> None:
        class AlreadyRecordedClient(FakeDetailedClient):
            def chat_with_usage(self, messages, *, temperature=0.7, max_tokens=None):
                result = super().chat_with_usage(messages, temperature=temperature, max_tokens=max_tokens)
                return ModelChatResult(
                    content=result.content, model=result.model, usage=result.usage,
                    usage_recorded=True,
                )

        response = LMStudioAdapter(AlreadyRecordedClient()).chat(
            ChatRequest(messages=[{"role": "user", "content": "hello"}])
        )
        self.assertTrue(response.usage_recorded)


if __name__ == "__main__":
    unittest.main()
