from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muse.calendar_context import (
    CalendarContextIncomplete,
    CalendarContextUnsupported,
    CalendarPolicy,
)
from muse.celestial_calendar_provider import CelestialCalendarProvider


@dataclass(frozen=True)
class FakeCivilDateTime:
    year: int
    month: int
    day: int
    fraction: float


class FakeJieqi(Enum):
    LICHUN = 0
    YUSHUI = 1
    JINGZHE = 2
    CHUNFEN = 3
    QINGMING = 4
    GUYU = 5
    LIXIA = 6
    XIAOMAN = 7
    MANGZHONG = 8
    XIAZHI = 9
    XIAOSHU = 10
    DASHU = 11
    LIQIU = 12
    CHUSHU = 13
    BAILU = 14
    QIUFEN = 15
    HANLU = 16
    SHUANGJIANG = 17
    LIDONG = 18
    XIAOXUE = 19
    DAXUE = 20
    DONGZHI = 21
    XIAOHAN = 22
    DAHAN = 23


class FakeLunarAlgorithm(Enum):
    ALGO1 = "algo1"
    ALGO2 = "algo2"
    ALGO3 = "algo3"


class FakeCalendar:
    CivilDateTime = FakeCivilDateTime
    Jieqi = FakeJieqi
    LunarAlgorithm = FakeLunarAlgorithm
    CivilScale = SimpleNamespace(UTC="utc")
    GeoLocation = staticmethod(lambda latitude_deg, longitude_deg: (latitude_deg, longitude_deg))
    HouseSystem = SimpleNamespace(EQUAL="equal")

    def __init__(self):
        self.last_gregorian_date = None
        self.last_location = None
        self.last_algorithm = None

    @staticmethod
    def _jd(civil):
        adjustment = (14 - civil.month) // 12
        year = civil.year + 4800 - adjustment
        month = civil.month + 12 * adjustment - 3
        jdn = civil.day + (153 * month + 2) // 5 + 365 * year + year // 4 - year // 100 + year // 400 - 32045
        return jdn - 0.5 + civil.fraction

    def chart_snapshot(self, civil, scale, location, system):
        self.last_location = location
        return SimpleNamespace(times=SimpleNamespace(jd_ut1=self._jd(civil)))

    def jieqi_moment(self, year, jieqi):
        dates = {
            FakeJieqi.LICHUN: (2, 4, 8.45), FakeJieqi.YUSHUI: (2, 19, 4.0),
            FakeJieqi.JINGZHE: (3, 5, 2.0), FakeJieqi.CHUNFEN: (3, 20, 3.0),
            FakeJieqi.QINGMING: (4, 4, 6.0), FakeJieqi.GUYU: (4, 19, 9.0),
            FakeJieqi.LIXIA: (5, 5, 2.0), FakeJieqi.XIAOMAN: (5, 20, 1.0),
            FakeJieqi.MANGZHONG: (6, 5, 4.15), FakeJieqi.XIAZHI: (6, 21, 4.85),
            FakeJieqi.XIAOSHU: (7, 6, 2.0), FakeJieqi.DASHU: (7, 22, 2.0),
            FakeJieqi.LIQIU: (8, 7, 2.0), FakeJieqi.CHUSHU: (8, 22, 2.0),
            FakeJieqi.BAILU: (9, 7, 2.0), FakeJieqi.QIUFEN: (9, 22, 2.0),
            FakeJieqi.HANLU: (10, 8, 2.0), FakeJieqi.SHUANGJIANG: (10, 23, 2.0),
            FakeJieqi.LIDONG: (11, 7, 2.0), FakeJieqi.XIAOXUE: (11, 22, 2.0),
            FakeJieqi.DAXUE: (12, 7, 2.0), FakeJieqi.DONGZHI: (12, 21, 2.0),
            FakeJieqi.XIAOHAN: (1, 5, 2.0), FakeJieqi.DAHAN: (1, 20, 2.0),
        }
        month, day, hour = dates[jieqi]
        return SimpleNamespace(moment_ut1=FakeCivilDateTime(year, month, day, hour / 24))

    def ut1_to_jd(self, moment):
        return self._jd(moment)

    def gregorian_to_lunar(self, algorithm, gregorian_date):
        self.last_algorithm = algorithm
        self.last_gregorian_date = gregorian_date
        if gregorian_date.month == 2 and gregorian_date.day < 10:
            return SimpleNamespace(year=2023, month=12, day=25, is_leap=False)
        if gregorian_date.month == 6 and gregorian_date.day < 6:
            return SimpleNamespace(year=2024, month=4, day=29, is_leap=False)
        if gregorian_date.month == 6:
            return SimpleNamespace(year=2024, month=5, day=10, is_leap=False)
        return SimpleNamespace(year=gregorian_date.year, month=1, day=1, is_leap=False)

    def apparent_solar_time(self, utc, longitude_deg):
        seconds = round(utc.fraction * 86400) + 8 * 3600 + 5 * 60 + 20
        value = datetime(utc.year, utc.month, utc.day) + timedelta(seconds=seconds)
        fraction = (value.hour * 3600 + value.minute * 60 + value.second) / 86400
        return FakeCivilDateTime(value.year, value.month, value.day, fraction)
