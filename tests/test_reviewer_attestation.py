"""Security tests for cryptographic independent-review attestations (WS1 P0)."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.reviewer_attestation import (
    ReviewAttestationError,
    ReviewAttestationVerifier,
    _signed_message,
    active_backend,
    build_review_binding,
    build_trust_record,
    create_attestation,
    load_trust_store,
    parse_trust_store,
    public_key_from_seed,
    pure_public_key,
    pure_sign,
    pure_verify,
    render_trust_store,
    sign_bytes,
    verify_bytes,
)

# RFC 8032 section 7.1, TEST 1 / TEST 2 / TEST 3
RFC8032_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

TEST_SEED_REVIEWER = bytes(range(32))
TEST_SEED_ATTACKER = bytes(range(1, 33))


class Ed25519BackendTests(unittest.TestCase):
    def test_pure_backend_matches_rfc8032_vectors(self):
        for seed_hex, public_hex, message_hex, signature_hex in RFC8032_VECTORS:
            seed, message = bytes.fromhex(seed_hex), bytes.fromhex(message_hex)
            self.assertEqual(pure_public_key(seed).hex(), public_hex)
            self.assertEqual(pure_sign(seed, message).hex(), signature_hex)
            self.assertTrue(pure_verify(bytes.fromhex(public_hex), message, bytes.fromhex(signature_hex)))

    def test_pure_backend_rejects_tampered_message_signature_and_bad_lengths(self):
        seed_hex, public_hex, _message, signature_hex = RFC8032_VECTORS[1]
        public, signature = bytes.fromhex(public_hex), bytearray(bytes.fromhex(signature_hex))
        self.assertFalse(pure_verify(public, b"\x73", bytes(signature)))
        signature[5] ^= 0x01
        self.assertFalse(pure_verify(public, b"\x72", bytes(signature)))
        self.assertFalse(pure_verify(public[:31], b"\x72", bytes(signature)))
        # s >= L must be rejected (malleability guard)
        forged = bytes.fromhex(signature_hex)[:32] + b"\xff" * 32
        self.assertFalse(pure_verify(public, b"\x72", forged))

    def test_active_backend_round_trip_and_cross_backend_agreement(self):
        message = b"muse cross-backend agreement"
        signature = sign_bytes(TEST_SEED_REVIEWER, message)
        public = public_key_from_seed(TEST_SEED_REVIEWER)
        self.assertEqual(public, pure_public_key(TEST_SEED_REVIEWER))
        self.assertTrue(verify_bytes(public, message, signature))
        self.assertTrue(pure_verify(public, message, signature))
        self.assertIn(active_backend(), {"cryptography", "pure_python_rfc8032"})
        pure_signature = sign_bytes(TEST_SEED_REVIEWER, message, backend="pure_python_rfc8032")
        self.assertEqual(pure_signature, signature)  # Ed25519 is deterministic across backends
        self.assertTrue(verify_bytes(public, message, pure_signature, backend="pure_python_rfc8032"))
        try:
            with patch.dict(os.environ, {"MUSE_ED25519_BACKEND": "pure"}):
                self.assertEqual(active_backend(), "pure_python_rfc8032")
        except TypeError:  # read-only environ proxy in restricted sandboxes
            pass


class ReviewerAttestationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1_800_000_000
        self.binding = build_review_binding(
            ticket_id="task-video-0001",
            artifact_sha256=hashlib.sha256(b"artifact bytes").hexdigest(),
            test_evidence_sha256=hashlib.sha256(b"Ran 12 tests OK").hexdigest(),
            proposer_id="muse-builder",
        )
        self.records = [
            build_trust_record(key_id="rk-prod-1", reviewer_id="creator-reviewer",
                               public_key=public_key_from_seed(TEST_SEED_REVIEWER), purpose="production"),
            build_trust_record(key_id="rk-test-1", reviewer_id="sandbox-reviewer",
                               public_key=public_key_from_seed(TEST_SEED_ATTACKER), purpose="ephemeral_test"),
        ]
        self.store_path = self.root / "trust.json"
        self.store_path.write_bytes(render_trust_store(self.records))
        self.pin = hashlib.sha256(self.store_path.read_bytes()).hexdigest()
        self.trusted = load_trust_store(self.store_path, self.pin)
        self.verifier = ReviewAttestationVerifier(self.trusted, self.root / "receipts.sqlite3",
                                                  clock=lambda: self.now)

    def tearDown(self):
        self.verifier.close()
        self.tmp.cleanup()

    def _sign(self, **overrides):
        values = dict(seed=TEST_SEED_REVIEWER, key_id="rk-prod-1", reviewer_id="creator-reviewer",
                      binding=self.binding, issued_at=self.now, ttl_seconds=600)
        values.update(overrides)
        return create_attestation(**values)

    def test_valid_attestation_is_verified_consumed_and_read_back(self):
        attestation = self._sign()
        facts = self.verifier.verify_and_consume(attestation, self.binding)
        self.assertEqual(facts["verdict"], "approve")
        self.assertEqual(facts["key_purpose"], "production")
        receipts = self.verifier.receipts("task-video-0001")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["attestation_sha256"], facts["attestation_sha256"])

    def test_replay_of_consumed_attestation_is_rejected(self):
        attestation = self._sign()
        self.verifier.verify_and_consume(attestation, self.binding)
        with self.assertRaisesRegex(ReviewAttestationError, "replay"):
            self.verifier.verify_and_consume(attestation, self.binding)

    def test_self_review_is_rejected_at_signing_and_at_verification(self):
        with self.assertRaisesRegex(ReviewAttestationError, "own proposal"):
            self._sign(reviewer_id="muse-builder")
        # A hand-forged document that bypasses create_attestation's guard is still rejected by the verifier,
        # even though the signature itself is cryptographically valid.
        self_binding = dict(self.binding, proposer_id="creator-reviewer")
        attestation = self._sign()
        attestation["binding"] = dict(self_binding)
        attestation["signature"] = sign_bytes(TEST_SEED_REVIEWER, _signed_message(attestation)).hex()
        with self.assertRaisesRegex(ReviewAttestationError, "self-review rejected"):
            self.verifier.verify_and_consume(attestation, self_binding)
        self.assertEqual(self.verifier.receipts("task-video-0001"), [])

    def test_actor_string_without_valid_signature_is_rejected(self):
        attestation = self._sign()
        attestation["signature"] = "00" * 64
        with self.assertRaisesRegex(ReviewAttestationError, "signature verification failed"):
            self.verifier.verify_and_consume(attestation, self.binding)

    def test_untrusted_key_claiming_trusted_key_id_is_rejected(self):
        attestation = self._sign(seed=TEST_SEED_ATTACKER)
        with self.assertRaisesRegex(ReviewAttestationError, "signature verification failed"):
            self.verifier.verify_and_consume(attestation, self.binding)

    def test_binding_to_different_artifact_or_test_evidence_is_rejected(self):
        attestation = self._sign()
        for field in ("artifact_sha256", "test_evidence_sha256", "ticket_id"):
            other = dict(self.binding)
            other[field] = hashlib.sha256(b"other").hexdigest() if field != "ticket_id" else "task-other"
            with self.assertRaisesRegex(ReviewAttestationError, "not bound"):
                self.verifier.check(attestation, other)

    def test_tampered_document_fields_break_signature(self):
        attestation = self._sign(verdict="reject")
        attestation["verdict"] = "approve"
        with self.assertRaisesRegex(ReviewAttestationError, "signature verification failed"):
            self.verifier.verify_and_consume(attestation, self.binding)

    def test_expired_future_and_overlong_attestations_are_rejected(self):
        expired = self._sign(issued_at=self.now - 2000, ttl_seconds=600)
        future = self._sign(issued_at=self.now + 1000, ttl_seconds=600)
        for attestation in (expired, future):
            with self.assertRaisesRegex(ReviewAttestationError, "expired"):
                self.verifier.check(attestation, self.binding)
        with self.assertRaises(ReviewAttestationError):
            self._sign(ttl_seconds=3600)

    def test_ephemeral_test_key_never_counts_as_production(self):
        attestation = self._sign(seed=TEST_SEED_ATTACKER, key_id="rk-test-1", reviewer_id="sandbox-reviewer")
        with self.assertRaisesRegex(ReviewAttestationError, "ephemeral test keys"):
            self.verifier.check(attestation, self.binding)
        sandbox = ReviewAttestationVerifier(self.trusted, self.root / "sandbox.sqlite3",
                                            require_production=False, clock=lambda: self.now)
        try:
            self.assertEqual(sandbox.check(attestation, self.binding)["key_purpose"], "ephemeral_test")
        finally:
            sandbox.close()

    def test_revoked_key_and_rejected_verdict_fail_closed(self):
        records = [dict(self.records[0], status="revoked"), self.records[1]]
        revoked = parse_trust_store(render_trust_store(records))
        verifier = ReviewAttestationVerifier(revoked, self.root / "revoked.sqlite3", clock=lambda: self.now)
        try:
            with self.assertRaisesRegex(ReviewAttestationError, "revoked"):
                verifier.check(self._sign(), self.binding)
        finally:
            verifier.close()
        with self.assertRaisesRegex(ReviewAttestationError, "rejected this artifact"):
            self.verifier.verify_and_consume(self._sign(verdict="reject"), self.binding)
        self.assertEqual(self.verifier.receipts("task-video-0001"), [])

    def test_trust_store_pin_mismatch_and_tampering_fail_closed(self):
        with self.assertRaisesRegex(ReviewAttestationError, "pinned SHA-256"):
            load_trust_store(self.store_path, "0" * 64)
        document = json.loads(self.store_path.read_text(encoding="utf-8"))
        document["keys"][0]["fingerprint_sha256"] = "1" * 64
        with self.assertRaisesRegex(ReviewAttestationError, "fingerprint"):
            parse_trust_store(json.dumps(document).encode("utf-8"))
        with self.assertRaises(ReviewAttestationError):
            load_trust_store("relative/trust.json", self.pin)

    def test_receipts_table_is_append_only(self):
        self.verifier.verify_and_consume(self._sign(), self.binding)
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            self.verifier.connection.execute("DELETE FROM reviewer_attestation_receipts")
        with self.assertRaises(sqlite3.DatabaseError):
            self.verifier.connection.execute("UPDATE reviewer_attestation_receipts SET verdict='reject'")

    def test_malformed_expected_binding_fails_closed(self):
        attestation = self._sign()
        for bad in (None, {}, dict(self.binding, extra="x"), {k: v for k, v in self.binding.items() if k != "proposer_id"}):
            with self.assertRaises(ReviewAttestationError):
                self.verifier.check(attestation, bad)

    def test_extra_or_missing_fields_are_rejected(self):
        attestation = self._sign()
        attestation["actor"] = "reviewer"
        with self.assertRaisesRegex(ReviewAttestationError, "unexpected fields"):
            self.verifier.check(attestation, self.binding)
        attestation = self._sign()
        del attestation["nonce"]
        with self.assertRaises(ReviewAttestationError):
            self.verifier.check(attestation, self.binding)


if __name__ == "__main__":
    unittest.main()
