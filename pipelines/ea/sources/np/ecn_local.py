"""Election Commission, Nepal: the 2017 (2074 BS) local elections from the seven provincial result books.

Each book prints, for every local level, its registered voters and every winner of the chief, deputy chief, ward
chair, and ward member posts, with the votes the winner received. The books print winners only, so each contest
holds its winners' votes and the declared outcomes, and no other candidate.

The books' text layers map many Devanagari glyphs to the wrong characters, so the text is read from glyph ids
through the embedded fonts (see `pdfglyphs`). Local level shapes come from geoBoundaries' ADM3 layer, matched by
province and name; a local level whose name does not match one shape with confidence is left without a shape."""

import functools
import json
import re
import ssl
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid5

import httpx
import numpy
import shapely
import shapely.geometry
from scipy.optimize import linear_sum_assignment

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.np import pdfglyphs

SOURCE = "np.ecn_local"
GEOSOURCE = "np.geoboundaries"
NEPAL = str(uuid5(package.NAMESPACE, "polity\x1fNP"))
BOOKS = "https://election.gov.np/ecn/uploads/userfiles/ElectionResultBook/LocalLevel/"
# The commission names the first book with a capital S and the other six with a small one.
BOOK_FILES = {1: "LLE_State_1.pdf", **{n: f"LLE_state_{n}.pdf" for n in range(2, 8)}}
# election.gov.np serves its certificate without the issuing intermediate; the issuer's own copy completes the
# chain, so verification stays on.
INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/NPL/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-NPL-ADM1_simplified.geojson"
ADM3 = GEOBOUNDARIES + "ADM3/geoBoundaries-NPL-ADM3_simplified.geojson"

GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
DISTRICT_GEO = "np.eci/geo"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def district_area(code: int) -> str:
    """A district area of the np.eci geo package, which this pipeline's local levels sit in."""
    return str(uuid5(package.NAMESPACE, "\x1f".join(["np.eci", "area", "np.district", str(code)])))


COMMISSION = key("source", "commission")
GEO_SOURCE = key("source", "geoboundaries")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
PARENT = key("election", "2017")
LOCALS_FROM = date(2017, 3, 10)  # 2073 Falgun 27: the 753 local levels took effect.

PHASES = {
    "2017-05-14": ((3, 4, 6), "२०७४ वैशाख ३१"),
    "2017-06-28": ((1, 5, 7), "२०७४ असार १४"),
    "2017-09-18": ((2,), "२०७४ असोज २"),
}
PROVINCES_EN = {1: "Koshi", 2: "Madhesh", 3: "Bagmati", 4: "Gandaki", 5: "Lumbini", 6: "Karnali", 7: "Sudurpashchim"}