class CelestialCalendarProviderTests(unittest.TestCase):
    def _instant(self):
        return datetime(2024, 6, 15, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_provider_resolves_solar_term_lunar_date_and_hour_branch(self) -> None:
        fake = FakeCalendar()
        provider = CelestialCalendarProvider(calendar_module=fake, provider_version="test-0.8")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight", true_solar_time=True,
        )
        context = provider.resolve(
            self._instant(), policy, latitude_deg=31.23, longitude_deg=121.47
        )
        self.assertEqual(context.solar_term, "芒種")
        self.assertEqual((context.lunar_year, context.lunar_month, context.lunar_day), (2024, 5, 10))
        self.assertFalse(context.is_leap_month)
        self.assertEqual(context.hour_branch, "午")
        self.assertEqual(context.provider, "celestial-calendar+muse-sexagenary")
        self.assertEqual(context.provider_version, "test-0.8+muse-sexagenary-v1")
        self.assertEqual(fake.last_algorithm, FakeLunarAlgorithm.ALGO3)
        self.assertEqual(fake.last_location, (31.23, 121.47))
        self.assertEqual(context.year_ganzhi, "甲辰")
        self.assertEqual(context.month_ganzhi, "庚午")
        self.assertEqual(context.day_ganzhi, "庚戌")
        self.assertEqual(context.hour_ganzhi, "壬午")
        self.assertEqual(context.to_ziwei_lunar_inputs()["shichen"], "午")
        self.assertEqual(context.to_qimen_args()["timezone"], "Asia/Shanghai")
        self.assertEqual(context.to_kinliuren_args(), {
            "jieqi": "芒種", "cmonth": "五", "daygangzhi": "庚戌", "hourgangzhi": "壬午",
        })

    def test_apparent_solar_date_basis_is_explicit_and_changes_lunar_date_input(self) -> None:
        fake = FakeCalendar()
        provider = CelestialCalendarProvider(calendar_module=fake, provider_version="test-0.8")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight", true_solar_time=True, lunar_date_basis="apparent_solar",
        )
        context = provider.resolve(
            self._instant(), policy, latitude_deg=31.23, longitude_deg=121.47
        )
        self.assertEqual(fake.last_gregorian_date, date(2024, 6, 15))
        self.assertEqual(context.policy.lunar_date_basis, "apparent_solar")

    def test_provider_rejects_out_of_range_utc_and_missing_true_solar_longitude(self) -> None:
        provider = CelestialCalendarProvider(calendar_module=FakeCalendar(), provider_version="test-0.8")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight",
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.resolve(datetime(1900, 1, 1, tzinfo=ZoneInfo("UTC")), policy)
        true_solar_policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight", true_solar_time=True,
        )
        with self.assertRaises(CalendarContextIncomplete):
            provider.resolve(self._instant(), true_solar_policy)

    def test_apparent_solar_date_basis_requires_true_solar_time(self) -> None:
        provider = CelestialCalendarProvider(calendar_module=FakeCalendar(), provider_version="test-0.8")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight", lunar_date_basis="apparent_solar",
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.resolve(self._instant(), policy)


    def test_lichun_year_boundary_uses_the_selected_policy(self) -> None:
        provider = CelestialCalendarProvider(calendar_module=FakeCalendar(), provider_version="test-0.8")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight",
        )
        before = provider.resolve(datetime(2024, 2, 4, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")), policy)
        after = provider.resolve(datetime(2024, 2, 4, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai")), policy)
        self.assertEqual((before.solar_term, before.year_ganzhi, before.month_ganzhi), ("大寒", "癸卯", "乙丑"))
        self.assertEqual((after.solar_term, after.year_ganzhi, after.month_ganzhi), ("立春", "甲辰", "丙寅"))

    def test_jieqi_month_boundary_differs_from_lunar_month_policy(self) -> None:
        provider = CelestialCalendarProvider(calendar_module=FakeCalendar(), provider_version="test-0.8")
        jieqi_policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight",
        )
        before = provider.resolve(datetime(2024, 6, 5, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")), jieqi_policy)
        after = provider.resolve(datetime(2024, 6, 5, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")), jieqi_policy)
        lunar_policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="lunar_month",
            day_rollover="midnight",
        )
        lunar_month_after = provider.resolve(datetime(2024, 6, 5, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")), lunar_policy)
        self.assertEqual((before.solar_term, before.month_ganzhi), ("小滿", "己巳"))
        self.assertEqual((after.solar_term, after.month_ganzhi), ("芒種", "庚午"))
        self.assertEqual(lunar_month_after.month_ganzhi, "己巳")

    def test_zi_hour_23_policy_changes_day_pillar_at_23(self) -> None:
        provider = CelestialCalendarProvider(calendar_module=FakeCalendar(), provider_version="test-0.8")
        instant = datetime(2024, 6, 15, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        midnight_policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight",
        )
        zi_policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="zi_hour_23",
        )
        midnight = provider.resolve(instant, midnight_policy)
        zi_start = provider.resolve(instant, zi_policy)
        self.assertEqual((midnight.day_ganzhi, midnight.hour_ganzhi), ("庚戌", "丙子"))
        self.assertEqual((zi_start.day_ganzhi, zi_start.hour_ganzhi), ("辛亥", "戊子"))

if __name__ == "__main__":
    unittest.main()
