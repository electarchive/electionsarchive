import csv
import io
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_general"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
RESULTS = "https://www.vote.nyc/sites/default/files/pdf/election_results"
PUBLISHER = "Board of Elections in the City of New York"
COUNTIES = {
    "New York": "36061",
    "Bronx": "36005",
    "Kings": "36047",
    "Queens": "36081",
    "Richmond": "36085",
}
COUNTERS = {
    "Public Counter",
    "Manually Counted Emergency",
    "Emergency",
    "Absentee / Military",
    "Absentee/Military",
    "Federal",
    "Special Presidential",
    "Affidavit",
}
INAPPLICABLE = {"Federal", "Special Presidential"}
SCATTERED = "Scattered"
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
    "Save Our City": "NY:save-our-city",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
    "Socialism & Lib": "party-for-socialism-and-liberation",
    "Independent": None,
    "Safe&Affordable/EndAntiSemitism": None,
}
PERSONS = {
    "Zohran Kwame Mamdani": ("us.klarner.person", "400070"),
    "Andrew M. Cuomo": ("wikidata", "Q11673"),
    "Curtis A. Sliwa": ("us.alged.person", "35566"),
    "Eric L. Adams": ("us.klarner.person", "277292"),
    "Jim Walden": (f"{SOURCE}.person", "jim-walden"),
    "Irene Estrada": (f"{SOURCE}.person", "irene-estrada"),
    "Jessica Hernandez": (f"{SOURCE}.person", "jessica-hernandez"),
}
NOTE = (
    "Election district returns from the Board of Elections' certified ED-level report. Each candidate's votes are "
    "split by ballot line, as New York's fusion ballot prints them, and write-ins are one Scattered line, as the "
    "report gives them. Ballots cast are the sum of the report's public counter, emergency, absentee and military, "
    "and affidavit ballots. Election districts the Board combined into another for the count hold no values and "
    "point to the district they were counted with, and districts missing from City Planning's map for the election, all with no ballots, have no shape. Summed by borough, every line matches the Board's certified "
    "recap."
)


SITE = "https://www.vote.nyc"
YEARS = range(2014, 2026)
OUT_OF_SCOPE = re.compile(r"Committee|Leader|Delegate", re.IGNORECASE)
STATE_FEDERAL = re.compile(
    r"Assembly|State Senator|Representative in Congress|President|Governor|State Comptroller|Attorney General|"
    r"United States Senator",
    re.IGNORECASE,
)
CITY = re.compile(
    r"^(Crossover |Democratic |Republican |Reform |Conservative |Working Families |Green |Libertarian )?"
    r"((Bronx|Kings|New York|Queens|Richmond|Citywide) )?"
    r"(Mayor|City Comptroller|Public Advocate|Borough President|Member of the City Council|District Attorney|Surrogate)",
    re.IGNORECASE,
)


def listing() -> list[dict[str, str]]:
    found = []
    for year in YEARS:
        page = raw.fetch(SOURCE, f"{SITE}/page/election-results-summary-{year}", suffix=".html")
        for href in re.findall(r'href="([^"]*EDLevel\.csv)"', Path(page.path).read_text()):
            path = unquote(href)
            if OUT_OF_SCOPE.search(path.rsplit("/", 1)[1]):
                continue
            match = re.search(r"election_results/\d{4}/([^/]+)/([0-9]{10}[0-9A-Z])(.*) EDLevel\.csv$", path)
            if match is None:
                raise ValueError(f"unrecognized results file {path}")
            held = re.match(r"(\d{8})(.*)$", match[1]) or re.match(r"(\d{1,2})-(\d{2})-(\d{4})_(.*)$", match[1])
            if held is None:
                raise ValueError(f"unrecognized election folder {path}")
            day = held[1] if held.lastindex == 2 else f"{held[3]}{int(held[1]):02d}{held[2]}"
            event = held[2] if held.lastindex == 2 else held[4]
            name = match[3]
            if OUT_OF_SCOPE.search(name) or (STATE_FEDERAL.search(name) and not CITY.search(name)):
                continue
            found.append(
                {"date": day, "event": event, "code": match[2], "name": name, "url": SITE + quote(path, safe="/")}
            )
    return found


NOTE_QUESTION = (
    "Election district returns from the Board of Elections' certified ED-level report. Ballots cast are the sum of "
    "the report's public counter, emergency, absentee and military, and affidavit ballots. Election districts the "
    "Board combined into another for the count hold no values and point to the district they were counted with. "
    "Summed by borough and assembly district, every line matches the Board's certified recap."
)


