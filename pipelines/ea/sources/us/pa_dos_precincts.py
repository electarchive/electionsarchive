import os
import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.pa_dos_returns import LANDING, PUBLISHER
from ea.sources.us.pa_dos_returns import SOURCE as RAW_SOURCE
from ea.store import DATA, connect

SOURCE = "us.pa_dos_precincts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/bulk-data/"
GENERALS = {
    1992: ("ElectionReturns_1992_General_PrecinctReturns.txt", date(1992, 11, 3)),
    1994: ("ElectionReturns_1994_General_PrecinctReturns.txt", date(1994, 11, 8)),
    1996: ("ElectionReturns_1996_General_PrecinctReturns.txt", date(1996, 11, 5)),
    1998: ("ElectionReturns_1998_General_PrecinctReturns.txt", date(1998, 11, 3)),
    2000: ("ElectionReturns_2000_General_PrecinctRetuns.txt", date(2000, 11, 7)),
    2002: ("ElectionReturns_2002_General_PrecinctReturns.txt", date(2002, 11, 5)),
    2004: ("ElectionReturns_2004_General_Precinct.txt", date(2004, 11, 2)),
    2006: ("ElectionReturns_2006_General_PrecinctReturns.txt", date(2006, 11, 7)),
    2008: ("ElectionReturns_2008_General_PrecinctReturns.txt", date(2008, 11, 4)),
    2010: ("ElectionReturns_2010_General_PrecinctReturns.txt", date(2010, 11, 2)),
    2012: ("ElectionReturns_2012_General_PrecinctReturns.txt", date(2012, 11, 6)),
    2014: ("ElectionReturns_2014_General_PrecinctReturns.txt", date(2014, 11, 4)),
    2016: ("ElectionReturns_2016_General_PrecinctReturns.txt", date(2016, 11, 8)),
    2018: ("ElectionReturns_2018_General_PrecinctReturns.txt", date(2018, 11, 6)),
    2020: ("ElectionReturns_2020_General_PrecinctReturns.txt", date(2020, 11, 3)),
    2022: ("ElectionReturns_2022_General_PrecinctReturns.txt", date(2022, 11, 8)),
}
PRIMARIES = {
    1994: ("ElectionReturns_1994_Primary_PrecinctReturns.txt", date(1994, 5, 10)),
    1996: ("ElectionReturns_1996_Primary_PrecinctReturns.txt", date(1996, 4, 23)),
    1998: ("ElectionReturns_1998_Primary_Precinct_Returns.txt", date(1998, 5, 19)),
    2000: ("ElectionReturns_2000_Primary_PrecinctReturns.txt", date(2000, 4, 4)),
    2002: ("ElectionReturns_2002_Primary_PrecinctReturns.txt", date(2002, 5, 21)),
    2004: ("ElectionReturns_2004_Primary_PrecinctReturns.txt", date(2004, 4, 27)),
    2006: ("ElectionReturns_2006_Primary_PrecinctReturns.txt", date(2006, 5, 16)),
    2008: ("ElectionReturns_2008_Primary_PrecinctReturns.txt", date(2008, 4, 22)),
    2010: ("ElectionReturns_2010_Primary_PrecinctReturns.txt", date(2010, 5, 18)),
    2012: ("ElectionReturns_2012_Primary_PrecinctReturns.txt", date(2012, 4, 24)),
    2014: ("ElectionReturns_2014_Primary_PrecinctReturns.txt", date(2014, 5, 20)),
    2016: ("ElectionReturns_2016_Primary_PrecinctReturns.txt", date(2016, 4, 26)),
    2018: ("ElectionReturns_2018_Primary_PrecinctReturns.txt", date(2018, 5, 15)),
    2020: ("ElectionReturns_2020_Primary_PrecinctReturns.txt", date(2020, 6, 2)),
    2022: ("ElectionReturns_2022_Primary_PrecinctReturns.txt", date(2022, 5, 17)),
}
PARTIES = {"DEM": "democratic", "REP": "republican"}
CONTESTS = {
    "LTG": "lieutenant-governor",
    "STH": "house:{d}:g",
    "STS": "senate:{d}:g",
    "USC": "house:{d}",
    "USS": "senate",
    "USP": "president",
    "GOV": "governor",
    "ATT": "attorney-general",
    "AUD": "auditor-general",
    "TRE": "treasurer",
}
NAMES = [
    "year",
    "county",
    "precinct",
    "district",
    "office",
    "party",
    "candidate",
    "last",
    "first",
    "middle",
    "suffix",
    "votes",
    "municipality",
    "break1_code",
    "break1_name",
    "break2_code",
    "break2_name",
    "mcd",
    "fips",
    "vtd",
]


