"""Deterministic sexagenary-cycle arithmetic with explicit reference vectors."""
from __future__ import annotations

from datetime import date, datetime, time

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

# Independent calendar tables agree that 2024-06-15 is 庚戌日 and 甲辰年.
DAY_REFERENCE_DATE = date(2024, 6, 15)
DAY_REFERENCE_PAIR = "庚戌"
YEAR_REFERENCE = 2024
YEAR_REFERENCE_PAIR = "甲辰"
REFERENCE_SOURCE_REFS = (
    "https://www.rili.com.cn/wannianli/2024/0615.html",
    "https://calendar.8s8s.net/ganzhichaxun.php?d=15&m=6&y=2024",
)


def _pair_index(pair: str) -> int:
    if not isinstance(pair, str) or len(pair) != 2:
        raise ValueError("pair must contain one Heavenly Stem and one Earthly Branch")
    if pair[0] not in STEMS or pair[1] not in BRANCHES:
        raise ValueError("pair contains an unknown Heavenly Stem or Earthly Branch")
    for index in range(60):
        if STEMS[index % 10] + BRANCHES[index % 12] == pair:
            return index
    raise ValueError("pair is not part of the 60-pair sexagenary cycle")


_DAY_REFERENCE_INDEX = _pair_index(DAY_REFERENCE_PAIR)
_YEAR_REFERENCE_INDEX = _pair_index(YEAR_REFERENCE_PAIR)


def _pair(index: int) -> str:
    cycle_index = index % 60
    return STEMS[cycle_index % 10] + BRANCHES[cycle_index % 12]


def _gregorian_jdn(value: date) -> int:
    month_adjustment = (14 - value.month) // 12
    year = value.year + 4800 - month_adjustment
    month = value.month + 12 * month_adjustment - 3
    return (
        value.day
        + (153 * month + 2) // 5
        + 365 * year
        + year // 4
        - year // 100
        + year // 400
        - 32045
    )


def sexagenary_year(year: int) -> str:
    """Return the 60-cycle year pair for an already policy-resolved year number."""
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError("year must be an integer from 1 to 9999")
    return _pair(_YEAR_REFERENCE_INDEX + year - YEAR_REFERENCE)


def sexagenary_month(year: int, month_index: int) -> str:
    """Return month pillar; month_index 0..11 maps 寅..丑."""
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError("year must be an integer from 1 to 9999")
    if isinstance(month_index, bool) or not isinstance(month_index, int) or not 0 <= month_index <= 11:
        raise ValueError("month_index must be an integer from 0 to 11")
    year_stem_index = STEMS.index(sexagenary_year(year)[0])
    first_month_stem = ((year_stem_index % 5) * 2 + 2) % 10
    stem = STEMS[(first_month_stem + month_index) % 10]
    branch = BRANCHES[(2 + month_index) % 12]
    return stem + branch


def sexagenary_day(value: date) -> str:
    """Return the day pillar for a Gregorian civil date label."""
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("value must be a datetime.date, not a datetime")
    day_offset = _gregorian_jdn(value) - _gregorian_jdn(DAY_REFERENCE_DATE)
    return _pair(_DAY_REFERENCE_INDEX + day_offset)


def sexagenary_hour(day_stem: str, hour_branch: str) -> str:
    """Return the hour pillar from the resolved day stem and Earthly Branch."""
    if not isinstance(day_stem, str) or day_stem not in STEMS:
        raise ValueError("day_stem must be one Heavenly Stem")
    if not isinstance(hour_branch, str) or hour_branch not in BRANCHES:
        raise ValueError("hour_branch must be one Earthly Branch")
    day_stem_index = STEMS.index(day_stem)
    first_hour_stem = (day_stem_index % 5) * 2
    branch_index = BRANCHES.index(hour_branch)
    return STEMS[(first_hour_stem + branch_index) % 10] + hour_branch


def hour_branch_from_clock(value: time) -> str:
    """Map local civil/apparent-solar wall time to a two-hour Earthly Branch."""
    if not isinstance(value, time) or value.tzinfo is not None:
        raise ValueError("value must be a naive local wall-clock time")
    minutes = value.hour * 60 + value.minute
    index = ((minutes + 60) // 120) % 12
    return BRANCHES[index]
