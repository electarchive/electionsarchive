from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_general"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
RESULTS = "https://www.vote.nyc/sites/default/files/pdf/election_results"
GENERALS = {
    date(2021, 11, 2): f"{RESULTS}/2021/20211102General%20Election/00000200000Citywide%20Mayor%20Citywide",
    date(2025, 11, 4): f"{RESULTS}/2025/20251104General%20Election/00000100000Citywide%20Mayor%20Citywide",
}
PUBLISHER = "Board of Elections in the City of New York"
COUNTIES = {
    "New York": "36061",
    "Bronx": "36005",
    "Kings": "36047",
    "Queens": "36081",
    "Richmond": "36085",
}
COUNTERS = ("Public Counter", "Manually Counted Emergency", "Absentee / Military", "Affidavit")
SCATTERED = "Scattered"
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
    "Save Our City": "NY:save-our-city",
    "Libertarian": "libertarian-party",
    "Socialism & Lib": "party-for-socialism-and-liberation",
    "Independent": None,
    "Safe&Affordable/EndAntiSemitism": None,
}
ALGED = {
    "Eric L. Adams": "35570",
    "Curtis A. Sliwa": "35566",
    "Catherine Rojas": "35565",
    "William A. Pepitone": "35623",
    "Quanda S. Francis": "35603",
    "Stacey H. Prussman": "35615",
    "Raja Michael Flores": "35607",
    "Fernando Mateo": "35573",
    "Skiboky Stora": "35617",
}
PERSONS = {
    "Zohran Kwame Mamdani": ("us.klarner.person", "400070"),
    "Andrew M. Cuomo": ("wikidata", "Q11673"),
    "Curtis A. Sliwa": ("us.alged.person", "35566"),
    "Eric L. Adams": ("us.klarner.person", "277292"),
}
ALGED_CONTEST = "alged:3651000_2021_11_new york_NY_Mayor_mayor_1"
CONTEST_2025 = "2025-11-04:new-york-city:mayor"
NOTE = (
    "Election district returns from the Board of Elections' certified ED-level report. Each candidate's votes are "
    "split by ballot line, as New York's fusion ballot prints them, and write-ins are one Scattered line, as the "
    "report gives them. Ballots cast are the sum of the report's public counter, emergency, absentee and military, "
    "and affidavit ballots. Election districts the Board combined into another for the count hold no values and "
    "point to the district they were counted with, and districts missing from City Planning's map for the election, all with no ballots, have no shape. Summed by borough, every line matches the Board's certified "
    "recap."
)


