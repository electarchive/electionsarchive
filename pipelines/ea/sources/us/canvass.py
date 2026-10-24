import html
import json
import re
import struct
import subprocess
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.us import openelections
from ea.store import DATA, connect

SOURCE = "us.canvass"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
RHODE_ISLAND = "https://s3.amazonaws.com/rigov/election/results/{year}/general_election/{town}.json"
RI_STATEWIDE = "https://s3.amazonaws.com/rigov/election/results/{year}/general_election/statewide.json"
RI_DAYS = {2010: date(2010, 11, 2), 2014: date(2014, 11, 4)}
RI_PUBLISHER = "Rhode Island Board of Elections"
RI_TITLE = "{year} General Election results"
KENTUCKY = (
    "https://elect.ky.gov/results/2020-2029/Documents/"
    "Certification%20of%20Election%20Results%20for%202023%20General%20Election%20Final.pdf"
)
KY_PUBLISHER = "Kentucky State Board of Elections"
KY_TITLE = "Certification of Election Results for 2023 General Election"
KY_METHODOLOGY = (
    "Votes are the county totals in the Kentucky State Board of Elections' certification of the 2023 general "
    "election for all 120 counties. Write-in candidates appear as the certification lists them."
)
OE_PUBLISHER = "OpenElections"
INDIANA = "https://enr.indianavoters.in.gov/archive/{year}General/data/OffCatC_{category}_A.json"
IN_CATEGORIES = {date(2018, 11, 6): (2202, 2198, 2208), date(2022, 11, 8): (1040, 1046, 1036)}
IN_PUBLISHER = "Indiana Election Division"
IN_TITLE = "{year} General Election results"
MICHIGAN = (
    "https://raw.githubusercontent.com/openelections/openelections-data-mi/"
    "2103114f669c2bb25f19792bf5ca290300381396/2022/20221108__mi__general__county.csv"
)
MI_TITLE = "Michigan 2022 general election results by county"
ARIZONA = (
    "https://web.archive.org/web/20221205195258id_/"
    "https://azsos.gov/sites/default/files/2022Dec05_General_Election_Canvass_Web.pdf"
)
AZ_PUBLISHER = "Arizona Secretary of State"
AZ_TITLE = "State of Arizona Official Canvass, 2022 General Election"
OREGON = {
    date(2022, 11, 8): (
        "https://web.archive.org/web/20230216221024id_/"
        "https://sos.oregon.gov/elections/Documents/results/november-general-2022.pdf"
    ),
    date(2024, 11, 5): "https://records.sos.state.or.us/ORSOSWebDrawer/Record/13735458/File/Document",
}
OR_PUBLISHER = "Oregon Secretary of State"
OR_TITLE = "{day:%B} {day.day}, {day.year}, General Election Abstract of Votes"
OR_OFFICES = {
    date(2022, 11, 8): {"Commissioner of the Bureau of Labor and Industries"},
    date(2024, 11, 5): {
        "Secretary of State",
        "State Treasurer",
        "Attorney General",
        "State Senator",
        "State Representative",
        *(f"Measure {number}" for number in range(115, 120)),
    },
}
OR_DISTRICTS = {"State Senator": {1, 5, 27, 29, 30}, "State Representative": {1, 9, 15, 16, 31, 32, 57, 58, 59}}
OR_CHAMBERS = {"State Senator": "senate", "State Representative": "house"}
AZ_COUNTIES = [f"040{code:02d}" for code in (1, 3, 5, 7, 9, 11, 12, 13, 15, 17, 19, 21, 23, 25, 27)]
SEATS = {
    "Corporation Commissioner": 2,
    "Justice of Supreme Court": 2,
    "State Board of Education": 2,
    "Regent of the University of Michigan": 2,
    "Trustee of Michigan State University": 2,
    "Governor of Wayne State University": 2,
}
NONPARTISAN = {"Justice of Supreme Court", "Commissioner of the Bureau of Labor and Industries"}
TOWNS = {
    "44001": ["Barrington", "Bristol", "Warren"],
    "44003": ["Coventry", "East Greenwich", "Warwick", "West Greenwich", "West Warwick"],
    "44005": ["Jamestown", "Little Compton", "Middletown", "Newport", "Portsmouth", "Tiverton"],
    "44007": [
        "Burrillville",
        "Central Falls",
        "Cranston",
        "Cumberland",
        "East Providence",
        "Foster",
        "Glocester",
        "Johnston",
        "Lincoln",
        "North Providence",
        "North Smithfield",
        "Pawtucket",
        "Providence",
        "Scituate",
        "Smithfield",
        "Woonsocket",
    ],
    "44009": [
        "Charlestown",
        "Exeter",
        "Hopkinton",
        "Narragansett",
        "New Shoreham",
        "North Kingstown",
        "Richmond",
        "South Kingstown",
        "Westerly",
    ],
}
OFFICES = {
    "GOVERNOR": ("Governor", "governor"),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", "lieutenant-governor"),
    "SECRETARY OF STATE": ("Secretary of State", "secretary-of-state"),
    "ATTORNEY GENERAL": ("Attorney General", "attorney-general"),
    "GENERAL TREASURER": ("General Treasurer", "treasurer"),
    "Governor": ("Governor", "governor"),
    "Secretary of State": ("Secretary of State", "secretary-of-state"),
    "Attorney General": ("Attorney General", "attorney-general"),
    "Auditor of Public Accounts": ("Auditor of Public Accounts", "auditor"),
    "State Treasurer": ("Treasurer", "treasurer"),
    "Commissioner of Agriculture": ("Commissioner of Agriculture", "commissioner-of-agriculture"),
    "Secretary Of State": ("Secretary of State", "secretary-of-state"),
    "Auditor Of State": ("Auditor of State", "auditor"),
    "Treasurer Of State": ("Treasurer of State", "treasurer"),
    "Superintendent of Public Instruction": (
        "Superintendent of Public Instruction",
        "superintendent-of-public-instruction",
    ),
    "State Mine Inspector": ("State Mine Inspector", "mine-inspector"),
    "Corporation Commissioner": ("Corporation Commissioner", "corporation-commissioner"),
    "Justice of Supreme Court": ("Justice of the Supreme Court", "justice-of-the-supreme-court"),
    "State Board of Education": ("Member of the State Board of Education", "member-of-the-state-board-of-education"),
    "Regent of the University of Michigan": (
        "Regent of the University of Michigan",
        "regent-of-the-university-of-michigan",
    ),
    "Trustee of Michigan State University": (
        "Trustee of Michigan State University",
        "trustee-of-michigan-state-university",
    ),
    "Governor of Wayne State University": ("Governor of Wayne State University", "governor-of-wayne-state-university"),
    "Commissioner of the Bureau of Labor and Industries": (
        "Commissioner of the Bureau of Labor and Industries",
        "commissioner-of-the-bureau-of-labor-and-industries",
    ),
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "MOD": "RI:moderate-party",
    "CM": "RI:cool-moose-party",
    "R": "republican-party",
    "D": "democratic-party",
    "L": "libertarian-party",
    "G": "green-party",
    "LIB": "libertarian-party",
    "LBT": "libertarian-party",
    "GRN": "green-party",
    "UST": "MI:u-s-taxpayers-party",
    "NLP": "natural-law-party",
    "WCP": "MI:working-class",
}
NAMED = {"RI:moderate-party": "Moderate Party of Rhode Island", "RI:cool-moose-party": "Cool Moose Party"}
CAVEATS = {
    "RI": (
        "Rhode Island's statewide totals run a few dozen votes above the sum of its town results because some ballots "
        "were reported only statewide. Those votes appear here as a statewide line with no county."
    ),
    "2022-11-08:AZ:attorney-general": (
        "These are the canvass totals certified on December 5, 2022. The automatic recount, whose results the "
        "Maricopa County Superior Court announced on December 29, cut Kris Mayes's lead over Abraham Hamadeh from 511 "
        "votes to 280 without changing the winner. The recount's county figures are not included here."
    ),
    "2022-11-08:AZ:superintendent-of-public-instruction": (
        "These are the canvass totals certified on December 5, 2022. The automatic recount, whose results the "
        "Maricopa County Superior Court announced on December 29, raised Tom Horne's lead over Kathy Hoffman from "
        "8,967 votes to 9,188 without changing the winner. The recount's county figures are not included here."
    ),
}


