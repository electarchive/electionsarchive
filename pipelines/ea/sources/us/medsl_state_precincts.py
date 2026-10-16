import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, medsl_state
from ea.sources.us.klarner import LOWER
from ea.sources.us.medsl_precincts import METHOD
from ea.store import DATA, connect

SOURCE = "us.medsl_state_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SWAPPED = {
    ("04025", "JAY MARCIA"): "MARCIA SMITH",
    ("04025", "RUBY SMITH"): "JAY RUBY",
    ("36031", "BILLY D JONES"): "D BILLY JONES",
}
POOLS = ("write-ins", "all-others", "scattering", "scatter", "others", "misc", "write-in")


def fetch() -> None:
    medsl_state.fetch()


def parse() -> None:
    frame = medsl.precincts({("state", 2024): raw.cached(medsl_state.SOURCE, medsl_state.RETURNS).path}).with_columns(
        pl.col("precinct", "jurisdiction").str.strip_chars(),
        seat=pl.col("seat").str.strip_chars().str.to_uppercase(),
        candidate=pl.struct("code", "candidate").map_elements(
            lambda row: SWAPPED.get((row["code"], row["candidate"]), row["candidate"]), return_dtype=pl.String
        ),
    )
    contest = ["state", "office", "seat", "special"]
    recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select("state", "office", "seat").unique()
    frame = pl.concat(
        [
            frame.filter(pl.col("stage") == "GEN").join(recounted, on=["state", "office", "seat"], how="anti"),
            frame.filter(pl.col("stage") == "GEN RECOUNT"),
        ]
    )
    homes = (
        frame.filter(pl.col("seat").is_not_null(), ~pl.col("writein"), pl.col("measure") == 1)
        .select("state", "office", "candidate", "seat", "special")
        .unique()
        .filter(pl.len().over("state", "office", "candidate") == 1)
        .rename({"seat": "home", "special": "home_special"})
    )
    frame = (
        frame.join(homes, on=["state", "office", "candidate"], how="left")
        .with_columns(
            seat=pl.coalesce("seat", pl.when(~pl.col("writein")).then("home")),
            special=pl.when(pl.col("seat").is_null() & ~pl.col("writein"))
            .then(pl.coalesce("home_special", "special"))
            .otherwise("special"),
        )
        .filter(pl.col("seat").is_not_null(), pl.col("seat") != "STATEWIDE")
        .with_columns(
            kind=pl.when(pl.col("kind").is_in(["county", "place"]) & (pl.col("precinct") != ""))
            .then(pl.lit("precinct"))
            .otherwise("kind"),
        )
        .with_columns(
            county=pl.when(pl.col("kind") == "precinct").then("code"),
            code=pl.when(pl.col("kind") == "precinct")
            .then(pl.concat_str("year", "state", "code", "jurisdiction", "precinct", separator=":"))
            .otherwise("code"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("kind") == "precinct").group_by("code").agg(
        pl.col("state").first(), pl.col("county").first(), pl.col("day").min()
    ).sort("code").write_parquet(PARSED / "units.parquet")
    frame.group_by(*contest, "kind", "code", "candidate", "party", "writein", "measure", maintain_order=True).agg(
        pl.col("votes").sum()
    ).write_parquet(PARSED / "returns.parquet")
    print(f"{frame.filter(pl.col('kind') == 'precinct')['code'].n_unique()} precincts")


def emit() -> Path:
    returns = pl.read_parquet(PARSED / "returns.parquet")
    day = medsl_state.DAY
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State house and senate results by precinct 2024, from the MIT Election Data and Science Lab",
            polity="US",
        )
        codes: dict[str, set[str]] = defaultdict(set)
        for scheme, code in conn.execute(
            "select a.scheme_code, c.code from area a join area_code c on c.area_id = a.id"
            " where a.scheme_code in ('us.sldu', 'us.sldl')"
        ).fetchall():
            codes[scheme].add(code)
        contests: dict[str, tuple[UUID, UUID]] = {
            key: (contest, election)
            for key, contest, election in conn.execute(
                """select x.value, c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value like %s""",
                [f"{day}:%"],
            ).fetchall()
        }
        options: dict[UUID, dict[str, tuple[UUID, str, UUID | None, int]]] = defaultdict(dict)
        for contest, key, option, kind, parent, votes in conn.execute(
            """select o.contest_id, x.value, o.id, o.kind, o.parent_option_id, coalesce(sum(v.value), 0)::bigint
               from contest_option o join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
               join result_set rs on rs.contest_id = o.contest_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.value like %s group by 1, 2, 3, 4, 5""",
            [f"{day}:%"],
        ).fetchall():
            options[contest][key] = (option, kind, parent, votes)
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        source = cs.source(medsl_state.PUBLISHER, medsl_state.TITLE)
        if source in cs.minted:
            raise ValueError(f"{medsl_state.TITLE} must already be a source")
        values: list[pl.DataFrame] = []
        result_sets: list[UUID] = []
        for (state, office, seat, special), lines in returns.group_by(
            "state", "office", "seat", "special", maintain_order=True
        ):
            if state == "NE":
                body_key, scheme = "NE:legislature", "us.sldu"
            elif office == "STATE SENATE":
                body_key, scheme = f"{state}:senate", "us.sldu"
            else:
                chamber = LOWER.get(state, "House of Representatives")
                body_key = f"{state}:{slug(chamber.removeprefix('State ').removesuffix(' of Representatives'))}"
                scheme = "us.sldl"
            code, post = medsl_state._district(state, str(seat))
            if f"{state}:{code}" not in codes[scheme]:
                letters = sorted(code.split("-")[0])
                number = code.split("-")[1] if "-" in code else None
                near = [
                    known
                    for known in codes[scheme]
                    if known.startswith(f"{state}:")
                    and sorted(known.split(":")[1].split("-")[0]) == letters
                    and (known.split("-")[1] if "-" in known else None) == number
                ]
                if len(near) == 1:
                    code = near[0].split(":")[1]
            key = (
                f"{day}:{state}:{body_key.split(':')[1]}:{code}"
                + (f":{post}" if post else "")
                + (":gs" if special else ":g")
            )
            total = int(lines.filter(pl.col("measure") == 1)["votes"].sum())
            if key not in contests:
                print(f"{key}: no contest ({office} {seat}), {total} skipped")
                continue
            contest, election = contests[key]
            known = options[contest]
            roots = {
                option_key: option
                for option_key, (option, kind, parent, _) in known.items()
                if parent is None and kind != "write_in"
            }
            children = {known[option_key][2] for option_key in known if known[option_key][2] is not None}
            picked: dict[tuple[str, str | None], UUID | None] = {}
            unmatched = 0
            for candidate, party, writein, votes in (
                lines.filter(pl.col("measure") == 1)
                .group_by("candidate", "party", "writein", maintain_order=True)
                .agg(pl.col("votes").sum())
                .iter_rows()
            ):
                root = None
                if candidate in medsl_state.DROPPED and not writein:
                    continue
                if writein:
                    root = next((f"{key}:{pool}" for pool in POOLS if f"{key}:{pool}" in known), None)
                    if root is None:
                        picked[(candidate, party)] = None
                        continue
                elif f"{key}:{slug(candidate)}" in known:
                    root = f"{key}:{slug(candidate)}"
                else:
                    words = [
                        word
                        for word in re.sub(r'"[^"]*"', " ", candidate).split()
                        if word not in {"JR", "SR", "II", "III", "IV"}
                    ]
                    if words:
                        surname, initial = re.sub(r"[^a-z]", "", slug(words[-1])), slug(words[0])[:1]
                        same = [
                            option_key
                            for option_key in roots
                            if re.sub(r"[^a-z]", "", option_key.removeprefix(f"{key}:").removesuffix("-jr")).endswith(
                                surname
                            )
                        ]
                        first = [option_key for option_key in same if option_key.removeprefix(f"{key}:")[:1] == initial]
                        root = next((group[0] for group in (first, same) if len(group) == 1), None)
                if root is not None and known[root][0] in children:
                    root = (
                        f"{root}:{slug(party or 'no party')}"
                        if f"{root}:{slug(party or 'no party')}" in known
                        else None
                    )
                if root is None:
                    unmatched += votes
                    continue
                picked[(candidate, party)] = known[root][0]
            tallies: dict[UUID, int] = defaultdict(int)
            for candidate, party, votes in (
                lines.filter(pl.col("measure") == 1).select("candidate", "party", "votes").iter_rows()
            ):
                if (option := picked.get((candidate, party))) is not None:
                    tallies[option] += votes
            official = sum(votes for _, _, _, votes in known.values())
            drifted = [
                (option_key, tallies.get(option, 0), votes)
                for option_key, (option, _, _, votes) in known.items()
                if option not in children and abs(tallies.get(option, 0) - votes) > max(10, votes * 0.005)
            ]
            if drifted:
                print(f"{key}: precinct sums differ from the district totals {drifted[:3]}, skipped")
                continue
            if unmatched > max(10, official * 0.01):
                print(f"{key}: {unmatched} votes match no option, skipped")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            result_sets.append(result_set)
            if None in picked.values():
                pool = cs.key("us.option", f"{key}:write-ins")
                cs.add("contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                picked = {line: pool if option is None else option for line, option in picked.items()}
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(sum(tallies.values()) / official, 4) if official else None,
            )
            members: dict[str, str] = {}
            for unit_code, kind in lines.select("code", "kind").unique().iter_rows():
                if kind in ("county", "place"):
                    if unit_code not in counties:
                        raise ValueError(f"{key}: no county {unit_code}")
                    members[unit_code] = str(counties[unit_code][0])
                elif kind == "precinct":
                    members[unit_code] = str(cs.unit("us.precinct", unit_code))
                else:
                    unit = cs.unit("us.ak_house_district" if kind == "district" else "us.aggregate", unit_code)
                    if unit in cs.minted:
                        raise ValueError(f"{key}: no unit {kind} {unit_code}")
                    members[unit_code] = str(unit)
            values.append(
                lines.join(
                    pl.DataFrame(
                        [(candidate, party, str(option)) for (candidate, party), option in picked.items()],
                        schema={"candidate": pl.String, "party": pl.String, "option": pl.String},
                        orient="row",
                    ),
                    on=["candidate", "party"],
                    how="left",
                    nulls_equal=True,
                )
                .filter((pl.col("measure") != 1) | pl.col("option").is_not_null())
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").replace_strict(members),
                    option_id=pl.when(pl.col("measure") == 1).then("option"),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.col("measure"),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.when(pl.col("kind") == "aggregate")
                    .then(pl.lit(32, pl.Int16))
                    .otherwise(pl.lit(0, pl.Int16)),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
        frame = pl.concat(values)
        shaped: dict[str, str] = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code like '2024:%'"""
            ).fetchall()
        }
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code like '2024:%'"
            ).fetchall()
        }
        used = set(frame["unit_id"].unique())
        units = (
            pl.read_parquet(PARSED / "units.parquet")
            .filter(~pl.col("code").is_in(list(existing)))
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.when(pl.col("code").is_in(list(shaped)))
                .then(pl.lit("equals"))
                .otherwise(pl.lit("within")),
                area_id=pl.col("code").replace_strict(
                    shaped,
                    default=pl.col("county").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                ),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        cs.frame("result_value", frame)
        for result_set in result_sets:
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                valid_from_precision="day",
                valid_to_precision="day",
            )
        print(f"{len(result_sets)} result sets, {frame.height} values, {units.height} new precincts")
        return cs.write()
