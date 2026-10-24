import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.la_oe_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    date(2003, 10, 4): "2003/20031004__la__primary__precinct.csv",
    date(2003, 11, 15): "2003/20031115__la__general__precinct.csv",
    date(2007, 10, 20): "2007/20071020__la__primary__precinct.csv",
    date(2007, 11, 17): "2007/20071117__la__general__precinct.csv",
    date(2011, 10, 22): "2011/20111022__la__primary__precinct.csv",
    date(2011, 11, 19): "2011/20111119__la__general__precinct.csv",
}
STATEWIDE = {
    "governor": "governor",
    "lieutenant governor": "lieutenant-governor",
    "secretary of state": "secretary-of-state",
    "attorney general": "attorney-general",
    "treasurer": "treasurer",
    "commissioner of insurance": "commissioner-of-insurance",
    "commissioner of agriculture and forestry": "commissioner-of-agriculture-and-forestry",
}


def url(path: str) -> str:
    return openelections.REPOSITORY.format(state="la", commit=openelections.COMMITS["LA"], path=path)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["LA"]
    frames = []
    for day, path in FILES.items():
        text = raw.cached(SOURCE, url(path)).path.read_bytes()
        frames.append(
            pl.read_csv(text.replace(b"\r\n", b"\n"), infer_schema_length=0, truncate_ragged_lines=True).with_columns(
                day=pl.lit(day)
            )
        )
    frame = (
        pl.concat(frames)
        .with_columns(
            county=pl.col("county").str.strip_chars(),
            fips=pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"saint ", "st ")
            .str.replace_all(r"\b(parish)\b", "")
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(fips, default=None),
            precinct=pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " "),
            office=pl.col("office").str.strip_chars(),
            district=pl.col("district").str.extract(r"(\d+)").cast(pl.Int16, strict=False),
            candidate=pl.col("candidate").fill_null("").str.strip_chars(),
            votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
        )
        .filter(pl.col("votes").is_not_null(), pl.col("precinct").is_not_null(), pl.col("precinct") != "")
    )
    lost = frame.filter(pl.col("fips").is_null())
    if lost.height:
        print(lost.group_by("county").agg(pl.col("votes").sum()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("fips").is_not_null()).write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "office").agg(pl.col("district").n_unique()).sort("day", "office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's 2003, 2007, and 2011 open primaries and runoffs for governor, statewide offices, and the legislature by precinct, from OpenElections",
            polity="US-LA",
        )
        source = cs.source("OpenElections", "LA precinct-level results")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="OpenElections",
                title="LA precinct-level results",
                url="https://github.com/openelections/openelections-data-la",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        cs.scope_units("us.precinct", "____-__-__:LA:%")
        keys = {
            key
            for (key,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^20(03|07|11)-1[01]-[0-9]{2}:LA:'"
            ).fetchall()
        }
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '22%%'"
            ).fetchall()
        }
        seats = []
        for day, office, district in frame.select("day", "office", "district").unique().iter_rows():
            if office in ("State Senate", "State House"):
                body = "senate" if office == "State Senate" else "house"
                base = f"{day}:LA:{body}:{district}"
                tried = [f"{base}:{suffix}" for suffix in ("lafsettled", "lafunsettled", "larunoff")]
            elif office.lower() in STATEWIDE:
                base = f"{day}:LA:{STATEWIDE[office.lower()]}"
                tried = [base, f"{base}:special"]
            else:
                continue
            key = next((key for key in tried if key in keys), None)
            if key is not None:
                seats.append((day, office, district, key.split(":", 2)[2]))
        seated = pl.DataFrame(
            seats,
            schema={"day": pl.Date, "office": pl.String, "district": pl.Int16, "chamber": pl.String},
            orient="row",
        )
        print(f"{seated.height} offices map to contest keys")
        returns = (
            frame.join(seated, on=["day", "office", "district"], how="inner", nulls_equal=True)
            .with_columns(candidate=pl.col("candidate").str.to_uppercase())
            .filter(~pl.col("candidate").str.contains(openelections.NOT_COUNTED), pl.col("candidate") != "")
            .with_columns(
                measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
                writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
                code=pl.concat_str(pl.col("day").cast(pl.String), pl.lit("LA"), "fips", pl.col("county").str.to_uppercase(), "precinct", separator=":"),
            )
            .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
            .filter(pl.col("measure") > 0)
            .group_by("day", "chamber", "code", "fips", "candidate", "measure")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .with_columns(
                year=pl.col("day").dt.year().cast(pl.Int32),
                special=pl.lit(False),
                state=pl.lit("LA"),
                district=pl.lit(None, pl.Int16),
                kind=pl.lit("precinct"),
                source=pl.lit("LA"),
            )
        )
        medsl.attach(cs, conn, returns.drop("fips"), {"LA": source}, grain="precinct", strict=True)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        units = (
            returns.group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-LA"))),
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
        print(f"{units.height} precincts")
        return cs.write()
