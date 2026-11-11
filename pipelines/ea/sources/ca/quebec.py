import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.quebec"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://donnees.electionsquebec.qc.ca/production/provincial/resultats"
MAP = "https://donnees.electionsquebec.qc.ca/autres/provincial/circonscriptions_electorales_sans_eau_2026.json"
GENERAL = date(2026, 10, 5)
GENERALS = [
    date(1966, 6, 5),
    date(1970, 4, 29),
    date(1973, 10, 29),
    date(1976, 11, 15),
    date(1981, 4, 13),
    date(1985, 12, 2),
    date(1989, 9, 25),
    date(1994, 9, 12),
    date(1998, 11, 30),
    date(2003, 4, 14),
    date(2007, 3, 26),
    date(2008, 12, 8),
    date(2012, 9, 4),
    date(2014, 4, 7),
    date(2018, 10, 1),
    date(2022, 10, 3),
    GENERAL,
]
BY_ELECTIONS = [
    date(1968, 12, 4),
    date(1969, 3, 3),
    date(1969, 10, 8),
    date(1971, 2, 8),
    date(1972, 10, 11),
    date(1974, 8, 28),
    date(1978, 7, 5),
    date(1979, 4, 30),
    date(1979, 11, 14),
    date(1979, 11, 26),
    date(1980, 11, 17),
    date(1982, 4, 5),
    date(1983, 6, 20),
    date(1983, 12, 5),
    date(1984, 6, 18),
    date(1984, 11, 26),
    date(1985, 6, 3),
    date(1986, 1, 20),
    date(1987, 9, 14),
    date(1988, 6, 20),
    date(1989, 5, 29),
    date(1991, 8, 12),
    date(1992, 1, 20),
    date(1993, 7, 5),
    date(1993, 12, 13),
    date(1994, 2, 21),
    date(1994, 2, 28),
    date(1996, 2, 19),
    date(1996, 6, 10),
    date(1996, 12, 9),
    date(1997, 4, 28),
    date(1997, 10, 6),
    date(1998, 6, 1),
    date(2001, 4, 9),
    date(2001, 10, 1),
    date(2002, 4, 15),
    date(2002, 6, 17),
    date(2004, 9, 20),
    date(2005, 12, 12),
    date(2006, 4, 10),
    date(2006, 8, 14),
    date(2007, 9, 24),
    date(2008, 5, 12),
    date(2008, 9, 29),
    date(2009, 6, 22),
    date(2009, 9, 21),
    date(2010, 7, 5),
    date(2010, 9, 13),
    date(2010, 11, 29),
    date(2011, 12, 5),
    date(2012, 6, 11),
    date(2013, 12, 9),
    date(2014, 10, 20),
    date(2015, 3, 9),
    date(2015, 6, 8),
    date(2015, 11, 9),
    date(2016, 4, 11),
    date(2016, 12, 5),
    date(2017, 5, 29),
    date(2017, 10, 2),
    date(2018, 12, 10),
    date(2019, 12, 2),
    date(2022, 4, 11),
    date(2023, 3, 13),
    date(2023, 10, 2),
    date(2025, 3, 17),
    date(2025, 8, 11),
    date(2026, 2, 23),
]
RESULTS = {
    day: f"{FILES}/resultats.json"
    if day == GENERAL
    else f"{FILES}/archives/{'gen' if day in GENERALS else 'part'}{day}/resultats.json"
    for day in sorted([*GENERALS, *BY_ELECTIONS])
}
MAPS = {
    date(1966, 6, 5): "1965",
    date(1973, 10, 29): "1972",
    date(1981, 4, 13): "1980",
    date(1989, 9, 25): "1988",
    date(1994, 9, 12): "1992",
    date(2003, 4, 14): "2001",
    date(2012, 9, 4): "2011",
    date(2018, 10, 1): "2017",
}
SEATS = {date(1966, 6, 5): 108, date(1973, 10, 29): 110, date(1981, 4, 13): 122, date(1989, 9, 25): 125, GENERAL: 127}
PUBLISHER = "Élections Québec"
LICENSE = "Licence d'utilisation des données ouvertes du directeur général des élections"
ATTRIBUTION = (
    "Comprend des données ouvertes octroyées sous la licence d'utilisation des données ouvertes du directeur "
    "général des élections disponible à l'adresse Web dgeq.org."
)
COLORS = {
    "8": "#004c9d",
    "6": "#ea2127",
    "22": "#1b3a8c",
    "40": "#ff5505",
    "27": "#1fb1e4",
    "10": "#3d9b35",
    "union-nationale:old": "#1d3f7a",
    "action-democratique-du-quebec": "#2e9bd6",
}
CURRENT = {
    "8",
    "6",
    "22",
    "40",
    "27",
    "10",
    "99237",
    "99263",
    "23",
    "99286",
    "99331",
    "42",
    "99291",
    "99318",
    "7",
    "2",
    "99341",
    "29",
    "99375",
    "99330",
    "99352",
}
PARTIES = {
    "Parti libéral du Québec": "6",
    "Parti libéral du Québec/Quebec Liberal Party": "6",
    "Parti québécois": "8",
    "Québec solidaire": "40",
    "Coalition avenir Québec - L'équipe François Legault": "27",
    "Parti vert du Québec/Green Party of Québec": "10",
    "Parti conservateur du Québec": "22",
    "Parti marxiste-léniniste du Québec": "7",
    "Bloc pot": "2",
    "Parti nul": "23",
    "Équipe autonomiste": "29",
    "Action démocratique du Québec/Équipe Mario Dumont": "action-democratique-du-quebec",
    "Option nationale - Pour l'indépendance du Québec": "option-nationale",
    "Union citoyenne du Québec / Québec Citizen's Union": "union-citoyenne-du-quebec-quebec-citizens-union",
    "Parti égalité/Equality Party": "parti-egalite",
    "Parti crédit social uni": "credit-social-uni",
    "Nouveau parti démocratique": "nouveau-parti-democratique-du-quebec:old",
}
REFOUNDED = {"Union nationale", "Parti indépendantiste", "Parti 51", "Nouveau parti démocratique du Québec"}
ABBREVIATIONS = {
    "LIB.": "Parti libéral du Québec",
    "U.-Q.": "Union nationale",
    "N.P.D.": "Nouveau parti démocratique",
    "R.C.": "Ralliement créditiste du Québec",
}
INDEPENDENT = {"Indépendant", "Sans désignation", "Candidats indépendants/Independent candidates"}
MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
RECOUNT_NOTE = (
    "Élections Québec flagged these results as final but said it would publish the definitive data after any "
    "judicial recounts under way. Winners are the candidates with the most votes in each division."
)
CHAMBLY_NOTE = (
    "Élections Québec's file prints 3,895 valid and 4,676 cast ballots for Chambly, which the candidates' "
    "31,703 votes exceed. The candidates' votes and the 781 rejected ballots are loaded as printed."
)


