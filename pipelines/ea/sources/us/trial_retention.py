import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats, elstats_local_measures
from ea.sources.us.retention import display
from ea.store import DATA, connect

SOURCE = "us.trial_retention"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = elstats_local_measures.SOURCE
COURTS = {
    ("CO", "District Court"): ("district", "Judge of the District Court"),
    ("CO", "County Court"): ("county", "Judge of the County Court"),
    ("CO", "Juvenile Court"): ("county", "Judge of the Juvenile Court"),
    ("ID", "Magistrate Judge"): ("county", "Magistrate Judge"),
    ("NM", "District Judge"): ("district", "District Judge"),
    ("NM", "Metropolitan Court Judge"): ("county", "Judge of the Metropolitan Court"),
}
ANSWERS = {"YES": "yes", "NO": "no", "YES/FOR": "yes", "NO/AGAINST": "no"}
THRESHOLDS = {"NM": 0.57}
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when Yes reaches {rule} of the Yes and No votes. Votes by county are the {publisher}'s "
    "election archive rows, or the sums of its precinct rows where it has no county row, summed across its vote "
    "channels."
)


def fetch() -> None:
    elstats_local_measures.fetch()


def parse() -> None:
    frames = []
    for state in ("CO", "ID", "NM"):
        for year in range(elstats_local_measures.FIRST, elstats_local_measures.LAST + 1):
            frame = pl.read_csv(
                raw.cached(ARCHIVE, elstats_local_measures.url(state, year)).path,
                infer_schema=False,
                truncate_ragged_lines=True,
            )
            if "retention_candidate_id" not in frame.columns or frame.height == 0:
                continue
            frames.append(
                frame.filter(
                    pl.col("retention_candidate_id").is_not_null(),
                    pl.col("office_name").is_in([office for (where, office) in COURTS if where == state]),
                    pl.col("candidate_name").str.to_uppercase().is_in(list(ANSWERS)),
                    pl.col("division_type").is_in(["County", "Precinct"]),
                ).select(
                    pl.lit(state).alias("state"),
                    pl.col("contest_id").alias("contest"),
                    pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                    pl.col("election_type").alias("stage"),
                    pl.col("office_name").alias("office"),
                    pl.col("district_name").str.strip_chars().alias("district"),
                    pl.col("retention_candidate_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("judge"),
                    pl.col("candidate_name").str.to_uppercase().replace_strict(ANSWERS).alias("answer"),
                    pl.col("division_type").alias("level"),
                    pl.col("division_name").str.strip_chars().alias("division"),
                    pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).alias("votes"),
                )
            )
    frame = pl.concat(frames)
    with connect() as conn:
        counties = {
            (iso[3:], elstats.plain(text)): code
            for code, text, iso in conn.execute(
                """select u.code, n.text, p.iso_code from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
                   join polity p on p.id = s.polity_id
                   where u.scheme_code = 'us.county' and p.iso_code in ('US-CO', 'US-ID', 'US-NM')"""
            ).fetchall()
        }
    places = []
    for state, level, division, district in frame.select("state", "level", "division", "district").unique().iter_rows():
        place = division if level == "County" else re.sub(r"\s*County$", "", district or "")
        places.append((state, level, division, district, counties.get((state, elstats.plain(place or "")))))
    frame = frame.join(
        pl.DataFrame(
            places,
            schema={
                "state": pl.String,
                "level": pl.String,
                "division": pl.String,
                "district": pl.String,
                "county": pl.String,
            },
            orient="row",
        ),
        on=["state", "level", "division", "district"],
        nulls_equal=True,
    ).with_columns(
        top=(pl.col("level") == "County").any().over("state", "contest"),
        seat=pl.col("office").replace_strict({office: kind for (_, office), (kind, _) in COURTS.items()}),
    )
    kept = frame.filter((pl.col("level") == "County") | ~pl.col("top"))
    missing = kept.filter(pl.col("county").is_null()).select("state", "contest", "district", "division").unique()
    if missing.height:
        print(f"no county for {missing.height} rows: {missing.head(10).rows()}")
    kept = kept.filter(pl.col("county").is_not_null().all().over("state", "contest"))
    kept = kept.filter(pl.col("contest") == pl.col("contest").min().over("state", "day", "office", "district", "judge"))
    PARSED.mkdir(parents=True, exist_ok=True)
    kept.group_by("state", "contest", "day", "stage", "office", "seat", "district", "judge", "county", "answer").agg(
        pl.col("votes").sum()
    ).write_parquet(PARSED / "retentions.parquet")


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "retentions.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Retention elections for Colorado's district, county, and juvenile court judges, Idaho's magistrate "
            "judges, and New Mexico's district and metropolitan court judges, by county, from the state election archives",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        states = {
            iso[3:]: (polity, text, code, unit, area)
            for iso, polity, text, code, unit, area in conn.execute(
                """select p.iso_code, p.id, n.text, u.code, u.id, u.area_id from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code in ('US-CO', 'US-ID', 'US-NM')"""
            ).fetchall()
        }
        counties = {
            code: (polity, text, unit, area)
            for code, polity, text, unit, area in conn.execute(
                """select u.code, t.polity_id, n.text, u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.county' and left(u.code, 2) in ('08', '16', '35')"""
            ).fetchall()
        }
        elections = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.election' and value ~ '^[0-9-]+:(CO|ID|NM)$'"
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        systems = {
            state: cs.key("us.electoral_system", "retention-57" if state in THRESHOLDS else "retention")
            for state in states
        }
        if cs.minted & set(systems.values()):
            raise ValueError("the retention systems must already exist")
        sources = {state: cs.source(elstats.TENANTS[state][1], elstats.TITLE) for state in states}
        stored: set[str] = set()
        for state in states:
            for year in range(elstats_local_measures.FIRST, elstats_local_measures.LAST + 1):
                artifact = raw.cached(ARCHIVE, elstats_local_measures.url(state, year))
                if artifact.storage_key not in stored:
                    stored.add(artifact.storage_key)
                    cs.artifact(artifact, sources[state])
        districts: dict[str, UUID] = {}
        offices: set[UUID] = set()
        persons: set[UUID] = set()
        memberships: set[tuple[UUID, UUID, object]] = set()
        for (state, contest_id), lines in rows.group_by("state", "contest", maintain_order=True):
            first = lines.row(0, named=True)
            day, office_name, seat, district = first["day"], first["office"], first["seat"], first["district"]
            election_key = f"{day}:{state}"
            if election_key not in elections:
                print(f"{election_key}: no election, skipped {first['judge']}")
                continue
            _, title = COURTS[(state, office_name)]
            if seat == "district" and not re.match(r"\d", district):
                seat, title = (
                    "county",
                    "Judge of the Probate Court" if "Probate" in district else "Judge of the Juvenile Court",
                )
                if lines["county"].n_unique() != 1:
                    raise ValueError(f"{contest_id}: {district} spans {lines['county'].unique().to_list()}")
            if seat == "district":
                number = int(re.match(r"\d+", district)[0])
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(number if number < 20 else number % 10, "th")
                label = f"{number}{suffix} Judicial District"
                code = f"{state}:{number}"
                area = cs.area("us.judicial_district", code)
                if code not in districts:
                    districts[code] = area
                    polity = cs.key("us.polity", f"judicial:{code}")
                    if polity in cs.minted:
                        cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                        name(polity, f"{states[state][1]} {label}")
                        cs.add("polity_parent", polity_id=polity, parent_id=states[state][0])
                        path = f"us/{state.lower()}/{slug(label)}"
                        if path in taken:
                            raise ValueError(f"{label} would take {path}")
                        taken.add(path)
                        cs.add("slug", path=path, entity_id=polity)
                        cs.add("area", id=area, scheme_code="us.judicial_district")
                        cs.add("area_code", area_id=area, code=code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=states[state][4])
                        name(area, f"{label}, {state}")
                        cs.add("polity_territory", polity_id=polity, area_id=area)
                        cs.add(
                            "reporting_unit",
                            id=cs.unit("us.judicial_district", code),
                            polity_id=polity,
                            scheme_code="us.judicial_district",
                            code=code,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                            valid_from=None,
                        )
                owner_polity, owner_label = cs.key("us.polity", f"judicial:{code}"), label
                office_key = f"{slug(title)}:{code}"
            else:
                home = first["county"]
                owner_polity, county_name, _, area = counties[home]
                owner_label = county_name
                code = home
                office_key = f"{slug(title)}:{state}:{home}"
            office = cs.key("us.office", office_key)
            if office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=owner_polity, kind="other")
                if office in cs.minted:
                    name(office, f"{title}, {owner_label}")
            judge = display(first["judge"])
            person_key = f"{state}:{slug(judge)}"
            person = cs.key(f"{SOURCE}.person", person_key)
            if person not in persons:
                persons.add(person)
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, judge)
            contest_key = f"{day}:{state}:{slug(title)}:{slug(code)}:{slug(judge)}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", election_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=systems[state],
                default_result_kind="counted",
            )
            name(contest, f"{day.year} {title}, {owner_label}, retention: {judge}")
            path = held.get(contest)
            if path is None:
                base = f"us/{state.lower()}/{day}-general/{slug(title)}-{slug(owner_label)}-{slug(judge)}"
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            options = {}
            for position, answer in enumerate(("yes", "no")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options["yes"],
                role="head",
                ballot_name=judge,
                is_independent=False,
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            share = THRESHOLDS.get(state)
            tallies = dict(lines.group_by("answer").agg(pl.col("votes").sum()).iter_rows())
            yes, no = tallies.get("yes", 0), tallies.get("no", 0)
            retained = yes >= share * (yes + no) if share is not None else yes > no
            if result_set not in cs.minted:
                continue
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.format(
                    rule="57 percent" if share is not None else "a simple majority",
                    publisher=elstats.TENANTS[state][1],
                ),
            )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
            for county, answer, votes in lines.select("county", "answer", "votes").iter_rows():
                unit = counties[county][2]
                if area != counties[county][3]:
                    memberships.add((unit, area, day))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for unit, area, day in memberships:
            cs.add("reporting_unit_area", unit_id=unit, area_id=area, valid_from=day, valid_to=day + timedelta(days=1))
        return cs.write()