# The district names as the books print them, with the Election Commission's district codes that np.eci uses.
DISTRICTS = {
    "ताप्लेजुङ": 1, "पाँचथर": 2, "इलाम": 3, "झापा": 4, "सङ्खुवासभा": 5, "तेह्रथुम": 6, "भोजपुर": 7, "धनकुटा": 8,
    "मोरङ": 9, "सुनसरी": 10, "सोलुखुम्बु": 11, "खोटाङ": 12, "ओखलढुङ्गा": 13, "उदयपुर": 14, "सप्तरी": 15,
    "सिराहा": 16, "दोलखा": 17, "रामेछाप": 18, "सिन्धुली": 19, "धनुषा": 20, "महोत्तरी": 21, "सर्लाही": 22,
    "रसुवा": 23, "धादिङ": 24, "नुवाकोट": 25, "काठमाडौं": 26, "भक्तपुर": 27, "ललितपुर": 28, "काभ्रेपलाञ्चोक": 29,
    "सिन्धुपाल्चोक": 30, "मकवानपुर": 31, "रौतहट": 32, "बारा": 33, "पर्सा": 34, "चितवन": 35, "गोरखा": 36,
    "मनाङ": 37, "लमजुङ": 38, "कास्की": 39, "तनहुँ": 40, "स्याङ्जा": 41, "गुल्मी": 42, "पाल्पा": 43,
    "अर्घाखांची": 44, "नवलपरासी (बर्दघाट सुस्ता पूर्व)": 45, "रुपन्देही": 46, "कपिलवस्तु": 47, "मुस्ताङ": 48,
    "म्याग्दी": 49, "बागलुङ": 50, "पर्वत": 51, "रुकुम पूर्व": 52, "रोल्पा": 53, "प्यूठान": 54, "सल्यान": 55,
    "दाङ": 56, "डोल्पा": 57, "मुगु": 58, "जुम्ला": 59, "कालिकोट": 60, "हुम्ला": 61, "जाजरकोट": 62, "दैलेख": 63,
    "सुर्खेत": 64, "बाँके": 65, "बर्दिया": 66, "बाजुरा": 67, "अछाम": 68, "बझाङ": 69, "डोटी": 70, "कैलाली": 71,
    "दार्चुला": 72, "बैतडी": 73, "डडेलधुरा": 74, "कन्चनपुर": 75, "नवलपरासी -बर्दघाट सुस्ता पश्चिम_": 77,
    "रुकुम पश्चिम": 78,
}  # fmt: skip
DISTRICTS_EN = {
    1: "Taplejung", 2: "Panchthar", 3: "Ilam", 4: "Jhapa", 5: "Sankhuwasabha", 6: "Terhathum", 7: "Bhojpur",
    8: "Dhankuta", 9: "Morang", 10: "Sunsari", 11: "Solukhumbu", 12: "Khotang", 13: "Okhaldhunga", 14: "Udayapur",
    15: "Saptari", 16: "Siraha", 17: "Dolakha", 18: "Ramechhap", 19: "Sindhuli", 20: "Dhanusha", 21: "Mahottari",
    22: "Sarlahi", 23: "Rasuwa", 24: "Dhading", 25: "Nuwakot", 26: "Kathmandu", 27: "Bhaktapur", 28: "Lalitpur",
    29: "Kavrepalanchok", 30: "Sindhupalchok", 31: "Makwanpur", 32: "Rautahat", 33: "Bara", 34: "Parsa",
    35: "Chitwan", 36: "Gorkha", 37: "Manang", 38: "Lamjung", 39: "Kaski", 40: "Tanahun", 41: "Syangja",
    42: "Gulmi", 43: "Palpa", 44: "Arghakhanchi", 45: "Nawalpur", 46: "Rupandehi", 47: "Kapilvastu",
    48: "Mustang", 49: "Myagdi", 50: "Baglung", 51: "Parbat", 52: "Eastern Rukum", 53: "Rolpa", 54: "Pyuthan",
    55: "Salyan", 56: "Dang", 57: "Dolpa", 58: "Mugu", 59: "Jumla", 60: "Kalikot", 61: "Humla", 62: "Jajarkot",
    63: "Dailekh", 64: "Surkhet", 65: "Banke", 66: "Bardiya", 67: "Bajura", 68: "Achham", 69: "Bajhang",
    70: "Doti", 71: "Kailali", 72: "Darchula", 73: "Baitadi", 74: "Dadeldhura", 75: "Kanchanpur", 77: "Parasi",
    78: "Western Rukum",
}  # fmt: skip

KINDS = {
    "metropolitan_city": ("महानगरपालिका", "Metropolitan City", "metropolitan city"),
    "sub_metropolitan_city": ("उपमहानगरपालिका", "Sub-Metropolitan City", "sub-metropolitan city"),
    "municipality": ("नगरपालिका", "Municipality", "municipality"),
    "rural_municipality": ("गाउँपालिका", "Rural Municipality", "rural municipality"),
}
SUFFIX = re.compile(r"\s*(उपमहानगरपालिका|महानगरपालिका|नगरपालिका|गा[उँ]+\s*पालिका?|गाँउपालिका)\s*$")

# The posts as the books print them: census class, seats, and the English title.
POSTS = {
    "प्रमुख": ("chief", 1, "Mayor"),
    "अध्यक्ष": ("chief", 1, "Chair"),
    "उपप्रमुख": ("deputy", 1, "Deputy Mayor"),
    "उपाध्यक्ष": ("deputy", 1, "Vice-Chair"),
    "वडा अध्यक्ष": ("ward_chair", 1, "ward chair"),
    "सदस्य": ("member", 2, "ward member"),
    "महिला सदस्य": ("woman", 1, "woman ward member"),
    "दलित महिला सदस्य": ("dalit_woman", 1, "Dalit woman ward member"),
}
WARD_POSTS = ("ward_chair", "member", "woman", "dalit_woman")
WARD_SLUGS = {
    "ward_chair": "chair",
    "member": "members",
    "woman": "woman-member",
    "dalit_woman": "dalit-woman-member",
}
INDEPENDENT = "स्वतन्त्र"
ELECTED = "निर्वाचित"
UNOPPOSED = "निर्विरोध"

