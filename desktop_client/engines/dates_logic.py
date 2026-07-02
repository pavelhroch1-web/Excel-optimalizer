"""
Python port of PlanningEngine.ts's inlined dates.ts SYNC-BLOCK (isoMonday,
easter, isHoliday, workDays). Same duplication-risk note as core_logic.py -
see docs/ARCHITECTURE.md "Desktop Client local engine execution".

JS `new Date(year, monthIndex0, day)` is LOCAL time; this port uses naive
(timezone-less) `datetime.date`, which is the correct match since core.ts/
PlanningEngine.ts never reads timezone-sensitive fields off these Date
objects - only year/month/day and weekday, which datetime.date gives
identically.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


def iso_monday(year: int, week: int) -> date:
    d = date(year, 1, 4)
    day = d.isoweekday()  # Mon=1..Sun=7, matches JS's `day==0 -> 7` remap of getDay() (Sun=0..Sat=6)
    d = d + timedelta(days=-(day - 1) + (week - 1) * 7)
    return d


def easter(y: int) -> date:
    f = math.floor
    a = y % 19
    b = f(y / 100)
    c = y % 100
    d = f(b / 4)
    e = b % 4
    g = f((8 * b + 13) / 25)
    h = (19 * a + b - d - g + 15) % 30
    i = f(c / 4)
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = f((a + 11 * h + 22 * l) / 451)
    month = f((h + l - 7 * m + 114) / 31)
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, int(month), int(day))


_FIXED_HOLIDAYS = {
    (1, 1), (1, 5), (8, 5), (5, 7), (6, 7), (28, 9),
    (28, 10), (17, 11), (24, 12), (25, 12), (26, 12),
}


def is_holiday(d: date, year: int) -> bool:
    if (d.day, d.month) in _FIXED_HOLIDAYS:
        return True
    e = easter(year)
    friday = e - timedelta(days=2)
    monday = e + timedelta(days=1)
    return d == friday or d == monday


@dataclass
class WorkDayRow:
    day: str
    date: date


def work_days(year: int, week: int) -> list[WorkDayRow]:
    names = ["MON", "TUE", "WED", "THU", "FRI"]
    start = iso_monday(year, week)
    result: list[WorkDayRow] = []
    for i in range(5):
        d = start + timedelta(days=i)
        if not is_holiday(d, year):
            result.append(WorkDayRow(day=names[i], date=d))
    return result


def to_cs_cz_date_string(d: date) -> str:
    """Matches Node's `date.toLocaleDateString("cs-CZ")` -> "D. M. YYYY" (no zero-padding,
    verified against a live `node -e` run rather than assumed - locale formatting is
    exactly the kind of thing worth checking instead of guessing)."""
    return f"{d.day}. {d.month}. {d.year}"
