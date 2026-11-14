import io
import json
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "pl.prg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UNITS = "https://opendata.geoportal.gov.pl/prg/granice/00_jednostki_administracyjne.zip"
SPARQL = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": "SELECT ?item ?code WHERE { ?item wdt:P1653 ?code . FILTER NOT EXISTS { ?item wdt:P576 ?end } }",
        "format": "json",
    }
)
AS_OF = date(2026, 11, 10)
TOLERANCE = 10
PUBLISHER = "Główny Urząd Geodezji i Kartografii"
TITLE = "Państwowy Rejestr Granic: jednostki administracyjne, stan na 10 listopada 2026"
PAGE = "https://www.geoportal.gov.pl/pl/dane/panstwowy-rejestr-granic-prg/"
LICENSE = "Free for any use (Dane PRG są dostępne bezpłatnie i do dowolnego wykorzystania)"
WARSAW = "1465011"
LAYERS = {
    "wojewodztwo": "wojew",
    "powiat": "powiat",
    "gmina": "gmin",
    "dzielnica": "jednostek_ewidencyjnych",
}
VOIVODESHIPS = {
    "02": ("dolnośląskie", "Lower Silesian Voivodeship"),
    "04": ("kujawsko-pomorskie", "Kuyavian-Pomeranian Voivodeship"),
    "06": ("lubelskie", "Lublin Voivodeship"),
    "08": ("lubuskie", "Lubusz Voivodeship"),
    "10": ("łódzkie", "Łódź Voivodeship"),
    "12": ("małopolskie", "Lesser Poland Voivodeship"),
    "14": ("mazowieckie", "Masovian Voivodeship"),
    "16": ("opolskie", "Opole Voivodeship"),
    "18": ("podkarpackie", "Subcarpathian Voivodeship"),
    "20": ("podlaskie", "Podlaskie Voivodeship"),
    "22": ("pomorskie", "Pomeranian Voivodeship"),
    "24": ("śląskie", "Silesian Voivodeship"),
    "26": ("świętokrzyskie", "Świętokrzyskie Voivodeship"),
    "28": ("warmińsko-mazurskie", "Warmian-Masurian Voivodeship"),
    "30": ("wielkopolskie", "Greater Poland Voivodeship"),
    "32": ("zachodniopomorskie", "West Pomeranian Voivodeship"),
}
GMINA_KINDS = {"1": "urban", "2": "rural", "3": "urban_rural"}
SUFFIX = {"2": "gmina-wiejska", "3": "gmina-miejsko-wiejska"}


def _gmina_paths(gminy: pl.DataFrame, powiaty: dict[str, str]) -> dict[str, str]:
    paths = {
        code: f"pl/{slug(VOIVODESHIPS[code[:2]][0])}/{slug(text)}" for code, text in gminy.select("code", "name").rows()
    }
    groups: dict[str, list[str]] = {}
    for code, path in paths.items():
        groups.setdefault(path, []).append(code)
    for path, codes in groups.items():
        if len(codes) == 1:
            continue
        if len({code[:4] for code in codes}) == 1 and len({code[-1] for code in codes}) == len(codes):
            for code in codes:
                if code[-1] in SUFFIX:
                    paths[code] = f"{path}-{SUFFIX[code[-1]]}"
            continue
        for code in codes:
            paths[code] = f"{path}-{slug(powiaty[code[:4]].removeprefix('powiat '))}"
    return paths


def fetch() -> None:
    raw.fetch(SOURCE, UNITS, suffix=".zip")
    raw.fetch(SOURCE, SPARQL, suffix=".json")


