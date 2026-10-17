import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl_local
from ea.sources.us.medsl import RECODED
from ea.sources.us.medsl_state import PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.medsl_local_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LOCAL = medsl_local.SOURCE
ANSWERS = (
    (r"^(?:YES|FOR|APPROVE|IN FAVOR|BONDS? ?(?:-|,)? ?YES|TAX ?(?:-|,)? ?YES|LEVY ?(?:-|,)? ?YES)\b", "YES"),
    (r"^(?:NO|AGAINST|REJECT|OPPOSED|BONDS? ?(?:-|,)? ?NO|TAX ?(?:-|,)? ?NO|LEVY ?(?:-|,)? ?NO)\b", "NO"),
)
IGNORED = r"(?i)^(?:OVER ?VOTES?|UNDER ?VOTES?|WRITE[- ]?INS?|\[WRITE-IN\]|BLANK|TOTAL VOTES|REGISTERED VOTERS)$"
EXCLUDED = re.compile(r"RETAIN|RETENTION|JUDGE|JUSTICE|COURT|RECALL")
STATEWIDE = re.compile(r"\bSTATE\b|STATEWIDE|CONSTITUTIONAL AMENDMENT|\bAMENDMENT (?:NO\.? )?\d|LEGISLATIVELY REFERRED")
MUNICIPAL = re.compile(
    r"\b(?:CITY|TOWN|VILLAGE|BOROUGH) OF ((?:[A-Z.'-]+ ?){1,4})|^((?:[A-Z.'-]+ ){1,3})(?:CITY|TOWN|VILLAGE)\b"
)
ACRONYMS = {
    "ESD",
    "USD",
    "UHSD",
    "ISD",
    "CSD",
    "EMS",
    "SPLOST",
    "ESPLOST",
    "TSPLOST",
    "LOST",
    "PUD",
    "ZAP",
    "RAP",
    "II",
    "III",
    "IV",
    "TBD",
    "LID",
    "RTA",
    "HOA",
}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
METHOD = (
    "Yes and No votes are the sums of the precinct rows in MEDSL's {year} precinct file for the state. The file "
    "does not say what share of the vote the question needed or whether it passed, so no outcome is recorded."
)
PARTIAL = (
    "Only part of the county votes on this question, likely a school, special, or municipal district the file "
    "does not name as such. The archive has no boundary for it, so the results are shown for the precincts that "
    "voted, as a whole."
)


def fetch() -> None:
    medsl_local.fetch()