def _towns() -> list[str]:
    return [slug(town).replace("-", "_") for towns in TOWNS.values() for town in towns]


def fetch() -> None:
    for year in RI_DAYS:
        raw.fetch(SOURCE, RI_STATEWIDE.format(year=year), suffix=".json")
        for town in _towns():
            raw.fetch(SOURCE, RHODE_ISLAND.format(year=year, town=town), suffix=".json")
    raw.fetch(SOURCE, KENTUCKY, suffix=".pdf")
    for day, categories in IN_CATEGORIES.items():
        for category in categories:
            raw.fetch(SOURCE, INDIANA.format(year=day.year, category=category), suffix=".json")
    raw.fetch(SOURCE, MICHIGAN, suffix=".csv")
    raw.fetch(SOURCE, ARIZONA, suffix=".pdf")
    for url in OREGON.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, day in RI_DAYS.items():
        found: list[dict[str, object]] = []
        for fips, towns in TOWNS.items():
            for town in towns:
                data = json.loads(
                    raw.cached(
                        SOURCE, RHODE_ISLAND.format(year=year, town=slug(town).replace("-", "_"))
                    ).path.read_text()
                )
                for contest in data["contests"]:
                    if contest["name"] not in OFFICES:
                        continue
                    for candidate in contest["candidates"]:
                        found.append(
                            {
                                "state": "RI",
                                "day": day,
                                "office": contest["name"],
                                "candidate": candidate["name"],
                                "mate": None,
                                "party": candidate["party_code"],
                                "fips": fips,
                                "votes": int(candidate["votes"]),
                            }
                        )
        statewide = {
            (contest["name"], candidate["name"]): int(candidate["votes"])
            for contest in json.loads(raw.cached(SOURCE, RI_STATEWIDE.format(year=year)).path.read_text())["contests"]
            if contest["name"] in OFFICES
            for candidate in contest["candidates"]
        }
        summed: dict[tuple[str, str], int] = defaultdict(int)
        for row in found:
            summed[(str(row["office"]), str(row["candidate"]))] += int(str(row["votes"]))
        if set(summed) != set(statewide) or any(statewide[key] < votes for key, votes in summed.items()):
            raise ValueError("Rhode Island's towns add up to more than its statewide totals")
        rows.extend(found)
        for (office, candidate), votes in statewide.items():
            party = next(row["party"] for row in found if row["office"] == office and row["candidate"] == candidate)
            rows.append(
                {
                    "state": "RI",
                    "day": day,
                    "office": office,
                    "candidate": candidate,
                    "mate": None,
                    "party": party,
                    "fips": "RI:statewide",
                    "votes": votes - summed[(office, candidate)],
                }
            )
    with connect() as conn:
        fips = {
            slug(text).replace("-", ""): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        }
    office: str | None = None
    header: list[list[str]] = []
    columns: list[tuple[str, str | None, str]] = []
    tallied: list[int] = []
    layout = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, KENTUCKY).path), "-"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for previous, line in zip(["", *layout], layout, strict=False):
        text = line.strip()
        if previous.strip() == "For the office of":
            office, header, columns, tallied = (text if text in OFFICES else None), [], [], []
            continue
        if office is None or not text or text == office or text.startswith("Commonwealth of Kentucky"):
            continue
        found = re.fullmatch(r"([A-Z][A-Za-z .']*?)\s{2,}([\d,]+(?:\s+[\d,]+)*)", text)
        if found is None:
            if not columns:
                header.append(re.split(r"\s{2,}", text))
            continue
        if not columns:
            parties, *names = header
            if len(names) not in (2, 4) or any(len(row) != len(parties) for row in names):
                raise ValueError(f"Kentucky {office}: cannot read the header {header}")
            columns = [
                (
                    f"{names[0][index]} {names[1][index]}",
                    f"{names[2][index]} {names[3][index]}" if len(names) == 4 else None,
                    parties[index],
                )
                for index in range(len(parties))
            ]
            tallied = [0] * (len(columns) + 1)
        votes = [int(figure.replace(",", "")) for figure in found[2].split()]
        if len(votes) != len(columns):
            raise ValueError(f"Kentucky {office}: {found[1]} has {len(votes)} figures for {len(columns)} candidates")
        if found[1] == "Total Votes":
            if tallied != [*votes, 120]:
                raise ValueError(f"Kentucky {office}: the counties do not add up to the printed totals")
            office = None
            continue
        tallied[-1] += 1
        for index, ((candidate, mate, label), count) in enumerate(zip(columns, votes, strict=True)):
            tallied[index] += count
            rows.append(
                {
                    "state": "KY",
                    "day": date(2023, 11, 7),
                    "office": office,
                    "candidate": candidate,
                    "mate": mate,
                    "party": {"Democratic Party": "DEM", "Republican Party": "REP", "Write-In": None}[label],
                    "fips": fips[slug(found[1]).replace("-", "")],
                    "votes": count,
                    "write_in": label == "Write-In",
                }
            )
    for day, category in [(day, category) for day, categories in IN_CATEGORIES.items() for category in categories]:
        data = json.loads(
            raw.cached(SOURCE, INDIANA.format(year=day.year, category=category)).path.read_text(encoding="utf-8-sig")
        )
        summary = data["Root"]["StatewideSummary"]["Race"]
        counted: dict[str, int] = defaultdict(int)
        for region in data["Root"]["OfficeCategory"]["Regions"]["Region"]:
            for candidate in region["Races"]["Race"]["Candidates"]["Candidate"]:
                counted[candidate["NAME_ON_BALLOT"]] += int(candidate["TOTAL_VOTES"])
                rows.append(
                    {
                        "state": "IN",
                        "day": day,
                        "office": summary["OFFICE_TITLE"],
                        "candidate": candidate["NAME_ON_BALLOT"].removesuffix(" (W/I)"),
                        "mate": None,
                        "party": candidate["PARTY_ABBREV"],
                        "fips": region["MAP_FIPS"],
                        "votes": int(candidate["TOTAL_VOTES"]),
                        "write_in": candidate["NAME_ON_BALLOT"].endswith(" (W/I)"),
                        "winner": candidate["isWinner"] == "T",
                    }
                )
        if counted != {
            candidate["NAME_ON_BALLOT"]: int(candidate["TOTAL"]) for candidate in summary["Candidates"]["Candidate"]
        }:
            raise ValueError(f"Indiana's counties do not add up to the statewide {summary['OFFICE_TITLE']} totals")
    pdf = raw.cached(SOURCE, ARIZONA).path
    office: str | None = None
    line: dict[str, object] | None = None
    for page in range(1, 16):
        lines: list[list[tuple[str, scan.Box]]] = []
        for word, box in sorted(scan.layer(pdf, page), key=lambda item: (item[1][1], item[1][0])):
            if lines and abs(lines[-1][0][1][1] - box[1]) < 20:
                lines[-1].append((word, box))
            else:
                lines.append([(word, box)])
        for words in lines:
            words.sort(key=lambda item: item[1][0])
            figures = [(word, box) for word, box in words if re.fullmatch(r"[\d,]+|---", word)]
            text = html.unescape(" ".join(word for word, _ in words[: len(words) - len(figures)])).replace('""', '"')
            if line is not None and not figures and text.endswith("In)"):
                label = str(line["label"])
                line["label"] = label + ("" if label.endswith("-") or text.startswith("-") else " ") + text
                continue
            if line is not None:
                rows.extend(arizona(line, office))
                line = None
            if not figures:
                office = text if text in OFFICES else None
                continue
            if office is None or not text.startswith("("):
                continue
            if len(figures) != 16 or "---" in (word for word, _ in figures):
                raise ValueError(f"Arizona {office}: {text} has {len(figures)} figures")
            line = {"label": text, "figures": figures, "page": page}
    if line is not None:
        rows.extend(arizona(line, office))
    with connect() as conn:
        michigan_fips = openelections.counties(conn)["MI"]
        oregon_fips = {
            slug(text).replace("-", ""): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%'"""
            ).fetchall()
        }
    office = district = None
    oregon_header: list[str] = []
    for day, printed in (
        (day, printed)
        for day, url in OREGON.items()
        for printed in subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    ):
        text = printed.strip()
        if text in OR_OFFICES[day] or text and len(printed) - len(printed.lstrip()) >= 50:
            office, district, oregon_header, tallied = (text if text in OR_OFFICES[day] else None), None, [], []
            continue
        if office is None or not text:
            continue
        numbered = re.fullmatch(r"(\d+)(?:st|nd|rd|th) District", text)
        if numbered is not None and office in OR_DISTRICTS:
            district, oregon_header, tallied = int(numbered[1]), [], []
            continue
        if office in OR_DISTRICTS and district is None:
            continue
        found = re.fullmatch(r"([A-Z][A-Za-z ]*?)\s{2,}([\d,]+(?:\s+[\d,]+)*)", text)
        if found is None:
            if not tallied:
                oregon_header.append(text)
            continue
        question = office.startswith("Measure ")
        if len(oregon_header) != 2 and not question or not oregon_header[-1].startswith("County"):
            raise ValueError(f"Oregon {office} {district}: cannot read the header {oregon_header}")
        names, given = oregon_header[0], oregon_header[-1].removeprefix("County").strip()
        votes = [int(figure.replace(",", "")) for figure in found[2].split()]
        surnames = re.split(r"\s{2,}", names) if len(re.split(r"\s{2,}", names)) == len(votes) else names.split()
        firsts = re.findall(r"(\S.*?) \(([A-Z]+)\)", given) or [(first, None) for first in re.split(r"\s{2,}", given)]
        if question:
            surnames, firsts = re.split(r"\s{2,}", given), []
        if len(votes) != len(surnames) or not question and len(firsts) != len(surnames) - 1:
            raise ValueError(f"Oregon {office} {district}: cannot read {found[1]} under {oregon_header}")
        if not tallied:
            tallied = [0] * (len(votes) + 1)
        if found[1].upper() == "TOTAL":
            if tallied[:-1] != votes or district is None and tallied[-1] != 36:
                raise ValueError(f"Oregon {office} {district}: the counties do not add up to the printed totals")
            office = None if district is None else office
            continue
        tallied[-1] += 1
        for index, (surname, count) in enumerate(zip(surnames, votes, strict=True)):
            tallied[index] += count
            if district is not None and district not in OR_DISTRICTS[office]:
                continue
            first, label = firsts[index] if index < len(firsts) else (None, None)
            rows.append(
                {
                    "state": "OR",
                    "day": day,
                    "office": office,
                    "district": district,
                    "candidate": surname.lstrip("*")
                    if question
                    else "Write-ins"
                    if first is None
                    else f"{first} {surname.lstrip('*')}",
                    "mate": None,
                    "party": label,
                    "fips": oregon_fips[slug(found[1]).replace("-", "")],
                    "votes": count,
                    "write_in": surname == "Misc.",
                    "winner": surname.startswith("*"),
                }
            )
    michigan = (
        pl.read_csv(raw.cached(SOURCE, MICHIGAN).path, infer_schema_length=0)
        .filter(
            pl.col("office").is_in(
                [text for text in OFFICES if text in SEATS or text in ("Secretary of State", "Attorney General")]
            )
        )
        .with_columns(
            state=pl.lit("MI"),
            day=pl.lit(date(2022, 11, 8)),
            mate=pl.lit(None, pl.String),
            fips=pl.col("county")
            .str.to_lowercase()
            .str.replace(r"^saint ", "st ")
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(michigan_fips),
            votes=pl.col("votes").cast(pl.Int64),
            write_in=pl.col("party").is_null() & ~pl.col("office").is_in(list(NONPARTISAN)),
        )
    )
    if michigan.group_by("office", "candidate").agg(pl.col("fips").n_unique())["fips"].ne(83).any():
        raise ValueError("Michigan's county file is missing counties")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.concat(
        [
            pl.DataFrame(rows, schema_overrides={"mate": pl.String, "winner": pl.Boolean}, infer_schema_length=None),
            michigan.select("state", "day", "office", "candidate", "mate", "party", "fips", "votes", "write_in"),
        ],
        how="diagonal_relaxed",
    ).with_columns(
        pl.col("write_in").fill_null(False),
        seats=pl.col("office").replace_strict(SEATS, default=1, return_dtype=pl.Int64),
    )
    frame.write_parquet(PARSED / "lines.parquet")