# The national summary each book prints (section 10, स्थानीय तह निर्वाचन २०७४ सारांशमा): winners by post.
NATIONAL = {"chief": 293 + 460, "deputy": 293 + 460, "ward_chair": 6742, "woman": 6742, "dalit_woman": 6567}
NATIONAL_MEMBERS = 13484
NATIONAL_LOCALS = 753
NATIONAL_WARDS = 6743

METHOD = (
    "From the Election Commission's result book of the 2074 local elections for this province. The book prints "
    "each winner and the votes the winner received, and no other candidate, so this result holds the winners' votes "
    "and the declared outcome only. A winner returned unopposed is stored without a vote count. Registered voters "
    "are the local level's total as the book prints it at the head of its table."
)
LOCAL_METHOD = (
    "The 753 rural municipalities and municipalities (local levels) in force from 10 March 2017, as the Election "
    "Commission's 2074 local result books name them, and their wards. Local levels are numbered within their "
    "district in the order the books print them. Shapes are geoBoundaries' ADM3 local levels (Open Data Nepal, "
    "Ministry of Federal Affairs and General Administration, 2019), matched by province and name."
)

DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
JOINERS = re.compile("्[\\s‌‍]+(?=[क-हक़-य़])")
SPACED_SIGN = re.compile("\\s+(?=[\u0900-\u0903\u093a-\u094f])")
HEADER = re.compile(r"जि\S*ल्ला\s*:\s*(.+?)\s+स्थानीय तह\s*:\s*(.+?)\s+कूल मतदाता\s*:\s*([\d,]+)")
GENDERS = {"पु.": "male", "म.": "female"}
RESULT = re.compile(r"(\d[\d,]*)\s*(निर्वाचित|निर्विरोध)$")


def book_url(n: int) -> str:
    return BOOKS + BOOK_FILES[n]


def fetch() -> None:
    cert = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=cert.path.read_bytes())
    with httpx.Client(
        verify=context, headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=600
    ) as client:
        for n in BOOK_FILES:
            url = book_url(n)
            if url in raw.keys(SOURCE):
                continue
            response = client.get(url)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError(f"{url} is not a PDF")
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "application/pdf"),
            )
    for url in (ADM1, ADM3):
        raw.fetch(GEOSOURCE, url, suffix=".geojson")


# Reading the books.


@dataclass
class Cell:
    x: float
    x1: float
    text: str


@dataclass
class Line:
    page: int
    y: float
    cells: list[Cell]


def clean(text: str) -> str:
    """Text without the joiners, dotted circles, and stray spaces that broken clusters in the books leave."""
    text = text.replace("◌", "").replace("‌", "").replace("‍", "")
    text = SPACED_SIGN.sub("", " ".join(text.split()))
    text = text.replace("््", "्").replace("अा", "आ")
    return JOINERS.sub("्", text)


def text_of(reader: pdfglyphs.Reader, g: pdfglyphs.Glyph) -> str:
    if not g.font.two_byte:
        # Simple fonts carry the page numbers and the ASCII digits of the tables.
        return chr(g.gid) if 32 <= g.gid < 127 else " "
    return reader.char(g.font, g.gid)


def lines(path: Path, reader: pdfglyphs.Reader, pages: range | None = None) -> Iterator[Line]:
    """Each printed line of a book, as cells split at wide gaps, in the order the glyphs are drawn."""
    doc = pdfglyphs.Document(path.read_bytes())
    fonts: dict[int, pdfglyphs.Font] = {}
    for number, page in enumerate(doc.pages(), start=1):
        if pages is not None and number not in pages:
            continue
        drawn = list(pdfglyphs.glyphs(doc, page, fonts))
        for font in fonts.values():
            reader.remember(font)
        order = {id(g): i for i, g in enumerate(drawn)}
        drawn.sort(key=lambda g: g.y)
        groups: list[list[pdfglyphs.Glyph]] = []
        for g in drawn:
            if groups and g.y - groups[-1][-1].y < 3:
                groups[-1].append(g)
            else:
                groups.append([g])
        for group in groups:
            group.sort(key=lambda g: order[id(g)])
            cells: list[list[pdfglyphs.Glyph]] = [[group[0]]]
            for a, b in pairwise(group):
                if b.x - (a.x + a.width) > 6 or b.x < a.x - 30:
                    cells.append([b])
                else:
                    cells[-1].append(b)
            out = []
            for c in cells:
                text = clean(pdfglyphs.logical([text_of(reader, g) for g in c]))
                if text:
                    out.append(Cell(min(g.x for g in c), max(g.x + g.width for g in c), text))
            if out:
                yield Line(number, group[0].y, sorted(out, key=lambda c: c.x))


