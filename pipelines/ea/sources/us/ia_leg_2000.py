import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ia_legislature import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ia_leg_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Iowa Official Results, November 7, 2000 General Election: State Senate and State House of Representatives"
DAY = date(2000, 11, 7)
WAYBACK = "https://web.archive.org/web/{}id_/http://sos.iowa.gov/elections/pdf/2000%20G%20{}.pdf"
HOUSE_TITLE = "Iowa Official Results, November 7, 2000 General Election: U.S. House of Representatives"
FILES = {"senate": ("20130128043033", "IS"), "house": ("20130128043800", "IH"), "us_house": ("2013", "USH%20USS")}
DISTRICT = re.compile(r"^\s*(\d+)\w\w District:\s*$")
JUNK = re.compile(
    r"^\s*-\s*\d+\s*-\s*$|General Election|Iowa Official Results|^\s*State (?:Senate|House)|^\s*U\.S\. House"
)
ROW = re.compile(r"^\s*([A-Z][A-Za-z.' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")


def fetch() -> None:
    for file in FILES.values():
        raw.fetch(SOURCE, WAYBACK.format(*file), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
            ).fetchall()
        }
    rows = []
    for chamber, file in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, WAYBACK.format(*file)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        district: int | None = None
        centers: list[float] = []
        names: list[list[str]] = []
        parts: list[tuple[str, dict[int, int]]] = []
        broken = False
        for line in text.splitlines():
            if found := DISTRICT.match(line):
                district, centers, names, parts, broken = int(found[1]), [], [], [], False
                continue
            if district is None:
                continue
            if not centers and line.rstrip().endswith("Totals"):
                centers = [m.start() + len(m[0]) / 2 for m in re.finditer(r"\S+(?: \S+)*", line)][:-2]
                names = [[] for _ in centers]
                continue
            if not centers:
                continue
            if (row := ROW.match(line)) is None:
                if not parts and not JUNK.search(line):
                    for chunk in re.finditer(r"\S+(?: \S+)*", line):
                        center = chunk.start() + len(chunk[0]) / 2
                        names[min(range(len(centers)), key=lambda k: abs(centers[k] - center))].append(chunk[0])
                continue
            numbers = [m for m in re.finditer(r"\d[\d,]*", line) if m.start() >= row.start(2)]
            cells = {
                min(range(len(centers)), key=lambda k: abs(centers[k] - m.start() - len(m[0]) / 2)): int(
                    m[0].replace(",", "")
                )
                for m in numbers[:-2]
            } | {len(centers) + k: int(m[0].replace(",", "")) for k, m in enumerate(numbers[-2:])}
            if len(cells) != len(numbers):
                broken = True
            if row[1] != "Totals":
                parts.append((counties[re.sub(r"[^a-z]", "", row[1].lower())], cells))
                continue
            columns = range(len(centers) + 1)
            sums = {k: sum(values.get(k, 0) for _, values in parts) for k in columns}
            labels = [" ".join(names[k]) for k in range(len(centers))] + ["SCATTERING"]
            if (
                broken
                or any(sums[k] != cells.get(k, 0) for k in columns)
                or sum(sums.values()) != cells[len(centers) + 1]
                or any(sums[k] and not labels[k] for k in columns)
            ):
                print(f"{chamber} {district}: {labels} counties sum to {sums}, printed {cells}, skipped")
            else:
                rows.extend(
                    (
                        "house" if chamber == "us_house" else f"{chamber}:{district}:g",
                        district if chamber == "us_house" else None,
                        code,
                        labels[k].upper(),
                        values.get(k, 0),
                        k == len(centers),
                    )
                    for code, values in parts
                    for k in columns
                    if labels[k]
                )
            district = None
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    )
    print(
        frame.group_by(pl.col("chamber").str.split(":").list.first().alias("office")).agg(
            pl.concat_str("chamber", "district", ignore_nulls=True).n_unique()
        )
    )
    frame.with_columns(
        candidate=pl.col("candidate").str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("IA"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2000 U.S. House, State Senate, and State House generals by county (Secretary of State official results, via the Wayback Machine)",
            polity="US-IA",
        )
        frame = pl.read_parquet(PARSED / "counties.parquet")
        for title, house in ((TITLE, False), (HOUSE_TITLE, True)):
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=LANDING,
                    license="Public record",
                )
            for chamber, file in FILES.items():
                if (chamber == "us_house") == house:
                    cs.artifact(raw.cached(SOURCE, WAYBACK.format(*file)), source)
            medsl.attach(cs, conn, frame.filter((pl.col("chamber") == "house") == house), {"IA": source}, strict=True)
        return cs.write()
