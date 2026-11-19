"""Bhutan: the Dzongkhag Thromde Tshogde elections of 15 July 2026 in Phuentshogling and Thimphu.

The Election Commission of Bhutan publishes the results as two PDF tables, one for the contested posts and one for
the posts filled without a contest, and a declaration of results that gives each thromde's electorate and turnout.
Each thromde elects a Thrompon by a vote of the whole thromde and one Thromde Tshogpa for each demkhong (ward)."""

import html
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.bt import ecb, pdfwords

SOURCE = "bt.ecb_thromde2026"
UPLOADS = f"{ecb.BASE}/wp-content/uploads/2026/08"
FILES = {
    "contested": f"{UPLOADS}/ContestedResult-4th-Thromde-Elections-2026-PLing-Thimphu-16072026.pdf",
    "uncontested": f"{UPLOADS}/UnContestedResults-4thThromde-Elections-2026-PLing-Thimphu-16072026.pdf",
    "declaration": f"{ecb.BASE}/wp-json/wp/v2/posts?slug=declaration-of-results-dzongkhag-thromde-tshogde-elections-2026"
    "&_fields=id,link,title,content",
}
# The Thimphu thromde's outline is OpenStreetMap relation 9330372 (admin_level 5, Wikidata Q9270). Nominatim serves
# the current geometry as GeoJSON. OpenStreetMap has no outline for Phuentshogling and no demkhong boundaries.
OSM_THIMPHU = "https://nominatim.openstreetmap.org/lookup?osm_ids=R9330372&format=geojson&polygon_geojson=1"
DAY = date(2026, 7, 15)
BHUTAN = ecb.BHUTAN
COUNTRY = ecb.COUNTRY
VOTES, REGISTERED, CAST = 1, 2, 3
SCHEME_THROMDE, SCHEME_DEMKHONG = "bt.thromde", "bt.thromde_demkhong"

# Each thromde, in the Commission's spelling, and the dzongkhag it lies in.
THROMDES = {"Phuentshogling": "Chhukha", "Thimphu": "Thimphu"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECB = key("source", "ecb")
OSM_SOURCE = key("source", "osm")
OFFICE = key("office", "thrompon")
SYSTEM = key("electoral_system", "fptp")


def thromde_area(name: str) -> str:
    return key("area", SCHEME_THROMDE, name)


def demkhong_area(thromde: str, name: str) -> str:
    return key("area", SCHEME_DEMKHONG, thromde, name)


def body_id(thromde: str) -> str:
    return key("body", thromde)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def fetch() -> None:
    raw.fetch(SOURCE, OSM_THIMPHU, suffix=".geojson")
    for name, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=".json" if name == "declaration" else ".pdf")


def path(name: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, FILES[name]).storage_key


def parse() -> None:
    geo().write()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


@dataclass(frozen=True)
class Candidate:
    thromde: str
    post: str  # "Thrompon" or "Tshogpa"
    demkhong: str  # the demkhong a Thromde Tshogpa stands in, or a Thrompon candidate's home demkhong
    name: str
    cid: str
    evm: int
    postal: int
    total: int
    elected: bool
    contested: bool
    evm_no: int = 0
    postal_no: int = 0
    total_no: int = 0
    female: bool = False


# Where each table's candidate-name column starts. The tables are laid out differently, and a demkhong's name in
# the column before it can end within a few points of the first name.
NAME_X = {"contested": 415.0, "uncontested": 366.0}


