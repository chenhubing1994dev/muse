from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muse.calendar_context import CalendarContext, CalendarPolicy, CalendarContextUnsupported
from muse.zhenyi_bazi_provider import (
    ZhenyiBaziExecutionError, ZhenyiBaziProvider, ZhenyiBaziUnavailable,
)


def _context(**overrides):
    values = {
        "instant": datetime(1990, 6, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        "policy": CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun",
            month_boundary="jieqi", day_rollover="midnight",
            true_solar_time=False, lunar_date_basis="civil_local",
        ),
        "lunar_year": 1990, "lunar_month": 5, "lunar_day": 23,
        "is_leap_month": False,
        "year_ganzhi": "庚午", "month_ganzhi": "壬午",
        "day_ganzhi": "辛亥", "hour_ganzhi": "甲午",
        "hour_branch": "午", "provider": "fixture", "provider_version": "1",
        "source_refs": ("calendar-fixture-v1",),
    }
    values.update(overrides)
    return CalendarContext(**values)


def _payload(pillars=("庚午", "壬午", "辛亥", "甲午")):
    def p(value): return {"heavenly_stem": value[0], "earthly_branch": value[1]}
    return {"ok": True, "data": {
        "bazi": dict(zip(("year_pillar", "month_pillar", "day_pillar", "hour_pillar"), map(p, pillars))),
        "shi_shen": {"day_stem": "日元"}, "hidden_stems": {"day": []},
        "five_elements": {"metal": 1, "wood": 0, "water": 1, "fire": 2, "earth": 0},
        "nayin": {}, "kong_wang": {}, "day_master": {"stem": "辛"},
        "da_yun": [{"pillar": "癸未"}], "xiao_yun": [],
        "blind_judgment": {"ok": True, "engine_version": "1.9.0"},
        "paipan_rules": {"system": "真一盲派"}, "time_basis": "civil",
        "use_true_solar_time": False,
    }}


class ZhenyiBaziProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine_root = Path(self.temp.name)
        (self.engine_root / "dist").mkdir()
        (self.engine_root / "dist" / "api.js").write_text("// fixture entry", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_local_node_bridge_builds_chart_and_compares_all_four_pillars(self):
        captured = {}
        def runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            captured["request"] = json.loads(kwargs["input"])
            return SimpleNamespace(returncode=0, stdout=json.dumps(_payload(), ensure_ascii=False), stderr="")
        provider = ZhenyiBaziProvider(
            project_root=Path("D:/Muse"), engine_root=self.engine_root,
            executable="node", command_runner=runner,
        )
        result = provider.calculate(_context(), gender="男")
        self.assertTrue(result["calendar_match"])
        self.assertEqual(result["pillars"]["day"], "辛亥")
        self.assertEqual(captured["request"]["timezone_id"], "Asia/Shanghai")
        self.assertEqual(captured["request"]["gender"], "male")
        self.assertFalse(captured["request"]["use_true_solar_time"])
        self.assertNotIn("name", result["chart"])
        self.assertNotIn("civil_time", result["chart"])
        self.assertFalse(result["persisted_birth_data"])
        self.assertFalse(captured["kwargs"]["shell"])

    def test_calendar_mismatch_is_reported_without_overwriting_muse(self):
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(_payload(("辛未", "壬午", "辛亥", "甲午")), ensure_ascii=False), stderr="")
        provider = ZhenyiBaziProvider(engine_root=self.engine_root, executable="node", command_runner=runner)
        result = provider.calculate(_context(), gender="female")
        self.assertFalse(result["calendar_match"])
        self.assertEqual(result["differences"]["year"], {"muse": "庚午", "zhenyi": "辛未"})

    def test_real_reviewed_upstream_engine_matches_the_fixture(self):
        import shutil
        engine_root = Path(__file__).resolve().parents[1] / "third_party" / "zhenyi-bazi-review"
        node = shutil.which("node")
        if not node or not (engine_root / "dist" / "api.js").is_file():
            self.skipTest("reviewed Zhenyi-Bazi runtime or Node.js is not present")
        result = ZhenyiBaziProvider(engine_root=engine_root, executable=node).calculate(_context(), gender="male")
        self.assertTrue(result["calendar_match"], result["differences"])
        self.assertEqual(result["pillars"], {"year": "庚午", "month": "壬午", "day": "辛亥", "hour": "甲午"})
        self.assertEqual(result["chart"]["blind_judgment_status"], "completed")
        self.assertNotIn("civil_time", result["chart"])

    def test_60_nonboundary_calendar_vectors_match_muse_sexagenary_arithmetic(self):
        import shutil
        engine_root = Path(__file__).resolve().parents[1] / "third_party" / "zhenyi-bazi-review"
        node = shutil.which("node")
        if not node or not (engine_root / "dist" / "api.js").is_file():
            self.skipTest("reviewed Zhenyi-Bazi runtime or Node.js is not present")
        from muse.sexagenary import (
            hour_branch_from_clock, sexagenary_day, sexagenary_hour,
            sexagenary_month, sexagenary_year,
        )
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun",
            month_boundary="jieqi", day_rollover="midnight",
            true_solar_time=False, lunar_date_basis="civil_local",
        )
        month_index = {1: 11, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9, 12: 10}
        provider = ZhenyiBaziProvider(engine_root=engine_root, executable=node)
        checked = 0
        for year in range(2020, 2025):
            for month in range(1, 13):
                instant = datetime(year, month, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
                effective_year = year - 1 if month == 1 else year
                day = sexagenary_day(date(year, month, 15))
                branch = hour_branch_from_clock(time(12, 0))
                context = CalendarContext(
                    instant=instant, policy=policy,
                    year_ganzhi=sexagenary_year(effective_year),
                    month_ganzhi=sexagenary_month(effective_year, month_index[month]),
                    day_ganzhi=day, hour_ganzhi=sexagenary_hour(day[0], branch),
                    hour_branch=branch, provider="muse-sexagenary-reference",
                    provider_version="1",
                )
                result = provider.calculate(context, gender="male")
                self.assertTrue(result["calendar_match"], str(instant.date()) + ": " + str(result["differences"]))
                checked += 1
        self.assertEqual(checked, 60)

    def test_real_upstream_engine_matches_midnight_and_zi_hour_boundaries(self):
        import shutil
        from muse.sexagenary import hour_branch_from_clock, sexagenary_day, sexagenary_hour
        engine_root = Path(__file__).resolve().parents[1] / "third_party" / "zhenyi-bazi-review"
        node = shutil.which("node")
        if not node or not (engine_root / "dist" / "api.js").is_file():
            self.skipTest("reviewed Zhenyi-Bazi runtime or Node.js is not present")
        provider = ZhenyiBaziProvider(engine_root=engine_root, executable=node)
        cases = ((0, 0), (0, 59), (1, 0), (2, 59), (3, 0), (22, 59), (23, 0), (23, 59))
        checked = 0
        for hour, minute in cases:
            wall = datetime(1990, 6, 15, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
            civil_day = sexagenary_day(wall.date())
            branch = hour_branch_from_clock(time(hour, minute))
            # 真一 uses midnight as the day boundary; night-Zi (23:00–23:59)
            # retains the civil day pillar but derives its hour stem from next day.
            hour_day_stem = sexagenary_day(wall.date() + timedelta(days=1))[0] if hour == 23 else civil_day[0]
            hour_pillar = sexagenary_hour(hour_day_stem, branch)
            context = _context(
                instant=wall, day_ganzhi=civil_day, hour_ganzhi=hour_pillar,
                hour_branch=branch,
            )
            result = provider.calculate(context, gender="male")
            self.assertTrue(result["calendar_match"], f"{wall.isoformat()}: {result['differences']}")
            expected_variant = "night" if hour == 23 else "early" if hour == 0 else None
            self.assertEqual(result["chart"]["zi_hour_variant"], expected_variant or "")
            self.assertEqual(result["pillars"]["day"], civil_day)
            self.assertEqual(result["pillars"]["hour"], hour_pillar)
            checked += 1
        self.assertEqual(checked, len(cases))

    def test_real_upstream_engine_matches_muse_with_true_solar_time_enabled(self):
        import shutil
        from muse.calendar_context import CalendarPolicy
        from muse.celestial_calendar_provider import CelestialCalendarProvider, CelestialCalendarUnavailable
        engine_root = Path(__file__).resolve().parents[1] / "third_party" / "zhenyi-bazi-review"
        node = shutil.which("node")
        if not node or not (engine_root / "dist" / "api.js").is_file():
            self.skipTest("reviewed Zhenyi-Bazi runtime or Node.js is not present")
        try:
            ZoneInfo("Asia/Shanghai")
        except Exception as error:
            self.skipTest(f"IANA timezone database is unavailable: {error}")
        policy = CalendarPolicy(
            timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
            day_rollover="midnight", true_solar_time=True, lunar_date_basis="civil_local",
        )
        provider = ZhenyiBaziProvider(engine_root=engine_root, executable=node)
        vectors = (
            (datetime(1990, 6, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")), 31.23, 121.47),
            (datetime(2005, 3, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), 30.67, 104.07),
            (datetime(2024, 12, 15, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")), 43.82, 87.62),
        )
        try:
            calendar = CelestialCalendarProvider()
            contexts = [
                calendar.resolve(instant, policy, latitude_deg=latitude, longitude_deg=longitude)
                for instant, latitude, longitude in vectors
            ]
        except CelestialCalendarUnavailable as error:
            self.skipTest(str(error))
        for context in contexts:
            with self.subTest(instant=context.local_instant.isoformat(), longitude=context.longitude_deg):
                result = provider.calculate(context, gender="male")
                self.assertTrue(result["chart"]["use_true_solar_time"])
                self.assertEqual(result["chart"]["time_basis"], "true_solar")
                self.assertTrue(result["calendar_match"], result["differences"])
                self.assertEqual(result["chart"]["zi_hour_variant"], "")

    def test_rejects_ambiguous_and_nonexistent_dst_local_times(self):
        provider = ZhenyiBaziProvider(engine_root=self.engine_root, executable="node")
        policy = CalendarPolicy(
            timezone="America/New_York", year_boundary="lichun",
            month_boundary="jieqi", day_rollover="midnight",
            true_solar_time=False, lunar_date_basis="civil_local",
        )
        for fold in (0, 1):
            ambiguous = datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=fold)
            with self.assertRaises(CalendarContextUnsupported):
                provider.calculate(_context(instant=ambiguous, policy=policy), gender="male")
        nonexistent = datetime(2024, 3, 10, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(instant=nonexistent, policy=policy), gender="male")

    def test_rejects_unsupported_day_boundary_and_missing_gender(self):
        provider = ZhenyiBaziProvider(engine_root=Path("D:/engine"), executable="node")
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun",
                month_boundary="jieqi", day_rollover="zi_hour_23",
            )), gender="male")
        with self.assertRaises(ValueError):
            provider.calculate(_context(), gender="")

    def test_malformed_response_and_missing_runtime_fail_closed(self):
        provider = ZhenyiBaziProvider(engine_root=self.engine_root, executable="node",
            command_runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="{}", stderr=""))
        with self.assertRaises(ZhenyiBaziExecutionError):
            provider.calculate(_context(), gender="male")
        missing = ZhenyiBaziProvider(engine_root=self.engine_root, executable="__missing_node__")
        with self.assertRaises(ZhenyiBaziUnavailable):
            missing.calculate(_context(), gender="male")


if __name__ == "__main__":
    unittest.main()
