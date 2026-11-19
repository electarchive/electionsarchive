"""India's 2024 Lok Sabha general election by polling station, from the returning officers' Form 20.

Form 20 is the final result sheet of a parliamentary constituency. Its Part I is one sheet per assembly segment, one
row per polling station: the votes of each candidate and of "none of the above" (NOTA) recorded on the station's
voting machine, the total of valid votes, the rejected votes, the total, and the tendered votes. Its Part II is one
sheet per constituency: the same columns totalled for each assembly segment, then the postal ballots, then the grand
total. Each State's Chief Electoral Officer posts the sheets; this module reads Bihar's (ceobihar.nic.in), from the
captures the Internet Archive holds of them, since the office's site refuses requests from outside India.

The sheets are scans. Each page was transcribed from its image by a multimodal model and the transcription is checked
here against the sheet's own arithmetic: in every row the candidates' votes sum to the printed total of valid votes,
and the valid votes, rejected votes, and NOTA sum to the printed total; every column of a segment sums to the total
row that ends its Part I; that total row equals the segment's row on Part II; and Part II's segments and postal row
sum to its grand total. A row or column that misses any of these was read again from a 300 dpi crop. The transcription
is `form20_br.json`, and each figure is stored with its page and the box of its row on the page, where the page's
ruling could be traced to exactly one band per row, and the whole page otherwise.

Each constituency's result set holds the polling stations of every segment whose Part I is archived, the segment
totals of Part II for each segment whose Part I is not, and the postal ballots as their own unit. So every set adds
up to the constituency's grand total. Form 20 names no party and declares no winner, so neither is stored here.
"""

import itertools
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast
from urllib.parse import unquote
from uuid import uuid5

import polars as pl
from PIL import Image

from ea import package, raw, scan
from ea.package import Package
from ea.raw import Artifact
from ea.store import DATA

SOURCE = "in.form20"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
INBOX = DATA / "inbox" / SOURCE
PAGES = DATA / "pages" / SOURCE
GRID = DATA / "parsed" / SOURCE / "grid"
TRANSCRIPTS = Path(__file__).parent

# The Lok Sabha and its first-past-the-post system are the rows the statistical-reports pipeline wrote; the
# constituencies and assembly segments are the areas of the 2008 plan the geography pipeline wrote.
_STAT = Package("in.eci_stat", "in.eci_stat", "ids")
_GEO = Package("in.geo", "in.geo", "ids")
BODY = _STAT.id("body", "lok-sabha")
FPTP = _STAT.id("electoral_system", "fptp")
PS_SCHEME = "in.ps"
AC_SCHEME = "in.ac"
PC_SCHEME = "in.pc"

VOTES, BALLOTS, INVALID = 1, 3, 5
ALL, POSTAL = 0, 3
DIGITIZED = 8
WHOLE = (0.0, 0.0, 1.0, 1.0)
DAY_FIRST, DAY_LAST = date(2024, 4, 19), date(2024, 6, 1)
COUNTED = datetime(2024, 6, 4, 23, 59)
PDF = "application/pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "form20")
ELECTION = key("election", 2024)


@dataclass(frozen=True)
class State:
    """A State: its ISO 3166-2 code, its name, its code in the Local Government Directory, the office that posts its
    Form 20, and the first phase the State polled in and the last."""

    iso: str
    name: str
    lgd: str
    office: str
    url: str


STATES = {
    "BR": State(
        "BR",
        "Bihar",
        "10",
        "Chief Electoral Officer, Bihar",
        "https://ceobihar.nic.in/GELS2024Form20_Part1_Part2.html",
    ),
}


def pc_area(state: State, number: int) -> str:
    return _GEO.id("area", PC_SCHEME, f"{state.lgd}{number:02d}", "2008")


def ac_area(state: State, number: int) -> str:
    return _GEO.id("area", AC_SCHEME, f"{state.lgd}{number:03d}", "2008")


