import json
import unittest

from muse.mempalace_projection import MemPalaceProjectionOutbox


class MemPalaceCheckpointAcknowledgementTests(unittest.TestCase):
    def test_added_record_is_confirmed(self):
        result = MemPalaceProjectionOutbox._validate_checkpoint_ack(
            {"structuredContent": {"added": [{"drawer_id": "drawer-1"}], "duplicates": [], "errors": []}},
            "exact payload",
        )
        self.assertEqual(result, {"outcome": "added", "drawer_id": "drawer-1"})

    def test_text_content_wrapper_is_supported(self):
        payload = {"added": [{"drawer_id": "drawer-2"}], "duplicates": [], "errors": []}
        result = MemPalaceProjectionOutbox._validate_checkpoint_ack(
            {"content": [{"type": "text", "text": json.dumps(payload)}]},
            "exact payload",
        )
        self.assertEqual(result["outcome"], "added")

    def test_exact_duplicate_is_accepted_for_idempotent_recovery(self):
        expected = "verbatim projected content"
        result = MemPalaceProjectionOutbox._validate_checkpoint_ack(
            {"structuredContent": {
                "added": [],
                "duplicates": [{"matches": [{"id": "drawer-existing", "content": expected}]}],
                "errors": [],
            }},
            expected,
        )
        self.assertEqual(result, {"outcome": "exact_duplicate", "drawer_id": "drawer-existing"})

    def test_semantically_similar_but_nonidentical_duplicate_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "exact content equality"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {
                    "added": [],
                    "duplicates": [{"matches": [{"id": "other", "content": "similar but different"}]}],
                    "errors": [],
                }},
                "expected exact payload",
            )

    def test_any_reported_error_prevents_delivery_success(self):
        with self.assertRaisesRegex(RuntimeError, "filing errors"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {
                    "added": [{"drawer_id": "drawer-1"}],
                    "duplicates": [],
                    "errors": [{"message": "partial failure"}],
                }},
                "exact payload",
            )

    def test_added_or_exact_duplicate_without_drawer_id_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "no drawer ID"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {"added": [{}], "duplicates": [], "errors": []}},
                "exact payload",
            )
        with self.assertRaisesRegex(RuntimeError, "no drawer ID"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {
                    "added": [],
                    "duplicates": [{"content": "exact payload"}],
                    "errors": [],
                }},
                "exact payload",
            )

    def test_multiple_or_unaccounted_results_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {"added": [], "duplicates": [], "errors": []}},
                "exact payload",
            )
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            MemPalaceProjectionOutbox._validate_checkpoint_ack(
                {"structuredContent": {
                    "added": [{"drawer_id": "a"}, {"drawer_id": "b"}],
                    "duplicates": [],
                    "errors": [],
                }},
                "exact payload",
            )


if __name__ == "__main__":
    unittest.main()
