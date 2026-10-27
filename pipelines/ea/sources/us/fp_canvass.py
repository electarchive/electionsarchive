import csv
import html
import io
import re
import subprocess
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl
import shapefile
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ar_abstract_2002, ks_sos_generals, medsl
from ea.store import DATA, connect

SOURCE = "us.fp_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOCUMENTS = {
    "AR": (
        ar_abstract_2002.SOURCE,
        ar_abstract_2002.URL,
        ar_abstract_2002.PUBLISHER,
        ar_abstract_2002.TITLE,
        "https://sos.arkansas.gov/elections/research/election-results",
    ),
    "CA2000": (
        SOURCE,
        "https://elections.cdn.sos.ca.gov/sov/2000-general/us-sen.pdf",
        "California Secretary of State",
        "Statement of Vote, November 7, 2000 General Election",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    **{
        f"IN2000{office[0]}": (
            SOURCE,
            f"https://web.archive.org/web/{stamp}id_/http://www.state.in.us:80/serv/sos_ge2000v?act=district&district=Statewide&office={office}",
            "Indiana Secretary of State",
            "2000 General Election Results",
            "http://www.state.in.us/serv/sos_ge2000v",
        )
        for office, stamp in (("US+Senator", "20020905180654"), ("Governor", "20021013053937"))
    },
    "DE2006": (
        "us.de_doe",
        "https://elections.delaware.gov/elections/resultsarchive/elect06/elect06_general/elect06_general.edrd.txt",
        "Delaware Department of Elections",
        "Election results archive",
        "https://elections.delaware.gov/elections/resultsarchive/",
    ),
    "NM2002": (
        SOURCE,
        "https://nm.elstats.civera.com/api/download_contest/1169_table.csv?split_party=false",
        "New Mexico Secretary of State",
        "ElectionStats",
        "https://electionstats.sos.nm.gov/contest/1169",
    ),
    "KS2002": (
        ks_sos_generals.SOURCE,
        ks_sos_generals.url((date(2002, 11, 5), "governor")),
        ks_sos_generals.PUBLISHER,
        ks_sos_generals.TITLE,
        ks_sos_generals.LANDING,
    ),
    "SC2010": (
        SOURCE,
        "https://www.enr-scvotes.org/SC/19077/40477/reports/detailxml.zip",
        "South Carolina State Election Commission",
        "Election night reporting results",
        "https://www.enr-scvotes.org/SC/19077/40477/en/summary.html",
    ),
    "OH2010": (
        SOURCE,
        "https://web.archive.org/web/20191120093740id_/https://www.ohiosos.gov/elections/election-results-and-data/2010-elections-results/u.s.-senator-november-2-2010/",
        "Ohio Secretary of State",
        "Election results and data",
        "https://www.ohiosos.gov/elections/election-results-and-data/",
    ),
    "GA2000": (
        SOURCE,
        "https://web.archive.org/web/2008id_/http://sos.georgia.gov/elections/election_results/2000_1107/0000200.htm",
        "Georgia Secretary of State",
        "Georgia Election Results: official results of the general election",
        "https://sos.ga.gov/page/historical-elections-results",
    ),
    "VI2024": (
        SOURCE,
        "https://usvi.elstats.civera.com/api/download_contest/39_table.csv?split_party=false",
        "Election System of the Virgin Islands",
        "Election archive",
        "https://electionarchive.vivote.gov/contest/39",
    ),
    "VI2024S": (
        SOURCE,
        "https://vivote.gov/wp-content/uploads/2024/11/Official-Territorial-Election-Summary-11.15.2024.pdf",
        "Election System of the Virgin Islands",
        "Territorial election summary results report, official results, November 5, 2024",
        "https://vivote.gov/",
    ),
    "IA2002": (
        SOURCE,
        "https://sos.iowa.gov/elections/pdf/2002/results/GovernorCanvass.pdf",
        "Iowa Secretary of State",
        "2002 General Election canvass summary: Governor/Lieutenant Governor",
        "https://sos.iowa.gov/elections/results/",
    ),
    "VI2018R": (
        SOURCE,
        "https://usvi.elstats.civera.com/api/download_contest/54_table.csv?split_party=false",
        "Election System of the Virgin Islands",
        "Election archive",
        "https://electionarchive.vivote.gov/contest/54",
    ),
    "IN2022": (
        SOURCE,
        "https://election.lab.ufl.edu/data-downloads/resultsdata/2022/precinct/in_2022.zip",
        "Voting and Election Science Team",
        "2022 Precinct-Level Election Results: Indiana",
        "https://election.lab.ufl.edu/dataset/in-2022-precinct-level-election-results/",
    ),
    "ME2014": (
        SOURCE,
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2014/20141104/elec_20141104aj.xlsx",
        "Maine Department of the Secretary of State",
        "Election results: general election tabulations by municipality",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2014/20141104/",
    ),
}


