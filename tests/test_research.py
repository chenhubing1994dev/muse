from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.research import DuckDuckGoHTMLSearchAdapter, ResearchError, ResearchNotebook, SearxngSearchAdapter


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self, size=-1):
        return self.payload[:size]


class WebResearchTests(unittest.TestCase):
    def test_search_normalizes_and_filters_untrusted_result_urls(self) -> None:
        payload = {"results": [
            {"title": " Muse Docs ", "url": "https://example.com/docs", "content": "Useful snippet", "engine": "demo"},
            {"title": "Bad scheme", "url": "javascript:alert(1)"},
            {"title": "Credential URL", "url": "https://user:pass@example.com/"},
            {"url": "https://example.com/no-title"},
        ]}
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse(json.dumps(payload).encode())
        adapter = SearxngSearchAdapter("https://search.example", opener=opener)
        results = adapter.search("Muse autonomous agents", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Muse Docs")
        self.assertEqual(results[0]["snippet"], "Useful snippet")
        self.assertIn("format=json", calls[0][0])

    def test_duckduckgo_search_unwraps_public_urls_and_keeps_snippets(self) -> None:
        html = (
            b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&rut=x">Muse docs</a>'
            b'<a class="result__snippet">Relevant evidence snippet</a>'
            b'<a class="result__a" href="javascript%3Aalert(1)">Unsafe result</a>'
        )
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse(html)
        adapter = DuckDuckGoHTMLSearchAdapter(opener=opener)
        results = adapter.search("Muse autonomous research", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.com/docs")
        self.assertEqual(results[0]["title"], "Muse docs")
        self.assertEqual(results[0]["snippet"], "Relevant evidence snippet")
        self.assertEqual(results[0]["engine"], "duckduckgo-html")
        self.assertIn("q=Muse", calls[0][0])

    def test_duckduckgo_search_rejects_invalid_queries_and_oversized_html(self) -> None:
        adapter = DuckDuckGoHTMLSearchAdapter(opener=lambda *_a, **_k: FakeResponse(b"x" * 2_000_001))
        with self.assertRaises(ValueError):
            adapter.search(" ")
        with self.assertRaises(ValueError):
            adapter.search("query", limit=21)
        with self.assertRaisesRegex(ResearchError, "2 MB"):
            adapter.search("query")

    def test_endpoint_and_query_validation(self) -> None:
        for endpoint in ("file:///tmp", "https://user:pass@example.com", "https://example.com/?token=x"):
            with self.assertRaises(ValueError):
                SearxngSearchAdapter(endpoint)
        adapter = SearxngSearchAdapter("https://search.example", opener=lambda *_args, **_kwargs: FakeResponse(b'{"results":[]}'))
        with self.assertRaises(ValueError):
            adapter.search(" ")
        with self.assertRaises(ValueError):
            adapter.search("hello", limit=21)

    def test_invalid_provider_payload_fails_closed(self) -> None:
        adapter = SearxngSearchAdapter("https://search.example", opener=lambda *_args, **_kwargs: FakeResponse(b"not json"))
        with self.assertRaises(ResearchError):
            adapter.search("Muse")

    def test_research_notebook_persists_search_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            results = [{"title": "Docs", "url": "https://example.com", "snippet": "Snippet", "engine": "demo", "published_at": ""}]
            with ResearchNotebook(path) as notebook:
                saved = notebook.save("Muse docs", results)
            self.assertEqual(saved["result_count"], 1)
            with ResearchNotebook(path) as notebook:
                row = notebook.connection.execute("SELECT query FROM research_runs WHERE run_id=?", (saved["run_id"],)).fetchone()
                source = notebook.connection.execute("SELECT title FROM research_sources WHERE run_id=?", (saved["run_id"],)).fetchone()
            self.assertEqual(row["query"], "Muse docs")
            self.assertEqual(source["title"], "Docs")


if __name__ == "__main__":
    unittest.main()
