import csv
import io
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import me_federal, medsl
from ea.store import DATA, connect

SOURCE = "us.me_pri_csv"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
MAINE = "http://www.maine.gov/sos/cec/elec/"
LLDC = "https://lldc.mainelegislature.org/Open/Elections/Returns/2000/20000613/elec_20000613{}.pdf"
FILES_2000 = [
    ("k", "house", "democratic"),
    ("l", "house", "republican"),
    ("m", "senate", "democratic"),
    ("n", "senate", "republican"),
]
COUNTY_NAMES = {
    c
    for c in [
        "ANDROSCOGGIN",
        "AROOSTOOK",
        "CUMBERLAND",
        "FRANKLIN",
        "HANCOCK",
        "KENNEBEC",
        "KNOX",
        "LINCOLN",
        "OXFORD",
        "PENOBSCOT",
        "PISCATAQUIS",
        "SAGADAHOC",
        "SOMERSET",
        "WALDO",
        "WASHINGTON",
        "YORK",
    ]
}
DISTRICTS_2000 = {"ALLEN": 1, "BALDACCI": 2, "AMERO": 1, "CAMPBELL": 2, "WINSLOW": 2}
FILES = [
    (date(2002, 6, 11), "20030728201528", MAINE + "2002p/pri02c1d.txt", "democratic"),
    (date(2002, 6, 11), "20030728201703", MAINE + "2002p/pri02c1r.txt", "republican"),
    (date(2002, 6, 11), "20030912070242", MAINE + "2002p/pri02c2d.txt", "democratic"),
    (date(2002, 6, 11), "20030728202334", MAINE + "2002p/pri02c2r.txt", "republican"),
    (date(2002, 6, 11), "20030728205741", MAINE + "2002p/pri02sd.txt", "democratic"),
    (date(2002, 6, 11), "20030728210240", MAINE + "2002p/pri02sr.txt", "republican"),
    (date(2004, 6, 8), "20040816152147", MAINE + "2004/pri04cd.txt", "democratic"),
    (date(2004, 6, 8), "20040816160545", MAINE + "2004/pri04cr.txt", "republican"),
    (date(2006, 6, 13), "20061104163036", MAINE + "2006p/pri06c1d.txt", "democratic"),
    (date(2006, 6, 13), "20061104162934", MAINE + "2006p/pri06c1r.txt", "republican"),
    (date(2006, 6, 13), "20061104162527", MAINE + "2006p/pri06c2d.txt", "democratic"),
    (date(2006, 6, 13), "20061104162741", MAINE + "2006p/pri06c2r.txt", "republican"),
    (date(2006, 6, 13), "20061104163114", MAINE + "2006p/pri06sd.txt", "democratic"),
    (date(2006, 6, 13), "20061104163019", MAINE + "2006p/pri06sr.txt", "republican"),
    (date(2008, 6, 10), "20080814085732", MAINE + "2008/tabs-can-cg-dem-1st-district-6-08.txt", "democratic"),
    (date(2008, 6, 10), "20080814085658", MAINE + "2008/tabs-can-cg-dem-2nd-district-6-08.txt", "democratic"),
    (date(2008, 6, 10), "20080814085236", MAINE + "2008/tabs-can-cg-rep-1st-district-6-08.txt", "republican"),
    (date(2008, 6, 10), "20080814085148", MAINE + "2008/tabs-can-cg-rep-2nd-district-6-08.txt", "republican"),
    (date(2008, 6, 10), "20080814085445", MAINE + "2008/tabs-can-us-dem-6-08.txt", "democratic"),
    (date(2008, 6, 10), "20080814085224", MAINE + "2008/tabs-can-us-rep-6-08.txt", "republican"),
    (date(2012, 6, 12), "20120822192430", MAINE + "2012/d-repcng1.txt", "democratic"),
    (date(2012, 6, 12), "20120802012936", MAINE + "2012/r-repcng1.txt", "republican"),
    (date(2012, 6, 12), "20120822192804", MAINE + "2012/r-repcng2.txt", "republican"),
    (date(2012, 6, 12), "20120822192742", MAINE + "2012/d-senator.txt", "democratic"),
    (date(2012, 6, 12), "20120822192806", MAINE + "2012/r-senator.txt", "republican"),
    (
        date(2016, 6, 14),
        "20170203093541",
        "https://www1.maine.gov/sos/cec/elec/results/2016/Rep%20to%20Congress%20DEM.xlsx",
        "democratic",
    ),
    (
        date(2016, 6, 14),
        "20170201143509",
        "http://maine.gov/sos/cec/elec/results/2016/Rep%20to%20Congress%20REP.xlsx",
        "republican",
    ),
]