def unlabeled(parties: list[str]) -> str:
    return (
        f"Élections Québec's file gives the party of {listed(parties)} only as an abbreviation, so "
        f"{'that candidate is' if len(parties) == 1 else 'those candidates are'} not linked to a party."
    )


def french(day: date) -> str:
    return f"{'1er' if day.day == 1 else day.day} {MONTHS[day.month - 1]} {day.year}"


def english(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def listed(items: list[str]) -> str:
    return (
        items[0]
        if len(items) == 1
        else " and ".join(items)
        if len(items) == 2
        else f"{', '.join(items[:-1])}, and {items[-1]}"
    )


def map_of(day: date) -> str | None:
    if day >= GENERAL:
        return None
    return MAPS[max(start for start in MAPS if start <= day)]


def party_key(name: str, number: int | None, day: date) -> str | None:
    if name in INDEPENDENT or number == 0:
        return None
    if number is not None and str(number) in CURRENT:
        return str(number)
    if name in PARTIES:
        return PARTIES[name]
    if name in REFOUNDED and day.year < 2000:
        return f"{slug(name)}:old"
    return slug(name)


def fetch() -> None:
    for url in (*RESULTS.values(), MAP):
        raw.fetch(SOURCE, url, suffix=".json")


def parse() -> None:
    parties = []
    ridings = []
    candidates = []
    numbers: dict[tuple[str | None, str], int] = {}
    named: dict[str, list[tuple[date, str]]] = {}
    for day, url in RESULTS.items():
        for party in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["statistiques"][
            "partisPolitiques"
        ]:
            if party["nomPartiPolitique"] and party.get("abreviationPartiPolitique"):
                named.setdefault(party["abreviationPartiPolitique"], []).append(
                    (day, party["nomPartiPolitique"].strip())
                )

    def resolve(short: str, day: date) -> str | None:
        if short == "P.V.Q." and day.year > 2000:
            return "Parti vert du Québec/Green Party of Québec"
        if short in ABBREVIATIONS:
            return ABBREVIATIONS[short]
        near = [(abs((seen - day).days), name) for seen, name in named.get(short, []) if abs(seen - day).days < 6 * 366]
        return min(near)[1] if near else None

    for day, url in RESULTS.items():
        data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
        if data["statistiques"].get("isResultatsFinaux") is False:
            raise ValueError(f"{day}: results are not final")
        general = day in GENERALS
        scheme = map_of(day)
        by_number = {}
        by_abbreviation = {}
        for party in data["statistiques"]["partisPolitiques"]:
            number = party.get("numeroPartiPolitique")
            short = party.get("abreviationPartiPolitique")
            name = party["nomPartiPolitique"].strip() if party["nomPartiPolitique"] else resolve(short, day)
            if number is not None:
                by_number[number] = name
            if short is not None:
                if short in by_abbreviation:
                    raise ValueError(f"{day}: abbreviation {short} is ambiguous")
                by_abbreviation[short] = name
            key = party_key(name, number, day) if name else None
            if key is not None:
                parties.append({"date": day, "party": key, "name": name, "abbreviation": short})
        for riding in data["circonscriptions"]:
            label = riding["nomCirconscription"]
            if riding.get("nbBureauComplete", 0) != riding.get("nbBureauTotal", 0):
                raise ValueError(f"{day} {label}: polls still out")
            if general:
                number = numbers[scheme, slug(label)] = riding["numeroCirconscription"]
            elif (scheme, slug(label)) in numbers:
                number = numbers[scheme, slug(label)]
            else:
                raise ValueError(f"{day} {label}: no division of that name on the {scheme} map")
            area = str(number) if scheme is None else f"{scheme}:{number}"
            ridings.append(
                {
                    "date": day,
                    "riding": number,
                    "area": area,
                    "name": label,
                    "registered": riding["nbElecteurInscrit"],
                    "rejected": riding["nbVoteRejete"],
                    "valid": riding["nbVoteValide"],
                }
            )
            votes = [c["nbVoteTotal"] for c in riding["candidats"]]
            if sum(votes) != riding["nbVoteValide"] and (day, label) != (date(1966, 6, 5), "Chambly"):
                raise ValueError(f"{day} {label}: candidates do not sum to valid votes")
            if votes.count(max(votes)) != 1:
                raise ValueError(f"{day} {label}: tie for first")
            for index, c in enumerate(riding["candidats"]):
                number_party = c.get("numeroPartiPolitique")
                name = (
                    by_number[number_party]
                    if number_party is not None
                    else by_abbreviation[c["abreviationPartiPolitique"]]
                )
                candidates.append(
                    {
                        "date": day,
                        "riding": number,
                        "candidate": str(c["numeroCandidat"]) if "numeroCandidat" in c else f"{day}:{number}:{index}",
                        "first": c["prenom"].strip(),
                        "family": c["nom"].strip(),
                        "party": party_key(name, number_party, day) if name else None,
                        "label": None if name else c["abreviationPartiPolitique"],
                        "votes": c["nbVoteTotal"],
                        "elected": c["nbVoteTotal"] == max(votes),
                    }
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
    pl.DataFrame(candidates, infer_schema_length=None).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(parties, infer_schema_length=None).write_parquet(PARSED / "parties.parquet")
    shapes.write_parquet(PARSED / "shapes.parquet")
    print(
        f"{len(RESULTS)} elections, {len(ridings)} contests, {len(candidates)} candidates, "
        f"{len({p['party'] for p in parties})} parties, {shapes.height} shapes"
    )


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
            summary=(
                "Quebec's general elections from 1966 to 2026 and its by-elections from 2013 to 2026, by division, "
                "from Élections Québec's open data"
            ),
            polity="CA-QC",
        )

        def name(
            entity: UUID,
            text: str,
            kind: str = "official",
            lang: str = "en",
            preferred: bool | None = None,
            valid: tuple[date, date] | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=(kind == "official") if preferred is None else preferred,
                **({"valid_from": valid[0], "valid_to": valid[1]} if valid else {}),
            )

        quebec = cs.polity("CA-QC")
        province = cs.area("ca.province", "24")
        if {quebec, province} & cs.minted:
            raise ValueError("run ca.elections first: Quebec and its province area must exist")
        cs.add("scheme", code="ca.qc", polity_id=quebec, kind="electoral_district", rank=2, noun="electoral division")

        sources: dict[date, UUID] = {}
        for day, url in RESULTS.items():
            day_names = ridings.filter(pl.col("date") == day)["name"].to_list()
            if day in GENERALS:
                title = f"Résultats des élections générales du {french(day)} (données ouvertes)"
            elif len(day_names) == 1:
                title = f"Résultats de l'élection partielle de {day_names[0]} du {french(day)} (données ouvertes)"
            else:
                title = f"Résultats des élections partielles du {french(day)} (données ouvertes)"
            sources[day] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[day],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
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
        starts = sorted(SEATS)
        for start, end in zip(starts, [*starts[1:], None], strict=True):
            cs.add("body_size", body_id=assembly, seats=SEATS[start], valid_from=start, valid_to=end)
        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=quebec, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")

        areas: dict[str, UUID] = {}
        for code, riding_name, geom, label_point in shapes.iter_rows():
            area = areas[code] = cs.area("ca.qc", code, GENERAL)
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
        map_starts = sorted(MAPS)
        map_ends = dict(zip(map_starts, [*map_starts[1:], GENERAL], strict=True))
        old = (
            ridings.filter(pl.col("date") < GENERAL)
            .sort("date")
            .group_by("area", maintain_order=True)
            .agg(pl.col("name"), pl.col("date"))
        )
        for code, labels, days in old.iter_rows():
            start = max(s for s in map_starts if s <= days[0])
            area = areas[code] = cs.area("ca.qc", code, start)
            cs.add("area", id=area, scheme_code="ca.qc")
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=map_ends[start])
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, labels[-1], "official", "fr")
            seen = {slug(labels[-1])}
            for label in labels:
                if slug(label) not in seen:
                    seen.add(slug(label))
                    name(area, label, "historical", "fr", False)

        party_ids: dict[str, UUID] = {}
        for (key,), rows in parties.sort("date").group_by("party", maintain_order=True):
            party_ids[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=party_ids[key], kind="party", polity_id=quebec)
            latest = rows.row(-1, named=True)
            french_name, _, english_name = (part.strip() for part in latest["name"].partition("/"))
            name(party_ids[key], french_name, "official", "fr")
            if english_name:
                name(party_ids[key], english_name, "official", "en", False)
            if latest["abbreviation"]:
                name(party_ids[key], latest["abbreviation"], "abbreviation")
            older = (
                rows.with_columns(french=pl.col("name").str.split("/").list.first().str.strip_chars())
                .filter(pl.col("french") != french_name)
                .group_by("french")
                .agg(first=pl.col("date").min(), last=pl.col("date").max())
                .sort("first")
            )
            for text, first, last in older.iter_rows():
                name(party_ids[key], text, "historical", "fr", False, (first, last + timedelta(days=1)))
            if key in COLORS:
                cs.add("party_color", party_id=party_ids[key], color=COLORS[key])

        for day in RESULTS:
            general = day in GENERALS
            kind = "general" if general else "by-election"
            day_ridings = ridings.filter(pl.col("date") == day)
            labels = day_ridings["name"].to_list()
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
                name(election, f"{day.year} Quebec general election")
                name(election, f"Élections générales québécoises de {day.year}", "native", "fr")
                if day == GENERAL:
                    cs.add("note", entity_id=election, kind="context", body_md=RECOUNT_NOTE)
            elif len(labels) == 1:
                name(election, f"{labels[0]} provincial by-election, {english(day)}")
                name(election, f"Élection partielle de {labels[0]} du {french(day)}", "native", "fr")
            else:
                name(election, f"{listed(labels)} provincial by-elections, {english(day)}")
                name(election, f"Élections partielles du {french(day)}", "native", "fr")
            cs.add("slug", path=f"ca/qc/{day}-{kind}", entity_id=election)

            day_candidates = candidates.filter(pl.col("date") == day)
            result_sets: dict[int, UUID] = {}
            units: dict[int, UUID] = {}
            options: dict[tuple[int, str], UUID] = {}
            for row in day_ridings.iter_rows(named=True):
                number = row["riding"]
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{number}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[row["area"]],
                    magnitude=1,
                    electoral_system_id=fptp,
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
                cs.add("slug", path=f"ca/qc/{day}-{kind}/{slug(row['name'])}", entity_id=contest)
                if (day, row["name"]) == (date(1966, 6, 5), "Chambly"):
                    cs.add("note", entity_id=contest, kind="context", body_md=CHAMBLY_NOTE)
                labels = sorted(
                    day_candidates.filter((pl.col("riding") == number) & pl.col("label").is_not_null())
                    .select(pl.format("{} {} ({})", "first", "family", "label"))
                    .to_series()
                    .to_list()
                )
                if labels:
                    cs.add("note", entity_id=contest, kind="context", body_md=unlabeled(labels))
                units[number] = cs.unit("ca.qc", f"{day}:{number}", day)
                cs.add(
                    "reporting_unit",
                    id=units[number],
                    polity_id=quebec,
                    scheme_code="ca.qc",
                    code=f"{day}:{number}",
                    area_relation="equals",
                    area_id=areas[row["area"]],
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
            for candidate in day_candidates.iter_rows(named=True):
                number = candidate["riding"]
                display = f"{candidate['first']} {candidate['family']}"
                person = cs.key(f"{SOURCE}.person", candidate["candidate"])
                cs.add("person", id=person)
                name(person, display)
                key = (number, candidate["candidate"])
                options[key] = cs.key(f"{SOURCE}.option", f"{day}:{number}:{candidate['candidate']}")
                party = party_ids[candidate["party"]] if candidate["party"] is not None else None
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
                    is_independent=party is None and candidate["label"] is None,
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
            sets = {n: str(i) for n, i in result_sets.items()}
            unit_ids = {n: str(i) for n, i in units.items()}
            frames = [
                day_ridings.select(
                    result_set_id=pl.col("riding").replace_strict(sets, return_dtype=pl.String),
                    unit_id=pl.col("riding").replace_strict(unit_ids, return_dtype=pl.String),
                    option_id=pl.lit(None, pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(measure, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col(field),
                )
                for measure, field in ((2, "registered"), (5, "rejected"))
            ]
            frames.append(
                day_candidates.select(
                    result_set_id=pl.col("riding").replace_strict(sets, return_dtype=pl.String),
                    unit_id=pl.col("riding").replace_strict(unit_ids, return_dtype=pl.String),
                    option_id=pl.struct("riding", "candidate").map_elements(
                        lambda r: str(options[r["riding"], r["candidate"]]), return_dtype=pl.String
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
            cs.frame("result_value", pl.concat(frames).select(columns))
        return cs.write()
