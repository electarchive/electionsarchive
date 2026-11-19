"""Victorian council general elections of 2008 to 2024, from the Victorian Electoral Commission (VEC).

The commission runs every council election in Victoria. Each council elects its councillors either council-wide or by
ward, by preferential vote where a ward has one vacancy and by proportional representation (the single transferable
vote) where it has more. Melbourne's voters also elect the Lord Mayor and Deputy Lord Mayor as a team (the leadership
team), and Greater Geelong's elected a mayor directly in 2012.

- The commission's results page for each election lists its councils. The 2008, 2012, 2016, and 2017 pages are the
  commission's archived HTML pages, one per council; the 2020 and 2024 pages are one page per council on the current
  site, and the 2021 South Gippsland page holds its contests itself.
- Each council's page gives, for every contest, the enrolment, the formal and informal votes, each candidate's first
  preference votes (with each group's above-the-line votes in Melbourne's councillor contests), and the candidates
  elected. The elections were postal, so the commission publishes no figures below the ward.
- Ward and council boundaries come from Vicmap Admin's ward layers for each election year, published on the
  Victorian Government's open data platform under CC BY 4.0.
- The pages do not state the election day, so the dates come from the Wikipedia articles on each election.

The packages share the `au.vec` pipeline name and raw cache with the state elections in `ea.sources.au.vec`.
"""

import functools
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, NamedTuple

import numpy
import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package

from .vec import AUSTRALIA, COUNTRY, ROOT, SOURCE, area, key, person_name

INDEX = f"{ROOT}/results/council-election-results"
BLOB = "https://itsitecoreblobvecprd01.blob.core.windows.net/public-files/historical-results"
WFS = (
    "https://opendata.maps.vic.gov.au/geoserver/wfs?service=WFS&version=2.0.0&request=GetFeature"
    "&typeNames=open-data-platform:{layer}&outputFormat=application/json&srsName=EPSG:4326"
)


def wiki_url(title: str) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={title}&action=raw"


@dataclass(frozen=True)
class Year:
    year: int
    day: date  # election day, or the day voting closed
    first: date | None  # the day voting opened, where the election ran over several days
    page: str  # the commission's results page for the election
    article: str  # the Wikipedia article that gives the date
    layer: str  # the Vicmap ward layer of the election's wards
    archived: str | None = None  # the commission's archived list of councils, for 2008 to 2016
    single: str | None = None  # the one council of a council-specific election


YEARS = {
    2008: Year(
        2008,
        date(2008, 11, 29),
        None,
        f"{INDEX}/2008-election-results",
        "2008_Victorian_local_elections",
        "ward_2008",
        f"{BLOB}/council2008/councilgeneralelectionresults2008.html",
    ),
    2012: Year(
        2012,
        date(2012, 10, 27),
        None,
        f"{INDEX}/2012-election-results",
        "2012_Victorian_local_elections",
        "ward_2012",
        f"{BLOB}/council2012/councilgeneralelectionresults2012.html",
    ),
    2016: Year(
        2016,
        date(2016, 10, 22),
        None,
        f"{INDEX}/2016-election-results",
        "2016_Victorian_local_elections",
        "ward_2016",
        f"{BLOB}/council2016/councilgeneralelectionresults2016.html",
    ),
    2017: Year(
        2017,
        date(2017, 10, 28),
        None,
        f"{INDEX}/2017-election-results",
        "2017_Greater_Geelong_City_Council_election",
        "ward_2017",
        single=f"{BLOB}/council2017/geelongresult.html",
    ),
    2020: Year(
        2020,
        date(2020, 10, 24),
        None,
        f"{INDEX}/2020-council-election-results",
        "2020_Victorian_local_elections",
        "ward_2020",
    ),
    2021: Year(
        2021,
        date(2021, 10, 29),
        date(2021, 10, 5),
        f"{INDEX}/2021-election-results",
        "2021_South_Gippsland_Shire_Council_election",
        "ward_2024",
        single=f"{INDEX}/2021-election-results",
    ),
    2024: Year(
        2024,
        date(2024, 10, 26),
        None,
        f"{INDEX}/2024-council-election-results",
        "2024_Victorian_local_elections",
        "ward_2024",
    ),
}


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")


