import json
import os
import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, oe_precincts, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(2000, 2026)
STATES = {state for state in os.environ.get("OE_STATES", "").upper().split(",") if state}
COMMITS = openelections.COMMITS | {}
TOP_TWO = {"WA", "AK"}
CONTESTS = r"^20([01][0-9]|2[0-5])-[01][0-9]-[0-9]{2}:[A-Z]{2}(-[0-9]{2})?:(president|senate|governor|house:[0-9]+)(:special)?$"


def wanted(conn: Connection, pipeline: str, state: str = "%", pattern: str = CONTESTS) -> set[str]:
    return {
        key
        for (key,) in conn.execute(
            """select x.value from external_id x
               where x.scheme = 'us.contest' and x.value ~ %s and split_part(x.value, ':', 2) like %s
                 and not exists (
                   select from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id = x.entity_id and r.status <> 'withdrawn' and c.pipeline <> %s
                     and coalesce(r.completeness, 1) >= 0.98
                     and exists (select from coverage_cache_unit cu join reporting_unit u on u.k = cu.unit_k
                                 where cu.result_set_k = r.k and u.scheme_code = 'us.precinct'))""",
            [pattern, state + "%", pipeline],
        ).fetchall()
    }


def picks(states: set[str] = STATES) -> list[tuple[str, int, str]]:
    chosen: list[tuple[str, int, str]] = []
    for state, commit in COMMITS.items():
        if states and state not in states:
            continue
        tree = json.loads(
            raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)).path.read_text()
        )
        found: dict[str, dict[bool, list[str]]] = defaultdict(lambda: defaultdict(list))
        for entry in tree["tree"]:
            matched = re.fullmatch(
                rf"(\d{{4}})/(?:counties/|General/)?((\d{{4}})\d{{4}})__{state.lower()}__(?:general{'|primary' if state in TOP_TWO else ''})"
                rf"(__?[a-z_]+?)?__(?:precinct|ward)\.csv",
                entry["path"],
            )
            if entry["type"] == "blob" and matched and int(matched[3]) in YEARS and matched[1] == matched[3]:
                found[matched[2]][matched[4] is None].append(entry["path"])
        for _, files in sorted(found.items()):
            chosen.extend((state, int(path[:4]), path) for path in sorted(files[True] + files[False]))
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
    swapped = (
        (pl.col("state") == "MS") & (pl.col("year") == 2016) & (pl.col("county") == "Perry") & (office == "u.s. house")
    )
    frame.with_columns(
        candidate=pl.when(swapped)
        .then(
            pl.col("candidate").replace(
                {"Shawn O'Hara": "Richard Blake McCluskey", "Richard Blake McCluskey": "Shawn O'Hara"}
            )
        )
        .otherwise("candidate"),
        party=pl.when(swapped)
        .then(pl.col("party").replace({"Reform": "Libertarian", "Libertarian": "Reform"}))
        .otherwise("party"),
        chamber=pl.when(office.str.contains(r"^(u\.?s\.? )?president|^presidential electors|^electors for president"))
        .then(pl.lit("president"))
        .when(office.str.contains(openelections.SENATE.pattern) & ~office.str.contains(r"\bstate\b"))
        .then(pl.lit("senate"))
        .when(office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
        .then(pl.lit("house"))
        .when(office.str.contains(r"^governor\b"))
        .then(pl.lit("governor")),
        day=pl.col("path").str.extract(r"(\d{8})__").str.to_date("%Y%m%d"),
        statewide=pl.col("fips").n_unique().over("path") > 1,
    ).filter(pl.col("chamber").is_not_null()).filter(
        pl.col("statewide") | ~pl.col("statewide").any().over("state", "day", "fips", "chamber")
    ).drop("statewide").write_parquet(PARSED / f"lines_{'_'.join(sorted(STATES)).lower()}.parquet")


def emit() -> Path:
    frame = (
        pl.concat(
            [
                pl.read_parquet(path).with_columns(parsed=pl.lit(path.stat().st_mtime))
                for path in sorted(PARSED.glob("lines_*.parquet"))
            ],
            how="diagonal_relaxed",
        )
        .filter(pl.col("parsed") == pl.col("parsed").max().over("path"))
        .drop("parsed")
        .filter(pl.col("state").is_in(list(STATES)) if STATES else pl.lit(True))
    )
    office = pl.col("office").str.to_lowercase()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, U.S. Senate, U.S. House, and governor general elections by precinct 2000–2025 from "
            "OpenElections, for contests no other source holds by precinct",
            polity="US",
        )
        gaps = wanted(conn, SOURCE)
        heads: dict[tuple[int, str, str], set[str]] = defaultdict(set)
        for text, key in conn.execute(
            """select n.text, k.value from name n join candidacy cy on cy.person_id = n.entity_id and cy.role = 'head'
               join contest_option o on o.id = cy.option_id
               join external_id k on k.entity_id = o.contest_id and k.scheme = 'us.contest'
               where n.is_preferred and k.value ~ '^20([01][0-9]|2[0-5])-[01][0-9]-[0-9]{2}:[A-Z]{2}:(president|governor)$'"""
        ).fetchall():
            heads[(int(key[:4]), key[11:13], key[14:])].update(
                re.sub(r"[^a-z]", "", word.lower()) for word in text.split()
            )

        def head(row: dict[str, object]) -> str:
            candidate = str(row["candidate"])
            known = heads[(int(str(row["year"])), str(row["state"]), str(row["chamber"]))]
            halves = [half.strip() for half in re.split(r"&|/|\\| and ", candidate) if half.strip()]
            if len(halves) == 1 and known:
                words = candidate.split()
                found = [index for index, word in enumerate(words) if re.sub(r"[^a-z]", "", word.lower()) in known]
                return " ".join(words[: found[-1] + 1]) if found else candidate
            if len(halves) != 2:
                return candidate
            named = [half for half in halves if re.sub(r"[^a-z]", "", half.split(",")[0].split()[-1].lower()) in known]
            return named[0] if len(named) == 1 else halves[0]

        base = pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "chamber",
            pl.col("district").cast(pl.String),
            separator=":",
            ignore_nulls=True,
        )
        frame = frame.with_columns(
            seat=pl.when(pl.col("chamber") == "house").then(
                pl.coalesce(pl.col("district").fill_null("").str.extract(r"(\d+)"), office.str.extract(r"(\d+)")).cast(
                    pl.Int16, strict=False
                )
            ),
            name=pl.col("candidate").str.to_uppercase().str.replace_all(r"[^A-Z]", ""),
        )
        seats = (
            frame.filter(
                (pl.col("chamber") == "house")
                & pl.col("seat").is_not_null()
                & ~pl.col("name").str.contains("WRITE|VOTE|^$|SCATTER|BLANK|OVER|UNDER")
            )
            .group_by("state", "year", "name", "seat")
            .agg(pl.col("votes").sum())
            .sort("votes", descending=True)
            .group_by("state", "year", "name")
            .agg(pl.col("seat").first().alias("held"))
        )
        returns = (
            frame.join(seats, on=["state", "year", "name"], how="left")
            .with_columns(
                special=office.str.contains("special|unexpired|vacancy")
                | pl.col("district").fill_null("").str.to_lowercase().str.contains("special|unexpired|vacancy"),
                district=pl.when(pl.col("chamber") == "house").then(pl.coalesce("held", "seat")),
                code=pl.concat_str("year", "state", "fips", pl.lit(""), "precinct", separator=":"),
            )
            .filter(
                pl.when("special").then(base + ":special").otherwise(base).is_in(list(gaps))
                | (pl.col("chamber") == "house")
                & ~pl.col("special")
                & pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("house:0"), separator=":").is_in(
                    list(gaps)
                )
            )
            .with_columns(
                candidate=pl.struct("candidate", "year", "state", "chamber").map_elements(head, return_dtype=pl.String)
            )
            .with_columns(candidate=pl.col("candidate").str.to_uppercase())
            .filter(
                ~pl.col("candidate").str.contains(openelections.NOT_COUNTED),
                ~pl.col("candidate").str.contains(r"^NONE OF THE"),
                pl.col("candidate") != "",
            )
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
        print(returns.group_by("state", "year", "chamber").agg(pl.col("district").n_unique()).sort("state", "year"))
        sources: dict[str, UUID] = {}
        for state, _, path in picks(set(returns["state"])):
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
        medsl.attach(
            cs,
            conn,
            returns,
            sources,
            grain="precinct",
            extend=os.environ.get("OE_EXTEND") == "1",
            redone=frozenset(os.environ.get("OE_REDONE", "").split(",")) - {""},
        )
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
