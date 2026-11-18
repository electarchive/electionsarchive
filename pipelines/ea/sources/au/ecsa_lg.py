"""South Australian council periodic elections of 2018 and 2022, from the Electoral Commission SA (ECSA).

ECSA runs every council election in South Australia by postal ballot. Each council elects its councillors, either
council-wide (area councillors) or by ward, and most councils also elect a mayor (the Lord Mayor in Adelaide). Seats
are filled by the single transferable vote.

- 2018: the commission's 2018 results page lists each council's contests, names the candidates elected unopposed, and
  links one summary PDF per council with the informal, formal, and first-preference votes of every contested
  election and the candidates elected.
- 2022: the commission's results site reads JSON from a public API: one static file of councils, contests, and
  candidates, and one file of counts with each candidate's first preferences, the informal ballot papers, and the
  candidates elected.
- Both years: the commission's participation workbook gives the electors enrolled for each contested election.
- Council boundaries come from Location SA's local government areas, and ward boundaries from its ward layers of 2018
  and November 2022, all published on data.sa.gov.au under CC BY.

The packages share the `au.ecsa` pipeline name and raw cache with the state elections in `ea.sources.au.ecsa`.
"""

import functools
import html
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import numpy
import polars as pl
import pymupdf
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.ecsa"
HOST = "https://www.ecsa.sa.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
ECSA = key("source", "ecsa", "council")
WIKIPEDIA = key("source", "wikipedia", "council")
LOCATION_SA = key("source", "location-sa")

API = "https://apim-ecsa-production.azure-api.net/results-display"
STATIC_2022 = f"{API}/LGEStatic/2022-08-23/0"
COUNTS_2022 = f"{API}/LGEChange/2022-08-23/0"
PAGE_2018 = (
    f"{HOST}/elections/council-supplementary-list?view=article&id=110:2018-council-elections-results&catid=12:elections"
)
PAGE_2022 = f"{HOST}/?view=article&id=1011:2022-council-elections-results-and-statistics&catid=9"
PARTICIPATION = f"{HOST}/component/edocman/participation-2022-and-2018-council-elections/download?Itemid=0"
LGA = "https://www.dptiapps.com.au/dataportal/LGA_geojson.zip"
WARDS = "https://www.dptiapps.com.au/dataportal/Wards_geojson.zip"


def wiki_url(title: str) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={title}&action=raw"


@dataclass(frozen=True)
class Year:
    year: int
    first: date  # the first day ballot packs went out
    day: date  # the close of voting
    article: str  # the Wikipedia article that gives both dates


YEARS = {
    2018: Year(2018, date(2018, 10, 22), date(2018, 11, 9), "2018_South_Australian_local_elections"),
    2022: Year(2022, date(2022, 10, 14), date(2022, 11, 10), "2022_South_Australian_local_elections"),
}


def text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")


def api_json(url: str) -> dict[str, Any]:
    out = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    assert isinstance(out, dict)
    return out


def summary_links() -> dict[str, str]:
    """Each council's summary PDF on the 2018 results page, by the council's printed name."""
    out: dict[str, str] = {}
    for name, body in re.findall(r"<summary>(.*?)</summary>(.*?)</details>", text(PAGE_2018), re.DOTALL):
        found = re.search(r'results here for\s*(?:&nbsp;)?\s*<a href="([^"]+)"', body)
        if found:
            out[html.unescape(name).strip()] = HOST + html.unescape(found.group(1))
    return out


def fetch() -> None:
    raw.fetch(SOURCE, STATIC_2022, suffix=".json")
    raw.fetch(SOURCE, COUNTS_2022, suffix=".json")
    raw.fetch(SOURCE, PAGE_2018, suffix=".html")
    raw.fetch(SOURCE, PAGE_2022, suffix=".html")
    raw.fetch(SOURCE, PARTICIPATION, suffix=".xlsx")
    raw.fetch(SOURCE, LGA, suffix=".zip")
    raw.fetch(SOURCE, WARDS, suffix=".zip")
    for year in YEARS.values():
        raw.fetch(SOURCE, wiki_url(year.article), suffix=".wiki")
    for url in summary_links().values():
        raw.fetch(SOURCE, url, suffix=".pdf")