def arizona(line: dict[str, object], office: str | None) -> list[dict[str, object]]:
    found = re.fullmatch(r"\((\w+)\) (.+?)( \(Write-In\))?( \*)?", str(line["label"]))
    if found is None or office is None:
        raise ValueError(f"Arizona: cannot read {line['label']}")
    figures = line["figures"]
    assert isinstance(figures, list)
    votes = [int(word.replace(",", "")) for word, _ in figures]
    if sum(votes[:15]) != votes[15]:
        raise ValueError(f"Arizona {office}: {found[2]}'s counties do not add up to {votes[15]}")
    return [
        {
            "state": "AZ",
            "day": date(2022, 11, 8),
            "office": office,
            "candidate": found[2],
            "mate": None,
            "party": found[1],
            "fips": fips,
            "votes": count,
            "write_in": found[3] is not None,
            "winner": found[4] is not None,
            "page": line["page"],
            "x0": box[0],
            "y0": box[1],
            "x1": box[2],
            "y1": box[3],
        }
        for fips, count, (_, box) in zip(AZ_COUNTIES, votes, figures, strict=False)
    ]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Rhode Island's 2010 and 2014, Indiana's 2018 and 2022, Michigan's, Arizona's, and Oregon's 2022, Kentucky's "
                "2023, and Oregon's 2024 statewide general elections and measures by county, and 14 of Oregon's 2024 "
                "legislative contests by county"
            ),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(r"\b([A-Z][A-Z'.-]+)\b", lambda match: match[1].title(), text)
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)

        boards = {}
        for year in RI_DAYS:
            boards[year] = cs.source(RI_PUBLISHER, RI_TITLE.format(year=year))
            cs.add(
                "source",
                id=boards[year],
                kind="official_body",
                publisher=RI_PUBLISHER,
                title=RI_TITLE.format(year=year),
                url=f"https://www.ri.gov/election/results/{year}/general_election/",
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, RI_STATEWIDE.format(year=year)), boards[year])
            for town in _towns():
                cs.artifact(raw.cached(SOURCE, RHODE_ISLAND.format(year=year, town=town)), boards[year])
        kentucky = cs.source(KY_PUBLISHER, KY_TITLE)
        cs.add(
            "source",
            id=kentucky,
            kind="official_body",
            publisher=KY_PUBLISHER,
            title=KY_TITLE,
            url=KENTUCKY,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, KENTUCKY), kentucky)
        divisions: dict[int, UUID] = {}
        for day, categories in IN_CATEGORIES.items():
            divisions[day.year] = cs.source(IN_PUBLISHER, IN_TITLE.format(year=day.year))
            cs.add(
                "source",
                id=divisions[day.year],
                kind="official_body",
                publisher=IN_PUBLISHER,
                title=IN_TITLE.format(year=day.year),
                url=f"https://enr.indianavoters.in.gov/archive/{day.year}General/",
                license="Public record",
            )
            for category in categories:
                cs.artifact(raw.cached(SOURCE, INDIANA.format(year=day.year, category=category)), divisions[day.year])
        michigan = cs.source(OE_PUBLISHER, MI_TITLE)
        cs.add(
            "source",
            id=michigan,
            kind="community",
            publisher=OE_PUBLISHER,
            title=MI_TITLE,
            url="https://github.com/openelections/openelections-data-mi",
            license="No license stated: facts published with attribution, raw files not redistributed",
        )
        cs.artifact(raw.cached(SOURCE, MICHIGAN), michigan)
        secretary = cs.source(AZ_PUBLISHER, AZ_TITLE)
        cs.add(
            "source",
            id=secretary,
            kind="official_body",
            publisher=AZ_PUBLISHER,
            title=AZ_TITLE,
            url="https://azsos.gov/sites/default/files/2022Dec05_General_Election_Canvass_Web.pdf",
            license="Public record",
        )
        canvass = cs.artifact(raw.cached(SOURCE, ARIZONA), secretary)
        pages: dict[int, tuple[UUID, int, int]] = {}
        for number in frame["page"].drop_nulls().unique().to_list():
            pdf = raw.cached(SOURCE, ARIZONA)
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            pages[number] = (cs.page(canvass, number), width * 3, height * 3)
            cs.add(
                "source_page",
                id=pages[number][0],
                artifact_id=canvass,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        oregon: dict[int, UUID] = {}
        for day, url in OREGON.items():
            oregon[day.year] = cs.source(OR_PUBLISHER, OR_TITLE.format(day=day))
            cs.add(
                "source",
                id=oregon[day.year],
                kind="official_body",
                publisher=OR_PUBLISHER,
                title=OR_TITLE.format(day=day),
                url=url.split("id_/")[-1],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), oregon[day.year])
        sources = {"RI": boards[2010], "KY": kentucky, "MI": michigan, "AZ": secretary}
        isos = [f"US-{state}" for state in (*sources, "IN", "OR")]
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code = any(%s)""",
                [isos],
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code = any(%s)
                   where u.scheme_code = 'us.state'""",
                [isos],
            ).fetchall()
        )
        county_areas: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select code, id, area_id from reporting_unit
                   where scheme_code = 'us.county' and left(code, 2) in ('21', '44', '18', '26', '04', '41')"""
            ).fetchall()
        }
        counties = {code: unit for code, (unit, _) in county_areas.items()}
        cs.add(
            "reporting_unit",
            id=cs.unit("us.aggregate", "RI:statewide"),
            polity_id=cs.polity("US-RI"),
            scheme_code="us.aggregate",
            code="RI:statewide",
            parent_id=cs.unit("us.state", "44"),
            area_relation="within",
            area_id=areas["RI"],
            kind="aggregate",
            valid_from=None,
            valid_to=None,
        )
        counties["RI:statewide"] = cs.unit("us.aggregate", "RI:statewide")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        existing: dict[str, tuple[list[tuple[UUID, set[str]]], list[tuple[object, ...]], UUID]] = {}
        for key, contest, district_area in conn.execute(
            """select e.value, e.entity_id, c.district_area_id from external_id e join contest c on c.id = e.entity_id
               where e.scheme = 'us.contest' and e.value like any(%s)""",
            [[f"{day}:{state}:%" for state, day in frame.select("state", "day").unique().iter_rows()]],
        ).fetchall():
            options = [
                (option, set(slug(label or "").split("-")))
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null",
                    [contest],
                ).fetchall()
            ]
            defaults = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            existing[key] = (options, defaults, district_area)
        people: dict[tuple[str, str], dict[UUID, int]] = defaultdict(lambda: defaultdict(int))
        for state, person, ballot in conn.execute(
            """select substr(p.iso_code, 4), cd.person_id, cd.ballot_name from candidacy cd
               join contest_option o on o.id = cd.option_id join contest ct on ct.id = o.contest_id
               join election e on e.id = ct.election_id join polity p on p.id = e.polity_id
               where p.iso_code = any(%s) and cd.ballot_name is not null""",
            [isos],
        ).fetchall():
            words = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', "", ballot)).split("-")
                if word not in ("jr", "sr", "ii", "iii", "iv") and len(word) > 1
            ]
            if len(words) > 1:
                people[(state, f"{words[0]} {words[-1]}")][person] += 1
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")
        if fptp in cs.minted or block in cs.minted:
            raise ValueError("the fptp and block systems must exist")
        persons: dict[tuple[str, str], UUID] = {}

        def person(state: str, text: str) -> UUID:
            if (state, text) in persons:
                return persons[(state, text)]
            words = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', "", text)).split("-")
                if word not in ("jr", "sr", "ii", "iii", "iv") and len(word) > 1
            ]
            found = people.get((state, f"{words[0]} {words[-1]}"), {})
            if found:
                persons[(state, text)] = max(found, key=lambda person: (found[person], str(person)))
            else:
                persons[(state, text)] = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                if persons[(state, text)] in cs.minted:
                    cs.add("person", id=persons[(state, text)])
                    name(persons[(state, text)], text)
            return persons[(state, text)]

        def party(label: str | None) -> UUID | None:
            key = PARTIES.get(label or "")
            if key is None:
                return None
            found = cs.key("us.party", key)
            if found in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{key.split(':')[0]}"))
                name(found, NAMED[key])
            return found

        for (state, day, office_text, district, seats), lines in frame.group_by(
            "state", "day", "office", "district", "seats", maintain_order=True
        ):
            state, office_text, seats = str(state), str(office_text), int(str(seats))
            assert isinstance(day, date)
            units = counties
            question = office_text.startswith("Measure ")
            contest_key = (
                f"{day}:{state}:{OR_CHAMBERS[office_text]}:{district}:g"
                if district is not None
                else next(
                    (
                        key
                        for key in existing
                        if key.startswith(f"{day}:{state}:question:medsl:state-{slug(office_text)}-")
                    ),
                    office_text,
                )
                if question
                else f"{day}:{state}:{OFFICES[office_text][1]}"
            )
            if (district is not None or question) and contest_key not in existing:
                raise ValueError(f"{contest_key}: no such contest")
            if district is not None:
                units = {}
                for fips in lines["fips"].unique().to_list():
                    part = f"{fips}:{day}:{OR_CHAMBERS[office_text]}:{district}"
                    units[fips] = cs.unit("us.county_part", part, on=day)
                    cs.add(
                        "reporting_unit",
                        id=units[fips],
                        polity_id=cs.polity(f"US-{state}"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=county_areas[fips][0],
                        area_relation="within",
                        area_id=county_areas[fips][1],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=units[fips],
                        area_id=existing[contest_key][2],
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            office, office_slug = OFFICES.get(office_text, (office_text, ""))
            election_key = f"{day}:{state}"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {states[state]} general election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-general", entity_id=election)
            office_key = f"{office_slug}:{state}"
            office_id = cs.key("us.office", office_key) if district is None and not question else None
            if office_id is not None and office_key not in offices_known:
                offices_known.add(office_key)
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="governor" if office_slug == "governor" else "other",
                )
                name(office_id, f"{office} of {states[state]}")
            contest = cs.key("us.contest", contest_key)
            options, defaults, _ = existing.get(contest_key, ([], [], None))
            if contest_key not in existing or state == "KY":
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=areas[state],
                    term="full",
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=fptp if seats == 1 else block,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} {states[state]} {office} election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-general/{office_slug}", entity_id=contest)
            first = cs.key(f"{SOURCE}.result_set", contest_key)
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:certified") if state == "KY" else first
            for row in defaults:
                if row[0] == result_set:
                    continue
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
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    )
                    | ({"status": "superseded"} if row[0] == first != result_set else {}),
                    is_default=False,
                )
                if row[0] == first != result_set:
                    for option, kind, seats in conn.execute(
                        "select option_id, kind, seats from outcome where result_set_id = %s", [first]
                    ).fetchall():
                        cs.add("outcome", result_set_id=first, option_id=option, kind=kind, seats=seats)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=divisions[day.year]
                if state == "IN"
                else oregon[day.year]
                if state == "OR"
                else boards[day.year]
                if state == "RI"
                else sources[state],
                is_default=True,
                supersedes_id=None if result_set == first else first,
                completeness=1,
            )
            for key in (state, contest_key):
                if key in CAVEATS:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEATS[key])
            if state == "KY":
                cs.add("note", entity_id=contest, kind="methodology", body_md=KY_METHODOLOGY)
            totals: dict[UUID, int] = {}
            declared: set[UUID] = set()
            for (candidate, mate, label, write_in), cells in lines.group_by(
                "candidate", "mate", "party", "write_in", maintain_order=True
            ):
                ballot = title(str(candidate)) if state in ("RI", "KY") else str(candidate)
                party_id = party(label)
                surname = [word for word in slug(ballot).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                option = next((option for option, words in options if surname in words), None)
                if option is None:
                    pooled = re.fullmatch(r"(?i)write-?ins?", ballot) is not None
                    head = person(state, ballot)
                    option = cs.key("us.option", f"{contest_key}:{head}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if write_in or pooled else "candidate",
                        party_id=party_id,
                        ballot_label="Write-ins" if pooled else ballot,
                    )
                    for role, text in (("head", ballot), ("running_mate", title(str(mate)) if mate else None)):
                        if text and not pooled:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{contest_key}:{head}:{role}"),
                                person_id=person(state, text),
                                option_id=option,
                                role=role,
                                ballot_name=text,
                                is_independent=label in ("IND", "NON")
                                or label is None
                                and office_text not in NONPARTISAN,
                            )
                totals[option] = totals.get(option, 0) + int(cells["votes"].sum())
                if cells["winner"].any():
                    declared.add(option)
                for fips, votes, page, x0, y0, x1, y1 in (
                    cells.group_by("fips", maintain_order=True)
                    .agg(
                        pl.col("votes").sum(), *(pl.col(column).first() for column in ("page", "x0", "y0", "x1", "y1"))
                    )
                    .iter_rows()
                ):
                    if result_set not in cs.minted:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[str(fips)],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=32 if fips == "RI:statewide" else 0,
                        value=votes,
                    )
                    if page is not None:
                        page_id, width, height = pages[page]
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=units[str(fips)],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            page_id=page_id,
                            source_id=None,
                            x0=x0 / width,
                            y0=y0 / height,
                            x1=x1 / width,
                            y1=y1 / height,
                        )
            elected = sorted(totals, key=totals.__getitem__, reverse=True)[:seats]
            if declared and declared != set(elected):
                raise ValueError(f"{contest_key}: the declared winners are not the leaders")
            for option in elected:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if question else "elected",
                    seats=None if question else 1,
                )
        cs.pool("A write-in line in the state canvass read as a candidate")
        return cs.write()
