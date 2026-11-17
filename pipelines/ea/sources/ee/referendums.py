"""Estonian national referendums of 1992 and 2003, by county and city, from the electoral committee's result pages.

The 1992 page on valimised.ee prints, for the constitution and for the supplementary question, every county, city and
Tallinn district. The 2003 results on vvk.ee print each county and the cities of Tallinn and Tartu. Those are the
finest figures either authority publishes.
"""

import html
import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ee.referendums"
ESTONIA = str(uuid5(package.NAMESPACE, "polity\x1fEE"))
PAGE_1992 = (
    "https://www.valimised.ee/et/valimiste-arhiiv/rahvahaaletused/"
    "rahvahaaletus-eesti-vabariigi-pohiseaduse-eelnou-pohiseaduse"
)
VVK_2003 = "https://www.vvk.ee/varasemad/rh03/tulemus"
RESULT_2003 = f"{VVK_2003}/haaletust.html"
VALID_2003 = f"{VVK_2003}/haaletusr.html"
BOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EST"
GB_COUNTIES = f"{BOUNDARIES}/ADM1/geoBoundaries-EST-ADM1.geojson"
GB_CITIES = f"{BOUNDARIES}/ADM2/geoBoundaries-EST-ADM2.geojson"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DERIVED = 1

# County code (ISO 3166-2:EE), the stem the sources use, English name.
COUNTIES = {
    "37": ("harju", "Harju"),
    "39": ("hiiu", "Hiiu"),
    "44": ("ida-viru", "Ida-Viru"),
    "49": ("jõgeva", "Jõgeva"),
    "51": ("järva", "Järva"),
    "57": ("lääne", "Lääne"),
    "59": ("lääne-viru", "Lääne-Viru"),
    "65": ("põlva", "Põlva"),
    "67": ("pärnu", "Pärnu"),
    "70": ("rapla", "Rapla"),
    "74": ("saare", "Saare"),
    "78": ("tartu", "Tartu"),
    "82": ("valga", "Valga"),
    "84": ("viljandi", "Viljandi"),
    "86": ("võru", "Võru"),
}
# City slug: (Estonian name, county code, the shape's name in the ADM2 file).
CITIES = {
    "tallinn": ("Tallinn", "37", "Tallinna linn"),
    "tartu": ("Tartu", "78", "Tartu linn"),
    "parnu": ("Pärnu", "67", "Parnu"),
    "narva": ("Narva", "44", "Narva linn"),
    "kohtla-jarve": ("Kohtla-Järve", "44", "Kohtla-Jlrve linn"),
    "sillamae": ("Sillamäe", "44", "Sillamae linn"),
}
CITY_STEMS = {
    "tallinna linn": "tallinn",
    "tallinn": "tallinn",
    "tartu linn": "tartu",
    "pärnu linn": "parnu",
    "narva linn": "narva",
    "kohtla-järve linn": "kohtla-jarve",
    "sillamäe linn": "sillamae",
}
DISTRICTS_1992 = {
    "Tallinn: Lõuna-Tallinn (sh Saue)": "louna",
    "Tallinn: Põhja-Tallinn": "pohja",
    "Tallinn: Lääne-Tallinn": "laane",
    "Tallinn: Ida-Tallinn (sh Maardu)": "ida",
}
EXTRA_1992 = "Lisajaoskonnad"

SYSTEM_RULES = {"formula": "referendum"}
SYSTEM_NOTE = (
    "A question put to a referendum carries when the votes for it outnumber the votes against it. In 1992 the "
    "Supreme Council's decision of 20 May 1992 also required more than half of the citizens on the voting lists to take "
    "part. The archive records the declared outcome and does not recompute it."
)


@dataclass(frozen=True)
class Question:
    slug: str
    en: str
    et: str
    text_en: str
    text_et: str | None
    adopted: bool
    declared: tuple[int, int, int, int | None, int | None]  # electors, cast, invalid, yes, no (cast None if derived)


