import csv
import html
import io
import json
import re
import subprocess
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.il_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {2000: date(2000, 11, 7), 2004: date(2004, 11, 2), 2008: date(2008, 11, 4), 2012: date(2012, 11, 6)}
COOK = "17031"
ISBE = "Illinois State Board of Elections"
ISBE_TITLE = "General election vote totals by county"
ISBE_FILES = {
    2000: "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/2000/ByCounty/GE2000Ctytxt.txt",
    2004: "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/2004/ByCounty/GE2004Ctytxt.txt",
    2008: "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/GE2008Cty.txt",
    2012: "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/GE2012Cty.txt",
}
CHICAGO = "Board of Election Commissioners for the City of Chicago"
CHICAGO_TITLE = "Election results by precinct (Excel download)"
CHICAGO_PAGE = "https://chicagoelections.gov/elections/results/{election}"
CHICAGO_DOWNLOAD = (
    "https://chicagoelections.gov/elections/results/{election}/download?contest={contest}&ward=&precinct="
)
CHICAGO_FILES = {2000: (120, 11), 2004: (90, 3), 2008: (40, 11), 2012: (19, 11)}
CLERK = "Cook County Clerk"
CLERK_TITLE = "Suburban Cook County precinct canvass"
CLERK_FILES = {
    2000: "https://raw.githubusercontent.com/openelections/openelections-sources-il/master/county-level%20files%20for%20processing/Cook/2000/2000NovemberPrecinct.PDF",
    2004: "https://web.archive.org/web/20050206224815id_/http://www.voterinfonet.com:80/results/110204/precincts.txt",
    2012: "https://d16ff5nedhorcz.cloudfront.net/Precinct%20Canvasses/Precinct%20Canvass%20-%2011.06.12.zip",
}
NOT_PRECINCT = re.compile(r"DIST\.|\bSUB\b|BOARD|TOTAL|^\d")
CLARITY = "https://results.enr.clarityelections.com/IL/{}/reports/{}"
LAKE_CDX = (
    "https://web.archive.org/cdx/search/cdx?url=co.lake.il.us/cntyclk/elections/{}/*&output=txt"
    "&filter=statuscode:200&collapse=urlkey"
)
COUNTIES = {
    "LAKE": ("17097", "Lake County Clerk", "Precinct election results"),
    "WILL": ("17197", "Will County Clerk", "General election canvass by precinct"),
    "KANE": ("17089", "Kane County Clerk", "General election canvass by precinct"),
    "MCHENRY": ("17111", "McHenry County Clerk", "General election canvass by precinct"),
    "DUPAGE": ("17043", "DuPage County Election Commission", "General election results by precinct"),
    "MADISON": ("17119", "Madison County Clerk", "General election canvass by precinct"),
}
LOADED: dict[int, list[tuple[str, int, int]]] = {year: [] for year in DAYS}
OCR_DIGITS = str.maketrans("SslIiBOo", "55111800")
OFFICES = {
    "17097": "Lake County",
    "17197": "Will County",
    "17089": "Kane County",
    "17111": "McHenry County",
    "17043": "DuPage County",
    "17119": "Madison County",
}
COUNTY_FILES = {
    ("LAKE", 2000): [LAKE_CDX.format("110700")],
    ("LAKE", 2004): [LAKE_CDX.format("electionresults/11022004")],
    ("LAKE", 2008): [CLARITY.format("Lake/7239/19367", "detailxml.zip")],
    ("LAKE", 2012): [CLARITY.format("Lake/42276/113523", "detailxml.zip")],
    ("WILL", 2000): [
        "https://assets01.aws.connect.clarityelections.com/Assets/Connect/RootPublish/will-il.connect.clarityelections.com/CanvassBooks/2000s/2000/November2000CanvassBook.pdf"
    ],
    ("WILL", 2008): [CLARITY.format("Will/9042/227236", "detailxls.zip")],
    ("KANE", 2004): ["https://clerk.kanecountyil.gov/Elections/Election%20Documents/Archive/2004-11-02/110204.xls"],
    ("KANE", 2008): [
        "https://clerk.kanecountyil.gov/Elections/Election%20Documents/Archive/2008-11-04/2008-11-04Canvass.xls"
    ],
    ("KANE", 2012): [
        "https://clerk.kanecountyil.gov/Elections/Election%20Documents/2012-11-06/Precinct%20Report%20Details.pdf"
    ],
    **{
        ("MCHENRY", year): [
            f"https://web.archive.org/web/{stamp}id_/https://www.mchenrycountyil.gov/home/showdocument?id={id}"
        ]
        for year, stamp, id in (
            (2000, 20201017154744, 1791),
            (2004, 20201018014027, 1807),
            (2008, 20201017151033, 1821),
            (2012, 20201017150755, 22328),
        )
    },
    ("MADISON", 2012): [
        "https://web.archive.org/web/20141010074813id_/http://www.madisonvotes.com/HTMLREPORTS/2012GeneralElectionCanvass.html"
    ],
    ("DUPAGE", 2012): [
        "https://results.enr.clarityelections.com/IL/DuPage/73889/193188/json/details.json",
        "https://results.enr.clarityelections.com/IL/DuPage/73889/193188/json/sum.json",
    ],
}


