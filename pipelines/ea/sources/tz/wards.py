"""Reading General Notice No. 1547 of the Government Gazette (28 October 2016), the 2015 councillors of Mainland Tanzania.

The notice runs from printed page 104 to 492 (pages 58 to 446 of the PDF). It is one column of text per page: a region
heading (``MKOA WA ARUSHA``), a council heading (``HALMASHAURI YA JIJI LA ARUSHA``), then for each ward a ``KATA YA
<name>`` heading, the candidates with party and votes, and a line naming the candidate declared elected. The text layer
carries the typing mistakes of the printed notice (a heading without its ``KATA``, a party misspelled, a figure with a
letter in it), which are read here with the repairs named in the tables below.
"""

import difflib
import re
from dataclasses import dataclass, field
from functools import cache

from ea import raw
from ea.sources.tz import text

FIRST, LAST = 58, 446
PARTIES = tuple(text.PARTY_TAILS)
UNOPPOSED = re.compile(r"(?:\w*pita)?\s*Bila\s*Kupingwa\s*$", re.IGNORECASE)
FIGURE = re.compile(r"[S$]*(?P<votes>\d(?:[\d,]| (?=\d))*)\s*$")
DOTS = re.compile(r"\s*[.…,·\s]{3,}\s*|\s*…+\s*")
REGION = re.compile(r"^(?:MKOA\s+(?:WA\s+)?)?(?P<name>[A-Z ]+)$")
COUNCIL = re.compile(r"^HALA?MASHA?U?RI\b(?P<rest>.*)$")
KATA = re.compile(r"^(?:S|M)?(?:KATA|KAYA|KAT)\s*YA\s*(?P<name>[A-Z].*)$|^KATA\s+(?P<bare>(?!YA\b)[A-Z][A-Z\s’'/.-]*)$")
ELECTED = re.compile(r"^(?:Mgombea\s*)?aliy\w*gu\w*\s*(?:ni\s*)*(?P<rest>.*)$", re.IGNORECASE)
PAGE = text.PAGE


# Lines the notice prints between candidates that are neither a candidate nor a heading: a repeat of the ward's name
# or of the constituency, and a candidate printed without party or votes (the declared winner is another candidate).
IGNORED = {"SAME MASHARIKI", "RUNG’ABURE", "SEDEKO", "MISIGARO Evaristi Baleruye"}
# Candidate lines the notice prints with the ward's name in front, where the ward's heading is missing.
PREFIXED = {
    "NYASA LUMEME ": ["LUMEME"],
    "IGALULA MMALE ": ["MMALE"],
    "UPUGE ": ["UPUGE"],
    "BUHIGWEMUGERA ": ["MUGERA", ""],
}
# Ward headings the notice prints without ``KATA YA``, or with a stray word, as printed and the ward's name.
HEADINGS = {
    "ESILALEI": "ESILALEI",
    "CHEMBA KWA MTORO": "CHEMBA KWA MTORO",
    "MWENGE MTAPIKA": "MWENGE MTAPIKA",
    "MASHARIKI KATA YA MATULI": "MATULI",
    "KATA YA (NYAMALIMBE) IKANGALA": "IKANGALA",
    "KATAYA": "",
}


@dataclass
class Candidate:
    name: str
    party: str
    votes: int | None
    printed: str = ""


@dataclass
class Ward:
    region: str
    council: str
    name: str
    candidates: list[Candidate] = field(default_factory=list)
    winner: str = ""  # the name the notice declares elected, as printed
    winner_party: str = ""
    earlier: list[tuple[str, str, int | None]] = field(default_factory=list)  # an earlier print with other figures


@cache
def lines() -> list[str]:
    from ea.sources.tz import inec

    path = str(raw.cached(inec.SOURCE, inec.ELECTIONS[2015].url).path)
    return text.pdftext(path, FIRST, LAST).split("\n")


def party_of(word: str) -> str | None:
    """A party's own spelling from what the notice printed, which sometimes drops or swaps a letter."""
    if not word:
        return None
    shown = re.sub(r"[^A-Za-z]", "", word)
    for party in PARTIES:
        if shown.upper() == re.sub(r"[^A-Za-z]", "", party).upper():
            return party
    near = difflib.get_close_matches(shown.upper(), [re.sub(r"[^A-Za-z]", "", p).upper() for p in PARTIES], 1, 0.75)
    return next((p for p in PARTIES if near and re.sub(r"[^A-Za-z]", "", p).upper() == near[0]), None)