def fetch() -> None:
    try:
        raw.cached(SOURCE, DOCUMENTS["IA2002"][1])
    except LookupError:
        raw.store(
            SOURCE,
            DOCUMENTS["IA2002"][1],
            (DATA / "inbox" / "manual" / "MF-142" / "GovernorCanvass.pdf").read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 27, tzinfo=UTC),
            media_type="application/pdf",
        )
    for document, (cached, url, *_) in DOCUMENTS.items():
        if cached == SOURCE and document != "IA2002":
            raw.fetch(SOURCE, url, suffix=Path(url.split("?")[0]).suffix)


def counties(conn: Connection, fips: str) -> dict[str, str]:
    return {
        re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
        for text, code in conn.execute(
            """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like %s""",
            [f"{fips}%"],
        ).fetchall()
    }


def arkansas(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "05")
    text = pdf("AR")
    line = re.compile(r"^(?:Governor )?(.+?) - (Democrat|Republican|Write-In)\s+([\d,]+)\s+[\d.]+%$")
    county = None
    statewide: dict[str, int] = {}
    rows = []
    reading = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if heading := re.fullmatch(r"Governor(?: \((.+?) County\))?", stripped):
            reading, county = True, heading[1]
            continue
        if not reading:
            continue
        if match := line.match(stripped):
            name = "WRITE-INS" if match[2] == "Write-In" else match[1].upper()
            votes = int(match[3].replace(",", ""))
            if county is None:
                statewide[name] = statewide.get(name, 0) + votes
            else:
                rows.append((date(2002, 11, 5), "governor", codes[re.sub(r"[^a-z]", "", county.lower())], name, votes))
            continue
        if not stripped.startswith(("Over", "Under")):
            reading = False
    for name, total in statewide.items():
        summed = sum(votes for *_, candidate, votes in rows if candidate == name)
        if summed != total:
            raise ValueError(f"AR governor {name}: counties sum to {summed}, statewide {total}")
    if len({code for _, _, code, _, _ in rows}) != 75:
        raise ValueError("AR governor: not 75 counties")
    return rows