def council_urls(year: int) -> list[str]:
    """The pages of the councils of one election, in the order the commission lists them."""
    config = YEARS[year]
    if config.single is not None:
        return [config.single]
    if config.archived is not None:
        folder = config.archived.rsplit("/", 1)[0]
        links = re.findall(r'href="([a-z]+result(?:\d{4})?\.html)"', read(config.archived))
        return [f"{folder}/{link}" for link in dict.fromkeys(links)]
    prefix = config.page.removeprefix(ROOT) + "/"
    links = re.findall(rf'href="({re.escape(prefix)}[a-z0-9-]+)"', read(config.page))
    return [f"{ROOT}{link}" for link in dict.fromkeys(links)]


def layers() -> list[str]:
    return sorted({config.layer for config in YEARS.values()})


def fetch() -> None:
    for config in YEARS.values():
        raw.fetch(SOURCE, config.page, suffix=".html")
        raw.fetch(SOURCE, wiki_url(config.article), suffix=".wiki")
        if config.archived is not None:
            raw.fetch(SOURCE, config.archived, suffix=".html")
        for url in council_urls(config.year):
            raw.fetch(SOURCE, url, suffix=".html")
    for layer in layers():
        raw.fetch(SOURCE, WFS.format(layer=layer), suffix=".geojson")


# Reading the pages -------------------------------------------------------------------------------------------------


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).replace("\xa0", " ").strip()


EVENT = re.compile(
    r'(?s)<h[1-6][^>]*>(?P<h>.*?)</h[1-6]>|<div class="GreenModuleHeaderFW">(?P<g>.*?)</div>|<tr[^>]*>(?P<r>.*?)</tr>'
    r"|<p[^>]*>(?P<p>.*?)</p>"
)


def events(page: str) -> list[tuple[str, list[str]]]:
    """The page's headings (`H`), table rows (`R`, with their cells' text), and paragraphs (`P`), in order."""
    page = re.sub(r"(?s)<script.*?</script>|<!--.*?-->", "", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    out: list[tuple[str, list[str]]] = []
    for m in EVENT.finditer(page):
        if m["r"] is not None:
            cells = [
                "\n".join(text_of(line) for line in c.split("\n") if text_of(line))
                for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", m["r"])
            ]
            if any(cells):
                out.append(("R", cells))
        elif m["p"] is not None:
            if text_of(m["p"]):
                out.append(("P", [text_of(m["p"])]))
        else:
            out.append(("H", [text_of(m["h"] if m["h"] is not None else m["g"])]))
    return out


@dataclass
class Entry:
    kind: str  # "candidate", "group" (a group's above-the-line votes), or "ticket" (a leadership team)
    name: str  # the candidate's name as printed, the group's name, or the team's name
    votes: int | None
    group: str | None = None  # the group a candidate stands in
    members: list[str] = field(default_factory=list)  # a team's Lord Mayor and Deputy Lord Mayor candidates


@dataclass
class Contest:
    year: int
    url: str
    council: str  # the council's name as the page prints it
    heading: str  # the contest's heading, without its vacancies
    vacancies: int
    elected: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    enrolment: int | None = None
    formal: int | None = None
    informal: int | None = None
    turnout: int | None = None
    group_totals: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)  # the page's notes on this contest

    @property
    def role(self) -> str:
        if self.heading.startswith("Leadership Team"):
            return "leadership"
        if re.match(r"(?i)(lord )?mayor\b", self.heading):
            return "mayor"
        if re.search(r"(?i)\bward$", self.heading):
            return "ward"
        return "council"

    @property
    def counted(self) -> bool:
        return self.formal is not None


HEADING = re.compile(r"^(?P<name>.+?) \((?:election of (?P<team>.+)|(?P<n>\d+) vacanc(?:y|ies))\)$")
NUMBER = re.compile(r"^(\d[\d,]*)")


def number(text: str) -> int:
    m = NUMBER.match(text.strip())
    if m is None:
        raise ValueError(f"no number in {text!r}")
    return int(m[1].replace(",", ""))


