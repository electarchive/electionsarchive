import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads" / "mnp_all.json"
URL = "https://electionresults.sos.mn.gov/Results/MediaFile_Archive/Index?erselectionId={election}&mediafileid={file}"
DAYS = {
    21: date(2000, 9, 12),
    31: date(2002, 9, 10),
    43: date(2004, 9, 14),
    56: date(2006, 9, 12),
    64: date(2008, 9, 9),
    70: date(2010, 8, 10),
    2: date(2012, 8, 14),
    19: date(2014, 8, 12),
}
FILES = [
    (21, 27), (31, 27), (31, 29), (43, 29), (56, 27), (56, 29), (64, 27), (64, 29), (70, 29), (2, 27), (2, 29), (19, 27), (19, 29),
]  # fmt: skip
PARTIES = {
    "DFL": "minnesota-democratic-farmer-labor",
    "R": "republican",
    "IP": "independence",
    "I": "independence",
    "GP": "green",
    "CP": "constitution",
}
OFFICE = re.compile(r"(?i)(?:u\.?s\.? )?(senator|representative)(?: district (\d+))?")
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Primary election results"


def fetch() -> None:
    files = json.loads(MANUAL.read_text())
    for election, file in FILES:
        raw.store(
            SOURCE,
            URL.format(election=election, file=file),
            files[f"{election}_{file}"].encode(),
            suffix=".txt",
            retrieved_at=datetime.fromtimestamp(MANUAL.stat().st_mtime, UTC),
            media_type="text/plain",
        )


def parse() -> None:
    rows: list[tuple[date, str, int | None, str, str, int]] = []
    for election, file in FILES:
        text = raw.cached(SOURCE, URL.format(election=election, file=file)).path.read_text(encoding="latin-1")
        for line in text.splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            if len(cells) < 14:
                continue
            found = OFFICE.fullmatch(cells[4])
            if found is None:
                continue
            party = PARTIES.get(cells[10])
            if party is None:
                print(f"{DAYS[election]}: unmapped party {cells[10]!r} for {cells[7]}")
                continue
            rows.append(
                (
                    DAYS[election],
                    ("house:" if found[2] else "senate:") + party,
                    int(found[2]) if found[2] else None,
                    re.sub(r"\s+", " ", cells[7].replace('"', "")),
                    f"27{2 * int(cells[1]) - 1:03}",
                    int(cells[13] or 0),
                )
            )
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
    )
    with connect() as conn:
        known: dict[str, list[int]] = {}
        for contest, total in conn.execute(
            """select x.value, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('candidate', 'ticket')
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^(2000-09-12|2002-09-10|2004-09-14|2006-09-12|2008-09-09|2010-08-10|2012-08-14|2014-08-12):MN:'
               group by x.value, o.id"""
        ).fetchall():
            known.setdefault(contest, []).append(total)
    keep = []
    for (day, chamber, district), lines in frame.group_by("day", "chamber", "district"):
        name, _, party = chamber.partition(":")
        contest = f"{day}:MN:" + (f"house:{district}" if district else name) + f":{party}"
        sums = sorted(
            total
            for total in lines.filter(~pl.col("candidate").str.contains("(?i)write"))
            .group_by("candidate")
            .agg(pl.col("votes").sum())["votes"]
            .to_list()
            if total > 0
        )
        filed = sorted(total for total in known.get(contest, []) if total > 0)
        if sums == filed:
            keep.append(lines)
        else:
            print(f"{contest}: county sums {sums} against the FEC's {filed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(keep).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("MN"),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        kind=pl.lit("county"),
        source=pl.lit("MN"),
    ).write_parquet(PARSED / "counties.parquet")
    print(f"{len(keep)} contests kept")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 2000–2014 U.S. House and Senate primaries by county",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionresults.sos.mn.gov/",
            license="Public record",
        )
        for election, file in FILES:
            cs.artifact(raw.cached(SOURCE, URL.format(election=election, file=file)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MN": source}, strict=True)
        return cs.write()
