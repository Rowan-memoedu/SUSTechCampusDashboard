"""Connect TIS class patterns to the existing sustech-calendar date engine."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Any

from .core import CHINA_TZ, DATA_ROOT, load_json, parse_dt

SOURCE = "https://github.com/dumixthestpd/sustech-calendar"
CACHE_ROOT = DATA_ROOT / "calendar-cache"
WEEKDAYS = {"Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
            "Thursday": "周四", "Friday": "周五", "Saturday": "周六", "Sunday": "周日"}
_calendar_failures = {}


def load_recent_calendar(year):
    """On-demand views reuse the calendar already refreshed by the background sync."""
    from sustech_survival.calendar import AcademicCalendar, CalendarError
    root=CACHE_ROOT/'calendar'
    files=load_json(root/str(year)/'.meta.json',{}).get('files',{})
    dates=[parse_dt(files.get(name,{}).get('fetched_at')) for name in ('undergraduate.json','graduate.json','general.json')]
    if all(d and datetime.now(CHINA_TZ)-d<timedelta(days=1) for d in dates):
        try:
            return AcademicCalendar.load(year,'undergraduate',base_url=str(root))
        except Exception:
            pass
    if time.monotonic()-_calendar_failures.get(year,-1000)<600:
        raise CalendarError('该年份校历暂不可用，稍后重试')
    try:
        return AcademicCalendar.load(year,'undergraduate',cache_root=CACHE_ROOT)
    except CalendarError:
        _calendar_failures[year]=time.monotonic()
        raise


def daily_view(term: Any, patterns: list, target: date) -> dict[str, Any]:
    """Holiday suppression and makeup transfer are owned by the upstream engine."""
    day = term.day(target)
    meta = {"date": target.isoformat(), "week": day.week, "source_url": SOURCE,
            "kind": "teaching", "label": "正常教学日"}
    if day.is_holiday():
        meta.update(kind="holiday", label=f"{day.holiday.name}放假，今日无课",
                    start=day.holiday.start.isoformat(), end=day.holiday.end.isoformat())
    elif day.is_extra_break():
        meta.update(kind="break", label="校历停课日，今日无课")
    elif not term.teaching_start <= target <= term.final_end:
        meta.update(kind="vacation", label="不在本学期上课期间")
    elif term.final_start <= target <= term.final_end:
        meta.update(kind="final", label="期末复习考试期间，无常规课程；请查看考试安排")
    elif day.is_compensatory():
        source = term._holiday_for(day.comp)
        if source is None:
            raise RuntimeError("校历补课规则无法映射到原课程日期")
        parity = {"odd": "单", "even": "双"}[day.comp.week_type]
        meta.update(kind="makeup", label=f"调休补课：上{parity}周{WEEKDAYS[day.comp.workday]}的课",
                    source_date=source.isoformat(), source_week=term.week_of(source),
                    source_weekday=day.comp.workday)
    elif day.is_weekend():
        meta.update(kind="weekend", label="周末，按已排课程显示")
    elif day.is_midterm():
        meta.update(label="期中考试期间，正常上课")
    if meta["kind"] in {"holiday", "break", "vacation", "final"}:
        classes = []
    else:
        for pattern in patterns:
            term.fill(pattern)
        classes = [{"KCMC": c.title, "SKSJ": " · ".join(x for x in (
                        f"第 {c.periods[0]}–{c.periods[-1]} 节", c.room, c.teacher) if x),
                    "periods": list(c.periods), "teacher": c.teacher, "room": c.room}
                   for c in day.schedule]
        classes.sort(key=lambda c: (c["periods"][0], c["KCMC"]))
    upcoming = sorted((c for c in term.compensatories if c.date > target), key=lambda c: c.date)
    meta["next_makeup"] = None
    if upcoming:
        comp = upcoming[0]
        meta["next_makeup"] = {"date": comp.date.isoformat(),
            "label": f"上{'单' if comp.week_type == 'odd' else '双'}周{WEEKDAYS[comp.workday]}的课"}
    return {"calendar": meta, "today_classes": classes}


def read_daily_calendar(semester: dict, target: date) -> dict[str, Any]:
    from sustech_survival.calendar import AcademicCalendar
    from sustech_survival.tis import schedule

    years = re.findall(r"20\d{2}", str(semester.get("XN", "")))
    season = str(semester.get("XQ", ""))
    if len(years) != 2 or season not in {"1", "2", "3"}:
        raise RuntimeError("TIS 学期标识无法与校历匹配")
    year = int(years[0] if season == "1" else years[1])
    calendar = AcademicCalendar.load(year, level="undergraduate", cache_root=CACHE_ROOT)
    term = next((t for t in (calendar.fall, calendar.spring, calendar.summer)
                 if t is not None and t.xn == semester["XN"] and t.xq == season), None)
    if term is None:
        raise RuntimeError("校历不包含当前 TIS 学期")
    # An autumn semester crosses New Year; never silently omit that year's holidays.
    checked_years = [year]
    monday = target - timedelta(days=target.weekday())
    for extra_year in sorted({monday.year, (monday+timedelta(days=6)).year, target.year}-{year}):
        other = AcademicCalendar.load(extra_year, level="undergraduate", cache_root=CACHE_ROOT)
        calendar.holidays.extend(other.holidays)
        checked_years.append(extra_year)
    patterns = schedule.class_times(xn=semester["XN"], xq=season)
    result = daily_view(term, patterns, target)
    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        view = daily_view(term, patterns, day)
        days.append({"date": day.isoformat(), "calendar": view["calendar"], "classes": view["today_classes"]})
    result["week_schedule"] = {"start": monday.isoformat(), "end": (monday+timedelta(days=6)).isoformat(), "days": days}
    dates = []
    for checked_year in checked_years:
        files = load_json(CACHE_ROOT / "calendar" / str(checked_year) / ".meta.json", {}).get("files", {})
        dates.extend(parse_dt(v.get("fetched_at")) for v in files.values())
    verified = min((d for d in dates if d is not None), default=None)
    result["calendar"]["data_checked_at"] = verified.isoformat() if verified else None
    result["calendar"]["cached_data"] = verified is None or datetime.now(CHINA_TZ) - verified > timedelta(days=1)
    return result