def fetch() -> None:
    for url in ISBE_FILES.values():
        raw.fetch(SOURCE, url, suffix=".txt")
    for election, contest in CHICAGO_FILES.values():
        raw.fetch(SOURCE, CHICAGO_PAGE.format(election=election), suffix=".html")
        raw.fetch(SOURCE, CHICAGO_DOWNLOAD.format(election=election, contest=contest), suffix=".xls")
    for url in CLERK_FILES.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())
    for (county, _), urls in COUNTY_FILES.items():
        for url in urls:
            if url.startswith("https://web.archive.org/"):
                if url not in raw.keys(SOURCE):
                    wayback(url, ".txt" if "/cdx/" in url else Path(url).suffix.lower() or ".pdf")
            else:
                raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())
            if county == "LAKE" and "/cdx/" in url:
                for page in lake_pages(url):
                    if page not in raw.keys(SOURCE):
                        wayback(page, ".htm")


def wayback(url: str, suffix: str) -> None:
    for wait in (4.5, 90, 300, 600, 900):
        time.sleep(wait)
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
            return
        except httpx.HTTPError as error:
            print(f"{url}: {error}")
    raise RuntimeError(f"{url}: Wayback kept refusing")


def lake_pages(cdx: str) -> list[str]:
    return [
        f"https://web.archive.org/web/{stamp}id_/{original}"
        for _, stamp, original, *_ in (
            line.split() for line in raw.cached(SOURCE, cdx).path.read_text().splitlines() if line.strip()
        )
        if re.search(r"/PREC\d+\.HTM$", original, re.IGNORECASE)
    ]


def fold(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper().replace("SAINT", "ST"))


def county_rows(year: int) -> list[dict[str, str | int]]:
    lines = raw.cached(SOURCE, ISBE_FILES[year]).path.read_text(encoding="latin-1").splitlines()
    delimiter = "\t" if "\t" in lines[0] else ","
    header = [cell.strip() for cell in next(csv.reader(lines[:1], delimiter=delimiter))]
    rows: list[dict[str, str | int]] = []
    for cells in csv.reader(lines[1:], delimiter=delimiter):
        row = dict(zip(header, (cell.strip() for cell in cells), strict=False))
        if row["OfficeName"].startswith("PRESIDENT"):
            rows.append(
                {
                    "first": row["CanFirstName"],
                    "last": row["CanLastName"],
                    "county": row["County"],
                    "votes": int(row["Votes"]),
                }
            )
    return rows


def surname(text: str) -> str:
    return fold(re.split(r"\s+(?:and|&|/|\\)\s+", text.strip(), maxsplit=1)[0].split()[-1])