# ---- the raw files ---------------------------------------------------------------------------------------------------


def _index() -> dict[str, dict[str, str]]:
    return json.loads((INBOX / "index.json").read_text())


def fetch() -> None:
    """Store the captures of the sheets, which the archive's own machines fetched, under the URLs they came from."""
    have = raw.keys(SOURCE)
    for url, entry in _index().items():
        if not any(state.url.split("/")[2] in url for state in STATES.values()):
            continue
        where = entry.get("source", url)
        if where in have:
            continue
        stored = raw.store(
            SOURCE,
            where,
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        if stored.sha256 != entry["sha256"]:
            raise ValueError(f"{url}: the stored file's hash {stored.sha256} differs from the index's")


def artifact(url: str) -> Artifact:
    return raw.cached(SOURCE, _index()[url].get("source", url))


# ---- the transcription -----------------------------------------------------------------------------------------------


@dataclass
class Columns:
    """Where the counts sit among a sheet's numeric cells: the candidates first, then the named columns."""

    candidates: int
    nota: int
    rejected: int | None
    total: int

    @staticmethod
    def of(candidates: list[str], extra: list[str]) -> Columns:
        names = [e.lower() for e in extra]

        def find(*words: str) -> int | None:
            for i, name in enumerate(names):
                if any(word in name for word in words):
                    return len(candidates) + i
            return None

        nota = find("nota", "none of")
        total = next(
            (len(candidates) + i for i, name in enumerate(names) if name.strip() in {"total", "total votes"}), None
        )
        if nota is None or total is None:
            raise ValueError(f"a sheet lacks a NOTA or total column: {extra}")
        return Columns(len(candidates), nota, find("reject"), total)

    def check(self, cells: list[int], where: str) -> None:
        valid = sum(cells[: self.candidates])
        rejected = cells[self.rejected] if self.rejected is not None else 0
        if valid + cells[self.nota] + rejected != cells[self.total]:
            raise ValueError(f"{where}: the votes, NOTA, and rejected votes do not sum to the total {cells}")

    def counts(self, cells: list[int]) -> list[tuple[int | None, int, int]]:
        """(candidate column or None for an accounting figure, measure, value) for each stored figure; the NOTA
        column is the option after the candidates."""
        out: list[tuple[int | None, int, int]] = [(i, VOTES, cells[i]) for i in range(self.candidates)]
        out.append((self.candidates, VOTES, cells[self.nota]))
        out.append((None, BALLOTS, cells[self.total]))
        if self.rejected is not None:
            out.append((None, INVALID, cells[self.rejected]))
        return out


def transcripts(state: State) -> dict[str, dict]:
    return json.loads((TRANSCRIPTS / f"form20_{state.iso.lower()}.json").read_text())


def segment_number(label: str) -> int:
    found = re.match(r"\s*(\d+)", label)
    if not found:
        raise ValueError(f"a Part II row has no segment number: {label!r}")
    return int(found.group(1))


def part1_pc(url: str) -> int:
    found = re.search(r"Part_1/(\d+)-", url)
    if not found:
        raise ValueError(url)
    return int(found.group(1))


# Constituencies some of whose Part I sheets are archived but whose Part II is not: without Part II's postal ballots
# and the segments whose Part I is missing, their sets would not add up to the constituency, so they are left out.
NO_PART_II = {"BR": [4]}


def serials(printed: list[str], form: dict) -> dict[str, str | None]:
    """The unit code of each printed serial. A station whose machine was counted in the special round (SR) is printed
    twice: once in its place, marked with an asterisk and all zeros, and again at the end as `<n>(SR)` with its votes.
    The marked row is dropped and the SR row takes the station's number."""
    plain = {re.sub(r"\s+", "", s).upper(): s for s in printed}
    found: dict[str, str | None] = {}
    rows = {str(row[0]): row for page in form["pages"] for row in page["rows"]}
    special = {re.sub(r"\(SR\d*\)$", "", c) for c in plain if re.search(r"\(SR\d*\)$", c)}
    for clean, raw_serial in plain.items():
        base = re.sub(r"\(SR\d*\)$|\*$|#$", "", clean)
        if re.search(r"\(SR\d*\)$", clean):
            found[raw_serial] = base
        elif base in special:
            if any(rows[raw_serial][1:]):
                raise ValueError(f"segment {form.get('segment')}: station {raw_serial} has votes and an SR row")
            found[raw_serial] = None
        else:
            found[raw_serial] = base
    codes = [c for c in found.values() if c is not None]
    if len(codes) != len(set(codes)):
        raise ValueError(f"segment {form.get('segment')}: two rows map to one station code")
    return found


TITLE_SMALL = {"OF", "AND", "THE"}


def title(text: str) -> str:
    words = []
    for word in " ".join(text.replace("\n", " ").split()).split(" "):
        if "." in word and len(word) <= 3:
            words.append(word.upper())
        elif word.upper() in TITLE_SMALL and words:
            words.append(word.lower())
        else:
            words.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


# ---- page images and the boxes of rows -------------------------------------------------------------------------------


def page_image(source: Artifact, page: int) -> tuple[Path, int, int]:
    """The page at 150 dpi as a greyscale JPEG named for the file's hash and the page number, and its size."""
    image = PAGES / f"{source.sha256}-{page}.jpg"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["pdftoppm", "-r", "150", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(source.path), f"{folder}/p"],
                check=True,
            )
            Image.open(f"{folder}/p.png").save(image, quality=70, optimize=True)
    width, height = Image.open(image).size
    return image, width, height


