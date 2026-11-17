"""Georgia's Supreme Council of the Autonomous Republic of Ajara, 2012-2024, from the Supreme Election Commission of Ajara.

The Commission (USKO, sec.ge) publishes each election's results on a results site of its own: the front page carries
the district totals and links to a page for every district, whose table lists each precinct's proportional (party
list) and majoritarian (single-member district) votes. The cache holds, under each site's address, its front page
followed by every district page it links to (results2012.sec.ge, results.sec.ge for 2016, results2020.sec.ge,
results20202.sec.ge for the 2020 runoff, and results2024.sec.ge). The
Commission's reports on each election (angarishi) carry the national totals, the seats each list won, and the 2016
runoff by district; they are the declared record that the precinct sums are compared with.

The 2004 and 2008 elections are not built: the Commission's site publishes only their national turnout and invalid
ballot counts, and its decisions that approve their results carry no numbers.

The body, its elections, schemes, and lists belong to Georgia's polity: the archive holds no polity for the Autonomous
Republic, and a package cannot create one. The census class finds the elections by the body's name.
"""

import functools
import html
import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "ge.ajara"
GEORGIA = str(uuid5(package.NAMESPACE, "polity\x1fGE"))
VOTES = 1
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
LAW_ID = key("source", "matsne.ajara")
OSM_ID = key("source", "osm.ajara")
SITE = "https://sec.ge"
REPORTS = {
    date(2012, 10, 1): f"{SITE}/uploads/angarishi%20%2018.12.2012.pdf",
    date(2016, 10, 8): f"{SITE}/uploads/angariSi_2016.pdf",
    date(2020, 10, 31): f"{SITE}/uploads/angarishi_2020.pdf",
    date(2024, 10, 26): f"{SITE}/uploads/angarishi_2024.pdf",
}
RESULTS = {
    date(2012, 10, 1): "https://results2012.sec.ge/",
    date(2016, 10, 8): "https://results.sec.ge/",
    date(2020, 10, 31): "https://results2020.sec.ge/",
    date(2020, 11, 21): "https://results20202.sec.ge/",
    date(2024, 10, 26): "https://results2024.sec.ge/",
}
STATUS_LAW = "https://matsne.gov.ge/ka/document/view/30442"
ELECTION_LAW = "https://matsne.gov.ge/ka/document/view/1687543"
OSM_RELATIONS = {"79": 2009237, "83": 2009238, "81": 2009239, "80": 2009240, "82": 2009241, "84": 2009242}
OSM = "https://nominatim.openstreetmap.org/lookup?format=geojson&polygon_geojson=1&osm_ids=" + ",".join(
    f"R{r}" for r in OSM_RELATIONS.values()
)
FETCHED = (*REPORTS.values(), STATUS_LAW, ELECTION_LAW, OSM)

# The municipalities of Ajara by the Commission's district code: (Georgian, English).
MUNICIPALITIES = {
    "79": ("ბათუმი", "Batumi"),
    "80": ("ქედა", "Keda"),
    "81": ("ქობულეთი", "Kobuleti"),
    "82": ("შუახევი", "Shuakhevi"),
    "83": ("ხელვაჩაური", "Khelvachauri"),
    "84": ("ხულო", "Khulo"),
}
FIRST = date(2012, 10, 1)
DAYS = (date(2012, 10, 1), date(2016, 10, 8), date(2020, 10, 31), date(2024, 10, 26))
RUNOFFS = {date(2016, 10, 8): date(2016, 10, 30), date(2020, 10, 31): date(2020, 11, 21)}
# Proportional seats of the 21 and the single-member districts, by election.
PROPORTIONAL = {FIRST: 15, DAYS[1]: 15, DAYS[2]: 18, DAYS[3]: 21}
# The seats each list won, from the Commission's reports (list number -> seats).
SEATS = {
    DAYS[1]: {"3": 1, "5": 5, "8": 1, "41": 8},
    DAYS[2]: {"5": 7, "41": 11},
    DAYS[3]: {"4": 1, "5": 4, "9": 1, "25": 2, "41": 13},
}
# The two lists that met in each runoff (list numbers) and the winner of each district, as the reports state them.
RUNOFF_LISTS = ("5", "41")

OLD_SYSTEM = key("electoral_system", "list", "largest_remainder")
NEW_SYSTEM = key("electoral_system", "list", "most_votes")
FPTP_SYSTEM = key("electoral_system", "district", "fptp")
ROUND_SYSTEM = key("electoral_system", "district", "two_round")
CHANGE = date(2020, 8, 8)
OLD_RULES = {"formula": "hare", "threshold": {"level": "list", "percent": 5}}
NEW_RULES = {"formula": "hare", "threshold": {"level": "list", "percent": 5}, "leftovers": "most_votes"}
OLD_NOTE = (
    "The proportional seats are divided among the lists that won at least 5 percent of the valid votes in the whole "
    "of Ajara, one district: each list gets the whole number of seats its votes times the seats, divided by the "
    "qualifying lists' votes, come to, and the seats left over go to the lists with the largest remainders. The "
    "2016 seat counts match this rule; the 2012 seat counts are not in the sources read."
)
NEW_NOTE = (
    "The proportional seats are divided among the lists that won at least 5 percent of the valid votes in the whole "
    "of Ajara, one district: each list gets the whole number of seats its votes times the seats, divided by the "
    "qualifying lists' votes, come to, and the seats left over go, one each, to the lists with the most votes "
    "(Ajara's election law, art. 8, as amended on 8 August 2020)."
)
TWO_ROUND_NOTE = (
    "A candidate with more than half of the votes in a single-member district is elected in the first round; "
    "otherwise the two candidates with the most votes meet in a runoff."
)
FPTP_NOTE = "The candidate with the most votes in a single-member district is elected."


