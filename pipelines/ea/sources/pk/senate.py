"""Pakistan's Senate: the members returned at the triennial elections of 2015, 2021, and 2024 and the by-election of
March 2024, by province and category of seat.

Each province's Assembly elects its senators by the single transferable vote, and the National Assembly's members
from the Federal Capital elect its four, in classes of half the seats every three years. The Election Commission
publishes a notification for each province of the candidates returned, grouped by category of seat (general,
technocrats including ulema, women, and non-Muslims), with the party printed beside each name. A notification under
section 118 of the Elections Act names candidates returned uncontested, which is a result decided without a count;
every other notification names the candidates the poll returned. The notifications print no votes, so the
contests hold the declared outcomes only. The notifications of 2015 and 2021 are scans, and each name was read from
the page image and checked against the Senate Secretariat's term-wise list and the Commission's list of former
members. The transcription is in `senate.json`; each notice's seat count is checked against the number its text
states.

Province shapes come from geoBoundaries (CC BY 4.0). They are the provinces as drawn today, so Khyber Pakhtunkhwa's
shape is dated from the merger of the tribal areas in 2018."""

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "pk.senate"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
SCHEME = "pk.province"
WAYBACK = "https://web.archive.org/web"
ECP = {
    "sn15": f"{WAYBACK}/20240813024930id_/https://ecp.gov.pk/storage/files/1/sn15.pdf",
    "hKfz": f"{WAYBACK}/20240813021938id_/https://ecp.gov.pk/storage/uploads/hKfzkhKAudUsRAsQETHPNQfj3gqRU15qAuSGV1lE.pdf",
    "kVwO": f"{WAYBACK}/20240813014229id_/https://ecp.gov.pk/storage/uploads/kVwO8jrkEolfnrqxvui1AuyNqmGJeEzec83ZPyT1.pdf",
    "EJbw": f"{WAYBACK}/20240813022129id_/https://ecp.gov.pk/storage/uploads/EJbwpfjYQgjpL21H4Eg8XLNdGpfAjqoBX9mqsuQC.pdf",
    "32984": f"{WAYBACK}/20240707212455id_/https://ecp.gov.pk/storage/uploads/32984.Jpeg",
    "32986": f"{WAYBACK}/20240707212514id_/https://ecp.gov.pk/storage/uploads/32986.Jpeg",
    "32987": f"{WAYBACK}/20240709155703id_/https://ecp.gov.pk/storage/uploads/32987.Jpeg",
    "L262": f"{WAYBACK}/20240707212536id_/https://ecp.gov.pk/storage/uploads/L262FQhkn4EycJgJRy0ISxFGhF5sPPmBIeiF75W6.png",
}
CHECKS = {
    "term_wise": "https://senate.gov.pk/2web/TERM-WISE%20LIST.pdf",
    "members": f"{WAYBACK}/20250403074505id_/https://ecp.gov.pk/storage/files/1/Member%20list.pdf",
}
PAGE = "https://ecp.gov.pk/senate-elections"
GEOBOUNDARIES = "pk.geoboundaries"
PROVINCES_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/PAK/ADM1/"
    "geoBoundaries-PAK-ADM1_simplified.geojson"
)
MERGER = date(2018, 5, 31)
DATA: dict[str, Any] = json.loads((Path(__file__).with_name("senate.json")).read_text())

