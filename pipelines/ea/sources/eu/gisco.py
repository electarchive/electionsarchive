import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "eu.gisco"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DISTRIBUTION = "https://gisco-services.ec.europa.eu/distribution/v2"
DATASETS = {
    "countries": (
        f"{DISTRIBUTION}/countries/geojson/CNTR_RG_01M_2024_4326.geojson",
        "CNTR_ID",
        "Countries 2024",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/countries",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units",
    ),
    "nuts": (
        f"{DISTRIBUTION}/nuts/geojson/NUTS_RG_01M_2024_4326_LEVL_3.geojson",
        "NUTS_ID",
        "NUTS 2024",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units",
    ),
    "nuts2016": (
        f"{DISTRIBUTION}/nuts/geojson/NUTS_RG_01M_2016_4326_LEVL_1.geojson",
        "NUTS_ID",
        "NUTS 2016",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units",
    ),
    "lau": (
        f"{DISTRIBUTION}/lau/geojson/LAU_RG_01M_2024_4326.geojson",
        "GISCO_ID",
        "Local Administrative Units (LAU) 2024",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/local-administrative-units",
        "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units",
    ),
}
LICENSE = "Eurostat GISCO download rules: free use for non-commercial purposes, with the source acknowledged"
ATTRIBUTION = "© EuroGeographics for the administrative boundaries"
DIRECT = date(1979, 6, 7)
GREENLAND = date(1985, 2, 1)
REUNIFICATION = date(1990, 10, 3)
FOUNDED = date(1958, 1, 1)
ENLARGED_1973 = date(1973, 1, 1)
ENLARGED_2004 = date(2004, 5, 1)
STATES = {
    "AT": ("AT", date(1995, 1, 1), None, "Austria"),
    "BE": ("BE", FOUNDED, None, "Belgium"),
    "BG": ("BG", date(2007, 1, 1), None, "Bulgaria"),
    "CY": ("CY", ENLARGED_2004, None, "Cyprus"),
    "CZ": ("CZ", ENLARGED_2004, None, "Czechia"),
    "DE": ("DE", FOUNDED, None, "Germany"),
    "DK": ("DK", ENLARGED_1973, None, "Denmark"),
    "EE": ("EE", ENLARGED_2004, None, "Estonia"),
    "ES": ("ES", date(1986, 1, 1), None, "Spain"),
    "FI": ("FI", date(1995, 1, 1), None, "Finland"),
    "FR": ("FR", FOUNDED, None, "France"),
    "GB": ("UK", ENLARGED_1973, date(2020, 2, 1), "United Kingdom"),
    "GR": ("EL", date(1981, 1, 1), None, "Greece"),
    "HR": ("HR", date(2013, 7, 1), None, "Croatia"),
    "HU": ("HU", ENLARGED_2004, None, "Hungary"),
    "IE": ("IE", ENLARGED_1973, None, "Ireland"),
    "IT": ("IT", FOUNDED, None, "Italy"),
    "LT": ("LT", ENLARGED_2004, None, "Lithuania"),
    "LU": ("LU", FOUNDED, None, "Luxembourg"),
    "LV": ("LV", ENLARGED_2004, None, "Latvia"),
    "MT": ("MT", ENLARGED_2004, None, "Malta"),
    "NL": ("NL", FOUNDED, None, "Netherlands"),
    "PL": ("PL", ENLARGED_2004, None, "Poland"),
    "PT": ("PT", date(1986, 1, 1), None, "Portugal"),
    "RO": ("RO", date(2007, 1, 1), None, "Romania"),
    "SE": ("SE", date(1995, 1, 1), None, "Sweden"),
    "SI": ("SI", ENLARGED_2004, None, "Slovenia"),
    "SK": ("SK", ENLARGED_2004, None, "Slovakia"),
}
WEST_GERMANY = ("nuts:DE1", "nuts:DE2", "nuts:DE5", "nuts:DE6", "nuts:DE7", "nuts:DE9", "nuts:DEA", "nuts:DEB")
WEST_GERMANY += ("nuts:DEC", "nuts:DEF")
ELECTIONS = (
    (date(1979, 6, 7), date(1979, 6, 10)),
    (date(1984, 6, 14), date(1984, 6, 17)),
    (date(1989, 6, 15), date(1989, 6, 18)),
    (date(1994, 6, 9), date(1994, 6, 12)),
    (date(1999, 6, 10), date(1999, 6, 13)),
    (date(2004, 6, 10), date(2004, 6, 13)),
    (date(2009, 6, 4), date(2009, 6, 7)),
    (date(2014, 5, 22), date(2014, 5, 25)),
    (date(2019, 5, 23), date(2019, 5, 26)),
    (date(2024, 6, 6), date(2024, 6, 9)),
)
ITALY = date(1979, 1, 24)
FRANCE = (date(2003, 4, 11), date(2018, 6, 25))
BRITAIN = (date(1999, 1, 14), date(2003, 5, 8), date(2020, 2, 1))
POLAND = date(2004, 1, 23)
BELGIUM = (date(1993, 7, 30), date(2012, 8, 22))
WARSAW = ("65", "05", "08", "14", "17", "18", "21", "32", "34")
PLANS = {
    "it": ((ITALY, None),),
    "fr": (FRANCE,),
    "gb": ((BRITAIN[0], BRITAIN[1]), (BRITAIN[1], BRITAIN[2])),
    "pl": ((POLAND, None),),
    "be": ((BELGIUM[0], BELGIUM[1]), (BELGIUM[1], None)),
}
NOUNS = {
    "it": "European Parliament constituency",
    "fr": "European Parliament constituency",
    "gb": "European Parliament electoral region",
    "pl": "European Parliament constituency",
    "be": "European Parliament constituency",
}
CONSTITUENCIES = {
    "it": [
        ("nord-occidentale", "North-West", ("it", "Italia nord-occidentale"), [(ITALY, None, ("nuts:ITC",))]),
        ("nord-orientale", "North-East", ("it", "Italia nord-orientale"), [(ITALY, None, ("nuts:ITH",))]),
        ("centrale", "Central", ("it", "Italia centrale"), [(ITALY, None, ("nuts:ITI",))]),
        ("meridionale", "Southern", ("it", "Italia meridionale"), [(ITALY, None, ("nuts:ITF",))]),
        ("insulare", "Islands", ("it", "Italia insulare"), [(ITALY, None, ("nuts:ITG",))]),
    ],
    "fr": [
        ("nord-ouest", "North-West", ("fr", "Nord-Ouest"), [(*FRANCE, ("nuts:FRD1", "nuts:FRD2", "nuts:FRE"))]),
        ("ouest", "West", ("fr", "Ouest"), [(*FRANCE, ("nuts:FRG", "nuts:FRH", "nuts:FRI3"))]),
        ("est", "East", ("fr", "Est"), [(*FRANCE, ("nuts:FRF", "nuts:FRC"))]),
        ("sud-ouest", "South-West", ("fr", "Sud-Ouest"), [(*FRANCE, ("nuts:FRI1", "nuts:FRJ"))]),
        ("sud-est", "South-East", ("fr", "Sud-Est"), [(*FRANCE, ("nuts:FRL", "nuts:FRK2", "nuts:FRM"))]),
        (
            "massif-central-centre",
            "Massif-central–Centre",
            ("fr", "Massif central-Centre"),
            [(*FRANCE, ("nuts:FRK1", "nuts:FRB", "nuts:FRI2"))],
        ),
        ("ile-de-france", "Île-de-France", ("fr", "Île-de-France"), [(*FRANCE, ("nuts:FR1",))]),
        (
            "outre-mer",
            "Overseas",
            ("fr", "Outre-mer"),
            [(*FRANCE, ("nuts:FRY", "countries:PM", "countries:NC", "countries:PF", "countries:WF", "countries:BL"))],
        ),
    ],
    "gb": [
        (code, label, None, [(BRITAIN[0], BRITAIN[2], (f"nuts2016:{nuts}",))])
        for code, label, nuts in (
            ("north-east", "North East", "UKC"),
            ("north-west", "North West", "UKD"),
            ("yorkshire-and-the-humber", "Yorkshire and the Humber", "UKE"),
            ("east-midlands", "East Midlands", "UKF"),
            ("west-midlands", "West Midlands", "UKG"),
            ("eastern", "East of England", "UKH"),
            ("london", "London", "UKI"),
            ("south-east", "South East", "UKJ"),
            ("wales", "Wales", "UKL"),
            ("scotland", "Scotland", "UKM"),
            ("northern-ireland", "Northern Ireland", "UKN"),
        )
    ]
    + [
        (
            "south-west",
            "South West",
            None,
            [
                (BRITAIN[0], BRITAIN[1], ("nuts2016:UKK",)),
                (BRITAIN[1], BRITAIN[2], ("nuts2016:UKK", "countries:GI")),
            ],
        )
    ],
    "pl": [
        (str(number), label, ("pl", f"Okręg wyborczy nr {number}"), [(POLAND, None, parts)])
        for number, label, parts in (
            (1, "Pomeranian", ("lau:22",)),
            (2, "Kuyavian-Pomeranian", ("lau:04",)),
            (3, "Podlaskie and Warmian-Masurian", ("lau:20", "lau:28")),
            (4, "Warsaw", tuple(f"lau:14{powiat}" for powiat in WARSAW)),
            (5, "Masovian", ("lau:14", *(f"-lau:14{powiat}" for powiat in WARSAW))),
            (6, "Łódź", ("lau:10",)),
            (7, "Greater Poland", ("lau:30",)),
            (8, "Lublin", ("lau:06",)),
            (9, "Subcarpathian", ("lau:18",)),
            (10, "Lesser Poland and Świętokrzyskie", ("lau:12", "lau:26")),
            (11, "Silesian", ("lau:24",)),
            (12, "Lower Silesian and Opole", ("lau:02", "lau:16")),
            (13, "Lubusz and West Pomeranian", ("lau:08", "lau:32")),
        )
    ],
    "be": [
        (
            "flemish",
            "Flemish",
            ("nl", "Vlaamse kieskring"),
            [(*BELGIUM, ("nuts:BE2", "-nuts:BE241")), (BELGIUM[1], None, ("nuts:BE2",))],
        ),
        (
            "walloon",
            "Walloon",
            ("fr", "Circonscription électorale wallonne"),
            [(BELGIUM[0], None, ("nuts:BE3", "-nuts:BE336"))],
        ),
        (
            "brussels",
            "Brussels-Capital",
            ("fr", "Bruxelles-Capitale"),
            [(*BELGIUM, ("nuts:BE100", "nuts:BE241")), (BELGIUM[1], None, ("nuts:BE100",))],
        ),
        (
            "german-speaking",
            "German-speaking",
            ("de", "Deutschsprachiger Wahlkreis"),
            [(BELGIUM[0], None, ("nuts:BE336",))],
        ),
    ],
}
BRUSSELS_HALLE_VILVOORDE = (
    ("en", "Brussels-Halle-Vilvoorde"),
    ("fr", "Bruxelles-Hal-Vilvorde"),
    ("nl", "Brussel-Halle-Vilvoorde"),
)
POLITIES = {
    "PL": ("Poland", (("pl", "Polska"),), "Q36", ENLARGED_2004),
    "BE": ("Belgium", (("nl", "België"), ("fr", "Belgique"), ("de", "Belgien")), "Q31", FOUNDED),
}