def chicago(year: int) -> list[tuple[str, str, int]]:
    election, contest = CHICAGO_FILES[year]
    page = raw.cached(SOURCE, CHICAGO_PAGE.format(election=election)).path.read_text(errors="replace")
    label = re.search(rf'<option value="{contest}"[^>]*>([^<]*)', page)
    if label is None or "PRESIDENT" not in html.unescape(label[1]).upper():
        raise ValueError(f"Chicago {year}: contest {contest} is not president")
    sheet = pl.read_excel(
        io.BytesIO(raw.cached(SOURCE, CHICAGO_DOWNLOAD.format(election=election, contest=contest)).path.read_bytes()),
        has_header=False,
        infer_schema_length=0,
    ).rows()
    ward = None
    header: list[str] = []
    found = []
    for line in sheet:
        first = (line[0] or "").strip()
        if match := re.fullmatch(r"Ward (\d+)", first):
            ward = int(match[1])
        elif first == "Precinct":
            header = [(cell or "").strip() for cell in line]
        elif ward is not None and first.isdigit() and header:
            for i, name in enumerate(header):
                if i >= 2 and name and name != "%":
                    found.append((f"{ward:02d}{int(first):03d}", name, int((line[i] or "0").replace(",", ""))))
    return found


def suburban(year: int) -> list[tuple[str, str, int]]:
    path = raw.cached(SOURCE, CLERK_FILES[year]).path
    found: list[tuple[str, str, int]] = []
    if year == 2012:
        with zipfile.ZipFile(path) as archive:
            for member in sorted(archive.namelist()):
                sheets = pl.read_excel(
                    io.BytesIO(archive.read(member)), sheet_id=0, has_header=False, infer_schema_length=0
                )
                for sheet in sheets.values():
                    rows = sheet.rows()
                    if len(rows) < 3 or not any("President" in (cell or "") for cell in rows[2]):
                        continue
                    header: list[str] = []
                    for line in rows:
                        if "Registered" in line:
                            header = [(cell or "").strip() for cell in line]
                        elif header and re.fullmatch(r"\d{7}", (line[0] or "").strip()):
                            for i, name in enumerate(header):
                                if i >= 5 and name and name not in ("Over Votes", "Under Votes", "Blank Ballots"):
                                    found.append((line[0].strip(), name, int(line[i] or 0)))
        return found
    if year == 2000:
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        tickets = re.compile(r"(\d\d)\s+\([A-Z]+\)\s+([A-Z' .-]+?\s\\\s[A-Z' .-]+?)(?:\s{2,}|$)")
        width = 30
    else:
        text = path.read_text(encoding="latin-1")
        tickets = re.compile(r"(\d\d) = (.+? and .+?) \([A-Z]+\)")
        width = 21
    columns: dict[int, str] = {}
    edges: dict[int, tuple[int, int]] = {}
    for line in text.splitlines():
        if line.startswith("1") or "PAGE " in line:
            columns, edges = {}, {}
        for number, name in tickets.findall(line):
            columns[int(number)] = name.strip()
        if re.match(r"^[^=\d]*\s01\s+02\b", line):
            ends = [(int(match[0]), match.end()) for match in re.finditer(r"\d\d", line)]
            edges = {number: (ends[i - 1][1] if i else width, end) for i, (number, end) in enumerate(ends)}
            continue
        name = line[1 if year == 2000 else 0 : width].strip()
        if (
            not columns
            or not re.fullmatch(r"[A-Z][A-Z.'/-]*(?: [A-Z0-9.'/-]+)* [A-Z0-9-]*\d", name)
            or NOT_PRECINCT.search(name)
        ):
            continue
        cells = (
            line[width:].split()[: max(columns)]
            if year == 2000
            else [line[edges[i][0] : edges[i][1]].strip() for i in sorted(columns)]
        )
        cells = [cell.replace(",", "") for cell in cells]
        if not all(re.fullmatch(r"\d*", cell) for cell in cells):
            raise ValueError(f"suburban Cook {year}: {line!r}")
        found.extend((name, columns[i], int(cell or 0)) for i, cell in zip(sorted(columns), cells, strict=True))
    return found