def split_label(label: str) -> tuple[str, str] | None:
    """The name and party of a ``NAME - PARTY`` or ``NAME (PARTY)`` label, or None."""
    label = re.sub(r"[()]", " ", label.replace("’", "'"))
    label = re.sub(r"\s+", " ", label).strip(" -–—.")
    found = text.split_party(label)
    if found:
        return found
    tail = re.match(r"^(?P<name>.*?)[\s–-]*(?P<party>[A-Za-z][A-Za-z\s.–-]*)$", label)
    if not tail:
        return None
    words = label.replace("–", "-").split()
    for take in (2, 1):
        party = party_of(" ".join(words[-take:]).strip("-"))
        if party and len(words) > take:
            return " ".join(words[:-take]).strip(" -–"), party
    return None


def candidate(line: str) -> Candidate | None:
    """A candidate line: name, party, and either votes or the note that the candidate was elected unopposed."""
    body = line.strip()
    unopposed = UNOPPOSED.search(body)
    votes: int | None = None
    if unopposed:
        body = body[: unopposed.start()]
    else:
        found = FIGURE.search(body)
        if not found:
            return None
        votes = int(re.sub(r"[, ]", "", found.group("votes")))
        body = body[: found.start()]
    label = DOTS.split(body, 1)[0] if DOTS.search(body) else body
    label = re.sub(r"\bCC\s+M\b", "CCM", label.replace("—", "-")).strip()
    split = split_label(label)
    if not split and unopposed and re.fullmatch(r"[A-Za-z'’.\s-]+", label):
        split = (text.clean(label), "")
    if not split or not split[0]:
        return None
    from ea.sources.tz import inec

    return Candidate(inec.tidy(split[0]), split[1], votes, line)


def council_name(rest: str) -> str:
    """A council's name from its heading, with the notice's typing mistakes put right: ``YA JIJI LA ARUSHA`` is JIJI LA ARUSHA."""
    rest = re.sub(r"\s+", " ", rest.replace("’", "'")).strip()
    rest = re.sub(r"\s*\((?:NKENGE|VWAWA)\)", "", rest)
    rest = re.sub(r"^(?:YA|LA)\s+", "", rest)
    rest = rest.replace("SWILAYA", "WILAYA").replace("DODODMA", "DODOMA").replace("KASULI", "KASULU")
    rest = re.sub(r"^WILAYA\s+(?:YA\s+)?(?:LA\s+)?", "WILAYA YA ", rest)
    rest = re.sub(r"^MJI\s+(?:WA\s+)?", "MJI WA ", rest)
    rest = re.sub(r"^MANISPAA\s+(?:YA\s+)?", "MANISPAA YA ", rest)
    return COUNCIL_FIXES.get(rest, rest)


def ward_name(name: str) -> str:
    """A ward's name from its heading, without the stray words and marks the notice prints in it."""
    name = re.sub(r"\(\s*MAJINA YA.*\)", "", name.replace("’", "'"))
    name = re.sub(r"^KATA YA\s+", "", name.strip())
    name = re.sub(r"\s*/\s*", "/", name)
    name = re.sub(r"[‘“”\"]+", "", name)
    return re.sub(r"\s+", " ", name).strip(" .")


# A heading the notice prints for one council that belongs with another, by the council the wards belong to.
COUNCIL_FIXES = {"WILAYA YA CHILONWA": "WILAYA YA CHAMWINO"}
# The notice prints the wards of Kongwa under the heading of Kondoa, from this ward on.
SPLITS = {("DODOMA", "WILAYA YA KONDOA", "CHAMKOROMA"): "WILAYA YA KONGWA"}