def council_name(year: int, page: str) -> str:
    for kind, cells in events(page):
        if kind != "H":
            continue
        m = re.match(
            r"^(?:Results for (?P<a>.+?) Elections? \d{4}|\d{4} (?P<b>.+?) election results"
            r"|(?P<c>.+?) election results \d{4}|(?P<d>.+? Council) results)$",
            cells[0],
        )
        if m:
            return m["a"] or m["b"] or m["c"] or m["d"]
    single = YEARS[year].single
    if single is not None and single != YEARS[year].page:  # the 2017 page names only its wards
        return council_name(year, read(YEARS[year].page))
    raise ValueError("no council name on the page")


def contests_of(year: int, url: str) -> list[Contest]:
    page = read(url)
    council = council_name(year, page)
    found: list[Contest] = []
    current: Contest | None = None
    section = ""  # the heading of the table being read
    group: str | None = None
    preamble: list[str] = []  # the page's notes before its first contest, such as a recount a tribunal ordered
    for kind, cells in events(page):
        if kind == "H":
            m = HEADING.match(cells[0])
            if m:
                vacancies = int(m["n"]) if m["n"] else 1
                current = Contest(year, url, council, m["name"], vacancies)
                found.append(current)
                section, group = "", None
            elif current is not None:
                section, group = cells[0].lower(), None
            continue
        if current is None:
            if kind == "P":
                preamble.append(cells[0])
            continue
        if kind == "P":
            current.notes.append(cells[0])
            continue
        while len(cells) > 1 and not cells[0]:
            cells = cells[1:]  # the 2012 and 2016 group tables indent their rows with empty cells
        label = cells[0]
        head = label.rstrip(":").strip().lower()
        if head == "elected" and len(cells) > 1:
            current.elected.append(cells[1])
            continue
        if head in {"enrolment", "formal votes", "informal votes", "voter turnout", "votes counted", "quota"}:
            value = number(cells[1])
            if head == "enrolment":
                current.enrolment = value
            elif head == "formal votes":
                current.formal = value
            elif head == "informal votes":
                current.informal = value
            elif head in {"voter turnout", "votes counted"}:
                current.turnout = value
            continue
        if not re.search(r"first\s+preference votes", section):
            continue
        values = [c for c in cells[1:] if c]
        if (
            not label
            or head in {"votes", "candidate", "candidates"}
            or (values and not any(re.fullmatch(r"[\d,]+(?:\.\d+)?%?", c) for c in values))
        ):
            continue  # a header row, or a blank row between groups
        if len(cells) >= 3 and "\n" in label:  # a leadership team: its candidates, its name, its votes
            members = label.split("\n")
            current.entries.append(Entry("ticket", cells[1], number(cells[2]), members=members))
        elif head.startswith("above the line"):
            assert group is not None, url
            current.entries.append(Entry("group", group, number(cells[1])))
        elif head == "below the line votes":
            continue
        elif head == "group total":
            assert group is not None, url
            current.group_totals[group] = number(cells[1])
        elif not values:  # a group's name
            group = label
        else:
            current.entries.append(Entry("candidate", label, number(values[0]), group=group))
    for c in found:
        if any(c.heading in note for note in preamble):
            c.caveats = [note for note in preamble if "Type a minimum" not in note]
    return found


# Councils and wards ------------------------------------------------------------------------------------------------

LGA_SCHEME, WARD_SCHEME = "au-vic.lga", "au-vic.ward"
TOLERANCE = 0.0002  # degrees, about 20 m
LATEST = "ward_2024"
# Moonee Valley's 2008 wards, which the 2008 layer draws under the names they took in 2010 (Buckley, Myrnong, and
# Rose Hill), with nothing to say which old name each new one replaced.
UNDRAWN = {"2008:349:central-ward", "2008:349:south-ward", "2008:349:west-ward"}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("&", "and"))


def council_key(name: str) -> str:
    """The Vicmap name of a council: 'Borough of Queenscliffe Council' is QUEENSCLIFFE, 'Moira Shire Council' MOIRA."""
    name = re.sub(r"^Borough of ", "", name)
    name = re.sub(r"\s+(?:(?:Rural )?City|Shire|Borough)?\s*Council$", "", name)
    return norm(name)


def ward_key(name: str) -> str:
    return norm(re.sub(r"(?i)\s+ward$", "", name))


