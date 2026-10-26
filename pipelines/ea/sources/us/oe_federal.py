import json
import os
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, oe_precincts, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(2016, 2026)
STATES = {state for state in os.environ.get("OE_STATES", "").upper().split(",") if state}
COMMITS = openelections.COMMITS | {}
CONTESTS = r"^20(1[6-9]|2[0-5])-1[01]-[0-9]{2}:[A-Z]{2}(-[0-9]{2})?:(president|senate|house|governor)(:|$)"


def picks() -> list[tuple[str, int, str]]:
    chosen: list[tuple[str, int, str]] = []
    for state, commit in COMMITS.items():
        if STATES and state not in STATES:
            continue
        tree = json.loads(
            raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)).path.read_text()
        )
        found: dict[str, dict[bool, list[str]]] = defaultdict(lambda: defaultdict(list))
        for entry in tree["tree"]:
            matched = re.fullmatch(
                rf"(\d{{4}})/(?:counties/)?((\d{{4}})1[01]\d{{2}})__{state.lower()}__general(__[a-z_]+)?__(?:precinct|ward)\.csv",
                entry["path"],
            )
            if entry["type"] == "blob" and matched and int(matched[3]) in YEARS and matched[1] == matched[3]:
                found[matched[2]][matched[4] is None].append(entry["path"])
        for _, files in sorted(found.items()):
            chosen.extend((state, int(path[:4]), path) for path in sorted(files[True] or files[False]))
    return chosen


def url(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path)


def fetch() -> None:
    for state, commit in COMMITS.items():
        if not STATES or state in STATES:
            try:
                raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit))
            except LookupError:
                raw.fetch(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit), suffix=".json")
    for state, _, path in picks():
        try:
            raw.cached(SOURCE, url(state, path))
        except LookupError:
            raw.fetch(SOURCE, url(state, path), suffix=".csv")


def parse() -> None:
    frame = oe_precincts.lines(
        [(state, year, path, raw.cached(SOURCE, url(state, path)).path.read_bytes()) for state, year, path in picks()]
    )
    office = pl.col("office").str.to_lowercase()
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        chamber=pl.when(office.str.contains(r"^(u\.?s\.? )?president|^presidential electors|^electors for president"))
        .then(pl.lit("president"))
        .when(office.str.contains(openelections.SENATE.pattern) & ~office.str.contains(r"\bstate\b"))
        .then(pl.lit("senate"))
        .when(office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
        .then(pl.lit("house"))
        .when(office.str.contains(r"^governor\b") & ~office.str.contains("lieutenant"))
        .then(pl.lit("governor")),
        day=pl.col("path").str.extract(r"(\d{8})__").str.to_date("%Y%m%d"),
    ).filter(pl.col("chamber").is_not_null()).write_parquet(
        PARSED / f"lines_{'_'.join(sorted(STATES)).lower()}.parquet"
    )


def emit() -> Path:
    frame = pl.concat(
        [pl.read_parquet(path) for path in sorted(PARSED.glob("lines_*.parquet"))], how="diagonal_relaxed"
    ).filter(pl.col("state").is_in(list(STATES)) if STATES else pl.lit(True))
    office = pl.col("office").str.to_lowercase()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, U.S. Senate, U.S. House, and governor general elections by precinct 2016–2025 from "
            "OpenElections, for contests no other source holds by precinct",
            polity="US",
        )
        wanted = {
            key
            for (key,) in conn.execute(
                """select x.value from external_id x
                   where x.scheme = 'us.contest' and x.value ~ %s
                     and not exists (
                       select from result_set r join changeset c on c.id = r.changeset_id
                       where r.contest_id = x.entity_id and r.status <> 'withdrawn' and c.pipeline <> %s
                         and coalesce(r.completeness, 1) >= 0.98
                         and exists (select from coverage_cache_unit cu join reporting_unit u on u.k = cu.unit_k
                                     where cu.result_set_k = r.k and u.scheme_code = 'us.precinct'))""",
                [CONTESTS, SOURCE],
            ).fetchall()
        }
        heads: dict[tuple[int, str], set[str]] = defaultdict(set)
        for text, key in conn.execute(
            """select n.text, k.value from name n join candidacy cy on cy.person_id = n.entity_id and cy.role = 'head'
               join contest_option o on o.id = cy.option_id
               join external_id k on k.entity_id = o.contest_id and k.scheme = 'us.contest'
               where n.is_preferred and k.value ~ '^20(1[6-9]|2[0-4])-11-[0-9]{2}:[A-Z]{2}:president$'"""
        ).fetchall():
            heads[(int(key[:4]), key[11:13])].update(re.sub(r"[^a-z]", "", word.lower()) for word in text.split())

        def head(row: dict[str, object]) -> str:
            candidate = str(row["candidate"])
            halves = [half.strip() for half in re.split(r"&|/| and ", candidate) if half.strip()]
            known = heads[(int(str(row["year"])), str(row["state"]))]
            named = [half for half in halves if re.sub(r"[^a-z]", "", half.split(",")[0].split()[-1].lower()) in known]
            return named[0] if len(halves) == 2 and len(named) == 1 else candidate

        returns = (
            frame.with_columns(
                special=office.str.contains("special|unexpired|vacancy")
                | pl.col("district").fill_null("").str.to_lowercase().str.contains("special|unexpired|vacancy"),
                district=pl.when(pl.col("chamber") == "house").then(
                    pl.coalesce(
                        pl.col("district").fill_null("").str.extract(r"(\d+)"), office.str.extract(r"(\d+)")
                    ).cast(pl.Int16, strict=False)
                ),
                code=pl.concat_str("year", "state", "fips", pl.lit(""), "precinct", separator=":"),
            )
            .with_columns(candidate=pl.struct("candidate", "year", "state").map_elements(head, return_dtype=pl.String))
            .with_columns(candidate=pl.col("candidate").str.to_uppercase())
            .filter(~pl.col("candidate").str.contains(openelections.NOT_COUNTED), pl.col("candidate") != "")
            .with_columns(
                measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
                writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            )
            .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
            .filter(pl.col("measure") > 0)
            .group_by("year", "chamber", "day", "special", "state", "district", "code", "fips", "candidate", "measure")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .with_columns(kind=pl.lit("precinct"), source="state")
        )
        base = pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "chamber",
            pl.col("district").cast(pl.String),
            separator=":",
            ignore_nulls=True,
        )
        returns = returns.filter(
            pl.when("special").then(base + ":special").otherwise(base).is_in(list(wanted))
            | (pl.col("chamber") == "house")
            & ~pl.col("special")
            & pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("house:0"), separator=":").is_in(
                list(wanted)
            )
        )
        print(returns.group_by("state", "year", "chamber").agg(pl.col("district").n_unique()).sort("state", "year"))
        sources: dict[str, UUID] = {}
        for state, _, path in picks():
            if state not in set(returns["state"]):
                continue
            title = f"{state} precinct-level results"
            if state not in sources:
                sources[state] = cs.source("OpenElections", title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="community",
                        publisher="OpenElections",
                        title=title,
                        url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                        license="No license stated: facts published with attribution, raw files not redistributed",
                    )
            cs.artifact(raw.cached(SOURCE, url(state, path)), sources[state])
        medsl.attach(cs, conn, returns, sources, grain="precinct")
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code = any(%s)",
                [returns["code"].unique().to_list()],
            ).fetchall()
        }
        minted = (
            returns.filter(~pl.col("code").is_in(list(existing)))
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
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
        print(f"{minted.height} new precincts")
        return cs.write()
