import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_primaries import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.ne_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.nebraska.gov/sites/default/files/doc/elections/"
FILES = {
    date(2016, 5, 10): "2016/Primary16CanvassBook.pdf",
    date(2018, 5, 15): "2018/2018-canvass-book.pdf",
    date(2020, 5, 12): "2020/2020%20Primary%20Canvass%20Book.pdf",
    date(2022, 5, 10): "2022/2022%20Primary%20Canvass%20Book.pdf",
}
CELL = re.compile(
    r"(?<![A-Za-z])(Total|"
    + "|".join(sorted(map(re.escape, COUNTIES), key=len, reverse=True))
    + r")(?: \(\d+ precincts?\))?\*{0,2}(?![A-Za-z])((?:\s+\d[\d,]*)+)"
)
PARTY = re.compile(r"(Republican|Democratic|Libertarian|Nebraska|Green|Legal Marijuana NOW) Party(?! did not)")
PARTIES = {"legal marijuana now": "legal-marijuana-now"}
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Primary election canvass"
SPLITS = {"Alisha Shelton Daniel M. Wik": "Alisha Shelton  Daniel M. Wik"}
SKIP = re.compile(r"Page \||Primary Election [–-]|Federal Offices|Member of the|Congressional District|did not make")


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    rows: list[tuple] = []
    for day, path in FILES.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        start = next(
            i
            for i, line in enumerate(lines)
            if i > 150
            and re.match(r"\s+(President of the United States|Member of the (United States Senate|U\.S\. House))", line)
        )
        end = next(
            i
            for i in range(start, len(lines))
            if re.match(
                r"\s+(State Offices|Statewide Constitutional Offices|Public Service Commissioner)\s*$", lines[i]
            )
        )
        chamber, district = "", None
        tables: list[dict] = []
        header: list[tuple[int, int, str]] = []
        done: list[dict] = []

        def close() -> None:
            done.extend(tables)
            tables.clear()
            header.clear()

        block: list[str] = []
        moved = ""
        for index in range(start, end):
            line = lines[index]
            found = list(CELL.finditer(line))
            if (
                found
                and re.search(r"\bCounty\b", line)
                and not any(c[1] == "Total" for c in found)
                and re.match(r"\s*Total\s+\d", lines[index + 1])
            ):
                moved = " " * found[0].start() + line[found[0].start() :]
                line = line[: found[0].start()]
                block.append(line)
                continue
            block.append(line)
            if moved:
                block.append(moved)
                moved = ""
        for line in block:
            for joined, split in SPLITS.items():
                line = line.replace(joined, split)
            if re.match(r"\s+President of the United States\s*$", line):
                close()
                chamber, district = "president", None
            elif re.match(r"\s+Member of the United States Senate", line):
                close()
                chamber, district = "senate", None
            elif re.match(r"\s+Member of the U\.S\. House", line):
                close()
                chamber, district = "house", None
            elif found := re.match(r"\s+Congressional District (\d|One|Two|Three)", line):
                close()
                district = int(found[1]) if found[1].isdigit() else ["One", "Two", "Three"].index(found[1]) + 1
            elif PARTY.search(line) and not CELL.search(line):
                close()
                for match in PARTY.finditer(line):
                    party = match[1].lower()
                    tables.append(
                        {
                            "key": (chamber, district, PARTIES.get(party, party)),
                            "names": None,
                            "tally": {},
                            "total": None,
                        }
                    )
            elif not tables:
                continue
            else:
                cells = list(CELL.finditer(line))
                if not cells:
                    if (
                        line.strip()
                        and not SKIP.search(line)
                        and not re.fullmatch(r"\s*\d+\s*", line)
                        and all(table["names"] is None for table in tables)
                    ):
                        counties_at = [m.start() for m in re.finditer(r"\bCounty\b", line)]
                        header.extend(
                            (m.start(), m.end(), m[0], len(header), bool(counties_at))
                            for m in re.finditer(r"\S{1,}(?: \S+)*", line)
                            if m[0] != "County"
                        )
                        header.append((-1, -1, ",".join(map(str, counties_at)), 0, False))
                    continue
                if tables[0]["names"] is None:
                    first = [c for c in cells if c[1] == "Total"]
                    if not first:
                        continue
                    segments = [[(c.start(2) + m.end()) for m in re.finditer(r"\d[\d,]*", c[2])] for c in first]
                    counties_at = [int(x) for h in header if h[0] == -1 for x in h[2].split(",") if x]
                    counties_at = sorted(set(counties_at)) or [0]
                    pieces = [h for h in header if h[0] >= 0]
                    order = {}
                    for h in pieces:
                        order.setdefault(h[3], len(order))
                    named: list[list[list[str]]] = []
                    for k, columns in enumerate(segments):
                        low = counties_at[k] if k < len(counties_at) else 0
                        high = counties_at[k + 1] if k + 1 < len(counties_at) else 10**6
                        mine = [h for h in pieces if low <= h[0] < high]
                        anchors = [h for h in mine if h[4]]
                        names: list[list[tuple[int, str]]] = [[] for _ in columns]
                        if len(anchors) == len(columns):
                            for i, h in enumerate(anchors):
                                names[i].append((h[3], h[2]))
                            for h in mine:
                                if not h[4]:
                                    centre = (h[0] + h[1]) / 2
                                    names[
                                        min(
                                            range(len(anchors)),
                                            key=lambda i: abs((anchors[i][0] + anchors[i][1]) / 2 - centre),
                                        )
                                    ].append((h[3], h[2]))
                        else:
                            for h in mine:
                                names[
                                    min(range(len(columns)), key=lambda i: abs(columns[i] - (h[0] + h[1]) / 2))
                                ].append((h[3], h[2]))
                        named.append([[w for _, w in sorted(p)] for p in names])
                    if len(tables) == 1 and len(segments) == 2:
                        tables[0]["names"] = [" ".join(p) for p in named[0]]
                        tables[0]["halves"] = 2
                    else:
                        for table, names in zip(tables, named, strict=False):
                            table["names"] = [" ".join(p) for p in names]
                    header.clear()
                for k, cell in enumerate(cells):
                    table = tables[0] if tables[0].get("halves") else tables[min(k, len(tables) - 1)]
                    numbers = [int(v.replace(",", "")) for v in cell[2].split()]
                    if len(numbers) != len(table["names"]):
                        print(f"{day} {table['key']}: {line.strip()}")
                        continue
                    if cell[1] == "Total":
                        table["total"] = numbers
                    else:
                        slot = table["tally"].setdefault(cell[1], [0] * len(numbers))
                        table["tally"][cell[1]] = [a + b for a, b in zip(slot, numbers, strict=True)]
        close()
        for table in done:
            if table["total"] is None:
                print(f"{day} {table['key']}: no Total row")
                continue
            sums = [sum(row[i] for row in table["tally"].values()) for i in range(len(table["names"]))]
            if any(abs(a - b) > 1 for a, b in zip(sums, table["total"], strict=True)):
                print(f"{day} {table['key']}: county sums {sums} against Total {table['total']}")
                continue
            chamber_, district_, party_ = table["key"]
            for county, values in table["tally"].items():
                for name, value in zip(table["names"], values, strict=True):
                    rows.append(
                        (
                            day.year,
                            f"{chamber_}:{party_}",
                            day,
                            False,
                            "NE",
                            district_,
                            codes[re.sub(r"[^a-z]", "", county.lower())],
                            name.upper(),
                            1,
                            False,
                            value,
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "special": pl.Boolean,
                "state": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "measure": pl.Int16,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(kind=pl.lit("county"), source=pl.lit("NE"))
        .write_parquet(PARSED / "counties.parquet")
    )
    print(len(rows), "candidate rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2018 House 3 Democratic and Senate Libertarian primaries by county",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.nebraska.gov/elections/election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(
                (pl.col("day") == date(2018, 5, 15))
                & (
                    (pl.col("chamber") == "senate:libertarian")
                    | ((pl.col("chamber") == "house:democratic") & (pl.col("district") == 3))
                )
            ),
            {"NE": source},
            strict=True,
        )
        return cs.write()
