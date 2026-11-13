import json
from collections.abc import Callable, Iterator
from datetime import date, timedelta

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


DAY_RULES = {"nov_tuesday_after_first_monday": tuesday_after_first_monday(11)}


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


def fixed_rules(conn: Connection, today: date) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select c.id, c.polity_id, p.kind polity_kind, c.class_id, c.government_kind, c.body_id, c.office_id,
                      c.period_years, c.phase, c.day_rule, c.valid_from, c.valid_to, c.source_id, c.locator
               from election_cadence c join polity p on p.id = c.polity_id
               where not c.called and c.period_years is not null and c.phase is not null
                 and c.day_rule = any(%s) and (c.valid_to is null or c.valid_to > %s)""",
            [list(DAY_RULES), today],
        )
        return cur.fetchall()


def due(today: date | None = None) -> list[dict]:
    today = today or date.today()
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select e.id election_id, p.iso_code, e.polity_id, e.kind, e.date_from, e.date_precision,
                      coalesce(max(c.certify_days), %s) certify_days
               from election e join polity p on p.id = e.polity_id
               join entity en on en.id = e.id and en.retired_by is null
               left join election_cadence c on c.polity_id = e.polity_id and c.certify_days is not null
               where e.status = 'scheduled' and e.date_from >= %s
               group by e.id, p.iso_code order by e.date_from, p.iso_code, e.kind""",
            [CERTIFY_DAYS, today],
        )
        return [
            {
                "election_id": str(row["election_id"]),
                "polity": row["iso_code"] or str(row["polity_id"]),
                "kind": row["kind"],
                "date": row["date_from"].isoformat(),
                "certify_days": row["certify_days"],
                "after": (row["date_from"] + timedelta(days=row["certify_days"])).isoformat(),
            }
            for row in cur.fetchall()
        ]


def show(as_json: bool) -> str:
    rows = due()
    if as_json:
        return json.dumps(rows, indent=1)
    return "\n".join(f"{r['date']}\t{r['after']}\t{r['polity']}\t{r['kind']}" for r in rows)