@dataclass(frozen=True)
class Row:
    book: int
    page: int
    district: str
    local: str
    registered: int
    serial: int
    post: str
    ward: int | None
    name: str
    age: int
    gender: str
    party: str
    votes: int | None
    remark: str


def rows(book: int, found: Iterator[Line]) -> Iterator[Row]:
    """The winner rows of a book's local level tables, with the local level each sits under."""
    district = local = ""
    registered = 0
    for line in found:
        cells = [Cell(c.x, c.x1, c.text.translate(DIGITS)) for c in line.cells]
        head = HEADER.search(" ".join(c.text for c in cells))
        if head:
            district, local = clean(head.group(1)), clean(head.group(2))
            registered = int(head.group(3).replace(",", ""))
            continue
        if not local or len(cells) < 6 or not cells[0].text.isdigit():
            continue
        g = next((i for i, c in enumerate(cells) if c.text in GENDERS), None)
        if g is None or g < 3 or not cells[g - 1].text.isdigit():
            raise ValueError(f"book {book} page {line.page}: no age and sex in {[c.text for c in cells]}")
        post = cells[1].text
        if post not in POSTS:
            raise ValueError(f"book {book} page {line.page}: unknown post {post!r}")
        rest = cells[2 : g - 1]
        ward = None
        if rest and rest[0].text.isdigit():
            ward = int(rest[0].text)
            rest = rest[1:]
        tail = cells[g + 1 :]
        if len(tail) < 2:
            raise ValueError(f"book {book} page {line.page}: no party or votes in {[c.text for c in cells]}")
        # The votes and the remark close the row, in one cell or two; the party is the first cell after the sex.
        m = RESULT.search(" ".join(c.text for c in tail[1:]))
        if m is None:
            raise ValueError(f"book {book} page {line.page}: no votes in {[c.text for c in tail]}")
        votes, remark = int(m.group(1).replace(",", "")), m.group(2)
        yield Row(
            book,
            line.page,
            district,
            local,
            registered,
            int(cells[0].text),
            post,
            ward,
            clean(" ".join(c.text for c in rest)),
            int(cells[g - 1].text),
            GENDERS[cells[g].text],
            tail[0].text,
            None if remark == UNOPPOSED else votes,
            remark,
        )


@functools.cache
def winners() -> tuple[Row, ...]:
    reader = pdfglyphs.Reader()
    # Books 4 to 7 embed whole fonts; books 1 to 3 embed Kalimati without its GSUB table and borrow it.
    order = [4, 5, 6, 7, 1, 2, 3]
    out: list[Row] = []
    for n in order:
        path = raw.cached(SOURCE, book_url(n)).path
        out.extend(rows(n, lines(path, reader)))
    lost = {k: v for k, v in reader.missing.items() if k.startswith("Kalimati")}
    if lost:
        raise ValueError(f"glyphs without text: {lost}")
    return tuple(sorted(out, key=lambda r: (r.book, r.page, r.serial)))


# The places.


@dataclass(frozen=True)
class Local:
    province: int
    district: int
    number: int
    name: str
    kind: str
    registered: int

    @property
    def code(self) -> str:
        return f"{self.district}-{self.number:02d}"

    @property
    def area(self) -> str:
        return key("area", "np.local", self.code)

    @property
    def base(self) -> str:
        return SUFFIX.sub("", self.name)


def kind_of(name: str) -> str:
    for kind in ("sub_metropolitan_city", "metropolitan_city", "municipality"):
        if name.endswith(KINDS[kind][0]):
            return kind
    if SUFFIX.search(name):
        return "rural_municipality"
    raise ValueError(f"no local level kind in {name!r}")


@functools.cache
def locals_() -> dict[tuple[str, str], Local]:
    """Each local level, keyed by the district and name the books print, numbered in the books' order."""
    out: dict[tuple[str, str], Local] = {}
    count: Counter[int] = Counter()
    for r in winners():
        if (r.district, r.local) in out:
            if out[(r.district, r.local)].registered != r.registered:
                raise ValueError(f"{r.local} prints two registered totals")
            continue
        district = DISTRICTS[r.district]
        count[district] += 1
        out[(r.district, r.local)] = Local(r.book, district, count[district], r.local, kind_of(r.local), r.registered)
    return out


