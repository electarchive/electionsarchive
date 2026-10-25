import re
from pathlib import Path
import subprocess
import unicodedata
from datetime import date
from urllib.parse import quote, unquote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_recap"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.vote.nyc"
PAGES = {date(2013, 11, 5): f"{SITE}/page/election-results-summary-2013"}
PUBLISHER = "Board of Elections in the City of New York"
TITLE = "Statement and Return Report for Certification"
NOTE = (
    "County totals from the Board of Elections' certified Statement and Return report, one line per ballot line as "
    "New York's fusion ballot prints them, with named and scattered write-ins as one line. Ballots cast are the "
    "report's total ballots. Borough president totals are the sum of the report's assembly district totals."
)
BOROUGHS = {"New York": ("36061", "Manhattan"), "Bronx": ("36005", "Bronx"), "Kings": ("36047", "Brooklyn"), "Queens": ("36081", "Queens"), "Richmond": ("36085", "Staten Island")}
CITYWIDE = {"Public Advocate": "public-advocate", "City Comptroller": "comptroller"}
LINE = re.compile(r"^\s+(.+?) \(([^()]+)\)\s+([\d,]+)$")
PARTIES = {
    "DEMOCRATIC": "democratic-party",
    "REPUBLICAN": "republican-party",
    "CONSERVATIVE": "NY:conservative-party",
    "WORKING FAMILIES": "NY:working-families-party",
    "INDEPENDENCE": "NY:independence-party",
    "LIBERTARIAN": "libertarian-party",
    "GREEN": "green-party",
}


def links(day: date) -> list[tuple[str, str, str | None]]:
    page = raw.cached(SOURCE, PAGES[day]).path.read_text()
    found = []
    for href in re.findall(r'href="([^"]*\.pdf)"', page):
        path = unquote(href)
        name = path.rsplit("/", 1)[1]
        if not path.startswith("/"):
            continue
        for label, office in CITYWIDE.items():
            if re.match(rf"0000\d+Citywide {label} Citywide Recap\.pdf$", name):
                found.append((SITE + quote(path, safe="/"), office, None))
        for county, (_, borough) in BOROUGHS.items():
            if re.match(rf"\d+{county} Borough President {county} Recap\.pdf$", name):
                found.append((SITE + quote(path, safe="/"), "borough-president", county))
    return found


