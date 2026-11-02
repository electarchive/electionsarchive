import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_pri_cert"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.sos.alabama.gov/sites/default/files/"
CERT_2006 = SITE + "voter-pdfs/2006/DemocraticPrimary-OfficialCertification-06-06-2006.pdf"
ZIP_2012 = SITE + "election-data/2017-06/2012-primary-precinctLevel_1.zip"
PUBLISHER = "Alabama Secretary of State"
TITLE = "Primary election results by county"
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
PARTIES = {"DEM": "democratic", "REP": "republican"}
RUNOFF_2010 = {
    ("republican", 2): (
        ("Barber", "Roby"),
        {
            "Autauga": (2642, 4034),
            "Barbour": (296, 383),
            "Bullock": (93, 172),
            "Butler": (585, 1307),
            "Coffee": (2341, 3010),
            "Conecuh": (253, 202),
            "Covington": (1786, 2012),
            "Crenshaw": (365, 525),
            "Dale": (2066, 2088),
            "Elmore": (4618, 5303),
            "Geneva": (1432, 2036),
            "Henry": (702, 1001),
            "Houston": (4306, 6541),
            "Lowndes": (209, 468),
            "Montgomery": (3290, 8318),
            "Pike": (1107, 1769),
        },
        (26091, 39169),
    ),
    ("republican", 7): (
        ("Chamberlain", "Salter"),
        {
            "Choctaw": (52, 52),
            "Clarke": (255, 296),
            "Dallas": (1365, 393),
            "Greene": (182, 124),
            "Hale": (469, 352),
            "Jefferson": (3324, 3277),
            "Marengo": (370, 294),
            "Perry": (317, 158),
            "Pickens": (221, 139),
            "Sumter": (68, 51),
            "Tuscaloosa": (4972, 4046),
            "Wilcox": (188, 167),
        },
        (11783, 9349),
    ),
    ("democratic", 7): (
        ("Sewell", "Smoot"),
        {
            "Choctaw": (2622, 2051),
            "Clarke": (400, 259),
            "Dallas": (3841, 799),
            "Greene": (1245, 1401),
            "Hale": (1016, 1095),
            "Jefferson": (13811, 11972),
            "Marengo": (2771, 2214),
            "Perry": (1132, 651),
            "Pickens": (690, 1031),
            "Sumter": (1773, 2253),
            "Tuscaloosa": (1884, 1659),
            "Wilcox": (1181, 1096),
        },
        (32366, 26481),
    ),
}


def fetch() -> None:
    for url, suffix in ((CERT_2006, ".pdf"), (ZIP_2012, ".zip")):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=suffix)


def cells(content: bytes) -> list[list[list[str]]]:
    if not content.lstrip(b"\xef\xbb\xbf").startswith(b"<?xml"):
        workbook = fastexcel.read_excel(content)
        return [
            [["" if cell is None else str(cell).strip() for cell in line] for line in sheet.iter_rows()]
            for sheet in (
                workbook.load_sheet_by_name(name, header_row=None).to_polars() for name in workbook.sheet_names
            )
        ]
    sheets = []
    for sheet in ElementTree.fromstring(content.lstrip(b"\xef\xbb\xbf")).iter(SS + "Worksheet"):
        rows = []
        for row in sheet.iter(SS + "Row"):
            line: list[str] = []
            for cell in row.findall(SS + "Cell"):
                index = cell.get(SS + "Index")
                if index is not None:
                    line.extend([""] * (int(index) - 1 - len(line)))
                data = cell.find(SS + "Data")
                line.append((data.text or "").strip() if data is not None else "")
                line.extend([""] * int(cell.get(SS + "MergeAcross") or 0))
            rows.append(line)
        sheets.append(rows)
    return sheets


def parse_2006(text: str, fips: dict[str, str]) -> list[tuple]:
    rows = []
    lines = text.split("\n")
    for number, line in enumerate(lines):
        if "United States Representative, District 2" not in line:
            continue
        names = [m for m in re.finditer(r"\S.*?(?=\s{2,}|$)", lines[number + 1])][1:]
        starts = [m.start() for m in names]
        candidates = [m.group().upper().replace(", JR.", " JR.") for m in names]
        districts = [2, 2, 7, 7]
        for row in lines[number + 3 :]:
            if row.startswith(("TOTAL", "Prepared")):
                break
            county = row[:20].strip()
            if not county or county.startswith("County"):
                continue
            for found in re.finditer(r"[\d,]+", row[20:]):
                end = found.end() + 20
                column = sum(1 for start in starts if start < end) - 1
                rows.append(
                    (
                        date(2006, 6, 6),
                        "house:democratic",
                        districts[column],
                        candidates[column],
                        fips[re.sub(r"[^a-z]", "", county.lower())],
                        int(found.group().replace(",", "")),
                    )
                )
    return rows


def parse_2012(path: Path, fips: dict[str, str]) -> list[tuple]:
    rows = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xls"):
                continue
            county = fips[re.sub(r"[^a-z]", "", name.rsplit("/", 1)[-1][:-4].lower())]
            for sheet in cells(archive.read(name)):
                found = re.match(
                    r"FOR UNITED STATES REPRESENTATIVE, (\d+)(?:ST|ND|RD|TH) CONGRESSIONAL DISTRICT - (DEM|REP)",
                    sheet[0][0] if sheet and sheet[0] else "",
                )
                if found is None or len(sheet) < 4:
                    continue
                header, sub = sheet[1], sheet[2]
                starts = [index for index, text in enumerate(header) if text and index >= 2]
                totals = [line for line in sheet[3:] if line and line[0].lower().startswith("total")]
                for position, start in enumerate(starts):
                    end = starts[position + 1] if position + 1 < len(starts) else len(sub)
                    column = next((index for index in range(start, end) if sub[index] == "Total Votes"), None)
                    if column is None or not totals:
                        continue
                    rows.append(
                        (
                            date(2012, 3, 13),
                            f"house:{PARTIES[found[2]]}",
                            int(found[1]),
                            header[start].upper(),
                            county,
                            int(float(totals[-1][column])),
                        )
                    )
    return rows


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CERT_2006).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = parse_2006(text, fips) + parse_2012(raw.cached(SOURCE, ZIP_2012).path, fips)
    for (party, district), (names, counties, totals) in RUNOFF_2010.items():
        for column, name in enumerate(names):
            if sum(votes[column] for votes in counties.values()) != totals[column]:
                raise ValueError(f"{party} {district} {name}: county rows do not add up to the printed total")
            for county, votes in counties.items():
                rows.append(
                    (date(2010, 7, 13), f"house:{party}", district, name.upper(), fips[county.lower()], votes[column])
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("AL"),
        )
    )
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "chamber", "district")
        .agg(counties=pl.col("code").n_unique(), votes=pl.col("votes").sum())
        .sort("day", "chamber", "district")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2006, 2010, and 2012 House primaries by county",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            license="Public record",
        )
        for url in (CERT_2006, ZIP_2012):
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"AL": source}, strict=True)
        return cs.write()
