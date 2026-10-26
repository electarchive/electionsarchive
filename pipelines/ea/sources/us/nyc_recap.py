import json
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_recap"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.vote.nyc"
YEARS = range(2005, 2014)
PUBLISHER = "Board of Elections in the City of New York"
TITLE = "Statement and Return Report for Certification"
NOTE = (
    "Assembly district returns from the Board of Elections' certified Statement and Return reports, one line per "
    "ballot line as New York's fusion ballot prints them, with named and scattered write-ins as one line. Ballots "
    "cast are the reports' total ballots, where they give one. The borough reports' assembly district lines sum to "
    "each report's county total."
)
BOROUGHS = {
    "New York": ("36061", "Manhattan"),
    "Bronx": ("36005", "Bronx"),
    "Kings": ("36047", "Brooklyn"),
    "Queens": ("36081", "Queens"),
    "Richmond": ("36085", "Staten Island"),
}
PARTIES = {
    "DEMOCRATIC": "democratic-party",
    "REPUBLICAN": "republican-party",
    "CONSERVATIVE": "NY:conservative-party",
    "WORKING FAMILIES": "NY:working-families-party",
    "INDEPENDENCE": "NY:independence-party",
    "LIBERTARIAN": "libertarian-party",
    "GREEN": "green-party",
    "LIBERAL": "NY:liberal-party",
}
PARTY_SCOPE = re.compile(r"^(DEMOCRATIC|REPUBLICAN|CONSERVATIVE|WORKING FAMILIES|INDEPENDENCE|GREEN|LIBERTARIAN) ")
COUNTIES = "New York|Bronx|Kings|Queens|Richmond"
SKIP = re.compile(
    r"^\s*(PUBLIC COUNTER|EMERGENCY|ABSENTEE/MILITARY|AFFIDAVIT|FEDERAL|SPECIAL PRESIDENTIAL|TOTAL BALLOTS|"
    r"TOTAL APPLICABLE BALLOTS|TOTAL VOTES|UNRECORDED|MANUAL|SCANNER|PAGE)\s{2,}|^\s*(LESS -|PAGE \d)",
    re.I,
)
LINE = re.compile(r"^\s+(.+?)\s{2,}\(?([\d,]+)\)?\s*$")
ELECTION = re.compile(r"(General|Primary Runoff|Presidential Primary|Primary|Special|Runoff)[A-Za-z ]*Election(?: \d{4})? - (\d\d/\d\d/\d{4})", re.I)
BLOCK = re.compile(
    rf"^\s*(?:(?P<ad>Assembly District|ASSEMBLY DISTRICT) (?P<adn>\d+)|(?P<cty>{COUNTIES}) County|(?P<tot>TOTAL FOR|Total for) (?P<what>.+))\s*$"
)
OFFICES = {
    "mayor": re.compile(r"^(?:FOR )?(?:DEMOCRATIC |REPUBLICAN |WORKING FAMILIES )?MAYOR \((?:CITYWIDE|NYC)\)"),
    "public-advocate": re.compile(r"^(?:FOR )?(?:\w+ )?(?:\w+ )?PUBLIC ADVOCATE \((?:CITYWIDE|NYC)\)"),
    "comptroller": re.compile(r"^(?:FOR )?(?:\w+ )?(?:\w+ )?(?:CITY )?COMPTROLLER \((?:CITYWIDE|NYC)\)"),
}
STATE_OFFICES = re.compile(
    r"GOVERNOR|ATTORNEY GENERAL|STATE COMPTROLLER|UNITED STATES SENATOR|PRESIDENT|SENATOR|ASSEMBLY|CONGRESS|COMMITTEE|LEADER|DELEGATE|CONVENTION",
    re.I,
)


