from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.contextual_inquiry import ContextualInquiry, Hypothesis, Observation, SourceKind


class FakeExecutionContext:
    def __init__(self, *, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def assert_current_lease(self) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise PermissionError("lease lost")


class FakeLocalClient:
    def __init__(self, content: str):
        self.content = content
        self.messages = None
        self.options = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.options = kwargs
        return self.content


def make_inquiry() -> ContextualInquiry:
    return ContextualInquiry(
        "local-review-case",
        "Assess whether this claim is adequately supported.",
        observations=(
            Observation("obs-1", SourceKind.USER_QUOTE, "The reported event happened yesterday.", "source:1"),
        ),
        hypotheses=(
            Hypothesis(
                "hyp-1", "The reported event occurred.", ("obs-1",), confidence=0.4,
                falsification_condition="An independent timestamp contradicts the report.",
            ),
        ),
    )


def make_result(inquiry: ContextualInquiry, assessment: str = "unresolved") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "inquiry_id": inquiry.inquiry_id,
            "summary": "The claim remains provisional.",
            "findings": [
                {
                    "finding_id": "finding-1",
                    "statement": "The supplied observation is a candidate, not independent confirmation.",
                    "observation_refs": ["obs-1"],
                    "hypothesis_refs": ["hyp-1"],
                    "assessment": assessment,
                    "rationale": "Independent corroboration is still required.",
                }
            ],
            "unknowns": ["Whether an independent source confirms the event."],
        },
        ensure_ascii=False,
    )


