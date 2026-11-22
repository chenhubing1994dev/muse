from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from muse.companion_security import (
    CompanionDeviceStore,
    CompanionSecurityError,
    sign_companion_request,
)


class CompanionDeviceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "companion.sqlite3"
        self.store = CompanionDeviceStore(self.database)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _approved_device(self, *, now: float = 1_800_000_000.0) -> tuple[str, str]:
        challenge = self.store.create_pairing_challenge(now=now)
        claim_secret = "c" * 48
        request = self.store.request_pairing(
            challenge["challenge_id"],
            challenge["code"],
            device_name="Muse iPhone",
            device_fingerprint="a" * 64,
            claim_secret=claim_secret,
            now=now + 1,
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.claim_pairing(request["request_id"], claim_secret, now=now + 2)
        self.store.approve_pairing(request["request_id"], approved_by="host-user", now=now + 3)
        claimed = self.store.claim_pairing(request["request_id"], claim_secret, now=now + 4)
        return claimed["device_id"], claimed["token"]

    def test_pairing_requires_host_approval_and_issues_one_time_device_token(self) -> None:
        challenge = self.store.create_pairing_challenge(now=1000)
        claim_secret = "claim-secret-" * 4
        request = self.store.request_pairing(
            challenge["challenge_id"], challenge["code"],
            device_name="Phone", device_fingerprint="b" * 64,
            claim_secret=claim_secret, now=1001,
        )
        self.assertEqual(request["status"], "pending")
        with self.assertRaises(CompanionSecurityError):
            self.store.claim_pairing(request["request_id"], claim_secret, now=1002)

        self.store.approve_pairing(request["request_id"], approved_by="local-host", now=1003)
        claimed = self.store.claim_pairing(request["request_id"], claim_secret, now=1004)
        self.assertEqual(claimed["permissions"], ["events.read", "status.read"])
        self.assertEqual(len(claimed["token"]), 43)
        with self.assertRaises(CompanionSecurityError):
            self.store.claim_pairing(request["request_id"], claim_secret, now=1005)

        raw = self.database.read_bytes()
        self.assertNotIn(claimed["token"].encode(), raw)
        device = self.store.get_device(claimed["device_id"])
        self.assertNotIn("token_hash", device)
        self.assertEqual(device["status"], "active")

    def test_new_host_challenge_supersedes_previous_open_challenge_atomically(self) -> None:
        first = self.store.create_pairing_challenge(now=1500)
        second = self.store.create_pairing_challenge(now=1501)

        with self.assertRaisesRegex(CompanionSecurityError, "invalid or unavailable"):
            self.store.request_pairing(
                first["challenge_id"], first["code"],
                device_name="Old phone", device_fingerprint="1" * 64,
                claim_secret="a" * 48, now=1502,
            )
        request = self.store.request_pairing(
            second["challenge_id"], second["code"],
            device_name="Current phone", device_fingerprint="2" * 64,
            claim_secret="b" * 48, now=1502,
        )
        self.assertEqual(request["status"], "pending")
        events = self.store.list_audit_events()
        self.assertTrue(any(event["event_type"] == "pairing.challenge_superseded" for event in events))
        self.assertTrue(all("code" not in json.dumps(event).lower() for event in events))

    def test_pairing_code_is_single_use_and_wrong_attempts_lock_challenge(self) -> None:
        challenge = self.store.create_pairing_challenge(now=2000, max_attempts=2)
        with self.assertRaises(CompanionSecurityError):
            self.store.request_pairing(
                challenge["challenge_id"], "wrong-code",
                device_name="Phone", device_fingerprint="c" * 64,
                claim_secret="d" * 48, now=2001,
            )
        with self.assertRaises(CompanionSecurityError):
            self.store.request_pairing(
                challenge["challenge_id"], "wrong-code",
                device_name="Phone", device_fingerprint="c" * 64,
                claim_secret="d" * 48, now=2002,
            )
        with self.assertRaises(CompanionSecurityError):
            self.store.request_pairing(
                challenge["challenge_id"], challenge["code"],
                device_name="Phone", device_fingerprint="c" * 64,
                claim_secret="d" * 48, now=2003,
            )

    def test_expired_pairing_challenge_and_request_are_rejected(self) -> None:
        challenge = self.store.create_pairing_challenge(now=3000, expires_in_seconds=60)
        with self.assertRaises(CompanionSecurityError):
            self.store.request_pairing(
                challenge["challenge_id"], challenge["code"],
                device_name="Phone", device_fingerprint="e" * 64,
                claim_secret="f" * 48, now=3061,
            )

    def test_pairing_inputs_reject_control_characters_and_invalid_fingerprint(self) -> None:
        challenge = self.store.create_pairing_challenge(now=4000)
        with self.assertRaises(ValueError):
            self.store.request_pairing(
                challenge["challenge_id"], challenge["code"],
                device_name="Phone\nInjected", device_fingerprint="a" * 64,
                claim_secret="g" * 48, now=4001,
            )
        with self.assertRaises(ValueError):
            self.store.request_pairing(
                challenge["challenge_id"], challenge["code"],
                device_name="Phone", device_fingerprint="not-a-fingerprint",
                claim_secret="g" * 48, now=4001,
            )

    def test_signed_read_request_binds_device_session_nonce_path_and_body(self) -> None:
        now = 1_800_000_000.0
        device_id, token = self._approved_device(now=now)
        timestamp_ms = int((now + 5) * 1000)
        session_id = "1" * 32
        nonce = "n" * 32
        path = "/api/v1/companion/status"
        body = b""
        signature = sign_companion_request(
            token, device_id=device_id, session_id=session_id, nonce=nonce,
            timestamp_ms=timestamp_ms, method="GET", path=path, body=body,
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id=session_id, nonce=nonce,
                timestamp_ms=timestamp_ms, method="GET", path=path,
                body=body, signature=signature, transport_verified=False, now=now + 5,
            )
        device = self.store.authenticate_request(
            device_id, token, session_id=session_id, nonce=nonce,
            timestamp_ms=timestamp_ms, method="GET", path=path,
            body=body, signature=signature, transport_verified=True, now=now + 5,
        )
        self.assertEqual(device["device_id"], device_id)
        self.assertEqual(device["permissions"], ["events.read", "status.read"])

    def test_signed_request_rejects_body_path_and_signature_tampering(self) -> None:
        now = 1_800_000_000.0
        device_id, token = self._approved_device(now=now)
        timestamp_ms = int((now + 5) * 1000)
        signature = sign_companion_request(
            token, device_id=device_id, session_id="2" * 32, nonce="x" * 32,
            timestamp_ms=timestamp_ms, method="GET",
            path="/api/v1/companion/status", body=b"",
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="2" * 32, nonce="x" * 32,
                timestamp_ms=timestamp_ms, method="GET",
                path="/api/v1/companion/status", body=b"tampered",
                signature=signature, transport_verified=True, now=now + 5,
            )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="2" * 32, nonce="y" * 32,
                timestamp_ms=timestamp_ms, method="GET",
                path="/api/v1/companion/events", body=b"",
                signature=signature, transport_verified=True, now=now + 5,
            )
        cursor_signature = sign_companion_request(
            token, device_id=device_id, session_id="7" * 32, nonce="c" * 32,
            timestamp_ms=timestamp_ms, method="GET",
            path="/api/v1/companion/events", body=b"", cursor="a" * 32,
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="7" * 32, nonce="c" * 32,
                timestamp_ms=timestamp_ms, method="GET",
                path="/api/v1/companion/events", body=b"",
                signature=cursor_signature, transport_verified=True,
                cursor="b" * 32, now=now + 5,
            )

    def test_signed_request_rejects_replay_stale_timestamp_and_mutations(self) -> None:
        now = 1_800_000_000.0
        device_id, token = self._approved_device(now=now)
        timestamp_ms = int((now + 5) * 1000)
        fields = dict(
            device_id=device_id, session_id="3" * 32, nonce="r" * 32,
            timestamp_ms=timestamp_ms, method="GET",
            path="/api/v1/companion/status", body=b"",
        )
        signature = sign_companion_request(token, **fields)
        self.store.authenticate_request(
            device_id, token, session_id=fields["session_id"], nonce=fields["nonce"],
            timestamp_ms=timestamp_ms, method="GET", path=fields["path"],
            body=b"", signature=signature, transport_verified=True, now=now + 5,
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id=fields["session_id"], nonce=fields["nonce"],
                timestamp_ms=timestamp_ms, method="GET", path=fields["path"],
                body=b"", signature=signature, transport_verified=True, now=now + 5,
            )

        stale_signature = sign_companion_request(
            token, device_id=device_id, session_id="4" * 32, nonce="s" * 32,
            timestamp_ms=int((now - 120) * 1000), method="GET",
            path="/api/v1/companion/status", body=b"",
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="4" * 32, nonce="s" * 32,
                timestamp_ms=int((now - 120) * 1000), method="GET",
                path="/api/v1/companion/status", body=b"",
                signature=stale_signature, transport_verified=True, now=now,
            )

        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="5" * 32, nonce="m" * 32,
                timestamp_ms=timestamp_ms, method="POST",
                path="/api/v1/companion/status", body=b"{}",
                signature="0" * 64, transport_verified=True, now=now + 5,
            )
    def test_revoked_device_cannot_authenticate(self) -> None:
        now = 1_800_000_000.0
        device_id, token = self._approved_device(now=now)
        self.assertTrue(self.store.revoke_device(device_id, revoked_by="host-user", now=now + 5))
        signature = sign_companion_request(
            token, device_id=device_id, session_id="6" * 32, nonce="v" * 32,
            timestamp_ms=int((now + 6) * 1000), method="GET",
            path="/api/v1/companion/status", body=b"",
        )
        with self.assertRaises(CompanionSecurityError):
            self.store.authenticate_request(
                device_id, token, session_id="6" * 32, nonce="v" * 32,
                timestamp_ms=int((now + 6) * 1000), method="GET",
                path="/api/v1/companion/status", body=b"",
                signature=signature, transport_verified=True, now=now + 6,
            )

    def test_pairing_audit_contains_lifecycle_without_secrets(self) -> None:
        now = 1_800_000_000.0
        device_id, token = self._approved_device(now=now)
        events = self.store.list_audit_events()
        encoded = json.dumps(events)
        self.assertIn("pairing.approved", encoded)
        self.assertIn("device.claimed", encoded)
        self.assertNotIn(token, encoded)
        self.assertNotIn("claim-secret", encoded)
        self.assertNotIn(hashlib.sha256(token.encode()).hexdigest(), encoded)


if __name__ == "__main__":
    unittest.main()