# Councils, contests, and candidates -------------------------------------------------------------------------------


def norm(text: str) -> str:
    """Letters and digits of a name, uppercased, with '&' read as 'and', to match one spelling to another."""
    folded = unicodedata.normalize("NFKD", text.upper().replace("&", " AND "))
    return re.sub(r"[^A-Z0-9]", "", folded)


@dataclass(frozen=True)
class Council:
    code: str  # the commission's three-letter code, such as ADE
    name: str  # the short name, such as Adelaide
    full: str  # the corporate name, such as Corporation of the City of Adelaide


@functools.cache
def councils() -> dict[str, Council]:
    out: dict[str, Council] = {}
    for c in api_json(STATIC_2022)["councils"]:
        short = SHORT_NAMES.get(c["councilCode"], c["councilName"].strip())
        out[c["councilCode"]] = Council(c["councilCode"], short, c["councilFullName"].strip())
    return out


# The API's short name misspells Orroroo; its full name and the other files spell it as the council does.
SHORT_NAMES = {"ORR": "Orroroo Carrieton"}


def council_by_name(name: str) -> Council:
    found = [c for c in councils().values() if norm(c.name) == norm(name)]
    if len(found) != 1:
        raise ValueError(f"no single council is named {name!r}")
    return found[0]


@dataclass
class Candidate:
    name: str
    votes: int | None = None
    elected: bool = False


@dataclass
class Contest:
    council: str  # council code
    role: str  # "mayor", "area" (council-wide councillors), or "ward"
    ward: str | None  # the ward's name without "Ward", such as "North" or "Ward 1"
    seats: int
    candidates: list[Candidate] = field(default_factory=list)
    formal: int | None = None
    informal: int | None = None
    registered: int | None = None
    counted: bool = True
    short: bool = False  # fewer candidates nominated than there were vacancies
    note: str | None = None  # a disagreement within the source's own figures

    @property
    def slug(self) -> str:
        return self.role if self.ward is None else f"ward-{changeset.slug(self.ward)}"


def ward_name(text: str) -> str:
    """'North Ward', 'Ward Councillor North Ward', or 'Goolwa  Hindmarsh Island Ward' as 'North' or 'Goolwa ...'."""
    text = " ".join(text.split())
    text = re.sub(r"^Ward Councillor\s+", "", text)
    return re.sub(r"\s+Ward$", "", text) if not re.fullmatch(r"Ward \d+", text) else text


def role_of(title: str) -> tuple[str, str | None]:
    title = " ".join(title.split())
    if title in ("Mayoral", "Lord Mayoral", "Mayor", "Lord Mayor"):
        return "mayor", None
    if title in ("Councillor at Large", "Area Councillor", "Area Councillors"):
        return "area", None
    return "ward", ward_name(title)


# 2018: the results page and one summary PDF per council ---------------------------------------------------------

NUMBER = re.compile(r"\d[\d,]*")
CONTESTING = re.compile(r"(\d+) candidates? contesting (\d+) vacanc(?:y|ies)")


def is_number(line: str) -> bool:
    return bool(NUMBER.fullmatch(line))


def summary_sections(lines: list[str]) -> list[tuple[str, list[int], list[str]]]:
    """Split a summary PDF's lines into (title, numbers before the table, table lines) per contest."""
    starts: list[tuple[int, int]] = []  # (index of title, index of '1st')
    for i, line in enumerate(lines):
        if line != "1st":
            continue
        j = i - 1
        while j >= 0 and is_number(lines[j]):
            j -= 1
        starts.append((j, i))
    out: list[tuple[str, list[int], list[str]]] = []
    for n, (title, first) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        numbers = [int(x.replace(",", "")) for x in lines[title + 1 : first]]
        out.append((lines[title], numbers, lines[first:end]))
    return out


