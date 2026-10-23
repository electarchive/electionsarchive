import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_house_generals import BASE, LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ne_leg_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Official Report of the Board of State Canvassers, Primary Election, May 13, 2014"
DAY = date(2014, 5, 13)
PATH = "elections/2014/2014-Final-Canvas-PR14-After-6-11-14-Recount.pdf"
DISTRICT = re.compile(r"\s*District (\d+)\b.*")
ROW = re.compile(r"\s*([A-Z][A-Za-z ]+?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*")


def fetch() -> None:
    raw.fetch(SOURCE, BASE + PATH, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + PATH).path), "-"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    start = next(k for k, line in enumerate(lines) if re.fullmatch(r"\s*Member of the Legislature\s*", line))
    end = next(k for k in range(start + 1, len(lines)) if re.match(r"\s*Member of the State Board", lines[k]))
    rows = []
    district = 0
    header: list[str] = []
    centers: list[float] = []
    names: list[str] = []
    parts: list[tuple[str, list[int]]] = []
    for line in lines[start + 1 : end]:
        if found := DISTRICT.fullmatch(line):
            if int(found[1]) <= district:
                break
            district, header, centers, names, parts = int(found[1]), [], [], [], []
            continue
        if not district or not line.strip() or line.strip().startswith("Page "):
            continue
        row = ROW.fullmatch(line)
        if row is None:
            if not centers:
                header.append(line)
            continue
        votes = [int(value.replace(",", "")) for value in row[2].split()]
        if not centers:
            centers = [m.start() + len(m[0]) / 2 for m in re.finditer(r"\d[\d,]*", line) if m.start() >= row.start(2)]
            words: list[list[str]] = [[] for _ in centers]
            for text in header:
                for chunk in re.finditer(r"\S+(?: \S+)*", text):
                    if chunk[0] != "County":
                        center = chunk.start() + len(chunk[0]) / 2
                        words[min(range(len(centers)), key=lambda k: abs(centers[k] - center))].append(chunk[0])
            names = [" ".join(group) for group in words]
        if len(votes) != len(names) or not all(names):
            raise ValueError(f"legislature {district}: {line.strip()} against {names}")
        if row[1] != "TOTAL":
            parts.append((counties[re.sub(r"[^a-z]", "", row[1].lower())], votes))
            continue
        if [sum(values[k] for _, values in parts) for k in range(len(names))] != votes:
            raise ValueError(f"legislature {district}: {names} counties do not add up to {votes}")
        rows.extend(
            (f"legislature:{district}:ttp", code, names[k], values[k])
            for code, values in parts
            for k in range(len(names))
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    with pl.Config(tbl_rows=150, fmt_str_lengths=40):
        print(frame.group_by("chamber", "candidate", maintain_order=True).agg(pl.col("votes").sum(), pl.len()))
    frame.with_columns(
        candidate=pl.col("candidate").str.to_uppercase().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        day=pl.lit(DAY),
        year=pl.lit(DAY.year, pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("NE"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NE"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2014 Legislature primaries by county (Board of State Canvassers' official report)",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, BASE + PATH), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"NE": source},
            strict=True,
            authoritative="Source: the Board of State Canvassers' official report, after the June 2014 recounts; the "
            "earlier set's figures run about 4 percent lower.",
        )
        return cs.write()
