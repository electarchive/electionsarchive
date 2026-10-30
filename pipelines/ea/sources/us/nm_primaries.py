import re
import unicodedata
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nm_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "https://electionresults.sos.nm.gov/resultsSW.aspx?type=FED&map=CTY&eid={eid}"
EVENTS = {
    1: date(2014, 6, 3),
    78: date(2016, 6, 7),
    112: date(2018, 6, 5),
    2778: date(2020, 6, 2),
    2827: date(2022, 6, 7),
    2878: date(2024, 6, 4),
}
PARTIES = {"DEM": "democratic", "REP": "republican", "LIB": "libertarian"}
CONTESTS: dict[tuple[int, int, str], tuple[str, int | None]] = {
    (1, 83, "DEM"): ("senate", None),
    (1, 83, "REP"): ("senate", None),
    (1, 6, "DEM"): ("house", 1),
    (1, 6, "REP"): ("house", 1),
    (1, 7, "DEM"): ("house", 2),
    (1, 7, "REP"): ("house", 2),
    (1, 8, "DEM"): ("house", 3),
    (1, 8, "REP"): ("house", 3),
    (78, 3522, "DEM"): ("house", 1),
    (78, 3522, "REP"): ("house", 1),
    (78, 3523, "DEM"): ("house", 2),
    (78, 3523, "REP"): ("house", 2),
    (78, 3524, "DEM"): ("house", 3),
    (78, 3524, "REP"): ("house", 3),
    (78, 3525, "DEM"): ("president", None),
    (78, 3525, "REP"): ("president", None),
    (112, 4526, "DEM"): ("house", 1),
    (112, 4526, "LIB"): ("house", 1),
    (112, 4526, "REP"): ("house", 1),
    (112, 4527, "DEM"): ("house", 2),
    (112, 4527, "REP"): ("house", 2),
    (112, 4528, "DEM"): ("senate", None),
    (112, 4528, "LIB"): ("senate", None),
    (112, 4528, "REP"): ("senate", None),
    (112, 4533, "DEM"): ("house", 3),
    (112, 4533, "LIB"): ("house", 3),
    (112, 4533, "REP"): ("house", 3),
    (2778, 6240, "DEM"): ("president", None),
    (2778, 6240, "LIB"): ("president", None),
    (2778, 6240, "REP"): ("president", None),
    (2778, 6241, "DEM"): ("house", 1),
    (2778, 6241, "REP"): ("house", 1),
    (2778, 6242, "DEM"): ("house", 2),
    (2778, 6242, "REP"): ("house", 2),
    (2778, 6243, "DEM"): ("house", 3),
    (2778, 6243, "REP"): ("house", 3),
    (2778, 6244, "DEM"): ("senate", None),
    (2778, 6244, "LIB"): ("senate", None),
    (2778, 6244, "REP"): ("senate", None),
    (2827, 7949, "DEM"): ("house", 1),
    (2827, 7949, "REP"): ("house", 1),
    (2827, 7950, "DEM"): ("house", 2),
    (2827, 7950, "REP"): ("house", 2),
    (2827, 7951, "DEM"): ("house", 3),
    (2827, 7951, "REP"): ("house", 3),
    (2878, 9434, "DEM"): ("president", None),
    (2878, 9434, "LIB"): ("president", None),
    (2878, 9434, "REP"): ("president", None),
    (2878, 9438, "DEM"): ("senate", None),
    (2878, 9438, "REP"): ("senate", None),
    (2878, 9439, "DEM"): ("house", 2),
    (2878, 9439, "REP"): ("house", 2),
    (2878, 9440, "DEM"): ("house", 3),
    (2878, 9440, "REP"): ("house", 3),
    (2878, 9441, "DEM"): ("house", 1),
    (2878, 9441, "REP"): ("house", 1),
}
PUBLISHER = "New Mexico Secretary of State"
TITLE = "New Mexico Election Results"


def url(eid: int, race: int, party: str) -> str:
    return f"{PAGE.format(eid=eid)}#race={race}&party={party}"


def fetch() -> None:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for eid in EVENTS:
            wanted = [key for key in CONTESTS if key[0] == eid]
            if all(_stored(*key) for key in wanted):
                continue
            page = client.get(PAGE.format(eid=eid)).text
            fields = {
                name: unescape(value)
                for name, value in re.findall(r'<input type="hidden" name="(__[A-Z]+)" id="\1" value="([^"]*)"', page)
            }
            controls = re.findall(
                r"__doPostBack\('(ctl00\$MainContent\$rptRace\$ctl\d+\$CountyExport)'.*?raceid=\"(\d+)\" party=\"(\w*)\"",
                page,
                flags=re.DOTALL,
            )
            listed = {(eid, int(race), party) for _, race, party in controls}
            if listed != set(wanted):
                raise ValueError(f"event {eid}: the page lists {sorted(listed)}, expected {sorted(wanted)}")
            for target, race, party in controls:
                response = client.post(
                    PAGE.format(eid=eid), data={**fields, "__EVENTTARGET": target, "__EVENTARGUMENT": ""}
                )
                response.raise_for_status()
                raw.store(
                    SOURCE,
                    url(eid, int(race), party),
                    response.content,
                    suffix=".xlsx",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )


def _stored(eid: int, race: int, party: str) -> bool:
    try:
        raw.cached(SOURCE, url(eid, race, party))
    except LookupError:
        return False
    return True


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '35%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for (eid, race, party), (kind, district) in CONTESTS.items():
        sheet = (
            fastexcel.read_excel(str(raw.cached(SOURCE, url(eid, race, party)).path))
            .load_sheet(0, header_row=None)
            .to_polars()
        )
        table = sheet.rows()
        head = next(i for i, row in enumerate(table) if row[1] == "County")
        names = [" ".join(cell.split()).upper() for cell in table[head][2:]]
        body = table[head + 1 : -1]
        if table[-1][1] != "TOTALS":
            raise ValueError(f"{eid} {race} {party}: no totals row")
        for c, name in enumerate(names, 2):
            counted = 0
            for row in body:
                votes = 0 if row[c] == "*" else int(row[c])
                counted += votes
                rows.append(
                    (
                        EVENTS[eid].year,
                        f"{kind}:{PARTIES[party]}",
                        EVENTS[eid],
                        False,
                        "NM",
                        district,
                        fips[re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", row[1]).lower())],
                        name,
                        1,
                        "WRITE" in name,
                        votes,
                    )
                )
            if counted != int(table[-1][c]):
                raise ValueError(f"{eid} {race} {party} {name}: counties sum to {counted}, printed {table[-1][c]}")
    PARSED.mkdir(parents=True, exist_ok=True)
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
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NM")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Mexico's 2014-2024 presidential, U.S. Senate, and U.S. House primaries by county",
            polity="US-NM",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionresults.sos.nm.gov/",
            license="Public record",
        )
        for eid, race, party in CONTESTS:
            cs.artifact(raw.cached(SOURCE, url(eid, race, party)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NM": source}, strict=True)
        return cs.write()