def _features(archive: zipfile.ZipFile, layer: str) -> list[tuple[str, str, str]]:
    stem = next(
        name[:-4]
        for name in archive.namelist()
        if name.lower().endswith(".shp") and LAYERS[layer] in name.lower() and "granice" in name.lower()
    )
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )
    features: list[tuple[str, str, str]] = []
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        code = str(record["JPT_KOD_JE"]).replace("_", "")
        if layer == "dzielnica" and not (code.startswith("1465") and code.endswith("8")):
            continue
        features.append(
            (code, str(record["JPT_NAZWA_"]).removesuffix(" - dzielnica").strip(), json.dumps(shape.__geo_interface__))
        )
    return features


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, UNITS).path) as archive:
        gminy = _features(archive, "gmina")
        districts = _features(archive, "dzielnica")
        names = {
            layer: {code: text for code, text, _ in _features(archive, layer)} for layer in ("wojewodztwo", "powiat")
        }
    pieces = [(code, text, geom) for code, text, geom in gminy if code != WARSAW] + districts
    warsaw = next(text for code, text, _ in gminy if code == WARSAW)
    with connect() as conn:
        conn.execute(
            """create temp table shape on commit drop as
               select code, name, st_setsrid(st_coveragesimplify(geom, %s) over (), 2180) as geom
               from (select code, name,
                            st_multi(st_collectionextract(st_makevalid(st_force2d(
                              st_transform(st_setsrid(st_geomfromgeojson(geojson), 4258), 2180))), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) features""",
            [TOLERANCE, *(list(column) for column in zip(*pieces, strict=True))],
        )
        rows = conn.execute(
            """with units as (
                 select case when code like '1465__8' then 'dzielnica' else 'gmina' end as layer, code,
                        case when code like '1465__8' then %s else left(code, 4) end as parent, name, geom
                 from shape
                 union all
                 select 'gmina', %s, '1465', %s, st_setsrid(st_multi(st_union(geom, 0.01)), 2180)
                 from shape where code like '1465__8'
               ), powiaty as (
                 select 'powiat' as layer, left(code, 4) as code, min(left(code, 2)) as parent, null as name,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 2180) as geom
                 from units where layer = 'gmina' group by left(code, 4)
               ), wojewodztwa as (
                 select 'wojewodztwo' as layer, parent as code, 'PL' as parent, null as name,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 2180) as geom
                 from powiaty group by parent
               ), layers as (
                 select * from units
                 union all select * from powiaty
                 union all select * from wojewodztwa
                 union all select 'nation', 'PL', null, null, st_setsrid(st_multi(st_union(geom, 0.01)), 2180)
                 from wojewodztwa
               )
               select layer, code, parent, name, st_area(geom),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from layers""",
            [WARSAW, WARSAW, warsaw],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "parent": pl.String,
            "name": pl.String,
            "area": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    ).with_columns(
        name=pl.when(pl.col("layer") == "powiat")
        .then(pl.col("code").replace_strict(names["powiat"], default=None))
        .when(pl.col("layer") == "wojewodztwo")
        .then(pl.col("code").replace_strict(names["wojewodztwo"], default=None))
        .otherwise(pl.col("name"))
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if (
        counts["wojewodztwo"] != 16
        or counts["dzielnica"] != 18
        or counts["powiat"] != len(names["powiat"])
        or frame.filter(pl.col("layer") != "nation")["name"].null_count()
        or frame["geom"].null_count()
    ):
        raise ValueError(f"unexpected layers: {counts}")
    totals = dict(frame.group_by("layer").agg(pl.col("area").sum()).iter_rows())
    totals["dzielnica"] = frame.filter(pl.col("code") == WARSAW)["area"][0] / totals["dzielnica"]
    totals = {layer: total for layer, total in totals.items() if layer != "dzielnica"}
    if max(totals.values()) / min(totals.values()) > 1.0001:
        raise ValueError(f"layer areas disagree: {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("area").write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    items: dict[str, str] = {}
    for row in json.loads(raw.cached(SOURCE, SPARQL).path.read_text())["results"]["bindings"]:
        items.setdefault(row["code"]["value"], row["item"]["value"].rsplit("/", 1)[1])
    layers = {layer: lines.sort("code") for (layer,), lines in frame.group_by("layer")}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Poland's {len(layers['wojewodztwo'])} voivodeships, {len(layers['powiat'])} powiats, "
                f"{len(layers['gmina'])} gminas, and Warsaw's {len(layers['dzielnica'])} districts from GUGiK's PRG"
            ),
            polity="PL",
        )

        def name(entity: UUID, text: str, lang: str = "pl", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license=LICENSE,
            license_url=PAGE,
            attribution="Główny Urząd Geodezji i Kartografii, Państwowy Rejestr Granic",
            notes=(
                f"Gmina and Warsaw district polygons simplified as one coverage to {TOLERANCE} m in EPSG:2180; "
                "Warsaw, powiats, voivodeships, and the country are unions of them."
            ),
        )
        cs.artifact(raw.cached(SOURCE, UNITS), source)
        wikidata = cs.source("Wikidata", "Wikidata items with a TERYT code (P1653)")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Wikidata items with a TERYT code (P1653)",
            url="https://www.wikidata.org/wiki/Property:P1653",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SPARQL), wikidata)

        def geometry(area: UUID, geom: str, point: str | None) -> None:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )

        poland = cs.polity("PL")
        if poland in cs.minted:
            raise ValueError("publish eu.members before pl.prg")
        for code, rank, noun, kind, kinds in (
            ("pl.nation", 0, "country", "administrative", None),
            ("pl.wojewodztwo", 1, "voivodeship", "administrative", None),
            ("pl.powiat", 2, "powiat", "administrative", {"powiat": "powiat", "city": "city with powiat rights"}),
            ("pl.gmina", 3, "gmina", "administrative", {kind: kind.replace("_", "-") for kind in GMINA_KINDS.values()}),
            ("pl.dzielnica_warszawy", 4, "district of Warsaw", "administrative", None),
        ):
            cs.add(
                "scheme",
                code=code,
                polity_id=poland,
                kind=kind,
                rank=rank,
                noun=noun,
                code_system=None if code == "pl.nation" else "teryt",
                kinds=kinds,
            )

        nation = cs.area("pl.nation", "PL")
        cs.add("area", id=nation, scheme_code="pl.nation")
        cs.add("area_code", area_id=nation, code="PL", valid_from=None)
        name(nation, "Poland", "en")
        name(nation, "Polska", "pl", "native")
        geometry(nation, layers["nation"]["geom"][0], None)

        areas: dict[tuple[str, str], UUID] = {("nation", "PL"): nation}
        polities: dict[tuple[str, str], UUID] = {}
        paths: dict[tuple[str, str], str] = {}
        taken: set[str] = set()
        parents = {"wojewodztwo": "nation", "powiat": "wojewodztwo", "gmina": "powiat", "dzielnica": "gmina"}
        schemes = {
            "wojewodztwo": "pl.wojewodztwo",
            "powiat": "pl.powiat",
            "gmina": "pl.gmina",
            "dzielnica": "pl.dzielnica_warszawy",
        }
        gmina_paths = _gmina_paths(layers["gmina"], dict(layers["powiat"].select("code", "name").iter_rows()))
        for layer in ("wojewodztwo", "powiat", "gmina", "dzielnica"):
            scheme = schemes[layer]
            for code, parent, text, geom, point in (
                layers[layer].select("code", "parent", "name", "geom", "label_point").rows()
            ):
                area = areas[(layer, code)] = cs.area(scheme, code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=areas[(parents[layer], parent)], valid_from=None)
                geometry(area, geom, point)
                city = layer == "powiat" and int(code[2:]) >= 61
                if layer == "powiat":
                    cs.add("area_kind", area_id=area, kind="city" if city else "powiat", valid_from=None)
                if layer == "gmina":
                    cs.add("area_kind", area_id=area, kind=GMINA_KINDS[code[-1]], valid_from=None)
                if layer == "wojewodztwo":
                    native, english = VOIVODESHIPS[code]
                    if text != native:
                        raise ValueError(f"voivodeship {code} is {text}, expected {native}")
                    polity = cs.polity(f"PL-{code}")
                    cs.add("polity", id=polity, kind="region", iso_code=f"PL-{code}")
                    cs.add("polity_parent", polity_id=polity, parent_id=poland)
                    path = f"pl/{slug(native)}"
                    for entity in (polity, area):
                        name(entity, f"województwo {native}")
                        name(entity, english, "en", "english")
                elif city:
                    name(area, text)
                    continue
                else:
                    polity = cs.key(f"{SOURCE}.polity", code)
                    if layer == "gmina":
                        kind, path = "municipality", gmina_paths[code]
                    else:
                        kind = "county" if layer == "powiat" else "submunicipal"
                        path = f"{paths[(parents[layer], parent)]}/{slug(text)}"
                    cs.add("polity", id=polity, kind=kind, iso_code=None)
                    cs.add(
                        "polity_parent",
                        polity_id=polity,
                        parent_id=polities.get((parents[layer], parent)) or polities[("wojewodztwo", code[:2])],
                    )
                    for entity in (polity, area):
                        name(entity, text)
                if path in taken:
                    raise ValueError(f"slug {path} taken twice")
                taken.add(path)
                paths[(layer, code)] = path
                polities[(layer, code)] = polity
                cs.add("slug", path=path, entity_id=polity)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                item = items.get(code)
                if item is not None:
                    cs.add("external_id", entity_id=polity, scheme="wikidata", value=item)
        for scheme in schemes.values():
            cs.add("boundary_set", scheme_code=scheme, vintage="2026", valid_from=None, valid_to=None, as_of=AS_OF)
        return cs.write()
