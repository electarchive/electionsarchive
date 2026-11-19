"""The 18 March 1990 election of the Supreme Council of the Latvian SSR: the deputy elected in each of 201 districts.

The Supreme Council (Augstākā Padome) was elected in 201 single-member districts. A candidate needed more than half
of the valid votes, with more than half of the registered voters taking part; otherwise the two leading candidates
met in a runoff, and districts with no result voted again in April and May 1990.

The Saeima, the Council's successor, publishes two lists of the deputies. Its history page names each district and
the deputy elected there, with the deputy's faction, party, year of birth, and nationality. Its archive of the
Council's sittings lists the deputies of the first session (May to August 1990) alphabetically, with each name spelled
as the Council's decisions printed in its gazette (Ziņotājs Nr. 20 and 21, 1990). The history page gives the district
for every deputy, so it is the record of the outcome; the session list supplies the official spelling of the names.

Neither list prints votes. The district results appeared in the press in March and April 1990, but the national
library's newspaper archive serves its text only to signed-in readers, so each contest holds its winner only.

The district boundaries that the Presidium of the Supreme Soviet published in late 1989 are in the same archive, so the
rural districts take approximate shapes from the State Land Service's boundaries of 2010: each parish and town of a
district (rajons) of 1990 goes to the nearest place a Supreme Council district of that rajons is named after.
"""

import difflib
import html
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.licenses import UNLICENSED
from ea.sources.lu.model import slug
from ea.sources.lv import boundaries, cvk

SOURCE = "lv.ap1990"
LATVIA = cvk.LATVIA
COUNTRY = cvk.COUNTRY
SCHEME = "lv.ap1990"
DAY = date(1990, 3, 18)
LAST = date(1990, 5, 3)

HISTORY = (
    "https://web.archive.org/web/20261006111422id_/"
    "https://saeima.lv/lv/par-saeimu/likumdeveju-vesture/latvijas-republikas-augstaka-padome/"
)
HISTORY_PAGE = "https://www.saeima.lv/lv/par-saeimu/likumdeveju-vesture/latvijas-republikas-augstaka-padome/"
SESSION = "https://www.saeima.lv/steno/AP_steno/ap_dep.htm"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SRC = key("source", "saeima")
BODY = key("body", "augstaka-padome")
SYSTEM = key("electoral_system", "1990")
ELECTION = key("election", DAY.isoformat())

FACTIONS = {"TF": "Latvian Popular Front", "L": "Līdztiesība", "N": "no faction"}


def fetch() -> None:
    raw.fetch(SOURCE, HISTORY, suffix=".html")
    raw.fetch(SOURCE, SESSION, suffix=".html")
    for url in (cvk.PV2001, cvk.PV2001_ACTIVITY):
        raw.fetch(cvk.SOURCE, url, suffix=".html")
    boundaries.fetch()


def text(url: str, encoding: str) -> str:
    body = raw.cached(SOURCE, url).path.read_bytes().decode(encoding)
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"[\s ]+", " ", html.unescape(body))


@dataclass(frozen=True)
class Deputy:
    district: int
    place: str
    name: str
    codes: tuple[str, ...]
    female: bool
    born: int


@dataclass(frozen=True)
class Listed:
    district: int
    name: str
    codes: tuple[str, ...]


HISTORY_ROW = re.compile(
    r"(\d{1,3})\. (\S+) vēlēšanu apgabals ([^()]+?) ((?:\([^)]*\);?)+) Dzim(is|usi) ?(\d{4})\. ?gadā"
)
SESSION_ROW = re.compile(r"(?:<b>)?([^<>]+?)(?:</b>)?((?: \([^)]*\))*?) \(([^)]*)\): no (\d+)\. ?(\S+) vēl\. ?apg\.")


def history() -> list[Deputy]:
    found = text(HISTORY, "utf-8")
    rows = [
        Deputy(
            int(number),
            place,
            name.strip(),
            tuple(code.strip("()") for code in codes.strip(";").split(";")),
            sex == "usi",
            int(born),
        )
        for number, place, name, codes, sex, born in HISTORY_ROW.findall(found)
    ]
    if sorted({row.district for row in rows}) != list(range(1, 202)):
        raise ValueError("the Saeima's history page does not name a deputy for each of the 201 districts")
    return rows


def session() -> list[Listed]:
    body = raw.cached(SOURCE, SESSION).path.read_bytes().decode("cp1257")
    body = re.sub(r"\s+", " ", html.unescape(body))
    rows: list[Listed] = []
    for paragraph in re.split(r"<br> <br>|</?p[^>]*>", body):
        match = SESSION_ROW.search(paragraph)
        if match is None:
            continue
        name, _, codes, number, _ = match.groups()
        name = re.sub(r"<[^>]+>", "", name).strip()
        rows.append(Listed(int(number), name, tuple(code.strip() for code in codes.split(";"))))
    return rows


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


