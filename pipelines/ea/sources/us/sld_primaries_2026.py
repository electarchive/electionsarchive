import json
import re
import time
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import (
    BASE,
    EV,
    FIPS,
    IN_URL,
    RUNOFF_SYSTEMS,
    THRESHOLDS,
    elstats,
    plain,
    town_counties,
    url,
)
from ea.store import DATA, connect

SOURCE = "us.sld_primaries_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ROUNDS = [
    (
        "MI",
        date(2026, 8, 4),
        "Michigan Board of State Canvassers",
        "August 4, 2026 primary election official results by county",
        "https://mielections.us/election/results/2026PRI_CENR.html",
        "https://mielections.us/election/results/2026PRI_CENR.html#2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt",
    ),
    (
        "NV",
        date(2026, 6, 9),
        "Nevada Secretary of State",
        "June 9, 2026 primary election results by county",
        "https://silverstateelection.nv.gov/",
        url("nv", "nv-2026-primary-county-results.xml"),
    ),
    (
        "SC",
        date(2026, 6, 9),
        "South Carolina State Election Commission",
        "June 9, 2026 statewide primaries: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-09/detailxml.zip"),
    ),
    (
        "SC",
        date(2026, 6, 23),
        "South Carolina State Election Commission",
        "June 23, 2026 primary runoffs: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-23/detailxml.zip"),
    ),
    (
        "UT",
        date(2026, 6, 23),
        "Utah Lieutenant Governor's Office",
        "June 23, 2026 primary election official results",
        "https://electionresults.utah.gov/",
        f"{EV['ut'][0]}ballot-items",
    ),
    (
        "MN",
        date(2026, 8, 11),
        "Minnesota Secretary of State",
        "August 11, 2026 state primary official results by precinct",
        "https://electionresults.sos.mn.gov/",
        "https://electionresultsfiles.sos.mn.gov/20260811/allracesbyprecinct.txt",
    ),
    (
        "RI",
        date(2026, 9, 9),
        "Rhode Island Board of Elections",
        "September 9, 2026 statewide primary official results",
        "https://electionresults.ri.gov/",
        f"{EV['ri'][0]}ballot-items",
    ),
]
OPENELECTIONS = {
    "SD": ("South Dakota", "sd", date(2026, 6, 2), "20260602__sd__primary__county.csv"),
    "ND": ("North Dakota", "nd", date(2026, 6, 9), "20260609__nd__primary__county.csv"),
    "OR": ("Oregon", "or", date(2026, 5, 19), "20260519__or__primary__county.csv"),
    "KY": ("Kentucky", "ky", date(2026, 5, 19), "20260519__ky__primary__county.csv"),
    "CO": ("Colorado", "co", date(2026, 6, 30), "20260630__co__primary__county.csv"),
    "AZ": ("Arizona", "az", date(2026, 7, 21), "20260721__az__primary__county.csv"),
    "WY": ("Wyoming", "wy", date(2026, 8, 18), "20260818__wy__primary__county.csv"),
    "TX": ("Texas", "tx", date(2026, 3, 3), "20260303__tx__primary__county.csv"),
    "PA": ("Pennsylvania", "pa", date(2026, 5, 19), "20260519__pa__primary__county.csv"),
    "MO": ("Missouri", "mo", date(2026, 8, 4), "20260804__mo__primary__county.csv"),
    "TN": ("Tennessee", "tn", date(2026, 8, 6), "20260801__tn__primary__county.csv"),
}
ROUNDS += [
    (
        "WV",
        date(2026, 5, 12),
        "West Virginia Secretary of State",
        "May 12, 2026 primary election results by county",
        "https://sos.wv.gov/elections/election-data/historical-election-results-and-turnout",
        url("wv", "detailxml"),
    ),
    (
        "WI",
        date(2026, 8, 11),
        "Wisconsin Elections Commission",
        "August 11, 2026 partisan primary: county by county report of the canvass",
        "https://elections.wi.gov/election-result/2026-partisan-primary",
        url("wi", "County by County Report_Partisan Primary 2026_All State Contests.xlsx"),
    ),
    (
        "GA",
        date(2026, 5, 19),
        "Georgia Secretary of State",
        "May 19, 2026 general primary official results by county",
        "https://results.sos.ga.gov/",
        f"{EV['ga'][0]}ballot-items",
    ),
    (
        "GA",
        date(2026, 6, 16),
        "Georgia Secretary of State",
        "June 16, 2026 general primary runoff official results by county",
        "https://results.sos.ga.gov/",
        f"{EV['ga2'][0]}ballot-items",
    ),
    *(
        (
            "AL",
            date(2026, 5, 19) if file == "2026_Primary_Election.zip" else date(2026, 6, 16),
            "Alabama Secretary of State",
            f"{'May 19, 2026 primary' if file == '2026_Primary_Election.zip' else 'June 16, 2026 primary runoff'} election results by precinct, county files",
            "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            url("alx", f"2026-07/{file}"),
        )
        for file in ("2026_Primary_Election.zip", "2026_PRIMARY_RUNOFF_ELECTION.zip")
    ),
    (
        "ID",
        date(2026, 5, 19),
        "Idaho Secretary of State",
        "May 19, 2026 primary election official results by county",
        "https://results.voteidaho.gov/",
        f"{EV['id'][0]}ballot-items",
    ),
    (
        "IN",
        date(2026, 5, 5),
        "Indiana Secretary of State",
        "May 5, 2026 primary election results by county",
        "https://enr.indianavoters.in.gov",
        IN_URL.format("OffCatC_1040_B.json"),
    ),
    (
        "IA",
        date(2026, 6, 2),
        "Iowa Secretary of State",
        "June 2, 2026 primary election official canvass by county",
        "https://sos.iowa.gov/elections/results/",
        url("ia", "detailxlsx.zip"),
    ),
    (
        "NC",
        date(2026, 3, 3),
        "North Carolina State Board of Elections",
        "March 3, 2026 primary election results by precinct",
        "https://www.ncsbe.gov/results-data",
        url("nc", "results_pct_20260303.zip"),
    ),
    *(
        (
            state,
            day,
            "OpenElections",
            f"{state_name} 2026 primary results by county",
            f"https://github.com/openelections/openelections-data-{code}",
            BASE.format(code, file),
        )
        for state, (state_name, code, day, file) in OPENELECTIONS.items()
    ),
]
LEGISLATIVE = re.compile(
    r"(?P<party>REP|DEM) (?:State (?P<chamber>Senate|House)|(?P<body>Senator|Representative) in General Assembly) District (?P<district>\d+)"
)
ITEMS = {
    "UT": LEGISLATIVE,
    "RI": LEGISLATIVE,
    "GA": re.compile(r"State (?P<chamber>Senate|House) - District (?P<district>\d+) - (?P<party>Rep|Dem)"),
    "ID": re.compile(
        r"State (?P<body>Senator|Representative) District (?P<district>\d+)(?: Seat (?P<seat>[AB]))? - (?P<party>Republican|Democratic)"
    ),
}
STATEWIDE = {
    "Lieutenant Governor": ("lieutenant-governor", "Nevada Lieutenant Governor"),
    "Attorney General": ("attorney-general", "Nevada Attorney General"),
    "Secretary Of State": ("secretary-of-state", "Nevada Secretary of State"),
    "State Treasurer": ("treasurer", "Nevada Treasurer"),
    "State Controller": ("controller", "Nevada Controller"),
}
PARTIES = {
    "dfl": ("dp", "MN:minnesota-democratic-farmer-labor-party", "Democratic–Farmer–Labor"),
    "democratic": ("dp", "democratic-party", "Democratic"),
    "republican": ("rp", "republican-party", "Republican"),
}


