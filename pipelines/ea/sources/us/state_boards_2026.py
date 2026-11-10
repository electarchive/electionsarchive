import csv
import html
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import enr_al
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect
from ea.typed import one

SOURCE = "us.state_boards_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DAY = "2026-11-03"
NE = "https://electionresults.nebraska.gov/resultsCSV.aspx?text=All&type={}&map=DIST"
ND = "https://vip.sos.nd.gov/CandidateList.aspx?eid=348"
ND_CONTEST = "22025"
MT = "https://esersws.mt.gov/ResultsAjax.svc/GetMapData?type=PSC&category=CTY&raceID={}&osn={}&county=0&party=0"
SOURCES = {
    "MI": (
        "Michigan Bureau of Elections",
        "Official candidate listing, November 3, 2026 general election",
        [
            "https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do?electionType=GEN&electionYear=2026&page=page.miboePublicReport"
        ],
        ".html",
    ),
    "CO": (
        "Colorado Secretary of State",
        "2026 general election candidate list",
        ["https://www.coloradosos.gov/pubs/elections/vote/generalCandidates.html"],
        ".html",
    ),
    "NE": (
        "Nebraska Secretary of State",
        "Election night results export, November 3, 2026 general election",
        [NE.format(kind) for kind in ("SE", "RG", "PS")],
        ".csv",
    ),
    "MT": (
        "Montana Secretary of State",
        "Election night results, Public Service Commissioner, November 3, 2026 general election",
        [MT.format("450000000004070", "3001"), MT.format("450000000004071", "3005")],
        ".json",
    ),
    "NM": (
        "New Mexico Secretary of State",
        "Election night results export, November 3, 2026 general election",
        ["https://electionresults.sos.nm.gov/resultsCSV.aspx?text=All&type=ECX&map=DIST"],
        ".csv",
    ),
    "UT": (
        "Utah Lieutenant Governor's Office",
        "Election night results, November 3, 2026 general election",
        ["https://electionresults.utah.gov/results/public/api/elections/utah/General11032026/ballot-items"],
        ".json",
    ),
    "AL": (
        "Alabama Secretary of State",
        "Election night results export, November 3, 2026 general election",
        [f"{enr_al.PAGE.format(enr_al.ECODE)}#export"],
        ".xlsx",
    ),
    "KS": (
        "Kansas Secretary of State",
        "Candidate list, 2026 general election",
        ["https://www.sos.ks.gov/elections/elections_upcoming_candidate.aspx#browser"],
        ".json",
    ),
    "NV": (
        "Nevada Secretary of State",
        "2026 General Election: Statewide Certified List of Candidates",
        ["https://www.nvsos.gov/sos/elections/election-information/2026-election-information"],
        ".pdf",
    ),
    "ND": (
        "North Dakota Secretary of State",
        "Candidate list, Public Service Commissioner, 2026 general election",
        [f"{ND}#contest-{ND_CONTEST}"],
        ".html",
    ),
}
HELD = {"KS": ("us.fec_2026", "application/json"), "NV": ("us.nv_districts_2026", "application/pdf")}
OFFICES = {
    "MI:sboe": ("member-of-the-state-board-of-education", "Member of the State Board of Education"),
    "MI:um": ("regent-of-the-university-of-michigan", "Regent of the University of Michigan"),
    "MI:msu": ("trustee-of-michigan-state-university", "Trustee of Michigan State University"),
    "MI:wsu": ("governor-of-wayne-state-university", "Governor of Wayne State University"),
    "CO:sboe": ("member-of-the-state-board-of-education", "Member of the State Board of Education"),
    "CO:regents": ("regent-of-the-university-of-colorado", "Regent of the University of Colorado"),
    "ND:psc": ("public-service-commissioner", "Public Service Commissioner"),
    "ND:psc-unexpired": ("public-service-commissioner", "Public Service Commissioner"),
}
BODIES = {
    "NE:sboe": ("state-board-of-education", "Nebraska State Board of Education"),
    "NE:regents": ("board-of-regents", "University of Nebraska Board of Regents"),
    "NE:psc": ("public-service-commission", "Nebraska Public Service Commission"),
    "MT:psc": ("public-service-commission", "Montana Public Service Commission"),
    "NM:pec": ("public-education-commission", "New Mexico Public Education Commission"),
    "UT:sboe": ("state-board-of-education", "Utah State Board of Education"),
    "AL:sboe": ("state-board-of-education", "Alabama State Board of Education"),
    "KS:sboe": ("state-board-of-education", "Kansas State Board of Education"),
    "NV:regents": ("board-of-regents", "Nevada System of Higher Education Board of Regents"),
}
SEATS = {
    "MI:sboe": {"": 2},
    "MI:um": {"": 2},
    "MI:msu": {"": 2},
    "MI:wsu": {"": 2},
    "CO:sboe": {"1": 1, "3": 1, "7": 1},
    "CO:regents": {"2": 1, "6": 1, "7": 1},
    "NE:sboe": {"5": 1, "6": 1, "7": 1, "8": 1},
    "NE:regents": {"1": 1, "2": 1, "4": 1},
    "NE:psc": {"2": 1},
    "MT:psc": {"1": 1, "5": 1},
    "NM:pec": {"2": 1, "3": 1, "5": 1, "6": 1, "7": 1},
    "UT:sboe": {"1": 1, "2": 1, "4": 1, "5": 1, "7": 1, "8": 1, "11": 1, "14": 1},
    "AL:sboe": {"2": 1, "4": 1, "6": 1, "8": 1},
    "KS:sboe": {"1": 1, "3": 1, "5": 1, "7": 1, "9": 1},
    "NV:regents": {"2": 1, "3": 1, "5": 1, "8": 1, "10": 1},
    "ND:psc": {"": 1},
    "ND:psc-unexpired": {"": 1},
}
REMAINDER = {("NE:regents", "4"), ("UT:sboe", "7"), ("ND:psc-unexpired", "")}
FIPS = {
    "MI": "26",
    "CO": "08",
    "NE": "31",
    "MT": "30",
    "NM": "35",
    "UT": "49",
    "AL": "01",
    "KS": "20",
    "NV": "32",
    "ND": "38",
}
STATES = {
    "MI": "Michigan",
    "CO": "Colorado",
    "NE": "Nebraska",
    "MT": "Montana",
    "NM": "New Mexico",
    "UT": "Utah",
    "AL": "Alabama",
    "KS": "Kansas",
    "NV": "Nevada",
    "ND": "North Dakota",
}
PARTIES = {
    "Democratic Party": "democratic-party",
    "Democratic": "democratic-party",
    "DEM": "democratic-party",
    "Republican Party": "republican-party",
    "Republican": "republican-party",
    "REP": "republican-party",
    "Libertarian Party": "libertarian-party",
    "Green Party": "green-party",
    "U.S. Taxpayers Party": "MI:u-s-taxpayers-party",
    "Working Class Party": "MI:working-class-party",
    "Natural Law Party": "MI:natural-law-party",
    "FWD": "forward-party",
    "Democratic-NPL": "democratic-party",
}
NONPARTISAN = {"NON", "Nonpartisan"}
MI_BOARDS = {
    "Member of the State Board of Education": "MI:sboe",
    "Regent of the University of Michigan": "MI:um",
    "Trustee of Michigan State University": "MI:msu",
    "Governor of Wayne State University": "MI:wsu",
}
CO_BOARDS = {"State Board of Education": "CO:sboe", "University of Colorado Board of Regents": "CO:regents"}
NE_BOARDS = {"SE": "NE:sboe", "RG": "NE:regents", "PS": "NE:psc"}