def url(year: int, primary: bool = False) -> str:
    return BASE + (PRIMARIES if primary else GENERALS)[year][0]


def fetch() -> None:
    for year in GENERALS:
        raw.fetch(RAW_SOURCE, url(year), suffix=".txt")
    for year in PRIMARIES:
        raw.fetch(RAW_SOURCE, url(year, True), suffix=".txt")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, primary in [(year, False) for year in GENERALS] + [(year, True) for year in PRIMARIES]:
        frame = pl.read_csv(
            raw.cached(RAW_SOURCE, url(year, primary)).path,
            has_header=False,
            encoding="latin1",
            infer_schema=False,
            truncate_ragged_lines=True,
        )
        shift = 2 if frame.width >= 37 else 0
        picks = [0, 2, 3, 5, 8, 9, 10, 11, 12, 13, 14, 15, 20 + shift, 21 + shift, 22 + shift, 23 + shift, 24 + shift]
        picks += [26 + shift, 27 + shift, 28 + shift]
        columns = [frame.columns[i] for i in picks]
        frame = frame.select(columns).rename(dict(zip(columns, NAMES, strict=True)))
        frame = frame.filter(pl.col("office").is_in(list(CONTESTS))).with_columns(
            pl.col("year", "county", "precinct", "district", "votes").cast(pl.Int64),
            pl.col("mcd", "fips", "vtd").fill_null(""),
            pl.col("municipality", "break1_code", "break1_name", "break2_code", "break2_name").fill_null(""),
        )
        frame.write_parquet(PARSED / f"{year}{'p' if primary else ''}.parquet")
        print(
            year,
            primary,
            frame.group_by("office").agg(pl.col("precinct").n_unique(), pl.col("votes").sum()).sort("office"),
        )


