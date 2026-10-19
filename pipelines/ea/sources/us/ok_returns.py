import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://oklahoma.gov/elections/elections-results/election-results/1998-election-results/"
FILES = [SITE + "98gencon.html", SITE + "98genst.html"]
DAY = date(1998, 11, 3)
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Official results of the general election, November 3, 1998"


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    done: set[str] = set()
    for url in FILES:
        text = html.unescape(raw.cached(SOURCE, url).path.read_text()).replace("\\n", "\n").replace('\\"', '"')
        for office, body in re.findall(r"<h3>([^<]+)</h3>\s*</span>(.*?)(?=<h3>|$)", text, flags=re.DOTALL):
            if office in ("GOVERNOR", "UNITED STATES SENATOR"):
                chamber, district = "governor" if office == "GOVERNOR" else "senate", None
            elif seat := re.fullmatch(r"UNITED STATES REPRESENTATIVE, DISTRICT (\d+)", office):
                chamber, district = "house", int(seat[1])
            else:
                continue
            if office in done:
                continue
            done.add(office)
            lines = re.sub(r"<[^>]+>", "", body).splitlines()
            rule = next(n for n, line in enumerate(lines) if "===" in line)
            first, second = (re.split(r"\s{2,}", lines[n].strip()) for n in (rule - 2, rule - 1))
            if len(first) != len(second) or second[-1] != "VOTES":
                raise ValueError(f"{office}: {first} and {second} do not line up")
            names = [
                re.sub(r"\s*\(\w+\)$", "", f"{given} {last}").upper() for given, last in zip(first, second, strict=True)
            ][:-1]
            seen = 0
            for line in lines[rule + 1 :]:
                if not (row := re.fullmatch(r"(\S.*?)\s+((?:[\d,]+\s+)+[\d,]+)\s*", line)):
                    continue
                votes = [int(value.replace(",", "")) for value in row[2].split()]
                if len(votes) != len(names) + 1 or sum(votes[:-1]) != votes[-1]:
                    raise ValueError(f"{office} {row[1]}: {votes}")
                if row[1].startswith("STATE TOTAL"):
                    continue
                seen += 1
                rows.extend(
                    {
                        "district": district,
                        "chamber": chamber,
                        "code": codes[re.sub(r"[^a-z]", "", row[1].lower())],
                        "candidate": name,
                        "votes": value,
                    }
                    for name, value in zip(names, votes, strict=False)
                )
            if not seen:
                raise ValueError(f"{office}: no counties")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("OK"),
            district=pl.col("district").cast(pl.Int16),
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("OK"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 1998 governor, U.S. Senate, and House general elections by county",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE + "98gshell.html",
            license="Public record",
        )
        for url in FILES:
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, frame, {"OK": source}, strict=True)
        return cs.write()
