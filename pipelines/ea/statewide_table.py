import re
from collections import Counter, defaultdict
from datetime import date

from ea.store import Connection

LAST = date.today().year
MODERN = 1990
JOINT = 1970
APPOINTED_LG = {"TN", "WV", "ME", "NH", "OR", "WY", "AZ"}
CLASSES = (
    ("lieutenant-governor", "lieutenant_governor"),
    ("attorney-general", "attorney_general"),
    ("secretary-of-state|secretary-of-the-commonwealth|secretary-of-the-territory", "secretary_of_state"),
    ("treasurer", "treasurer"),
    ("auditor|comptroller|controller", "auditor"),
    ("superintendent|public-instruction|education", "superintendent"),
)
OFFICES_SQL = """
select o.id, split_part(x.value, ':', 1), substr(p.iso_code, 4), o.kind = 'governor',
    extract(year from e.date_from)::int,
    e.kind not in ('primary', 'caucus', 'runoff', 'special', 'by_election')
        and c.party_scope_id is null and c.term is distinct from 'remainder'
from contest c
join election e on e.id = c.election_id
join office o on o.id = c.office_id
join polity p on p.id = o.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code like 'US-%%'
join external_id x on x.entity_id = o.id and x.scheme = 'us.office'
where e.date_from is not null and x.value !~ '(justice|judge|court)'
"""
TENURES_SQL = """
select o.id, split_part(x.value, ':', 1), substr(p.iso_code, 4), min(extract(year from t.from_date)::int)
from tenure t
join office o on o.id = t.office_id
join polity p on p.id = o.polity_id and p.kind in ('federal_unit', 'region') and p.iso_code like 'US-%%'
join external_id x on x.entity_id = o.id and x.scheme = 'us.office'
where t.from_date is not null and o.kind <> 'governor' and x.value !~ '(justice|judge|court)'
group by 1, 2, 3
"""


def schedule(conn: Connection, last: int = LAST) -> list[tuple]:
    observed: dict[tuple, set[int]] = defaultdict(set)
    governors: dict[str, set[int]] = defaultdict(set)
    for office, key, st, governor, year, general in conn.execute(OFFICES_SQL):
        if governor:
            if general:
                governors[st].add(year)
        elif general:
            observed[(office, key, st)].add(year)
    tenures = {(office, key, st): year for office, key, st, year in conn.execute(TENURES_SQL)}
    rows: list[tuple] = []
    for (office, key, st), recent in sorted(observed.items()):
        modern = {year for year in recent if year >= MODERN}
        if len(modern) < 2 or key == "governor":
            continue
        period = 2 if st in ("NH", "VT") else 4
        phase = Counter(year % period for year in modern).most_common(1)[0][0]
        spine = sorted(governors[st])
        first = min(spine) if spine else MODERN
        begun = tenures.get((office, key, st))
        start = begun - 1 if begun is not None and begun - 1 <= first + 15 else first
        start = min(max(start, 1850), min(recent))
        if begun is None and min(recent) >= 2000:
            start = min(recent)
        together = any(year % period == phase for year in governors[st] if year >= 2000)
        old = (
            {year for year in governors[st] if start <= year < MODERN}
            if together
            else {year for year in range(start, MODERN) if year % period == phase}
        )
        years = {year for year in range(max(start, MODERN), last + 1) if year % period == phase} | old | modern
        if max(recent) < 2016:
            years = {year for year in years if year <= max(recent)}
        if st == "CO" and key in ("member-of-the-state-board-of-education", "regent-of-the-university-of-colorado"):
            years &= recent
        if st == "ND" and key in (
            "attorney-general",
            "secretary-of-state",
            "commissioner-of-agriculture",
            "tax-commissioner",
        ):
            years -= {1994, 1998, 2002}
        if st == "IN" and key == "superintendent-of-public-instruction":
            years = {year for year in years if year <= 2016}
        if st == "GA" and key == "public-service-commissioner":
            years = years - {2022, 2024} | {2025}
        kind = next((name for pattern, name in CLASSES if re.search(pattern, key)), "other")
        for year in sorted(years):
            if year > last:
                continue
            rows.append((st, office, kind, year, False, "observed" if year in recent else "cycle"))
    for (office, key, st), begun in tenures.items():
        if key != "lieutenant-governor" or st in APPOINTED_LG or (office, key, st) in observed:
            continue
        for year in sorted(governors[st]):
            if year >= max(begun - 1, JOINT):
                rows.append((st, office, "lieutenant_governor", year, True, "joint"))
    return rows


def rebuild(conn: Connection) -> int:
    rows = schedule(conn)
    conn.execute("delete from statewide_office_year")
    with conn.cursor().copy(
        "copy statewide_office_year (state, office_id, class, year, joint_ticket, basis) from stdin"
    ) as copy:
        for row in {(row[1], row[3]): row for row in rows}.values():
            copy.write_row(row)
    return len(rows)