class LocalContextualInquiryReviewTests(unittest.TestCase):
    def test_rejects_non_loopback_endpoint(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        with self.assertRaisesRegex(ValueError, "loopback"):
            LocalContextualInquiryReviewHandler(
                base_url="https://api.example.com/v1",
                model="some-model",
                client=FakeLocalClient("{}"),
            )

    def test_requires_explicit_model_identifier(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        with self.assertRaisesRegex(ValueError, "model"):
            LocalContextualInquiryReviewHandler(
                base_url="http://127.0.0.1:1234/v1",
                model=" ",
                client=FakeLocalClient("{}"),
            )

    def test_reviews_only_with_live_context_and_returns_dpapi_ciphertext(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        inquiry = make_inquiry()
        client = FakeLocalClient(make_result(inquiry))
        context = FakeExecutionContext()
        envelope = {
            "version": 1,
            "protection": "windows-dpapi-current-user",
            "ciphertext_b64": "ZW5jcnlwdGVk",
        }
        handler = LocalContextualInquiryReviewHandler(
            base_url="http://127.0.0.1:1234/v1",
            model="local-review-model",
            client=client,
        )

        with patch(
            "muse.contextual_inquiry_local_review.protect_case_bytes",
            return_value=envelope,
        ) as protect:
            result = handler.execute_review(inquiry, execution_context=context)

        self.assertEqual(result, envelope)
        self.assertEqual(context.calls, 3)
        self.assertEqual(handler.model, "local-review-model")
        self.assertEqual(client.options, {"temperature": 0.0, "max_tokens": 3500})
        request_data = json.loads(client.messages[1]["content"])
        self.assertEqual(request_data["inquiry_id"], inquiry.inquiry_id)
        self.assertIn("The reported event happened yesterday.", json.dumps(request_data))
        protect.assert_called_once()
        self.assertNotIn("The reported event happened yesterday.", json.dumps(result))

    def test_rejects_model_verdict_and_never_encrypts_it(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler
        from muse.contextual_inquiry_security import CaseProtectionError

        inquiry = make_inquiry()
        client = FakeLocalClient(make_result(inquiry, assessment="verified"))
        handler = LocalContextualInquiryReviewHandler(
            base_url="http://127.0.0.1:1234/v1",
            model="local-review-model",
            client=client,
        )
        with patch("muse.contextual_inquiry_local_review.protect_case_bytes") as protect:
            with self.assertRaisesRegex(CaseProtectionError, "cannot promote evidence"):
                handler.execute_review(inquiry, execution_context=FakeExecutionContext())
        protect.assert_not_called()

    def test_does_not_send_case_to_model_after_lease_is_lost(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        inquiry = make_inquiry()
        client = FakeLocalClient(make_result(inquiry))
        handler = LocalContextualInquiryReviewHandler(
            base_url="http://127.0.0.1:1234/v1",
            model="local-review-model",
            client=client,
        )
        with self.assertRaisesRegex(PermissionError, "lease lost"):
            handler.execute_review(inquiry, execution_context=FakeExecutionContext(fail_on_call=1))
        self.assertIsNone(client.messages)

    def test_rechecks_lease_before_encrypting_model_output(self) -> None:
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        inquiry = make_inquiry()
        client = FakeLocalClient(make_result(inquiry))
        handler = LocalContextualInquiryReviewHandler(
            base_url="http://127.0.0.1:1234/v1",
            model="local-review-model",
            client=client,
        )
        with patch("muse.contextual_inquiry_local_review.protect_case_bytes") as protect:
            with self.assertRaisesRegex(PermissionError, "lease lost"):
                handler.execute_review(inquiry, execution_context=FakeExecutionContext(fail_on_call=3))
        protect.assert_not_called()

    def test_non_normal_safety_route_is_never_sent_to_local_model(self) -> None:
        from muse.contextual_inquiry import SafetyRoute
        from muse.contextual_inquiry_local_review import LocalContextualInquiryReviewHandler

        inquiry = ContextualInquiry(
            "professional-support-case",
            "This needs a professional.",
            safety_route=SafetyRoute.PROFESSIONAL_SUPPORT,
        )
        client = FakeLocalClient(make_result(inquiry))
        handler = LocalContextualInquiryReviewHandler(
            base_url="http://127.0.0.1:1234/v1",
            model="local-review-model",
            client=client,
        )
        with self.assertRaisesRegex(PermissionError, "support route"):
            handler.execute_review(inquiry, execution_context=FakeExecutionContext())
        self.assertIsNone(client.messages)

    def test_factory_requires_an_explicit_local_provider_and_loaded_model(self) -> None:
        from muse.contextual_inquiry_local_review import build_local_contextual_review_handler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            (config / "muse.toml").write_text(
                '[models]\nprovider = "lmstudio"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "review-model"\n',
                encoding="utf-8",
            )
            with patch("muse.contextual_inquiry_local_review.LMStudioClient") as client_class:
                client_class.return_value.list_models.return_value = [{"id": "review-model"}]
                handler = build_local_contextual_review_handler(root)
            client_class.assert_called_once_with(
                "http://127.0.0.1:1234/v1",
                model="review-model",
                timeout=90.0,
                chat_template_kwargs=None,
                bypass_proxies=True,
            )
            self.assertEqual(handler.model, "review-model")

    def test_factory_rejects_invalid_timeout_before_network_access(self) -> None:
        from muse.contextual_inquiry_local_review import build_local_contextual_review_handler

        with patch("muse.contextual_inquiry_local_review.LMStudioClient") as client_class:
            with self.assertRaisesRegex(ValueError, "timeout"):
                build_local_contextual_review_handler(".", timeout_seconds=True)
        client_class.assert_not_called()

    def test_factory_rejects_cloud_provider_and_missing_local_model(self) -> None:
        from muse.contextual_inquiry_local_review import build_local_contextual_review_handler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            (config / "muse.toml").write_text(
                '[models]\nprovider = "anthropic"\nbase_url = "https://api.example.com/v1"\nmodel = "cloud-model"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "local LM Studio/llama.cpp provider"):
                build_local_contextual_review_handler(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            (config / "muse.toml").write_text(
                '[models]\nprovider = "lmstudio"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "missing-model"\n',
                encoding="utf-8",
            )
            with patch(
                "muse.contextual_inquiry_local_review.LMStudioClient.list_models",
                return_value=[{"id": "other-model"}],
            ):
                with self.assertRaisesRegex(ValueError, "not currently available"):
                    build_local_contextual_review_handler(root)


if __name__ == "__main__":
    unittest.main()
