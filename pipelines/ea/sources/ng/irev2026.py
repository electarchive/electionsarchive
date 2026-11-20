"""The Federal Capital Territory's area council elections of 21 February 2026 by polling unit, from the Commission's
result viewing portal.

The portal (IReV) lists, for each of the six chairmanship elections and the 62 councillorship elections, the polling
units of the area with the figures the Commission entered from each unit's Form EC8A: registered voters, accredited
voters, ballot papers issued and used, rejected and valid votes, and the votes of each party that fielded a
candidate. Its API answers `elections` with every election on the portal, `elections/<id>/lga` with the area and its
wards, and `elections/<id>/pus?ward=<id>` with a ward's polling units and their figures.

The portal names no candidates and declares no winners, and it carries no collated totals (its area records hold
zeros), so the sets here are the polling units' own figures with options by party. Area and ward totals are what the
units add up to.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import uuid5

import httpx

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ng import inec2022, irev2023

SOURCE = "ng.irev"
API = "https://dolphin-app-sleqh.ondigitalocean.app/api/v1/elections"
DAY = date(2026, 2, 21)
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
FCT = "37"


def _json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not data.get("success"):
        raise ValueError(f"{path} is not a successful answer")
    return data


def listing() -> list[dict]:
    """The portal's FCT elections of the day: chairmanships and councillorships, ordered by election id."""
    found = [
        e
        for e in _json(raw.cached(SOURCE, API).path)["data"]
        if e["election_date"][:10] == DAY.isoformat()
        and e["state"]["code"] == FCT
        and e["election_type"]["name"] in ("Chairmanship", "Councillor")
    ]
    return sorted(found, key=lambda e: e["election_id"])


def ward_url(election: dict, ward: str) -> str:
    return f"{API}/{election['_id']}/pus?ward={ward}"


def area_url(election: dict) -> str:
    return f"{API}/{election['_id']}/lga"


def council_of(election: dict) -> str:
    """The portal's name for the area council an election belongs to."""
    (entry,) = _json(raw.cached(SOURCE, area_url(election)).path)["data"]
    return entry["lga"]["name"]


def wards(election: dict) -> list[dict]:
    return [w for entry in _json(raw.cached(SOURCE, area_url(election)).path)["data"] for w in entry["wards"]]


def _get(url: str) -> None:
    """Fetch one URL, trying again when the portal's proxy times out (a 522 comes and goes)."""
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, url, suffix=".json")
            return
        except httpx.HTTPError:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


def fetch() -> None:
    fetch_shapes()
    _get(API)
    with ThreadPoolExecutor(8) as pool:
        areas = list(pool.map(_get, [area_url(e) for e in listing()]))
        del areas
        urls = [ward_url(e, w["_id"]) for e in listing() for w in wards(e)]
        list(pool.map(_get, urls))


# Measures, as `ea.sources.ng.irev2023` uses them.
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
# The portal's area council names against the number the geo package gives each council.
COUNCILS = {
    "ABAJI": ("Abaji", 1),
    "BWARI": ("Bwari", 2),
    "GWAGWALADA": ("Gwagwalada", 3),
    "KUJE": ("Kuje", 4),
    "KWALI": ("Kwali", 5),
    "MUNICIPAL": ("Abuja Municipal", 6),
}
# The sheet's figures, by the portal's field and the archive's measure code.
FIGURES = {
    "total_registered": REGISTERED,
    "total_accredited": VOTERS,
    "valid_votes": VALID,
    "invalid_votes": INVALID,
}
URL = "https://www.inecelectionresults.ng/"
# Parties on the portal that the Commission's registry package does not hold; the portal gives only the abbreviation.
NEW_PARTIES = {"POP"}
METHOD = (
    "The figures are those the Commission's result viewing portal (IReV) shows for each polling unit's Form EC8A: "
    "registered voters, accredited voters, rejected ballots, total valid votes, and each party's votes. The portal "
    "names no candidates, so each option stands for the party's candidate. Ballot papers issued and used have no "
    "measure here. A polling unit the portal lists without figures (its result not yet uploaded) is left out, so "
    "the set covers only the units that reported. These are the polling units' own figures, not a collated "
    "declaration; the portal carries no declared totals or winners."
)