def fetch() -> None:
    for url, *_ in DATASETS.values():
        raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    features: dict[tuple[str, str], str] = {}
    keys: dict[str, dict[str, str]] = {}
    for dataset, (url, field, *_) in DATASETS.items():
        keys[dataset] = {}
        for feature in json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"]:
            id = feature["properties"][field]
            if dataset == "lau" and not id.startswith("PL_"):
                continue
            keys[dataset][id] = f"{id[7:9]}{id[-4:-2]}" if dataset == "lau" else id
            features[dataset, id] = json.dumps(feature["geometry"])

    def expand(parts: tuple[str, ...]) -> set[tuple[str, str]]:
        chosen: set[tuple[str, str]] = set()
        for part in sorted(parts, key=lambda part: part.startswith("-")):
            dataset, _, prefix = part.removeprefix("-").partition(":")
            matched = {(dataset, id) for id, key in keys[dataset].items() if key.startswith(prefix)}
            if not matched:
                raise ValueError(f"{part} matches no feature")
            chosen = chosen - matched if part.startswith("-") else chosen | matched
        return chosen

    versions: list[tuple[str, str, date | None, date | None, set[tuple[str, str]]]] = []
    for iso, (gisco, accession, withdrawal, _) in STATES.items():
        start = max(accession, DIRECT)
        if iso == "DE":
            versions.append(("eu.member_state", iso, start, REUNIFICATION, expand(WEST_GERMANY)))
            start = REUNIFICATION
        if iso == "DK":
            versions.append(("eu.member_state", iso, start, GREENLAND, expand(("countries:DK", "countries:GL"))))
            start = GREENLAND
        versions.append(("eu.member_state", iso, start, withdrawal, {("countries", gisco)}))
    for country, constituencies in CONSTITUENCIES.items():
        for code, _, _, spans in constituencies:
            versions.extend(
                (f"{country}.ep_constituency", code, start, stop, expand(parts)) for start, stop, parts in spans
            )
        coverage = {
            "it": ("nuts:IT",),
            "fr": ("nuts:FR",),
            "gb": ("nuts2016:UK",),
            "pl": ("lau:",),
            "be": ("nuts:BE",),
        }[country]
        for start, _ in PLANS[country]:
            live = [
                parts
                for scheme, _, begin, end, parts in versions
                if scheme == f"{country}.ep_constituency"
                and begin is not None
                and begin <= start
                and (end is None or end > start)
            ]
            used = [part for parts in live for part in parts if part[0] != "countries"]
            if len(used) != len(set(used)) or set(used) != expand(coverage):
                raise ValueError(f"{country}'s constituencies as of {start} do not partition the country")

    with connect() as conn:
        conn.execute("create temp table feature (dataset text, id text, geojson text) on commit drop")
        conn.execute("create temp table member (version int, dataset text, id text) on commit drop")
        needed = {part for *_, parts in versions for part in parts}
        with conn.cursor() as cursor, cursor.copy("copy feature (dataset, id, geojson) from stdin") as copy:
            for dataset, id in sorted(needed):
                copy.write_row((dataset, id, features[dataset, id]))
        with conn.cursor() as cursor, cursor.copy("copy member (version, dataset, id) from stdin") as copy:
            for index, (*_, parts) in enumerate(versions):
                for dataset, id in parts:
                    copy.write_row((index, dataset, id))
        rows = conn.execute(
            """with unions as (
                 select m.version,
                        st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(
                          st_force2d(st_setsrid(st_geomfromgeojson(f.geojson), 4326))))), 3)) as geom
                 from member m join feature f using (dataset, id) group by m.version
               )
               select version, encode(st_asewkb(geom), 'hex'),
                      encode(st_asewkb((select st_pointonsurface(d.geom) from st_dump(geom) d
                                        order by st_area(d.geom::geography) desc limit 1)), 'hex')
               from unions order by version"""
        ).fetchall()
    frame = pl.DataFrame(
        [
            (
                versions[index][0],
                versions[index][1],
                versions[index][2],
                versions[index][3],
                max((dataset for dataset, _ in versions[index][4]), key=lambda dataset: dataset != "countries"),
                geom,
                point,
            )
            for index, geom, point in rows
        ],
        schema={
            "scheme": pl.String,
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "dataset": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    if frame.height != len(versions) or frame["geom"].null_count():
        raise ValueError(f"expected {len(versions)} geometries, got {frame.height}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "The European Union's member states since the first direct election, and the European Parliament "
                "constituencies of Italy, France, the United Kingdom, Poland, and Belgium, from Eurostat's GISCO "
                "countries, NUTS regions, and local administrative units"
            ),
            polity="EU",
        )
        union = cs.polity("EU")
        if union in cs.minted:
            raise ValueError("publish eu.eurlex before eu.gisco")
        sources: dict[str, UUID] = {}
        for dataset, (url, _, title, page, terms) in DATASETS.items():
            sources[dataset] = cs.source("Eurostat", title)
            cs.add(
                "source",
                id=sources[dataset],
                kind="official_body",
                publisher="Eurostat",
                title=title,
                url=page,
                license=LICENSE,
                license_url=terms,
                attribution=ATTRIBUTION,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[dataset])

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", **valid: object) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official", **valid)

        for iso, (label, natives, item, accession) in POLITIES.items():
            polity = cs.polity(iso)
            cs.add("polity", id=polity, kind="sovereign_state", iso_code=iso)
            name(polity, label)
            for lang, text in natives:
                name(polity, text, lang, "native")
            cs.add("slug", path=iso.lower(), entity_id=polity)
            cs.add("external_id", entity_id=polity, scheme="wikidata", value=item)
            cs.add("polity_parent", polity_id=polity, parent_id=union, valid_from=accession)

        cs.add("scheme", code="eu.member_state", polity_id=union, kind="administrative", rank=0, noun="member state")
        states: dict[str, UUID] = {}
        for iso, (*_, label) in STATES.items():
            states[iso] = cs.area("eu.member_state", iso)
            cs.add("area", id=states[iso], scheme_code="eu.member_state")
            cs.add("area_code", area_id=states[iso], code=iso, valid_from=None)
            name(states[iso], label)
        for iso in POLITIES:
            cs.add("polity_territory", polity_id=cs.polity(iso), area_id=states[iso])
        for country, constituencies in CONSTITUENCIES.items():
            scheme = f"{country}.ep_constituency"
            cs.add(
                "scheme",
                code=scheme,
                polity_id=cs.polity(country.upper()),
                kind="electoral_district",
                rank=1,
                noun=NOUNS[country],
            )
            for code, label, native, _ in constituencies:
                area = cs.area(scheme, code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=states[country.upper()], valid_from=None)
                if code == "brussels":
                    for lang, text in BRUSSELS_HALLE_VILVOORDE:
                        name(area, text, lang, "official" if lang == "en" else "native", valid_to=BELGIUM[1])
                    name(area, label, valid_from=BELGIUM[1])
                    name(area, "Bruxelles-Capitale", "fr", "native", valid_from=BELGIUM[1])
                    name(area, "Brussel-Hoofdstad", "nl", "native", valid_from=BELGIUM[1])
                    continue
                name(area, label)
                if native is not None:
                    name(area, native[1], native[0], "native")
        for scheme, code, start, stop, dataset, geom, point in frame.rows():
            cs.add(
                "area_geometry",
                area_id=cs.area(scheme, code),
                valid_from=start,
                valid_to=stop,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[dataset],
            )
        for (begin, end), following in zip(ELECTIONS, [*ELECTIONS[1:], None], strict=True):
            cs.add(
                "boundary_set",
                scheme_code="eu.member_state",
                vintage=str(begin.year),
                valid_from=begin,
                valid_to=None if following is None else following[0],
                as_of=end,
            )
        for country, plans in PLANS.items():
            for start, stop in plans:
                cs.add(
                    "boundary_set",
                    scheme_code=f"{country}.ep_constituency",
                    vintage=str(start.year),
                    valid_from=start,
                    valid_to=stop,
                    as_of=max(end for begin, end in ELECTIONS if begin >= start and (stop is None or begin < stop)),
                )
        return cs.write()
