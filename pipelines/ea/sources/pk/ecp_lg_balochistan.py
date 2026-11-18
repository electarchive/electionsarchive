"""Balochistan's local government elections of 7 December 2013, from the Election Commission of Pakistan's
notification of the members returned to the union councils, municipal committees and corporations, the Quetta
Metropolitan Corporation, and the district councils.

The notification names each returned member, the council, and the ward, but prints no votes, so each contest
carries its winner only. Its tables are ruled: every row of a table is a band between vertical rules, and a council
cell that spans several rows has no horizontal rule between them. The parser rebuilds the grid from those rules and
reads each cell's words from the PDF's text layer."""

import re
from collections.abc import Iterator
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pk.ecp_lg_balochistan"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
DAY = date(2013, 12, 7)
PAGE_2013 = "https://ecp.gov.pk/local-government-elections-balochistan-2013"
RETURNED_2013 = "https://web.archive.org/web/20240706051051id_/https://ecp.gov.pk/storage/files/1/media-14.pdf"
GLANCE_2013 = "https://web.archive.org/web/20240706051044id_/https://ecp.gov.pk/storage/files/1/media-12.pdf"

KINDS = {
    "DISTRICT COUNCIL": "district_council",
    "UNION COUNCIL WISE": "union_council",
    "MUNCIPAL COMMITEE WISE": "municipal_committee",
    "MUNCIPAL CORPORATION WISE": "municipal_corporation",
    "METROPOLITAN CORPORATION WISE": "metropolitan_corporation",
}


class Cell(NamedTuple):
    text: str
    box: tuple[float, float, float, float]


class Row(NamedTuple):
    district: str
    kind: str
    council: str
    candidate: Cell
    ward: str
    page: int


class Band(NamedTuple):
    top: float
    bottom: float
    edges: list[float]


def fetch() -> None:
    for url in (RETURNED_2013, GLANCE_2013):
        raw.fetch(SOURCE, url, suffix=".pdf")


def _lines(page: pymupdf.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    vertical: list[tuple[float, float, float]] = []
    horizontal: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "re":
                r = item[1]
                vertical += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
                horizontal += [(r.y0, r.x0, r.x1), (r.y1, r.x0, r.x1)]
            elif item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.x - b.x) < 0.5 and abs(a.y - b.y) > 3:
                    vertical.append((a.x, min(a.y, b.y), max(a.y, b.y)))
                elif abs(a.y - b.y) < 0.5 and abs(a.x - b.x) > 3:
                    horizontal.append((a.y, min(a.x, b.x), max(a.x, b.x)))
    return vertical, horizontal


def _bands(vertical: list[tuple[float, float, float]]) -> list[Band]:
    spans: dict[tuple[float, float], list[float]] = {}
    for x, top, bottom in vertical:
        spans.setdefault((round(top * 2) / 2, round(bottom * 2) / 2), []).append(x)
    bands = []
    for (top, bottom), xs in sorted(spans.items()):
        edges: list[float] = []
        for x in sorted(xs):
            if not edges or x - edges[-1] > 3:
                edges.append(x)
        if len(edges) >= 4:
            bands.append(Band(top, bottom, edges))
    # A band nested inside a taller one (a rule drawn for one cell only) adds nothing.
    return [b for b in bands if not any(o is not b and o.top <= b.top and b.bottom <= o.bottom for o in bands)]


def _ruled(horizontal: list[tuple[float, float, float]], y: float, x: float) -> bool:
    return any(abs(hy - y) < 1.5 and x0 - 1 <= x <= x1 + 1 for hy, x0, x1 in horizontal)


def _text(words: list[tuple[float, float, float, float, str]]) -> str:
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if lines and abs(lines[-1][0][1] - w[1]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return " ".join(" ".join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in lines)


def _box(words: list[tuple[float, float, float, float, str]]) -> tuple[float, float, float, float]:
    return (min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words))


