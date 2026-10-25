import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.fr.names import person_name
from ea.store import DATA, connect

SOURCE = "fr.interieur"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DATASET = (
    "https://static.data.gouv.fr/resources/elections-legislatives-des-30-juin-et-7-juillet-2024-resultats-definitifs"
)
FILES = {
    (1, "circ"): f"{DATASET}-du-1er-tour/20240710-171413/resultats-definitifs-par-circonscriptions-legislatives.csv",
    (1, "station"): f"{DATASET}-du-1er-tour/20240710-171445/resultats-definitifs-par-bureau-de-vote.csv",
    (2, "circ"): f"{DATASET}-du-2nd-tour/20240710-170728/resultats-definitifs-par-circonscription.csv",
    (2, "station"): f"{DATASET}-du-2nd-tour/20240710-170658/resultats-definitifs-par-bureau-de-vote.csv",
}
SHAPES = (
    "https://static.data.gouv.fr/resources/contours-geographiques-des-circonscriptions-legislatives/"
    "20240613-191449/circonscriptions-legislatives-shp.zip"
)
ROUNDS = {1: date(2024, 6, 30), 2: date(2024, 7, 7)}
CLOSED = date(2024, 7, 8)
REDISTRICTING = date(2012, 6, 10)
NUANCES = {
    "EXG": "Extrême gauche",
    "COM": "Parti communiste français",
    "FI": "La France insoumise",
    "SOC": "Parti socialiste",
    "RDG": "Parti radical de gauche",
    "VEC": "Les Écologistes",
    "DVG": "Divers gauche",
    "UG": "Union de la gauche",
    "ECO": "Écologistes",
    "REG": "Régionaliste",
    "DIV": "Divers",
    "REN": "Renaissance",
    "MDM": "Modem",
    "HOR": "Horizons",
    "ENS": "Ensemble",
    "DVC": "Divers centre",
    "UDI": "Union des démocrates et indépendants",
    "LR": "Les Républicains",
    "DVD": "Divers droite",
    "DSV": "Droite souverainiste",
    "RN": "Rassemblement national",
    "REC": "Reconquête",
    "UXD": "Union de l'extrême droite",
    "EXD": "Extrême droite",
}
PARTIES = {
    "COM": "Q192821",
    "SOC": "Q170972",
    "RDG": "Q427965",
    "VEC": "Q613786",
    "REN": "Q23731823",
    "MDM": "Q587370",
    "HOR": "Q108846587",
    "UDI": "Q82892",
    "LR": "Q20012759",
    "RN": "Q205150",
}
ALLIANCES = {"UG": ("New Popular Front", "Nouveau Front populaire"), "ENS": ("Ensemble", "Ensemble")}
SOURCE_NOTE = (
    "Candidates carry the Interior Ministry's political label (nuance), not their party. The polling-station files "
    "have no constituency column, so each station is placed in its constituency by matching its list of candidates."
)
SHAPES_NOTE = (
    "Boundaries from the 2010 redistricting, first used in 2012. There are no shapes for New Caledonia, French "
    "Polynesia, Wallis and Futuna, Saint-Martin and Saint-Barthélemy, or the 11 constituencies for French residents "
    "abroad."
)
METHODOLOGY = (
    "A candidate wins in the first round with more than half of the valid votes, provided that is at least a quarter "
    "of registered voters. Otherwise, every candidate with at least 12.5 percent of registered voters may stand in "
    "the second round, and the top two always may. The candidate with the most votes in the second round wins; in a "
    "tie, the older candidate wins."
)
ROUND_NOTE = (
    "Voters in France's territories in the Americas and in French Polynesia voted the day before, and voters abroad "
    "could also vote online during the week before. Their results are filed under this day."
)
WITHDREW = "Qualified for the second round but did not stand in it."
MERGED = (
    "Four polling stations in Montbéliard share the code 0 in the Interior Ministry's files. Their results can't be "
    "told apart, so they are combined here as one unit."
)
SHAPE_DEPARTMENTS = {"ZA": "971", "ZB": "972", "ZC": "973", "ZD": "974", "ZM": "976", "ZS": "975"}
ACCOUNTING = {"Inscrits": 2, "Votants": 14, "Exprimés": 4, "Blancs": 6, "Nuls": 5}
CANDIDATE = {
    "Numéro de panneau": "panel",
    "Nuance candidat": "nuance",
    "Nom candidat": "surname",
    "Prénom candidat": "forename",
    "Sexe candidat": "sex",
    "Voix": "votes",
    "Elu": "elected",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, SHAPES, suffix=".zip")