@functools.cache
def wards() -> dict[str, list[int]]:
    """The wards of each local level, by local level code."""
    found: dict[str, set[int]] = defaultdict(set)
    for r in winners():
        if r.ward is not None:
            found[locals_()[(r.district, r.local)].code].add(r.ward)
    out = {}
    for code, numbers in found.items():
        top = max(numbers)
        # A ward that elected no one at all would leave a gap; none does.
        if numbers != set(range(1, top + 1)):
            raise ValueError(f"{code} has wards {sorted(numbers)}")
        out[code] = list(range(1, top + 1))
    return out


def ward_area(local: Local, ward: int) -> str:
    return key("area", "np.ward", f"{local.code}-{ward:02d}")


CONSONANTS = dict(
    zip(
        "कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहळ",
        [
            "k",
            "kh",
            "g",
            "gh",
            "ng",
            "ch",
            "chh",
            "j",
            "jh",
            "n",
            "t",
            "th",
            "d",
            "dh",
            "n",
            "t",
            "th",
            "d",
            "dh",
            "n",
            "p",
            "ph",
            "b",
            "bh",
            "m",
            "y",
            "r",
            "l",
            "b",
            "s",
            "s",
            "s",
            "h",
            "l",
        ],
        strict=True,
    )
)
VOWELS = dict(zip("अआइईउऊएऐओऔऋ", ["a", "a", "i", "i", "u", "u", "e", "ai", "o", "au", "ri"], strict=True))
SIGNS = dict(zip("ािीुूेैोौृंँः", ["a", "i", "i", "u", "u", "e", "ai", "o", "au", "ri", "n", "n", "-"], strict=True))


def latin(text: str) -> str:
    """A rough Latin spelling of a Devanagari name, used to match names and to build slugs."""
    out: list[str] = []
    chars = list(text)
    for i, c in enumerate(chars):
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in CONSONANTS:
            out.append(CONSONANTS[c])
            final = nxt in ("", " ", "-") or not ("ऀ" <= nxt <= "ॿ")
            if nxt not in SIGNS and nxt not in ("्", "़") and not final:
                out.append("a")
        elif c in VOWELS:
            out.append(VOWELS[c])
        elif c in SIGNS:
            out.append(SIGNS[c].strip("-"))
        elif c.isascii() and (c.isalnum() or c == " "):
            out.append(c)
        elif c in " -":
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _fold(text: str) -> str:
    text = re.sub(r"[^a-z]", "", text.lower())
    for a, b in (("w", "b"), ("v", "b"), ("sh", "s"), ("aa", "a"), ("ee", "i"), ("oo", "u"), ("ou", "au")):
        text = text.replace(a, b)
    return text


def _likeness(a: str, b: str) -> float:
    def skeleton(s: str) -> str:
        return re.sub(r"[aeiou]", "", s)

    return 0.5 * SequenceMatcher(None, a, b).ratio() + 0.5 * SequenceMatcher(None, skeleton(a), skeleton(b)).ratio()


def _features(url: str) -> list[dict[str, Any]]:
    return json.loads(raw.cached(GEOSOURCE, url).path.read_text(encoding="utf-8"))["features"]


def _polygons(geometry: dict[str, Any]) -> shapely.MultiPolygon:
    shape = shapely.make_valid(shapely.geometry.shape(geometry))
    parts = [g for g in shapely.get_parts(shape) if g.geom_type in {"Polygon", "MultiPolygon"}]
    flat = [p for g in parts for p in shapely.get_parts(g)]
    return shapely.set_srid(shapely.MultiPolygon(flat), 4326)


MATCH_FLOOR = 0.7


