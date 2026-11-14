import json
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from ea.store import connect

CERTIFY_DAYS = 14
HORIZON_MONTHS = 18


def tuesday_after_first_monday(month: int) -> Callable[[int], date]:
    def day(year: int) -> date:
        first = date(year, month, 1)
        return first + timedelta(days=(7 - first.weekday()) % 7 + 1)

    return day


def nth_weekday(month: int, weekday: int, nth: int) -> Callable[[int], date]:
    def day(year: int) -> date:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))

    return day


def last_weekday(month: int, weekday: int) -> Callable[[int], date]:
    def day(year: int) -> date:
        last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - weekday) % 7)

    return day


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def apr_last_sunday_unless_easter(year: int) -> date:
    when = last_weekday(4, 6)(year)
    return nth_weekday(5, 6, 1)(year) if when == easter(year) else when


def wednesday_from(month: int, day_of_month: int) -> Callable[[int], date]:
    def day(year: int) -> date:
        start = date(year, month, day_of_month)
        when = start + timedelta(days=(2 - start.weekday()) % 7)
        return when + timedelta(days=7) if abs((date(year, 6, 6) - when).days) <= 1 else when

    return day


DAY_RULES = {
    "nov_tuesday_after_first_monday": tuesday_after_first_monday(11),
    "mar_second_tuesday": nth_weekday(3, 1, 2),
    "may_third_tuesday": nth_weekday(5, 1, 3),
    "aug_fourth_tuesday": nth_weekday(8, 1, 4),
    "may_second_monday": nth_weekday(5, 0, 2),
    "sep_last_tuesday": last_weekday(9, 1),
    "oct_first_monday": nth_weekday(10, 0, 1),
    "oct_third_monday": nth_weekday(10, 0, 3),
    "oct_fourth_monday": nth_weekday(10, 0, 4),
    "oct_fourth_wednesday": nth_weekday(10, 2, 4),
    "oct_third_saturday": nth_weekday(10, 5, 3),
    "nov_first_monday": nth_weekday(11, 0, 1),
    "nov_first_sunday": nth_weekday(11, 6, 1),
    "oct_second_last_sunday": lambda year: last_weekday(10, 6)(year) - timedelta(days=7),
    "mar_first_sunday": nth_weekday(3, 6, 1),
    "may_first_sunday": nth_weekday(5, 6, 1),
    "apr_last_sunday_unless_easter": apr_last_sunday_unless_easter,
    "oct_first_sunday": nth_weekday(10, 6, 1),
    "oct_last_sunday": last_weekday(10, 6),
    "mar_last_sunday": last_weekday(3, 6),
    "may_first_thursday": nth_weekday(5, 3, 1),
    "wednesday_from_apr_9": wednesday_from(4, 9),
    "wednesday_from_may_31_unless_jun_6": wednesday_from(5, 31),
    "apr_first_wednesday": nth_weekday(4, 2, 1),
    "jun_first_sunday": nth_weekday(6, 6, 1),
    "jul_first_sunday": nth_weekday(7, 6, 1),
    "aug_first_sunday": nth_weekday(8, 6, 1),
    "aug_third_sunday": nth_weekday(8, 6, 3),
    "may_fourth_sunday": nth_weekday(5, 6, 4),
}