def parse() -> None:
    accounting: list[pl.DataFrame] = []
    candidates: list[pl.DataFrame] = []
    labels: list[pl.DataFrame] = []
    for (number, grain), url in FILES.items():
        frame = pl.read_csv(raw.cached(SOURCE, url).path, separator=";", infer_schema=False).with_columns(
            round=pl.lit(number),
            department=pl.col("Code département").str.zfill(2),
        )
        if grain == "circ":
            frame = frame.with_columns(
                unit=pl.col("department") + pl.col("Code circonscription législative").str.slice(-2).str.zfill(2)
            )
            labels.append(
                frame.select(
                    circ="unit",
                    department="department",
                    department_label="Libellé département",
                    label="Libellé circonscription législative",
                )
            )
        else:
            frame = frame.with_columns(
                commune=pl.when(pl.col("Code commune").str.contains(r"^\d+$"))
                .then(pl.col("Code commune").str.zfill(5))
                .otherwise(pl.col("Code commune")),
                commune_label=pl.col("Libellé commune"),
            ).with_columns(unit=pl.col("commune") + ":" + pl.col("Code BV").str.zfill(4))
            labels.append(frame.select("round", "unit", "department", "commune", "commune_label"))
        keys = ["round", "department", "unit"]
        accounting.append(
            frame.unpivot(on=list(ACCOUNTING), index=keys, variable_name="field").select(
                *keys, pl.lit(grain).alias("grain"), measure=pl.col("field").replace_strict(ACCOUNTING), value="value"
            )
        )
        slots = sorted(
            {int(column.rsplit(" ", 1)[1]) for column in frame.columns if column.startswith("Nom candidat ")}
        )
        candidates.append(
            pl.concat(
                [
                    frame.select(
                        *keys, *(pl.col(f"{column} {slot}").alias(field) for column, field in CANDIDATE.items())
                    )
                    for slot in slots
                ]
            )
            .filter(pl.col("surname").is_not_null() & (pl.col("surname") != ""))
            .with_columns(pl.lit(grain).alias("grain"))
        )
    values = pl.concat(accounting).with_columns(pl.col("value").cast(pl.Int64))
    standing = pl.concat(candidates).with_columns(
        pl.col("panel").cast(pl.Int64), pl.col("votes").cast(pl.Int64), elected=pl.col("elected") == "élu"
    )
    circs = pl.concat([frame for frame in labels if "label" in frame.columns]).unique("circ").sort("circ")
    if circs.height != 577:
        raise ValueError(f"expected 577 constituencies, got {circs.height}")

    slates = standing.group_by("round", "grain", "department", "unit").agg(
        slate=pl.concat_str("panel", pl.lit("|"), "surname", pl.lit("|"), "forename").unique().sort().str.join(";")
    )
    by_circ = slates.filter(pl.col("grain") == "circ").select("round", "department", "slate", circ="unit")
    if by_circ.select("round", "department", "slate").is_duplicated().any():
        raise ValueError("two constituencies in one department share a candidate slate")
    membership = (
        slates.filter(pl.col("grain") == "station")
        .join(by_circ, on=["round", "department", "slate"], how="left")
        .select("round", "unit", "circ")
    )
    unassigned = membership.filter(pl.col("circ").is_null())
    conflicting = membership.unique(["unit", "circ"]).filter(pl.col("unit").is_duplicated())
    if unassigned.height or conflicting.height:
        raise ValueError(f"stations without one constituency: {unassigned.head(3)} {conflicting.head(3)}")
    membership = membership.unique("unit").select("unit", "circ")

    station_values = pl.concat(
        [
            values.filter(pl.col("grain") == "station").select(
                "round", "unit", "measure", "value", panel=pl.lit(None, pl.Int64)
            ),
            standing.filter(pl.col("grain") == "station").select(
                "round", "unit", "panel", pl.lit(1, pl.Int64).alias("measure"), value="votes"
            ),
        ],
        how="diagonal",
    )
    station_values = (
        station_values.group_by("round", "unit", "measure", "panel")
        .agg(pl.col("value").sum())
        .join(membership, on="unit")
    )
    circ_values = pl.concat(
        [
            values.filter(pl.col("grain") == "circ").select(
                "round", "measure", "value", circ="unit", panel=pl.lit(None, pl.Int64)
            ),
            standing.filter(pl.col("grain") == "circ").select(
                "round", "panel", pl.lit(1, pl.Int64).alias("measure"), value="votes", circ="unit"
            ),
        ],
        how="diagonal",
    )
    keys = ["round", "circ", "measure", "panel"]
    compared = circ_values.join(
        station_values.group_by(keys).agg(pl.col("value").sum().alias("stations")),
        on=keys,
        how="left",
        nulls_equal=True,
    )
    short = compared.filter(pl.col("value").ne_missing(pl.col("stations")))
    if short.height:
        raise ValueError(f"stations do not add up to the constituency totals: {short.head(5)}")

    first = standing.filter((pl.col("grain") == "circ") & (pl.col("round") == 1)).rename({"unit": "circ"})
    second = standing.filter((pl.col("grain") == "circ") & (pl.col("round") == 2)).rename({"unit": "circ"})
    totals = values.filter((pl.col("grain") == "circ") & (pl.col("round") == 1)).pivot(
        "measure", index="unit", values="value"
    )
    first = first.join(totals.select(circ="unit", registered="2", valid="4"), on="circ").with_columns(
        rank=pl.col("votes").rank("ordinal", descending=True).over("circ"),
        wins=(pl.col("votes") * 2 > pl.col("valid")) & (pl.col("votes") * 4 >= pl.col("registered")),
    )
    won = first.filter(pl.col("wins"))
    qualified = first.filter(
        ~pl.col("circ").is_in(won["circ"].implode())
        & ((pl.col("votes") * 8 >= pl.col("registered")) | (pl.col("rank") <= 2))
    )
    runoff = second.select("circ", "panel", "surname", "forename")
    problems = {
        "first-round winners differ from the rule": first.filter(pl.col("wins") != pl.col("elected")),
        "second-round candidates who did not qualify": runoff.join(
            qualified, on=["circ", "panel", "surname", "forename"], how="anti"
        ),
        "second rounds where the winner is not the sole top vote-getter": second.with_columns(
            top=pl.col("votes") == pl.col("votes").max().over("circ"),
            ties=(pl.col("votes") == pl.col("votes").max().over("circ")).sum().over("circ"),
        ).filter((pl.col("top") != pl.col("elected")) | (pl.col("ties") > 1)),
        "constituencies without exactly one winner": pl.concat(
            [won.select("circ"), second.filter(pl.col("elected")).select("circ")]
        )
        .group_by("circ")
        .len()
        .filter(pl.col("len") != 1),
    }
    failures = [f"{label}: {frame.head(3)}" for label, frame in problems.items() if frame.height]
    if won.height + second["circ"].n_unique() != 577:
        failures.append(f"{won.height} first-round wins and {second['circ'].n_unique()} second rounds")
    if failures:
        raise ValueError("\n".join(failures))
    outcomes = pl.concat(
        [
            won.select("round", "circ", "panel", outcome=pl.lit("elected")),
            qualified.join(
                runoff.select("circ", "panel").with_columns(advanced=pl.lit(True)), on=["circ", "panel"], how="left"
            ).select(
                "round",
                "circ",
                "panel",
                outcome=pl.when(pl.col("advanced")).then(pl.lit("advanced")).otherwise(pl.lit("withdrew")),
            ),
            second.filter(pl.col("elected")).select("round", "circ", "panel", outcome=pl.lit("elected")),
        ]
    )

    with zipfile.ZipFile(raw.cached(SOURCE, SHAPES).path) as archive:
        parts = {
            member.rsplit(".", 1)[1].lower(): io.BytesIO(archive.read(member))
            for member in archive.namelist()
            if member.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    shapes = [
        (
            SHAPE_DEPARTMENTS.get(str(record["codeDepart"]), str(record["codeDepart"]))
            + str(record["codeCircon"])[-2:],
            json.dumps(shape.__geo_interface__),
        )
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record is not None and shape is not None
    ]
    unknown = {code for code, _ in shapes} - set(circs["circ"])
    if unknown or len(shapes) != 559:
        raise ValueError(f"expected 559 shapes of known constituencies, got {len(shapes)} ({sorted(unknown)})")
    with connect() as conn:
        geometry = pl.DataFrame(
            conn.execute(
                """select code, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, st_multi(st_collectionextract(st_makevalid(
                                  st_setsrid(st_geomfromgeojson(geojson), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[]) as s(code, geojson)) shapes
                   order by code""",
                [[code for code, _ in shapes], [geojson for _, geojson in shapes]],
            ).fetchall(),
            schema=["circ", "geom", "label_point"],
            orient="row",
        )

    PARSED.mkdir(parents=True, exist_ok=True)
    circs.write_parquet(PARSED / "circs.parquet")
    geometry.write_parquet(PARSED / "shapes.parquet")
    stations = pl.concat([frame for frame in labels if "commune" in frame.columns])
    stations.with_columns(merged=pl.len().over("round", "unit") > 1).sort("merged", descending=True).unique(
        "unit", keep="first"
    ).drop("round").join(membership, on="unit").sort("unit").write_parquet(PARSED / "stations.parquet")
    standing.filter(pl.col("grain") == "circ").select(
        "round", "panel", "nuance", "surname", "forename", "sex", circ="unit"
    ).join(outcomes, on=["round", "circ", "panel"], how="left").write_parquet(PARSED / "candidates.parquet")
    circ_values.write_parquet(PARSED / "circ_values.parquet")
    station_values.write_parquet(PARSED / "station_values.parquet")


def emit() -> Path:
    circs = pl.read_parquet(PARSED / "circs.parquet")
    shapes = {circ: (geom, point) for circ, geom, point in pl.read_parquet(PARSED / "shapes.parquet").iter_rows()}
    stations = pl.read_parquet(PARSED / "stations.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    circ_values = pl.read_parquet(PARSED / "circ_values.parquet")
    station_values = pl.read_parquet(PARSED / "station_values.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2024 French legislative election, both rounds, by constituency and polling station",
            polity="FR",
        )

        def name(entity: UUID, text: str, lang: str = "fr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        interieur = cs.source("Ministère de l'Intérieur", "Élections législatives des 30 juin et 7 juillet 2024")
        etalab = cs.source("Etalab", "Contours géographiques des circonscriptions législatives")
        for source, publisher, title, url, notes in (
            (
                interieur,
                "Ministère de l'Intérieur",
                "Élections législatives des 30 juin et 7 juillet 2024",
                "https://www.data.gouv.fr/datasets/668b4092826ddcf70e9e62cd",
                SOURCE_NOTE,
            ),
            (
                etalab,
                "Etalab",
                "Contours géographiques des circonscriptions législatives",
                "https://www.data.gouv.fr/datasets/666b1890fcffad38a387aeba",
                SHAPES_NOTE,
            ),
        ):
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Licence Ouverte v2.0",
                license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
                notes=notes,
            )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), interieur)
        cs.artifact(raw.cached(SOURCE, SHAPES), etalab)

        france = cs.polity("FR")
        cs.add("polity", id=france, kind="sovereign_state", iso_code="FR")
        name(france, "France", "en")
        name(france, "République française", "fr", "native")
        cs.add("slug", path="fr", entity_id=france)
        cs.add("external_id", entity_id=france, scheme="wikidata", value="Q142")
        for code, rank, noun, kind in (
            ("fr.nation", 0, "country", "administrative"),
            ("fr.departement", 1, "department", "administrative"),
            ("fr.circonscription", 2, "constituency", "electoral_district"),
            ("fr.commune", 2, "commune", "administrative"),
            ("fr.bureau", 3, "polling station", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=france, kind=kind, rank=rank, noun=noun)
        nation = cs.area("fr.nation", "FR")
        cs.add("area", id=nation, scheme_code="fr.nation")
        cs.add("area_code", area_id=nation, code="FR", valid_from=None)
        name(nation, "France", "en")
        cs.add("polity_territory", polity_id=france, area_id=nation)

        departments: dict[str, UUID] = {}
        for department, label in circs.select("department", "department_label").unique().sort("department").iter_rows():
            if department == "ZZ":
                continue
            departments[department] = cs.area("fr.departement", department)
            cs.add("area", id=departments[department], scheme_code="fr.departement")
            cs.add("area_code", area_id=departments[department], code=department, valid_from=None)
            cs.add("area_parent", area_id=departments[department], parent_id=nation, valid_from=None)
            name(departments[department], label)
        areas: dict[str, UUID] = {}
        circ_units: dict[str, UUID] = {}
        current_areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
               where a.scheme_code = 'fr.circonscription' and c.valid_from = %s and c.valid_to is null
                 and not exists (select from area_code o where o.area_id = a.id and o.valid_from < %s)""",
                [REDISTRICTING, REDISTRICTING],
            ).fetchall()
        )
        for circ, department, department_label, label in circs.iter_rows():
            areas[circ] = current_areas.get(circ) or cs.area("fr.circonscription", circ)
            cs.add("area", id=areas[circ], scheme_code="fr.circonscription")
            cs.add("area_code", area_id=areas[circ], code=circ, valid_from=REDISTRICTING)
            cs.add(
                "area_parent",
                area_id=areas[circ],
                parent_id=departments.get(department, nation),
                valid_from=REDISTRICTING,
            )
            name(areas[circ], f"{department_label}, {label}")
            if circ in shapes:
                cs.add(
                    "area_geometry",
                    area_id=areas[circ],
                    valid_from=REDISTRICTING,
                    geom=shapes[circ][0],
                    label_point=shapes[circ][1],
                    quality="official",
                    source_id=etalab,
                )
            circ_units[circ] = cs.unit("fr.circonscription", circ, REDISTRICTING)
            cs.add(
                "reporting_unit",
                id=circ_units[circ],
                polity_id=france,
                scheme_code="fr.circonscription",
                code=circ,
                area_relation="equals",
                area_id=areas[circ],
                kind="standard",
                valid_from=REDISTRICTING,
            )
        cs.add(
            "boundary_set",
            scheme_code="fr.circonscription",
            vintage="2010",
            valid_from=REDISTRICTING,
            as_of=date(2024, 6, 13),
        )

        located = stations.filter(pl.col("department") != "ZZ")
        communes = located.unique("commune", keep="first").sort("commune")
        commune_ids = {code: str(cs.area("fr.commune", code)) for code in communes["commune"]}
        department_ids = {code: str(id) for code, id in departments.items()}
        commune_area = pl.col("commune").replace_strict(commune_ids)
        cs.frame("area", communes.select(id=commune_area, scheme_code=pl.lit("fr.commune")))
        cs.frame(
            "area_code",
            communes.select(area_id=commune_area, code="commune", valid_from=pl.lit(None, pl.Date)),
        )
        cs.frame(
            "area_parent",
            communes.select(
                area_id=commune_area,
                parent_id=pl.col("department").replace_strict(department_ids),
                valid_from=pl.lit(None, pl.Date),
            ),
        )
        cs.frame(
            "name",
            communes.select(
                entity_id=commune_area,
                text="commune_label",
                lang=pl.lit("fr"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )
        unit_ids = {code: str(cs.unit("fr.bureau", code)) for code in stations["unit"]}
        unit = pl.col("unit").replace_strict(unit_ids)
        abroad = pl.col("department") == "ZZ"
        cs.frame(
            "reporting_unit",
            stations.select(
                id=unit,
                polity_id=pl.lit(str(france)),
                scheme_code=pl.lit("fr.bureau"),
                code="unit",
                area_relation=pl.when(abroad).then(pl.lit("unlocated")).otherwise(pl.lit("within")),
                area_id=pl.col("commune").replace_strict(commune_ids, default=None),
                kind=pl.when(pl.col("merged"))
                .then(pl.lit("aggregate"))
                .when(abroad)
                .then(pl.lit("abroad_mission"))
                .otherwise(pl.lit("standard")),
                valid_from=pl.lit(ROUNDS[1]),
                valid_to=pl.lit(CLOSED),
            ),
        )
        cs.frame(
            "reporting_unit_area",
            stations.select(
                unit_id=unit,
                area_id=pl.col("circ").replace_strict({code: str(id) for code, id in areas.items()}),
                valid_from=pl.lit(ROUNDS[1]),
                valid_to=pl.lit(CLOSED),
            ),
        )
        cs.frame(
            "name",
            stations.select(
                entity_id=unit,
                text=pl.col("commune_label") + ", bureau " + pl.col("unit").str.split(":").list.last(),
                lang=pl.lit("fr"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )

        nuances: dict[str, UUID] = {}
        for code, label in NUANCES.items():
            nuances[code] = cs.key(f"{SOURCE}.nuance", f"{ROUNDS[1].year}:{code}")
            cs.add("party", id=nuances[code], kind="nuance", polity_id=france)
            name(nuances[code], label)
            name(nuances[code], code, "fr", "abbreviation")
        assembly = cs.key("fr.body", "national-assembly")
        cs.add("body", id=assembly, polity_id=france, kind="lower_house")
        name(assembly, "National Assembly", "en")
        name(assembly, "Assemblée nationale", "fr", "native")
        cs.add("body_size", body_id=assembly, seats=577, valid_from=date(1986, 3, 16))
        system = cs.key("fr.electoral_system", "legislative-two-round")
        cs.add("electoral_system", id=system, polity_id=france, rules={"formula": "two_round"}, rules_version=1)
        name(system, "French legislative two-round system", "en")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)
        cycle = cs.key("fr.election", f"{ROUNDS[1]}:legislative")
        cs.add(
            "election",
            id=cycle,
            polity_id=france,
            kind="general",
            date_from=ROUNDS[1],
            date_to=ROUNDS[2],
            status="held",
        )
        blocs = {code: cs.key("fr.alliance", f"{ROUNDS[1].year}:{code}") for code in ALLIANCES}
        for code, (english, french) in ALLIANCES.items():
            cs.add("alliance", id=blocs[code], kind="electoral_alliance", election_id=cycle)
            name(blocs[code], english, "en")
            name(blocs[code], french, "fr", "native")
        name(cycle, "2024 French legislative election", "en")
        name(cycle, "Élections législatives de 2024", "fr", "native")
        cs.add("slug", path=f"fr/{ROUNDS[1]}-legislative", entity_id=cycle)
        elections: dict[int, UUID] = {}
        for number, day in ROUNDS.items():
            stage = "general" if number == 1 else "runoff"
            elections[number] = cs.key("fr.election", str(day))
            cs.add(
                "election",
                id=elections[number],
                polity_id=france,
                parent_id=cycle,
                kind=stage,
                date_from=day,
                date_to=day,
                status="held",
            )
            name(
                elections[number],
                f"2024 French legislative election, {'first' if number == 1 else 'second'} round",
                "en",
            )
            cs.add("slug", path=f"fr/{day}-{stage}", entity_id=elections[number])
            cs.add("note", entity_id=elections[number], kind="context", body_md=ROUND_NOTE)

        labels = {circ: (department_label, int(circ[-2:])) for circ, _, department_label, _ in circs.iter_rows()}
        contests: dict[tuple[int, str], UUID] = {}
        result_sets: dict[str, str] = {}
        options: dict[str, str] = {}
        persons: dict[str, UUID] = {}
        for (number, circ), frame in sorted(candidates.group_by("round", "circ"), key=lambda group: group[0]):
            day = ROUNDS[number]
            department_label, ordinal = labels[circ]
            contest_key = f"{day}:{circ}"
            contests[(number, circ)] = cs.key("fr.contest", contest_key)
            cs.add(
                "contest",
                id=contests[(number, circ)],
                election_id=elections[number],
                kind="body_seats",
                body_id=assembly,
                district_area_id=areas[circ],
                magnitude=1,
                round_number=number,
                previous_round_id=contests.get((1, circ)) if number == 2 else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(
                contests[(number, circ)],
                f"{department_label}, constituency {ordinal}, {'first' if number == 1 else 'second'} round",
                "en",
            )
            cs.add(
                "slug",
                path=f"fr/{day}-{'general' if number == 1 else 'runoff'}/{slug(department_label)}-{ordinal}",
                entity_id=contests[(number, circ)],
            )
            totals = cs.key(f"{SOURCE}.result_set", contest_key)
            by_station = cs.key(f"{SOURCE}.result_set", f"{contest_key}:stations")
            for result_set, default in ((totals, True), (by_station, False)):
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[number],
                    contest_id=contests[(number, circ)],
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=interieur,
                    is_default=default,
                    completeness=1,
                )
            if totals in cs.minted:
                result_sets[f"{number}:{circ}"] = str(totals)
            if by_station in cs.minted:
                result_sets[f"{number}:{circ}:stations"] = str(by_station)
            for panel, nuance, surname, forename, sex, outcome in frame.select(
                "panel", "nuance", "surname", "forename", "sex", "outcome"
            ).iter_rows():
                person_key = f"{day.year}:{circ}:{surname}:{forename}"
                if person_key not in persons:
                    persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add("person", id=persons[person_key], gender={"FEMININ": "female", "MASCULIN": "male"}.get(sex))
                    name(persons[person_key], person_name(forename, surname))
                option = cs.key("fr.option", f"{contest_key}:{panel}")
                options[f"{number}:{circ}:{panel}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contests[(number, circ)],
                    kind="candidate",
                    party_id=nuances[nuance],
                    alliance_id=blocs.get(nuance),
                    ballot_position=panel,
                    ballot_label=f"{surname} {forename}",
                )
                cs.add(
                    "candidacy",
                    id=cs.key("fr.candidacy", f"{contest_key}:{panel}"),
                    person_id=persons[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=f"{surname} {forename}",
                    member_of_party_id=cs.key("wikidata", PARTIES[nuance]) if nuance in PARTIES else None,
                )
                if outcome is not None:
                    cs.add(
                        "outcome",
                        result_set_id=totals,
                        option_id=option,
                        kind=outcome,
                        notes=WITHDREW if outcome == "withdrew" else None,
                    )
        merged = stations.filter(pl.col("merged"))["unit"]
        for code in merged:
            cs.add("note", entity_id=UUID(unit_ids[code]), kind="caveat", body_md=MERGED)

        option = (
            pl.when(pl.col("panel").is_not_null())
            .then(pl.concat_str(pl.col("round"), pl.lit(":"), pl.col("circ"), pl.lit(":"), pl.col("panel")))
            .replace_strict(options, default=None)
        )
        fixed = {
            "channel": pl.lit(0, pl.Int16),
            "measure": pl.col("measure").cast(pl.Int16),
            "count_round": pl.lit(0, pl.Int16),
            "flags": pl.lit(0, pl.Int16),
            "value": pl.col("value"),
        }
        totals_key = pl.concat_str(pl.col("round"), pl.lit(":"), pl.col("circ"))
        cs.frame(
            "result_value",
            circ_values.filter(totals_key.is_in(list(result_sets))).select(
                result_set_id=totals_key.replace_strict(result_sets),
                unit_id=pl.col("circ").replace_strict({code: str(id) for code, id in circ_units.items()}),
                option_id=option,
                **fixed,
            ),
        )
        stations_key = totals_key + ":stations"
        cs.frame(
            "result_value",
            station_values.filter(stations_key.is_in(list(result_sets))).select(
                result_set_id=stations_key.replace_strict(result_sets),
                unit_id=unit,
                option_id=option,
                **fixed,
            ),
        )
        return cs.write()