def summary_contest(code: str, title: str, numbers: list[int], body: list[str]) -> Contest:
    role, ward = role_of(title)
    match = next(m for line in body if (m := CONTESTING.fullmatch(line)))
    seats = int(match.group(2))
    start = body.index("Ballot Papers") + 1
    candidates: list[Candidate] = []
    unopposed = "Elected Unopposed" in body
    for line in body[start:]:
        if line.startswith(("Provisional Declaration", "Awaiting", "Page ")) or not line:
            continue
        if re.fullmatch(r"\d+(\.\d+)?%", line) and not candidates:
            continue
        if line in ("Elected", "Elected Unopposed") or line.startswith("Elected "):
            candidates[-1].elected = True
        elif line in ("Excluded", "Quota") or is_number(line):
            if is_number(line) and candidates[-1].votes is None and not unopposed:
                candidates[-1].votes = int(line.replace(",", ""))
        else:
            candidates.append(Candidate(line))
    if len(candidates) != int(match.group(1)):
        raise ValueError(f"2018 {code} {title}: {len(candidates)} candidates for {match.group(0)!r}")
    contest = Contest(code, role, ward, seats, candidates, counted=not unopposed)
    if unopposed:
        if numbers:
            raise ValueError(f"2018 {code} {title}: an unopposed election with votes {numbers}")
        return contest
    if len(numbers) == 3:
        contest.informal, contest.formal = numbers[0], numbers[1]
    elif len(numbers) == 2:  # the summary leaves the informal cell blank when there were none
        contest.informal, contest.formal = 0, numbers[0]
    else:
        raise ValueError(f"2018 {code} {title}: unexpected header numbers {numbers}")
    if any(c.votes is None for c in candidates):
        raise ValueError(f"2018 {code} {title}: a candidate has no first preferences")
    total = sum(c.votes or 0 for c in candidates)
    if total != contest.formal:
        contest.note = (
            f"The summary prints {contest.formal:,} formal ballot papers; its first preferences add up to {total:,}."
        )
    if sum(c.elected for c in candidates) != seats:
        raise ValueError(f"2018 {code} {title}: {sum(c.elected for c in candidates)} elected for {seats} seats")
    return contest


def page_unopposed(body: str) -> list[tuple[str, str | None, list[str]]]:
    """The contests the 2018 results page lists as decided without a poll, with the candidates it names."""
    strongs = [
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
        for s in re.findall(r"<strong>(.*?)</strong>", body, re.DOTALL)
    ]
    out: list[tuple[str, str | None, list[str]]] = []
    current: list[str] | None = None
    for s in filter(None, strongs):
        if "No Election" in s or re.match(r"No .*Election", s):
            title = re.sub(r"\s*-?\s*No\b.*$", "", s) or "Area Councillor"
            if s.startswith("No ") and "Area Councillor" in s:
                title = "Area Councillor"
            role, ward = role_of(title)
            current = []
            out.append((role, ward, current))
        elif current is not None and re.fullmatch(r"[^-–]+, [^-–]+", s) and "Vacanc" not in s:
            current.append(s)
        else:
            current = None
    return out


@functools.cache
def contests_2018() -> list[Contest]:
    page = text(PAGE_2018)
    out: list[Contest] = []
    for printed, body in re.findall(r"<summary>(.*?)</summary>(.*?)</details>", page, re.DOTALL):
        council = council_by_name(html.unescape(printed).strip())
        found: list[Contest] = []
        link = re.search(r'results here for\s*(?:&nbsp;)?\s*<a href="([^"]+)"', body)
        if link:
            doc = pymupdf.open(raw.cached(SOURCE, HOST + html.unescape(link.group(1))).path)
            lines = [line.strip() for page_ in doc for line in page_.get_text().split("\n")]
            found = [summary_contest(council.code, *section) for section in summary_sections(lines)]
        have = {(c.role, norm(c.ward or "")) for c in found}
        for role, ward, names in page_unopposed(body):
            if (role, norm(ward or "")) in have or not names:
                continue  # in the summary already, or a failed election with no candidates
            found.append(
                Contest(council.code, role, ward, len(names), [Candidate(n, None, True) for n in names], counted=False)
            )
        out.extend(found)
    return out


