import json
import re
import subprocess
from difflib import SequenceMatcher
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
HEADER = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9 .&-]*?) - (\d\d/\d\d/\d{4})\s*$", re.M)
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


PARTY_POSITION = re.compile(
    r"^(MALE |FEMALE )?(ALTERNATE )?(COUNTY COMMITTEE|STATE COMMITTEE|DISTRICT LEADER|DELEGATE TO (?:THE )?(?:JUDICIAL|NATIONAL) CONVENTION) \((.*?)\)"
)
STATE_SEATS = (
    (re.compile(r"^PRESIDENT/VICE PRESIDENT"), "president"),
    (re.compile(r"^GOVERNOR"), "governor"),
    (re.compile(r"^LIEUTENANT GOVERNOR"), "lieutenant-governor"),
    (re.compile(r"^ATTORNEY GENERAL"), "attorney-general"),
    (re.compile(r"^STATE COMPTROLLER"), "state-comptroller"),
    (re.compile(r"^UNITED STATES SENATOR"), "us-senator"),
)
NUMBERED_SEATS = (
    (re.compile(r"^REPRESENTATIVE IN CONGRESS \((\d+)"), "house"),
    (re.compile(r"^STATE SENAT(?:OR|E) \((\d+)"), "state-senate"),
    (re.compile(r"^MEMBER OF THE ASSEMBLY \((\d+)"), "assembly"),
)
STATE_TARGETS = {
    "president": ("electoral_college", "us.state"),
    "governor": ("governor:NY", "us.state"),
    "lieutenant-governor": ("lieutenant-governor:NY", "us.state"),
    "attorney-general": ("attorney-general:NY", "us.state"),
    "state-comptroller": ("comptroller:NY", "us.state"),
    "us-senator": ("senate", "us.state"),
    "house": ("house", "us.cd"),
    "state-senate": ("NY:senate", "us.sldu"),
    "assembly": ("NY:assembly", "us.sldl"),
}
BODY_KEYS = {"electoral_college", "senate", "house", "NY:senate", "NY:assembly"}
NOTE_STATE = (
    " The Board's report covers only the city's part of a contest that reaches beyond it; the official total for "
    "the whole contest is the default set."
)


