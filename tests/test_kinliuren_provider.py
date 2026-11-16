from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muse.calendar_context import CalendarContext, CalendarPolicy, CalendarContextUnsupported
from muse.kinliuren_provider import (
    KinliurenExecutionError,
    KinliurenProvider,
    KinliurenUnavailable,
)


def _context(**overrides) -> CalendarContext:
    values = {
        "instant": datetime(2024, 6, 15, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        "policy": CalendarPolicy(
            timezone="Asia/Shanghai",
            year_boundary="lichun",
            month_boundary="jieqi",
            day_rollover="midnight",
        ),
        "solar_term": "芒種",
        "lunar_year": 2024,
        "lunar_month": 5,
        "lunar_day": 10,
        "is_leap_month": False,
        "year_ganzhi": "甲辰",
        "month_ganzhi": "庚午",
        "day_ganzhi": "庚戌",
        "hour_ganzhi": "壬午",
        "hour_branch": "午",        "provider": "fixture",
        "provider_version": "fixture-v1",
    }
    values.update(overrides)
    return CalendarContext(**values)


def _chart() -> dict:
    return {
        "農曆月": "五",
        "節氣": "芒種",
        "日期": "庚戌日壬午時",
        "格局": ["比用", "知一1"],
        "日馬": "申",        "三傳": {
            "初傳": ["卯", "勾", "父", "空"],
            "中傳": ["亥", "貴", "官", "辛"],
            "末傳": ["未", "常", "子", "丁"],
        },
        "四課": {
            "四課": ["亥卯", "貴"],
            "三課": ["卯未", "勾"],
            "二課": ["亥卯", "貴"],
            "一課": ["卯丁", "勾"],
        },
        "天地盤": {
            "天盤": list("丑寅卯辰巳午未申酉戌亥子"),
            "地盤": list("巳午未申酉戌亥子丑寅卯辰"),
            "天將": list("雀合勾龍空虎常玄陰后貴蛇"),
        },
        "地轉天盤": dict(zip("巳午未申酉戌亥子丑寅卯辰", "丑寅卯辰巳午未申酉戌亥子")),
        "地轉天將": dict(zip("巳午未申酉戌亥子丑寅卯辰", "雀合勾龍空虎常玄陰后貴蛇")),
    }


class KinliurenProviderTests(unittest.TestCase):
    def test_provider_passes_exact_four_calendar_arguments_and_returns_auditable_chart(self) -> None:
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"version": "0.1.2.9", "chart": _chart()}, ensure_ascii=False),
                stderr="",
            )

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        result = provider.calculate(_context())
        self.assertEqual(captured["command"][-2], provider.bridge_script)
        self.assertEqual(json.loads(captured["command"][-1]), {
            "jieqi": "芒種", "cmonth": "五",
            "daygangzhi": "庚戌", "hourgangzhi": "壬午",
        })
        self.assertEqual(captured["kwargs"]["shell"], False)
        self.assertEqual(captured["kwargs"]["timeout"], 15)
        self.assertEqual(result["engine_version"], "0.1.2.9")
        self.assertEqual(result["chart"]["格局"], ["比用", "知一1"])
        self.assertFalse(result["is_evidence"])
        self.assertEqual(result["interpretation_status"], "not_generated")

    def test_provider_rejects_leap_month_before_spawning_engine(self) -> None:
        def runner(*args, **kwargs):
            self.fail("unsupported leap month must not spawn the third-party engine")

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        with self.assertRaises(CalendarContextUnsupported):
            provider.calculate(_context(is_leap_month=True))

    def test_provider_rejects_unknown_engine_version_and_malformed_chart(self) -> None:
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "9.9.9", "chart": _chart(),
            }, ensure_ascii=False), stderr="")

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=runner,
        )
        with self.assertRaises(KinliurenExecutionError):
            provider.calculate(_context())

        def malformed_runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "version": "0.1.2.9", "chart": {"格局": ["賊尅"]},
            }, ensure_ascii=False), stderr="")

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=malformed_runner,
        )
        with self.assertRaises(KinliurenExecutionError):
            provider.calculate(_context())

    def test_provider_reports_timeout_and_nonzero_exit_without_fabricating_result(self) -> None:
        def timeout_runner(command, **kwargs):
            raise __import__("subprocess").TimeoutExpired(command, kwargs["timeout"])

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=timeout_runner,
        )
        with self.assertRaises(KinliurenExecutionError):
            provider.calculate(_context())

        def failed_runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="engine error")

        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path(sys.executable),
            command_runner=failed_runner,
        )
        with self.assertRaises(KinliurenExecutionError):
            provider.calculate(_context())

    def test_provider_reports_missing_interpreter_as_unavailable(self) -> None:
        provider = KinliurenProvider(
            project_root=Path("D:/Muse"),
            executable=Path("Z:/missing/python.exe"),
        )
        with self.assertRaises(KinliurenUnavailable):
            provider.calculate(_context())

    def test_isolated_poc_venv_runs_the_pinned_engine_sample_chart(self) -> None:
        root = Path(__file__).resolve().parents[1]
        interpreter = root / "artifacts" / "kinliuren-poc-venv" / "Scripts" / "python.exe"
        if not interpreter.is_file():
            self.skipTest("isolated Kinliuren POC interpreter is not present on this platform")
        provider = KinliurenProvider(project_root=root, executable=interpreter)
        result = provider.calculate(_context())
        self.assertEqual(result["engine_version"], "0.1.2.9")
        self.assertEqual(result["chart"]["格局"], ["比用", "知一1"])
        self.assertEqual(result["chart"]["日期"], "庚戌日壬午時")
        self.assertEqual(set(result["chart"]["三傳"]), {"初傳", "中傳", "末傳"})
        self.assertFalse(result["is_evidence"])

if __name__ == "__main__":
    unittest.main()
