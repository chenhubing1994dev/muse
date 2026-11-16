"""Shared, explicit calendar context contract for Muse metaphysics engines.

This module validates and routes already-resolved calendar facts. Deterministic
calendar calculations live in separately versioned providers; missing facts fail
closed instead of being guessed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


GAN = frozenset("甲乙丙丁戊己庚辛壬癸")
ZHI = frozenset("子丑寅卯辰巳午未申酉戌亥")
SOLAR_TERMS = frozenset({
    "立春", "雨水", "惊蛰", "驚蟄", "春分", "清明", "谷雨", "穀雨",
    "立夏", "小满", "小滿", "芒种", "芒種", "夏至", "小暑", "大暑",
    "立秋", "处暑", "處暑", "白露", "秋分", "寒露", "霜降", "立冬",
    "小雪", "大雪", "冬至", "小寒", "大寒",
})
LUNAR_MONTH_LABELS = {
    1: "正", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
    7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二",
}


class CalendarContextIncomplete(ValueError):
    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        self.missing_fields = missing_fields
        super().__init__("calendar context is missing: " + ", ".join(missing_fields))


class CalendarContextUnsupported(ValueError):
    """Raised when a target engine cannot represent a supplied calendar distinction."""


@dataclass(frozen=True)
class CalendarPolicy:
    timezone: str
    year_boundary: str
    month_boundary: str
    day_rollover: str
    true_solar_time: bool = False
    lunar_date_basis: str = "civil_local"

    def __post_init__(self) -> None:
        if not isinstance(self.timezone, str) or not self.timezone.strip():
            raise ValueError("timezone must be a non-empty IANA timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from error
        if not isinstance(self.year_boundary, str) or self.year_boundary not in {"lichun", "lunar_new_year"}:
            raise ValueError("year_boundary must be lichun or lunar_new_year")
        if not isinstance(self.month_boundary, str) or self.month_boundary not in {"jieqi", "lunar_month"}:
            raise ValueError("month_boundary must be jieqi or lunar_month")
        if not isinstance(self.day_rollover, str) or self.day_rollover not in {"midnight", "zi_hour_23"}:
            raise ValueError("day_rollover must be midnight or zi_hour_23")
        if not isinstance(self.true_solar_time, bool):
            raise ValueError("true_solar_time must be a boolean")
        if not isinstance(self.lunar_date_basis, str) or self.lunar_date_basis not in {"civil_local", "apparent_solar"}:
            raise ValueError("lunar_date_basis must be civil_local or apparent_solar")


@dataclass(frozen=True)
class CalendarContext:
    instant: datetime
    policy: CalendarPolicy
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    solar_term: str | None = None
    lunar_year: int | None = None
    lunar_month: int | None = None
    lunar_day: int | None = None
    is_leap_month: bool | None = None
    year_ganzhi: str | None = None
    month_ganzhi: str | None = None
    day_ganzhi: str | None = None
    hour_ganzhi: str | None = None
    hour_branch: str | None = None
    true_solar_time: datetime | None = None
    provider: str | None = None
    provider_version: str | None = None
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instant, datetime):
            raise ValueError("instant must be a datetime")
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError("instant must be timezone-aware")
        if not isinstance(self.policy, CalendarPolicy):
            raise ValueError("policy must be a CalendarPolicy")
        for name, value, minimum, maximum in (
            ("latitude_deg", self.latitude_deg, -90.0, 90.0),
            ("longitude_deg", self.longitude_deg, -180.0, 180.0),
        ):
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{name} must be a finite number")
                if not minimum <= value <= maximum:
                    raise ValueError(f"{name} is outside its valid range")
        if self.solar_term is not None and (
            not isinstance(self.solar_term, str) or self.solar_term not in SOLAR_TERMS
        ):
            raise ValueError("solar_term must be one of the declared 24 Jieqi names")
        lunar_values = (self.lunar_year, self.lunar_month, self.lunar_day, self.is_leap_month)
        if any(value is not None for value in lunar_values) and not all(value is not None for value in lunar_values):
            raise ValueError("lunar_year/month/day/is_leap_month must be supplied together")
        if self.lunar_year is not None:
            if isinstance(self.lunar_year, bool) or not isinstance(self.lunar_year, int) or not 1 <= self.lunar_year <= 9999:
                raise ValueError("lunar_year must be an integer from 1 to 9999")
            if isinstance(self.lunar_month, bool) or not isinstance(self.lunar_month, int) or not 1 <= self.lunar_month <= 12:
                raise ValueError("lunar_month must be an integer from 1 to 12")
            if isinstance(self.lunar_day, bool) or not isinstance(self.lunar_day, int) or not 1 <= self.lunar_day <= 30:
                raise ValueError("lunar_day must be an integer from 1 to 30")
            if not isinstance(self.is_leap_month, bool):
                raise ValueError("is_leap_month must be a boolean")
        for name in ("year_ganzhi", "month_ganzhi", "day_ganzhi", "hour_ganzhi"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or len(value) != 2
                or value[0] not in GAN or value[1] not in ZHI
            ):
                raise ValueError(f"{name} must be a valid Heavenly-Stem/Earthly-Branch pair")
        if self.hour_branch is not None and (
            not isinstance(self.hour_branch, str) or self.hour_branch not in ZHI
        ):
            raise ValueError("hour_branch must be a declared Earthly Branch")
        if self.hour_ganzhi is not None and self.hour_branch is not None and self.hour_ganzhi[1] != self.hour_branch:
            raise ValueError("hour_branch must match the branch in hour_ganzhi")
        if self.true_solar_time is not None:
            if not isinstance(self.true_solar_time, datetime) or self.true_solar_time.tzinfo is not None:
                raise ValueError("true_solar_time must be a naive local apparent-solar datetime")
        if (self.provider is None) != (self.provider_version is None):
            raise ValueError("provider and provider_version must be supplied together")
        for name in ("provider", "provider_version"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 120):
                raise ValueError(f"{name} must be a non-empty string no longer than 120 characters")
        if isinstance(self.source_refs, str) or not isinstance(self.source_refs, (tuple, list)):
            raise ValueError("source_refs must be a tuple or list of strings")
        normalized_refs: list[str] = []
        for ref in self.source_refs:
            if not isinstance(ref, str) or not ref.strip() or len(ref) > 4096:
                raise ValueError("source_refs must contain non-empty strings no longer than 4096 characters")
            normalized_refs.append(ref.strip())
        object.__setattr__(self, "source_refs", tuple(normalized_refs))

    @property
    def local_instant(self) -> datetime:
        return self.instant.astimezone(ZoneInfo(self.policy.timezone))

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "instant_utc": self.instant.astimezone(timezone.utc).isoformat(),
            "timezone": self.policy.timezone,
            "year_boundary": self.policy.year_boundary,
            "month_boundary": self.policy.month_boundary,
            "day_rollover": self.policy.day_rollover,
            "true_solar_time_enabled": self.policy.true_solar_time,
            "lunar_date_basis": self.policy.lunar_date_basis,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "solar_term": self.solar_term,
            "lunar_date": (
                {
                    "year": self.lunar_year, "month": self.lunar_month,
                    "day": self.lunar_day, "is_leap_month": self.is_leap_month,
                } if self.lunar_year is not None else None
            ),
            "ganzhi": {
                "year": self.year_ganzhi, "month": self.month_ganzhi,
                "day": self.day_ganzhi, "hour": self.hour_ganzhi,
            },
            "hour_branch": self.hour_branch,
            "true_solar_time": self.true_solar_time.isoformat() if self.true_solar_time else None,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "source_refs": list(self.source_refs),
        }

    def to_kinliuren_args(self) -> dict[str, str]:
        if self.is_leap_month:
            raise CalendarContextUnsupported(
                "kinliuren 0.1.2.9 has no leap-month flag; do not collapse a leap month silently"
            )
        missing = tuple(
            name for name, value in (
                ("solar_term", self.solar_term),
                ("lunar_month", self.lunar_month),
                ("day_ganzhi", self.day_ganzhi),
                ("hour_ganzhi", self.hour_ganzhi),
            ) if value is None
        )
        if missing:
            raise CalendarContextIncomplete(missing)
        return {
            "jieqi": self.solar_term,  # type: ignore[dict-item]
            "cmonth": LUNAR_MONTH_LABELS[self.lunar_month],  # type: ignore[index]
            "daygangzhi": self.day_ganzhi,  # type: ignore[dict-item]
            "hourgangzhi": self.hour_ganzhi,  # type: ignore[dict-item]
        }

    def to_qimen_args(self) -> dict[str, object]:
        if self.policy.true_solar_time and self.longitude_deg is None:
            raise CalendarContextIncomplete(("longitude_deg",))
        if self.policy.true_solar_time and self.true_solar_time is None:
            raise CalendarContextIncomplete(("true_solar_time",))
        return {
            "date_str": self.local_instant.strftime("%Y-%m-%d %H:%M"),
            "timezone": self.policy.timezone,
            "longitude_deg": self.longitude_deg,
            "true_solar_time_enabled": self.policy.true_solar_time,
            "true_solar_time": self.true_solar_time.isoformat() if self.true_solar_time else None,
            "calendar_context_fingerprint": self.fingerprint,
        }

    def to_ziwei_lunar_inputs(self) -> dict[str, object]:
        missing = tuple(
            name for name, value in (
                ("lunar_year", self.lunar_year),
                ("lunar_month", self.lunar_month),
                ("lunar_day", self.lunar_day),
                ("is_leap_month", self.is_leap_month),
                ("hour_branch", self.hour_branch),
            ) if value is None
        )
        if missing:
            raise CalendarContextIncomplete(missing)
        return {
            "year": self.lunar_year,
            "month": self.lunar_month,
            "day": self.lunar_day,
            "is_leap_month": self.is_leap_month,
            "shichen": self.hour_branch,
        }