def fetch() -> None:
    for stem in GENERALS.values():
        raw.fetch(SOURCE, f"{stem}%20EDLevel.csv", suffix=".csv")
        raw.fetch(SOURCE, f"{stem}%20Recap.csv", suffix=".csv")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for day, stem in GENERALS.items():
        rows = (
            pl.read_csv(raw.cached(SOURCE, f"{stem}%20EDLevel.csv").path, has_header=False, infer_schema=False)
            .rename({"column_11": "ad", "column_12": "ed", "column_13": "county", "column_14": "status"})
            .rename({"column_20": "unit", "column_21": "tally"})
            .select(
                date=pl.lit(day),
                code=pl.col("ad") + pl.col("ed"),
                county=pl.col("county").replace_strict(COUNTIES),
                combined=pl.col("status")
                .str.extract(r"^COMBINED INTO (\d{3})/(\d{2})$", 2)
                .add(pl.col("status").str.extract(r"^COMBINED INTO (\d{3})/(\d{2})$", 1)),
                status="status",
                unit="unit",
                votes=pl.col("tally").str.replace_all(",", "").cast(pl.Int64),
            )
        )
        odd = rows.filter((pl.col("status") != "IN-PLAY") & pl.col("combined").is_null())
        if odd.height:
            raise ValueError(f"{day}: unknown ED statuses {odd['status'].unique().to_list()}")
        if rows.filter(pl.col("combined").is_not_null() & (pl.col("votes") != 0)).height:
            raise ValueError(f"{day}: a combined election district holds votes")
        recap = (
            pl.read_csv(raw.cached(SOURCE, f"{stem}%20Recap.csv").path, has_header=False, infer_schema=False)
            .filter(pl.nth(3).str.ends_with(" County"))
            .select(
                county=pl.nth(3).str.replace(r" County$", "").replace_strict(COUNTIES),
                unit=pl.nth(4).str.replace(r" \(Write-In\)$", "").str.strip_chars(),
                write_in=pl.nth(4).str.ends_with("(Write-In)"),
                votes=pl.nth(5).str.replace_all(",", "").cast(pl.Int64),
            )
            .with_columns(unit=pl.when("write_in").then(pl.lit(SCATTERED)).otherwise("unit"))
            .group_by("county", "unit")
            .agg(pl.col("votes").sum())
        )
        differences = (
            rows.group_by("county", "unit")
            .agg(pl.col("votes").sum())
            .join(recap, on=["county", "unit"], how="full", coalesce=True, suffix="_recap")
            .filter(pl.col("votes").fill_null(0) != pl.col("votes_recap").fill_null(0))
        )
        if differences.height:
            raise ValueError(f"{day}: the ED-level report differs from the recap:\n{differences}")
        frames.append(rows.drop("status"))
    pl.concat(frames).write_parquet(PARSED / "districts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's 2021 and 2025 mayoral general elections by election district",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        city = cs.key("us.polity", "new-york-city")
        office = cs.key("us.office", "NY:3651000:mayor")
        place = cs.area("us.place", "3651000")
        if city in cs.minted or office in cs.minted:
            raise ValueError("New York City and its mayor must already exist")
        parties: dict[str, UUID | None] = {}
        for line in (
            frame.filter(pl.col("unit").str.contains(r"\("))
            .select(pl.col("unit").str.extract(r"\((.+)\)$"))["unit"]
            .unique()
            .sort()
        ):
            key = PARTIES.get(line, f"NY:{slug(line)}")
            if key is None:
                parties[line] = None
                continue
            party = cs.key("us.party", key)
            if party in cs.minted:
                if line in PARTIES:
                    raise ValueError(f"the {line} party must already exist")
                cs.add("party", id=party, kind="party", polity_id=cs.polity("US-NY"))
                name(party, line)
            parties[line] = party

        for day, stem in GENERALS.items():
            rows = frame.filter(pl.col("date") == day)
            title = f"{day.year} General Election: Mayor, Citywide, ED Level"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{stem}%20EDLevel.csv",
                license="Public record",
            )
            for suffix in ("EDLevel", "Recap"):
                cs.artifact(raw.cached(SOURCE, f"{stem}%20{suffix}.csv"), source)

            places = {
                code: (area, county)
                for code, area, county in conn.execute(
                    """select c.code, c.area_id, u.id from area_code c
                       join area a on a.id = c.area_id and a.scheme_code = 'us.nyc_ed'
                       join area_parent p on p.area_id = c.area_id and p.valid @> %s::date
                       join area_code pc on pc.area_id = p.parent_id
                       join reporting_unit u on u.scheme_code = 'us.county' and u.code = pc.code
                       where c.valid @> %s::date""",
                    [day, day],
                ).fetchall()
            }
            districts = (
                rows.group_by("code")
                .agg(pl.col("county").first(), pl.col("combined").first(), pl.col("votes").max())
                .sort("code")
            )
            counties = {fips: cs.unit("us.county", fips) for fips in COUNTIES.values()}
            units = {code: cs.unit("us.nyc_ed", code, day) for code in districts["code"]}
            for code, county, combined, most in districts.iter_rows():
                area, parent = places.get(code, (None, counties[county]))
                if area is None and most:
                    raise ValueError(f"{day}: no area for election district {code}, which has votes")
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=city,
                    scheme_code="us.nyc_ed",
                    code=code,
                    parent_id=parent,
                    counted_with_id=None if combined is None else units[combined],
                    area_relation="unlocated" if area is None else "equals",
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=units[code],
                    area_id=place,
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )

            if day.year == 2021:
                contest_key = ALGED_CONTEST
                contest = cs.key("us.contest", contest_key)
                election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
                if contest in cs.minted or election is None:
                    raise ValueError(f"{contest_key} must already exist")
                election = election[0]
                roots = {label: cs.key("us.option", f"{contest_key}:{person}") for label, person in ALGED.items()}
                if cs.minted & set(roots.values()):
                    raise ValueError(f"{contest_key}: ALGED's candidate options must already exist")
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                default = True
                columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
                prior = conn.execute(
                    f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                        join external_id x on x.entity_id = r.id and x.scheme = 'us.alged.result_set'
                        where x.value = %s""",
                    [contest_key],
                ).fetchone()
                if prior is None:
                    raise ValueError(f"{contest_key}: no ALGED result set")
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded")
            else:
                contest_key = CONTEST_2025
                election = cs.key("us.election", f"{day}:NY:3651000")
                cs.add(
                    "election",
                    id=election,
                    polity_id=city,
                    parent_id=cs.key("us.election", f"{day}:NY"),
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} New York City local election")
                cs.add("slug", path=f"us/ny/new-york-city/{day}-local", entity_id=election)
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=place,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=cs.key("us.electoral_system", "fptp"),
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, f"{day.year} New York City mayoral election")
                cs.add("slug", path=f"us/ny/new-york-city/{day}-local/mayor", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                default = True
                candidates = (
                    rows.filter(pl.col("unit").str.contains(r"\("))
                    .group_by(pl.col("unit").str.extract(r"^(.+) \(").alias("candidate"))
                    .agg(pl.col("votes").sum())
                    .sort("votes", descending=True)
                )
                roots = {}
                for candidate, _ in candidates.iter_rows():
                    option_key = f"{contest_key}:{slug(candidate)}"
                    roots[candidate] = cs.key("us.option", option_key)
                    cs.add(
                        "contest_option",
                        id=roots[candidate],
                        contest_id=contest,
                        kind="candidate",
                        ballot_label=candidate,
                    )
                    if candidate in PERSONS:
                        person = cs.key(*PERSONS[candidate])
                        if person in cs.minted:
                            raise ValueError(f"{candidate} must already exist")
                    else:
                        person = cs.key(f"{SOURCE}.person", slug(candidate))
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, candidate)
                    head = cs.key("us.candidacy", f"{option_key}:head")
                    cs.add(
                        "candidacy",
                        id=head,
                        person_id=person,
                        option_id=roots[candidate],
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    for line in rows.filter(pl.col("unit").str.starts_with(f"{candidate} ("))["unit"].unique().sort():
                        party = parties[line.removeprefix(f"{candidate} (").removesuffix(")")]
                        if party is not None:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=roots[candidate],
                        kind="won" if candidate == candidates["candidate"][0] else "lost",
                    )

            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=1,
            )
            if day.year == 2021:
                totals = (
                    rows.filter(pl.col("combined").is_null(), pl.col("unit").str.contains(r"\("))
                    .group_by(pl.col("unit").str.extract(r"^(.+) \(").alias("candidate"))
                    .agg(pl.col("votes").sum())
                    .sort("votes", descending=True)
                )
                for candidate, _ in totals.iter_rows():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=roots[candidate],
                        kind="won" if candidate == totals["candidate"][0] else "lost",
                    )
            if result_set not in cs.minted:
                continue
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            options: dict[str, UUID] = {}
            for line in rows.filter(pl.col("unit").str.contains(r"\("))["unit"].unique().sort():
                candidate, party = line.removesuffix(")").split(" (", 1)
                options[line] = cs.key(
                    "us.option", f"{contest_key}:{ALGED.get(candidate, slug(candidate))}:{slug(party)}"
                )
                cs.add(
                    "contest_option",
                    id=options[line],
                    contest_id=contest,
                    parent_option_id=roots[candidate],
                    kind="ticket",
                    party_id=parties[party],
                    ballot_label=line,
                )
            options[SCATTERED] = cs.key("us.option", f"{contest_key}:write_in:scattered")
            cs.add("contest_option", id=options[SCATTERED], contest_id=contest, kind="write_in", ballot_label=SCATTERED)
            counted = rows.filter(pl.col("combined").is_null()).with_columns(
                unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()})
            )
            lines = counted.filter(~pl.col("unit").is_in(COUNTERS))
            cs.frame(
                "result_value",
                pl.concat(
                    [
                        lines.select(
                            result_set_id=pl.lit(str(result_set)),
                            unit_id="unit_id",
                            option_id=pl.col("unit").replace_strict(
                                {line: str(option) for line, option in options.items()}
                            ),
                            channel=pl.lit(0, pl.Int16),
                            measure=pl.lit(1, pl.Int16),
                            count_round=pl.lit(0, pl.Int16),
                            flags=pl.lit(0, pl.Int16),
                            value="votes",
                        ),
                        *(
                            frame_.group_by("unit_id")
                            .agg(pl.col("votes").sum())
                            .select(
                                result_set_id=pl.lit(str(result_set)),
                                unit_id="unit_id",
                                option_id=pl.lit(None, pl.String),
                                channel=pl.lit(0, pl.Int16),
                                measure=pl.lit(measure, pl.Int16),
                                count_round=pl.lit(0, pl.Int16),
                                flags=pl.lit(0, pl.Int16),
                                value="votes",
                            )
                            for measure, frame_ in (
                                (3, counted.filter(pl.col("unit").is_in(COUNTERS))),
                                (4, lines),
                            )
                        ),
                    ]
                ),
            )
        return cs.write()