@functools.cache
def shapes() -> tuple[dict[str, tuple[str, shapely.MultiPolygon]], list[Local]]:
    """geoBoundaries ADM3 outlines with their English names by local level code, and the local levels left out.

    A local level takes a shape when, within its province, the two are each other's closest name and the
    likeness of the Latin spellings reaches MATCH_FLOOR, and when no other local level of the province has the
    same name."""
    provinces = [_polygons(f["geometry"]) for f in _features(ADM1)]
    features = [
        f
        for f in _features(ADM3)
        if not re.search(r"National Park|Reserve|Development Area|Watershed", f["properties"]["shapeName"])
    ]
    outlines = [_polygons(f["geometry"]) for f in features]

    def province(shape: shapely.MultiPolygon) -> int:
        point = shapely.point_on_surface(shape)
        hits = [i + 1 for i, p in enumerate(provinces) if p.contains(point)]
        return hits[0] if len(hits) == 1 else 0

    where = [province(s) for s in outlines]
    names = [_fold(f["properties"]["shapeName"]) for f in features]
    places = sorted(locals_().values(), key=lambda x: x.code)
    spelled = [_fold(latin(x.base)) for x in places]
    same = Counter((x.province, s) for x, s in zip(places, spelled, strict=True))
    score = numpy.array(
        [
            [_likeness(s, n) if where[j] == x.province else -1.0 for j, n in enumerate(names)]
            for x, s in zip(places, spelled, strict=True)
        ]
    )
    rows_, cols = linear_sum_assignment(-score)
    out: dict[str, tuple[str, shapely.MultiPolygon]] = {}
    for i, j in zip(rows_, cols, strict=True):
        x = places[i]
        mutual = score[i].argmax() == j and score[:, j].argmax() == i
        if score[i, j] >= MATCH_FLOOR and mutual and same[(x.province, spelled[i])] == 1:
            out[x.code] = (str(features[j]["properties"]["shapeName"]), outlines[j])
    return out, [x for x in places if x.code not in out]


def english(local: Local) -> str:
    matched = shapes()[0].get(local.code)
    return f"{matched[0] if matched else latin(local.base).title()} {KINDS[local.kind][1]}"


def local_slug(local: Local) -> str:
    return f"{changeset.slug(DISTRICTS_EN[local.district])}/{changeset.slug(english(local))}"


# The packages.


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{day}" for day in PHASES)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in PHASES:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def commission(p: Package) -> None:
    p.add(
        "source",
        id=COMMISSION,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="स्थानीय तह निर्वाचन, २०७४: निर्वाचन परिणाम पुस्तक (local election result books, one per province)",
        url=BOOKS,
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Nepal's 753 local levels and their wards as of 2017, from the Election Commission's 2074 local result books, "
        "with geoBoundaries ADM3 outlines",
        depends=[DISTRICT_GEO],
    )
    p.external |= {NEPAL}
    commission(p)
    for n in BOOK_FILES:
        p.artifact(raw.cached(SOURCE, book_url(n)), COMMISSION)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Open Data Nepal, Ministry of Federal Affairs and General Administration)",
        title="geoBoundaries gbOpen Nepal ADM1 and ADM3, 2019",
        url="https://www.geoboundaries.org/",
        license="CC BY-SA 4.0 (ADM3); CC BY 3.0 IGO (ADM1)",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from Open Data Nepal and the Survey Department of Nepal",
    )
    for url in (ADM1, ADM3):
        p.artifact(raw.cached(GEOSOURCE, url), GEO_SOURCE)
    p.add(
        "scheme",
        code="np.local",
        polity_id=NEPAL,
        kind="administrative",
        rank=3,
        noun="local level",
        kinds={kind: noun for kind, (_, _, noun) in KINDS.items()},
        code_system=None,
    )
    p.add("scheme", code="np.ward", polity_id=NEPAL, kind="administrative", rank=4, noun="ward", code_system=None)
    p.add(
        "boundary_set",
        id=key("boundary_set", "np.local", 2017),
        scheme_code="np.local",
        vintage="2017",
        valid_from=LOCALS_FROM,
        as_of=date(2017, 5, 14),
    )
    matched, _ = shapes()
    for local in sorted(locals_().values(), key=lambda x: x.code):
        a = local.area
        p.add("area", id=a, scheme_code="np.local")
        p.add("area_code", area_id=a, code=local.code, valid_from=LOCALS_FROM)
        p.add("area_parent", area_id=a, parent_id=district_area(local.district), valid_from=LOCALS_FROM)
        p.add("area_kind", area_id=a, kind=local.kind, valid_from=LOCALS_FROM, source_id=COMMISSION)
        p.name(a, local.name, "ne", "official")
        p.name(a, english(local), "en", "official", False)
        p.add("note", id=key("note", a), entity_id=a, kind="methodology", body_md=LOCAL_METHOD, lang="en")
        if local.code in matched:
            shape = matched[local.code][1]
            p.add(
                "area_geometry",
                area_id=a,
                valid_from=LOCALS_FROM,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=GEO_SOURCE,
            )
        for ward in wards()[local.code]:
            w = ward_area(local, ward)
            p.add("area", id=w, scheme_code="np.ward")
            p.add("area_code", area_id=w, code=f"{local.code}-{ward:02d}", valid_from=LOCALS_FROM)
            p.add("area_parent", area_id=w, parent_id=a, valid_from=LOCALS_FROM)
            p.name(w, f"{local.name} वडा नं. {ward}", "ne", "official")
            p.name(w, f"{english(local)} ward {ward}", "en", "official", False)
    return p