def emit() -> Path:
    year = int(os.environ["PA_YEAR"])
    offices = os.environ["PA_OFFICES"].split(",")
    primary = "PA_PRIMARY" in os.environ
    day = (PRIMARIES if primary else GENERALS)[year][1]
    title = f"Precinct Election Returns {year}" + (" Primary" if "PA_PRIMARY" in os.environ else "")
    frame = (
        pl.read_parquet(PARSED / f"{year}{'p' if primary else ''}.parquet")
        .filter(pl.col("office").is_in(offices))
        .filter(pl.col("party").is_in(list(PARTIES)) if primary else pl.lit(True))
        .with_columns(
            party=pl.col("party") if primary else pl.lit(""),
            district=pl.when(pl.col("office").is_in(["STH", "STS", "USC"])).then(pl.col("district")).otherwise(0),
            label=pl.concat_str(
                [pl.col("first"), pl.col("middle"), pl.col("last"), pl.col("suffix")], separator=" ", ignore_nulls=True
            )
            .str.replace_all(r"\s+", " ")
            .str.strip_chars(),
            unit=pl.format(
                "{}:PA:{}:{}", pl.lit(str(day)), pl.col("county").cast(pl.String).str.zfill(2), pl.col("precinct")
            ),
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Pennsylvania's {year} {'primary' if primary else 'general'} election returns by precinct from the Department of State's precinct files",
            polity="US-PA",
        )
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(RAW_SOURCE, url(year, primary)), source)
        counties = {
            (int(code) - 42000 + 1) // 2: (str(unit), str(area))
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '42%%'"
            ).fetchall()
        }
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like %s",
                [f"{day}:PA:%"],
            )
        )
        ours = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = %s and value like %s",
                [f"{SOURCE}.result_set", f"{day}:PA:%"],
            )
        )
        values = []
        memberships: set[tuple[str, str]] = set()
        loaded: set[str] = set()
        for (office, district, party), rows in frame.group_by("office", "district", "party", maintain_order=True):
            key = f"{day}:PA:" + CONTESTS[office].removesuffix(":g" if primary else "").format(d=district)
            if primary:
                key += ":" + (PARTIES[party][0] + "p" if office in ("STH", "STS") else PARTIES[party])
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
            if rows["votes"].sum() == 0:
                if key in ours:
                    columns = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of")
                    columns += ("completeness", "supersedes_id", "is_default")
                    stale = conn.execute(
                        f"select {', '.join(columns)} from result_set where id = %s", [ours[key]]
                    ).fetchone()
                    if stale is not None:
                        cs.add("result_set", **dict(zip(columns, stale, strict=True)) | {"status": "withdrawn"})
                        if stale[10] and stale[9] is not None:
                            prior = conn.execute(
                                f"select {', '.join(columns)} from result_set where id = %s", [stale[9]]
                            ).fetchone()
                            if prior is not None:
                                cs.add("result_set", **dict(zip(columns, prior, strict=True)) | {"is_default": True})
                continue
            current = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                          supersedes_id, is_default
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            if len(current) != 1:
                print(f"{key}: {len(current)} default result sets")
                continue
            row = current[0]
            if row[0] == ours.get(key):
                row = conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                              supersedes_id, is_default from result_set where id = %s""",
                    [row[9]],
                ).fetchone()
                if row is None:
                    print(f"{key}: our default has no predecessor")
                    continue
            ((k,),) = conn.execute("select k from result_set where id = %s", [row[0]]).fetchall()
            if conn.execute(
                f"""select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                    where v.result_set_k = {int(k)} and u.scheme_code = 'us.precinct' limit 1"""
            ).fetchone():
                continue
            roots = conn.execute(
                f"""select o.id, o.ballot_label, o.kind, sum(v.value) from contest_option o
                    join result_value v on v.option_k = o.k
                    where o.contest_id = %s and o.parent_option_id is null and v.result_set_k = {int(k)}
                      and v.measure = 1 and v.count_round = 0
                    group by o.id, o.ballot_label, o.kind""",
                [contest],
            ).fetchall()
            writein = [option for option, _, kind, _ in roots if kind == "write_in"]
            people = [
                (option, re.sub(r"(jr|sr|ii|iii|iv)$", "", re.sub(r"[^a-z]", "", label.lower())), int(value))
                for option, label, kind, value in roots
                if kind in ("candidate", "ticket")
            ]
            targets: dict[str, object] = {}
            notes: list[str] = []
            exact = True
            dropped = 0
            unmatched: list[tuple[str, str, int]] = []
            tallies = rows.group_by("candidate", "label").agg(pl.col("votes").sum()).rows()
            for candidate, label, _ in tallies:
                letters = re.sub(r"(jr|sr|ii|iii|iv)$", "", re.sub(r"[^a-z]", "", label.lower()))
                same = [p for p in people if p[1] == letters and p[0] not in targets.values()]
                if len(same) == 1 and label.upper() not in ("WRITE-IN", "OTHER", "WRITE IN", "SCATTERED"):
                    targets[candidate] = same[0][0]
            for candidate, label, votes in tallies:
                if candidate in targets:
                    held = next(p[2] for p in people if p[0] == targets[candidate])
                    if held != votes:
                        exact = False
                        notes.append(f"{label.title()}: {votes:,} here, {held:,} in the default set")
                    continue
                letters = re.sub(r"(jr|sr|ii|iii|iv)$", "", re.sub(r"[^a-z]", "", label.lower()))
                if label.upper() in ("WRITE-IN", "OTHER", "WRITE IN", "SCATTERED"):
                    if len(writein) == 1:
                        targets[candidate] = writein[0]
                    else:
                        dropped += votes
                    continue
                found = [
                    p
                    for p in people
                    if p[1] == letters
                    or p[1][:1] == letters[:1] and (p[1].endswith(letters[-7:]) or letters.endswith(p[1][-7:]))
                    or ("PA_ADD" in os.environ and p[1][-5:] == letters[-5:])
                ]
                found = [p for p in found if p[0] not in targets.values()]
                if len(found) != 1:
                    found = [p for p in people if p[2] == votes and p[0] not in targets.values()]
                if len(found) != 1:
                    unmatched.append((candidate, label, votes))
                    continue
                targets[candidate] = found[0][0]
                if found[0][2] != votes:
                    exact = False
                    notes.append(f"{label.title()}: {votes:,} here, {found[0][2]:,} in the default set")
            remaining = sorted((p for p in people if p[0] not in targets.values()), key=lambda p: p[2], reverse=True)
            if "PA_ADD" in os.environ and not remaining:
                for candidate, label, votes in unmatched:
                    display = label.title()
                    (person,) = conn.execute(
                        "select p.id from person p join name n on n.entity_id = p.id where n.text = %s limit 1",
                        [display],
                    ).fetchone() or (None,)
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", re.sub(r"[^a-z]", "", display.lower()))
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=display, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=display)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=display,
                        member_of_party_id=None,
                        is_independent=False,
                        is_incumbent=None,
                    )
                    targets[candidate] = option
                    notes.append(f"{display}: {votes:,} here, not in the default set")
                    exact = False
                unmatched = []
            if len(unmatched) != len(remaining):
                print(f"{key}: {[(u[1], u[2]) for u in unmatched]} has no match in {[(p[1], p[2]) for p in remaining]}")
                continue
            for (candidate, label, votes), person in zip(
                sorted(unmatched, key=lambda u: u[2], reverse=True), remaining, strict=True
            ):
                targets[candidate] = person[0]
                if person[2] != votes:
                    exact = False
                    notes.append(f"{label.title()}: {votes:,} here, {person[2]:,} in the default set as {person[1]}")
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
                            strict=False,
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
                source_id=source,
                is_default=exact,
                supersedes_id=row[0] if exact else None,
                completeness=1,
            )
            body = (
                "Precinct totals from the Department of State's precinct returns file for the "
                + ("primary" if primary else "general")
                + " election "
                "(extracted 2005 for 1992 through 2004 and by the Bureau of Commissions, Elections and Legislation after). "
                "County and district totals are rolled up from the precincts."
            )
            if not exact:
                body += " The sums differ from the default result set's candidate totals: " + "; ".join(notes) + "."
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
            merged: dict[tuple[str, str], int] = {}
            for unit, candidate, votes in rows.select("unit", "candidate", "votes").iter_rows():
                if candidate in targets:
                    merged[(unit, str(targets[candidate]))] = merged.get((unit, str(targets[candidate])), 0) + votes
            for (unit, option), votes in merged.items():
                values.append((str(result_set), unit, option, votes))
                if office in ("STH", "STS", "USC") and district_area is not None:
                    memberships.add((unit, str(district_area)))
            loaded.update(rows["unit"].unique())
        if not values:
            print("nothing to load")
            return cs.write()
        units = (
            frame.filter(pl.col("unit").is_in(list(loaded)))
            .group_by("unit")
            .agg(pl.all().exclude("unit").first())
            .with_columns(
                id=pl.col("unit").map_elements(
                    lambda code: str(cs.unit("us.precinct", code, day)), return_dtype=pl.String
                ),
                name=pl.concat_str(
                    ["municipality", "break1_code", "break1_name", "break2_code", "break2_name"], separator=" "
                )
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
            )
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-PA"))),
                scheme_code=pl.lit("us.precinct"),
                code="unit",
                parent_id=pl.col("county").replace_strict({n: unit for n, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("county").replace_strict({n: area for n, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from=pl.lit(day),
                valid_to=pl.lit(day + timedelta(days=1)),
            ),
        )
        cs.frame(
            "name",
            units.select(
                entity_id="id",
                text="name",
                lang=pl.lit("en"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )
        cs.frame(
            "external_id",
            units.select(
                entity_id="id",
                scheme=pl.lit(f"{SOURCE}.unit"),
                value=pl.format("{}:{}:{}:{}:{}", pl.lit(str(day)), "fips", "mcd", "vtd", "unit"),
            ),
        )
        unit_ids = dict(zip(units["unit"], units["id"], strict=True))
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [(unit_ids[unit], area) for unit, area in memberships],
                schema=["unit_id", "area_id"],
                orient="row",
            ).with_columns(valid_from=pl.lit(day), valid_to=pl.lit(day + timedelta(days=1))),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                [(rs, unit_ids[unit], option, votes) for rs, unit, option, votes in values],
                schema=["result_set_id", "unit_id", "option_id", "value"],
                orient="row",
            ).with_columns(channel=pl.lit(0), measure=pl.lit(1), count_round=pl.lit(0), flags=pl.lit(0)),
        )
        print(f"{len(loaded)} precincts, {len(values)} values, {len(cs.rows.get('result_set', []))} result set rows")
        return cs.write()
