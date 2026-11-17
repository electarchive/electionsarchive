import re
import sys

from ea import raw

SOURCE = "eg.nea"
BASE = "https://www.elections.eg/static"
YEARS = (2020, 2025)
STAGES = ("stage_1_round_1", "stage_1_round_2", "stage_2_round_1", "stage_2_round_2")
LISTS = ("99901", "99902", "99903", "99904")
OPTION = re.compile(r"<option value='(\d+)' class='\d+'>")


def archive_url(year: int, stage: str) -> str:
    return f"{BASE}/house_{year}/results/{stage}_results/"


def fragment_url(year: int, stage: str, code: str) -> str:
    return f"{BASE}/house_{year}/results/{stage}_results/html/{code}.html"


def codes(year: int) -> dict[str, list[str]]:
    """Individual constituency codes per stage, read from the option values of whichever archive pages answered."""
    found: dict[str, set[str]] = {"stage_1": set(), "stage_2": set()}
    for stage in STAGES:
        try:
            page = raw.cached(SOURCE, archive_url(year, stage)).path.read_text("utf-8")
        except LookupError:
            continue
        found[stage[:7]] |= set(OPTION.findall(page))
    if not found["stage_2"]:
        # The 2020 stage 2 archive pages refuse scripted requests, so reuse the 2025 codes and probe a few numbers
        # past each governorate's last one; a code with no fragment answers 404 and is left out.
        found["stage_2"] = set(codes_for_stage_two(2025))
        highest: dict[str, int] = {}
        for code in found["stage_2"]:
            highest[code[:-2]] = max(highest.get(code[:-2], 0), int(code[-2:]))
        found["stage_2"] |= {f"{gov}{n:02d}" for gov, top in highest.items() for n in range(1, top + 4)}
    return {stage: sorted(values, key=int) for stage, values in found.items()}


def codes_for_stage_two(year: int) -> list[str]:
    page = raw.cached(SOURCE, archive_url(year, "stage_2_round_1")).path.read_text("utf-8")
    return sorted(set(OPTION.findall(page)), key=int)


def fetch() -> None:
    for year in YEARS:
        for stage in STAGES:
            try:
                raw.fetch(SOURCE, archive_url(year, stage), suffix=".html")
            except Exception as failure:
                print(f"{archive_url(year, stage)}: {failure}", file=sys.stderr, flush=True)
    urls: list[str] = []
    for year in YEARS:
        known = codes(year)
        for stage in STAGES:
            urls += [fragment_url(year, stage, code) for code in (*known[stage[:7]], *LISTS)]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=4)


import functools
import html
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package
from ea.package import Package