def _items(path: str) -> Iterator[tuple[int, str, list[Cell], bool]]:
    """Yield each page's title text and table rows from top to bottom: ("title", text) as a cell list of one, or a
    row's cells. The flag says whether the row's first cells continue the merged cells of the row above."""
    doc = pymupdf.open(path)
    for number in range(1, doc.page_count + 1):
        page = doc[number - 1]
        vertical, horizontal = _lines(page)
        bands = _bands(vertical)
        words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]
        placed: dict[int, list[list[tuple[float, float, float, float, str]]]] = {i: [] for i in range(len(bands))}
        loose: list[tuple[float, float, float, float, str]] = []
        for w in words:
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            for i, band in enumerate(bands):
                if band.top <= cy <= band.bottom and band.edges[0] - 1 <= cx <= band.edges[-1] + 1:
                    if not placed[i]:
                        placed[i] = [[] for _ in range(len(band.edges) - 1)]
                    column = max(0, min(len(band.edges) - 2, sum(1 for e in band.edges if e < cx) - 1))
                    placed[i][column].append(w)
                    break
            else:
                loose.append(w)
        events: list[tuple[float, int, str, list[Cell], bool]] = []
        if loose:
            groups: list[list[tuple[float, float, float, float, str]]] = []
            for w in sorted(loose, key=lambda w: w[1]):
                if groups and w[1] - groups[-1][-1][3] < 6:
                    groups[-1].append(w)
                else:
                    groups.append([w])
            for group in groups:
                events.append((group[0][1], 0, "title", [Cell(_text(group), _box(group))], False))
        previous: Band | None = None
        for i, band in enumerate(bands):
            cells = placed[i] or [[] for _ in range(len(band.edges) - 1)]
            joined = (
                previous is not None
                and abs(previous.bottom - band.top) < 1.5
                and not _ruled(horizontal, band.top, (band.edges[0] + band.edges[1]) / 2 + 1)
                and not _ruled(horizontal, band.top, (band.edges[1] + band.edges[2]) / 2)
            )
            row = [Cell(_text(c), _box(c)) if c else Cell("", (0, 0, 0, 0)) for c in cells]
            events.append((band.top, 1, "row", row, joined))
            previous = band
        for _, _, kind, cells, joined in sorted(events, key=lambda e: (e[0], e[1])):
            yield number, kind, cells, joined


def rows(path: str) -> list[Row]:
    """Read the notification's rows: one per returned member, with the district, the kind of council, the council
    (the union council a district council member represents), and the ward."""
    out: list[Row] = []
    district = kind = council = ""
    for number, what, cells, joined in _items(path):
        if what == "title":
            text = cells[0].text
            if "LIST OF RETURNED" in text:
                kind = next(v for k, v in KINDS.items() if k in text)
                found = re.search(r"DISTRICT (?:COUNCIL )?([A-Z][A-Z .]*?)\s*(?:,|BALOCHISTAN|$)", text)
                if found is None or _fold(found.group(1)) != _fold(district):
                    raise ValueError(f"page {number}: {text!r} does not name the district {district!r}")
                council = ""
            elif re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)*", text):
                district = text
            continue
        texts = [c.text for c in cells]
        if any("Name of the" in t or "returned candidate" in t for t in texts):
            continue
        if kind == "district_council":
            if len(cells) != 4:
                raise ValueError(f"page {number}: a district council row has {len(cells)} cells: {texts}")
            if not texts[1]:
                continue
            if not texts[0]:
                raise ValueError(f"page {number}: a district council row names no union council: {texts}")
            out.append(Row(district, kind, texts[0], cells[1], "", number))
            continue
        if len(cells) != 6:
            raise ValueError(f"page {number}: a {kind} row has {len(cells)} cells: {texts}")
        if texts[1]:
            council = texts[1]
        elif not council:
            raise ValueError(f"page {number}: a row names no council: {texts}")
        if not texts[2]:
            if texts[5]:
                raise ValueError(f"page {number}: a ward with no candidate: {texts}")
            continue
        if not texts[5]:
            raise ValueError(f"page {number}: a candidate with no ward: {texts}")
        out.append(Row(district, kind, council, cells[2], texts[5], number))
    return out


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# The district council lists spell these union councils differently from the union council lists.
SPELLINGS = {
    ("Chagai", "Aamuri"): "Amri",
    ("Kalat", "Toba"): "Tooba",
    ("Khuzdar", "Goneko"): "Goniko",
    ("Nasirabad", "Mir Behram Khan"): "Mir Behram Khan Buledi",
    ("Washuk", "Garang"): "Garrang",
}
URBAN = re.compile(r"munic|muncip|corporation|committee|commitee", re.IGNORECASE)
WARD = re.compile(r"(?:\bWard\.?\s*(?:No\.?|#)?|\bW\.?\s*No\.?)\s*(\d+)\b\.?", re.IGNORECASE)
LEAD = re.compile(r"^(\d+)\s*(?:[.,:)-]\s*|\s+|$)")
NOUNS = {
    "union_council": "union council",
    "municipal_committee": "municipal committee",
    "municipal_corporation": "municipal corporation",
    "metropolitan_corporation": "metropolitan corporation",
}


