"""Congo-Kinshasa's electoral commission (CENI): the national and provincial deputy elections of 15 December 2024 in
Masi-Manimba and Yakoma, from its results portal's JSON interface."""

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "cd.ceni_2024"
PORTAL = "https://bosolo.ceni.cd/e20241215/bosolo/"
API = f"{PORTAL}mapapi.php"
PROVINCES = f"{API}?data=provinces"
DISPATCH = "https://www.ceni.cd/depeches/2024/12/21/la-ceni-rend-publics-les-resultats-provisoires-des-elections-legislatives-masi"
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/COD/ADM2/"
    "geoBoundaries-COD-ADM2_simplified.geojson"
)
SHAPES = "cd.geoboundaries"
DAY = date(2024, 12, 15)
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

# The portal numbers each province; the circonscription is the territory's own code in the results.
PROVINCE_FILES = {"4": "KWILU", "7": "NORD-UBANGI"}
CIRCONSCRIPTIONS = {
    "13032": {"province": "4", "name": "Masi-Manimba", "shape": "Masi-Manimba", "national": 5, "provincial": 8},
    "14052": {"province": "7", "name": "Yakoma", "shape": "Yakoma", "national": 2, "provincial": 4},
}
PROVINCE_NAMES = {"4": ("Kwilu", "Kwilu"), "7": ("Nord-Ubangi", "Nord-Ubangi")}
ELECTIONS = {"2": "national", "3": "provincial"}
# The dispatch's own figures: valid votes, blank ballots, voters, and registered voters, by circonscription and tier.
DISPATCHED = {
    ("13032", "national"): {"valid": 167035, "blank": 247, "cast": 167282, "registered": 442383},
    ("13032", "provincial"): {"valid": 166974, "blank": 308, "cast": 167282, "registered": 442383},
    ("14052", "national"): {"valid": 108792, "blank": 169, "cast": 108961, "registered": 200247},
    ("14052", "provincial"): {"valid": 108764, "blank": 197, "cast": 108961, "registered": 200247},
}

