"""Isolated local adapter for the MIT-licensed Zhenyi-Bazi deterministic chart engine.

Runs the reviewed, prebuilt upstream TypeScript API in a child Node process.
No network service is opened; birth-derived input stays on the local machine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable

from muse.sexagenary import STEMS, BRANCHES

_VALID_GANZHI = frozenset(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))

from muse.calendar_context import (
    CalendarContext,
    CalendarContextIncomplete,
    CalendarContextUnsupported,
)

ENGINE_VERSION = "2.0.2"
ENGINE_COMMIT = "383a726437a81db2b87eed517ef5e6ccd30b7bd4"
ENGINE_REPOSITORY = "https://github.com/ZHENYI-ORG/Zhenyi-bazi"
ENGINE_TIMEOUT_SECONDS = 30
MAX_INPUT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

_BRIDGE_SCRIPT = r"""
const fs = require('node:fs');
const path = require('node:path');
const root = process.argv[1];
const raw = fs.readFileSync(0, 'utf8');
if (Buffer.byteLength(raw, 'utf8') > 16384) throw new Error('input too large');
const { computePaipan } = require(path.join(root, 'dist', 'api.js'));
const result = computePaipan(JSON.parse(raw));
process.stdout.write(JSON.stringify(result));
"""


class ZhenyiBaziUnavailable(RuntimeError):
    """Raised when Node or the reviewed Zhenyi-Bazi runtime is unavailable."""


class ZhenyiBaziExecutionError(RuntimeError):
    """Raised when Zhenyi-Bazi fails or returns a malformed chart."""


def _pillar(value: object, name: str) -> str:
    if not isinstance(value, dict):
        raise ZhenyiBaziExecutionError(f"Zhenyi-Bazi {name} pillar is missing")
    stem, branch = value.get("heavenly_stem"), value.get("earthly_branch")
    if (
        not isinstance(stem, str) or len(stem) != 1 or stem not in "甲乙丙丁戊己庚辛壬癸"
        or not isinstance(branch, str) or len(branch) != 1 or branch not in "子丑寅卯辰巳午未申酉戌亥"
        or stem + branch not in _VALID_GANZHI
    ):
        raise ZhenyiBaziExecutionError(f"Zhenyi-Bazi {name} pillar is malformed")
    return stem + branch


class ZhenyiBaziProvider:
    """Run the audited local deterministic chart engine and compare it to Muse."""

    bridge_script = _BRIDGE_SCRIPT

    @staticmethod
    def _resolve_node_executable() -> str:
        """Prefer a real node binary over a `.cmd`/`.bat` wrapper on PATH.

        Python's ``subprocess.run(..., shell=False)`` cannot feed stdin to or
        capture stdout from a Windows command-script wrapper reliably, which
        makes the bridge look like it returned empty JSON. Skip such shims and
        use the first real executable on PATH instead.
        """
        import os as _os

        script_suffixes = {".cmd", ".bat", ".ps1", ".sh"}
        seen: set[str] = set()
        found_wrapper = ""
        for directory in _os.environ.get("PATH", "").split(_os.pathsep):
            for name in ("node.exe", "node"):
                candidate = _os.path.join(directory, name)
                if not _os.path.isfile(candidate):
                    continue
                normalized = _os.path.normcase(_os.path.abspath(candidate))
                if normalized in seen:
                    continue
                seen.add(normalized)
                if _os.path.splitext(candidate)[1].lower() in script_suffixes:
                    found_wrapper = found_wrapper or candidate
                    continue
                return candidate
        return found_wrapper or shutil.which("node") or ""

    def __init__(
        self,
        *,
        project_root: Path | str | None = None,
        engine_root: Path | str | None = None,
        executable: str | Path | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
        self.engine_root = (
            Path(engine_root).resolve() if engine_root
            else self.project_root / "third_party" / "zhenyi-bazi-review"
        )
        supplied = str(executable) if executable else ""
        if supplied.lower().endswith((".cmd", ".bat", ".ps1", ".sh")):
            # A caller may pass shutil.which("node"), which prefers a command
            # wrapper over the real binary. Subprocess cannot capture its stdout.
            supplied = ""
        self.executable = supplied or self._resolve_node_executable()
        self._command_runner = command_runner or subprocess.run

    @staticmethod
    def _request(context: CalendarContext, *, gender: str) -> dict[str, Any]:
        if not isinstance(context, CalendarContext):
            raise TypeError("context must be a CalendarContext")
        if gender not in {"男", "女", "male", "female"}:
            raise ValueError("Zhenyi-Bazi requires an explicitly supplied gender")
        policy = context.policy
        if policy.year_boundary != "lichun" or policy.month_boundary != "jieqi":
            raise CalendarContextUnsupported("Zhenyi-Bazi requires Lichun year and Jieqi month boundaries")
        if policy.day_rollover != "midnight":
            raise CalendarContextUnsupported("Zhenyi-Bazi uses 00:00 day rollover with a separate night-Zi rule")
        if policy.lunar_date_basis != "civil_local":
            raise CalendarContextUnsupported("Zhenyi-Bazi request must be based on the civil local date")
        local = context.local_instant
        zone = ZoneInfo(policy.timezone)
        wall = local.replace(tzinfo=None)
        fold0 = wall.replace(tzinfo=zone, fold=0)
        fold1 = wall.replace(tzinfo=zone, fold=1)
        if fold0.utcoffset() != fold1.utcoffset():
            raise CalendarContextUnsupported("ambiguous daylight-saving local time cannot be represented by Zhenyi-Bazi input")
        round_trip = fold0.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip != wall:
            raise CalendarContextUnsupported("nonexistent daylight-saving local time cannot be represented by Zhenyi-Bazi input")
        if local.second or local.microsecond:
            raise CalendarContextUnsupported("Zhenyi-Bazi API accepts minute precision only")
        use_true_solar = policy.true_solar_time
        if use_true_solar and context.longitude_deg is None:
            raise CalendarContextIncomplete(("longitude_deg",))
        request: dict[str, Any] = {
            "name": "Muse local chart",
            "birthday": local.strftime("%Y-%m-%d"),
            "birth_time": local.strftime("%H:%M"),
            "gender": "male" if gender in {"男", "male"} else "female",
            "is_lunar": 0,
            "is_leap": 0,
            "use_true_solar_time": use_true_solar,
            "timezone_id": policy.timezone,
        }
        if use_true_solar:
            request["longitude"] = context.longitude_deg
        return request

    @staticmethod
    def _validate_response(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ZhenyiBaziExecutionError(
                "Zhenyi-Bazi rejected the chart request" + (f": {message[:240]}" if isinstance(message, str) else "")
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi response is missing data")
        bazi = data.get("bazi")
        if not isinstance(bazi, dict):
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi response is missing four pillars")
        pillars = {
            "year": _pillar(bazi.get("year_pillar"), "year"),
            "month": _pillar(bazi.get("month_pillar"), "month"),
            "day": _pillar(bazi.get("day_pillar"), "day"),
            "hour": _pillar(bazi.get("hour_pillar"), "hour"),
        }
        for key in ("shi_shen", "hidden_stems", "da_yun", "five_elements"):
            if key not in data:
                raise ZhenyiBaziExecutionError(f"Zhenyi-Bazi response is missing {key}")
        if not isinstance(data["da_yun"], list) or len(data["da_yun"]) > 12:
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi returned an invalid decade-cycle list")
        judgment = data.get("blind_judgment")
        judgment_status = (
            judgment.get("ok") is True if isinstance(judgment, dict) else False
        )
        # Only chart-derived fields survive. Names, exact civil/effective birth time,
        # timezone, profile IDs and original birth-date fields are intentionally omitted.
        chart = {
            "bazi": pillars,
            "shi_shen": data["shi_shen"],
            "hidden_stems": data["hidden_stems"],
            "five_elements": data["five_elements"],
            "nayin": data.get("nayin"),
            "kong_wang": data.get("kong_wang"),
            "day_master": data.get("day_master"),
            "minggong": data.get("minggong"),
            "shenggong": data.get("shenggong"),
            "taiyuan": data.get("taiyuan"),
            "qiyun": data.get("qiyun"),
            "jiaoyun": data.get("jiaoyun"),
            "da_yun": data["da_yun"],
            "xiao_yun": data.get("xiao_yun"),
            "blind_judgment": judgment if judgment_status else None,
            "blind_judgment_status": "completed" if judgment_status else "not_completed",
            "paipan_rules": data.get("paipan_rules"),
            "time_basis": data.get("time_basis"),
            "use_true_solar_time": data.get("use_true_solar_time"),
            "zi_hour_variant": data.get("zi_hour_variant"),
        }
        return {"pillars": pillars, "chart": chart, "blind_judgment_status": chart["blind_judgment_status"]}

    def calculate(self, context: CalendarContext, *, gender: str) -> dict[str, Any]:
        request = self._request(context, gender=gender)
        if not self.executable:
            raise ZhenyiBaziUnavailable("Node.js was not found; install Node.js 20+ to run Zhenyi-Bazi")
        entry = self.engine_root / "dist" / "api.js"
        if not entry.is_file():
            raise ZhenyiBaziUnavailable("reviewed Zhenyi-Bazi dist/api.js is missing")
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Zhenyi-Bazi input exceeds the size limit")
        command = [self.executable, "-e", self.bridge_script, str(self.engine_root)]
        try:
            completed = self._command_runner(
                command, cwd=str(self.engine_root), shell=False,
                input=encoded, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=ENGINE_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi calculation exceeded its time limit") from error
        except OSError as error:
            raise ZhenyiBaziUnavailable("Zhenyi-Bazi Node process could not be started") from error
        if completed.returncode != 0:
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi calculation exited unsuccessfully")
        raw = completed.stdout
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi output exceeded the size limit")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ZhenyiBaziExecutionError("Zhenyi-Bazi returned invalid JSON") from error
        result = self._validate_response(payload)
        expected = {
            "year": context.year_ganzhi,
            "month": context.month_ganzhi,
            "day": context.day_ganzhi,
            "hour": context.hour_ganzhi,
        }
        missing = tuple(f"{key}_ganzhi" for key, value in expected.items() if value is None)
        if missing:
            raise CalendarContextIncomplete(missing)
        differences = {
            key: {"muse": expected[key], "zhenyi": result["pillars"][key]}
            for key in expected if expected[key] != result["pillars"][key]
        }
        return {
            "engine": "zhenyi-bazi",
            "engine_version": ENGINE_VERSION,
            "engine_commit": ENGINE_COMMIT,
            "repository": ENGINE_REPOSITORY,
            "pillars": result["pillars"],
            "calendar_match": not differences,
            "differences": differences,
            "chart": result["chart"],
            "calendar_context_fingerprint": context.fingerprint,
            "epistemic_status": "traditional_calculation",
            "is_evidence": False,
            "is_prediction": False,
            "persisted_birth_data": False,
            "limitations": [
                "Zhenyi-Bazi is an independently versioned traditional chart engine, not a scientific validation system.",
                "A calendar mismatch is preserved for review; neither engine silently overwrites the other.",
                "Raw birth details are passed only to a local child process and omitted from the returned chart object.",
            ],
        }
