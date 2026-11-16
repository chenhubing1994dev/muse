from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from muse.calendar_context import (
    CalendarContext,
    CalendarContextIncomplete,
    CalendarContextUnsupported,
    CalendarPolicy,
)


class CalendarContextTests(unittest.TestCase):
    def _context(self, **overrides):
        values = {
            "instant": datetime(2024, 6, 15, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            "policy": CalendarPolicy(
                timezone="Asia/Shanghai",
                year_boundary="lichun",
                month_boundary="jieqi",
                day_rollover="midnight",
                true_solar_time=True,
            ),
            "latitude_deg": 31.23,
            "longitude_deg": 121.47,
            "solar_term": "芒種",
            "lunar_year": 2024,
            "lunar_month": 5,
            "lunar_day": 10,
            "is_leap_month": False,
            "year_ganzhi": "甲辰",
            "month_ganzhi": "庚午",
            "day_ganzhi": "庚戌",
            "hour_ganzhi": "壬午",
            "hour_branch": "午",
            "true_solar_time": datetime(2024, 6, 15, 12, 35, 20),
            "provider": "synthetic-fixture",
            "provider_version": "test-v1",
            "source_refs": ("https://example.org/calendar-fixture",),
        }
        values.update(overrides)
        return CalendarContext(**values)

    def test_context_requires_aware_instant_and_explicit_calendar_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self._context(instant=datetime(2024, 6, 15, 12, 30))
        with self.assertRaisesRegex(ValueError, "unknown IANA timezone"):
            CalendarPolicy(
                timezone="Not/AZone",
                year_boundary="lichun",
                month_boundary="jieqi",
                day_rollover="midnight",
            )
        with self.assertRaises(ValueError):
            self._context(solar_term=[])
        with self.assertRaises(ValueError):
            self._context(hour_branch=[])
        with self.assertRaisesRegex(ValueError, "lunar_date_basis"):
            CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
                day_rollover="midnight", lunar_date_basis="ambiguous",
            )

    def test_kinliuren_input_adapter_maps_month_and_ganzhi_fields(self) -> None:
        context = self._context()
        self.assertEqual(context.to_kinliuren_args(), {
            "jieqi": "芒種",
            "cmonth": "五",
            "daygangzhi": "庚戌",
            "hourgangzhi": "壬午",
        })

    def test_kinliuren_adapter_rejects_leap_month_loss_and_missing_fields(self) -> None:
        with self.assertRaises(CalendarContextUnsupported):
            self._context(is_leap_month=True).to_kinliuren_args()
        with self.assertRaises(CalendarContextIncomplete):
            self._context(hour_ganzhi=None).to_kinliuren_args()

    def test_qimen_input_adapter_uses_explicit_local_timezone_and_longitude(self) -> None:
        result = self._context().to_qimen_args()
        self.assertEqual(result["date_str"], "2024-06-15 12:30")
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual(result["longitude_deg"], 121.47)
        self.assertTrue(result["true_solar_time_enabled"])
        with self.assertRaises(CalendarContextIncomplete):
            self._context(longitude_deg=None).to_qimen_args()

    def test_ziwei_input_adapter_requires_complete_lunar_date_and_hour_branch(self) -> None:
        result = self._context().to_ziwei_lunar_inputs()
        self.assertEqual(result, {
            "year": 2024,
            "month": 5,
            "day": 10,
            "is_leap_month": False,
            "shichen": "午",
        })
        with self.assertRaises(CalendarContextIncomplete):
            self._context(hour_branch=None).to_ziwei_lunar_inputs()

    def test_fingerprint_is_stable_and_sensitive_to_policy_and_input(self) -> None:
        first = self._context()
        second = self._context()
        self.assertEqual(first.fingerprint, second.fingerprint)
        changed = self._context(policy=CalendarPolicy(
            timezone="Asia/Shanghai",
            year_boundary="lunar_new_year",
            month_boundary="lunar_month",
            day_rollover="zi_hour_23",
            true_solar_time=False,
        ))
        self.assertNotEqual(first.fingerprint, changed.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
