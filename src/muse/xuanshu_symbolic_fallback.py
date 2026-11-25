"""Zero-dependency symbolic fallback for the B396 Xuanshu lab (紫微 / 奇门 / 六壬).

When the pinned external chart engines (``ziwei_provider`` / ``qimen_provider`` /
``kinliuren_provider``) are not installed — the normal case on a freshly installed desktop
client — the lab used to show three "unavailable" lenses. This module provides a pure-Python,
stdlib-only *symbolic* derivation from the traditional 干支 / 宫位 vocabularies so the page is
usable out of the box.

It is deliberately NOT a chart engine: no birth data, no astronomical calculation, no claim of
fidelity to any school. Every output is a deterministic function of ``seed`` (and an optional
calendar day used only for the sexagenary day pillar) and is always labelled
``symbolic_inspiration`` / ``unverified_hypothesis`` by :mod:`muse.xuanshu_fusion`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Mapping

FALLBACK_ENGINE = "muse.xuanshu_symbolic_fallback"
FALLBACK_VERSION = "1"
PRECISION_NOTE = "symbolic_only_not_a_real_chart"

STEMS = ("甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸")
BRANCHES = ("子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥")
STEM_ELEMENTS = ("木", "木", "火", "火", "土", "土", "金", "金", "水", "水")
BRANCH_ELEMENTS = ("水", "土", "木", "木", "土", "火", "火", "土", "金", "金", "土", "水")

# 紫微斗数 symbolic vocabulary
ZIWEI_PALACES = ("命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄", "迁移", "交友", "官禄", "田宅", "福德", "父母")
ZIWEI_MAJOR_STARS = ("紫微", "天机", "太阳", "武曲", "天同", "廉贞", "天府",
                     "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军")
ZIWEI_BUREAUS = ("水二局", "木三局", "金四局", "土五局", "火六局")
ZIWEI_TRANSFORMS = ("化禄", "化权", "化科", "化忌")

# 奇门遁甲 symbolic vocabulary
QIMEN_PALACES = ("坎一宫", "坤二宫", "震三宫", "巽四宫", "中五宫", "乾六宫", "兑七宫", "艮八宫", "离九宫")
QIMEN_STARS = ("天蓬", "天芮", "天冲", "天辅", "天禽", "天心", "天柱", "天任", "天英")
QIMEN_DOORS = ("休门", "生门", "伤门", "杜门", "景门", "死门", "惊门", "开门")
QIMEN_SPIRITS = ("值符", "螣蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天")

# 大六壬 symbolic vocabulary
LIUREN_MONTHLY_GENERALS = ("神后", "大吉", "功曹", "太冲", "天罡", "太乙",
                           "胜光", "小吉", "传送", "从魁", "河魁", "登明")
LIUREN_HEAVENLY_GENERALS = ("贵人", "螣蛇", "朱雀", "六合", "勾陈", "青龙",
                            "天空", "白虎", "太常", "玄武", "太阴", "天后")
LIUREN_LESSON_TYPES = ("元首", "重审", "知一", "涉害", "遥克", "昴星", "别责", "八专", "伏吟", "返吟")

SUPPORTED = ("ziwei", "qimen", "liuren")


def _stream(seed: str, salt: str) -> bytes:
    """Deterministic 64-byte stream for one lens."""
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a non-empty string")
    first = hashlib.sha256(f"{FALLBACK_ENGINE}|{FALLBACK_VERSION}|{salt}|{seed}".encode("utf-8")).digest()
    return first + hashlib.sha256(first + salt.encode("utf-8")).digest()


def _pick(stream: bytes, index: int, options: tuple[str, ...]) -> str:
    return options[stream[index % len(stream)] % len(options)]


def julian_day_number(day: _dt.date) -> int:
    """Proleptic Gregorian calendar → Julian Day Number (integer, at noon)."""
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    return day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def sexagenary_day(day: _dt.date) -> dict[str, Any]:
    """Traditional 60-cycle day pillar (甲子 = index 0). 2000-01-01 → 戊午 (index 54)."""
    index = (julian_day_number(day) + 49) % 60
    stem, branch = index % 10, index % 12
    return {"index": index, "pillar": STEMS[stem] + BRANCHES[branch], "stem": STEMS[stem],
            "branch": BRANCHES[branch], "stem_element": STEM_ELEMENTS[stem],
            "branch_element": BRANCH_ELEMENTS[branch]}


def _coerce_day(moment: Any) -> _dt.date | None:
    if moment is None:
        return None
    if isinstance(moment, _dt.datetime):
        return moment.date()
    if isinstance(moment, _dt.date):
        return moment
    if isinstance(moment, str) and moment.strip():
        return _dt.date.fromisoformat(moment.strip()[:10])
    raise ValueError("moment must be a date, datetime or ISO date string")


def _context_parts(context: Any) -> tuple[str, _dt.date | None]:
    if isinstance(context, Mapping):
        seed = str(context.get("seed", "")).strip()
        return seed, _coerce_day(context.get("moment"))
    if isinstance(context, str):
        return context, None
    raise ValueError("context must be a mapping with a seed or a seed string")


def _ziwei(seed: str, day: _dt.date | None) -> dict[str, Any]:
    s = _stream(seed, "ziwei")
    ming = s[0] % 12
    body = (ming + 2 + s[1] % 9) % 12
    year_stem = s[2] % 10
    stars = []
    used: set[int] = set()
    for offset in range(3, 40):
        candidate = s[offset] % len(ZIWEI_MAJOR_STARS)
        if candidate not in used:
            used.add(candidate)
            stars.append(ZIWEI_MAJOR_STARS[candidate])
        if len(stars) == 2:
            break
    focus = _pick(s, 41, ZIWEI_PALACES[1:])
    result = {
        "life_palace": BRANCHES[ming] + "宫",
        "body_palace": BRANCHES[body] + "宫",
        "major_stars": "、".join(stars),
        "element_bureau": _pick(s, 42, ZIWEI_BUREAUS),
        "year_stem_transform": STEMS[year_stem] + "干" + _pick(s, 43, ZIWEI_TRANSFORMS),
        "focus_palace": focus,
    }
    if day is not None:
        result["day_pillar"] = sexagenary_day(day)["pillar"]
    return result


def _qimen(seed: str, day: _dt.date | None) -> dict[str, Any]:
    s = _stream(seed, "qimen")
    if day is not None:
        # symbolic convention only: 冬至→夏至 half of the year is 阳遁, the other half 阴遁
        solstice_half = "阳遁" if (day.month == 12 and day.day >= 21) or day.month <= 5 or (
            day.month == 6 and day.day < 21) else "阴遁"
    else:
        solstice_half = "阳遁" if s[0] % 2 == 0 else "阴遁"
    bureau = 1 + s[1] % 9
    result = {
        "dun": f"{solstice_half}{'一二三四五六七八九'[bureau - 1]}局",
        "duty_star": _pick(s, 2, QIMEN_STARS),
        "duty_door": _pick(s, 3, QIMEN_DOORS),
        "spirit": _pick(s, 4, QIMEN_SPIRITS),
        "palace": _pick(s, 5, QIMEN_PALACES),
        "hour_stem_branch": STEMS[s[6] % 10] + BRANCHES[(s[6] % 10 + 2 * (s[7] % 6)) % 12],
    }
    if day is not None:
        result["day_pillar"] = sexagenary_day(day)["pillar"]
    return result


def _liuren(seed: str, day: _dt.date | None) -> dict[str, Any]:
    s = _stream(seed, "liuren")
    start = s[0] % 12
    step = 1 + s[1] % 5
    transmissions = [BRANCHES[(start + step * i) % 12] for i in range(3)]
    result = {
        "monthly_general": _pick(s, 2, LIUREN_MONTHLY_GENERALS),
        "three_transmissions": "→".join(transmissions),
        "heavenly_general": _pick(s, 3, LIUREN_HEAVENLY_GENERALS),
        "lesson_type": _pick(s, 4, LIUREN_LESSON_TYPES) + "课",
        "element_flow": "→".join(BRANCH_ELEMENTS[BRANCHES.index(b)] for b in transmissions),
    }
    if day is not None:
        result["day_pillar"] = sexagenary_day(day)["pillar"]
    return result


_BUILDERS = {"ziwei": _ziwei, "qimen": _qimen, "liuren": _liuren}


class SymbolicFallbackProvider:
    """Provider-compatible object (``calculate(context) -> dict``) for one lens."""

    def __init__(self, lens: str) -> None:
        if lens not in _BUILDERS:
            raise ValueError(f"unsupported lens {lens!r}; expected one of {SUPPORTED}")
        self.lens = lens

    def calculate(self, context: Any) -> dict[str, Any]:
        seed, day = _context_parts(context)
        if not seed:
            raise ValueError("seed must be a non-empty string")
        return {"engine": FALLBACK_ENGINE, "version": FALLBACK_VERSION, **_BUILDERS[self.lens](seed, day)}


def fallback_lens_payload(lens: str, seed: str, *, moment: Any = None, reason: str = "") -> dict[str, Any]:
    """Unlabelled lens body; :mod:`muse.xuanshu_fusion` adds the immutable epistemic labels."""
    raw = SymbolicFallbackProvider(lens).calculate({"seed": seed, "moment": moment})
    digest = hashlib.sha256(repr(sorted(raw.items())).encode("utf-8")).hexdigest()
    keywords = [str(v)[:40] for k, v in raw.items() if k not in {"engine", "version"} and isinstance(v, str)][:6]
    return {
        "status": "computed",
        "engine": f"{lens}:{FALLBACK_ENGINE}",
        "mode": "symbolic_fallback",
        "precision": PRECISION_NOTE,
        "fallback_reason": reason or "provider_not_configured",
        "raw_sha256": digest,
        "keywords": keywords,
        "symbols": {k: v for k, v in raw.items() if k not in {"engine", "version"}},
    }


def today_in_shanghai() -> _dt.date:
    """Calendar day in UTC+8 (no tzdata dependency)."""
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=8)).date()