@functools.cache
def features(layer: str) -> list[dict[str, Any]]:
    data = json.loads(raw.cached(SOURCE, WFS.format(layer=layer)).path.read_bytes())
    found = [f for f in data["features"] if f["geometry"] is not None and str(f["properties"]["lga_code"]).strip()]
    assert found, layer
    return found


@functools.cache
def lga_codes(layer: str) -> dict[str, str]:
    """Each council's Vicmap code by its name in the layer."""
    return {norm(str(f["properties"]["lga_name"])): str(f["properties"]["lga_code"]) for f in features(layer)}


def lga_of(c: Contest) -> str:
    found = lga_codes(YEARS[c.year].layer).get(council_key(c.council))
    if found is None:  # the 2020 layer names Moreland by its later name, Merri-bek
        found = next((codes[k] for codes in map(lga_codes, layers()) if (k := council_key(c.council)) in codes), None)
    if found is None:
        raise ValueError(f"{c.year}: no Vicmap council is named {c.council}")
    return found


def polygonal(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [p for p in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(p) in (3, 6)]
    return shapely.MultiPolygon(list(shapely.get_parts(parts)))


@functools.cache
def shapes(layer: str) -> list[shapely.Geometry]:
    items = numpy.array([polygonal(shapely.geometry.shape(f["geometry"])) for f in features(layer)])
    out = []
    for shape in shapely.coverage_simplify(items, TOLERANCE):
        parts = [p for p in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(p) in (3, 6)]
        out.append(shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326))
    return out


class Drawn(NamedTuple):
    shape: shapely.Geometry
    since: date | None  # the day the ward structure took effect


@functools.cache
def ward_shapes(layer: str) -> dict[tuple[str, str], Drawn]:
    """The layer's wards by council code and ward name; an unsubdivided council's single polygon is not a ward."""
    out: dict[tuple[str, str], Drawn] = {}
    for f, shape in zip(features(layer), shapes(layer), strict=True):
        props = f["properties"]
        if str(props["ward_type"]).strip() != "WARD":
            continue
        since = str(props.get("effective_from") or "")[:10]
        for label in (str(props["ward_label"]), str(props["ward_name"])):
            out[(str(props["lga_code"]), ward_key(label))] = Drawn(shape, date.fromisoformat(since) if since else None)
    return out


@functools.cache
def council_shapes() -> dict[str, shapely.Geometry]:
    """Each council's outline: the union of its polygons in the latest ward layer."""
    pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for f, shape in zip(features(LATEST), shapes(LATEST), strict=True):
        pieces[str(f["properties"]["lga_code"])].append(shape)
    out = {}
    for code, parts in pieces.items():
        union = shapely.union_all(parts)
        polygons = [p for p in shapely.get_parts(union) if shapely.get_type_id(p) == 3]
        out[code] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return out


# Contests ----------------------------------------------------------------------------------------------------------

SUFFIX = re.compile(r"\s*\((?:\d+(?:st|nd|rd|th) elected|Unopposed|(?:Deputy )?Lord Mayor|Mayor)\)\s*$", re.IGNORECASE)


def clean(name: str) -> str:
    """A candidate's name without the role or the order of election printed after it."""
    name = " ".join(name.split())
    while SUFFIX.search(name):
        name = SUFFIX.sub("", name)
    return name.strip()


@functools.cache
def contests(year: int) -> list[Contest]:
    found = [c for url in council_urls(year) for c in contests_of(year, url)]
    for c in found:
        check(c)
    return [c for c in found if c.entries or c.elected]


def failed(year: int) -> list[Contest]:
    """The contests no candidate nominated for, which the pages list without a result."""
    return [c for url in council_urls(year) for c in contests_of(year, url) if not c.entries and not c.elected]


def check(c: Contest) -> None:
    """Fail on a contest whose figures disagree with the totals the page prints."""
    label = f"{c.year} {c.council}, {c.heading}"
    if not c.entries:
        assert c.formal is None, label
        return
    assert c.formal is not None and c.informal is not None, label
    total = sum(e.votes or 0 for e in c.entries)
    assert total == c.formal, f"{label}: first preferences sum to {total}, not the {c.formal} formal votes"
    if c.turnout is not None:
        assert c.turnout == c.formal + c.informal, f"{label}: turnout {c.turnout} is not formal plus informal"
    for group, printed in c.group_totals.items():
        votes = sum(e.votes or 0 for e in c.entries if e.name == group and e.kind == "group" or e.group == group)
        assert votes == printed, f"{label}: group {group} sums to {votes}, not {printed}"
    names = {norm(clean(m)) for e in c.entries for m in ([*e.members] if e.kind == "ticket" else [e.name])}
    for name in c.elected:
        assert norm(clean(name)) in names, f"{label}: {name} is elected but not a candidate"