# 2022: the results API ------------------------------------------------------------------------------------------


@functools.cache
def contests_2022() -> list[Contest]:
    static, counts = api_json(STATIC_2022), api_json(COUNTS_2022)
    counted = {e["uid"]: e for c in counts["councils"] for e in (c["elections"] or []) if e}
    out: list[Contest] = []
    for c in static["councils"]:
        for e in c["elections"] or []:
            status = e["electionStatus"]
            people = sorted(e["candidates"] or [], key=lambda x: x["candidateOrder"])
            if status == "electionCancelled" or not people:
                continue  # no poll and no candidate: the council under administration, or a failed election
            role, ward = role_of(e["electionName"])
            contest = Contest(c["councilCode"], role, ward, e["vacancies"])
            if status == "election":
                count = counted[e["uid"]]
                votes = {x["uid"]: x for x in count["candidates"]}
                for person in people:
                    row = votes[person["uid"]]
                    contest.candidates.append(Candidate(person["ballotName"], row["firstPrefVotes"], row["isElected"]))
                contest.informal = count["informalBallotCount"]
                contest.formal = sum(x.votes or 0 for x in contest.candidates)
                if contest.formal != count["totalEnrollment"]:  # the API's name for the formal ballot papers
                    raise ValueError(f"2022 {c['councilCode']} {e['electionName']}: first preferences disagree")
                if sum(x.elected for x in contest.candidates) != contest.seats:
                    raise ValueError(f"2022 {c['councilCode']} {e['electionName']}: elected and seats disagree")
            elif status in ("nonElection", "requiresSupElection"):
                contest.counted = False
                contest.candidates = [Candidate(p["ballotName"], None, True) for p in people]
                contest.short = len(people) < contest.seats
            else:
                raise ValueError(f"2022 {c['councilCode']} {e['electionName']}: unknown status {status}")
            out.append(contest)
    return out


def contests(year: int) -> list[Contest]:
    found = contests_2018() if year == 2018 else contests_2022()
    enrolled = participation(year)
    for c in found:
        if not c.counted:
            continue
        hit = enrolled.get((c.council, c.role, norm(c.ward or "")))
        if hit and hit[0] == (c.formal or 0) + (c.informal or 0):
            c.registered = hit[1]
    return found


WORKBOOK_NAMES = {"Lower Eyre": "Lower Eyre Peninsula"}


@functools.cache
def participation(year: int) -> dict[tuple[str, str, str], tuple[int, int]]:
    """Total votes and eligible electors per contested election, by council code, role, and ward."""
    sheet = pl.read_excel(raw.cached(SOURCE, PARTICIPATION).path, has_header=False, engine="calamine")
    total, enrolled = (1, 2) if year == 2022 else (4, 5)
    out: dict[tuple[str, str, str], tuple[int, int]] = {}
    council: Council | None = None
    for row in sheet.iter_rows():
        label = row[0]
        if label is None:
            continue
        label = " ".join(str(label).split())
        if all(v is None for v in row[1:]):
            try:
                council = council_by_name(WORKBOOK_NAMES.get(label, label))
            except ValueError:
                council = None
            continue
        if council is None or not (str(row[total] or "").strip().isdigit() and str(row[enrolled] or "").isdigit()):
            continue
        title = re.sub(r"\s*\(.*\)$", "", label)
        title = re.sub(r"\s+councillors?$", "", title, flags=re.IGNORECASE)
        if title.lower() in ("mayor", "lord mayor"):
            role, ward = "mayor", ""
        elif title.lower() == "area":
            role, ward = "area", ""
        else:
            role, ward = "ward", norm(ward_name(re.sub(r"\s+ward$", " Ward", title, flags=re.IGNORECASE)))
        out[(council.code, role, ward)] = (int(str(row[total]).strip()), int(str(row[enrolled]).strip()))
    return out