def read(name: str) -> list[Candidate]:
    contested = name == "contested"
    found: list[Candidate] = []
    for _, words in pdfwords.pages(path(name)):
        head = {w.text: w for w in words if w.y < 130}
        if "Tshogde" not in head:
            continue
        bottom = max(w.y for w in words if w.text in ("No.", "YES"))
        demkhong_x = max(w.x for w in words if w.text == "Thromde" and w.y < 130) - 12
        role_x = head["Elective"].x - 15
        rows = [w for w in words if w.y > bottom + 2]
        cids = [w for w in rows if re.fullmatch(r"\d{11}", w.text)]
        roles = [w for w in rows if w.text in ("Thrompon", "Tshogpa") and role_x <= w.x < demkhong_x]
        thromdes = [w for w in rows if w.text in THROMDES and w.x < role_x]
        marks = pdfwords.lines([w for w in rows if demkhong_x <= w.x < NAME_X[name]])
        for cid in cids:
            line = [w for w in rows if abs(w.y - cid.y) <= pdfwords.LINE_GAP]
            nums = [w.number for w in sorted(line, key=lambda w: w.x) if w.x > cid.x1 and w.number is not None]
            role = min(roles, key=lambda w: abs(w.y - cid.y)).text
            near = min(marks, key=lambda row: abs(row[0].y - cid.y))
            thromde = min(thromdes, key=lambda w: abs(w.y - cid.y)).text
            text = " ".join(w.text for w in sorted(line, key=lambda w: w.x) if NAME_X[name] <= w.x < cid.x)
            female = text.endswith("(F)")
            text = text.removesuffix("(F)").strip()
            if contested:
                evm, postal, total = nums
                extra = (0, 0, 0)
            else:
                evm, evm_no, postal, postal_no, total, total_no = nums
                extra = (evm_no, postal_no, total_no)
            if evm + postal != total or (not contested and extra[0] + extra[1] != extra[2]):
                raise ValueError(f"{text}: the split does not add up")
            demkhong = " ".join(w.text for w in sorted(near, key=lambda w: w.x))
            elected = any(w.text == "ELECTED" for w in line)
            found.append(
                Candidate(
                    thromde, role, demkhong, text, cid.text, evm, postal, total, elected, contested, *extra, female
                )
            )
    return found


def declaration() -> dict[str, dict[str, int]]:
    """Each thromde's registered voters and ballots cast, as the Commission's declaration of results prints them."""
    posts = json.loads(path("declaration").read_text())
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", posts[0]["content"]["rendered"])))
    found: dict[str, dict[str, int]] = {}
    for name, registered, cast, evm, postal in re.findall(
        r"\d\.\s*(Phuentshogling|Thimphu) Of the ([\d,]+) registered eligible voters \(M= [\d,]+ & F= [\d,]+\), "
        r"([\d,]+) \(EVM:\s*([\d,]+) & Postal Ballot:\s*([\d,]+)\)",
        text,
    ):
        number = [int(n.replace(",", "")) for n in (registered, cast, evm, postal)]
        found[name] = dict(zip(("registered", "cast", "evm", "postal"), number, strict=True))
        if number[2] + number[3] != number[1]:
            raise ValueError(f"{name}: the declaration's machine and postal ballots do not sum to {number[1]}")
    if set(found) != set(THROMDES):
        raise ValueError(f"declaration gives {sorted(found)}")
    return found


def everyone() -> list[Candidate]:
    candidates = [*read("contested"), *read("uncontested")]
    if len(candidates) != 18:
        raise ValueError(f"expected 18 candidates, found {len(candidates)}")
    return candidates


def demkhongs() -> dict[str, list[str]]:
    """The demkhongs of each thromde, from the Thromde Tshogpa posts the Commission reports."""
    found: dict[str, list[str]] = {name: [] for name in THROMDES}
    for c in everyone():
        if c.post == "Tshogpa" and c.demkhong not in found[c.thromde]:
            found[c.thromde].append(c.demkhong)
    return {thromde: sorted(names) for thromde, names in found.items()}


def pretty(name: str) -> str:
    return name.replace("_", "-")


def thimphu_outline() -> shapely.MultiPolygon:
    data = json.loads(path_of(OSM_THIMPHU).read_text())
    shape = shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))
    shape = shapely.make_valid(shape)
    if shape.geom_type == "Polygon":
        shape = shapely.MultiPolygon([shape])
    if shape.geom_type != "MultiPolygon" or not 20 < shapely.area(shape) * 111.32 * 110.57 * 0.888 < 35:
        raise ValueError("the Thimphu outline is not a polygon of about 26 square kilometers")
    return shape