def packages() -> list[str]:
    runoffs = [f"{SOURCE}/{day}" for day in RUNOFFS.values()]
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS), *runoffs]


def _data(path: Path) -> None:
    if not path.stat().st_size:
        raise ValueError(f"{path.name} is empty")
    if b"<title>Access Denied</title>" in path.read_bytes()[:2000]:
        raise ValueError(f"{path.name} is a refusal page")


def fetch() -> None:
    for url in FETCHED:
        raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".html", check=_data)
    for day in RESULTS:
        site(day)


# The district pages each site's menu links to; the menus are written by script, so they are listed here as the
# rendered menus show them. The 2024 links are those of the protocol (trad) view, which the site opens on.
DISTRICT_PAGES = {
    date(2012, 10, 1): [f"?action=res_{d}_{kind}" for kind in ("partia", "maj") for d in range(79, 85)],
    date(2016, 10, 8): [f"?action=res_{kind}_olqi&olqi={d}" for kind in ("partia", "maj") for d in range(68, 74)],
    date(2020, 10, 31): [f"?action=res_{kind}_olqi&olqi={d}" for kind in ("partia", "maj") for d in (28, 29, 30)],
    date(2020, 11, 21): [f"?action=res_maj_olqi&olqi={d}" for d in (28, 30)],
    date(2024, 10, 26): [f"?action=res_olqi&olqi={d}&type=trad" for d in range(79, 85)],
}


def site(day: date) -> None:
    """Store one copy of a results site under its address: the front page followed by every district page, each
    fetched into the cache on its own first."""
    url = RESULTS[day]
    try:
        raw.cached(SOURCE, url)
        return
    except LookupError:
        pass
    text = raw.fetch(SOURCE, url + "index.php", suffix=".html", check=_data).path.read_text(encoding="utf-8")
    parts = [text]
    for link in DISTRICT_PAGES[day]:
        parts.append(raw.fetch(SOURCE, url + link, suffix=".html", check=_data).path.read_text(encoding="utf-8"))
    raw.store(
        SOURCE,
        url,
        "\n".join(parts).encode("utf-8"),
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type="text/html",
    )


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()
        if day in RUNOFFS:
            runoff(RUNOFFS[day]).write()


def emit() -> None:
    package.run("load", packages())


# Reading the pages.


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: str) -> str:
    return " ".join(re.sub(r"[\"'“”„‟«»]", "", unicodedata.normalize("NFKC", clean(text))).casefold().split())


