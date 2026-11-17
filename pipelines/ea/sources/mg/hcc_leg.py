"""Madagascar's Haute Cour Constitutionnelle: the deputies of the National Assembly proclaimed elected in the legislative
election of 20 December 2013, by constituency, from the Cour Electorale Spéciale's proclamation judgment (arrêt)."""

import html
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.mg import ceni

SOURCE = "mg.hcc_leg"
SHAPES = "mg.geoboundaries"
SITE = "https://www.hcc.gov.mg"
PAGE = f"{SITE}/?p=1342"
BOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/MDG/ADM2"
OUTLINE = f"{BOUNDARIES}/geoBoundaries-MDG-ADM2_simplified.geojson"
META = f"{BOUNDARIES}/geoBoundaries-MDG-ADM2-metaData.json"
MADAGASCAR = ceni.MADAGASCAR
SCHEME = "mg.constituency"
DAY = "2013-12-20"
DAY_DATE = date(2013, 12, 20)
PLAN_FROM = date(2013, 1, 1)
SEATS = 151
DECLARED_SEATS = 147

# The judgment spells some constituencies differently from the boundary file; this maps its spelling to the file's.
SHAPE_NAMES = {
    "BEFOTAKASUD": "Befotaka",
    "MIDONGYSUD": "Midongy-Atsimo",
    "MANAKARA": "Manakara Atsimo",
    "PORTBERGE": "Port-Berge (Boriziny-Vaovao)",
    "MANANARANORD": "Mananara-Avaratra",
    "BELOHAANDROY": "Beloha",
    "AMBOASARYSUD": "Amboasary-Atsimo",
    "TAOLANARO": "Taolagnaro",
    "ANKAZOABOSUD": "Ankazoabo",
    "BETIOKYSUD": "Betioky Atsimo",
    "AMBOVOMBEANDROY": "Ambovombe-Androy",
    "TOLIARYI": "Toliary-I",
    "TOLIARYII": "Toliary-II",
    "ANTANAMBAOMANAMPOTSY": "Antanambao Manampontsy",
    **{
        f"ANTANANARIVO{roman}": f"{number}{'er' if number == 1 else 'e'} Arrondissement"
        for number, roman in enumerate(("I", "II", "III", "IV", "V", "VI"), 1)
    },
}
# The constituencies whose elections the judgment annuls (article 13), so that it declares no deputies for them.
ANNULLED = ("AMBANJA", "BELOSURTSIRIBIHINA", "MAROVOAY", "SAINTEMARIE")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "hcc")
SHAPES_ID = key("source", SHAPES)


def norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", plain)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SHAPES, OUTLINE, suffix=".geojson")
    raw.fetch(SHAPES, META, suffix=".json")


@dataclass
class Deputy:
    constituency: str
    name: str
    party: str


def party_label(label: str) -> str:
    """The party as the judgment prints it, with its spelling variants of one name made one."""
    code = norm(label)
    if code.startswith("MIARAKA"):
        return "MIARAKA AMIN'I ANDRY RAJOELINA"
    if code.startswith(("MOUVANCERAVALOMANANA", "MOVANSYRAVALOMANANA")):
        return "MOUVANCE RAVALOMANANA"
    return re.sub(r"\s+", " ", label).strip()


def is_independent(label: str) -> bool:
    return norm(label).startswith("INDEPENDANT")


def deputies() -> list[Deputy]:
    page = raw.cached(SOURCE, PAGE).path.read_text(errors="replace")
    start = page.find("Article 15")
    if start < 0:
        raise ValueError("no article 15 in the judgment")
    found: list[Deputy] = []
    for row in re.findall(r"<tr.*?</tr>", page[start:], flags=re.DOTALL):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ")).strip()
            for cell in re.findall(r"<t[dh].*?</t[dh]>", row, flags=re.DOTALL)
        ]
        if len(cells) != 3:
            raise ValueError(f"not a deputy row: {cells}")
        if cells[0] == "District":
            continue
        found.append(Deputy(cells[0], cells[1], party_label(cells[2])))
    if len(found) != DECLARED_SEATS:
        raise ValueError(f"{len(found)} deputies, expected {DECLARED_SEATS}")
    return found


def shapes() -> dict[str, tuple[str, object]]:
    """The boundary file's constituencies by normalized name: its spelling and its shape."""
    path = raw.cached(SHAPES, OUTLINE).path
    return {
        norm(f["properties"]["shapeName"]): (f["properties"]["shapeName"], shapely.geometry.shape(f["geometry"]))
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]
    }


def shape_key(label: str) -> str:
    code = norm(label)
    return norm(SHAPE_NAMES.get(code, label))


def area(code: str) -> str:
    return key("area", SCHEME, code)


def multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shape)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def titled(text: str) -> str:
    return text.title() if text.isupper() else text