# Geography ---------------------------------------------------------------------------------------------------------

LGA_SCHEME, WARD_SCHEME = "au-sa.lga", "au-sa.ward"
TOLERANCE = 0.0002  # degrees, about 20 m


def council_area(code: str) -> str:
    return key("area", LGA_SCHEME, code)


def ward_code(year: int, contest: Contest) -> str:
    assert contest.ward is not None
    return f"{year}:{contest.council}:{changeset.slug(contest.ward)}"


def ward_area(year: int, contest: Contest) -> str:
    return key("area", WARD_SCHEME, ward_code(year, contest))


def layer(url: str) -> list[dict[str, Any]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    name = next(n for n in archive.namelist() if n.endswith("GDA2020.geojson"))
    features = json.loads(archive.read(name))["features"]
    assert isinstance(features, list)
    return features


def simplified(features: list[dict[str, Any]]) -> list[shapely.Geometry]:
    shapes = numpy.array([shapely.make_valid(shapely.geometry.shape(f["geometry"])) for f in features])
    out = []
    for shape in shapely.coverage_simplify(shapes, TOLERANCE):
        parts = [p for p in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(p) in (3, 6)]
        out.append(shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326))
    return out


def outline(p: Package, area: str, shape: shapely.Geometry, since: date | None = None) -> None:
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=since,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=LOCATION_SA,
    )


# Councils the current layer names differently: Grant has since become Southern Limestone Coast.
LAYER_NAMES = {"GRA": "Southern Limestone Coast"}


@functools.cache
def council_shapes() -> dict[str, shapely.Geometry]:
    features = layer(LGA)
    by_name = {norm(f["properties"]["abbname"]): shape for f, shape in zip(features, simplified(features), strict=True)}
    out: dict[str, shapely.Geometry] = {}
    for c in councils().values():
        found = by_name.get(norm(LAYER_NAMES.get(c.code, c.name)))
        if found is None:
            raise ValueError(f"no local government area is named {c.name}")
        out[c.code] = found
    return out


@functools.cache
def ward_shapes() -> dict[tuple[str, str], shapely.Geometry]:
    """The current ward layer's shapes by council code and ward, for councils whose wards are the 2022 ones."""
    features = layer(WARDS)
    by_council: dict[str, dict[str, shapely.Geometry]] = {}
    for f, shape in zip(features, simplified(features), strict=True):
        name = norm(ward_name(str(f["properties"]["ward"]).title()))
        by_council.setdefault(norm(f["properties"]["abbname"]), {})[name] = (
            shape  # a council not in the layer has no wards
        )
    out: dict[tuple[str, str], shapely.Geometry] = {}
    for code, council in councils().items():
        wards = {norm(c.ward) for c in contests(2022) if c.council == code and c.ward is not None}
        drawn = by_council.get(norm(council.name), {})
        if wards and set(drawn) == wards:
            out |= {(code, ward): shape for ward, shape in drawn.items()}
    return out


