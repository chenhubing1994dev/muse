"""Optional celestial-calendar provider for Muse's shared CalendarContext.

It resolves civil/lunar date labels, the active solar term, apparent solar
clock time and four pillars under explicit year/month/day rollover policies.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any
from zoneinfo import ZoneInfo

from muse.calendar_context import (
    CalendarContext,
    CalendarContextIncomplete,
    CalendarContextUnsupported,
    CalendarPolicy,
)
from muse.sexagenary import (
    hour_branch_from_clock,
    sexagenary_day,
    sexagenary_hour,
    sexagenary_month,
    sexagenary_year,
)
from muse.sexagenary import REFERENCE_SOURCE_REFS


class CelestialCalendarUnavailable(RuntimeError):
    """Raised when the optional celestial-calendar distribution is unavailable."""


TERM_NAMES = {
    "LICHUN": "立春",
    "YUSHUI": "雨水",
    "JINGZHE": "驚蟄",
    "CHUNFEN": "春分",
    "QINGMING": "清明",
    "GUYU": "穀雨",
    "LIXIA": "立夏",
    "XIAOMAN": "小滿",
    "MANGZHONG": "芒種",
    "XIAZHI": "夏至",
    "XIAOSHU": "小暑",
    "DASHU": "大暑",
    "LIQIU": "立秋",
    "CHUSHU": "處暑",
    "BAILU": "白露",
    "QIUFEN": "秋分",
    "HANLU": "寒露",
    "SHUANGJIANG": "霜降",
    "LIDONG": "立冬",
    "XIAOXUE": "小雪",
    "DAXUE": "大雪",
    "DONGZHI": "冬至",
    "XIAOHAN": "小寒",
    "DAHAN": "大寒",
}
SOURCE_URL = "https://pypi.org/project/celestial-calendar/0.8.0/"


def _civil_to_naive_datetime(civil: Any) -> datetime:
    seconds = round(float(civil.fraction) * 86400)
    return datetime(int(civil.year), int(civil.month), int(civil.day)) + timedelta(seconds=seconds)


def _hour_branch(local_clock: datetime) -> str:
    return hour_branch_from_clock(local_clock.time())

class CelestialCalendarProvider:
    """Resolve calendar context through celestial-calendar 0.8.x.

    The library's chart_snapshot is used only as its UTC-to-UT1 bridge; calculated
    planetary and house outputs are discarded. Supported UTC input years are
    restricted to 1972-2099, matching the provider's documented time-scale range.
    """

    def __init__(
        self,
        *,
        calendar_module: Any | None = None,
        lunar_algorithm: str = "ALGO3",
        provider_version: str | None = None,
    ) -> None:
        if lunar_algorithm not in {"ALGO1", "ALGO2", "ALGO3"}:
            raise ValueError("lunar_algorithm must be ALGO1, ALGO2, or ALGO3")
        self._calendar_module = calendar_module
        self._lunar_algorithm = lunar_algorithm
        self._provider_version = provider_version

    def _load_module(self) -> Any:
        if self._calendar_module is not None:
            return self._calendar_module
        try:
            import celestial_calendar
        except ImportError as error:
            raise CelestialCalendarUnavailable(
                "optional celestial-calendar==0.8.0 is not installed in this Python environment"
            ) from error
        self._calendar_module = celestial_calendar
        return celestial_calendar

    def _version(self) -> str:
        if self._provider_version:
            return self._provider_version
        try:
            return package_version("celestial-calendar")
        except PackageNotFoundError:
            return "unknown"

    def resolve(
        self,
        instant: datetime,
        policy: CalendarPolicy,
        *,
        latitude_deg: float | None = None,
        longitude_deg: float | None = None,
    ) -> CalendarContext:
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("instant must be timezone-aware")
        if not isinstance(policy, CalendarPolicy):
            raise ValueError("policy must be a CalendarPolicy")
        if policy.lunar_date_basis == "apparent_solar" and not policy.true_solar_time:
            raise CalendarContextUnsupported(
                "apparent_solar lunar date basis requires true_solar_time to be enabled"
            )
        if policy.true_solar_time and longitude_deg is None:
            raise CalendarContextIncomplete(("longitude_deg",))
        if latitude_deg is not None and abs(latitude_deg) >= 90:
            raise CalendarContextUnsupported(
                "celestial-calendar chart_snapshot does not support geographic poles"
            )

        utc_instant = instant.astimezone(timezone.utc)
        if not 1972 <= utc_instant.year <= 2099:
            raise CalendarContextUnsupported(
                "celestial-calendar UTC time-scale bridge supports years 1972-2099 only"
            )

        module = self._load_module()
        local_instant = instant.astimezone(ZoneInfo(policy.timezone))
        utc_civil = module.CivilDateTime(
            utc_instant.year,
            utc_instant.month,
            utc_instant.day,
            (
                utc_instant.hour * 3600
                + utc_instant.minute * 60
                + utc_instant.second
                + utc_instant.microsecond / 1_000_000
            ) / 86400,
        )
        location = module.GeoLocation(
            0.0 if latitude_deg is None else latitude_deg,
            0.0 if longitude_deg is None else longitude_deg,
        )
        snapshot = module.chart_snapshot(
            utc_civil,
            module.CivilScale.UTC,
            location,
            module.HouseSystem.EQUAL,
        )
        input_jd_ut1 = float(snapshot.times.jd_ut1)

        term_moments: list[tuple[float, str, int]] = []
        for year in (local_instant.year - 1, local_instant.year, local_instant.year + 1):
            for term in module.Jieqi:
                term_name = TERM_NAMES.get(term.name)
                if term_name is None:
                    raise ValueError(f"unsupported Jieqi enum member: {term.name}")
                moment = module.jieqi_moment(year, term)
                term_jd_ut1 = float(module.ut1_to_jd(moment.moment_ut1))
                term_moments.append((term_jd_ut1, term_name, year))
        solar_term_candidates = [item for item in term_moments if item[0] <= input_jd_ut1]
        if not solar_term_candidates:
            raise CalendarContextUnsupported("provider could not resolve the preceding solar term")
        solar_term = max(solar_term_candidates, key=lambda item: item[0])[1]
        true_solar_time = None
        if policy.true_solar_time:
            apparent = module.apparent_solar_time(utc_civil, float(longitude_deg))
            true_solar_time = _civil_to_naive_datetime(apparent)

        if policy.lunar_date_basis == "apparent_solar":
            if true_solar_time is None:
                raise CalendarContextIncomplete(("true_solar_time",))
            lunar_basis_date = true_solar_time.date()
        else:
            lunar_basis_date = local_instant.date()

        algorithm = getattr(module.LunarAlgorithm, self._lunar_algorithm)
        lunar = module.gregorian_to_lunar(algorithm, date(
            lunar_basis_date.year, lunar_basis_date.month, lunar_basis_date.day
        ))
        lunar_year = int(lunar.year)
        lunar_month = int(lunar.month)
        lunar_day = int(lunar.day)
        is_leap_month = bool(getattr(lunar, "is_leap", getattr(lunar, "is_leap_month", False)))

        hour_clock = true_solar_time if policy.true_solar_time else local_instant.replace(tzinfo=None)
        if policy.year_boundary == "lunar_new_year":
            effective_year = lunar_year
        else:
            lichun_moments = [
                item[0] for item in term_moments
                if item[1] == "立春" and item[2] == hour_clock.year
            ]
            if not lichun_moments:
                raise CalendarContextUnsupported("provider could not resolve Li Chun for the effective year")
            effective_year = hour_clock.year if input_jd_ut1 >= min(lichun_moments) else hour_clock.year - 1

        if policy.month_boundary == "lunar_month":
            month_index = lunar_month - 1
        else:
            jie_month_index = {
                "立春": 0, "驚蟄": 1, "清明": 2, "立夏": 3,
                "芒種": 4, "小暑": 5, "立秋": 6, "白露": 7,
                "寒露": 8, "立冬": 9, "大雪": 10, "小寒": 11,
            }
            month_candidates = [
                (jd, jie_month_index[name]) for jd, name, _year in term_moments
                if name in jie_month_index and jd <= input_jd_ut1
            ]
            if not month_candidates:
                raise CalendarContextUnsupported("provider could not resolve the preceding Jie month boundary")
            month_index = max(month_candidates, key=lambda item: item[0])[1]

        year_ganzhi = sexagenary_year(effective_year)
        month_ganzhi = sexagenary_month(effective_year, month_index)
        day_clock_date = hour_clock.date()
        if policy.day_rollover == "zi_hour_23" and hour_clock.hour >= 23:
            day_clock_date += timedelta(days=1)
        day_ganzhi = sexagenary_day(day_clock_date)
        hour_branch = _hour_branch(hour_clock)
        hour_ganzhi = sexagenary_hour(day_ganzhi[0], hour_branch)
        return CalendarContext(
            instant=instant,
            policy=policy,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            solar_term=solar_term,
            lunar_year=lunar_year,
            lunar_month=lunar_month,
            lunar_day=lunar_day,
            is_leap_month=is_leap_month,
            year_ganzhi=year_ganzhi,
            month_ganzhi=month_ganzhi,
            day_ganzhi=day_ganzhi,
            hour_ganzhi=hour_ganzhi,
            hour_branch=hour_branch,
            true_solar_time=true_solar_time,
            provider="celestial-calendar+muse-sexagenary",
            provider_version=f"{self._version()}+muse-sexagenary-v1",
            source_refs=(SOURCE_URL, *REFERENCE_SOURCE_REFS),
        )