# Packages ----------------------------------------------------------------------------------------------------------

VEC_COUNCIL = key("source", "council")
VICMAP = key("source", "vicmap", "ward")
WIKIPEDIA = key("source", "wikipedia", "council")
STV = key("electoral_system", "council", "stv")
IRV = key("electoral_system", "council", "irv")
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
SYSTEMS = [
    (
        STV,
        {"formula": "stv", "quota": "droop"},
        (
            "Where a ward or council-wide contest elects more than one councillor, the councillors are elected by "
            "proportional representation: the single transferable vote with a Droop quota. Voters number every "
            "candidate; in the City of Melbourne they may instead mark one group above the line and take its ticket."
        ),
    ),
    (
        IRV,
        {"formula": "irv"},
        (
            "Where a ward elects one councillor, and for a directly elected mayor or Melbourne's leadership team, the "
            "vote is preferential: the candidate with the fewest votes is excluded and their ballots passed on until one "
            "holds an absolute majority. Voters number every candidate."
        ),
    ),
]
LORD_MAYOR = key("office", "council", "343", "lord-mayor")
UNGROUPED = {"ungrouped"}


def body(lga: str) -> str:
    return key("body", "council", lga)


def mayor(lga: str) -> str:
    return LORD_MAYOR if lga == "343" else key("office", "council", lga, "mayor")


def council_area(lga: str) -> str:
    return area(LGA_SCHEME, lga)


def ward_code(c: Contest) -> str:
    return f"{c.year}:{lga_of(c)}:{changeset.slug(c.heading)}"


def ward_area(c: Contest) -> str:
    return area(WARD_SCHEME, ward_code(c))


def district(c: Contest) -> str:
    return ward_area(c) if c.role == "ward" else council_area(lga_of(c))


def all_contests() -> list[Contest]:
    return [c for year in YEARS for c in contests(year)]


def council_names() -> dict[str, list[str]]:
    """Each council's names, latest first."""
    found: dict[str, list[str]] = defaultdict(list)
    for year in sorted(YEARS, reverse=True):
        for c in contests(year):
            names = found[lga_of(c)]
            if c.council not in names:
                names.append(c.council)
    return found


def party_code(name: str) -> str:
    return norm(name)


def team_names() -> dict[str, str]:
    """The groups and leadership teams, by their code, under the name the latest page prints."""
    found: dict[str, str] = {}
    for year in sorted(YEARS, reverse=True):
        for c in contests(year):
            for e in c.entries:
                if e.kind in ("group", "ticket") and party_code(e.name) not in found:
                    found[party_code(e.name)] = e.name
    return found


def party(name: str) -> str:
    return key("party", "council", party_code(name))


def display(name: str) -> str:
    return re.sub(r"'S\b", "'s", name.title()) if name.isupper() else name


def outline(p: Package, area_id: str, shape: shapely.Geometry, since: date | None = None) -> None:
    p.add(
        "area_geometry",
        area_id=area_id,
        valid_from=since,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=VICMAP,
    )