def parse() -> None:
    with connect() as conn:
        places: dict[str, dict[str, tuple[str, str]]] = {}
        for scheme, code, text in conn.execute(
            """select u.scheme_code, u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.polity_id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.place' and u.code ~ '^[0-9]{7}$'
                  or u.scheme_code = 'us.town' and u.code ~ '^[0-9]{10}$'
               order by u.scheme_code desc"""
        ).fetchall():
            places.setdefault(code[:2], {}).setdefault(
                re.sub(r"[^A-Z]", "", re.sub(r"^(?:CITY|TOWN|VILLAGE) OF ", "", text.upper())).removesuffix("CITY"),
                (code, scheme),
            )
    columns = [
        "state_po",
        pl.col("office").str.to_uppercase().str.strip_chars(),
        pl.col("district").str.to_uppercase(),
        "county_fips",
        pl.col("county_name").str.to_uppercase(),
        "precinct",
        pl.col("jurisdiction_name").str.to_uppercase(),
        pl.col("mode").str.to_uppercase(),
        "stage",
        pl.col("candidate").str.to_uppercase().str.strip_chars(),
        "votes",
    ]
    scans = [
        (
            day,
            (
                pl.read_csv(zipfile.ZipFile(path).read(zipfile.ZipFile(path).namelist()[0]), infer_schema=False).lazy()
                if path.suffix == ".zip"
                else pl.scan_csv(path, infer_schema=False)
            )
            .filter(
                pl.col("dataverse").is_null() if local else pl.col("dataverse") == "LOCAL",
                pl.col("stage").is_in(["GEN", "GEN RECOUNT"]),
            )
            .select(*columns),
        )
        for listing, local, day in (
            (medsl_local.LISTING, False, date(2024, 11, 5)),
            (medsl_local.LISTING2022, True, date(2022, 11, 8)),
            (medsl_local.LISTING2020, False, date(2020, 11, 3)),
        )
        for state, url in sorted(medsl_local._files(listing, local).items())
        if listing != medsl_local.LISTING2020 or state == "OR"
        if (path := raw.cached(LOCAL, url).path)
    ]
    scans.append(
        (
            date(2016, 11, 8),
            pl.scan_csv(
                raw.cached(LOCAL, medsl_local.LOCAL2016).path,
                infer_schema=False,
                encoding="utf8-lossy",
                null_values=["", "NA"],
            ).select(
                pl.col("state_postal").alias("state_po"),
                pl.col("office").str.to_uppercase().str.strip_chars(),
                pl.when(pl.col("state_postal") != "WV").then(pl.col("district").str.to_uppercase()).alias("district"),
                "county_fips",
                pl.col("county_name").str.to_uppercase(),
                "precinct",
                pl.col("jurisdiction").str.to_uppercase().alias("jurisdiction_name"),
                pl.col("mode").str.to_uppercase(),
                pl.lit("GEN").alias("stage"),
                pl.col("candidate").str.to_uppercase().str.strip_chars(),
                "votes",
            ),
        )
    )
    scans.append(
        (
            date(2018, 11, 6),
            pl.scan_csv(raw.cached(LOCAL, medsl_local.LOCAL2018).path, infer_schema=False)
            .filter(pl.col("stage").is_in(["GEN", "GEN RECOUNT"]))
            .select(*columns),
        )
    )
    answer = pl.lit(None, pl.String)
    for pattern, value in reversed(ANSWERS):
        answer = pl.when(pl.col("candidate").str.contains(pattern)).then(pl.lit(value)).otherwise(answer)
    frames = []
    for day, scan in scans:
        frame = (
            scan.with_columns(
                pl.lit(day).alias("day"),
                pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                pl.col("county_fips").str.zfill(5).replace(RECODED),
                answer.alias("answer"),
            )
            .filter(
                ~pl.col("state_po").is_in(list(medsl_local.COVERED)),
                pl.col("office").is_not_null(),
                pl.col("county_fips").is_not_null(),
                ~pl.col("candidate").fill_null("").str.contains(IGNORED),
            )
            .collect()
        )
        if frame.height == 0:
            continue
        recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select("office", "district", "county_fips").unique()
        frame = pl.concat(
            [
                frame.join(recounted, on=["office", "district", "county_fips"], how="anti", nulls_equal=True),
                frame.filter(pl.col("stage") == "GEN RECOUNT"),
            ]
        )
        frame = frame.with_columns(
            unit=pl.when(
                (pl.col("precinct").fill_null("").str.strip_chars() != "")
                & ~pl.col("precinct").fill_null("").str.to_uppercase().str.contains("TOTAL")
            )
            .then(
                pl.concat_str(
                    pl.lit(str(day.year)),
                    "state_po",
                    "county_fips",
                    pl.col("jurisdiction_name").fill_null(""),
                    pl.col("precinct").str.strip_chars(),
                    separator=":",
                )
            )
            .otherwise(pl.lit(""))
        )
        sizes = frame.filter(pl.col("unit") != "").group_by("county_fips").agg(pl.col("unit").n_unique().alias("size"))
        counties = frame.group_by("state_po").agg(pl.col("county_fips").n_unique().alias("counties"))
        race = ["state_po", "office", "district", "county_fips"]
        questions = (
            frame.group_by(*race)
            .agg(pl.col("answer").is_null().any().alias("other"), pl.col("answer").n_unique().alias("answers"))
            .filter(~pl.col("other"), pl.col("answers") == 2)
            .select(race)
        )
        frame = frame.join(questions, on=race, nulls_equal=True)
        frame = (
            frame.with_columns(
                total=(pl.col("mode") == "TOTAL").any().over("state_po", "office", "district", "unit", "candidate")
            )
            .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
            .join(counties, on="state_po")
            .with_columns(spread=pl.col("county_fips").n_unique().over("state_po", "office", "district"))
            .filter(
                (pl.col("spread") <= pl.col("counties") / 2) | (pl.col("counties") < 4),
                ~pl.col("office").str.contains(EXCLUDED.pattern),
                ~pl.col("office").str.contains(STATEWIDE.pattern),
            )
        )
        labels = []
        for state, office, fips in (
            frame.select("state_po", "office", pl.col("county_fips").str.slice(0, 2))
            .unique(["state_po", "office"])
            .iter_rows()
        ):
            index = places.get(fips, {})
            found = None
            for match in MUNICIPAL.finditer(re.sub(r"[^A-Z .'-]", " ", office)):
                words = (match[1] or match[2] or "").split()
                for size in range(len(words), 0, -1):
                    key = re.sub(r"[^A-Z]", "", "".join(words[:size])).removesuffix("CITY")
                    if key in index:
                        found = index[key]
                        break
                if found is not None:
                    break
            labels.append(
                {
                    "state_po": state,
                    "office": office,
                    "place": found[0] if found else None,
                    "scheme": found[1] if found else "us.county",
                }
            )
        frame = frame.join(
            pl.DataFrame(
                labels, schema={"state_po": pl.String, "office": pl.String, "place": pl.String, "scheme": pl.String}
            ),
            on=["state_po", "office"],
        )
        frames.append(
            frame.with_columns(
                code=pl.coalesce("place", "county_fips"),
            )
            .join(sizes, on="county_fips", how="left")
            .with_columns(
                partial=pl.col("place").is_null()
                & (
                    pl.col("unit").filter(pl.col("unit") != "").n_unique().over("day", "code", "office", "district")
                    < 0.9 * pl.col("size")
                )
            )
            .group_by(
                "day", "state_po", "code", "scheme", "office", "district", "partial", "county_fips", "unit", "answer"
            )
            .agg(pl.col("votes").sum())
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "questions.parquet")