# The geoBoundaries name of each area the notifications name.
AREAS = {
    "Federal Capital": "Islamabad Capital Territory",
    "Punjab": "Punjab",
    "Sindh": "Sindh",
    "Khyber Pakhtunkhwa": "Khyber Pakhtunkhwa",
    "Balochistan": "Balochistan",
}
CATEGORIES = {
    "general": ("general seats", "General seats"),
    "technocrat": ("technocrats including ulema", "Seats for technocrats including ulema"),
    "women": ("women", "Seats for women"),
    "non_muslim": ("non-Muslims", "Seats for non-Muslims"),
}
# The seats the notification text states for each category, by election and area.
EXPECTED = {
    ("2015", "Balochistan"): {"general": 7, "technocrat": 2, "women": 2, "non_muslim": 1},
    ("2015", "Khyber Pakhtunkhwa"): {"general": 7, "technocrat": 2, "women": 2, "non_muslim": 1},
    ("2015", "Sindh", "returned"): {"general": 7},
    ("2015", "Sindh", "uncontested"): {"technocrat": 2, "women": 2},
    ("2021", "Federal Capital"): {"general": 1, "women": 1},
    ("2021", "Punjab"): {"general": 7, "technocrat": 2, "women": 2},
    ("2021", "Sindh"): {"general": 7, "technocrat": 2, "women": 2},
    ("2021", "Khyber Pakhtunkhwa"): {"general": 7, "technocrat": 2, "women": 2, "non_muslim": 1},
    ("2021", "Balochistan"): {"general": 7, "technocrat": 2, "women": 2, "non_muslim": 1},
    ("2024", "Federal Capital"): {"general": 1, "technocrat": 1},
    ("2024", "Balochistan"): {"general": 7, "technocrat": 2, "women": 2},
    ("2024-bye", "Federal Capital"): {"general": 1},
    ("2024-bye", "Sindh"): {"general": 2},
    ("2024-bye", "Balochistan"): {"general": 3},
}
# The parties as the notifications print them, by the printed text.
PARTIES = {
    "ANP": "Awami National Party",
    "BAP": "Balochistan Awami Party",
    "BNP": "Balochistan National Party",
    "BNP(M)": "Balochistan National Party (Mengal)",
    "JI": "Jamaat-e-Islami Pakistan",
    "JUI(F)": "Jamiat Ulema-e-Islam (F)",
    "JUIP": "Jamiat Ulema-e-Islam Pakistan",
    "MQM": "Muttahida Qaumi Movement",
    "MQM(P)": "Muttahida Qaumi Movement Pakistan",
    "National Party": "National Party",
    "NP": "National Party",
    "PMA": "PMA",
    "PMAP": "Pakhtunkhwa Milli Awami Party",
    "PML": "Pakistan Muslim League",
    "PML(N)": "Pakistan Muslim League (N)",
    "PML-N": "Pakistan Muslim League (N)",
    "PPPP": "Pakistan Peoples Party Parliamentarians",
    "PTI": "Pakistan Tehreek-e-Insaf",
}
ELECTIONS = {
    "2015": (
        date(2015, 3, 1),
        "general",
        "2015 Pakistani Senate election",
        "March 2015",
        "The triennial election in place of the members whose term expired on 11 March 2015.",
    ),
    "2021": (
        date(2021, 3, 1),
        "general",
        "2021 Pakistani Senate election",
        "March 2021",
        "The triennial election in place of the members whose term expired on 11 March 2021.",
    ),
    "2024": (
        date(2024, 4, 1),
        "general",
        "2024 Pakistani Senate election",
        "April 2024",
        "The triennial election in place of the members whose term expired on 11 March 2024.",
    ),
    "2024-bye": (
        date(2024, 3, 1),
        "by_election",
        "2024 Pakistani Senate by-election",
        "March 2024",
        "The by-election of March 2024 for seats left vacant, each for the remaining term to 11 March 2027.",
    ),
}
METHOD = (
    "Published by the Election Commission of Pakistan in its notification of the candidates returned to the Senate, "
    "which names each candidate with the party affiliation, the father's or husband's name, and the address, and "
    "prints no votes. Only the name and the party are stored."
)
TACIT = (
    "The Commission's notification, issued under section 118 of the Elections Act, 2017, names these candidates as "
    "returned uncontested: no poll was held for the seats."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECP_SOURCE = key("source", "ecp")
SENATE_SOURCE = key("source", "senate-secretariat")
SHAPES = key("source", "geoboundaries-pak")
BODY = key("body", "senate")
SYSTEM = key("electoral_system", "senate", "stv")


def area(name: str) -> str:
    return key("area", SCHEME, name)


def party(printed: str) -> str | None:
    return None if printed == "Independent" else key("party", PARTIES[printed])


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e}" for e in ELECTIONS)]


def fetch() -> None:
    for url in ECP.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower())
    for url in CHECKS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(GEOBOUNDARIES, PROVINCES_URL, suffix=".geojson")