def path_of(url: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, url).storage_key


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Phuentshogling and Thimphu thromdes and the demkhongs of each that elected a Thromde Tshogpa in 2026, as "
        "the Election Commission of Bhutan lists them",
        depends=[],
    )
    p.external |= {BHUTAN, COUNTRY}
    p.add(
        "source",
        id=ECB,
        kind="official_body",
        publisher="Election Commission of Bhutan",
        title="Dzongkhag Thromde Tshogde Elections, 2026",
        url="https://ecb.bt/4ththromdeelections2026/",
        license="No license stated; published by the Election Commission of Bhutan",
        attribution="Election Commission of Bhutan",
    )
    for code, rank, noun in ((SCHEME_THROMDE, 3, "thromde"), (SCHEME_DEMKHONG, 4, "demkhong")):
        kind = "administrative" if rank == 3 else "electoral_district"
        p.add("scheme", code=code, polity_id=BHUTAN, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="academic",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap relation 9330372, Thimphu",
        url="https://www.openstreetmap.org/relation/9330372",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SOURCE, OSM_THIMPHU), OSM_SOURCE)
    for thromde, names in demkhongs().items():
        area = thromde_area(thromde)
        p.add("area", id=area, scheme_code=SCHEME_THROMDE)
        p.add("area_code", area_id=area, code=thromde.lower())
        p.add("area_parent", area_id=area, parent_id=COUNTRY)
        p.name(area, thromde, "en", "official")
        for name in names:
            seat = demkhong_area(thromde, name)
            p.add("area", id=seat, scheme_code=SCHEME_DEMKHONG)
            p.add("area_code", area_id=seat, code=f"{thromde.lower()}.{name.lower()}")
            p.add("area_parent", area_id=seat, parent_id=area)
            p.name(seat, pretty(name), "en", "official")
    outline = thimphu_outline()
    p.add(
        "area_geometry",
        area_id=thromde_area("Thimphu"),
        valid_from=None,
        valid_to=None,
        geom=shapely.to_wkb(shapely.set_srid(outline, 4326), hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=OSM_SOURCE,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME_THROMDE),
        scheme_code=SCHEME_THROMDE,
        vintage="osm",
        valid_from=None,
        valid_to=None,
        as_of=date(2026, 11, 19),
    )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Thromde Tshogdes of Phuentshogling and Thimphu, the Thrompon office, and the Election Commission's "
        "result files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BHUTAN}
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), ECB)
    for thromde in THROMDES:
        body = body_id(thromde)
        p.add("body", id=body, polity_id=BHUTAN, kind="council")
        p.name(body, f"{thromde} Thromde Tshogde", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BHUTAN,
        valid_from=date(2008, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "One Thrompon is elected by the whole thromde and one Thromde Tshogpa by each demkhong, each by the most "
            "votes. A candidate with no opponent stands on a yes or no vote."
        ),
        lang="en",
    )
    p.add("office", id=OFFICE, polity_id=BHUTAN, body_id=None, kind="mayor")
    p.name(OFFICE, "Thrompon", "en", "official")
    return p


METHOD = (
    "Counted by the Election Commission of Bhutan and printed as a table of votes for each candidate. Votes cast on "
    "the electronic voting machines are stored on the election-day channel, and postal ballots on the postal "
    "channel. {rule} The tables give no counts of invalid ballots; the declaration of results gives the ballots cast "
    "in each thromde, which can exceed the votes counted for the candidates."
)
CONTESTED = "The candidate with the most votes is elected."
UNCONTESTED = (
    "The only candidate stands on a yes or no vote and is elected with more yes votes than no votes; the no votes "
    "are stored as the No option."
)


@dataclass(frozen=True)
class Units:
    evm: str
    postal: str
    total: str | None


def units(p: Package, tag: str, area: str, equals: bool, name: str, *, roll: bool) -> Units:
    def add(suffix: str, kind: str, relation: str, text: str) -> str:
        code = f"{DAY}:{tag}" + (f":{suffix}" if suffix else "")
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BHUTAN,
            scheme_code=SCHEME_THROMDE if equals else SCHEME_DEMKHONG,
            code=code,
            area_relation=relation,
            area_id=area,
            kind=kind,
        )
        p.name(unit, text, "en", "official")
        return unit

    return Units(
        add("evm", "standard", "within", f"{name}, voting machines"),
        add("postal", "postal_district", "within", f"{name}, postal ballots"),
        add("", "aggregate", "equals" if equals else "within", name) if roll else None,
    )


