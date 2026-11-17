"""Egypt's House of Representatives election of 2015, from the High Elections Committee.

The Committee's results site publishes, for each of the 205 individual constituencies and each round, the tally of
every sub-committee (al-lajna al-far'iyya), the unit that counted ballots: its roll, the voters, the valid and invalid
ballots, and each candidate's votes. It also prints, for each constituency, the roll, the voters, and the valid and
invalid ballots as the Committee declared them, which add the votes of Egyptians abroad to the sub-committees' sums.
The four list constituencies (sectors) have no tally of their own: the same sub-committee pages carry each
sub-committee's list votes, and the sectors' declared totals and winning list are printed per sector.

The 2015 election ran in two stages. Stage one voted on 17 to 19 October 2015 and stage two on 21 to 23 November, each
with a runoff the following week for the seats no candidate filled in the first round. Courts stopped the count in four
stage-one constituencies (Damanhur, al-Raml, Beni Suef, and al-Wasta) and ordered the vote there again, which the
Committee held in December and finished on 18 December 2015. The tallies the Committee publishes for those four are
those of the repeated vote.
"""

import difflib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry
import xlrd

from ea import package, raw
from ea.package import Package

SOURCE = "eg.hec"
EGYPT = str(uuid5(package.NAMESPACE, "polity\x1fEG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fEGY"))
HOME = "https://parliament2015.elections.eg/"
STATIC = HOME + "static/"
REPORT = HOME + "images/pdfs/reports/2015HoR-ReportSummary_Ar.pdf"
DECREES = {n: f"{HOME}images/pdfs/decrees/hec-decree-{n}_2015.pdf" for n in (103, 107, 126, 129)}
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/EGY"
GB_GOVERNORATES = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-EGY-ADM1.geojson"


def _xls(path: str) -> str:
    return STATIC + quote(path)


CANDIDATES = _xls("publish_candidates/excels/جميع المرشحين.xls")
LIST_CANDIDATES = _xls("publish_candidates/excels/جميع مرشحي القوائم.xls")
RUNOFF_CANDIDATES = {
    stage: _xls(f"stage{stage}_round2_candidates/excels/جميع مرشحي الإعادة.xls") for stage in (1, 2, 3)
}
WINNERS = {
    "individual1": STATIC + "stage_1_winners/excels/stage1_individual-winners.xls",
    "list1": STATIC + "stage_1_winners/excels/stage1_list-winners.xls",
    "individual2": STATIC + "stage_2_winners/excels/Phase2_individual-winners.xls",
    "list2": STATIC + "stage_2_winners/excels/Phase2_list-winners.xls",
    "individual3": STATIC + "stage_3_winners/excels/Stage1-re-elections_individual-winners.xls",
}
# The results widgets, one per stage and round. Each has a page of options and one html fragment per constituency.
DETAILED = "detailed_results"
ROUND_ONE = {1: "stage1_round1_winners", 2: "stage2_round1_results", 3: "stage3_round1_results"}
WINNER_WIDGET = {1: "stage_1_winners", 2: "stage_2_winners", 3: "stage_3_winners"}


def fragment(widget: str, code: str) -> str:
    return f"{STATIC}{widget}/html/{code}.html"


def whole(value: object) -> int:
    return int(float(str(value)))


def num(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


@dataclass(frozen=True)
class Option:
    code: str
    parent: str
    name: str


def options(widget: str) -> list[Option]:
    """The selector options of a results widget: governorates have no parent, constituencies name their governorate."""
    text = raw.cached(SOURCE, f"{STATIC}{widget}/").path.read_text(encoding="utf-8")
    return [
        Option(code, parent, html.unescape(name).strip())
        for code, parent, name in re.findall(r"<option value='(\d+)'(?: class='(\d+)')?>([^<]*)</option>", text)
    ]


def constituencies(widget: str) -> list[Option]:
    return [o for o in options(widget) if o.parent]


def fetch() -> None:
    for widget in [DETAILED, *ROUND_ONE.values(), *WINNER_WIDGET.values()]:
        raw.fetch(SOURCE, f"{STATIC}{widget}/", suffix=".html")
    urls: list[str] = []
    for option in constituencies(DETAILED):
        urls += [fragment(DETAILED, f"{option.code}{round_}") for round_ in (1, 2)]
    for stage, widget in ROUND_ONE.items():
        urls += [fragment(widget, o.code) for o in constituencies(widget)]
        urls += [fragment(WINNER_WIDGET[stage], o.code) for o in constituencies(WINNER_WIDGET[stage])]
    for widget in ("stage1_round1_winners", "stage2_round1_results"):
        urls += [fragment(widget, o.code) for o in options(widget) if not o.parent and o.code.startswith("999")]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=8)
    for url in (CANDIDATES, LIST_CANDIDATES, *RUNOFF_CANDIDATES.values(), *WINNERS.values(), REPORT, *DECREES.values()):
        raw.fetch(SOURCE, url, suffix=".xls" if "excels" in url else ".pdf")
    raw.fetch(SOURCE, GB_GOVERNORATES, suffix=".geojson")


def _cached_text(url: str) -> str | None:
    try:
        return raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    except LookupError:
        return None


@dataclass
class Block:
    """One sub-committee's tally of the individual or the list ballot."""

    voters: int
    valid: int
    invalid: int
    seats: int
    rows: list[tuple[int, str, int]]