def unprefix(line: str, ward: str) -> str:
    """A candidate's line without the constituency and ward names the text layer sometimes prints in front of it."""
    pattern = r"\s*".join(re.escape(c) for c in ward.replace(" ", "").replace("'", "’"))
    found = re.match(rf"^(?:[A-Z’']+[\s-]*){{1,3}}?{pattern.replace('’', "[’']")}[\s-]*(?=[A-Z])", line)
    if found and found.group(0).strip() != ward and len(found.group(0)) < len(line) - 10:
        return line[found.end() :]
    return line


def parse() -> tuple[list[Ward], list[str]]:
    out: list[Ward] = []
    region = council = ""
    ward: Ward | None = None
    stray: list[str] = []
    started = False
    for raw_line in lines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or PAGE.search(line):
            continue
        if not started:
            started = "MATOKEO YA UCHAGUZI MKUU WA MADIWANI" in line
            continue
        if re.match(r"^Dar es Salaam,|^\.{3,}.*2015|Mkurugenzi wa Uchaguzi", line):
            continue
        found = text.region_of(re.sub(r"^MKOA\s+(?:WA\s+)?", "", line)) if line.startswith("MKOA") else None
        found = found or (text.region_header(line) if not (ward and ward.candidates and not ward.winner) else None)
        if found:
            region, ward = found, None
            continue
        heading = COUNCIL.match(line)
        if heading or re.match(r"^WILAYA\s*YA\b", line):
            council = council_name(heading.group("rest") if heading else line)
            ward = None
            continue
        if line in IGNORED:
            continue
        header = KATA.match(line)
        if line in HEADINGS:
            ward = Ward(region, council, HEADINGS[line])
            out.append(ward)
            continue
        if header:
            name = ward_name(header.group("name") or header.group("bare"))
            council = SPLITS.get((region, council, name), council)
            if (
                out
                and (out[-1].region, out[-1].council, out[-1].name) == (region, council, name)
                and not out[-1].winner
            ):
                ward = out[-1]  # the notice repeats a heading where a page breaks
                continue
            ward = Ward(region, council, name)
            out.append(ward)
            continue
        elected = ELECTED.match(line)
        if elected and ward:
            ward.winner = elected.group("rest")
            continue
        for prefix, names in PREFIXED.items():
            if line.startswith(prefix):
                if ward is None or ward.winner:
                    ward = Ward(region, council, names.pop(0) if len(names) > 1 else names[0])
                    out.append(ward)
                line = line[len(prefix) :]
        if ward and not ward.winner:
            line = unprefix(line, ward.name)
        cand = candidate(line)
        if cand and ward and ward.winner:
            ward = Ward(region, council, "")  # a block whose heading the notice leaves out
            out.append(ward)
        if cand and ward:
            ward.candidates.append(cand)
            continue
        stray.append(f"{region}/{council}/{ward.name if ward else ''}: {line}")
    return out, stray


def words(name: str) -> set[str]:
    return {
        t for t in re.split(r"[\s.\-–—]+", re.sub(r"(?i)ndugu", " ", name).casefold().replace("'", "")) if len(t) > 2
    }


def winner_of(ward: Ward) -> tuple[int | None, str]:
    """The candidate the notice declares elected, and how the notice's line was matched ('' when it matched by name and party)."""
    cands = ward.candidates
    if not ward.winner:
        return (0, "unopposed") if len(cands) == 1 else (None, "none printed")
    rest = re.sub(r"(?i)^\s*(?:ni\s*)+|^\s*-\s*", "", ward.winner).strip()
    rest = re.sub(r"(?i)^ndugu\s*", "", rest)
    split = split_label(rest)
    from ea.sources.tz import inec

    name, party = split if split else (rest, "")
    name = inec.tidy(name)
    named = [i for i, c in enumerate(cands) if party and c.party == party]
    if not named:
        named = list(range(len(cands)))
    best = max(named, key=lambda i: len(words(name) & words(cands[i].name)))
    overlap = len(words(name) & words(cands[best].name))
    if len(named) == 1 and party:
        return named[0], "" if overlap else "party only"
    if overlap:
        return best, "" if party else "name only"
    return None, f"unmatched {ward.winner!r}"


# Wards the notice prints twice, the second time under the wrong council: dropped, with the council and the first
# candidate's line that marks the copy.
STRAY_COPIES = {("MPANDA VIJIJINI", "BULAMATA", "KAVUU CHAMALENDI")}


def dedupe(found: list[Ward]) -> tuple[list[Ward], list[str]]:
    """Each ward once: a block printed twice keeps its later print when the two differ, and the notes say so."""
    notes: list[str] = []
    out: dict[tuple[str, str, str], Ward] = {}
    for ward in found:
        if not ward.candidates:
            notes.append(f"{ward.region}/{ward.council}/{ward.name or '(no heading)'}: no candidates printed")
            continue
        if any(
            ward.council.endswith(council) and ward.name == name and ward.candidates[0].printed.startswith(first)
            for council, name, first in STRAY_COPIES
        ):
            notes.append(f"{ward.region}/{ward.council}/{ward.name}: copy of another council's ward")
            continue
        if not ward.name:
            notes.append(f"{ward.region}/{ward.council}: block without a ward heading ({ward.candidates[0].name})")
            continue
        key = (ward.region, ward.council, ward.name)
        if key in out:
            old = out[key]
            same = {(c.party, c.votes) for c in old.candidates} == {(c.party, c.votes) for c in ward.candidates}
            if not same:
                ward.earlier = [(c.name, c.party, c.votes) for c in old.candidates]
                out[key] = ward
            continue
        out[key] = ward
    return list(out.values()), notes


# The notice prints one ward's block with two wards' candidates; the first two are the ward's own, the rest are dropped.
TRIMMED = {("WILAYA YA UKEREWE", "BUKIKO"): 2}


@cache
def read() -> tuple[tuple[Ward, ...], tuple[str, ...]]:
    """The notice's wards, and the blocks and lines left out of them."""
    parsed, stray = parse()
    odd = [line for line in stray if not line.startswith("//")]
    if odd:
        raise ValueError(f"lines that fit nothing: {odd[:5]}")
    found, dropped = dedupe(parsed)
    for ward in found:
        keep = TRIMMED.get((ward.council, ward.name))
        if keep:
            dropped.append(
                f"{ward.region}/{ward.council}/{ward.name}: candidates {[c.name for c in ward.candidates[keep:]]} "
                "belong to a ward whose heading is missing"
            )
            ward.candidates = ward.candidates[:keep]
    return tuple(found), tuple(dropped)


def wards() -> tuple[Ward, ...]:
    return read()[0]


def left_out() -> tuple[str, ...]:
    return read()[1]


import json
from datetime import date

import shapely
import shapely.geometry
from shapely.strtree import STRtree

from ea import changeset
from ea.package import Package

GB = "tz.geoboundaries"
GB_BASE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/TZA"
GB_DISTRICTS = f"{GB_BASE}/ADM2/geoBoundaries-TZA-ADM2_simplified.geojson"
GB_WARDS = f"{GB_BASE}/ADM3/geoBoundaries-TZA-ADM3_simplified.geojson"
DAY = date(2015, 10, 25)
VOTES = 1
EARLIER = (
    "The notice prints this ward twice, with other figures in the earlier print: {earlier}. The later print is stored."
)


def fetch() -> None:
    raw.fetch(GB, GB_DISTRICTS, suffix=".geojson")
    raw.fetch(GB, GB_WARDS, suffix=".geojson")


def key(kind: str, *parts: object) -> str:
    from ea.sources.tz import inec

    return inec.key(kind, *parts)


def shown(name: str) -> str:
    """A place's name in the capitals the notice prints, with the small words in lower case: JIJI LA ARUSHA."""
    from ea.sources.tz import inec

    return re.sub(r"\b(Ya|La|Wa|Na)\b", lambda m: m.group(1).lower(), inec.pretty(name))


def council_title(council: str) -> str:
    return f"Halmashauri ya {shown(council)}"


def council_area(region: str, council: str) -> str:
    return key("area", "tz.council", changeset.slug(region), changeset.slug(council))


def ward_area(region: str, council: str, ward: str) -> str:
    return key("area", "tz.ward", changeset.slug(region), changeset.slug(council), changeset.slug(ward))


def councils() -> dict[tuple[str, str], list[Ward]]:
    out: dict[tuple[str, str], list[Ward]] = {}
    for ward in wards():
        out.setdefault((ward.region, ward.council), []).append(ward)
    return out


def parties() -> set[str]:
    return {c.party for ward in wards() for c in ward.candidates if c.party}


def fold(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.casefold())


def polygon(geometry: object) -> shapely.MultiPolygon:
    shape = shapely.make_valid(shapely.geometry.shape(geometry))
    parts = [g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3]
    if not parts:
        parts = [
            p
            for g in shapely.get_parts(shape)
            if shapely.get_type_id(g) == 7
            for p in shapely.get_parts(g)
            if shapely.get_type_id(p) == 3
        ]
    return shapely.MultiPolygon(parts)


@cache
def shapes() -> tuple[
    dict[tuple[str, str], shapely.Geometry], dict[tuple[str, str, str], shapely.Geometry], dict[str, str]
]:
    """Council and ward shapes from geoBoundaries, matched by name inside the district each council's wards fall in."""
    districts = [
        (f["properties"]["shapeName"], polygon(f["geometry"]))
        for f in json.loads(raw.cached(GB, GB_DISTRICTS).path.read_text("utf-8"))["features"]
    ]
    units = [
        (f["properties"]["shapeName"], polygon(f["geometry"]))
        for f in json.loads(raw.cached(GB, GB_WARDS).path.read_text("utf-8"))["features"]
    ]
    tree = STRtree([g for _, g in districts])
    where: list[int] = []
    for _, g in units:
        point = g.representative_point()
        hits = [i for i in tree.query(point) if districts[i][1].contains(point)]
        where.append(hits[0] if hits else -1)
    exact: dict[tuple[str, int], list[int]] = {}
    for index, ((name, _), district) in enumerate(zip(units, where, strict=True)):
        exact.setdefault((fold(name), district), []).append(index)
    by_name: dict[str, set[int]] = {}
    for name, district in exact:
        by_name.setdefault(name, set()).add(district)
    home: dict[tuple[str, str], int] = {}
    for (region, council), items in councils().items():
        votes: dict[int, int] = {}
        for ward in items:
            for district in by_name.get(fold(ward.name), ()):
                votes[district] = votes.get(district, 0) + 1
        if votes:
            best = max(votes, key=votes.__getitem__)
            if votes[best] >= max(3, len(items) * 0.4):
                home[(region, council)] = best
    taken: set[int] = set()
    ward_shape: dict[tuple[str, str, str], shapely.Geometry] = {}
    for (region, council), items in councils().items():
        district = home.get((region, council))
        if district is None:
            continue
        for ward in items:
            hits = exact.get((fold(ward.name), district), [])
            if len(hits) == 1 and hits[0] not in taken:
                taken.add(hits[0])
                ward_shape[(region, council, ward.name)] = units[hits[0]][1]
    for (region, council), items in councils().items():
        district = home.get((region, council))
        if district is None:
            continue
        free = {i: fold(units[i][0]) for i, d in enumerate(where) if d == district and i not in taken}
        for ward in items:
            if (region, council, ward.name) in ward_shape or not free:
                continue
            ranked = sorted(
                ((difflib.SequenceMatcher(None, fold(ward.name), name).ratio(), i) for i, name in free.items()),
                reverse=True,
            )
            if ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.05):
                taken.add(ranked[0][1])
                free.pop(ranked[0][1])
                ward_shape[(region, council, ward.name)] = units[ranked[0][1]][1]
    council_shape: dict[tuple[str, str], shapely.Geometry] = {}
    owners: dict[int, list[tuple[str, str]]] = {}
    for key_, district in home.items():
        owners.setdefault(district, []).append(key_)
    for district, keys in owners.items():
        if len(keys) == 1:
            council_shape[keys[0]] = districts[district][1]
    for key_ in home:
        if key_ in council_shape:
            continue
        parts = [ward_shape[(*key_, w.name)] for w in councils()[key_] if (*key_, w.name) in ward_shape]
        if parts:
            council_shape[key_] = polygon(shapely.union_all(parts))
    district_names = {f"{r}/{c}": districts[d][0] for (r, c), d in home.items()}
    return council_shape, ward_shape, district_names


def geometry_row(area: str, shape: shapely.Geometry, source: str, quality: str) -> dict[str, object]:
    shape = shapely.set_srid(shape, 4326)
    return {
        "area_id": area,
        "geom": shapely.to_wkb(shape, hex=True, include_srid=True),
        "label_point": shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        "quality": quality,
        "source_id": source,
    }


GB_SOURCES = {
    "districts": key("source", "geoboundaries-adm2"),
    "wards": key("source", "geoboundaries-adm3"),
}


def geo() -> Package:
    from ea.sources.tz import inec

    p = Package(
        f"{inec.SOURCE}/geo-wards",
        inec.SOURCE,
        "Tanzania's councils (halmashauri) and wards (kata) of 2015, keyed by region, council, and ward name, with the "
        "shapes geoBoundaries holds for them",
        depends=[f"{inec.SOURCE}/geo"],
    )
    p.external |= {inec.TANZANIA, inec.TERRITORY}
    for code, rank, kind, noun in (
        ("tz.council", 3, "administrative", "council"),
        ("tz.ward", 4, "electoral_district", "ward"),
    ):
        p.add("scheme", code=code, polity_id=inec.TANZANIA, kind=kind, rank=rank, noun=noun)
    for layer, url, title, license, license_url in (
        (
            "districts",
            GB_DISTRICTS,
            "geoBoundaries gbOpen Tanzania ADM2 (districts), 2021",
            "CC BY 3.0 IGO (Tanzania National Bureau of Statistics and UN OCHA ROSA, through geoBoundaries)",
            "https://creativecommons.org/licenses/by/3.0/igo/",
        ),
        (
            "wards",
            GB_WARDS,
            "geoBoundaries gbOpen Tanzania ADM3 (wards), 2015",
            "ODbL 1.0 (OpenStreetMap contributors, through geoBoundaries)",
            "https://opendatacommons.org/licenses/odbl/1-0/",
        ),
    ):
        p.add(
            "source",
            id=GB_SOURCES[layer],
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab)",
            title=title,
            url="https://www.geoboundaries.org/",
            license=license,
            license_url=license_url,
            attribution="geoBoundaries (William & Mary geoLab); OpenStreetMap contributors"
            if layer == "wards"
            else "geoBoundaries (William & Mary geoLab); Tanzania National Bureau of Statistics; UN OCHA ROSA",
        )
        p.artifact(raw.cached(GB, url), GB_SOURCES[layer])
    for code in ("tz.council", "tz.ward"):
        p.add("boundary_set", id=key("boundary_set", code, "2015"), scheme_code=code, vintage="2015", as_of=DAY)
    council_shape, ward_shape, _ = shapes()
    for (region, council), items in councils().items():
        id = council_area(region, council)
        p.add("area", id=id, scheme_code="tz.council")
        p.add("area_code", area_id=id, code=f"{changeset.slug(region)}:{changeset.slug(council)}")
        p.add("area_parent", area_id=id, parent_id=inec.region_area(region))
        p.name(id, council_title(council), "sw", "official")
        if (region, council) in council_shape:
            p.add(
                "area_geometry",
                **geometry_row(id, council_shape[(region, council)], GB_SOURCES["districts"], "academic"),
            )
        for ward in items:
            wid = ward_area(region, council, ward.name)
            p.add("area", id=wid, scheme_code="tz.ward")
            p.add(
                "area_code",
                area_id=wid,
                code=f"{changeset.slug(region)}:{changeset.slug(council)}:{changeset.slug(ward.name)}",
            )
            p.add("area_parent", area_id=wid, parent_id=id)
            p.name(wid, shown(ward.name), "sw", "official")
            if (region, council, ward.name) in ward_shape:
                p.add(
                    "area_geometry",
                    **geometry_row(wid, ward_shape[(region, council, ward.name)], GB_SOURCES["wards"], "osm"),
                )
    return p