def fetch() -> None:
    for letter, _, _ in FILES_2000:
        raw.fetch(SOURCE, LLDC.format(letter), suffix=".pdf")
    for _, stamp, path, _ in FILES:
        url = WAYBACK.format(stamp, path)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "--resolve", "web.archive.org:443:207.241.237.2", "-A", raw.USER_AGENT, url],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE,
                url,
                body,
                suffix=path[-5:] if path.endswith("xlsx") else ".txt",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if path.endswith("xlsx")
                else "text/plain",
            )


def parse() -> None:
    rows = []
    for letter, chamber, party in FILES_2000:
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, LLDC.format(letter)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        county = None
        names: list[str] = []
        pending = None
        for position, line in enumerate(lines):
            text = line.strip()
            if not text:
                continue
            ahead = next(
                (
                    n
                    for n in lines[position + 1 : position + 12]
                    if re.fullmatch(r"[A-Z][A-Z .'-]+, [A-Z][A-Z .'-]+(\s{2,}.+)?", n.strip())
                ),
                "",
            )
            if ahead and text in COUNTY_NAMES:
                county = text[:3]
                names = [n.strip() for n in re.split(r"\s{2,}", ahead.strip())]
                continue
            if county is None or re.search(r"(Ward \d|Central Voting|Precinct \d)", text):
                continue
            if re.fullmatch(r"[A-Z][A-Z .'-]+", text):
                pending = text
                continue
            tokens = [t for t in text.split() if "%" not in t]
            tail = len(tokens)
            while tail and re.fullmatch(r"[\d,]+", tokens[tail - 1]):
                tail -= 1
            if tail == len(tokens):
                continue
            tail = max(tail, len(tokens) - len(names))
            label = " ".join(tokens[:tail])
            values = [int(v.replace(",", "")) for v in tokens[tail:]]
            if len(values) != len(names) or not re.search(r"[A-Za-z]{2}", label) or label == "County Total":
                continue
            if label == "Municipal Total":
                label = pending or label
            for name, votes in zip(names, values, strict=True):
                surname = name.split(",")[0]
                rows.append(
                    (
                        date(2000, 6, 13),
                        f"{chamber}:{party}",
                        DISTRICTS_2000.get(surname) if chamber == "house" else None,
                        county,
                        label,
                        name,
                        votes,
                    )
                )
    for day, stamp, path, party in FILES:
        cached = raw.cached(SOURCE, WAYBACK.format(stamp, path)).path
        if path.endswith("xlsx"):
            workbook = fastexcel.read_excel(cached)
            for number, sheet in enumerate(workbook.sheet_names, 1):
                table = workbook.load_sheet(sheet, header_row=None, dtypes="string").to_polars().rows()
                names = [" ".join((cell or "").upper().split()) for cell in table[0][2:]]
                for row in table[2:]:
                    town = (row[1] or "").strip().upper()
                    if not town or "TOTAL" in town:
                        continue
                    for name, votes in zip(names, row[2:], strict=True):
                        if name not in ("BLANK", "TOTAL BALLOTS CAST"):
                            rows.append((day, f"house:{party}", number, row[0] or "", town, name, int(votes or 0)))
            continue
        text = cached.read_text(encoding="latin-1")
        if "/2012/" in path:
            names: list[str] = []
            fresh = True
            for line in text.splitlines():
                cells = line.split("|")
                if line.startswith("|||") and "VOTES1" not in line:
                    if fresh:
                        names = [re.sub(r"\s*\(Declared Write-In\)", "", c).strip().upper() for c in cells[3:-2:2]]
                        fresh = False
                elif cells[0] == "DISTRICT" or "VOTES1" in line:
                    fresh = True
                elif len(cells) > 3 and (cells[1] in me_federal.COUNTIES or "UOCAVA" in cells[2]):
                    fresh = True
                    for i, name in enumerate(names):
                        if name != "BLANK":
                            rows.append(
                                (
                                    day,
                                    f"{'senate' if 'senator' in path else 'house'}:{party}",
                                    int(cells[0]) if cells[0] else None,
                                    cells[1],
                                    cells[2],
                                    name,
                                    int(cells[3 + 2 * i] or 0),
                                )
                            )
            continue
        for record in csv.DictReader(io.StringIO(text)):
            record = {key.strip().lower(): (value or "").strip() for key, value in record.items() if key}
            if "/2008/" in path:
                district = record.get("dist_#")
                rows.append(
                    (
                        day,
                        f"{'senate' if record['office_code' if 'office_code' in record else 'off_code'] == 'US' else 'house'}:{party}",
                        int(district) if district else None,
                        record["county_code"],
                        record["town_name"],
                        " ".join(record["can_name"].upper().split()),
                        int(record["votes"] or 0),
                    )
                )
                continue
            office = record["office"]
            district = int(record["district"]) if record["district"] else None
            for number in [k.removeprefix("fullname") for k in record if k.startswith("fullname")]:
                name = record[f"fullname{number}"]
                if name:
                    rows.append(
                        (
                            day,
                            f"{'senate' if office == 'US' else 'house'}:{party}",
                            district,
                            record["county"],
                            record["town"],
                            name.upper(),
                            int(record[f"votes{number}"] or 0),
                        )
                    )
    frame = pl.DataFrame(
        rows,
        schema=["day", "chamber", "district", "county", "town", "candidate", "votes"],
        orient="row",
    )
    homes = frame.filter(pl.col("county") != "").group_by("town").agg(pl.col("county").mode().first().alias("home"))
    frame = frame.join(homes, on="town", how="left").with_columns(
        pl.when(pl.col("county") != "").then(pl.col("county")).otherwise(pl.col("home")).alias("county")
    )
    lost = frame.filter(~pl.col("county").is_in(list(me_federal.COUNTIES)) & ~pl.col("town").str.contains("UOCAVA"))
    if lost.height:
        raise ValueError(f"unplaced towns: {lost['town'].unique().to_list()[:10]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("ME"),
        code=pl.when(pl.col("town").str.contains("UOCAVA"))
        .then(pl.lit("ME:uocava"))
        .otherwise(pl.col("county").replace_strict(me_federal.COUNTIES, default=None)),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.when(pl.col("town").str.contains("UOCAVA")).then(pl.lit("aggregate")).otherwise(pl.lit("county")),
        source=pl.lit("ME"),
        district=pl.col("district").cast(pl.Int16),
    ).group_by(
        "year",
        "chamber",
        "day",
        "special",
        "state",
        "district",
        "code",
        "candidate",
        "measure",
        "writein",
        "kind",
        "source",
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2000, 2002, 2004, 2006, 2008, 2012, and 2016 U.S. Senate and House primaries by county, summed from the Secretary of State's town and ward files",
            polity="US-ME",
        )
        source = cs.source(me_federal.PUBLISHER, me_federal.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=me_federal.PUBLISHER,
            title=me_federal.TITLE,
            url="https://www.maine.gov/sos/cec/elec/results/",
            license="Public record",
        )
        for _, stamp, path, _ in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)
        for letter, _, _ in FILES_2000:
            cs.artifact(raw.cached(SOURCE, LLDC.format(letter)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
