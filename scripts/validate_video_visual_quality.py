from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

MODULES = (
    "tests.test_video_visual_quality",
    "tests.test_video_visual_evidence",
    "tests.test_video_human_review",
    "tests.test_video_product_api",
    "tests.test_video_publish_package",
    "tests.test_scene_analysis",
    "tests.test_video_product_builder",
    "tests.test_video_prompt_composer",
    "tests.test_video_prompt_studio",
    "tests.test_wan_video",
    "tests.test_video_task_worker",
    "tests.test_business_opportunities",
    "tests.test_autonomous_research",
)


def main() -> int:
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for module in MODULES:
        suite.addTests(loader.loadTestsFromName(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sample = ROOT / "ember-fire" / "火种不灭.mp4"
    if sample.is_file():
        from muse.video_visual_quality import analyze_video_visual_quality
        report = analyze_video_visual_quality(sample)
        print("REAL_SAMPLE_VISUAL_DIAGNOSTICS", report.get("status"),
              "samples=", report.get("sampled_frame_count"),
              "signals=", [item.get("code") for item in report.get("signals", [])])
    else:
        print("REAL_SAMPLE_VISUAL_DIAGNOSTICS unavailable: sample file not present")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