@dataclass
class Sub:
    number: int
    center: str
    registered: int
    individual: Block | None
    lists: Block | None


def _block(body: str, kind: str) -> Block | None:
    found = re.search(rf"<tbody id=['\"]sub_list-{kind}-[^'\"]*['\"][^>]*>(.*?)</tbody>", body, re.DOTALL)
    if not found:
        return None
    block = found.group(1)
    figures: dict[str, int] = {}
    for label, name in (
        ("اجمالي الحضور", "voters"),
        ("اجمالي الاصوات الصحيحة", "valid"),
        ("اجمالي الاصوات الباطلة", "invalid"),
        ("عدد المقاعد", "seats"),
    ):
        match = re.search(re.escape(label) + r"[^<\d]*(?:<[^>]*>)*\s*([\d,]+)", block)
        if match:
            figures[name] = num(match.group(1))
    rows = re.findall(r"<tr>\s*<td>(\d+)</td>\s*<td>([^<]*)</td>\s*<td>([\d,]+)</td>\s*</tr>", block)
    return Block(
        figures["voters"],
        figures["valid"],
        figures["invalid"],
        figures.get("seats", 0),
        [(int(n), html.unescape(name).strip(), num(votes)) for n, name, votes in rows],
    )


def subcommittees(code: str, round_: int) -> list[Sub] | None:
    """The sub-committees of a constituency and round, or None when the Committee has no page for it."""
    text = _cached_text(fragment(DETAILED, f"{code}{round_}"))
    if text is None:
        return None
    parts = re.split(r"<tr>\s*<td>(\d+)</td>\s*<td>([^<]*)</td>\s*<td>([\d,]+)</td>(?=\s*<td><a)", text)
    return [
        Sub(
            int(parts[i]),
            html.unescape(parts[i + 1]).strip(),
            num(parts[i + 2]),
            _block(parts[i + 3], "indiv"),
            _block(parts[i + 3], "list"),
        )
        for i in range(1, len(parts), 4)
    ]


def declared(widget: str, code: str) -> dict[str, int] | None:
    """A constituency's declared roll, voters, and valid and invalid ballots, from the Committee's results page."""
    text = _cached_text(fragment(widget, code))
    if text is None:
        return None
    figures: dict[str, int] = {}
    for label, name in (
        ("عدد المقاعد :", "seats"),
        ("عدد الناخبين المقيدين", "registered"),
        ("عدد الحضور", "voters"),
        ("إجمالي الأصوات الصحيحة", "valid"),
        ("إجمالي الأصوات الباطله", "invalid"),
    ):
        match = re.search(re.escape(label) + r"[^<]*<span class='result_number'>([\d,]+)</span>", text)
        if match:
            figures[name] = num(match.group(1))
    return figures or None


def sheet(url: str) -> list[list[object]]:
    return xlrd.open_workbook(raw.cached(SOURCE, url).path).sheet_by_index(0)._cell_values