def occurrences(
    day_rule: str | None,
    period: int | None,
    phase: int | None,
    after: date,
    until: date | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> Iterator[date]:
    if day_rule not in DAY_RULES or period is None or phase is None:
        return
    year = after.year + (phase - after.year) % period
    while until is None or year <= until.year:
        when = DAY_RULES[day_rule](year)
        if valid_to is not None and when >= valid_to:
            return
        if when > after and (until is None or when <= until) and (valid_from is None or when >= valid_from):
            yield when
        year += period


def next_date(day_rule: str | None, period: int | None, phase: int | None, after: date) -> date | None:
    return next(occurrences(day_rule, period, phase, after), None)


def horizon(today: date) -> date:
    months = today.month - 1 + HORIZON_MONTHS
    return date(today.year + months // 12, months % 12 + 1, min(today.day, 28))


def fixed_rules(conn: Connection, today: date, country: str = "US") -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """with recursive within as (
                 select id from polity where iso_code = %s
                 union select pp.polity_id from polity_parent pp join within w on w.id = pp.parent_id
                   where pp.valid_to is null
               )
               select c.id, c.polity_id, p.kind polity_kind, c.class_id, c.government_kind, c.body_id, c.office_id,
                      c.period_years, c.phase, c.day_rule, c.valid_from, c.valid_to, c.source_id, c.locator
               from election_cadence c join polity p on p.id = c.polity_id
               where c.polity_id in (select id from within) and not c.called
                 and c.period_years is not null and c.phase is not null and c.day_rule = any(%s) and (c.valid_to is null or c.valid_to > %s)""",
            [country, list(DAY_RULES), today],
        )
        return cur.fetchall()


def class_next_date(conn: Connection, class_id: UUID, after: date) -> date | None:
    rows = conn.execute(
        """select day_rule, period_years, phase, valid_from, valid_to from election_cadence
           where class_id = %s and not called and (valid_to is null or valid_to > %s)""",
        [class_id, after],
    ).fetchall()
    dates = (
        next(occurrences(rule, period, phase, after, None, start, end), None)
        for rule, period, phase, start, end in rows
    )
    return min((d for d in dates if d is not None), default=None)


def certify_for(conn: Connection, polity_id: UUID, when: date) -> tuple[int, list[str]]:
    rows = conn.execute(
        """select day_rule, period_years, phase, valid_from, valid_to, certify_days, class_id
           from election_cadence where polity_id = %s and not called and day_rule = any(%s)""",
        [polity_id, list(DAY_RULES)],
    ).fetchall()
    days: list[int] = []
    classes: list[str] = []
    for rule, period, phase, valid_from, valid_to, certify, class_id in rows:
        if when in occurrences(rule, period, phase, when - timedelta(days=1), when, valid_from, valid_to):
            if certify is not None:
                days.append(certify)
            if class_id is not None:
                classes.append(str(class_id))
    return max(days, default=CERTIFY_DAYS), sorted(set(classes))


def due(today: date | None = None) -> list[dict]:
    today = today or date.today()
    with connect() as conn:
        found = conn.execute(
            """select e.id,
                      coalesce(p.iso_code, (
                        with recursive up as (
                          select e.polity_id id, 0 depth
                          union all
                          select pp.parent_id, up.depth + 1 from up
                            join polity_parent pp on pp.polity_id = up.id and pp.valid_to is null
                          where up.depth < 8
                        )
                        select a.iso_code from up join polity a on a.id = up.id
                        where a.iso_code is not null order by up.depth limit 1)),
                      e.polity_id, e.kind, e.date_from
               from election e join polity p on p.id = e.polity_id
               join entity en on en.id = e.id and en.retired_by is null
               where e.status = 'scheduled' and e.date_from >= %s
               order by e.date_from, p.iso_code, e.kind""",
            [today],
        ).fetchall()
        out = []
        for election_id, iso, polity_id, kind, when in found:
            certify, classes = certify_for(conn, polity_id, when)
            out.append(
                {
                    "election_id": str(election_id),
                    "polity": iso or str(polity_id),
                    "kind": kind,
                    "date": when.isoformat(),
                    "certify_days": certify,
                    "classes": classes,
                    "after": (when + timedelta(days=certify)).isoformat(),
                }
            )
        return out


def show(as_json: bool) -> str:
    rows = due()
    if as_json:
        return json.dumps(rows, indent=1)
    return "\n".join(f"{r['date']}\t{r['after']}\t{r['polity']}\t{r['kind']}" for r in rows)
