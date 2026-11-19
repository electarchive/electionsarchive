"""Romania's Great National Assembly elections of 1957 to 1985, by region and county, from the communiqués of the
Central Electoral Commission.

After each election to the Great National Assembly (Marea Adunare Națională) the Central Electoral Commission
published a communiqué with the national totals and a table by region (until 1968) or county (from 1968): the
registered voters, the votes cast, the votes for the candidates of the Front, the votes against them, and the
annulled votes. The Buletinul Oficial printed the communiqués, and the party daily Scînteia reprinted them in full the
day they were released. The Buletinul Oficial issues of the period are not reachable, so the communiqués are read from
Scînteia's pages, as the Hunedoara county library (Biblioteca Județeană „Ovid Densusianu" Hunedoara-Deva) serves them.
The 1975 communiqué is read from Scînteia tineretului, the youth daily, because the library lacks Scînteia's issue of
11 March 1975.

Each table was read from the page image into `scanteia_tables/{day}.tsv`, in printed order with the printed total
row last, and each figure keeps its page and box in `scanteia_tables/{day}.boxes.json`. Parsing checks every row
(votes cast equal the votes for plus the votes against plus the annulled votes) and every column against the printed
total, and raises on a mismatch the source does not itself print.

Every deputy was elected in a single-member constituency (circumscripție electorală). The communiqués print the
results by region or county only, so each election is stored as one contest for all the Assembly's seats, by region or
county. The 1948 election (several lists, provisional county results split across two issues) and the 1952 election
(no issue in the library) are not covered.

The referendum of 23 November 1986 on cutting Romania's armaments, troops, and military spending by 5 percent is read
the same way, from the communiqué of the Central Commission for the Referendum that Scînteia printed on 25 November
1986. Its two tables give, by county, the citizens who took part and their yes and no votes, and the young people aged
14 to 18 who were consulted alongside and their answers; they are stored as two questions of one referendum. The
communiqué gives the registered voters and the young people listed nationally only.
"""

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import shapely

from ea import package, raw
from ea.package import Package

SOURCE = "ro.scanteia"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
LIBRARY = "https://www.bibliotecadeva.ro/periodice/"
BOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ROU/ADM1/"
    "geoBoundaries-ROU-ADM1_simplified.geojson"
)
TABLES = Path(__file__).with_name("scanteia_tables")
GEO, REGISTRY = f"{SOURCE}/geo", f"{SOURCE}/registry"
REGIONS, COUNTIES = "ro.regiune", "ro.judet"
VOTES, REGISTERED, CAST, INVALID, VOTERS = 1, 2, 3, 5, 14
DIGITIZED = 8
FIELDS = ("constituencies", "candidates", "registered", "cast", "for", "against", "null")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ASSEMBLY = key("body", "marea-adunare-nationala")
SYSTEM = key("electoral_system", "single-member-majority")
REFERENDUM_SYSTEM = key("electoral_system", "referendum")
GEOBOUNDARIES = key("source", "geoboundaries-adm1")


class Front(NamedTuple):
    code: str
    name: str
    english: str
    abbreviation: str


FRONTS = {
    "fdp": Front("fdp", "Frontul Democrației Populare", "People's Democratic Front", "FDP"),
    "fus": Front("fus", "Frontul Unității Socialiste", "Front of Socialist Unity", "FUS"),
    "fdus": Front(
        "fdus", "Frontul Democrației și Unității Socialiste", "Front of Socialist Democracy and Unity", "FDUS"
    ),
}


@dataclass(frozen=True)
class Election:
    day: date
    path: str  # the issue's path under the library's periodicals folder
    paper: str
    issue: str
    printed: date
    page: int
    front: str
    seats: int
    plan: str  # the administrative plan whose units the table prints
    cast_label: str  # the column heading the table gives the votes cast


