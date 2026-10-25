import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.ceda_measures"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
TYPES = {
    "Initiative": "initiative",
    "Recall": "recall_question",
    "Advisory": "plebiscite",
    "Policy/Position": "plebiscite",
}
REQUIRED = {"M": "a simple majority", "F": "55 percent", "T": "two-thirds"}
METHOD = (
    "Yes and No votes as compiled by the California Elections Data Archive from the county election offices; for a "
    "measure voted on in more than one county, the votes are the archive's sums across the counties. Passing needed "
    "{required} of the votes cast on the measure, and the outcome is the archive's own Pass or Fail mark."
)


def fetch() -> None:
    for url in ceda.FILES.values():
        raw.fetch(ceda.SOURCE, url, suffix=".xlsx")


def parse() -> None:
    frames = []
    for year, url in ceda.FILES.items():
        frame = pl.read_excel(raw.cached(ceda.SOURCE, url).path, sheet_name=f"Measures {year}", infer_schema_length=0)
        frames.append(frame.rename({column: column.upper() for column in frame.columns}))
    frame = (
        pl.concat(frames, how="diagonal")
        .filter(pl.col("CSD") == "0")
        .sort("MEASID")
        .unique("MULTI_MEASID", keep="first", maintain_order=True)
        .select(
            pl.col("MULTI_MEASID").alias("measure"),
            pl.col("JUR").alias("jur"),
            pl.col("CNTYNAME").str.strip_chars().str.to_uppercase().alias("county"),
            pl.col("PLACE").str.strip_chars().alias("place"),
            pl.when(pl.col("DATE").str.contains(r"^\d+$"))
            .then(pl.date(1899, 12, 30) + pl.duration(days=pl.col("DATE").cast(pl.Int64, strict=False)))
            .otherwise(pl.col("DATE").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False))
            .alias("day"),
            pl.col("LTR").str.strip_chars().alias("letter"),
            pl.col("BALQUEST").str.replace_all(r"\s+", " ").str.strip_chars().alias("question"),
            pl.col("RECTYPENAME").alias("type"),
            pl.col("REQ").alias("required"),
            pl.col("YES_SUM").cast(pl.Float64).cast(pl.Int64).alias("yes"),
            pl.col("NO_SUM").cast(pl.Float64).cast(pl.Int64).alias("no"),
            pl.col("PASSFAIL_SUM").str.starts_with("Pass").alias("passed"),
        )
        .filter(~pl.col("place").str.contains(ceda.SKIPPED.pattern))
    )
    with connect() as conn:
        rows = conn.execute(
            """select a.scheme_code, c.code, n.text,
                 array(select k.code from area b join area_code k on k.area_id = b.id
                       join area_geometry h on h.area_id = b.id
                       where b.scheme_code = 'us.county' and k.code like '06%%' and h.geom && g.geom
                         and st_area(st_intersection(h.geom, g.geom)) > 0.01 * st_area(g.geom))
               from area a
               join area_code c on c.area_id = a.id
               join area_geometry g on g.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '06%%'"""
        ).fetchall()
    counties: dict[str, str] = {}
    cities: dict[str, str] = {}
    schools: dict[str, list[tuple[str | None, str, list[str]]]] = {}
    for scheme, code, text, within in rows:
        if scheme == "us.county":
            counties[re.sub(r" County$", "", text).upper()] = code
        elif scheme == "us.place":
            for part in re.findall(r"[^()]+", text):
                cities[ceda._city(part)] = code
        else:
            schools.setdefault(ceda._core(text), []).append((ceda._kind(text), f"sd{code}", within))
    codes: list[str | None] = []
    for jur, place, county in frame.select("jur", "place", "county").iter_rows():
        fips = counties.get(county)
        found = None
        if jur == "1":
            found = fips if counties.get(place.upper().removesuffix(" COUNTY")) == fips else None
        elif jur == "2":
            found = cities.get(ceda._city(place))
        elif not re.search(r"(?i)college|\bCCD\b", place):
            if re.search(r"(?i)(?:county|co\.) (?:office|board) of education", place):
                found = fips
            else:
                options = [option for option in schools.get(ceda._core(place), []) if fips in option[2]]
                kind = ceda._kind(place)
                typed = [option for option in options if option[0] == kind] if kind else options
                typed = typed or [option for option in options if option[0] is None]
                found = typed[0][1] if len(typed) == 1 else None
        codes.append(found)
    frame = frame.with_columns(code=pl.Series(codes, dtype=pl.String))
    missing = frame.filter(pl.col("code").is_null())
    print(f"kept {frame.height - missing.height} of {frame.height} measures")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("code").is_not_null()).write_parquet(PARSED / "measures.parquet")
    (PARSED / "missing.txt").write_text(
        "\n".join(
            sorted(
                {
                    f"{jur} {county}: {place}"
                    for jur, county, place in missing.select("jur", "county", "place").iter_rows()
                }
            )
        )
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's county, city, and school district ballot measures, 1996–2025",
            polity="US-CA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        named: dict[UUID, tuple[UUID, str]] = {
            entity: (id, text)
            for entity, id, text in conn.execute(
                """select n.entity_id, n.id, n.text from name n join contest c on c.id = n.entity_id
                   where n.is_preferred and n.lang = 'en' and c.kind = 'ballot_question'"""
            ).fetchall()
        }
        source = cs.source(ceda.PUBLISHER, ceda.TITLE)
        if source in cs.minted:
            raise ValueError("us.ceda must be published first")
        for url in ceda.FILES.values():
            cs.artifact(raw.cached(ceda.SOURCE, url), source)
        system = cs.key("us.electoral_system", "referendum")
        state = cs.polity("US-CA")
        polities: dict[str, tuple[UUID, UUID, str, str]] = {}
        for code, polity, area, text, path, kind in conn.execute(
            """select c.code, t.polity_id, a.id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '06%%'"""
        ).fetchall():
            polities[f"sd{code}" if kind == "school_district" else code] = (polity, area, text, path)
        parents = dict(
            conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:CA(:primary)?$'"""
            ).fetchall()
        )
        known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.election' and value like '%%:CA:%%'"
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ca/%%'").fetchall()}
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        official: dict[tuple[UUID, date], list[tuple[UUID, int, int]]] = {}
        for contest, area, day, yes, no in conn.execute(
            """select c.id, c.district_area_id, e.date_from,
                      sum(v.value) filter (where o.ballot_label = 'Yes')::int,
                      sum(v.value) filter (where o.ballot_label = 'No')::int
               from contest c join election e on e.id = c.election_id
               join changeset ch on ch.id = c.changeset_id and ch.pipeline <> %s
               join result_set r on r.contest_id = c.id and r.is_default and r.status = 'active'
               join result_value v on v.result_set_k = r.k
               join contest_option o on o.k = v.option_k
               where c.kind = 'ballot_question' and e.date_from = any(%s) and e.polity_id = any(%s)
               group by 1, 2, 3""",
            [SOURCE, sorted(set(frame["day"])), [polity for polity, *_ in polities.values()]],
        ).fetchall():
            official.setdefault((area, day), []).append((contest, yes or 0, no or 0))
        elections: set[UUID] = set()
        units: set[UUID] = set()
        for measure, code, day, letter, question, kind, required, yes, no, passed in (
            frame.sort("day", "measure")
            .select("measure", "code", "day", "letter", "question", "type", "required", "yes", "no", "passed")
            .iter_rows()
        ):
            polity, area, place, path = polities[code]
            if (
                survivor := next(
                    (
                        held_contest
                        for held_contest, held_yes, held_no in official.get((area, day), [])
                        if abs(held_yes - yes) <= max(2, yes * 0.005) and abs(held_no - no) <= max(2, no * 0.005)
                    ),
                    None,
                )
            ) is not None:
                if (own := cs.key("us.contest", f"ceda:measure:{measure}")) not in cs.minted:
                    cs.merge(own, survivor)
                continue
            title = place.removeprefix("City of ")
            geoid = code.removeprefix("sd")
            scheme = {5: "us.county", 7: "us.place"}.get(len(code), "us.school_district")
            unit = cs.unit(scheme, geoid)
            if unit in cs.minted and unit not in units:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=geoid,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            units.add(unit)
            parent_key = next((value for value in (f"{day}:CA", f"{day}:CA:primary") if value in parents), None)
            stage = "local" if parent_key is None else parents[parent_key]
            parent = cs.key("us.election", parent_key or f"{day}:CA")
            if parent_key is None and parent in cs.minted and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} California local elections")
                cs.add("slug", path=f"us/ca/{day}-local", entity_id=parent)
            election_key = f"{day}:CA:{code}"
            election = cs.key("us.election", election_key)
            if election_key not in known and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {title} {stage} election")
                cs.add("slug", path=f"{path}/{day}-{stage}", entity_id=election)
            contest_key = f"ceda:measure:{measure}"
            contest = cs.key("us.contest", contest_key)
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
            cs.add("ballot_question", contest_id=contest, type=TYPES.get(kind, "mandatory_referendum"))
            letter = " ".join(dict.fromkeys(letter.split())) if letter else letter
            text = f"{day.year} {title} Measure {letter}" if letter else f"{day.year} {title} ballot measure"
            if (current := named.get(contest)) is not None and current[1] != text:
                cs.add("name", id=current[0], entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
            else:
                name(contest, text)
            if contest in cs.minted:
                contest_path = held.get(contest)
                if contest_path is None:
                    base = f"{path}/{day}-{stage}/measure{f'-{slug(letter)}' if letter else ''}"
                    contest_path, n = base, 1
                    while contest_path in taken:
                        n += 1
                        contest_path = f"{base}-{n}"
                    taken.add(contest_path)
                cs.add("slug", path=contest_path, entity_id=contest)
            if question:
                cs.add("note", entity_id=contest, kind="context", body_md=question)
            options = {}
            for position, answer in enumerate(("Yes", "No")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(required=REQUIRED[required]))
            if result_set in cs.minted:
                for answer, votes in (("Yes", yes), ("No", no)):
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
            cs.add("outcome", result_set_id=result_set, option_id=options["Yes" if passed else "No"], kind="won")
        return cs.write()