def fetch() -> None:
    for item in listing():
        for kind in ("EDLevel", "Recap"):
            url = item["url"].removesuffix("EDLevel.csv") + f"{kind}.csv"
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                time.sleep(1)
                try:
                    raw.fetch(SOURCE, url, suffix=".csv")
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise
                    print(f"404 {url}")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    contests = []
    for item in listing():
        base = item["url"].removesuffix("EDLevel.csv")
        item = {**item, "file": base}
        try:
            raw.cached(SOURCE, base + "EDLevel.csv")
            raw.cached(SOURCE, base + "Recap.csv")
        except LookupError:
            contests.append({**item, "ok": False, "detail": "not fetched"})
            continue
        try:
            table = pl.read_csv(
                raw.cached(SOURCE, base + "EDLevel.csv").path,
                has_header=False,
                infer_schema=False,
                truncate_ragged_lines=True,
            )
        except pl.exceptions.PolarsError as error:
            contests.append({**item, "ok": False, "detail": f"unreadable ED file: {error}"})
            continue
        offset = table.width - 11
        table = table.select(pl.nth(range(offset, table.width))).rename(
            dict(
                zip(
                    table.select(pl.nth(range(offset, table.width))).columns,
                    (
                        "ad",
                        "ed",
                        "county",
                        "status",
                        "event",
                        "body",
                        "office",
                        "district",
                        "vote_for",
                        "unit",
                        "tally",
                    ),
                    strict=True,
                )
            )
        )
        table = table.filter(pl.col("ad") != "AD")
        if table.is_empty():
            contests.append({**item, "ok": False, "detail": "empty ED file"})
            continue
        malformed = table.filter(~pl.col("tally").str.strip_chars().str.replace_all(",", "").str.contains(r"^\d+$"))
        if malformed.height:
            contests.append(
                {
                    **item,
                    "ok": False,
                    "detail": f"{malformed.height} ED lines with a non-numeric tally, first {malformed.row(0)}",
                }
            )
            continue
        rows = table.select(
            code=pl.col("ad") + pl.col("ed"),
            ad="ad",
            county=pl.col("county").replace_strict(COUNTIES),
            combined=pl.col("status")
            .str.extract(r"^COMBINED INTO (\d{3})/(\d{2})$", 2)
            .add(pl.col("status").str.extract(r"^COMBINED INTO (\d{3})/(\d{2})$", 1)),
            status="status",
            unit="unit",
            votes=pl.col("tally").str.strip_chars().str.replace_all(",", "").cast(pl.Int64),
        )
        first = table.row(0, named=True)
        key = {
            "office": first["office"],
            "body": first["body"] or "",
            "district": first["district"],
            "vote_for": first["vote_for"],
        }
        odd = rows.filter((pl.col("status") != "IN-PLAY") & pl.col("combined").is_null())
        if odd.height:
            contests.append(
                {**item, **key, "ok": False, "detail": f"unknown ED statuses {odd['status'].unique().to_list()}"}
            )
            continue
        if rows.filter(pl.col("combined").is_not_null() & (pl.col("votes") != 0)).height:
            contests.append({**item, **key, "ok": False, "detail": "a combined election district holds votes"})
            continue
        recap = []
        text = Path(raw.cached(SOURCE, base + "Recap.csv").path).read_text()
        county = ad = scope = None
        for cells in csv.reader(io.StringIO(text)):
            cells = [cell.strip() for cell in cells]
            for cell in cells:
                found = re.search(r"^(Bronx|Kings|New York|Queens|Richmond) County$", cell, re.MULTILINE)
                if found:
                    county = COUNTIES[found[1]]
                    if cell == found[0]:
                        scope = "county"
                        ad = None
                found = re.fullmatch(r"Assembly District (\d+)", cell)
                if found:
                    ad = found[1]
                    scope = "ad"
                if cell.startswith("Total for "):
                    ad = None
                    scope = "county" if re.search(r" (Bronx|Kings|New York|Queens|Richmond) County$", cell) else "city"
            for index, cell in enumerate(cells[:-1]):
                if (
                    cell
                    and not re.fullmatch(r"[\d,]+", cell)
                    and re.fullmatch(r"[\d,]+", cells[index + 1])
                    and not cell.startswith(("Total", "Unrecorded", "Less - ", "Page ", "Assembly District"))
                ):
                    recap.append(
                        {
                            "scope": scope,
                            "county": county,
                            "ad": ad,
                            "unit": SCATTERED if cell.endswith("(Write-In)") else cell,
                            "votes": int(cells[index + 1].replace(",", "")),
                        }
                    )
        if not recap:
            contests.append({**item, **key, "ok": False, "detail": "no recap values"})
            continue
        recap = pl.DataFrame(
            recap,
            schema={"scope": pl.String, "county": pl.String, "ad": pl.String, "unit": pl.String, "votes": pl.Int64},
        )
        differences = []
        finest = None
        for grain, level in ((["county", "ad"], "ad"), (["county"], "county"), ([], "city")):
            scope = recap.filter(pl.col("scope") == level)
            if scope.is_empty():
                continue
            finest = finest or level
            theirs = scope.group_by(*grain, "unit").agg(pl.col("votes").sum())
            ours = rows.group_by(*grain, "unit").agg(pl.col("votes").sum())
            if grain:
                ours = ours.join(theirs.select(grain).unique(), on=grain, how="semi")
            differences.append(
                (
                    level,
                    ours.join(theirs, on=[*grain, "unit"], how="full", coalesce=True, suffix="_recap").filter(
                        pl.col("votes").fill_null(0) != pl.col("votes_recap").fill_null(0)
                    ),
                )
            )
        differences = [(level, d) for level, d in differences if d.height]
        gap = max(
            (
                abs(votes - recap_votes)
                for _, d in differences
                for votes, recap_votes in zip(d["votes"].fill_null(0), d["votes_recap"].fill_null(0), strict=True)
            ),
            default=0,
        )
        if any(level == finest for level, _ in differences) and gap > TOLERANCE:
            contests.append(
                {
                    **item,
                    **key,
                    "ok": False,
                    "detail": f"{sum(d.height for _, d in differences)} lines differ from the recap, first {differences[0][1].row(0)}",
                }
            )
            continue
        if differences:
            contests.append(
                {
                    **item,
                    **key,
                    "ok": True,
                    "detail": "; ".join(
                        f"the ED report differs from the recap's {level} figures by at most {gap} votes in {d.height} lines, "
                        f"first {d.row(0)}"
                        for level, d in differences
                    ),
                }
            )
            frames.append(rows.drop("status").with_columns(file=pl.lit(base), date=pl.lit(item["date"])))
            continue
        contests.append({**item, **key, "ok": True, "detail": ""})
        frames.append(rows.drop("status").with_columns(file=pl.lit(base), date=pl.lit(item["date"])))
    if frames:
        pl.concat(frames).write_parquet(PARSED / "districts.parquet")
    pl.DataFrame(contests).write_parquet(PARSED / "contests.parquet")