def num(text: str) -> int:
    return int(text.replace(",", ""))


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def read(path: Path) -> dict[str, object] | None:
    lines = pdf_text(path).splitlines()
    found = ELECTION.search("\n".join(lines[:25]))
    if found is None:
        return None
    scope = None
    for line in lines[:25]:
        matched = re.match(rf"^({COUNTIES}) County - (.+)$", line.strip()) or re.match(r"^(Crossover) - (.+)$", line.strip())
        if matched:
            scope = (matched[1], matched[2])
    office = None
    block = None
    expecting = False
    entries = []
    totals = []
    ballots = []
    for line in lines:
        if line.strip().startswith("PRINTED AS OF"):
            expecting = True
            continue
        if expecting:
            stripped = line.strip()
            if stripped and re.search(r"vote for|ELECTED|NO\. OF", stripped, re.I):
                expecting = False
                if stripped != office:
                    office = stripped
                    block = None
                continue
        matched = BLOCK.match(line)
        if matched:
            block = (
                ("ad", matched["adn"]) if matched["adn"] else ("county", matched["cty"]) if matched["cty"] else ("total", matched["what"].strip())
            )
            continue
        if block is None or office is None:
            continue
        counted = LINE.match(line)
        if re.match(r"\s*(TOTAL VOTES|Total Votes)\s", line) and counted:
            totals.append((office, block[0], block[1], num(counted[2])))
        elif re.match(r"\s*(TOTAL BALLOTS|Total Ballots)\s", line) and counted:
            ballots.append((office, block[0], block[1], num(counted[2])))
        elif counted and not SKIP.match(line) and "Recap" not in line:
            name, party = counted[1].strip(), None
            split = re.match(r"^(.*?) \(([^()]*)\)$", name)
            if split:
                name, party = split[1].strip(), split[2].strip()
            entries.append((office, block[0], block[1], name, party, num(counted[2])))
    return {
        "day": datetime.strptime(found[2], "%m/%d/%Y").date(),
        "kind": found[1].title(),
        "scope": scope,
        "entries": entries,
        "totals": totals,
        "ballots": ballots,
    }


