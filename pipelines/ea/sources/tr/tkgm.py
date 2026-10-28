import csv
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import UNLICENSED
from ea.store import DATA, connect

SOURCE = "tr.tkgm"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
COUNTIES = Path.home() / "Downloads" / "counties"
URL = "https://parselsorgu.tkgm.gov.tr/"
PARENTS = Path(__file__).with_name("tkgm_parents.tsv")
RENAMED = {"19 Mayıs": "19-May"}
TOLERANCE = 10
GAP = 50
ELECTION = date(2023, 5, 14)
CREATIONS = (
    (date(1953, 7, 15), "day", "64", "43", {"Eşme": "45"}),
    (date(1954, 6, 22), "day", "02", "44", {}),
    (date(1954, 6, 22), "day", "54", "41", {}),
    (date(1954, 6, 30), "month", "50", "51", {"Avanos": "40", "Hacıbektaş": "40", "Kozaklı": "40"}),
    (date(1989, 6, 21), "day", "68", "51", {"Ağaçören": "06", "Sarıyahşi": "06"}),
    (date(1989, 6, 21), "day", "69", "29", {}),
    (date(1989, 6, 21), "day", "70", "42", {}),
    (date(1989, 6, 21), "day", "71", "06", {}),
    (date(1990, 5, 18), "day", "72", "56", {"Gercüş": "47", "Hasankeyf": "47"}),
    (
        date(1990, 5, 18),
        "day",
        "73",
        "56",
        {"Cizre": "47", "İdil": "47", "Silopi": "47", "Beytüşşebap": "30", "Uludere": "30"},
    ),
    (date(1991, 9, 7), "day", "74", "67", {}),
    (date(1992, 6, 3), "day", "75", "36", {}),
    (date(1992, 6, 3), "day", "76", "36", {}),
    (date(1995, 6, 6), "day", "77", "34", {}),
    (date(1995, 6, 6), "day", "78", "67", {"Eskipazar": "18", "Ovacık": "18"}),
    (date(1995, 6, 6), "day", "79", "27", {}),
    (date(1996, 10, 28), "day", "80", "01", {}),
    (date(1999, 12, 9), "day", "81", "14", {}),
)
ABOLITION = (date(1954, 6, 30), "month", date(1957, 6, 19), "day", ("40", "50"))
NOTES = (
    "TKGM's boundaries as retrieved in July 2024, with Şişli's and İskenderun's in October 2026; no license stated. Provinces "
    "before each province's creation are unions of today's ilçeler, dated by the creation laws: Uşak 1953-07-15 "
    "(Law 6129, RG 8458); Adıyaman and Sakarya 1954-06-22 (Laws 6418 and 6419); Nevşehir, with Kırşehir made one "
    "of its ilçeler, mid-1954 (Law 6429, adopted 1954-06-30 per TÜBA, 1954-07-20 per other sources); Kırşehir "
    "restored 1957-06-19 (Law 7001, RG 9637); Aksaray, Bayburt, Karaman, and Kırıkkale 1989-06-21 (Law 3578, "
    "RG 20202); Batman and Şırnak 1990-05-18 (Law 3647, RG 20522); Bartın 1991-09-07 (Law 3760, RG 20984); "
    "Ardahan and Iğdır 1992-06-03 (Law 3806, RG 21247); Yalova, Karabük, and Kilis 1995-06-06 (KHK 550, RG "
    "22305); Osmaniye 1996-10-28 (Law 4200, RG 22801); Düzce 1999-12-09 (KHK 584, RG 23091). Each new province's "
    "ilçeler are assigned to the province named in its law, except those listed from another province; transfers "
    "between existing provinces are not modeled. Each ilçe's outline before its first election with its own results "
    "is drawn inside the ilçe it was carved from, so an ilçe's outline at an election is the union of today's "
    "ilçeler that reported under it; ilçeler that existed in 1961 keep today's outline back to then, and villages "
    "moved between existing ilçeler are not modeled. The parent of each of the 360 ilçeler that first reported "
    "after 1961 is in tkgm_parents.tsv: 69 from the split laws (5747, 6360, and the metropolitan laws), 138 where "
    "the ilçe's Turkish Wikipedia article names a single neighboring ilçe in the sentence on its creation, and 153 "
    "from the neighboring ilçe in the same province whose registered voters fell by the new ilçe's at its first "
    "election. Electoral districts at each election are the union of the ilçeler that reported in them."
)


