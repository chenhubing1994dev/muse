from __future__ import annotations

import unittest

from muse.source_identity import (
    build_source_identity_candidates,    find_exact_content_matches,

    extract_html_source_metadata,
    normalize_source_url,
)


class SourceIdentityTests(unittest.TestCase):
    def test_url_normalization_removes_tracking_but_preserves_meaningful_query(self):
        self.assertEqual(
            normalize_source_url("HTTPS://Example.COM:443/story?utm_source=x&id=7#part"),
            "https://example.com/story?id=7",
        )
        self.assertNotEqual(
            normalize_source_url("https://example.com/story?id=7"),
            normalize_source_url("https://example.com/story?id=8"),
        )
        self.assertEqual(
            normalize_source_url("../original?id=4", base_url="https://example.com/news/item"),
            "https://example.com/original?id=4",
        )
        with self.assertRaises(ValueError):
            normalize_source_url("file:///etc/passwd")

    def test_extracts_canonical_author_publisher_and_jsonld_lineage(self):
        html = """
        <html><head>
          <link rel="canonical" href="/story/42?utm_campaign=x">
          <meta name="citation_author" content="Ada Lovelace">
          <meta name="citation_publisher" content="Example Press">
          <meta property="og:site_name" content="Example Daily">
          <script type="application/ld+json">
          {
            "@context":"https://schema.org",
            "@type":"NewsArticle",
            "datePublished":"2026-11-08",
            "author":[{"@type":"Person","name":"Ada Lovelace"},{"@type":"Person","url":"https://person.example/profile"}],
            "publisher":{"@type":"Organization","name":"Example Press"},
            "citation":"https://source.example/research/1",
            "isBasedOn":{"@id":"https://origin.example/paper/2"}
          }
          </script>
        </head><body><p>Text</p></body></html>
        """
        metadata = extract_html_source_metadata(html, "https://mirror.example/copy")
        self.assertEqual(metadata["canonical_url"], "https://mirror.example/story/42")
        self.assertEqual(metadata["authors"], ["Ada Lovelace"])
        self.assertEqual(metadata["publisher"], "Example Press")
        self.assertEqual(metadata["site_name"], "Example Daily")
        self.assertEqual(metadata["citation_urls"], ["https://source.example/research/1"])
        self.assertEqual(metadata["is_based_on"], ["https://origin.example/paper/2"])
        self.assertEqual(metadata["published_at"], "2026-11-08")
        self.assertFalse(metadata["metadata_extraction"]["is_ownership_verification"])
        self.assertNotIn("https://person.example/profile", metadata["authors"])


    def test_invalid_jsonld_is_ignored_and_plain_citation_text_is_not_a_url(self):
        html = """
        <meta name="citation_reference" content="A. Author, A title, 2025">
        <script type="application/ld+json">{invalid json}</script>
        """
        metadata = extract_html_source_metadata(html, "https://example.com/page")
        self.assertEqual(metadata["citation_urls"], [])
        self.assertEqual(metadata["authors"], [])

    def test_source_identity_candidates_are_directional_bounded_and_explicitly_unverified(self):
        sources = [
            {
                "source_node_id": "source-a",
                "url": "https://publisher-a.example/copy?utm_source=feed",
                "metadata": {
                    "canonical_url": "https://original.example/story/9",
                    "publisher": "Example Press",
                    "authors": ["Ada Lovelace"],
                    "citation_urls": ["https://publisher-b.example/article"],
                },
            },
            {
                "source_node_id": "source-b",
                "url": "https://publisher-b.example/article",
                "metadata": {
                    "canonical_url": "https://original.example/story/9",
                    "publisher": "Example Press",
                    "authors": ["Ada Lovelace"],
                },
            },
        ]
        candidates = build_source_identity_candidates(sources)
        by_type = {item["candidate_type"]: item for item in candidates}
        self.assertIn("canonical_equivalent", by_type)
        self.assertIn("same_publisher", by_type)
        self.assertIn("same_author", by_type)
        self.assertEqual(by_type["cites"]["source_node_id"], "source-a")
        self.assertEqual(by_type["cites"]["related_source_node_id"], "source-b") 
        reversed_candidates = build_source_identity_candidates(list(reversed(sources)))
        original_symmetric = {
            kind: (item["source_node_id"], item["related_source_node_id"])
            for kind, item in by_type.items()
            if kind in {"canonical_equivalent", "same_publisher", "same_author"}
        }
        reversed_symmetric = {
            item["candidate_type"]: (item["source_node_id"], item["related_source_node_id"])
            for item in reversed_candidates
            if item["candidate_type"] in {"canonical_equivalent", "same_publisher", "same_author"}
        }
        self.assertEqual(original_symmetric, reversed_symmetric)
        cross_run_candidates = build_source_identity_candidates(
            [sources[0]], existing_sources=[sources[1]],
        )
        cross_run_types = {item["candidate_type"] for item in cross_run_candidates}
        self.assertTrue({"canonical_equivalent", "same_publisher", "same_author", "cites"}.issubset(cross_run_types))


        self.assertTrue(all(item["evidence"]["candidate_only"] for item in candidates))
        self.assertTrue(all(item["evidence"]["identity_is_not_ownership_proof"] for item in candidates))
        self.assertEqual(
            by_type["canonical_equivalent"]["evidence"]["matched_values"]["normalized_canonical_url"],
            "https://original.example/story/9",
        )
        self.assertEqual(
            by_type["same_publisher"]["evidence"]["matched_values"]["normalized_publisher_name"],
            "example press",
        )
        self.assertEqual(
            by_type["same_author"]["evidence"]["matched_values"]["matched_author_names"],
            ["ada lovelace"],
        )
        self.assertEqual(
            by_type["cites"]["evidence"]["matched_values"]["matched_reference_urls"],
            ["https://publisher-b.example/article"],
        )


    def test_historical_comparison_is_bounded_and_does_not_compare_old_sources_to_each_other(self):
        current = {
            "id": "new",
            "url": "https://new.example/story",
            "metadata": {"publisher": "Example Press"},
        }
        historical = [
            {"id": f"old-{index}", "url": f"https://old{index}.example/story",
             "metadata": {"publisher": "Example Press"}}
            for index in range(510)
        ]
        candidates = build_source_identity_candidates(
            [current], existing_sources=historical,
        )
        self.assertEqual(len(candidates), 500)
        self.assertTrue(all(item["source_node_id"] == "new" or item["related_source_node_id"] == "new"
                            for item in candidates))
        self.assertEqual(len({item["related_source_node_id"] for item in candidates}), 500)

    def test_exact_content_matches_include_history_but_exclude_truncated_snapshots(self):
        digest = "a" * 64
        matches = find_exact_content_matches(
            [{"id": "new", "url": "https://new.example/copy", "metadata": {
                "content_hash": digest, "truncated": False,
            }}],
            existing_sources=[
                {"id": "old", "url": "https://old.example/original", "metadata": {
                    "content_hash": digest, "truncated": False,
                }},
                {"id": "partial", "url": "https://partial.example/copy", "metadata": {
                    "content_hash": digest, "truncated": True,
                }},
                {"id": "bad-hash", "url": "https://bad.example/copy", "metadata": {
                    "content_hash": "not-a-hash", "truncated": False,
                }},
            ],
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["source_node_id"], "new")
        self.assertEqual(matches[0]["related_source_node_id"], "old")
        self.assertEqual(matches[0]["match_type"], "exact_fetched_text_hash")
        self.assertTrue(matches[0]["candidate_only"])
        self.assertTrue(matches[0]["independence_is_not_assessed"])

    def test_different_paths_are_not_merged_without_identity_metadata(self):
        candidates = build_source_identity_candidates([
            {"id": "a", "url": "https://example.com/story/1", "metadata": {}},
            {"id": "b", "url": "https://example.com/story/2", "metadata": {}},
        ])
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
