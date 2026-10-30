import csv
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ms_supreme"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-357"
INBOX_363 = DATA / "inbox" / "manual" / "MF-363"
BASE = "https://www.sos.ms.gov/elections-voting/results/manual/"
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official election results by county"
CSVS = {
    "2020 General Election Official Statewide Recapitulation (CSV).csv": date(2020, 11, 3),
    "2024ElectionRecapSheets.csv": date(2024, 11, 5),
}
PDFS = {
    "Supreme Court- District 1, Position 1.pdf": date(2012, 11, 6),
    "Supreme Court- District 1, Position 2.pdf": date(2012, 11, 6),
    "Supreme Court- District 2, Position 3.pdf": date(2012, 11, 6),
    "Supreme Court- District 3, Position 3.pdf": date(2012, 11, 6),
}
CERTS_2004 = {
    "2004/CertCentral.pdf": (1, 1, ("RICHARD RAY GRINDSTAFF", "WILLIAM L. BILL WALLER")),
    "2004/CertNorthern.pdf": (3, 3, ("WILLIAM L. BILL BAMBACH", "GEORGE C. CARLSON")),
}
OCR_FIXES_2004 = {("2004/CertNorthern.pdf", "tallahatchie", 0): 1437}
TOTALS_2018 = {
    "Adams": 7765,
    "Amite": 4185,
    "Clarke": 5010,
    "Covington": 5662,
    "Forrest": 18355,
    "Franklin": 2277,
    "George": 5702,
    "Greene": 2860,
    "Hancock": 10026,
    "Harrison": 41017,
    "Jackson": 31043,
    "Jasper": 5119,
    "Jefferson Davis": 3439,
    "Jones": 19384,
    "Lamar": 16408,
    "Lawrence": 3949,
    "Lincoln": 10103,
    "Marion": 7316,
    "Pearl River": 11336,
    "Perry": 3018,
    "Pike": 9871,
    "Simpson": 7042,
    "Smith": 5135,
    "Stone": 4112,
    "Walthall": 3644,
    "Wayne": 6046,
    "Wilkinson": 1948,
}
COUNTY_SHEETS_2018 = [f"2018/{county} ({2 if county == 'Jefferson Davis' else 1}).pdf" for county in TOTALS_2018]
SUMMARY_2016 = "Supreme Court Judge, District 1, Position 3.pdf"
RECAP = "2016 GE Statewide Recap Report.pdf"
CERTIFIED_2016 = {
    (1, 3, "KENNY GRIFFIS"): 169084,
    (1, 3, "JIM KITCHENS"): 198130,
    (2, 2, "DAWN H. BEAM"): 232094,
    (2, 2, "MICHAEL T. SHAREEF"): 113464,
    (3, 2, "JIMMY MAXWELL"): 296384,
}
METHOD = (
    "County totals come from the Secretary of State's official statewide recapitulation reports and certified county summations. "
    "Mississippi elects each Supreme Court justice by district and position, so each contest covers only that district's counties."
)


def link(name: str) -> str:
    return f"{BASE}{name.replace(' ', '%20')}"


