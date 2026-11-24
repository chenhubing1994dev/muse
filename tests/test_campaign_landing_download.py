from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "outputs" / "campaign_videos_20261124_v2" / "campaign-landing-preview.html"


class CampaignLandingDownloadTests(unittest.TestCase):
    def test_free_checklist_download_keeps_blob_url_alive_after_click(self):
        source = PAGE.read_text(encoding="utf-8")
        self.assertIn("new Blob([checklist]", source)
        self.assertIn("document.body.appendChild(a)", source)
        self.assertIn("a.click();a.remove()", source)
        self.assertIn("window.setTimeout(()=>URL.revokeObjectURL(url),1000)", source)

    def test_free_download_is_local_and_paid_cta_stays_disabled(self):
        source = PAGE.read_text(encoding="utf-8")
        self.assertIn("不會提交資料，也不會連接第三方服務", source)
        self.assertIn("尚未開放購買", source)
        self.assertIn("disabled aria-disabled=\"true\"", source)
        self.assertNotIn("fetch(", source)

    def test_checklist_has_three_steps_and_uncertainty_states(self):
        source = PAGE.read_text(encoding="utf-8")
        for expected in ("## 1. 拆開主張", "## 2. 查原始來源", "## 3. 主動找反證", "UNVERIFIED / UNKNOWN"):
            self.assertIn(expected, source)


if __name__ == "__main__":
    unittest.main()
