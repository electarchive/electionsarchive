import csv
import html
import io
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import initials
from ea.store import DATA, connect

SOURCE = "us.fec_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CN26 = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
BROWSER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
FL_URL = "https://dos.elections.myflorida.com/candidates/extractCanList.asp?elecID=20261103-GEN"
OFFICIAL = {
    "FL": (
        "Florida Department of State, Division of Elections",
        "Florida 2026 general election candidate list",
        [FL_URL],
    ),
    "NC": (
        "North Carolina State Board of Elections",
        "North Carolina 2026 candidate listing",
        ["https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/Candidate_Listing_2026.csv"],
    ),
    "WY": (
        "Wyoming Secretary of State",
        "2026 Wyoming general election candidates",
        ["https://sos.wyo.gov/Elections/Docs/2026/2026_WY_General_Election_Candidates.csv"],
    ),
    "ME": (
        "Maine Department of the Secretary of State",
        "2026 general election candidate list",
        [
            "https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/2026%20General%20Candidate%20List%20-%20FINAL.xlsx"
        ],
    ),
    "VT": (
        "Vermont Secretary of State",
        "2026 general election qualified candidates",
        [
            "https://outside.vermont.gov/dept/sos/Elections_Division/election_info_resources/candidates/2026_general_election_qualified_candidates.xlsx"
        ],
    ),
    "DE": (
        "Delaware Department of Elections",
        "2026 general election candidate list",
        ["https://elections.delaware.gov/candidates/candidatelist/genl_fcddt_2026.html"],
    ),
    "MT": (
        "Montana Secretary of State",
        "Federal general 2026 candidate list",
        ["https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx?e=450002987#csv"],
    ),
    "WA": (
        "Washington Secretary of State",
        "2026 general election candidate list",
        ["https://voter.votewa.gov/CandidateList.aspx?e=899#csv"],
    ),
    "SD": (
        "South Dakota Secretary of State",
        "2026 general election candidate list",
        ["https://vip.sdsos.gov/candidatelist.aspx?eid=774"],
    ),
    "VA": (
        "Virginia Department of Elections",
        "November 3, 2026 general election candidate list: federal offices",
        [
            "https://www.elections.virginia.gov/casting-a-ballot/candidate-list/november-3-2026-gen-elect-federal-offices/"
        ],
    ),
}
INDEPENDENT = re.compile(r"(?i)^(independent|unaffiliated|unenrolled|undeclared|no party.*|npa|non|none|nonpartisan|non-partisan|petition|)$")
MAJOR = [
    (re.compile(r"(?i)farmer.?labor"), "MN:minnesota-democratic-farmer-labor-party"),
    (re.compile(r"(?i)\bdemocrat"), "democratic-party"),
    (re.compile(r"(?i)\brepublican"), "republican-party"),
    (re.compile(r"(?i)\blibertarian"), "libertarian-party"),
    (re.compile(r"(?i)\bgreen\b"), "green-party"),
    (re.compile(r"(?i)\bconstitution\b"), "constitution-party"),
]
SUFFIX = re.compile(r"(?i)^(jr|sr|ii|iii|iv|v)\.?$")
DAY = date(2026, 11, 3)
REDISTRICTED = {"AL", "CA", "FL", "LA", "NC", "OH", "TN", "TX", "UT"}
CLASS_THREE_SPECIALS = ("OH", "FL")
NO_GENERAL = ("PR",)
SYSTEM_OVERRIDES = {"AK": "top_four", "LA": "fptp"}
ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def cells(text: str) -> list[list[str]]:
    return [
        [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL)
    ]