ELECTIONS = [
    Election(
        date(1957, 2, 3),
        "scanteia/1957/02/scinteia_1957_02_3823.pdf",
        "Scînteia",
        "3823",
        date(1957, 2, 6),
        1,
        "fdp",
        437,
        "1956",
        "Votanți",
    ),
    Election(
        date(1961, 3, 5),
        "scanteia/1961/03/scinteia_1961_03_5097.pdf",
        "Scînteia",
        "5097",
        date(1961, 3, 8),
        1,
        "fdp",
        465,
        "1960",
        "Voturi exprimate",
    ),
    Election(
        date(1965, 3, 7),
        "scanteia/1965/03/scanteia_1965_03_6554.pdf",
        "Scînteia",
        "6554",
        date(1965, 3, 10),
        1,
        "fdp",
        465,
        "1960",
        "Voturi exprimate",
    ),
    Election(
        date(1969, 3, 2),
        "scanteia/1969/03/scanteia_1969_03_7994.pdf",
        "Scînteia",
        "7994",
        date(1969, 3, 4),
        1,
        "fus",
        465,
        "1968",
        "Nr. total al voturilor exprimate",
    ),
    Election(
        date(1975, 3, 9),
        "scinteiatin/1975/03/scinteia_tineretului_1975_03_8025.pdf",
        "Scînteia tineretului",
        "8025",
        date(1975, 3, 11),
        5,
        "fus",
        349,
        "1968",
        "Voturi exprimate",
    ),
    Election(
        date(1980, 3, 9),
        "scanteia/1980/03/scanteia_1980_03_11680.pdf",
        "Scînteia",
        "11680",
        date(1980, 3, 11),
        1,
        "fdus",
        369,
        "1968",
        "Voturi exprimate",
    ),
    Election(
        date(1985, 3, 17),
        "scanteia/1985/03/scanteia_1985_03_13241.pdf",
        "Scînteia",
        "13241",
        date(1985, 3, 19),
        1,
        "fdus",
        369,
        "1981",
        "Voturi exprimate",
    ),
]

# The areas each plan's table prints, keyed by a code; the printed names map onto these codes. Regions changed
# boundaries with the reform of December 1960, so the two region plans have separate areas. The county plan of 1968
# changed in 1981 only around Bucharest: Ilfov and Ialomița were divided into Călărași, Giurgiu, Ialomița, and the
# Ilfov agricultural sector, which the 1985 table counts with the municipality of Bucharest.
REGION_1956 = {
    "orasul-bucuresti": "Orașul București",
    "bacau": "Regiunea Bacău",
    "baia-mare": "Regiunea Baia Mare",
    "bucuresti": "Regiunea București",
    "cluj": "Regiunea Cluj",
    "constanta": "Regiunea Constanța",
    "craiova": "Regiunea Craiova",
    "galati": "Regiunea Galați",
    "hunedoara": "Regiunea Hunedoara",
    "iasi": "Regiunea Iași",
    "oradea": "Regiunea Oradea",
    "pitesti": "Regiunea Pitești",
    "ploesti": "Regiunea Ploești",
    "stalin": "Regiunea Stalin",
    "suceava": "Regiunea Suceava",
    "timisoara": "Regiunea Timișoara",
    "autonoma-maghiara": "Regiunea Autonomă Maghiară",
}
REGION_1960 = {
    "arges": "Regiunea Argeș",
    "bacau": "Regiunea Bacău",
    "banat": "Regiunea Banat",
    "brasov": "Regiunea Brașov",
    "bucuresti": "Regiunea București",
    "cluj": "Regiunea Cluj",
    "crisana": "Regiunea Crișana",
    "dobrogea": "Regiunea Dobrogea",
    "galati": "Regiunea Galați",
    "hunedoara": "Regiunea Hunedoara",
    "iasi": "Regiunea Iași",
    "maramures": "Regiunea Maramureș",
    "mures-autonoma-maghiara": "Regiunea Mureș-Autonomă Maghiară",
    "oltenia": "Regiunea Oltenia",
    "ploiesti": "Regiunea Ploiești",
    "suceava": "Regiunea Suceava",
    "orasul-bucuresti": "Orașul București",
    "orasul-constanta": "Orașul Constanța",
}
COUNTY_NAMES = {
    "alba": "Alba",
    "arad": "Arad",
    "arges": "Argeș",
    "bacau": "Bacău",
    "bihor": "Bihor",
    "bistrita-nasaud": "Bistrița-Năsăud",
    "botosani": "Botoșani",
    "brasov": "Brașov",
    "braila": "Brăila",
    "buzau": "Buzău",
    "caras-severin": "Caraș-Severin",
    "calarasi": "Călărași",
    "cluj": "Cluj",
    "constanta": "Constanța",
    "covasna": "Covasna",
    "dambovita": "Dîmbovița",
    "dolj": "Dolj",
    "galati": "Galați",
    "giurgiu": "Giurgiu",
    "gorj": "Gorj",
    "harghita": "Harghita",
    "hunedoara": "Hunedoara",
    "ialomita": "Ialomița",
    "iasi": "Iași",
    "ilfov": "Ilfov",
    "maramures": "Maramureș",
    "mehedinti": "Mehedinți",
    "mures": "Mureș",
    "neamt": "Neamț",
    "olt": "Olt",
    "prahova": "Prahova",
    "satu-mare": "Satu Mare",
    "salaj": "Sălaj",
    "sibiu": "Sibiu",
    "suceava": "Suceava",
    "teleorman": "Teleorman",
    "timis": "Timiș",
    "tulcea": "Tulcea",
    "vaslui": "Vaslui",
    "valcea": "Vîlcea",
    "vrancea": "Vrancea",
    "bucuresti": "Municipiul București",
}
# Counties whose 1968 area differs from their 1981 one, and the counties created in 1981.
CHANGED_1981 = {"bucuresti", "ilfov", "ialomita"}
NEW_1981 = {"calarasi", "giurgiu"}
PLANS = {
    "1956": (REGIONS, date(1956, 12, 27), date(1960, 12, 24)),
    "1960": (REGIONS, date(1960, 12, 24), date(1968, 2, 17)),
    "1968": (COUNTIES, date(1968, 2, 17), date(1981, 1, 23)),
    "1981": (COUNTIES, date(1981, 1, 23), None),
}


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.replace("ș", "s").replace("ț", "t")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def code_of(plan: str, printed: str) -> str:
    """The area code of a printed unit name."""
    text = slug(printed)
    text = re.sub(r"^(reg|regiunea|municipiul)-", "", text)
    text = re.sub(r"^oras(ul)?-", "orasul-", text)
    if plan == "1956" and text == "orasul-bucuresti":
        return text
    if plan == "1956" and text == "autonoma-maghiara":
        return text
    if plan == "1960" and text.startswith("mures"):
        return "mures-autonoma-maghiara"
    text = {"dimbovita": "dambovita", "vilcea": "valcea"}.get(text, text)
    names = REGION_1956 if plan == "1956" else REGION_1960 if plan == "1960" else COUNTY_NAMES
    if text not in names:
        raise ValueError(f"{printed!r} is not a unit of the {plan} plan")
    return text