CONGO = str(uuid5(package.NAMESPACE, "polity\x1fCD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCOD"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CENI = key("source", "ceni")
SHAPE_SOURCE = key("source", "geoboundaries")
NATIONAL_ASSEMBLY = key("body", "national_assembly")
SYSTEM = key("electoral_system", "open_list_pr")
CIRCONSCRIPTION = "cd.circonscription"
PROVINCE = "cd.province"
BUREAU = "cd.bureau"


def api(province: str) -> str:
    return f"{API}?value={province}&type=pres&circ=province"


def fetch() -> None:
    raw.fetch(SOURCE, PROVINCES, suffix=".json")
    for province in PROVINCE_FILES:
        raw.fetch(SOURCE, api(province), suffix=".json")
    raw.fetch(SOURCE, DISPATCH, suffix=".html")
    raw.fetch(SHAPES, OUTLINES, suffix=".geojson")


def rows(province: str) -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = json.loads(raw.cached(SOURCE, api(province)).path.read_text(encoding="utf-8"))
    return found


def norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip().upper()


def party_id(sigle: str) -> str:
    return key("party", norm(sigle))


def independent(sigle: str) -> bool:
    return norm(sigle).startswith("INDEPENDANT")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def build_geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The provinces of Kwilu and Nord-Ubangi and the territories of Masi-Manimba and Yakoma, "
        "with outlines from geoBoundaries, and the electoral commission's results portal",
    )
    p.external |= {CONGO, COUNTRY}
    shapes = raw.cached(SHAPES, OUTLINES)
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Democratic Republic of the Congo ADM2 (territory outlines, simplified, 2019), "
        "derived from the Référentiel Géographique Commun, OCHA DR Congo",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Référentiel Géographique Commun",
    )
    p.artifact(shapes, SHAPE_SOURCE)
    p.add(
        "source",
        id=CENI,
        kind="official_body",
        publisher="Commission électorale nationale indépendante",
        title="Résultats des élections combinées du 15 décembre 2024 pour les circonscriptions de Masi-Manimba "
        "et de Yakoma (Bosolo results portal)",
        url=PORTAL,
        license="Official publication; reuse terms are not stated",
        attribution="Commission électorale nationale indépendante de la République démocratique du Congo",
    )
    for url in (PROVINCES, *map(api, PROVINCE_FILES), DISPATCH):
        p.artifact(raw.cached(SOURCE, url), CENI)
    p.add("scheme", code=PROVINCE, polity_id=CONGO, kind="administrative", rank=1, noun="province")
    p.add("scheme", code=CIRCONSCRIPTION, polity_id=CONGO, kind="electoral_district", rank=2, noun="circonscription")
    p.add("scheme", code=BUREAU, polity_id=CONGO, kind="precinct", rank=3, noun="polling station")
    p.add(
        "boundary_set",
        id=key("boundary_set", CIRCONSCRIPTION, 2019),
        scheme_code=CIRCONSCRIPTION,
        vintage="2019",
        as_of=shapes.retrieved_at.date(),
    )
    for number, (name, _) in PROVINCE_NAMES.items():
        p.add("area", id=area(PROVINCE, number), scheme_code=PROVINCE)
        p.add("area_kind", area_id=area(PROVINCE, number), kind="province", source_id=CENI)
        p.add("area_parent", area_id=area(PROVINCE, number), parent_id=COUNTRY)
        p.name(area(PROVINCE, number), name, "fr", "official")
    outlines = {
        feature["properties"]["shapeName"]: shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    for code, info in CIRCONSCRIPTIONS.items():
        id = area(CIRCONSCRIPTION, code)
        p.add("area", id=id, scheme_code=CIRCONSCRIPTION)
        p.add("area_code", area_id=id, code=code)
        p.add("area_kind", area_id=id, kind="territory", source_id=CENI)
        p.add("area_parent", area_id=id, parent_id=area(PROVINCE, str(info["province"])))
        p.name(id, str(info["name"]), "fr", "official")
        shape = shapely.make_valid(outlines[str(info["shape"])])
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    return p


def sigles() -> dict[str, str]:
    found: dict[str, str] = {}
    for province in PROVINCE_FILES:
        for row in rows(province):
            sigle = row["sigle"]
            if sigle and not independent(sigle):
                found.setdefault(party_id(sigle), re.sub(r"\s+", " ", sigle).strip())
    return found


def build_registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties on the 15 December 2024 ballots in Masi-Manimba and Yakoma, the National Assembly and the "
        "provincial assemblies, and the open-list system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CONGO)
    p.add("body", id=NATIONAL_ASSEMBLY, polity_id=CONGO, kind="lower_house")
    p.name(NATIONAL_ASSEMBLY, "National Assembly of the Democratic Republic of the Congo", "en", "official")
    p.name(NATIONAL_ASSEMBLY, "Assemblée nationale", "fr", "native", False)
    for number, (name, _) in PROVINCE_NAMES.items():
        body = key("body", "provincial_assembly", number)
        p.add("body", id=body, polity_id=CONGO, kind="council")
        p.name(body, f"Provincial Assembly of {name}", "en", "official")
        p.name(body, f"Assemblée provinciale du {name}", "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=CONGO,
        rules={"formula": "hare", "lists": {"order": "open", "vacancy": "next_on_list"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Seats go to lists by the largest remainder of the electoral quotient, the valid votes over the "
        "seats; within a list, the candidates with the most votes take its seats.",
        lang="en",
    )
    for id, label in sorted(sigles().items(), key=lambda item: item[1].casefold()):
        p.add("party", id=id, kind="party", polity_id=CONGO)
        p.name(id, label, "fr", "abbreviation")
    return p


def count(row: dict[str, str | None], field: str) -> int:
    return int(str(row[field]))


def build(day: str = "2024-12-15") -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The national and provincial deputy elections of 15 December 2024 in Masi-Manimba and Yakoma, by "
        "circonscription, with every candidate's votes",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external.add(CONGO)
    for url in (PROVINCES, *map(api, PROVINCE_FILES), DISPATCH):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=CONGO,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "2024 deputy elections rerun in Masi-Manimba and Yakoma", "en", "official")
    p.name(election, "Élections législatives du 15 décembre 2024 à Masi-Manimba et Yakoma", "fr", "native", False)
    p.add("slug", path=f"cd/{day}-legislative-rerun", entity_id=election, is_current=True)
    for province in PROVINCE_FILES:
        by_election: dict[str, list[dict[str, str | None]]] = defaultdict(list)
        for row in rows(province):
            by_election[str(row["election_id"])].append(row)
        for code, tier in ELECTIONS.items():
            _contest(p, election, province, tier, by_election[code])
    p.accept(
        "accounting.votes_valid",
        "in Yakoma the candidate lines add up to 108,792 (national) and 108,764 (provincial) votes while the portal "
        "prints 108,794 and 108,767 valid votes; stored as printed",
    )
    p.accept(
        "accounting.ballots_sum",
        "the portal prints no null-ballot figure, and its voters exceed valid and blank ballots together by 104 in "
        "Masi-Manimba and 64 in Yakoma, the null ballots the commission's dispatch counts with the blank ones; stored "
        "as printed",
    )
    return p


def _contest(p: Package, election: str, province: str, tier: str, data: list[dict[str, str | None]]) -> None:
    first = data[0]
    code = str(first["circonscription_id"])
    info = CIRCONSCRIPTIONS[code]
    name = str(info["name"])
    seats = int(info[tier])
    scope = f"{tier}:{code}"
    if tier == "national":
        body = NATIONAL_ASSEMBLY
        title = f"National deputies, {name}"
        french = f"Députés nationaux, {name}"
    else:
        body = key("body", "provincial_assembly", province)
        title = f"Provincial deputies, {name}"
        french = f"Députés provinciaux, {name}"
    contest = key("contest", scope)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=body,
        district_area_id=area(CIRCONSCRIPTION, code),
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2024 rerun, {title}", "en", "official")
    p.name(contest, f"Reprise de 2024, {french}", "fr", "native", False)
    p.add("slug", path=f"cd/2024-12-15-legislative-rerun/{tier}-{name.lower()}", entity_id=contest, is_current=True)
    result_set = key("result_set", scope)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="preliminary",
        kind="counted",
        source_id=CENI,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md="Provisional results published by the electoral commission for the whole circonscription. The portal "
        "lists each candidate's votes; the figure it prints for a list is shown only on a list's elected "
        "candidates and is not stored.",
        lang="en",
    )
    unit = key("unit", code)
    if not any(r["id"] == unit for r in p.rows.get("reporting_unit", [])):
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CONGO,
            scheme_code=CIRCONSCRIPTION,
            code=f"2024-12-15:{code}",
            area_relation="equals",
            area_id=area(CIRCONSCRIPTION, code),
            kind="standard",
        )
        p.name(unit, name, "fr", "official")
    lists: dict[str, str] = {}
    elected: dict[str, int] = defaultdict(int)
    position = 0
    for row in data:
        if row["candidat_id"] == "0":
            continue
        position += 1
        sigle = re.sub(r"\s+", " ", str(row["sigle"])).strip()
        is_independent = independent(sigle)
        list_key = norm(sigle)
        if list_key not in lists:
            option = key("option", scope, "list", list_key)
            lists[list_key] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="independents" if is_independent else "list",
                party_id=None if is_independent else party_id(sigle),
                ballot_label=sigle,
            )
        candidate = str(row["candidat_id"])
        option = key("option", scope, candidate)
        person = key("person", candidate)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=lists[list_key],
            ballot_position=position,
            ballot_label=str(row["candidat"]),
        )
        p.add("person", id=person)
        p.name(person, str(row["candidat"]).strip(), "fr", "official")
        candidacy = key("candidacy", candidate)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=lists[list_key],
            role="list_member",
            ballot_name=str(row["candidat"]).strip(),
            member_of_party_id=None if is_independent else party_id(sigle),
            is_independent=is_independent,
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=count(row, "voixobtenues"),
        )
        if row["voixliste"] is not None:
            elected[lists[list_key]] += 1
            p.add(
                "outcome", result_set_id=result_set, option_id=lists[list_key], candidacy_id=candidacy, kind="elected"
            )
    if sum(elected.values()) != seats:
        raise ValueError(f"{scope}: {sum(elected.values())} elected for {seats} seats")
    for option, n in elected.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=n)
    registered, cast = count(first, "electeurs_attendus_bvd"), count(first, "votants_bvd")
    blank, valid = count(first, "bulletins_blancs"), count(first, "suffrage_exprime")
    for measure, value in ((REGISTERED, registered), (CAST, cast), (VALID, valid), (BLANK, blank)):
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2024-12-15"]


def parse() -> None:
    build_geo().write()
    build_registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())
