import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.quebec"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://donnees.electionsquebec.qc.ca/production/provincial/resultats"
MAP = "https://donnees.electionsquebec.qc.ca/autres/provincial/circonscriptions_electorales_sans_eau_2026.json"
GENERAL = date(2026, 10, 5)
BY_ELECTION = date(2026, 2, 23)
RESULTS = {GENERAL: f"{FILES}/resultats.json", BY_ELECTION: f"{FILES}/archives/part2026-02-23/resultats.json"}
PUBLISHER = "Élections Québec"
LICENSE = "Licence d'utilisation des données ouvertes du directeur général des élections"
ATTRIBUTION = (
    "Comprend des données ouvertes octroyées sous la licence d'utilisation des données ouvertes du directeur "
    "général des élections disponible à l'adresse Web dgeq.org."
)
COLORS = {8: "#004c9d", 6: "#ea2127", 22: "#1b3a8c", 40: "#ff5505", 27: "#1fb1e4", 10: "#3d9b35"}
RECOUNT_NOTE = (
    "Élections Québec flagged these results as final but said it would publish the definitive data after any "
    "judicial recounts under way. Winners are the candidates with the most votes in each division."
)
OLD_MAP_NOTE = "Chicoutimi voted under the 2017 electoral map. Its boundary is not yet in the archive."


def fetch() -> None:
    for url in (*RESULTS.values(), MAP):
        raw.fetch(SOURCE, url, suffix=".json")


