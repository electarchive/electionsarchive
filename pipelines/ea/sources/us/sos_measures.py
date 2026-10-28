import gzip
import html
import json
import re
import subprocess
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.sos_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST, LAST = 2000, 2011
NCSL = "https://wabi-west-us-c-primary-api.analysis.windows.net/public/reports/querydata?synchronous=true"
NCSL_PAGE = "https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database"
NCSL_KEY = "b1042657-d1d3-4dc8-ad12-570a7dfc10b1"
NCSL_COLUMNS = (
    "StateName",
    "Year",
    "Election",
    "CombinedForID",
    "ID",
    "Title",
    "IRTypeDefinition",
    "IRStatusDefinition",
)
TX = "https://elections.sos.state.tx.us/"
TX_ELECTIONS = (85, 95, 103, 117, 132, 133, 147, 158)
CA = "https://elections.cdn.sos.ca.gov/sov/"
CA_FILES = {
    date(2000, 3, 7): "2000-primary/measures.pdf",
    date(2000, 11, 7): "2000-general/measures.pdf",
    date(2002, 3, 5): "2002-primary/measures.xls",
    date(2002, 11, 5): "2002-general/measures.xls",
    date(2003, 10, 7): "2003-special/measures.xls",
    date(2004, 3, 2): "2004-primary/measures.xls",
    date(2004, 11, 2): "2004-general/formatted_ballot_measures_detail.xls",
    date(2005, 11, 8): "2005-special/detail_props_formatted_pg1_7.xls",
    date(2006, 6, 6): "2006-primary/sov_detail_primary_props.xls",
    date(2006, 11, 7): "2006-general/measures.xls",
    date(2008, 2, 5): "2008-primary/ballotmeasures.xls",
    date(2008, 6, 3): "2008-statewide-direct-primary/bm08.xls",
    date(2008, 11, 4): "2008-general/57_65_ballot_measures.xls",
    date(2009, 5, 19): "2009-special/ssov/1-6-summary-by-county.xls",
    date(2010, 6, 8): "2010-primary/125-props.xls",
    date(2010, 11, 2): "2010-general/88-state-ballot-measures.xls",
}
AZ = "https://web.archive.org/web/2020id_/http://apps.azsos.gov/election/"
AZ_FILES = {
    date(2000, 11, 7): "2000/General/Canvass2000GE.pdf",
    date(2002, 11, 5): "2002/General/Canvass2002GE.pdf",
    date(2004, 11, 2): "2004/General/Canvass2004General.pdf",
    date(2006, 11, 7): "2006/General/Canvass2006GE.pdf",
    date(2008, 11, 4): "2008/General/Canvass2008GE.pdf",
    date(2010, 5, 18): "2010/may_Special/2010_Special_Election_Official_Canvass.pdf",
    date(2010, 11, 2): "2010/General/Canvass2010GE.pdf",
}
AZ_RECOUNTS = {(date(2010, 11, 2), "112"): "2010/GeneralRecount/Canvass2010Prop112.pdf"}
AZ_COUNTIES = (
    "Apache",
    "Cochise",
    "Coconino",
    "Gila",
    "Graham",
    "Greenlee",
    "La Paz",
    "Maricopa",
    "Mohave",
    "Navajo",
    "Pima",
    "Pinal",
    "Santa Cruz",
    "Yavapai",
    "Yuma",
)
AL = "https://web.archive.org/web/{stamp}id_/http://sos.alabama.gov/sites/default/files/election-data/2017-06/"
AL_FILES = {
    date(2000, 11, 7): ("20170706141913", "2000g-cty.exe"),
    date(2002, 11, 5): ("20170706141828", "2002GeneralElectionTotals.XLS"),
    date(2003, 9, 9): ("20170706141819", "20030909countylevel.xls"),
    date(2004, 11, 2): ("20170706141810", "2004ge-countylevel_0.xls"),
}
AL_COUNTIES = {
    "autauga",
    "baldwin",
    "barbour",
    "bibb",
    "blount",
    "bullock",
    "butler",
    "calhoun",
    "chambers",
    "cherokee",
    "chilton",
    "choctaw",
    "clarke",
    "clay",
    "cleburne",
    "coffee",
    "colbert",
    "conecuh",
    "coosa",
    "covington",
    "crenshaw",
    "cullman",
    "dale",
    "dallas",
    "dekalb",
    "elmore",
    "escambia",
    "etowah",
    "fayette",
    "franklin",
    "geneva",
    "greene",
    "hale",
    "henry",
    "houston",
    "jackson",
    "jefferson",
    "lamar",
    "lauderdale",
    "lawrence",
    "lee",
    "limestone",
    "lowndes",
    "macon",
    "madison",
    "marengo",
    "marion",
    "marshall",
    "mobile",
    "monroe",
    "montgomery",
    "morgan",
    "perry",
    "pickens",
    "pike",
    "randolph",
    "russell",
    "st. clair",
    "shelby",
    "sumter",
    "talladega",
    "tallapoosa",
    "tuscaloosa",
    "walker",
    "washington",
    "wilcox",
    "winston",
}
MISPRINTS = {("CA", date(2000, 11, 7), "38", "NO"): 3000}
STATES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}
PUBLISHERS = {
    "TX": ("Texas Secretary of State", "Texas election history: returns by county, 1992–2019", TX),
    "CA": (
        "California Secretary of State",
        "Statement of Vote",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "AL": ("Alabama Secretary of State", "Election results by county", "https://www.sos.alabama.gov/alabama-votes"),
    "AZ": ("Arizona Secretary of State", "State of Arizona Official Canvass", "https://azsos.gov/elections"),
}
TYPES = {
    "Initiative": "initiative",
    "Popular Referendum": "optional_referendum",
    "Legislative Referendum": "mandatory_referendum",
    "Constitutional Revision Commission": "mandatory_referendum",
}
METHODOLOGY = (
    "County returns from the {publisher}'s official results. The measure's number, title, type, and outcome are from "
    "NCSL's Statewide Ballot Measures Database."
)


def ncsl_query() -> dict[str, object]:
    return {
        "version": "1.0.0",
        "queries": [
            {
                "Query": {
                    "Commands": [
                        {
                            "SemanticQueryDataShapeCommand": {
                                "Query": {
                                    "Version": 2,
                                    "From": [{"Name": "a", "Entity": "All Years Table", "Type": 0}],
                                    "Select": [
                                        {
                                            "Column": {
                                                "Expression": {"SourceRef": {"Source": "a"}},
                                                "Property": column,
                                            },
                                            "Name": f"a.{column}",
                                        }
                                        for column in NCSL_COLUMNS
                                    ],
                                },
                                "Binding": {
                                    "Primary": {"Groupings": [{"Projections": list(range(len(NCSL_COLUMNS)))}]},
                                    "DataReduction": {"DataVolume": 3, "Primary": {"Window": {"Count": 30000}}},
                                    "Version": 1,
                                },
                            }
                        }
                    ]
                },
                "ApplicationContext": {
                    "DatasetId": "26a52b20-202c-4bf4-8184-f5ce3e49fc38",
                    "Sources": [{"ReportId": "fe633d7c-6c27-4488-9db3-a28c45966d68"}],
                },
            }
        ],
        "cancelQueries": [],
        "modelId": 1709732,
    }


def tx_races(election: int) -> list[tuple[int, str]]:
    text = raw.cached(SOURCE, f"{TX}elchist{election}_raceselect.htm").path.read_text(errors="replace")
    return [
        (int(race), title.strip())
        for race, title in re.findall(r"<OPTION value=\"?(\d+)\"?[^>]*>([^<\n]+)", text)
        if re.match(r"Prop\.? \d+ ", title.strip())
    ]


def fetch() -> None:
    try:
        raw.cached(SOURCE, NCSL_PAGE)
    except LookupError:
        response = httpx.post(
            NCSL,
            json=ncsl_query(),
            headers={"X-PowerBI-ResourceKey": NCSL_KEY, "Accept-Encoding": "gzip"},
            timeout=300,
        )
        response.raise_for_status()
        raw.store(
            SOURCE,
            NCSL_PAGE,
            response.content,
            suffix=".json",
            retrieved_at=datetime.now(UTC),
            media_type="application/json",
        )
    for election in TX_ELECTIONS:
        raw.fetch(SOURCE, f"{TX}elchist{election}_raceselect.htm", suffix=".htm")
        for race, _ in tx_races(election):
            url = f"{TX}elchist{election}_race{race}.htm"
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.fetch(SOURCE, url, suffix=".htm")
                time.sleep(1)
    for path in CA_FILES.values():
        raw.fetch(SOURCE, CA + path, suffix=Path(path).suffix)
        time.sleep(1)
    for stamp, file in AL_FILES.values():
        raw.fetch(SOURCE, AL.format(stamp=stamp) + file, suffix=Path(file).suffix.lower())
        time.sleep(1)
    for path in [*AZ_FILES.values(), *AZ_RECOUNTS.values()]:
        raw.fetch(SOURCE, AZ + path, suffix=".pdf")
        time.sleep(1)


def ncsl() -> pl.DataFrame:
    content = raw.cached(SOURCE, NCSL_PAGE).path.read_bytes()
    data = json.loads(gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content)
    shape = data["results"][0]["result"]["data"]["dsr"]["DS"][0]
    if shape.get("RT"):
        raise ValueError("NCSL returned a partial table")
    dictionaries = shape.get("ValueDicts", {})
    rows: list[dict[str, object]] = []
    previous: list[object] = [None] * len(NCSL_COLUMNS)
    schema: list[dict[str, object]] = []
    for block in shape["PH"]:
        for entries in block.values():
            for entry in entries:
                schema = entry.get("S", schema)
                values = iter(entry.get("C", []))
                row: list[object] = []
                for index in range(len(NCSL_COLUMNS)):
                    if entry.get("R", 0) >> index & 1:
                        value = previous[index]
                    elif entry.get("Ø", 0) >> index & 1:
                        value = None
                    else:
                        value = next(values)
                        name = schema[index].get("DN")
                        if name is not None and isinstance(value, int):
                            value = dictionaries[name][value]
                    row.append(value)
                previous = row
                rows.append(dict(zip(NCSL_COLUMNS, row, strict=True)))
    return (
        pl.DataFrame(rows, schema=dict.fromkeys(NCSL_COLUMNS, pl.String))
        .filter(
            pl.col("StateName").is_in(list(STATES)),
            pl.col("IRStatusDefinition").is_in(["Pass", "Fail"]),
            pl.col("Year").cast(pl.Int32).is_between(FIRST, LAST),
        )
        .unique("CombinedForID", keep="first", maintain_order=True)
        .select(
            state=pl.col("StateName").replace_strict(STATES),
            year=pl.col("Year").cast(pl.Int32),
            election=pl.col("Election"),
            measure=pl.col("ID").str.strip_chars(),
            number=pl.col("ID").str.extract(r"(\d+[A-Z]?)\s*$"),
            title=pl.col("Title").str.replace_all(r"\s+", " ").str.strip_chars(),
            type=pl.col("IRTypeDefinition"),
            passed=pl.col("IRStatusDefinition") == "Pass",
        )
    )


def cells(row: str, tag: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(rf"<{tag}[^>]*>(.*?)(?=<{tag}[\s>]|</TR>|$)", row, re.IGNORECASE | re.DOTALL)
    ]


def texas() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for election in TX_ELECTIONS:
        for race, title in tx_races(election):
            url = f"{TX}elchist{election}_race{race}.htm"
            text = raw.cached(SOURCE, url).path.read_text(errors="replace")
            heading = re.search(r"<H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
            if heading is None:
                raise ValueError(f"{url}: no date")
            day = date(int(heading[3]), int(heading[1]), int(heading[2]))
            lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
            labels = next(cells(line, "TH") for line in lines if re.search(r">IN FAVOR<", line, re.IGNORECASE))
            labels = [label.upper() for label in labels]
            columns = {labels.index("IN FAVOR"): "YES", labels.index("AGAINST"): "NO"}
            data = [cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
            total = next(row for row in data if row[0] == "ALL COUNTIES")
            counties = [row for row in data if row[0] != "ALL COUNTIES"]
            if len(counties) != 254:
                raise ValueError(f"{url}: {len(counties)} counties")
            match = re.match(r"Prop\.? (\d+) (.+)", title)
            assert match is not None
            for column, answer in columns.items():
                votes = [int(row[column].replace(",", "") or 0) for row in counties]
                if sum(votes) != int(total[column].replace(",", "")):
                    raise ValueError(f"{url} {answer}: counties sum to {sum(votes)}, not {total[column]}")
                rows.extend(
                    {
                        "state": "TX",
                        "day": day,
                        "number": match[1],
                        "label": match[2].strip(),
                        "url": url,
                        "county": row[0],
                        "answer": answer,
                        "votes": value,
                    }
                    for row, value in zip(counties, votes, strict=True)
                )
    return rows


def crosstab(
    book: fastexcel.ExcelReader,
    heading: re.Pattern[str],
    names: list[str],
    counties: set[str],
    votes: dict[tuple[str, str], dict[str, int]],
    totals: dict[tuple[str, str], int],
) -> None:
    for sheet in book.sheet_names:
        heads: dict[int, str | None] = {}
        labels: dict[int, tuple[str, str]] = {}
        for row in book.load_sheet(sheet, header_row=None).to_polars().rows():
            cells = [
                str(int(value)) if isinstance(value, float) and value.is_integer() else str(value or "").strip()
                for value in row
            ]
            found = {
                index: match[1] if (match := heading.search(cell)) else None
                for index, cell in enumerate(cells)
                if heading.search(cell) or re.search(r"(?i)\b(amendment|proposition)\b", cell)
            }
            if found:
                heads, labels = found, {}
                continue
            answers = {
                index: "YES" if cell.lower() in ("for", "yes") else "NO"
                for index, cell in enumerate(cells)
                if cell.lower() in ("for", "yes", "against", "no")
            }
            if answers and heads:
                ordered = sorted(heads)
                labels = {}
                for position, head in enumerate(ordered):
                    end = ordered[position + 1] if position + 1 < len(ordered) else len(cells)
                    for answer in ("YES", "NO"):
                        index = next((i for i in sorted(answers) if head <= i < end and answers[i] == answer), None)
                        prop = heads[head]
                        if index is not None and prop is not None:
                            labels[index] = (prop, answer)
                continue
            name = re.sub(r"\s+", " ", next((cell for cell in cells if cell and not cell.isdigit()), "").lower())
            if not labels or name not in names:
                continue
            for index, key in labels.items():
                value = cells[index].replace(",", "")
                if not re.fullmatch(r"\d+", value):
                    continue
                if name in counties:
                    votes.setdefault(key, {})[name] = int(value)
                else:
                    totals[key] = int(value)


def california() -> list[dict[str, object]]:
    counties = {
        "alameda",
        "alpine",
        "amador",
        "butte",
        "calaveras",
        "colusa",
        "contra costa",
        "del norte",
        "el dorado",
        "fresno",
        "glenn",
        "humboldt",
        "imperial",
        "inyo",
        "kern",
        "kings",
        "lake",
        "lassen",
        "los angeles",
        "madera",
        "marin",
        "mariposa",
        "mendocino",
        "merced",
        "modoc",
        "mono",
        "monterey",
        "napa",
        "nevada",
        "orange",
        "placer",
        "plumas",
        "riverside",
        "sacramento",
        "san benito",
        "san bernardino",
        "san diego",
        "san francisco",
        "san joaquin",
        "san luis obispo",
        "san mateo",
        "santa barbara",
        "santa clara",
        "santa cruz",
        "shasta",
        "sierra",
        "siskiyou",
        "solano",
        "sonoma",
        "stanislaus",
        "sutter",
        "tehama",
        "trinity",
        "tulare",
        "tuolumne",
        "ventura",
        "yolo",
        "yuba",
    }
    names = sorted(counties | {"state totals", "state total", "totals", "total"}, key=len, reverse=True)
    heading = re.compile(r"Proposition\s+(?:No\.\s*)?(\d+[A-Z]?)")
    rows: list[dict[str, object]] = []
    for day, path in CA_FILES.items():
        url = CA + path
        file = raw.cached(SOURCE, url).path
        votes: dict[tuple[str, str], dict[str, int]] = {}
        totals: dict[tuple[str, str], int] = {}
        if path.endswith(".pdf"):
            text = subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True)
            props: list[str] = []
            pending = None
            for line in text.stdout.splitlines():
                if found := heading.findall(line):
                    props = found
                    continue
                low = re.sub(r"\s+", " ", line.strip().lower())
                name = next((name for name in names if low.startswith(name)), None)
                if name is None and pending and re.fullmatch(r"[-\d, ]+", low):
                    name, low = pending, f"{pending} {low}"
                pending = None
                if name is None or not props:
                    continue
                rest = low[len(name) :]
                if not rest.strip():
                    pending = name
                    continue
                if not re.fullmatch(r"[-\d, ]+", rest):
                    continue
                numbers = [int(value.replace(",", "")) for value in rest.split()]
                if len(numbers) != 3 * len(props):
                    raise ValueError(f"{url} {name}: {len(numbers)} figures for {props}")
                for index, prop in enumerate(props):
                    for offset, answer in enumerate(("YES", "NO")):
                        value = numbers[3 * index + offset]
                        if name in counties:
                            votes.setdefault((prop, answer), {})[name] = value
                        else:
                            totals[(prop, answer)] = value
        else:
            crosstab(fastexcel.read_excel(file), heading, names, counties, votes, totals)
        if {(prop, answer) for prop, _ in votes for answer in ("YES", "NO")} != set(votes):
            raise ValueError(f"{url}: a proposition lacks a yes or no column")
        for (prop, answer), found in votes.items():
            if len(found) != len(counties):
                raise ValueError(f"{url} {prop} {answer}: {len(found)} counties")
            gap = sum(found.values()) - totals[(prop, answer)]
            if gap != MISPRINTS.get(("CA", day, prop, answer), 0):
                raise ValueError(f"{url} {prop} {answer}: counties sum {gap:+} off the state total")
            rows.extend(
                {
                    "state": "CA",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in found.items()
            )
    return rows


def alabama() -> list[dict[str, object]]:
    names = sorted(AL_COUNTIES | {"state total", "state totals", "total", "totals"}, key=len, reverse=True)
    heading = re.compile(r"^(?:Statewide\s+)?Amendment\s*(?:No\.|#)?\s*(\d+)\s*$", re.IGNORECASE)
    rows: list[dict[str, object]] = []
    for day, (stamp, file) in AL_FILES.items():
        url = AL.format(stamp=stamp) + file
        path = raw.cached(SOURCE, url).path
        votes: dict[tuple[str, str], dict[str, int]] = {}
        totals: dict[tuple[str, str], int] = {}
        if file.endswith(".exe"):
            crosstab(
                fastexcel.read_excel(zipfile.ZipFile(path).read("2000g-amend.xls")),
                heading,
                names,
                AL_COUNTIES,
                votes,
                totals,
            )
        elif day.year == 2002:
            columns: dict[int, str] = {}
            for row in fastexcel.read_excel(path).load_sheet("MASTER", header_row=None).to_polars().rows():
                cells = [
                    str(int(value)) if isinstance(value, float) and value.is_integer() else str(value or "").strip()
                    for value in row
                ]
                if "Autauga" in cells:
                    columns = {index: cell.lower() for index, cell in enumerate(cells) if cell.lower() in AL_COUNTIES}
                    continue
                match = re.fullmatch(r"Statewide Amendment #(\d+)", cells[1]) if len(cells) > 4 else None
                if match is None or cells[3] not in ("YES", "NO"):
                    continue
                key = (match[1], cells[3])
                votes[key] = {county: int(cells[index]) for index, county in columns.items()}
                totals[key] = int(cells[4])
        else:
            crosstab(fastexcel.read_excel(path), heading, names, AL_COUNTIES, votes, totals)
        if {(prop, answer) for prop, _ in votes for answer in ("YES", "NO")} != set(votes):
            raise ValueError(f"{url}: an amendment lacks a yes or no column")
        for (prop, answer), found in votes.items():
            if len(found) != len(AL_COUNTIES) or sum(found.values()) != totals[(prop, answer)]:
                raise ValueError(f"{url} {prop} {answer}: {len(found)} counties, {sum(found.values())} votes")
            rows.extend(
                {
                    "state": "AL",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": answer,
                    "votes": value,
                }
                for county, value in found.items()
            )
    return rows


def arizona() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    recounted = {(day, prop): AZ + path for (day, prop), path in AZ_RECOUNTS.items()}
    for day, path in [*AZ_FILES.items(), *((day, path) for (day, _), path in AZ_RECOUNTS.items())]:
        url = AZ + path
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        )
        prop = None
        for line in text.stdout.splitlines():
            if match := re.match(r"\s*PROPOSITION\s+(\d+)\b", line):
                prop = match[1]
                continue
            match = re.match(r"\s*(YES|NO)\s*\*?\s+([\d,]+(?:\s+[\d,]+)*)\s*$", line)
            if match is None or prop is None or recounted.get((day, prop), url) != url:
                continue
            numbers = [int(value.replace(",", "")) for value in match[2].split()]
            if len(numbers) != len(AZ_COUNTIES) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"{url} {prop} {match[1]}: {numbers}")
            rows.extend(
                {
                    "state": "AZ",
                    "day": day,
                    "number": prop,
                    "label": "",
                    "url": url,
                    "county": county,
                    "answer": match[1],
                    "votes": value,
                }
                for county, value in zip(AZ_COUNTIES, numbers, strict=False)
            )
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    listed = ncsl()
    frame = pl.DataFrame([*texas(), *california(), *arizona(), *alabama()])
    words = re.compile(r"[a-z]{4,}")
    matched: list[dict[str, object]] = []
    for (state, day, number, label), _ in frame.group_by("state", "day", "number", "label", maintain_order=True):
        mine = set(words.findall(label.lower()))
        found = listed.filter(pl.col("state") == state, pl.col("year") == day.year, pl.col("number") == number)
        if found.height == 0:
            found = listed.filter(pl.col("state") == state, pl.col("year") == day.year, pl.col("number").is_null())
        if found.height != 1 or found["number"][0] is None:
            found = (
                found.with_columns(
                    overlap=pl.col("title").map_elements(
                        lambda title, mine=mine: len(mine & set(words.findall(title.lower()))), return_dtype=pl.Int64
                    )
                )
                .filter(pl.col("overlap") > 0)
                .sort("overlap", descending=True)
            )
            if found.height == 0 or found.height > 1 and found["overlap"][0] == found["overlap"][1]:
                raise ValueError(f"{state} {day} {number} {label!r}: {found.height} NCSL rows")
        entry = found.row(0, named=True)
        matched.append(
            {
                "state": state,
                "day": day,
                "number": number,
                "measure": entry["measure"] if entry["number"] else f"Proposition {number}",
                "title": entry["title"],
                "type": entry["type"],
                "passed": entry["passed"],
            }
        )
    frame.write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(matched).write_parquet(PARSED / "measures.parquet")
    print(f"{len(matched)} measures, {frame.height} county rows")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    measures = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot measures by county, 2000–2011, from state election offices' official returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ncsl_source = cs.source("National Conference of State Legislatures", "Statewide Ballot Measures Database")
        cs.add(
            "source",
            id=ncsl_source,
            kind="aggregator",
            publisher="National Conference of State Legislatures",
            title="Statewide Ballot Measures Database",
            url=NCSL_PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, NCSL_PAGE), ncsl_source)
        sources: dict[str, UUID] = {}
        for state in frame["state"].unique(maintain_order=True):
            publisher, title, url = PUBLISHERS[state]
            sources[state] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public record",
            )
        for state, url in frame.select("state", "url").unique(maintain_order=True).iter_rows():
            cs.artifact(raw.cached(SOURCE, url), sources[state])
        system = cs.key("us.electoral_system", "referendum")
        states: dict[str, tuple[UUID, str]] = {}
        fips: dict[str, str] = {}
        for abbreviation, area, code, text in conn.execute(
            """select substr(p.iso_code, 4), u.area_id, u.code, n.text from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.state'"""
        ).fetchall():
            states[abbreviation] = (area, text)
            fips[abbreviation] = code
        counties: dict[tuple[str, str], str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and (u.valid_from is null or u.valid_from <= '2011-12-31')
               and (u.valid_to is null or u.valid_to > '2000-01-01')"""
        ).fetchall():
            counties[(code[:2], re.sub(r"[^a-z]", "", text.lower()))] = code
        frame = frame.with_columns(
            code=pl.struct("state", "county").map_elements(
                lambda row: counties.get((fips[row["state"]], re.sub(r"[^a-z]", "", row["county"].lower()))),
                return_dtype=pl.String,
            )
        )
        missing = frame.filter(pl.col("code").is_null()).select("state", "county").unique()
        if missing.height:
            raise ValueError(f"unmatched counties: {missing.rows()}")
        elections = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and e.date_from between '2000-01-01' and '2011-12-31'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election' and x.value ~ '^20(0[0-9]|1[01])-'"""
            ).fetchall()
        )
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        for entry in measures.iter_rows(named=True):
            state, day = entry["state"], entry["day"]
            rows = frame.filter(pl.col("state") == state, pl.col("day") == day, pl.col("number") == entry["number"])
            election_key = next(
                (
                    key
                    for key in (f"{day}:{state}:special", f"{day}:{state}", f"{day}:{state}:primary")
                    if key in elections
                ),
                f"{day}:{state}:referendum",
            )
            election = cs.key("us.election", election_key)
            if election_key not in elections:
                elections[election_key] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="referendum",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{states[state][1]} referendum of {day:%B} {day.day}, {day.year}")
                paths[election_key] = f"us/{state.lower()}/{day}-referendum"
                cs.add("slug", path=paths[election_key], entity_id=election)
            title = f"{states[state][1]} {entry['measure']}, {entry['title']}"
            contest_key = f"{day}:{state}:question:{slug(entry['measure'])}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=states[state][0],
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add(
                "ballot_question",
                contest_id=contest,
                type=TYPES.get(entry["type"], "plebiscite"),
            )
            name(contest, title)
            if contest in cs.minted:
                path = held.get(contest)
                if path is None:
                    base = f"{paths[election_key]}/{slug(title)[:60].rstrip('-') or 'question'}"
                    path, n = base, 1
                    while path in taken:
                        n += 1
                        path = f"{base}-{n}"
                    taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            options: dict[str, UUID] = {}
            for position, answer in enumerate(("YES", "NO")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
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
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHODOLOGY.format(publisher=PUBLISHERS[state][0]),
            )
            yes = int(rows.filter(pl.col("answer") == "YES")["votes"].sum())
            no = int(rows.filter(pl.col("answer") == "NO")["votes"].sum())
            if entry["passed"] != (yes > no):
                print(f"{contest_key}: NCSL says {'passed' if entry['passed'] else 'failed'} at {yes} to {no}")
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options["YES" if entry["passed"] else "NO"],
                kind="won",
            )
            if result_set in cs.minted:
                for code, answer, votes in rows.select("code", "answer", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=options[answer],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        return cs.write()
