import csv
import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid7

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "it.istat"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
LIMITS = {
    2022: "https://www.istat.it/storage/cartografia/confini_amministrativi/non_generalizzati/2022/Limiti01012022.zip",
    2025: "https://www.istat.it/storage/cartografia/confini_amministrativi/non_generalizzati/2025/Limiti01012025.zip",
}
COLLEGI = "https://www.istat.it/storage/Basi%20Geografiche%202022/Collegi_Elettorali_BasiGeografiche.zip"
VARIATIONS = (
    "https://www.istat.it/storage/codici-unita-amministrative/Variazioni-amministrative-e-territoriali-dal-1991.zip"
)
VARIATIONS_CSV = (
    "Variazioni amministrative e territoriali dal 1991/Variazioni_amministrative_territoriali_dal_01011991.csv"
)
LAYERS = {
    "comune_2022": (LIMITS[2022], "Limiti01012022/Com01012022/Com01012022_WGS84", "PRO_COM_T", "COMUNE"),
    "comune_2025": (LIMITS[2025], "Com01012025/Com01012025_WGS84", "PRO_COM_T", "COMUNE"),
    "regione": (LIMITS[2025], "Reg01012025/Reg01012025_WGS84", "COD_REG", "DEN_REG"),
    "circoscrizione_camera": (
        COLLEGI,
        "Collegi_Elettorali_BasiGeografiche/Circoscrizioni_Camera/Circo_Camera_2020",
        "CIRC_COD",
        "CIRC_DEN",
    ),
    "plurinominale_camera": (
        COLLEGI,
        "Collegi_Elettorali_BasiGeografiche/CAMERA_CollegiPLURINOMINALI_2020/CAMERA_CollegiPLURINOMINALI_2020",
        "CP20_COD",
        "CP20_DEN",
    ),
    "uninominale_camera": (
        COLLEGI,
        "Collegi_Elettorali_BasiGeografiche/CAMERA_CollegiUNINOMINALI_2020/CAMERA_CollegiUNINOMINALI_2020",
        "CU20_COD",
        "CU20_DEN",
    ),
    "plurinominale_senato_2020": (
        COLLEGI,
        "Collegi_Elettorali_BasiGeografiche/SENATO_CollegiPLURINOMINALI_2020/SENATO_CollegiPLURINOMINALI_2020",
        "SP20_COD",
        "SP20_DEN",
    ),
    "collegio_senato_2020": (
        COLLEGI,
        "Collegi_Elettorali_BasiGeografiche/SENATO_CollegiUNINOMINALI_2020/SENATO_CollegiUNINOMINALI_2020",
        "SU20_COD",
        "SU20_DEN",
    ),
}
COUNTS = {
    "comune_2022": 7904,
    "comune_2025": 7896,
    "regione": 20,
    "circoscrizione_camera": 28,
    "plurinominale_camera": 49,
    "uninominale_camera": 147,
    "plurinominale_senato_2020": 26,
    "collegio_senato_2020": 74,
    "nation": 1,
}
TOLERANCE = 10
FIRST = date(2022, 1, 1)
LAST = date(2025, 1, 1)
ELECTIONS = {2022: date(2022, 9, 25), 2025: date(2025, 6, 8)}
PUBLISHER = "ISTAT"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "© ISTAT"
TITLES = {
    LIMITS[2022]: "Confini delle unità amministrative a fini statistici al 1° gennaio 2022",
    LIMITS[2025]: "Confini delle unità amministrative a fini statistici al 1° gennaio 2025",
    COLLEGI: "Basi geografiche dei collegi elettorali 2020",
    VARIATIONS: "Variazioni amministrative e territoriali dal 1991",
}
PAGES = {
    LIMITS[
        2022
    ]: "https://www.istat.it/notizia/confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/",
    LIMITS[
        2025
    ]: "https://www.istat.it/notizia/confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/",
    COLLEGI: "https://www.istat.it/notizia/le-basi-geografiche-dei-nuovi-collegi-elettorali-2/",
    VARIATIONS: "https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/",
}


