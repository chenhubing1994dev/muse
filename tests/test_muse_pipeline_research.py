from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = ROOT / "pipeline" / "muse_pipeline.py"
SPEC = importlib.util.spec_from_file_location("muse_pipeline_under_test", PIPELINE_PATH)
assert SPEC is not None and SPEC.loader is not None
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


class TrendResearchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.topic = {
            "source": "bilibili",
            "title": "一條真實榜單內容",
            "view": 12345,
            "coin": 321,
            "favorite": 654,
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "retrieved_at": "2026-11-24T12:00:00+00:00",
            "signal_type": "platform_ranking_snapshot",
        }

    def test_fetch_bilibili_keeps_live_source_metrics_and_timestamp(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "code": 0,
            "data": {"list": [{
                "title": "真实榜单条目",
                "bvid": "BV1xx411c7mD",
                "stat": {"view": 9876, "coin": 123, "favorite": 456},
            }]},
        }).encode("utf-8")
        with patch.object(PIPELINE.urllib.request, "urlopen", return_value=response):
            items = PIPELINE.fetch_bilibili()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["view"], 9876)
        self.assertEqual(items[0]["coin"], 123)
        self.assertEqual(items[0]["favorite"], 456)
        self.assertEqual(items[0]["url"], "https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertTrue(items[0]["retrieved_at"])
        self.assertEqual(items[0]["signal_type"], "platform_ranking_snapshot")

    def test_fetch_bilibili_rejects_unsuccessful_api_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "code": -352, "message": "risk control",
        }).encode("utf-8")
        with patch.object(PIPELINE.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "unsuccessful"):
                PIPELINE.fetch_bilibili()

    def test_script_uses_observed_metrics_and_labels_them_as_trend_signals(self):
        script = PIPELINE.make_script(self.topic)
        self.assertIn("12,345", script["body"][0])
        self.assertIn("321", script["body"][0])
        self.assertEqual(script["research_state"], "trend_signal_only")
        self.assertTrue(script["fact_check_required"])
        self.assertEqual(script["publish_state"], "draft_only")
        self.assertNotIn("冲破百万", " ".join(script["body"]))

    def test_script_rejects_fallback_or_unattributed_topic(self):
        with self.assertRaisesRegex(ValueError, "live Bilibili"):
            PIPELINE.make_script({**self.topic, "source": "fallback"})
        with self.assertRaisesRegex(ValueError, "timestamp"):
            PIPELINE.make_script({key: value for key, value in self.topic.items() if key != "retrieved_at"})

    def test_empty_live_ranking_fails_closed_without_writing_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            original_data = PIPELINE.DATA
            PIPELINE.DATA = Path(directory)
            try:
                with patch.object(PIPELINE, "fetch_bilibili", return_value=[]):
                    with self.assertRaisesRegex(RuntimeError, "不会生成伪热点文案"):
                        PIPELINE.cmd_research()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                PIPELINE.DATA = original_data

    def test_provider_failure_fails_closed_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            original_data = PIPELINE.DATA
            PIPELINE.DATA = Path(directory)
            try:
                with patch.object(PIPELINE, "fetch_bilibili", side_effect=OSError("offline")):
                    with self.assertRaisesRegex(RuntimeError, "不会使用虚构样本"):
                        PIPELINE.cmd_research()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                PIPELINE.DATA = original_data


if __name__ == "__main__":
    unittest.main()
