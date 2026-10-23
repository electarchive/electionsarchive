import html
import json
import re
from datetime import datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl, nv_primaries, ri_federal, state_primaries
from ea.store import DATA, connect

SOURCE = "us.leg_primary_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATES = ["CT", "ID", "NY", "VT"]
NV_PARTIES = {"DEMOCRATIC": "dp", "REPUBLICAN": "rp"}


def fetch() -> None:
    pass


def parse() -> None:
    lines = pl.read_parquet(raw.parsed(state_primaries.SOURCE) / "lines.parquet").filter(
        pl.col("state").is_in(STATES),
        pl.col("office").is_in(["house", "senate", "assembly"]),
        pl.col("code").is_not_null(),
        pl.col("scheme") == "us.county",
    )
    extra = []
    for day, stamp, url in nv_primaries.FILES:
        text = raw.cached(nv_primaries.SOURCE, nv_primaries.address(stamp, url)).path.read_text(errors="ignore")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
        tokens = [
            token.strip() for token in re.sub(r"(\|\s*)+", "|", html.unescape(re.sub(r"<[^>]+>", "|", text))).split("|")
        ]
        for at, token in enumerate(tokens):
            if token != "Percent" or tokens[at + 1] != "Total Votes":
                continue
            found = re.match(
                r"STATE (SENATE|ASSEMBLY),? DISTRICT (\d+) \((\w+)\)$", " ".join(tokens[at - 1].upper().split())
            )
            if found is None or found[3] not in NV_PARTIES:
                continue
            names = []
            cursor = at + 2
            while tokens[cursor].upper() in nv_primaries.COUNTIES:
                names.append(tokens[cursor].upper())
                cursor += 1
            while cursor + 2 + len(names) < len(tokens) and re.fullmatch(r"[\d.]+%", tokens[cursor + 1]):
                for county, cell in zip(names, tokens[cursor + 3 : cursor + 3 + len(names)], strict=True):
                    if cell != "N/A" and not re.match(r"(NONE OF|UNDER VOTES|OVER VOTES)", tokens[cursor].upper()):
                        extra.append(
                            (
                                "NV",
                                day,
                                f"{found[1].lower()}:{int(found[2])}:{NV_PARTIES[found[3]]}",
                                nv_primaries.COUNTIES[county],
                                tokens[cursor].upper(),
                                int(cell.replace(",", "")),
                            )
                        )
                cursor += 3 + len(names)
    for folder in ri_federal.FOLDERS:
        if not folder.endswith("statewide_primary"):
            continue
        for town, fips in ri_federal.TOWNS.items():
            if (folder, town) in ri_federal.MISSING:
                continue
            data = json.loads(raw.cached(ri_federal.SOURCE, f"{ri_federal.BASE}{folder}/{town}.json").path.read_text())
            day = datetime.strptime(data["election_date"], "%B %d, %Y").date()
            for contest in data["contests"]:
                found = re.match(r"(SENATOR|REP) IN GENERAL ASSEMBLY DISTRICT (\d+)$", contest["name"].upper().strip())
                party = {"DEM": "dp", "REP": "rp"}.get((contest.get("contest_party_designation") or "").upper())
                if found is None or party is None:
                    continue
                for candidate in contest["candidates"]:
                    extra.append(
                        (
                            "RI",
                            day,
                            f"{'senate' if found[1] == 'SENATOR' else 'house'}:{int(found[2])}:{party}",
                            fips,
                            " ".join(html.unescape(candidate["name"]).split()).rstrip("*").strip().upper(),
                            int(str(candidate["votes"]).replace(",", "")),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(
        [
            lines.with_columns(
                chamber=pl.concat_str(
                    "office",
                    "code",
                    pl.col("party").replace_strict({party: row[2] for party, row in state_primaries.PARTIES.items()}),
                    separator=":",
                ),
                candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise(pl.col("choice").str.to_uppercase()),
            )
            .group_by("state", "day", "chamber", "unit", "candidate")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state="state",
                district=pl.lit(None, pl.Int16),
                code="unit",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein="writein",
                votes="votes",
                kind=pl.lit("county"),
                source="state",
            ),
            pl.DataFrame(extra, schema=["state", "day", "chamber", "code", "candidate", "votes"], orient="row")
            .group_by("state", "day", "chamber", "code", "candidate")
            .agg(pl.col("votes").sum())
            .select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state="state",
                district=pl.lit(None, pl.Int16),
                code="code",
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                votes="votes",
                kind=pl.lit("county"),
                source="state",
            ),
        ]
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State House, Senate, and Assembly primaries by county from the ElectionStats archives of "
            "Connecticut, Idaho, New York, and Vermont, Nevada's election summary pages, and Rhode Island's town results, "
            "for contests held only as district totals",
            polity="US",
        )
        totals = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x
                   where x.scheme = 'us.contest' and x.value ~ '^20[0-9-]{8}:(CT|ID|NV|NY|RI|VT):(house|senate|assembly):'
                     and exists (select from result_set r where r.contest_id = x.entity_id and r.status <> 'withdrawn')
                     and not exists (select from result_set r join result_value v on v.result_set_k = r.k
                                     join reporting_unit u on u.k = v.unit_k
                                     where r.contest_id = x.entity_id and r.status <> 'withdrawn'
                                       and u.scheme_code in ('us.county', 'us.town', 'us.county_part', 'us.precinct'))"""
            ).fetchall()
        }
        frame = frame.filter(
            pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(totals))
        )
        sources = {}
        for state in STATES:
            sources[state] = cs.source(elstats.TENANTS[state][1], elstats.TITLE)
            if sources[state] in cs.minted:
                raise ValueError(f"no source {elstats.TENANTS[state][1]}")
            for office in state_primaries.ELSTATS[state]:
                for year in frame.filter(pl.col("state") == state)["year"].unique().sort():
                    cs.artifact(
                        raw.cached(state_primaries.SOURCE, state_primaries.search(state, office, year)), sources[state]
                    )
        sources["NV"] = cs.source(nv_primaries.PUBLISHER, nv_primaries.TITLE)
        if sources["NV"] in cs.minted:
            raise ValueError(f"no source {nv_primaries.TITLE}")
        for day, stamp, url in nv_primaries.FILES:
            if day in frame.filter(pl.col("state") == "NV")["day"].to_list():
                cs.artifact(raw.cached(nv_primaries.SOURCE, nv_primaries.address(stamp, url)), sources["NV"])
        sources["RI"] = cs.source(ri_federal.PUBLISHER, ri_federal.TITLE)
        if sources["RI"] in cs.minted:
            raise ValueError(f"no source {ri_federal.TITLE}")
        for year in frame.filter(pl.col("state") == "RI")["year"].unique():
            for town in ri_federal.TOWNS:
                if (f"{year}/statewide_primary", town) not in ri_federal.MISSING:
                    cs.artifact(
                        raw.cached(ri_federal.SOURCE, f"{ri_federal.BASE}{year}/statewide_primary/{town}.json"),
                        sources["RI"],
                    )
        medsl.attach(cs, conn, frame, sources, extend=True)
        return cs.write()
