from __future__ import annotations

import sqlite3
import unittest

from muse.failure_taxonomy import MuseTaskError, classify_failure


class FailureTaxonomyTests(unittest.TestCase):
    def test_common_failure_types_map_to_stable_codes_and_guidance(self) -> None:
        cases = [
            (TimeoutError("private timeout detail"), "execution.timeout", True),
            (PermissionError("private permission detail"), "authorization.denied", False),
            (FileNotFoundError("private path detail"), "dependency.missing", False),
            (ConnectionError("private endpoint detail"), "provider.unavailable", True),
            (ValueError("private input detail"), "input.invalid", False),
            (sqlite3.OperationalError("database is locked"), "storage.busy", True),
            (RuntimeError("private exception detail"), "handler.unexpected", False),
        ]
        for error, expected_code, expected_retryable in cases:
            with self.subTest(expected_code=expected_code):
                failure = classify_failure(error)
                self.assertEqual(failure.code, expected_code)
                self.assertEqual(failure.retryable, expected_retryable)
                self.assertTrue(failure.guidance)
                self.assertNotIn(str(error), failure.as_result().__repr__())

    def test_handler_can_raise_an_explicit_bounded_failure_code(self) -> None:
        error = MuseTaskError(
            "provider.rate_limited", category="provider", retryable=True,
            guidance="Wait for the provider quota window to reset.",
        )
        failure = classify_failure(error)
        self.assertEqual(failure.code, "provider.rate_limited")
        self.assertTrue(failure.retryable)

    def test_custom_failure_code_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            MuseTaskError("invalid code with spaces", category="provider", retryable=True,
                          guidance="Retry later.")


if __name__ == "__main__":
    unittest.main()