EGYPT = str(uuid5(package.NAMESPACE, "polity\x1fEG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fEGY"))
GEOBOUNDARIES = "eg.geoboundaries"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EGY/ADM1/"
    "geoBoundaries-EGY-ADM1_simplified.geojson"
)
FIRST_DAY = {2020: date(2020, 10, 24), 2025: date(2025, 11, 10)}
# The governorate codes the authority uses, with the ISO 3166-2 code that geoBoundaries gives the same governorate.
GOVERNORATES = {
    "19": "EG-C", "27": "EG-ALX", "35": "EG-PTS", "43": "EG-SUZ", "51": "EG-DT", "61": "EG-DK", "78": "EG-SHR",
    "86": "EG-KB", "94": "EG-KFS", "108": "EG-GH", "116": "EG-MNF", "124": "EG-BH", "132": "EG-IS", "141": "EG-GZ",
    "159": "EG-BNS", "167": "EG-FYM", "175": "EG-MN", "183": "EG-AST", "191": "EG-SHG", "205": "EG-KN",
    "213": "EG-ASN", "221": "EG-BA", "231": "EG-WAD", "248": "EG-MT", "256": "EG-SIN", "264": "EG-JS",
    "311": "EG-LX",
}  # fmt: skip
SECTORS = {
    "99901": "Cairo, South and Middle Delta Sector",
    "99902": "North, Middle and South Upper Egypt Sector",
    "99903": "East Delta Sector",
    "99904": "West Delta Sector",
}
INDEPENDENT = "مستقل"
METHOD = (
    "Published by the National Elections Authority as a results page for each constituency. The pages name the "
    "winners and the candidates sent to a runoff, and a list district's winning list with its candidates. They do not "
    "name candidates who lost in the first round, and they carry no turnout, valid, or invalid counts. Votes appear "
    "only where the page prints them: the votes of the individual-seat winners and the votes for and against the "
    "winning list in the 2025 first round. Runoff candidates and runoff winners carry no votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def cells(row: str) -> list[str]:
    return [clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Page:
    title: str = ""
    winners: list[list[str]] = field(default_factory=list)
    runoff: list[list[str]] = field(default_factory=list)
    remaining: int | None = None
    list_name: str = ""
    list_votes: tuple[int, int] | None = None
    members: list[list[str]] = field(default_factory=list)


@functools.cache
def page(year: int, stage: str, code: str) -> Page | None:
    try:
        text = raw.cached(SOURCE, fragment_url(year, stage, code)).path.read_text("utf-8")
    except LookupError:
        return None
    heading = re.search(r"<h2[^>]*>(.*?)</h2>", text, re.DOTALL)
    if heading is None:
        raise ValueError(f"{year} {stage} {code}: no heading")
    out = Page(title=clean(heading.group(1)))
    for section in re.split(r'<h3 class="winner_header red">', text)[1:]:
        head, _, body = section.partition("</h3>")
        head = clean(head)
        if head == "القائمة الفائزة":
            main, _, sub = body.partition("class='sub-list'")
            row = next(r for r in re.findall(r"<tr>(.*?)</tr>", main, re.DOTALL) if "candidate_list_link" in r)
            found = cells(row)
            out.list_name = re.sub(r"\s*\(\s*عرض المرشحين\s*\)\s*$", "", found[1])
            if len(found) >= 4:
                out.list_votes = (number(found[2]), number(found[3]))
            out.members = [c for c in (cells(r) for r in re.findall(r"<tr>(.*?)</tr>", sub, re.DOTALL)) if len(c) == 4]
            continue
        match = re.search(r"result_number'>(\d+)<", body)
        if match:
            out.remaining = int(match.group(1))
        rows = [c for c in (cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)) if c]
        if head == "المرشحين الفائزين":
            out.winners = rows
        elif head.startswith("المرشحين فى مرحلة"):
            out.runoff = rows
        else:
            raise ValueError(f"{year} {stage} {code}: unknown section {head!r}")
    return out


def stage_codes(year: int) -> list[tuple[str, str]]:
    known = codes(year)
    return [
        (stage, code)
        for stage in ("stage_1", "stage_2")
        for code in known[stage]
        if page(year, f"{stage}_round_2", code)
    ]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/hor-{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        house(year).write()


def emit() -> None:
    package.run("load", packages())


def governorate_of(code: str) -> str:
    return code[:-2]


def constituency_area(year: int, code: str) -> str:
    return key("area", "eg.constituency", year, code)


def governorate_area(code: str) -> str:
    return key("area", "eg.governorate", code)


def sector_area(year: int, code: str) -> str:
    return key("area", "eg.sector", year, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Egypt's 27 governorates from geoBoundaries, with the House of Representatives constituencies and list "
        "districts of 2020 and 2025 placed within them",
    )
    p.external |= {EGYPT, COUNTRY}
    artifact = raw.cached(GEOBOUNDARIES, ADM1)
    source = key("source", "geoboundaries")
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Egypt ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(artifact, source)
    for code, rank, kind, noun in (
        ("eg.governorate", 1, "administrative", "governorate"),
        ("eg.constituency", 2, "electoral_district", "constituency"),
        ("eg.sector", 2, "electoral_district", "list district"),
    ):
        p.add("scheme", code=code, polity_id=EGYPT, kind=kind, rank=rank, noun=noun)
    p.add(
        "boundary_set",
        id=key("boundary_set", "eg.governorate", "2017"),
        scheme_code="eg.governorate",
        vintage="2017",
        as_of=artifact.retrieved_at.date(),
    )
    shapes = {
        str(f["properties"]["shapeISO"]): shapely.geometry.shape(f["geometry"])
        for f in json.loads(artifact.path.read_text("utf-8"))["features"]
    }
    arabic = _governorate_names()
    english = {
        str(f["properties"]["shapeISO"]): re.sub(r" Gover?nate$|rnorate$", "", str(f["properties"]["shapeName"]))
        for f in json.loads(artifact.path.read_text("utf-8"))["features"]
    }
    for gov, iso in GOVERNORATES.items():
        id = governorate_area(gov)
        p.add("area", id=id, scheme_code="eg.governorate")
        p.add("area_code", area_id=id, code=gov)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, arabic[gov], "ar", "official")
        p.name(id, re.sub(r" Governorate$| Governate$", "", english[iso]), "en", "english", False)
        shape = shapely.make_valid(shapes[iso])
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=source,
        )
    for year in YEARS:
        for stage, code in stage_codes(year):
            found = page(year, f"{stage}_round_2", code)
            assert found is not None
            id = constituency_area(year, code)
            p.add("area", id=id, scheme_code="eg.constituency")
            p.add("area_code", area_id=id, code=f"{year}:{code}")
            p.add("area_parent", area_id=id, parent_id=governorate_area(governorate_of(code)))
            p.name(id, found.title, "ar", "official")
        for code, title in SECTORS.items():
            sector = page(year, _sector_stage(year, code), code)
            assert sector is not None
            id = sector_area(year, code)
            p.add("area", id=id, scheme_code="eg.sector")
            p.add("area_code", area_id=id, code=f"{year}:{code}")
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, sector.title, "ar", "official")
            p.name(id, title, "en", "english", False)
    for scheme in ("eg.constituency", "eg.sector"):
        for year in YEARS:
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, year),
                scheme_code=scheme,
                vintage=str(year),
                as_of=FIRST_DAY[year],
            )
    return p


