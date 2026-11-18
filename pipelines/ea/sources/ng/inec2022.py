"""The Federal Capital Territory's area council elections of 12 February 2022 from the Commission's declaration of results.

The declaration is a 60-page scanned booklet with no text layer. Page 1 is the cover. Each area council's chairmanship
table (candidates with party logo, gender, and votes, then the registered, accredited, valid, rejected, and cast counts
under it) is followed by a page for each ward's councillorship, with candidates' votes and an "elected" mark. Wards have
no counts beyond the candidates' votes.

The numbers were read from the page images by eye and are kept in `inec2022.tsv`, one row per candidate: the page,
the contest (`CHAIR:<council>` or the ward name as printed), the party read from the logo, the name as printed, the
gender as printed, the votes, and `E` where the page prints the elected mark. Each number's box on its page comes from
the placement of the candidate's logo (the row it sits in) and the fixed layout of the totals tiles. The candidates'
votes are checked against each chairmanship's printed valid votes.
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import numpy as np
import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "ng.inec"
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNGA"))
URL = "https://www.inecnigeria.org/documents/resources/downloads/DECLARATION-OF-RESULTS-Layout-1.pdf"
DAY = date(2022, 2, 12)
KEY = f"{SOURCE}/{DAY}"
TABLE = Path(__file__).with_name("inec2022.tsv")
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
DIGITIZED = 8
DPI = 100

# The councils in the order the declaration prints them, with each one's number in the geo package.
COUNCILS = (("Abaji", 1), ("Abuja Municipal", 6), ("Bwari", 2), ("Gwagwalada", 3), ("Kuje", 4), ("Kwali", 5))
# The printed ward names against the 2019 report's ward codes (the geo package's ward areas), by council number.
WARDS = {
    1: {
        "Abaji Central": "01",
        "Abaji North East": "02",
        "Abaji South East": "03",
        "Agyana/Pandagi": "04",
        "Rimba/Ebagi": "05",
        "Nuku": "06",
        "Alu Mamagi": "07",
        "Yaba": "08",
        "Gurdi": "09",
        "Gawu": "10",
    },
    2: {
        "Bwari Central": "01",
        "Kuduru": "02",
        "Igu": "03",
        "Shere": "04",
        "Kawu": "05",
        "Ushafa": "06",
        "Dutsen Alhaji": "07",
        "Byazhin": "08",
        "Kubwa": "09",
        "Usuma": "10",
    },
    3: {
        "Gwagwalada Central": "01",
        "Kutunku": "02",
        "Staff Quarters": "03",
        "Ibwa": "04",
        "Dobi": "05",
        "Paiko": "06",
        "Tungan Maje": "07",
        "Zuba": "08",
        "Ikwa": "09",
        "Gwako": "10",
    },
    4: {
        "Kuje Central": "01",
        "Chibiri": "02",
        "Gaube": "03",
        "Kwaku": "04",
        "Kabi": "05",
        "Rubochi": "06",
        "Gwargwada": "07",
        "Gudun Karya": "08",
        "Kujekwa": "09",
        "Yenche": "10",
    },
    5: {
        "Kwali": "01",
        "Yangoji": "02",
        "Pai": "03",
        "Kilankwa": "04",
        "Dafa": "05",
        "Kundu": "06",
        "Ashara": "07",
        "Gumbo": "08",
        "Wako": "09",
        "Yebu": "10",
    },
    6: {
        "City Centre": "01",
        "Garki": "02",
        "Kabusa": "03",
        "Wuse": "04",
        "Gwarimpa": "05",
        "Jiwa": "06",
        "Gui": "07",
        "Karshi": "08",
        "Orozo": "09",
        "Karu": "10",
        "Nyanya": "11",
        "Gwagwa": "12",
    },
}
# Each chairmanship's printed counts: page of the tiles, then registered, accredited, valid, rejected, and cast.
TOTALS = {
    "Abaji": (2, 63_735, 11_522, 11_398, 107, 11_505),
    "Abuja Municipal": (9, 687_248, 35_010, 33_764, 911, 34_675),
    "Bwari": (23, 231_215, 22_706, 21_990, 656, 22_646),
    "Gwagwalada": (34, 169_803, 22_455, 21_037, 739, 21_776),
    "Kuje": (45, 127_444, 21_785, 21_186, 412, 21_598),
    "Kwali": (52, 90_401, 15_348, 15_114, 206, 15_320),
}
TURNOUT = {"Abaji": 18.08, "Abuja Municipal": 5.09, "Bwari": 9.82, "Gwagwalada": 13.22, "Kuje": 17.09, "Kwali": 16.98}
# The tiles under a chairmanship table, in points on the page: left and right edges of registered, accredited, valid,
# rejected, and cast votes. The vertical band comes from the page's pixels.
TILES = ((45.6, 100.0), (117.6, 172.0), (185.6, 240.0), (254.4, 308.0), (322.4, 380.8))
TILE_MEASURES = (REGISTERED, VOTERS, VALID, INVALID, CAST)
# A chairmanship row's votes column, in points.
CHAIR_VOTES = (270.0, 330.0)
METHOD = (
    "The Commission's declaration of results for the 12 February 2022 FCT area council elections is a scanned "
    "booklet. Each chairmanship page lists the candidates with party logo, gender, and votes, and the registered, "
    "accredited, valid, rejected, and cast counts; each ward's page lists the candidates with votes and marks the "
    "elected one. The numbers were read from the page images, and each is stored with its box on the page. The "
    "chairmanship pages print no ward breakdown, so a chairmanship's votes are the council total. Names are as "
    'printed, including a chairman of Abaji printed as "No Name".'
)
NOTES = {
    ("ward", "Staff Quarters"): 'The page prints "(Court Order)" beside the elected candidate\'s name.',
    ("ward", "Kilankwa"): "The page prints no elected mark for any candidate; no outcome is recorded.",
}


def path() -> Path:
    return raw.cached(SOURCE, URL).path


def fetched() -> raw.Artifact:
    return raw.cached(SOURCE, URL)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def shared(kind: str, *parts: object) -> str:
    """An id the geo and registry packages define, built the way `ea.sources.ng.inec` builds it."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def key(kind: str, *parts: object) -> str:
    return shared(kind, "2022", *parts)


