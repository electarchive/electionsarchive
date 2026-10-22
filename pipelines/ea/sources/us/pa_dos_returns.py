import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.pa_dos_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Pennsylvania Department of State"
TITLE = "Precinct Election Returns"
LANDING = "https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics/election-data"
BASE = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/bulk-data/ElectionReturns_{year}_General_PrecinctReturns.txt"
DAYS = {
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
}
COLUMNS = [
    "year",
    "type",
    "county",
    "precinct",
    "office_rank",
    "candidate_district",
    "party_rank",
    "position",
    "office",
    "party",
    "candidate",
    "last",
    "first",
    "middle",
    "suffix",
    "votes",
    "usc",
    "sts",
    "sth",
]


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, BASE.format(year=year), suffix=".txt")


def read(year: int) -> pl.DataFrame:
    frame = pl.read_csv(
        raw.cached(SOURCE, BASE.format(year=year)).path,
        has_header=False,
        encoding="latin1",
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    return frame.select(frame.columns[: len(COLUMNS)]).rename(dict(zip(frame.columns, COLUMNS, strict=False)))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for year in DAYS:
        frame = read(year).filter(pl.col("office").is_in(["USC", "USS"]))
        frames.append(
            frame.select(
                pl.col("year").cast(pl.Int64),
                "office",
                (
                    pl.when(pl.col("office") == "USC").then(pl.col("candidate_district").cast(pl.Int64)).otherwise(0)
                ).alias("district"),
                (42000 + 2 * pl.col("county").cast(pl.Int64) - 1).cast(pl.String).alias("county"),
                pl.col("candidate").cast(pl.Int64),
                pl.concat_str(
                    [pl.col("first"), pl.col("middle"), pl.col("last"), pl.col("suffix")],
                    separator=" ",
                    ignore_nulls=True,
                )
                .str.replace_all(r"\s+", " ")
                .str.strip_chars()
                .alias("label"),
                "party",
                pl.col("votes").cast(pl.Int64),
            )
        )
    frame = (
        pl.concat(frames)
        .group_by("year", "office", "district", "county", "candidate", "label", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
    )
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "office")
        .agg(pl.col("district").n_unique(), pl.col("county").n_unique(), pl.col("candidate").n_unique())
        .sort("year", "office")
    )


SUMMARY = "Pennsylvania's U.S. House and Senate general elections of 1992 through 1998 by county, rolled up from the Department of State's precinct returns"
METHOD = (
    "County totals are sums of the Department of State's precinct returns for the general election (the bulk file extracted in 2005). "
    "Counties split between congressional districts are reported by the district each precinct voted in."
)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US-PA")
        sources: dict[int, object] = {}
        for year in DAYS:
            title = f"{TITLE} {year}"
            sources[year] = cs.source(PUBLISHER, title)
            if sources[year] in cs.minted:
                cs.add(
                    "source",
                    id=sources[year],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=LANDING,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, BASE.format(year=year)), sources[year])
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '42%%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                "select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '42%%'"
            ).fetchall()
        )
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':PA:(house:[0-9]+|senate)$'"
            )
        )
        for (year, office, district), rows in frame.group_by("year", "office", "district", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:PA:" + (f"house:{district}" if office == "USC" else "senate")
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
            old = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            if len(old) != 1:
                print(f"{key}: {len(old)} default result sets")
                continue
            row = old[0]
            ((county_rows,),) = conn.execute(
                """select count(*) from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [row[0]],
            ).fetchall()
            if county_rows:
                continue
            roots = conn.execute(
                """select o.id, o.ballot_label, o.kind, v.value from contest_option o
                   join result_value v on v.option_k = o.k join result_set r on r.k = v.result_set_k
                   where o.contest_id = %s and o.parent_option_id is null and r.id = %s""",
                [contest, row[0]],
            ).fetchall()
            writein = [option for option, _, kind, _ in roots if kind == "write_in"]
            people = [
                (option, re.sub(r"[^a-z]", "", label.lower()), int(value))
                for option, label, kind, value in roots
                if kind == "candidate"
            ]
            targets: dict[int, object] = {}
            notes: list[str] = []
            exact = True
            dropped = 0
            for candidate, label, votes in (
                rows.group_by("candidate", "label")
                .agg(pl.col("votes").sum())
                .select("candidate", "label", "votes")
                .iter_rows()
            ):
                letters = re.sub(r"[^a-z]", "", label.lower())
                if label in ("WRITE-IN", "OTHER"):
                    if len(writein) == 1:
                        targets[candidate] = writein[0]
                    else:
                        dropped += votes
                    continue
                found = [
                    p for p in people if p[1] == letters or p[1].endswith(letters[-7:]) or letters.endswith(p[1][-7:])
                ]
                found = [p for p in found if p[0] not in targets.values()]
                if len(found) != 1:
                    found = [p for p in people if p[2] == votes and p[0] not in targets.values()]
                if len(found) != 1:
                    print(f"{key}: {label} {votes} has no match in {[(p[1], p[2]) for p in people]}")
                    exact = None
                    break
                targets[candidate] = found[0][0]
                if found[0][2] != votes:
                    exact = False
                notes.append(f"{label.title()}: {votes:,} here, {found[0][2]:,} in the FEC set")
            if exact is None:
                continue
            exact = exact and {option for option, _, _ in people} <= set(targets.values())
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if exact:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=row[1],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=exact,
                supersedes_id=row[0] if exact else None,
                completeness=1,
            )
            body = (
                METHOD
                if exact
                else f"{METHOD} The county sums differ from the default result set's candidate totals: "
                + "; ".join(notes)
                + "."
            )
            if dropped:
                body += f" {dropped:,} write-in votes have no option in the archive's set and are left out."
            cs.add("note", entity_id=result_set, kind="methodology", body_md=body)
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            ((district_area,),) = conn.execute(
                "select district_area_id from contest where id = %s", [contest]
            ).fetchall()
            unit_ids = dict(units)
            if office == "USC":
                split = {
                    county
                    for (county,), g in frame.filter((pl.col("year") == year) & (pl.col("office") == "USC")).group_by(
                        "county"
                    )
                    if g["district"].n_unique() > 1
                }
                for code in rows["county"].unique():
                    if code not in split:
                        continue
                    part = f"{year}:{code}:{district}"
                    unit_ids[code] = cs.unit("us.county_part", part)
                    if unit_ids[code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit_ids[code],
                            polity_id=cs.polity("US-PA"),
                            scheme_code="us.county_part",
                            code=part,
                            parent_id=units[code],
                            area_relation="within",
                            area_id=areas[code],
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                        cs.add(
                            "reporting_unit_area",
                            unit_id=unit_ids[code],
                            area_id=district_area,
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
            merged: dict[tuple[str, object], int] = {}
            for county, candidate, votes in rows.select("county", "candidate", "votes").iter_rows():
                if candidate in targets:
                    merged[(county, targets[candidate])] = merged.get((county, targets[candidate]), 0) + votes
            for (county, option), votes in merged.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[county],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=house_books.DERIVED,
                    value=votes,
                )
        return cs.write()