@dataclass(frozen=True)
class Seat:
    deputy: Deputy
    spelled: str
    listed: bool
    later: tuple[Deputy, ...]


def seats() -> list[Seat]:
    """The deputy elected in each district in spring 1990, spelled as the Council's decisions spell the name.

    Where the history page names two deputies for a district, the second won a by-election after the first left;
    the first is the one elected in 1990 and the one on the first session's list.
    """
    listed = session()
    by: dict[int, list[Deputy]] = {}
    for row in history():
        by.setdefault(row.district, []).append(row)
    found: list[Seat] = []
    for district in range(1, 202):
        rows = by[district]
        matches = [
            (similar(row.name, entry.name), row, entry)
            for row in rows
            for entry in listed
            if entry.district == district and similar(row.name, entry.name) >= 0.8
        ] or [
            (similar(row.name, entry.name), row, entry)
            for row in rows
            for entry in listed
            if similar(row.name, entry.name) >= 0.9
        ]
        if matches:
            _, row, entry = max(matches, key=lambda item: item[0])
            found.append(Seat(row, entry.name, True, tuple(other for other in rows if other is not row)))
        elif len(rows) == 1:
            found.append(Seat(rows[0], rows[0].name, False, ()))
        else:
            raise ValueError(f"district {district}: none of {[row.name for row in rows]} is on the session list")
    return found


def area(district: int) -> str:
    return key("area", district)


def district_name(deputy: Deputy) -> str:
    return f"{deputy.district}. {deputy.place} vēlēšanu apgabals"


END = date(1993, 6, 5)

# The rural districts are numbered by the 26 districts (rajoni) of 1990 in alphabetical order: the first number of
# each, with the district as the 2001 local elections name it (Stučkas rajons became Aizkraukles rajons in 1990).
RAYONS = (
    (104, "Alūksnes rajons"),
    (107, "Balvu rajons"),
    (110, "Bauskas rajons"),
    (114, "Cēsu rajons"),
    (118, "Daugavpils rajons"),
    (121, "Dobeles rajons"),
    (124, "Gulbenes rajons"),
    (127, "Jēkabpils rajons"),
    (132, "Jelgavas rajons"),
    (135, "Krāslavas rajons"),
    (138, "Kuldīgas rajons"),
    (141, "Liepājas rajons"),
    (145, "Limbažu rajons"),
    (148, "Ludzas rajons"),
    (151, "Madonas rajons"),
    (155, "Ogres rajons"),
    (160, "Preiļu rajons"),
    (163, "Rēzeknes rajons"),
    (166, "Rīgas rajons"),
    (177, "Saldus rajons"),
    (180, "Aizkraukles rajons"),
    (183, "Talsu rajons"),
    (187, "Tukuma rajons"),
    (191, "Valkas rajons"),
    (194, "Valmieras rajons"),
    (199, "Ventspils rajons"),
)
# Districts named after a place whose name the 2010 units do not carry in the genitive: the units of the place.
PLACES = {
    "Pārgaujas": ("Raiskuma pagasts", "Stalbes pagasts", "Straupes pagasts"),  # the later Pārgaujas novads
    "Piebalgas": ("Vecpiebalgas pagasts", "Jaunpiebalgas pagasts"),
    "Vecpils": ("Naujenes pagasts",),  # the old castle of Daugavpils (Dinaburg) stands in Naujene parish
    "Kalkūnas": ("Kalkūnes pagasts",),
    "Dricēnu": ("Dricānu pagasts",),
    "Jaunogres": ("Ogre",),
    "Pārogres": ("Ogre",),
    "Stučkas": ("Aizkraukle",),
}
SHAPES = (
    "The district's shape is an approximation. The Presidium of the Supreme Soviet published the district boundaries "
    "in late 1989, but no machine-readable copy of them is known. Each rural district is drawn as the parishes and "
    "towns of its district (rajons) of 1990 that lie nearest the place it is named after, using the State Land "
    "Service's generalized boundaries of 1 January 2010; districts named after the same town share one shape. The "
    "districts within the cities have no shape."
)


def genitive(name: str) -> str:
    """The genitive of a 2010 unit's name, as a district's name carries it ("Cēsis" to "Cēsu")."""
    if name.endswith((" pagasts", " novads")):
        return name.rsplit(" ", 1)[0]
    if name.endswith(("pils", "us")):
        return name
    if name.endswith("is"):
        return name[:-2] + "u"
    if name.endswith("i"):
        return name[:-1] + "u"
    if name.endswith("s"):
        return name[:-1] + "a"
    return name + "s"


