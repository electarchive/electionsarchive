import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ne_house_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.nebraska.gov/sites/default/files/doc/"
LANDING = "https://sos.nebraska.gov/elections/previous-elections"
PUBLISHER = "Nebraska Secretary of State"
TITLE = "{year} General Election Canvass"
BOOKS = {
    2004: (date(2004, 11, 2), "2004%20General.pdf"),
    2010: (date(2010, 11, 2), "elections/2010/2010%20Gen%20Canvass%20Book%2011-30-Final.pdf"),
    2012: (date(2012, 11, 6), "elections/2012/2012-general-canvass.pdf"),
    2014: (date(2014, 11, 4), "elections/2014/2014-General-Canvass-Recount-Final.pdf"),
}
NUMBERS = {"one": 1, "two": 2, "three": 3, "1": 1, "2": 2, "3": 3}
DISTRICT = re.compile(r"\bDistrict (One|Two|Three|[123])\b", re.IGNORECASE)
MARKER = re.compile(r"\b(County|District|TOTAL)\b", re.IGNORECASE)
PARTY = re.compile(
    r"(Republican|Democrat|Democratic|Libertarian|Green|Nebraska|By Petition|Write In|Write-ins|Independent)",
    re.IGNORECASE,
)
SEGMENT = re.compile(r"([A-Za-z][A-Za-z.']*(?: [A-Za-z.']+)*)\s+((?:\d[\d,]*\s+)*\d[\d,]*)(?=\s|$)")


def fetch() -> None:
    for _, path in BOOKS.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows = []
    for year, (day, path) in BOOKS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        start = [
            m.start() for m in re.finditer(r"Member of the U\.S\. House of Representatives\s*\n", text, re.IGNORECASE)
        ]
        names: dict[int, list[str]] = {}
        totals: dict[int, list[int]] = {}
        counts: dict[int, dict[str, list[int]]] = {}
        order: list[int] = []

        def done(district: int) -> bool:
            sums = [sum(cells[i] for cells in counts[district].values()) for i in range(len(names[district]))]
            return district in totals and sums == totals[district]

        for page in text[start[-1] :].split("\f"):
            lines = [re.sub(r"\s*/\s*\d+\s*prec\.?", lambda m: " " * len(m[0]), line) for line in page.splitlines()]
            split = min(
                (found[1].start() for line in lines if len(found := list(MARKER.finditer(line))) > 1),
                default=10**6,
            )
            sides: list[list[str]] = [[], []]
            cut = None
            for line in lines:
                markers = list(MARKER.finditer(line))
                pieces = [p for p in re.split(r"\s{2,}", line.strip()) if p]
                if len(markers) > 1:
                    cut = markers[1].start()
                elif not (cut is not None and pieces and all(PARTY.fullmatch(p) for p in pieces)):
                    starts = [m.start() for m in SEGMENT.finditer(line) if m.start() >= split - 2]
                    cut = starts[0] if starts and starts[0] > 0 else None
                if cut is None:
                    sides[0].append(line)
                else:
                    if pieces and all(PARTY.fullmatch(p) for p in pieces):
                        cut = next((m.start() for m in PARTY.finditer(line) if m.start() >= cut - 6), len(line))
                    sides[0].append(line[:cut])
                    sides[1].append(" " * cut + line[cut:])
                    if len(markers) <= 1:
                        cut = None
            for side in (0, 1):
                district = None
                header: list[str] = []
                for part in sides[side]:
                    if not part.strip():
                        continue
                    if found := DISTRICT.search(part):
                        district = NUMBERS[found[1].lower()]
                        if district not in names:
                            names[district], counts[district] = [], {}
                            order.append(district)
                        header = []
                        continue
                    pieces = [p for p in re.split(r"\s{2,}", part.strip()) if p]
                    if pieces and all(PARTY.fullmatch(p) for p in pieces):
                        current = district or next((d for d in reversed(order) if not done(d)), None)
                        if current is not None and header:
                            names[current] = [
                                p
                                for p in re.split(r"\s{2,}", header[-1].strip())
                                if p.lower() not in ("county", "candidate")
                            ]
                        header = []
                        continue
                    segments = list(SEGMENT.finditer(part))
                    if not segments:
                        header.append(part)
                        continue
                    current = district or next((d for d in reversed(order) if not done(d)), None)
                    if current is None or len(order) == 3 and all(done(d) for d in order):
                        continue
                    for segment in segments:
                        cells = [int(v.replace(",", "")) for v in segment[2].split()]
                        if len(cells) != len(names[current]):
                            continue
                        label = re.sub(r"[^a-z]", "", segment[1].lower())
                        if label == "total":
                            totals[current] = cells
                        elif label in counties:
                            counts[current][counties[label]] = cells
        for district in order:
            if not names[district] or not done(district):
                print(
                    f"{year} District {district}: {names[district]} counties {len(counts[district])}, not summing to {totals.get(district)}, skipped"
                )
                continue
            rows.extend(
                (year, day, district, code, name, cells[i], "write" in name.lower() or "scatter" in name.lower())
                for code, cells in counts[district].items()
                for i, name in enumerate(names[district])
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    ).with_columns(
        chamber=pl.lit("house"),
        special=pl.lit(False),
        state=pl.lit("NE"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("NE"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's U.S. House generals by county, 2004 and 2010-2014 (Secretary of State canvass books)",
            polity="US-NE",
        )
        for year, (_, path) in BOOKS.items():
            source = cs.source(PUBLISHER, TITLE.format(year=year))
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=TITLE.format(year=year),
                    url=LANDING,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
            medsl.attach(cs, conn, frame.filter(pl.col("year") == year), {"NE": source}, strict=True)
        return cs.write()
