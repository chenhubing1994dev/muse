from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.media_search import MediaAsset, classify_commercial_license, download_asset


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int) -> bytes:
        payload, self.payload = self.payload[:size], self.payload[size:]
        return payload


class CommercialLicensePolicyTests(unittest.TestCase):
    def test_cc0_is_commercially_allowed_but_still_requires_evidence_review(self):
        result = classify_commercial_license("CC0-1.0")
        self.assertEqual(result["commercial_use_status"], "commercial_allowed")
        self.assertFalse(result["attribution_required"])
        self.assertTrue(result["license_evidence_review_required"])

    def test_creative_commons_url_is_normalized(self):
        result = classify_commercial_license("https://creativecommons.org/licenses/by/4.0/")
        self.assertEqual(result["canonical_license"], "cc-by-4.0")
        self.assertEqual(result["commercial_use_status"], "attribution_required")
        self.assertTrue(result["attribution_required"])

    def test_share_alike_condition_is_preserved(self):
        result = classify_commercial_license("CC BY-SA 4.0")
        self.assertEqual(result["commercial_use_status"], "attribution_required")
        self.assertTrue(result["share_alike_required"])

    def test_noncommercial_and_all_rights_reserved_labels_are_prohibited(self):
        for label in ("CC BY-NC 4.0", "All Rights Reserved"):
            with self.subTest(label=label):
                self.assertEqual(classify_commercial_license(label)["commercial_use_status"], "prohibited")

    def test_no_derivatives_license_is_blocked_for_video_adaptation(self):
        result = classify_commercial_license("CC BY-ND 4.0")
        self.assertEqual(result["commercial_use_status"], "blocked_for_derivative_video_use")

    def test_unknown_or_missing_license_never_becomes_commercial_permission(self):
        for label in ("", "unknown", "MIT", "some random license"):
            with self.subTest(label=label):
                result = classify_commercial_license(label)
                self.assertEqual(result["commercial_use_status"], "unknown")
                self.assertTrue(result["license_evidence_review_required"])

    def test_download_manifest_keeps_reuse_blocked_until_human_review(self):
        payload = b"image-bytes-for-policy-test"
        asset = MediaAsset(
            title="licensed image",
            url="https://cdn.example/image.jpg",
            license="CC BY 4.0",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = download_asset(
                asset, directory, max_bytes=1024,
                opener=lambda *_args, **_kwargs: _Response(payload),
            )
            self.assertEqual(result["commercial_use_status"], "attribution_required")
            self.assertTrue(result["attribution_required"])
            self.assertEqual(result["license_status"], "unreviewed")
            self.assertFalse(result["reuse_allowed"])
            self.assertTrue(result["license_evidence_review_required"])
            self.assertTrue(Path(result["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