def california(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "06")
    names = ["FEINSTEIN", "CAMPBELL", "TEMPLIN", "BENJAMIN", "LIGHTFOOT", "REES", "CAMAHORT", "JONES"]
    text = pdf("CA2000")
    rows = []
    totals = None
    for line in text.splitlines():
        if not (match := re.fullmatch(r"(\S.*?)\s{2,}((?:[\d,]+\s+){7}[\d,]+)", line.strip())):
            continue
        votes = [int(value.replace(",", "")) for value in match[2].split()]
        if match[1] == "State Totals":
            totals = votes
            continue
        code = codes[re.sub(r"[^a-z]", "", match[1].lower())]
        rows += [(date(2000, 11, 7), "senate", code, name, value) for name, value in zip(names, votes, strict=True)]
    if totals is None or len(rows) != 58 * 8:
        raise ValueError("CA Senate: missing totals or counties")
    for name, total in zip(names, totals, strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != total:
            raise ValueError(f"CA Senate {name}: counties sum to {summed}, statewide {total}")
    return rows


def maine(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    towns: dict[str, list[str]] = {}
    for code, text in conn.execute(
        """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
           where u.scheme_code = 'us.town' and u.code like '23%%'"""
    ).fetchall():
        towns.setdefault(re.sub(r"[^a-z]", "", text.removesuffix(", ME").lower()), []).append(code)
    codes = counties(conn, "23")
    source, url, *_ = DOCUMENTS["ME2014"]
    cells = pl.read_excel(raw.cached(source, url).path, has_header=False, infer_schema_length=0).rows()
    names = ["BELLOWS", "COLLINS", "WRITE-INS", "CHRISTOPHER A. LYONS", "DERIL V. STUBENROD"]
    rows: list[tuple[date, str, str, str, int]] = []
    pending: list[tuple[str, list[int]]] = []
    totals = None
    for row in cells[3:]:
        if row[0] is None:
            continue
        label = re.sub(r"\s+", " ", row[0]).strip()
        votes = [int(value or 0) for value in row[1:6]]
        if label == "Statewide Totals":
            totals = votes
            continue
        if label.endswith("County Totals"):
            county = codes[re.sub(r"[^a-z]", "", label.removesuffix(" County Totals").lower())]
            for place, counted in pending:
                key = re.sub(r"^saint", "st", re.sub(r"[^a-z]", "", place.lower()))
                found = {
                    code
                    for code in towns.get(key, []) + towns.get(key.removesuffix("plt") + "plantation", [])
                    if code.startswith(county)
                }
                code = found.pop() if len(found) == 1 else f"2014:ME:{county}:{place}"
                rows += [
                    (date(2014, 11, 4), "senate", code, name, value) for name, value in zip(names, counted, strict=True)
                ]
            pending = []
            continue
        if label in ("STATE UOCAVA", "EMERGENCY UTILITY WORKERS"):
            rows += [
                (date(2014, 11, 4), "senate", f"2014:ME:{label}", name, value)
                for name, value in zip(names, votes, strict=True)
            ]
            continue
        pending.append((label, votes))
    if totals is None or pending:
        raise ValueError("ME Senate: missing totals or a town after the last county")
    for index, name in enumerate(names[:2]):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != totals[index]:
            raise ValueError(f"ME Senate {name}: towns sum to {summed}, statewide {totals[index]}")
    if sum(r[4] for r in rows) != sum(totals):
        raise ValueError("ME Senate: write-ins do not tie")
    return rows


def indiana(conn: Connection, document: str) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "18")
    rows = []
    chamber = "senate" if document == "IN2000U" else "governor"
    source, url, *_ = DOCUMENTS[document]
    text = raw.cached(source, url).path.read_text("latin-1")
    heads = [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
        for cell in re.findall(r"<th[^>]*>(.*?)</th>", text, re.DOTALL | re.IGNORECASE)
    ]
    names = [re.sub(r"\s*\(.*", "", head).upper() for head in heads[1:4]]
    totals = [int(value) for value in heads[7:10]]
    found = re.findall(
        r'county=([^"&]+)"[^>]*>[^<]*</a>\s*</td>((?:\s*<td[^>]*>[^<]*</td>)+)', text, re.DOTALL | re.IGNORECASE
    )
    if len(found) != 92:
        raise ValueError(f"IN {chamber}: {len(found)} counties")
    for county, cells in found:
        votes = [int(value) for value in re.findall(r">(\d+)", cells)[:3]]
        code = codes[re.sub(r"[^a-z]", "", html.unescape(county).replace("+", " ").lower())]
        rows += [(date(2000, 11, 7), chamber, code, name, value) for name, value in zip(names, votes, strict=True)]
    for name, total in zip(names, totals, strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name and r[1] == chamber)) != total:
            raise ValueError(f"IN {chamber} {name}: counties sum to {summed}, statewide {total}")
    return rows


