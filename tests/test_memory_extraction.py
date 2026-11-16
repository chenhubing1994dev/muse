from __future__ import annotations

import json
import unittest

from muse.memory_extraction import LocalMemoryCandidateExtractor


class SequenceModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


class MemoryExtractionTests(unittest.TestCase):
    def test_extracts_only_valid_candidates_from_local_model_json(self) -> None:
        model = SequenceModel(json.dumps({"candidates": [
            {"content": "User prefers concise Cantonese", "project": "voice", "confidence": 0.9},
            {"content": "Muse project uses SQLite", "project": "Muse", "confidence": 0.8},
        ]}))
        candidates = LocalMemoryCandidateExtractor(model).extract(
            "Please remember that I prefer concise Cantonese replies"
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["project"], "voice")
        self.assertEqual(candidates[0]["confidence"], 0.9)
        self.assertIn("Do not include", model.calls[0][0]["content"])

    def test_malformed_model_output_fails_closed(self) -> None:
        extractor = LocalMemoryCandidateExtractor(SequenceModel("not json"))
        with self.assertRaises(ValueError):
            extractor.extract("some message")

    def test_rejects_oversized_candidate_payloads(self) -> None:
        extractor = LocalMemoryCandidateExtractor(SequenceModel(json.dumps({"candidates": [
            {"content": "x" * 5000}
        ]})))
        with self.assertRaises(ValueError):
            extractor.extract("some message")


if __name__ == "__main__":
    unittest.main()