class Seat(NamedTuple):
    district: str
    kind: str
    council: str
    number: int | None
    ward: str
    occurrence: int
    candidate: str
    page: int
    box: tuple[float, float, float, float]


def _case(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text.isupper() else text


def _council_name(kind: str, text: str) -> str:
    text = _case(re.sub(r"Muncipal Commitee", "Municipal Committee", text))
    if kind == "union_council":
        return re.sub(r"^(?:Union Council|UC)\s+", "", text, flags=re.IGNORECASE)
    return text


def _ward(text: str) -> tuple[int | None, str]:
    """A ward cell's number and name. A leading number is the ward's number; a "Ward No." inside the name (Jiwani's
    "7. Gwatri Bazar Ward No.1") is part of the name."""
    text = re.sub(r"\s+", " ", text).strip()
    lead = LEAD.match(text)
    if lead:
        return int(lead.group(1)), text[lead.end() :].strip(" .,-:")
    found = WARD.search(text)
    if found:
        return int(found.group(1)), (text[: found.start()] + " " + text[found.end() :]).strip(" .,-:")
    return None, text.strip(" .,-:")


def seats(path: str) -> list[Seat]:
    """Every seat the notification fills. A council listed under an urban heading whose own name is not urban, and
    that the district's union council lists name, is a union council: the notification continues some districts'
    union council lists under the following heading (Kech, Lehri)."""
    found = rows(path)
    unions: dict[str, set[str]] = {}
    for row in found:
        if row.kind in ("union_council", "district_council"):
            name = SPELLINGS.get((row.district, row.council), row.council)
            unions.setdefault(row.district, set()).add(_fold(_council_name("union_council", name)))
    out: list[Seat] = []
    seen: dict[tuple[str, str, str, int | None, str], int] = {}
    for row in found:
        kind = row.kind
        council = SPELLINGS.get((row.district, row.council), row.council)
        if kind not in ("union_council", "district_council") and not URBAN.search(council):
            if _fold(_council_name("union_council", council)) in unions[row.district]:
                kind = "union_council"
            elif kind == "municipal_committee":
                council = f"Municipal Committee {council}"
            else:
                raise ValueError(f"{row.district}: {council!r} under a {kind} heading")
        name = _council_name("union_council" if kind == "district_council" else kind, council)
        number, ward = (None, "") if kind == "district_council" else _ward(row.ward)
        key = (row.district, kind, _fold(name), number, _fold(ward))
        occurrence = seen[key] = seen.get(key, -1) + 1
        out.append(
            Seat(
                row.district,
                kind,
                name,
                number,
                ward,
                occurrence,
                _case(row.candidate.text),
                row.page,
                row.candidate.box,
            )
        )
    return out


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECP_SOURCE = key("source", "ecp-lg-2013")
SYSTEM = key("electoral_system", "ward", "fptp")
UNION_SCHEME = "pk.ba_union_council"
WARD_SCHEME = "pk.ba_ward"


def council_area(district: str, kind: str, name: str) -> str:
    return key("area", UNION_SCHEME, district, kind, _fold(name))


def ward_area(seat: Seat) -> str:
    return key("area", WARD_SCHEME, seat.district, seat.kind, _fold(seat.council), seat.number, _fold(seat.ward))


def council_body(district: str, kind: str, name: str) -> str:
    return key("body", district, kind, _fold(name))


def body_name(seat: Seat) -> str:
    if seat.kind == "district_council":
        return f"District Council {seat.district}"
    if seat.kind == "union_council":
        return f"Union Council {seat.council}, {seat.district}"
    return seat.council if seat.district.lower() in seat.council.lower() else f"{seat.council}, {seat.district}"


def ward_name(seat: Seat) -> str:
    """The ward's name, or nothing when the notification prints only its number again ("1. Ward No.1")."""
    if seat.number is None:
        return _case(seat.ward)
    repeat = rf"(?:\bWard\.?\s*(?:No\.?)?|\bW\.?)\s*{seat.number}"
    name = re.sub(
        rf"[\s,]*{repeat}$", "", re.sub(rf"^{repeat}[\s,.]*", "", seat.ward, flags=re.IGNORECASE), flags=re.IGNORECASE
    )
    return _case(name.strip(" .,-:"))


def ward_label(seat: Seat) -> str:
    if seat.number is None:
        return ward_name(seat)
    return f"Ward {seat.number}" + (f" ({ward_name(seat)})" if ward_name(seat) else "")


def _path() -> str:
    return str(raw.cached(SOURCE, RETURNED_2013).path)


def geo() -> Package:
    p = Package(
        "pk.ecp_lg_balochistan/geo",
        SOURCE,
        "Balochistan's union councils, municipal committees and corporations, the Quetta Metropolitan Corporation, "
        "and their wards, as the Election Commission's notification of the 2013 local government results names them",
        depends=["pk.ecp_lg_balochistan/registry"],
    )
    p.external.add(PAKISTAN)
    p.uses(raw.cached(SOURCE, RETURNED_2013))
    p.add(
        "scheme",
        code=UNION_SCHEME,
        polity_id=PAKISTAN,
        kind="administrative",
        rank=4,
        noun="local council",
        kinds=NOUNS,
        code_system=None,
    )
    p.add(
        "scheme", code=WARD_SCHEME, polity_id=PAKISTAN, kind="electoral_district", rank=5, noun="ward", code_system=None
    )
    councils: dict[str, tuple[str, str, str]] = {}
    wards: dict[str, Seat] = {}
    for seat in seats(_path()):
        kind = "union_council" if seat.kind == "district_council" else seat.kind
        councils.setdefault(council_area(seat.district, kind, seat.council), (seat.district, kind, seat.council))
        if seat.kind != "district_council":
            wards.setdefault(ward_area(seat), seat)
    for id, (district, kind, name) in councils.items():
        p.add("area", id=id, scheme_code=UNION_SCHEME)
        p.add("area_kind", area_id=id, kind=kind, source_id=ECP_SOURCE)
        p.name(id, f"{name}, {district}" if kind == "union_council" else name, "en", "official")
    for id, seat in wards.items():
        p.add("area", id=id, scheme_code=WARD_SCHEME)
        p.add("area_parent", area_id=id, parent_id=council_area(seat.district, seat.kind, seat.council), valid_from=DAY)
        p.name(id, f"{ward_label(seat)}, {seat.council}", "en", "official")
    return p


def registry() -> Package:
    p = Package(
        "pk.ecp_lg_balochistan/registry",
        SOURCE,
        "The Election Commission of Pakistan's notification of Balochistan's 2013 local government results, the "
        "councils it fills, and their ward electoral system",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Local Government Elections Balochistan 2013: official result notification and elections at a glance",
        url=PAGE_2013,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in (RETURNED_2013, GLANCE_2013):
        p.artifact(raw.cached(SOURCE, url), ECP_SOURCE)
    bodies: dict[str, str] = {}
    for seat in seats(_path()):
        district = seat.kind == "district_council"
        body = (
            key("body", seat.district, "district_council")
            if district
            else council_body(seat.district, seat.kind, seat.council)
        )
        bodies.setdefault(body, body_name(seat))
    for body, name in bodies.items():
        p.add("body", id=body, polity_id=PAKISTAN, kind="council")
        p.name(body, name, "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under the Balochistan Local Government Act 2010, each ward of a union council, municipal committee, "
        "municipal corporation, or metropolitan corporation elects one general member by plurality, and each union "
        "council elects one general member of its district council the same way. Seats for women, peasants and "
        "workers, and non-Muslims, and the chairmen and vice chairmen, are filled afterwards by the elected members "
        "and are not part of these results.",
        lang="en",
    )
    return p


METHOD_2013 = (
    "From the Election Commission of Pakistan's official notification of the general members returned to Balochistan's "
    "local councils in the elections of 7 December 2013. The notification names the member returned for each ward and "
    "for each union council's seat on its district council, but prints no votes and does not say which members were "
    "returned unopposed, so each contest stores its winner only. The Commission's summary sheet for the elections "
    "counts 7,190 general seats, of which 2,509 were filled unopposed and 513 drew no candidate."
)


def election_2013() -> Package:
    p = Package(
        "pk.ecp_lg_balochistan/2013-12-07",
        SOURCE,
        "Balochistan's local government elections of 7 December 2013: the general member returned for every ward and "
        "district council seat, from the Election Commission's notification",
        depends=["pk.ecp_lg_balochistan/registry", "pk.ecp_lg_balochistan/geo"],
    )
    p.external.add(PAKISTAN)
    p.uses(raw.cached(SOURCE, RETURNED_2013))
    p.uses(raw.cached(SOURCE, GLANCE_2013))
    election = key("election", "2013")
    root = "pk/2013-12-07-balochistan-local"
    p.add("election", id=election, polity_id=PAKISTAN, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=root, entity_id=election, is_current=True)
    p.name(election, "2013 Balochistan local government elections", "en", "official")
    paths: set[str] = set()
    found = seats(_path())
    twice = {
        (s.district, s.kind, _fold(s.council), s.number, _fold(s.ward), s.occurrence) for s in found if s.occurrence
    }
    for seat in found:
        tag = (seat.district, seat.kind, _fold(seat.council), seat.number, _fold(seat.ward), seat.occurrence)
        contest = key("contest", *tag)
        if seat.kind == "district_council":
            body = key("body", seat.district, "district_council")
            area = council_area(seat.district, "union_council", seat.council)
            label = f"District Council {seat.district}, {seat.council} union council seat"
            path = f"{root}/{changeset.slug(seat.district)}/district-council/{changeset.slug(seat.council)}"
        else:
            body = council_body(seat.district, seat.kind, seat.council)
            area = ward_area(seat)
            label = f"{body_name(seat)}, {ward_label(seat)}"
            ward = f"ward-{seat.number}" if seat.number is not None else "ward"
            if ward_name(seat):
                ward += f"-{changeset.slug(ward_name(seat))}"
            council = changeset.slug(seat.council)
            if seat.kind == "union_council":
                council = f"uc-{council}"
            path = f"{root}/{changeset.slug(seat.district)}/{council}/{ward}"
        if seat.occurrence:
            path += f"-{seat.occurrence + 1}"
        if path in paths:
            raise ValueError(f"two contests share the path {path}")
        paths.add(path)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"2013 Balochistan local government elections, {label}", "en", "official")
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECP_SOURCE,
            status="active",
            is_default=True,
            completeness=0,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_2013, lang="en"
        )
        if (*tag[:-1], 1) in twice:
            p.add(
                "note",
                id=key("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md="The notification prints this ward twice, with a different member returned on each line; "
                "one of the two lines probably belongs to another ward of the same council. Both are stored as "
                "printed.",
                lang="en",
            )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=ECP_SOURCE,
            locator={"url": RETURNED_2013, "page": seat.page, "box": [round(v, 1) for v in seat.box]},
        )
        option = key("option", *tag)
        person = key("person", *tag)
        p.add("person", id=person)
        p.name(person, seat.candidate, "en", "official")
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=seat.candidate)
        p.add(
            "candidacy",
            id=key("candidacy", *tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=seat.candidate,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p


def parse() -> None:
    for build in (registry, geo, election_2013):
        print(build().write())


def dump(path: str) -> None:
    for number, kind, cells, joined in _items(path):
        print(number, kind, "+" if joined else " ", " | ".join(c.text for c in cells))
