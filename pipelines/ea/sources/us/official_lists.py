import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.official_lists"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/https://www.maine.gov/sos/sites/maine.gov.sos/files/{}"
MAINE = {
    date(2016, 11, 8): ("20250408154329", "inline-files/r-e-active-11-08-16.txt"),
    date(2018, 11, 6): ("20250408154316", "data-text/r-e-active1118.txt"),
    date(2020, 11, 3): ("20250408154356", "data-text/r-e-active1120.txt"),
}
PUBLISHER = "Maine Secretary of State"
TITLE = "Statewide Registered and Enrolled Data File"
ALIASES = {
    "MONHEGAN ISLAND PLT": "MONHEGAN PLT",
    "CARY TWP": "CARY PLT",
    "ATKINSON TWP": "ATKINSON",
    "PRENTISS TWP T7 R3 NBPP": "PRENTISS TWP",
    "INDIAN TWP": "PASSAMAQUODDY INDIAN TWP RESERVATION",
    "PLEASANT POINT VOTING DISTRICT": "PASSAMAQUODDY PLEASANT POINT RESERVATION",
    "PENOBSCOT NATION VOTING DISTRICT": "PENOBSCOT INDIAN ISLAND RESERVATION",
}
GRAIN = (
    "The Secretary of State's list for {day} gives {town} {count} wards and precincts. Every result set that day "
    "reports the town as one unit, the whole town, so completeness here is measured at town grain; it is unmeasured "
    "at ward and precinct grain."
)


def fold(text: str) -> str:
    text = text.upper().removesuffix(", ME")
    for pattern, short in (
        (r"\bPLANTATION\b", "PLT"),
        (r"\bTOWNSHIP\b", "TWP"),
        (r"\bUT\b", "TWP"),
        (r"^ST\.? ", "SAINT "),
    ):
        text = re.sub(pattern, short, text)
    text = re.sub(r"[.']", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return ALIASES.get(text, text)


def fetch() -> None:
    for stamp, path in MAINE.values():
        raw.fetch(SOURCE, WAYBACK.format(stamp, path), suffix=".txt")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for day, (stamp, path) in MAINE.items():
        frame = pl.read_csv(
            raw.cached(SOURCE, WAYBACK.format(stamp, path)).path,
            separator="|",
            infer_schema=False,
            truncate_ragged_lines=True,
        )
        frames.append(
            frame.filter(pl.col("MUNICIPALITY").is_not_null())
            .group_by("COUNTY", "MUNICIPALITY")
            .agg(pl.col("W/P").n_unique().alias("count"))
            .with_columns(day=pl.lit(day), town=pl.col("MUNICIPALITY").map_elements(fold, pl.String))
        )
    pl.concat(frames).write_parquet(PARSED / "maine.parquet")


def emit() -> Path:
    lists = pl.read_parquet(PARSED / "maine.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Precinct universes counted from official ward and precinct lists: Maine 2016-2020",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.maine.gov/sos/elections-voting/voter-data/previous-enrollment-data",
                license=None,
            )
        for stamp, path in MAINE.values():
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)
        towns: dict[str, list[tuple[str, str]]] = {}
        for area, text in conn.execute(
            """select a.id::text, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred
               where a.scheme_code = 'us.town' and c.code like '23%%'"""
        ).fetchall():
            towns.setdefault(fold(text), []).append((area, text))
        held = {
            (area, day)
            for area, day in conn.execute(
                """select u.area_id::text, lower(u.valid) from area_universe u join area a on a.id = u.area_id
                   join area_code c on c.area_id = a.id where a.scheme_code = 'us.town' and c.code like '23%%'
                   and not u.shaped"""
            ).fetchall()
        }
        grains: dict[tuple[str, date], int] = {}
        for day in MAINE:
            for area, most in conn.execute(
                """with units as (
                     select ua.area_id, ru.k from reporting_unit_area ua join area a on a.id = ua.area_id
                     join area_code c on c.area_id = a.id and a.scheme_code = 'us.town' and c.code like '23%%'
                     join reporting_unit ru on ru.id = ua.unit_id left join area own on own.id = ru.area_id
                     where ru.scheme_code = 'us.precinct' and lower(ru.valid) = %s and not ua.approximate
                       and own.scheme_code is distinct from 'us.precinct'
                   )
                   select area_id::text, max(n) from (
                     select u.area_id, v.result_set_k, count(distinct v.unit_k) n
                     from units u join result_value v on v.unit_k = u.k group by 1, 2
                   ) s group by 1""",
                [day],
            ).fetchall():
                grains[(area, day)] = most
        report = {"town grain": 0, "ward grain": 0, "noted": 0, "already held": 0, "no sets": 0}
        unmatched: list[str] = []
        mismatched: list[str] = []
        for county, municipality, count, day, town in lists.sort("day", "COUNTY", "MUNICIPALITY").iter_rows():
            found = towns.get(town, [])
            if len(found) != 1:
                unmatched.append(f"{day} {county} {municipality}: {len(found)} areas")
                continue
            area, label = found[0]
            most = grains.get((area, day))
            if most is None:
                report["no sets"] += 1
                continue
            if (area, day) in held:
                report["already held"] += 1
                continue
            if most not in (1, count):
                mismatched.append(f"{day} {label}: sets report up to {most} units, the list gives {count}")
                continue
            expected = 1 if most == 1 else count
            report["town grain" if most == 1 else "ward grain"] += 1
            cs.add(
                "area_universe",
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
                expected=expected,
                method="official_list",
                shaped=False,
                source_id=source,
                result_set_id=None,
            )
            if most == 1 and count > 1:
                report["noted"] += 1
                cs.add(
                    "note",
                    entity_id=area,
                    kind="context",
                    body_md=GRAIN.format(
                        day=f"{day:%B} {day.day}, {day.year}", town=label.removesuffix(", ME"), count=count
                    ),
                )
        print(report)
        for line in unmatched:
            print("  unmatched:", line)
        for line in mismatched:
            print("  left unmeasured, grain differs:", line)
        return cs.write()