def party(name: str) -> str:
    return key("party", name)


def assembly(local: Local) -> str:
    return key("body", "assembly", local.code)


def office(local: Local, post: str) -> str:
    return key("office", post, local.code)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The 2017 Nepal local elections, the parties that won seats in them, each local level's assembly and its "
        "chief, deputy chief, and ward chair offices, and the electoral systems",
        depends=[GEO],
    )
    p.external |= {NEPAL}
    for n in BOOK_FILES:
        p.uses(raw.cached(SOURCE, book_url(n)))
    p.add(
        "election",
        id=PARENT,
        polity_id=NEPAL,
        kind="local",
        date_from=date(2017, 5, 14),
        date_to=date(2017, 9, 18),
        original_text="स्थानीय तह निर्वाचन, २०७४",
        status="held",
    )
    p.name(PARENT, "2017 Nepal local elections", "en", "official")
    p.name(PARENT, "स्थानीय तह निर्वाचन, २०७४", "ne", "native", False)
    p.add("slug", path="np/2017-local", entity_id=PARENT, is_current=True)
    p.add(
        "note",
        id=key("note", PARENT),
        entity_id=PARENT,
        kind="methodology",
        body_md=(
            "Held in three phases: on 14 May 2017 in Provinces 3, 4, and 6, on 28 June 2017 in Provinces 1, 5, and "
            "7, and on 18 September 2017 in Province 2. Each phase elected the chiefs and deputy chiefs of its local "
            "levels and the chair and four members of each ward."
        ),
        lang="en",
    )
    for id, formula, method in (
        (FPTP, "fptp", "One seat, won by the candidate with the most votes."),
        (BLOCK, "block", "Two open ward member seats; each voter marks two candidates, and the two with the most win."),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEPAL,
            valid_from=date(2015, 9, 20),
            valid_to=None,
            rules={"formula": formula},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in sorted({r.party for r in winners()} - {INDEPENDENT}):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=NEPAL)
        p.name(id, name, "ne", "official")
    for local in sorted(locals_().values(), key=lambda x: x.code):
        en = english(local)
        rural = local.kind == "rural_municipality"
        body = assembly(local)
        p.add("body", id=body, polity_id=NEPAL, kind="council")
        p.name(body, f"{en} {'Village' if rural else 'Municipal'} Assembly", "en", "official")
        p.name(body, f"{local.base} {'गाउँ' if rural else 'नगर'} सभा", "ne", "native", False)
        for post, kind, ne, title in (
            ("chief", "mayor", "अध्यक्ष" if rural else "प्रमुख", "Chair" if rural else "Mayor"),
            ("deputy", "deputy_mayor", "उपाध्यक्ष" if rural else "उपप्रमुख", "Vice-Chair" if rural else "Deputy Mayor"),
            ("ward_chair", "other", "वडा अध्यक्ष", "Ward chair"),
        ):
            id = office(local, post)
            p.add("office", id=id, polity_id=NEPAL, body_id=None, kind=kind)
            p.name(id, f"{title}, {en}", "en", "official")
            p.name(id, f"{local.name} {ne}", "ne", "native", False)
    return p