def parse() -> None:
    check()
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def check() -> None:
    """Each notice holds the seats its text states."""
    for notice in DATA["notices"]:
        expected = EXPECTED.get(
            (notice["election"], notice["area"], notice["basis"]),
            EXPECTED.get((notice["election"], notice["area"])),
        )
        found = {category: len(rows) for category, rows in notice["seats"].items()}
        if expected != found:
            raise ValueError(f"{notice['election']} {notice['area']}: {found} but the notice states {expected}")
        for rows in notice["seats"].values():
            for _, printed in rows:
                if printed != "Independent" and printed not in PARTIES:
                    raise ValueError(f"unknown party {printed}")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The provinces of Pakistan and the Federal Capital, from geoBoundaries",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Pakistan ADM1 (simplified)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    artifact = raw.cached(GEOBOUNDARIES, PROVINCES_URL)
    p.artifact(artifact, SHAPES)
    p.add("scheme", code=SCHEME, polity_id=PAKISTAN, kind="administrative", rank=1, noun="province")
    features = {
        f["properties"]["shapeName"]: f for f in json.loads(artifact.path.read_text(encoding="utf-8"))["features"]
    }
    for name, theirs in AREAS.items():
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.name(id, name, "en", "official")
        shape = shapely.geometry.shape(features[theirs]["geometry"])
        if not shape.is_valid:
            shape = shapely.make_valid(shape)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=MERGER if name == "Khyber Pakhtunkhwa" else None,
            geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Senate of Pakistan, its electoral system, the parties of the senators returned, and the sources",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Senate elections: notifications of the candidates returned",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in ECP.values():
        p.artifact(raw.cached(SOURCE, url), ECP_SOURCE)
    p.artifact(raw.cached(SOURCE, CHECKS["members"]), ECP_SOURCE)
    p.add(
        "source",
        id=SENATE_SOURCE,
        kind="official_body",
        publisher="Senate Secretariat of Pakistan",
        title="Term-wise list of members of the Senate",
        url="https://senate.gov.pk/2web/TERM-WISE%20LIST.pdf",
        license="Official publication; reuse terms are not stated",
        attribution="Senate Secretariat of Pakistan",
    )
    p.artifact(raw.cached(SOURCE, CHECKS["term_wise"]), SENATE_SOURCE)
    p.add("body", id=BODY, polity_id=PAKISTAN, kind="upper_house")
    p.name(BODY, "Senate of Pakistan", "en", "official")
    p.add(
        "electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "stv", "quota": "droop"}, rules_version=1
    )
    p.name(SYSTEM, "Election of senators by the single transferable vote", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each Provincial Assembly elects its senators for the general seats, the seats for technocrats "
        "including ulema, the seats for women, and the seat for non-Muslims by the single transferable vote, and the "
        "National Assembly's members elect the Federal Capital's senators the same way (Constitution, Article 59). "
        "Half of the seats fall vacant every three years. The notifications print no votes.",
        lang="en",
    )
    for printed in sorted(
        {p_ for n in DATA["notices"] for rows in n["seats"].values() for _, p_ in rows} - {"Independent"}
    ):
        id = party(printed)
        if id is None or any(r["id"] == id for r in p.rows.get("party", [])):
            continue
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, PARTIES[printed], "en", "official")
    return p


def build(election_key: str) -> Package:
    first, kind, title, when, text = ELECTIONS[election_key]
    p = Package(
        f"{SOURCE}/{election_key}",
        SOURCE,
        f"The {title} by province and category of seat, from the Election Commission's notifications of the "
        "candidates returned",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(PAKISTAN)
    notices = [n for n in DATA["notices"] if n["election"] == election_key]
    for url in sorted({ECP[n["ref"]] for n in notices}):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", election_key)
    p.add(
        "election",
        id=election,
        polity_id=PAKISTAN,
        kind=kind,
        date_from=first,
        date_to=None,
        date_precision="month",
        status="held",
    )
    slug = f"pk/{first:%Y-%m}-senate" + ("-by-election" if kind == "by_election" else "")
    p.add("slug", path=slug, entity_id=election, is_current=True)
    p.name(election, title, "en", "official")
    dated = "; ".join(sorted({f"{n['area']} {n['dated']}" for n in notices}))
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="context",
        body_md=f"{text} The Commission's notifications are dated {dated}. They print no poll date, so the election "
        f"is dated {when}.",
        lang="en",
    )
    for notice in notices:
        tacit = notice["basis"] == "uncontested"
        area_name = notice["area"]
        for category, rows in notice["seats"].items():
            label = CATEGORIES[category][0]
            contest = key("contest", election_key, area_name, category)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=BODY,
                district_area_id=area(area_name),
                magnitude=len(rows),
                round_number=1,
                electoral_system_id=SYSTEM,
                default_result_kind="tacit" if tacit else "counted",
            )
            p.name(contest, f"{title}, {area_name}, {label}", "en", "official")
            p.add(
                "slug",
                path=f"{slug}/{area_name.lower().replace(' ', '-')}-{category.replace('_', '-')}",
                entity_id=contest,
                is_current=True,
            )
            result_set = key("result_set", election_key, area_name, category)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=ECP_SOURCE,
                status="active",
                is_default=True,
                completeness=None if tacit else 0.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD + (" " + TACIT if tacit else ""),
                lang="en",
            )
            for position, (name, printed) in enumerate(rows, 1):
                tag = (election_key, area_name, category, position)
                person = key("person", *tag)
                option = key("option", *tag)
                p.add("person", id=person)
                p.name(person, name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(printed),
                    ballot_position=position,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", *tag),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party(printed),
                    is_independent=printed == "Independent",
                )
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p