def value(p: Package, rs: str, unit: str, option: str | None, measure: int, number: int) -> None:
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=number,
    )


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2026 Dzongkhag Thromde Tshogde elections in Phuentshogling and Thimphu, by thromde and demkhong, from the "
        "Election Commission of Bhutan",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BHUTAN, COUNTRY}
    eid = key("election", DAY)
    p.add("election", id=eid, polity_id=BHUTAN, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(eid, "2026 Dzongkhag Thromde Tshogde elections", "en", "official")
    slug = f"bt/{DAY}-thromde"
    p.add("slug", path=slug, entity_id=eid, is_current=True)
    totals = declaration()
    candidates = everyone()
    seats = demkhongs()
    for thromde in THROMDES:
        post(p, eid, slug, thromde, [c for c in candidates if c.thromde == thromde and c.post == "Thrompon"], totals)
        for name in seats[thromde]:
            race = [c for c in candidates if c.thromde == thromde and c.post == "Tshogpa" and c.demkhong == name]
            post(p, eid, slug, thromde, race, None, name)
    return p


def post(
    p: Package,
    eid: str,
    slug: str,
    thromde: str,
    rows: list[Candidate],
    totals: dict[str, dict[str, int]] | None,
    demkhong: str | None = None,
) -> None:
    thrompon = demkhong is None
    tag = f"{thromde}" if thrompon else f"{thromde}:{demkhong}"
    area = thromde_area(thromde) if thrompon else demkhong_area(thromde, str(demkhong))
    name = f"{thromde} Thrompon" if thrompon else f"{pretty(str(demkhong))} demkhong, {thromde}"
    unit = units(p, tag, area, thrompon, name, roll=totals is not None)
    cid = key("contest", tag)
    p.add(
        "contest",
        id=cid,
        election_id=eid,
        round_number=1,
        default_result_kind="counted",
        kind="office" if thrompon else "body_seats",
        office_id=OFFICE if thrompon else None,
        body_id=body_id(thromde),
        district_area_id=area,
        magnitude=1,
        electoral_system_id=SYSTEM,
    )
    p.name(
        cid,
        f"{thromde} Thrompon" if thrompon else f"{pretty(str(demkhong))} demkhong, {thromde} Thromde Tshogde",
        "en",
        "official",
    )
    p.add("slug", path=f"{slug}/{tag.lower().replace(':', '/').replace('_', '-')}", entity_id=cid, is_current=True)
    rs = key("result_set", tag)
    p.add(
        "result_set",
        id=rs,
        election_id=eid,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=ECB,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    uncontested = not rows[0].contested
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=METHOD.format(rule=UNCONTESTED if uncontested else CONTESTED)
        + (" The table lists each Thrompon candidate with the demkhong the candidate belongs to." if thrompon else ""),
        lang="en",
    )
    for position, row in enumerate(rows, 1):
        opt = key("option", tag, position)
        person = key("person", tag, position)
        p.add("person", id=person, gender="female" if row.female else None)
        p.name(person, row.name, "en", "official")
        p.add(
            "contest_option",
            id=opt,
            contest_id=cid,
            kind="candidate",
            party_id=None,
            ballot_position=position,
            ballot_label=row.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=opt,
            role="head",
            ballot_name=row.name,
            member_of_party_id=None,
            is_independent=True,
        )
        if row.elected:
            p.add("outcome", result_set_id=rs, option_id=opt, kind="elected")
        value(p, rs, unit.evm, opt, VOTES, row.evm)
        value(p, rs, unit.postal, opt, VOTES, row.postal)
        if uncontested:
            no = key("option", tag, position, "no")
            p.add(
                "contest_option",
                id=no,
                contest_id=cid,
                kind="none_of_the_above",
                ballot_position=position + 1,
                ballot_label="No",
            )
            value(p, rs, unit.evm, no, VOTES, row.evm_no)
            value(p, rs, unit.postal, no, VOTES, row.postal_no)
    if len([r for r in rows if r.elected]) != 1:
        raise ValueError(f"{name}: expected one elected candidate")
    if totals is not None and unit.total is not None:
        given = totals[thromde]
        counted = sum(r.evm for r in rows), sum(r.postal for r in rows)
        # The Commission's declaration prints the machine ballots cast; every one is counted for a candidate.
        if counted[0] != given["evm"] or counted[1] > given["postal"]:
            raise ValueError(f"{thromde}: {counted} counted against the declaration's {given}")
        value(p, rs, unit.total, None, REGISTERED, given["registered"])
        value(p, rs, unit.evm, None, CAST, given["evm"])
        value(p, rs, unit.postal, None, CAST, given["postal"])