def fetch() -> None:
    for day, page in PAGES.items():
        raw.fetch(SOURCE, page, suffix=".html")
        for url, _, _ in links(day):
            raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    lines = []
    ballots = []
    for day in PAGES:
        for url, office, borough in links(day):
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
            ).stdout
            county = borough
            total_votes = {}
            summed: dict[tuple[str, str, str], int] = {}
            cast: dict[str, int] = {}
            in_total = False
            for line in text.splitlines():
                if re.match(r"\s*Total for ", line):
                    in_total = True
                    if office != "borough-president":
                        county = None
                elif (found := re.fullmatch(r"\s*(New York|Bronx|Kings|Queens|Richmond) County\s*", line)) is not None:
                    county = found[1]
                elif county is None:
                    continue
                elif (found := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)) is not None:
                    total_votes[county] = int(found[1].replace(",", ""))
                elif (found := re.fullmatch(r"\s+Total Ballots\s+([\d,]+)", line)) is not None:
                    cast[county] = cast.get(county, 0) + int(found[1].replace(",", ""))
                elif (office != "borough-president" or in_total) and (found := LINE.match(line)) is not None:
                    name, party = found[1], found[2]
                    if party == "WRITE-IN":
                        name = "Write-in"
                    key = (county, name, party)
                    summed[key] = summed.get(key, 0) + int(found[3].replace(",", ""))
            for (county_name, name, party), votes in summed.items():
                lines.append((day, office, BOROUGHS[county_name][0], name, party, votes))
            for county_name, votes in cast.items():
                ballots.append((day, office, BOROUGHS[county_name][0], votes))
            for county_name, expected in total_votes.items():
                got = sum(v for (c, _, _), v in summed.items() if c == county_name)
                print(day, office, county_name, got, expected, "ties" if got == expected else "DIFFERS")
    pl.DataFrame(lines, schema=["day", "office", "county", "candidate", "party", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )
    pl.DataFrame(ballots, schema=["day", "office", "county", "ballots"], orient="row").write_parquet(
        PARSED / "ballots.parquet"
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    ballots = pl.read_parquet(PARSED / "ballots.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's 2013 borough president, public advocate, and comptroller races from the Board of Elections' certified recaps",
            polity="US-NY",
        )
        city = conn.execute(
            "select entity_id from external_id where scheme = 'us.polity' and value = 'new-york-city'"
        ).fetchone()[0]
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=f"{SITE}/page/election-results-summary",
                license="Public record",
            )
        fptp = cs.key("us.electoral_system", "fptp")
        known_parties = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.party'"
            ).fetchall()
        }
        people: dict[tuple[str, str], list[UUID]] = {}
        for person_id, label in conn.execute(
            """select distinct p.id, n.text from person p join name n on n.entity_id = p.id and n.is_preferred
               join candidacy y on y.person_id = p.id join contest_option o on o.id = y.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id where e.polity_id = %s""",
            [city],
        ).fetchall():
            parts = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii")]
            if len(parts) >= 2:
                people.setdefault((parts[0], parts[-1]), []).append(person_id)
        values = []
        made_people: set[UUID] = set()
        for day in sorted(set(lines["day"])):
            for url, _, _ in links(day):
                cs.artifact(raw.cached(SOURCE, url), source)
            election, election_path = conn.execute(
                """select e.id, s.path from election e join slug s on s.entity_id = e.id and s.is_current
                   where e.polity_id = %s and e.date_from = %s and e.kind = 'local'""",
                [city, day],
            ).fetchone()
            contests = [(office, county) for office, county in {(r["office"], r["county"]) for r in lines.filter(pl.col("day") == day).iter_rows(named=True)}]
            for office_kind in sorted({office for office, _ in contests}):
                counties = sorted(county for office, county in contests if office == office_kind)
                if office_kind == "borough-president":
                    groups = [[county] for county in counties]
                else:
                    groups = [counties]
                for group in groups:
                    borough = next(b for _, (fips, b) in BOROUGHS.items() if fips == group[0]) if office_kind == "borough-president" else None
                    seat = f"borough-president-{slug(borough)}" if borough else office_kind
                    title = (
                        f"{day.year} {borough} Borough President election"
                        if borough
                        else f"{day.year} New York City {'Public Advocate' if office_kind == 'public-advocate' else 'Comptroller'} election"
                    )
                    office_key = (
                        f"new-york-city:borough-president:{slug(borough)}" if borough else f"new-york-city:{office_kind}"
                    )
                    office = conn.execute(
                        "select entity_id from external_id where scheme = 'us.office' and value = %s", [office_key]
                    ).fetchone()
                    if office is None:
                        raise ValueError(f"the office {office_key} must already exist")
                    area = cs.area("us.county", group[0]) if borough else cs.area("us.place", "3651000")
                    if conn.execute(
                        "select 1 from contest where election_id = %s and office_id = %s",
                        [election, office[0]],
                    ).fetchone():
                        continue
                    contest_key = f"{day}:new-york-city:{seat}"
                    contest = cs.key("us.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office",
                        office_id=office[0],
                        district_area_id=area,
                        magnitude=1,
                        round_number=1,
                        electoral_system_id=fptp,
                        default_result_kind="counted",
                        term="full",
                    )
                    cs.add("name", entity_id=contest, text=title, lang="en", kind="official", is_preferred=True)
                    cs.add("slug", path=f"{election_path}/{seat}", entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=True,
                        completeness=1,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
                    frame = lines.filter(
                        (pl.col("day") == day) & (pl.col("office") == office_kind) & pl.col("county").is_in(group)
                    )
                    totals = (
                        frame.filter(pl.col("candidate") != "Write-in")
                        .group_by("candidate")
                        .agg(pl.col("votes").sum())
                        .sort("votes", descending=True)
                    )
                    roots: dict[str, UUID] = {}
                    for rank, candidate in enumerate(totals["candidate"]):
                        label = candidate.title()
                        option_key = f"{contest_key}:{slug(label)}"
                        roots[candidate] = cs.key("us.option", option_key)
                        parts = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii")]
                        matched = people.get((parts[0], parts[-1])) if len(parts) >= 2 else None
                        if matched is not None and len(matched) == 1:
                            person = matched[0]
                        else:
                            person = cs.key(f"{SOURCE}.person", f"NY:{slug(label)}")
                            if person in cs.minted and person not in made_people:
                                made_people.add(person)
                                cs.add("person", id=person)
                                cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                        cs.add("contest_option", id=roots[candidate], contest_id=contest, kind="candidate", ballot_label=label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:head"),
                            person_id=person,
                            option_id=roots[candidate],
                            role="head",
                            ballot_name=label,
                            is_independent=False,
                        )
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=roots[candidate],
                            kind="elected" if rank == 0 else "lost",
                        )
                    tickets: dict[tuple[str, str], UUID] = {}
                    for county, candidate, party, votes in frame.select("county", "candidate", "party", "votes").iter_rows():
                        unit = cs.unit("us.county", county)
                        if candidate == "Write-in":
                            option = cs.key("us.option", f"{contest_key}:write_in")
                            if option not in tickets.values():
                                tickets[(candidate, party)] = option
                                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                        else:
                            if (candidate, party) not in tickets:
                                key = PARTIES.get(party)
                                party_id = known_parties.get(key) if key else None
                                option = cs.key("us.option", f"{contest_key}:{slug(candidate.title())}:{slug(party)}")
                                tickets[(candidate, party)] = option
                                cs.add(
                                    "contest_option",
                                    id=option,
                                    contest_id=contest,
                                    parent_option_id=roots[candidate],
                                    kind="ticket",
                                    party_id=party_id,
                                    ballot_label=f"{candidate.title()} ({party.title()})",
                                )
                            option = tickets[(candidate, party)]
                        values.append((str(result_set), str(unit), str(option), 1, votes))
                    for county, cast in ballots.filter(
                        (pl.col("day") == day) & (pl.col("office") == office_kind) & pl.col("county").is_in(group)
                    ).select("county", "ballots").iter_rows():
                        values.append((str(result_set), str(cs.unit("us.county", county)), None, 3, cast))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
