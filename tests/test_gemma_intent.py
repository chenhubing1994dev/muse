from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.gemma_intent import LazyGemmaIntentClassifier, LMStudioGemmaIntentClassifier, assist_unknown_request


class FakeModel:
    def __init__(self, content: str):
        self.content = content
        self.closed = False
        self.calls = 0

    def create_chat_completion(self, **_kwargs):
        self.calls += 1
        return {"choices": [{"message": {"content": self.content}}]}

    def close(self):
        self.closed = True


class GemmaIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp.name) / "model.gguf"
        self.model_path.write_bytes(b"fake")
        self.base = {
            "domain": "unknown", "capability": "unknown", "confidence": 0.0,
            "matched_phrase": None, "approval_required": True,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_path_returns_deterministic_result_without_model_factory(self):
        with patch.dict(os.environ, {"MUSE_GEMMA_ENABLED": "0"}, clear=True):
            result = assist_unknown_request("请帮我处理这个复杂请求", self.base)
        self.assertIs(result, self.base)

    def test_valid_suggestion_is_advisory_and_preserves_approval_gate(self):
        content = json.dumps({
            "domain": "software", "capability": "coding",
            "confidence": 0.91, "rationale": "A software change is requested.",
        })
        model = FakeModel(content)
        classifier = LazyGemmaIntentClassifier(
            self.model_path, model_factory=lambda **_kw: model, idle_unload_seconds=10,
        )
        with patch.dict(os.environ, {
            "MUSE_GEMMA_ENABLED": "1", "MUSE_GEMMA_MODEL_PATH": str(self.model_path),
        }, clear=True), patch("muse.gemma_intent.LazyGemmaIntentClassifier", return_value=classifier):
            result = assist_unknown_request("请帮我处理这个复杂请求", self.base)
        self.assertEqual(result["capability"], "coding")
        self.assertEqual(result["tool_status"], "unverified")
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["classifier_source"], "optional_local_gemma")
        classifier.unload()

    def test_rejects_unsupported_pair_extra_keys_and_low_confidence(self):
        for payload in (
            {"domain": "software", "capability": "shell", "confidence": .99, "rationale": "x"},
            {"domain": "reminders", "capability": "reminder_capture", "confidence": .99, "rationale": "x"},
            {"domain": "software", "capability": "coding", "confidence": .99, "rationale": "x", "tool": "cmd"},
            {"domain": "software", "capability": "coding", "confidence": .4, "rationale": "x"},
        ):
            model = FakeModel(json.dumps(payload))
            classifier = LazyGemmaIntentClassifier(
                self.model_path, model_factory=lambda **_kw: model, idle_unload_seconds=10,
            )
            self.assertIsNone(classifier.classify("request"))
            classifier.unload()

    def test_timeout_fails_closed_and_model_is_not_unloaded_during_generation(self):
        class SlowModel(FakeModel):
            def create_chat_completion(self, **kwargs):
                time.sleep(.15)
                return super().create_chat_completion(**kwargs)
        model = SlowModel(json.dumps({
            "domain": "software", "capability": "coding", "confidence": .9, "rationale": "x",
        }))
        classifier = LazyGemmaIntentClassifier(
            self.model_path, model_factory=lambda **_kw: model,
            timeout_seconds=.05, idle_unload_seconds=10,
        )
        self.assertIsNone(classifier.classify("request"))
        self.assertFalse(classifier.unload())
        time.sleep(.2)
        self.assertTrue(classifier.unload())
        self.assertTrue(model.closed)

    def test_idle_timer_unloads_model_after_quiet_period(self):
        model = FakeModel(json.dumps({
            "domain": "software", "capability": "coding", "confidence": .9, "rationale": "x",
        }))
        classifier = LazyGemmaIntentClassifier(
            self.model_path, model_factory=lambda **_kw: model, idle_unload_seconds=1,
        )
        self.assertIsNotNone(classifier.classify("request"))
        deadline = time.monotonic() + 2.5
        while classifier._model is not None and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertIsNone(classifier._model)
        self.assertTrue(model.closed)

    def test_explicit_unload_releases_loaded_model(self):
        model = FakeModel(json.dumps({
            "domain": "software", "capability": "coding", "confidence": .9, "rationale": "x",
        }))
        classifier = LazyGemmaIntentClassifier(
            self.model_path, model_factory=lambda **_kw: model, idle_unload_seconds=10,
        )
        self.assertIsNotNone(classifier.classify("request"))
        self.assertIsNotNone(classifier._model)
        self.assertTrue(classifier.unload())
        self.assertTrue(model.closed)

    def test_missing_model_fails_closed(self):
        classifier = LazyGemmaIntentClassifier(Path(self.temp.name) / "missing.gguf")
        self.assertIsNone(classifier.classify("request"))



    def test_lmstudio_backend_uses_loopback_api_without_gguf_path(self):
        class FakeClient:
            def chat(self, messages, **_kwargs):
                self.messages = messages
                return json.dumps({
                    "domain": "software", "capability": "coding",
                    "confidence": 0.93, "rationale": "A coding request.",
                })
        client = FakeClient()
        classifier = LMStudioGemmaIntentClassifier(
            base_url="http://127.0.0.1:1234/v1", timeout_seconds=2, client=client,
        )
        with patch.dict(os.environ, {
            "MUSE_GEMMA_ENABLED": "1",
            "MUSE_GEMMA_BACKEND": "lmstudio",
            "MUSE_GEMMA_LMSTUDIO_URL": "http://127.0.0.1:1234/v1",
        }, clear=True), patch(
            "muse.gemma_intent.LMStudioGemmaIntentClassifier", return_value=classifier,
        ):
            result = assist_unknown_request("帮我处理一个不明确的请求", self.base)
        self.assertEqual(result["capability"], "coding")
        self.assertEqual(result["classifier_source"], "optional_local_gemma")
        self.assertTrue(result["approval_required"])
        self.assertEqual(client.messages[0]["role"], "system")
        classifier.unload()

    def test_lmstudio_classifier_propagates_model_usage_context_to_worker_thread(self):
        from muse.model_call_context import current_model_call_context, model_call_scope, record_current_model_usage
        from muse.usage_ledger import UsageLedger

        observed_contexts = []
        test_case = self

        class ContextAwareClient:
            def chat(self, _messages, **_kwargs):
                context = current_model_call_context()
                observed_contexts.append(context)
                test_case.assertIsNotNone(context)
                test_case.assertTrue(record_current_model_usage(
                    "lmstudio", "intent-local-model",
                    {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
                ))
                return json.dumps({
                    "domain": "software", "capability": "coding",
                    "confidence": 0.93, "rationale": "A coding request.",
                })

        classifier = LMStudioGemmaIntentClassifier(
            base_url="http://127.0.0.1:1234/v1", timeout_seconds=2,
            client=ContextAwareClient(),
        )
        database = Path(self.temp.name) / "tasks.sqlite3"
        with model_call_scope(
            database, task_id="intent-task-123", agent_id="intent-agent-456", project_id="muse",
        ):
            result = classifier.classify("help with a software task")
        self.assertEqual(result["capability"], "coding")
        self.assertEqual(len(observed_contexts), 1)
        self.assertEqual(observed_contexts[0].task_id, "intent-task-123")
        self.assertEqual(observed_contexts[0].agent_id, "intent-agent-456")
        with UsageLedger(Path(self.temp.name) / "model_usage.sqlite3") as ledger:
            events = ledger.list_events(task_id="intent-task-123", limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["provider_id"], "lmstudio")
        self.assertEqual(events[0]["model"], "intent-local-model")
        self.assertEqual(events[0]["input_tokens"], 17)
        self.assertEqual(events[0]["output_tokens"], 5)
        self.assertEqual(events[0]["attribution_status"], "complete")

    def test_lmstudio_backend_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            LMStudioGemmaIntentClassifier(base_url="http://example.com/v1")

if __name__ == "__main__":
    unittest.main()
