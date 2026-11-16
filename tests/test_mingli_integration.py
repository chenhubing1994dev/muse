from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from muse.calendar_context import CalendarContext, CalendarPolicy, CalendarContextIncomplete
from muse.inspiration import InspirationEngine
from muse.memory import MemoryStore
from muse.research_knowledge import ResearchKnowledgeStore


class _Provider:
    def __init__(self):
        self.chart = None
    def interpret_bazi(self, chart, *, question):
        self.chart = chart
        return {
            "engine": "mingli-ai", "model": "fixture-model",
            "upstream_repository": "https://github.com/ZHENYI-ORG/Mingli-AI",
            "interpretation": "传统框架解释：仅作灵感假设。",
            "calendar_context_fingerprint": chart["calendar_context_fingerprint"],
            "epistemic_status": "traditional_framework_interpretation_unverified",
            "is_evidence": False, "is_prediction": False,
        }


def _context(**overrides):
    values = {
        "instant": datetime(2024, 6, 15, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        "policy": CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun",
            month_boundary="jieqi", day_rollover="midnight",
        ),
        "solar_term": "芒种", "lunar_year": 2024, "lunar_month": 5,
        "lunar_day": 10, "is_leap_month": False,
        "year_ganzhi": "甲辰", "month_ganzhi": "庚午",
        "day_ganzhi": "庚戌", "hour_ganzhi": "壬午",
        "hour_branch": "午", "provider": "fixture", "provider_version": "1",
        "source_refs": ("calendar-fixture-v1",),
    }
    values.update(overrides)
    return CalendarContext(**values)


class MingliIntegrationTests(unittest.TestCase):
    def test_inspiration_uses_muse_calendar_and_explicitly_records_idea_not_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            with MemoryStore(root / "memory.sqlite3") as memory:
                with ResearchKnowledgeStore(root / "wiki.sqlite3") as wiki:
                    engine = InspirationEngine(memory, research_knowledge=wiki)
                    result = engine.interpret_bazi_with_mingli(
                        _context(), question="比较不同流派的结构解释", provider=provider,
                    )
                    self.assertEqual(provider.chart["year"], "甲辰")
                    self.assertEqual(provider.chart["hour"], "壬午")
                    self.assertNotIn("1990-", str(provider.chart))
                    recorded = engine.record_mingli_inspiration(result)
                    self.assertEqual(recorded["idea"]["node_type"], "idea")
                    self.assertEqual(recorded["source"]["node_type"], "source")
                    self.assertEqual(recorded["edge"]["edge_type"], "inspired_by")
                    self.assertFalse(recorded["persisted_birth_data"])
                    self.assertEqual(wiki.list_nodes(node_type="claim"), [])

    def test_zhenyi_crosscheck_is_required_before_mingli_interpretation(self):
        class _BaziProvider:
            def __init__(self, match=True): self.match = match
            def calculate(self, context, *, gender):
                return {
                    "engine_version": "2.0.2", "calendar_match": self.match,
                    "pillars": {"year": "甲辰", "month": "庚午", "day": "庚戌", "hour": "壬午"},
                    "chart": {"shi_shen": {}, "hidden_stems": {}, "five_elements": {}, "da_yun": [], "paipan_rules": {}},
                }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ResearchKnowledgeStore(root / "wiki.sqlite3") as wiki:
                engine = InspirationEngine(MemoryStore(":memory:"), research_knowledge=wiki)
                provider = _Provider()
                result = engine.interpret_bazi_with_mingli(
                    _context(), question="test", provider=provider,
                    bazi_provider=_BaziProvider(), gender="男",
                )
                recorded = engine.record_mingli_inspiration(result)
                self.assertEqual(recorded["source"]["metadata"]["bazi_engine"], "zhenyi-bazi")
                self.assertEqual(recorded["source"]["metadata"]["bazi_engine_version"], "2.0.2")
                self.assertTrue(result["calendar_match"])
                self.assertEqual(result["bazi_engine"], "zhenyi-bazi")
                self.assertEqual(provider.chart["zhenyi_chart"]["engine_version"], "2.0.2")
        mismatch_provider = _Provider()
        with self.assertRaises(Exception):
            engine.interpret_bazi_with_mingli(
                _context(), question="test", provider=mismatch_provider,
                bazi_provider=_BaziProvider(match=False), gender="男",
            )
        self.assertIsNone(mismatch_provider.chart)

    def test_incomplete_calendar_facts_fail_before_provider_call(self):
        engine = InspirationEngine(MemoryStore(":memory:"))
        provider = _Provider()
        with self.assertRaises(CalendarContextIncomplete):
            engine.interpret_bazi_with_mingli(
                _context(hour_ganzhi=None), question="test", provider=provider,
            )
        self.assertIsNone(provider.chart)


if __name__ == "__main__":
    unittest.main()