def bands(source: Artifact, page: int) -> tuple[list[tuple[int, int]], list[int], int, int]:
    """The horizontal bands between the rules of the page's largest ruled table, its column rules, and the size of
    the 300 dpi image they were traced on; cached, since they derive from the raw file alone."""
    cache = GRID / f"{source.sha256}-{page}.json"
    if not cache.exists():
        GRID.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            image = scan.rendered(source.path, page, Path(folder))
            width, height = Image.open(image).size
            found = scan.tables(image)
        rows, columns = max(found, key=lambda t: len(t[0]) * len(t[1])) if found else ([], [])
        cache.write_text(json.dumps({"rows": rows, "columns": columns, "width": width, "height": height}))
    grid = json.loads(cache.read_text())
    rows = cast(list[int], grid["rows"])
    return list(itertools.pairwise(rows)), grid["columns"], grid["width"], grid["height"]


def row_boxes(source: Artifact, page: int, count: int, cells: int) -> list[tuple[float, float, float, float]] | None:
    """The box of each of the page's `count` table rows, as fractions of the page, when the traced ruling has exactly
    one plausible band for each; None when it does not, and the page's figures then cite the whole page."""
    found, columns, width, height = bands(source, page)
    if not found or len(columns) < cells:
        return None
    heights = sorted(b - a for a, b in found)
    usual = heights[len(heights) // 2]
    data = [band for band in found if 0.6 * usual <= band[1] - band[0] <= 1.7 * usual]
    if len(data) == count - 1:
        # The rule under the last row is often lost at the sheet's edge or under a signature.
        data.append((data[-1][1], data[-1][1] + usual))
    if len(data) != count:
        # More bands than rows (a total row, a footnote) or fewer leave the alignment ambiguous.
        return None
    left, right = columns[0] / width, columns[-1] / width
    return [(round(left, 4), round(a / height, 4), round(right, 4), round(min(b, height) / height, 4)) for a, b in data]


# ---- the packages ----------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/2024-{iso.lower()}" for iso in STATES)]


def parse() -> None:
    registry().write()
    for state in STATES.values():
        election(state).write()


def emit() -> None:
    package.run("load", packages())


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Chief Electoral Officers of the States of India",
        title="Form 20, final result sheets of the 2024 general election to the House of the People",
        url=STATES["BR"].url,
        license="Official publication of the Election Commission of India and the States' Chief Electoral Officers",
        license_url="https://www.eci.gov.in/",
        attribution="Returning officers' Form 20, as posted by each State's Chief Electoral Officer",
        notes="Read from the Internet Archive's captures of the Chief Electoral Officers' sites.",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The 2024 Indian general election to the Lok Sabha and the polling-station scheme of Form 20",
    )
    p.external.add(INDIA)
    source_row(p)
    p.uses(artifact(STATES["BR"].url))
    p.add(
        "scheme",
        code=PS_SCHEME,
        polity_id=INDIA,
        kind="precinct",
        rank=3,
        noun="polling station",
        code_system=None,
    )
    p.add(
        "election",
        id=ELECTION,
        polity_id=INDIA,
        kind="general",
        date_from=DAY_FIRST,
        date_to=DAY_LAST,
        date_precision="day",
        status="held",
    )
    p.name(ELECTION, "2024 Indian general election", "en", "official")
    p.name(ELECTION, "General Elections, 2024 (18th Lok Sabha)", "en", "short", False)
    p.add("slug", path="in/2024-general", entity_id=ELECTION, is_current=True)
    p.add(
        "note",
        id=key("note", ELECTION),
        entity_id=ELECTION,
        kind="methodology",
        body_md="The eighteenth Lok Sabha general election, polled in seven phases from 19 April to 1 June 2024 and "
        "counted on 4 June 2024.",
        lang="en",
    )
    return p