METHOD = (
    "Votes for each candidate in the ward, as the National Electoral Commission published them in General Notice No. "
    "1547 of the Government Gazette (28 October 2016). The notice prints no registered voters, ballots cast, or valid "
    "votes for the ward. The candidate the notice declares elected is stored as elected."
)
UNOPPOSED_METHOD = (
    "General Notice No. 1547 of the Government Gazette (28 October 2016) lists this candidate as elected without "
    "opposition (amepita bila kupingwa) and prints no votes. The candidate is stored as elected, with no counted votes."
)


def leader(ward: Ward) -> int:
    return max(range(len(ward.candidates)), key=lambda i: ward.candidates[i].votes or 0)


def build() -> Package:
    from ea.sources.tz import inec

    election = key("election", DAY)
    p = Package(
        f"{inec.SOURCE}/councillors-2015",
        inec.SOURCE,
        "The 2015 councillor elections of Mainland Tanzania, by ward, from the Government Gazette",
        depends=[
            f"{inec.SOURCE}/geo",
            f"{inec.SOURCE}/registry",
            f"{inec.SOURCE}/geo-wards",
            f"{inec.SOURCE}/assembly-2015",
        ],
    )
    p.external |= {inec.TANZANIA}
    p.uses(raw.cached(inec.SOURCE, inec.ELECTIONS[2015].url))
    mismatches: list[str] = []
    for (region, council), items in councils().items():
        body = key("body", "council", changeset.slug(region), changeset.slug(council))
        p.add("body", id=body, polity_id=inec.TANZANIA, kind="council")
        p.name(body, council_title(council), "sw", "official")
        for ward in items:
            if _contest(p, election, body, region, council, ward):
                mismatches.append(f"{shown(ward.name)} ({shown(council)})")
    if mismatches:
        p.accept(
            "outcomes.winner",
            "In these wards the notice declares elected a candidate other than the one it prints the most votes for, "
            f"and the declared candidate is stored as elected: {'; '.join(sorted(mismatches))}.",
        )
    return p


