from __future__ import annotations

import unittest
from unittest.mock import patch

from muse.research import BingRSSSearchAdapter, ResearchError


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/xml") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self, size=-1): return self.body[:size]


class BingRSSSearchAdapterTests(unittest.TestCase):
    def test_parses_bounded_rss_items_and_filters_unsafe_urls(self):
        body = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>Muse docs</title><link>https://example.com/docs</link>
            <description>Official project docs</description><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
          <item><title>Unsafe</title><link>mailto:unsafe@example.com</link><description>no</description></item>
        </channel></rss>"""
        adapter = BingRSSSearchAdapter(opener=lambda *_a, **_k: FakeResponse(body))
        results = adapter.search("Muse project", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Muse docs")
        self.assertEqual(results[0]["url"], "https://example.com/docs")
        self.assertEqual(results[0]["engine"], "bing-rss")

    def test_rejects_malformed_and_oversized_responses(self):
        for body in (b"<rss><channel>", b"<rss>" + b"x" * 1_000_001):
            adapter = BingRSSSearchAdapter(opener=lambda *_a, body=body, **_k: FakeResponse(body))
            with self.assertRaises(ResearchError):
                adapter.search("Muse")

    def test_rejects_invalid_queries_and_limits(self):
        adapter = BingRSSSearchAdapter(opener=lambda *_a, **_k: FakeResponse(b"<rss><channel/></rss>"))
        with self.assertRaises(ValueError): adapter.search(" ")
        with self.assertRaises(ValueError): adapter.search("Muse", limit=0)

    def test_uses_bing_rss_format_and_public_query(self):
        captured = {}
        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(b"<rss><channel/></rss>")
        BingRSSSearchAdapter(opener=opener).search("Muse")
        self.assertIn("format=rss", captured["url"])
        self.assertIn("q=Muse", captured["url"])
        self.assertEqual(captured["timeout"], 15.0)


class ArxivSearchAdapterTests(unittest.TestCase):
    def test_parses_recent_atom_preprints_and_marks_them_not_peer_reviewed(self):
        from muse.research import ArxivSearchAdapter

        body = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2611.00001</id>
            <published>2026-11-06T10:00:00Z</published>
            <title>Quantum measurement feedback experiment</title>
            <summary>We demonstrate an experimental measurement feedback protocol.</summary>
          </entry>
          <entry>
            <id>https://arxiv.org/abs/2611.00002</id>
            <published>2026-11-05T10:00:00Z</published>
            <title>Quantum information foundations</title>
            <summary>A theoretical analysis of quantum information.</summary>
          </entry>
        </feed>"""
        captured = {}
        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(body, "application/atom+xml")

        results = ArxivSearchAdapter(opener=opener).search("quantum information 2026", limit=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://arxiv.org/abs/2611.00001")
        self.assertEqual(results[0]["engine"], "arxiv-api")
        self.assertEqual(results[0]["source_kind"], "arxiv_preprint_not_peer_review_confirmation")
        self.assertIn("sortBy=submittedDate", captured["url"])
        self.assertIn("all%3Aquantum", captured["url"])
        self.assertEqual(captured["timeout"], 15.0)

    def test_quantum_aware_adapter_adds_arxiv_only_for_quantum_queries(self):
        from muse.research import QuantumAwareSearchAdapter

        class Primary:
            def __init__(self): self.calls = []
            def search(self, query, *, limit=8):
                self.calls.append(query)
                return [{"title": "primary", "url": "https://example.org/a"}]

        class Arxiv:
            def __init__(self): self.calls = []
            def search(self, query, *, limit=8):
                self.calls.append(query)
                return [{"title": "preprint", "url": "https://arxiv.org/abs/1",
                         "source_kind": "arxiv_preprint_not_peer_review_confirmation"}]

        primary, arxiv = Primary(), Arxiv()
        adapter = QuantumAwareSearchAdapter(primary=primary, arxiv=arxiv)
        regular = adapter.search("agent memory research", limit=2)
        self.assertEqual(len(regular), 1)
        self.assertEqual(arxiv.calls, [])
        quantum = adapter.search("quantum foundations", limit=2)
        self.assertEqual(len(quantum), 2)
        self.assertEqual(len(arxiv.calls), 1)

    def test_arxiv_api_metadata_wins_when_primary_search_returns_same_abstract(self):
        from muse.research import QuantumAwareSearchAdapter

        class Primary:
            def search(self, query, *, limit=8):
                return [{"title": "arXiv result", "url": "https://arxiv.org/abs/2611.00001",
                         "engine": "duckduckgo-html", "published_at": ""}]

        class Arxiv:
            def search(self, query, *, limit=8):
                return [{"title": "API result", "url": "https://arxiv.org/abs/2611.00001v1",
                         "engine": "arxiv-api", "published_at": "2026-11-06T10:00:00Z",
                         "source_kind": "arxiv_preprint_not_peer_review_confirmation"}]

        result = QuantumAwareSearchAdapter(primary=Primary(), arxiv=Arxiv()).search("quantum")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["engine"], "arxiv-api")
        self.assertEqual(result[0]["published_at"], "2026-11-06T10:00:00Z")
        self.assertTrue(result[0]["url"].endswith("v1"))
        self.assertEqual(result[0]["source_kind"], "arxiv_preprint_not_peer_review_confirmation")

    def test_arxiv_adapter_rejects_invalid_inputs_and_bad_atom(self):
        from muse.research import ArxivSearchAdapter, ResearchError

        adapter = ArxivSearchAdapter(opener=lambda *_a, **_k: FakeResponse(b"<feed>"))
        with self.assertRaises(ValueError):
            adapter.search(" ")
        with self.assertRaises(ValueError):
            adapter.search("quantum", limit=0)
        with self.assertRaises(ResearchError):
            adapter.search("quantum")