@cache
def districts() -> dict[int, shapely.Geometry]:
    """Each rural district's shape in the State Land Service's projection (EPSG:3059).

    Each unit of a district (rajons) goes to the nearest place a district of that rajons is named after.
    """
    atlas = boundaries.Atlas()
    day = "2001-03-11"
    councils = [boundaries.Council(day, c.code, c.name, c.group) for c in cvk.read_local(day).councils]
    plan = boundaries.build(atlas, {day: councils})
    places = {seat.deputy.district: seat.deputy.place for seat in seats()}
    found: dict[int, shapely.Geometry] = {}
    for (first, rayon), (last, _) in zip(RAYONS, [*RAYONS[1:], (202, "")], strict=True):
        units = plan.places[f"rajons:{slug(rayon)}"].units
        anchors: dict[tuple[str, ...], list[int]] = {}
        for district in range(first, last):
            place = places[district]
            named = PLACES.get(place)
            anchor = tuple(
                code
                for code in units
                if (atlas.units[code].name in named if named else genitive(atlas.units[code].name) == place)
            )
            if not anchor:
                raise ValueError(f"district {district}: no unit of {rayon} is named {place}")
            anchors.setdefault(anchor, []).append(district)
        cells: dict[tuple[str, ...], list[str]] = {anchor: list(anchor) for anchor in anchors}
        shapes = {anchor: shapely.union_all([atlas.units[code].shape for code in anchor]) for anchor in anchors}
        taken = {code for anchor in anchors for code in anchor}
        for code in units:
            if code in taken:
                continue
            point = shapely.point_on_surface(atlas.units[code].shape)
            nearest = min(anchors, key=lambda anchor: (shapely.distance(point, shapes[anchor]), anchor))
            cells[nearest].append(code)
        for anchor, members in anchors.items():
            shape = shapely.union_all([atlas.units[code].shape for code in cells[anchor]])
            for district in members:
                found[district] = shape
    return found


def geo() -> Package:
    p = Package(
        "lv.ap1990/geo",
        SOURCE,
        "The 201 districts of the 1990 Supreme Council election in Latvia, from the Saeima, with approximate shapes "
        "of the rural districts from the State Land Service's boundaries of 2010",
    )
    p.external |= {LATVIA, COUNTRY}
    shaped = districts()
    land = key("source", "territorial-units")
    p.add(
        "source",
        id=land,
        kind="official_body",
        publisher="Centrālā statistikas pārvalde",
        title="Ģeneralizētas teritoriālo vienību robežas (generalized boundaries of territorial units), 1 January 2010, "
        "1:1 200 000, from the State Land Service's data",
        url=boundaries.DATASET,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Centrālā statistikas pārvalde, Valsts zemes dienests",
    )
    p.artifact(boundaries.artifact(), land)
    for url in (cvk.PV2001, cvk.PV2001_ACTIVITY):
        p.uses(raw.cached(cvk.SOURCE, url))
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME, "1990"),
        scheme_code=SCHEME,
        vintage="1990",
        valid_from=DAY,
        valid_to=END,
        as_of=DAY,
    )
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=LATVIA,
        kind="electoral_district",
        rank=1,
        noun="Supreme Council district",
    )
    for seat in seats():
        deputy = seat.deputy
        id = area(deputy.district)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=str(deputy.district), valid_from=DAY, valid_to=END)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=DAY, valid_to=END)
        p.name(id, district_name(deputy), "lv", "official")
        p.name(id, f"Supreme Council district {deputy.district} ({deputy.place})", "en", "english", False)
        if deputy.district not in shaped:
            continue
        shape = boundaries.wgs(shaped[deputy.district])
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=DAY,
            valid_to=END,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="synthetic",
            source_id=land,
        )
        p.add(
            "note",
            id=key("note", "shape", deputy.district),
            entity_id=id,
            kind="methodology",
            body_md=SHAPES,
            lang="en",
        )
    return p