def area_key(plan: str, code: str) -> str:
    """Areas that did not change between the 1968 and 1981 county plans share one id."""
    if plan in ("1956", "1960"):
        return f"{plan}:{code}"
    if plan == "1968" and code in CHANGED_1981:
        return f"1968:{code}"
    if plan == "1981" and code in CHANGED_1981:
        return f"1981:{code}"
    return code


def area_id(plan: str, code: str) -> str:
    return key("area", PLANS[plan][0], area_key(plan, code))


def area_name(plan: str, code: str) -> str:
    if plan == "1956":
        return REGION_1956[code]
    if plan == "1960":
        return REGION_1960[code]
    name = COUNTY_NAMES[code]
    if code == "bucuresti" and plan == "1981":
        return "Municipiul București (with the Ilfov agricultural sector)"
    return name if code == "bucuresti" else f"Județul {name}"


class Row(NamedTuple):
    printed: str
    values: dict[str, int | None]  # None where the table prints no such column; 0 where it prints a dash


@cache
def table(day: date) -> tuple[list[Row], Row]:
    rows: list[Row] = []
    for line in (TABLES / f"{day}.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, *cells = line.split("\t")
        if len(cells) != len(FIELDS):
            raise ValueError(f"{day}: {name} has {len(cells)} cells")
        values = {f: None if c == "" else 0 if c == "-" else int(c) for f, c in zip(FIELDS, cells, strict=True)}
        rows.append(Row(name, values))
    return rows[:-1], rows[-1]


# Cells the source prints inconsistently, kept as printed: (day, unit, field) -> what the other figures imply.
MISPRINTS = {
    (date(1969, 3, 2), "MUNICIPIUL BUCUREȘTI", "cast"): 1_211_323,
}


def check(e: Election) -> None:
    rows, total = table(e.day)
    for row in rows:
        v = row.values
        parts = (v["for"] or 0) + (v["against"] or 0) + (v["null"] or 0)
        expected = MISPRINTS.get((e.day, row.printed, "cast"), v["cast"])
        if expected != parts:
            raise ValueError(f"{e.day} {row.printed}: votes cast {v['cast']} are not for + against + null {parts}")
        if (v["registered"] or 0) < (v["cast"] or 0):
            raise ValueError(f"{e.day} {row.printed}: more votes cast than registered voters")
    for f in FIELDS:
        printed = total.values[f]
        if printed is None or all(r.values[f] is None for r in rows):
            continue
        summed = sum(r.values[f] or 0 for r in rows) - sum(
            given - MISPRINTS[(e.day, r.printed, f)]
            for r in rows
            if (given := r.values[f]) is not None and (e.day, r.printed, f) in MISPRINTS
        )
        if summed != printed:
            raise ValueError(f"{e.day} {f}: rows add up to {summed}, the total row prints {printed}")
    seats = total.values["constituencies"]
    if seats is not None and seats != e.seats:
        raise ValueError(f"{e.day}: {seats} constituencies printed, {e.seats} expected")
    for row in rows:
        code_of(e.plan, row.printed)


@dataclass(frozen=True)
class Referendum:
    day: date
    path: str
    paper: str
    issue: str
    printed: date
    page: int
    plan: str
    registered: int  # the citizens on the voting lists, which the communiqué gives nationally only
    youth_registered: int  # the young people aged 14 to 18 on the consultation lists, nationally only


REFERENDUM = Referendum(
    date(1986, 11, 23),
    "scanteia/1986/11/scanteia_1986_11_13767.pdf",
    "Scînteia",
    "13767",
    date(1986, 11, 25),
    1,
    "1981",
    16_073_845,
    1_577_357,
)
REFERENDUM_FIELDS = ("cast", "yes", "no", "youth_cast", "youth_yes", "youth_no")


@cache
def referendum_table(day: date) -> tuple[list[Row], Row]:
    rows: list[Row] = []
    for line in (TABLES / f"{day}.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, *cells = line.split("\t")
        if len(cells) != len(REFERENDUM_FIELDS):
            raise ValueError(f"{day}: {name} has {len(cells)} cells")
        values: dict[str, int | None] = {
            f: 0 if c == "-" else int(c) for f, c in zip(REFERENDUM_FIELDS, cells, strict=True)
        }
        rows.append(Row(name, values))
    return rows[:-1], rows[-1]


def check_referendum(r: Referendum) -> None:
    rows, total = referendum_table(r.day)
    for row in rows:
        v = row.values
        for prefix in ("", "youth_"):
            if v[f"{prefix}cast"] != (v[f"{prefix}yes"] or 0) + (v[f"{prefix}no"] or 0):
                raise ValueError(f"{r.day} {row.printed}: {prefix}participants are not yes + no")
        code_of(r.plan, row.printed)
    for f in REFERENDUM_FIELDS:
        summed = sum(row.values[f] or 0 for row in rows)
        if summed != total.values[f]:
            raise ValueError(f"{r.day} {f}: rows add up to {summed}, the total row prints {total.values[f]}")
    if (total.values["cast"] or 0) > r.registered or (total.values["youth_cast"] or 0) > r.youth_registered:
        raise ValueError(f"{r.day}: more participants than registered")


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{e.day}" for e in ELECTIONS), f"{SOURCE}/{REFERENDUM.day}"]


def fetch() -> None:
    for e in ELECTIONS:
        raw.fetch(SOURCE, LIBRARY + e.path, suffix=".pdf")
    raw.fetch(SOURCE, LIBRARY + REFERENDUM.path, suffix=".pdf")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".geojson")


def parse() -> None:
    for e in ELECTIONS:
        check(e)
    check_referendum(REFERENDUM)
    geo().write()
    registry().write()
    for e in ELECTIONS:
        build(e).write()
    build_referendum(REFERENDUM).write()


def emit() -> None:
    package.run("load", packages())


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
    parts = [part for part in shapely.get_parts(found) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Romania's regions of 1956 and 1960 and counties of 1968 and 1981 as the Great National Assembly communiqués "
        "report them, with the county outlines geoBoundaries draws",
    )
    p.external.add(ROMANIA)
    p.add("scheme", code=REGIONS, polity_id=ROMANIA, kind="administrative", rank=1, noun="region")
    p.add("scheme", code=COUNTIES, polity_id=ROMANIA, kind="administrative", rank=1, noun="county")
    p.add(
        "source",
        id=GEOBOUNDARIES,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from the World Bank",
        title="geoBoundaries gbOpen: Romania ADM1 (counties), simplified",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries; World Bank",
        notes=(
            "Boundary year 2017, built December 2023. Used as the nearest open outline of the counties of 1968 and "
            "1981; communes moved between counties since then are not reflected."
        ),
    )
    stored = raw.cached(SOURCE, BOUNDARIES)
    p.artifact(stored, GEOBOUNDARIES)
    shapes = {
        slug(str(f["properties"]["shapeName"])): _shape(f) for f in json.loads(stored.path.read_bytes())["features"]
    }
    used: dict[str, tuple[str, str]] = {}
    for e in ELECTIONS:
        for row in table(e.day)[0]:
            code = code_of(e.plan, row.printed)
            used.setdefault(area_key(e.plan, code), (e.plan, code))
    for row in referendum_table(REFERENDUM.day)[0]:
        code = code_of(REFERENDUM.plan, row.printed)
        used.setdefault(area_key(REFERENDUM.plan, code), (REFERENDUM.plan, code))
    for ak, (plan, code) in sorted(used.items()):
        scheme, valid_from, valid_to = PLANS[plan]
        area = area_id(plan, code)
        p.add("area", id=area, scheme_code=scheme)
        shared = plan in ("1968", "1981") and ak == code
        since = PLANS["1981"][1] if code in NEW_1981 else PLANS["1968"][1] if shared else valid_from
        until = None if shared else valid_to
        p.add("area_code", area_id=area, code=code, valid_from=since, valid_to=until)
        p.add(
            "area_kind",
            area_id=area,
            kind="region" if scheme == REGIONS else "municipality" if code == "bucuresti" else "county",
        )
        p.name(area, area_name(plan, code), "ro", "official")
        geometry: shapely.Geometry | None = None
        if shared:
            geometry = shapes[code]
        elif plan == "1981" and code == "bucuresti":
            geometry = shapely.set_srid(shapely.union(shapes["bucuresti"], shapes["ilfov"]), 4326)
        elif plan == "1981":
            geometry = shapes[code]
        if geometry is not None:
            p.add(
                "area_geometry",
                area_id=area,
                valid_from=since,
                valid_to=until,
                geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=GEOBOUNDARIES,
            )
    for unit in units().values():
        p.add(
            "reporting_unit",
            id=unit.id,
            polity_id=ROMANIA,
            scheme_code=unit.scheme,
            code=unit.code,
            area_relation="equals",
            area_id=area_id(unit.plan, unit.code),
            kind="standard",
            valid_from=unit.valid_from,
            valid_from_precision="day",
            valid_to=unit.valid_to,
            valid_to_precision=None if unit.valid_to is None else "day",
        )
        p.name(unit.id, area_name(unit.plan, unit.code), "ro", "official")
    return p


class Unit(NamedTuple):
    id: str
    scheme: str
    code: str
    plan: str
    valid_from: date
    valid_to: date | None


@cache
def units() -> dict[tuple[str, str], Unit]:
    """One reporting unit per scheme, code, and area, keyed by (scheme, area key).

    Each unit starts on the first election or referendum that reports it and stays open; where a later plan gives the
    same code a different area (the regions of 1960, and Bucharest and Ialomița in 1981), the earlier unit ends on the
    day the later one starts, so no two units of a scheme and code overlap.
    """
    polls = [(e.day, e.plan, [r.printed for r in table(e.day)[0]]) for e in ELECTIONS]
    polls.append((REFERENDUM.day, REFERENDUM.plan, [r.printed for r in referendum_table(REFERENDUM.day)[0]]))
    found: dict[tuple[str, str], Unit] = {}
    for day, plan, printed in sorted(polls):
        scheme = PLANS[plan][0]
        for name in printed:
            code = code_of(plan, name)
            ak = area_key(plan, code)
            if (scheme, ak) not in found:
                found[scheme, ak] = Unit(key("unit", scheme, ak), scheme, code, plan, day, None)
    by_code: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for k, unit in found.items():
        by_code.setdefault((unit.scheme, unit.code), []).append(k)
    for keys in by_code.values():
        keys.sort(key=lambda k: found[k].valid_from)
        for earlier, later in pairwise(keys):
            found[earlier] = found[earlier]._replace(valid_to=found[later].valid_from)
    return found


def unit_id(plan: str, code: str) -> str:
    return units()[PLANS[plan][0], area_key(plan, code)].id


def unshaped() -> list[str]:
    p = geo()
    drawn = {r["area_id"] for r in p.rows.get("area_geometry", [])}
    names = {r["entity_id"]: str(r["text"]) for r in p.rows["name"]}
    return sorted(names[r["id"]] for r in p.rows["area"] if r["id"] not in drawn)


SYSTEM_NOTE = (
    "Each deputy was elected in a single-member constituency. A candidate needed the votes of more than half of the "
    "voters of the constituency; where no candidate reached it, the election was held again. Every candidate stood "
    "for the Front (the People's Democratic Front, then the Front of Socialist Unity, and from 1980 the Front of "
    "Socialist Democracy and Unity), and a voter voted against a candidate by striking the name out. From 1975 "
    "several of the Front's candidates stood in some constituencies."
)


REFERENDUM_SYSTEM_NOTE = (
    "Law no. 20 of 23 October 1986 and Decree no. 360/1986 of the Council of State called a referendum on 23 November "
    "1986 on the 5 percent cut in armaments, troops, and military spending. Citizens with the right to vote answered "
    "yes or no; county commissions and the commission of the municipality of Bucharest drew up the records, which the "
    "Central Commission for the Referendum verified and summed up in a communiqué. Young people aged 14 to 18, who "
    "could not vote, were consulted alongside on lists of their own, and the communiqué reports their answers apart."
)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Romania's Great National Assembly, its electoral system, and the Fronts whose candidates stood from 1957 to "
        "1985, and the rules of the referendum of 1986",
    )
    p.external.add(ROMANIA)
    p.add("body", id=ASSEMBLY, polity_id=ROMANIA, kind="unicameral")
    p.name(ASSEMBLY, "Great National Assembly", "en", "official")
    p.name(ASSEMBLY, "Marea Adunare Națională", "ro", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ROMANIA,
        valid_from=date(1952, 9, 27),
        valid_to=date(1989, 12, 22),
        rules={"formula": "two_round"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    p.add(
        "electoral_system",
        id=REFERENDUM_SYSTEM,
        polity_id=ROMANIA,
        valid_from=date(1986, 10, 23),
        valid_to=date(1989, 12, 22),
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", REFERENDUM_SYSTEM),
        entity_id=REFERENDUM_SYSTEM,
        kind="methodology",
        body_md=REFERENDUM_SYSTEM_NOTE,
        lang="en",
    )
    for front in FRONTS.values():
        party = key("party", front.code)
        p.add("party", id=party, kind="bloc", polity_id=ROMANIA)
        p.name(party, front.name, "ro", "official")
        p.name(party, front.english, "en", "english", False)
        p.name(party, front.abbreviation, "ro", "abbreviation", False)
    return p


def sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def build(e: Election) -> Package:
    rows, total = table(e.day)
    front = FRONTS[e.front]
    unit_noun = "region" if PLANS[e.plan][0] == REGIONS else "county"
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"The Great National Assembly election of {e.day} by {unit_noun}, digitized from the Central Electoral "
        f"Commission's communiqué as {e.paper} printed it",
        depends=[GEO, REGISTRY],
    )
    p.external.add(ROMANIA)
    url = LIBRARY + e.path
    source = key("source", e.day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=f"Comisia Electorală Centrală (communiqué printed in {e.paper})",
        title=(
            f"Comunicatul Comisiei Electorale Centrale cu privire la rezultatul alegerilor de deputați în Marea "
            f"Adunare Națională din {e.day:%d.%m.%Y} ({e.paper} nr. {e.issue}, {e.printed:%d.%m.%Y}, p. {e.page})"
        ),
        url=url,
        license="No license stated: an official communiqué of a state body, excluded from copyright by Romanian law",
        attribution=f"{e.paper}; Biblioteca Județeană „Ovid Densusianu” Hunedoara-Deva",
        notes=(
            "The communiqué was printed in the Buletinul Oficial and reprinted in full in the daily press on the day "
            "of its release; this is the newspaper's reprint, as the county library of Hunedoara serves its scan."
        ),
    )
    stored = raw.cached(SOURCE, url)
    artifact = p.artifact(stored, source)
    width, height = sizes(stored.path)[e.page]
    page = key("page", e.day, e.page)
    p.add(
        "source_page",
        id=page,
        artifact_id=artifact,
        page_number=e.page,
        image_key=f"pages/{SOURCE}/{stored.sha256}-{e.page}.png",
        width=round(width * 100 / 72),
        height=round(height * 100 / 72),
    )

    election = key("election", e.day)
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{e.day.year} Romanian Great National Assembly election", "en", "official")
    p.name(election, f"Alegerile de deputați în Marea Adunare Națională din {e.day:%d.%m.%Y}", "ro", "native", False)
    base = f"ro/{e.day}-general"
    p.add("slug", path=base, entity_id=election, is_current=True)

    contest = key("contest", e.day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=ASSEMBLY,
        magnitude=e.seats,
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"Great National Assembly ({e.seats} constituencies)", "en", "official")
    p.name(contest, "Marea Adunare Națională", "ro", "native", False)
    p.add("slug", path=f"{base}/great-national-assembly", entity_id=contest, is_current=True)
    party = key("party", front.code)
    for_option, against_option = key("option", e.day, "for"), key("option", e.day, "against")
    p.add(
        "contest_option",
        id=for_option,
        contest_id=contest,
        kind="list",
        party_id=party,
        ballot_position=1,
        ballot_label=f"Candidații {front.name}",
    )
    p.add(
        "contest_option",
        id=against_option,
        contest_id=contest,
        kind="none_of_the_above",
        ballot_position=2,
        ballot_label="Voturi contra",
    )
    p.name(against_option, "Against the Front's candidates", "en", "official")

    result_set = key("result_set", e.day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    cast_measure = VOTERS if e.cast_label == "Votanți" else CAST
    method = (
        f"The Central Electoral Commission's communiqué on the election of {e.day:%-d %B %Y}, as {e.paper} no. "
        f"{e.issue} of {e.printed:%-d %B %Y} printed it on page {e.page}. The table prints, for each {unit_noun}, the "
        f"registered voters, the {'voters' if cast_measure == VOTERS else 'votes cast'} (“{e.cast_label}”), the "
        f"votes for the candidates of the {front.english}, the votes against them, and the annulled votes; its "
        "percentages are not stored. A dash is stored as zero. Every figure was read from the page image and keeps "
        f"its page and box. The {e.seats} constituencies are not reported one by one, so the {unit_noun} figures add "
        "up the constituencies within each."
    )
    if e.day == date(1985, 3, 17):
        method += (
            " The 1985 table prints no column of annulled votes; the communiqué's figures leave none (the votes for "
            "and against add up to the votes cast). The municipality of Bucharest includes the Ilfov agricultural "
            "sector, which the table does not print separately."
        )
    if e.day == date(1969, 3, 2):
        method += (
            " The table prints no total row; the national totals are the communiqué's text. The table prints "
            "1,211,325 votes cast in the municipality of Bucharest, where its own votes for, against, and annulled "
            "add up to 1,211,323, and so do the national totals; the figure is kept as printed."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    p.add("outcome", result_set_id=result_set, option_id=for_option, kind="won", seats=e.seats)

    boxes = json.loads((TABLES / f"{e.day}.boxes.json").read_text())
    if [b["unit"] for b in boxes] != [r.printed for r in rows]:
        raise ValueError(f"{e.day}: the boxes do not follow the table's rows")
    measures = {
        "registered": (None, REGISTERED),
        "cast": (None, cast_measure),
        "for": (for_option, VOTES),
        "against": (against_option, VOTES),
        "null": (None, INVALID),
    }
    values: list[tuple[str, str | None, int, int]] = []
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    for row, located in zip(rows, boxes, strict=True):
        code = code_of(e.plan, row.printed)
        unit = unit_id(e.plan, code)
        for field, (option, measure) in measures.items():
            value = row.values[field]
            if value is None:
                continue
            values.append((unit, option, measure, value))
            x0, y0, x1, y1 = located["boxes"][field]
            locators.append((unit, option, measure, page, x0, y0, x1, y1))
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators, schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"], orient="row"
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    for field, (option, measure) in measures.items():
        value = total.values[field]
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    if e.day == date(1969, 3, 2):
        p.accept(
            "accounting.declared",
            "the county table prints 1,211,325 votes cast in the municipality of Bucharest, where its votes for, "
            "against, and annulled add up to 1,211,323; the counties so add up to 13,577,145 votes cast against the "
            "communiqué's 13,577,143",
        )
    return p


REFERENDUM_QUESTIONS = {
    "": (
        "Reduction of armaments, troops, and military spending by 5 percent",
        (
            "Reducerea, de către Republica Socialistă România, cu 5 la sută, a armamentelor, efectivelor și "
            "cheltuielilor militare"
        ),
        "military-spending-cut",
    ),
    "youth_": (
        (
            "Reduction of armaments, troops, and military spending by 5 percent: consultation of young people aged 14 "
            "to 18"
        ),
        (
            "Consultarea tinerilor în vîrstă de 14-18 ani cu privire la reducerea armamentelor, efectivelor și "
            "cheltuielilor militare"
        ),
        "youth-consultation",
    ),
}


def build_referendum(r: Referendum) -> Package:
    rows, total = referendum_table(r.day)
    p = Package(
        f"{SOURCE}/{r.day}",
        SOURCE,
        f"The referendum of {r.day:%-d %B %Y} on cutting military spending, by county, digitized from the Central "
        f"Commission for the Referendum's communiqué as {r.paper} printed it",
        depends=[GEO, REGISTRY],
    )
    p.external.add(ROMANIA)
    url = LIBRARY + r.path
    source = key("source", r.day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=f"Comisia Centrală pentru Referendum (communiqué printed in {r.paper})",
        title=(
            f"Comunicatul Comisiei Centrale pentru Referendum cu privire la rezultatul referendumului de la "
            f"{r.day:%d.%m.%Y} ({r.paper} nr. {r.issue}, {r.printed:%d.%m.%Y}, p. {r.page})"
        ),
        url=url,
        license="No license stated: an official communiqué of a state body, excluded from copyright by Romanian law",
        attribution=f"{r.paper}; Biblioteca Județeană „Ovid Densusianu” Hunedoara-Deva",
        notes=(
            "The communiqué was published in the Buletinul Oficial and reprinted in full in the daily press; this is "
            "the newspaper's reprint, as the county library of Hunedoara serves its scan."
        ),
    )
    stored = raw.cached(SOURCE, url)
    artifact = p.artifact(stored, source)
    width, height = sizes(stored.path)[r.page]
    page = key("page", r.day, r.page)
    p.add(
        "source_page",
        id=page,
        artifact_id=artifact,
        page_number=r.page,
        image_key=f"pages/{SOURCE}/{stored.sha256}-{r.page}.png",
        width=round(width * 100 / 72),
        height=round(height * 100 / 72),
    )

    election = key("election", r.day)
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="referendum",
        date_from=r.day,
        date_to=r.day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{r.day.year} Romanian military spending referendum", "en", "official")
    p.name(election, f"Referendumul din {r.day:%d.%m.%Y}", "ro", "native", False)
    base = f"ro/{r.day}-referendum"
    p.add("slug", path=base, entity_id=election, is_current=True)

    boxes = json.loads((TABLES / f"{r.day}.boxes.json").read_text())
    if [b["unit"] for b in boxes] != [row.printed for row in rows]:
        raise ValueError(f"{r.day}: the boxes do not follow the table's rows")
    reported: dict[str, str] = {}
    for row in rows:
        code = code_of(r.plan, row.printed)
        reported[row.printed] = unit_id(r.plan, code)

    for number, (prefix, (english, romanian, path)) in enumerate(REFERENDUM_QUESTIONS.items(), start=1):
        contest = key("contest", r.day, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=REFERENDUM_SYSTEM,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, english, "en", "official")
        p.name(contest, romanian, "ro", "native", False)
        p.add("slug", path=f"{base}/{path}", entity_id=contest, is_current=True)
        yes, no = key("option", r.day, number, "yes"), key("option", r.day, number, "no")
        for option, position, label in ((yes, 1, "DA"), (no, 2, "NU")):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, "Yes" if option == yes else "No", "en", "official")
        result_set = key("result_set", r.day, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        who = "young people aged 14 to 18 consulted" if prefix else "citizens who took part"
        method = (
            f"The Central Commission for the Referendum's communiqué on the referendum of {r.day:%-d %B %Y}, as "
            f"{r.paper} no. {r.issue} of {r.printed:%-d %B %Y} printed it on page {r.page}. The table prints, for "
            f"each county and the municipality of Bucharest, the {who} and their yes and no answers; its percentages "
            "are not stored. Every no answer is printed as a dash and stored as zero, and the participants are stored "
            "as the ballots cast. The communiqué prints no invalid ballots. Every figure was read from the page image "
            "and keeps its page and box. "
        )
        if prefix:
            method += (
                f"The communiqué gives {r.youth_registered:,} young people on the consultation lists nationally, and "
                "none by county. Young people could not vote; the consultation ran alongside the referendum on "
                "separate lists and had no legal effect."
            )
        else:
            method += (
                f"The communiqué gives {r.registered:,} citizens on the voting lists nationally, and none by county. "
                "The municipality of Bucharest includes the Ilfov agricultural sector."
            )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        p.add("outcome", result_set_id=result_set, option_id=yes, kind="won")
        p.add("outcome", result_set_id=result_set, option_id=no, kind="lost")

        measures = {f"{prefix}cast": (None, CAST), f"{prefix}yes": (yes, VOTES), f"{prefix}no": (no, VOTES)}
        values: list[tuple[str, str | None, int, int]] = []
        locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
        for row, located in zip(rows, boxes, strict=True):
            unit = reported[row.printed]
            for field, (option, measure) in measures.items():
                value = row.values[field]
                if value is None:
                    continue
                values.append((unit, option, measure, value))
                x0, y0, x1, y1 = located["boxes"][field]
                locators.append((unit, option, measure, page, x0, y0, x1, y1))
        p.frame(
            "result_value",
            pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(DIGITIZED, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        p.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=None,
            measure=REGISTERED,
            value=r.youth_registered if prefix else r.registered,
        )
        for field, (option, measure) in measures.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=total.values[field])
    p.accept(
        "accounting.declared",
        f"the communiqué gives the {r.registered:,} citizens on the voting lists and the {r.youth_registered:,} young "
        "people on the consultation lists nationally only, and its county tables print no registered figures to sum",
    )
    return p