def key(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    text = text.replace("â", "a").replace("î", "i").replace("û", "u").strip()
    return re.sub(r"[^a-zçğöşü0-9]", "", re.sub(r"\s+(mah\.?|mahallesi|köyü)$", "", text))


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    files = sorted(COUNTIES.glob("*.json"))
    raw.store(
        SOURCE,
        URL,
        gzip.compress(
            "".join(
                json.dumps(
                    {"file": unicodedata.normalize("NFC", file.name), **json.loads(file.read_bytes())},
                    ensure_ascii=False,
                )
                + "\n"
                for file in files
            ).encode(),
            mtime=0,
        ),
        suffix=".jsonl.gz",
        retrieved_at=datetime.fromtimestamp(max(file.stat().st_mtime for file in files), UTC),
        media_type="application/gzip",
    )


def parse() -> None:
    with connect() as conn:
        provinces: dict[str, str] = {}
        titles: dict[str, str] = {}
        for plate, area, name in conn.execute(
            """select c.code, a.id::text, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'tr.il'"""
        ).fetchall():
            provinces[plate] = area
            titles[area] = key(name)
        plates = {area: plate for plate, area in provinces.items()}
        districts: dict[tuple[str, str], str] = {}
        names: dict[str, tuple[str, str]] = {}
        for area, name, parent in conn.execute(
            """select a.id::text, n.text, p.parent_id::text from area a
               join name n on n.entity_id = a.id and n.is_preferred
               join area_parent p on p.area_id = a.id and p.valid_to is null
               where a.scheme_code = 'tr.ilce'"""
        ).fetchall():
            names[area] = (plates[parent], name)
            districts[plates[parent], "merkez" if key(name) == f"{titles[parent]}merkez" else key(name)] = area
        historical: dict[str, date] = {}
        lookup = {(plate, name): area for area, (plate, name) in names.items()}
        for area, name, plate, end in conn.execute(
            """select a.id::text, n.text, c.code, p.valid_to from area a
               join name n on n.entity_id = a.id and n.is_preferred
               join area_parent p on p.area_id = a.id and p.valid_to is not null
               join area_code c on c.area_id = p.parent_id
               where a.scheme_code = 'tr.ilce'
                 and not exists (select from area_parent q where q.area_id = a.id and q.valid_to is null)"""
        ).fetchall():
            historical[area] = end
            lookup[plate, name] = area
        starts: dict[str, date] = {}
        parent_of: dict[str, str] = {}
        with PARENTS.open() as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                child = lookup[row["plate"], row["ilce"]]
                starts[child] = date.fromisoformat(row["first"])
                parent_of[child] = lookup[row["parent_plate"], row["parent"]]

        def owner(area: str, day: date) -> str | None:
            while area in starts and day < starts[area]:
                area = parent_of[area]
            return None if area in historical and day >= historical[area] else area

        neighborhoods: dict[tuple[str, str], list[str]] = defaultdict(list)
        for area, name, parent in conn.execute(
            """select a.id::text, n.text, p.parent_id::text from area a
               join name n on n.entity_id = a.id and n.is_preferred
               join area_parent p on p.area_id = a.id and p.valid_to is null
               where a.scheme_code = 'tr.mahalle'"""
        ).fetchall():
            neighborhoods[parent, key(name)].append(area)
        compositions: dict[str, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
        for code, area, held in conn.execute(
            """select distinct c.code, ia.id::text, e.date_from
               from contest t join election e on e.id = t.election_id
               join area d on d.id = t.district_area_id and d.scheme_code = 'tr.secim_cevresi'
               join area_code c on c.area_id = d.id
               join result_set s on s.contest_id = t.id
               join result_rollup r on r.result_set_k = s.k
               join area ia on ia.k = r.area_k and ia.scheme_code = 'tr.ilce'"""
        ).fetchall():
            compositions[code][held].add(area)
        polls = sorted(
            day
            for (day,) in conn.execute(
                """select distinct e.date_from from election e join polity p on p.id = e.polity_id
                   where p.iso_code = 'TR' and e.date_from is not null"""
            ).fetchall()
        )
        constituencies = dict(
            conn.execute(
                """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'tr.secim_cevresi'"""
            ).fetchall()
        )

        features = []
        found = set()
        with gzip.open(raw.cached(SOURCE, URL).path, "rt") as lines:
            for line in lines:
                data = json.loads(line)
                for feature in data["features"]:
                    properties = feature["properties"]
                    plate = f"{properties['CityId']:02d}"
                    county = RENAMED.get(properties["County"], properties["County"])
                    area = districts.get((plate, key(county)))
                    if area is None:
                        raise ValueError(f"{data['file']}: no ilçe {plate} {county}")
                    found.add(area)
                    features.append((area, key(properties["District"]), json.dumps(feature["geometry"])))
        if missing := sorted(names[area] for area in names.keys() - found):
            raise ValueError(f"ilçeler without a file: {missing}")

        breaks = sorted({day for day, *_ in CREATIONS} | {ABOLITION[0], ABOLITION[2]})
        precisions = {day: precision for day, precision, *_ in CREATIONS} | {
            ABOLITION[0]: ABOLITION[1],
            ABOLITION[2]: ABOLITION[3],
        }
        created = {child: (day, parent, moved) for day, _, child, parent, moved in CREATIONS}

        def province(plate: str, name: str, day: date) -> str | None:
            while plate in created and day < created[plate][0]:
                plate = created[plate][2].get(name, created[plate][1])
            if plate in ABOLITION[4] and ABOLITION[0] <= day < ABOLITION[2]:
                return None
            return plate

        periods = list(zip([None, *breaks], [*breaks, None], strict=True))
        versions: list[tuple[str, str, date | None, date | None, frozenset[str], bool]] = []
        for plate in provinces:
            history: list[tuple[date | None, date | None, frozenset[str]]] = []
            for start, end in periods:
                members = frozenset(
                    area
                    for area, (home, name) in names.items()
                    if province(home, name, start or date(1950, 1, 1)) == plate
                )
                if members and history and history[-1][1] == start and history[-1][2] == members:
                    history[-1] = (history[-1][0], end, members)
                elif members:
                    history.append((start, end, members))
            versions += [("il", plate, start, end, members, end is None) for start, end, members in history]
        changes = sorted(set(starts.values()) | set(historical.values()))
        spans = list(zip([None, *changes], [*changes, None], strict=True))
        held_by: dict[date | None, dict[str, frozenset[str]]] = {}
        for start, _ in spans:
            owned: dict[str, set[str]] = defaultdict(set)
            for area in names:
                if (holder := owner(area, start or date(1950, 1, 1))) is not None:
                    owned[holder].add(area)
            held_by[start] = {holder: frozenset(members) for holder, members in owned.items()}
        for area in [*names, *historical]:
            history = []
            for start, end in spans:
                members = held_by[start].get(area)
                if members and history and history[-1][1] == start and history[-1][2] == members:
                    history[-1] = (history[-1][0], end, members)
                elif members:
                    history.append((start, end, members))
            versions += [("ilce", area, start, end, members, members == {area}) for start, end, members in history]
        elections = sorted({day for held in compositions.values() for day in held})
        for code, held in compositions.items():
            history = []
            for day in sorted(held):
                members = frozenset(area for area in names if owner(area, day) in held[day])
                if history and history[-1][2] == members:
                    continue
                if history:
                    history[-1] = (history[-1][0], day, history[-1][2])
                history.append((day, None, members))
            history[-1] = (history[-1][0], next((day for day in elections if day > max(held)), None), history[-1][2])
            versions += [
                ("secim_cevresi", code, start, end, members, all(owner(area, start) == area for area in members))
                for start, end, members in history
            ]

        lineage = []
        for area, (home, name) in names.items():
            history = []
            for start, end in periods:
                plate = province(home, name, start or date(1950, 1, 1))
                if plate is not None and history and history[-1][1] == start and history[-1][2] == plate:
                    history[-1] = (history[-1][0], end, plate)
                elif plate is not None:
                    history.append((start, end, plate))
            lineage += [(area, provinces[plate], start, end) for start, end, plate in history]
        boundaries = []
        for scheme, ranges in (
            ("tr.il", periods),
            ("tr.ilce", spans),
            ("tr.secim_cevresi", list(zip(elections, [*elections[1:], None], strict=True))),
        ):
            for start, end in ranges:
                inside = [day for day in polls if (start is None or day >= start) and (end is None or day < end)]
                if not inside:
                    continue
                vintage = "2024" if end is None and scheme != "tr.secim_cevresi" else inside[0].isoformat()
                as_of = ELECTION if end is None and scheme != "tr.secim_cevresi" else inside[0]
                boundaries.append((scheme, vintage, start, end, as_of))

        conn.execute("create temp table feature (district text, neighborhood text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (district, neighborhood, geojson) from stdin") as copy:
            for row in features:
                copy.write_row(row)
        conn.execute(
            "create temp table version (id int, layer text, code text, valid_from date, valid_to date, exact boolean) "
            "on commit drop"
        )
        conn.execute("create temp table member (id int, district text) on commit drop")
        with (
            conn.cursor() as cursor,
            cursor.copy("copy version (id, layer, code, valid_from, valid_to, exact) from stdin") as copy,
        ):
            for index, (layer, code, start, end, _, exact) in enumerate(versions):
                copy.write_row((index, layer, code, start, end, exact))
        with conn.cursor() as cursor, cursor.copy("copy member (id, district) from stdin") as copy:
            for index, (*_, members, _) in enumerate(versions):
                for area in members:
                    copy.write_row((index, area))
        conn.execute(
            """create temp table unit on commit drop as
               select district, neighborhood,
                      st_setsrid(st_coveragesimplify(geom, %s) over (), 3035) as geom
               from (select district, neighborhood, st_setsrid(st_coverageclean(geom, %s) over (), 3035) as geom
                     from (select district, neighborhood,
                                  st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(st_force2d(
                                    st_transform(st_setsrid(st_geomfromgeojson(geojson), 4326), 3035))))), 3)) as geom
                           from feature group by district, neighborhood) features) cleaned""",
            [TOLERANCE, GAP],
        )
        conn.execute(
            """create temp table district on commit drop as
               select district, st_setsrid(st_coverageunion(geom), 3035) as geom from unit group by district"""
        )
        rows = conn.execute(
            """with layers as (
                 select 'mahalle' as layer, district, neighborhood as code, null::date as valid_from,
                        null::date as valid_to, true as exact, geom
                 from unit
                 union all
                 select v.layer, null, v.code, v.valid_from, v.valid_to, v.exact,
                        st_setsrid(st_coverageunion(d.geom), 3035)
                 from version v join member m on m.id = v.id join district d on d.district = m.district
                 group by v.id, v.layer, v.code, v.valid_from, v.valid_to, v.exact
                 union all
                 select 'nation', null, 'TR', null, null, true, st_setsrid(st_coverageunion(geom), 3035) from district
               )
               select layer, district, code, valid_from, valid_to, exact,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      case when layer <> 'nation'
                        then encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') end
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "district": pl.String,
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "exact": pl.Boolean,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    shapes = frame.filter(pl.col("layer") == "mahalle")
    matched = [
        (area, geom, point)
        for district, code, geom, point in shapes.select("district", "code", "geom", "label_point").rows()
        for area in neighborhoods.get((district, code), [])
    ]
    frame = pl.concat(
        [
            frame.filter(pl.col("layer") != "mahalle").with_columns(
                pl.when(pl.col("layer") == "ilce")
                .then(pl.col("code"))
                .when(pl.col("layer") == "il")
                .then(pl.col("code").replace_strict(provinces, default=None))
                .when(pl.col("layer") == "secim_cevresi")
                .then(pl.col("code").replace_strict(constituencies, default=None))
                .alias("area")
            ),
            pl.DataFrame(
                [("mahalle", None, None, None, None, True, geom, point, area) for area, geom, point in matched],
                schema=frame.schema | {"area": pl.String},
                orient="row",
            ),
        ]
    ).with_columns(
        pl.col("valid_from")
        .replace_strict(precisions, default="day", return_dtype=pl.String)
        .fill_null("day")
        .alias("from_precision"),
        pl.col("valid_to")
        .replace_strict(precisions, default="day", return_dtype=pl.String)
        .fill_null("day")
        .alias("to_precision"),
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    print(counts, f"{len({area for area, *_ in matched})} of {sum(len(v) for v in neighborhoods.values())} mahalleler")
    if (
        set(frame.filter(pl.col("layer") == "ilce")["area"]) < set(names)
        or frame["geom"].null_count()
        or frame.filter(pl.col("layer") != "nation")["area"].null_count()
    ):
        raise ValueError(f"unexpected layers {counts}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("district", "code").write_parquet(PARSED / "areas.parquet")
    dates = {"valid_from": pl.Date, "valid_to": pl.Date}
    pl.DataFrame(lineage, schema={"area": pl.String, "parent": pl.String} | dates, orient="row").with_columns(
        pl.col("valid_from").replace_strict(precisions, default="day", return_dtype=pl.String).alias("from_precision"),
        pl.col("valid_to").replace_strict(precisions, default="day", return_dtype=pl.String).alias("to_precision"),
    ).write_parquet(PARSED / "parents.parquet")
    pl.DataFrame(
        boundaries, schema={"scheme": pl.String, "vintage": pl.String} | dates | {"as_of": pl.Date}, orient="row"
    ).with_columns(
        pl.col("valid_from").replace_strict(precisions, default="day", return_dtype=pl.String).alias("from_precision"),
        pl.col("valid_to").replace_strict(precisions, default="day", return_dtype=pl.String).alias("to_precision"),
    ).write_parquet(PARSED / "boundaries.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Turkey's mahalleler from TKGM's 2024 administrative boundaries, with ilçeler, provinces, İstanbul's, "
                "Ankara's, İzmir's, and Bursa's electoral districts, and the nation as their unions, each "
                "province's outline before the provinces created since 1953, each ilçe's outline before the ilçeler "
                "carved from it since 1961, the electoral districts of every general election since 1983, the "
                "province each ilçe belonged to on each date, and a boundary set for each era"
            ),
            polity="TR",
        )
        title = "Parsel Sorgu: il, ilçe, and mahalle boundaries"
        source = cs.source("Tapu ve Kadastro Genel Müdürlüğü", title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Tapu ve Kadastro Genel Müdürlüğü",
            title=title,
            url=URL,
            license=UNLICENSED,
            license_url=None,
            attribution="Tapu ve Kadastro Genel Müdürlüğü",
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        nation = cs.area("tr.nation", "TR")
        cs.manifest["scope"] = {
            "polity": "TR",
            "replaces": {"area_geometry": ["tr.reforms"], "area_parent": ["tr.oveo"]},
        }
        for layer, area, valid_from, from_precision, valid_to, to_precision, exact, geom, point in frame.select(
            "layer", "area", "valid_from", "from_precision", "valid_to", "to_precision", "exact", "geom", "label_point"
        ).rows():
            cs.add(
                "area_geometry",
                area_id=nation if layer == "nation" else UUID(area),
                valid_from=valid_from,
                valid_from_precision=from_precision,
                valid_to=valid_to,
                valid_to_precision=to_precision,
                geom=geom,
                label_point=point,
                quality="official" if exact else "synthetic",
                source_id=source,
            )
        for area, parent, valid_from, valid_to, from_precision, to_precision in pl.read_parquet(
            PARSED / "parents.parquet"
        ).rows():
            cs.add(
                "area_parent",
                area_id=UUID(area),
                parent_id=UUID(parent),
                valid_from=valid_from,
                valid_from_precision=from_precision or "day",
                valid_to=valid_to,
                valid_to_precision=to_precision or "day",
            )
        existing = dict(
            conn.execute(
                "select scheme_code || ' ' || vintage, id from boundary_set where starts_with(scheme_code, 'tr.')"
            ).fetchall()
        )
        cs.add(
            "boundary_set",
            id=existing.get("tr.mahalle 2024"),
            scheme_code="tr.mahalle",
            vintage="2024",
            valid_from=None,
            valid_from_precision="day",
            valid_to=None,
            valid_to_precision="day",
            as_of=ELECTION,
        )
        for scheme, vintage, valid_from, valid_to, as_of, from_precision, to_precision in pl.read_parquet(
            PARSED / "boundaries.parquet"
        ).rows():
            cs.add(
                "boundary_set",
                id=existing.get(f"{scheme} {vintage}"),
                scheme_code=scheme,
                vintage=vintage,
                valid_from=valid_from,
                valid_from_precision=from_precision or "day",
                valid_to=valid_to,
                valid_to_precision=to_precision or "day",
                as_of=as_of,
            )
        return cs.write()