def lake(year: int) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    if year >= 2008:
        with zipfile.ZipFile(raw.cached(SOURCE, COUNTY_FILES["LAKE", year][0]).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        contest = next(item for item in root.iter("Contest") if "President" in item.get("text", ""))
        for choice in contest.iter("Choice"):
            for precinct in choice.iter("Precinct"):
                found.append((precinct.get("name", "").upper(), choice.get("text", ""), int(precinct.get("votes", 0))))
        return found
    for page in lake_pages(COUNTY_FILES["LAKE", year][0]):
        text = re.sub(r"<[^>]+>", "", html.unescape(raw.cached(SOURCE, page).path.read_text(errors="replace")))
        head = re.search(
            r"^\s+\d+\s{2}(\S.*?)\s{2,}TOTAL" if year == 2000 else r"^\s+\d{4} (\S.*)$", text, re.MULTILINE
        )
        block = re.search(
            r"UNITED STATES PRESIDENT[^\n]*\n(.*?)\n\s*\n" if year == 2000 else r"U\. S\. President.*?\n(.*?)Total",
            text,
            re.DOTALL,
        )
        if head is None or block is None:
            raise ValueError(f"Lake {year}: no president block in {page}")
        for line in block[1].splitlines():
            if year == 2000:
                match = re.match(r"\s+(\S.*?)\.{2,}\s*[A-Z]{3}\s+\d*\s+(\d+)\s", line)
            else:
                match = re.match(r"\s+(\S.*?)[ .]{2,}\s(\d+)(?:\s|$)", line)
            if match:
                found.append((head[1].strip().upper(), match[1], int(match[2])))
    return found


def will(year: int) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    if year == 2000:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, COUNTY_FILES["WILL", year][0]).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names: dict[str, str] = {}
        section = False
        for line in text.splitlines():
            if re.match(r"\s*PRESIDENT AND VICE PRESIDENT \* VF 1\s+Statistics", line):
                section = True
            if not section:
                continue
            for number, name in re.findall(r"(\d)- [A-Z]{3}\s+\d{3}\s+(\S.*?)(?=\s+\d- |\s*$)", line):
                names.setdefault(number, name)
            if line.startswith("Totals"):
                section = False
                continue
            tokens = [re.sub(r"[^\w=!]", "", token) for token in line.split()]
            votes = [token.translate(OCR_DIGITS) for token in tokens[-6:]]
            if len(tokens) > 6 and all(token.isdigit() for token in votes) and tokens[0].isalpha():
                *words, last = tokens[:-6]
                name = " ".join([*words, last.translate(OCR_DIGITS) if words else last])
                name = name.replace("!=", "F").replace("DU PAGE", "DUPAGE").replace("J3ALLOT", "BALLOT")
                found.extend((name, names[str(i + 1)], int(cell)) for i, cell in enumerate(votes))
        aurora = re.search(r"RESULTS\s+((?:\d+\s+){5}\d+)\s*\n\s*FINAL TOT", text)
        if aurora is None:
            raise ValueError("Will 2000: no Aurora Election Commission row")
        found.extend(
            ("CITY OF AURORA ELECTION COMMISSION", names[str(i + 1)], int(cell))
            for i, cell in enumerate(aurora[1].split())
        )
        return found
    with zipfile.ZipFile(raw.cached(SOURCE, COUNTY_FILES["WILL", year][0]).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xls"))
    space = "{urn:schemas-microsoft-com:office:spreadsheet}"
    for sheet in root.iter(f"{space}Worksheet"):
        rows = [
            [(cell.get(f"{space}MergeAcross"), (cell.findtext(f"{space}Data") or "").strip()) for cell in row]
            for row in sheet.iter(f"{space}Row")
        ]
        if not rows or not rows[0] or "PRESIDENT" not in rows[0][0][1]:
            continue
        candidates = [text for merge, text in rows[1] if merge]
        header = [text for _, text in rows[2]]
        columns = [i for i, text in enumerate(header) if text == "Total Votes"]
        for row in rows[3:]:
            cells = [text for _, text in row]
            if cells and cells[0] and not cells[0].startswith("Total"):
                found.extend(
                    (cells[0], candidate, int(cells[i])) for candidate, i in zip(candidates, columns, strict=True)
                )
    return found


def kane(year: int) -> list[tuple[str, str, int]]:
    path = raw.cached(SOURCE, COUNTY_FILES["KANE", year][0]).path
    found: list[tuple[str, str, int]] = []
    if year == 2012:
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        precinct = None
        section = False
        for line in text.splitlines():
            if match := re.match(r"\s*Precinct (\S+) \(Ballots Cast", line):
                precinct = match[1]
            elif "FOR PRESIDENT AND VICE PRESIDENT" in line:
                section = True
            elif section and "Cast Votes:" in line:
                section = False
            elif (
                section
                and precinct
                and (
                    match := re.match(
                        r"\s+(?:[A-Z]{3}\s+)?(\S.*?)\s{2,}\d+\s+[\d.]+%\s+\d+\s+[\d.]+%\s+\d+\s+[\d.]+%\s+(\d+)\s+[\d.]+%",
                        line,
                    )
                )
            ):
                found.append((precinct, match[1], int(match[2])))
        return found
    sheets = pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0)
    if year == 2004:
        rows = sheets["KANE & AURORA"].rows()
        columns = [i for i, cell in enumerate(rows[1]) if cell == "PRESIDENT"]
        for row in rows:
            if re.fullmatch(r"[A-Z]{2}\d{4}|\d{6}", row[0] or ""):
                found.extend((row[1].strip(), rows[3][i], int(row[i] or 0)) for i in columns)
        return found
    for name, sheet in sheets.items():
        if not name.startswith("PRESIDENT AND VICE"):
            continue
        header: list[str] = []
        for row in sheet.rows():
            if row[1] == "Registered Voters":
                header = [(cell or "").strip() for cell in row]
            elif header and row[0] and "TOTALS" not in row[0] and (row[1] or "").isdigit():
                found.extend(
                    (re.sub(r"\s+", " ", row[0]).strip(), candidate, int(row[i] or 0))
                    for i, candidate in enumerate(header)
                    if i >= 4 and candidate
                )
    return found


