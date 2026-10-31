import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_primaries"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/https://www.ohiosos.gov/globalassets/elections/{}"
FILES = {
    (date(2018, 5, 8), "democratic"): ("20251221121224", "2018/pri/summarydem.xlsx"),
    (date(2018, 5, 8), "republican"): ("20251221121225", "2018/pri/summaryrep.xlsx"),
    (date(2018, 5, 8), "green"): ("20251221121224", "2018/pri/summarygreen.xlsx"),
    (date(2020, 4, 28), "democratic"): ("20250613200022", "2020/pri/democratsummaryall.xlsx"),
    (date(2020, 4, 28), "republican"): ("20250613200029", "2020/pri/republicancountyall.xlsx"),
    (date(2020, 4, 28), "libertarian"): ("20250613200026", "2020/pri/libertariancountyall.xlsx"),
    (date(2022, 5, 3), "democratic"): (
        "20250928224116",
        "2022/pri/summarylevelofficialresults_2022-05-03_primaryelection_democratic.xlsx",
    ),
    (date(2022, 5, 3), "republican"): (
        "20250928224116",
        "2022/pri/summarylevelofficialresults_2022-05-03_primaryelection_republican.xlsx",
    ),
}
INBOX = DATA / "inbox" / "manual" / "MF-044"
PORTAL = "https://www.ohiosos.gov/globalassets/elections/{}"
MANUAL = {
    (date(2014, 5, 6), "democratic"): "2014/primary/democrat.xlsx",
    (date(2014, 5, 6), "republican"): "2014/primary/republican.xlsx",
    (date(2014, 5, 6), "green"): "2014/primary/green.xlsx",
    (date(2014, 5, 6), "libertarian"): "2014/primary/libertarian.xlsx",
    (date(2014, 5, 6), "constitution"): "2014/primary/constitution.xlsx",
    (date(2016, 3, 15), "democratic"): "2016/primary/dem.xlsx",
    (date(2016, 3, 15), "republican"): "2016/primary/republican.xlsx",
    (date(2016, 3, 15), "green"): "2016/primary/green.xlsx",
}
LOCAL = {
    (date(2024, 3, 19), "democratic"): (
        "2024/primary/summary-level-official-results-primary-2024---democratic.xlsx",
        DATA / "inbox" / "manual" / "MF-285" / "summary-level-official-results-primary-2024---democratic.xlsx",
    ),
    (date(2024, 3, 19), "republican"): (
        "2024/primary/summary-level-results-primary-2024---republican.xlsx",
        DATA / "inbox" / "manual" / "MF-285" / "summary-level-results-primary-2024---republican.xlsx",
    ),
}
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official primary election results by county"


def urls() -> dict[tuple[date, str], str]:
    return (
        {key: WAYBACK.format(stamp, path) for key, (stamp, path) in FILES.items()}
        | {key: PORTAL.format(path) for key, path in MANUAL.items()}
        | {key: PORTAL.format(path) for key, (path, _) in LOCAL.items()}
    )


def fetch() -> None:
    for stamp, path in FILES.values():
        raw.fetch(SOURCE, WAYBACK.format(stamp, path), suffix=".xlsx")
    for (day, _), path in MANUAL.items():
        try:
            raw.cached(SOURCE, PORTAL.format(path))
        except LookupError:
            raw.store(
                SOURCE,
                PORTAL.format(path),
                (INBOX / day.isoformat() / path.rsplit("/", 1)[1]).read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    for path, file in LOCAL.values():
        raw.store(
            SOURCE,
            PORTAL.format(path),
            file.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%'"""
            ).fetchall()
        }
    rows = []
    for (day, party), url in urls().items():
        sheet = pl.read_excel(
            raw.cached(SOURCE, url).path,
            sheet_name="Master" if day.year > 2014 else None,
            sheet_id=None if day.year > 2014 else 1,
            has_header=False,
            infer_schema_length=0,
        ).rows()
        total = next((row for row in sheet if row[0] == "Total" and row[3] is not None), None)
        office = None
        start = (
            sheet[1].index(
                next(text for text in sheet[1] if text in ("Total Voters", "Total Votes Cast", "Ballots Counted"))
            )
            + 1
        )
        start += sheet[1][start] in ("Turnout Percentage", "Official Voter Turnout")
        for column in range(start, len(sheet[0])):
            office = sheet[0][column] or office
            candidate = sheet[1][column]
            if office is None or candidate is None:
                continue
            title = " ".join(office.split())
            district = re.match(r"(?:Representative to Congress|U\.S\. House of Representatives)\b\D*(\d+)", title)
            chamber = (
                "house"
                if district
                else "senate"
                if title.startswith("U.S. Senator")
                else "president"
                if title.startswith("President (at-large)")
                else None
            )
            if chamber is None:
                continue
            values = [
                (row[0], int(float(row[column] or 0)))
                for row in sheet[2:]
                if re.sub(r"[^a-z]", "", (row[0] or "").lower()) in counties
            ]
            if (
                len(values) != 88
                or total is not None
                and sum(votes for _, votes in values) != int(float(total[column]))
            ):
                raise ValueError(f"{day} {party} {title} {candidate}: the counties do not add up to the total")
            rows.extend(
                (
                    day,
                    f"{chamber}:{party}",
                    "Unexpired" in title,
                    int(district[1]) if district else None,
                    re.sub(r"(?:\s*\([A-Z]+\)\*?)+$", "", " ".join(candidate.split())).upper(),
                    counties[re.sub(r"[^a-z]", "", county.lower())],
                    votes,
                )
                for county, votes in values
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "special": pl.Boolean,
            "district": pl.Int16,
            "candidate": pl.String,
            "code": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).filter(
        pl.col("district").is_null() | (pl.col("votes").sum().over("day", "chamber", "special", "district", "code") > 0)
    ).select(
        year=pl.col("day").dt.year(),
        chamber="chamber",
        day="day",
        special="special",
        state=pl.lit("OH"),
        district="district",
        code="code",
        candidate="candidate",
        writein=pl.col("candidate").str.contains("WRITE-IN"),
        votes="votes",
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("OH"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's House, Senate, and presidential primaries 2014–2024 by county",
            polity="US-OH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ohiosos.gov/elections/election-results-and-data/",
            license="Public record",
        )
        for url in urls().values():
            cs.artifact(raw.cached(SOURCE, url), source)
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-2][0-9]-0[1-9]-[0-9]{2}:OH:(house:[0-9]+|senate|president):[a-z-]+$'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission' and s.id <> %s)""",
                [source],
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("OH"),
                    pl.when(pl.col("district").is_not_null()).then(
                        pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String))
                    ),
                    pl.col("chamber").str.strip_prefix("house:"),
                    separator=":",
                    ignore_nulls=True,
                ).is_in(held)
            ).sort("special", maintain_order=True),
            {"OH": source},
            redone=frozenset(
                value.removesuffix(":county")
                for (value,) in conn.execute(
                    "select value from external_id where scheme = %s and value ~ ':house:\\d+(:special)?:[a-z-]+:county$'",
                    [f"{SOURCE}.result_set"],
                ).fetchall()
            ),
            strict=True,
        )
        return cs.write()