def _governorate_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for stage in STAGES:
            try:
                text = raw.cached(SOURCE, archive_url(year, stage)).path.read_text("utf-8")
            except LookupError:
                continue
            for code, name in re.findall(r"<option value='(\d+)'>([^<]+)</option>", text):
                if code in GOVERNORATES:
                    found.setdefault(code, clean(name))
    missing = set(GOVERNORATES) - set(found)
    if missing:
        raise LookupError(f"no Arabic name for governorates {sorted(missing)}")
    return found


def _sector_stage(year: int, code: str) -> str:
    for stage in ("stage_1", "stage_2"):
        if page(year, f"{stage}_round_1", code) is not None:
            return f"{stage}_round_1"
    raise LookupError(f"{year}: no page for list district {code}")


SOURCE_ID = key("source", SOURCE)
BODY = key("body", "house")
SYSTEM_INDIVIDUAL = key("electoral_system", "individual")
SYSTEM_LIST = key("electoral_system", "list")


def party_name(text: str) -> str:
    text = clean(text)
    return text.removeprefix("حزب ").strip()


def party_id(text: str) -> str | None:
    name = party_name(text)
    return None if name in ("", "-", INDEPENDENT) else key("party", name)


def person_key(text: str) -> str:
    return clean(text)


def party_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in YEARS:
        for stage, code in stage_codes(year):
            final = page(year, f"{stage}_round_2", code)
            for row in final.winners if final else []:
                found.setdefault(party_name(row[2]), clean(row[2]))
        for code in SECTORS:
            for stage in ("stage_1", "stage_2"):
                found_page = page(year, f"{stage}_round_1", code)
                for member in found_page.members if found_page else []:
                    found.setdefault(party_name(member[2]), clean(member[2]))
    found.pop(INDEPENDENT, None)
    found.pop("", None)
    found.pop("-", None)
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry", SOURCE, "Egypt's House of Representatives, its parties, and its electoral systems"
    )
    p.external.add(EGYPT)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="الهيئة الوطنية للانتخابات (National Elections Authority)",
        title="House of Representatives results, elections.eg",
        url="https://www.elections.eg/",
        license="Published by the authority with no license stated",
        license_url="https://www.elections.eg/",
        attribution="National Elections Authority, Egypt",
    )
    for year in YEARS:
        for stage in STAGES:
            for url in (archive_url(year, stage),):
                try:
                    p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
                except LookupError:
                    pass
    p.add("body", id=BODY, polity_id=EGYPT, kind="lower_house")
    p.name(BODY, "House of Representatives", "en", "official")
    p.name(BODY, "مجلس النواب", "ar", "native", False)
    for id, rules, text in (
        (
            SYSTEM_INDIVIDUAL,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "Individual seats are filled in constituencies of one or more seats. A candidate who wins more than half "
                "of the valid votes is elected in the first round; the remaining seats go to a runoff among the leading "
                "candidates, in a number twice the seats left, and the candidates with the most votes win."
            ),
        ),
        (
            SYSTEM_LIST,
            {"formula": "winner_take_all"},
            (
                "List seats are filled in four large closed-list districts. The list that wins more than half of the "
                "valid votes takes every seat of its district."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=EGYPT,
            valid_from=date(2020, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for name, full in sorted(party_names().items()):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=EGYPT)
        p.add("external_id", entity_id=id, scheme="eg.nea.party", value=name)
        p.name(id, name, "ar", "official")
    return p


def house(year: int) -> Package:
    first = FIRST_DAY[year]
    p = Package(
        f"{SOURCE}/hor-{year}",
        SOURCE,
        f"The {year} Egyptian House of Representatives election by constituency, with its runoffs",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=EGYPT,
        kind="general",
        date_from=first,
        date_to=None,
        date_precision="month",
        status="held",
    )
    p.name(election, f"{year} Egyptian parliamentary election", "en", "official")
    p.name(election, f"انتخابات مجلس النواب {year}", "ar", "native", False)
    path = f"eg/{first}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    known = party_names()
    people: dict[str, str] = {}

    def person(scope: str, name: str, nickname: str = "") -> str:
        id = key("person", year, scope, person_key(name))
        if id not in people.values():
            p.add("person", id=id)
            p.name(id, clean(name), "ar", "official")
            if nickname and nickname != "-" and nickname != clean(name):
                p.name(id, nickname, "ar", "ballot", False)
            people[id] = id
        return id

    def result_set(contest: str, tag: str) -> str:
        id = key("result_set", year, tag)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD, lang="en")
        return id

    def unit(tag: str, title: str, area: str) -> str:
        id = key("unit", year, tag)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=EGYPT,
            scheme_code="eg.constituency",
            code=f"{year}:{tag}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(id, title, "ar", "official")
        return id

    counted: set[str] = set()

    def value(rs: str, unit_id: str, option: str, amount: int) -> None:
        counted.add(rs)
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit_id,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=amount,
        )

    tally: Counter[str] = Counter()
    for stage, code in stage_codes(year):
        one = page(year, f"{stage}_round_1", code)
        two = page(year, f"{stage}_round_2", code)
        assert two is not None
        if one is None:
            raise LookupError(f"{year} {stage} {code}: no first-round page")
        slug = f"{path}/{code}"
        area = constituency_area(year, code)
        final = {clean(row[0]): row for row in two.winners}
        round_one = {clean(row[1]): row for row in one.winners}
        runoff = {clean(row[1]): row for row in one.runoff}
        elected_later = set(final) - set(round_one)
        if not set(round_one) <= set(final) or elected_later - set(runoff):
            raise ValueError(f"{year} {code}: round 1 and final winners disagree")
        seats = len(final)
        if not final and not round_one and not runoff:
            print(f"{year} {code} {one.title}: no winners and no runoff published; no contest built", flush=True)
            continue
        if one.remaining is not None and len(round_one) + one.remaining != seats:
            raise ValueError(f"{year} {code}: {len(round_one)} winners and {one.remaining} seats left, not {seats}")
        previous: str | None = None
        for round_number in (1, 2):
            if round_number == 2 and not runoff:
                break
            contest = key("contest", year, code, round_number)
            suffix = "" if round_number == 1 else ", runoff"
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=BODY,
                district_area_id=area,
                magnitude=seats if round_number == 1 else len(elected_later),
                round_number=round_number,
                previous_round_id=previous,
                electoral_system_id=SYSTEM_INDIVIDUAL,
                default_result_kind="counted",
            )
            previous = contest
            p.name(contest, f"{year} Egyptian parliamentary election, {one.title}{suffix}", "en", "official")
            p.name(contest, f"{one.title}{' (الإعادة)' if round_number == 2 else ''}", "ar", "native", False)
            p.add("slug", path=slug + ("" if round_number == 1 else "-runoff"), entity_id=contest, is_current=True)
            rs = result_set(contest, f"{code}:{round_number}")
            unit_id = unit(f"{code}:{round_number}", one.title, area)
            entries = (
                [(name, "elected", row) for name, row in round_one.items()]
                + [(name, "advanced", row) for name, row in runoff.items()]
                if round_number == 1
                else [(name, "elected" if name in elected_later else "lost", row) for name, row in runoff.items()]
            )
            for position, (name, outcome, row) in enumerate(entries, 1):
                option = key("option", year, code, round_number, name)
                member = final.get(name)
                party = party_id(member[2]) if member else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=name,
                )
                candidacy = key("candidacy", year, code, round_number, name)
                independent = member is not None and party_name(member[2]) == INDEPENDENT
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person(code, name, member[1] if member else ""),
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party if party in {key("party", n) for n in known} else None,
                    is_independent=independent,
                )
                p.add("outcome", result_set_id=rs, option_id=option, kind=outcome)
                if round_number == 1 and name in round_one and len(row) == 3:
                    value(rs, unit_id, option, number(row[2]))
                    tally[f"votes{year}"] += number(row[2])
            tally["individual_seats"] += len(entries) if round_number == 1 else 0
    for code, title in SECTORS.items():
        stage = _sector_stage(year, code)
        one = page(year, stage, code)
        two = page(year, stage.replace("round_1", "round_2"), code)
        assert one is not None
        if two is not None and (
            two.list_name != one.list_name or two.members != one.members and len(two.members) != len(one.members)
        ):
            raise ValueError(f"{year} {code}: list winners differ between rounds")
        area = sector_area(year, code)
        contest = key("contest", year, code)
        seats = len(one.members)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Egyptian parliamentary election, {title}", "en", "official")
        p.name(contest, one.title, "ar", "native", False)
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        rs = result_set(contest, code)
        unit_id = unit(code, one.title, area)
        option = key("option", year, code, "list")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=1,
            ballot_label=one.list_name,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=seats)
        if one.list_votes is not None:
            against = key("option", year, code, "against")
            p.add(
                "contest_option",
                id=against,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_position=2,
                ballot_label="الأصوات التي لم تنتخب القائمة",
            )
            p.add("outcome", result_set_id=rs, option_id=against, kind="lost")
            value(rs, unit_id, option, one.list_votes[0])
            value(rs, unit_id, against, one.list_votes[1])
            tally["list_for"] += one.list_votes[0]
            tally["list_against"] += one.list_votes[1]
        for member in one.members:
            name = member[1]
            candidacy = key("candidacy", year, code, "list", member[0])
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person(f"list{code}", name),
                option_id=option,
                role="list_member",
                list_position=int(member[0]),
                ballot_name=name,
                member_of_party_id=party_id(member[2]),
                is_independent=party_name(member[2]) == INDEPENDENT,
            )
            p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
        tally["list_seats"] += seats
    for stage in STAGES:
        for code in (*(c for s, c in stage_codes(year) if s == stage[:7]), *SECTORS):
            if page(year, stage, code) is not None:
                p.uses(raw.cached(SOURCE, fragment_url(year, stage, code)))
    for row in p.rows["result_set"]:
        if row["id"] not in counted:
            row["completeness"] = None
    print(f"{p.key}: {dict(tally)}", flush=True)
    return p