def dupage(year: int) -> list[tuple[str, str, int]]:
    details, summary = (json.loads(raw.cached(SOURCE, url).path.read_bytes()) for url in COUNTY_FILES["DUPAGE", year])
    contest = next(item for item in summary["Contests"] if "President" in item["C"])
    detail = next(item for item in details["Contests"] if item["K"] == contest["K"])
    return [
        (re.sub(r"\s+", " ", precinct).upper(), candidate, int(votes))
        for precinct, row in zip(detail["P"], detail["V"], strict=True)
        for candidate, votes in zip(contest["CH"], row, strict=True)
    ]


def mchenry(year: int) -> list[tuple[str, str, int]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, COUNTY_FILES["MCHENRY", year][0]).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    official = {
        f"{row['first']} {row['last']}": int(row["votes"]) for row in county_rows(year) if row["county"] == "McHENRY"
    }
    found: list[tuple[str, str, int]] = []
    rows: list[tuple[str, list[int]]] = []
    header = ""
    for page in text.split("\f"):
        if "PRESIDENT/VICE PRES" not in "\n".join(page.splitlines()[:12]):
            continue
        for line in page.splitlines():
            match = re.match(r"\s*(\S.*?)\s{2,}((?:\d+\s+){0,5})((?:\d+\s+[\d.]+%\s*)+)$", line)
            if match is None:
                if not rows:
                    header += " " + line
                continue
            votes = [int(cell) for cell in re.findall(r"(\d+)\s+[\d.]+%", match[3])]
            if match[1] != "Total":
                rows.append((re.sub(r"\s+", " ", match[1]), votes))
                continue
            labels = []
            for total in votes:
                named = [
                    name
                    for name, votes in official.items()
                    if votes == total and total and fold(name.split()[-1]) in fold(header)
                ]
                labels.append(named[0] if len(named) == 1 else "Write-ins")
            found.extend(
                (name, label, count)
                for name, counts in rows
                for label, count in zip(labels, counts, strict=True)
                if label != "Write-ins" or count
            )
            rows, header = [], ""
    return found