def delaware(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    units = {
        code.rsplit("::", 1)[1]: code
        for (code,) in conn.execute(
            "select code from reporting_unit where scheme_code = 'us.precinct' and code like '2006:DE:%%'"
        ).fetchall()
    }
    source, url, *_ = DOCUMENTS["DE2006"]
    rows: list[tuple[date, str, str, str, int]] = []
    names: list[str] = []
    reading = False
    for line in raw.cached(source, url).path.read_text("latin-1").splitlines():
        if line.strip():
            reading = (
                line.strip() == "UNITED STATES SENATOR"
                or (reading and not line[0].isalpha())
                or (reading and line.startswith(("District", "RD Tot")))
            )
        if not reading:
            continue
        if line.startswith("District"):
            names = re.findall(r"([A-Z]+) [A-Z](?: [A-Z])?\b", line.removeprefix("District").replace("Total", ""))
            continue
        if found := re.fullmatch(r"(\d\d-\d\d)\s+([\d\s]+)", line.strip()):
            votes = [int(value) for value in found[2].split()]
            totals = votes[3::3]
            if len(totals) != len(names) or sum(totals) != votes[0]:
                raise ValueError(f"DE Senate {found[1]}: {votes}")
            rows += [
                (date(2006, 11, 7), "senate", units[found[1]], name, value)
                for name, value in zip(names, totals, strict=True)
            ]
    if len(rows) != 436 * 3:
        raise ValueError(f"DE Senate: {len(rows)} rows")
    return rows


def elstats(
    conn: Connection, document: str, fips: str, day: date, chamber: str
) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, fips)
    source, url, *_ = DOCUMENTS[document]
    lines = list(csv.reader(io.StringIO(raw.cached(source, url).path.read_text())))
    names = [name.upper() for name in lines[0][2:] if name and not name.startswith("Total")]
    rows = [
        (day, chamber, codes[re.sub(r"[^a-z]", "", line[1].lower())], name, int(value))
        for line in lines[2:]
        if line[0] == "County"
        for name, value in zip(names, line[2:], strict=False)
    ]
    state = next(line for line in lines if line[0] == "State")
    for name, total in zip(names, state[2:], strict=False):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != int(total):
            raise ValueError(f"{document} {name}: counties sum to {summed}, statewide {total}")
    return rows


def kansas(_: Connection) -> list[tuple[date, str, str, str, int]]:
    return (
        pl.read_parquet(raw.parsed(ks_sos_generals.SOURCE) / "precincts.parquet")
        .filter(pl.col("chamber") == "governor", pl.col("year") == 2002)
        .select("day", "chamber", "code", "candidate", "votes")
        .rows()
    )


