import csv
import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.openelections import HOUSE, SENATE
from ea.store import DATA, connect

SOURCE = "us.md_primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://elections.maryland.gov/elections/archive/"
DAYS = {
    2004: date(2004, 3, 2),
    2006: date(2006, 9, 12),
    2008: date(2008, 2, 12),
    2010: date(2010, 9, 14),
    2012: date(2012, 4, 3),
    2014: date(2014, 6, 24),
    2016: date(2016, 4, 26),
    2018: date(2018, 6, 26),
    2020: date(2020, 6, 2),
    2022: date(2022, 7, 19),
}
FILES = {
    2004: r"([A-Za-z_.']+)_(Democratic|Republican)_Primary_2004\.csv",
    **{
        year: rf"([A-Za-z_.']+?)(?:_County)?_(Democratic|Republican)_{year}_Primary\.csv"
        for year in (2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020)
    },
    2022: r"GP22_(\d\d)(Democratic|Republican)Results\.csv",
}
PUBLISHER = "Maryland State Board of Elections"
TITLE = "Election results archive: primaries"


def files(year: int) -> list[str]:
    listing = raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/index.html").path.read_text(errors="replace")
    return sorted(
        {
            match.group(0).lstrip("'")
            for match in re.finditer(FILES[year], listing)
            if not re.search("Precinct|Legislative|Congressional|^All_|^State_", match.group(0).lstrip("'"))
        }
    )


def fetch() -> None:
    for year in FILES:
        raw.fetch(SOURCE, f"{ARCHIVE}{year}/election_data/index.html", suffix=".html")
        for name in files(year):
            raw.fetch(SOURCE, f"{ARCHIVE}{year}/election_data/{name}", suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips: dict[str, str] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '24%' order by u.code"""
        ).fetchall():
            plain = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower().replace("saint ", "st "))
            if int(code[2:]) >= 500:
                fips[f"{plain}city"] = code
            fips.setdefault(plain, code)
    ordinals = sorted(set(fips.values()))
    rows: list[tuple[int, str, str, int | None, str, str, int]] = []
    for year, pattern in FILES.items():
        for name in files(year):
            found = re.fullmatch(pattern, name)
            if year == 2022 and found[1] == "00":
                continue
            plain = re.sub(
                r"[^a-z]", "", found[1].lower().replace("_", " ").replace("saint ", "st ").replace("st.", "st")
            )
            code = ordinals[int(found[1]) - 1] if year == 2022 else fips[plain]
            entries = [
                {key.strip(): value for key, value in entry.items() if key is not None}
                for entry in csv.DictReader(
                    io.StringIO(
                        raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/{name}").path.read_text(errors="replace")
                    )
                )
            ]
            label = "Candidate Name" if "Candidate Name" in entries[0] else "Candidate"
            for candidate, party, office, district, votes in (
                (entry[label], entry["Party"], entry["Office Name"], entry["Office District"], entry["Total Votes"])
                for entry in entries
            ):
                office = (office or "").strip().lower()
                if office.startswith("president"):
                    if district:
                        continue
                    kind = "president"
                elif "state" in office:
                    continue
                elif SENATE.search(office):
                    kind = "senate"
                elif HOUSE.search(office):
                    kind = "house"
                else:
                    continue
                rows.append(
                    (
                        year,
                        f"{kind}:{'democratic' if party.strip() == 'DEM' else 'republican'}",
                        code,
                        int(district) if kind == "house" and district else None,
                        (candidate or "").strip().upper(),
                        party.strip(),
                        int(float(votes or 0)),
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "code": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "party": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            day=pl.col("year").replace_strict(DAYS, return_dtype=pl.Date),
            special=pl.lit(False),
            state=pl.lit("MD"),
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source=pl.lit("MD"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's House, Senate, and presidential primaries by county, 2004–2022",
            polity="US-MD",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=f"{ARCHIVE}index.html",
            license="Public record",
        )
        for year in FILES:
            for name in files(year):
                cs.artifact(raw.cached(SOURCE, f"{ARCHIVE}{year}/election_data/{name}"), source)
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[01][0-9]-[0-9]{2}-[0-9]{2}:MD:(house:[0-9]+|senate|president):[a-z-]+$'
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
                    pl.lit("MD"),
                    pl.when(pl.col("district").is_not_null())
                    .then(
                        pl.concat_str(
                            pl.lit("house:"),
                            pl.col("district").cast(pl.String),
                            pl.lit(":"),
                            pl.col("chamber").str.split(":").list.last(),
                        )
                    )
                    .otherwise(pl.col("chamber")),
                    separator=":",
                ).is_in(list(held))
            ),
            {"MD": source},
            strict=True,
        )
        return cs.write()