CHARTER_2005 = "https://www.nyc.gov/assets/charter/downloads/pdf/crc_ballot_questions.pdf"
CHARTER_2010 = "https://www.nyc.gov/assets/charter/downloads/pdf/final_ballot_questions_combined.pdf"
QUESTIONS = (
    (
        "2005-11-08",
        "Q3. ETHICS CODE",
        "ethics-code-for-city-administrative-judges",
        "Question 3: Ethics Code for City Administrative Judges",
        CHARTER_2005,
        "2005 Ballot Questions, New York City Charter Revision Commission, August 2, 2005",
        "Question 3: Ethics Code for City Administrative Judges. These changes to the City Charter, as proposed by the New York City Charter Revision Commission, would require the Mayor and the Chief Administrative Law Judge of the Office of Administrative Trials and Hearings to jointly issue rules establishing a code or codes of professional conduct for the administrative law judges and hearing officers in the City's administrative tribunals. Shall the proposed changes be adopted?",
    ),
    (
        "2005-11-08",
        "Q4 BALANCED BUDGET",
        "balanced-budget-and-other-city-fiscal-requirements",
        "Question 4: Balanced Budget and Other City Fiscal Requirements",
        CHARTER_2005,
        "2005 Ballot Questions, New York City Charter Revision Commission, August 2, 2005",
        "Question 4: Balanced Budget and Other City Fiscal Requirements. These changes to the City Charter, as proposed by the New York City Charter Revision Commission, would establish as Charter requirements the following fiscal mandates that, in general, now apply to the City through a State law enacted in response to the City's 1975 fiscal crisis. The changes would add these mandates to the City Charter so that they would continue to apply after the State law expires. The changes would: Require that the City annually prepare a budget balanced in accordance with generally accepted accounting principles (GAAP), and end each year not showing a deficit in accordance with those principles; Require that the Mayor annually prepare a four-year City financial plan, to be based on reasonable assumptions and modified on at least a quarterly basis, and that the plan provide for payment of the City's debts and a general reserve of at least $100 million to cover shortfalls; Impose additional conditions on the Charter's current restrictions on short-term debt (which may be issued by the City to fund a projected deficit or in anticipation of the receipt of funds from taxes, revenues, and bonds). These conditions generally limit the duration and amount of the short-term debt; and Impose additional conditions on the annual audit of the City's accounts that is currently required by the Charter. These conditions relate to application of generally accepted auditing standards and access by auditors to records so that the audit may be issued within four months after the close of the City fiscal year. Shall the proposed changes be adopted?",
    ),
    (
        "2010-11-02",
        "TERM LIMITS",
        "term-limits",
        "City Question 1: Term Limits",
        CHARTER_2010,
        "2010 Ballot Questions, New York City Charter Revision Commission, August 23, 2010",
        "City Question 1. Term Limits: The proposal would amend the City Charter to: Reduce from three to two the maximum number of consecutive full terms that can be served by elected city officials; and Make this change in term limits applicable only to those city officials who were first elected at or after the 2010 general election; and Prohibit the City Council from altering the term limits of elected city officials then serving in office. Shall this proposal be adopted?",
    ),
    (
        "2010-11-02",
        "ELECTIONS AND GOVERNMENT ADMINISTRATION",
        "elections-and-government-administration",
        "City Question 2: Elections and Government Administration",
        CHARTER_2010,
        "2010 Ballot Questions, New York City Charter Revision Commission, August 23, 2010",
        "City Question 2. Elections and Government Administration: The proposal would amend the City Charter to: Disclosure of Independent Campaign Spending: Require public disclosure of expenditures made by entities and individuals independent from candidates to influence the outcome of a city election or referendum; Ballot Access: Generally reduce the number of petition signatures needed by candidates for city elective office to appear on a ballot; Voter Assistance and Campaign Finance Board: Merge voter assistance functions, including a reconstituted Voter Assistance Advisory Committee, into the Campaign Finance Board, and change when Campaign Finance Board member terms begin; Conflicts of Interest Law: Require all public servants to receive conflicts of interest training, raise the maximum fine for a public servant who violates the City's conflicts of interest law, and allow the City to recover any benefits obtained from such violations; City Administrative Tribunals: Authorize the Mayor to direct the merger of administrative tribunals and adjudications into the Office of Administrative Trials and Hearings and permit the Department of Consumer Affairs to adjudicate all violations issued by that department; City Reporting Requirements and Advisory Bodies: Create a commission to review requirements for reports and advisory bodies and waive the requirements, subject to City Council review, where the commission finds they are not of continuing value; and Map for Facility Siting: Include in the City's facilities siting map those transportation and waste management facilities operated by or for governmental entities, or by private entities that provide comparable services. Shall this proposal be adopted?",
    ),
)
NOTE_QUESTION = (
    "Assembly district returns of a city charter question from the Board of Elections' certified Statement and Return "
    "reports. Ballots cast are the reports' total ballots. The borough reports' assembly district lines sum to each "
    "report's county total."
)


