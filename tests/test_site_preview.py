import unittest
from muse.site_preview import patch_navigation, build_portfolio_page, local_html_references

class SitePreviewTests(unittest.TestCase):
    def test_navigation_links_to_portfolio_without_duplicate(self):
        source = '<nav><a href="./products.html">数字产品</a></nav>'
        patched = patch_navigation(source)
        self.assertIn('href="./portfolio.html"', patched)
        self.assertEqual(patch_navigation(patched), patched)

    def test_portfolio_video_is_manual_play_and_rights_blocked(self):
        page = build_portfolio_page()
        self.assertIn("<video controls playsinline", page)
        self.assertNotIn(" autoplay", page)
        self.assertIn("Noto Sans CJK HK", page)
        self.assertIn("SIL OFL 1.1", page)
        self.assertIn("内容、来源与编辑审校仍未完成", page)
        self.assertIn("noindex,nofollow", page)

    def test_local_reference_parser_ignores_external_and_anchor_links(self):
        refs = local_html_references('<a href="./products.html">P</a><a href="https://example.com">E</a><a href="#top">T</a><video src="./media/demo.mp4"></video>')
        self.assertEqual(refs, ["./products.html", "./media/demo.mp4"])

if __name__ == "__main__":
    unittest.main()