def norm(text: object) -> str:
    """An Arabic string with its letter variants, diacritics, and punctuation folded, for matching names across the
    Committee's pages and workbooks, which spell the same name in different ways."""
    value = unicodedata.normalize("NFKC", str(text))
    value = re.sub(r"[ً-ٟـٰ]", "", value)
    value = value.translate(str.maketrans("أإآٱىةؤئ", "اااايهوي"))
    value = re.sub(r"[^\w]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def names_en(governorate: str) -> str:
    return GOVERNORATES[governorate][0]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


HEC = key("source", "hec")
GEO = key("source", "geoboundaries-adm1")
HOUSE = key("body", "house-of-representatives")
SYSTEM_SINGLE = key("electoral_system", "individual", "single")
SYSTEM_BLOCK = key("electoral_system", "individual", "block")
SYSTEM_RUNOFF = key("electoral_system", "individual", "runoff")
SYSTEM_LIST = key("electoral_system", "list")
ELECTION = key("election", 2015)
FIRST, LAST = date(2015, 10, 17), date(2015, 12, 18)
PATH = "eg/2015-10-17-house-of-representatives"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
BOUNDARY_FROM = date(2014, 1, 1)

# The governorates by the name the Committee prints, with the English name and geoBoundaries' name for its shape.
GOVERNORATES = {
    "القاهرة": ("Cairo", "Cairo Governorate"),
    "الأسكندرية": ("Alexandria", "Alexandria Governorate"),
    "بورسعيد": ("Port Said", "Port Said Governorate"),
    "السويس": ("Suez", "Suez Governorate"),
    "دمياط": ("Damietta", "Damietta Governorate"),
    "الدقهلية": ("Dakahlia", "Dakahlia Governorate"),
    "الشرقية": ("Sharqia", "Al Sharqia Governorate"),
    "القليوبية": ("Qalyubia", "Qalyubia Governorate"),
    "كفر الشيخ": ("Kafr el-Sheikh", "Kafr el-Sheikh Governorate"),
    "الغربية": ("Gharbia", "Gharbiyya Governorate"),
    "المنوفية": ("Monufia", "Monufia Governorate"),
    "البحيرة": ("Beheira", "Beheira Governorate"),
    "الإسماعيلية": ("Ismailia", "Ismailia Governorate"),
    "الجيزة": ("Giza", "Giza Governorate"),
    "بني سويف": ("Beni Suef", "Beni Suef Governorate"),
    "الفيوم": ("Faiyum", "Faiyum Governorate"),
    "المنيا": ("Minya", "Minya Governate"),
    "أسيوط": ("Asyut", "Asyut Governorate"),
    "سوهاج": ("Sohag", "Sohag Governorate"),
    "قنا": ("Qena", "Qena Governorate"),
    "أسوان": ("Aswan", "Aswan Governorate"),
    "الأقصر": ("Luxor", "Luxor Governate"),
    "البحر الأحمر": ("Red Sea", "Red Sea Governorate"),
    "الوادي الجديد": ("New Valley", "New Valley Governorate"),
    "مرسى مطروح": ("Matrouh", "Matrouh Governorate"),
    "شمال سيناء": ("North Sinai", "North Sinai Governorate"),
    "جنوب سيناء": ("South Sinai", "South Sinai Governorate"),
}
# The four list constituencies by the Committee's code: the English name, the governorates each joins, and its seats.
SECTORS = {
    "99901": (
        "Cairo, South and Middle Delta",
        ("القاهرة", "القليوبية", "الدقهلية", "المنوفية", "الغربية", "كفر الشيخ"),
        45,
        2,
    ),
    "99902": (
        "North, Middle and South Upper Egypt",
        (
            "الجيزة",
            "الفيوم",
            "بني سويف",
            "المنيا",
            "أسيوط",
            "سوهاج",
            "قنا",
            "الأقصر",
            "أسوان",
            "البحر الأحمر",
            "الوادي الجديد",
        ),
        45,
        1,
    ),
    "99903": (
        "East Delta",
        ("الشرقية", "الإسماعيلية", "بورسعيد", "السويس", "دمياط", "شمال سيناء", "جنوب سيناء"),
        15,
        2,
    ),
    "99904": ("West Delta", ("الأسكندرية", "البحيرة", "مرسى مطروح"), 15, 1),
}
SECTOR_WORDS = {"99901": "القاهره", "99902": "الصعيد", "99903": "شرق", "99904": "غرب"}
METHOD = (
    "The High Elections Committee counted the ballots in sub-committees and publishes each sub-committee's roll, "
    "voters, valid and invalid ballots, and each candidate's or list's votes. This set holds those tallies. Each "
    "constituency's results page also declares a roll, voters, and valid and invalid ballots for the whole "
    "constituency, which the Committee's report says add the votes of Egyptians abroad before the result is "
    "declared; where a declared figure exceeds the sub-committees' sum, the difference is stored as a derived "
    "constituency-level value without a candidate breakdown, which the Committee does not publish. In individual "
    "constituencies voters cast as many votes as the constituency has seats, so a candidate's votes are counted "
    "per vote and the valid ballots per voter. A candidate who wins more than half of the valid ballots is elected "
    "in the first round; the rest of the seats go to a runoff among twice as many candidates as seats remain."
)
GENDER = {"ذكر": "male", "انثى": "female"}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2015"]


def parse() -> None:
    for build in (geo(), registry(), election()):
        build.write()


def emit() -> None:
    package.run("load", packages())


def area(kind: str, code: str) -> str:
    return key("area", kind, code)


def governorates() -> dict[str, str]:
    """The Committee's governorate codes by name, from the options of its governorate selector."""
    found = {o.name: o.code for o in options(DETAILED) if not o.parent and not o.code.startswith("999")}
    missing = [name for name in GOVERNORATES if name not in found]
    if missing or len(found) != len(GOVERNORATES):
        raise ValueError(f"the governorate selector does not match the table: {missing}")
    return found


def sectors() -> dict[str, str]:
    """The Committee's name for each list constituency."""
    found: dict[str, str] = {}
    for widget in ("stage1_round1_winners", "stage2_round1_results"):
        for o in options(widget):
            if o.code in SECTORS:
                found[o.code] = o.name
    if set(found) != set(SECTORS):
        raise ValueError(f"the list constituencies are {sorted(found)}")
    return found


def _shapes() -> dict[str, shapely.Geometry]:
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_GOVERNORATES).path.read_bytes())["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        parts = [piece for part in parts for piece in shapely.get_parts(part)]
        found[feature["properties"]["shapeName"]] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    return found


def _geometry(p: Package, id: str, shape: shapely.Geometry) -> None:
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=BOUNDARY_FROM,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=GEO,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Egypt's governorates, the 205 individual constituencies and 4 list constituencies of 2015, and the "
        "sub-committee scheme",
    )
    p.external |= {EGYPT, COUNTRY}
    p.add(
        "source",
        id=GEO,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
        title="geoBoundaries gbOpen: Egypt ADM1 (governorates)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes="Boundary year 2017, built December 2023. The twenty-seven governorates of 2015 are the same as these.",
    )
    p.artifact(raw.cached(SOURCE, GB_GOVERNORATES), GEO)
    for scheme, rank, kind, noun in (
        ("eg.hec_governorate", 1, "administrative", "governorate"),
        ("eg.constituency", 2, "electoral_district", "constituency"),
        ("eg.list_constituency", 2, "electoral_district", "list constituency"),
        ("eg.hec_subcommittee", 3, "precinct", "sub-committee"),
    ):
        p.add("scheme", code=scheme, polity_id=EGYPT, kind=kind, rank=rank, noun=noun, code_system=None)
    for scheme in ("eg.hec_governorate", "eg.list_constituency"):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, 2015),
            scheme_code=scheme,
            vintage="2015",
            valid_from=BOUNDARY_FROM,
            as_of=FIRST,
        )
    shapes = _shapes()
    ids = governorates()
    by_name: dict[str, shapely.Geometry] = {}
    for arabic, (english, shape_name) in GOVERNORATES.items():
        id = area("governorate", ids[arabic])
        by_name[arabic] = shapes[shape_name]
        p.add("area", id=id, scheme_code="eg.hec_governorate")
        p.add("area_code", area_id=id, code=ids[arabic])
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, english, "en", "official")
        p.name(id, arabic, "ar", "native", False)
        _geometry(p, id, shapes[shape_name])
    for code, name in sectors().items():
        english, members, _, _ = SECTORS[code]
        id = area("sector", code)
        p.add("area", id=id, scheme_code="eg.list_constituency")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"{english} list constituency", "en", "official")
        p.name(id, name, "ar", "official", False)
        shape = shapely.union_all([by_name[member] for member in members])
        parts = [
            piece
            for part in shapely.get_parts(shape)
            if shapely.get_type_id(part) in (3, 6)
            for piece in shapely.get_parts(part)
        ]
        _geometry(p, id, shapely.set_srid(shapely.MultiPolygon(parts), 4326))
    parents = {o.code: o.parent for o in constituencies(DETAILED)}
    for o in constituencies(DETAILED):
        id = area("constituency", o.code)
        p.add("area", id=id, scheme_code="eg.constituency")
        p.add("area_code", area_id=id, code=o.code)
        p.add("area_parent", area_id=id, parent_id=area("governorate", parents[o.code]))
        p.name(id, o.name, "ar", "official")
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "Egypt's House of Representatives and the systems that elect it")
    p.external.add(EGYPT)
    p.add("body", id=HOUSE, polity_id=EGYPT, kind="unicameral")
    p.name(HOUSE, "House of Representatives of Egypt", "en", "official")
    p.name(HOUSE, "مجلس النواب", "ar", "native", False)
    for id, rules, text in (
        (
            SYSTEM_SINGLE,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A constituency with one seat elects the candidate with more than half of the valid ballots in "
                "the first round; otherwise the two leading candidates face a runoff."
            ),
        ),
        (
            SYSTEM_RUNOFF,
            {"formula": "fptp"},
            "A runoff for the last seat of a constituency goes to the candidate with the most votes.",
        ),
        (
            SYSTEM_BLOCK,
            {"formula": "block"},
            (
                "A constituency with several seats gives every voter as many votes as there are seats. In the "
                "first round a candidate with more than half of the valid ballots is elected; the runoff among "
                "twice as many candidates as seats remain fills the rest by plurality."
            ),
        ),
        (
            SYSTEM_LIST,
            {"formula": "winner_take_all"},
            (
                "Four list constituencies of 15, 15, 45, and 45 seats elect closed lists. The list with the most "
                "votes takes every seat."
            ),
        ),
    ):
        p.add("electoral_system", id=id, polity_id=EGYPT, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def _rows(url: str) -> list[list[object]]:
    return xlrd.open_workbook(raw.cached(SOURCE, url).path).sheet_by_index(0)._cell_values


@dataclass
class Candidate:
    serial: int
    name: str
    short: str
    party: str
    symbol: str


def candidate_table() -> dict[tuple[str, str], list[Candidate]]:
    """Every individual candidate by governorate and constituency, in ballot order, from the Committee's workbook."""
    rows = _rows(CANDIDATES)
    expected = [
        "المرحلة",
        "المحافظة",
        "دائرة فردي",
        "مسلسل",
        "اسم المرشح",
        "اسم الشهرة",
        "الانتماء الحزبي",
        "اسم الرمز",
    ]
    if rows[0] != expected:
        raise ValueError(f"the candidate workbook's columns changed: {rows[0]}")
    found: dict[tuple[str, str], list[Candidate]] = {}
    for row in rows[1:]:
        found.setdefault((norm(row[1]), norm(row[2])), []).append(
            Candidate(whole(row[3]), str(row[4]).strip(), str(row[5]).strip(), str(row[6]).strip(), str(row[7]).strip())
        )
    return found


@dataclass
class Winner:
    name: str
    short: str
    party: str
    gender: str
    runoff: bool


def winner_table(codes: dict[tuple[str, str], str]) -> dict[str, list[Winner]]:
    """The declared winners of each individual constituency, from the Committee's two stage workbooks."""
    found: dict[str, list[Winner]] = {}
    for url in (WINNERS["individual1"], WINNERS["individual2"]):
        rows = _rows(url)
        if rows[0][2] != "الاسم الكامل للمرشح" or rows[0][13] != "النتيجة":
            raise ValueError(f"the winners workbook's columns changed: {rows[0]}")
        for row in rows[1:]:
            code = codes[(norm(row[0]), norm(row[1]))]
            result = str(row[13])
            if "الإعادة" not in result and "الأولى" not in result:
                raise ValueError(f"{code}: the result {result!r} is neither a first-round nor a runoff win")
            found.setdefault(code, []).append(
                Winner(
                    str(row[2]).strip(),
                    str(row[3]).strip(),
                    str(row[10]).strip(),
                    str(row[8]).strip(),
                    "الإعادة" in result,
                )
            )
    return found


def match(names: dict[str, int], name: str) -> int | None:
    """The serial of the candidate a page names, allowing for the Committee's differing spellings."""
    target = norm(name)
    if target in names:
        return names[target]
    tokens = {tuple(sorted(known.split())): serial for known, serial in names.items()}
    if tuple(sorted(target.split())) in tokens:
        return tokens[tuple(sorted(target.split()))]
    close = difflib.get_close_matches(target, list(names), n=2, cutoff=0.85)
    if len(close) == 1 or (len(close) == 2 and difflib.SequenceMatcher(None, target, close[0]).ratio() > 0.95):
        return names[close[0]]
    return None


def round_page(text: str) -> tuple[list[str], list[str], int | None]:
    """The winners and the runoff candidates a constituency's first-round page names, and the seats left."""
    winners: list[str] = []
    runoff: list[str] = []
    left: int | None = None
    for part in re.split(r'<h[23] class="winner_header red">', text)[1:]:
        title = part.split("</h", 1)[0]
        names = [
            html.unescape(name).strip()
            for _, name in re.findall(r"<tr>\s*<td>(\d+)</td>\s*<td>([^<]*)</td>\s*</tr>", part)
        ]
        if "الفائزين" in title:
            winners = names
        elif "الإعادة" in title:
            runoff = names
            seats = re.search(r"عدد المقاعد المتبقيه[^<]*<span class='result_number'>(\d+)</span>", part)
            left = int(seats.group(1)) if seats else None
    return winners, runoff, left


def runoff_table(codes: dict[tuple[str, str], str]) -> dict[str, list[Candidate]]:
    """The candidates of each runoff, which also carry party names the first-round workbook lacks."""
    found: dict[str, list[Candidate]] = {}
    for url in RUNOFF_CANDIDATES.values():
        for row in _rows(url)[1:]:
            code = codes.get((norm(row[0]), norm(row[1])))
            if code is not None:
                found.setdefault(code, []).append(
                    Candidate(
                        whole(row[2]),
                        str(row[3]).strip(),
                        str(row[4]).strip(),
                        str(row[5]).strip(),
                        str(row[6]).strip(),
                    )
                )
    return found


class Build:
    """The state shared while one package's rows are written."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.values: list[dict[str, object]] = []
        self.units: set[str] = set()
        self.parties: set[str] = set()
        self.people: set[str] = set()
        self.issues: list[str] = []

    def put(self, result_set: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
        self.values.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": flags,
                "value": value,
            }
        )

    def party(self, text: str) -> str | None:
        if not text or text in ("مستقل", "-", "--"):
            return None
        id = key("party", norm(text))
        if id not in self.parties:
            self.parties.add(id)
            self.p.add("party", id=id, kind="party", polity_id=EGYPT)
            self.p.name(id, text, "ar", "official")
        return id

    def person(self, id: str, name: str, short: str, gender: str | None = None) -> None:
        if id in self.people:
            return
        self.people.add(id)
        self.p.add("person", id=id, gender=GENDER.get(gender or ""))
        self.p.name(id, name, "ar", "official")
        if short and short not in ("-", "--") and norm(short) != norm(name):
            self.p.name(id, short, "ar", "short", False)

    def result_set(self, election: str, contest: str, name: str, completeness: float = 1.0) -> str:
        id = key("result_set", name)
        self.p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=HEC,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        return id


def election() -> Package:
    p = Package(
        f"{SOURCE}/2015",
        SOURCE,
        "The 2015 Egyptian House of Representatives election by constituency and sub-committee, from the High "
        "Elections Committee",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {EGYPT, COUNTRY}
    b = Build(p)
    p.add(
        "source",
        id=HEC,
        kind="official_body",
        publisher="High Elections Committee of Egypt",
        title="Results of the 2015 House of Representatives election",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="High Elections Committee of Egypt",
    )
    cited: set[str] = set()
    for url in sorted(raw.keys(SOURCE)):
        artifact = raw.cached(SOURCE, url)
        if url != GB_GOVERNORATES and artifact.storage_key not in cited:
            cited.add(artifact.storage_key)
            p.artifact(artifact, HEC)
    p.accept(
        "accounting.votes_valid",
        "Where a constituency's declared valid ballots exceed its sub-committees' sum, the difference is stored on "
        "the constituency's own unit without a breakdown by candidate, because the Committee publishes the votes "
        "of Egyptians abroad only as totals; those units carry valid ballots and no candidate votes.",
    )
    p.add("election", id=ELECTION, polity_id=EGYPT, kind="general", date_from=FIRST, date_to=LAST, status="held")
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    p.name(ELECTION, "2015 Egyptian House of Representatives election", "en", "official")
    p.name(ELECTION, "انتخابات مجلس النواب 2015", "ar", "native", False)
    p.add(
        "note",
        id=key("note", ELECTION),
        entity_id=ELECTION,
        kind="methodology",
        body_md=METHOD
        + " The election ran in two stages, on 17 to 19 October and 21 to 23 November 2015, with runoffs the following week "
        "later (High Elections Committee decrees 103 and 107, 126 and 129 of 2015). Courts stopped the count in "
        "four stage-one constituencies, which the Committee ran again in December and finished on 18 December "
        "2015 (decree 134); the tallies stored for those four are the repeated vote's, and the Committee "
        "publishes no results of the annulled vote. The president appointed another 28 members, who are not "
        "elected and are not stored.",
        lang="en",
    )
    names = {o.code: o.name for o in options(DETAILED) if not o.parent}
    stage: dict[str, int] = {}
    for number, widget in reversed(list(ROUND_ONE.items())):
        for o in constituencies(widget):
            stage[o.code] = number
    codes = {(norm(names[o.parent]), norm(o.name)): o.code for o in constituencies(DETAILED)}
    table = candidate_table()
    runoffs = runoff_table(codes)
    winners = winner_table(codes)
    sector_of = {name: code for code, (_, members, _, _) in SECTORS.items() for name in members}
    list_subs: dict[str, list[tuple[str, Sub]]] = {code: [] for code in SECTORS}
    for o in constituencies(DETAILED):
        governorate = names[o.parent]
        sector = sector_of[governorate]
        area_id = area("constituency", o.code)
        total_unit = key("unit", "constituency", o.code)
        p.add(
            "reporting_unit",
            id=total_unit,
            polity_id=EGYPT,
            scheme_code="eg.constituency",
            code=o.code,
            area_relation="equals",
            area_id=area_id,
            kind="aggregate",
        )
        p.name(total_unit, o.name, "ar", "official")
        first = subcommittees(o.code, 1)
        second = subcommittees(o.code, 2)
        if first is None:
            raise ValueError(f"{o.code} has no first-round page")
        for subs in (first, second or []):
            if len({s.number for s in subs}) != len(subs):
                raise ValueError(f"{o.code}: sub-committee numbers repeat")
        known: dict[int, Candidate] = {}
        for candidate in table.get((norm(governorate), norm(o.name)), []):
            known[candidate.serial] = candidate
        # sub-committee units
        for subs in (second or [], first):
            for s in subs:
                unit = key("unit", "sub", o.code, s.number)
                if unit in b.units:
                    continue
                b.units.add(unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=EGYPT,
                    scheme_code="eg.hec_subcommittee",
                    code=f"{o.code}:{s.number}",
                    parent_id=total_unit,
                    area_relation="within",
                    area_id=area_id,
                    kind="standard",
                )
                p.name(unit, f"{s.number} - {s.center}", "ar", "official")
                p.add(
                    "reporting_unit_area",
                    unit_id=unit,
                    area_id=area("sector", sector),
                    method="official_list",
                    approximate=False,
                    partial=False,
                )
        list_subs[sector] += [(o.code, s) for s in first if s.lists]
        _individual(
            b,
            o,
            governorate,
            stage[o.code],
            first,
            second,
            known,
            runoffs.get(o.code, []),
            winners.get(o.code, []),
            total_unit,
        )
    for code in SECTORS:
        _list(b, code, list_subs[code])
    p.frame("result_value", pl.DataFrame(b.values, infer_schema_length=None))
    if b.issues:
        print("\n".join(b.issues))
    return p


def _tally(b: Build, result_set: str, unit: str, s: Sub, block: Block | None, options: dict[int, str]) -> None:
    b.put(result_set, unit, None, REGISTERED, s.registered)
    if block is None:
        return
    b.put(result_set, unit, None, BALLOTS, block.voters)
    b.put(result_set, unit, None, VALID, block.valid)
    b.put(result_set, unit, None, INVALID, block.invalid)
    for number, _, votes in block.rows:
        if number in options:
            b.put(result_set, unit, options[number], VOTES, votes)


def _residual(
    b: Build, result_set: str, unit: str, declared_: dict[str, int] | None, subs: list[Sub], kind: str, label: str
) -> None:
    """The constituency-level votes the Committee declares beyond its sub-committees' tallies."""
    if declared_ is None:
        return
    blocks = [getattr(s, kind) for s in subs if getattr(s, kind)]
    if not blocks:
        for measure, name in ((BALLOTS, "voters"), (VALID, "valid"), (INVALID, "invalid")):
            b.put(result_set, unit, None, measure, declared_[name])
        return
    extra = {
        name: declared_[name] - sum(getattr(block, name) for block in blocks) for name in ("voters", "valid", "invalid")
    }
    if any(value < 0 for value in extra.values()):
        b.issues.append(f"{label}: the sub-committees exceed the declared figures by {sorted(extra.items())}")
        return
    if "registered" in declared_:
        gap = declared_["registered"] - sum(s.registered for s in subs)
        if gap < 0:
            b.issues.append(f"{label}: the sub-committees' roll exceeds the declared roll by {-gap}")
        elif gap >= extra["voters"]:
            b.put(result_set, unit, None, REGISTERED, gap, 1)
        elif gap:
            b.issues.append(
                f"{label}: the roll the sub-committees leave out ({gap}) is below the votes added ({extra['voters']})"
            )
    if any(extra.values()):
        for measure, name in ((BALLOTS, "voters"), (VALID, "valid"), (INVALID, "invalid")):
            b.put(result_set, unit, None, measure, extra[name], 1)


def _individual(
    b: Build,
    o: Option,
    governorate: str,
    stage: int,
    first: list[Sub],
    second: list[Sub] | None,
    known: dict[int, Candidate],
    runoff: list[Candidate],
    winners: list[Winner],
    total_unit: str,
) -> None:
    p = b.p
    code = o.code
    rerun = code in {x.code for x in constituencies(ROUND_ONE[3])}
    widget = ROUND_ONE[3 if rerun else stage]
    declared_ = declared(widget, code)
    text = _cached_text(fragment(widget, code)) or ""
    won_first, advanced, left = round_page(text)
    # the candidates on each round's ballot, by their number
    ballots: dict[int, dict[int, str]] = {1: {}, 2: {}}
    for round_, subs in ((1, first), (2, second or [])):
        for s in subs:
            for number, name, _ in s.individual.rows if s.individual else []:
                ballots[round_].setdefault(number, name)
    if not ballots[1]:
        ballots[1] = {c.serial: c.name for c in known.values()}
    by_name = {norm(name): number for number, name in ballots[1].items()}
    pool = {norm(c.name): c for c in [*known.values(), *runoff]}
    pool |= {norm(w.name): Candidate(0, w.name, w.short, w.party, "") for w in winners}

    def candidate(number: int) -> Candidate:
        name = ballots[1][number]
        record = known.get(number)
        if record is None or norm(record.name) != norm(name):
            record = pool.get(norm(name))
            if record is None:
                for other in pool.values():
                    if match({norm(name): 0}, other.name) is not None:
                        record = other
                        break
        return Candidate(number, name, record.short if record else "", record.party if record else "", "")

    def person(number: int) -> str:
        record = candidate(number)
        id = key("person", code, number)
        won = next((w for w in winners if match({norm(record.name): 0}, w.name) is not None), None)
        b.person(id, record.name, record.short, won.gender if won else None)
        return id

    def option(contest: str, round_: int, number: int) -> str:
        record = candidate(number)
        id = key("option", code, round_, number)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="candidate",
            ballot_position=number,
            ballot_label=record.name,
        )
        party = b.party(record.party)
        p.add(
            "candidacy",
            id=key("candidacy", code, round_, number),
            person_id=person(number),
            option_id=id,
            role="head",
            ballot_name=record.name,
            member_of_party_id=party,
            is_independent=record.party == "مستقل",
        )
        return id

    seats = (declared_ or {}).get("seats") or next((s.individual.seats for s in first if s.individual), 0)
    if not seats:
        raise ValueError(f"{code}: no seat count")
    runoff_seats = left or next((s.individual.seats for s in (second or []) if s.individual), 0)
    contests: dict[int, str] = {}
    results: dict[int, str] = {}
    for round_, magnitude in ((1, seats), (2, runoff_seats)):
        if round_ == 2 and not second:
            continue
        contest = contests[round_] = key("contest", code, round_)
        suffix = "" if round_ == 1 else "-runoff"
        p.add(
            "contest",
            id=contest,
            election_id=ELECTION,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area("constituency", code),
            magnitude=magnitude,
            round_number=round_,
            previous_round_id=contests.get(1) if round_ == 2 else None,
            electoral_system_id=(SYSTEM_SINGLE if magnitude == 1 else None)
            if round_ == 1
            else (SYSTEM_RUNOFF if magnitude == 1 else SYSTEM_BLOCK),
            default_result_kind="counted",
        )
        english = names_en(governorate)
        p.name(
            contest,
            f"2015 House of Representatives election, {english}, {o.name}" + (", runoff" if round_ == 2 else ""),
            "en",
            "official",
        )
        p.name(contest, o.name + (" - جولة الإعادة" if round_ == 2 else ""), "ar", "native", False)
        p.add("slug", path=f"{PATH}/c{code}{suffix}", entity_id=contest, is_current=True)
        has_votes = any(s.individual for s in (first if round_ == 1 else second or []))
        results[round_] = b.result_set(ELECTION, contest, f"{code}:{round_}", 1.0 if has_votes else 0.0)
        if not has_votes:
            p.add(
                "note",
                id=key("note", results[round_]),
                entity_id=results[round_],
                kind="caveat",
                body_md="The High Elections Committee's page of sub-committee tallies for this constituency and "
                "round lists only each sub-committee's roll, so the votes are not stored, and the Committee "
                "declares no totals for a runoff.",
                lang="en",
            )
    # options, then values and outcomes; a runoff numbers its candidates afresh, so they are matched by name
    options_by_round: dict[int, dict[int, str]] = {}
    serial_of: dict[int, dict[int, int]] = {1: {number: number for number in ballots[1]}, 2: {}}
    for local, name in ballots[2].items():
        number = match(by_name, name)
        if number is None:
            b.issues.append(f"{code}: runoff candidate {name!r} matches no first-round candidate")
            number = 1000 + local
            ballots[1][number] = name
        serial_of[2][local] = number
    for round_, contest in contests.items():
        options_by_round[round_] = {
            local: option(contest, round_, number) for local, number in serial_of[round_].items()
        }
    for round_, subs in ((1, first), (2, second or [])):
        if round_ not in contests:
            continue
        for s in subs:
            _tally(b, results[round_], key("unit", "sub", code, s.number), s, s.individual, options_by_round[round_])
    _residual(b, results[1], total_unit, declared_, first, "individual", f"{code} {o.name}")

    def serials(names: list[str], label: str) -> set[int]:
        found: set[int] = set()
        for name in names:
            number = match(by_name, name)
            if number is None:
                b.issues.append(f"{code}: {label} {name!r} matches no candidate")
            else:
                found.add(number)
        return found

    elected_first = serials(won_first, "first-round winner")
    ahead = serials(advanced, "runoff candidate")
    elected_runoff = serials([w.name for w in winners if w.runoff], "runoff winner")
    elected_first |= serials([w.name for w in winners if not w.runoff], "first-round winner")
    for number, id in options_by_round[1].items():
        kind = "elected" if number in elected_first else "advanced" if number in ahead else "eliminated"
        if 2 not in contests and kind == "advanced":
            kind = "lost"
        p.add("outcome", result_set_id=results[1], option_id=id, kind=kind)
    if 2 in contests:
        for local, id in options_by_round[2].items():
            kind = "elected" if serial_of[2][local] in elected_runoff else "lost"
            p.add("outcome", result_set_id=results[2], option_id=id, kind=kind)