def page(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")


def text_of(source: str) -> str:
    """The visible text of a page, with whitespace collapsed."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", source, flags=re.DOTALL)
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", body)))


@functools.cache
def report(day: date) -> str:
    path = raw.cached(SOURCE, REPORTS[day]).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return clean(out)


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._rows = []
            self.tables.append(self._rows)
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip -= 1
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._rows is not None and self._row is not None:
            self._rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._skip:
            self._cell.append(data)


@dataclass
class Section:
    kind: str  # "list" or "district"
    district: str
    columns: list[tuple[str, str]]  # (number, label printed in the header after the number)
    rows: dict[str, dict[str, int]]  # precinct code -> column number -> votes
    labels: dict[str, str]  # precinct code -> the note the page prints beside its number, where it prints one


HEADING = re.compile(r"N\s*(\d+)[^<]*?ოლქი.{0,400}?შედეგები\s+(პროპორციული|მაჟორიტარული)", re.DOTALL)
HEADING_2024 = re.compile(r"N\s*(\d+)\s*ოლქი")


def sections(day: date) -> list[Section]:
    source = page(RESULTS[day])
    if day.year == 2024:
        found = [(m.start(), m.group(1), "list") for m in HEADING_2024.finditer(source)]
    else:
        found = [
            (m.start(), m.group(1), "list" if m.group(2) == "პროპორციული" else "district")
            for m in HEADING.finditer(source)
        ]
    out: list[Section] = []
    for i, (start, district, kind) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(source)
        parser = Tables()
        parser.feed(source[start:end])
        table = max(parser.tables, key=len, default=[])
        if len(table) < 6:
            continue
        columns = []
        for cell in table[0][1:]:
            m = re.match(r"(\d+)\s*(.*)", cell)
            if m:
                columns.append((m.group(1), m.group(2)))
        rows: dict[str, dict[str, int]] = {}
        labels: dict[str, str] = {}
        for row in table[1:]:
            if len(row) < len(columns) + 1 or not row[0]:
                continue
            printed = re.fullmatch(r"([\d.]+)(?: \((.+)\))?", row[0].strip())
            if not printed:
                continue
            code = printed.group(1)
            if printed.group(2):
                labels[code] = printed.group(2)
            if not any(row[1 : 1 + len(columns)]):
                continue  # a precinct the page lists with no count: the district's count stops before it
            if code in rows:
                raise ValueError(f"{day}: precinct {code} of {kind} {district} listed twice")
            rows[code] = {n: int(row[1 + j]) for j, (n, _) in enumerate(columns)}
        out.append(Section(kind, district, columns, rows, labels))
    return out


def counts(row: str) -> list[int]:
    """The counts of a run of "votes percent%" pairs."""
    return [int(v) for v, _ in re.findall(r"(\d+) (\d+(?:\.\d+)?)%", row)]


# The parties.


@functools.cache
def list_names(day: date) -> dict[str, str]:
    """The name of each list on the ballot, by its number."""
    source = page(RESULTS[day])
    names: dict[str, str] = {}
    if day.year == 2012:
        text = text_of(source)
        head = text.find("წინასწარი მონაცემები მაჟორიტარული")
        for n, party in re.findall(r"N(\d+) - \S+ \S+ (.+?) \d+(?:\.\d+)?% \(\d+\)", text[head:]):
            names.setdefault(n, party.replace('"', "").strip())
    elif day.year == 2016:
        for n, name in re.findall(r"\['(\d+)\. ([^']+)',\s*\d+\]", source):
            names[n] = clean(name)
    else:
        text = text_of(source)
        start = text.find("წინასწარი მონაცემები პროპორციული")
        block = text[start : end_of_lists(text, start)]
        block = re.sub(r"^.*?ხმების რაოდენობა (?:\d+ )+(?=\d+ [^\d])", "", block)
        for n, name in re.findall(r"(?<![\d%.])(\d{1,2}) ([^\d%][^%]*?) (?=\d+ \d+(?:\.\d+)?% )", block):
            names[n] = clean(name)
    return names


def party_kind(name: str) -> str:
    folded = fold(name)
    return "bloc" if "ბლოკი" in folded or "კოალიცია" in folded else "party"


def party(day: date, n: str) -> str:
    return key("party", fold(list_names(day)[n]))


# The districts' own totals, from the front page of each results site.

PAIR = r"\d+ \d+(?:\.\d+)?%"


def end_of_lists(text: str, start: int) -> int:
    """Where the front page's table of list votes ends: at the majoritarian table, or at the next electronic summary."""
    stops = [i for i in (text.find("მაჟორიტარული", start), text.find("(ელექტრონული", start)) if i > 0]
    return min(stops, default=len(text))


@functools.cache
def front_lists(day: date) -> dict[str, dict[str, int]]:
    """Each district's votes for each list as the front page prints them; "all" is the total where it prints one."""
    text = text_of(page(RESULTS[day]))
    start = text.find("წინასწარი მონაცემები პროპორციული")
    block = text[start : end_of_lists(text, start)]
    out: dict[str, dict[str, int]] = defaultdict(dict)
    if day.year == 2012:
        numbers = list_names(day)
        for code, (ka, _) in MUNICIPALITIES.items():
            m = re.search(rf"{ka} ((?:{PAIR} ?){{{len(numbers)}}})", block)
            if m:
                out[code] = dict(zip(numbers, counts(m.group(1)), strict=True))
    elif day.year == 2016:
        n = len(list_names(day))
        for code, row in re.findall(rf"N (\d+) ((?:{PAIR} ?){{{n}}})", block):
            out[code] = dict(zip(list_names(day), counts(row), strict=True))
        m = re.search(rf"სულ ((?:{PAIR} ?){{{n}}})", block)
        if m:
            out["all"] = dict(zip(list_names(day), counts(m.group(1)), strict=True))
    else:
        districts = ["28", "29", "30"] if day.year == 2020 else ["79", "80", "81", "82", "83", "84"]
        block = re.sub(r"^.*?ხმების რაოდენობა (?:\d+ )+(?=\d+ [^\d])", "", block)
        k = len(districts) + 1
        for n, _, row in re.findall(rf"(?<![\d%.])(\d{{1,2}}) ([^\d%][^%]*?) ((?:{PAIR} ?){{{k}}})", block):
            values = counts(row)
            for code, v in zip([*districts, "all"], values, strict=True):
                out[code][n] = v
    return dict(out)


@dataclass
class Candidate:
    number: str
    name: str
    party: str
    votes: int


@functools.cache
def front_districts(day: date) -> dict[str, list[Candidate]]:
    """Each majoritarian district's candidates, with their votes, as the front page prints them (2012 and 2016)."""
    text = text_of(page(RESULTS[day]))
    head = text.find("წინასწარი მონაცემები მაჟორიტარული")
    out: dict[str, list[Candidate]] = {}
    for block in re.split(r"# (?=\d+ \S+ ოლქი)", text[head:])[1:]:
        district = block.split()[0]
        out[district] = [
            Candidate(n, who, party.replace('"', "").strip(), int(votes))
            for n, who, party, votes in re.findall(r"N(\d+) - (\S+ \S+) (.+?) \d+(?:\.\d+)?% \((\d+)\)", block)
        ]
    return out


def front_2020(day: date, sec: list[Section]) -> dict[str, dict[str, int]]:
    """The 2020 majoritarian votes by district and candidate number, from the front page's three blocks."""
    text = text_of(page(RESULTS[day]))
    head = text.find("წინასწარი მონაცემები მაჟორიტარული")
    blocks = text[head:].split("დათვლილი უბნების რაოდენობა")[1:]
    out: dict[str, dict[str, int]] = {}
    for block, s in zip(
        blocks, sorted((x for x in sec if x.kind == "district"), key=lambda x: int(x.district)), strict=True
    ):
        found: dict[str, int] = {}
        for n, name in s.columns:
            m = re.search(rf"(?<![\d%.]){n} {re.escape(name)} .*?(\d+) \d+(?:\.\d+)?%", block)
            if not m:
                raise ValueError(f"{day}: district {s.district} candidate {n} {name} not on the front page")
            found[n] = int(m.group(1))
        out[s.district] = found
    return out


@functools.cache
def report_lists(day: date) -> dict[str, int]:
    """The votes each list won in the whole of Ajara, as the Commission's report prints them, by list number."""
    text = report(day)
    head = text.find("დასახელება")
    stop = text.find("5%-იანი ბარიერი")
    names = sorted(list_names(day), key=int)
    found = re.findall(r"(?<![\d,])(\d{1,3}(?: \d{3})*|\d+) (\d+,\d+|\d+\.\d+)(?: \d+)?(?= |$)", text[head:stop])
    values = [int(v.replace(" ", "")) for v, _ in found]
    if len(values) != len(names):
        raise ValueError(f"{day}: the report prints {len(values)} list totals for {len(names)} lists")
    return dict(zip(names, values, strict=True))


# Places.


def region() -> str:
    return key("area", "ge-aj.region", "GE-AJ")


def municipality(code: str) -> str:
    return key("area", "ge-aj.municipality", code)


def district_area(day: date, number: str) -> str:
    return key("area", "ge-aj.district", plan(day), number)


def plan(day: date) -> str:
    """The district plan an election used: the year of its first election."""
    return str(day.year)


PLAN_SPAN = {"2012": (DAYS[0], DAYS[1]), "2016": (DAYS[1], DAYS[2]), "2020": (DAYS[2], None)}


def precinct_municipality(code: str) -> str:
    """The municipality of a precinct by its code: the middle part of "68.79.04", or the district itself in 2012."""
    parts = code.split(".")
    return parts[1] if len(parts) == 3 else parts[0]


@functools.cache
def composition(day: date) -> dict[str, dict[str, int]]:
    """For each majoritarian district, how many precincts it holds in each municipality."""
    found: dict[str, Counter[str]] = defaultdict(Counter)
    for s in sections(day):
        if s.kind == "district":
            for code in s.rows:
                found[s.district][precinct_municipality(code if day.year != 2012 else f"{s.district}.{code}")] += 1
    return {d: dict(c) for d, c in found.items()}


def whole(day: date) -> dict[str, list[str]]:
    """The districts that are exactly a union of whole municipalities, with those municipalities."""
    held = composition(day)
    out: dict[str, list[str]] = {}
    for d, places in held.items():
        elsewhere = {m for e, other in held.items() if e != d for m in other}
        if not set(places) & elsewhere:
            out[d] = sorted(places)
    return out


@functools.cache
def shapes() -> dict[str, shapely.Geometry]:
    data = json.loads(raw.cached(SOURCE, OSM).path.read_bytes())
    ids = {str(r): code for code, r in OSM_RELATIONS.items()}
    out: dict[str, shapely.Geometry] = {}
    for feature in data["features"]:
        code = ids[str(feature["properties"]["osm_id"])]
        shape = shapely.make_valid(shapely.from_geojson(json.dumps(feature["geometry"])))
        out[code] = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if g.geom_type == "Polygon"])
    if set(out) != set(MUNICIPALITIES):
        raise ValueError(f"OpenStreetMap shapes for {sorted(out)}, expected {sorted(MUNICIPALITIES)}")
    return out


def wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def shape_row(p: Package, area: str, shape: shapely.Geometry, start: date | None, end: date | None) -> None:
    point = shapely.set_srid(shapely.point_on_surface(shape), 4326)
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=start,
        valid_to=end,
        geom=wkb(shape),
        label_point=shapely.to_wkb(point, hex=True, include_srid=True),
        quality="osm",
        source_id=OSM_ID,
    )


def sources(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="აჭარის ავტონომიური რესპუბლიკის უმაღლესი საარჩევნო კომისია",
        title="Results of the elections to the Supreme Council of the Autonomous Republic of Ajara",
        url=f"{SITE}/",
        license="Official election results of the Autonomous Republic of Ajara, published for public use",
        license_url=f"{SITE}/",
        attribution="Supreme Election Commission of the Autonomous Republic of Ajara (USKO), sec.ge",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Ajara's six municipalities and the majoritarian districts of the Supreme Council elections of 2012, 2016, and "
        "2020, with the precinct scheme",
    )
    p.external |= {GEORGIA}
    sources(p)
    p.add(
        "source",
        id=OSM_ID,
        kind="community",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap administrative boundaries of the municipalities of Ajara",
        url="https://www.openstreetmap.org/",
        license="Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SOURCE, OSM), OSM_ID)
    for day in DAYS[:3]:
        p.uses(raw.cached(SOURCE, RESULTS[day]))
    schemes = (
        ("ge-aj.region", "administrative", 1, "autonomous republic"),
        ("ge-aj.district", "electoral_district", 2, "majoritarian district"),
        ("ge-aj.municipality", "administrative", 3, "municipality"),
        ("ge-aj.precinct", "precinct", 4, "precinct"),
    )
    for code, kind, rank, noun in schemes:
        p.add("scheme", code=code, polity_id=GEORGIA, kind=kind, rank=rank, noun=noun)
    shape = shapes()
    ajara = region()
    p.add("area", id=ajara, scheme_code="ge-aj.region")
    p.add("area_code", area_id=ajara, code="GE-AJ")
    p.name(ajara, "Autonomous Republic of Ajara", "en", "official")
    p.name(ajara, "აჭარის ავტონომიური რესპუბლიკა", "ka", "native", False)
    shape_row(p, ajara, shapely.union_all(list(shape.values())), None, None)
    for code, (ka, en) in MUNICIPALITIES.items():
        id = municipality(code)
        p.add("area", id=id, scheme_code="ge-aj.municipality")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=ajara)
        p.name(id, en, "en", "official")
        p.name(id, ka, "ka", "native", False)
        shape_row(p, id, shape[code], None, None)
    for day in DAYS[:3]:
        start, end = PLAN_SPAN[plan(day)]
        whole_districts = whole(day)
        for number in sorted(composition(day), key=int):
            id = district_area(day, number)
            p.add("area", id=id, scheme_code="ge-aj.district")
            p.add("area_code", area_id=id, code=number, valid_from=start, valid_to=end)
            p.add("area_parent", area_id=id, parent_id=ajara, valid_from=start, valid_to=end)
            p.name(id, f"Majoritarian district {number}", "en", "official")
            p.name(id, f"№{number} მაჟორიტარული საარჩევნო ოლქი", "ka", "native", False)
            if number in whole_districts:
                shape_row(p, id, shapely.union_all([shape[m] for m in whole_districts[number]]), start, end)
        p.add(
            "boundary_set",
            id=p.id("boundary_set", "ge-aj.district", plan(day)),
            scheme_code="ge-aj.district",
            vintage=plan(day),
            valid_from=start,
            valid_to=end,
            as_of=day,
        )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", "ge-aj.municipality", "2012"),
        scheme_code="ge-aj.municipality",
        vintage="2012",
        valid_from=None,
        valid_to=None,
        as_of=FIRST,
    )
    return p


