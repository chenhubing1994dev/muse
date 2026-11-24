from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

from muse.web_portal import safe_static_file


ROOT = Path(__file__).resolve().parents[1]


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.append(dict(attrs))


class MonetizationLandingPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = (ROOT / "web-portal" / "ai-fact-check.html").read_text(encoding="utf-8")
        self.css = ROOT / "web-portal" / "ai-fact-check.css"
        self.free_download = ROOT / "web-portal" / "ai-fact-check-free.md"
        self.plan = (ROOT / "outputs" / "digital_products" / "ai-fact-check-starter-kit-v1"
                     / "SOCIAL_CONTENT_PLAN.md").read_text(encoding="utf-8")

    def test_landing_page_is_a_self_contained_local_static_page(self) -> None:
        self.assertTrue(self.css.is_file())
        self.assertTrue(self.free_download.is_file())
        self.assertIn('href="/ai-fact-check.css"', self.page)
        self.assertIn('name="robots" content="noindex,nofollow"', self.page)
        self.assertNotIn("<script", self.page.lower())

    def test_portal_static_resolver_serves_page_styles_and_free_download(self) -> None:
        static_root = ROOT / "web-portal"
        for route, filename in (
            ("/ai-fact-check.html", "ai-fact-check.html"),
            ("/ai-fact-check.css", "ai-fact-check.css"),
            ("/ai-fact-check-free.md", "ai-fact-check-free.md"),
        ):
            with self.subTest(route=route):
                self.assertEqual(safe_static_file(static_root, route), (static_root / filename).resolve())

    def test_free_download_link_resolves_to_existing_file(self) -> None:
        parser = _Links()
        parser.feed(self.page)
        download_links = [link for link in parser.links
                          if link.get("href") == "/ai-fact-check-free.md"]
        self.assertGreaterEqual(len(download_links), 1)
        self.assertTrue(all("download" in link for link in download_links))
    def test_paid_offer_is_not_misrepresented_as_live_checkout(self) -> None:
        self.assertIn("未正式上架", self.page)
        self.assertIn("未經市場驗證", self.page)
        self.assertIn("暫未開放付款或購買", self.page)
        self.assertNotIn("checkout", self.page.lower())
        self.assertNotIn("stripe", self.page.lower())
        self.assertNotIn("gumroad", self.page.lower())

    def test_source_attribution_and_non_endorsement_are_explicit(self) -> None:
        self.assertIn("NIST AI 600-1", self.page)
        self.assertIn("並非 NIST 官方方法", self.page)
        self.assertIn("https://www.nist.gov/publications/", self.page)

    def test_social_plan_is_a_draft_with_real_metrics_only(self) -> None:
        self.assertIn("未發布", self.plan)
        self.assertIn("未有實際成效數據", self.plan)
        self.assertIn("唔以播放量單獨判斷商業價值", self.plan)
        self.assertIn("發布前阻擋項目", self.plan)


if __name__ == "__main__":
    unittest.main()
