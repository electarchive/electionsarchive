import io
import os
import re
import struct
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import shapefile

from ea import precinct_code, raw
from ea.changeset import Changeset
from ea.sources.us import medsl, oe_federal
from ea.store import DATA, connect

SOURCE = "us.swdb_sov"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SWDB = "https://statewidedatabase.org/pub/data/"
ELECTIONS = {
    "G02": date(2002, 11, 5),
    "G04": date(2004, 11, 2),
    "G06": date(2006, 11, 7),
    "G08": date(2008, 11, 4),
    "G10": date(2010, 11, 2),
    "G12": date(2012, 11, 6),
    "G14": date(2014, 11, 4),
    "G16": date(2016, 11, 8),
    "G18": date(2018, 11, 6),
    "G20": date(2020, 11, 3),
    "G22": date(2022, 11, 8),
    "G24": date(2024, 11, 5),
    "P12": date(2012, 6, 5),
    "P14": date(2014, 6, 3),
    "P16": date(2016, 6, 7),
    "P18": date(2018, 6, 5),
    "P20": date(2020, 3, 3),
    "P22": date(2022, 6, 7),
    "P24": date(2024, 3, 5),
}
OFFICES = {
    "PRS": ("president", False),
    "USS": ("senate", False),
    "USP": ("senate", True),
    "GOV": ("governor", False),
    "CNG": ("house", False),
}
PARTIES = {
    "DEM": "Democratic Party",
    "REP": "Republican Party",
    "LIB": "Libertarian Party",
    "GRN": "Green Party",
    "AIP": "American Independent Party",
    "PAF": "Peace and Freedom Party",
    "NLP": "Natural Law Party",
    "REF": "Reform Party",
}
COUNTIES = [f"{number:03d}" for number in range(1, 116, 2)]
PUBLISHER = "Statewide Database, University of California, Berkeley"
TITLE = "Statement of Vote data by SOV precinct"
CODE = re.compile(r"^([A-Z]{3}(?:\d\d)?[A-Z]{3}(?:\d\d)?)(?:\t|\s{2,})(.+?)(?:(?:\t|\s{2,})-?\d+)?\s*$")
OFFICE = re.compile(r"(PRS|USS|USP|GOV|CNG)(?!VNC)([A-Z]{3})(\d\d)?")


def sov(election: str) -> str:
    return f"{SWDB}{election}/state/state_{election.lower()}_sov_data_by_{election.lower()}_svprec.zip"


def codes(election: str, county: str) -> str:
    return f"{SWDB}{election}/c{county}/{county}.codes"


def fetch() -> None:
    for election in ELECTIONS:
        for address in (sov(election), *(codes(election, county) for county in COUNTIES)):
            try:
                raw.cached(SOURCE, address)
            except LookupError:
                raw.fetch(SOURCE, address, suffix=".zip" if address.endswith(".zip") else ".txt")


def read(election: str) -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, sov(election)).path) as archive:
        (member,) = [name for name in archive.namelist() if name.lower().endswith((".csv", ".dbf"))]
        if member.lower().endswith(".csv"):
            return pl.read_csv(io.BytesIO(archive.read(member)), infer_schema_length=0)
        data = bytearray(archive.read(member))
        count, start, size = struct.unpack("<IHH", data[4:12])
        for offset in range(start, start + count * size, size):
            if data[offset] == 0:
                data[offset] = 32
        reader = shapefile.Reader(dbf=io.BytesIO(bytes(data)))
        fields = [field[0] for field in reader.fields[1:]]
        keep = [
            field for field in fields if field in ("FIPS", "COUNTY_COD", "SVPREC", "CDDIST") or OFFICE.fullmatch(field)
        ]
        return pl.DataFrame(
            [[None if value is None else str(value) for value in record] for record in reader.iterRecords(fields=keep)],
            schema=keep,
            orient="row",
        ).rename({"COUNTY_COD": "FIPS"}, strict=False)