def parse() -> None:
    parties: dict[int, tuple[str, str]] = {}
    ridings = []
    candidates = []
    for day, url in RESULTS.items():
        data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
        if not data["statistiques"]["isResultatsFinaux"]:
            raise ValueError(f"{day}: results are not final")
        for party in data["statistiques"]["partisPolitiques"]:
            parties.setdefault(
                party["numeroPartiPolitique"], (party["nomPartiPolitique"].strip(), party["abreviationPartiPolitique"])
            )
        for riding in data["circonscriptions"]:
            if riding["nbBureauComplete"] != riding["nbBureauTotal"]:
                raise ValueError(f"{day} {riding['nomCirconscription']}: polls still out")
            ridings.append(
                {
                    "date": day,
                    "riding": riding["numeroCirconscription"],
                    "name": riding["nomCirconscription"],
                    "registered": riding["nbElecteurInscrit"],
                    "rejected": riding["nbVoteRejete"],
                    "valid": riding["nbVoteValide"],
                }
            )
            if sum(c["nbVoteTotal"] for c in riding["candidats"]) != riding["nbVoteValide"]:
                raise ValueError(f"{day} {riding['nomCirconscription']}: candidates do not sum to valid votes")
            top = max(c["nbVoteTotal"] for c in riding["candidats"])
            if sum(c["nbVoteTotal"] == top for c in riding["candidats"]) != 1:
                raise ValueError(f"{day} {riding['nomCirconscription']}: tie for first")
            candidates.extend(
                {
                    "date": day,
                    "riding": riding["numeroCirconscription"],
                    "candidate": c["numeroCandidat"],
                    "first": c["prenom"],
                    "family": c["nom"],
                    "party": c["numeroPartiPolitique"],
                    "abbreviation": c["abreviationPartiPolitique"],
                    "votes": c["nbVoteTotal"],
                    "elected": c["nbVoteTotal"] == top,
                }
                for c in riding["candidats"]
            )
    with connect() as conn:
        conn.execute(
            """create temp table riding on commit drop as
               select code, name, st_multi(st_collectionextract(st_makevalid(st_setsrid(st_geomfromgeojson(geojson), 4326)), 3)) as geom
               from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)""",
            list(
                map(
                    list,
                    zip(
                        *[
                            (str(f["properties"]["CO_CEP"]), f["properties"]["NM_CEP"], json.dumps(f["geometry"]))
                            for f in json.loads(raw.cached(SOURCE, MAP).path.read_text(encoding="utf-8"))["features"]
                        ],
                        strict=True,
                    ),
                )
            ),
        )
        shapes = pl.DataFrame(
            conn.execute(
                """select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from riding order by code"""
            ).fetchall(),
            schema=["code", "name", "geom", "label_point"],
            orient="row",
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(ridings).write_parquet(PARSED / "ridings.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(
        [{"party": number, "name": name, "abbreviation": short} for number, (name, short) in parties.items()]
    ).write_parquet(PARSED / "parties.parquet")
    shapes.write_parquet(PARSED / "shapes.parquet")
    print(f"{len(ridings)} contests, {len(candidates)} candidates, {len(parties)} parties, {shapes.height} shapes")


def emit() -> Path:
    ridings = pl.read_parquet(PARSED / "ridings.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    parties = pl.read_parquet(PARSED / "parties.parquet")
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Quebec's general election of 5 October 2026 and the Chicoutimi by-election of 23 February 2026, by division",
            polity="CA-QC",
        )

        def name(
            entity: UUID, text: str, kind: str = "official", lang: str = "en", preferred: bool | None = None
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=(kind == "official") if preferred is None else preferred,
            )

        quebec = cs.polity("CA-QC")
        province = cs.area("ca.province", "24")
        if {quebec, province} & cs.minted:
            raise ValueError("run ca.elections first: Quebec and its province area must exist")
        cs.add("scheme", code="ca.qc", polity_id=quebec, kind="electoral_district", rank=2, noun="electoral division")

        source_title = {
            GENERAL: "Résultats des élections générales du 5 octobre 2026 (données ouvertes)",
            BY_ELECTION: "Résultats de l'élection partielle de Chicoutimi du 23 février 2026 (données ouvertes)",
        }
        sources: dict[date, UUID] = {}
        for day, url in RESULTS.items():
            sources[day] = cs.source(PUBLISHER, source_title[day])
            cs.add(
                "source",
                id=sources[day],
                kind="official_body",
                publisher=PUBLISHER,
                title=source_title[day],
                url="https://dgeq.org/donnees.html" if day == GENERAL else "https://dgeq.org/archives.html",
                license=LICENSE,
                license_url="https://dgeq.org/licence.html",
                attribution=ATTRIBUTION,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[day])
        boundaries = cs.source(PUBLISHER, "Carte électorale 2026 (données ouvertes)")
        cs.add(
            "source",
            id=boundaries,
            kind="official_body",
            publisher=PUBLISHER,
            title="Carte électorale 2026 (données ouvertes)",
            url="https://dgeq.org/donnees.html",
            license=LICENSE,
            license_url="https://dgeq.org/licence.html",
            attribution=ATTRIBUTION,
        )
        cs.artifact(raw.cached(SOURCE, MAP), boundaries)

        assembly = cs.key(f"{SOURCE}.body", "national_assembly")
        cs.add("body", id=assembly, polity_id=quebec, kind="unicameral")
        name(assembly, "National Assembly of Quebec")
        name(assembly, "Assemblée nationale du Québec", "native", "fr")
        cs.add("body_size", body_id=assembly, seats=125, valid_from=None, valid_to=GENERAL)
        cs.add("body_size", body_id=assembly, seats=127, valid_from=GENERAL, valid_to=None)
        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=quebec, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")

        areas: dict[tuple[date, int], UUID] = {}
        for code, riding_name, geom, label_point in shapes.iter_rows():
            area = areas[GENERAL, int(code)] = cs.area("ca.qc", code, GENERAL)
            cs.add("area", id=area, scheme_code="ca.qc")
            cs.add("area_code", area_id=area, code=code, valid_from=GENERAL, valid_to=None)
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, riding_name, "official", "fr")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=GENERAL,
                valid_to=None,
                geom=geom,
                label_point=label_point,
                quality="official",
                source_id=boundaries,
            )
        cs.add("boundary_set", scheme_code="ca.qc", vintage="2026", valid_from=GENERAL, valid_to=None, as_of=GENERAL)
        for row in ridings.filter(pl.col("date") == BY_ELECTION).iter_rows(named=True):
            area = areas[BY_ELECTION, row["riding"]] = cs.area("ca.qc", f"2017:{row['riding']}", BY_ELECTION)
            cs.add("area", id=area, scheme_code="ca.qc")
            cs.add("area_code", area_id=area, code=f"2017:{row['riding']}", valid_from=None, valid_to=GENERAL)
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, row["name"], "official", "fr")

        party_ids: dict[int, UUID] = {}
        for number, full, short in parties.filter(pl.col("party") != 0).iter_rows():
            party_ids[number] = cs.key(f"{SOURCE}.party", str(number))
            cs.add("party", id=party_ids[number], kind="party", polity_id=quebec)
            french, _, english = (part.strip() for part in full.partition("/"))
            name(party_ids[number], french, "official", "fr")
            if english:
                name(party_ids[number], english, "official", "en", False)
            name(party_ids[number], short, "abbreviation")
            if number in COLORS:
                cs.add("party_color", party_id=party_ids[number], color=COLORS[number])

        persons: dict[int, UUID] = {}
        for day in (BY_ELECTION, GENERAL):
            general = day == GENERAL
            kind = "general" if general else "by-election"
            election = cs.key(f"{SOURCE}.election", day.isoformat())
            cs.add(
                "election",
                id=election,
                polity_id=quebec,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            if general:
                name(election, "2026 Quebec general election")
                name(election, "Élections générales québécoises de 2026", "native", "fr")
                cs.add("note", entity_id=election, kind="context", body_md=RECOUNT_NOTE)
            else:
                name(election, "Chicoutimi provincial by-election, February 23, 2026")
                name(election, "Élection partielle de Chicoutimi du 23 février 2026", "native", "fr")
            cs.add("slug", path=f"ca/qc/{day}-{kind}", entity_id=election)

            day_ridings = ridings.filter(pl.col("date") == day)
            result_sets: dict[int, UUID] = {}
            units: dict[int, UUID] = {}
            options: dict[tuple[int, int], UUID] = {}
            for row in day_ridings.iter_rows(named=True):
                number = row["riding"]
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{number}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[day, number],
                    magnitude=1,
                    electoral_system_id=fptp,
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
                cs.add("slug", path=f"ca/qc/{day}-{kind}/{slug(row['name'])}", entity_id=contest)
                if not general:
                    cs.add("note", entity_id=contest, kind="context", body_md=OLD_MAP_NOTE)
                units[number] = cs.unit("ca.qc", f"{day}:{number}", day)
                cs.add(
                    "reporting_unit",
                    id=units[number],
                    polity_id=quebec,
                    scheme_code="ca.qc",
                    code=f"{day}:{number}",
                    area_relation="equals",
                    area_id=areas[day, number],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                result_sets[number] = cs.key(f"{SOURCE}.result_set", f"{day}:{number}")
                cs.add(
                    "result_set",
                    id=result_sets[number],
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=sources[day],
                    status="active",
                    is_default=True,
                    completeness=1,
                )
            for candidate in candidates.filter(pl.col("date") == day).iter_rows(named=True):
                number = candidate["riding"]
                display = f"{candidate['first']} {candidate['family']}"
                person = persons.get(candidate["candidate"])
                if person is None:
                    person = persons[candidate["candidate"]] = cs.key(f"{SOURCE}.person", str(candidate["candidate"]))
                    cs.add("person", id=person)
                    name(person, display)
                key = (number, candidate["candidate"])
                options[key] = cs.key(f"{SOURCE}.option", f"{day}:{number}:{candidate['candidate']}")
                party = party_ids.get(candidate["party"])
                cs.add(
                    "contest_option",
                    id=options[key],
                    contest_id=cs.key(f"{SOURCE}.contest", f"{day}:{number}"),
                    kind="candidate",
                    party_id=party,
                    ballot_label=display,
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{day}:{number}:{candidate['candidate']}"),
                    person_id=person,
                    option_id=options[key],
                    role="head",
                    ballot_name=display,
                    member_of_party_id=party,
                    is_independent=candidate["party"] == 0,
                )
                if candidate["elected"]:
                    cs.add(
                        "outcome",
                        result_set_id=result_sets[number],
                        option_id=options[key],
                        kind="elected",
                        seats=1,
                    )
            columns = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"]
            frames = []
            for measure, field in ((2, "registered"), (5, "rejected")):
                frames.append(
                    day_ridings.select(
                        result_set_id=pl.col("riding").replace_strict(
                            {n: str(i) for n, i in result_sets.items()}, return_dtype=pl.String
                        ),
                        unit_id=pl.col("riding").replace_strict(
                            {n: str(i) for n, i in units.items()}, return_dtype=pl.String
                        ),
                        option_id=pl.lit(None, pl.String),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(measure, pl.Int16),
                        count_round=pl.lit(0, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value=pl.col(field),
                    )
                )
            frames.append(
                pl.DataFrame(
                    {
                        "riding": [number for number, _ in options],
                        "option": [str(option) for option in options.values()],
                        "candidate": [candidate for _, candidate in options],
                    }
                )
                .join(
                    candidates.filter(pl.col("date") == day).select("riding", "candidate", "votes"),
                    on=["riding", "candidate"],
                )
                .select(
                    result_set_id=pl.col("riding").replace_strict(
                        {n: str(i) for n, i in result_sets.items()}, return_dtype=pl.String
                    ),
                    unit_id=pl.col("riding").replace_strict(
                        {n: str(i) for n, i in units.items()}, return_dtype=pl.String
                    ),
                    option_id="option",
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
            cs.frame("result_value", pl.concat(frames).select(columns))
        return cs.write()