# The body, its electoral systems, and the lists on the ballots.

BODY = key("body", "supreme_council")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Supreme Council of Ajara, its electoral systems, and the party lists on the ballots of 2012 to 2024",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GEORGIA}
    p.add(
        "source",
        id=LAW_ID,
        kind="official_body",
        publisher="Legislative Herald of Georgia",
        title="Laws on the status and the Supreme Council elections of the Autonomous Republic of Ajara",
        url="https://matsne.gov.ge/",
        license="Official legislation of Georgia",
        license_url="https://matsne.gov.ge/",
        attribution="Legislative Herald of Georgia (Matsne), matsne.gov.ge",
    )
    p.artifact(raw.cached(SOURCE, ELECTION_LAW), LAW_ID)
    p.artifact(raw.cached(SOURCE, STATUS_LAW), LAW_ID)
    for day in DAYS:
        p.uses(raw.cached(SOURCE, RESULTS[day]))
    p.add("body", id=BODY, polity_id=GEORGIA, kind="unicameral")
    p.name(BODY, "Supreme Council of the Autonomous Republic of Ajara", "en", "official")
    p.name(BODY, "აჭარის ავტონომიური რესპუბლიკის უმაღლესი საბჭო", "ka", "native", False)
    systems = (
        (OLD_SYSTEM, None, CHANGE, OLD_RULES, OLD_NOTE),
        (NEW_SYSTEM, CHANGE, None, NEW_RULES, NEW_NOTE),
        (FPTP_SYSTEM, None, DAYS[1], {"formula": "fptp"}, FPTP_NOTE),
        (ROUND_SYSTEM, DAYS[1], None, {"formula": "two_round", "majority": 0.5, "advance": 2}, TWO_ROUND_NOTE),
    )
    for id, start, end, rules, note in systems:
        p.add(
            "electoral_system", id=id, polity_id=GEORGIA, valid_from=start, valid_to=end, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    made: set[str] = set()
    for day in DAYS:
        for n, name in sorted(list_names(day).items(), key=lambda item: int(item[0])):
            id = party(day, n)
            if id in made:
                continue
            made.add(id)
            p.add("party", id=id, kind=party_kind(name), polity_id=GEORGIA)
            p.name(id, name.strip('„“" '), "ka", "official")
    return p


# The elections.


class Facts:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str | None, int, int]] = []

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        self.rows.append((result_set, unit, option, measure, value))

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def unit_id(day: date, code: str) -> str:
    return key("unit", f"{day}:{code}")


