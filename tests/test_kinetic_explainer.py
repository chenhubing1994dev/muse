import tempfile
import unittest
from pathlib import Path

from muse.kinetic_explainer import SLIDES, SOCIAL_COPY, WIDTH, HEIGHT, FPS, wrap_text, render_scene, _font_path, _load_font


class KineticExplainerTests(unittest.TestCase):
    def test_master_is_eight_scenes_of_silent_vertical_content(self):
        self.assertEqual(len(SLIDES), 8)
        self.assertEqual((WIDTH, HEIGHT, FPS), (1080, 1920, 24))
        self.assertTrue(all(slide["title"] and slide["body"] and slide["tag"] for slide in SLIDES))

    def test_storyboard_covers_claim_source_counterevidence_and_uncertainty(self):
        content = " ".join(
            slide["eyebrow"] + slide["title"] + slide["body"] + slide["tag"]
            for slide in SLIDES
        )
        for term in ("拆開", "原始來源", "反面證據", "未證實", "NIST"):
            self.assertIn(term, content)
        self.assertNotIn("confabulation", content)

    def test_wrap_text_respects_pixel_width(self):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(image)
        font = _load_font(20)
        lines = wrap_text(draw, "人工智能可以答得好快，但未必答得啱。", font, 100)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(draw.textlength(line, font=font) <= 100 for line in lines))

    def test_social_copy_has_five_platform_specific_drafts_and_no_fake_link(self):
        for platform in ("YouTube Shorts", "TikTok", "Instagram Reels", "Bilibili", "小红书"):
            self.assertIn(platform, SOCIAL_COPY)
        self.assertIn("並非 NIST 官方方法或背書", SOCIAL_COPY)
        self.assertIn("不放占位链接", SOCIAL_COPY)

    def test_bundled_noto_font_and_sil_ofl_license_are_required(self):
        font_path = _font_path()
        self.assertEqual(font_path.name, "NotoSansCJKhk-VF.ttf")
        license_path = font_path.with_name("NotoCJK-OFL.txt")
        license_text = license_path.read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", license_text)
        self.assertIn("Version 1.1", license_text)
        regular = _load_font(24, bold=False)
        bold = _load_font(24, bold=True)
        self.assertTrue(regular.get_variation_axes())
        self.assertTrue(bold.get_variation_axes())

    def test_scene_renderer_creates_1080_by_1920_png(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "scene.png"
            render_scene(0, SLIDES[0], target)
            from PIL import Image
            with Image.open(target) as image:
                self.assertEqual(image.size, (1080, 1920))
                self.assertEqual(image.format, "PNG")
if __name__ == "__main__":
    unittest.main()
