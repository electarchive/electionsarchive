import io
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.nv_leg_oe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [date(2000, 11, 7), date(2002, 11, 5), date(2004, 11, 2), date(2006, 11, 7), date(2008, 11, 4), date(2010, 11, 2)]
FILE = re.compile(r"\d{4}/(\d{8})__nv__general__([a-z_]+?)(__precinct)?\.csv")
ASSEMBLY = re.compile(r"STATE ASSEMBLY,?(?: [A-Z ]+?)?(?: DISTRICT)? (\d+)$")
SENATE = re.compile(r"STATE SENATE,? (?:(CLARK|WASHOE) )?DISTRICT (\d+)$")
REGION = re.compile(r"(?:STATE SENATE,? )?(CAPITAL|CENTRAL|NORTHERN|RURAL|SOUTHERN) SENATORIAL DISTRICT$")
SKIP = re.compile(r"(?i)^(total|over ?votes|under ?votes|times|registered|ballots|blank)")
TITLE = "NV precinct-level results"


def tree() -> str:
    return openelections.TREE.format(state="nv", commit=openelections.COMMITS["NV"])


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="nv", commit=openelections.COMMITS["NV"], path=path)


def paths() -> list[str]:
    found = [
        entry["path"]
        for entry in json.loads(raw.cached(SOURCE, tree()).path.read_text())["tree"]
        if (matched := FILE.fullmatch(entry["path"])) is not None
        and matched[2] != "precinct"
        and date(int(matched[1][:4]), int(matched[1][4:6]), int(matched[1][6:])) in DAYS
    ]
    precinct = {path[:4] for path in found if path.endswith("__precinct.csv")}
    return sorted(path for path in found if path.endswith("__precinct.csv") == (path[:4] in precinct))


def fetch() -> None:
    raw.fetch(SOURCE, tree(), suffix=".json")
    for path in paths():
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["NV"]
    rows = []
    for path in paths():
        matched = FILE.fullmatch(path)
        day = date(int(matched[1][:4]), int(matched[1][4:6]), int(matched[1][6:]))
        county = matched[2]
        frame = pl.read_csv(io.BytesIO(raw.cached(SOURCE, url(path)).path.read_bytes()), infer_schema_length=0)
        for office, candidate, votes in frame.select("office", "candidate", "votes").iter_rows():
            label = re.sub(r"\s+", " ", (office or "").upper()).strip()
            if (found := ASSEMBLY.fullmatch(label)) is not None:
                chamber, district = "assembly", found[1]
            elif (found := SENATE.fullmatch(label)) is not None:
                chamber = "senate"
                district = f"{county if county in ('clark', 'washoe') else (found[1] or 'clark').lower()}-{found[2]}"
            elif (found := REGION.fullmatch(label)) is not None:
                chamber, district = "senate", f"{found[1].lower()}-1"
            else:
                continue
            if candidate is None or SKIP.match(candidate) or votes is None:
                continue
            rows.append(
                (day, chamber, district, fips[county.replace("_", "")],
                 re.sub(r"\s+Incumbent$", "", candidate.strip()).upper(), int(votes.replace(",", "")))
            )  # fmt: skip
    frame = (
        pl.DataFrame(
            rows,
            schema={"day": pl.Date, "chamber": pl.String, "district": pl.String, "code": pl.String,
                    "candidate": pl.String, "votes": pl.Int64},
            orient="row",
        )  # fmt: skip
        .group_by("day", "chamber", "district", "code", "candidate")
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "chamber").agg(pl.col("district").n_unique()).sort("day", "chamber"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        keys: dict[tuple[str, str, str], list[str]] = {}
        for (value,) in conn.execute(
            """select value from external_id where scheme = 'us.contest'
               and value ~ '^20(0[02468]|10)-11-\\d\\d:NV:(assembly|senate):.*:g$'"""
        ).fetchall():
            day, _, chamber, *district, _ = value.split(":")
            name = district[0]
            keys.setdefault((day, chamber, name.rsplit("-", 1)[1] if chamber == "assembly" else name), []).append(
                ":".join([chamber, *district, "g"])
            )
        frame = frame.with_columns(
            chamber=pl.struct("day", "chamber", "district").map_elements(
                lambda row: (
                    found[0]
                    if len(found := keys.get((str(row["day"]), row["chamber"], row["district"]), [])) == 1
                    else None
                ),
                return_dtype=pl.String,
            )
        )
        print(frame.filter(pl.col("chamber").is_null()).select("day", "district").unique().sort("day", "district"))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nevada's 2000 to 2010 State Assembly and Senate generals by county from OpenElections",
            polity="US-NV",
        )
        source = cs.source("OpenElections", TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title=TITLE,
                url="https://github.com/openelections/openelections-data-nv",
                license=None,
            )
        for path in paths():
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("chamber").is_not_null()).select(
                year=pl.col("day").dt.year().cast(pl.Int32), chamber="chamber", day="day", special=pl.lit(False),
                state=pl.lit("NV"), district=pl.lit(None, pl.Int16), code="code", kind=pl.lit("county"),
                candidate="candidate", measure=pl.lit(1, pl.Int16), writein=pl.lit(False), votes="votes",
                source=pl.lit("NV"),
            ),
            {"NV": source},
            strict=True,
        )  # fmt: skip
        return cs.write()