def fetch() -> None:
    for state, *_, address in ROUNDS:
        if state == "IN":
            for file in ("OffCatC_1018_B.json", "OffCatC_1040_B.json"):
                raw.fetch(SOURCE, IN_URL.format(file), suffix=".json")
            continue
        if state in (*ITEMS, "MN"):
            artifact = raw.fetch(SOURCE, address, suffix=".json" if state != "MN" else ".txt")
            if state != "MN":
                for item in json.loads(artifact.path.read_bytes())["data"]:
                    if ITEMS[state].fullmatch(item["name"][0]["text"]):
                        for attempt in range(4):
                            try:
                                raw.fetch(SOURCE, f"{address}/{item['id']}", suffix=".json")
                                break
                            except httpx.HTTPStatusError as error:
                                if error.response.status_code != 404:
                                    raise
                                time.sleep(2 * (attempt + 1))
                        else:
                            print("no detail for", state, item["name"][0]["text"])
            continue
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            held = raw.cached("us.oe_primaries_2026", address)
            raw.store(
                SOURCE,
                address,
                held.path.read_bytes(),
                suffix=held.path.suffix,
                retrieved_at=held.retrieved_at,
                media_type=held.media_type,
            )


def parse() -> None:
    frames = []
    for state, day, *_, address in ROUNDS:
        path = raw.cached(SOURCE, address).path
        if state == "MI":
            frames.append(
                pl.read_csv(
                    path,
                    separator="\t",
                    skip_rows=1,
                    infer_schema=False,
                    quote_char=None,
                    truncate_ragged_lines=True,
                )
                .filter(pl.col("OfficeCode(text)").is_in(["9", "10"]))
                .select(
                    state=pl.lit(state),
                    day=pl.lit(day),
                    party=pl.col("PartyDescription").str.to_lowercase(),
                    chamber=pl.when(pl.col("OfficeCode(text)") == "9")
                    .then(pl.lit("senate"))
                    .otherwise(pl.lit("house")),
                    district=pl.col("DistrictCode(Text)").cast(pl.Int32).cast(pl.String),
                    candidate=pl.concat_str(
                        "CandidateFirstName",
                        "CandidateMiddleName",
                        "CandidateLastName",
                        separator=" ",
                        ignore_nulls=True,
                    ).str.replace_all(r"\s+", " "),
                    write_in=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("").is_in(["W", "Y"]),
                    county=pl.col("CountyName"),
                    votes=pl.col("CandidateVotes").str.replace_all(",", "").cast(pl.Int64),
                )
            )
            continue
        rows = []
        if state in OPENELECTIONS:
            for county, office, district, party, candidate, votes, *_ in pl.read_csv(
                path, infer_schema=False
            ).iter_rows():
                chamber = {"State House": "house", "State Representative": "house"}.get(office) or (
                    "senate" if office in ("State Senate", "State Senator") else None
                )
                party = {"REP": "republican", "R": "republican", "DEM": "democratic", "D": "democratic"}.get(
                    party, (party or "").lower()
                )
                if (
                    chamber is None
                    or party not in PARTIES
                    or candidate is None
                    or re.search(r"(?i)write-?ins?$|^(over|under) votes$|withdrawn", candidate)
                ):
                    continue
                rows.append(
                    (
                        chamber,
                        re.sub(r"^(\d+)([AB])$", lambda match: f"{match[1]}-{match[2].lower()}", district),
                        party,
                        candidate,
                        county,
                        int(votes or 0),
                    )
                )
        if state == "MN":
            totals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
            for line in path.read_text(encoding="latin-1").splitlines():
                cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
                found = (
                    re.fullmatch(r"State (Senator|Representative) District (\d+)([AB]?)", cells[4])
                    if len(cells) > 13
                    else None
                )
                if found is None or cells[10] not in ("DFL", "R"):
                    continue
                totals[
                    (
                        "senate" if found[1] == "Senator" else "house",
                        "-".join(filter(None, (found[2], found[3].lower()))),
                        "dfl" if cells[10] == "DFL" else "republican",
                        re.sub(r"\s+", " ", cells[7]),
                        f"27{2 * int(cells[1]) - 1:03}",
                    )
                ] += int(cells[13] or 0)
            rows = [(*key, votes) for key, votes in totals.items()]
        if state in ITEMS:
            for item in json.loads(path.read_bytes())["data"]:
                found = ITEMS[state].fullmatch(item["name"][0]["text"])
                if found is None:
                    continue
                try:
                    breakdown = json.loads(raw.cached(SOURCE, f"{address}/{item['id']}").path.read_bytes())[
                        "breakdownResults"
                    ]
                except LookupError:
                    if item["reportingStatus"]["totalUnits"] != 1 or item["crossCounties"]:
                        raise
                    breakdown = [{"locality": {"name": [{"text": ""}]}, **item["summaryResults"]}]
                for county in breakdown:
                    locality = county["locality"]["name"][0]["text"]
                    if state == "RI":
                        town = plain(re.sub(elstats.SUFFIXES, "", locality.lower()))
                        if town not in town_counties("RI"):
                            continue
                        locality = town_counties("RI")[town]
                    rows.extend(
                        (
                            "senate" if "Senat" in (found.groupdict().get("chamber") or found["body"]) else "house",
                            found["district"]
                            + (f":{'AB'.index(found['seat']) + 1}" if found.groupdict().get("seat") else ""),
                            "republican" if found["party"].upper().startswith("REP") else "democratic",
                            re.sub(
                                r'"(\w{1,3})"',
                                lambda match: f'"{match[1].upper()}"',
                                re.sub(r"\bMc(\w)", lambda match: f"Mc{match[1].upper()}", text.title()),
                            )
                            if text.isupper()
                            else text,
                            locality.removesuffix(" County"),
                            option["voteCount"],
                        )
                        for option in county["ballotOptions"]
                        if not option["isWriteIn"]
                        for text in [re.sub(r"\s*(\(I\)|\*)$", "", option["name"][0]["text"]).strip()]
                    )
        if state == "NV":
            for county in ElementTree.fromstring(path.read_text()).iter("County"):
                for race in county.iter("Race"):
                    found = re.fullmatch(r"State (Senate|Assembly), District\s+(\d+) \((\w+)\)", race.get("RaceTitle"))
                    office = re.fullmatch(r"(.+) \((Democratic|Republican)\)", race.get("RaceTitle"))
                    if found is None and (office is None or office[1] not in STATEWIDE):
                        continue
                    for candidate in race.iter("Candidate"):
                        last, _, first = candidate.get("CandidateName").partition(", ")
                        rows.append(
                            (
                                found[1].lower() if found else STATEWIDE[office[1]][0],
                                found[2] if found else "",
                                (found or office)[3 if found else 2].lower(),
                                f"{first} {last}".strip(),
                                county.get("Name"),
                                int(candidate.get("Votes")),
                            )
                        )
        if state == "WI":
            for sheet in pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0).values():
                if sheet.height < 5 or sheet.width < 4:
                    continue
                found = re.fullmatch(
                    r"(STATE SENATOR|REPRESENTATIVE TO THE ASSEMBLY) DISTRICT (\d+) - (Republican|Democratic)",
                    sheet.row(2)[0] or "",
                )
                if found is None:
                    continue
                names = sheet.row(4)
                for line in sheet.rows()[5:]:
                    if line[0] is None or line[0].startswith("Office Totals"):
                        break
                    rows.extend(
                        (
                            "senate" if found[1] == "STATE SENATOR" else "assembly",
                            found[2],
                            found[3].lower(),
                            names[column],
                            line[0].strip().title(),
                            int(line[column] or 0),
                        )
                        for column in range(3, sheet.width)
                        if names[column] and names[column] != "SCATTERING"
                    )
        if state == "IN":
            for file in ("OffCatC_1018_B.json", "OffCatC_1040_B.json"):
                category = json.loads(raw.cached(SOURCE, IN_URL.format(file)).path.read_bytes())["Root"][
                    "OfficeCategory"
                ]
                for region in category["Regions"]["Region"]:
                    races = region["Races"]["Race"]
                    for race in races if isinstance(races, list) else [races]:
                        found = re.fullmatch(r"State (Senator|Representative), District (\d+)", race["OFFICE_TITLE"])
                        candidates = race["Candidates"]["Candidate"]
                        rows.extend(
                            (
                                "senate" if found[1] == "Senator" else "house",
                                str(int(found[2])),
                                {"R": "republican", "D": "democratic"}[candidate["PARTY_ABBREV"]],
                                candidate["NAME_ON_BALLOT"],
                                race["Jurisdiction"]["JURISDICTION_NAME"],
                                int(candidate["TOTAL_VOTES"]),
                            )
                            for candidate in (candidates if isinstance(candidates, list) else [candidates])
                            if candidate["PARTY_ABBREV"] in ("R", "D")
                        )
        if state == "IA":
            with zipfile.ZipFile(path) as archive:
                book = archive.read("detail.xlsx")
            for sheet in pl.read_excel(book, sheet_id=0, has_header=False, infer_schema_length=0).values():
                found = re.fullmatch(
                    r"State (Senator|Representative) District (\d+) - (Rep|Dem)\. \(\s*\d+\)", sheet.row(0)[0] or ""
                )
                if found is None:
                    continue
                names, headers = sheet.row(1), sheet.row(2)
                columns = [
                    (names[start], next(c for c in range(start, sheet.width) if headers[c] == "Total Votes"))
                    for start in range(sheet.width)
                    if names[start] and names[start] != "Write-in"
                ]
                for line in sheet.rows()[3:]:
                    if line[0] is None or line[0].startswith("Total"):
                        break
                    rows.extend(
                        (
                            "senate" if found[1] == "Senator" else "house",
                            found[2],
                            {"Rep": "republican", "Dem": "democratic"}[found[3]],
                            name,
                            line[0],
                            int(line[column] or 0),
                        )
                        for name, column in columns
                    )
        if state == "AL":
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    sheet = pl.read_excel(archive.read(member), infer_schema_length=0, engine="calamine")
                    for line in sheet.rows():
                        found = re.fullmatch(
                            r"STATE (SENATOR|REPRESENTATIVE), DISTRICT (\d+) \((REP|DEM)\)", (line[0] or "").strip()
                        )
                        candidate = (line[2] or "").strip()
                        if found is None or re.fullmatch(r"(?i)(over|under) votes|write-in", candidate):
                            continue
                        rows.append(
                            (
                                "senate" if found[1] == "SENATOR" else "house",
                                found[2],
                                {"REP": "republican", "DEM": "democratic"}[found[3]],
                                candidate,
                                member.rsplit("-", 1)[1].removesuffix(".xls"),
                                sum(int(value or 0) for value in line[3:]),
                            )
                        )
        if state == "NC":
            totals = defaultdict(int)
            with zipfile.ZipFile(path) as archive:
                text = archive.read("results_pct_20260303.txt").decode("latin-1")
            for line in text.splitlines()[1:]:
                cells = line.split("\t")
                found = re.fullmatch(
                    r"NC (HOUSE OF REPRESENTATIVES|STATE SENATE) DISTRICT (\d+) \((REP|DEM)\)", cells[5]
                )
                if found is None or cells[6].lower().startswith("write-in"):
                    continue
                totals[
                    (
                        "senate" if found[1] == "STATE SENATE" else "house",
                        str(int(found[2])),
                        {"REP": "republican", "DEM": "democratic"}[found[3]],
                        cells[6],
                        cells[0],
                    )
                ] += int(cells[13])
            rows = [(*key, votes) for key, votes in totals.items()]
        if state in ("SC", "WV"):
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            for contest in root.iter("Contest"):
                found = re.fullmatch(
                    r"State (Senate|House of Representatives), District (\d+) - (REP|DEM)"
                    if state == "SC"
                    else r"(STATE SENATOR|HOUSE OF DELEGATES), (\d+)\w\w (?:Senatorial )?District - (REP|DEM)",
                    re.sub(r"\s+", " ", contest.get("text")),
                )
                if found is None:
                    continue
                for choice in contest.iter("Choice"):
                    if "*Decertified" in choice.get("text"):
                        continue
                    totals: dict[str, int] = defaultdict(int)
                    for vote_type in choice.iter("VoteType"):
                        for county in vote_type.iter("County"):
                            totals[county.get("name")] += int(county.get("votes"))
                    rows.extend(
                        (
                            {
                                "Senate": "senate",
                                "STATE SENATOR": "senate",
                                "HOUSE OF DELEGATES": "house-of-delegates",
                            }.get(found[1], "house"),
                            found[2] + (":2" if found[1] == "STATE SENATOR" else ""),
                            {"REP": "republican", "DEM": "democratic"}[found[3]],
                            choice.get("text"),
                            county,
                            votes,
                        )
                        for county, votes in totals.items()
                    )
        frames.append(
            pl.DataFrame(
                rows,
                schema=[
                    ("chamber", pl.String),
                    ("district", pl.String),
                    ("party", pl.String),
                    ("candidate", pl.String),
                    ("county", pl.String),
                    ("votes", pl.Int64),
                ],
                orient="row",
            ).select(
                state=pl.lit(state),
                day=pl.lit(day),
                party="party",
                chamber="chamber",
                district="district",
                candidate=pl.col("candidate").str.replace(r"(?i)\s*\(write-in\)$", ""),
                write_in=pl.col("candidate").str.contains(r"(?i)\(write-in\)$"),
                county="county",
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    contest = ["state", "day", "party", "chamber", "district"]
    frame = pl.concat(frames).with_columns(
        pl.col("candidate").str.replace_all("`", "'"),
        spelling=pl.col("candidate").str.to_lowercase().str.replace_all("`", "'"),
    )
    frame.join(
        frame.group_by(*contest, "spelling").agg(
            canonical=pl.col("candidate")
            .sort_by(pl.col("candidate").str.count_matches("[A-Z]"), descending=True)
            .first()
        ),
        on=[*contest, "spelling"],
    ).group_by(*contest, pl.col("canonical").alias("candidate"), "write_in", "county", maintain_order=True).agg(
        pl.col("votes").sum()
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 state legislative primaries and primary runoffs by county: Alabama, Georgia, Idaho, Indiana, Iowa, Michigan, Minnesota, Nevada (with its statewide offices), North Carolina, Rhode Island, South Carolina, Utah, West Virginia, and Wisconsin from the states' official results; Arizona, Colorado, Kentucky, Missouri, North Dakota, Oregon, Pennsylvania, South Dakota, Tennessee, Texas, and Wyoming from OpenElections",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        counts = {"contests": 0, "candidates": 0, "matched": 0, "skipped": 0}
        for state, day, publisher, title, page, address in ROUNDS:
            first = min(when for other, when, *_ in ROUNDS if other == state)
            runoff = day != first
            system = cs.key("us.electoral_system", "fptp" if state == "SD" else RUNOFF_SYSTEMS.get(state, "fptp"))
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=page,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, address), source)
            if state == "IN":
                cs.artifact(raw.cached(SOURCE, IN_URL.format("OffCatC_1018_B.json")), source)
            election = cs.key("us.election", f"{day}:{state}:{'runoff' if runoff else 'primary'}")
            if election in cs.minted:
                raise ValueError(f"the {day} {state} primary election must exist")
            templates = {
                value: row
                for value, *row in conn.execute(
                    """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.magnitude, co.term, n.text
                       from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                       left join name n on n.entity_id = co.body_id and n.is_preferred
                       where x.value ~ %s""",
                    [
                        f"^2026-11-03:{state}:((house|senate|assembly|house-of-delegates):[0-9]+(-[ab]|:[0-9])?:g|[a-z-]+)$"
                    ],
                ).fetchall()
            }
            counties = {}
            for unit, code, text in conn.execute(
                """select u.id, u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{FIPS[state]}___"],
            ).fetchall():
                counties[code] = counties[re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))] = unit
            if state == "MO":
                counties["kansascity"] = conn.execute(
                    "select id from reporting_unit where scheme_code = 'us.place' and code = '2938000'"
                ).fetchone()[0]
                counties["stlouiscity"], counties["stlouis"] = counties["29510"], counties["29189"]
            pool: dict[tuple[str, str], set[object]] = defaultdict(set)
            for person, text in conn.execute(
                """select distinct cy.person_id, n.text from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest co on co.id = o.contest_id
                   join name b on b.entity_id = co.body_id and b.is_preferred and b.text !~ 'United States'
                   join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where x.value ~ %s""",
                [
                    f"^20[0-2][0-9]-[0-9-]+:{state}:(house|senate|assembly|house-of-delegates|{'|'.join(key for key, _ in STATEWIDE.values())})(:|$)"
                ],
            ).fetchall():
                words = [
                    w for w in re.sub(r"[^a-z ]", "", text.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
                ]
                if len(words) > 1:
                    pool[(words[0], words[-1])].add(person)
            grouped = (
                frame.filter((pl.col("state") == state) & (pl.col("day") == day))
                .group_by("party", "chamber", "district", "candidate", "write_in")
                .agg(pl.col("county"), pl.col("votes"))
            )
            for (party, chamber, district), contest_rows in sorted(
                grouped.group_by("party", "chamber", "district"), key=lambda entry: entry[0]
            ):
                scope, party_key, label = PARTIES[party]
                template = templates.get(f"2026-11-03:{state}:{chamber}" + (f":{district}:g" if district else ""))
                if template is None:
                    print("no 2026 general contest for", state, chamber, district)
                    counts["skipped"] += 1
                    continue
                kind, office, body, area, magnitude, term, chamber_name = template
                contest_key = (
                    f"{day}:{state}:{chamber}:{district}:{scope}" if district else f"{day}:{state}:{chamber}:{party}"
                )
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term=term,
                    round_number=2 if runoff else 1,
                    previous_round_id=cs.key("us.contest", contest_key.replace(str(day), str(first), 1))
                    if runoff
                    else None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    party_scope_id=cs.key("us.party", party_key),
                )
                stage = "primary runoff" if runoff else "primary"
                if district:
                    name(contest, f"2026 {chamber_name} {label} {stage}, District {district.replace(':', ', Seat ')}")
                else:
                    name(
                        contest,
                        f"2026 {next(title for key, title in STATEWIDE.values() if key == chamber)} {label} {stage}",
                    )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{stage.replace(' ', '-')}/"
                    + (f"state-{chamber}-{district.replace(':', '-')}-{party}" if district else f"{chamber}-{party}"),
                    entity_id=contest,
                )
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
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(
                        f"Results by county from OpenElections' 2026 primary file for {OPENELECTIONS[state][0]}, a community compilation of the state's county results."
                        if state in OPENELECTIONS
                        else f"Results by county from the {publisher}'s official results."
                    )
                    + (
                        " Declared write-in candidates are listed by name; the candidate with the most votes is shown as the nominee."
                        if state == "MI"
                        else ""
                    ),
                )
                totals = []
                if "" in contest_rows["county"].explode().to_list():
                    (counties[""],) = {
                        unit
                        for (unit,) in conn.execute(
                            """select distinct u.id from external_id x
                               join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
                               join result_value v on v.result_set_k = rs.k
                               join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.county'
                               where x.scheme = 'us.contest' and x.value ~ %s""",
                            [f"^20(22|24)-11-[0-9]+:{state}:{chamber}:{district}:g$"],
                        ).fetchall()
                    }
                for candidate, write_in, by_county, votes in contest_rows.select(
                    "candidate", "write_in", "county", "votes"
                ).iter_rows():
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", candidate.lower()).split()
                        if w not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    found = pool.get((words[0], words[-1]), set())
                    if len(found) == 1:
                        person = next(iter(found))
                        counts["matched"] += 1
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, candidate)
                            pool[(words[0], words[-1])].add(person)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key),
                        ballot_label=f"{candidate} (write-in)" if write_in else candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    for county, value in zip(by_county, votes, strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties.get(county)
                            or counties[re.sub(r"[^a-z]", "", county.lower().replace("saint ", "st "))],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                    totals.append((sum(votes), option))
                    counts["candidates"] += 1
                totals.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in totals)
                if (
                    not runoff
                    and state in THRESHOLDS
                    and state != "SD"
                    and len(totals) > 1
                    and totals[0][0] * 100 <= THRESHOLDS[state] * everyone
                ):
                    if len(totals) == 2 or totals[1][0] > totals[2][0]:
                        for _, option in totals[:2]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                elif magnitude > 1:
                    seated = [entry for entry in totals[:magnitude] if entry[0] > 0]
                    if len(totals) <= magnitude or totals[magnitude - 1][0] > totals[magnitude][0]:
                        for _, option in seated:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=None)
                elif totals[0][0] > 0 and (len(totals) == 1 or totals[0][0] > totals[1][0]):
                    cs.add("outcome", result_set_id=result_set, option_id=totals[0][1], kind="won", seats=None)
                counts["contests"] += 1
        print(counts)
        return cs.write()