def emit() -> Path:
    questions = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        polities = {
            (scheme, code): (polity, text, path, area)
            for scheme, code, polity, text, path, area in conn.execute(
                """select a.scheme_code, c.code, t.polity_id, n.text, s.path, a.id from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id and s.is_current
                   where a.scheme_code in ('us.county', 'us.place', 'us.town')"""
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20|22|24):'"
            ).fetchall()
        )
        parents: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local ballot questions voted in November 2016 through 2024, from MEDSL's precinct files",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("us.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("the referendum system must already exist")
        sources = {}
        for day, (text, url) in medsl_local.DATASETS.items():
            source = sources[day] = cs.source(PUBLISHER, text)
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=text,
                url=url,
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        for listing, local, day in (
            (medsl_local.LISTING, False, date(2024, 11, 5)),
            (medsl_local.LISTING2022, True, date(2022, 11, 8)),
            (medsl_local.LISTING2020, False, date(2020, 11, 3)),
        ):
            files = {state.removesuffix(".zip"): url for state, url in medsl_local._files(listing, local).items()}
            for state in sorted(set(questions.filter(pl.col("day") == day)["state_po"])):
                cs.artifact(raw.cached(LOCAL, files[state]), sources[day])
        cs.artifact(raw.cached(LOCAL, medsl_local.LOCAL2016), sources[date(2016, 11, 8)])
        cs.artifact(raw.cached(LOCAL, medsl_local.LOCAL2018), sources[date(2018, 11, 6)])
        elections: dict[tuple[date, str], UUID] = {}
        values: dict[tuple[str, str, str], int] = {}
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        districts: set[str] = set()
        for (day, state, code, scheme, office, district, partial), rows in questions.group_by(
            "day", "state_po", "code", "scheme", "office", "district", "partial", maintain_order=True
        ):
            if (scheme, code) not in polities:
                continue
            polity, place, path, whole = polities[(scheme, code)]
            parent = cs.key("us.election", f"{day}:{state}")
            if parent in cs.minted:
                raise ValueError(f"no election {day}:{state}")
            if (day, code) not in elections:
                election = elections[(day, code)] = cs.key("us.election", f"{day}:{state}:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                if election in cs.minted:
                    name(election, f"{day.year} {place} general election")
                    cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
            election = elections[(day, code)]
            words = re.sub(r"\s+", " ", office).split()
            heading = " ".join(
                word
                if any(character.isdigit() for character in word)
                and len(word) < 6
                or word in ACRONYMS
                or len(word) > 1
                and not re.search(r"[AEIOUY]", word)
                and word.isalpha()
                or len(word) == 1
                and index == len(words) - 1
                else word.lower()
                if index and word.lower() in SMALL
                else "-".join(part.capitalize() for part in word.lower().split("-"))
                for index, word in enumerate(words)
            )
            if district and district not in office and not district.isdigit():
                heading = f"{heading}, {district.title()}"
            if len(heading) > 120:
                heading = heading[:117].rsplit(" ", 1)[0] + "..."
            contest_key = f"{day}:{state}:{code}:question:{slug(office)[:80]}" + (
                f":{slug(district)[:40]}" if district else ""
            )
            contest = cs.key("us.contest", contest_key)
            area = whole
            if partial:
                local = f"{code[:2]}:{code}:question:{slug(office)[:60]}" + (
                    f":{slug(district)[:30]}" if district else ""
                )
                area = cs.area("us.local_district", local)
                if area in cs.minted and local not in districts:
                    districts.add(local)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=local, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{heading} ({place})")
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.local_district", local),
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=local,
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
            text = office.lower()
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative"
                if "initiat" in text
                else "plebiscite"
                if "advisory" in text or "straw" in text
                else "mandatory_referendum",
            )
            if contest in cs.minted:
                name(contest, f"{day.year} {place}: {heading}")
                base = f"{path}/{day}-general/{slug(heading)[:60].rstrip('-') or 'question'}"
                target, n = base, 1
                while target in taken:
                    n += 1
                    target = f"{base}-{n}"
                taken.add(target)
                cs.add("slug", path=target, entity_id=contest)
                if partial:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=PARTIAL)
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
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[day],
                is_default=True,
                completeness=1,
            )
            if result_set not in cs.minted:
                continue
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=day.year))
            unit = cs.unit(scheme, code)
            for county, place_code, answer, votes in rows.select("county_fips", "unit", "answer", "votes").iter_rows():
                if votes is None:
                    continue
                target = unit
                if county in parents:
                    target = parents[county][0]
                    if place_code:
                        target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                        if place_code not in precincts and place_code not in minted:
                            minted[place_code] = (county, day)
                    if area != parents[county][1]:
                        memberships.add((target, area, day))
                cell = (str(result_set), str(target), str(options[answer]))
                values[cell] = values.get(cell, 0) + votes
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    (
                        str(cs.unit("us.precinct", place)),
                        str(cs.polity(f"US-{place.split(':')[1]}")),
                        "us.precinct",
                        place,
                        str(parents[county][0]),
                        "within",
                        str(parents[county][1]),
                        "standard",
                        day,
                        day + timedelta(days=1),
                    )
                    for place, (county, day) in minted.items()
                ],
                schema={
                    "id": pl.String,
                    "polity_id": pl.String,
                    "scheme_code": pl.String,
                    "code": pl.String,
                    "parent_id": pl.String,
                    "area_relation": pl.String,
                    "area_id": pl.String,
                    "kind": pl.String,
                    "valid_from": pl.Date,
                    "valid_to": pl.Date,
                },
                orient="row",
            ),
        )
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [(str(target), str(area), day, day + timedelta(days=1)) for target, area, day in memberships],
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                [(*cell, value) for cell, value in values.items()],
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