def geo() -> Package:
    names = council_names()
    wards = {ward_code(c): c for c in all_contests() if c.role == "ward"}
    p = Package(
        f"{SOURCE}/lg-geo",
        SOURCE,
        f"Victoria's {len(names)} councils and the {len(wards)} wards their councillors were elected from at the "
        "general elections of 2008 to 2024, with boundaries from Vicmap Admin's ward layer of each election",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.add(
        "source",
        id=VICMAP,
        kind="official_body",
        publisher="Department of Transport and Planning, State of Victoria",
        title="Vicmap Admin local government ward boundaries, by election year",
        url="https://opendata.maps.vic.gov.au/geoserver/wfs",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of Victoria (Department of Transport and Planning)",
        notes="Each layer (ward_2008 to ward_2024) holds the wards of one general election, and an unsubdivided "
        "council as one polygon. A council's outline is the union of its polygons in the 2024 layer.",
    )
    for layer in layers():
        p.artifact(raw.cached(SOURCE, WFS.format(layer=layer)), VICMAP)
    p.add("scheme", code=LGA_SCHEME, polity_id=AUSTRALIA, kind="administrative", rank=2, noun="council area")
    p.add("scheme", code=WARD_SCHEME, polity_id=AUSTRALIA, kind="electoral_district", rank=3, noun="council ward")
    latest = features(LATEST)
    p.add(
        "boundary_set",
        id=key("boundary_set", LGA_SCHEME, 2024),
        scheme_code=LGA_SCHEME,
        vintage="2024",
        as_of=YEARS[2024].day,
    )
    for config in YEARS.values():
        if any(c.role == "ward" for c in contests(config.year)):
            p.add(
                "boundary_set",
                id=key("boundary_set", WARD_SCHEME, config.year),
                scheme_code=WARD_SCHEME,
                vintage=str(config.year),
                as_of=config.day,
            )
    outlines = council_shapes()
    assert latest
    for lga, labels in sorted(names.items()):
        id = council_area(lga)
        p.add("area", id=id, scheme_code=LGA_SCHEME)
        p.add("area_code", area_id=id, code=lga)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        for i, label in enumerate(labels):
            p.name(id, label, "en", "official", i == 0)
        outline(p, id, outlines[lga])
    missing = []
    for code, c in sorted(wards.items()):
        id = ward_area(c)
        p.add("area", id=id, scheme_code=WARD_SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=council_area(lga_of(c)))
        p.name(id, f"{c.heading}, {c.council}", "en", "official")
        drawn = ward_shapes(YEARS[c.year].layer).get((lga_of(c), ward_key(c.heading)))
        if drawn is None:
            missing.append(code)
            continue
        outline(p, id, drawn.shape, drawn.since)
    if set(missing) != UNDRAWN:
        raise ValueError(f"wards without a shape: {missing}")
    return p


def registry() -> Package:
    names = council_names()
    teams = team_names()
    p = Package(
        f"{SOURCE}/lg-registry",
        SOURCE,
        f"Victoria's {len(names)} councils, the Lord Mayor of Melbourne and the Mayor of Greater Geelong, the "
        f"electoral systems of their general elections, and the {len(teams)} groups and teams on the Victorian "
        "Electoral Commission's council results of 2008 to 2024, with the files those elections cite",
    )
    p.external |= {AUSTRALIA}
    p.add(
        "source",
        id=VEC_COUNCIL,
        kind="official_body",
        publisher="Victorian Electoral Commission",
        title="Victorian council election results",
        url=INDEX,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of Victoria (Victorian Electoral Commission)",
    )
    p.add(
        "source",
        id=WIKIPEDIA,
        kind="community",
        publisher="Wikipedia",
        title="Wikipedia articles on the Victorian council elections",
        url="https://en.wikipedia.org/",
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
        notes="Read from each article's wikitext, pinned to the revision current at retrieval. Used only for the "
        "election dates, which the commission's results pages do not state.",
    )
    for config in YEARS.values():
        for url in vec_files(config.year):
            p.artifact(raw.cached(SOURCE, url), VEC_COUNCIL)
        p.artifact(raw.cached(SOURCE, wiki_url(config.article)), WIKIPEDIA)
    mayors = {lga_of(c): c.council for c in all_contests() if c.role in ("mayor", "leadership")}
    for lga, labels in sorted(names.items()):
        p.add("body", id=body(lga), polity_id=AUSTRALIA, kind="council")
        for i, label in enumerate(labels):
            p.name(body(lga), label, "en", "official", i == 0)
    for lga, council in sorted(mayors.items()):
        title = "Lord Mayor of Melbourne" if lga == "343" else f"Mayor of {re.sub(r' (City )?Council$', '', council)}"
        p.add("office", id=mayor(lga), polity_id=AUSTRALIA, body_id=body(lga), kind="mayor")
        p.name(mayor(lga), title, "en", "official")
    for id, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=AUSTRALIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in sorted(teams.items()):
        id = key("party", "council", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, display(name), "en", "official")
    return p


def vec_files(year: int) -> list[str]:
    config = YEARS[year]
    extra = [config.archived] if config.archived else []
    return list(dict.fromkeys([config.page, *extra, *council_urls(year)]))


METHOD = (
    "From the Victorian Electoral Commission's results page for the council, which gives the enrolment, the formal and "
    "informal votes, each candidate's first preference votes, and the candidates elected. The election was by post, "
    "so the commission publishes no figures below the ward, and the ward (or the whole council) is the one reporting "
    "unit. Where the page gives a recount or recheck of the first preferences, those are the figures stored. The "
    "distribution of preferences is not stored."
)
GROUPS = (
    " A voter could mark one group above the line and take its ticket, or number the candidates below the line: "
    "each group's above-the-line votes are stored on the group's list option and each candidate's below-the-line "
    "first preferences on the candidate's option, so the two together add up to the formal votes."
)
TEAM = (
    " Each team of a Lord Mayor candidate and a Deputy Lord Mayor candidate is one ballot line, stored as a ticket "
    "with the Lord Mayor candidate at its head and the Deputy Lord Mayor candidate as running mate."
)
UNOPPOSED = (
    "There was no poll: no more candidates nominated than there were vacancies, and the candidates who nominated "
    "were declared elected unopposed."
)


def title(c: Contest) -> str:
    if c.role == "leadership":
        return f"{c.council}: Lord Mayor and Deputy Lord Mayor"
    if c.role == "mayor":
        return f"{c.council}: Mayor"
    if c.role == "ward":
        return f"{c.council}: {c.heading}"
    return f"{c.council}: Councillors"


def contest_slug(c: Contest) -> str:
    council = changeset.slug(council_names()[lga_of(c)][0])
    part = {"leadership": "lord-mayor", "mayor": "mayor", "council": "councillors"}.get(c.role)
    return f"{council}-{part or changeset.slug(c.heading)}"


def package_key(year: int) -> str:
    return f"{SOURCE}/lg-{YEARS[year].day}"


def election(year: int) -> Package:
    config = YEARS[year]
    found = contests(year)
    single = config.single is not None
    what = f"the {found[0].council} election" if single else "the Victorian council general elections"
    p = Package(
        package_key(year),
        SOURCE,
        f"{what[0].upper()}{what[1:]} of {config.day:%-d %B %Y}: {len(found)} contests for councillors"
        + (" and the Lord Mayor" if any(c.role == "leadership" for c in found) else "")
        + ", with first preferences by ward, from the Victorian Electoral Commission",
        depends=[f"{SOURCE}/lg-geo", f"{SOURCE}/lg-registry"],
    )
    p.external |= {AUSTRALIA}
    for url in vec_files(year):
        p.uses(raw.cached(SOURCE, url))
    p.uses(raw.cached(SOURCE, wiki_url(config.article)))
    election_id = key("election", "council", config.day)
    path = f"au/vic/{config.day}-local"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=config.first or config.day,
        date_to=config.day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    label = f"{year} {found[0].council} election" if single else f"{year} Victorian council elections"
    p.name(election_id, label, "en", "official")
    if config.first is not None:
        context = (
            f"A postal election. Voting opened on {config.first:%-d %B %Y} and was to close on 22 October 2021; "
            f"the commission extended it to {config.day:%-d %B %Y} after many ballot papers arrived late."
        )
    else:
        context = (
            f"Victoria's councils elect their councillors every four years, mostly by post; {config.day:%-d %B %Y} "
            "was election day, the Saturday after postal voting closed."
        )
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="context", body_md=context, lang="en")
    values: list[tuple[str, str, str | None, int, int]] = []
    units: set[str] = set()
    for c in found:
        add_contest(p, c, election_id, path, values, units)
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def add_contest(
    p: Package,
    c: Contest,
    election_id: str,
    path: str,
    values: list[tuple[str, str, str | None, int, int]],
    units: set[str],
) -> None:
    day, lga = YEARS[c.year].day, lga_of(c)
    tag = (day.isoformat(), lga, c.role, changeset.slug(c.heading))
    contest_id = key("contest", "council", *tag)
    common = {
        "id": contest_id,
        "election_id": election_id,
        "district_area_id": district(c),
        "magnitude": c.vacancies,
        "round_number": 1,
        "default_result_kind": "counted" if c.counted else "tacit",
    }
    if c.role in ("leadership", "mayor"):
        p.add("contest", kind="office", office_id=mayor(lga), electoral_system_id=IRV, **common)
    else:
        system = STV if c.vacancies > 1 else IRV
        p.add("contest", kind="body_seats", body_id=body(lga), electoral_system_id=system, **common)
    p.name(contest_id, title(c), "en", "official")
    p.add("slug", path=f"{path}/{contest_slug(c)}", entity_id=contest_id, is_current=True)
    result_set = key("result_set", *tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="official",
        kind="counted" if c.counted else "tacit",
        source_id=VEC_COUNCIL,
        status="active",
        is_default=True,
        completeness=1.0 if c.counted else None,
    )
    grouped = any(e.kind == "group" for e in c.entries)
    method = METHOD + (GROUPS if grouped else "") + (TEAM if c.role == "leadership" else "")
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=UNOPPOSED if not c.counted else method,
        lang="en",
    )
    elected = {norm(clean(name)) for name in c.elected}
    if len(elected) < c.vacancies:
        p.add(
            "note",
            id=key("note", result_set, "short"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"Only {len(elected)} of the {c.vacancies} vacancies were filled at this election.",
            lang="en",
        )
    for index, caveat in enumerate(c.caveats):
        p.add(
            "note",
            id=key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=caveat,
            lang="en",
        )
    entries = c.entries or [Entry("candidate", name, None) for name in c.elected]
    lists: dict[str, str] = {}
    unit = unit_for(p, units, c) if c.counted else None
    for position, e in enumerate(entries, 1):
        option = key("option", *tag, position)
        group = e.group if e.group is not None and norm(e.group) not in {norm(u) for u in UNGROUPED} else None
        if e.kind == "group":
            lists[e.name] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list",
                party_id=party(e.name),
                ballot_position=position,
                ballot_label=e.name,
            )
        elif e.kind == "ticket":
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="ticket",
                party_id=party(e.name),
                ballot_position=position,
                ballot_label=e.name,
            )
            for i, member in enumerate(e.members):
                person = key("person", *tag, position, i)
                p.add("person", id=person)
                p.name(person, person_name(clean(member)), "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", *tag, position, i),
                    person_id=person,
                    option_id=option,
                    role="head" if i == 0 else "running_mate",
                    ballot_name=clean(member),
                    member_of_party_id=party(e.name),
                    is_independent=False,
                )
            won = norm(clean(e.members[0])) in elected
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        else:
            name = clean(e.name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                parent_option_id=lists.get(group) if group else None,
                party_id=party(group) if group else None,
                ballot_position=position if c.counted else None,
                ballot_label=name,
            )
            person = key("person", *tag, position)
            p.add("person", id=person)
            p.name(person, person_name(name), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *tag, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party(group) if group else None,
                is_independent=group is None,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if norm(name) in elected else "lost",
            )
        if unit is not None and e.votes is not None:
            values.append((result_set, unit, option, VOTES, e.votes))
    if unit is None:
        return
    assert c.formal is not None and c.informal is not None
    values.append((result_set, unit, None, VALID, c.formal))
    values.append((result_set, unit, None, INVALID, c.informal))
    values.append((result_set, unit, None, CAST, c.formal + c.informal))
    if c.enrolment is not None:
        values.append((result_set, unit, None, REGISTERED, c.enrolment))


def unit_for(p: Package, units: set[str], c: Contest) -> str:
    """The reporting unit of a contest's electorate: one ward, or the whole council."""
    lga = lga_of(c)
    if c.role == "ward":
        scheme, code, name = WARD_SCHEME, ward_code(c), f"{c.heading}, {c.council}"
    else:
        scheme, code, name = LGA_SCHEME, f"{c.year}:{lga}", c.council
    id = key("unit", scheme, code)
    if id not in units:
        units.add(id)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=AUSTRALIA,
            scheme_code=scheme,
            code=code,
            area_relation="equals",
            area_id=district(c),
            kind="standard",
        )
        p.name(id, name, "en", "official")
    return id


# Pipeline steps ----------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/lg-geo", f"{SOURCE}/lg-registry", *(package_key(year) for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
