from __future__ import annotations

import unittest
from datetime import date, time, timedelta

from muse.sexagenary import (
    sexagenary_day,
    sexagenary_hour,
    sexagenary_month,
    sexagenary_year,
    hour_branch_from_clock,
)


class SexagenaryCycleTests(unittest.TestCase):
    def test_independent_calendar_golden_vector_for_2024_06_15(self) -> None:
        self.assertEqual(sexagenary_year(2024), "甲辰")
        self.assertEqual(sexagenary_month(2024, 4), "庚午")
        self.assertEqual(sexagenary_day(date(2024, 6, 15)), "庚戌")
        self.assertEqual(sexagenary_hour("庚", "午"), "壬午")

    def test_year_cycle_advances_and_wraps_deterministically(self) -> None:
        self.assertEqual(sexagenary_year(2023), "癸卯")
        self.assertEqual(sexagenary_year(2024), "甲辰")
        self.assertEqual(sexagenary_year(2025), "乙巳")
        self.assertEqual(sexagenary_year(2084), "甲辰")

    def test_day_cycle_contains_all_sixty_pairs_before_repeating(self) -> None:
        start = date(2024, 6, 15)
        pairs = [sexagenary_day(start + timedelta(days=offset)) for offset in range(60)]
        self.assertEqual(pairs[0], "庚戌")
        self.assertEqual(len(set(pairs)), 60)
        self.assertEqual(sexagenary_day(start + timedelta(days=60)), "庚戌")

    def test_month_stem_branch_progression_for_all_twelve_jie_months(self) -> None:
        pairs = [sexagenary_month(2024, index) for index in range(12)]
        self.assertEqual(pairs[0], "丙寅")
        self.assertEqual(pairs[4], "庚午")
        self.assertEqual(pairs[11], "丁丑")
        self.assertEqual(len(set(pair[1] for pair in pairs)), 12)

    def test_hour_branch_boundaries_follow_two_hour_clock(self) -> None:
        self.assertEqual(hour_branch_from_clock(time(22, 59)), "亥")
        self.assertEqual(hour_branch_from_clock(time(23, 0)), "子")
        self.assertEqual(hour_branch_from_clock(time(0, 59)), "子")
        self.assertEqual(hour_branch_from_clock(time(1, 0)), "丑")
        self.assertEqual(hour_branch_from_clock(time(12, 59)), "午")
        self.assertEqual(hour_branch_from_clock(time(13, 0)), "未")

    def test_hour_stem_uses_day_stem_group_and_branch_index(self) -> None:
        self.assertEqual(sexagenary_hour("庚", "子"), "丙子")
        self.assertEqual(sexagenary_hour("庚", "午"), "壬午")
        self.assertEqual(sexagenary_hour("辛", "子"), "戊子")
        self.assertEqual(sexagenary_hour("甲", "亥"), "乙亥")

    def test_invalid_cycle_inputs_fail_closed(self) -> None:
        for year in (True, "2024", 0):
            with self.subTest(year=year):
                with self.assertRaises(ValueError):
                    sexagenary_year(year)
        for month_index in (-1, 12, True):
            with self.subTest(month_index=month_index):
                with self.assertRaises(ValueError):
                    sexagenary_month(2024, month_index)
        for stem, branch in (("甲", "甲"), ("甲甲", "子"), ("bad", "子")):
            with self.subTest(stem=stem, branch=branch):
                with self.assertRaises(ValueError):
                    sexagenary_hour(stem, branch)


if __name__ == "__main__":
    unittest.main()