KEY = f"{SOURCE}/{DAY}"
GEO = f"{SOURCE}/geo-fct"
# The wards the portal spells differently from the declaration (the same ward by code and by place in the order).
RESPELT = {(2, "07"), (3, "01"), (6, "05"), (5, "01"), (4, "01")}
# GRID3's name for each ward, by council number and the ward's code in the declaration's order.
GRID3_WARDS = {
    1: ["Central Abaji", "North East", "South East", "Pandagi", "Ebagi", "Nuku", "Alu Mamagi", "Yaba", "Gurdi", "Gawu"],
    2: ["Bwari Central", "Kuduru", "Igu", "Shere", "Kawu", "Ushafa", "Dutse", "Byazhin", "Kubwa", "Usuma"],
    3: ["Central-Gwa", "Kutunku", "Quarters", "Ibwa", "Dobi", "Paiko", "Tungan Maje", "Zuba", "Ikwa", "Gwako"],
    4: ["Kuje", "Chibiri", "Gaube", "Kwaku", "Kabi", "Rubochi", "Gwargwada", "Gudunkarya", "Kujekwa", "Yenche"],
    5: ["Kwali", "Yangoji", "Pai", "Kinlakwa", "Dafa", "Kundu 1", "Ashara", "Gumbo", "Wako", "Yebu"],
    6: [
        "City Center 1", "Garki 1", "Kabusa", "Wuse", "Gwarinpa", "Jiwa",
        "Gui", "Karshi 1", "Orozo", "Karu", "Nyanya 1", "Gwagwa",
    ],
}  # fmt: skip
# GRID3's and geoBoundaries' names for each area council.
GRID3_COUNCILS = {1: "Abaji", 2: "Bwari", 3: "Gwagwalada", 4: "Kuje", 5: "Kwali", 6: "Municipal Area Council"}


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def title(text: str) -> str:
    text = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", text)).strip()
    words = re.sub(r"[A-Za-z]+", lambda m: m[0].capitalize(), text)
    return re.sub(r"\b(I{1,3}|Iv|Vi{0,3})\b", lambda m: m[0].upper(), words)


def ward_areas() -> dict[tuple[int, str], tuple[str, str]]:
    """The geo package's ward area and name, by council number and the portal's ward code, matched on the name the
    2022 declaration prints (the portal's ward codes follow the same order)."""
    found: dict[tuple[int, str], tuple[str, str]] = {}
    for _, number in inec2022.COUNCILS:
        for name, code in inec2022.WARDS[number].items():
            found[(number, code)] = (inec2022.area("ng.ward", f"{number:02d}/{code}"), name)
    return found