def area(scheme: str, code: str) -> str:
    return shared("area", scheme, code)


def party(code: str) -> str:
    return shared("party", code)


@dataclass(frozen=True)
class Row:
    page: int
    unit: str
    party: str
    name: str
    gender: str
    votes: int
    elected: bool


def rows() -> list[Row]:
    found: list[Row] = []
    with TABLE.open(encoding="utf-8", newline="") as handle:
        for page, unit, party, name, gender, votes, elected in csv.reader(handle, delimiter="\t"):
            found.append(Row(int(page), unit, party, name, gender, int(votes), elected == "E"))
    return found


def contests(found: list[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = {}
    for row in found:
        grouped.setdefault(row.unit, []).append(row)
    return grouped


def checked(grouped: dict[str, list[Row]]) -> None:
    """Fail when the transcription disagrees with what the declaration says it holds."""
    chairs = [unit for unit in grouped if unit.startswith("CHAIR:")]
    if sorted(chairs) != sorted(f"CHAIR:{name}" for name, _ in COUNCILS):
        raise ValueError(f"chairmanships {chairs}")
    wards = {f"{name} Ward": (number, name) for number, names in WARDS.items() for name in names}
    if set(grouped) - set(chairs) != set(wards):
        raise ValueError(f"wards differ: {sorted(set(grouped) - set(chairs) ^ set(wards))}")
    for unit, candidates in grouped.items():
        if sum(c.elected for c in candidates) > 1:
            raise ValueError(f"{unit} marks more than one candidate elected")
        parties = [c.party for c in candidates]
        if len(set(parties)) != len(parties):
            raise ValueError(f"{unit} repeats a party")
    for name, (_, registered, accredited, valid, rejected, cast) in TOTALS.items():
        if valid + rejected != cast or accredited < cast or registered < accredited:
            raise ValueError(f"{name} counts disagree")


def page_boxes(file: Path, pages: set[int]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Each page's candidate logos as boxes in points, top to bottom."""
    found: dict[int, list[tuple[float, float, float, float]]] = {}
    with pymupdf.open(file) as document:
        for number in pages:
            page = document[number - 1]
            logos = {
                tuple(round(v, 1) for v in info["bbox"])
                for info in page.get_image_info()
                if 10 < info["bbox"][0] < 100 and info["bbox"][2] - info["bbox"][0] < 100 and 0 < info["bbox"][1] < 580
            }
            found[number] = sorted(logos, key=lambda box: box[1])
    return found


def tiles(file: Path, number: int, below: float) -> tuple[float, float]:
    """The vertical band, in points, of the dark green totals tiles lowest on a page and below a given height."""
    with pymupdf.open(file) as document:
        page = document[number - 1]
        scale = 90 / 72
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3).astype(int)
    green = (abs(image[:, :, 0]) < 12) & (abs(image[:, :, 1] - 77) < 12) & (abs(image[:, :, 2] - 40) < 12)
    wide = np.where(green[:, 40:500].sum(axis=1) > 150)[0]
    wide = wide[wide > below * scale]
    if not len(wide):
        raise ValueError(f"page {number} has no totals tiles")
    bottom = wide[-1]
    top = bottom
    while top - 1 in set(wide.tolist()):
        top -= 1
    return top / scale, (bottom + 1) / scale


def preview(file: Path, found: list[Row], out: Path) -> None:
    """Draw the boxes on the page images, for checking by eye."""
    out.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(file) as document:
        for number in sorted({row.page for row in found}):
            page = document[number - 1]
            for box in layout(file, found)[number]:
                page.draw_rect(pymupdf.Rect(*box), color=(1, 0, 0), width=0.8)
            page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(out / f"{number:02d}.png")


def layout(file: Path, found: list[Row]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Each candidate's box on its page, in points, in the order of `found`, grouped by page."""
    by_page: dict[int, list[Row]] = {}
    for row in found:
        by_page.setdefault(row.page, []).append(row)
    logos = page_boxes(file, set(by_page))
    boxes: dict[int, list[tuple[float, float, float, float]]] = {}
    with pymupdf.open(file) as document:
        width = document[0].rect.width
    for number, candidates in by_page.items():
        if len(logos[number]) != len(candidates):
            raise ValueError(f"page {number} has {len(logos[number])} logos for {len(candidates)} candidates")
        chair = candidates[0].unit.startswith("CHAIR:")
        found_boxes = []
        for (left, top, right, bottom), _ in zip(logos[number], candidates, strict=True):
            if chair:
                found_boxes.append((CHAIR_VOTES[0], top - 8, CHAIR_VOTES[1], bottom + 8))
            else:
                found_boxes.append((right + 2, top - 2, width - 20, bottom + 2))
        boxes[number] = found_boxes
    return boxes


def source_rows(p: Package) -> tuple[str, dict[int, str], int, int]:
    artifact = fetched()
    source = key("source", SOURCE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title="Declaration of Results: FCT Area Council Election, 12 February 2022",
        url=URL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    artifact_id = p.artifact(artifact, source)
    with pymupdf.open(path()) as document:
        size = document[0].rect
        pages = document.page_count
    width, height = round(size.width * DPI / 72), round(size.height * DPI / 72)
    ids = {}
    for number in range(2, pages):
        ids[number] = p.id("page", number)
        p.add(
            "source_page",
            id=ids[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
            width=width,
            height=height,
        )
    return source, ids, width, height


def build() -> Package:
    found = rows()
    grouped = contests(found)
    checked(grouped)
    p = Package(
        KEY,
        SOURCE,
        "The 12 February 2022 FCT area council elections: each council's chairmanship with candidates' votes and "
        "counts, and each ward's councillorship with candidates' votes, read from the Commission's scanned "
        "declaration of results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NIGERIA, COUNTRY}
    p.accept(
        "accounting.votes_valid",
        "The Bwari chairmanship table prints candidate votes that add up to 22,015 against 21,990 valid votes. Both "
        "are transcribed as printed; the table prints the same name and 109 votes on its ADC and ADP rows.",
    )
    source, pages, _, _ = source_rows(p)
    election_id = key("election")
    path_ = "ng/2022-02-12-area-council"
    p.add("election", id=election_id, polity_id=NIGERIA, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2022 FCT Area Council elections", "en", "official")
    p.add("slug", path=path_, entity_id=election_id, is_current=True)
    plurality = shared("electoral_system", "fptp")
    boxes = layout(path(), found)
    cursor: dict[int, int] = {}
    wards = {f"{name} Ward": (number, name) for number, names in WARDS.items() for name in names}
    for unit, candidates in grouped.items():
        if unit.startswith("CHAIR:"):
            council = unit.removeprefix("CHAIR:")
            number = dict(COUNCILS)[council]
            tag = f"chairman:{number}"
            district = area("ng.area_council", f"{number:02d}")
            name = f"2022 {council} Area Council chairmanship election"
            slug = f"{path_}/area-council-chairman/{changeset.slug(council)}"
            office, body, kind = shared("office", "chairman", number), None, "office"
            scheme, code = "ng.tally", f"{DAY}:chairman:{number:02d}"
            note = None
        else:
            number, ward = wards[unit]
            code_in = WARDS[number][ward]
            council = next(c for c, n in COUNCILS if n == number)
            tag = f"councillor:{number}:{code_in}"
            district = area("ng.ward", f"{number:02d}/{code_in}")
            name = f"2022 {council} Area Council councillorship election, {ward} ward"
            slug = f"{path_}/area-council-councillor/{changeset.slug(council)}/{code_in}-{changeset.slug(ward)}"
            office, body, kind = None, shared("body", "council", number), "body_seats"
            scheme, code = "ng.ward", f"{number:02d}/{code_in}"
            note = NOTES.get(("ward", ward))
        id = key("contest", tag)
        p.add(
            "contest",
            id=id,
            election_id=election_id,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=plurality,
            default_result_kind="counted",
        )
        p.name(id, name, "en", "official")
        p.add("slug", path=slug, entity_id=id, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD if note is None else f"{METHOD} {note}",
            lang="en",
        )
        unit_id = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=NIGERIA,
            scheme_code=scheme,
            code=code,
            area_relation="equals",
            area_id=district,
            kind="aggregate",
        )
        for position, c in enumerate(candidates, 1):
            page = c.page
            option = key("option", tag, c.party)
            person = key("person", tag, c.party)
            p.add("person", id=person, gender="male" if c.gender == "M" else "female")
            p.name(person, c.name, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="candidate",
                party_id=party(c.party),
                ballot_position=position if unit.startswith("CHAIR:") else None,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, c.party),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=party(c.party),
                is_independent=False,
            )
            index = cursor.get(page, 0)
            cursor[page] = index + 1
            left, top, right, bottom = boxes[page][index]
            fact(p, result_set, unit_id, option, VOTES, c.votes, pages[page], source, (left, top, right, bottom))
            if c.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)
        if unit.startswith("CHAIR:"):
            tile_page, *counts = TOTALS[council]
            low = max((box[3] for box in boxes.get(tile_page, [])), default=0.0)
            top, bottom = tiles(path(), tile_page, low)
            for measure, count, (left, right) in zip(TILE_MEASURES, counts, TILES, strict=True):
                fact(p, result_set, unit_id, None, measure, count, pages[tile_page], source, (left, top, right, bottom))
    return p


def fact(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    number: int,
    page: str,
    source: str,
    box: tuple[float, float, float, float],
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=DIGITIZED,
        value=number,
    )
    width, height = 419.527587890625, 595.2755737304688
    left, top, right, bottom = box
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page,
        source_id=source,
        x0=left / width,
        y0=top / height,
        x1=right / width,
        y1=bottom / height,
    )


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", [KEY])