class CrossrefSearchAdapterTests(unittest.TestCase):
    def test_parses_doi_metadata_and_marks_review_status_unverified(self):
        import json
        from muse.research import CrossrefSearchAdapter

        payload = {
            "message": {
                "items": [
                    {
                        "title": ["Embodied interactive video lectures for improving learning comprehension and retention"],
                        "DOI": "10.1016/j.compedu.2017.10.005",
                        "published": {"date-parts": [[2018, 1, 1]]},
                        "type": "journal-article",
                        "abstract": "<jats:p>A controlled study of learning comprehension.</jats:p>",
                    },
                    {"title": ["Unsafe DOI"], "DOI": "not-a-doi", "type": "journal-article"},
                ]
            }
        }
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"), "application/json")

        results = CrossrefSearchAdapter(timeout=7, opener=opener).search(
            "interactive video lecture learning retention", limit=3
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://doi.org/10.1016/j.compedu.2017.10.005")
        self.assertEqual(results[0]["engine"], "crossref-rest")
        self.assertEqual(results[0]["source_kind"], "crossref_metadata_review_status_unverified")
        self.assertEqual(results[0]["published_at"], "2018")
        self.assertIn("controlled study", results[0]["snippet"])
        self.assertNotIn("<jats:p>", results[0]["snippet"])
        self.assertIn("query.bibliographic=", captured["url"])
        self.assertIn("rows=3", captured["url"])
        self.assertEqual(captured["timeout"], 7)


class QuantumAwareFallbackTests(unittest.TestCase):
    def test_crossref_is_used_when_primary_returns_no_results(self):
        from muse.research import QuantumAwareSearchAdapter

        class Primary:
            def search(self, query, *, limit=8):
                return []

        class Crossref:
            def __init__(self):
                self.calls = []

            def search(self, query, *, limit=8):
                self.calls.append((query, limit))
                return [{"title": "Research article", "url": "https://doi.org/10.1000/example",
                         "engine": "crossref-rest"}]

        crossref = Crossref()
        adapter = QuantumAwareSearchAdapter(primary=Primary(), crossref=crossref)
        results = adapter.search("interactive learning retention", limit=4)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["engine"], "crossref-rest")
        self.assertEqual(crossref.calls, [("interactive learning retention", 4)])


if __name__ == "__main__":
    unittest.main()
