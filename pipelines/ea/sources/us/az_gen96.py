import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.az_gen96"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-278" / "az-1996-general-county-results.html"
URL = "https://apps.azsos.gov/results/1996general/"
PUBLISHER = "Arizona Secretary of State"
TITLE = "General Election, November 5, 1996: results by county"
DAY = date(1996, 11, 5)


def fetch() -> None:
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".html",
        retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
        media_type="text/html",
    )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '04%'"""
            ).fetchall()
        }
    html = raw.cached(SOURCE, URL).path.read_text(errors="replace")
    rows: list[dict[str, object]] = []
    for page in html.split("<!-- URL:")[1:]:
        title = re.search(r"<TITLE>(.*?)</TITLE>", page, re.S | re.I).group(1).strip()
        if title == "U.S. PRESIDENT":
            chamber, district = "president", None
        elif (match := re.fullmatch(r"U\.S\. REPRESENTATIVE IN CONGRESS - DISTRICT NO\. (\d+)", title)) is not None:
            chamber, district = "house", int(match.group(1))
        else:
            continue
        table = [
            [re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
            for row in re.findall(r"<tr>(.*?)</tr>", page, re.S | re.I)
        ]
        head = table[0]
        names = [cell.rsplit(" ", 1)[0].rstrip("*").strip() for cell in head[1 : head.index("Precincts")]]
        body = [row for row in table[1:] if row[0] not in {"Total", "Percentages"}]
        sums = [0] * len(names)
        for row in body:
            for column, cell in enumerate(row[1 : 1 + len(names)]):
                sums[column] += int(cell)
                rows.append(
                    {
                        "chamber": chamber,
                        "district": district,
                        "code": counties[re.sub(r"[^a-z]", "", row[0].lower())],
                        "candidate": names[column],
                        "writein": False,
                        "votes": int(cell),
                    }
                )
        total = next((row for row in table[1:] if row[0] == "Total"), None)
        if total is not None and [int(cell) for cell in total[1 : 1 + len(names)]] != sums:
            raise ValueError(f"{title}: county sums {sums} differ from the printed total {total}")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16})
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("AZ"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("AZ"),
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
            summary="Arizona's 1996 president and House general elections by county",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, frame, {"AZ": source}, strict=True)
        return cs.write()
