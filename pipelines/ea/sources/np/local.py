"""Election Commission of Nepal: the 13 May 2022 local elections by local level and ward, from the commission's
results portal, with the local-level boundaries its results map draws."""

import functools
import json
from collections import defaultdict
from datetime import date
from typing import Any
from uuid import uuid5

import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "np.local"
NEPAL = str(uuid5(package.NAMESPACE, "polity\x1fNP"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNPL"))
PORTAL = "https://result.election.gov.np/"
DATA_URL = PORTAL + "JSONFiles/Election2079/Local/"
STATES_URL = DATA_URL + "Lookup/states.json"
DISTRICTS_URL = DATA_URL + "Lookup/districts.json"
LOCALBODIES_URL = DATA_URL + "Lookup/localbodies.json"
POSTS_URL = DATA_URL + "VdcPost.json"
SHAPES_URL = PORTAL + "JSONFiles/JSONMap/geojson/LL/DCODE_"
DAY = "2022-05-13"
DATE = date(2022, 5, 13)
TACIT = "निर्विरोध"
ELECTED = {"निर्वाचित", TACIT}
INDEPENDENT = "स्वतन्त्र"
GENDER = {"पुरुष": "male", "महिला": "female"}

LOCAL, WARD = "np.local", "np.ward"
# The posts of the portal's own numbering: the English name, the Nepali name, and how the seat is filled.
POSTS: dict[int, tuple[str, str]] = {
    1: ("Chairperson", "अध्यक्ष"),
    2: ("Vice chairperson", "उपाध्यक्ष"),
    3: ("Mayor", "प्रमुख"),
    4: ("Deputy mayor", "उपप्रमुख"),
    5: ("Ward chairperson", "वडा अध्यक्ष"),
    6: ("Ward member", "वडा सदस्य"),
    7: ("Ward woman member", "वडा महिला सदस्य"),
    8: ("Ward Dalit woman member", "वडा दलित महिला सदस्य"),
    9: ("Additional member, post 9", "थप सदस्य, पद ९"),
    10: ("Additional member, post 10", "थप सदस्य, पद १०"),
    11: ("Additional member, post 11", "थप सदस्य, पद ११"),
    12: ("Additional member, post 12", "थप सदस्य, पद १२"),
}
HEADS = {1, 2, 3, 4}
WARD_POSTS = {5, 6, 7, 8}
SEATS = {6: 2, 10: 2}
OFFICE_KIND = {1: "other", 2: "other", 3: "mayor", 4: "deputy_mayor", 5: "other"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def eci_area(scheme: str, code: str) -> str:
    """An id the Election Commission's constituency pipeline already minted for a province or district."""
    return str(uuid5(package.NAMESPACE, f"np.eci\x1farea\x1f{scheme}\x1f{code}"))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


NCOMMISSION = key("source", SOURCE)
BLOCK = key("electoral_system", "block")
FPTP = key("electoral_system", "fptp")
WARD_COMMITTEE = key("body", "ward_committee")
LOCAL_GOVERNMENT = key("body", "local_government")


def body_url(local: int | str) -> str:
    return f"{DATA_URL}{local}.json"


def load(url: str) -> Any:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))


@functools.cache
def localbodies() -> list[dict[str, Any]]:
    return load(LOCALBODIES_URL)


def district_codes() -> list[int]:
    return sorted({int(b["parentId"]) for b in localbodies()})


SHAPE_FILE = {78: 76}  # the results map files Western Rukum under its own code, 76


def shape_url(district: int) -> str:
    return f"{SHAPES_URL}{SHAPE_FILE.get(district, district)}.json"


def fetch() -> None:
    for url in (STATES_URL, DISTRICTS_URL, LOCALBODIES_URL, POSTS_URL):
        raw.fetch(SOURCE, url, suffix=".json")
    raw.fetch_many(SOURCE, [body_url(b["id"]) for b in localbodies()], suffix=".json")
    raw.fetch_many(SOURCE, [shape_url(d) for d in district_codes()], suffix=".json")


@functools.cache
def shapes() -> dict[int, dict[str, Any]]:
    """The local-level features of the commission's results map, by local-level code."""
    out: dict[int, dict[str, Any]] = {}
    for district in district_codes():
        for feature in load(shape_url(district))["features"]:
            out[int(feature["properties"]["GN_CODE"])] = feature
    return out


def english(local: int) -> str | None:
    props = shapes().get(local, {}).get("properties")
    if not props:
        return None
    kind = str(props["Type_GN"]).replace("Metropolitian", "Metropolitan")
    return f"{' '.join(str(props['GaPa_NaPa']).split())} {kind}"


@functools.cache
def results() -> dict[int, list[dict[str, Any]]]:
    return {int(b["id"]): load(body_url(b["id"])) for b in localbodies()}


def wards(local: int) -> list[int]:
    return sorted({int(r["Ward"]) for r in results()[local] if r["Ward"] is not None})


def party(party_id: object) -> str:
    return key("party", party_id)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def source(p: Package) -> None:
    p.add(
        "source",
        id=NCOMMISSION,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="Election results portal of the Election Commission, Nepal: 2079 local elections",
        url=PORTAL + "LocalElectionResult2079.aspx",
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )


def outline(p: Package, id: str, feature: dict[str, Any]) -> None:
    shape = shapely.make_valid(shapely.from_geojson(json.dumps(feature["geometry"])))
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    polygons: list[shapely.Geometry] = []
    for part in parts:
        polygons += [x for x in shapely.get_parts(part) if shapely.get_type_id(x) == 3]
    wgs = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
        label_point=shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True),
        quality="official",
        source_id=NCOMMISSION,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nepal's 753 local levels and their wards at the 2022 local elections, with the local-level boundaries of "
        "the Election Commission's results map",
        depends=["np.eci/geo"],
    )
    p.external |= {NEPAL, COUNTRY}
    source(p)
    for url in (LOCALBODIES_URL, POSTS_URL, *(shape_url(d) for d in district_codes())):
        p.artifact(raw.cached(SOURCE, url), NCOMMISSION)
    for code, rank, noun in ((LOCAL, 5, "local level"), (WARD, 6, "ward")):
        p.add("scheme", code=code, polity_id=NEPAL, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("boundary_set", id=key("boundary_set", LOCAL, 2022), scheme_code=LOCAL, vintage="2022", as_of=DATE)
    for lb in localbodies():
        local = int(lb["id"])
        a = area(LOCAL, local)
        p.add("area", id=a, scheme_code=LOCAL)
        p.add("area_code", area_id=a, code=str(local))
        p.add("area_parent", area_id=a, parent_id=eci_area("np.district", str(lb["parentId"])))
        p.name(a, " ".join(str(lb["name"]).split()), "ne", "official")
        en = english(local)
        if en:
            p.name(a, en, "en", "official", False)
        if local in shapes():
            outline(p, a, shapes()[local])
        for ward in wards(local):
            w = area(WARD, f"{local}-{ward}")
            p.add("area", id=w, scheme_code=WARD)
            p.add("area_code", area_id=w, code=f"{local}-{ward}")
            p.add("area_parent", area_id=w, parent_id=a)
            p.name(w, f"{' '.join(str(lb['name']).split())} वडा {ward}", "ne", "official")
            if en:
                p.name(w, f"{en} Ward {ward}", "en", "official", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nepal's local-level offices, ward committees, electoral systems, and the parties that stood in the 2022 "
        "local elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEPAL}
    for id, kind, ne, en in (
        (WARD_COMMITTEE, "council", "वडा समिति", "Ward committee"),
        (LOCAL_GOVERNMENT, "council", "स्थानीय तह", "Local government"),
    ):
        p.add("body", id=id, polity_id=NEPAL, kind=kind)
        p.name(id, en, "en", "official")
        p.name(id, ne, "ne", "native", False)
    for post, kind in OFFICE_KIND.items():
        id = key("office", post)
        p.add("office", id=id, polity_id=NEPAL, body_id=None, kind=kind)
        p.name(id, POSTS[post][0] if post < 5 else "Ward chairperson", "en", "official")
        p.name(id, POSTS[post][1], "ne", "native", False)
    for id, rules, method in (
        (FPTP, {"formula": "fptp"}, "One member for each seat, elected by the most votes."),
        (
            BLOCK,
            {"formula": "block"},
            "Two members, elected by the most votes: each voter marks up to two candidates.",
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEPAL,
            valid_from=date(2015, 9, 20),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[object, tuple[str, str]] = {}
    for rows in results().values():
        for r in rows:
            if r["PoliticalPartyName"] != INDEPENDENT:
                names[r["PartyID"]] = (
                    " ".join(str(r["PoliticalPartyName"]).split()),
                    " ".join(str(r["PoliticalPartyNameEng"] or "").split()),
                )
    for party_id, (ne, en) in sorted(names.items()):
        id = party(party_id)
        p.add("party", id=id, kind="party", polity_id=NEPAL)
        p.name(id, ne, "ne", "official")
        if en:
            p.name(id, en, "en", "official", False)
    return p


METHOD = (
    "Counted by the Election Commission, Nepal, and published on its results portal for each local level and "
    "ward: the votes each candidate received and the candidate it marks as elected. The portal gives no registered, "
    "cast, valid, or invalid totals, so none are stored."
)
TACIT_NOTE = (
    "Every candidate stood unopposed. The portal marks each as elected without unopposed and prints a count of 0, "
    "which is not a vote count, so no votes are stored."
)
UNDECLARED = (
    "The portal marks no candidate as elected for this post, and its three candidates received 20, 20, and 12 "
    "votes for two seats."
)
EXTRA = (
    "The portal numbers this post but gives no name for it, and its results show every elected candidate as "
    "unopposed with a count of 0."
)


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 13 May 2022 Nepal local elections by local level and ward from the Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEPAL, COUNTRY}
    for url in (LOCALBODIES_URL, POSTS_URL, *(body_url(b["id"]) for b in localbodies())):
        p.uses(raw.cached(SOURCE, url))
    path = f"np/{DAY}-local"
    el = key("election", DAY)
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        kind="local",
        date_from=DATE,
        date_to=DATE,
        original_text="२०७९ वैशाख ३०",
        status="held",
    )
    p.name(el, "2022 Nepal local elections", "en", "official")
    p.name(el, "स्थानीय तह निर्वाचन २०७९", "ne", "native", False)
    p.add("slug", path=path, entity_id=el, is_current=True)
    units: set[str] = set()
    for lb in localbodies():
        local = int(lb["id"])
        ne = " ".join(str(lb["name"]).split())
        en = english(local) or ne
        slug = f"{changeset.slug(en)}-{local}"
        groups: dict[tuple[int, int | None], list[dict[str, Any]]] = defaultdict(list)
        for r in results()[local]:
            groups[(int(r["PostId"]), None if r["Ward"] is None else int(r["Ward"]))].append(r)
        for (post, ward), rows in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1] or 0)):
            where = area(LOCAL, local) if ward is None else area(WARD, f"{local}-{ward}")
            code = str(local) if ward is None else f"{local}-{ward}"
            tag = f"{code}:{post}"
            label = POSTS[post]
            ward_en = "" if ward is None else f", ward {ward}"
            ward_ne = "" if ward is None else f", वडा {ward}"
            winners = [r for r in rows if r["Remarks"] in ELECTED]
            tacit = all(r["Remarks"] == TACIT for r in rows)
            c = key("contest", DAY, tag)
            fields: dict[str, Any] = {}
            if post in OFFICE_KIND:
                fields = {"kind": "office", "office_id": key("office", post), "magnitude": 1}
            else:
                seats = SEATS.get(post, len(winners) if post > 8 else 1)
                body = WARD_COMMITTEE if post in WARD_POSTS else LOCAL_GOVERNMENT
                fields = {"kind": "body_seats", "body_id": body, "magnitude": seats}
            system = None
            if post <= 8:
                system = BLOCK if SEATS.get(post, 1) > 1 else FPTP
            p.add(
                "contest",
                id=c,
                election_id=el,
                district_area_id=where,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if tacit else "counted",
                **fields,
            )
            p.name(c, f"2022 local election, {en}{ward_en}: {label[0]}", "en", "official")
            p.name(c, f"स्थानीय तह निर्वाचन २०७९, {ne}{ward_ne}: {label[1]}", "ne", "native", False)
            suffix = f"{post}" if ward is None else f"ward-{ward}-{post}"
            p.add("slug", path=f"{path}/{slug}/{suffix}", entity_id=c, is_current=True)
            rs = key("result_set", DAY, tag)
            p.add(
                "result_set",
                id=rs,
                election_id=el,
                contest_id=c,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=NCOMMISSION,
                status="active",
                is_default=True,
                completeness=0.0 if tacit else 1.0,
            )
            note = METHOD
            if tacit:
                note = TACIT_NOTE
            if post > 8:
                note += " " + EXTRA
            if not winners:
                note += " " + UNDECLARED
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
            unit = key("unit", DAY, code)
            if unit not in units:
                units.add(unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=NEPAL,
                    scheme_code=LOCAL if ward is None else WARD,
                    code=f"{DAY}:{code}",
                    area_relation="equals",
                    area_id=where,
                    kind="standard",
                )
            for r in rows:
                cid = str(r["CandidateID"])
                option = key("option", DAY, tag, cid)
                person = key("person", cid)
                name = " ".join(str(r["CandidateName"]).split())
                solo = r["PoliticalPartyName"] == INDEPENDENT
                member = None if solo else party(r["PartyID"])
                p.add("person", id=person, gender=GENDER.get(str(r["Gender"])))
                p.name(person, name, "ne", "official")
                if r["CandidateNameEng"] and " ".join(str(r["CandidateNameEng"]).split()):
                    p.name(person, " ".join(str(r["CandidateNameEng"]).split()), "en", "official", False)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=c,
                    kind="candidate",
                    party_id=member,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", DAY, tag, cid),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=member,
                    is_independent=solo,
                )
                if not tacit:
                    p.add(
                        "result_value",
                        result_set_id=rs,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=int(r["TotalVoteReceived"]),
                    )
                if r["Remarks"] in ELECTED:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    return p


def verify() -> dict[str, Any]:
    """Counts the portal's lookups let us compare with: the local levels, the wards, and the declared winners."""
    posts = load(POSTS_URL)
    ward_posts = {(p["vdc"], p["postid"]): p["wards"] for p in posts}
    mismatch = {
        local: (wards(local), sorted(int(w) for w in ward_posts.get((str(local), "5"), [])))
        for local in results()
        if wards(local) != sorted(int(w) for w in ward_posts.get((str(local), "5"), []))
    }
    return {
        "local_levels": len(results()),
        "wards": sum(len(wards(local)) for local in results()),
        "elected": sum(1 for rows in results().values() for r in rows if r["Remarks"] in ELECTED),
        "ward_lists_differing_from_the_portal_lookup": mismatch,
    }