def system_for(day: date, kind: str) -> str:
    if kind == "list":
        return NEW_SYSTEM if day >= CHANGE else OLD_SYSTEM
    return FPTP_SYSTEM if day < DAYS[1] else ROUND_SYSTEM


def election_row(p: Package, day: date, runoff: bool) -> tuple[str, str]:
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=GEORGIA,
        kind="runoff" if runoff else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    suffix = " runoff" if runoff else ""
    p.name(id, f"{day.year} Ajara Supreme Council election{suffix}", "en", "official")
    p.name(
        id,
        f"აჭარის ავტონომიური რესპუბლიკის უმაღლესი საბჭოს {day.year} წლის არჩევნები"
        + (", მეორე ტური" if runoff else ""),
        "ka",
        "native",
        False,
    )
    path = f"ge/aj/{day}-{'runoff' if runoff else 'general'}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    return id, path


METHOD = (
    "Votes by precinct from the tables of the Supreme Election Commission of Ajara's results site, which the site "
    "labels preliminary data and which cover every precinct. The site prints each precinct's votes for each list or "
    "candidate and no turnout, valid, or invalid ballots, so only votes are stored. {check}"
)


def result_set(p: Package, election_id: str, contest: str, id: str, check: str) -> None:
    p.add(
        "result_set",
        id=id,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD.format(check=check), lang="en")


def precinct_units(p: Package, day: date, rows: dict[str, str | None]) -> dict[str, str]:
    """The reporting units of a count: precinct code -> unit id. `rows` maps a code to its printed note, if any."""
    out: dict[str, str] = {}
    for code, note in rows.items():
        id = out[code] = unit_id(day, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=GEORGIA,
            scheme_code="ge-aj.precinct",
            code=f"{day}:{code}",
            area_relation="within",
            area_id=municipality(precinct_municipality(code)),
            kind="standard",
        )
        p.name(id, f"{code} ({note})" if note else code, "ka", "official")
    return out


def precinct_code(day: date, district: str, code: str) -> str:
    """A precinct's code: the Commission's own in 2016 and later, the district's number before the precinct's in 2012."""
    return f"{district}.{code}" if day.year == 2012 else code


def person(day: date, district: str, name: str) -> str:
    return key("person", day, district, fold(name))


def candidates(day: date) -> dict[str, dict[str, str]]:
    """Each majoritarian district's candidates by list number: the candidate's name."""
    if day.year == 2012 or day.year == 2016:
        return {d: {c.number: c.name for c in found} for d, found in front_districts(day).items()}
    return {s.district: {n: label for n, label in s.columns} for s in sections(day) if s.kind == "district"}


def district_votes(day: date) -> dict[str, dict[str, int]]:
    """The votes of each majoritarian candidate as the front page prints them, by district and list number."""
    if day.year == 2020:
        return front_2020(day, sections(day))
    return {d: {c.number: c.votes for c in found} for d, found in front_districts(day).items()}


def tally(rows: dict[str, dict[str, int]]) -> Counter[str]:
    total: Counter[str] = Counter()
    for row in rows.values():
        total.update(row)
    return total