def parse() -> None:
    frames = []
    with connect() as conn:
        ballots = {
            (date.fromisoformat(key[:10]), int(key.rsplit(":", 1)[1]), party): name
            for key, party, name in conn.execute(
                """select x.value, pn.text, min(n.text)
                   from external_id x
                   join contest_option o on o.contest_id = x.entity_id
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   join name pn on pn.entity_id = o.party_id and pn.is_preferred
                   where x.scheme = 'us.contest' and x.value ~ '^20[0-9-]{8}:CA:house:[0-9]+$'
                   group by 1, 2 having count(*) = 1"""
            ).fetchall()
        }
    for election, day in ELECTIONS.items():
        names = {}
        for county in COUNTIES:
            found = [
                (matched[1] if matched[1][-2:].isdigit() else matched[1] + "01", matched[2])
                for line in raw.cached(SOURCE, codes(election, county)).path.read_text(errors="replace").splitlines()
                if (matched := CODE.match(line))
            ]
            shifted = {code[:-5] for code, name in found if name.strip().lower() == "percent"}
            names |= {code: name for code, name in found if code[:-5] not in shifted}
        for (held, district, party), name in ballots.items():
            for code, label in PARTIES.items():
                if held == day and party == label:
                    names.setdefault(f"CNG{district:02d}{code}01", name)
        frame = read(election)
        columns = [column for column in frame.columns if OFFICE.fullmatch(column)]
        long = (
            frame.filter(~pl.col("SVPREC").str.to_uppercase().str.contains("TOT"))
            .unpivot(columns, index=["FIPS", "SVPREC", "CDDIST"], variable_name="column", value_name="votes")
            .with_columns(pl.col("votes").replace("***", None).cast(pl.Float64).cast(pl.Int64))
            .filter(pl.col("votes").is_not_null() & (pl.col("votes") != 0))
            .with_columns(
                office=pl.col("column").str.slice(0, 3),
                district=pl.when(pl.col("column").str.starts_with("CNG")).then(
                    pl.col("CDDIST").cast(pl.Float64).cast(pl.Int16)
                ),
            )
            .with_columns(
                named=pl.concat_str(
                    "office",
                    pl.col("district").cast(pl.String).str.zfill(2),
                    pl.col("column").str.slice(3, 3),
                    pl.col("column").str.slice(6, 2).replace("", "01"),
                    ignore_nulls=True,
                )
            )
        )
        missing = long.filter(~pl.col("named").is_in(list(names)))
        if missing.height:
            print(f"{election}: {sorted(set(missing['named']))} have no reliable name in the codes, skipped")
            contest = pl.col("named").str.replace(r"[A-Z]{3}\d\d$", "")
            long = long.filter(~contest.is_in(list(set(missing.select(contest).to_series()))))
        frames.append(
            long.select(
                year=pl.lit(day.year),
                day=pl.lit(day),
                chamber=pl.col("office").replace_strict({k: v for k, (v, _) in OFFICES.items()}),
                special=pl.col("office").replace_strict({k: s for k, (_, s) in OFFICES.items()}),
                state=pl.lit("CA"),
                district="district",
                fips=pl.col("FIPS").str.zfill(5),
                code=pl.concat_str(
                    pl.lit(f"{day.year}:CA:"), pl.col("FIPS").str.zfill(5), pl.lit(":SOV:"), pl.col("SVPREC")
                ),
                candidate=pl.col("named").replace_strict(names).str.replace(r"\*$", "").str.to_uppercase(),
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "returns.parquet")


def emit() -> Path:
    years = [int(year) for year in os.environ.get("SWDB_YEARS", "").split(",") if year]
    counties = [county for county in os.environ.get("SWDB_COUNTIES", "").split(",") if county]
    returns = (
        pl.read_parquet(PARSED / "returns.parquet")
        .filter(pl.col("year").is_in(years) if years else pl.lit(True))
        .with_columns(
            code=pl.when(pl.col("fips").is_in(counties) if counties else pl.lit(True))
            .then(
                precinct_code.expr(
                    pl.col("year"), pl.col("state"), pl.col("fips"), pl.col("code").str.split(":SOV:").list.last()
                )
            )
            .otherwise("code"),
            measure=pl.lit(1, pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("precinct"),
            source=pl.lit("CA"),
        )
        .group_by(
            "year",
            "chamber",
            "day",
            "special",
            "state",
            "district",
            "code",
            "fips",
            "candidate",
            "measure",
            "kind",
            "source",
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's president, U.S. Senate, U.S. House, and governor general elections and top-two primaries by Statement of "
            "Vote precinct 2002–2024 from the Statewide Database, for contests no other source holds by precinct",
            polity="US",
        )
        wanted = oe_federal.wanted(conn, SOURCE, "CA")
        key = pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "chamber",
            pl.col("district").cast(pl.String),
            pl.when("special").then(pl.lit("special")),
            separator=":",
            ignore_nulls=True,
        )
        returns = returns.filter(key.is_in(list(wanted)))
        print(returns.group_by("year", "chamber", "special").agg(pl.col("votes").sum()).sort("year"))
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://statewidedatabase.org/election.html",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        for election, day in ELECTIONS.items():
            if day.year in set(returns["year"]):
                for address in (sov(election), *(codes(election, county) for county in COUNTIES)):
                    cs.artifact(raw.cached(SOURCE, address), source)
        medsl.attach(cs, conn, returns, {"CA": source}, grain="precinct", restage=True)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        canonical = returns.filter(~pl.col("code").str.contains(":SOV:"))["code"].unique()
        precinct_code.units(
            cs, conn, canonical.filter(canonical.map_elements(lambda code: str(cs.unit("us.precinct", code)) in used))
        )
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        minted = (
            returns.filter(pl.col("code").str.contains(":SOV:"))
            .group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-CA"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} precincts")
        return cs.write()