BOROUGH = {fips: name for name, fips in COUNTIES.items()}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)")
RANKED_OFFICES = {"Mayor", "City Comptroller", "Public Advocate", "Borough President", "Member of the City Council"}
RANKED_DAYS = {"20210622", "20250624", "20210202", "20210223", "20201222"}
TOLERANCE = 2
DATES = os.environ.get("NYC_DATES")
OFFICES = re.compile(
    r"^(Mayor|City Comptroller|Public Advocate|Borough President|Member of the City Council|District Attorney|"
    r"Surrogate|Judge of the Civil Court|Justice of the Supreme Court)"
)
QUESTION_DAYS = set(os.environ.get("NYC_QUESTIONS", "").split(",")) - {""}
STATE_QUESTIONS = ("Environmental Bond Act", "Olympic Sports Complex")


def emit() -> Path:
    added_offices = set()
    frame = pl.read_parquet(PARSED / "districts.parquet")
    contests = pl.read_parquet(PARSED / "contests.parquet").filter(pl.col("ok"))
    contests = contests.filter(
        ~(pl.col("date").is_in(RANKED_DAYS) & pl.col("office").is_in(RANKED_OFFICES)),
        pl.col("office").str.contains(OFFICES.pattern)
        | (pl.col("date").is_in(QUESTION_DAYS) & ~pl.col("office").str.contains("|".join(STATE_QUESTIONS))),
    )
    if DATES:
        contests = contests.filter(pl.col("date").is_in(DATES.split(",")))
    frame = frame.join(contests.select("file", "office", "body", "district", "vote_for"), on="file").with_columns(
        pl.col("unit").str.replace_all(r"\s+", " ")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's contests by election district from the Board of Elections' certified ED-level reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        city = cs.key("us.polity", "new-york-city")
        place = cs.area("us.place", "3651000")
        council = cs.key("us.body", "NY:3651000:council")
        if cs.minted & {city, council}:
            raise ValueError("New York City and its council must already exist")
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")
        referendum = cs.key("us.electoral_system", "referendum")
        taken: list[str] = []
        counties = {fips: cs.unit("us.county", fips) for fips in BOROUGH}
        parties: dict[str, UUID | None] = {}
        lines = (
            frame.filter(pl.col("unit").str.contains(r"\(.+\)$"), pl.col("unit") != SCATTERED)
            .select(pl.col("unit").str.extract(r"\(([^()]+)\)$").alias("line"))["line"]
            .unique()
            .sort()
            .to_list()
        )
        known_parties: dict[str, str] = {}
        for value, text in conn.execute(
            "select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id "
            "where x.scheme = 'us.party' and x.value like 'NY:%' order by x.value desc"
        ).fetchall():
            known_parties[re.sub(r"(?: of new york(?: state)?| party)$", "", slug(text).replace("-", " "))] = value
        for line in {*lines, *(b for b in frame["body"].unique().to_list() if b)}:
            key = PARTIES.get(
                line,
                known_parties.get(re.sub(r"(?: of new york(?: state)?| party)$", "", slug(line).replace("-", " ")))
                or f"NY:{slug(line)}",
            )
            if key is None:
                parties[line] = None
                continue
            party = cs.key("us.party", key)
            if party in cs.minted:
                if line in PARTIES:
                    raise ValueError(f"the {line} party must already exist")
                cs.add("party", id=party, kind="party", polity_id=cs.polity("US-NY"))
                name(party, line)
            parties[line] = party

        for day_text in sorted(frame["date"].unique()):
            day = date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:]))
            day_frame = frame.filter(pl.col("date") == day_text)
            event = contests.filter(pl.col("date") == day_text)["event"][0]
            kind = "primary" if "Primary" in event else "special" if "Special" in event else "local"
            election = conn.execute(
                "select id from election where polity_id = %s and date_from = %s and kind in ('local', 'primary', 'special', 'general') "
                "and (%s = 'primary') = (kind = 'primary') order by kind = 'local' desc",
                [city, day, kind],
            ).fetchall()
            if len(election) > 1:
                election = [election[0]]
            if election:
                election = election[0][0]
            else:
                election = cs.key(
                    "us.election", f"{day}:new-york-city:primary" if kind == "primary" else f"{day}:NY:3651000"
                )
                parent = None
                if kind == "local":
                    parent = conn.execute(
                        "select entity_id from external_id where scheme = 'us.election' and value = %s", [f"{day}:NY"]
                    ).fetchone()
                    if parent is None:
                        raise ValueError(f"{day}: the state's election must already exist")
                    parent = parent[0]
                cs.add(
                    "election",
                    id=election,
                    polity_id=city,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} New York City {kind if kind != 'local' else 'local'} election")
                cs.add("slug", path=f"us/ny/new-york-city/{day}-{kind}", entity_id=election)
            election_path = conn.execute("select path from slug where entity_id = %s limit 1", [election]).fetchone()
            election_path = election_path[0] if election_path else f"us/ny/new-york-city/{day}-{kind}"

            places = {
                code: (area, county)
                for code, area, county in conn.execute(
                    """select c.code, c.area_id, u.id from area_code c
                       join area a on a.id = c.area_id and a.scheme_code = 'us.nyc_ed'
                       join area_parent p on p.area_id = c.area_id and p.valid @> %s::date
                       join area_code pc on pc.area_id = p.parent_id
                       join reporting_unit u on u.scheme_code = 'us.county' and u.code = pc.code
                       where c.valid @> %s::date""",
                    [day, day],
                ).fetchall()
            }
            districts = (
                day_frame.group_by("code")
                .agg(pl.col("county").first(), pl.col("combined").first(), pl.col("votes").max())
                .sort("code")
            )
            units = {code: cs.unit("us.nyc_ed", code, day) for code in districts["code"]}
            for code, county, combined, most in districts.iter_rows():
                area, parent = places.get(code, (None, counties[county]))
                if area is None and most and places:
                    raise ValueError(f"{day}: no area for election district {code}, which has votes")
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=city,
                    scheme_code="us.nyc_ed",
                    code=code,
                    parent_id=parent,
                    counted_with_id=None if combined is None else units[combined],
                    area_relation="unlocated" if area is None else "equals",
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=units[code],
                    area_id=place,
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )

            for (office_text, body, district), rows in day_frame.group_by(
                "office", "body", "district", maintain_order=True
            ):
                files = contests.filter(
                    (pl.col("date") == day_text)
                    & (pl.col("office") == office_text)
                    & (pl.col("body") == body)
                    & (pl.col("district") == district)
                )
                spread = rows.group_by("code", "unit").agg(
                    (pl.col("votes").max() - pl.col("votes").min()).alias("spread")
                )
                if spread.filter(pl.col("spread") > TOLERANCE).height:
                    raise ValueError(f"{day} {office_text} {district}: files disagree on an election district's votes")
                disagree = spread.filter(pl.col("spread") > 0).height
                rows = rows.unique(["code", "unit"], keep="first", maintain_order=True)
                borough = rows["county"].unique().to_list()
                title_text = files["name"][0]
                ordinal = ORDINAL.search(title_text)
                ordinal_text, number = (ordinal[0], ordinal[1]) if ordinal else ("", "")
                if not number and office_text in (
                    "Justice of the Supreme Court",
                    "Judge of the Civil Court - District",
                ):
                    raise ValueError(f"{day}: no district number in {title_text}")
                magnitude = int(rows["vote_for"][0])
                office = body_id = area = None
                question = OFFICES.match(office_text) is None
                if question:
                    seat, title, area = f"proposal-{slug(office_text)[:48].rstrip('-')}", office_text, place
                elif office_text == "Mayor":
                    seat, title, area = "mayor", "New York City Mayor", place
                    office = cs.key("us.office", "NY:3651000:mayor")
                elif office_text in ("City Comptroller", "Public Advocate"):
                    seat = "comptroller" if office_text == "City Comptroller" else "public-advocate"
                    title, area = f"New York City {office_text.removeprefix('City ')}", place
                    office = cs.key("us.office", f"new-york-city:{seat}")
                elif office_text == "Member of the City Council":
                    seat, title = (
                        f"council-district-{int(district)}",
                        f"New York City Council, District {int(district)}",
                    )
                    area = cs.area("us.local_district", f"36:3651000:council:district-{int(district)}")
                    body_id = council
                else:
                    if len(borough) != 1:
                        raise ValueError(f"{day} {title_text}: a county contest spans {borough}")
                    fips = borough[0]
                    county_name = BOROUGH[fips]
                    area = cs.area("us.county", fips)
                    if office_text == "Borough President":
                        nickname = {"New York": "Manhattan", "Kings": "Brooklyn", "Richmond": "Staten Island"}.get(
                            county_name, county_name
                        )
                        seat, title = f"borough-president-{slug(nickname)}", f"{nickname} Borough President"
                        office = cs.key("us.office", f"new-york-city:borough-president:{slug(nickname)}")
                    elif office_text == "District Attorney":
                        seat, title = (
                            f"district-attorney-{slug(county_name)}",
                            f"{county_name} County District Attorney",
                        )
                        office = cs.key("us.office", f"NY:{fips}:district-attorney")
                    else:
                        if office_text == "Surrogate":
                            seat, title, office_key = (
                                f"surrogate-{slug(county_name)}",
                                f"{county_name} County Surrogate",
                                f"NY:{fips}:surrogate",
                            )
                        elif office_text == "Justice of the Supreme Court":
                            seat = f"supreme-court-{number}-{slug(county_name)}"
                            title = f"{ordinal_text} Judicial District Supreme Court Justice"
                            office_key = f"NY:{fips}:supreme-court:{number}"
                        elif office_text == "Judge of the Civil Court - County":
                            seat, title = f"civil-court-{slug(county_name)}", f"{county_name} County Civil Court Judge"
                            office_key = f"NY:{fips}:civil-court"
                        elif office_text == "Judge of the Civil Court - District":
                            seat = f"civil-court-district-{number}-{slug(county_name)}"
                            title = f"{ordinal_text} Municipal Court District, {county_name} County Civil Court Judge"
                            office_key = f"NY:{fips}:civil-court-district-{number}"
                            area = None
                        else:
                            raise ValueError(f"{day}: unclassified office {office_text}")
                        office = cs.key("us.office", office_key)
                        if office in cs.minted and office not in added_offices:
                            added_offices.add(office)
                            cs.add("office", id=office, polity_id=city, kind="other")
                            name(
                                office,
                                title.removesuffix(" Civil Court Judge") + " Civil Court Judge"
                                if "Civil" in title
                                else title,
                            )
                if (
                    office is not None
                    and office in cs.minted
                    and office_text
                    in ("Mayor", "City Comptroller", "Public Advocate", "Borough President", "District Attorney")
                ):
                    raise ValueError(f"{day}: the office for {title} must already exist")
                if body_id is not None and area in cs.minted:
                    raise ValueError(f"{day}: council district for {title} must already exist")
                party = parties[body] if body else None
                adjective = body
                seat_path = f"{seat}-{slug(body)}" if body else seat

                existing = (
                    []
                    if question
                    else conn.execute(
                        """select c.id from contest c where c.election_id = %s and c.office_id is not distinct from %s
                       and c.body_id is not distinct from %s and c.district_area_id is not distinct from %s
                       and c.party_scope_id is not distinct from %s""",
                        [election, office, body_id, area, party],
                    ).fetchall()
                )
                if len(existing) > 1:
                    raise ValueError(f"{day}: several contests match {title}")
                if existing:
                    contest = existing[0][0]
                    contest_key = conn.execute(
                        "select value from external_id where entity_id = %s and scheme = 'us.contest' limit 1",
                        [contest],
                    ).fetchone()
                    if contest_key is None:
                        raise ValueError(f"{day}: {title} has no contest key")
                    contest_key = contest_key[0]
                else:
                    contest_key = f"{day}:new-york-city:{seat_path}"
                    contest = cs.key("us.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="ballot_question" if question else "body_seats" if body_id else "office",
                        office_id=office,
                        body_id=body_id,
                        party_scope_id=party,
                        district_area_id=area,
                        magnitude=magnitude,
                        round_number=1,
                        electoral_system_id=referendum if question else fptp if magnitude == 1 else block,
                        default_result_kind="counted",
                        term=None if question else "full",
                    )
                    if question:
                        cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                    name(
                        contest,
                        f"{day.year} New York City ballot question: {title}"
                        if question
                        else f"{day.year} {adjective + ' primary for ' if body else ''}{title}{'' if body else ' election'}",
                    )
                    cs.add("slug", path=f"{election_path}/{seat_path}", entity_id=contest)

                base_files = files["file"].to_list()
                inconsistent = (
                    f" The Board's borough and citywide reports differ by at most {TOLERANCE} votes in {disagree} "
                    "election district lines; the first report listed, the citywide one where there is one, is used."
                    if disagree
                    else ""
                ) + "".join(
                    f" The Board's recap is not consistent with itself: {detail}."
                    for detail in files["detail"].unique().sort().to_list()
                    if detail
                )
                source_title = f"{event} {day.year}: {title_text}, ED Level"
                source = cs.source(PUBLISHER, source_title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=source_title,
                    url=base_files[0] + "EDLevel.csv",
                    license="Public record",
                )
                for base in base_files:
                    for suffix in ("EDLevel", "Recap"):
                        cs.artifact(raw.cached(SOURCE, f"{base}{suffix}.csv"), source)

                if question:
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
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE_QUESTION + inconsistent)
                    for county_contest, county_key, county_name in conn.execute(
                        """select c.id, x.value, n.text from contest c
                           join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
                           join name n on n.entity_id = c.id and n.is_preferred
                           join changeset h on h.id = c.changeset_id
                           where h.pipeline = 'us.medsl_local_measures' and c.kind = 'ballot_question'""",
                        [f"{day}:NY:36%:question:%"],
                    ).fetchall():
                        county_title = slug(re.sub(r"^\d{4} .+? County: ", "", county_name))
                        if len(county_title) < 8 or not slug(title).startswith(county_title):
                            continue
                        for prior in conn.execute(
                            """select id, election_id, contest_id, stage, kind, source_id, completeness from result_set
                               where contest_id = %s and is_default""",
                            [county_contest],
                        ).fetchall():
                            cs.add(
                                "result_set",
                                **dict(
                                    zip(
                                        (
                                            "id",
                                            "election_id",
                                            "contest_id",
                                            "stage",
                                            "kind",
                                            "source_id",
                                            "completeness",
                                        ),
                                        prior,
                                        strict=True,
                                    )
                                ),
                                is_default=False,
                                status="superseded",
                            )
                        cs.add(
                            "entity_retirement",
                            entity_id=county_contest,
                            reason="Superseded by the New York City measure from the Board of Elections' ED-level report",
                        )
                        taken.append(county_key)
                    answers = {}
                    for position, answer in enumerate(("Yes", "No"), start=1):
                        answers[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                        cs.add(
                            "contest_option",
                            id=answers[answer],
                            contest_id=contest,
                            kind="answer",
                            ballot_position=position,
                            ballot_label=answer,
                        )
                    counted = rows.filter(pl.col("combined").is_null()).with_columns(
                        unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()})
                    )
                    votes = counted.filter(pl.col("unit").is_in(answers))
                    cast = counted.filter(pl.col("unit").is_in(COUNTERS), ~pl.col("unit").is_in(INAPPLICABLE))
                    cs.frame(
                        "result_value",
                        pl.concat(
                            [
                                votes.select(
                                    result_set_id=pl.lit(str(result_set)),
                                    unit_id="unit_id",
                                    option_id=pl.col("unit").replace_strict({k: str(v) for k, v in answers.items()}),
                                    channel=pl.lit(0, pl.Int16),
                                    measure=pl.lit(1, pl.Int16),
                                    count_round=pl.lit(0, pl.Int16),
                                    flags=pl.lit(0, pl.Int16),
                                    value="votes",
                                ),
                                *(
                                    frame_.group_by("unit_id")
                                    .agg(pl.col("votes").sum())
                                    .select(
                                        result_set_id=pl.lit(str(result_set)),
                                        unit_id="unit_id",
                                        option_id=pl.lit(None, pl.String),
                                        channel=pl.lit(0, pl.Int16),
                                        measure=pl.lit(measure, pl.Int16),
                                        count_round=pl.lit(0, pl.Int16),
                                        flags=pl.lit(0, pl.Int16),
                                        value="votes",
                                    )
                                    for measure, frame_ in ((3, cast), (4, votes))
                                ),
                            ]
                        ),
                    )
                    continue

                totals = (
                    rows.filter(
                        pl.col("combined").is_null(), ~pl.col("unit").is_in(COUNTERS), pl.col("unit") != SCATTERED
                    )
                    .group_by(pl.col("unit").str.replace(r" \([^()]+\)$", "").alias("candidate"))
                    .agg(pl.col("votes").sum())
                    .sort("votes", "candidate", descending=[True, False])
                )
                if totals.is_empty():
                    continue
                if totals.height > magnitude and totals["votes"][magnitude - 1] == totals["votes"][magnitude]:
                    raise ValueError(f"{day}: {title} has a tie at the margin")

                known = {}
                clash = set()
                roots = conn.execute(
                    """select o.id, ca.ballot_name, ca.person_id, x.value from contest_option o
                       join candidacy ca on ca.option_id = o.id
                       left join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
                       where o.contest_id = %s and o.parent_option_id is null and o.kind = 'candidate'""",
                    [contest],
                ).fetchall()
                for option_id, ballot_name, person_id, option_value in roots:
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", slug(ballot_name).replace("-", " ")).split()
                        if w not in SUFFIXES
                    ]
                    token = (words[0], words[-1])
                    if token in known:
                        clash.add(token)
                    known[token] = (option_id, person_id, option_value)
                peers = {}
                for ballot_name, person_id in conn.execute(
                    """select ca.ballot_name, ca.person_id from candidacy ca
                       join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
                       join election e on e.id = c.election_id
                       where extract(year from e.date_from) = %s and c.district_area_id is not distinct from %s
                         and c.office_id is not distinct from %s and c.body_id is not distinct from %s
                         and c.id <> %s""",
                    [day.year, area, office, body_id, contest],
                ).fetchall():
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", slug(ballot_name).replace("-", " ")).split()
                        if w not in SUFFIXES
                    ]
                    token = (words[0], words[-1])
                    if peers.setdefault(token, person_id) != person_id:
                        peers[token] = None

                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                for prior in conn.execute(
                    """select id, election_id, contest_id, stage, kind, source_id, completeness from result_set
                       where contest_id = %s and is_default and id <> %s""",
                    [contest, result_set],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness"),
                                prior,
                                strict=True,
                            )
                        ),
                        is_default=False,
                        status="superseded",
                    )
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
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE + inconsistent)

                option_ids: dict[str, UUID] = {}
                for rank, candidate in enumerate(totals["candidate"]):
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", slug(candidate).replace("-", " ")).split()
                        if w not in SUFFIXES
                    ]
                    token = (words[0], words[-1]) if words else (candidate,)
                    match = known.get(token) if token not in clash else None
                    if match is not None:
                        option_ids[candidate] = match[0]
                        root_key = match[2]
                        if root_key is None:
                            raise ValueError(f"{contest_key}: option for {candidate} has no key")
                    else:
                        root_key = f"{contest_key}:{slug(candidate)}"
                        option_ids[candidate] = cs.key("us.option", root_key)
                        cs.add(
                            "contest_option",
                            id=option_ids[candidate],
                            contest_id=contest,
                            kind="candidate",
                            party_id=party,
                            ballot_label=candidate,
                        )
                        if candidate in PERSONS:
                            person = cs.key(*PERSONS[candidate])
                            if person in cs.minted:
                                raise ValueError(f"{candidate} must already exist")
                        elif (peer := peers.get(token)) is not None:
                            person = peer
                        else:
                            person = cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(candidate)}")
                            if person in cs.minted:
                                cs.add("person", id=person)
                                name(person, candidate)
                        head = cs.key("us.candidacy", f"{root_key}:head")
                        cs.add(
                            "candidacy",
                            id=head,
                            person_id=person,
                            option_id=option_ids[candidate],
                            role="head",
                            ballot_name=candidate,
                            is_independent=False,
                        )
                        if party is not None:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                        for line in (
                            rows.filter(pl.col("unit").str.starts_with(f"{candidate} ("))["unit"].unique().sort()
                        ):
                            line_party = parties[line.removeprefix(f"{candidate} (").removesuffix(")")]
                            if line_party is not None:
                                cs.add("candidacy_nomination", candidacy_id=head, party_id=line_party, kind="nominated")
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option_ids[candidate],
                        kind=("advanced" if kind == "primary" else "elected") if rank < magnitude else "lost",
                    )

                ballots = {}
                for unit_text in rows["unit"].unique().sort().to_list():
                    if unit_text in COUNTERS:
                        continue
                    if unit_text == SCATTERED:
                        ballots[unit_text] = cs.key("us.option", f"{contest_key}:write_in:scattered")
                        cs.add(
                            "contest_option",
                            id=ballots[unit_text],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label=SCATTERED,
                        )
                        continue
                    candidate = re.sub(r" \([^()]+\)$", "", unit_text)
                    line_party = re.search(r"\(([^()]+)\)$", unit_text)
                    if line_party is None:
                        ballots[unit_text] = option_ids[candidate]
                        continue
                    root = option_ids[candidate]
                    root_key = conn.execute(
                        "select value from external_id where entity_id = %s and scheme = 'us.option'", [root]
                    ).fetchone()
                    root_key = root_key[0] if root_key else f"{contest_key}:{slug(candidate)}"
                    ballots[unit_text] = cs.key("us.option", f"{root_key}:{slug(line_party[1])}")
                    cs.add(
                        "contest_option",
                        id=ballots[unit_text],
                        contest_id=contest,
                        parent_option_id=root,
                        kind="ticket",
                        party_id=parties[line_party[1]],
                        ballot_label=unit_text,
                    )
                counted = rows.filter(pl.col("combined").is_null()).with_columns(
                    unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()})
                )
                votes = counted.filter(~pl.col("unit").is_in(COUNTERS))
                cast = counted.filter(pl.col("unit").is_in(COUNTERS), ~pl.col("unit").is_in(INAPPLICABLE))
                cs.frame(
                    "result_value",
                    pl.concat(
                        [
                            votes.select(
                                result_set_id=pl.lit(str(result_set)),
                                unit_id="unit_id",
                                option_id=pl.col("unit").replace_strict({k: str(v) for k, v in ballots.items()}),
                                channel=pl.lit(0, pl.Int16),
                                measure=pl.lit(1, pl.Int16),
                                count_round=pl.lit(0, pl.Int16),
                                flags=pl.lit(0, pl.Int16),
                                value="votes",
                            ),
                            *(
                                frame_.group_by("unit_id")
                                .agg(pl.col("votes").sum())
                                .select(
                                    result_set_id=pl.lit(str(result_set)),
                                    unit_id="unit_id",
                                    option_id=pl.lit(None, pl.String),
                                    channel=pl.lit(0, pl.Int16),
                                    measure=pl.lit(measure, pl.Int16),
                                    count_round=pl.lit(0, pl.Int16),
                                    flags=pl.lit(0, pl.Int16),
                                    value="votes",
                                )
                                for measure, frame_ in ((3, cast), *(((4, votes),) if magnitude == 1 else ()))
                            ),
                        ]
                    ),
                )
        print(f"{len(taken)} county measures taken over: {taken}")
        return cs.write()
