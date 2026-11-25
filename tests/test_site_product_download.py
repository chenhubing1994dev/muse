from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "ui-prototype" / "products.html"

class SiteProductDownloadTests(unittest.TestCase):
    def test_free_product_has_a_real_local_download_action(self):
        source = PAGE.read_text(encoding="utf-8")
        self.assertIn('id="download-checklist"', source)
        self.assertIn("new Blob([checklist]", source)
        self.assertIn("anchor.download = 'muse-ai-verification-checklist.md'", source)
        self.assertIn("document.body.appendChild(anchor)", source)
        self.assertIn("window.setTimeout(() => URL.revokeObjectURL(url), 1000)", source)

    def test_download_is_offline_and_does_not_collect_user_data(self):
        source = PAGE.read_text(encoding="utf-8")
        self.assertNotIn("fetch(", source)
        self.assertNotIn("XMLHttpRequest", source)
        self.assertNotIn("localStorage", source)
        self.assertIn("UNVERIFIED / UNKNOWN", source)

    def test_paid_product_remains_a_hypothesis_not_a_checkout(self):
        source = PAGE.read_text(encoding="utf-8")
        self.assertIn("US$9 测试假设", source)
        self.assertIn("尚未验证的价格假设；不是当前售价", source)
        self.assertIn("内部审核 · 暂未销售", source)
        self.assertNotIn("checkout", source.lower())

if __name__ == "__main__":
    unittest.main()