def ward_label(ward: str) -> str:
    return ward if ward.startswith("Ward ") else f"{ward} Ward"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/lg-geo",
        SOURCE,
        "South Australia's 67 elected councils and the wards their councillors were elected from in 2018 and 2022, "
        "with council and 2022 ward boundaries from Location SA",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.add(
        "source",
        id=LOCATION_SA,
        kind="official_body",
        publisher="Location SA, Government of South Australia",
        title="Local government areas and local government area wards (GeoJSON, GDA2020)",
        url="https://data.sa.gov.au/data/dataset/local-government-areas",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Government of South Australia (Location SA)",
        notes="The layers are kept current. Each council's outline is the current one; council boundaries did not "
        "change between 2018 and 2022. The current ward layer draws a council's 2022 wards only where its ward names "
        "are the ones the 2022 elections used.",
    )
    lga, wards = raw.cached(SOURCE, LGA), raw.cached(SOURCE, WARDS)
    p.artifact(lga, LOCATION_SA)
    p.artifact(wards, LOCATION_SA)
    p.add("scheme", code=LGA_SCHEME, polity_id=AUSTRALIA, kind="administrative", rank=2, noun="council area")
    p.add("scheme", code=WARD_SCHEME, polity_id=AUSTRALIA, kind="electoral_district", rank=3, noun="council ward")
    p.add(
        "boundary_set",
        id=key("boundary_set", LGA_SCHEME, lga.retrieved_at.year),
        scheme_code=LGA_SCHEME,
        vintage=str(lga.retrieved_at.year),
        as_of=lga.retrieved_at.date(),
    )
    for year in YEARS.values():
        p.add(
            "boundary_set",
            id=key("boundary_set", WARD_SCHEME, year.year),
            scheme_code=WARD_SCHEME,
            vintage=str(year.year),
            as_of=year.day,
        )
    shapes = council_shapes()
    for code, council in sorted(councils().items()):
        id = council_area(code)
        p.add("area", id=id, scheme_code=LGA_SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, council.full, "en", "official")
        p.name(id, council.name, "en", "short", False)
        outline(p, id, shapes[code])
    drawn = ward_shapes()
    for year in YEARS:
        seen: set[str] = set()
        for contest in contests(year):
            if contest.ward is None or ward_code(year, contest) in seen:
                continue
            seen.add(ward_code(year, contest))
            id = ward_area(year, contest)
            p.add("area", id=id, scheme_code=WARD_SCHEME)
            p.add("area_code", area_id=id, code=ward_code(year, contest))
            p.add("area_parent", area_id=id, parent_id=council_area(contest.council))
            p.name(id, f"{ward_label(contest.ward)}, {councils()[contest.council].name}", "en", "official")
            shape = drawn.get((contest.council, norm(contest.ward))) if year == 2022 else None
            if shape is not None:
                outline(p, id, shape)
    return p


# Registry ----------------------------------------------------------------------------------------------------------

STV = key("electoral_system", "council", "stv")
IRV = key("electoral_system", "council", "irv")
SYSTEMS = [
    (
        STV,
        {"formula": "stv", "quota": "droop", "surplus": "gregory"},
        (
            "Councillors are elected council-wide (area councillors) or by ward by the single transferable vote with a "
            "Droop quota, at a postal election every four years. Voters number at least as many candidates as there are "
            "vacancies, and a surplus passes on at a transfer value."
        ),
    ),
    (
        IRV,
        {"formula": "irv"},
        (
            "A mayor (the Lord Mayor in Adelaide) is elected by all of a council's electors by preferential vote, at the "
            "same postal election as the councillors."
        ),
    ),
]


def body(code: str) -> str:
    return key("body", "council", code)


def mayor(code: str) -> str:
    return key("office", "council", code, "mayor")


def ecsa_files() -> list[str]:
    return [STATIC_2022, COUNTS_2022, PAGE_2018, PAGE_2022, PARTICIPATION, *sorted(summary_links().values())]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/lg-registry",
        SOURCE,
        "South Australia's 67 elected councils, their mayors, and the electoral systems of their periodic elections, "
        "with the Electoral Commission SA files the 2018 and 2022 elections cite",
    )
    p.external |= {AUSTRALIA}
    p.add(
        "source",
        id=ECSA,
        kind="official_body",
        publisher="Electoral Commission SA",
        title="South Australian council election results published by the Electoral Commission SA",
        url=f"{HOST}/elections/council-supplementary-list",
        license="Creative Commons Attribution 3.0 Australia",
        license_url="https://creativecommons.org/licenses/by/3.0/au/",
        attribution="Electoral Commission SA",
    )
    p.add(
        "source",
        id=WIKIPEDIA,
        kind="community",
        publisher="Wikipedia",
        title="Wikipedia articles on the South Australian council elections",
        url="https://en.wikipedia.org/",
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
        notes="Read from each article's wikitext, pinned to the revision current at retrieval. Used only for the "
        "dates ballot packs went out and voting closed, which the commission's results pages do not state.",
    )
    for url in ecsa_files():
        p.artifact(raw.cached(SOURCE, url), ECSA)
    for year in YEARS.values():
        p.artifact(raw.cached(SOURCE, wiki_url(year.article)), WIKIPEDIA)
    with_mayor = {c.council for year in YEARS for c in contests(year) if c.role == "mayor"}
    for code, council in sorted(councils().items()):
        p.add("body", id=body(code), polity_id=AUSTRALIA, kind="council")
        p.name(body(code), council.full, "en", "official")
        if code in with_mayor:
            title = "Lord Mayor" if code == "ADE" else "Mayor"
            p.add("office", id=mayor(code), polity_id=AUSTRALIA, body_id=body(code), kind="mayor")
            p.name(mayor(code), f"{title} of {council.name}", "en", "official")
    for id, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=AUSTRALIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