def read(state: str, path: Path) -> list[tuple[str, int | None, str, str]]:
    rows: list[tuple[str, int | None, str, str]] = []
    if state == "FL":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1")), delimiter="\t"):
            if row["StatusCode"] in ("QUA", "UNO") and row["PartyCode"] != "WRI":
                office = {"USR": "house", "USS": "senate", "GOV": "governor"}.get(row["OfficeCode"], row["OfficeDesc"])
                district = row["Juris1num"] or row["Juris2num"]
                text = " ".join(part for part in (row["NameFirst"], row["NameMiddle"], row["NameLast"]) if part)
                rows.append((office, int(district) if district.isdigit() else None, text, row["PartyDesc"]))
    if state == "NC":
        seen: set[tuple[str, str]] = set()
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1"))):
            if row["election_dt"] != "11/03/2026" or (row["contest_name"], row["name_on_ballot"]) in seen:
                continue
            seen.add((row["contest_name"], row["name_on_ballot"]))
            house = re.fullmatch(r"US HOUSE OF REPRESENTATIVES DISTRICT (\d+)", row["contest_name"])
            office = "house" if house else "senate" if row["contest_name"] == "US SENATE" else row["contest_name"]
            party = {
                "DEM": "Democratic",
                "REP": "Republican",
                "LIB": "Libertarian",
                "GRE": "Green",
                "UNA": "Unaffiliated",
            }.get(row["party_candidate"], row["party_candidate"])
            rows.append((office, int(house.group(1)) if house else None, row["name_on_ballot"], party))
    if state == "VA":
        for office, district, party, text, *_ in cells(path.read_text())[1:]:
            number = re.match(r"(\d+)", district)
            chamber = {"Member, United States Senate": "senate", "Member, House of Representatives": "house"}.get(
                office, office
            )
            rows.append((chamber, int(number.group(1)) if number else None, text, party))
    if state == "WY":
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="latin-1"))):
            if not row["Date Withdrawn"]:
                office = {
                    "UNITED STATES SENATOR": "senate",
                    "UNITED STATES REPRESENTATIVE": "house",
                    "GOVERNOR": "governor",
                }.get(row["Office Sought"], row["Office Sought"])
                party = {
                    "REP": "Republican",
                    "DEM": "Democratic",
                    "LBR": "Libertarian",
                    "CT": "Constitution",
                    "IND": "Independent",
                }.get(row["Party Affiliation"], row["Party Affiliation"])
                rows.append((office, None, row["Ballot Name"], party))
    if state == "DE":
        for office, _, party, text, status, *_ in (row for row in cells(path.read_text()) if len(row) >= 5):
            if status == "Qualified":
                chamber = {"U.S. Senator": "senate", "Representative in Congress": "house", "Governor": "governor"}.get(
                    office, office
                )
                text = re.split(
                    r"\s+(?:WITHDRAWN|Mailing Address:|Residential Address:|Email #|Phone #|Website:)", text
                )[0]
                rows.append((chamber, None, text, party))
    if state in ("MT", "WA"):
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            if row["Status"] in ("FILED", "Active"):
                race = row["Race"].upper()
                chamber = (
                    "house"
                    if race in ("UNITED STATES REPRESENTATIVE", "U.S. REPRESENTATIVE")
                    else "senate"
                    if race in ("UNITED STATES SENATOR", "U.S. SENATOR")
                    else "governor"
                    if race == "GOVERNOR"
                    else row["Race"]
                )
                number = re.search(r"\d+", row["District"])
                party = {
                    "REP": "Republican",
                    "DEM": "Democratic",
                    "LIB": "Libertarian",
                    "GRN": "Green",
                    "IND": "Independent",
                }.get(row["Party Preference"], row["Party Preference"])
                rows.append((chamber, int(number[0]) if number and chamber == "house" else None, row["Name"].lstrip("*"), party))
    if state == "SD":
        for contest, text, party, _, _, _, withdrawn, *_ in (row for row in cells(path.read_text()) if len(row) >= 7):
            if text and not withdrawn and contest != "Contest":
                chamber = {
                    "United States Senator": "senate",
                    "United States Representative": "house",
                    "Governor": "governor",
                }.get(contest, contest)
                rows.append(
                    (
                        chamber,
                        None,
                        text,
                        {"REP": "Republican", "DEM": "Democratic", "LIB": "Libertarian", "IND": "Independent"}.get(
                            party, party
                        ),
                    )
                )
    if state in ("ME", "VT"):
        frame = fastexcel.read_excel(path).load_sheet(0).to_polars()
        for row in frame.iter_rows(named=True):
            if state == "ME":
                office = {"US": "senate", "CG": "house", "GOV": "governor"}.get(row["Office"], row["Office"])
                text = " ".join(
                    part for part in (row["First Name"], row["Middle Name"], row["Last Name"], row["Suffix"]) if part
                )
                party = {"D": "Democratic", "R": "Republican", "G": "Green Independent", "L": "Libertarian"}.get(
                    row["Party"] or "", row["Party"] or ""
                )
                district = int(row["Dist"]) if str(row["Dist"] or "").isdigit() else None
            else:
                office = {"REPRESENTATIVE TO CONGRESS": "house", "GOVERNOR": "governor"}.get(
                    row["Contest"], row["Contest"]
                )
                text = row["Name On Ballot"]
                party = "/".join(
                    {"DEM": "Democratic", "REP": "Republican", "PROG": "Progressive"}.get(
                        part, re.sub(r"\b(And|Of|The)\b", lambda match: match[0].lower(), part.title())
                    )
                    for part in (row["Party"] or "").split("/")
                )
                district = int(row["District Name"]) if str(row["District Name"] or "").isdigit() else None
            rows.append((office, district, text, party))
    return rows


