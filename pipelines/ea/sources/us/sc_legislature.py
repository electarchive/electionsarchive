import difflib
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
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
FIXES = str.maketrans({"&": "6", "l": "1", "I": "1", "O": "0", "o": "0", "S": "5", "B": "8", "Z": "2", "|": "1"})
HEADING = re.compile(r"STATE\s+(HOUSE OF REPRESENTATIVES|SENATE)\s+DISTRICT\s+(\d+)")
PRECINCTS = re.compile(r"^\s*District\s+(\d+)\s+PRECINCTS")
NUMBER = re.compile(r"[0-9&lIOoSBZ|][0-9&lIOoSBZ|.,]*")
COUNTIES = [
    "Abbeville",
    "Aiken",
    "Allendale",
    "Anderson",
    "Bamberg",
    "Barnwell",
    "Beaufort",
    "Berkeley",
    "Calhoun",
    "Charleston",
    "Cherokee",
    "Chester",
    "Chesterfield",
    "Clarendon",
    "Colleton",
    "Darlington",
    "Dillon",
    "Dorchester",
    "Edgefield",
    "Fairfield",
    "Florence",
    "Georgetown",
    "Greenville",
    "Greenwood",
    "Hampton",
    "Horry",
    "Jasper",
    "Kershaw",
    "Lancaster",
    "Laurens",
    "Lee",
    "Lexington",
    "McCormick",
    "Marion",
    "Marlboro",
    "Newberry",
    "Oconee",
    "Orangeburg",
    "Pickens",
    "Richland",
    "Saluda",
    "Spartanburg",
    "Sumter",
    "Union",
    "Williamsburg",
    "York",
]
TOKEN = re.compile(r"\S+")


def parse_line(line: str) -> tuple[str | None, list[int], list[int]] | None:
    tokens = list(TOKEN.finditer(line.upper()))
    first = next((i for i, t in enumerate(tokens) if NUMBER.fullmatch(t[0])), None)
    if first is None or any(not NUMBER.fullmatch(t[0]) for t in tokens[first:]):
        return None
    values = numbers(" ".join(t[0] for t in tokens[first:]))
    if values is None:
        return None
    name = " ".join(t[0] for t in tokens[:first])
    ends = [t.end() for t in tokens[first:]]
    if re.search(r"T[OD0]TAL", name) and len(name) <= 16:
        return ("TOTAL", ends, values)
    return (re.sub(r"[^A-Z ]", "", name).strip() or None, ends, values)


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
                if year == 2006:
                    if (numbered := PRECINCTS.match(line)) is not None:
                        blocks.append(("house", int(numbered[1]), []))
                    elif blocks:
                        blocks[-1][2].append(line)
                    continue
                if found:
                    blocks.append(
                        (
                            "house" if found[1].startswith("HOUSE") else "senate",
                            -1 if "RUNOFF" in line else int(found[2]),
                            [],
                        )
                    )
                elif blocks:
                    blocks[-1][2].append(line)
            seen: set[str] = set()
            for body, district, lines in blocks:
                chamber = f"{body}:{district}:g"
                key = f"{day}:SC:{chamber}"
                if key not in contests or key in seen:
                    continue
                lines_ = [parse_line(line) for line in lines]
                lines_ = [item for item in lines_ if item is not None]
                total_at = next((i for i, item in enumerate(lines_) if item[0] == "TOTAL"), None)
                if total_at is None and len(lines_) >= 2 and lines_[-1][2] == lines_[-2][2] and lines_[-2][0] is None:
                    total_at = len(lines_) - 1
                if total_at is None:
                    skipped += 1
                    continue
                total_ends = lines_[total_at][1]
                total = lines_[total_at][2]
                if year == 2006:
                    if len(total) < 2 or sum(total[:-1]) != total[-1]:
                        skipped += 1
                        continue
                    total, total_ends = total[:-1], total_ends[:-1]
                    lines_ = [
                        (name, ends[1:-1], values[1:-1])
                        if name is not None and len(values) > 2 and sum(values[1:-1]) == values[-1]
                        else (name, ends, values)
                        for name, ends, values in lines_
                    ]
                counties: list = []
                bad = False
                for name, ends, values in lines_[:total_at]:
                    if name is not None:
                        key_ = re.sub(r"[^a-z]", "", name.lower().removesuffix(" county"))
                        close = [key_] if key_ in fips else difflib.get_close_matches(key_, list(fips), n=1, cutoff=0.7)
                        if not close:
                            continue
                    counties.append((close[0] if name is not None else None, values, ends))
                if any(c is not None for c, _, _ in counties):
                    counties = [item for item in counties if item[0] is not None]
                elif len(counties) != 1:
                    bad = True
                aligned: list[tuple[str | None, list[int]]] = []
                for county, values, ends in counties:
                    if len(values) != len(total):
                        placed = [0] * len(total)
                        for end, value in zip(ends, values, strict=True):
                            nearest = min(range(len(total)), key=lambda i: abs(total_ends[i] - end))
                            if abs(total_ends[nearest] - end) > 4 or placed[nearest] != 0:
                                bad = True
                            placed[nearest] = value
                        values = placed
                    aligned.append((county, values))
                counties = aligned
                if bad or not counties:
                    skipped += 1
                    continue
                if counties[0][0] is None:
                    fraction = conn.execute(
                        """select u.code, st_area(st_intersection(st_makevalid(dg.geom), st_makevalid(cg.geom))::geography)
                                  / st_area(dg.geom::geography)
                           from contest ct
                           join area_geometry dg on dg.area_id = ct.district_area_id and dg.valid @> %s::date
                           join reporting_unit u on u.scheme_code = 'us.county' and u.code like '45%%'
                           join area_geometry cg on cg.area_id = u.area_id and cg.valid @> %s::date
                           where ct.id = %s and st_intersects(dg.geom, cg.geom) order by 2 desc limit 1""",
                        [day, day, contests[key]],
                    ).fetchone()
                    if fraction is None or fraction[1] < 0.9:
                        skipped += 1
                        continue
                    counties = [(next(k for k, v in fips.items() if v == fraction[0]), counties[0][1])]
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
                    code = fips[county]
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
            summary="South Carolina's 2000, 2002, 2004, and 2006 state legislative general elections by county",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, "Election Reports 2000, 2002, 2004, and 2006")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="Election Reports 2000, 2002, 2004, and 2006",
            url=LANDING,
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, URL.format(year)), source)
        medsl.attach(cs, conn, frame, {"SC": source}, strict=True)
        return cs.write()
