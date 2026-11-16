from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muse.calendar_context import CalendarContext, CalendarPolicy, CalendarContextIncomplete, CalendarContextUnsupported
from muse.qimen_provider import (
    QimenExecutionError,
    QimenRsProvider,
    QimenUnavailable,
)


def _context(**overrides) -> CalendarContext:
    values = {
        "instant": datetime(2024, 6, 15, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        "policy": CalendarPolicy(
            timezone="Asia/Shanghai",
            year_boundary="lichun",
            month_boundary="jieqi",
            day_rollover="midnight",
            true_solar_time=False,
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
        "provider": "verified-calendar-reference",
        "provider_version": "fixture-v1",
    }
    values.update(overrides)
    return CalendarContext(**values)


def _chart(request: dict) -> dict:
    return {
        "schema_version": "1.1",
        "input": dict(request),
        "calendar": {
            "four_pillars": {
                "year": {"index": 40, "stem": "jia", "branch": "chen"},
                "month": {"index": 6, "stem": "geng", "branch": "wu"},
                "day": {"index": 46, "stem": "geng", "branch": "xu"},
                "hour": {"index": 18, "stem": "ren", "branch": "wu"},
            },
            "lunar_date": {
                "year": 2024, "month": 5, "day": 10, "is_leap_month": False,
            },
            "solar_term": {"index": 11, "name": "芒种"},
            "next_solar_term": {"index": 12, "name": "夏至"},
        },
        "conventions": {
            "center_palace": "always_kun",
            "horse_basis": "hour",
            "tian_qin": "follows_tian_rui",
            "void_basis": "hour",
        },
        "dun": "yang",
        "ju": 6,
        "method": "shi_jia_chai_bu_zhuan_pan",
        "yuan": "upper",
        "leaders": {
            "door": "jing", "door_palace": 6, "star": "tian_zhu", "star_palace": 1,
        },
        "xun": {"head": {"stem": "jia", "branch": "xu"}, "hidden_stem": "ji", "void_branches": ["shen", "you"]},
        "palaces": [
            {
                "number": n, "direction": "center" if n == 5 else "north",
                "earth_stem": "yi", "heaven_stems": [{"stem": "ren", "source_palace": 1}],
                "stars": ["tian_zhu"], "door": "kai", "deity": "zhi_fu",
                "void_branches": [], "is_horse": False,
            }
            for n in range(1, 10)
        ],
    }


class QimenRsProviderTests(unittest.TestCase):
    def test_provider_maps_explicit_calendar_policy_and_returns_auditable_chart(self) -> None:
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            request = json.loads(command[-1])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"version": "0.2.1", "chart": _chart(request)}, ensure_ascii=True),
                stderr="",
            )

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        result = provider.calculate(_context())
        request = json.loads(captured["command"][-1])
        self.assertEqual(request, {
            "year": 2024, "month": 6, "day": 15, "hour": 12,
            "minute": 30, "second": 0, "utc_offset_minutes": 480,
            "day_boundary": "midnight",
        })
        self.assertEqual(captured["kwargs"]["shell"], False)
        self.assertEqual(captured["kwargs"]["timeout"], 15)
        self.assertEqual(result["engine_version"], "0.2.1")
        self.assertEqual(result["chart"]["method"], "shi_jia_chai_bu_zhuan_pan")
        self.assertEqual(result["chart"]["ju"], 6)
        self.assertFalse(result["is_evidence"])
        self.assertEqual(result["interpretation_status"], "not_generated")

    def test_provider_rejects_unrepresentable_true_solar_and_calendar_policies(self) -> None:
        def runner(*args, **kwargs):
            self.fail("unsupported calendar policy must not start the engine")

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
                day_rollover="midnight", true_solar_time=True,
            )))
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lunar_new_year", month_boundary="jieqi",
                day_rollover="midnight",
            )))
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="lunar_month",
                day_rollover="midnight",
            )))

    def test_provider_rejects_mismatched_pillars_lunar_date_and_solar_term(self) -> None:
        def runner_for(mutator):
            def runner(command, **kwargs):
                request = json.loads(command[-1])
                chart = _chart(request)
                mutator(chart)
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"version": "0.2.1", "chart": chart}, ensure_ascii=True),
                    stderr="",
                )
            return runner

        for mutate in (
            lambda chart: chart["calendar"]["four_pillars"]["day"].update(index=45),
            lambda chart: chart["calendar"]["lunar_date"].update(day=9),
            lambda chart: chart["calendar"]["solar_term"].update(name="夏至"),
        ):
            provider = QimenRsProvider(
                project_root=Path("D:/Muse"), executable=Path(sys.executable),
                command_runner=runner_for(mutate),
            )
            with self.assertRaises(QimenExecutionError):
                provider.calculate(_context())

    def test_provider_rejects_missing_calendar_facts_and_subsecond_precision_before_spawn(self) -> None:
        provider = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=lambda *args, **kwargs: self.fail("incomplete calendar facts must not spawn"),
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(instant=datetime(2024, 6, 15, 12, 30, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai"))))
        with self.assertRaises(CalendarContextIncomplete):
            provider.calculate(_context(day_ganzhi=None))

    def test_provider_rejects_unknown_version_and_malformed_nine_palace_chart(self) -> None:
        def unknown_version(command, **kwargs):
            request = json.loads(command[-1])
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "9.9.9", "chart": _chart(request),
            }), stderr="")

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=unknown_version,
        )
        with self.assertRaises(QimenExecutionError):
            provider.calculate(_context())

        def malformed(command, **kwargs):
            request = json.loads(command[-1])
            chart = _chart(request)
            chart["palaces"] = chart["palaces"][:8]
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "0.2.1", "chart": chart,
            }), stderr="")

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=malformed,
        )
        with self.assertRaises(QimenExecutionError):
            provider.calculate(_context())

    def test_provider_reports_timeout_nonzero_exit_and_missing_interpreter(self) -> None:
        import subprocess

        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=timeout_runner,
        )
        with self.assertRaises(QimenExecutionError):
            provider.calculate(_context())

        def failed_runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="engine error")

        provider = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=failed_runner,
        )
        with self.assertRaises(QimenExecutionError):
            provider.calculate(_context())

        missing = QimenRsProvider(
            project_root=Path("D:/Muse"), executable=Path("Z:/missing/python.exe"),
        )
        with self.assertRaises(QimenUnavailable):
            missing.calculate(_context())

    def test_isolated_qimen_rs_poc_matches_verified_2024_calendar_vector(self) -> None:
        root = Path(__file__).resolve().parents[1]
        interpreter = root / "artifacts" / "qimen-poc-venv" / "Scripts" / "python.exe"
        if not interpreter.is_file():
            self.skipTest("isolated qimen-rs POC interpreter is not present on this platform")
        provider = QimenRsProvider(project_root=root, executable=interpreter)
        result = provider.calculate(_context())
        self.assertEqual(result["engine_version"], "0.2.1")
        self.assertEqual(result["chart"]["method"], "shi_jia_chai_bu_zhuan_pan")
        self.assertEqual(result["chart"]["calendar"]["four_pillars"]["day"]["index"], 46)
        self.assertEqual(result["chart"]["calendar"]["lunar_date"]["day"], 10)
        self.assertEqual(result["chart"]["calendar"]["solar_term"]["name"], "芒种")
        self.assertEqual(len(result["chart"]["palaces"]), 9)
        self.assertFalse(result["is_evidence"])


if __name__ == "__main__":
    unittest.main()