def election(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} Ajara Supreme Council election, party lists and single-member districts, by precinct from "
        "the Supreme Election Commission of Ajara",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GEORGIA}
    p.artifact(raw.cached(SOURCE, RESULTS[day]), SOURCE_ID)
    p.artifact(raw.cached(SOURCE, REPORTS[day]), SOURCE_ID)
    all_sections = sections(day)
    list_sections = [s for s in all_sections if s.kind == "list"]
    district_sections = [s for s in all_sections if s.kind == "district"]
    election_id, path = election_row(p, day, runoff=False)
    notes: dict[tuple[str, str], str | None] = {}
    for s in all_sections:
        for code in s.rows:
            notes[(s.district, precinct_code(day, s.district, code))] = s.labels.get(code)
    units = precinct_units(p, day, {code: note for (_, code), note in notes.items()})
    if len(units) != len(notes):
        raise ValueError(f"{day}: a precinct code repeats in two districts")
    facts = Facts()
    # The party lists.
    contest = key("contest", day, "list")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=region(),
        magnitude=PROPORTIONAL[day],
        round_number=1,
        electoral_system_id=system_for(day, "list"),
        default_result_kind="counted",
    )
    p.name(contest, "Supreme Council of Ajara, proportional seats", "en", "official")
    p.name(contest, "უმაღლესი საბჭოს პროპორციული არჩევნები", "ka", "native", False)
    p.add("slug", path=f"{path}/list", entity_id=contest, is_current=True)
    rs = key("result_set", day, "list")
    options: dict[str, str] = {}
    for n, name in sorted(list_names(day).items(), key=lambda item: int(item[0])):
        options[n] = key("option", day, "list", n)
        p.add(
            "contest_option",
            id=options[n],
            contest_id=contest,
            kind="list",
            party_id=party(day, n),
            ballot_position=int(n),
            ballot_label=name.strip('„“" '),
        )
    summed: Counter[str] = Counter()
    for s in list_sections:
        for code, row in s.rows.items():
            unit = unit_id(day, precinct_code(day, s.district, code))
            for n, v in row.items():
                facts.add(rs, unit, options[n], VOTES, v)
                summed[n] += v
    front = front_lists(day)
    for d in {s.district for s in list_sections}:
        got = tally({c: r for s in list_sections if s.district == d for c, r in s.rows.items()})
        if dict(got) != {n: v for n, v in front[d].items() if v or n in got}:
            raise ValueError(f"{day}: district {d} precinct sums differ from its printed totals")
    check = "The precincts of each district add up to the district's totals on the site's front page."
    if day in REPORTS and day != FIRST:
        printed = report_lists(day)
        differing = {n: (summed[n], v) for n, v in printed.items() if summed[n] != v}
        if sum(printed.values()) != sum(summed.values()) or any(abs(a - b) > 1 for a, b in differing.values()):
            raise ValueError(f"{day}: precinct sums {differing} differ from the report's national totals")
        for n, v in sorted(printed.items(), key=lambda item: int(item[0])):
            p.add("declared", result_set_id=rs, option_id=options[n], measure=VOTES, value=v)
        check += " The national totals are the Commission's report's."
        if differing:
            p.accept(
                "accounting.declared",
                "The report's final national list totals differ from the sum of the site's precinct tables by one "
                f"vote for each of lists {', '.join(sorted(differing, key=int))} (the total is the same): "
                + "; ".join(f"list {n}: precincts {a}, report {b}" for n, (a, b) in sorted(differing.items())),
            )
    else:
        check += " The Commission's report prints no list totals for this election, so none are declared."
    result_set(p, election_id, contest, rs, check)
    for n, seats in sorted(SEATS.get(day, {}).items(), key=lambda item: int(item[0])):
        p.add("outcome", result_set_id=rs, option_id=options[n], kind="elected", seats=seats)
    # The single-member districts.
    if district_sections:
        names = candidates(day)
        votes = district_votes(day)
        made: set[str] = set()
        for s in sorted(district_sections, key=lambda x: int(x.district)):
            d = s.district
            contest = key("contest", day, "district", d)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="body_seats",
                body_id=BODY,
                district_area_id=district_area(day, d),
                magnitude=1,
                round_number=1,
                electoral_system_id=system_for(day, "district"),
                default_result_kind="counted",
            )
            p.name(contest, f"Supreme Council of Ajara, district {d}", "en", "official")
            p.name(contest, f"უმაღლესი საბჭო, №{d} მაჟორიტარული ოლქი", "ka", "native", False)
            p.add("slug", path=f"{path}/district-{d}", entity_id=contest, is_current=True)
            rs = key("result_set", day, "district", d)
            dopt: dict[str, str] = {}
            for n, _ in s.columns:
                who = names[d][n]
                dopt[n] = key("option", day, "district", d, n)
                p.add(
                    "contest_option",
                    id=dopt[n],
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(day, n),
                    ballot_position=int(n),
                    ballot_label=who,
                )
                pid = person(day, d, who)
                if pid not in made:
                    made.add(pid)
                    p.add("person", id=pid)
                    p.name(pid, who, "ka", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, d, n),
                    person_id=pid,
                    option_id=dopt[n],
                    role="head",
                    ballot_name=who,
                    member_of_party_id=party(day, n),
                    is_independent=False,
                )
            for code, row in s.rows.items():
                unit = unit_id(day, precinct_code(day, d, code))
                for n, v in row.items():
                    facts.add(rs, unit, dopt[n], VOTES, v)
                p.add("reporting_unit_area", unit_id=unit, area_id=district_area(day, d), method="source_code")
            got = tally(s.rows)
            if dict(got) != votes[d]:
                raise ValueError(f"{day}: district {d} precinct sums differ from its printed totals")
            for n, v in votes[d].items():
                p.add("declared", result_set_id=rs, option_id=dopt[n], measure=VOTES, value=v)
            result_set(p, election_id, contest, rs, "The precincts add up to the district's totals on the front page.")
            ranked = sorted(votes[d], key=lambda n: -votes[d][n])
            if day.year > 2012:
                top = ranked[0]
                if 2 * votes[d][top] > sum(votes[d].values()):
                    if (day, d) != (DAYS[2], "29") or top != "41" or votes[d][top] != 20809:
                        raise ValueError(f"{day}: district {d} has a first-round winner the report does not state")
                    p.add("outcome", result_set_id=rs, option_id=dopt[top], kind="elected")
                else:
                    if sorted(ranked[:2]) != sorted(RUNOFF_LISTS):
                        raise ValueError(f"{day}: district {d} runoff would not be between lists 5 and 41")
                    for n in RUNOFF_LISTS:
                        p.add("outcome", result_set_id=rs, option_id=dopt[n], kind="advanced")
    p.frame("result_value", facts.frame())
    print(f"{day}: {len(units)} precincts, {len(district_sections)} districts", flush=True)
    return p


# The runoffs.

