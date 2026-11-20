from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

from muse.contextual_inquiry import ContextualInquiry, Hypothesis, Observation, SafetyRoute, SourceKind
from muse.contextual_inquiry_security import (
    CaseProtectionError,
    ENVELOPE_VERSION,
    PROTECTION_SCHEME,
    _serialize_contextual_inquiry,
    deserialize_contextual_inquiry,
    protect_case_bytes,
    protect_contextual_inquiry,
    unprotect_case_bytes,
)


class ContextualInquirySecurityTests(unittest.TestCase):
    def test_protection_is_windows_only_and_fails_closed(self):
        with patch("muse.contextual_inquiry_security.os.name", "posix"):
            with self.assertRaisesRegex(CaseProtectionError, "requires Windows DPAPI"):
                protect_case_bytes(b"private case data")

    def test_rejects_invalid_plaintext_inputs(self):
        for value in ("not bytes", b""):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    protect_case_bytes(value)
        with self.assertRaises(ValueError):
            protect_case_bytes(b"x" * (2 * 1024 * 1024 + 1))

    def test_rejects_unknown_or_malformed_envelopes_before_decryption(self):
        invalid = [
            None,
            {},
            {"version": True, "protection": PROTECTION_SCHEME, "ciphertext_b64": "YQ=="},
            {"version": 1.0, "protection": PROTECTION_SCHEME, "ciphertext_b64": "YQ=="},
            {"version": ENVELOPE_VERSION + 1, "protection": PROTECTION_SCHEME, "ciphertext_b64": "YQ=="},
            {"version": ENVELOPE_VERSION, "protection": "plaintext", "ciphertext_b64": "YQ=="},
            {"version": ENVELOPE_VERSION, "protection": PROTECTION_SCHEME, "ciphertext_b64": "%%%"},
            {"version": ENVELOPE_VERSION, "protection": PROTECTION_SCHEME, "ciphertext_b64": "YQ==", "extra": "no"},
        ]
        for envelope in invalid:
            with self.subTest(envelope=envelope):
                with self.assertRaises(ValueError):
                    unprotect_case_bytes(envelope)

    def test_case_storage_consent_is_separate_from_knowledge_memory_consent(self):
        inquiry = ContextualInquiry("case-security", "A generic review goal.")
        consented_for_memory = ContextualInquiry(
            "case-security", "A generic review goal.", consent_to_store=True,
        )
        self.assertEqual(
            _serialize_contextual_inquiry(inquiry),
            _serialize_contextual_inquiry(consented_for_memory),
        )
        with self.assertRaises(PermissionError):
            protect_contextual_inquiry(inquiry)

    def test_deserializer_rejects_noncanonical_or_malformed_plaintext(self):
        for value in (b"not-json", b"{}", b'{"unexpected":true}'):
            with self.subTest(value=value):
                with self.assertRaises(CaseProtectionError):
                    deserialize_contextual_inquiry(value)

    def test_deserializer_round_trips_validated_contract_without_memory_consent(self):
        inquiry = ContextualInquiry(
            "case-deserialize", "A review goal.",
            observations=(Observation("obs-1", SourceKind.USER_QUOTE, "Quoted text.", "source:1"),),
            hypotheses=(Hypothesis("hyp-1", "A tentative idea.", ("obs-1",), confidence=0.4,
                                   falsification_condition="Independent evidence contradicts it."),),
            safety_route=SafetyRoute.NORMAL,
            consent_to_store=True,
        )
        restored = deserialize_contextual_inquiry(_serialize_contextual_inquiry(inquiry))
        self.assertEqual(restored.inquiry_id, inquiry.inquiry_id)
        self.assertEqual(restored.observations, inquiry.observations)
        self.assertEqual(restored.hypotheses, inquiry.hypotheses)
        self.assertFalse(restored.consent_to_store)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_windows_current_user_round_trip(self):
        plaintext = b'{"case":"private","unicode":"\xe7\x81\xaf"}'
        envelope = protect_case_bytes(plaintext)
        self.assertEqual(envelope["version"], ENVELOPE_VERSION)
        self.assertEqual(envelope["protection"], PROTECTION_SCHEME)
        self.assertNotIn(plaintext.decode("utf-8"), str(envelope))
        self.assertEqual(unprotect_case_bytes(envelope), plaintext)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration test")
    def test_ciphertext_tampering_fails_closed(self):
        envelope = protect_case_bytes(b"private case data")
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext_b64"]))
        ciphertext[-1] ^= 0x01
        envelope["ciphertext_b64"] = base64.b64encode(ciphertext).decode("ascii")
        with self.assertRaises(CaseProtectionError):
            unprotect_case_bytes(envelope)


if __name__ == "__main__":
    unittest.main()