def madison(year: int) -> list[tuple[str, str, int]]:
    text = html.unescape(
        re.sub(r"<[^>]+>", "", raw.cached(SOURCE, COUNTY_FILES["MADISON", year][0]).path.read_text(errors="replace"))
    )
    lines = text.split("PRESIDENT AND VICE PRESIDENT", 1)[1].split("(Vote for not more than", 2)[1].splitlines()
    labels = {
        number: name.removeprefix("(")
        for line in lines[:8]
        for number, name in re.findall(r"(\d\d) = (.+?)\s{2,}[\d,]+\s+[\d.]+", line)
    }
    found: list[tuple[str, str, int]] = []
    for line in lines:
        if match := re.match(rf"(\d{{4}}) (\S.*?)((?:\s+\d+){{{len(labels)}}})$", line):
            votes = match[3].split()
            found.extend(
                (f"{match[1]} {re.sub(r'\s+', ' ', match[2])}", labels[f"{i + 1:02d}"], int(cell))
                for i, cell in enumerate(votes)
            )
    return found


READERS = {"LAKE": lake, "WILL": will, "KANE": kane, "MCHENRY": mchenry, "DUPAGE": dupage, "MADISON": madison}


def resolve(name: str, heads: dict[str, str]) -> str:
    for word in re.findall(r"[A-Za-z]+", name):
        if fold(word) in heads:
            return heads[fold(word)]
    return "WRITE-INS"


