"""Security tests for the isolated production Reviewer signing service."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from muse.video_reviewer_attestation import load_video_reviewer_public_keys_from_environment
from muse.video_reviewer_signer import ReviewerSignerError, VideoReviewerSigner


class VideoReviewerSignerTests(unittest.TestCase):
    def _key_environment(self, root: Path):
        private = Ed25519PrivateKey.generate()
        raw = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        key_path = root / "reviewer-private.pem"
        key_path.write_bytes(raw)
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        return private, {
            "MUSE_VIDEO_REVIEWER_PRIVATE_KEY_FILE": str(key_path.resolve()),
            "MUSE_VIDEO_REVIEWER_PRIVATE_KEY_SHA256": hashlib.sha256(raw).hexdigest(),
            "MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256": hashlib.sha256(public).hexdigest(),
            "MUSE_VIDEO_REVIEWER_KEY_ID": "reviewer-prod-key-01",
            "MUSE_VIDEO_REVIEWER_ID": "independent-reviewer",
            "MUSE_VIDEO_REVIEWER_TRUST_STORE_OUTPUT": str((root / "trust" / "reviewer-trust.json").resolve()),
        }

    def test_signer_refuses_to_start_without_separate_service_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _private, env = self._key_environment(root)
            with self.assertRaisesRegex(ReviewerSignerError, "distinct Reviewer and Worker"):
                VideoReviewerSigner(root, environ=env)

    def test_public_key_registration_is_fingerprinted_and_runtime_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _private, env = self._key_environment(root)
            signer = VideoReviewerSigner(root, environ=env, identity_check=lambda _path, _env: None)
            registration = signer.register_public_key()
            self.assertEqual(registration["key_id"], "reviewer-prod-key-01")
            self.assertEqual(registration["reviewer_id"], "independent-reviewer")
            self.assertEqual(registration["public_key_fingerprint_sha256"], env["MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256"])
            self.assertEqual(
                hashlib.sha256(Path(registration["trust_store_path"]).read_bytes()).hexdigest(),
                registration["trust_store_sha256"],
            )
            runtime_env = {
                "MUSE_VIDEO_REVIEWER_TRUST_STORE": registration["trust_store_path"],
                "MUSE_VIDEO_REVIEWER_TRUST_STORE_SHA256": registration["trust_store_sha256"],
            }
            with patch.dict(os.environ, runtime_env, clear=True):
                trusted = load_video_reviewer_public_keys_from_environment()
            self.assertEqual(trusted["reviewer-prod-key-01"]["reviewer_id"], "independent-reviewer")
            self.assertEqual(trusted["reviewer-prod-key-01"]["fingerprint_sha256"], registration["public_key_fingerprint_sha256"])

    def test_private_key_fingerprint_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _private, env = self._key_environment(root)
            env["MUSE_VIDEO_REVIEWER_PUBLIC_KEY_SHA256"] = "0" * 64
            with self.assertRaisesRegex(ReviewerSignerError, "pinned public-key fingerprint"):
                VideoReviewerSigner(root, environ=env, identity_check=lambda _path, _env: None)

    def test_registration_never_overwrites_an_existing_trust_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _private, env = self._key_environment(root)
            signer = VideoReviewerSigner(root, environ=env, identity_check=lambda _path, _env: None)
            first = signer.register_public_key()
            with self.assertRaisesRegex(ReviewerSignerError, "new non-symlink path"):
                signer.register_public_key(first["trust_store_path"])


if __name__ == "__main__":
    unittest.main()
