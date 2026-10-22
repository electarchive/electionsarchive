import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.md_manual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://msa.maryland.gov/msa/mdmanual/42electg/html/{year}/{short}usrep.html"
DAYS = {1994: date(1994, 11, 8), 1996: date(1996, 11, 5), 1998: date(1998, 11, 3)}
PUBLISHER = "Maryland State Archives"
TITLE = "Maryland Manual On-Line: General Election Returns, U.S. Representative"
NAMES = {"Baltimore City": "baltimorecity", "Baltimore Co.": "baltimore"}


def _url(year: int) -> str:
    return PAGE.format(year=year, short=str(year)[2:])


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, _url(year), suffix=".html")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%%'"""
            ).fetchall()
        }
        fips["baltimorecity"] = "24510"
    rows: list[tuple[int, int, str, str, int, bool]] = []
    for year in DAYS:
        text = raw.cached(SOURCE, _url(year)).path.read_text(errors="ignore")
        text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", text)
        text = re.sub(r"(?i)</t[dr]>|<br\s*/?>|</p>|</h\d>", "\n", text)
        lines = [re.sub(r"\s+", " ", line).strip() for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).split("\n")]
        lines = [line for line in lines if line]
        starts = [i for i, line in enumerate(lines) if re.fullmatch(r"(?:\d+(?:ST|ND|RD|TH) )?CONGRESSIONAL DISTRICT(?: \d+)?", line)]
        for start, end in zip(starts, [*starts[1:], len(lines)]):
            district = int(re.search(r"\d+", lines[start])[0])
            block = lines[start + 1 : end]
            first = next(i for i, line in enumerate(block) if line.endswith(" Co.") and "," not in line or line == "Baltimore City")
            people = []
            for line in block[:first]:
                if "," not in line:
                    continue
                parts = line.split(",")
                name = parts[0]
                if len(parts) > 2 and parts[1].strip() in {"Jr.", "Sr.", "III", "II"}:
                    name = f"{parts[0]}, {parts[1].strip()}"
                people.append((re.sub(r" \(.*", "", name), "(write-in)" in line))
            body = block[first:]
            cells: dict[str, list[int]] = {}
            current = None
            for line in body:
                if re.fullmatch(r"[\d,]+", line):
                    cells[current].append(int(line.replace(",", "")))
                else:
                    current = line
                    cells[current] = []
            printed = cells.get("TOTAL")
            cells = {k: v for k, v in cells.items() if k.endswith("Co.") or k == "Baltimore City"}
            printed = printed or [sum(v[column] for v in cells.values()) for column in range(len(people))]
            if len(printed) != len(people) or any(len(v) != len(people) for v in cells.values()):
                raise ValueError(f"{year} district {district}: row lengths {sorted({len(v) for v in cells.values()})} for {len(people)}")
            for column, (name, writein) in enumerate(people):
                if sum(v[column] for v in cells.values()) != printed[column]:
                    raise ValueError(f"{year} district {district} {name}: counties do not sum to the printed total")
                for place, votes in cells.items():
                    code = fips[re.sub(r"[^a-z]", "", re.sub(r" Co\.$", "", place).lower())]
                    rows.append((year, district, re.sub(r"\s+", " ", name).upper(), code, votes[column], writein))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"year": pl.Int32, "district": pl.Int16, "candidate": pl.String, "code": pl.String, "votes": pl.Int64, "writein": pl.Boolean},
        orient="row",
    ).with_columns(
        day=pl.col("year").replace_strict({y: d for y, d in DAYS.items()}, return_dtype=pl.Date),
        chamber=pl.lit("house"),
        special=pl.lit(False),
        state=pl.lit("MD"),
        measure=pl.lit(1, dtype=pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("MD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's 1994, 1996, and 1998 U.S. House general elections by county from the Maryland Manual On-Line",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://msa.maryland.gov/msa/mdmanual/42electg/html/elect.html",
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, _url(year)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MD": source}, strict=True)
        return cs.write()