def _contest(p: Package, election: str, body: str, region: str, council: str, ward: Ward) -> bool:
    from ea.sources.tz import inec

    tag = f"{DAY}:{changeset.slug(region)}:{changeset.slug(council)}:{changeset.slug(ward.name)}"
    area = ward_area(region, council, ward.name)
    unopposed = len(ward.candidates) == 1 and ward.candidates[0].votes is None
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=inec.TANZANIA,
        scheme_code="tz.ward",
        code=tag,
        area_relation="equals",
        area_id=area,
        kind="standard",
    )
    p.name(unit, shown(ward.name), "sw", "official")
    p.add("reporting_unit_area", unit_id=unit, area_id=area, method="official_list", approximate=False, partial=False)
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body,
        district_area_id=area,
        magnitude=1,
        round_number=1,
        electoral_system_id=inec.SYSTEM,
        default_result_kind="tacit" if unopposed else "counted",
    )
    p.name(contest, f"2015 councillor election, {shown(ward.name)} ward, {shown(council)}", "en", "official")
    p.add(
        "slug",
        path=f"tz/{DAY}/councillors/{changeset.slug(region)}/{changeset.slug(council)}/{changeset.slug(ward.name)}",
        entity_id=contest,
        is_current=True,
    )
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if unopposed else "counted",
        source_id=inec.SOURCES[2015],
        status="active",
        is_default=True,
        completeness=0.0 if unopposed else 1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=UNOPPOSED_METHOD if unopposed else METHOD,
        lang="en",
    )
    index, how = winner_of(ward)
    caveats: list[str] = []
    if index is None:
        index = leader(ward)
        caveats.append(
            "The notice prints no line declaring the winner of this ward. The candidate with the most votes is stored "
            "as elected."
        )
    elif how.startswith(("party only", "name only")):
        caveats.append(
            f"The notice's line declaring the winner prints {ward.winner!r}, which matches {ward.candidates[index].name} "
            f"by {'party' if how.startswith('party') else 'name'} only. That candidate is stored as elected."
        )
    if ward.earlier:
        caveats.append(EARLIER.format(earlier="; ".join(f"{n} ({q}) {v}" for n, q, v in ward.earlier)))
    for number, caveat in enumerate(caveats):
        p.add(
            "note", id=key("note", result_set, number), entity_id=result_set, kind="caveat", body_md=caveat, lang="en"
        )
    for position, c in enumerate(ward.candidates, 1):
        person = key("person", tag, position)
        option = key("option", tag, position)
        party = inec.party_id(c.party) if c.party else None
        p.add("person", id=person)
        p.name(person, c.name, "sw", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party,
            is_independent=False,
        )
        if c.votes is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=c.votes,
            )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if position - 1 == index else "lost",
            seats=1 if position - 1 == index else None,
        )
    top = max((c.votes or 0 for c in ward.candidates), default=0)
    return len(ward.candidates) > 1 and (ward.candidates[index].votes or 0) != top