def num(text: str) -> int:
    return int(text.replace(",", ""))


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def read(path: Path) -> dict[str, object] | None:
    lines = pdf_text(path).splitlines()
    found = ELECTION.search("\n".join(lines[:25]))
    if found is None:
        found = HEADER.search("\n".join(lines[:25]))
        if found is None:
            return None
        kind = "Primary Runoff" if "run" in found[1].lower() else "Primary" if "primary" in found[1].lower() else "General" if "general" in found[1].lower() else "Special"
        found = (None, kind, found[2])
    else:
        found = (None, found[1].title(), found[2])
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
        "kind": found[1],
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
    matched = PARTY_POSITION.match(bare)
    if matched and party:
        pname = ((matched[1] or "") + (matched[2] or "") + matched[3]).title().replace(" To ", " to ")
        if matched[2] and "Judicial" in pname:
            pname = pname.replace("to Judicial", "to the Judicial")
        inner = matched[4]
        if (found := re.fullmatch(r"(\d+)/(\d+)", inner)) is not None:
            return {"seat": f"party|{pname}|{int(found[2]):02d}{int(found[1]):03d}", "party": party[1], "county": None, "state": None, "area": None, "part": f"ed-{int(found[1]):03d}", "ad": int(found[2])}
        if (found := re.fullmatch(r"(\d+)(?:ST|ND|RD|TH)? ASSEMBLY DISTRICT", inner)) or (found := re.fullmatch(r"(\d+)", inner)):
            return {"seat": f"party|{pname}|{int(found[1]):02d}", "party": party[1], "county": None, "state": None, "area": ("us.sldl", f"NY:{int(found[1])}"), "part": None, "ad": int(found[1])}
        if (found := re.fullmatch(r"(\d+)(?:ST|ND|RD|TH)? CONGRESSIONAL DISTRICT", inner)):
            return {"seat": f"party|{pname}|cd{int(found[1]):02d}", "party": party[1], "county": None, "state": None, "area": ("us.cd", f"36{int(found[1]):02d}"), "part": f"nat-{int(found[1])}", "ad": None}
        if (found := re.fullmatch(r"(?:DEMOCRATIC LEADERSHIP DISTRICT )?(\d+) ?(?:PART )?([A-Z])", inner)):
            return {"seat": f"party|{pname}|{int(found[1]):02d}{found[2].lower()}", "party": party[1], "county": None, "state": None, "area": ("us.sldl", f"NY:{int(found[1])}"), "part": f"dl-{found[2].lower()}", "ad": int(found[1])}
        if (found := re.fullmatch(r"(?:DEMOCRATIC LEADERSHIP DISTRICT )?(\d+)", inner)):
            return {"seat": f"party|{pname}|{int(found[1]):02d}", "party": party[1], "county": None, "state": None, "area": ("us.sldl", f"NY:{int(found[1])}"), "part": None, "ad": int(found[1])}
        return None
    for pattern, seat in STATE_SEATS:
        if pattern.match(bare):
            if seat == "us-senator":
                seat += "-unexpired" if "UNEXPIRED" in bare else "-full" if "FULL TERM" in bare else ""
            return {"seat": seat, "party": party[1] if party else None, "county": None, "state": 1}
    for pattern, seat in NUMBERED_SEATS:
        matched = pattern.match(bare)
        if matched:
            return {"seat": seat, "number": int(matched[1]), "party": party[1] if party else None, "county": None, "state": 1}
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


