import io
import json
import re
import zipfile
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.departementales"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATIC = "https://static.data.gouv.fr/resources"
D15 = f"{STATIC}/elections-departementales-2015"
D21 = f"{STATIC}/elections-departementales-2021-resultats-du"
ROUNDS = {
    2015: (date(2015, 3, 22), date(2015, 3, 29)),
    2021: (date(2021, 6, 20), date(2021, 6, 27)),
}
RESULTS = {
    (2015, 1): f"{D15}-resultats-tour-1/20150323-162800/Dep_15_Resultats_T1_c.xlsx",
    (2015, 2): f"{D15}-resultats-tour-2/community/20150511-110358/Dep_15_Resultats_T2_c.xlsx",
    (2021, 1): f"{D21}-1er-tour/20210622-165908/resultats-par-niveau-can-t1-france-entiere.xlsx",
    (2021, 2): f"{D21}-2eme-tour/20210713-101000/dep-resultats-par-niveau-can-t2-france-entiere-2021-07-12-10h16.xlsx",
}
CANDIDATES = {
    (2015, 1): f"{D15}-candidatures-1er-tour/community/20150309-124441/Dep_15_Candidatures_T1_c_09_03_2015.xlsx",
    (2015, 2): f"{D15}-candidatures-2eme-tour/20150325-111447/Dep_15_Candidatures_T2.xlsx",
}
CONTOURS = f"{STATIC}/contours-des-cantons-electoraux-departementaux-2015/20150220-161605/Cartes_cantons_2015.zip"
COG = "https://www.insee.fr/fr/statistiques/fichier/7766585/v_departement_2024.csv"
LAYERS = {"CANTON_FRANCE": 27572, "Guadeloupe": 32620, "La R": 32740, "Mayotte": 32738}
TOLERANCE = 10


def fetch() -> None:
    for url in (*RESULTS.values(), *CANDIDATES.values(), CONTOURS, COG):
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)


FIELDS = {
    "N°Panneau": "panel",
    "Nuance": "nuance",
    "Binôme": "binome",
    "Sièges": "seat",
    "Siège": "seat",
    "Voix": "votes",
}
TOTALS = {"Inscrits": "registered", "Votants": "voters", "Blancs": "blank", "Nuls": "null", "Exprimés": "valid"}
OVERSEAS = {"ZA": "971", "ZB": "972", "ZC": "973", "ZD": "974", "ZM": "976"}
MEMBER = re.compile(r"(M\.|Mme) (.+)")


def number(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).replace(" ", "").replace(",", ".")))


def department(value: object) -> str:
    text = str(value).strip()
    return OVERSEAS.get(text, text.zfill(2))