def names() -> dict[str, str]:
    """Each constituency's name: the judgment's spelling where it declares deputies, else the boundary file's."""
    found = {shape_key(label): label for label in (d.constituency for d in deputies())}
    return {code: titled(found.get(code, spelling)) for code, (spelling, _) in shapes().items()}


def build_geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Madagascar's 119 legislative constituencies, which are its districts, with their outlines from geoBoundaries",
    )
    p.external.add(MADAGASCAR)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Madagascar ADM2 (simplified), from the national disaster management office and "
        "OCHA ROSA",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 3.0 Intergovernmental Organisations (CC BY 3.0 IGO)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Bureau National de Gestion des "
        "Risques et des Catastrophes of Madagascar and OCHA ROSA",
    )
    p.artifact(raw.cached(SHAPES, OUTLINE), SHAPES_ID)
    p.artifact(raw.cached(SHAPES, META), SHAPES_ID)
    p.add("scheme", code=SCHEME, polity_id=MADAGASCAR, kind="electoral_district", rank=1, noun="constituency")
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, "2013"),
        scheme_code=SCHEME,
        vintage="2013",
        valid_from=PLAN_FROM,
        as_of=date(2023, 1, 19),
    )
    label_for = names()
    for code, (_, shape) in sorted(shapes().items()):
        p.add("area", id=area(code), scheme_code=SCHEME)
        p.add("area_code", area_id=area(code), code=code)
        p.name(area(code), label_for[code], "fr", "official")
        geometry = multipolygon(shape)
        p.add(
            "area_geometry",
            area_id=area(code),
            valid_from=PLAN_FROM,
            geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_ID,
        )
    return p


def party(label: str) -> str:
    return key("party", norm(label))


def build_registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source, and the parties and groupings named on the court's list of deputies elected in 2013",
        depends=["mg.ceni/registry"],
    )
    p.external.add(MADAGASCAR)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Haute Cour Constitutionnelle de Madagascar",
        title="Judgment of the Cour Electorale Spéciale proclaiming the official results of the 2013 legislative "
        "election",
        url=f"{SITE}/",
        license="Official judgments published by the court; reuse terms are not stated",
        attribution="Haute Cour Constitutionnelle de Madagascar",
    )
    p.artifact(raw.cached(SOURCE, PAGE), SOURCE_ID)
    labels = sorted({d.party for d in deputies() if not is_independent(d.party)}, key=str.casefold)
    for label in labels:
        p.add("party", id=party(label), kind="party", polity_id=MADAGASCAR)
        p.name(party(label), label, "fr", "official")
    return p


METHOD = (
    "The court's proclamation of the deputies elected, by constituency, with each deputy's party or independence as "
    "the judgment prints it. The judgment prints no vote counts, so none are stored; the outcomes are the declared "
    "winners. Spelling variants of one party's name are stored as one party."
)


def build(day: str = DAY) -> Package:
    if day != DAY:
        raise ValueError(f"no election on {day}")
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The deputies elected to the National Assembly of Madagascar on 20 December 2013, by constituency, from the "
        "Cour Electorale Spéciale's proclamation judgment",
        depends=["mg.ceni/registry", f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external.add(MADAGASCAR)
    p.uses(raw.cached(SOURCE, PAGE))
    election = key("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=MADAGASCAR,
        kind="general",
        date_from=DAY_DATE,
        date_to=DAY_DATE,
        date_precision="day",
        status="held",
    )
    p.name(election, "2013 Malagasy legislative election", "en", "official")
    p.name(election, "Élections législatives de 2013", "fr", "native", False)
    base = f"mg/{DAY}-legislative"
    p.add("slug", path=base, entity_id=election, is_current=True)

    known = names()
    by_constituency: dict[str, list[Deputy]] = defaultdict(list)
    for d in deputies():
        code = shape_key(d.constituency)
        if code not in known:
            raise ValueError(f"no boundary for {d.constituency!r}")
        by_constituency[code].append(d)
    for code in ANNULLED:
        if code in by_constituency:
            raise ValueError(f"{code} has deputies but its election was annulled")
        if code not in known:
            raise ValueError(f"no boundary for annulled {code}")
    for code, members in sorted(by_constituency.items()):
        text = known[code]
        contest = key("contest", DAY, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ceni.ASSEMBLY,
            district_area_id=area(code),
            magnitude=len(members),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"2013 Malagasy legislative election, {text}", "en", "official")
        p.name(contest, f"Élections législatives de 2013, circonscription de {text}", "fr", "native", False)
        p.add("slug", path=f"{base}/{ceni.slug(text)}", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=0.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        for position, d in enumerate(members, 1):
            option = key("option", DAY, code, position)
            free = is_independent(d.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=d.name,
            )
            person = key("person", DAY, code, position)
            p.add("person", id=person)
            p.name(person, d.name, "fr", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=d.name,
                member_of_party_id=None if free else party(d.party),
                is_independent=free,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    build_geo().write()
    build_registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())
