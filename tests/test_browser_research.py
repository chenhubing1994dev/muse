from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from muse.browser_research import BrowserAugmentedSearchAdapter, HybridSourceReader
from muse.research_fetch import SourceReadError


class BrowserAugmentedSearchAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.primary = Mock()
        self.browser = Mock()
        self.primary.search.return_value = [
            {"title": "A", "url": "https://example.com/a", "snippet": "alpha", "engine": "primary"},
        ]
        self.browser.search_web.return_value = [
            {"title": "A duplicate", "url": "https://example.com/a", "snippet": "alpha", "engine": "ego-lite-google"},
            {"title": "B", "url": "https://example.org/b", "snippet": "beta", "engine": "ego-lite-google"},
        ]
        self.adapter = BrowserAugmentedSearchAdapter(
            self.primary, self.browser, workspace=self.workspace,
            allowed_queries={"public topic"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_merges_and_deduplicates_public_browser_search_results(self):
        result = self.adapter.search("public topic", limit=5)
        self.assertEqual([item["url"] for item in result], [
            "https://example.com/a", "https://example.org/b",
        ])
        self.browser.search_web.assert_called_once()
        self.assertEqual(result[1]["engine"], "ego-lite-google")

    def test_does_not_send_unapproved_or_unlisted_query_to_browser(self):
        result = self.adapter.search("private user details", limit=5)
        self.assertEqual(len(result), 1)
        self.browser.search_web.assert_not_called()

    def test_browser_results_are_interleaved_when_primary_results_fill_limit(self):
        self.primary.search.return_value = [
            {"title": f"Primary {i}", "url": f"https://primary.example/{i}", "engine": "primary"}
            for i in range(5)
        ]
        self.browser.search_web.return_value = [
            {"title": f"Browser {i}", "url": f"https://browser.example/{i}", "engine": "ego-lite-google"}
            for i in range(5)
        ]
        result = self.adapter.search("public topic", limit=4)
        self.assertEqual([item["engine"] for item in result], [
            "primary", "ego-lite-google", "primary", "ego-lite-google",
        ])

    def test_primary_results_survive_browser_failure_and_failure_is_recorded(self):
        self.browser.search_web.side_effect = RuntimeError("browser unavailable")
        result = self.adapter.search("public topic", limit=5)
        self.assertEqual(len(result), 1)
        failures = self.adapter.drain_failures()
        self.assertEqual(failures[0]["stage"], "browser_search")
        self.assertEqual(failures[0]["error"], "RuntimeError")


class HybridSourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.primary = Mock()
        self.browser = Mock()
        self.browser.doctor.return_value = {"available": True}
        self.adapter = HybridSourceReader(
            self.primary, self.browser, workspace=self.workspace,
            asset_download_enabled=False,
        )

    def tearDown(self):
        self.temp.cleanup()

    def capture_payload(self, url="https://example.com/article"):
        text = "Public browser-rendered research content " * 20
        return {
            "schema_version": 1,
            "provider": "ego-lite",
            "source": {
                "requested_url": url, "url": url, "title": "Browser Article",
                "captured_at": "2026-11-12T00:00:00+00:00",
                "page_sha256": "a" * 64, "screenshot_path": "page.png",
            },
            "text": text,
            "snapshot": "browser snapshot",
            "links": [{"text": "Citation", "url": "https://example.com/citation"}],
            "assets": [{"kind": "img", "url": "https://example.com/hero.png"}],
            "source_metadata": {"publisher": "Example Publisher", "authors": ["A. Author"]},
        }

    def test_primary_http_reader_is_kept_when_it_returns_usable_content(self):
        text = "HTTP research content " * 20
        self.primary.read.return_value = {
            "url": "https://example.com/article", "content_type": "text/html",
            "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "fetched_at": "2026-11-12T00:00:00+00:00", "truncated": False,
        }
        result = self.adapter.read("https://example.com/article")
        self.assertEqual(result["capture_method"], "http")
        self.browser.capture_url.assert_not_called()

    def test_browser_fallback_reads_dynamic_page_and_preserves_evidence_paths(self):
        self.primary.read.side_effect = SourceReadError("dynamic page not readable over HTTP")
        output = self.workspace / "data" / "browser-capture"
        def capture(url, **kwargs):
            output.mkdir(parents=True)
            (output / "capture.json").write_text(
                json.dumps(self.capture_payload(url)), encoding="utf-8",
            )
            return {
                "evidence_directory": str(output),
                "source": self.capture_payload(url)["source"],
                "screenshot_saved": True,
                "assets": self.capture_payload(url)["assets"],
            }
        self.browser.capture_url.side_effect = capture
        with patch("muse.browser_research._validate_public_url"):
            result = self.adapter.read("https://example.com/article")
        self.assertEqual(result["capture_method"], "ego-lite")
        self.assertEqual(result["source_metadata"]["publisher"], "Example Publisher")
        self.assertEqual(result["browser_evidence_directory"], str(output))
        self.assertEqual(result["browser_screenshot_path"], str(output / "page.png"))
        self.browser.capture_url.assert_called_once()
        self.assertTrue(self.browser.capture_url.call_args.kwargs["public_only"])

    def test_browser_fallback_refuses_private_or_unresolvable_target(self):
        self.primary.read.side_effect = SourceReadError("HTTP fetch failed")
        with patch("muse.browser_research._validate_public_url", side_effect=SourceReadError("private host")):
            with self.assertRaises(SourceReadError):
                self.adapter.read("http://127.0.0.1/private")
        self.browser.capture_url.assert_not_called()

    def test_browser_fallback_rejects_login_and_challenge_pages(self):
        self.primary.read.side_effect = SourceReadError("HTTP fetch failed")
        payload = self.capture_payload()
        payload["text"] = "Please sign in to continue " * 20
        output = self.workspace / "data" / "browser-capture"
        def capture(url, **kwargs):
            output.mkdir(parents=True)
            (output / "capture.json").write_text(json.dumps(payload), encoding="utf-8")
            return {"evidence_directory": str(output), "source": payload["source"], "assets": []}
        self.browser.capture_url.side_effect = capture
        with patch("muse.browser_research._validate_public_url"):
            with self.assertRaises(SourceReadError):
                self.adapter.read("https://example.com/article")
        self.assertFalse((output / "capture.json").exists())


if __name__ == "__main__":
    unittest.main()