def fetch() -> None:
    for url in (*LIMITS.values(), COLLEGI, VARIATIONS):
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    archives = {url: zipfile.ZipFile(raw.cached(SOURCE, url).path) for url in (*LIMITS.values(), COLLEGI)}
    with connect() as conn:
        conn.execute(
            "create temp table feature (layer text, code text, name text, region text, geojson text) on commit drop"
        )
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (layer, code, name, region, geojson) from stdin") as copy,
        ):
            for layer, (url, stem, code, label) in LAYERS.items():
                archive = archives[url]
                reader = shapefile.Reader(
                    shp=io.BytesIO(archive.read(f"{stem}.shp")),
                    shx=io.BytesIO(archive.read(f"{stem}.shx")),
                    dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                    encoding="utf-8",
                )
                for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                    if record is None or shape is None or not record[label]:
                        continue
                    copy.write_row(
                        (
                            layer,
                            f"{record[code]:02d}" if layer == "regione" else str(record[code]),
                            f"{record[label]}/{record['COMUNE_A']}"
                            if layer.startswith("comune") and record["COMUNE_A"] not in ("0", "")
                            else record[label],
                            f"{record['COD_REG']:02d}" if layer.startswith("comune") else None,
                            json.dumps(shape.__geo_interface__),
                        )
                    )
        conn.execute(
            """create temp table shape on commit drop as
               select layer, code, name, region,
                      st_setsrid(st_coveragesimplify(geom, %s) over (partition by layer), 32632) as geom
               from (select layer, code, name, region,
                            st_multi(st_collectionextract(st_makevalid(st_force2d(
                              st_setsrid(st_geomfromgeojson(geojson), 32632))), 3)) as geom
                     from feature) features""",
            [TOLERANCE],
        )
        rows = conn.execute(
            """with layers as (
                 select layer, code, name, region, geom from shape
                 union all
                 select 'nation', 'IT', null, null, st_setsrid(st_coverageunion(geom), 32632)
                 from shape where layer = 'regione'
               )
               select layer, code, name, region,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      case when layer <> 'nation'
                        then encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') end
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "name": pl.String,
            "region": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != COUNTS or frame["geom"].null_count():
        raise ValueError(f"expected {COUNTS}, got {counts}")
    with zipfile.ZipFile(raw.cached(SOURCE, VARIATIONS).path) as archive:
        lines = list(csv.reader(io.StringIO(archive.read(VARIATIONS_CSV).decode("cp1252")), delimiter=";"))
    variations = pl.DataFrame(
        [
            (kind, code, name, other, other_name, datetime.strptime(day, "%d/%m/%Y").date())
            for year, kind, _, _, code, name, _, _, other, other_name, _, _, day, *_ in lines[1:]
            if FIRST.year <= int(year) < LAST.year
        ],
        schema={
            "kind": pl.String,
            "code": pl.String,
            "name": pl.String,
            "other": pl.String,
            "other_name": pl.String,
            "date": pl.Date,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")
    variations.write_parquet(PARSED / "variations.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    variations = pl.read_parquet(PARSED / "variations.parquet")
    shapes = {
        (layer, code): (geom, point)
        for layer, code, geom, point in frame.select("layer", "code", "geom", "label_point").rows()
    }
    names = {code: name for code, name in frame.filter(pl.col("layer") == "comune_2022").select("code", "name").rows()}
    successors = {
        code: name for code, name in frame.filter(pl.col("layer") == "comune_2025").select("code", "name").rows()
    }
    ended = {code: day for kind, code, day in variations.select("kind", "code", "date").rows() if kind == "ES"}
    created = {code: day for kind, code, day in variations.select("kind", "code", "date").rows() if kind == "CS"}
    reshaped = {
        code: day
        for kind, code, day in variations.select("kind", "code", "date").rows()
        if kind in ("AQ", "CE", "AQES")
    }
    continued = {
        code: other
        for kind, code, name, other, other_name in variations.select(
            "kind", "code", "name", "other", "other_name"
        ).rows()
        if kind == "CS" and name == other_name
    }
    current = set(frame.filter(pl.col("layer") == "comune_2025")["code"])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Italy's regions, its municipalities as of 2022 and 2025, and the Chamber's 2020 constituencies "
                "from ISTAT's boundaries, with municipal mergers and boundary changes from 2022 to 2024"
            ),
            polity="IT",
        )
        sources: dict[str, UUID] = {}
        for url, title in TITLES.items():
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGES[url],
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])

        def geometry(area: UUID, key: tuple[str, str], source: str, start: date | None, end: date | None) -> None:
            geom, point = shapes[key]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_to=end,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[source],
            )

        geometry(cs.area("it.nation", "IT"), ("nation", "IT"), LIMITS[2025], None, None)
        for code in frame.filter(pl.col("layer") == "regione")["code"]:
            geometry(cs.area("it.regione", code), ("regione", code), LIMITS[2025], None, None)
        for layer in (
            "circoscrizione_camera",
            "plurinominale_camera",
            "uninominale_camera",
            "plurinominale_senato_2020",
            "collegio_senato_2020",
        ):
            known = {
                slug(code): area
                for code, area in conn.execute(
                    "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = %s",
                    [f"it.{layer}"],
                ).fetchall()
            }
            for code, name in frame.filter(pl.col("layer") == layer).select("code", "name").rows():
                key = slug(name).replace("-vallee-d-aoste", "")
                if layer.endswith("_senato_2020"):
                    key = f"{int(code) // 10000:02d}-{key.replace('-sudtirol', '')}"
                if key not in known:
                    raise ValueError(f"no it.{layer} area matches ISTAT's {name}")
                geometry(known.pop(key), (layer, code), COLLEGI, None, None)
            if known:
                raise ValueError(f"no ISTAT shape for it.{layer} {sorted(known)}")

        coded = {
            code: area
            for code, area in conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'it.comune'"
            ).fetchall()
        }
        if set(coded) - set(ended) != current:
            raise ValueError(f"municipality codes differ from ISTAT's 2025 list: {set(coded) ^ current}")
        by_name = {
            (region, slug(name).replace("-", "")): code
            for code, name, region in frame.filter(pl.col("layer") == "comune_2022")
            .select("code", "name", "region")
            .rows()
        }
        extinct: dict[str, UUID] = {}
        for key, area in conn.execute(
            """select e.value, a.id from area a join external_id e on e.entity_id = a.id
               where a.scheme_code = 'it.comune' and e.scheme = 'it.eligendo.comune'"""
        ).fetchall():
            region, name = key.split(":")
            code = by_name.get((region, name.lower()))
            if code is None:
                raise ValueError(f"Eligendo's {key} matches no municipality in ISTAT's 2022 list")
            if code in current:
                if coded[code] != area:
                    cs.add("entity_merge", entity_id=area, into_id=coded[code])
                continue
            extinct[code] = area
            cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=ended[code])
            geometry(area, ("comune_2022", code), LIMITS[2022], None, ended[code])
        for code in sorted(current):
            area = coded[code]
            if code in continued:
                geometry(area, ("comune_2022", continued[code]), LIMITS[2022], None, created[code])
                geometry(area, ("comune_2025", code), LIMITS[2025], created[code], None)
            elif code in created:
                geometry(area, ("comune_2025", code), LIMITS[2025], created[code], None)
            elif code in reshaped:
                geometry(area, ("comune_2022", code), LIMITS[2022], None, reshaped[code])
                geometry(area, ("comune_2025", code), LIMITS[2025], reshaped[code], None)
            else:
                geometry(area, ("comune_2025", code), LIMITS[2025], None, None)

        events = {
            (kind, day, area): event
            for event, kind, day, area in conn.execute(
                """select e.id, e.kind::text, e.date, m.area_id from area_event e
                   join area_event_member m on m.event_id = e.id where m.role = 'to'"""
            ).fetchall()
        }

        def event(kind: str, day: date, members: list[tuple[UUID, str]], notes: str) -> None:
            target = next(area for area, role in members if role == "to")
            id = events.get((kind, day, target)) or uuid7()
            events[(kind, day, target)] = id
            cs.add("area_event", id=id, kind=kind, date=day, source_id=sources[VARIATIONS], notes=notes)
            for area, role in members:
                cs.add("area_event_member", event_id=id, area_id=area, role=role)

        def area_of(code: str) -> UUID:
            return coded[code] if code in coded else extinct[code]

        for code, day in sorted(created.items()):
            predecessors = sorted(
                other
                for kind, new, other in variations.select("kind", "code", "other").rows()
                if kind == "CS" and new == code and other != continued.get(code)
            )
            listed = [names[other] for other in predecessors]
            joined = " and ".join(listed) if len(listed) < 3 else f"{', '.join(listed[:-1])}, and {listed[-1]}"
            event(
                "absorbed" if code in continued else "merged",
                day,
                [*((area_of(other), "from") for other in predecessors), (coded[code], "to")],
                f"{joined} merged into {successors[code]}, which took the new code {code}."
                if code in continued
                else f"{joined} merged to form {successors[code]}.",
            )
        for kind, code, other, day in variations.select("kind", "code", "other", "date").rows():
            if kind == "AQES":
                event(
                    "absorbed",
                    day,
                    [(area_of(other), "from"), (coded[code], "to")],
                    f"{names[code]} absorbed {names[other]}.",
                )
            if kind == "CE":
                event(
                    "boundary_changed",
                    day,
                    [(coded[code], "from"), (coded[other], "to")],
                    f"Part of {names[code]} was transferred to {names[other]}.",
                )

        for scheme, vintage, start, end, on in (
            ("it.comune", "2022", None, LAST, ELECTIONS[2022]),
            ("it.comune", "2025", LAST, None, ELECTIONS[2025]),
            ("it.regione", "2025", None, None, ELECTIONS[2025]),
            ("it.circoscrizione_camera", "2020", None, None, ELECTIONS[2022]),
            ("it.plurinominale_camera", "2020", None, None, ELECTIONS[2022]),
            ("it.uninominale_camera", "2020", None, None, ELECTIONS[2022]),
            ("it.plurinominale_senato_2020", "2020", None, None, ELECTIONS[2022]),
            ("it.collegio_senato_2020", "2020", None, None, ELECTIONS[2022]),
        ):
            cs.add("boundary_set", scheme_code=scheme, vintage=vintage, valid_from=start, valid_to=end, as_of=on)
        return cs.write()