def clarity(
    conn: Connection, document: str, fips: str, day: date, chamber: str, contest: str
) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, fips)
    source, url, *_ = DOCUMENTS[document]
    with zipfile.ZipFile(raw.cached(source, url).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    found = next(item for item in root.iter("Contest") if item.get("text") == contest)
    rows = []
    for choice in found.iter("Choice"):
        name = "WRITE-INS" if choice.get("text") == "WRITE-IN" else str(choice.get("text")).upper()
        counted = {}
        for county in choice.iter("County"):
            code = codes[re.sub(r"[^a-z]", "", str(county.get("name")).lower())]
            counted[code] = counted.get(code, 0) + int(str(county.get("votes")))
        if sum(counted.values()) != int(str(choice.get("totalVotes"))):
            raise ValueError(f"{document} {name}: counties do not sum to the total")
        rows += [(day, chamber, code, name, votes) for code, votes in counted.items()]
    return rows


def ohio(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "39")
    source, url, *_ = DOCUMENTS["OH2010"]
    table = [
        [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", line, re.DOTALL | re.IGNORECASE)
        ]
        for line in re.findall(
            r"<tr[^>]*>(.*?)</tr>", raw.cached(source, url).path.read_text(), re.DOTALL | re.IGNORECASE
        )
    ]
    names = [name.upper() for name in table[0][1:]]
    rows = [
        (date(2010, 11, 2), "senate", codes[re.sub(r"[^a-z]", "", line[0].lower())], name, int(value.replace(",", "")))
        for line in table[2:]
        if line and line[0] not in ("Total", "Percentage of Votes")
        for name, value in zip(names, line[1:], strict=True)
    ]
    total = next(line for line in table if line and line[0] == "Total")
    if len({row[2] for row in rows}) != 88:
        raise ValueError("OH Senate: not 88 counties")
    for name, printed in zip(names, total[1:], strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != int(printed.replace(",", "")):
            raise ValueError(f"OH Senate {name}: counties sum to {summed}, statewide {printed}")
    return rows


def georgia(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "13")
    source, url, *_ = DOCUMENTS["GA2000"]
    text = raw.cached(source, url).path.read_text(errors="replace")
    body = html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<pre>(.*?)</pre>", text, re.DOTALL)[1])).splitlines()
    start = next(index for index, line in enumerate(body) if line.startswith("County"))
    names = body[start - 4].split()
    printed = [int(cell.replace(",", "")) for cell in body[start - 2].split()]
    rows = []
    for line in body[start + 1 :]:
        if found := re.fullmatch(r"(\D+?)\s+\d+\s+\d+\s+([\d,\s]+)", line.strip()):
            votes = [int(cell.replace(",", "")) for cell in found[2].split()]
            code = codes[re.sub(r"[^a-z]", "", found[1].lower())]
            rows += [(date(2000, 11, 7), "senate", code, name, value) for name, value in zip(names, votes, strict=True)]
    if len({row[2] for row in rows}) != 159:
        raise ValueError("GA Senate: not 159 counties")
    for name, total in zip(names, printed, strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != total:
            raise ValueError(f"GA Senate {name}: counties sum to {summed}, statewide {total}")
    return rows


def virgin_islands(conn: Connection, document: str) -> list[tuple[date, str, str, str, int]]:
    day = date(2024, 11, 5)
    source, url, *_ = DOCUMENTS["VI2024"]
    lines = list(csv.reader(io.StringIO(raw.cached(source, url).path.read_text())))
    names = ["PLASKETT", "SMITH", "PICKARD", "WRITE-INS"]
    summary = re.search(r"DELEGATE TO THE HOUSE.*?Total Votes Cast", pdf("VI2024S"), re.DOTALL)[0]
    official = [
        [int(value.replace(",", "")) for value in found]
        for found in re.findall(
            r"(?m)^(?:DEM|IND|REP)?\s*[A-Za-z. -]+?\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$", summary
        )
    ]
    rows = []
    if document == "VI2024":
        district = None
        for line in lines[2:]:
            if line[0] == "District":
                district = "78010" if line[1] == "St. Croix" else "78030"
            elif line[0] == "Precinct":
                county = "78020" if line[1] in ("Coral Bay", "Cruz Bay") else district
                code = f"2024:VI:{county}::{line[1].lower()}"
                rows += [(day, "house", code, name, int(value)) for name, value in zip(names, line[2:6], strict=True)]
        for index, name in enumerate(names):
            if sum(r[4] for r in rows if r[3] == name) != official[index][1] + official[index][2]:
                raise ValueError(f"VI delegate {name}: precincts do not equal election-day plus early votes")
    else:
        rows = [
            (day, "house", "2024:VI:absentee and provisional", name, official[index][3] + official[index][4])
            for index, name in enumerate(names)
        ]
    return rows


def iowa(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "19")
    names = ["VILSACK", "GROSS", "HART", "CAMPAGNA", "WRITE-INS"]
    rows = []
    totals = None
    for line in pdf("IA2002").splitlines():
        if match := re.fullmatch(r"\s*([A-Z][A-Za-z' ]+?)\s+((?:[\d,]+\s+){5}[\d,]+)\s*", line):
            values = [int(value.replace(",", "")) for value in match[2].split()]
            if sum(values[:5]) != values[5]:
                raise ValueError(f"IA governor {match[1]}: columns do not sum to the total")
            if match[1] == "Totals":
                totals = values
                continue
            code = codes[re.sub(r"[^a-z]", "", match[1].lower())]
            rows += [(date(2002, 11, 5), "governor", code, name, votes) for name, votes in zip(names, values, strict=False)]
    if len({row[2] for row in rows}) != 99 or totals is None:
        raise ValueError("IA governor: not 99 counties and a totals row")
    for index, name in enumerate(names):
        if sum(row[4] for row in rows if row[3] == name) != totals[index]:
            raise ValueError(f"IA governor {name}: counties do not sum to the statewide total")
    return rows


def virgin_islands_runoff(_: Connection) -> list[tuple[date, str, str, str, int]]:
    source, url, *_ = DOCUMENTS["VI2018R"]
    lines = list(csv.reader(io.StringIO(raw.cached(source, url).path.read_text())))
    names = ["BRYAN", "MAPP", "Write-ins"]
    codes = {"St. Croix": "78010", "St. Thomas/St. John": "2018:VI:st. thomas and st. john district"}
    rows = [
        (date(2018, 11, 20), "governor", codes[line[1]], name, int(value))
        for line in lines
        if line[0] == "District"
        for name, value in zip(names, line[2:5], strict=True)
    ]
    territory = next(line for line in lines if line[0] == "Territory")
    for index, name in enumerate(names):
        if sum(row[4] for row in rows if row[3] == name) != int(territory[2 + index]):
            raise ValueError(f"VI 2018 runoff {name}: districts do not sum to the territory")
    return rows


def indiana_vest(_: Connection) -> list[tuple[date, str, str, str, int]]:
    source, url, *_ = DOCUMENTS["IN2022"]
    with zipfile.ZipFile(raw.cached(source, url).path) as archive:
        reader = shapefile.Reader(dbf=io.BytesIO(archive.read("in_2022.dbf")))
    fields = [field[0] for field in reader.fields[1:]]
    columns = {
        "G22USSRYOU": ("senate", "Todd Young"),
        "G22USSDMCD": ("senate", "Thomas M. McDermott Jr."),
        "G22USSLSCE": ("senate", "James M. Sceniak"),
        "G22SOSRMOR": ("secretary-of-state", "Diego Morales"),
        "G22SOSDWEL": ("secretary-of-state", "Destiny Wells"),
        "G22SOSLMAU": ("secretary-of-state", "Jeffrey Maurer"),
        "G22AUDRKLU": ("auditor", "Tera K. Klutz"),
        "G22AUDDBRO": ("auditor", "ZeNai Brooks"),
        "G22AUDLSCH": ("auditor", "John Andrew Schick"),
        "G22TRERELL": ("treasurer", "Daniel Elliott"),
        "G22TREDMCC": ("treasurer", "Jessica McClellan"),
    }
    return [
        (date(2022, 11, 8), chamber, f"2022:IN:18{row['COUNTY']}::{row['P22']}", name, int(row[column]))
        for record in reader.iterRecords()
        for row in [dict(zip(fields, list(record), strict=True))]
        for column, (chamber, name) in columns.items()
    ]


def pdf(document: str) -> str:
    source, url, *_ = DOCUMENTS[document]
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def parse() -> None:
    with connect() as conn:
        rows = [
            (document, *row)
            for document, read in (
                ("AR", arkansas),
                ("CA2000", california),
                ("ME2014", maine),
                ("IA2002", iowa),
                ("IN2022", indiana_vest),
                ("DE2006", delaware),
                ("KS2002", kansas),
                ("VI2024", lambda conn: virgin_islands(conn, "VI2024")),
                ("VI2024S", lambda conn: virgin_islands(conn, "VI2024S")),
                ("VI2018R", virgin_islands_runoff),
                ("GA2000", georgia),
                ("OH2010", ohio),
                ("SC2010", lambda conn: clarity(conn, "SC2010", "45", date(2010, 11, 2), "senate", "U.S. Senate")),
                ("NM2002", lambda conn: elstats(conn, "NM2002", "35", date(2002, 11, 5), "senate")),
                ("IN2000U", lambda conn: indiana(conn, "IN2000U")),
                ("IN2000G", lambda conn: indiana(conn, "IN2000G")),
            )
            for row in read(conn)
        ]
    frame = (
        pl.DataFrame(rows, schema=["source", "day", "chamber", "code", "candidate", "votes"], orient="row")
        .with_columns(state=pl.col("source").str.slice(0, 2))
        .group_by("source", "state", "day", "chamber", "code", "candidate")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            special=pl.col("source") == "GA2000",
            writein=pl.col("candidate") == "WRITE-INS",
            measure=pl.lit(1, pl.Int16),
            kind=pl.when(pl.col("code").str.contains(r"^\d{5}$"))
            .then(pl.lit("county"))
            .when(pl.col("code").str.contains(r"^\d{10}$"))
            .then(pl.lit("town"))
            .when(pl.col("code").str.contains("::"))
            .then(pl.lit("precinct"))
            .otherwise(pl.lit("aggregate")),
        )
    )
    print(frame.group_by("state", "chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("state", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor and U.S. Senate generals by county from state canvasses, for contests whose only "
            "county results were Algara and Amlani's or ICPSR's",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for document in frame["source"].unique().to_list():
            cached, url, publisher, title, home = DOCUMENTS[document]
            source = cs.source(publisher, title)
            if source in cs.minted and source not in sources.values():
                cs.add(
                    "source",
                    id=source,
                    kind="academic" if document == "IN2022" else "official_body",
                    publisher=publisher,
                    title=title,
                    url=home,
                    license="CC BY-NC-ND 4.0" if document == "IN2022" else "Public record",
                    license_url="https://creativecommons.org/licenses/by-nc-nd/4.0/" if document == "IN2022" else None,
                )
            sources[document] = source
            cs.artifact(raw.cached(cached, url), source)
        cs.add("scheme", code="us.aggregate", polity_id=cs.polity("US"), kind="synthetic", rank=3, noun="aggregate")
        for code, state in frame.filter(pl.col("kind") == "aggregate").select("code", "state").unique().iter_rows():
            polity, unit, area = conn.execute(
                """select p.id, u.id, u.area_id from reporting_unit u join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state' and p.iso_code = %s""",
                [f"US-{state}"],
            ).fetchone()
            county = code.split(":")[2] if re.fullmatch(r"\d{5}", code.split(":")[2]) else None
            cs.add(
                "reporting_unit",
                id=cs.unit("us.aggregate", code),
                polity_id=polity,
                scheme_code="us.aggregate",
                code=code,
                parent_id=unit if county is None else cs.unit("us.county", county),
                area_relation="within",
                area_id=area if county is None else cs.area("us.county", county),
                kind="aggregate",
                valid_from=None,
                valid_to=None,
            )
        alone = {"IA2002", "IN2022"}
        grains = frame.filter(~pl.col("source").is_in(list(alone))).group_by("state").agg(
            grain=pl.when((pl.col("kind") == "town").any())
            .then(pl.lit("town"))
            .when((pl.col("kind") == "precinct").any())
            .then(pl.lit("precinct"))
            .otherwise(pl.lit("county"))
        )
        authoritative = {
            "IA2002": "Algara and Amlani's set pools Hart, Campagna, and the write-ins into one line that the "
            "certified canvass reports separately."
        }
        for grain in ("county", "town", "precinct"):
            documents = grains.filter(pl.col("grain") == grain)["state"]
            if len(documents):
                medsl.attach(
                    cs,
                    conn,
                    frame.filter(pl.col("state").is_in(documents) & ~pl.col("source").is_in(list(alone))),
                    sources,
                    grain=grain,
                    strict=True,
                    extend=True,
                )
        medsl.attach(
            cs, conn, frame.filter(pl.col("source") == "IN2022"), sources, grain="precinct", strict=True, extend=True
        )
        for document, reason in authoritative.items():
            medsl.attach(
                cs,
                conn,
                frame.filter(pl.col("source") == document),
                sources,
                redone=frozenset({"2002-11-05:IA:governor"}),
                strict=True,
                extend=True,
                authoritative=reason,
            )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if "result_value" in cs.frames else set()
        minted = {str(unit) for unit in cs.minted}
        units = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                county=pl.col("code").str.split(":").list.get(2),
            )
            .filter(pl.col("id").is_in(list(used)) & pl.col("id").is_in(list(minted)))
        )
        if units.height:
            cs.frame(
                "reporting_unit",
                units.select(
                    "id",
                    polity_id=pl.col("state").map_elements(
                        lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                    ),
                    scheme_code=pl.lit("us.precinct"),
                    code="code",
                    parent_id=pl.col("county").map_elements(
                        lambda code: str(cs.unit("us.county", code)), return_dtype=pl.String
                    ),
                    area_relation=pl.lit("within"),
                    area_id=pl.col("county").map_elements(
                        lambda code: str(cs.area("us.county", code)), return_dtype=pl.String
                    ),
                    kind=pl.lit("standard"),
                    valid_from="day",
                    valid_to=pl.col("day") + timedelta(days=1),
                ),
            )
        print(f"{units.height} new precinct units")
        return cs.write()
