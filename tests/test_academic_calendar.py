from datetime import date

import pytest
from sustech_survival.calendar import AcademicCalendar, ClassTime

from sustech_dashboard.academic_calendar import daily_view


def make_calendar(year=2026):
    start = f"{year}-09-07" if year == 2026 else f"{year}-09-04"
    comp = f"{year}-10-10" if year == 2026 else f"{year}-10-14"
    def term(teaching_start, sign_in, final_start, final_end):
        return {"teaching_start": teaching_start, "sign_in": sign_in, "total_teaching_weeks": 16,
                "midterm": {"start": f"{year}-10-26", "end": f"{year}-11-08", "equivalent_weeks": [8, 9]},
                "final": {"start": final_start, "end": final_end, "equivalent_weeks": [17]},
                "compensatories": [{"date": comp, "week_type": "odd", "workday_type": "Wednesday"}],
                "extra_breaks": [f"{year}-11-20"]}
    academic = {
        "spring_semester": term(f"{year}-02-25", f"{year}-02-24", f"{year}-06-08", f"{year}-06-18"),
        "fall_semester": term(start, f"{year}-09-04", f"{year}-12-28", f"{year+1}-01-08"),
    }
    general = {"holidays": [{"name": "国庆节", "start": f"{year}-10-01", "end": f"{year}-10-07"}],
               "compensatory_workdays": [comp]}
    return AcademicCalendar.from_payloads(year, "undergraduate", undergraduate=academic,
                                         graduate=academic, general=general)


def patterns():
    return [ClassTime(weeks=tuple(range(1, 17)), weekday=3, periods=(3, 4), title="Thursday"),
            ClassTime(weeks=(5,), weekday=2, periods=(1, 2), title="Original Wednesday"),
            ClassTime(weeks=(6,), weekday=2, periods=(5, 6), title="Even Wednesday"),
            ClassTime(weeks=(5,), weekday=5, periods=(7, 8), title="Normal Saturday")]


@pytest.mark.parametrize("day", range(1, 8))
def test_every_national_day_holiday_suppresses_weekly_classes(day):
    result = daily_view(make_calendar().fall, patterns(), date(2026, 10, day))
    assert result["today_classes"] == []
    assert result["calendar"]["kind"] == "holiday"


@pytest.mark.parametrize("year,target,original", [(2026, "2026-10-10", "2026-10-07"),
                                                   (2028, "2028-10-14", "2028-10-04")])
def test_makeup_uses_original_week_not_saturdays_or_current_week(year, target, original):
    result = daily_view(make_calendar(year).fall, patterns(), date.fromisoformat(target))
    assert [c["KCMC"] for c in result["today_classes"]] == ["Original Wednesday"]
    assert result["calendar"]["source_date"] == original
    assert result["calendar"]["source_week"] == 5


def test_regular_classes_return_after_holiday():
    result = daily_view(make_calendar().fall, patterns(), date(2026, 10, 8))
    assert [c["KCMC"] for c in result["today_classes"]] == ["Thursday"]


def test_extra_break_and_final_suppress_regular_classes_but_midterm_does_not():
    term = make_calendar().fall
    friday = ClassTime(weeks=tuple(range(1, 19)), weekday=4, periods=(1, 2), title="Friday")
    assert daily_view(term, [friday], date(2026, 11, 20))["today_classes"] == []
    assert daily_view(term, [friday], date(2027, 1, 8))["today_classes"] == []
    thursday = ClassTime(weeks=tuple(range(1, 19)), weekday=3, periods=(1, 2), title="Thursday")
    assert daily_view(term, [thursday], date(2026, 10, 29))["today_classes"]


def test_calendar_failure_never_falls_back_to_weekday_course_list(monkeypatch):
    from sustech_survival import sso
    from sustech_survival.tis import schedule, exams
    from sustech_dashboard import academic_calendar, provider

    class Auth:
        def ensure(self):
            return True, ""
    monkeypatch.setattr(sso, "TISAuth", Auth)
    monkeypatch.setattr(schedule, "current_semester", lambda: {"XN": "2026-2027", "XQ": "1"})
    monkeypatch.setattr(schedule, "current_week", lambda: 4)
    def unavailable(*args):
        raise RuntimeError("calendar unavailable")
    monkeypatch.setattr(academic_calendar, "read_daily_calendar", unavailable)
    def forbidden(*args):
        raise AssertionError("Must not infer classes from an unadjusted weekly list")
    monkeypatch.setattr(schedule, "week_schedule", forbidden)
    monkeypatch.setattr(exams, "fetch_exams", lambda auth: [])
    monkeypatch.setattr(provider, "_pending_evals", lambda auth, semester: [])
    result = provider.read_tis()
    assert result["today_classes"] == []
    assert result["calendar"]["kind"] == "unknown"
    assert result["errors"]