def fetch() -> None:
    for name in [*CSVS, *PDFS, RECAP, SUMMARY_2016, *CERTS_2004, *COUNTY_SHEETS_2018]:
        path = (INBOX_363 if name in CERTS_2004 or name in COUNTY_SHEETS_2018 else INBOX) / name
        raw.store(
            SOURCE,
            link(name),
            path.read_bytes(),
            suffix=Path(name).suffix,
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="text/csv" if name in CSVS else "application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%'"""
            ).fetchall()
        }
    codes["jeffdavis"] = codes["jeffersondavis"]
    rows: list[tuple[date, int, int, str, str, int]] = []
    for name, day in CSVS.items():
        sums: dict[tuple[str, str], int] = {}
        printed: dict[tuple[str, str], int] = {}
        text = raw.cached(SOURCE, link(name)).path.read_text(encoding="utf-8-sig")
        for row in csv.DictReader(text.splitlines()):
            found = re.search(r"Supreme Court District (\d)\([A-Za-z]+\) Position (\d)", row["Office"])
            if found is None:
                continue
            candidate = row["Candidate"].strip().upper()
            votes = int(row["County Total"])
            rows.append(
                (
                    day,
                    int(found[1]),
                    int(found[2]),
                    candidate,
                    codes[re.sub(r"[^a-z]", "", row["County"].lower())],
                    votes,
                )
            )
            key = (row["Office"], candidate)
            sums[key] = sums.get(key, 0) + votes
            printed[key] = int(row["State Total"])
        for key, summed in sums.items():
            if printed[key] != summed:
                raise ValueError(f"{name} {key}: counties sum to {summed}, printed total {printed[key]}")
    for name, day in PDFS.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link(name)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        seat = re.search(r"District (\d) .*?, Position (\d)", " ".join(lines[:4]))
        county = None
        sums: dict[str, int] = {}
        printed: dict[str, int] = {}
        for line in lines:
            if line.startswith("Totals"):
                county = "Totals"
            found = re.match(r"^(?:(\S.*?)\s{2,}|\s+)?(\S.*?)\s{2,}([\d,]+)(?:\s+[\d.]+%)?\s*$", line)
            if found is None or found[2].startswith("Total For"):
                continue
            if found[1] is not None:
                county = found[1].strip()
            if found[2] == "CANDIDATE" or county is None:
                continue
            votes = int(found[3].replace(",", ""))
            candidate = re.sub(r"^Totals\s+", "", found[2]).upper()
            if county == "Totals":
                printed[candidate] = votes
            else:
                rows.append(
                    (day, int(seat[1]), int(seat[2]), candidate, codes[re.sub(r"[^a-z]", "", county.lower())], votes)
                )
                sums[candidate] = sums.get(candidate, 0) + votes
        if printed != sums:
            raise ValueError(f"{name}: counties sum to {sums}, printed totals {printed}")
    for name, (district, position, names) in CERTS_2004.items():
        sums = dict.fromkeys(names, 0)
        printed = {}
        county = None
        county_label = None
        seen = 0
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(
                ["pdftoppm", "-r", "200", "-png", str(raw.cached(SOURCE, link(name)).path), f"{scratch}/page"],
                check=True,
            )
            for image in sorted(Path(scratch).glob("page-*.png"))[1:]:
                text = subprocess.run(
                    ["tesseract", str(image), "-", "--psm", "6"], capture_output=True, text=True, check=True
                ).stdout
                for line in text.splitlines():
                    votes = re.search(r"([\d,.]+),?\s+[\d.]+%[\s,.]*$", line)
                    label = re.sub(r"[^a-z]", "", line.lower())
                    if votes is not None:
                        total = OCR_FIXES_2004.get((name, county_label, seen), int(re.sub(r"\D", "", votes[1])))
                        if county is None:
                            printed[names[seen]] = total
                        else:
                            rows.append((date(2004, 11, 2), district, position, names[seen], county, total))
                            sums[names[seen]] += total
                        seen += 1
                    elif label == "statetotals":
                        county = None
                        seen = 0
                    elif label in codes:
                        county = codes[label]
                        county_label = label
                        seen = 0
        if printed != sums:
            raise ValueError(f"{name}: counties sum to {sums}, printed totals {printed}")
    for county, votes in TOTALS_2018.items():
        rows.append((date(2018, 11, 6), 2, 1, "DAVID M. ISHEE", codes[re.sub(r"[^a-z]", "", county.lower())], votes))
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, link(RECAP)).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    ordered = sorted(set(codes.values()))
    counties: list[str] | None = None
    done = 0
    last: tuple[int, int, str] | None = None
    seat: tuple[int, int] | None = None
    sums: dict[tuple[int, int, str], int] = {}
    printed_2016: dict[tuple[int, int, str], int] = {}
    for index, line in enumerate(lines):
        if line.strip().startswith("day of November 2016"):
            seat = None
            counties = None
            continue
        cells = line.split()
        if last is not None and len(cells) == 1 and cells[0].isdigit():
            printed_2016[last] = int(f"{printed_2016[last]}{cells[0]}")
            last = None
            continue
        if cells:
            last = None
        if re.fullmatch(r"\s*\w+ District-Supreme Court District", line):
            found = re.search(r"(\d)\(\w+\) Position (\d)", lines[index + 1])
            seat = (int(found[1]), int(found[2]))
            continue
        if re.match(r"\s*(?:\d\(\w+\) Position|Statewide Election)", line):
            continue
        start = next((i for i, cell in enumerate(cells) if cell == "X" or cell.replace(",", "").isdigit()), None)
        if start is None or not all(cell == "X" or cell.replace(",", "").isdigit() for cell in cells[start:]):
            if re.search(r"\b(?:Position|Congressional|District|Court|Senate)\b", line) and not re.match(
                r"\s*\d\(\w+\) Position", line
            ):
                seat = None
            continue
        if counties is None:
            take = ordered[done : done + len(cells) - start]
            counties = [*take, *(["TOTAL"] if len(take) < len(cells) - start else [])]
            done = 0 if len(take) < len(cells) - start else done + len(take)
        if seat is None:
            continue
        name = " ".join(cells[:start]).upper()
        for county, cell in zip(counties, cells[start:], strict=True):
            if county == "TOTAL" and cell != "X":
                last = (*seat, name)
                printed_2016[last] = int(cell.replace(",", ""))
            elif cell != "X" and county != "TOTAL":
                rows.append((date(2016, 11, 8), seat[0], seat[1], name, county, int(cell.replace(",", ""))))
                sums[(*seat, name)] = sums.get((*seat, name), 0) + int(cell.replace(",", ""))
    if sums != printed_2016 or any(sums[key] != total for key, total in CERTIFIED_2016.items()):
        raise ValueError(f"2016 recap sums {sums} differ from the printed totals {printed_2016}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema=["day", "district", "position", "candidate", "county", "votes"], orient="row"
    ).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        day="day",
        state=pl.lit("MS"),
        title=pl.lit("Justice of the Supreme Court"),
        post=pl.format("DISTRICT {} POSITION {}", "district", "position"),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(82),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "post")
        .agg(pl.col("votes").sum(), pl.col("county").n_unique())
        .sort("year", "post")
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.ms.gov/elections-voting/results",
                license="Public record",
            )
        for name in [*CSVS, *PDFS, RECAP, SUMMARY_2016, *CERTS_2004, *COUNTY_SHEETS_2018]:
            cs.artifact(raw.cached(SOURCE, link(name)), source)
        return {"MS": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Mississippi's Supreme Court district elections by county from the Secretary of State's official results",
        cite=cite,
        method=METHOD,
    )
