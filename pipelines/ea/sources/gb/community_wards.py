import io
import json
import sys
import zipfile
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb.parish import fold
from ea.store import DATA, connect

SOURCE = "gb.community_wards"
VERSION = "1"
WARDS = "gb.parish_ward"
PARSED = DATA / "parsed" / SOURCE
URL = "https://api.os.uk/downloads/v1/products/BoundaryLine/downloads?area=GB&format=ESRI%C2%AE+Shapefile&redirect"
LAYER = "Data/Wales/community_ward_region"
TITLE = "Boundary-Line: Wales community wards"
PAGE = "https://www.ordnancesurvey.co.uk/products/boundary-line"
CONTAINED = 0.9
LADS = {
    "ISLE_OF_ANGLESEY": "W06000001",
    "GWYNEDD": "W06000002",
    "CONWY": "W06000003",
    "DENBIGHSHIRE": "W06000004",
    "FLINTSHIRE": "W06000005",
    "WREXHAM": "W06000006",
    "CEREDIGION": "W06000008",
    "PEMBROKESHIRE": "W06000009",
    "CARMARTHENSHIRE": "W06000010",
    "SWANSEA": "W06000011",
    "NEATH_PORT_TALBOT": "W06000012",
    "BRIDGEND": "W06000013",
    "THE_VALE_OF_GLAMORGAN": "W06000014",
    "CARDIFF": "W06000015",
    "RHONDDA_CYNON_TAF": "W06000016",
    "CAERPHILLY": "W06000018",
    "BLAENAU_GWENT": "W06000019",
    "TORFAEN": "W06000020",
    "MONMOUTHSHIRE": "W06000021",
    "NEWPORT": "W06000022",
    "POWYS": "W06000023",
    "MERTHYR_TYDFIL": "W06000024",
}
ALIASES = {
    "Aber:abergwyngregyn": "Aber",
    "Aberdaron:de-aberdaron": "South",
    "Aberdaron:dwyrain-aberdaron-rhiw": "East",
    "Aberdaron:gogledd-aberdaron": "North",
    "Botwnnog:sarn-meillteyrn": "Meillteyrn",
    "Buan:boduan": "Bodfuan",
    "Caernarfon:canol-tref-caernarfon": "Canol Tref",
    "Caernarfon:deheuol-peblig": "Peblig",
    "Caernarfon:dwyrain-cadnant": "Cadnant",
    "Caernarfon:gogledd-menai": "Menai",
    "Caernarfon:hendre-caernarfon": "Hendre",
    "Dolgellau:deheuol-southern-dolgellau": "Southern",
    "Dolgellau:gogleddol-northern-dolgellau": "Northern",
    "Ffestiniog:conglywal": "Conglwal",
    "Llanllechid:llanllechid-isaf": "Isaf",
    "Llanllechid:llanllechid-uchaf": "Uchaf",
    "Llannor:y-ffor": "Y Ffr",
    "Llanwnda:dinas-llanwnda": "Dinas",
    "Pentir:faenol-vaynol": "Vaynol",
    "Porthmadog:dwyrain-east-porthmadog": "East",
    "Porthmadog:gorllewin-west-porthmadog": "West",
    "Pwllheli:de-pwllheli": "South",
    "Pwllheli:gogledd-pwllheli": "North",
    "Tudweiliog:dinas-tudweiliog": "Dinas",
    "Tywyn:dwyrain-east-tywyn": "East",
    "Tywyn:gorllewin-west-tywyn": "West",
    "Waunfawr:caeathro": "Caethro",
    "Aberystwyth:central": "Canol/Central",
    "Aberystwyth:north": "Gogledd/North",
    "Beulah:bettws-ifan": "Bettws Evan",
    "Llandysul:pontsian": "Pontshaen",
    "Llandysul:town": "Trefol",
    "Llanfihangel Ystrad:dihewyd": "Dihewid",
    "Ammanford:pantyffynnon": "Newcastle Emlyn",
    "Cwmamman:pistyllwyd": "Pistillwyd",
    "Kidwelly:mynydd-y-garreg": "Mynedd-y-Garreg",
    "Llangadog:dyffryn-ceidrych": "Dyffryn Ceidyrch",
    "Llangyndeyrn:pontyates": "Pontiets",
    "Quarter Bach:cwarter-bach": "Quarter Bach",
    "St. Ishmael:ferryside": "Glanyfferi",
    "Bargoed:aberbargod": "Aberbargoed",
    "Bedwas, Trethomas and Machen:tretomos": "Trethomas",
    "Blackwood:canol-coed-duon": "Blackwood Central",
    "Blackwood:de-coed-duon": "Blackwood South",
    "Blackwood:dwyrain-cefn-fforest": "Cefn Fforest East",
    "Blackwood:gogledd-coed-duon": "Blackwood North",
    "New Tredegar:tredegar-newydd": "New Tredegar",
    "New Tredegar:treffilip": "Phillipstown",
    "Rhymney:abertyswg": "Abertysswg",
    "Rhymney:pontlotyn": "Pontlottyn",
    "Rudry:rhydri": "Rudry",
    "Caerphilly:cwrt-rawlin": "Cwrt Rawlins",
    "Penyrheol, Trecenydd and Energlyn:eneur-glyn": "Energlyn",
    "Risca East:pont-y-meistr-ty-sign": "Pontymister Ty-Sign",
    "Risca East:pont-y-meistr-uchaf": "Pontymister Upper",
    "Risca West:pont-y-meistr-isaf": "Pontymister Lower",
    "Risca West:tref-rhisga-a-fernlea": "Risca Fernlea and Town",
    "Blaenavon:blaenavon-east": "East",
    "Blaenavon:blaenavon-west": "West",
    "Ponthir:ponthir-and-lower-llanfrechfa": "Ponthir",
    "Cwmbran:upper-cwmbran-thornhill": "Thornhill",
    "Pontypool:cwmynyscoy": "Cwmyniscoy",
    "Conwy:castell": "Castle",
    "Gobion Fawr:llanddewi-rhydderch": "Llandewi Rhydderch",
    "Goetre Fawr:goetre": "Goytre",
    "Llanelly:darrenfelin": "Darrenfelen",
    "Mitchel Troy:mitchel-troy": "Mitchell Troy",
}
MISLABELED = {
    "Ammanford:pantyffynnon": (
        "Boundary-Line labels this ward of Ammanford 'Newcastle Emlyn'. It is the fourth of Ammanford's four community "
        "wards beside Iscennen, Myddynfych, and Wernddu, which Boundary-Line names, so its shape is taken as "
        "Pantyffynnon's."
    )
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")


def wards() -> list[dict]:
    with zipfile.ZipFile(raw.cached(SOURCE, URL).path) as z:
        parts = {suffix: io.BytesIO(z.read(f"{LAYER}.{suffix}")) for suffix in ("shp", "shx", "dbf")}
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    features = []
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        assert record is not None and shape is not None
        features.append(
            {
                "index": len(features),
                "lad": LADS[str(record["FILE_NAME"]).split("_-_")[1]],
                "community": record["COMMUNITY"],
                "name": record["NAME"],
                "geojson": json.dumps(shape.__geo_interface__),
            }
        )
    return features


def parse() -> None:
    features = wards()
    with connect() as conn:
        areas = conn.execute(
            """select c.code, n.text, pn.text,
                      coalesce((select lc.code from area_parent lp join area la on la.id = lp.parent_id
                                and la.scheme_code = 'gb.lad' join area_code lc on lc.area_id = la.id
                                where lp.area_id = p.parent_id limit 1), split_part(pc.code, ':', 1))
               from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'en'
               join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
               join name pn on pn.entity_id = p.parent_id and pn.is_preferred and pn.lang = 'en'
               where a.scheme_code = %s and c.code like 'W0%%'""",
            [WARDS],
        ).fetchall()
        by_name: dict[tuple[str, str], list[int]] = {}
        for feature in features:
            by_name.setdefault((feature["lad"], fold(feature["name"])), []).append(feature["index"])
        candidates = []
        for code, name, parent, lad in areas:
            alias = ALIASES.get(f"{parent}:{code.rsplit(':', 1)[1]}")
            for index in by_name.get((lad, fold(alias or name)), []):
                candidates.append((code, index, lad))
        conn.execute("create temp table feature (index int, geojson text) on commit drop")
        with conn.cursor().copy("copy feature from stdin") as copy:
            for feature in features:
                copy.write_row((feature["index"], feature["geojson"]))
        rows = conn.execute(
            """with shape as (
                 select index, st_multi(st_collectionextract(st_makevalid(
                          st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom
                 from feature),
               land as (
                 select f.code, f.index, s.geom, st_intersection(s.geom, st_transform(lg.geom, 27700)) as land,
                        st_transform(g.geom, 27700) as parent
                 from unnest(%s::text[], %s::int[], %s::text[]) f(code, index, lad)
                 join shape s on s.index = f.index
                 join area_code c on c.code = f.code join area a on a.id = c.area_id and a.scheme_code = %s
                 join area_parent p on p.area_id = a.id
                 join area_geometry g on g.area_id = p.parent_id and upper_inf(g.valid)
                 join area_code lc on lc.code = f.lad join area l on l.id = lc.area_id and l.scheme_code = 'gb.lad'
                 join area_geometry lg on lg.area_id = l.id and upper_inf(lg.valid)),
               pick as (
                 select code, index, geom, land,
                        st_area(st_intersection(land, parent)) / nullif(st_area(land), 0) as share from land)
               select code, index, share, encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(land), 4326)), 'hex')
               from pick where share >= %s""",
            [*(list(column) for column in zip(*candidates, strict=True)), WARDS, CONTAINED],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"code": pl.String, "index": pl.Int32, "share": pl.Float64, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    if (doubled := frame.filter(pl.col("code").is_duplicated())).height:
        raise ValueError(f"two Boundary-Line wards for {sorted(set(doubled['code']))}")
    if (reused := frame.filter(pl.col("index").is_duplicated())).height:
        raise ValueError(f"one Boundary-Line ward for {sorted(reused['code'])}")
    unmatched = sorted({code for code, *_ in areas} - set(frame["code"]))
    print(f"{frame.height} of {len(areas)} wards matched; unmatched: {unmatched}", file=sys.stderr)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "wards.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "wards.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Shapes for the Welsh community wards from Ordnance Survey's Boundary-Line",
            polity="GB",
        )
        source = cs.source("Ordnance Survey", TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Ordnance Survey",
            title=TITLE,
            url=PAGE,
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="Contains OS data © Crown copyright and database right, licensed under the Open Government "
            "Licence v3.0.",
            notes="Community ward boundaries in force until the communities orders that take effect at the May 2027 "
            "local elections.",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        codes = {
            code: parent
            for code, parent in conn.execute(
                """select c.code, pn.text from area a join area_code c on c.area_id = a.id
                   join area_parent p on p.area_id = a.id
                   join name pn on pn.entity_id = p.parent_id and pn.is_preferred and pn.lang = 'en'
                   where a.scheme_code = %s and c.code = any(%s)""",
                [WARDS, list(frame["code"])],
            ).fetchall()
        }
        for code, geom, point in frame.select("code", "geom", "label_point").rows():
            area = cs.area(WARDS, code)
            if area in cs.minted:
                raise ValueError(f"{code} is not an area in the archive")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
            if note := MISLABELED.get(f"{codes[code]}:{code.rsplit(':', 1)[1]}"):
                cs.add("note", entity_id=area, kind="caveat", body_md=note)
        return cs.write()