@dataclass(frozen=True)
class Referendum:
    day: date
    en: str
    et: str
    slug: str
    questions: tuple[Question, ...]
    url: str
    source_title: str
    publisher: str
    attribution: str
    license: str
    method: str


ELECTIONS = {
    "1992": Referendum(
        date(1992, 6, 28),
        "1992 Estonian constitutional referendum",
        "Põhiseaduse rahvahääletus 1992",
        "constitution",
        (
            Question(
                "constitution",
                "Adoption of the Constitution and its implementing act",
                "Põhiseaduse ja põhiseaduse rakendamise seaduse eelnõu",
                "Are you in favor of the draft Constitution of the Republic of Estonia and the draft Constitution "
                "Implementation Act put to the referendum?",
                "Kas Teie olete rahvahääletusele esitatud Eesti Vabariigi põhiseaduse eelnõu ja põhiseaduse "
                "rakendamise seaduse eelnõu poolt?",
                True,
                (669080, 446708, 2694, 407867, 36147),
            ),
            Question(
                "citizenship-applicants",
                "Citizenship applicants' vote in the first elections",
                "Lisaküsimus: kodakondsuse taotlejate osalemine esimestel valimistel",
                'Are you in favor of adding to the Constitution Implementation Act the provision: "After the '
                "Constitution enters into force, applicants for Estonian citizenship who filed their application by "
                '5 June 1992 may also take part in the first Riigikogu and presidential elections"?',
                "Kas Teie olete selle poolt, et põhiseaduse rakendamise seadusse lisatakse säte: „Lubada pärast "
                "põhiseaduse jõustumist toimuval esimesel Riigikogu ja Vabariigi Presidendi valimisel osaleda ka "
                "Eesti kodakondsuse taotlejatel, kes on taotluse esitanud 1992. aasta 5. juuniks“?",
                False,
                (669080, 446478, 3679, 205980, 236819),
            ),
        ),
        PAGE_1992,
        "Rahvahääletus 1992. aastal (valimised.ee)",
        "Riigi Valimisteenistus",
        "Riigi Valimisteenistus, valimised.ee",
        "Published by the State Electoral Office on valimised.ee; no license is stated for this page, reuse with the "
        "source cited",
        (
            "Counted by the National Electoral Committee (Vabariigi Valimiskomisjon) and published by the State "
            "Electoral Office on valimised.ee. Each county, city and Tallinn district row is what the page's turnout "
            "and result tables print: citizens on the voting lists, ballots cast on the main question, and valid and "
            "invalid ballots and yes and no votes on each question. Counties exclude the cities counted apart "
            "(Tallinn, Tartu, Pärnu, Kohtla-Järve, Narva and Sillamäe), and Tallinn is split into four districts, "
            "South Tallinn holding Saue and East Tallinn Maardu. The 'additional polling stations' (lisajaoskonnad) "
            "are the 16 stations abroad and the ships, which the committee counted with South Tallinn. Voters had two "
            "votes, one for each question. Ballots cast on the supplementary question are not printed by unit; "
            "they are the unit's valid and invalid ballots added, which the page's national figures confirm."
        ),
    ),
    "2003": Referendum(
        date(2003, 9, 14),
        "2003 Estonian European Union membership referendum",
        "Euroopa Liiduga ühinemise rahvahääletus 2003",
        "eu-accession",
        (
            Question(
                "eu-accession",
                "Accession to the European Union",
                "Eesti ühinemine Euroopa Liiduga",
                "",
                None,
                True,
                (867714, 555835, 2724, 369657, 183454),
            ),
        ),
        RESULT_2003,
        "Rahvahääletus 2003: hääletamistulemus (vvk.ee)",
        "Vabariigi Valimiskomisjon",
        "Vabariigi Valimiskomisjon, vvk.ee",
        "Published by the National Electoral Committee on vvk.ee; no license is stated, reuse with the source cited",
        (
            "Counted and published by the National Electoral Committee (Vabariigi Valimiskomisjon) on vvk.ee. Each "
            "row is what the committee's result table prints for a county or for the cities of Tallinn and Tartu: "
            "voters on the lists, ballots cast, invalid ballots, and yes and no votes. Counties exclude those two "
            "cities. Valid votes by unit come from the committee's second results table, which prints them. The page "
            "does not print the ballot's wording, so the question is named for its subject."
        ),
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def fetch() -> None:
    for file in (PAGE_1992, RESULT_2003, VALID_2003):
        raw.fetch(SOURCE, file, suffix=".html")
    for file in (GB_COUNTIES, GB_CITIES):
        raw.fetch(SOURCE, file, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for name in ELECTIONS:
        election(name).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading


def clean(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def number(text: str) -> int:
    digits = re.sub(r"[\s\xa0]", "", text)
    if not re.fullmatch(r"\d+", digits):
        raise ValueError(f"not a count: {text!r}")
    return int(digits)


def tables(data: bytes, encoding: str) -> list[list[list[str]]]:
    page = data.decode(encoding)
    out = []
    for table in re.findall(r"<table[^>]*>(.*?)</table>", page, flags=re.DOTALL | re.IGNORECASE):
        rows = [
            [
                clean(re.sub(r"<[^>]+>", " ", cell))
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
            ]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.DOTALL | re.IGNORECASE)
        ]
        out.append(rows)
    return out


@dataclass
class Unit:
    label: str
    registered: int
    cast: int | None
    valid: int
    invalid: int
    yes: int
    no: int


def read_1992() -> list[dict[int, Unit]]:
    """Units of the main and the supplementary question, in the page's order."""
    found = tables(raw.cached(SOURCE, PAGE_1992).path.read_bytes(), "utf-8")
    turnout: dict[str, list[str]] = {}
    results: dict[str, list[str]] = {}
    for table in found:
        for row in table:
            if len(row) == 8 and re.fullmatch(r"[\d ]+", row[1]):
                turnout[row[0]] = row[1:]
            elif len(row) == 11 and re.fullmatch(r"[\d ]+", row[1]):
                results[row[0]] = row[1:]
    if turnout.keys() != results.keys() or "KOKKU" not in turnout:
        raise ValueError("the turnout and result tables list different units")
    total = turnout.pop("KOKKU"), results.pop("KOKKU")
    main: dict[int, Unit] = {}
    extra: dict[int, Unit] = {}
    for index, (label, row) in enumerate(turnout.items()):
        electors, voted, valid1, invalid1, valid2, invalid2 = (number(row[i]) for i in (0, 1, 3, 4, 5, 6))
        cells = results[label]
        yes1, no1, total1, yes2, no2, total2 = (number(cells[i]) for i in (0, 2, 4, 5, 7, 9))
        if (yes1 + no1, yes2 + no2) != (total1, total2) or (total1, total2) != (valid1, valid2):
            raise ValueError(f"{label}: yes and no votes do not add up to the valid ballots")
        if voted != valid1 + invalid1:
            raise ValueError(f"{label}: ballots cast are not valid plus invalid")
        main[index] = Unit(label, electors, voted, valid1, invalid1, yes1, no1)
        extra[index] = Unit(label, electors, None, valid2, invalid2, yes2, no2)
    printed = [number(total[0][i]) for i in (0, 1, 3, 4, 5, 6)] + [number(total[1][i]) for i in (0, 2, 5, 7)]
    if printed != [
        sum(u.registered for u in main.values()),
        sum(u.cast or 0 for u in main.values()),
        sum(u.valid for u in main.values()),
        sum(u.invalid for u in main.values()),
        sum(u.valid for u in extra.values()),
        sum(u.invalid for u in extra.values()),
        sum(u.yes for u in main.values()),
        sum(u.no for u in main.values()),
        sum(u.yes for u in extra.values()),
        sum(u.no for u in extra.values()),
    ]:
        raise ValueError("the units do not add up to the KOKKU row")
    return [main, extra]


def read_2003() -> dict[str, Unit]:
    counts = tables(raw.cached(SOURCE, RESULT_2003).path.read_bytes(), "iso-8859-15")
    valid_page = tables(raw.cached(SOURCE, VALID_2003).path.read_bytes(), "iso-8859-15")
    labels = {"Hääletajaid", "Hääletanuid", "Kehtetuid", "Jah-hääli", "Ei-hääli"}
    totals = {
        cell: number(row[i + 1])
        for t in counts
        for row in t
        for i, cell in enumerate(row[:-1])
        if cell in labels and re.fullmatch(r"\d+", row[i + 1])
    }
    declared_valid: dict[str, int] = {}
    for t in valid_page:
        for row in t:
            if len(row) == 5 and re.fullmatch(r"\d+", row[1]) and row[0].isupper():
                declared_valid[row[0]] = number(row[1])
    units: dict[str, Unit] = {}
    for t in counts:
        for row in t:
            if len(row) == 8 and row[0].isupper() and re.fullmatch(r"\d+", row[1]):
                label = row[0]
                electors, cast, invalid, yes, no = (number(c) for c in row[1:6])
                valid = declared_valid[label]
                if yes + no != valid or cast != valid + invalid:
                    raise ValueError(f"{label}: the two result tables disagree")
                units[label] = Unit(label, electors, cast, valid, invalid, yes, no)
    if len(units) != 17:
        raise ValueError(f"2003 lists {len(units)} units, expected 17")
    declared = (
        totals["Hääletajaid"],
        totals["Hääletanuid"],
        totals["Kehtetuid"],
        totals["Jah-hääli"],
        totals["Ei-hääli"],
    )
    if declared != ELECTIONS["2003"].questions[0].declared:
        raise ValueError(f"2003 national totals read as {declared}")
    return units


# ---------------------------------------------------------------- shapes


def county_of(label: str) -> str | None:
    found = clean(label).lower()
    found = found.removesuffix(" maakond") if " " in found else found.removesuffix("maa")
    for code, (name, _) in COUNTIES.items():
        if found == name:
            return code
    return None


def city_of(label: str) -> str | None:
    return CITY_STEMS.get(clean(label).lower())


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
    parts = [part for part in shapely.get_parts(found) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def shapes() -> tuple[dict[str, shapely.Geometry], dict[str, shapely.Geometry]]:
    counties_by_name = {f"{name} maakond": code for code, (_, name) in COUNTIES.items()}
    counties: dict[str, shapely.Geometry] = {}
    for f in json.loads(raw.cached(SOURCE, GB_COUNTIES).path.read_bytes())["features"]:
        counties[counties_by_name[f["properties"]["shapeName"]]] = _shape(f)
    by_shape = {shape: slug for slug, (_, _, shape) in CITIES.items()}
    cities: dict[str, shapely.Geometry] = {}
    for f in json.loads(raw.cached(SOURCE, GB_CITIES).path.read_bytes())["features"]:
        slug = by_shape.get(f["properties"]["shapeName"])
        if slug is not None:
            cities[slug] = _shape(f)
    if set(counties) != set(COUNTIES) or set(cities) != set(CITIES):
        raise LookupError("the boundary files lack a county or a city")
    return counties, cities


GB_COUNTY_ID = key("source", "geoboundaries-adm1")
GB_CITY_ID = key("source", "geoboundaries-adm2")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Estonian counties and the cities counted apart in the 1992 and 2003 referendums, with shapes from "
        "OpenStreetMap through geoBoundaries",
    )
    p.external.add(ESTONIA)
    for id, title, license_, notes in (
        (GB_COUNTY_ID, "geoBoundaries gbOpen: Estonia ADM1 (counties)", "ODbL", "Boundary year 2016, built Dec 2023."),
        (
            GB_CITY_ID,
            "geoBoundaries gbOpen: Estonia ADM2 (parishes and towns)",
            "ODbL",
            "Boundary year 2017, built Dec 2023. Only the six cities counted apart in the referendums are used.",
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
            title=title,
            url="https://www.geoboundaries.org/",
            license=f"Open Data Commons Open Database License 1.0 (OpenStreetMap contributors); {license_}",
            license_url="https://opendatacommons.org/licenses/odbl/1-0/",
            attribution="geoBoundaries; © OpenStreetMap contributors",
            notes=notes,
        )
    p.artifact(raw.cached(SOURCE, GB_COUNTIES), GB_COUNTY_ID)
    p.artifact(raw.cached(SOURCE, GB_CITIES), GB_CITY_ID)
    p.add("scheme", code="ee.maakond", polity_id=ESTONIA, kind="administrative", rank=1, noun="county")
    p.add("scheme", code="ee.linn", polity_id=ESTONIA, kind="administrative", rank=2, noun="city")
    p.add("scheme", code="ee.rh_unit", polity_id=ESTONIA, kind="precinct", rank=3, noun="reporting unit")
    counties, cities = shapes()
    for code, (_, name) in COUNTIES.items():
        id = area("ee.maakond", code)
        p.add("area", id=id, scheme_code="ee.maakond")
        p.add("area_code", area_id=id, code=code)
        p.name(id, f"{name} maakond", "et", "official")
        p.name(id, f"{name} County", "en", "official", False)
        _geometry(p, id, counties[code], GB_COUNTY_ID)
    for slug, (name, county, _) in CITIES.items():
        id = area("ee.linn", slug)
        p.add("area", id=id, scheme_code="ee.linn")
        p.add("area_code", area_id=id, code=slug)
        p.add("area_parent", area_id=id, parent_id=area("ee.maakond", county))
        p.name(id, name, "et", "official")
        _geometry(p, id, cities[slug], GB_CITY_ID)
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "ee.maakond", "2016"),
        scheme_code="ee.maakond",
        vintage="2016",
        as_of=date(2016, 1, 1),
    )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "ee.linn", "2017"),
        scheme_code="ee.linn",
        vintage="2017",
        as_of=date(2017, 1, 1),
    )
    return p


