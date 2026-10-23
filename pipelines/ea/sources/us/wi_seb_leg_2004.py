import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.wi_seb_primaries import LANDING, PUBLISHER, URL
from ea.store import DATA, connect

SOURCE = "us.wi_seb_leg_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Canvass Summary, Fall General Election 2004: State Senator and State Assembly"
DAY = date(2004, 11, 2)
HOUSE_TITLE = "Canvass Summary, Fall General Election 2004: US Congress"
DOCS = {1414: "assembly", 1438: "senate", 1423: "house"}
OFFICE = re.compile(r"\s*(?:State Assembly|State Senate|US Congress), District No\. (\d+)\s*")
ROW = re.compile(r"^\s*(\S.*?)\s*:?\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$")


def fetch() -> None:
    for doc in DOCS:
        raw.fetch(SOURCE, URL.format(doc), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    rows = []
    carried: dict[tuple[str, int], dict[str, list[int]]] = {}
    for doc, chamber in DOCS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(doc)).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        for page in text.split("\f"):
            lines = page.splitlines()
            title = next((k for k, line in enumerate(lines) if OFFICE.fullmatch(line)), None)
            if title is None:
                continue
            district = int(OFFICE.fullmatch(lines[title])[1])
            body = lines[title + 1 :]
            stop = next(k for k, line in enumerate(body) if ROW.match(line))
            ends: list[int] = []
            for line in body[stop:]:
                if row := ROW.match(line):
                    for k, cell in enumerate(re.finditer(r"[\d,]+", line[row.start(2) :])):
                        if k == len(ends):
                            ends.append(0)
                        ends[k] = max(ends[k], row.start(2) + cell.end())
            words: dict[int, list[str]] = {}
            for line in body[:stop]:
                if re.fullmatch(r"(?:\s*[A-Z]{3})*\s*", line):
                    continue
                for match in re.finditer(r"\S+", line):
                    if match[0] not in ("Total", "Votes", "Cast"):
                        column = next((k for k in range(1, len(ends)) if ends[k] >= match.start()), len(ends) - 1)
                        words.setdefault(column, []).append(match[0])
            names = [" ".join(words.get(k, [])) for k in range(1, len(ends))]
            parts: dict[str, list[int]] = {}
            totals: list[int] = []
            for line in body[stop:]:
                if (row := ROW.match(line)) is None:
                    continue
                cells = [int(cell.replace(",", "")) for cell in row[2].split()]
                if row[1].startswith("Office Totals"):
                    totals = cells
                else:
                    parts[counties[re.sub(r"[^a-z]", "", row[1].lower().removesuffix("county"))]] = cells
            if not totals:
                carried[(chamber, district)] = parts
                continue
            parts = carried.pop((chamber, district), {}) | parts
            sums = [sum(values[k] for values in parts.values()) for k in range(len(ends))]
            if not all(names) or sums != totals or any(len(values) != len(ends) for values in parts.values()):
                print(f"{chamber} {district}: {names} counties sum to {sums}, printed {totals}, skipped")
                continue
            rows.extend(
                (
                    chamber if chamber == "house" else f"{chamber}:{district}:g",
                    district if chamber == "house" else None,
                    code,
                    name.upper(),
                    values[k + 1],
                    name.upper().startswith("SCATTER"),
                )
                for code, values in parts.items()
                for k, name in enumerate(names)
            )
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
        state=pl.lit("WI"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WI"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's 2004 U.S. House, State Senate, and State Assembly generals by county (State Elections Board canvass summaries, via the Wayback Machine)",
            polity="US-WI",
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
            for doc, chamber in DOCS.items():
                if (chamber == "house") == house:
                    cs.artifact(raw.cached(SOURCE, URL.format(doc)), source)
            medsl.attach(cs, conn, frame.filter((pl.col("chamber") == "house") == house), {"WI": source}, strict=True)
        return cs.write()