def collar(year: int, heads: dict[str, str], counties: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for county, (fips, *_) in COUNTIES.items():
        if (county, year) not in COUNTY_FILES or county not in READERS:
            continue
        found = [
            {"code": f"{county}:{precinct}", "fips": fips, "candidate": resolve(name, heads), "votes": votes}
            for precinct, name, votes in READERS[county](year)
        ]
        summed: dict[str, int] = {}
        for row in found:
            summed[str(row["candidate"])] = summed.get(str(row["candidate"]), 0) + int(row["votes"])
        official = {
            f"{row['first']} {row['last']}".upper(): int(row["votes"])
            for row in counties
            if fold(str(row["county"])) == county
        }
        apart = {
            candidate: (summed.get(candidate, 0), votes)
            for candidate, votes in official.items()
            if summed.get(candidate, 0) != votes
        }
        precincts = len({row["code"] for row in found})
        print(f"{year} {county}: {precincts} precincts, write-ins {summed.get('WRITE-INS', 0)}, apart {apart}")
        tolerance = max(25, sum(official.values()) // 1000)
        absent = sum(votes for candidate, (counted, votes) in apart.items() if not counted)
        if (
            sum(abs(counted - votes) for counted, votes in apart.values() if counted) <= tolerance
            and absent <= summed.get("WRITE-INS", 0) + tolerance
        ):
            rows.extend(found)
            LOADED[year].append((county, precincts, sum(summed.values()) - sum(official.values())))
    return rows


def parse() -> None:
    frames = []
    for year, day in DAYS.items():
        counties = county_rows(year)
        heads = {}
        for row in counties:
            heads.setdefault(fold(str(row["last"])), f"{row['first']} {row['last']}".upper())
        precincts: list[tuple[str, str, int]] = []
        if year in CLERK_FILES:
            precincts = [(f"CHICAGO:{code}", name, votes) for code, name, votes in chicago(year)] + [
                (f"COOK:{code}", name, votes) for code, name, votes in suburban(year)
            ]
        rows = [
            {
                "code": f"{day}:IL:{COOK}:{code}",
                "kind": "precinct",
                "fips": COOK,
                "candidate": "WRITE-INS" if re.search(r"write", name, re.IGNORECASE) else heads[surname(name)],
                "votes": votes,
            }
            for code, name, votes in precincts
        ] + [
            row | {"code": f"{day}:IL:{row['fips']}:{row['code']}", "kind": "precinct"}
            for row in collar(year, heads, counties)
        ]
        rows += [
            {
                "code": str(row["county"]),
                "kind": "county",
                "fips": str(row["county"]),
                "candidate": f"{row['first']} {row['last']}".upper(),
                "votes": row["votes"],
            }
            for row in counties
            if not (
                (precincts and row["county"] == "COOK")
                or any(fold(str(row["county"])) == county for county, *_ in LOADED[year])
            )
        ]
        frame = (
            pl.DataFrame(rows)
            .select("code", "kind", "fips", "candidate", "votes")
            .with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day))
        )
        if precincts:
            cook = {
                f"{row['first']} {row['last']}".upper(): row["votes"] for row in counties if row["county"] == "COOK"
            }
            summed = dict(
                frame.filter(pl.col("fips") == COOK, pl.col("kind") == "precinct")
                .group_by("candidate")
                .agg(pl.col("votes").sum())
                .iter_rows()
            )
            for candidate, votes in sorted(cook.items()):
                print(f"{year} Cook {candidate}: precincts {summed.get(candidate, 0):,} against {votes:,}")
            if summed.get("WRITE-INS"):
                print(f"{year} Cook write-ins: precincts {summed['WRITE-INS']:,}")
        frames.append(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.concat(frames)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year", "kind").agg(pl.len(), pl.col("votes").sum()).sort("year", "kind").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    loaded = {
        year: sorted(set(fips))
        for year, fips in frame.filter(pl.col("kind") == "precinct").group_by("year").agg("fips").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois presidential general elections of 2000 through 2012 by precinct in Cook County (the "
            "Chicago Board of Election Commissioners and the Cook County Clerk) and the Lake, Will, Kane, McHenry, "
            "DuPage, and Madison county election offices where their precinct canvasses tie the State Board's county "
            "figures, and by county elsewhere (the State Board of Elections)",
            polity="US-IL",
        )
        sources = {}
        for key, publisher, title, url, files in (
            ("ISBE", ISBE, ISBE_TITLE, "https://www.elections.il.gov/ElectionOperations/VoteTotals.aspx", ISBE_FILES),
            ("CHICAGO", CHICAGO, CHICAGO_TITLE, "https://chicagoelections.gov/elections/results", {}),
            (
                "CLERK",
                CLERK,
                CLERK_TITLE,
                "https://www.cookcountyclerkil.gov/elections/results-and-election-data/election-data/precinct-canvasses",
                CLERK_FILES,
            ),
        ):
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add(
                    "source",
                    id=sources[key],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            for year, link in files.items():
                cs.artifact(raw.cached(SOURCE, link), sources[key])
        for county, (fips, publisher, title) in COUNTIES.items():
            for year in (year for year, codes in loaded.items() if fips in codes):
                if county not in sources:
                    sources[county] = cs.source(publisher, title)
                    if sources[county] in cs.minted:
                        cs.add(
                            "source",
                            id=sources[county],
                            kind="official_body",
                            publisher=publisher,
                            title=title,
                            url=next(
                                link if "/cdx/" not in link else lake_pages(link)[0]
                                for link in COUNTY_FILES[county, year]
                            ),
                            license="Public record",
                        )
                for link in COUNTY_FILES[county, year]:
                    cs.artifact(raw.cached(SOURCE, link), sources[county])
                    if county == "LAKE" and "/cdx/" in link:
                        for page in lake_pages(link):
                            cs.artifact(raw.cached(SOURCE, page), sources[county])
        for year in CLERK_FILES:
            election, contest = CHICAGO_FILES[year]
            for link in (
                CHICAGO_PAGE.format(election=election),
                CHICAGO_DOWNLOAD.format(election=election, contest=contest),
            ):
                cs.artifact(raw.cached(SOURCE, link), sources["CHICAGO"])
        counties = {
            fold(name.removesuffix(", IL").removesuffix(" County")): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
        lines = (
            frame.with_columns(
                code=pl.when(pl.col("kind") == "county")
                .then(pl.col("code").map_elements(lambda name: counties[fold(name)], return_dtype=pl.String))
                .otherwise("code"),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate") == "WRITE-INS",
            )
            .group_by("year", "day", "code", "kind", "candidate", "measure")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .with_columns(
                chamber=pl.lit("president"),
                special=pl.lit(False),
                state=pl.lit("IL"),
                district=pl.lit(None, pl.Int16),
                source=pl.lit("ISBE"),
            )
        )
        medsl.attach(cs, conn, lines, sources, grain="precinct", restage=True)
        parents = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '17%%'"
            ).fetchall()
        }
        state = cs.polity("US-IL")
        for code, day in lines.filter(pl.col("kind") == "precinct").select("code", "day").unique().iter_rows():
            unit = cs.unit("us.precinct", code, day)
            if unit in cs.minted:
                parent, area = parents[code.split(":")[2]]
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=parent,
                    area_relation="within",
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        names = {fips: county for county, (fips, *_) in COUNTIES.items()} | {COOK: "COOK"}
        for year, day in DAYS.items():
            if year not in loaded:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:IL:president:precinct")
            gaps = []
            for fips in loaded[year]:
                counted = int(frame.filter(pl.col("year") == year, pl.col("fips") == fips)["votes"].sum())
                certified = sum(
                    int(row["votes"]) for row in county_rows(year) if fold(str(row["county"])) == names[fips]
                )
                if counted != certified:
                    gaps.append(
                        f"{names[fips].title().replace('Dupage', 'DuPage').replace('Mchenry', 'McHenry')} "
                        f"{counted:,} against {certified:,}"
                    )
            offices = [OFFICES[fips] for fips in loaded[year] if fips != COOK]
            listed = f"{', '.join(offices[:-1])}, and {offices[-1]}" if len(offices) > 2 else " and ".join(offices)
            body = (
                (
                    "Cook County is by precinct: Chicago from the Board of Election Commissioners' results download "
                    "and suburban Cook from the County Clerk's precinct canvass. "
                    if COOK in loaded[year]
                    else ""
                )
                + (
                    f"{listed} {'are' if len(offices) > 1 else 'is'} by precinct from the county's own canvass. "
                    if offices
                    else ""
                )
                + "Every other county is the State Board of Elections' county total. "
                + (
                    "The precinct figures add up to a different vote total than the State Board reports for "
                    f"{'these counties' if len(gaps) > 1 else 'this county'}: {'; '.join(gaps)}. "
                    if gaps
                    else ""
                )
                + "The office documents were located through the SAGE precinct source map (Noah Dasanaike, "
                "Small-Area Global Elections; CC0 on Harvard Dataverse, doi:10.7910/DVN/YGJR1L)."
            )
            held = conn.execute(
                """select n.id, n.body_md from note n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.kind = 'caveat' and c.pipeline = %s""",
                [result_set, SOURCE],
            ).fetchall()
            for note, text in held:
                if text != body:
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=result_set,
                        body_md=text,
                        reason="the caveat was rewritten for the added county canvasses",
                    )
            if all(text != body for _, text in held):
                cs.add("note", entity_id=result_set, kind="caveat", body_md=body)
        return cs.write()