METHOD = (
    "The returning officer's Form 20 (final result sheet) for the constituency, as {office} posted it, read from "
    "the Internet Archive's capture of each scanned sheet. Each figure was transcribed from the page image and checked "
    "against the sheet's arithmetic: each polling station's votes, NOTA, and rejected votes sum to its printed total, "
    "each segment's columns sum to the total row of its Part I, which equals the segment's row on Part II, and Part "
    "II's segments and postal ballots sum to its grand total. Polling stations are numbered as Form 20 numbers them; "
    "the votes at a station are those recorded on its voting machine (with any VVPAT slip count the sheet "
    "substitutes), and postal ballots are counted for the constituency as a whole. The total of valid votes the sheet "
    "prints leaves out NOTA, so it is not stored; the total and the rejected votes are. Tendered votes are not "
    "counted and are not stored. Form 20 names no party and declares no winner."
)


def election(state: State) -> Package:
    p = Package(
        f"{SOURCE}/2024-{state.iso.lower()}",
        SOURCE,
        f"The 2024 Lok Sabha election in {state.name} by polling station, from the returning officers' Form 20",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {INDIA, BODY, FPTP, AC_SCHEME, PC_SCHEME}
    data = transcripts(state)
    part1: dict[tuple[int, int], tuple[str, dict]] = {}
    for url, sheet in data["part1"].items():
        part1[(part1_pc(url), int(sheet["segment"]))] = (url, sheet)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    stations = 0
    for url, sheet in sorted(data["part2"].items(), key=lambda item: int(item[1]["pc_number"])):
        stations += constituency(p, state, url, sheet, part1, values, locators)
    covered = {int(s["pc_number"]) for s in data["part2"].values()}
    if sorted({pc for pc, _ in part1} - covered) != sorted(NO_PART_II[state.iso]):
        raise ValueError(f"Part I sheets without their Part II: {sorted({pc for pc, _ in part1} - covered)}")
    schema = {"option_id": pl.String}
    p.frame(
        "result_value",
        pl.DataFrame(values, schema_overrides=schema).with_columns(
            channel=pl.col("channel").cast(pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(locators, schema_overrides=schema).with_columns(
            channel=pl.col("channel").cast(pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )
    return p


class Pages:
    """The source_page rows of one sheet, added as figures cite them."""

    def __init__(self, p: Package, url: str) -> None:
        self.p = p
        self.source = artifact(url)
        self.artifact = p.artifact(self.source, SOURCE_ID)
        self.ids: dict[int, str] = {}

    def page(self, number: int) -> str:
        if number not in self.ids:
            image, width, height = page_image(self.source, number)
            self.ids[number] = key("page", self.source.sha256, number)
            self.p.add(
                "source_page",
                id=self.ids[number],
                artifact_id=self.artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        return self.ids[number]


def constituency(
    p: Package,
    state: State,
    url: str,
    sheet: dict,
    part1: dict[tuple[int, int], tuple[str, dict]],
    values: list[dict[str, object]],
    locators: list[dict[str, object]],
) -> int:
    number = int(sheet["pc_number"])
    name = title(re.sub(r"\s*\(\s*(SC|ST)\s*\)\s*$", "", re.sub(r"\(\d+\)", "", sheet["pc_name"])).strip())
    tag = f"2024:{state.iso}:{number}"
    columns = Columns.of(sheet["candidates"], sheet["extra_columns"])
    n = columns.candidates
    rows = [(row[0], [int(x) for x in row[1:]]) for row in sheet["segments"]]
    total, postal, grand = (list(map(int, sheet[k])) for k in ("total", "postal", "grand_total"))
    for label, cells in rows:
        columns.check(cells, f"PC {number} Part II {label}")
    sums = [sum(cells[i] for _, cells in rows) for i in range(len(total))]
    if sums != total or [a + b for a, b in zip(total, postal, strict=True)] != grand:
        raise ValueError(f"PC {number}: Part II's segments and postal row do not sum to its totals")

    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=ELECTION,
        kind="body_seats",
        body_id=BODY,
        district_area_id=pc_area(state, number),
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
    )
    p.external.add(pc_area(state, number))
    p.name(contest, f"2024 Lok Sabha election, {name} ({state.name})", "en", "official")
    p.add("slug", path=f"in/2024-general/{state.iso.lower()}/{number}", entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=ELECTION,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
        as_of=COUNTED,
    )
    options: list[str] = []
    for position, label in enumerate(sheet["candidates"], start=1):
        option, person = key("option", tag, position), key("person", tag, position)
        ballot = title(label)
        p.add("person", id=person)
        p.name(person, ballot, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=ballot,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=ballot,
        )
        options.append(option)
    nota = key("option", tag, "nota")
    p.add("contest_option", id=nota, contest_id=contest, kind="none_of_the_above", ballot_label="NOTA")
    options.append(nota)

    part2 = Pages(p, url)

    def put(
        unit: str,
        channel: int,
        cells: list[int],
        cols: Columns,
        page: str,
        box: tuple[float, float, float, float],
    ) -> None:
        for column, measure, value in cols.counts(cells):
            option = options[column] if column is not None else None
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": channel,
                    "measure": measure,
                    "value": value,
                }
            )
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": channel,
                    "measure": measure,
                    "page_id": page,
                    "x0": box[0],
                    "y0": box[1],
                    "x1": box[2],
                    "y1": box[3],
                }
            )

    stations = 0
    whole: list[str] = []
    fine: list[str] = []
    coarse: list[str] = []
    for label, cells in rows:
        ac = segment_number(label)
        ac_name = title(re.sub(r"^\s*\d+\s*-?\s*", "", re.sub(r"\(\s*(SC|ST)\s*\)", "", label)).strip())
        segment = key("unit", tag, ac)
        p.external.add(ac_area(state, ac))
        p.add(
            "reporting_unit",
            id=segment,
            polity_id=INDIA,
            scheme_code=AC_SCHEME,
            code=f"LS{tag}:{ac}",
            area_relation="equals",
            area_id=ac_area(state, ac),
            kind="standard",
            valid_from=DAY_FIRST,
            valid_from_precision="day",
        )
        p.name(segment, f"{ac} {ac_name}", "en", "official")
        found = part1.get((number, ac))
        if found is None:
            put(segment, ALL, cells, columns, part2.page(1), WHOLE)
            coarse.append(f"{ac} {ac_name}")
            continue
        fine.append(f"{ac} {ac_name}")
        sheet_url, form = found
        cols = Columns.of(form["candidates"], form["extra_columns"])
        if cols.candidates != n:
            raise ValueError(f"PC {number} segment {ac}: Part I has {cols.candidates} candidates, Part II {n}")
        printed = [int(x) for x in form["total_row"]]
        compare = [*range(n), cols.nota, cols.total, *([cols.rejected] if cols.rejected is not None else [])]
        expect = [*range(n), columns.nota, columns.total, *([columns.rejected] if columns.rejected is not None else [])]
        if [printed[i] for i in compare] != [cells[i] for i in expect[: len(compare)]]:
            raise ValueError(f"PC {number} segment {ac}: Part I's total row differs from Part II's row")
        pages = Pages(p, sheet_url)
        sums = [0] * len(printed)
        seen: set[str] = set()
        codes = serials([str(row[0]) for page in form["pages"] for row in page["rows"]], form)
        for sheet_page in form["pages"]:
            page_rows = sheet_page["rows"]
            if not page_rows:
                continue
            number_of_page = int(sheet_page["page"])
            boxes = row_boxes(pages.source, number_of_page, len(page_rows), 1 + len(printed))
            if boxes is None:
                whole.append(f"{ac}:{number_of_page}")
            page_id = pages.page(number_of_page)
            for i, row in enumerate(page_rows):
                serial = codes.get(str(row[0]))
                station_cells = [int(x) for x in row[1:]]
                if serial is None:
                    if any(station_cells):
                        raise ValueError(f"PC {number} segment {ac}: row {row[0]} is dropped but holds votes")
                    continue
                if len(station_cells) != len(printed):
                    raise ValueError(f"PC {number} segment {ac} station {serial}: {len(station_cells)} cells")
                cols.check(station_cells, f"PC {number} segment {ac} station {serial}")
                if serial in seen:
                    raise ValueError(f"PC {number} segment {ac}: station {serial} appears twice")
                seen.add(serial)
                sums = [a + b for a, b in zip(sums, station_cells, strict=True)]
                unit = key("unit", tag, ac, serial)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=INDIA,
                    scheme_code=PS_SCHEME,
                    code=f"LS{tag}:{ac}:{serial}",
                    parent_id=segment,
                    area_relation="within",
                    area_id=ac_area(state, ac),
                    kind="standard",
                    valid_from=DAY_FIRST,
                    valid_from_precision="day",
                )
                p.name(unit, f"Polling station {serial}", "en", "official")
                put(unit, ALL, station_cells, cols, page_id, boxes[i] if boxes else WHOLE)
                stations += 1
        if sums != printed:
            raise ValueError(f"PC {number} segment {ac}: the stations do not sum to Part I's total row")

    unit = key("unit", tag, "postal")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=INDIA,
        scheme_code=PC_SCHEME,
        code=f"LS{tag}:postal",
        area_relation="within",
        area_id=pc_area(state, number),
        kind="postal_district",
        valid_from=DAY_FIRST,
        valid_from_precision="day",
    )
    p.name(unit, f"{name}: postal ballots", "en", "official")
    put(unit, POSTAL, postal, columns, part2.page(1), WHOLE)

    method = METHOD.format(office=state.office)
    if coarse:
        method += (
            " No capture of Part I is archived for the segments "
            + ", ".join(coarse)
            + ", so they are stored with Part II's segment totals."
        )
    if whole:
        method += " On some pages the ruling could not be traced, and their figures cite the whole page."
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": unquote(url)}),
    )
    return stations