# Elections ---------------------------------------------------------------------------------------------------------

METHOD = {
    2018: "From the Electoral Commission SA's summary of results for the council, which prints the informal and "
    "formal ballot papers of each contested election and each candidate's first preference votes, and marks the "
    "candidates elected. Candidates are listed in the summary's order. The distribution of preferences is not "
    "stored. Electors enrolled come from the commission's participation workbook for 2018 and 2022.",
    2022: "From the Electoral Commission SA's results site, which gives each candidate's first preference votes, the "
    "informal ballot papers, and the candidates elected, with the candidates in ballot paper order. The "
    "distribution of preferences is not stored. Electors enrolled come from the commission's participation "
    "workbook for 2018 and 2022.",
}
UNOPPOSED = (
    "There was no poll: no more candidates nominated than there were vacancies, and the candidates who nominated "
    "were elected unopposed."
)


def contest_title(council: Council, contest: Contest) -> str:
    if contest.role == "mayor":
        return f"{council.name}: {'Lord Mayor' if council.code == 'ADE' else 'Mayor'}"
    if contest.role == "area":
        return f"{council.name}: Area councillors"
    assert contest.ward is not None
    return f"{council.name}: {ward_label(contest.ward)} councillors"


def package_key(year: int) -> str:
    return f"{SOURCE}/lg-{YEARS[year].day}"


