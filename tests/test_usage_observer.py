from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.providers import ChatRequest, ChatResponse, ProviderDescriptor
from muse.usage_ledger import UsageLedger
from muse.usage_observer import UsageRecordingAdapter


class FakeAdapter:
    descriptor = ProviderDescriptor(
        provider_id="fake-provider",
        display_name="Fake Provider",
        is_local=True,
        requires_verified_free_quota=False,
        capabilities=frozenset({"chat"}),
    )

    def __init__(self, response: ChatResponse) -> None:
        self.response = response

    def chat(self, request: ChatRequest) -> ChatResponse:
        return self.response


class BrokenLedger:
    def record_usage(self, *_: object, **__: object) -> None:
        raise OSError("private database path must not leak")


class UsageRecordingAdapterTests(unittest.TestCase):
    def test_decorator_records_reported_usage_and_preserves_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = ChatResponse(
                provider_id="fake-provider", model="fake-model",
                content="secret completion must not be stored",
                usage={"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
            )
            with UsageLedger(Path(directory) / "usage.sqlite3") as ledger:
                adapter = UsageRecordingAdapter(
                    FakeAdapter(response), ledger,
                    task_id="task-7", agent_id="researcher", project_id="muse",
                )
                actual = adapter.chat(ChatRequest(messages=[{"role": "user", "content": "secret prompt"}]))
                self.assertEqual(actual.content, response.content)
                row = ledger.list_events()[0]
                self.assertEqual(row["input_tokens"], 21)
                self.assertEqual(row["output_tokens"], 8)
                self.assertEqual(row["total_tokens"], 29)
                self.assertEqual(row["attribution_status"], "complete")
                self.assertNotIn("secret prompt", str(row))
                self.assertNotIn("secret completion", str(row))
                self.assertEqual(adapter.observer_snapshot()["record_failures"], 0)
                self.assertTrue(actual.usage_recorded)

    def test_nested_observers_do_not_record_the_same_response_twice(self) -> None:
        response = ChatResponse(
            provider_id="fake-provider", model="fake-model", content="reply",
            usage={"total_tokens": 5},
        )
        with tempfile.TemporaryDirectory() as directory:
            with UsageLedger(Path(directory) / "usage.sqlite3") as ledger:
                inner = UsageRecordingAdapter(FakeAdapter(response), ledger)
                outer = UsageRecordingAdapter(inner, ledger)
                actual = outer.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
                self.assertTrue(actual.usage_recorded)
                self.assertEqual(len(ledger.list_events()), 1)

    def test_missing_provider_usage_is_recorded_as_unknown(self) -> None:
        response = ChatResponse(
            provider_id="fake-provider", model="fake-model",
            content="reply", usage={},
        )
        with tempfile.TemporaryDirectory() as directory, UsageLedger(Path(directory) / "usage.sqlite3") as ledger:
            UsageRecordingAdapter(FakeAdapter(response), ledger).chat(
                ChatRequest(messages=[{"role": "user", "content": "hello"}])
            )
            row = ledger.list_events()[0]
            self.assertEqual(row["token_source"], "not_reported")
            self.assertIsNone(row["total_tokens"])

    def test_telemetry_failure_does_not_turn_successful_inference_into_retryable_failure(self) -> None:
        response = ChatResponse(
            provider_id="fake-provider", model="fake-model",
            content="reply", usage={"total_tokens": 5},
        )
        adapter = UsageRecordingAdapter(FakeAdapter(response), BrokenLedger())
        self.assertIs(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])), response)
        self.assertEqual(adapter.observer_snapshot(), {
            "record_failures": 1,
            "last_record_error": "OSError",
        })


if __name__ == "__main__":
    unittest.main()
