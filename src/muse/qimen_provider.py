"""Isolated adapter for the pinned qimen-rs 0.2.1 calculation engine.

This adapter accepts only the engine's documented civil-time, Lichun/Jieqi,
hour-school Chai-Bu rotating-chart conventions. It cross-checks the engine's
calendar output against Muse's shared CalendarContext before exposing a chart.
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
from muse.sexagenary import BRANCHES, STEMS


ENGINE_VERSION = "0.2.1"
ENGINE_WHEEL_SHA256 = "1a95ba7e086e46c7b8fb6c4f0726f390547853cc409ad900bef0489d443bf920"
ENGINE_SOURCE_REFS = (
    "https://pypi.org/project/qimen-rs/0.2.1/",
    "https://github.com/SpenserCai/qimen-rs",
)
ENGINE_TIMEOUT_SECONDS = 15
_TERM_CANONICAL = {
    "惊蛰": "驚蟄", "谷雨": "穀雨", "小满": "小滿",
    "芒种": "芒種", "处暑": "處暑",
}
_REQUIRED_PALACE_KEYS = {
    "number", "direction", "earth_stem", "heaven_stems",
    "stars", "door", "deity", "void_branches",
}
_BRIDGE_SCRIPT = (
    "import json, sys; "
    "from importlib.metadata import version; "
    "from qimen_rs import calculate; "
    "request = json.loads(sys.argv[1]); "
    "chart = calculate(request); "
    "print(json.dumps({'version': version('qimen-rs'), 'chart': chart}, "
    "ensure_ascii=True, separators=(',', ':')))"
)


class QimenUnavailable(RuntimeError):
    """Raised when the isolated qimen-rs interpreter is not available."""


class QimenExecutionError(RuntimeError):
    """Raised when qimen-rs fails or its output conflicts with Muse calendar facts."""


class QimenRsProvider:
    """Run qimen-rs in an isolated interpreter and verify its calendar result."""

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
            self.project_root / "artifacts" / "qimen-poc-venv" / "Scripts" / "python.exe",
            self.project_root / "artifacts" / "qimen-poc-venv" / "bin" / "python",
        )
        return next((path for path in candidates if path.is_file()), Path(sys.executable))

    @staticmethod
    def _request(context: CalendarContext) -> dict[str, Any]:
        policy = context.policy
        required = {
            "year_ganzhi": context.year_ganzhi,
            "month_ganzhi": context.month_ganzhi,
            "day_ganzhi": context.day_ganzhi,
            "hour_ganzhi": context.hour_ganzhi,
            "solar_term": context.solar_term,
            "lunar_year": context.lunar_year,
            "lunar_month": context.lunar_month,
            "lunar_day": context.lunar_day,
            "is_leap_month": context.is_leap_month,
        }
        missing = tuple(name for name, value in required.items() if value is None)
        if missing:
            raise CalendarContextIncomplete(missing)

        if policy.year_boundary != "lichun":
            raise CalendarContextUnsupported("qimen-rs 0.2.1 requires the Lichun year boundary")
        if policy.month_boundary != "jieqi":
            raise CalendarContextUnsupported("qimen-rs 0.2.1 requires Jie month boundaries")
        if policy.true_solar_time or policy.lunar_date_basis != "civil_local":
            raise CalendarContextUnsupported(
                "qimen-rs 0.2.1 uses civil local time and does not support true-solar-time inputs"
            )
        local = context.local_instant
        offset = local.utcoffset()
        if context.local_instant.microsecond:
            raise CalendarContextUnsupported("qimen-rs 0.2.1 does not accept microsecond precision")

        if offset is None:
            raise CalendarContextIncomplete(("timezone_utc_offset",))
        return {
            "year": local.year,
            "month": local.month,
            "day": local.day,
            "hour": local.hour,
            "minute": local.minute,
            "second": local.second,
            "utc_offset_minutes": int(offset.total_seconds() // 60),
            "day_boundary": "zi_start" if policy.day_rollover == "zi_hour_23" else "midnight",
        }

    @staticmethod
    def _validate_chart(chart: Any, request: dict[str, Any], context: CalendarContext) -> dict[str, Any]:
        if not isinstance(chart, dict):
            raise QimenExecutionError("qimen-rs returned a non-object chart")
        if chart.get("input") != request:
            raise QimenExecutionError("qimen-rs chart input does not match the requested civil time")
        calendar = chart.get("calendar")
        if not isinstance(calendar, dict):
            raise QimenExecutionError("qimen-rs chart is missing its calendar block")
        pillars = calendar.get("four_pillars")
        if not isinstance(pillars, dict):
            raise QimenExecutionError("qimen-rs chart is missing four pillars")
        expected = {
            "year": context.year_ganzhi,
            "month": context.month_ganzhi,
            "day": context.day_ganzhi,
            "hour": context.hour_ganzhi,
        }
        missing = tuple(name for name, value in expected.items() if value is None)
        if missing:
            raise CalendarContextIncomplete(tuple(f"{name}_ganzhi" for name in missing))
        for name, pair in expected.items():
            value = pillars.get(name)
            if not isinstance(value, dict):
                raise QimenExecutionError(f"qimen-rs chart is missing the {name} pillar")
            index = value.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 60:
                raise QimenExecutionError(f"qimen-rs returned an invalid {name} pillar index")
            computed = STEMS[index % 10] + BRANCHES[index % 12]
            if computed != pair:
                raise QimenExecutionError(f"qimen-rs {name} pillar {computed} conflicts with Muse {pair}")

        lunar = calendar.get("lunar_date")
        if not isinstance(lunar, dict):
            raise QimenExecutionError("qimen-rs chart is missing its lunar date")
        expected_lunar = {
            "year": context.lunar_year,
            "month": context.lunar_month,
            "day": context.lunar_day,
            "is_leap_month": context.is_leap_month,
        }
        missing_lunar = tuple(name for name, value in expected_lunar.items() if value is None)
        if missing_lunar:
            raise CalendarContextIncomplete(tuple(f"lunar_{name}" for name in missing_lunar))
        if any(lunar.get(name) != value for name, value in expected_lunar.items()):
            raise QimenExecutionError("qimen-rs lunar date conflicts with Muse CalendarContext")

        solar_term = calendar.get("solar_term")
        if not isinstance(solar_term, dict) or not isinstance(solar_term.get("name"), str):
            raise QimenExecutionError("qimen-rs chart is missing its solar-term name")
        actual_term = _TERM_CANONICAL.get(solar_term["name"], solar_term["name"])
        expected_term = _TERM_CANONICAL.get(context.solar_term, context.solar_term)
        if expected_term is None:
            raise CalendarContextIncomplete(("solar_term",))
        if actual_term != expected_term:
            raise QimenExecutionError("qimen-rs solar term conflicts with Muse CalendarContext")

        palaces = chart.get("palaces")
        if not isinstance(palaces, list) or len(palaces) != 9:
            raise QimenExecutionError("qimen-rs must return exactly nine palaces")
        numbers: list[int] = []
        for palace in palaces:
            if not isinstance(palace, dict) or not _REQUIRED_PALACE_KEYS.issubset(palace):
                raise QimenExecutionError("qimen-rs returned a malformed palace")
            number = palace.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 9:
                raise QimenExecutionError("qimen-rs returned an invalid palace number")
            numbers.append(number)
            if not isinstance(palace["heaven_stems"], list) or not isinstance(palace["stars"], list):
                raise QimenExecutionError("qimen-rs returned invalid palace layer arrays")
        if sorted(numbers) != list(range(1, 10)):
            raise QimenExecutionError("qimen-rs palace numbers must cover 1 through 9 exactly once")
        if chart.get("dun") not in {"yin", "yang"}:
            raise QimenExecutionError("qimen-rs returned an invalid Yin/Yang Dun value")
        ju = chart.get("ju")
        if isinstance(ju, bool) or not isinstance(ju, int) or not 1 <= ju <= 9:
            raise QimenExecutionError("qimen-rs returned an invalid Ju number")
        if not isinstance(chart.get("conventions"), dict) or not isinstance(chart.get("method"), str):
            raise QimenExecutionError("qimen-rs chart is missing calculation conventions")
        return chart

    def calculate(self, context: CalendarContext) -> dict[str, Any]:
        """Calculate a Chai-Bu rotating hour chart after strict policy validation."""
        if not isinstance(context, CalendarContext):
            raise TypeError("context must be a CalendarContext")
        request = self._request(context)
        if not self.executable.is_file():
            raise QimenUnavailable("the isolated qimen-rs Python interpreter is unavailable")
        command = [
            str(self.executable),
            "-c",
            self.bridge_script,
            json.dumps(request, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        ]
        try:
            completed = self._command_runner(
                command,
                cwd=str(self.project_root),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ENGINE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise QimenExecutionError("qimen-rs calculation exceeded its time limit") from error
        except OSError as error:
            raise QimenUnavailable("the isolated qimen-rs interpreter could not be started") from error
        if completed.returncode != 0:
            raise QimenExecutionError("qimen-rs calculation exited unsuccessfully")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise QimenExecutionError("qimen-rs returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("version") != ENGINE_VERSION:
            raise QimenExecutionError("qimen-rs runtime version does not match the pinned version")
        chart = self._validate_chart(payload.get("chart"), request, context)
        return {
            "engine": "qimen-rs",
            "engine_version": ENGINE_VERSION,
            "wheel_sha256": ENGINE_WHEEL_SHA256,
            "calculation_method": chart["method"],
            "calendar_request": request,
            "chart": chart,
            "calendar_context_fingerprint": context.fingerprint,
            "source_refs": list(ENGINE_SOURCE_REFS),
            "is_evidence": False,
            "epistemic_status": "traditional_calculation",
            "interpretation_status": "not_generated",
            "limitations": [
                "This adapter implements only hour-school Chai-Bu rotating charts, not Zhi Run, flying charts, or day/month/year charts.",
                "The engine uses local civil time; true-solar-time contexts are explicitly rejected.",
                "Muse and qimen-rs calendar pillars, lunar date, and solar term must match exactly or the chart is rejected.",
                "The wheel SHA-256 is pinned to the PyPI release; PyPI reported no build provenance for this release.",
                "The chart is a traditional-system calculation, not scientific evidence or a factual prediction.",
            ],
        }