def _geometry(p: Package, area_id: str, shape: shapely.Geometry, source: str) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=source,
    )


# ---------------------------------------------------------------- packages


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The electoral system of Estonian national referendums")
    p.external.add(ESTONIA)
    id = key("electoral_system", "referendum")
    p.add("electoral_system", id=id, polity_id=ESTONIA, rules=SYSTEM_RULES, rules_version=1)
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def election(name: str) -> Package:
    spec = ELECTIONS[name]
    day = spec.day
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {day.year} Estonian referendum by county and city",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ESTONIA)
    source = p.id("source", name)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=spec.publisher,
        title=spec.source_title,
        url=spec.url,
        license=spec.license,
        attribution=spec.attribution,
    )
    pages = [PAGE_1992] if name == "1992" else [RESULT_2003, VALID_2003]
    for file in pages:
        p.artifact(raw.cached(SOURCE, file), source)

    ident = p.id("election", name)
    p.add("election", id=ident, polity_id=ESTONIA, kind="referendum", date_from=day, date_to=day, status="held")
    p.name(ident, spec.en, "en", "official")
    p.name(ident, spec.et, "et", "native", False)
    base = f"ee/{day}-{spec.slug}"
    p.add("slug", path=base, entity_id=ident, is_current=True)
    p.add("note", id=p.id("note", ident), entity_id=ident, kind="methodology", body_md=spec.method, lang="en")

    if name == "1992":
        sets = read_1992()
        sets_by_slug = dict(zip((q.slug for q in spec.questions), sets, strict=True))
    else:
        found = read_2003()
        sets_by_slug = {spec.questions[0].slug: dict(enumerate(found.values()))}

    units: dict[str, str] = {}

    def unit(label: str) -> str:
        if label in DISTRICTS_1992:
            slug = DISTRICTS_1992[label]
            code, how, where, kind, shown = (
                f"{day}:tallinn:{slug}",
                "within",
                area("ee.maakond", "37"),
                "standard",
                label.replace("Tallinn: ", ""),
            )
        elif label == EXTRA_1992:
            code, how, where, kind, shown = f"{day}:additional", "unlocated", None, "abroad_mission", label
        elif (c := city_of(label)) is not None:
            code, how, where, kind, shown = f"{day}:city:{c}", "equals", area("ee.linn", c), "standard", label
        elif (county := county_of(label)) is not None:
            code, how, where, kind, shown = (
                f"{day}:county:{county}",
                "within",
                area("ee.maakond", county),
                "standard",
                label,
            )
        else:
            raise LookupError(f"{name}: unknown unit {label!r}")
        if code not in units:
            units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=units[code],
                polity_id=ESTONIA,
                scheme_code="ee.rh_unit",
                code=code,
                area_relation=how,
                area_id=where,
                kind=kind,
            )
            p.name(units[code], shown if name == "1992" else shown.title(), "et", "official")
        return units[code]

    rows: list[tuple[str, str, str | None, int, int, int]] = []
    for q in spec.questions:
        contest = p.id("contest", name, q.slug)
        p.add(
            "contest",
            id=contest,
            election_id=ident,
            kind="ballot_question",
            electoral_system_id=key("electoral_system", "referendum"),
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, q.en, "en", "official")
        p.name(contest, q.et, "et", "native", False)
        p.add("slug", path=f"{base}/{q.slug}", entity_id=contest, is_current=True)
        if q.text_et:
            p.add(
                "note",
                id=p.id("note", contest, "question"),
                entity_id=contest,
                kind="context",
                body_md=f"Ballot wording: {q.text_et}\n\nIn English: {q.text_en}",
                lang="et",
            )
        yes = p.id("option", name, q.slug, "yes")
        no = p.id("option", name, q.slug, "no")
        p.add("contest_option", id=yes, contest_id=contest, kind="answer", ballot_position=1, ballot_label="Jah")
        p.add("contest_option", id=no, contest_id=contest, kind="answer", ballot_position=2, ballot_label="Ei")
        result_set = p.id("result_set", name, q.slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=ident,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("outcome", result_set_id=result_set, option_id=yes if q.adopted else no, kind="won")
        electors, cast, invalid, yes_votes, no_votes = q.declared
        valid = yes_votes + no_votes if yes_votes is not None and no_votes is not None else 0
        for option, measure, value in (
            (yes, VOTES, yes_votes),
            (no, VOTES, no_votes),
            (None, REGISTERED, electors),
            (None, CAST, cast),
            (None, VALID, valid),
            (None, INVALID, invalid),
        ):
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
        listed = sets_by_slug[q.slug]
        totals = [0] * 6
        for u in listed.values():
            id = unit(u.label)
            cast_value = u.cast if u.cast is not None else u.valid + u.invalid
            derived_cast = u.cast is None
            for option, measure, value, flag in (
                (yes, VOTES, u.yes, 0),
                (no, VOTES, u.no, 0),
                (None, REGISTERED, u.registered, 0),
                (None, CAST, cast_value, DERIVED if derived_cast else 0),
                (None, VALID, u.valid, 0),
                (None, INVALID, u.invalid, 0),
            ):
                rows.append((result_set, id, option, measure, value, flag))
            for i, v in enumerate((u.yes, u.no, u.registered, cast_value, u.valid, u.invalid)):
                totals[i] += v
        if tuple(totals) != (yes_votes, no_votes, electors, cast, valid, invalid):
            raise ValueError(f"{name} {q.slug}: units add up to {totals}, the authority declares {q.declared}")
    p.frame(
        "result_value",
        pl.DataFrame(
            rows, schema=["result_set_id", "unit_id", "option_id", "measure", "value", "flags"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.col("flags").cast(pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