RUNOFF_2020 = {"28": {"5": 4115, "41": 30169}, "30": {"5": 3956, "41": 32613}}


def runoff_2016() -> dict[str, dict[str, int]]:
    """The 2016 runoff votes by district and list number, from the report."""
    text = report(DAYS[1])
    block = text[text.rfind("მეორე ტურის შედეგები") :]
    block = block[: block.find("აჭარის ავტონომიური რესპუბლიკის უმაღლესი საბჭოს არჩევნების შესახებ")]
    found = [int(v.replace(" ", "")) for v in re.findall(r"\((\d[\d ]*) ხმა\)", block)]
    numbers = re.findall(r"№(\d+)", block)
    if numbers != ["68", "69", "70", "71", "72", "73"] or len(found) != 12:
        raise ValueError(f"2016 runoff parsed as districts {numbers} and votes {found}")
    return {d: dict(zip(RUNOFF_LISTS, found[2 * i : 2 * i + 2], strict=True)) for i, d in enumerate(numbers)}


def runoff(day: date) -> Package:
    first = next(d for d, r in RUNOFFS.items() if r == day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The runoffs of the {first.year} Ajara Supreme Council election in its single-member districts",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{first}"],
    )
    p.external |= {GEORGIA}
    p.uses(raw.cached(SOURCE, REPORTS[first]))  # the first round's package holds its source artifact
    election_id, path = election_row(p, day, runoff=True)
    names = candidates(first)
    facts = Facts()
    if first.year == 2016:
        districts = runoff_2016()
        units = {}
        for d in districts:
            id = units[d] = unit_id(day, d)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=GEORGIA,
                scheme_code="ge-aj.precinct",
                code=f"{day}:{d}",
                area_relation="equals",
                area_id=district_area(first, d),
                kind="aggregate",
            )
            p.name(id, f"District {d} total", "en", "official")
        method = (
            "The Commission's report prints each district's runoff votes for the two candidates and no precinct "
            "results, so the district totals are stored as aggregate units."
        )
    else:
        p.artifact(raw.cached(SOURCE, RESULTS[day]), SOURCE_ID)
        secs = {s.district: s for s in sections(day)}
        districts = {d: dict(tally(s.rows)) for d, s in secs.items()}
        if districts != RUNOFF_2020:
            raise ValueError(f"2020 runoff precinct sums {districts} differ from the report's {RUNOFF_2020}")
        units = {}
        for d, s in secs.items():
            for code in s.rows:
                units[code] = unit_id(day, code)
        for code, id in units.items():
            p.add(
                "reporting_unit",
                id=id,
                polity_id=GEORGIA,
                scheme_code="ge-aj.precinct",
                code=f"{day}:{code}",
                area_relation="within",
                area_id=municipality(precinct_municipality(code)),
                kind="standard",
            )
            p.name(id, code, "ka", "official")
        method = "Votes by precinct from the Commission's runoff results site; the districts add up to the report's."
    for d in sorted(districts, key=int):
        contest = key("contest", day, "district", d)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=BODY,
            district_area_id=district_area(first, d),
            magnitude=1,
            round_number=2,
            previous_round_id=key("contest", first, "district", d),
            electoral_system_id=ROUND_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Supreme Council of Ajara, district {d}, runoff", "en", "official")
        p.name(contest, f"უმაღლესი საბჭო, №{d} მაჟორიტარული ოლქი, მეორე ტური", "ka", "native", False)
        p.add("slug", path=f"{path}/district-{d}", entity_id=contest, is_current=True)
        rs = key("result_set", day, "district", d)
        opts: dict[str, str] = {}
        for n in RUNOFF_LISTS:
            who = names[d][n]
            opts[n] = key("option", day, "district", d, n)
            p.add(
                "contest_option",
                id=opts[n],
                contest_id=contest,
                kind="candidate",
                party_id=party(first, n),
                ballot_position=int(n),
                ballot_label=who,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, d, n),
                person_id=person(first, d, who),
                option_id=opts[n],
                role="head",
                ballot_name=who,
                member_of_party_id=party(first, n),
                is_independent=False,
            )
        if first.year == 2016:
            for n, v in districts[d].items():
                facts.add(rs, units[d], opts[n], VOTES, v)
        else:
            for code, row in secs[d].rows.items():
                for n, v in row.items():
                    facts.add(rs, units[code], opts[n], VOTES, v)
                p.add("reporting_unit_area", unit_id=units[code], area_id=district_area(first, d), method="source_code")
            for n, v in districts[d].items():
                p.add("declared", result_set_id=rs, option_id=opts[n], measure=VOTES, value=v)
        result_set(p, election_id, contest, rs, method)
        winner = max(districts[d], key=lambda n: districts[d][n])
        if winner != "41":
            raise ValueError(f"{day}: district {d} was not won by list 41, as the report states")
        p.add("outcome", result_set_id=rs, option_id=opts[winner], kind="elected")
        p.add("outcome", result_set_id=rs, option_id=opts[next(n for n in RUNOFF_LISTS if n != winner)], kind="lost")
    p.frame("result_value", facts.frame())
    print(f"{day}: {len(districts)} runoff districts", flush=True)
    return p