def registry() -> Package:
    p = Package(
        "lv.ap1990/registry",
        SOURCE,
        "The Supreme Council of the Latvian SSR, its 1990 electoral system, and the Saeima's deputy lists as a source",
        depends=["lv.ap1990/geo"],
    )
    p.external |= {LATVIA}
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="Latvijas Republikas Saeima",
        title="Latvijas Republikas Augstākā Padome: deputāti (Supreme Council of the Republic of Latvia: deputies)",
        url=HISTORY_PAGE,
        license=UNLICENSED,
        attribution="Latvijas Republikas Saeima",
    )
    for url in (HISTORY, SESSION):
        p.artifact(raw.cached(SOURCE, url), SRC)
    p.add("body", id=BODY, polity_id=LATVIA, kind="unicameral")
    p.name(BODY, "Supreme Council of Latvia", "en", "official")
    p.name(BODY, "Augstākā Padome", "lv", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LATVIA,
        valid_from=DAY,
        valid_to=LAST,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.name(SYSTEM, "Supreme Council election, 1990", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Each of the 201 districts elected one deputy. A candidate was elected with more than half of the valid "
            "votes, provided more than half of the registered voters took part. Otherwise the two candidates with the "
            "most votes met in a runoff, and a district where the vote failed held a repeat election."
        ),
        lang="en",
    )
    for seat in seats():
        person = key("person", seat.deputy.district, seat.deputy.name)
        p.add(
            "person",
            id=person,
            birth_date=date(seat.deputy.born, 1, 1),
            birth_precision="year",
            gender="female" if seat.deputy.female else "male",
        )
        p.name(person, seat.spelled, "lv", "official")
        if seat.deputy.name != seat.spelled:
            p.name(person, seat.deputy.name, "lv", "official", False)
    return p


METHOD = (
    "The deputy elected in each district, from the Saeima's list of the Supreme Council's deputies by district. "
    "Names are spelled as in the list of deputies of the Council's first session, which follows the spelling in the "
    "Council's decisions. The Saeima's lists print no votes, so each contest records its winner only; the votes, "
    "turnout, and the round in which each deputy was elected are not stored."
)


def build() -> Package:
    p = Package(
        f"lv.ap1990/{DAY.isoformat()}",
        SOURCE,
        "The 1990 Supreme Council election in Latvia: the deputy elected in each of the 201 districts, from the Saeima",
        depends=["lv.ap1990/geo", "lv.ap1990/registry"],
    )
    p.external |= {LATVIA}
    for url in (HISTORY, SESSION):
        p.uses(raw.cached(SOURCE, url))
    where = "lv/1990-03-18-supreme-council"
    p.add(
        "election",
        id=ELECTION,
        polity_id=LATVIA,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(ELECTION, "1990 Latvian Supreme Council election", "en", "official")
    p.name(ELECTION, "Latvijas PSR Augstākās Padomes vēlēšanas 1990. gadā", "lv", "native", False)
    p.add("slug", path=where, entity_id=ELECTION, is_current=True)
    p.add(
        "note",
        id=key("note", ELECTION),
        entity_id=ELECTION,
        kind="context",
        body_md=(
            "The first election of the Supreme Council of the Latvian SSR in which districts could field several "
            "candidates. The first round was held on 18 March 1990; runoffs and repeat elections followed in some "
            "districts in March, April, and May. The Council met on 3 May 1990 and declared the restoration of "
            "Latvia's independence on 4 May. Its deputies sat in two factions: the Latvian Popular Front (TF) and "
            "Līdztiesība (L); deputies in neither are marked N. The factions formed after the election and are not "
            "stored as the candidates' parties."
        ),
        lang="en",
    )
    for seat in seats():
        deputy = seat.deputy
        district = deputy.district
        contest = key("contest", district)
        p.add(
            "contest",
            id=contest,
            election_id=ELECTION,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area(district),
            magnitude=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, district_name(deputy), "lv", "official")
        p.name(contest, f"Supreme Council district {district} ({deputy.place})", "en", "english", False)
        p.add("slug", path=f"{where}/district-{district}", entity_id=contest, is_current=True)
        option = key("option", district)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=seat.spelled)
        candidacy = key("candidacy", district)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=key("person", district, deputy.name),
            option_id=option,
            role="head",
            ballot_name=seat.spelled,
        )
        result_set = key("result_set", district)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SRC,
            status="active",
            is_default=True,
            completeness=0.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        codes = ", ".join(deputy.codes)
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SRC,
            locator={"url": HISTORY_PAGE, "district": district, "entry": f"{deputy.name} ({codes})"},
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        caveats: list[str] = []
        if not seat.listed:
            caveats.append(
                f"{deputy.name} is not on the Saeima's list of the deputies of the Council's first session (May to "
                "August 1990); the Saeima's list by district names this deputy for the district."
            )
        for later in seat.later:
            caveats.append(
                f"{later.name} later held this seat after a by-election; that by-election is not stored here."
            )
        if caveats:
            p.add(
                "note",
                id=key("note", contest),
                entity_id=contest,
                kind="caveat",
                body_md=" ".join(caveats),
                lang="en",
            )
    return p


def packages() -> list[str]:
    return ["lv.ap1990/geo", "lv.ap1990/registry", f"lv.ap1990/{DAY.isoformat()}"]


def parse() -> None:
    geo().write()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())