def fetch() -> None:
    for year in YEARS:
        page = raw.fetch(SOURCE, f"{SITE}/page/election-results-summary-{year}", suffix=".html").path.read_text()
        for href in sorted(set(re.findall(r'href="([^"]*\.pdf)"', page))):
            path = unquote(href)
            if path.startswith("/"):
                raw.fetch(SOURCE, SITE + quote(path, safe="/,"), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    entries = []
    totals = []
    ballots = []
    unread = []
    for url, entry in sorted(index.items()):
        if not url.endswith(".pdf"):
            continue
        report = read(DATA / "raw" / SOURCE / str(entry["file"]))
        if report is None:
            unread.append(url)
            continue
        scope = report["scope"]
        head = (url, str(report["day"]), report["kind"], scope[0] if scope else None, scope[1] if scope else None)
        entries.extend((*head, *row) for row in report["entries"])
        totals.extend((*head, *row) for row in report["totals"])
        ballots.extend((*head, *row) for row in report["ballots"])
    head_schema = {"url": pl.String, "day": pl.String, "kind": pl.String, "county": pl.String, "scope": pl.String}
    pl.DataFrame(
        entries,
        schema={**head_schema, "office": pl.String, "bkind": pl.String, "bid": pl.String, "name": pl.String, "party": pl.String, "votes": pl.Int64},
        orient="row",
    ).write_parquet(PARSED / "entries.parquet")
    for name, rows in (("totals", totals), ("ballots", ballots)):
        pl.DataFrame(
            rows,
            schema={**head_schema, "office": pl.String, "bkind": pl.String, "bid": pl.String, "votes": pl.Int64},
            orient="row",
        ).write_parquet(PARSED / f"{name}.parquet")
    print(f"{len(unread)} reports unread:", *unread, sep="\n  ")


def classify(office_text: str, county: str | None) -> dict[str, str | int | None] | None:
    text = re.sub(r"\s+", " ", re.sub(r",? vote for \d+|NO\. OF CANDIDATES TO BE ELECTED \d+", "", office_text, flags=re.I)).strip().upper()
    text = re.sub(r"^FOR ", "", text)
    party = PARTY_SCOPE.match(text)
    bare = text[party.end() :] if party else text
    if STATE_OFFICES.search(bare.replace("BOROUGH PRESIDENT", "")):
        return None
    for seat, pattern in OFFICES.items():
        if pattern.match(text):
            return {"seat": seat, "party": party[1] if party else None, "county": None}
    matched = re.match(r"^(BOROUGH PRESIDENT|DISTRICT ATTORNEY|SURROGATE|JUDGE OF THE CIVIL COURT - COUNTY) \((NEW YORK|BRONX|KINGS|QUEENS|RICHMOND)\)", bare)
    if matched:
        return {"seat": matched[1].lower().replace(" ", "-"), "party": party[1] if party else None, "county": matched[2].title()}
    matched = re.match(r"^MEMBER OF THE CITY COUNCIL \((\d+)(?: COUNCIL DISTRICT)?\)", bare)
    if matched:
        return {"seat": "council", "number": int(matched[1]), "party": party[1] if party else None, "county": None}
    matched = re.match(r"^JUSTICE OF THE SUPREME COURT \((\d+)(?:ST|ND|RD|TH)? JUDICIAL DISTRICT\)", bare)
    if matched and county not in (None, "Crossover"):
        return {"seat": "supreme-court", "number": int(matched[1]), "party": party[1] if party else None, "county": county}
    matched = re.match(r"^JUDGE OF THE CIVIL COURT - DISTRICT \((\d+)(?:ST|ND|RD|TH)? MUNICIPAL COURT DISTRICT - (NEW YORK|BRONX|KINGS|QUEENS|RICHMOND)\)", bare)
    if matched:
        return {"seat": "civil-court-district", "number": int(matched[1]), "party": party[1] if party else None, "county": matched[2].title()}
    return None


def emit() -> Path:
    entries = pl.read_parquet(PARSED / "entries.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    ballots = pl.read_parquet(PARSED / "ballots.parquet")
    keys = []
    for office, county in entries.select("office", "county").unique().iter_rows():
        found = classify(office, county)
        if found is not None:
            keys.append((office, county, found))
    frames = []
    for office, county, found in keys:
        frames.append(
            entries.filter((pl.col("office") == office) & (pl.col("county") == county) & (pl.col("kind").is_in(["General", "Primary", "Primary Runoff"])))
            .with_columns(
                seat=pl.lit(found["seat"], pl.String),
                number=pl.lit(found.get("number"), pl.Int64),
                party_scope=pl.lit(found["party"], pl.String),
                contest_county=pl.lit(found["county"], pl.String),
                size=pl.lit(int(next(iter(re.findall(r"(?:vote for|ELECTED) (\d+)", office, re.I)), 1)), pl.Int64),
            )
        )
    frame = (
        pl.concat(frames)
        .filter(pl.col("county") != "Crossover")
        .with_columns(pl.col("name").str.replace_all(r"\s+", " ").str.replace_all(r"\s+([.,])", "$1").str.strip_chars())
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's 2005-2013 city and county offices by assembly district from the Board of Elections' certified recaps",
            polity="US-NY",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        city = cs.key("us.polity", "new-york-city")
        place = cs.area("us.place", "3651000")
        council = cs.key("us.body", "NY:3651000:council")
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")
        fips = {county: code for county, (code, _) in BOROUGHS.items()}
        county_units = {county: cs.unit("us.county", code) for county, code in fips.items()}
        county_areas = {county: cs.area("us.county", code) for county, code in fips.items()}
        known_parties = {
            value: entity
            for value, entity in conn.execute("select value, entity_id from external_id where scheme = 'us.party'").fetchall()
        }
        crossing = {
            (era, bid)
            for era, bid, count in (
                frame.filter(pl.col("bkind") == "ad")
                .with_columns(era=pl.when(pl.col("day") < "2012-01-01").then(pl.lit("2002")).otherwise(pl.lit("2012")))
                .group_by("era", "bid")
                .agg(pl.col("county").n_unique())
                .iter_rows()
            )
            if count > 1
        }
        unit_cache: dict[tuple[str, str, str], UUID] = {}
        added: set[UUID] = set()

        def party_id(line: str) -> list[UUID]:
            found = []
            for part in re.split(r"[\\/]", line):
                part = part.strip()
                key = PARTIES.get(part) or PARTIES.get(part.split("/")[0]) or f"NY:{slug(part)}"
                entity = known_parties.get(key)
                if entity is None:
                    entity = cs.key("us.party", key)
                    if entity in cs.minted and entity not in added:
                        added.add(entity)
                        cs.add("party", id=entity, kind="party", polity_id=cs.polity("US-NY"))
                        name(entity, part.title())
                    known_parties[key] = entity
                found.append(entity)
            return found

        def unit_for(era: str, bid: str, county: str) -> UUID:
            if (era, bid) not in crossing:
                return cs.unit("us.sldl", f"NY:{int(bid)}")
            code = f"NY:{int(bid)}:{fips[county]}"
            if (key := (era, bid, county)) not in unit_cache:
                unit_cache[key] = cs.unit("us.sldl", code)
                if unit_cache[key] in cs.minted and unit_cache[key] not in added:
                    added.add(unit_cache[key])
                    cs.add(
                        "reporting_unit",
                        id=unit_cache[key],
                        polity_id=city,
                        scheme_code="us.sldl",
                        code=code,
                        parent_id=county_units[county],
                        area_relation="within",
                        area_id=cs.area("us.sldl", f"NY:{int(bid)}"),
                        kind="standard",
                    )
            return unit_cache[key]

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
        made: set[UUID] = set()
        values = []
        skipped = []
        taken = 0
        groups = frame.select("day", "kind", "seat", "number", "party_scope", "contest_county").unique().sort("day", "seat")
        for day_text, kind, seat, number, scope, only_county in groups.iter_rows():
            day = date.fromisoformat(day_text)
            rows = frame.filter(
                (pl.col("day") == day_text)
                & (pl.col("kind") == kind)
                & (pl.col("seat") == seat)
                & (pl.col("number") == number if number is not None else pl.col("number").is_null())
                & (pl.col("party_scope") == scope if scope is not None else pl.col("party_scope").is_null())
            )
            if only_county is not None:
                rows = rows.filter(pl.col("county") == only_county)
            label = f"{day} {kind} {seat} {number or ''} {scope or ''} {only_county or ''}".strip()
            urls = {}
            for county, url in rows.select("county", "url").unique().sort("url").iter_rows():
                urls.setdefault(county, url)
            rows = rows.filter(pl.col("url").is_in(list(urls.values())))
            counties = sorted(urls)
            wide = seat in ("mayor", "public-advocate", "comptroller")
            if wide and len(counties) < 5:
                skipped.append(f"{label}: only {counties}")
                continue
            parts = []
            tied = True
            for county, url in urls.items():
                part = rows.filter(pl.col("url") == url)
                mine = part.filter(pl.col("bkind") == "ad")
                if mine.is_empty():
                    mine = part.filter(pl.col("bkind") == "total")
                    mine = mine.with_columns(pl.lit("county").alias("bkind"), pl.lit(county).alias("bid"))
                for office_text in part["office"].unique():
                    declared = totals.filter((pl.col("url") == url) & (pl.col("office") == office_text) & (pl.col("bkind") == "total"))["votes"]
                    if declared.is_empty() or int(mine.filter(pl.col("office") == office_text)["votes"].sum()) != int(declared[-1]):
                        tied = False
                parts.append(mine)
            if not tied:
                skipped.append(f"{label}: assembly district lines do not sum to the report's total")
                continue
            rows = pl.concat(parts)
            era = "2002" if day_text < "2012-01-01" else "2012"
            if kind == "General":
                election = conn.execute(
                    "select id from election where polity_id = %s and date_from = %s and kind in ('local', 'general') order by kind = 'local' desc",
                    [city, day],
                ).fetchone()
                if election is None:
                    election = conn.execute(
                        "select entity_id from external_id where scheme = 'us.election' and value = %s", [f"{day}:NY"]
                    ).fetchone()
                if election is None:
                    skipped.append(f"{label}: no election")
                    continue
                election = election[0]
            else:
                election_kind = "primary" if kind == "Primary" else "runoff"
                election = conn.execute(
                    "select id from election where polity_id = %s and date_from = %s and kind in ('primary', 'runoff')", [city, day]
                ).fetchone()
                if election is None:
                    election = cs.key("us.election", f"{day}:new-york-city:primary")
                    if election not in cs.minted:
                        pass
                    elif election not in added:
                        added.add(election)
                        cs.add(
                            "election",
                            id=election,
                            polity_id=city,
                            kind=election_kind,
                            date_from=day,
                            date_to=day,
                            date_precision="day",
                            status="held",
                        )
                        name(election, f"{day.year} New York City {election_kind} election")
                        cs.add("slug", path=f"us/ny/new-york-city/{day}-{election_kind}", entity_id=election)
                else:
                    election = election[0]
            election_path = conn.execute("select path from slug where entity_id = %s limit 1", [election]).fetchone()
            election_path = election_path[0] if election_path else f"us/ny/new-york-city/{day}-{'primary' if kind == 'Primary' else 'runoff'}"
            office = body_id = None
            area = None
            county_name = only_county
            nickname = BOROUGHS[county_name][1] if county_name else None
            if seat == "mayor":
                path, title, office, area = "mayor", "New York City Mayor", cs.key("us.office", "NY:3651000:mayor"), place
            elif seat in ("public-advocate", "comptroller"):
                path, title = seat, f"New York City {seat.replace('-', ' ').title()}"
                office, area = cs.key("us.office", f"new-york-city:{seat}"), place
            elif seat == "borough-president":
                path, title = f"borough-president-{slug(nickname)}", f"{nickname} Borough President"
                office, area = cs.key("us.office", f"new-york-city:borough-president:{slug(nickname)}"), county_areas[county_name]
            elif seat == "district-attorney":
                path, title = f"district-attorney-{slug(county_name)}", f"{county_name} County District Attorney"
                office, area = cs.key("us.office", f"NY:{fips[county_name]}:district-attorney"), county_areas[county_name]
            elif seat == "council":
                path, title = f"council-district-{number}", f"New York City Council, District {number}"
                area = cs.area("us.local_district", f"36:3651000:council:district-{number}")
                body_id = council
            else:
                office_key, path, title, area = {
                    "surrogate": (f"NY:{fips[county_name]}:surrogate", f"surrogate-{slug(county_name)}", f"{county_name} County Surrogate", county_areas[county_name]),
                    "judge-of-the-civil-court---county": (f"NY:{fips[county_name]}:civil-court", f"civil-court-{slug(county_name)}", f"{county_name} County Civil Court Judge", county_areas[county_name]),
                    "supreme-court": (f"NY:{fips[county_name]}:supreme-court:{number}", f"supreme-court-{number}-{slug(county_name)}", f"Judicial District {number} Supreme Court Justice", county_areas[county_name]),
                    "civil-court-district": (f"NY:{fips[county_name]}:civil-court-district-{number}", f"civil-court-district-{number}-{slug(county_name)}", f"Municipal Court District {number}, {county_name} County Civil Court Judge", None),
                }[seat]
                office = cs.key("us.office", office_key)
                if office in cs.minted and office not in added:
                    added.add(office)
                    cs.add("office", id=office, polity_id=city, kind="other")
                    name(office, title)
            party = party_id(scope.title())[0] if scope is not None else None
            seat_path = f"{path}-{slug(scope)}" if scope else path
            if (office is not None and office in cs.minted and office not in added) or (area is not None and area in cs.minted):
                skipped.append(f"{label}: its office or district is not in the archive")
                continue
            if conn.execute(
                """select 1 from contest c join entity e on e.id = c.id and e.retired_by is null
                   where c.election_id = %s and c.office_id is not distinct from %s and c.body_id is not distinct from %s
                   and c.district_area_id is not distinct from %s and c.party_scope_id is not distinct from %s
                   and c.changeset_id not in (select id from changeset where pipeline = %s)""",
                [election, office, body_id, area, party, SOURCE],
            ).fetchone():
                skipped.append(f"{label}: already in the archive")
                continue
            magnitude = int(rows["size"][0])
            contest_key = f"{day}:new-york-city:{seat_path}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if body_id else "office",
                office_id=office,
                body_id=body_id,
                party_scope_id=party,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=fptp if magnitude == 1 else block,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"{day.year} {scope.title() + ' primary for ' if scope else ''}{title}{'' if scope else ' election'}")
            cs.add("slug", path=f"{election_path}/{seat_path}", entity_id=contest)
            source_title = f"{TITLE}, {kind} {day}"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted and source not in added:
                added.add(source)
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url=next(iter(urls.values())), license="Public record")
            for url in urls.values():
                cs.artifact(raw.cached(SOURCE, url), source)
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
            real = rows.filter(pl.col("party").is_null() | (pl.col("party") != "WRITE-IN")).with_columns(
                pl.col("name").str.replace_all(r"\s+", " ").alias("name")
            )
            ranked = real.group_by("name").agg(pl.col("votes").sum()).sort("votes", "name", descending=[True, False])
            margin = magnitude > 0 and ranked.height > magnitude and ranked["votes"][magnitude - 1] == ranked["votes"][magnitude]
            roots: dict[str, UUID] = {}
            for rank, candidate in enumerate(ranked["name"]):
                label_text = candidate.title()
                root_key = f"{contest_key}:{slug(candidate)}"
                roots[candidate] = cs.key("us.option", root_key)
                words = [w for w in re.sub(r"[^a-z ]", " ", label_text.lower()).split() if w not in ("jr", "sr", "ii", "iii")]
                matched = people.get((words[0], words[-1])) if len(words) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person = matched[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"NY:{slug(label_text)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, label_text)
                cs.add("contest_option", id=roots[candidate], contest_id=contest, kind="candidate", party_id=party, ballot_label=label_text)
                head = cs.key("us.candidacy", f"{root_key}:head")
                cs.add("candidacy", id=head, person_id=person, option_id=roots[candidate], role="head", ballot_name=label_text, is_independent=False)
                if party is not None:
                    cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                else:
                    for line in real.filter(pl.col("name") == candidate)["party"].drop_nulls().unique().sort():
                        for entity in party_id(line):
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=entity, kind="nominated")
                if not margin:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=roots[candidate],
                        kind=("advanced" if kind != "General" else "elected") if rank < magnitude else "lost",
                    )
            tickets: dict[tuple[str, str | None], UUID] = {}
            write_in = None
            if rows.filter(pl.col("party") == "WRITE-IN").height:
                write_in = cs.key("us.option", f"{contest_key}:write_in")
                cs.add("contest_option", id=write_in, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            summed: dict[tuple[UUID, UUID], int] = {}
            for county, bkind, bid, candidate, line, votes in (
                rows.with_columns(pl.col("name").str.replace_all(r"\s+", " "))
                .group_by("county", "bkind", "bid", "name", "party")
                .agg(pl.col("votes").sum())
                .iter_rows()
            ):
                unit = unit_for(era, bid, county) if bkind == "ad" else county_units[county]
                if line == "WRITE-IN":
                    option = write_in
                elif line is None:
                    option = roots[candidate]
                else:
                    if (candidate, line) not in tickets:
                        tickets[(candidate, line)] = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{slug(line)}")
                        cs.add(
                            "contest_option",
                            id=tickets[(candidate, line)],
                            contest_id=contest,
                            parent_option_id=roots[candidate],
                            kind="ticket",
                            party_id=party_id(line)[0],
                            ballot_label=f"{candidate.title()} ({line.title()})",
                        )
                    option = tickets[(candidate, line)]
                summed[(unit, option)] = summed.get((unit, option), 0) + votes
            values.extend((str(result_set), str(unit), str(option), 1, votes) for (unit, option), votes in summed.items())
            for county, bkind, bid in rows.select("county", "bkind", "bid").unique().iter_rows():
                unit = unit_for(era, bid, county) if bkind == "ad" else county_units[county]
                cast = ballots.filter((pl.col("url") == urls[county]) & (pl.col("bkind") == bkind) & (pl.col("bid") == bid) & pl.col("office").is_in(rows["office"].unique().to_list()))["votes"]
                if bkind == "ad" and not cast.is_empty():
                    values.append((str(result_set), str(unit), None, 3, int(cast[0])))
                if magnitude == 1:
                    got = rows.filter((pl.col("county") == county) & (pl.col("bid") == bid))["votes"].sum()
                    values.append((str(result_set), str(unit), None, 4, int(got)))
            taken += 1
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        print(f"{taken} contests taken; {len(skipped)} not loaded:", *skipped, sep="\n  ")
        return cs.write()
