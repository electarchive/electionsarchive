import csv
import html
import io
import re
import subprocess
import zipfile
from datetime import date, timedelta
from pathlib import Path

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


def fetch() -> None:
    for url in ISBE_FILES.values():
        raw.fetch(SOURCE, url, suffix=".txt")
    for election, contest in CHICAGO_FILES.values():
        raw.fetch(SOURCE, CHICAGO_PAGE.format(election=election), suffix=".html")
        raw.fetch(SOURCE, CHICAGO_DOWNLOAD.format(election=election, contest=contest), suffix=".xls")
    for url in CLERK_FILES.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())


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
            {
                "code": str(row["county"]),
                "kind": "county",
                "fips": str(row["county"]),
                "candidate": f"{row['first']} {row['last']}".upper(),
                "votes": row["votes"],
            }
            for row in counties
            if not (precincts and row["county"] == "COOK")
        ]
        frame = pl.DataFrame(rows).with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day))
        if precincts:
            cook = {
                f"{row['first']} {row['last']}".upper(): row["votes"] for row in counties if row["county"] == "COOK"
            }
            summed = dict(
                frame.filter(pl.col("kind") == "precinct").group_by("candidate").agg(pl.col("votes").sum()).iter_rows()
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
    frame = pl.read_parquet(PARSED / "lines.parquet").filter(pl.col("year").is_in(list(CLERK_FILES)))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois presidential general elections of 2000, 2004, and 2012 by precinct in Cook County (the "
            "Chicago Board of Election Commissioners and the Cook County Clerk) and by county elsewhere (the State "
            "Board of Elections)",
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
                if year in CLERK_FILES:
                    cs.artifact(raw.cached(SOURCE, link), sources[key])
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
        medsl.attach(cs, conn, lines, sources, grain="precinct")
        cook = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [COOK]
            ).fetchall()
        }[COOK]
        state = cs.polity("US-IL")
        for code, day in lines.filter(pl.col("kind") == "precinct").select("code", "day").unique().iter_rows():
            unit = cs.unit("us.precinct", code, day)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=cook[0],
                    area_relation="within",
                    area_id=cook[1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        for year, day in DAYS.items():
            if year not in CLERK_FILES:
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:IL:president:precinct")
            counted = int(frame.filter(pl.col("year") == year, pl.col("kind") == "precinct")["votes"].sum())
            certified = sum(int(row["votes"]) for row in county_rows(year) if row["county"] == "COOK")
            short = (
                f" Cook's precinct figures add up to {counted:,} votes, {abs(certified - counted):,} "
                f"{'fewer' if counted < certified else 'more'} than the {certified:,} the State Board of Elections "
                "reports for the county."
                if counted != certified
                else ""
            )
            if result_set in cs.minted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        "Cook County is by precinct: Chicago from the Board of Election Commissioners' results download "
                        "and suburban Cook from the County Clerk's precinct canvass. Every other county is the State "
                        f"Board of Elections' county total.{short} The office documents were located through the SAGE "
                        "precinct source map (Noah Dasanaike, Small-Area Global Elections; CC0 on Harvard Dataverse, "
                        "doi:10.7910/DVN/YGJR1L)."
                    ),
                )
        return cs.write()