def emit(
    pipeline: str = SOURCE,
    parsed: Path = PARSED,
    version: str = VERSION,
    summary: str = "New York City's 2005-2013 city and county offices by assembly district from the Board of Elections' certified recaps",
    note: str = NOTE,
    strict: bool = False,
    questions: bool = True,
) -> Path:
    entries = pl.read_parquet(parsed / "entries.parquet")
    totals = pl.read_parquet(parsed / "totals.parquet")
    ballots = pl.read_parquet(parsed / "ballots.parquet")
    keys = []
    for office, county in entries.select("office", "county").unique().iter_rows():
        found = classify(office, county)
        if found is not None:
            keys.append((office, county, found))
    frames = []
    for office, county, found in keys:
        frames.append(
            entries.filter((pl.col("office") == office) & (pl.col("county") == county) & (pl.col("kind").is_in(["General", "Primary", "Primary Runoff", "Presidential Primary", "Special"])))
            .with_columns(
                seat=pl.lit(found["seat"], pl.String),
                number=pl.lit(found.get("number"), pl.Int64),
                party_scope=pl.lit(found["party"], pl.String),
                contest_county=pl.lit(found["county"], pl.String),
                size=pl.lit(int(next(iter(re.findall(r"(?:vote for|ELECTED) (\d+)", office, re.I)), 1)), pl.Int64),
                area_scheme=pl.lit((found.get("area") or (None, None))[0], pl.String),
                area_code=pl.lit((found.get("area") or (None, None))[1], pl.String),
                part_code=pl.lit(found.get("part"), pl.String),
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
            pipeline=pipeline,
            version=version,
            summary=summary,
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
        unit_cache: dict[tuple[str, str, str, str | None], UUID] = {}
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

        def unit_for(era: str, bid: str, county: str, part: str | None = None) -> UUID:
            if part is None and (era, bid) not in crossing:
                return cs.unit("us.sldl", f"NY:{int(bid)}")
            code = f"NY:{int(bid)}:{fips[county]}" + (f"/{part}" if part else "")
            if (key := (era, bid, county, part)) not in unit_cache:
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
        groups = frame.select("day", "kind", "seat", "number", "party_scope", "contest_county", "area_scheme", "area_code", "part_code").unique().sort("day", "seat")
        for day_text, kind, seat, number, scope, only_county, area_scheme, area_code, part_code in groups.iter_rows():
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
            target_seat = "us-senator" if seat.startswith("us-senator") else seat
            is_party = seat.startswith("party|")
            is_state = target_seat in STATE_TARGETS
            statewide = target_seat in ("president", "governor", "lieutenant-governor", "attorney-general", "state-comptroller", "us-senator")
            wide = seat in ("mayor", "public-advocate", "comptroller") or statewide
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
            if is_state:
                state_kind = "general" if kind == "General" else "primary" if kind in ("Primary", "Presidential Primary") else "special" if kind == "Special" else "runoff"
                election = None
                if state_kind == "special":
                    election = conn.execute(
                        "select e.id, s.path from election e left join slug s on s.entity_id = e.id "
                        "where e.polity_id = %s and e.date_from = %s and e.kind = 'special' limit 1",
                        [cs.polity("US-NY"), day],
                    ).fetchone()
                for value in ([] if state_kind == "special" else [f"{day}:NY"] if state_kind == "general" else [f"{day}:NY:primary"]):
                    election = conn.execute(
                        "select x.entity_id, s.path from external_id x left join slug s on s.entity_id = x.entity_id "
                        "where x.scheme = 'us.election' and x.value = %s limit 1",
                        [value],
                    ).fetchone()
                if election is None:
                    if state_kind == "general":
                        skipped.append(f"{label}: the state's election is not in the archive")
                        continue
                    election = (cs.key("us.election", f"{day}:NY:{state_kind}"), f"us/ny/{day}-{state_kind}")
                    if election[0] in cs.minted and election[0] not in added:
                        added.add(election[0])
                        cs.add(
                            "election",
                            id=election[0],
                            polity_id=cs.polity("US-NY"),
                            kind=state_kind,
                            date_from=day,
                            date_to=day,
                            date_precision="day",
                            status="held",
                        )
                        name(election[0], f"{day.year} New York {state_kind} election")
                        cs.add("slug", path=election[1], entity_id=election[0])
                election, election_path = election[0], election[1] or f"us/ny/{day}-{state_kind}"
            elif kind == "General":
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
                election_kind = "primary" if kind in ("Primary", "Presidential Primary") else "special" if kind == "Special" else "runoff"
                election = conn.execute(
                    "select id from election where polity_id = %s and date_from = %s and kind in ('primary', 'runoff', 'special')", [city, day]
                ).fetchone()
                if election is None:
                    election = cs.key("us.election", f"{day}:new-york-city:{election_kind}")
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
            if not is_state:
                election_path = conn.execute("select path from slug where entity_id = %s limit 1", [election]).fetchone()
                election_path = election_path[0] if election_path else f"us/ny/new-york-city/{day}-{'primary' if kind == 'Primary' else 'special' if kind == 'Special' else 'runoff'}"
            office = body_id = None
            area = None
            county_name = only_county
            nickname = BOROUGHS[county_name][1] if county_name else None
            if is_party:
                _, pname, pkey = seat.split("|")
                office = cs.key("us.office", f"NY:party:{slug(scope.title())}:{slug(pname)}")
                if office in cs.minted and office not in added:
                    added.add(office)
                    cs.add("office", id=office, polity_id=cs.polity("US-NY"), kind="other")
                    name(office, f"{scope.title()} Party {pname}")
                area = cs.area(area_scheme, area_code) if area_scheme else None
                path = f"party-{slug(pname)}-{pkey}"
                where = (
                    f"Assembly District {int(pkey[:2])}, Election District {int(pkey[2:])}"
                    if pname.endswith("County Committee")
                    else f"{int(pkey[2:])}th Congressional District"
                    if pkey.startswith("cd")
                    else f"Assembly District {int(pkey[:2])}" + (f", Part {pkey[2:].upper()}" if len(pkey) > 2 else "")
                )
                title = f"{pname}, {where}"
            elif is_state:
                key, scheme = STATE_TARGETS[target_seat]
                path = f"{seat}-{number}" if number is not None else seat
                title = {
                    "house": f"U.S. House, New York's {number} congressional district",
                    "state-senate": f"New York State Senate, District {number}",
                    "assembly": f"New York State Assembly, District {number}",
                }.get(seat, {"president": "President", "governor": "Governor", "lieutenant-governor": "Lieutenant Governor", "attorney-general": "Attorney General", "state-comptroller": "State Comptroller"}.get(seat, "U.S. Senator"))
                area = cs.area(scheme, "36" if scheme == "us.state" else f"36{number:02d}" if scheme == "us.cd" else f"NY:{number}")
                if key in BODY_KEYS:
                    body_id = cs.key("us.body", key)
                else:
                    office = cs.key("us.office", key)
            elif seat == "mayor":
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
            if (office is not None and office in cs.minted and office not in added) or (area is not None and area in cs.minted) or (body_id is not None and body_id in cs.minted):
                skipped.append(f"{label}: its office or district is not in the archive")
                continue
            found = [] if is_party else conn.execute(
                """select c.id from contest c join entity e on e.id = c.id and e.retired_by is null
                   where c.election_id = %s and c.office_id is not distinct from %s and c.body_id is not distinct from %s
                   and c.district_area_id is not distinct from %s and c.party_scope_id is not distinct from %s
                   and c.changeset_id not in (select id from changeset where pipeline = %s)""",
                [election, office, body_id, area, party, pipeline],
            ).fetchall()
            if len(found) > 1:
                skipped.append(f"{label}: several contests match")
                continue
            if not found and is_state:
                found = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s",
                    [f"{day}:NY:{path.replace('-', ':')}" + (f":{slug(scope)}" if scope else "")],
                ).fetchall()
            part = None
            if is_party:
                part = f"{part_code}/{era}" if part_code and part_code.startswith("nat-") else part_code
            elif seat in ("house", "state-senate"):
                part = f"{seat}-{number}/{era}"
            elif seat == "council":
                part = f"council-{number}/{'2003' if day_text < '2013-01-01' else '2013'}"
            elif seat == "civil-court-district":
                part = f"civil-district-{number}"
            magnitude = int(rows["size"][0])
            attach = bool(found)
            inside = False
            if is_state and area is not None:
                outside = conn.execute(
                    """select st_area(st_difference(g.geom, st_buffer(n.geom, 0.002))) / st_area(g.geom)
                       from area_geometry g, area_geometry n where g.area_id = %s and g.valid @> %s::date and n.area_id = %s""",
                    [area, day, place],
                ).fetchone()
                inside = outside is not None and outside[0] <= 0.01
            if attach:
                contest = found[0][0]
                contest_key = conn.execute(
                    "select value from external_id where entity_id = %s and scheme = 'us.contest' limit 1", [contest]
                ).fetchone()[0]
                mine = int(rows["votes"].sum())
                prior = conn.execute(
                    """select rs.id, sum(v.value) from result_set rs
                       join result_value v on v.result_set_k = rs.k and v.measure = 1
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and rs.is_default
                         and not exists (select 1 from contest_option ch where ch.parent_option_id = o.id)
                       group by rs.id""",
                    [contest],
                ).fetchall()
                if len(prior) != 1 or not prior[0][1]:
                    skipped.append(f"{label}: no single default set to compare with")
                    continue
                completeness = round(min(mine / float(prior[0][1]), 1), 4)
                if (completeness < 0.995 and not is_state) or mine > float(prior[0][1]) * 1.005:
                    skipped.append(f"{label}: totals disagree ({mine} against {prior[0][1]})")
                    continue
                whole = completeness >= 0.999 and (inside or not is_state)
            elif is_state and not inside:
                skipped.append(f"{label}: not in the archive and not wholly inside the city")
                continue
            else:
                contest_key = (
                    f"{day}:NY:{path.replace('-', ':')}" + (f":{slug(scope)}" if scope else "")
                    if is_state
                    else f"{day}:new-york-city:{seat_path}"
                )
                contest = cs.key("us.contest", contest_key)
                whole = True
                completeness = 1
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
            real = rows.filter(pl.col("party").is_null() | (pl.col("party") != "WRITE-IN")).with_columns(
                pl.col("name").str.replace_all(r"\s+", " ").alias("name")
            )
            ranked = real.group_by("name").agg(pl.col("votes").sum()).sort("votes", "name", descending=[True, False])
            margin = magnitude > 0 and ranked.height > magnitude and ranked["votes"][magnitude - 1] == ranked["votes"][magnitude]
            roots: dict[str, UUID] = {}
            tickets: dict[tuple[str, str | None], UUID] = {}
            known: dict[tuple[str, str], UUID] = {}
            clash: set[tuple[str, str]] = set()
            if attach:
                for option_id, ballot_name in conn.execute(
                    """select o.id, ca.ballot_name from contest_option o join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                       where o.contest_id = %s and o.parent_option_id is null and o.kind in ('candidate', 'ticket')""",
                    [contest],
                ).fetchall():
                    words = [w for w in re.sub(r"[^a-z ]", " ", ballot_name.lower().split("/")[0]).split() if w not in ("jr", "sr", "ii", "iii")]
                    for token in {(words[0], words[-1]), (words[0][0], words[-1])}:
                        if token in known:
                            clash.add(token)
                        known[token] = option_id

            def lookup(candidate: str) -> UUID | None:
                words = [w for w in re.sub(r"[^a-z ]", " ", candidate.lower().split("/")[0]).split() if w not in ("jr", "sr", "ii", "iii")]
                if len(words) < 2:
                    return None
                rest = "".join(words[1:])
                for token in ((words[0], words[-1]), (words[0][0], words[-1]), (words[0], rest), (words[0][0], rest)):
                    if token in known and token not in clash:
                        return known[token]
                found = {option for (first, last), option in known.items() if words[-1] in (first, last) or rest == last}
                if len(found) == 1:
                    return found.pop()
                close = [
                    (SequenceMatcher(None, f"{words[0]} {rest}", f"{first} {last}").ratio(), option)
                    for (first, last), option in known.items()
                    if len(first) > 1 and first[0] == words[0][0]
                ]
                close = sorted(close, key=lambda t: -t[0])
                if close and close[0][0] >= 0.88 and (len(close) == 1 or close[1][0] < 0.8):
                    return close[0][1]
                return None

            if attach and strict and (unmatched := [c for c in ranked["name"] if lookup(c.title()) is None]):
                skipped.append(f"{label}: {unmatched} do not match the contest's options {sorted(known)}")
                continue
            source_title = f"{TITLE}, {kind} {day}"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted and source not in added:
                added.add(source)
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url=next(iter(urls.values())).partition("#")[0], license="Public record")
            for url in urls.values():
                cs.artifact(raw.cached(pipeline, url.partition("#")[0]), source)
            result_set = cs.key(f"{pipeline}.result_set", contest_key)
            declared = {}
            if attach and whole:
                columns = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
                for row in conn.execute(
                    f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s", [contest, result_set]
                ).fetchall():
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
                declared = dict(
                    conn.execute(
                        """select o.option_id, o.kind from outcome o join result_set r on r.id = o.result_set_id
                           where r.contest_id = %s and r.is_default and r.id <> %s""",
                        [contest, result_set],
                    ).fetchall()
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
                is_default=whole,
                completeness=None if is_state and not whole else completeness,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note + (NOTE_STATE if not whole else ""))
            for rank, candidate in enumerate(ranked["name"]):
                label_text = candidate.title()
                root_key = f"{contest_key}:{slug(candidate)}"
                words = [w for w in re.sub(r"[^a-z ]", " ", label_text.lower().split("/")[0]).split() if w not in ("jr", "sr", "ii", "iii")]
                if attach and (existing := lookup(label_text)) is not None:
                    roots[candidate] = existing
                    if whole:
                        cs.add("outcome", result_set_id=result_set, option_id=roots[candidate], kind=declared.get(roots[candidate], "lost"))
                    continue
                roots[candidate] = cs.key("us.option", root_key)
                if is_party:
                    cs.add("contest_option", id=roots[candidate], contest_id=contest, kind="candidate", ballot_label=label_text)
                    if not margin and whole:
                        cs.add("outcome", result_set_id=result_set, option_id=roots[candidate], kind="advanced" if rank < magnitude else "lost")
                    continue
                matched = people.get((words[0], words[-1])) if len(words) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person = matched[0]
                else:
                    person = cs.key(f"{pipeline}.person", f"NY:{slug(label_text)}")
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
                if whole and attach:
                    cs.add("outcome", result_set_id=result_set, option_id=roots[candidate], kind="lost")
                elif not margin and whole:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=roots[candidate],
                        kind=("advanced" if kind not in ("General", "Special") else "elected") if rank < magnitude else "lost",
                    )
            write_in = None
            if rows.filter(pl.col("party") == "WRITE-IN").height:
                write_in = conn.execute(
                    "select id from contest_option where contest_id = %s and kind = 'write_in' limit 1", [contest]
                ).fetchone() if attach else None
                if write_in is not None:
                    write_in = write_in[0]
                else:
                    write_in = cs.key("us.option", f"{contest_key}:write_in")
                    cs.add("contest_option", id=write_in, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            summed: dict[tuple[UUID, UUID], int] = {}
            for county, bkind, bid, candidate, line, votes in (
                rows.with_columns(pl.col("name").str.replace_all(r"\s+", " "))
                .group_by("county", "bkind", "bid", "name", "party")
                .agg(pl.col("votes").sum())
                .iter_rows()
            ):
                unit = unit_for(era, bid, county, part) if bkind == "ad" else county_units[county]
                if line == "WRITE-IN":
                    option = write_in
                elif line is None:
                    option = roots[candidate]
                else:
                    if (candidate, line) not in tickets:
                        reused = (
                            conn.execute(
                                "select id from contest_option where parent_option_id = %s and party_id = %s limit 1",
                                [roots[candidate], party_id(line)[0]],
                            ).fetchone()
                            if attach
                            else None
                        )
                        if reused is not None:
                            tickets[(candidate, line)] = reused[0]
                        else:
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
                unit = unit_for(era, bid, county, part) if bkind == "ad" else county_units[county]
                cast = ballots.filter((pl.col("url") == urls[county]) & (pl.col("bkind") == bkind) & (pl.col("bid") == bid) & pl.col("office").is_in(rows["office"].unique().to_list()))["votes"]
                if bkind == "ad" and not cast.is_empty():
                    values.append((str(result_set), str(unit), None, 3, int(cast[0])))
                if magnitude == 1:
                    got = rows.filter((pl.col("county") == county) & (pl.col("bid") == bid))["votes"].sum()
                    values.append((str(result_set), str(unit), None, 4, int(got)))
            taken += 1
        for day_text, marker, qslug, qtitle, wording_url, wording_title, wording in QUESTIONS if questions else ():
            day = date.fromisoformat(day_text)
            era = "2002" if day_text < "2012-01-01" else "2012"
            qrows = entries.filter(
                (pl.col("day") == day_text) & pl.col("office").str.to_uppercase().str.contains(marker, literal=True) & (pl.col("county") != "Crossover")
            )
            urls = {}
            for county, url in qrows.select("county", "url").unique().sort("url").iter_rows():
                urls.setdefault(county, url)
            qrows = qrows.filter(pl.col("url").is_in(list(urls.values())))
            if len(urls) < 5:
                skipped.append(f"{day} question {qslug}: only {sorted(urls)}")
                continue
            parts = []
            for county, url in urls.items():
                part = qrows.filter(pl.col("url") == url)
                mine = part.filter(pl.col("bkind") == "ad")
                declared = totals.filter((pl.col("url") == url) & (pl.col("bkind") == "total") & pl.col("office").is_in(part["office"].unique().to_list()))["votes"]
                if mine.is_empty() or declared.is_empty() or int(mine["votes"].sum()) != int(declared[-1]):
                    skipped.append(f"{day} question {qslug}: {county} lines do not sum to the total")
                    parts = None
                    break
                parts.append(mine)
            if parts is None:
                continue
            qrows = pl.concat(parts)
            election = conn.execute(
                "select id from election where polity_id = %s and date_from = %s and kind in ('local', 'general') order by kind = 'local' desc",
                [city, day],
            ).fetchone() or conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = %s", [f"{day}:NY"]
            ).fetchone()
            election_path = conn.execute("select path from slug where entity_id = %s limit 1", [election[0]]).fetchone()[0]
            contest_key = f"{day}:new-york-city:proposal-{qslug}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election[0],
                kind="ballot_question",
                district_area_id=place,
                magnitude=1,
                round_number=1,
                electoral_system_id=cs.key("us.electoral_system", "referendum"),
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, f"{day.year} New York City ballot question: {qtitle}")
            cs.add("slug", path=f"{election_path}/proposal-{qslug}", entity_id=contest)
            source_title = f"{TITLE}, {day}"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted and source not in added:
                added.add(source)
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url=next(iter(urls.values())).partition("#")[0], license="Public record")
            for url in urls.values():
                cs.artifact(raw.cached(pipeline, url.partition("#")[0]), source)
            text_source = cs.source("New York City Charter Revision Commission", wording_title)
            if text_source in cs.minted and text_source not in added:
                added.add(text_source)
                cs.add("source", id=text_source, kind="official_body", publisher="New York City Charter Revision Commission", title=wording_title, url=wording_url, license="Public record")
            cs.add("ballot_question_text", contest_id=contest, kind="text", lang="en", body=wording, url=wording_url, source_id=text_source)
            result_set = cs.key(f"{pipeline}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election[0],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE_QUESTION)
            answers = {}
            for position, answer in enumerate(("YES", "NO"), start=1):
                answers[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add("contest_option", id=answers[answer], contest_id=contest, kind="answer", ballot_position=position, ballot_label=answer.title())
            summed = {}
            for county, bid, answer, votes in qrows.select("county", "bid", "name", "votes").iter_rows():
                if answer.upper() not in answers:
                    continue
                unit = unit_for(era, bid, county)
                summed[(unit, answers[answer.upper()])] = summed.get((unit, answers[answer.upper()]), 0) + votes
            values.extend((str(result_set), str(unit), str(option), 1, votes) for (unit, option), votes in summed.items())
            for county, bid in qrows.select("county", "bid").unique().iter_rows():
                unit = unit_for(era, bid, county)
                cast = ballots.filter((pl.col("url") == urls[county]) & (pl.col("bkind") == "ad") & (pl.col("bid") == bid) & pl.col("office").is_in(qrows["office"].unique().to_list()))["votes"]
                if not cast.is_empty():
                    values.append((str(result_set), str(unit), None, 3, int(cast[0])))
                values.append((str(result_set), str(unit), None, 4, int(qrows.filter((pl.col("county") == county) & (pl.col("bid") == bid))["votes"].sum())))
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
