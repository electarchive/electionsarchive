import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.sources.us.elstats_local import STAGES
from ea.store import DATA, connect

SOURCE = "us.elstats_local_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIRST, LAST = 1990, 2025
TENANTS = ("VA", "NM", "CT", "ID", "CO", "NH", "VT")
ANSWERS = {
    "YES": "YES",
    "NO": "NO",
    "IN FAVOR": "YES",
    "AGAINST": "NO",
    "YES/FOR": "YES",
    "NO/AGAINST": "NO",
    "FOR": "YES",
}
COUNTED = {"TOTAL VOTES CAST", "TOTAL BALLOTS CAST", "UNDERVOTES", "OVERVOTES", "REGISTERED VOTERS", "BLANK VOTES"}
JUDICIAL = r"Court|Judicial|Judge|District Attorney|State Senate"
MUNICIPAL = {"City/Town", "City", "Town", "Village"}
METHOD = (
    "Yes and No votes are the {unit} rows of the {publisher}'s election archive, summed across its vote channels. "
    "The outcome is the archive's own mark."
)
PARTIAL = (
    "The question went to the voters of {district}, which the archive has no boundary for, so the results are "
    "shown for the precincts that voted, as a whole."
)


def url(state: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    for state in TENANTS:
        for year in range(FIRST, LAST + 1):
            raw.fetch(SOURCE, url(state, year), suffix=".csv")


def parse() -> None:
    frames = []
    for state in TENANTS:
        for year in range(FIRST, LAST + 1):
            frame = pl.read_csv(
                raw.cached(SOURCE, url(state, year)).path, infer_schema=False, truncate_ragged_lines=True
            )
            if "question_text" not in frame.columns or frame.height == 0:
                continue
            frames.append(
                frame.filter(
                    pl.col("district_type") != "State",
                    ~pl.col("district_type").str.contains(JUDICIAL),
                    pl.col("retention_candidate_id").is_null(),
                    pl.col("question_text").is_not_null(),
                    pl.col("election_type").is_in(list(STAGES)),
                ).select(
                    pl.lit(state).alias("state"),
                    pl.col("contest_id").alias("contest"),
                    pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                    pl.col("election_type").replace_strict(STAGES).alias("stage"),
                    pl.col("question_text").str.replace_all(r"\s+", " ").str.strip_chars().alias("title"),
                    pl.col("question_type").alias("type"),
                    pl.col("district_type").alias("kind"),
                    pl.col("district_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("district"),
                    pl.col("candidate_name").str.to_uppercase().str.strip_chars().alias("answer"),
                    pl.col("division_type").alias("level"),
                    "division_id",
                    pl.col("division_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("division"),
                    pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).alias("votes"),
                    (pl.col("is_winner") == "true").alias("winner"),
                )
            )
    frame = pl.concat(frames).filter(~pl.col("answer").is_in(list(COUNTED)))
    frame = frame.filter(
        pl.col("answer").is_in(list(ANSWERS)).all().over("state", "contest"),
        pl.col("answer").replace_strict(ANSWERS, default=None).n_unique().over("state", "contest") == 2,
    ).with_columns(pl.col("answer").replace_strict(ANSWERS))
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        jurisdictions: dict[str, str] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515'"""
        ).fetchall():
            city = code[:2] == "51" and int(code[2:]) >= 500
            counties.setdefault(iso[3:], {})[elstats.plain(text) + ("city" if city else "county")] = code
            jurisdictions[code] = text.upper() + (" CITY" if city else "")
        places: dict[str, dict[str, tuple[str, str]]] = {}
        for scheme, code, text, iso in conn.execute(
            """select u.scheme_code, u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.polity_id and n.is_preferred and n.lang = 'en'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code in ('us.place', 'us.town', 'us.school_district')
                 and left(u.code, 2) in ('51', '35', '09', '16')
               order by u.scheme_code"""
        ).fetchall():
            text = re.sub(r"^(?:City|Town|Village) of ", "", text)
            if scheme == "us.school_district":
                text = re.sub(
                    r" (?:Public|Municipal|Consolidated|Independent)?\s*(?:School District|Schools)$", "", text
                )
            places.setdefault(iso[3:], {}).setdefault(f"{scheme}:{elstats.plain(text)}", (scheme, code))
    labels = []
    for state, contest, kind, district in frame.select("state", "contest", "kind", "district").unique().iter_rows():
        plain = elstats.plain(re.sub(r"\s*\([^)]*\)", "", district))
        found = None
        if kind in ("County", "Locality"):
            code = next(
                (code for key in (plain, f"{plain}county") if (code := counties[state].get(key)) is not None), None
            )
            found = None if code is None else ("us.county", code)
        elif kind in MUNICIPAL:
            found = places.get(state, {}).get(f"us.place:{plain}") or places.get(state, {}).get(f"us.town:{plain}")
        elif "School" in kind:
            found = places.get(state, {}).get(
                "us.school_district:"
                + elstats.plain(
                    re.sub(
                        r" (?:Public|Municipal|Consolidated|Independent)?\s*(?:School District|Schools)$", "", district
                    )
                )
            )
        labels.append(
            {
                "state": state,
                "contest": contest,
                "scheme": None if found is None else found[0],
                "code": None if found is None else found[1],
            }
        )
    frame = frame.join(
        pl.DataFrame(labels, schema={"state": pl.String, "contest": pl.String, "scheme": pl.String, "code": pl.String}),
        on=["state", "contest"],
    )
    named = frame.filter(pl.col("level").is_in(["County", "Locality"])).select(
        "state",
        "contest",
        pl.col("division").map_elements(elstats.plain, return_dtype=pl.String).alias("plain"),
        "votes",
    )
    placed = []
    for state, contest, plain, votes in named.iter_rows():
        code = next((code for key in (plain, f"{plain}county") if (code := counties[state].get(key)) is not None), None)
        placed.append((state, contest, code, votes))
    homes = (
        pl.DataFrame(
            placed,
            schema={"state": pl.String, "contest": pl.String, "county": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .filter(pl.col("county").is_not_null())
        .group_by("state", "contest", "county")
        .agg(pl.col("votes").sum())
    )
    spread = homes.group_by("state", "contest").agg(pl.col("county").n_unique().alias("counties"))
    home = homes.sort("votes", descending=True).group_by("state", "contest").agg(pl.col("county").first().alias("home"))
    frame = (
        frame.join(home, on=["state", "contest"], how="left")
        .join(spread, on=["state", "contest"], how="left")
        .with_columns(
            home=pl.coalesce(
                pl.when(pl.col("scheme") == "us.county").then("code"),
                "home",
                pl.when(pl.col("scheme") == "us.town").then(pl.col("code").str.slice(0, 5)),
            )
        )
        .filter(pl.col("home").is_not_null())
    )
    single = frame.filter((pl.col("level") == "Precinct") & (pl.col("counties").fill_null(1) == 1)).with_columns(
        county="home"
    )
    frame = frame.with_columns(
        top=pl.col("level").is_in(["County", "Locality", "City/Town"]).any().over("state", "contest")
    )
    totals = (
        frame.filter(pl.col("level").is_in(["County", "Locality", "City/Town"]) | ~pl.col("top"))
        .group_by("state", "contest", "answer")
        .agg(pl.col("votes").sum().alias("total"), pl.col("winner").all().alias("won"))
    )
    precincts = (
        single.filter(pl.col("state").is_in(["VA", "NM", "ID"]))
        .with_columns(
            unit=pl.concat_str(
                pl.col("day").dt.year().cast(pl.String),
                "state",
                "county",
                pl.col("county").replace_strict(jurisdictions),
                pl.col("division").str.to_uppercase(),
                separator=":",
            )
        )
        .group_by("state", "contest", "county", "unit", "answer")
        .agg(pl.col("votes").sum())
    )
    matched = (
        precincts.group_by("state", "contest", "answer")
        .agg(pl.col("votes").sum())
        .join(totals, on=["state", "contest", "answer"])
        .group_by("state", "contest")
        .agg((pl.col("votes") == pl.col("total")).all().alias("matched"))
        .filter("matched")
        .select("state", "contest")
    )
    print(
        f"{matched.height} of {totals.select('state', 'contest').unique().height} questions have precincts that sum to the totals"
    )
    contests = (
        frame.select("state", "contest", "day", "stage", "title", "type", "kind", "district", "scheme", "code", "home")
        .unique(["state", "contest"])
        .join(totals, on=["state", "contest"])
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    contests.write_parquet(PARSED / "questions.parquet")
    precincts.join(matched, on=["state", "contest"]).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    questions = pl.read_parquet(PARSED / "questions.parquet")
    grouped = {keys: rows for keys, rows in pl.read_parquet(PARSED / "precincts.parquet").group_by("state", "contest")}
    with connect() as conn:
        polities: dict[str, tuple[UUID, str, str, UUID]] = {}
        for scheme, code, polity, text, path, area in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, n.text, s.path, a.id from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) in ('51', '35', '09', '16')"""
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text.removeprefix("City of "), path, area)
        states = {
            iso[3:]: (polity, text)
            for iso, polity, text in conn.execute(
                """select p.iso_code, p.id, n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        }
        precincts: dict[str, UUID] = dict(
            conn.execute(
                """select code, id from reporting_unit where scheme_code = 'us.precinct'
                   and split_part(code, ':', 2) in ('VA', 'ID', 'NM')"""
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select code, id, area_id from reporting_unit where scheme_code = 'us.county'
                   and left(code, 2) in ('51', '16', '35', '09')"""
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
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County, city, town, school, and special district ballot questions from the archives of Virginia, New Mexico, Idaho, and Connecticut",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("us.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("the referendum system must already exist")
        sources = {state: cs.source(elstats.TENANTS[state][1], elstats.TITLE) for state in TENANTS}
        stored: set[str] = set()
        for state in sorted(set(questions["state"])):
            for year in range(FIRST, LAST + 1):
                if (artifact := raw.cached(SOURCE, url(state, year))).storage_key not in stored:
                    stored.add(artifact.storage_key)
                    cs.artifact(artifact, sources[state])
        elections: set[UUID] = set()
        districts: set[str] = set()
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        for row in questions.unique(["state", "contest"]).sort("state", "day", "contest").iter_rows(named=True):
            state, day, home = row["state"], row["day"], row["home"]
            local = f"{row['scheme']}:{row['code']}" if row["code"] is not None else None
            owner = local if local in polities else f"us.county:{home}"
            if owner not in polities:
                continue
            polity, place, path, whole = polities[owner]
            stage = row["stage"]
            primary = stage == "primary"
            kind = "primary" if primary else "general" if day.month == 11 and stage == "general" else "local"
            parent = cs.key("us.election", f"{day}:{state}" + (":primary" if primary else ""))
            if parent in cs.minted and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=states[state][0],
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} {states[state][1]} " + ("primary" if primary else f"{kind} elections"))
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=parent)
            code = owner.split(":", 1)[1]
            election = cs.key("us.election", f"{day}:{state}:{code}" + (":primary" if primary else ""))
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind} election")
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            title = row["title"]
            heading = title if len(title) <= 120 else title[:117].rsplit(" ", 1)[0] + "..."
            contest_key = f"{state}:local:question:{row['contest']}"
            contest = cs.key("us.contest", contest_key)
            area, unit = whole, cs.unit(owner.split(":", 1)[0], code)
            partial = owner != local
            if partial:
                district = f"{home[:2]}:{home}:question:{row['contest']}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    label = row["district"] if row["kind"] in row["district"] else f"{row['district']} {row['kind']}"
                    name(area, label)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            text = f"{row['type'] or ''} {title}".lower()
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative"
                if "initiat" in text or "petition" in text
                else "plebiscite"
                if "advisory" in text or "straw" in text
                else "mandatory_referendum",
            )
            name(contest, f"{day.year} {place}: {heading}")
            target = held.get(contest)
            if target is None:
                base = f"{path}/{day}-{kind}/{slug(heading)[:60].rstrip('-') or 'question'}"
                target, n = base, 1
                while target in taken:
                    n += 1
                    target = f"{base}-{n}"
                taken.add(target)
            cs.add("slug", path=target, entity_id=contest)
            if partial:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=PARTIAL.format(
                        district=row["district"]
                        if row["kind"] in row["district"]
                        else f"{row['district']} {row['kind']}"
                    ),
                )
            options = {}
            for position, answer in enumerate(("YES", "NO")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            counted = grouped.get((state, row["contest"]))
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
            if result_set not in cs.minted:
                continue
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.format(
                    unit="precinct" if counted is not None else "county, city, or town",
                    publisher=elstats.TENANTS[state][1],
                ),
            )
            winners = questions.filter(pl.col("state") == state, pl.col("contest") == row["contest"], pl.col("won"))[
                "answer"
            ].to_list()
            if len(winners) == 1:
                cs.add("outcome", result_set_id=result_set, option_id=options[winners[0]], kind="won")
            if counted is None:
                for answer, total in (
                    questions.filter(pl.col("state") == state, pl.col("contest") == row["contest"])
                    .select("answer", "total")
                    .iter_rows()
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[answer],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=total,
                    )
                continue
            for county, place_code, answer, votes in counted.select("county", "unit", "answer", "votes").iter_rows():
                target_unit = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                if place_code not in precincts:
                    minted.setdefault(place_code, (county, day))
                if area != counties[county][1]:
                    memberships.add((target_unit, area, day))
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=target_unit,
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for place_code, (county, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=states[place_code.split(":")[1]][0],
                scheme_code="us.precinct",
                code=place_code,
                parent_id=counties[county][0],
                area_relation="within",
                area_id=counties[county][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target_unit, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target_unit,
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        return cs.write()