def sector_code(name: str) -> str:
    text = norm(name)
    for code, word in (("99901", "القاهره"), ("99902", "الصعيد"), ("99903", "شرق"), ("99904", "غرب")):
        if word in text:
            return code
    raise ValueError(f"{name!r} names no list constituency")


def list_workbook(url: str) -> list[list[list[object]]]:
    return [
        [sheet.row_values(i) for i in range(sheet.nrows)]
        for sheet in xlrd.open_workbook(raw.cached(SOURCE, url).path).sheets()
    ]


def _list(b: Build, sector: str, members: list[tuple[str, Sub]]) -> None:
    p = b.p
    english, _, seats, stage = SECTORS[sector]
    area_id = area("sector", sector)
    unit = key("unit", "sector", sector)
    name = sectors()[sector]
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=EGYPT,
        scheme_code="eg.list_constituency",
        code=sector,
        area_relation="equals",
        area_id=area_id,
        kind="aggregate",
    )
    p.name(unit, name, "ar", "official")
    contest = key("contest", "list", sector)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION,
        kind="body_seats",
        body_id=HOUSE,
        district_area_id=area_id,
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM_LIST,
        default_result_kind="counted",
    )
    p.name(contest, f"2015 House of Representatives election, {english} list constituency", "en", "official")
    p.name(contest, name, "ar", "native", False)
    p.add("slug", path=f"{PATH}/list-{sector}", entity_id=contest, is_current=True)
    result_set = b.result_set(ELECTION, contest, f"list:{sector}")
    lists: dict[int, str] = {}
    for _, s in members:
        for number, label, _ in s.lists.rows if s.lists else []:
            if lists.setdefault(number, label) != label:
                raise ValueError(f"{sector}: list {number} is {lists[number]!r} and {label!r}")
    options_: dict[int, str] = {}
    for number, label in lists.items():
        id = options_[number] = key("option", "list", sector, number)
        party_id = key("party", "list", norm(label))
        if party_id not in b.parties:
            b.parties.add(party_id)
            p.add("party", id=party_id, kind="list_vehicle", polity_id=EGYPT)
            p.name(party_id, label, "ar", "official")
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="list",
            party_id=party_id,
            ballot_position=number,
            ballot_label=label,
        )
    by_label = {norm(label): number for number, label in lists.items()}
    for code, s in members:
        _tally(b, result_set, key("unit", "sub", code, s.number), s, s.lists, options_)
    widget = ROUND_ONE[stage]
    _residual(b, result_set, unit, declared(widget, sector), [s for _, s in members], "lists", f"list {sector}")
    # the members of every list, and the winning list's elected members
    rosters: dict[int, list[tuple[str, int, str, str]]] = {}
    for row in _rows(LIST_CANDIDATES)[1:]:
        if sector_code(str(row[1])) != sector:
            continue
        number = by_label.get(norm(row[2]))
        if number is None:
            b.issues.append(f"list {sector}: the roster list {row[2]!r} has no ballot line")
            continue
        rosters.setdefault(number, []).append(
            (str(row[3]).strip(), whole(row[4]), str(row[5]).strip(), str(row[6]).strip())
        )
    elected: list[str] = []
    winner_label = ""
    for url in (WINNERS["list1"], WINNERS["list2"]):
        for sheet in list_workbook(url):
            if sector_code(str(sheet[2][2])) == sector:
                winner_label = str(sheet[1][2]).strip()
                elected = [str(row[2]).strip() for row in sheet[8:] if str(row[0]).strip() == "أساسي"]
    if len(elected) != seats:
        raise ValueError(f"{sector}: {len(elected)} winners for {seats} seats")
    winner = by_label[norm(winner_label)]
    ids: dict[str, str] = {}
    for number, roster in rosters.items():
        mains = sum(1 for role, *_ in roster if role == "أساسي")
        for role, position, full, status in roster:
            main = role == "أساسي"
            person = key("person", "list", sector, number, role, position)
            b.person(person, full, "")
            candidacy = key("candidacy", "list", sector, number, role, position)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options_[number],
                role="list_member" if main else "substitute",
                list_position=position if main else mains + position,
                ballot_name=full,
            )
            ids[norm(full)] = candidacy
    for number, id in options_.items():
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=id,
            kind="elected" if number == winner else "lost",
            seats=seats if number == winner else None,
        )
    for full in elected:
        candidacy = ids.get(norm(full))
        if candidacy is None:
            b.issues.append(f"list {sector}: winner {full!r} is not on the roster")
            continue
        p.add("outcome", result_set_id=result_set, option_id=options_[winner], candidacy_id=candidacy, kind="elected")