def fetch() -> None:
    for state, (_, _, urls, suffix) in SOURCES.items():
        for url in urls:
            if state not in ("AL", "ND") and state not in HELD:
                raw.fetch(SOURCE, url, suffix=suffix)
    for state, (origin, media_type) in HELD.items():
        held = raw.cached(origin, SOURCES[state][2][0])
        raw.store(
            SOURCE,
            held.url,
            held.path.read_bytes(),
            suffix=SOURCES[state][3],
            retrieved_at=held.retrieved_at,
            media_type=media_type,
        )
    with httpx.Client(timeout=120, follow_redirects=True) as client:

        def get(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> bytes | None:
            response = client.get(url, headers=headers)
            return response.content if response.status_code == 200 else None

        def store(url: str, content: bytes, suffix: str) -> None:
            raw.store(
                SOURCE, url, content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type="application/xlsx"
            )

        try:
            raw.cached(SOURCE, SOURCES["AL"][2][0])
        except LookupError:
            enr_al.collect(client, get, store)
            raw.cached(SOURCE, SOURCES["AL"][2][0])
        try:
            raw.cached(SOURCE, SOURCES["ND"][2][0])
            return
        except LookupError:
            pass
        page = client.get(ND, headers={"User-Agent": "Mozilla/5.0"}).text
        for fields in (
            {"__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlContest"},
            {"ctl00$ContentPlaceHolder1$btnSearch": "Search"},
        ):
            form = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page))
            form |= {"ctl00$ContentPlaceHolder1$ddlContest": ND_CONTEST, **fields}
            response = client.post(ND, data=form, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            page = response.text
        raw.store(
            SOURCE,
            SOURCES["ND"][2][0],
            page.encode(),
            suffix=".html",
            retrieved_at=datetime.now(UTC),
            media_type="text/html",
        )


def cells(text: str) -> list[str]:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    return [
        re.sub(r"\s+", " ", html.unescape(cell)).strip()
        for cell in re.split(r"<[^>]+>", text)
        if re.sub(r"\s+", " ", html.unescape(cell)).strip()
    ]


def cells_of(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
    ]


def number(pattern: str, text: str) -> str:
    found = re.fullmatch(pattern, text)
    if found is None:
        raise ValueError(f"{text!r} does not match {pattern}")
    return str(int(found[1]))


def parse() -> None:
    rows: list[dict[str, str | None]] = []

    def add(state: str, board: str, district: str, candidate: str, party: str | None, url: str) -> None:
        name = cased(re.sub(r"\s+", " ", candidate).strip())
        if party is not None and party not in PARTIES and party not in NONPARTISAN:
            raise ValueError(f"{board} {district}: {name} has party {party!r}")
        rows.append(
            {
                "state": state,
                "board": board,
                "district": district,
                "candidate": name,
                "party": PARTIES.get(party or ""),
                "url": url,
            }
        )

    url = SOURCES["MI"][2][0]
    tokens = cells(raw.cached(SOURCE, url).path.read_text(errors="replace"))
    board = None
    for index, token in enumerate(tokens):
        if found := re.fullmatch(r"(.+?) \d+ Year Terms? \((\d+)\) Positions?", token):
            board = MI_BOARDS.get(found[1])
            continue
        if (
            board
            and token in PARTIES
            and index + 3 < len(tokens)
            and tokens[index + 3] in ("Convention", "Filing Fee", "Petition")
        ):
            surname, _, given = tokens[index + 1].partition(", ")
            add("MI", board, "", f"{given} {surname}", token, url)
    url = SOURCES["CO"][2][0]
    tokens = cells(raw.cached(SOURCE, url).path.read_text(errors="replace"))
    for index, token in enumerate(tokens):
        if token in CO_BOARDS and index + 2 < len(tokens):
            add("CO", CO_BOARDS[token], tokens[index + 1], tokens[index - 1], tokens[index + 2], url)
    for kind, board in NE_BOARDS.items():
        url = NE.format(kind)
        for row in csv.DictReader(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig").splitlines()):
            add(
                "NE",
                board,
                number(r"District (\d+)", row["AreaNum"]),
                row["CandidateName"],
                row["PartyCode"],
                url,
            )
    for url in SOURCES["MT"][2]:
        for row in json.loads(raw.cached(SOURCE, url).path.read_text())["d"]:
            district = number(r"PUBLIC SERVICE COMMISSIONER, DISTRICT (\d+)", row["RaceName"])
            add("MT", "MT:psc", district, row["calcCandidate"], row["PartyCode"], url)
    url = SOURCES["NM"][2][0]
    for row in csv.DictReader(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig").splitlines()):
        if row["RaceName"] == "Public Education Commissioner":
            district = number(r"DISTRICT (\d+)", row["AreaNum"])
            add("NM", "NM:pec", district, row["CandidateName"], row["PartyCode"], url)
    url = SOURCES["UT"][2][0]
    for item in json.loads(raw.cached(SOURCE, url).path.read_text())["data"]:
        if found := re.fullmatch(r"State School Board District (\d+)\b.*", item["name"][0]["text"]):
            for option in item["summaryResults"]["ballotOptions"]:
                if not option["isWriteIn"]:
                    party = option["party"]["name"][0]["text"] if option.get("party") else None
                    add("UT", "UT:sboe", found[1], option["name"][0]["text"], party, url)
    url = SOURCES["AL"][2][0]
    for row in enr_al.sheets(raw.cached(SOURCE, url).path)["AllResults"]:
        found = re.fullmatch(r"STATE BOARD OF EDUCATION MEMBER DISTRICT (\d+)", row["Contest Title"])
        if found and row["Candidate Name"] != "Write-In":
            add("AL", "AL:sboe", found[1], row["Candidate Name"], row["Party Code"], url)
    url = SOURCES["KS"][2][0]
    header, *table = json.loads(raw.cached(SOURCE, url).path.read_text())["rows"]
    for row in table:
        record = dict(zip(header, row, strict=True))
        if record["Office"] == "Member, State Board of Education":
            add("KS", "KS:sboe", record["District"], record["Candidate"], record["Party"], url)
    url = SOURCES["NV"][2][0]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    for line in text.splitlines():
        if found := re.fullmatch(
            r"\s*\S.*?\s{2,}(\S.*?)\s{2,}(Nonpartisan)\s{2,}Board of Regent, District (\d+)\s{2,}.*", line
        ):
            add("NV", "NV:regents", found[3], found[1], found[2], url)
    url = SOURCES["ND"][2][0]
    page = raw.cached(SOURCE, url).path.read_text()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL):
        found = cells_of(row)
        if len(found) > 9 and found[2] == "Public Service Commissioner" and found[4] == "STATE":
            board = "ND:psc-unexpired" if "Unexpired" in found[0] else "ND:psc"
            add("ND", board, "", found[5], found[9], url)
    frame = pl.DataFrame(rows).unique(maintain_order=True)
    for board, seats in SEATS.items():
        found = set(frame.filter(pl.col("board") == board)["district"].to_list())
        if found != set(seats):
            raise ValueError(f"{board}: districts {sorted(found)} instead of {sorted(seats)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, "candidates", frame.select("board", "district").n_unique(), "contests")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide boards and commissions on the November 3, 2026 ballot in Michigan, Colorado, Nebraska, Montana, New Mexico, Utah, Alabama, Kansas, Nevada, and North Dakota, with candidates from each state's official candidate list or results site",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for state, (publisher, title, urls, _) in SOURCES.items():
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=urls[0].partition("#")[0],
                    license=None,
                )
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), source)
            sources[state] = source
        made: set[UUID] = set()
        counts = {"contests": 0, "candidates": 0, "matched": 0}
        for state, rows in frame.group_by("state", maintain_order=True):
            state = str(state[0])
            polity = cs.polity(f"US-{state}")
            (state_area,) = one(
                conn.execute(
                    "select area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [polity]
                )
            )
            election = cs.key("us.election", f"{DAY}:{state}")
            if election in cs.minted:
                raise ValueError(f"election {DAY}:{state} must already exist")
            pool: dict[str, set[UUID]] = {}
            for person, text in conn.execute(
                """select distinct cy.person_id, cy.ballot_name from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ %s
                   where cy.ballot_name is not null""",
                [f"^20(1[0-9]|2[0-6])-\\d\\d-\\d\\d:{state}:"],
            ).fetchall():
                pool.setdefault(slug(text), set()).add(person)
            source = sources[state]
            for (board, district), group in rows.group_by("board", "district", maintain_order=True):
                board, district = str(board), str(district)
                remainder = (board, district) in REMAINDER
                office = body = None
                if board in OFFICES:
                    tail, title = OFFICES[board]
                    office = cs.key("us.office", f"{tail}:{state}")
                    if office in cs.minted:
                        raise ValueError(f"office {tail}:{state} must already exist")
                    if district:
                        tail = f"{tail}:district-{district}"
                        label = f"{STATES[state]} {title}, Congressional District {district}"
                        area = cs.area("us.cd", f"{FIPS[state]}{int(district):02d}")
                        if area in cs.minted:
                            raise ValueError(f"{state} congressional district {district} must already exist")
                    else:
                        label = f"{STATES[state]} {title}"
                        area = state_area
                else:
                    stem, title = BODIES[board]
                    body = cs.key("us.body", f"{state}:{stem}")
                    if body not in made:
                        made.add(body)
                        if body in cs.minted:
                            cs.add("body", id=body, polity_id=polity, kind="council")
                            name(body, title)
                    tail = f"{stem}:{district}"
                    label = f"{title}, District {district}"
                    code = f"{FIPS[state]}:{stem}:district-{district}"
                    area = cs.area("us.local_district", code)
                    if area in cs.minted and area not in made:
                        made.add(area)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=state_area)
                        name(area, label)
                if remainder:
                    tail, label = f"{tail}:remainder", f"{label}, unexpired term"
                key = f"{DAY}:{state}:{tail}"
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    seat_id=None,
                    boundary_set_id=None,
                    magnitude=SEATS[board][district],
                    term="remainder" if remainder else "full",
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=cs.key(
                        "us.electoral_system", "fptp" if SEATS[board][district] == 1 else "block"
                    ),
                    default_result_kind="counted",
                )
                name(contest, f"2026 {label} election")
                cs.add("slug", path=f"us/{state.lower()}/{DAY}-general/{slug(label)}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source, locator=None)
                counts["contests"] += 1
                for position, (text, party) in enumerate(group.select("candidate", "party").iter_rows(), start=1):
                    found_people = pool.get(slug(text), set())
                    if len(found_people) == 1:
                        person = next(iter(found_people))
                        counts["matched"] += 1
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                        if person in cs.minted and person not in made:
                            made.add(person)
                            cs.add("person", id=person)
                            name(person, text)
                    option = cs.key("us.option", f"{key}:{slug(text)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party) if party else None,
                        ballot_position=position,
                        ballot_label=text,
                    )
                    candidacy = cs.key("us.candidacy", f"{key}:{slug(text)}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=text,
                        is_independent=False,
                    )
                    cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                    counts["candidates"] += 1
        print(counts)
        return cs.write()
