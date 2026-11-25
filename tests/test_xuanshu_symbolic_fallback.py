"""Tests for the zero-dependency Xuanshu symbolic fallback (紫微 / 奇门 / 六壬)."""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from muse.xuanshu_fusion import EPISTEMIC_LABELS, fuse, validate_labels  # noqa: E402
from muse.xuanshu_symbolic_fallback import (  # noqa: E402
    SymbolicFallbackProvider,
    fallback_lens_payload,
    sexagenary_day,
)


class SexagenaryTests(unittest.TestCase):
    def test_known_day_pillars(self) -> None:
        self.assertEqual(sexagenary_day(dt.date(2000, 1, 1))["pillar"], "戊午")
        self.assertEqual(sexagenary_day(dt.date(1984, 2, 2))["pillar"], "丙寅")  # 1984-01-31 甲子 + 2

    def test_cycle_repeats_every_sixty_days(self) -> None:
        day = dt.date(2026, 11, 25)
        self.assertEqual(sexagenary_day(day)["pillar"], sexagenary_day(day + dt.timedelta(days=60))["pillar"])


class FallbackProviderTests(unittest.TestCase):
    def test_deterministic_per_seed_and_lens(self) -> None:
        for lens in ("ziwei", "qimen", "liuren"):
            provider = SymbolicFallbackProvider(lens)
            first = provider.calculate({"seed": "灵感", "moment": "2026-11-25"})
            second = provider.calculate({"seed": "灵感", "moment": "2026-11-25"})
            self.assertEqual(first, second)
            self.assertNotEqual(first, provider.calculate({"seed": "另一个种子", "moment": "2026-11-25"}))

    def test_rejects_unknown_lens_and_empty_seed(self) -> None:
        with self.assertRaises(ValueError):
            SymbolicFallbackProvider("astrology")
        with self.assertRaises(ValueError):
            SymbolicFallbackProvider("ziwei").calculate({"seed": ""})

    def test_payload_is_marked_symbolic(self) -> None:
        payload = fallback_lens_payload("qimen", "seed-1", reason="provider_not_configured")
        self.assertEqual(payload["mode"], "symbolic_fallback")
        self.assertEqual(payload["precision"], "symbolic_only_not_a_real_chart")
        self.assertTrue(payload["keywords"])


class FuseIntegrationTests(unittest.TestCase):
    def test_default_behaviour_is_unchanged(self) -> None:
        result = fuse("一个短片开场", seed="abc")
        for lens in ("ziwei", "qimen", "liuren"):
            self.assertEqual(result["lenses"][lens]["status"], "unavailable")

    def test_fallback_makes_every_lens_available_and_labelled(self) -> None:
        result = fuse("一个短片开场", seed="abc", symbolic_fallback=True, moment=dt.date(2026, 11, 25))
        validate_labels(result)
        self.assertEqual(set(result["computed_lenses"]), {"yijing", "ziwei", "qimen", "liuren"})
        for lens in ("ziwei", "qimen", "liuren"):
            body = result["lenses"][lens]
            self.assertEqual(tuple(body["epistemic_labels"]), EPISTEMIC_LABELS)
            self.assertFalse(body["is_evidence"])
            self.assertFalse(body["is_prediction"])
            self.assertEqual(body["mode"], "symbolic_fallback")
        self.assertGreaterEqual(len(result["prompts"]), 6)

    def test_real_provider_still_wins(self) -> None:
        class Provider:
            def calculate(self, context):
                return {"palace": "命宫", "star": "紫微"}

        result = fuse("测试", seed="abc", providers={"ziwei": Provider()}, context={"seed": "abc"},
                      symbolic_fallback=True)
        self.assertEqual(result["lenses"]["ziwei"]["engine"], "ziwei")
        self.assertEqual(result["lenses"]["qimen"]["mode"], "symbolic_fallback")


if __name__ == "__main__":
    unittest.main()