def election(day: str) -> Package:
    provinces, bs = PHASES[day]
    when = date.fromisoformat(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} phase of Nepal's 2017 local elections (Provinces {', '.join(map(str, provinces))}): each local "
        "level's chief and deputy chief and each ward's chair and members, winners only, from the result books",
        depends=[GEO, REGISTRY],
    )
    p.external |= {NEPAL}
    for n in provinces:
        p.uses(raw.cached(SOURCE, book_url(n)))
    el = key("election", day)
    path = f"np/{day}-local"
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        parent_id=PARENT,
        kind="local",
        date_from=when,
        date_to=when,
        original_text=bs,
        status="held",
    )
    p.name(el, f"2017 Nepal local elections, {when:%-d %B} phase", "en", "official")
    p.name(el, f"स्थानीय तह निर्वाचन, {bs}", "ne", "native", False)
    p.add("slug", path=path, entity_id=el, is_current=True)
    groups: dict[tuple[str, str, int | None], list[Row]] = defaultdict(list)
    for r in winners():
        if r.book in provinces:
            local = locals_()[(r.district, r.local)]
            groups[(local.code, POSTS[r.post][0], r.ward)].append(r)
    by_code = {x.code: x for x in locals_().values()}
    units: set[str] = set()
    unnamed: set[str] = set()
    for (code, post, ward), group in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        local = by_code[code]
        seats = POSTS[group[0].post][1]
        if len(group) > seats:
            raise ValueError(f"{local.name} {post} {ward}: {len(group)} winners for {seats} seats")
        unopposed = [r.remark == UNOPPOSED for r in group]
        if any(unopposed) and not all(unopposed):
            raise ValueError(f"{local.name} {post} {ward}: unopposed and counted winners together")
        result_kind = "tacit" if all(unopposed) else "counted"
        tag = f"{code}:{post}:{ward or 0}"
        contest = key("contest", day, tag)
        en = english(local)
        if ward is None:
            area = local.area
            title = POSTS[group[0].post][2]
            p.add(
                "contest",
                id=contest,
                election_id=el,
                kind="office",
                office_id=office(local, post),
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=FPTP,
                default_result_kind=result_kind,
            )
            p.name(contest, f"{title} of {en}", "en", "official")
            p.name(contest, f"{local.name} {group[0].post}", "ne", "native", False)
            rural = local.kind == "rural_municipality"
            post_slug = {"chief": ("mayor", "chair"), "deputy": ("deputy-mayor", "vice-chair")}[post][rural]
            slug = f"{path}/{local_slug(local)}/{post_slug}"
        else:
            area = ward_area(local, ward)
            title = POSTS[group[0].post][2]
            if post == "ward_chair":
                p.add(
                    "contest",
                    id=contest,
                    election_id=el,
                    kind="office",
                    office_id=office(local, post),
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=FPTP,
                    default_result_kind=result_kind,
                )
            else:
                p.add(
                    "contest",
                    id=contest,
                    election_id=el,
                    kind="body_seats",
                    body_id=assembly(local),
                    district_area_id=area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=BLOCK if seats > 1 else FPTP,
                    default_result_kind=result_kind,
                )
            p.name(contest, f"{en} ward {ward} {title}", "en", "official")
            p.name(contest, f"{local.name} वडा नं. {ward} {group[0].post}", "ne", "native", False)
            slug = f"{path}/{local_slug(local)}/ward-{ward}/{WARD_SLUGS[post]}"
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        rs = key("result_set", day, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=contest,
            stage="official",
            kind=result_kind,
            source_id=COMMISSION,
            status="active",
            is_default=True,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", code if ward is None else f"{code}-{ward:02d}")
        if unit not in units:
            units.add(unit)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEPAL,
                scheme_code="np.local" if ward is None else "np.ward",
                code=f"2017:{code}" if ward is None else f"2017:{code}-{ward:02d}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=when,
                valid_to=when,
            )
        if ward is None and result_kind == "counted":
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=2,
                count_round=0,
                flags=0,
                value=local.registered,
            )
        for r in group:
            option = key("option", day, tag, r.serial)
            solo = r.party == INDEPENDENT
            member = None if solo else party(r.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_label=r.name or None,
            )
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
            if r.name:
                person = key("person", day, tag, r.serial)
                p.add("person", id=person, gender=r.gender)
                p.name(person, r.name, "ne", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, tag, r.serial),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=r.name,
                    member_of_party_id=member,
                    is_independent=solo,
                )
            elif rs not in unnamed:
                unnamed.add(rs)
                p.add(
                    "note",
                    id=key("note", rs, "unnamed"),
                    entity_id=rs,
                    kind="caveat",
                    body_md="The result book prints this local level's table without its name column, so the "
                    "winner is stored with the party and votes the book gives, and without a name.",
                    lang="en",
                )
            if r.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=r.votes,
                )
    return p


def verify() -> dict[str, object]:
    """The parsed winners against the national summary the books print."""
    posts: Counter[str] = Counter(POSTS[r.post][0] for r in winners())
    found = {post: posts[post] for post in NATIONAL}
    return {
        "winners": dict(found),
        "printed": NATIONAL,
        "members": (posts["member"], NATIONAL_MEMBERS),
        "local_levels": (len(locals_()), NATIONAL_LOCALS),
        "wards": (sum(len(w) for w in wards().values()), NATIONAL_WARDS),
        "unopposed": sum(r.remark == UNOPPOSED for r in winners()),
        "unmatched_shapes": [f"{x.code} {x.name}" for x in shapes()[1]],
    }
