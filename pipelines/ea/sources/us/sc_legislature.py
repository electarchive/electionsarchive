import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sc_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "South Carolina State Election Commission"
TITLE = "{year} Election Report"
LANDING = "https://scvotes.gov/election-results/"
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2)}
FIXES = str.maketrans({"&": "6", "l": "1", "I": "1", "O": "0", "o": "0", "S": "5", "B": "8", "Z": "2", "|": "1"})
HEADING = re.compile(r"STATE\s+(HOUSE OF REPRESENTATIVES|SENATE)\s+DISTRICT\s+(\d+)")
NUMBER = re.compile(r"[0-9&lIOoSBZ|][0-9&lIOoSBZ|.,]*")
COUNTIES = [
    "Abbeville", "Aiken", "Allendale", "Anderson", "Bamberg", "Barnwell", "Beaufort", "Berkeley", "Calhoun",
    "Charleston", "Cherokee", "Chester", "Chesterfield", "Clarendon", "Colleton", "Darlington", "Dillon",
    "Dorchester", "Edgefield", "Fairfield", "Florence", "Georgetown", "Greenville", "Greenwood", "Hampton", "Horry",
    "Jasper", "Kershaw", "Lancaster", "Laurens", "Lee", "Lexington", "McCormick", "Marion", "Marlboro", "Newberry",
    "Oconee", "Orangeburg", "Pickens", "Richland", "Saluda", "Spartanburg", "Sumter", "Union", "Williamsburg", "York",
]
ROW = re.compile(
    r"^\s*(" + "|".join(sorted((c.upper() for c in COUNTIES), key=len, reverse=True)) + r")\s+(\S.*)$|^\s*(STATE TOTAL)\s+(\S.*)$"
)


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, URL.format(year), suffix=".pdf")


def numbers(text: str) -> list[int] | None:
    found = text.split()
    if not found or not all(NUMBER.fullmatch(token) for token in found):
        return None
    try:
        return [int(re.sub(r"[.,]", "", token.translate(FIXES))) for token in found]
    except ValueError:
        return None


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%' order by u.code"""
            ).fetchall()
        }
        contests = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        rows: list[tuple[int, str, date, str, str, int, bool]] = []
        skipped = 0
        for year, day in DAYS.items():
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(year)).path), "-"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            blocks: list[tuple[str, int, list[str]]] = []
            for line in text.splitlines():
                found = HEADING.search(line)
                if found:
                    blocks.append(("house" if found[1].startswith("HOUSE") else "senate", int(found[2]), []))
                elif blocks:
                    blocks[-1][2].append(line)
            seen: set[str] = set()
            for body, district, lines in blocks:
                chamber = f"{body}:{district}:g"
                key = f"{day}:SC:{chamber}"
                if key not in contests or key in seen:
                    continue
                counties: list[tuple[str, list[int]]] = []
                total: list[int] | None = None
                for line in lines:
                    matched = ROW.match(line.upper())
                    if not matched:
                        continue
                    values = numbers(matched[2] or matched[4])
                    if values is None:
                        continue
                    if matched[3]:
                        total = values
                    else:
                        counties.append((matched[1], values))
                if total is None or not counties or any(len(v) != len(total) for _, v in counties):
                    skipped += 1
                    continue
                if [sum(v[i] for _, v in counties) for i in range(len(total))] != total:
                    skipped += 1
                    continue
                options = dict(
                    conn.execute(
                        """select o.ballot_label, sum(v.value)::bigint from contest_option o
                           join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                           join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                           where o.contest_id = %s and o.parent_option_id is null and o.ballot_label is not null
                           group by 1""",
                        [contests[key]],
                    ).fetchall()
                )
                by_total: dict[int, list[str]] = {}
                for label, votes in options.items():
                    by_total.setdefault(votes, []).append(label)
                names: list[tuple[str, bool]] = []
                for index, value in enumerate(total):
                    labels = by_total.get(value, [])
                    if len(labels) == 1:
                        names.append((labels[0].upper(), False))
                    elif not labels and index == len(total) - 1:
                        names.append(("WRITE-INS", True))
                    else:
                        break
                if len(names) != len(total) or len({n for n, _ in names}) != len(names):
                    skipped += 1
                    continue
                seen.add(key)
                for county, values in counties:
                    code = fips[re.sub(r"[^a-z]", "", county.lower())]
                    for (candidate, writein), votes in zip(names, values, strict=True):
                        rows.append((year, chamber, day, code, candidate, votes, writein))
        print(len({(r[0], r[1]) for r in rows}), "contests read,", skipped, "skipped")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
                "writein": pl.Boolean,
            },
            orient="row",
        )
        .with_columns(
            special=pl.lit(False),
            state=pl.lit("SC"),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, dtype=pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("SC"),
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
            summary="South Carolina's 2000, 2002, and 2004 state legislative general elections by county",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, "Election Reports 2000, 2002, and 2004")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="Election Reports 2000, 2002, and 2004",
            url=LANDING,
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, URL.format(year)), source)
        medsl.attach(cs, conn, frame, {"SC": source}, strict=True)
        return cs.write()