def build() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The 21 February 2026 FCT area council elections: the six chairmanship elections and the 62 councillorship "
        "elections by polling unit, from the figures on the Commission's result viewing portal",
        depends=[f"{inec2022.SOURCE}/geo", f"{inec2022.SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {NIGERIA, inec2022.COUNTRY}
    source = p.id("source", "irev", DAY)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title="INEC Result Viewing Portal (IReV): FCT Area Council elections, 21 February 2026",
        url=URL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    for key_ in sorted(raw.keys(SOURCE)):
        if key_ == API or key_.startswith(API + "/"):
            p.artifact(raw.cached(SOURCE, key_), source)
    election_id = p.id("election")
    path_ = "ng/2026-02-21-area-council"
    p.add("election", id=election_id, polity_id=NIGERIA, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2026 FCT Area Council elections", "en", "official")
    p.add("slug", path=path_, entity_id=election_id, is_current=True)
    plurality = inec2022.shared("electoral_system", "fptp")
    new_parties: set[str] = set()
    geo_wards = ward_areas()
    units: dict[str, str] = {}
    sheets = mismatched = 0
    for election in listing():
        council, number = COUNCILS[council_of(election)]
        chair = election["election_type"]["name"] == "Chairmanship"
        portal_wards = wards(election)
        if chair:
            tag = f"chairman:{number}"
            district = inec2022.area("ng.area_council", f"{number:02d}")
            name = f"2026 {council} Area Council chairmanship election"
            slug = f"{path_}/area-council-chairman/{changeset.slug(council)}"
            office, body, kind = inec2022.shared("office", "chairman", number), None, "office"
        else:
            (ward,) = portal_wards
            district, ward_name = geo_wards[(number, ward["code"])]
            if squash(ward_name) != squash(ward["name"]) and (number, ward["code"]) not in RESPELT:
                raise ValueError(f"ward {number}/{ward['code']}: portal {ward['name']!r}, declaration {ward_name!r}")
            tag = f"councillor:{number}:{ward['code']}"
            name = f"2026 {council} Area Council councillorship election, {title(ward['name'])} ward"
            slug = f"{path_}/area-council-councillor/{changeset.slug(council)}/{ward['code']}-{changeset.slug(ward['name'])}"
            office, body, kind = None, inec2022.shared("body", "council", number), "body_seats"
        p.external |= {x for x in (district, office, body) if x}
        contest = p.id("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=plurality,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        rows = []
        for ward in portal_wards:
            rows += _json(raw.cached(SOURCE, ward_url(election, ward["_id"])).path)["data"]
        reported = [r for r in rows if r.get("votes") is not None]
        parties = sorted({v["party_code"] for r in reported for v in _votes(r)})
        options = {}
        for position, code in enumerate(parties, 1):
            acronym = code.upper()
            options[code] = p.id("option", tag, acronym)
            if acronym in NEW_PARTIES:
                party_id = p.id("party", acronym)
                if acronym not in new_parties:
                    new_parties.add(acronym)
                    p.add("party", id=party_id, kind="party", polity_id=NIGERIA)
                    p.add("external_id", entity_id=party_id, scheme="ng.irev.party", value=acronym)
                    p.name(party_id, acronym, "en", "abbreviation")
            else:
                party_id = inec2022.party(acronym)
                p.external.add(party_id)
            p.add(
                "contest_option",
                id=options[code],
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=position,
                ballot_label=acronym,
            )
        result_set = p.id("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=round(len(reported) / len(rows), 4) if rows else None,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        silent = len(rows) - len(reported)
        if silent:
            p.add(
                "note",
                id=p.id("note", result_set, "unreported"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The portal lists {len(rows)} polling units for this election and shows figures for "
                f"{len(reported)}; the other {silent} are left out.",
                lang="en",
            )
        for row in reported:
            sheets += 1
            mismatched += sum(v["vote"] for v in _votes(row)) != row["valid_votes"]
            pu = row["polling_unit"]
            code = pu["pu_code"]
            if code not in units:
                units[code] = p.id("unit", code)
                ward_area, _ = geo_wards[(number, pu["ward"]["code"])]
                p.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=NIGERIA,
                    scheme_code="ng.polling_unit",
                    code=code,
                    area_relation="within",
                    area_id=ward_area,
                    kind="standard",
                )
                p.name(units[code], title(pu["name"]), "en", "official")
            unit = units[code]
            for party_code, count in ((v["party_code"], v["vote"]) for v in _votes(row)):
                _fact(p, result_set, unit, options[party_code], VOTES, count)
            for field, measure in FIGURES.items():
                _fact(p, result_set, unit, None, measure, row[field])
    p.accept(
        "accounting.votes_valid",
        f"At {mismatched:,} of the {sheets:,} polling-unit figure sets, the parties' votes the portal shows do not add "
        "up to the total valid votes it shows for the same unit (some by a few votes, some by far more, and a few "
        "units show parties' votes against zero valid votes). The portal's figures are stored as shown, and each "
        "result set's note says the figures are the units' own, not a collated declaration.",
    )
    p.accept(
        "accounting.turnout",
        "One polling unit's figures on the portal show more votes than accredited voters; they are stored as shown.",
    )
    return p


def _fact(p: Package, result_set: str, unit: str, option: str | None, measure: int, number: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=number,
    )


def _votes(row: dict) -> list[dict]:
    votes = row["votes"]
    return json.loads(votes) if isinstance(votes, str) else votes


def fetch_shapes() -> None:
    raw.fetch(irev2023.SHAPES, irev2023.LGAS, suffix=".geojson")
    raw.fetch(irev2023.GRID3, irev2023.WARDS.format("FC"), suffix=".geojson")


def geo() -> Package:
    """Outlines for the FCT area councils and wards the geo package lists without shapes, from geoBoundaries and
    GRID3, with a boundary set for each of the two schemes."""
    p = Package(
        GEO,
        SOURCE,
        "Outlines of the six FCT area councils (geoBoundaries) and 62 wards (GRID3), for the areas the Commission's "
        "geo package lists",
        depends=[f"{inec2022.SOURCE}/geo"],
    )
    lga_source = irev2023.key("source", irev2023.SHAPES, "adm2")
    ward_source = irev2023.key("source", irev2023.GRID3, "wards-v4")
    lga_file = raw.cached(irev2023.SHAPES, irev2023.LGAS)
    ward_url_ = irev2023.WARDS.format("FC")
    ward_file = raw.cached(irev2023.GRID3, ward_url_)
    p.add(
        "source",
        id=lga_source,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Nigeria ADM2 (local government areas, simplified; from GRID3)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); GRID3",
    )
    p.artifact(lga_file, lga_source)
    p.add(
        "source",
        id=ward_source,
        kind="academic",
        publisher="GRID3",
        title="GRID3 NGA - Operational Wards v4.0",
        url="https://data.grid3.org/datasets/GRID3::grid3-nga-operational-wards-v4-0/about",
        license="Free to use, copy, distribute, and adapt with attribution (GRID3 terms of use)",
        attribution="GRID3 (Geo-Referenced Infrastructure and Demographic Data for Development); CIESIN",
    )
    p.artifact(ward_file, ward_source)
    for scheme, file in (("ng.area_council", lga_file), ("ng.ward", ward_file)):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme),
            scheme_code=scheme,
            vintage="geoBoundaries gbOpen (GRID3)" if scheme == "ng.area_council" else "GRID3 Operational Wards v4.0",
            as_of=file.retrieved_at.date(),
        )
    shaped = irev2023.gb_lgas()
    found: dict[tuple[str, str], object] = {}
    for feature in json.loads(ward_file.path.read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        place = (props["lga"], props["ward"])
        if place in found:
            raise ValueError(f"GRID3 lists {place} twice")
        found[place] = irev2023.shapely.geometry.shape(feature["geometry"])
    used = set()
    for number, names in GRID3_WARDS.items():
        council = GRID3_COUNCILS[number]
        area = inec2022.area("ng.area_council", f"{number:02d}")
        p.external.add(area)
        irev2023.outline(p, area, shaped[council], lga_source)
        for index, name in enumerate(names, 1):
            ward = inec2022.area("ng.ward", f"{number:02d}/{index:02d}")
            p.external.add(ward)
            irev2023.outline(p, ward, found[(council, name)], ward_source)
            used.add((council, name))
    if used != set(found):
        raise ValueError(f"GRID3 wards left unused: {sorted(set(found) - used)}")
    return p


def parse() -> None:
    geo().write()
    build().write()


def emit() -> None:
    package.run("load", [GEO, KEY])
