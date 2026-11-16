from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muse.calendar_context import CalendarContext, CalendarPolicy, CalendarContextUnsupported
from muse.ziwei_provider import (
    ZiweiExecutionError,
    ZiweiProvider,
    ZiweiUnavailable,
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
            lunar_date_basis="civil_local",
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


def _chart() -> dict:
    return {
        "calculation": {
            "lunar_year": 2024, "lunar_month": 5, "lunar_day": 10,
            "is_leap_month": False, "shichen": "午", "ganzhi_year": 2024,
            "year_ganzhi": "甲辰", "lunar_algorithm": "混合",
            "lunar_algorithm_version": "0.8.0",
            "apparent_solar": False, "day_rolled": False,
        },
        "summary": {
            "minggong": "寅", "shengong": "申",
            "bureau": "火六局", "bureau_number": 6,
        },
        "palaces": [
            {"name": f"宫{i}", "branch": branch, "stem": "甲", "ganzhi": "甲子"}
            for i, branch in enumerate("子丑寅卯辰巳午未申酉戌亥", 1)
        ],
        "stars": [
            {"name": f"星{i}", "branch": "子", "rule_id": f"rule.{i}", "concept_id": f"concept.{i}"}
            for i in range(30)
        ],
        "sihua": {
            "year_stem": "甲", "variant": "排命盘口诀网页转录",
            "rule_id": "variant.sihua.zhongyi999",
            "lu": "廉贞", "quan": "破军", "ke": "武曲", "ji": "太阳",
        },
        "decades": [
            {"branch": branch, "direction": "顺行", "start_age": 6 + i * 10,
             "end_age": 15 + i * 10, "age_convention": "虚岁"}
            for i, branch in enumerate("寅卯辰巳午未申酉戌亥子丑")
        ],
        "render_text": "合成命盘，不含原始出生资料",
    }


class ZiweiProviderTests(unittest.TestCase):
    def test_provider_passes_explicit_birth_and_school_policies_and_returns_redacted_chart(self) -> None:
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            request = json.loads(command[-1])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"version": "0.4.1", "chart": _chart()}, ensure_ascii=True),
                stderr="",
            )

        provider = ZiweiProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        result = provider.calculate(
            _context(), gender="女",
            leap_month_policy="current_month",
            sihua_variant="zhongyi999",
        )
        request = json.loads(captured["command"][-1])
        self.assertEqual(request["gender"], "女")
        self.assertEqual(request["timezone"], "Asia/Shanghai")
        self.assertEqual(request["year_boundary"], "lichun")
        self.assertEqual(request["day_rollover"], "midnight")
        self.assertEqual(request["leap_month_policy"], "current_month")
        self.assertEqual(request["sihua_variant"], "zhongyi999")
        self.assertEqual(request["lunar_algorithm"], "ALGO3")
        self.assertEqual(captured["kwargs"]["shell"], False)
        self.assertEqual(captured["kwargs"]["timeout"], 20)
        self.assertEqual(result["engine_version"], "0.4.1")
        self.assertEqual(result["chart"]["sihua"]["rule_id"], "variant.sihua.zhongyi999")
        self.assertFalse(result["is_evidence"])
        self.assertEqual(result["interpretation_status"], "not_generated")
        self.assertNotIn("2024-06-15", json.dumps(result, ensure_ascii=False))

    def test_provider_rejects_invalid_gender_and_unknown_school_variants(self) -> None:
        provider = ZiweiProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=lambda *args, **kwargs: self.fail("invalid input must not spawn"),
        )
        for gender in ("未知", "other", None):
            with self.assertRaises(ValueError):
                provider.calculate(_context(), gender=gender, leap_month_policy="current_month", sihua_variant="zhongyi999")
        for policy in ("implicit", "bad"):
            with self.assertRaises(ValueError):
                provider.calculate(_context(), gender="女", leap_month_policy=policy, sihua_variant="zhongyi999")
        with self.assertRaises(ValueError):
            provider.calculate(_context(), gender="女", leap_month_policy="current_month", sihua_variant="implicit")

    def test_provider_rejects_calendar_policy_and_result_mismatches(self) -> None:
        provider = ZiweiProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=lambda *args, **kwargs: self.fail("unsupported policy must not spawn"),
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
                day_rollover="midnight", true_solar_time=True, lunar_date_basis="civil_local",
            )), gender="女", leap_month_policy="current_month", sihua_variant="zhongyi999")

        def mismatch(field, value):
            def runner(command, **kwargs):
                chart = _chart()
                chart["calculation"][field] = value
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"version": "0.4.1", "chart": chart}, ensure_ascii=True),
                    stderr="",
                )
            return runner

        for field, value in (("lunar_day", 9), ("year_ganzhi", "乙巳"), ("shichen", "巳")):
            provider = ZiweiProvider(
                project_root=Path("D:/Muse"), executable=Path(sys.executable),
                command_runner=mismatch(field, value),
            )
            with self.assertRaises(ZiweiExecutionError):
                provider.calculate(_context(), gender="女", leap_month_policy="current_month", sihua_variant="zhongyi999")

    def test_provider_rejects_unknown_version_malformed_chart_and_process_failures(self) -> None:
        def unknown(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "9.9.9", "chart": _chart(),
            }), stderr="")

        provider = ZiweiProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=unknown,
        )
        with self.assertRaises(ZiweiExecutionError):
            provider.calculate(_context(), gender="女", leap_month_policy="current_month", sihua_variant="zhongyi999")

        def malformed(command, **kwargs):
            chart = _chart()
            chart["palaces"] = chart["palaces"][:11]
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "0.4.1", "chart": chart,
            }), stderr="")

        provider = ZiweiProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=malformed,
        )
        with self.assertRaises(ZiweiExecutionError):
            provider.calculate(_context(), gender="女", leap_month_policy="current_month", sihua_variant="zhongyi999")

        import subprocess
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        provider = ZiweiProvider(
            project_root=Path("D:/Muse"), executable=Path(sys.executable),
            command_runner=timeout,
        )
        with self.assertRaises(ZiweiExecutionError):
            provider.calculate(_context(), gender="女", leap_month_policy="current_month", sihua_variant="zhongyi999")

    def test_isolated_ziwei_poc_venv_builds_chart_from_verified_calendar_context(self) -> None:
        root = Path(__file__).resolve().parents[1]
        interpreter = root / "artifacts" / "ziwei-poc-venv" / "Scripts" / "python.exe"
        if not interpreter.is_file():
            self.skipTest("isolated Zi Wei POC interpreter is not present on this platform")
        provider = ZiweiProvider(project_root=root, executable=interpreter)
        result = provider.calculate(
            _context(), gender="女",
            leap_month_policy="current_month",
            sihua_variant="zhongyi999",
        )
        self.assertEqual(result["engine_version"], "0.4.1")
        self.assertEqual(result["chart"]["calculation"]["year_ganzhi"], "甲辰")
        self.assertEqual(result["chart"]["calculation"]["lunar_day"], 10)
        self.assertEqual(result["chart"]["calculation"]["shichen"], "午")
        self.assertEqual(len(result["chart"]["palaces"]), 12)
        self.assertEqual(len(result["chart"]["stars"]), 30)
        self.assertEqual(len(result["chart"]["decades"]), 12)
        self.assertFalse(result["is_evidence"])


    def test_isolated_ziwei_poc_uses_verified_apparent_solar_time_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        interpreter = root / "artifacts" / "ziwei-poc-venv" / "Scripts" / "python.exe"
        if not interpreter.is_file():
            self.skipTest("isolated Zi Wei POC interpreter is not present on this platform")
        context = _context(
            policy=CalendarPolicy(
                timezone="Asia/Shanghai", year_boundary="lichun", month_boundary="jieqi",
                day_rollover="midnight", true_solar_time=True,
                lunar_date_basis="apparent_solar",
            ),
            true_solar_time=datetime(2024, 6, 15, 12, 35, 20),
        )
        result = ZiweiProvider(project_root=root, executable=interpreter).calculate(
            context, gender="女", leap_month_policy="current_month",
            sihua_variant="zhongyi999",
        )
        self.assertTrue(result["calculation_policy"]["true_solar_time"])
        self.assertTrue(result["chart"]["calculation"]["apparent_solar"])
        self.assertEqual(result["chart"]["calculation"]["shichen"], "午")
        self.assertEqual(result["chart"]["calculation"]["lunar_day"], 10)

if __name__ == "__main__":
    unittest.main()
