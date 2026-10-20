import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, elstats_leg_counties
from ea.store import DATA, connect

SOURCE = "us.registered"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
OE_SOURCES = ["us.openelections", "us.oe_primary", "us.oe_legislature", "us.oe_precincts"]
FILE = re.compile(r"openelections-data-(\w\w)/.*/(\d{8})__\w\w__(\w+?)__(?:([a-z_]+?)__)?(?:precinct|county)")
LABEL = r"(?i)^registered voters$"


def scan() -> pl.DataFrame:
    parts = []
    for source in OE_SOURCES:
        index = json.loads((DATA / "raw" / source / "index.json").read_text())
        for url, entry in index.items():
            match = FILE.search(url)
            if match is None:
                continue
            path = DATA / "raw" / source / str(entry["file"])
            if not path.exists() or b"egistered" not in path.read_bytes():
                continue
            state, stamp, kind, place = match.groups()
            frame = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
            if "office" not in frame.columns or "votes" not in frame.columns:
                continue
            rows = frame.filter(pl.col("office").fill_null("").str.contains(LABEL))
            if "party" in frame.columns and kind == "primary":
                rows = rows.filter(pl.col("party").is_null())
            if not rows.height:
                continue
            parts.append(
                rows.select(
                    state=pl.lit(state.upper()),
                    day=pl.lit(date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))),
                    kind=pl.lit(kind),
                    county=(pl.col("county") if "county" in rows.columns else pl.lit(place)),
                    precinct=pl.col("precinct").str.strip_chars().str.to_lowercase().str.replace(r"\.0$", "")
                    if "precinct" in rows.columns
                    else pl.lit(None, pl.String),
                    registered=pl.col("votes").str.replace_all(",", "").cast(pl.Float64, strict=False).cast(pl.Int64),
                    url=pl.lit(url),
                    source=pl.lit(source),
                )
            )
    return pl.concat(parts)


def parse() -> None:
    frame = scan().filter(pl.col("registered").is_not_null() & pl.col("county").is_not_null())
    frame = (
        frame.group_by("state", "day", "kind", "county", "precinct", "url", "source")
        .agg(pl.col("registered").max())
        .group_by("state", "day", "kind", "county", "url", "source")
        .agg(pl.col("registered").sum().alias("total"), pl.col("registered").max().alias("largest"), pl.len().alias("precincts"))
        .with_columns(
            registered=pl.when((pl.col("total") - 2 * pl.col("largest")).abs() <= 0.02 * pl.col("largest") + 2)
            .then("largest")
            .otherwise("total")
        )
        .filter(pl.col("registered") > 0)
        .drop("total", "largest")
    )
    colorado = []
    for year in range(1980, 2003, 2):
        url = elstats_leg_counties.search("CO", year)
        table = pl.read_csv(raw.fetch(elstats_leg_counties.SOURCE, url, suffix=".csv").path, infer_schema=False)
        if "candidate_name" not in table.columns:
            continue
        counties = table.filter(
            pl.col("division_type") == "County", pl.col("office_name").str.contains(elstats_leg_counties.OFFICE)
        ).select(
            state=pl.lit("CO"),
            day=pl.col("election_date").str.slice(0, 10).str.to_date(),
            kind=pl.col("election_type").str.to_lowercase(),
            county=pl.col("division_name"),
            chamber=pl.col("office_name").str.to_lowercase().str.contains("senat"),
            name=pl.col("candidate_name").str.to_lowercase(),
            value=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            url=pl.lit(url),
            source=pl.lit(elstats_leg_counties.SOURCE),
        )
        colorado.append(
            counties.group_by("state", "day", "kind", "county", "chamber", "url", "source")
            .agg(
                pl.col("value").filter(pl.col("name") == "registered voters").sum().alias("registered"),
                pl.col("value").filter(pl.col("name") == "total ballots cast").sum().alias("ballots"),
            )
            .group_by("state", "day", "kind", "county", "url", "source")
            .agg(pl.col("registered").max(), pl.col("ballots").max())
            .filter(pl.col("registered") > 0)
            .with_columns(precincts=pl.lit(None, pl.UInt32))
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat([frame, *colorado], how="diagonal_relaxed").write_parquet(PARSED / "oe.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "oe.parquet")
    eavs = pl.read_parquet(DATA / "parsed" / "us.eavs" / "counties.parquet").filter(pl.col("registered").is_not_null())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters by county, summed from the precinct registration rows in OpenElections' files and from Colorado's ElectionStats archive",
            polity="US",
        )
        elections = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall())
        done = {value for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]).fetchall()}
        defaults = {
            election
            for (election,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default"
            ).fetchall()
        }
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' order by u.code"""
        ).fetchall():
            counties.setdefault(iso.removeprefix("US-"), {}).setdefault(elstats.plain(text), code)
        sources = {}
        artifacts = set()
        for (state, day, kind), rows in frame.group_by("state", "day", "kind", maintain_order=True):
            key = f"{day}:{state}" if kind == "general" else f"{day}:{state}:{kind}"
            if key not in elections:
                print(f"{key}: no election")
                continue
            if f"{key}:county" in done:
                continue
            names = counties.get(state, {})
            values = {}
            ballots = {}
            for county, registered, cast in rows.select("county", "registered", "ballots" if "ballots" in rows.columns else pl.lit(None).alias("ballots")).iter_rows():
                code = names.get(re.sub(r"(county|parish)$", "", elstats.plain(county)))
                if code is None:
                    print(f"{key}: {county!r} has no county unit")
                    continue
                values[code] = max(values.get(code, 0), registered)
                if kind == "general" and cast:
                    ballots[code] = max(ballots.get(code, 0), cast)
            if kind == "general":
                survey = {
                    code: registered
                    for code, registered in eavs.filter(pl.col("year") == day.year).select("code", "registered").iter_rows()
                    if registered
                }
                for code in [c for c, v in values.items() if c in survey and not 0.5 <= v / survey[c] <= 1.5]:
                    print(f"{key}: {code} registered {values.pop(code)} against {survey[code]} in the survey")
            if not values:
                continue
            if state not in sources:
                if state == "CO":
                    title, publisher, kind, license, link = (
                        elstats.TITLE,
                        elstats.TENANTS["CO"][1],
                        "official_body",
                        "Public record",
                        elstats.TENANTS["CO"][2],
                    )
                else:
                    title, publisher, kind, license, link = (
                        f"{state} precinct-level results",
                        "OpenElections",
                        "community",
                        "No license stated: facts published with attribution, raw files not redistributed",
                        f"https://github.com/openelections/openelections-data-{state.lower()}",
                    )
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add("source", id=sources[state], kind=kind, publisher=publisher, title=title, url=link, license=license)
            for source, url in rows.select("source", "url").unique().iter_rows():
                if (state, url) not in artifacts:
                    artifacts.add((state, url))
                    cs.artifact(raw.cached(source, url), sources[state])
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[key],
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=elections[key] not in defaults,
                completeness=round(len(values) / len(set(names.values())), 4),
            )
            for code, registered in values.items():
                for measure, value in ((2, registered), (3, ballots.get(code))):
                    if value and not (measure == 3 and value > registered):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
