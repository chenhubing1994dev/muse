"""Isolated adapter for the pinned Zi Wei 0.4.1 chart calculator.

The optional Alpha engine remains in its own venv. The adapter requires explicit
gender and school choices, verifies its derived calendar against Muse's shared
CalendarContext, and omits the original birth record from returned chart data.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from muse.calendar_context import (
    CalendarContext,
    CalendarContextIncomplete,
    CalendarContextUnsupported,
)


ENGINE_VERSION = "0.4.1"
ENGINE_WHEEL_SHA256 = "dab8b0d8eb58da8760c520ea81e1f4635ce275bf3197960d065c116fe1a3f492"
ENGINE_SOURCE_REFS = ("https://pypi.org/project/ziwei/0.4.1/",)
ENGINE_TIMEOUT_SECONDS = 20
_LEAP_POLICIES = {
    "next_month": "全月归下月",
    "current_month": "全月归本月",
    "split_month": "上半月归本月、下半月归下月",
}
_SIHUA_VARIANTS = {
    "zhongyi999": "排命盘口诀网页转录",
    "wangtingzhi_lecture": "王亭之讲义转录",
}
_BRIDGE_SCRIPT = (
    "import json, sys; "
    "from datetime import date, time; "
    "from importlib.metadata import version; "
    "from ziwei.birth import GregorianBirthRecord, Gender, TimePrecision; "
    "from ziwei.chart import create_chart; "
    "from ziwei.config import ChartConfig, DayRollover, LeapMonthPolicy, YearBoundary; "
    "from ziwei.calendar import LunarAlgorithm; "
    "from ziwei.ganzhi import year_ganzhi; "
    "from ziwei.render import render_text; "
    "from ziwei.sihua import SihuaVariant; "
    "from ziwei.timezone import IanaTimezone; "
    "r = json.loads(sys.argv[1]); "
    "precision = {'hour': TimePrecision.HOUR, 'minute': TimePrecision.MINUTE, "
    "'second': TimePrecision.SECOND, 'microsecond': TimePrecision.MICROSECOND}[r['precision']]; "
    "record = GregorianBirthRecord(birth_date=date(r['year'], r['month'], r['day']), "
    "birth_time=time(r['hour'], r['minute'], r['second'], r['microsecond']), "
    "time_precision=precision, timezone=IanaTimezone(key=r['timezone'], fold=r['fold']), "
    "gender=Gender(r['gender']), longitude_deg=r['longitude_deg']); "
    "leap_policy = {'next_month': LeapMonthPolicy.NEXT_MONTH, 'current_month': LeapMonthPolicy.CURRENT_MONTH, "
    "'split_month': LeapMonthPolicy.SPLIT_MONTH}[r['leap_month_policy']]; "
    "day_policy = {'midnight': DayRollover.SAME_CIVIL_DAY, 'zi_hour_23': DayRollover.LATE_ZI_NEXT_DAY}[r['day_rollover']]; "
    "year_policy = {'lichun': YearBoundary.LICHUN, 'lunar_new_year': YearBoundary.LUNAR_NEW_YEAR}[r['year_boundary']]; "
    "config = ChartConfig(leap_month_policy=leap_policy, day_rollover=day_policy, year_boundary=year_policy); "
    "variant = {'zhongyi999': SihuaVariant.ZHONGYI999, 'wangtingzhi_lecture': SihuaVariant.WANGTINGZHI_LECTURE}[r['sihua_variant']]; "
    "chart = create_chart(record, config, sihua_variant=variant, lunar_algorithm=LunarAlgorithm.ALGO3); "
    "calc = chart.calculation; yg = year_ganzhi(calc.ganzhi_year); "
    "palaces = [{'name': p.name.value, 'branch': p.dizhi.value, 'stem': p.tiangan.value, "
    "'ganzhi': p.ganzhi.tiangan.value + p.ganzhi.dizhi.value} for p in chart.palaces]; "
    "stars = [{'name': p.star.value, 'branch': p.dizhi.value, 'rule_id': p.rule_id, "
    "'concept_id': p.star.concept_id} for p in chart.stars]; "
    "decades = [{'branch': d.dizhi.value, 'direction': d.direction.value, "
    "'start_age': d.start_age, 'end_age': d.end_age, 'age_convention': d.age_convention, "
    "'rule_id': d.rule_id} for d in chart.decades]; "
    "sihua = {'year_stem': chart.sihua.year_tiangan.value, 'variant': chart.sihua.variant.value, "
    "'rule_id': chart.sihua.rule_id, 'lu': chart.sihua.lu.value, 'quan': chart.sihua.quan.value, "
    "'ke': chart.sihua.ke.value, 'ji': chart.sihua.ji.value}; "
    "summary = {'minggong': next(p.dizhi.value for p in chart.palaces if p.name.value == '命宫'), "
    "'shengong': chart.shengong.value, 'bureau': chart.bureau.value, 'bureau_number': chart.bureau.number}; "
    "calculation = {'lunar_year': calc.lunar_year, 'lunar_month': calc.month, 'lunar_day': calc.day, "
    "'is_leap_month': calc.is_leap_month, 'shichen': calc.shichen.value if calc.shichen else None, "
    "'ganzhi_year': calc.ganzhi_year, 'year_ganzhi': yg.tiangan.value + yg.dizhi.value, "
    "'lunar_algorithm': calc.lunar_algorithm.value, 'lunar_algorithm_version': calc.lunar_algorithm_version, "
    "'apparent_solar': calc.apparent_solar, 'day_rolled': calc.day_rolled}; "
    "out = {'version': version('ziwei'), 'chart': {'calculation': calculation, 'summary': summary, "
    "'palaces': palaces, 'stars': stars, 'sihua': sihua, 'decades': decades, 'render_text': render_text(chart)}}; "
    "print(json.dumps(out, ensure_ascii=True, separators=(',', ':')))"
)


class ZiweiUnavailable(RuntimeError):
    """Raised when the isolated Zi Wei interpreter is not available."""


class ZiweiExecutionError(RuntimeError):
    """Raised when Zi Wei fails or its result conflicts with Muse calendar facts."""


class ZiweiProvider:
    """Build a redacted Zi Wei natal chart with explicit school and birth policies."""

    bridge_script = _BRIDGE_SCRIPT

    def __init__(
        self,
        *,
        project_root: Path | str | None = None,
        executable: Path | str | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.executable = Path(executable).resolve() if executable is not None else self._default_executable()
        self._command_runner = command_runner or subprocess.run

    def _default_executable(self) -> Path:
        candidates = (
            self.project_root / "artifacts" / "ziwei-poc-venv" / "Scripts" / "python.exe",
            self.project_root / "artifacts" / "ziwei-poc-venv" / "bin" / "python",
        )
        return next((path for path in candidates if path.is_file()), Path(sys.executable))

    @staticmethod
    def _request(
        context: CalendarContext,
        *,
        gender: str,
        leap_month_policy: str,
        sihua_variant: str,
    ) -> dict[str, Any]:
        if not isinstance(gender, str) or gender not in {"男", "女"}:
            raise ValueError("Zi Wei full natal chart requires an explicitly supplied gender: 男 or 女")
        if leap_month_policy not in _LEAP_POLICIES:
            raise ValueError("leap_month_policy must be next_month, current_month, or split_month")
        if sihua_variant not in _SIHUA_VARIANTS:
            raise ValueError("sihua_variant must be zhongyi999 or wangtingzhi_lecture")
        if context.hour_branch is None:
            raise CalendarContextIncomplete(("hour_branch",))
        if context.lunar_year is None or context.lunar_month is None or context.lunar_day is None:
            raise CalendarContextIncomplete(("lunar_year", "lunar_month", "lunar_day"))
        if context.is_leap_month is None or context.year_ganzhi is None:
            raise CalendarContextIncomplete(("is_leap_month", "year_ganzhi"))
        if context.policy.true_solar_time:
            if context.longitude_deg is None:
                raise CalendarContextUnsupported("Zi Wei true-solar-time calculation requires longitude")
            if context.policy.lunar_date_basis != "apparent_solar":
                raise CalendarContextUnsupported(
                    "Zi Wei true-solar-time calculation uses apparent-solar date labels"
                )
        elif context.policy.lunar_date_basis != "civil_local":
            raise CalendarContextUnsupported(
                "Zi Wei civil-time calculation requires civil_local lunar-date basis"
            )
        local = context.local_instant
        if local.microsecond:
            precision = "microsecond"
        elif local.second:
            precision = "second"
        elif local.minute:
            precision = "minute"
        else:
            precision = "hour"
        return {
            "year": local.year,
            "month": local.month,
            "day": local.day,
            "hour": local.hour,
            "minute": local.minute,
            "second": local.second,
            "microsecond": local.microsecond,
            "precision": precision,
            "timezone": context.policy.timezone,
            "fold": local.fold,
            "gender": gender,
            "longitude_deg": context.longitude_deg if context.policy.true_solar_time else None,
            "year_boundary": context.policy.year_boundary,
            "day_rollover": context.policy.day_rollover,
            "leap_month_policy": leap_month_policy,
            "sihua_variant": sihua_variant,
            "lunar_algorithm": "ALGO3",
        }

    @staticmethod
    def _validate_chart(chart: Any, context: CalendarContext, sihua_variant: str) -> dict[str, Any]:
        if not isinstance(chart, dict):
            raise ZiweiExecutionError("Zi Wei engine returned a non-object chart")
        calculation = chart.get("calculation")
        if not isinstance(calculation, dict):
            raise ZiweiExecutionError("Zi Wei chart is missing its calculation block")
        expected = {
            "lunar_year": context.lunar_year,
            "lunar_month": context.lunar_month,
            "lunar_day": context.lunar_day,
            "is_leap_month": context.is_leap_month,
            "shichen": context.hour_branch,
            "year_ganzhi": context.year_ganzhi,
        }
        for field, value in expected.items():
            if calculation.get(field) != value:
                raise ZiweiExecutionError(f"Zi Wei {field} conflicts with Muse CalendarContext")
        if calculation.get("lunar_algorithm") != "混合":
            raise ZiweiExecutionError("Zi Wei chart used an unexpected lunar algorithm")
        if calculation.get("lunar_algorithm_version") != "0.8.0":
            raise ZiweiExecutionError("Zi Wei chart used an unexpected calendar dependency version")
        if calculation.get("apparent_solar") is not context.policy.true_solar_time:
            raise ZiweiExecutionError("Zi Wei chart solar-time mode conflicts with Muse policy")
        rollover_clock = context.true_solar_time if context.policy.true_solar_time else context.local_instant
        expected_rolled = (
            context.policy.day_rollover == "zi_hour_23"
            and rollover_clock is not None and rollover_clock.hour == 23
        )
        if calculation.get("day_rolled") is not expected_rolled:
            raise ZiweiExecutionError("Zi Wei chart day-rollover result conflicts with Muse policy")
        summary = chart.get("summary")
        if not isinstance(summary, dict) or not {"minggong", "shengong", "bureau", "bureau_number"}.issubset(summary):
            raise ZiweiExecutionError("Zi Wei chart is missing its natal summary")
        palaces = chart.get("palaces")
        if not isinstance(palaces, list) or len(palaces) != 12:
            raise ZiweiExecutionError("Zi Wei chart must contain twelve palaces")
        if len({item.get("name") for item in palaces if isinstance(item, dict)}) != 12:
            raise ZiweiExecutionError("Zi Wei chart palace names must be unique")
        if any(not isinstance(item, dict) or not {"name", "branch", "stem", "ganzhi"}.issubset(item) for item in palaces):
            raise ZiweiExecutionError("Zi Wei chart contains a malformed palace")
        stars = chart.get("stars")
        if not isinstance(stars, list) or len(stars) != 30:
            raise ZiweiExecutionError("Zi Wei chart must contain thirty star placements")
        if any(not isinstance(item, dict) or not {"name", "branch", "rule_id", "concept_id"}.issubset(item) for item in stars):
            raise ZiweiExecutionError("Zi Wei chart contains a malformed star placement")
        decades = chart.get("decades")
        if not isinstance(decades, list) or len(decades) != 12:
            raise ZiweiExecutionError("Zi Wei chart must contain twelve decade labels")
        sihua = chart.get("sihua")
        expected_sihua_id = {
            "zhongyi999": "variant.sihua.zhongyi999",
            "wangtingzhi_lecture": "variant.sihua.wangtingzhi-lecture",
        }[sihua_variant]
        if not isinstance(sihua, dict) or sihua.get("rule_id") != expected_sihua_id:
            raise ZiweiExecutionError("Zi Wei chart used an unexpected Four Transformations table")
        if not isinstance(chart.get("render_text"), str) or not chart["render_text"]:
            raise ZiweiExecutionError("Zi Wei chart is missing its text rendering")
        return chart

    def calculate(
        self,
        context: CalendarContext,
        *,
        gender: str,
        leap_month_policy: str,
        sihua_variant: str,
    ) -> dict[str, Any]:
        """Calculate a complete natal chart without returning or persisting raw birth data."""
        if not isinstance(context, CalendarContext):
            raise TypeError("context must be a CalendarContext")
        request = self._request(
            context, gender=gender, leap_month_policy=leap_month_policy,
            sihua_variant=sihua_variant,
        )
        if not self.executable.is_file():
            raise ZiweiUnavailable("the isolated Zi Wei Python interpreter is unavailable")
        command = [
            str(self.executable), "-c", self.bridge_script,
            json.dumps(request, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        ]
        try:
            completed = self._command_runner(
                command, cwd=str(self.project_root), shell=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=ENGINE_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ZiweiExecutionError("Zi Wei calculation exceeded its time limit") from error
        except OSError as error:
            raise ZiweiUnavailable("the isolated Zi Wei interpreter could not be started") from error
        if completed.returncode != 0:
            raise ZiweiExecutionError("Zi Wei calculation exited unsuccessfully")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise ZiweiExecutionError("Zi Wei engine returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("version") != ENGINE_VERSION:
            raise ZiweiExecutionError("Zi Wei runtime version does not match the pinned version")
        chart = self._validate_chart(payload.get("chart"), context, sihua_variant)
        return {
            "engine": "ziwei",
            "engine_version": ENGINE_VERSION,
            "wheel_sha256": ENGINE_WHEEL_SHA256,
            "calculation_policy": {
                "year_boundary": request["year_boundary"],
                "day_rollover": request["day_rollover"],
                "leap_month_policy": leap_month_policy,
                "sihua_variant": sihua_variant,
                "lunar_algorithm": "ALGO3",
                "true_solar_time": context.policy.true_solar_time,
            },
            "chart": chart,
            "calendar_context_fingerprint": context.fingerprint,
            "source_refs": list(ENGINE_SOURCE_REFS),
            "is_evidence": False,
            "epistemic_status": "traditional_calculation",
            "interpretation_status": "not_generated",
            "raw_birth_record_returned": False,
            "limitations": [
                "Upstream ziwei 0.4.1 is Alpha; Muse keeps it in an isolated optional environment.",
                "Gender, leap-month policy, year boundary, day rollover, and Four Transformations table must be explicit.",
                "The chart is a traditional-system calculation, not scientific evidence or a factual prediction.",
                "Muse does not persist the raw birth record or automatically write it to User Track.",
            ],
        }
