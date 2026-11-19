"""Isolated adapter for the pinned Kinliuren 0.1.2.9 day-chart engine.

The third-party package remains outside Muse's default interpreter. Calls use a
fixed bridge, literal argv, a bounded timeout, and strict output-shape checks.
The returned traditional chart is not evidence and is never interpreted here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


from muse.process_utils import no_console_window_kwargs

from muse.calendar_context import CalendarContext


ENGINE_VERSION = "0.1.2.9"
ENGINE_SOURCE_REFS = (
    "https://pypi.org/project/kinliuren/0.1.2.9/",
    "https://github.com/kentang2017/kinliuren",
)
ENGINE_TIMEOUT_SECONDS = 15
_REQUIRED_CHART_KEYS = {
    "農曆月", "節氣", "日期", "格局", "日馬", "三傳", "四課",
    "天地盤", "地轉天盤", "地轉天將",
}
_THREE_PASS_KEYS = {"初傳", "中傳", "末傳"}
_FOUR_LESSON_KEYS = {"四課", "三課", "二課", "一課"}
_BRIDGE_SCRIPT = (
    "import json, sys; "
    "from importlib.metadata import version; "
    "from kinliuren import kinliuren; "
    "args = json.loads(sys.argv[1]); "
    "chart = kinliuren.Liuren(args['jieqi'], args['cmonth'], "
    "args['daygangzhi'], args['hourgangzhi']).result(0); "
    "print(json.dumps({'version': version('kinliuren'), 'chart': chart}, "
    "ensure_ascii=True, separators=(',', ':')))"
)


class KinliurenUnavailable(RuntimeError):
    """Raised when the isolated Kinliuren interpreter is not available."""


class KinliurenExecutionError(RuntimeError):
    """Raised when the pinned engine fails or returns an invalid chart."""


class KinliurenProvider:
    """Execute Kinliuren's deterministic chart calculation in an isolated venv."""

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
            self.project_root / "artifacts" / "kinliuren-poc-venv" / "Scripts" / "python.exe",
            self.project_root / "artifacts" / "kinliuren-poc-venv" / "bin" / "python",
        )
        return next((path for path in candidates if path.is_file()), Path(sys.executable))

    @staticmethod
    def _validate_chart(chart: Any, inputs: dict[str, str]) -> dict[str, Any]:
        if not isinstance(chart, dict) or not _REQUIRED_CHART_KEYS.issubset(chart):
            raise KinliurenExecutionError("Kinliuren returned an incomplete chart object")
        if chart.get("農曆月") != inputs["cmonth"]:
            raise KinliurenExecutionError("Kinliuren chart lunar month does not match its inputs")
        if chart.get("節氣") != inputs["jieqi"]:
            raise KinliurenExecutionError("Kinliuren chart solar term does not match its inputs")
        if chart.get("日期") != inputs["daygangzhi"] + "日" + inputs["hourgangzhi"] + "時":
            raise KinliurenExecutionError("Kinliuren chart Ganzhi label does not match its inputs")
        if not isinstance(chart.get("格局"), list) or len(chart["格局"]) != 2:
            raise KinliurenExecutionError("Kinliuren chart pattern must contain two labels")
        three_pass = chart.get("三傳")
        if not isinstance(three_pass, dict) or set(three_pass) != _THREE_PASS_KEYS:
            raise KinliurenExecutionError("Kinliuren chart must contain all three transmissions")
        if any(not isinstance(three_pass[key], list) or len(three_pass[key]) != 4 for key in _THREE_PASS_KEYS):
            raise KinliurenExecutionError("Kinliuren transmission entries must contain four fields")
        four_lessons = chart.get("四課")
        if not isinstance(four_lessons, dict) or set(four_lessons) != _FOUR_LESSON_KEYS:
            raise KinliurenExecutionError("Kinliuren chart must contain all four lessons")
        if any(not isinstance(four_lessons[key], list) or len(four_lessons[key]) != 2 for key in _FOUR_LESSON_KEYS):
            raise KinliurenExecutionError("Kinliuren lesson entries must contain two fields")
        plate = chart.get("天地盤")
        if not isinstance(plate, dict) or not {"天盤", "地盤", "天將"}.issubset(plate):
            raise KinliurenExecutionError("Kinliuren chart is missing heaven/earth plate arrays")
        if any(not isinstance(plate[key], list) or len(plate[key]) != 12 for key in ("天盤", "地盤", "天將")):
            raise KinliurenExecutionError("Kinliuren heaven/earth plate arrays must each contain twelve entries")
        for key in ("地轉天盤", "地轉天將"):
            mapping = chart.get(key)
            if not isinstance(mapping, dict) or len(mapping) != 12:
                raise KinliurenExecutionError(f"Kinliuren chart {key} must contain twelve mappings")
        return chart

    def calculate(self, context: CalendarContext) -> dict[str, Any]:
        """Calculate a day chart from an already-resolved Muse calendar context."""
        if not isinstance(context, CalendarContext):
            raise TypeError("context must be a CalendarContext")
        inputs = context.to_kinliuren_args()
        if not self.executable.is_file():
            raise KinliurenUnavailable("the isolated Kinliuren Python interpreter is unavailable")
        command = [
            str(self.executable),
            "-c",
            self.bridge_script,
            json.dumps(inputs, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
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
                **no_console_window_kwargs(),
            )
        except subprocess.TimeoutExpired as error:
            raise KinliurenExecutionError("Kinliuren calculation exceeded its time limit") from error
        except OSError as error:
            raise KinliurenUnavailable("the isolated Kinliuren interpreter could not be started") from error
        if completed.returncode != 0:
            raise KinliurenExecutionError("Kinliuren calculation exited unsuccessfully")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise KinliurenExecutionError("Kinliuren returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("version") != ENGINE_VERSION:
            raise KinliurenExecutionError("Kinliuren runtime version does not match the pinned version")
        chart = self._validate_chart(payload.get("chart"), inputs)
        return {
            "engine": "kinliuren",
            "engine_version": ENGINE_VERSION,
            "calculation_mode": "day_chart_mode_0",
            "inputs": inputs,
            "chart": chart,
            "calendar_context_fingerprint": context.fingerprint,
            "source_refs": list(ENGINE_SOURCE_REFS),
            "is_evidence": False,
            "epistemic_status": "traditional_calculation",
            "interpretation_status": "not_generated",
            "limitations": [
                "The upstream package has no bundled automated test suite or independently supplied golden charts.",
                "The upstream README Quickstart has a stale three-argument example; Muse uses the audited four-argument source signature.",
                "The engine does not accept a leap-month flag; Muse rejects leap-month contexts before execution.",
                "The returned chart is a traditional-system calculation, not scientific evidence or a factual prediction.",
            ],
        }