def election(year: int) -> Package:
    when = YEARS[year]
    found = contests(year)
    p = Package(
        package_key(year),
        SOURCE,
        f"The {year} South Australian council periodic elections: {len(found)} contests for mayor and councillors in "
        "67 councils, with first preferences per contest, from the Electoral Commission SA",
        depends=[f"{SOURCE}/lg-geo", f"{SOURCE}/lg-registry"],
    )
    p.external |= {AUSTRALIA}
    for url in [STATIC_2022, COUNTS_2022] if year == 2022 else [PAGE_2018, *summary_links().values()]:
        p.uses(raw.cached(SOURCE, url))
    p.uses(raw.cached(SOURCE, PARTICIPATION))
    p.uses(raw.cached(SOURCE, wiki_url(when.article)))
    election_id = key("election", "council", when.day)
    path = f"au-sa/{when.day}-council-elections"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=when.first,
        date_to=when.day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} South Australian council elections", "en", "official")
    p.add(
        "note",
        id=key("note", election_id),
        entity_id=election_id,
        kind="context",
        body_md=f"A postal election. Ballot packs went out from {when.first:%-d %B %Y}, and voting closed at 5 pm on "
        f"{when.day:%-d %B %Y}.",
        lang="en",
    )
    values: list[tuple[str, str, str | None, int, int]] = []
    units: set[str] = set()
    for contest in found:
        council = councils()[contest.council]
        tag = (when.day.isoformat(), contest.council, contest.slug)
        contest_id = key("contest", "council", *tag)
        district = council_area(contest.council) if contest.ward is None else ward_area(year, contest)
        common = {
            "id": contest_id,
            "election_id": election_id,
            "district_area_id": district,
            "magnitude": contest.seats,
            "round_number": 1,
            "default_result_kind": "counted" if contest.counted else "tacit",
        }
        if contest.role == "mayor":
            p.add("contest", kind="office", office_id=mayor(contest.council), electoral_system_id=IRV, **common)
        else:
            p.add("contest", kind="body_seats", body_id=body(contest.council), electoral_system_id=STV, **common)
        p.name(contest_id, contest_title(council, contest), "en", "official")
        p.add(
            "slug",
            path=f"{path}/{changeset.slug(council.name)}-{contest.slug}",
            entity_id=contest_id,
            is_current=True,
        )
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted" if contest.counted else "tacit",
            source_id=ECSA,
            status="active",
            is_default=True,
            completeness=1.0 if contest.counted else None,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD[year],
            lang="en",
        )
        caveats = [] if contest.counted else [UNOPPOSED]
        if contest.short:
            caveats.append(
                f"Only {len(contest.candidates)} candidates nominated for {contest.seats} vacancies; the vacancies left "
                "were filled at a supplementary election."
            )
        if contest.note:
            caveats.append(contest.note)
        for index, caveat in enumerate(caveats):
            p.add(
                "note",
                id=key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        options: list[str] = []
        for position, candidate in enumerate(contest.candidates, 1):
            option = key("option", *tag, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                ballot_position=position if year == 2022 else None,
                ballot_label=candidate.name,
            )
            person = key("person", *tag, position)
            p.add("person", id=person)
            p.name(person, person_name(candidate.name), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *tag, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
            )
            p.add(
                "outcome", result_set_id=result_set, option_id=option, kind="elected" if candidate.elected else "lost"
            )
            if contest.counted and candidate.votes is not None:
                values.append((result_set, unit_for(p, units, year, contest), option, VOTES, candidate.votes))
        if not contest.counted:
            continue
        unit = unit_for(p, units, year, contest)
        assert contest.formal is not None and contest.informal is not None
        values.append((result_set, unit, None, VALID, contest.formal))
        values.append((result_set, unit, None, INVALID, contest.informal))
        values.append((result_set, unit, None, CAST, contest.formal + contest.informal))
        if contest.registered is not None:
            values.append((result_set, unit, None, REGISTERED, contest.registered))
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


def unit_for(p: Package, units: set[str], year: int, contest: Contest) -> str:
    """The reporting unit of a contest's electorate: the whole council, or one ward."""
    council = councils()[contest.council]
    if contest.ward is None:
        scheme, code, area, name = LGA_SCHEME, f"{year}:{contest.council}", council_area(contest.council), council.full
    else:
        scheme, code = WARD_SCHEME, ward_code(year, contest)
        area, name = ward_area(year, contest), f"{ward_label(contest.ward)}, {council.name}"
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
            area_id=area,
            kind="standard",
        )
        p.name(id, name, "en", "official")
    return id


def person_name(ballot: str) -> str:
    """'SMITH, Jane Mary' as 'Jane Mary Smith', keeping a surname's inner capitals such as McGRATH or Du BOIS."""
    if "," not in ballot:
        return ballot
    surname, given = (part.strip() for part in ballot.split(",", 1))
    words = []
    for word in surname.split():
        if word.isupper():
            word = "-".join(part.capitalize() for part in word.split("-"))
            word = re.sub(r"^(O'|Mc|Mac)([a-z])", lambda m: m.group(1) + m.group(2).upper(), word)
        elif re.fullmatch(r"(Mc|Mac|De|Di|Du|Le|La|Van|Von)[A-Z]+", word):
            word = re.sub(
                r"^(Mc|Mac|De|Di|Du|Le|La|Van|Von)([A-Z]+)$", lambda m: m.group(1) + m.group(2).capitalize(), word
            )
        words.append(word)
    return f"{given} {' '.join(words)}".strip()


# Pipeline steps ----------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/lg-geo", f"{SOURCE}/lg-registry", *(package_key(y) for y in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