def read(year: int, round_: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    frame = pl.read_excel(
        raw.cached(SOURCE, RESULTS[year, round_]).path,
        sheet_name="Cantons" if year == 2015 else None,
        engine="calamine",
        has_header=False,
        infer_schema_length=0,
    )
    rows = [list(row) for row in frame.iter_rows()]
    start = next(i for i, row in enumerate(rows) if "Inscrits" in row)
    header = [str(cell).strip() if cell is not None else "" for cell in rows[start]]
    first = header.index("N°Panneau")
    width = (
        header.index("N°Panneau", first + 1) - first
        if header.count("N°Panneau") > 1
        else max(i for i, cell in enumerate(header) if cell) + 1 - first
    )
    names = [FIELDS.get(cell) for cell in header[first : first + width]]
    dep_col = header.index("Code du département")
    totals: list[dict[str, object]] = []
    binomes: list[dict[str, object]] = []
    for row in rows[start + 1 :]:
        if row[dep_col] is None:
            continue
        dep = department(row[dep_col])
        canton = f"{dep}{str(row[dep_col + 2]).strip().zfill(2)}"
        entry: dict[str, object] = {
            "year": year,
            "round": round_,
            "dep": dep,
            "dep_label": str(row[dep_col + 1]).strip(),
            "canton": canton,
            "label": str(row[dep_col + 3]).strip(),
        }
        for field, key in TOTALS.items():
            entry[key] = number(row[header.index(field)])
        totals.append(entry)
        for order, offset in enumerate(range(first, len(row), width), 1):
            block = dict(zip(names, row[offset : offset + width], strict=False))
            if block.get("binome") is None or str(block["binome"]).strip() == "":
                continue
            binomes.append(
                {
                    "year": year,
                    "round": round_,
                    "canton": canton,
                    "order": order,
                    "panel": number(block["panel"]),
                    "nuance": str(block["nuance"]).strip(),
                    "binome": " ".join(str(block["binome"]).split()),
                    "votes": number(block["votes"]),
                    "elected": str(block.get("seat") or "").strip() == "Elus",
                }
            )
    return totals, binomes


def contours() -> pl.DataFrame:
    features: list[tuple[str, int, str, str | None]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, CONTOURS).path) as archive:
        for prefix, srid in LAYERS.items():
            stem = next(n[:-4] for n in archive.namelist() if n.startswith(prefix) and n.endswith("_region.shp"))
            shp, shx, dbf = (io.BytesIO(archive.read(f"{stem}.{ext}")) for ext in ("shp", "shx", "dbf"))
            reader = shapefile.Reader(shp=shp, shx=shx, dbf=dbf, encoding="latin-1")
            for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                if record is None or shape is None:
                    continue
                if prefix == "Mayotte":
                    code = f"976{str(record[0])[-2:]}"
                else:
                    code = f"{department(record['DEP'])}{record['CT']}"
                rings: tuple[list[list[list[list[float]]]], list[list[list[list[float]]]]] = ([], [])
                for start, end in zip(shape.parts, [*shape.parts[1:], len(shape.points)], strict=True):
                    ring = [[float(x), float(y)] for x, y, *_ in shape.points[start:end]]
                    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairwise(ring))
                    rings[area > 0].append([ring])
                if not rings[0]:
                    rings = (rings[1], [])
                outer, holes = (json.dumps({"type": "MultiPolygon", "coordinates": part}) for part in rings)
                features.append((code, srid, outer, holes if rings[1] else None))
    with connect() as conn:
        conn.execute("create temp table feature (code text, srid int, shell text, holes text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (code, srid, shell, holes) from stdin") as copy:
            for row in features:
                copy.write_row(row)
        rows = conn.execute(
            """with cleaned as (
                 select code, srid, st_setsrid(st_coverageclean(geom) over (partition by left(code, -2)), srid) as geom
                 from (select code, srid,
                              st_multi(st_collectionextract(st_makevalid(st_difference(
                                st_union(st_makevalid(st_setsrid(st_geomfromgeojson(shell), srid))),
                                coalesce(st_union(st_makevalid(st_setsrid(st_geomfromgeojson(holes), srid))),
                                         st_setsrid('GEOMETRYCOLLECTION EMPTY'::geometry, srid)))), 3)) as geom
                       from feature group by code, srid) features
               ), simplified as (
                 select code, srid, st_setsrid(st_coveragesimplify(geom, %s) over (partition by left(code, -2)), srid) as geom
                 from cleaned
               )
               select code, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from simplified""",
            [TOLERANCE],
        ).fetchall()
    return pl.DataFrame(rows, schema={"canton": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row")


def parse() -> None:
    totals: list[dict[str, object]] = []
    binomes: list[dict[str, object]] = []
    for year, round_ in RESULTS:
        found_totals, found_binomes = read(year, round_)
        totals += found_totals
        binomes += found_binomes
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(binomes, infer_schema_length=None).write_parquet(PARSED / "binomes.parquet")
    shapes = contours()
    if shapes.height != 2054 or shapes["canton"].n_unique() != 2054 or shapes["geom"].null_count():
        raise ValueError(f"expected 2,054 canton shapes, got {shapes.height}")
    shapes.write_parquet(PARSED / "cantons.parquet")


REDISTRICTING = date(2015, 3, 22)
CORSICA = date(2018, 1, 1)
ALSACE = date(2021, 1, 1)
LICENSE = ("Licence Ouverte v1.0", "https://www.etalab.gouv.fr/licence-ouverte-open-licence")
DATASETS = {
    2015: ("Élections départementales 2015", "https://www.data.gouv.fr/datasets/55103071c751df04f9882845"),
    2021: ("Élections départementales 2021", "https://www.data.gouv.fr/datasets/60d1fa73986a6e7da8017808"),
}
ARTICLES = {"0": "de ", "1": "d'", "2": "du ", "3": "de la ", "4": "des ", "5": "de l'"}
ENGLISH = {
    "Corse-du-Sud": "Corse-du-Sud",
    "Haute-Corse": "Haute-Corse",
    "La Réunion": "Réunion",
}
NUANCES = {
    "BC-EXG": "Binôme d'extrême gauche",
    "BC-FG": "Binôme du Front de gauche",
    "BC-PG": "Binôme du Parti de gauche",
    "BC-COM": "Binôme du Parti communiste français",
    "BC-FI": "Binôme de La France insoumise",
    "BC-SOC": "Binôme du Parti socialiste",
    "BC-RDG": "Binôme du Parti radical de gauche",
    "BC-UG": "Binôme d'union de la gauche",
    "BC-UGE": "Binôme d'union de la gauche et des écologistes",
    "BC-UCG": "Binôme d'union du centre et de la gauche",
    "BC-DVG": "Binôme divers gauche",
    "BC-VEC": "Binôme Europe Écologie-Les Verts",
    "BC-ECO": "Binôme écologiste",
    "BC-REG": "Binôme régionaliste",
    "BC-GJ": "Binôme Gilets jaunes",
    "BC-DIV": "Binôme divers",
    "BC-REM": "Binôme La République en marche",
    "BC-MDM": "Binôme du MoDem",
    "BC-UDI": "Binôme de l'Union des démocrates et indépendants",
    "BC-UC": "Binôme d'union du centre",
    "BC-DVC": "Binôme divers centre",
    "BC-UCD": "Binôme d'union du centre et de la droite",
    "BC-UMP": "Binôme de l'Union pour un mouvement populaire",
    "BC-LR": "Binôme Les Républicains",
    "BC-UD": "Binôme d'union de la droite",
    "BC-DVD": "Binôme divers droite",
    "BC-DLF": "Binôme Debout la France",
    "BC-DSV": "Binôme droite souverainiste",
    "BC-FN": "Binôme du Front national",
    "BC-RN": "Binôme du Rassemblement national",
    "BC-UXD": "Binôme d'union de l'extrême droite",
    "BC-EXD": "Binôme d'extrême droite",
}
SYSTEM_NOTE = (
    "Since 2015 each canton elects a pair of councillors, one woman and one man, who stand together as a binôme on "
    "one ballot, each with a substitute of the same sex. A binôme is elected in the first round if it wins an "
    "absolute majority of the valid votes and a number of votes at least equal to a quarter of the registered "
    "voters. Otherwise a second round is held a week later among the binômes that won at least 12.5 percent of the "
    "registered voters (the top two if fewer qualify), and the binôme with the most votes is elected. Both members "
    "sit on the departmental council for six years."
)
METHOD = (
    "Results by canton from the Interior Ministry's results file. Votes are for binômes: each ballot elects a pair, "
    "so the votes add up to the valid ballots."
)
ROUND_ONE_NOTE = (
    "No binôme won both an absolute majority of the valid votes and a quarter of the registered voters, so a "
    "second round was held. Binômes that stood again are marked as advancing; binômes that won at least 12.5 "
    "percent of the registered voters but did not stand again are marked as withdrawn."
)
MISSING_NOTE = (
    "The leading binôme won an absolute majority of the valid votes but fewer votes than a quarter of the "
    "registered voters, so a second round was due. The Interior Ministry's second-round files do not include this "
    "canton, so no outcome is recorded."
)


def members(binome: str) -> list[tuple[str, str, str | None]]:
    people: list[tuple[str, str, str | None]] = []
    for part in binome.split(" et "):
        match = MEMBER.fullmatch(part.strip())
        if match is None:
            raise ValueError(f"binôme member not understood: {binome}")
        words = match.group(2).split()
        split = next((i for i, word in enumerate(words) if i and any(c.islower() for c in word)), len(words))
        people.append(
            ("female" if match.group(1) == "Mme" else "male", " ".join(words[:split]), " ".join(words[split:]) or None)
        )
    if len(people) != 2:
        raise ValueError(f"binôme of {len(people)}: {binome}")
    return people


def emit() -> Path:
    totals = pl.read_parquet(PARSED / "totals.parquet")
    binomes = pl.read_parquet(PARSED / "binomes.parquet")
    shapes = {row["canton"]: row for row in pl.read_parquet(PARSED / "cantons.parquet").iter_rows(named=True)}
    cog = {
        row["DEP"]: row
        for row in pl.read_csv(raw.cached(SOURCE, COG).path, infer_schema_length=0).iter_rows(named=True)
    }
    sums = binomes.group_by("year", "round", "canton").agg(pl.col("votes").sum())
    off = totals.join(sums, on=["year", "round", "canton"]).filter(pl.col("votes") != pl.col("valid"))
    if off.height:
        raise ValueError(f"binôme votes do not add up to valid votes: {off.to_dicts()}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "French departmental elections of 2015 and 2021, both rounds, by canton, with each binôme and the "
                "elected pairs, from the Interior Ministry's results files, and the 2015 cantons with their contours"
            ),
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        france = cs.polity("FR")
        sources: dict[int, UUID] = {}
        for year, (title, url) in DATASETS.items():
            sources[year] = cs.source("Ministère de l'Intérieur", title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher="Ministère de l'Intérieur",
                title=title,
                url=url,
                license=LICENSE[0],
                license_url=LICENSE[1],
            )
            for (file_year, _), file_url in [*RESULTS.items(), *CANDIDATES.items()]:
                if file_year == year:
                    cs.artifact(raw.cached(SOURCE, file_url), sources[year])
        contours_source = cs.source("Ministère de l'Intérieur", "Contours des cantons électoraux départementaux 2015")
        cs.add(
            "source",
            id=contours_source,
            kind="official_body",
            publisher="Ministère de l'Intérieur",
            title="Contours des cantons électoraux départementaux 2015",
            url="https://www.data.gouv.fr/datasets/54e74d51c751df375a467389",
            license=LICENSE[0],
            license_url=LICENSE[1],
        )
        cs.artifact(raw.cached(SOURCE, CONTOURS), contours_source)
        cog_source = cs.source(
            "Institut national de la statistique et des études économiques",
            "Code officiel géographique 2024: départements",
        )
        cs.add(
            "source",
            id=cog_source,
            kind="official_body",
            publisher="Institut national de la statistique et des études économiques",
            title="Code officiel géographique 2024: départements",
            url="https://www.insee.fr/fr/information/7766585",
            license="Licence Ouverte v2.0",
            license_url=LICENSE[1],
        )
        cs.artifact(raw.cached(SOURCE, COG), cog_source)

        cs.add("scheme", code="fr.canton", polity_id=france, kind="electoral_district", rank=2, noun="canton")
        cs.add("boundary_set", scheme_code="fr.canton", vintage="2015", valid_from=REDISTRICTING, as_of=REDISTRICTING)
        labels: dict[str, str] = {}
        for row in totals.filter(pl.col("round") == 1).sort("year").iter_rows(named=True):
            labels.setdefault(str(row["canton"]), str(row["label"]))
        latest = {
            str(row["canton"]): str(row["label"])
            for row in totals.filter(pl.col("round") == 1).sort("year").iter_rows(named=True)
        }
        departments = {str(code): str(dep) for code, dep in totals.select("canton", "dep").unique().iter_rows()}
        missing = sorted(set(labels) - set(shapes))
        if missing:
            raise ValueError(f"no contour for cantons {missing}")
        areas: dict[str, UUID] = {}
        units: dict[str, UUID] = {}
        for code in sorted(labels):
            dep = departments[code]
            areas[code] = cs.area("fr.canton", code)
            cs.add("area", id=areas[code], scheme_code="fr.canton")
            cs.add("area_code", area_id=areas[code], code=code, valid_from=REDISTRICTING)
            cs.add(
                "area_parent",
                area_id=areas[code],
                parent_id=cs.area("fr.departement", dep, date(2024, 1, 1)),
                valid_from=REDISTRICTING,
            )
            name(areas[code], latest[code])
            if labels[code] != latest[code]:
                name(areas[code], labels[code], "fr", "historical")
            cs.add(
                "area_geometry",
                area_id=areas[code],
                valid_from=REDISTRICTING,
                geom=shapes[code]["geom"],
                label_point=shapes[code]["label_point"],
                quality="official",
                source_id=contours_source,
            )
            units[code] = cs.unit("fr.canton", code, REDISTRICTING)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=france,
                scheme_code="fr.canton",
                code=code,
                area_relation="equals",
                area_id=areas[code],
                kind="standard",
                valid_from=REDISTRICTING,
            )
            name(units[code], latest[code])

        corsica = cs.key("fr.regionales.polity", "94")
        polities: dict[str, UUID] = {}
        paths: dict[str, str] = {}
        bodies: dict[str, UUID] = {}
        for dep in sorted(set(departments.values())):
            entry = cog[dep]
            label = str(entry["LIBELLE"])
            article = ARTICLES[str(entry["TNCC"])]
            of = f"{article}{entry['NCCENR']}"
            polities[dep] = cs.key(f"{SOURCE}.polity", dep)
            cs.add("polity", id=polities[dep], kind="county", iso_code=None)
            name(polities[dep], label)
            ending = CORSICA if dep in ("2A", "2B") else ALSACE if dep in ("67", "68") else None
            cs.add("polity_parent", polity_id=polities[dep], parent_id=france, valid_from=None, valid_to=ending)
            cs.add(
                "polity_territory",
                polity_id=polities[dep],
                area_id=cs.area("fr.departement", dep, date(2024, 1, 1)),
                valid_from=None,
                valid_to=ending,
            )
            paths[dep] = f"fr/departement-{slug(label)}"
            cs.add("slug", path=paths[dep], entity_id=polities[dep])
            bodies[dep] = cs.key(f"{SOURCE}.body", dep)
            cs.add("body", id=bodies[dep], polity_id=polities[dep], kind="council")
            name(bodies[dep], f"Conseil départemental {of}", "fr", "official")
            name(bodies[dep], f"Departmental Council of {ENGLISH.get(label, label)}", "en", "english")
            if dep in ("2A", "2B"):
                cs.add(
                    "polity_relation",
                    id=uuid5(NAMESPACE, f"polity_relation\x1f{polities[dep]}\x1fmerged_into\x1f{corsica}"),
                    from_polity_id=polities[dep],
                    to_polity_id=corsica,
                    kind="merged_into",
                    date=CORSICA,
                )
        alsace = cs.key(f"{SOURCE}.polity", "6AE")
        cs.add("polity", id=alsace, kind="county", iso_code=None)
        name(alsace, "Collectivité européenne d'Alsace")
        name(alsace, "European Collectivity of Alsace", "en", "english")
        cs.add("polity_parent", polity_id=alsace, parent_id=france, valid_from=ALSACE, valid_to=None)
        cs.add("slug", path="fr/collectivite-europeenne-d-alsace", entity_id=alsace)
        alsace_body = cs.key(f"{SOURCE}.body", "6AE")
        cs.add("body", id=alsace_body, polity_id=alsace, kind="council")
        name(alsace_body, "Conseil de la Collectivité européenne d'Alsace", "fr", "official")
        name(alsace_body, "Council of the European Collectivity of Alsace", "en", "english")
        for dep in ("67", "68"):
            cs.add(
                "polity_relation",
                id=uuid5(NAMESPACE, f"polity_relation\x1f{polities[dep]}\x1fmerged_into\x1f{alsace}"),
                from_polity_id=polities[dep],
                to_polity_id=alsace,
                kind="merged_into",
                date=ALSACE,
            )

        system = cs.key(f"{SOURCE}.electoral_system", "binomial-two-round")
        cs.add("electoral_system", id=system, polity_id=france, rules={"formula": "two_round"}, rules_version=1)
        name(system, "French departmental binomial two-round system", "en")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        nuances: dict[tuple[int, str], UUID] = {}
        for year in ROUNDS:
            for code in sorted(binomes.filter(pl.col("year") == year)["nuance"].unique()):
                nuances[year, code] = cs.key(f"{SOURCE}.nuance", f"{year}:{code}")
                cs.add("party", id=nuances[year, code], kind="nuance", polity_id=france)
                name(nuances[year, code], NUANCES[code])
                name(nuances[year, code], code, "fr", "abbreviation")

        persons: dict[str, UUID] = {}
        values: list[dict[str, object]] = []
        sizes: dict[UUID, list[tuple[int, date]]] = {}
        for year, days in ROUNDS.items():
            umbrella = cs.key(f"{SOURCE}.election", str(year))
            cs.add(
                "election",
                id=umbrella,
                polity_id=france,
                kind="local",
                date_from=days[0],
                date_to=days[1],
                status="held",
            )
            name(umbrella, f"{year} French departmental elections", "en")
            name(umbrella, f"Élections départementales de {year}", "fr", "native")
            cs.add("slug", path=f"fr/{days[0]}-departmental", entity_id=umbrella)
            year_totals = totals.filter(pl.col("year") == year)
            year_binomes = binomes.filter(pl.col("year") == year)
            for dep in sorted(year_totals["dep"].unique()):
                merged = year == 2021 and dep in ("67", "68")
                polity = alsace if merged else polities[dep]
                body = alsace_body if merged else bodies[dep]
                place = "Alsace" if merged else ENGLISH.get(str(cog[dep]["LIBELLE"]), str(cog[dep]["LIBELLE"]))
                path = "fr/collectivite-europeenne-d-alsace" if merged else paths[dep]
                election_key = f"{year}:{'6AE' if merged else dep}"
                cantons = year_totals.filter((pl.col("dep") == dep) & (pl.col("round") == 1))["canton"].to_list()
                sizes.setdefault(body, [])
                if not merged or dep == "67":
                    count = (
                        year_totals.filter(pl.col("dep").is_in(["67", "68"]) & (pl.col("round") == 1)).height
                        if merged
                        else len(cantons)
                    )
                    sizes[body].append((2 * count, days[0]))
                elections: dict[int, UUID] = {}
                for round_ in (1, 2):
                    if not year_totals.filter((pl.col("dep") == dep) & (pl.col("round") == round_)).height:
                        continue
                    day = days[round_ - 1]
                    elections[round_] = cs.key(f"{SOURCE}.election", f"{election_key}:{round_}")
                    if merged and dep == "68":
                        continue
                    cs.add(
                        "election",
                        id=elections[round_],
                        polity_id=polity,
                        parent_id=umbrella if round_ == 1 else elections[1],
                        kind="general" if round_ == 1 else "runoff",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    suffix = ", second round" if round_ == 2 else ""
                    name(elections[round_], f"{year} {place} departmental election{suffix}", "en")
                    cs.add(
                        "slug",
                        path=f"{path}/{day}-{'general' if round_ == 1 else 'runoff'}",
                        entity_id=elections[round_],
                    )
                for code in cantons:
                    label = latest[code]
                    rounds = {
                        round_: year_binomes.filter((pl.col("canton") == code) & (pl.col("round") == round_)).sort(
                            "order"
                        )
                        for round_ in (1, 2)
                    }
                    held = [round_ for round_ in (1, 2) if rounds[round_].height]
                    decisive = 2 if 2 in held else 1 if rounds[1]["elected"].any() else None
                    previous: UUID | None = None
                    for round_ in held:
                        day = days[round_ - 1]
                        total = year_totals.filter((pl.col("canton") == code) & (pl.col("round") == round_)).row(
                            0, named=True
                        )
                        entries = rounds[round_]
                        if round_ == decisive and entries["elected"].sum() != 1:
                            raise ValueError(f"{year} {code} round {round_}: {entries['elected'].sum()} elected")
                        contest_key = f"{year}:{code}:{round_}"
                        contest = cs.key(f"{SOURCE}.contest", contest_key)
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=elections[round_],
                            kind="body_seats",
                            body_id=body,
                            district_area_id=areas[code],
                            magnitude=2,
                            round_number=round_,
                            previous_round_id=previous,
                            electoral_system_id=system,
                            default_result_kind="counted",
                        )
                        round_word = "first" if round_ == 1 else "second"
                        name(contest, f"{label} canton, {round_word} round", "en")
                        cs.add(
                            "slug",
                            path=f"{path}/{day}-{'general' if round_ == 1 else 'runoff'}/{slug(label)}",
                            entity_id=contest,
                        )
                        previous = contest
                        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                        cs.add(
                            "result_set",
                            id=result_set,
                            election_id=elections[round_],
                            contest_id=contest,
                            stage="official",
                            kind="counted",
                            status="active",
                            source_id=sources[year],
                            is_default=True,
                            completeness=1,
                        )
                        cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
                        if round_ == 1 and decisive == 2:
                            cs.add("note", entity_id=result_set, kind="context", body_md=ROUND_ONE_NOTE)
                        if decisive is None:
                            cs.add("note", entity_id=result_set, kind="caveat", body_md=MISSING_NOTE)
                        registered = int(total["registered"])
                        runoff = {
                            (surname, forename)
                            for entry in rounds[2].iter_rows(named=True)
                            for _, surname, forename in members(entry["binome"])
                        }
                        qualified = {
                            entry["binome"]
                            for entry in entries.iter_rows(named=True)
                            if entry["votes"] * 8 >= registered
                        }
                        if len(qualified) < 2:
                            qualified |= set(entries.sort("votes", descending=True).head(2)["binome"])
                        for field, measure in (
                            ("registered", 2),
                            ("voters", 14),
                            ("valid", 4),
                            ("null", 5),
                            ("blank", 6),
                        ):
                            values.append(
                                {
                                    "result_set_id": str(result_set),
                                    "unit_id": str(units[code]),
                                    "option_id": None,
                                    "measure": measure,
                                    "value": total[field],
                                }
                            )
                        for entry in entries.iter_rows(named=True):
                            panel = int(entry["panel"])
                            option = cs.key(f"{SOURCE}.option", f"{contest_key}:{panel}")
                            cs.add(
                                "contest_option",
                                id=option,
                                contest_id=contest,
                                kind="ticket",
                                party_id=nuances[year, entry["nuance"]],
                                ballot_position=panel,
                                ballot_label=entry["binome"],
                            )
                            for position, (gender, surname, forename) in enumerate(members(entry["binome"]), 1):
                                person_key = f"{year}:{code}:{surname}:{forename}"
                                if person_key not in persons:
                                    persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                                    cs.add("person", id=persons[person_key], gender=gender)
                                    name(persons[person_key], person_name(forename, surname))
                                cs.add(
                                    "candidacy",
                                    id=cs.key(f"{SOURCE}.candidacy", f"{contest_key}:{panel}:{position}"),
                                    person_id=persons[person_key],
                                    option_id=option,
                                    role="head" if position == 1 else "running_mate",
                                    list_position=position,
                                    ballot_name=" ".join(part for part in (surname, forename) if part),
                                    member_of_party_id=None,
                                    is_independent=False,
                                )
                            if decisive is None:
                                kind: str | None = None
                            elif round_ == decisive:
                                kind = "elected" if entry["elected"] else "lost"
                            elif any(
                                (surname, forename) in runoff for _, surname, forename in members(entry["binome"])
                            ):
                                kind = "advanced"
                            elif entry["binome"] in qualified:
                                kind = "withdrew"
                            else:
                                kind = "eliminated"
                            if kind is not None:
                                cs.add(
                                    "outcome",
                                    result_set_id=result_set,
                                    option_id=option,
                                    kind=kind,
                                    seats=2 if kind == "elected" else None,
                                )
                            values.append(
                                {
                                    "result_set_id": str(result_set),
                                    "unit_id": str(units[code]),
                                    "option_id": str(option),
                                    "measure": 1,
                                    "value": entry["votes"],
                                }
                            )
        for body, history in sizes.items():
            for index, (seats, start) in enumerate(history):
                end = history[index + 1][1] if index + 1 < len(history) else None
                if end is None and body in (bodies.get("2A"), bodies.get("2B")):
                    end = CORSICA
                if end is None and body in (bodies.get("67"), bodies.get("68")):
                    end = ALSACE
                cs.add("body_size", body_id=body, seats=seats, valid_from=start, valid_to=end)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema_overrides={"value": pl.Int64}, infer_schema_length=None).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