def fetch() -> None:
    raw.fetch(SOURCE, CN26, suffix=".zip")
    for _, _, urls in OFFICIAL.values():
        for url in urls:
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                pass
            if url == FL_URL:
                response = httpx.post(
                    url.split("?")[0],
                    data={
                        "elecID": "20261103-GEN",
                        "office": "All",
                        "status": "All",
                        "cantype": "STA",
                        "FormsButton2": "Download",
                    },
                    headers=BROWSER,
                    timeout=300,
                )
            elif url.endswith("#csv"):
                with httpx.Client(headers=BROWSER, follow_redirects=True, timeout=300) as client:
                    page = client.get(url.removesuffix("#csv")).text
                    fields = {
                        field: html.unescape(value)
                        for field, value in re.findall(
                            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page
                        )
                    }
                    button = re.findall(r'name="([^"]*ExportToCsvButton[^"]*)"', page)[0]
                    response = client.post(url.removesuffix("#csv"), data={**fields, button: " "})
            else:
                response = httpx.get(url, headers=BROWSER, follow_redirects=True, timeout=300, verify=False)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".txt"
                if url == FL_URL
                else ".csv"
                if url.endswith("#csv")
                else Path(url.split("?")[0].rstrip("/")).suffix or ".html",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", ""),
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "state": state,
            "office": office,
            "district": 0 if office == "house" and district is None else district,
            "name": initials(
                re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), re.sub(r"\s+", " ", text).strip().title())
                if text.isupper()
                else re.sub(r"\s+", " ", text).strip()
            ),
            "party": party.strip().title() if party.isupper() and len(party) > 4 else party.strip(),
            "url": url,
        }
        for state, (_, _, urls) in OFFICIAL.items()
        for url in urls
        for office, district, text, party in read(state, raw.cached(SOURCE, url).path)
    ]
    frame = pl.DataFrame(
        rows,
        schema={
            "state": pl.String,
            "office": pl.String,
            "district": pl.Int64,
            "name": pl.String,
            "party": pl.String,
            "url": pl.String,
        },
    )
    frame.write_parquet(PARSED / "candidates.parquet")
    print(
        frame.filter(pl.col("office").is_in(["house", "senate", "governor"]))
        .group_by("state", "office")
        .len()
        .sort("state", "office")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2026-11-03 general election: every House and Senate contest and the 36 governor contests as scheduled, with candidates from the states' certified lists, else the archive's own primary nominees",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        national = cs.key("us.election", f"{DAY}")
        cs.add(
            "election",
            id=national,
            polity_id=us,
            parent_id=None,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            status="scheduled",
        )
        name(national, "2026 United States general election")
        cs.add("slug", path=f"us/{DAY}-general", entity_id=national)

        systems = {key: cs.key("us.electoral_system", key) for key in SYSTEM_OVERRIDES.values()}
        elections: dict[str, UUID] = {}

        templates = conn.execute(
            """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                      co.boundary_set_id, co.magnitude, co.term, co.electoral_system_id,
                      co.default_result_kind, e.polity_id
               from contest co
               join election e on e.id = co.election_id
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               where x.value ~ '^2024-11-05:[A-Z]{2}:house:[0-9]+$'
                  or x.value ~ '^2020-11-03:[A-Z]{2}:senate$'
                  or x.value ~ '^2022-11-08:(OH|FL):senate$'
                  or x.value ~ '^2022-11-08:(?!GU|VI)[A-Z]{2}:governor$'"""
        ).fetchall()
        latest_plan = conn.execute(
            "select id from boundary_set where scheme_code = 'us.cd' order by valid_from desc limit 1"
        ).fetchone()[0]
        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )

        nominees: dict[str, dict[tuple[str, UUID], UUID | None]] = {}
        for value, ballot, person, party_id in conn.execute(
            """select x.value, op.ballot_label, cy.person_id, op.party_id
               from external_id x
               join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
               join outcome o on o.result_set_id = rs.id and o.kind in ('won', 'advanced')
               join contest_option op on op.id = o.option_id
               join candidacy cy on cy.option_id = op.id and cy.role = 'head'
               where x.scheme = 'us.contest'
                 and (x.value ~ '^2026-[0-9-]+:[A-Z]{2}:(governor|senate|house:[0-9]+):(democratic|republican|minnesota-democratic-farmer-labor)$'
                      and o.kind = 'won'
                      or x.value ~ '^2026-[0-9-]+:(CA|WA):(governor|house:[0-9]+)$' and o.kind = 'advanced')"""
        ):
            _, state, chamber, *rest = value.split(":")
            general = f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else "")
            known = nominees.setdefault(general, {})
            known[(ballot, person)] = known.get((ballot, person)) or party_id

        listed: dict[str, list[dict[str, str | int | None]]] = {}
        for row in (
            pl.read_parquet(PARSED / "candidates.parquet")
            .filter(pl.col("office").is_in(["house", "senate", "governor"]))
            .iter_rows(named=True)
        ):
            key = f"{DAY}:{row['state']}:{row['office']}" + (f":{row['district']}" if row["office"] == "house" else "")
            listed.setdefault(key, []).append(row)

        def surname(text: str) -> str:
            words = [word for word in re.sub(r"\"[^\"]*\"|\([^)]*\)|,", " ", text).split() if not SUFFIX.match(word)]
            return slug(words[-1]) if words else ""

        filers: dict[tuple[str, str, int | None], list[tuple[str, str, str]]] = {}
        with zipfile.ZipFile(raw.cached(SOURCE, CN26).path) as archive:
            for line in archive.read("cn.txt").decode("latin-1").splitlines():
                fec, text, _, year, state, office, district, _, status, *_ = line.split("|")
                if year == "2026" and status == "C" and office in ("H", "S"):
                    last, _, first = text.partition(",")
                    chamber = "house" if office == "H" else "senate"
                    filers.setdefault((state, chamber, int(district or 0) if office == "H" else None), []).append(
                        (fec, surname(last), slug(first)[:1])
                    )
        fec_persons = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'fec.candidate'").fetchall()
        )
        fec_held = {entity for entity in fec_persons.values()}
        known: dict[str, list[tuple[UUID, str]]] = {}
        earlier: dict[str, list[tuple[UUID, str]]] = {}
        for value, person, text in conn.execute(
            """select x.value, cy.person_id, n.text
               from external_id x
               join contest_option op on op.contest_id = x.entity_id
               join candidacy cy on cy.option_id = op.id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value ~ '^20(1[8-9]|2[0-6])-[0-9-]+:[A-Z]{2}:(governor|senate|house:[0-9]+)'"""
        ).fetchall():
            day, state, chamber, *rest = value.split(":")
            (known if day.startswith("2026") else earlier).setdefault(
                f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else ""), []
            ).append((person, text))
        surnames = {surname(str(row["name"])) for rows in listed.values() for row in rows if surname(str(row["name"]))}
        statewide: dict[tuple[str, str], set[UUID]] = {}
        for state, person, text in conn.execute(
            """select distinct split_part(x.value, ':', 2), cy.person_id, n.text
               from name n
               join candidacy cy on cy.person_id = n.entity_id
               join contest_option op on op.id = cy.option_id
               join external_id x on x.entity_id = op.contest_id and x.scheme = 'us.contest'
               where n.is_preferred and x.value ~ '^20(1[4-9]|2[0-6])-'
                 and lower(regexp_replace(n.text, '^.* ', '')) = any(%s)""",
            [sorted(surnames)],
        ).fetchall():
            statewide.setdefault((state, slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text))), set()).add(person)
        held: dict[str, dict[UUID, tuple[UUID, str]]] = {}
        for value, option, candidacy, person in conn.execute(
            """select x.value, op.id, cy.id, cy.person_id
               from external_id x
               join contest_option op on op.contest_id = x.entity_id
               join changeset c on c.id = op.changeset_id and c.pipeline = %s
               left join candidacy cy on cy.option_id = op.id
               where x.scheme = 'us.contest' and x.value like %s""",
            [SOURCE, f"{DAY}:%"],
        ).fetchall():
            held.setdefault(value, {})[person] = (option, candidacy)
        option_keys = dict(
            conn.execute(
                "select entity_id, value from external_id where scheme = 'us.option' and value like %s", [f"{DAY}:%"]
            ).fetchall()
        )
        candidacy_keys = dict(
            conn.execute(
                "select entity_id, value from external_id where scheme = 'us.candidacy' and value like %s", [f"{DAY}:%"]
            ).fetchall()
        )
        own_notes = conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where c.pipeline = %s and n.kind = 'methodology'""",
            [SOURCE],
        ).fetchall()
        sources: dict[str, UUID] = {}
        parties: dict[str, UUID] = {}
        party_keys = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }

        def party_of(state: str, label: str) -> UUID | None:
            label = label.split("/")[0].strip()
            if INDEPENDENT.match(label):
                return None
            key = next((key for pattern, key in MAJOR if pattern.search(label)), None)
            if key is None:
                key = next(
                    (key for key in (f"{state}:{slug(label)}", f"{state}:{slug(label)}-party") if key in party_keys),
                    f"{state}:{slug(label)}",
                )
            if key not in parties:
                parties[key] = cs.key("us.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=cs.polity(f"US-{state}"))
                    name(parties[key], label)
            return parties[key]

        def person_of(state: str, key: str, chamber: str, district: int | None, text: str) -> UUID:
            last, first = surname(text), slug(re.sub(r"^\W+", "", text))[:1]
            pool = {person for person, held_text in known.get(key, []) if surname(held_text) == last}
            if len(pool) > 1 or not pool:
                pool = {
                    person
                    for person, held_text in [*known.get(key, []), *earlier.get(key, [])]
                    if surname(held_text) == last and slug(held_text)[:1] == first
                }
            filed = [fec for fec, filer_last, _ in filers.get((state, chamber, district), []) if filer_last == last]
            if len({fec_persons.get(fec, fec) for fec in filed}) > 1:
                filed = [
                    fec
                    for fec, filer_last, filer_first in filers.get((state, chamber, district), [])
                    if filer_last == last and filer_first == first
                ]
            matched = {fec_persons[fec] for fec in filed if fec in fec_persons}
            if not pool and len(matched) == 1:
                pool = matched
            if not pool:
                pool = statewide.get((state, slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", text))), set())
            if len(pool) == 1:
                person = next(iter(pool))
                free = [fec for fec in filed if fec not in fec_persons]
                if len(free) == 1 and person not in fec_held:
                    cs.add("external_id", entity_id=person, scheme="fec.candidate", value=free[0])
                    fec_persons[free[0]] = person
                    fec_held.add(person)
                return person
            if len({*filed}) == 1:
                person = cs.key("fec.candidate", filed[0])
            else:
                person = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, text)
            known.setdefault(key, []).append((person, text))
            return person

        counts = {"house": 0, "senate": 0, "governor": 0, "candidates": 0, "listed": 0, "dropped": 0}
        for (
            value,
            kind,
            office,
            body,
            area,
            seat,
            plan,
            magnitude,
            term,
            system,
            result_kind,
            polity,
        ) in sorted(templates):
            _, state, chamber, *rest = value.split(":")
            if state in NO_GENERAL:
                continue
            state_name = state_names[state]
            if state not in elections:
                elections[state] = cs.key("us.election", f"{DAY}:{state}")
                cs.add(
                    "election",
                    id=elections[state],
                    polity_id=polity,
                    parent_id=national,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="scheduled",
                )
                name(elections[state], f"2026 {state_name} general election")
                cs.add("slug", path=f"us/{state.lower()}/{DAY}-general", entity_id=elections[state])
            key = f"{DAY}:{state}:{chamber}" + (f":{rest[0]}" if chamber == "house" else "")
            contest = cs.key("us.contest", key)
            special = chamber == "senate" and state in CLASS_THREE_SPECIALS
            cs.add(
                "contest",
                id=contest,
                election_id=elections[state],
                kind=kind,
                office_id=office,
                body_id=body,
                district_area_id=area,
                seat_id=seat,
                boundary_set_id=latest_plan if chamber == "house" and state not in REDISTRICTED else None,
                magnitude=magnitude,
                term="remainder" if special else term,
                round_number=1,
                electoral_system_id=systems[SYSTEM_OVERRIDES[state]]
                if state in SYSTEM_OVERRIDES and chamber != "governor"
                else system,
                default_result_kind=result_kind,
            )
            counts[chamber] += 1
            if chamber == "house":
                number = int(rest[0])
                suffix = "th" if 10 <= number % 100 <= 20 else ORDINALS.get(number % 10, "th")
                at_large = (
                    conn.execute(
                        "select 1 from external_id where scheme = 'us.contest' and value = %s",
                        [f"2024-11-05:{state}:house:2"],
                    ).fetchone()
                    is None
                )
                title = (
                    f"2026 U.S. House election in {state_name}'s at-large congressional district"
                    if at_large
                    else f"2026 U.S. House election in {state_name}'s {number}{suffix} congressional district"
                )
                path = f"us/{state.lower()}/{DAY}-general/house-{number}"
            elif chamber == "senate":
                title = f"2026 U.S. Senate {'special ' if special else ''}election in {state_name}"
                path = f"us/{state.lower()}/{DAY}-general/senate"
            else:
                title = f"2026 {state_name} gubernatorial election"
                path = f"us/{state.lower()}/{DAY}-general/governor"
            name(contest, title)
            cs.add("slug", path=path, entity_id=contest)
            if chamber == "house" and state in REDISTRICTED:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="methodology",
                    body_md=f"{state_name} redrew its congressional districts for 2026. The contest is recorded by district number; the new plan's boundaries are not loaded yet.",
                )
            staged: set[UUID] = set()
            entries: list[tuple[str, UUID, UUID | None]] = []
            if state in OFFICIAL and key in listed:
                publisher, title, urls = OFFICIAL[state]
                if state not in sources:
                    sources[state] = cs.source(publisher, title)
                    if sources[state] in cs.minted:
                        cs.add(
                            "source",
                            id=sources[state],
                            kind="official_body",
                            publisher=publisher,
                            title=title,
                            url=urls[0],
                            license=None,
                        )
                    for url in urls:
                        cs.artifact(raw.cached(SOURCE, url), sources[state])
                for row in sorted(listed[key], key=lambda row: str(row["name"])):
                    text, label = str(row["name"]), str(row["party"])
                    entries.append(
                        (
                            text,
                            person_of(state, key, chamber, int(rest[0]) if chamber == "house" else None, text),
                            party_of(state, label),
                        )
                    )
                counts["listed"] += 1
            else:
                entries = [
                    (ballot, person, party_id)
                    for (ballot, person), party_id in sorted(
                        nominees.get(key, {}).items(), key=lambda nominee: (nominee[0][0], str(nominee[0][1]))
                    )
                ]
            for ballot, person, party_id in entries:
                if person in staged:
                    continue
                staged.add(person)
                previous = held.get(key, {}).get(person)
                option = cs.key("us.option", option_keys[previous[0]] if previous else f"{key}:{slug(ballot)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=ballot,
                )
                candidacy = cs.key(
                    "us.candidacy", candidacy_keys[previous[1]] if previous and previous[1] else f"{key}:{slug(ballot)}"
                )
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    is_independent=party_id is None,
                )
                if state in sources and key in listed:
                    cs.add("citation", entity_id=candidacy, source_id=sources[state], locator=None)
                counts["candidates"] += 1
            reason = (
                f"Not on the certified {state_name} 2026 general election candidate list"
                if state in sources and key in listed
                else f"No longer a nominee in the archive's {state_name} 2026 primary result sets"
            )
            for person, (option, candidacy) in held.get(key, {}).items():
                if person not in staged:
                    if candidacy is not None:
                        cs.add(
                            "entity_retirement",
                            entity_id=candidacy,
                            reason=reason,
                        )
                    cs.add("entity_retirement", entity_id=option, reason=reason)
                    counts["dropped"] += 1
            if state in sources and key in listed:
                body = f"Candidates are those on the {OFFICIAL[state][0]}'s {OFFICIAL[state][1]}, as certified for the 2026-11-03 ballot. Declared write-in candidates are not listed."
            elif key in nominees:
                body = f"Candidates are the {state_name} nominees recorded in the archive's own 2026 primary result sets ({'the two advancing candidates of the top-two primary' if state in ('CA', 'WA') else 'the major-party primary winners'}). Where a party's primary was uncontested or has not been loaded, that party's nominee is not yet listed. No ballot list from the state has been confirmed, so third-party and independent candidates are not yet listed."
            else:
                body = None
            if body is not None:
                cs.add("note", entity_id=contest, kind="methodology", body_md=body)
            for note, entity, text in own_notes:
                if (
                    entity == contest
                    and text != body
                    and not text.endswith("the new plan's boundaries are not loaded yet.")
                ):
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=contest,
                        body_md=text,
                        reason="The contest's candidates now come from another list",
                    )
        scheduled = {
            f"{DAY}:{value.split(':')[1]}:{value.split(':')[2]}"
            + (f":{value.split(':')[3]}" if value.split(":")[2] == "house" else "")
            for value, *_ in templates
        }
        print("listed but not scheduled:", sorted({*listed} - scheduled))
        for entity, retired in conn.execute(
            """select x.entity_id, e.retired_by from external_id x join entity e on e.id = x.entity_id
               where (x.scheme, x.value) in (select 'us.contest', unnest(%s::text[]) union select 'us.election', unnest(%s::text[]))""",
            [[f"{DAY}:{state}:house:0" for state in NO_GENERAL], [f"{DAY}:{state}" for state in NO_GENERAL]],
        ).fetchall():
            if retired is None:
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason="Puerto Rico's Resident Commissioner serves a four-year term, elected in 2024; Puerto Rico holds no general election on 2026-11-03",
                )
        print(counts)
        return cs.write()
