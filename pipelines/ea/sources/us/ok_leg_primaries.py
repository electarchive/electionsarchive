import csv
import io
import re
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ok_county
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.store import connect

SOURCE = "us.ok_leg_primaries"
VERSION = "1"
PUBLISHER = ok_county.PUBLISHER
TITLE = ok_county.TITLE
PAIRS = {
    date(2016, 6, 28): None,
    date(2016, 8, 23): date(2016, 6, 28),
    date(2018, 6, 26): None,
    date(2018, 8, 28): date(2018, 6, 26),
    date(2020, 6, 30): None,
    date(2020, 8, 25): date(2020, 6, 30),
    date(2022, 6, 28): None,
    date(2022, 8, 23): date(2022, 6, 28),
    date(2024, 6, 18): None,
    date(2024, 8, 27): date(2024, 6, 18),
}
PARTIES = {"REP": ("republican", "rp", "Republican"), "DEM": ("democratic", "dp", "Democratic")}
NOTE = (
    "Results by county from the Oklahoma State Election Board's county level results. The statewide total for each "
    "candidate is the sum of the county votes."
)


def url(day: date) -> str:
    return ok_county.url(day)


def fetch() -> None:
    for day in PAIRS:
        try:
            raw.cached(SOURCE, url(day))
        except LookupError:
            raw.store(
                SOURCE,
                url(day),
                (ok_county.DOWNLOADS / f"{day:%Y%m%d}_CountyResults_csv.zip").read_bytes(),
                suffix=".zip",
                retrieved_at=datetime.now(UTC),
                media_type="application/zip",
            )


def races(day: date) -> dict[tuple[str, int, str, bool], dict[str, dict[str, int]]]:
    with zipfile.ZipFile(raw.cached(SOURCE, url(day)).path) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf8", errors="replace")
    found: dict[tuple[str, int, str, bool], dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for row in csv.DictReader(io.StringIO(text)):
        match = re.fullmatch(ok_county.RACE, row["race_description"].upper())
        if match is None or row["race_party"] not in PARTIES:
            continue
        chamber = "senate" if match[1] == "SENATOR" else "house"
        found[(chamber, int(match[2]), row["race_party"], match[3] is not None)][row["cand_name"].upper().strip()][row["county"]] += int(
            row["cand_tot_votes"]
        )
    return found


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2016 through 2024 state House and Senate primaries and runoffs by county from the State Election Board's county level results",
            polity="US-OK",
        )
        counties = {
            plain(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }

        def shape(year: int, chamber: str, district: int) -> tuple:
            row = conn.execute(
                """select c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id, c.magnitude, c.term
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value ~ %s order by abs(left(x.value, 4)::int - %s), x.value desc limit 1""",
                [rf"^\d{{4}}-11-\d\d:OK:{chamber}:{district}:gs?$", year],
            ).fetchone()
            if row is None:
                raise ValueError(f"no general template for {chamber} {district}")
            return row

        scope: dict[str, tuple] = {}
        for party, (word, _, _) in PARTIES.items():
            scope[party] = conn.execute(
                """select c.party_scope_id, (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [f"2018-06-26:OK:house:1:{word}"],
            ).fetchone()
        system = conn.execute(
            """select c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2014-06-24:OK:house:1:rpfsettled'"""
        ).fetchone()[0]
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(1[4-9]|2[0-9])-[0-9-]+:OK:'"""
        ).fetchall():
            pool[plain(text)].add(person)

        def person_of(text: str) -> UUID:
            found = pool.get(plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"OK:{slug(text)}")
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://results.okelections.gov/OKER/",
                license="Public record",
            )
        elections: dict[date, UUID] = {}
        for day, first in PAIRS.items():
            kind = "primary" if first is None else "runoff"
            election = elections[day] = cs.key("us.election", f"{day}:OK:{kind}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-OK"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} Oklahoma primary {'runoff ' if first else ''}election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ok/{day}-{kind}", entity_id=election)
            cs.artifact(raw.cached(SOURCE, url(day)), source)

        loaded = {day: races(day) for day in PAIRS}
        made = 0
        for day, first in PAIRS.items():
            runoff = first is not None
            for (chamber, district, party, remainder), by_candidate in loaded[day].items():
                word, etype, label = PARTIES[party]
                contest_key = f"{day}:OK:{chamber}:{district}:{etype}" + ("runoff" if runoff else "")
                previous = f"{first}:OK:{chamber}:{district}:{etype}" if runoff else None
                if runoff and (chamber, district, party, remainder) not in loaded[first]:
                    raise ValueError(f"{contest_key}: no first round in {first}")
                shaped = shape(day.year, chamber, district)
                advanced = {
                    name
                    for other, start in PAIRS.items()
                    if start == day
                    for name in loaded[other].get((chamber, district, party, remainder), {})
                }
                contest = cs.key("us.contest", contest_key)
                body = "House of Representatives" if chamber == "house" else "Senate"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[day],
                    kind="body_seats",
                    office_id=shaped[0],
                    body_id=shaped[1],
                    district_area_id=shaped[2],
                    seat_id=shaped[3],
                    boundary_set_id=shaped[4],
                    magnitude=shaped[5],
                    party_scope_id=scope[party][0],
                    term="remainder" if remainder else shaped[6],
                    round_number=2 if runoff else 1,
                    previous_round_id=cs.key("us.contest", previous) if runoff else None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{day.year} Oklahoma {body} {label} primary{' runoff' if runoff else ''}, District {district}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                if contest in cs.minted:
                    cs.add(
                        "slug",
                        path=f"us/ok/{day}-{'runoff' if runoff else 'primary'}/state-{chamber}-{district}-{word}-primary"
                        + ("-runoff" if runoff else ""),
                        entity_id=contest,
                    )
                cs.add("citation", entity_id=contest, source_id=source)
                result = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result,
                    election_id=elections[day],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
                ranked = sorted(by_candidate, key=lambda name: -sum(by_candidate[name].values()))
                options = {}
                for position, name in enumerate(ranked):
                    text = title(name)
                    options[name] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                    cs.add(
                        "contest_option",
                        id=options[name],
                        contest_id=contest,
                        kind="candidate",
                        party_id=scope[party][1],
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(text)}"),
                        person_id=person_of(text),
                        option_id=options[name],
                        role="head",
                        ballot_name=text,
                        is_independent=False,
                    )
                for rank, name in enumerate(ranked):
                    if name in advanced:
                        kind = "advanced"
                    elif rank == 0 and not advanced:
                        kind = "won"
                    else:
                        continue
                    cs.add("outcome", result_set_id=result, option_id=options[name], kind=kind)
                by_place: dict[str, int] = defaultdict(int)
                rows = []
                for name, by_county in by_candidate.items():
                    for county, votes in by_county.items():
                        fips = counties[plain(county)]
                        by_place[fips] += votes
                        rows.append(
                            (str(result), str(cs.unit("us.county", fips)), str(options[name]), 0, 1, 0, 0, votes)
                        )
                rows += [
                    (str(result), str(cs.unit("us.county", fips)), None, 0, 4, 0, 0, votes)
                    for fips, votes in by_place.items()
                ]
                cs.frame(
                    "result_value",
                    pl.DataFrame(
                        rows,
                        schema={
                            "result_set_id": pl.String,
                            "unit_id": pl.String,
                            "option_id": pl.String,
                            "channel": pl.Int16,
                            "measure": pl.Int16,
                            "count_round": pl.Int16,
                            "flags": pl.Int16,
                            "value": pl.Int64,
                        },
                        orient="row",
                    ),
                )
                made += 1
        print(made)
        return cs.write()
