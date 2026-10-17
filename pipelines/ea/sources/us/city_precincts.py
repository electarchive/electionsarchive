import re
import zipfile
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats, elstats_local, medsl, medsl_local, nc_local, ncsbe
from ea.store import DATA, connect

SOURCE = "us.city_precincts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
OFFICE = r"MAYOR|COUNCIL|ALDER|WARD|COMMISSION|SELECT|CITY|TOWN|VILLAGE|BOROUGH|TRUSTEE"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "junior", "senior"}
METHOD = (
    "Precinct returns for city races from the North Carolina State Board of Elections', the MIT Election Data and "
    "Science Lab's, or OpenElections' precinct files, matched to the city's official contest by candidate and checked against its official totals. Absentee, early, and provisional "
    "ballots that a county reported only countywide stay in their own countywide rows."
)


def fetch() -> None:
    medsl_local.fetch()


def parse() -> None:
    columns = {
        "state_po": "state",
        "county_fips": "fips",
        "jurisdiction_name": "jurisdiction",
        "precinct": "precinct",
        "office": "office",
        "district": "district",
        "candidate": "candidate",
        "writein": "writein",
        "mode": "mode",
        "votes": "votes",
    }
    scans: list[tuple[date, pl.LazyFrame]] = [
        (
            date(2016, 11, 8),
            pl.scan_csv(
                raw.cached(medsl_local.SOURCE, medsl_local.LOCAL2016).path,
                infer_schema=False,
                encoding="utf8-lossy",
                null_values=["", "NA"],
            ).rename({"state_postal": "state_po", "jurisdiction": "jurisdiction_name"}),
        ),
        (
            date(2018, 11, 6),
            pl.scan_csv(raw.cached(medsl_local.SOURCE, medsl_local.LOCAL2018).path, infer_schema=False),
        ),
    ]
    for listing, local, day in (
        (medsl_local.LISTING, False, date(2024, 11, 5)),
        (medsl_local.LISTING2022, True, date(2022, 11, 8)),
        (medsl_local.LISTING2020, False, date(2020, 11, 3)),
    ):
        for state, url in sorted(medsl_local._files(listing, local).items()):
            if listing == medsl_local.LISTING2020 and state != "OR":
                continue
            path = raw.cached(medsl_local.SOURCE, url).path
            scan = (
                pl.read_csv(zipfile.ZipFile(path).read(zipfile.ZipFile(path).namelist()[0]), infer_schema=False).lazy()
                if path.suffix == ".zip"
                else pl.scan_csv(path, infer_schema=False)
            )
            scans.append(
                (
                    day,
                    scan.filter(
                        pl.col("dataverse").is_null() if local else pl.col("dataverse") == "LOCAL",
                        pl.col("stage").is_in(["GEN", "GEN RECOUNT"]),
                    ),
                )
            )
    with connect() as conn:
        counties = {
            (
                code[:2],
                re.sub(r"[^A-Z]", "", re.sub(r" (?:COUNTY|PARISH)$", "", text.upper()).replace("SAINT", "ST")),
            ): code
            for code, text in conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        jurisdictions = {
            code: text.upper() + (" CITY" if code[:2] == "51" and int(code[2:]) >= 500 else "")
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code <> '51515'"""
            ).fetchall()
        }
        towns = dict(
            conn.execute(
                """select distinct split_part(code, ':', 4), split_part(code, ':', 3) from reporting_unit
                   where scheme_code = 'us.precinct' and code like '20__:CT:%'"""
            ).fetchall()
        )
    origins = [(day, "medsl", scan.select(list(columns)).rename(columns)) for day, scan in scans]
    stats = []
    for state in ("VA", "ID", "NM", "CT"):
        for year in range(elstats_local.STATES[state][0], 2026):
            frame = pl.read_csv(
                raw.cached(elstats_local.SOURCE, elstats_local.url(state, year)).path,
                infer_schema=False,
                truncate_ragged_lines=True,
            )
            if frame.height:
                stats.append(
                    frame.filter(
                        pl.col("division_type").is_in(["Precinct", "Polling Place"])
                        & ~pl.col("candidate_name").str.contains(
                            r"(?i)^total |^unknown candidate$|^(?:under|over) ?votes?$|^blank"
                        )
                    ).select(
                        state=pl.lit(state),
                        contest="contest_id",
                        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
                        office="office_name",
                        kind="district_type",
                        town=pl.col("district_name").str.replace(r" - .*$", "").str.to_uppercase(),
                        candidate="candidate_name",
                        division="division_id",
                        precinct=pl.col("division_name")
                        .str.replace_all(r"\s+", " ")
                        .str.strip_chars()
                        .str.to_uppercase(),
                        votes="votes",
                    )
                )
    stats = pl.concat(stats)
    homes = (
        stats.join(
            pl.read_parquet(elstats_local.PARSED / "candidates.parquet")
            .select("state", "contest", "code")
            .unique()
            .filter(pl.col("code").str.len_chars() == 5),
            on=["state", "contest"],
        )
        .group_by("state", "division")
        .agg(pl.col("code").unique())
        .filter(pl.col("code").list.len() == 1)
        .select("state", "division", home=pl.col("code").list.first())
    )
    stats = (
        stats.filter(pl.col("kind").is_in(list(elstats_local.MUNICIPAL)))
        .join(homes, on=["state", "division"], how="left")
        .with_columns(
            fips=pl.when(pl.col("state") == "CT")
            .then(pl.col("town").replace_strict(towns, default=None))
            .otherwise("home")
        )
        .with_columns(
            jurisdiction=pl.when(pl.col("state") == "CT")
            .then("town")
            .otherwise(pl.col("fips").replace_strict(jurisdictions, default=None))
        )
    )
    for (day,), part in stats.group_by("day"):
        origins.append(
            (
                day,
                "elstats",
                part.lazy().select(
                    "state",
                    "fips",
                    "jurisdiction",
                    "precinct",
                    "office",
                    district="contest",
                    candidate="candidate",
                    writein=pl.when(pl.col("candidate").str.contains(r"(?i)^write"))
                    .then(pl.lit("TRUE"))
                    .otherwise(pl.lit("FALSE")),
                    mode=pl.lit("TOTAL"),
                    votes="votes",
                ),
            )
        )
    for (state, day), commit in medsl_local.OPENELECTIONS_FILES.items():
        names = {
            name: code
            for (prefix, name), code in counties.items()
            if prefix
            == {"FL": "12", "MN": "27", "NV": "32", "SD": "46", "TX": "48", "UT": "49", "WV": "54", "IN": "18"}[state]
        }
        county = pl.col("county").str.to_uppercase().str.replace(r" (?:COUNTY|PARISH)$", "")
        origins.append(
            (
                day,
                "openelections",
                pl.scan_csv(
                    raw.cached(
                        medsl_local.SOURCE,
                        medsl_local.OPENELECTIONS.format(state=state.lower(), commit=commit, day=day),
                    ).path,
                    infer_schema=False,
                    encoding="utf8-lossy",
                )
                .rename(lambda column: column.lstrip("\ufeff"))
                .filter(
                    ~pl.col("candidate").str.contains(
                        r"(?i)^(?:over|under) ?votes?$|^(?:rejected|unassigned) write|^total"
                    )
                )
                .select(
                    state=pl.lit(state),
                    fips=county.str.replace("SAINT", "ST")
                    .str.replace_all(r"[^A-Z]", "")
                    .replace_strict(names, default=None),
                    jurisdiction=county,
                    precinct="precinct",
                    office="office",
                    district=pl.when(~pl.col("district").is_in(["NA", ""])).then("district"),
                    candidate="candidate",
                    writein=pl.when(pl.col("candidate").str.contains(r"(?i)write[- ]?in"))
                    .then(pl.lit("TRUE"))
                    .otherwise(pl.lit("FALSE")),
                    mode=pl.lit("TOTAL"),
                    votes="votes",
                ),
            )
        )
    for (day,), part in (
        pl.read_parquet(nc_local.PARSED / "results.parquet")
        .filter(pl.col("code").str.contains(r"^\d{7}$") & pl.col("unit").is_not_null())
        .group_by("day")
    ):
        unit = pl.col("unit").str.splitn(":", 5)
        origins.append(
            (
                day,
                "ncsbe",
                part.lazy().select(
                    state=pl.lit("NC"),
                    fips=unit.struct.field("field_2"),
                    jurisdiction=unit.struct.field("field_3"),
                    precinct=unit.struct.field("field_4"),
                    office="contest",
                    district=pl.lit(""),
                    candidate="choice",
                    writein=pl.when(pl.col("choice").str.contains(r"(?i)write-in"))
                    .then(pl.lit("TRUE"))
                    .otherwise(pl.lit("FALSE")),
                    mode=pl.lit("TOTAL"),
                    votes=pl.col("votes").cast(pl.String),
                ),
            )
        )
    frames = []
    for day, origin, scan in origins:
        fips = pl.col("fips").str.replace(r"\.0$", "")
        frames.append(
            scan.filter(pl.col("office").str.to_uppercase().str.contains(OFFICE))
            .with_columns(pl.col("precinct", "mode", "jurisdiction", "district").fill_null(""))
            .filter(
                ~pl.col("precinct").str.to_uppercase().str.contains(r"TOTALS$|^COUNTY TOTAL$|- TOTAL$")
                & ~((pl.col("precinct").str.to_uppercase() == "TOTAL") & (pl.col("mode").str.to_uppercase() == "TOTAL"))
            )
            .select(
                day=pl.lit(day),
                origin=pl.lit(origin),
                state="state",
                fips=pl.when(fips.str.contains(r"^\d{4,5}$")).then(fips.str.zfill(5).replace(medsl.RECODED)),
                jurisdiction=pl.col("jurisdiction").str.to_uppercase().str.strip_chars(),
                precinct=pl.col("precinct").str.strip_chars(),
                office=pl.col("office").str.to_uppercase().str.strip_chars(),
                district=pl.col("district").str.to_uppercase().str.strip_chars(),
                candidate=pl.col("candidate")
                .fill_null("")
                .str.to_uppercase()
                .str.strip_chars()
                .str.strip_prefix("WRITE-IN: "),
                writein=pl.col("writein").fill_null("").str.to_uppercase() == "TRUE",
                mode=pl.col("mode").str.to_uppercase(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
            .filter(pl.col("votes").is_not_null() & pl.col("fips").is_not_null() & (pl.col("precinct") != ""))
            .collect()
        )
    precinct = ["day", "origin", "state", "fips", "jurisdiction", "precinct", "office", "district"]
    frame = (
        pl.concat(frames)
        .with_columns(rank=pl.col("origin").replace_strict({"ncsbe": 0, "elstats": 0, "medsl": 1, "openelections": 2}))
        .filter(pl.col("rank") == pl.col("rank").min().over("day", "state"))
        .drop("rank")
        .with_columns(measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16))
        .filter(pl.col("measure") > 0)
        .filter((pl.col("mode") == "TOTAL").any().over([*precinct, "candidate"]).not_() | (pl.col("mode") == "TOTAL"))
        .group_by([*precinct, "candidate", "measure"])
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            code=pl.concat_str(
                pl.col("day").dt.year().cast(pl.String), "state", "fips", "jurisdiction", "precinct", separator=":"
            )
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day", "origin").agg(pl.len(), pl.col("code").n_unique()).sort("day"))


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City council and mayoral results by precinct 2014–2025, from the MIT Election Data and Science Lab, OpenElections, and the North Carolina State Board of Elections",
            polity="US",
        )
        states = {
            iso.removeprefix("US-"): polity
            for iso, polity in conn.execute("select iso_code, id from polity where iso_code like 'US-%%'").fetchall()
        }
        fips = dict(rows.select(pl.col("fips").str.slice(0, 2), "state").unique().iter_rows())
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20|22|24):'"
            ).fetchall()
        )
        shaped = dict(
            conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^20(16|18|20|22|24):'"""
            ).fetchall()
        )
        notes = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'methodology'""",
                [SOURCE],
            ).fetchall()
        )
        days = sorted(set(rows["day"]))
        contests = conn.execute(
            """select distinct on (k.id) k.id, k.election_id, e.date_from, left(m.place, 2), rs.stage, rs.kind,
                      (select min(v.count_round) from result_value v where v.result_set_k = rs.k and v.measure = 1)
               from contest k join election e on e.id = k.election_id
               join area a on a.id = k.district_area_id join area_code c on c.area_id = a.id
               cross join lateral (select case a.scheme_code when 'us.place' then c.code
                                   else split_part(c.code, ':', 2) end as place) m
               left join office o on o.id = k.office_id left join body b on b.id = k.body_id
               join result_set rs on rs.contest_id = k.id and rs.is_default
               where e.date_from = any(%s) and (o.kind = 'mayor' or b.kind = 'council') and m.place <> '3651000'
                 and (a.scheme_code = 'us.place' and c.code ~ '^[0-9]{7}$'
                      or a.scheme_code = 'us.local_district' and split_part(c.code, ':', 3) <> 'board-of-education'
                         and (m.place ~ '^[0-9]{7}$|^(?!18)[0-9]{10}$' or m.place in ('24510', '29510', '32510')
                              or m.place ~ '^51[5-8][0-9]{2}$'))""",
            [days],
        ).fetchall()
        ids = [contest for contest, *_ in contests]
        filled = {
            contest
            for (contest,) in conn.execute(
                """select distinct rs.contest_id from result_set rs
                   where rs.contest_id = any(%s) and not rs.is_default and exists (
                     select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                     where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [ids],
            ).fetchall()
        }
        options: dict[str, list[tuple[str, str, set[str], int]]] = {}
        for contest, option, okind, label, name, official in conn.execute(
            """with first as (
                 select rs.contest_id, rs.k, min(v.count_round) as round from result_set rs
                 join result_value v on v.result_set_k = rs.k and v.measure = 1
                 where rs.contest_id = any(%s) and rs.is_default group by 1, 2
               )
               select o.contest_id, o.id, o.kind, o.ballot_label, n.text,
                      (select sum(v.value)::bigint from result_value v
                       where v.result_set_k = f.k and v.option_k = o.k and v.measure = 1 and v.count_round = f.round)
               from contest_option o join first f on f.contest_id = o.contest_id
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               where o.parent_option_id is null""",
            [ids],
        ).fetchall():
            tokens = [slug(text).split("-") for text in (label, name) if text]
            texts = {word for words in tokens for word in words} | {
                a + b for words in tokens for a, b in pairwise(words)
            }
            options.setdefault(str(contest), []).append((str(option), okind, texts, int(official or 0)))
        sources = {}
        for day, (title, _) in medsl_local.DATASETS.items():
            sources[day] = cs.source(medsl_local.PUBLISHER, title)
            if sources[day] in cs.minted:
                raise ValueError(f"{title} must already be a source")
        artifacts = [
            raw.cached(medsl_local.SOURCE, medsl_local.LOCAL2016),
            raw.cached(medsl_local.SOURCE, medsl_local.LOCAL2018),
        ]
        for artifact, day in zip(artifacts, (date(2016, 11, 8), date(2018, 11, 6)), strict=True):
            cs.artifact(artifact, sources[day])
        for listing, local, day in (
            (medsl_local.LISTING, False, date(2024, 11, 5)),
            (medsl_local.LISTING2022, True, date(2022, 11, 8)),
            (medsl_local.LISTING2020, False, date(2020, 11, 3)),
        ):
            for state, url in medsl_local._files(listing, local).items():
                if listing != medsl_local.LISTING2020 or state == "OR":
                    cs.artifact(raw.cached(medsl_local.SOURCE, url), sources[day])
        for (state, day), commit in medsl_local.OPENELECTIONS_FILES.items():
            sources[(state, day)] = cs.source("OpenElections", f"{state} precinct-level results")
            if sources[(state, day)] in cs.minted:
                raise ValueError(f"OpenElections' {state} source must already exist")
            cs.artifact(
                raw.cached(
                    medsl_local.SOURCE, medsl_local.OPENELECTIONS.format(state=state.lower(), commit=commit, day=day)
                ),
                sources[(state, day)],
            )
        for day in sorted(set(rows.filter(pl.col("origin") == "ncsbe")["day"])):
            sources[("NC", day)] = cs.source(ncsbe.PUBLISHER, ncsbe.TITLE)
            if sources[("NC", day)] in cs.minted:
                raise ValueError(f"{ncsbe.TITLE} must already be a source")
            cs.artifact(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)), sources[("NC", day)])
        for state, day in sorted(set(rows.filter(pl.col("origin") == "elstats").select("state", "day").iter_rows())):
            sources[(state, day)] = cs.source(elstats.TENANTS[state][1], elstats.TITLE)
            if sources[(state, day)] in cs.minted:
                raise ValueError(f"{elstats.TENANTS[state][1]}'s {elstats.TITLE} must already be a source")
        for state, year in sorted(
            {
                (state, day.year)
                for state, day in rows.filter(pl.col("origin") == "elstats").select("state", "day").iter_rows()
            }
        ):
            cs.artifact(
                raw.cached(elstats_local.SOURCE, elstats_local.url(state, year)),
                cs.source(elstats.TENANTS[state][1], elstats.TITLE),
            )
        groups = {key: frame for key, frame in rows.group_by("day", "state")}
        index: dict[tuple[date, str], dict[str, set[str]]] = {}
        for (day, state), frame in groups.items():
            for candidate in frame.filter(pl.col("measure") == 1)["candidate"].unique():
                for word in slug(candidate).split("-"):
                    if len(word) > 1 and word not in SUFFIXES:
                        index.setdefault((day, state), {}).setdefault(word, set()).add(candidate)
        taken: set[str] = set()
        values: list[pl.DataFrame] = []
        report: dict[str, int] = {}
        for contest, election, day, statefips, stage, kind, first in contests:
            state = fips.get(statefips)
            lines = groups.get((day, state))
            if contest in filled or lines is None or state is None or first is None:
                report["no precinct file"] = report.get("no precinct file", 0) + 1
                continue
            known = options.get(str(contest), [])
            candidates = [entry for entry in known if entry[1] != "write_in" and entry[3] > 0]
            if not candidates:
                report["no official votes"] = report.get("no official votes", 0) + 1
                continue
            pool = {
                candidate
                for _, _, texts, _ in candidates
                for text in texts
                for candidate in index.get((day, state), {}).get(text, ())
            }
            names = (
                lines.filter((pl.col("measure") == 1) & pl.col("candidate").is_in(list(pool)))
                .select("office", "district", "candidate")
                .unique()
            )
            picked: dict[str, str] = {}
            for candidate in pool:
                words = [word for word in slug(candidate).split("-") if len(word) > 1 and word not in SUFFIXES]
                if not words:
                    continue
                surname = words[0] if "," in candidate else words[-1]
                scores = sorted(
                    (
                        (surname in texts or "".join(words[-2:]) in texts, len(set(words) & texts), option)
                        for option, okind, texts, _ in candidates
                    ),
                    reverse=True,
                )
                if scores[0][0] and scores[0][1] >= 2 and (len(scores) == 1 or scores[0][:2] != scores[1][:2]):
                    picked[candidate] = scores[0][2]
            races = (
                names.filter(pl.col("candidate").is_in(list(picked)))
                .with_columns(option=pl.col("candidate").replace_strict(picked))
                .group_by("office", "district")
                .agg(pl.col("option").n_unique())
                .filter(pl.col("option") == len(candidates))
            )
            if races.height != 1:
                report["no single matching race"] = report.get("no single matching race", 0) + 1
                continue
            office, district = races.row(0)[:2]
            race = lines.filter((pl.col("office") == office) & (pl.col("district") == district))
            mine = set(race.filter(pl.col("candidate").is_in(list(picked)))["code"])
            race = race.filter(pl.col("code").is_in(list(mine)))
            others = race.filter(
                (pl.col("measure") == 1) & ~pl.col("candidate").is_in(list(picked)) & ~pl.col("writein")
            )
            total = sum(entry[3] for entry in candidates)
            if others["votes"].sum() > total * 0.01:
                report["other candidates in the race"] = report.get("other candidates in the race", 0) + 1
                continue
            sums = (
                race.filter(pl.col("candidate").is_in(list(picked)))
                .with_columns(option=pl.col("candidate").replace_strict(picked))
                .group_by("option")
                .agg(pl.col("votes").sum())
            )
            counted = dict(sums.iter_rows())
            if any(
                abs(counted.get(option, 0) - official) > max(2, official * 0.005)
                for option, _, _, official in candidates
            ):
                report["totals differ"] = report.get("totals differ", 0) + 1
                continue
            key = f"{day}:{office}:{district}:{min(mine)}"
            if key in taken:
                report["race already used"] = report.get("race already used", 0) + 1
                continue
            taken.add(key)
            report["matched"] = report.get("matched", 0) + 1
            result_set = cs.key(f"{SOURCE}.result_set", str(contest))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                status="active",
                source_id=sources[day] if lines["origin"][0] == "medsl" else sources[(state, day)],
                is_default=False,
                completeness=round(sum(counted.values()) / total, 4),
            )
            for code, county in race.select("code", "fips").unique().iter_rows():
                if code not in units:
                    if county not in counties:
                        raise ValueError(f"{code}: no county {county}")
                    units[code] = cs.unit("us.precinct", code)
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=states[state],
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=counties[county][0],
                        area_relation="equals" if code in shaped else "within",
                        area_id=shaped.get(code, counties[county][1]),
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            values.append(
                race.filter((pl.col("measure") != 1) | pl.col("candidate").is_in(list(picked)))
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").replace_strict({code: str(units[code]) for code in mine}),
                    option_id=pl.when(pl.col("measure") == 1).then(
                        pl.col("candidate").replace_strict(picked, default=None)
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.col("measure"),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
            cs.add(
                "note",
                id=notes.get(result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                valid_from_precision="day",
                valid_to_precision="day",
            )
        if values:
            cs.frame("result_value", pl.concat(values))
        print(report)
        return cs.write()
