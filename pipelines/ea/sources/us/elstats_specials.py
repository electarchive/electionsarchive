import csv
import io
import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.elstats import SOURCE as ARCHIVE
from ea.sources.us.elstats import TENANTS, plain, url
from ea.store import connect

SOURCE = "us.elstats_specials"
VERSION = "2"
SEARCHES = [
    ("NY", 2020),
    ("NY", 2022),
    ("NY", 2024),
    ("NM", 2021),
    ("VA", 2023),
]
FLORIDA = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=1/11/2022&OfficialResults=Y"
STATES = {"NY": "New York", "NM": "New Mexico", "VA": "Virginia", "FL": "Florida"}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LPF": "libertarian-party",
    "Green": "green-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
    "Independence": "NY:independence-party",
}
MAIN = {"Democratic", "Republican", "DEM", "REP"}


def fetch() -> None:
    for state, year in SEARCHES:
        raw.fetch(ARCHIVE, url(TENANTS[state][0], year), suffix=".csv")
    raw.fetch(SOURCE, FLORIDA, suffix=".txt")


def emit() -> Path:
    frames = []
    for state, year in SEARCHES:
        frame = pl.read_csv(raw.cached(ARCHIVE, url(TENANTS[state][0], year)).path, infer_schema=False)
        frames.append(
            frame.filter(
                pl.col("election_type").str.starts_with("Special")
                & pl.col("office_name").str.contains(
                    "(?i)^(representative in congress|united states representative|u\\.s\\. house)$"
                )
                & pl.col("division_type").is_in(["County", "Locality"])
            ).with_columns(state=pl.lit(state))
        )
    florida = [
        row
        for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FLORIDA).path.read_text()), delimiter="\t")
        if row["RaceCode"] == "USR"
    ]
    frames.append(
        pl.DataFrame(
            {
                "state": ["FL"] * len(florida),
                "election_date": ["2022-01-11"] * len(florida),
                "district_name": [str(int(row["Juris1num"])) for row in florida],
                "candidate_name": [
                    "Write-ins"
                    if row["PartyCode"] == "WRI"
                    else " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split())
                    for row in florida
                ],
                "candidate_party_name": [
                    None if row["PartyCode"] in ("NPA", "WRI") else row["PartyCode"] for row in florida
                ],
                "division_name": [row["CountyName"] for row in florida],
                "votes": [row["CanVotes"] for row in florida],
            }
        )
    )
    frame = pl.concat(frames, how="diagonal").with_columns(
        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
        district=pl.col("district_name").cast(pl.Int32),
        votes=pl.col("votes").cast(pl.Int64),
    )
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{plain(text)}city"] = code
            names.setdefault(plain(text), code)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="U.S. House special elections in Florida, New York, New Mexico, and Virginia by county from the state "
            "election archives",
            polity="US",
        )
        house = cs.key("us.body", "house")
        fptp = cs.key("us.electoral_system", "fptp")
        sources: dict[str, UUID] = {}
        for state in STATES:
            if state == "FL":
                publisher, title = (
                    "Florida Division of Elections",
                    "Official results extract, January 11, 2022 special general election",
                )
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=FLORIDA,
                        license="Public record",
                    )
                cs.artifact(raw.cached(SOURCE, FLORIDA), sources[state])
                continue
            _, publisher, _ = TENANTS[state]
            sources[state] = cs.source(publisher, "Election statistics archive")
            if sources[state] in cs.minted:
                raise ValueError(f"{state}: the archive's source must already exist")
            for year in frame.filter(pl.col("state") == state)["election_date"].str.slice(0, 4).unique():
                cs.artifact(raw.cached(ARCHIVE, url(TENANTS[state][0], int(year))), sources[state])

        created: set[UUID] = set()
        elections: set[UUID] = set()
        for (state, day, district), lines in frame.group_by("state", "day", "district", maintain_order=True):
            label = STATES[state]
            key = f"{day}:{state}:house:{district}:special"
            general = conn.execute(
                """select ct.id, ct.district_area_id, ct.boundary_set_id from external_id e
                   join contest ct on ct.id = e.entity_id
                   where e.scheme = 'us.contest' and e.value ~ %s and left(e.value, 10) < %s
                   order by e.value desc limit 1""",
                [f"^\\d{{4}}-11-\\d\\d:{state}:house:{district}$", str(day)],
            ).fetchone()
            if general is None:
                raise ValueError(f"{key}: no earlier general for the district")
            election = cs.key("us.election", f"{day}:{state}:special")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="special",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} {label} special election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-special", entity_id=election)
            contest = cs.key("us.contest", key)
            ordinal = f"{district}{'th' if district % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(district % 10, 'th')}"
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=house,
                district_area_id=general[1],
                seat_id=None,
                term="remainder",
                boundary_set_id=general[2],
                party_scope_id=None,
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} U.S. House special election in {label}'s {ordinal} congressional district",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"us/{state.lower()}/{day}-special/house-{district}-special", entity_id=contest)

            candidates: dict[str, tuple[str | None, int]] = {}
            writein = 0
            pseudo = re.compile(r"(?i)^(blank|void|total votes( cast)?|undervotes|overvotes)$")
            for name, party, votes in (
                lines.group_by("candidate_name", "candidate_party_name")
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                if pseudo.match(name):
                    continue
                if re.match(r"(?i)^(scattering|write-?ins?)$", name):
                    writein += votes
                    continue
                previous = candidates.get(name)
                if previous is None:
                    candidates[name] = (party, votes)
                else:
                    candidates[name] = (
                        previous[0] if previous[0] in MAIN else party if party in MAIN else previous[0],
                        previous[1] + votes,
                    )
            winner = max(candidates, key=lambda candidate: candidates[candidate][1])

            known = {
                slug(text): person
                for text, person in conn.execute(
                    """select distinct n.text, c.person_id from candidacy c
                       join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
                       join name n on n.entity_id = c.person_id and n.is_preferred
                       where ct.district_area_id = %s""",
                    [general[1]],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options: list[tuple[UUID, dict[str, int]]] = []
            for name, (party, _) in candidates.items():
                if name == winner:
                    row = conn.execute(
                        """select t.person_id from tenure t join name n on n.entity_id = t.person_id and n.is_preferred
                           where t.body_id = %s and t.from_date > %s and n.text ilike %s order by t.from_date limit 1""",
                        [house, day, f"%{name.split()[-1].removesuffix(' Jr.').removesuffix(' Sr.')}%"],
                    ).fetchone()
                    if row is None:
                        raise ValueError(f"{key}: no House tenure for {name}")
                    person = row[0]
                else:
                    person = known.get(slug(name)) or cs.key(f"{SOURCE}.person", f"{state}:{slug(name)}")
                    if person in cs.minted and person not in created:
                        created.add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                party_id = cs.key("us.party", PARTIES[party]) if party in PARTIES else None
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=party_id is None,
                    is_incumbent=False,
                )
                cs.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="elected" if name == winner else "lost"
                )
                by_unit = {
                    str(division): int(count)
                    for division, count in lines.filter(pl.col("candidate_name") == name)
                    .group_by("division_name")
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                }
                options.append((option, by_unit))
            write_in = cs.key("us.option", f"{key}:write_in")
            cs.add("contest_option", id=write_in, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            options.append(
                (
                    write_in,
                    {
                        str(division): int(count)
                        for division, count in lines.filter(
                            pl.col("candidate_name").str.contains("(?i)^(scattering|write-?ins?)$")
                        )
                        .group_by("division_name")
                        .agg(pl.col("votes").sum())
                        .iter_rows()
                    },
                )
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            totals: dict[str, int] = {}
            for option, by_unit in options:
                for division, count in by_unit.items():
                    simple = plain(
                        re.sub(r"\s*\(.*\)$", "", division).lower().replace("saint ", "st ").replace("&", "and")
                    )
                    code = counties[state].get(re.sub(r"(county|parish|borough)$", "", simple)) or counties[state].get(
                        simple
                    )
                    if code is None:
                        raise ValueError(f"{key}: no county for {division}")
                    unit = cs.unit("us.county", code, day)
                    totals[code] = totals.get(code, 0) + count
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=1,
                        flags=0,
                        value=count,
                    )
            for code, total in totals.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", code, day),
                    option_id=None,
                    channel=0,
                    measure=4,
                    count_round=1,
                    flags=0,
                    value=total,
                )
        return cs.write()
