"""Ontario school board trustee elections: the Toronto District School Board and the Toronto Catholic District School
Board by voting subdivision, 2003 to 2026.

The City of Toronto runs the trustee elections of the two English boards that lie wholly inside the city and publishes
them beside its municipal results: one workbook per board, with a sheet for every trustee ward. A sheet holds a block
for each city ward the trustee ward covers, with a row per candidate, a column per voting subdivision, and (from 2006)
a row of the block's valid votes. Before 2006 the workbooks hold one sheet per trustee ward and city ward. Each trustee
ward elects one trustee by plurality, so each ward is a contest with one result set whose values sit at the voting
subdivisions. The city's files do not name the winners and the province's lists of successful candidates cover only
municipal offices, so no outcomes are recorded.

Trustee wards are the unions of the city's wards that the workbooks list for them: the 44-ward model for 2003 to 2014
and the 25-ward model from 2018. Their outlines are those unions, dated to the ward model they come from.

The French-language boards that serve parts of Toronto are not loaded. Their trustee wards reach beyond the city, so the
city's files hold only part of each contest, and for 2018 the city publishes none because the races were acclaimed or
voided.
"""

import functools
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import shapefile
import shapely
import shapely.geometry
import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.ca import on_municipal as city

SOURCE = "ca.on_school"
WARDS = "ca.on_school.wards"
RESULTS_PAGE = city.RESULTS_PAGE
WARDS_PAGE = city.WARDS_PAGE
RESULTS = city.RESULTS
DAYS = city.DAYS
WARDS_44 = city.WARDS_44
WARDS_25 = city.WARDS_25
CANADA = city.CANADA
ONTARIO_POLITY = city.ONTARIO_POLITY
ONTARIO_AREA = city.ONTARIO_AREA
NEW_WARDS = city.NEW_WARDS
SIMPLIFY = city.SIMPLIFY
SYSTEM_NOTE = (
    "Each trustee ward elects one trustee. Every elector marks one candidate, and the candidate with the most votes "
    "wins."
)
# Board key: (English name, file-name test, slug).
BOARDS = {
    "tdsb": ("Toronto District School Board", "toronto-district-school-board"),
    "tcdsb": ("Toronto Catholic District School Board", "toronto-catholic-district-school-board"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CITY_SOURCE = key("source", "toronto")
WARD_SOURCE = key("source", "toronto_wards")
SYSTEM = key("electoral_system", "plurality")


def model(year: int) -> str:
    return "25" if year >= NEW_WARDS else "44"


def fetch() -> None:
    for url in RESULTS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    raw.fetch(WARDS, WARDS_44, suffix=".zip")
    raw.fetch(WARDS, WARDS_25, suffix=".zip")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


class Block(NamedTuple):
    city_ward: int
    candidates: list[tuple[str, dict[str, int], int | None]]
    valid: dict[str, int]


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def _name(text: str) -> str:
    text = " ".join(text.replace(",", " ").split())
    return text.title() if text.isupper() and len(text) > 1 else text


def _board(member: str) -> str | None:
    base = fold(member.rsplit("/", 1)[-1])
    if "all_offices" in base or not re.search(r"school|scolaire|dsb|viamonde|monavenir", base):
        return None
    if re.search(r"conseil|viamonde|monavenir|tfdsb|tfcdsb", base):
        return None
    if re.search(r"catholic|tcdsb", base):
        return "tcdsb"
    return "tdsb"


def _blocks(rows: list[tuple[object, ...]], fallback: int | None) -> list[Block]:
    """The blocks of one sheet: a header row, candidate rows, and an optional totals row naming the city ward."""
    found: list[Block] = []
    i = 0
    while i < len(rows):
        if str(rows[i][0]).strip() not in ("Subdivision", "Name"):
            i += 1
            continue
        head = rows[i]
        total = next((c for c, cell in enumerate(head) if str(cell).strip() == "Total"), None)
        # A repeated label column (the city's 2006 workbook has one) splits a block; its totals row lacks the column.
        stray = [c for c, cell in enumerate(head) if c > 0 and str(cell).strip() in ("Subdivision", "Name")]
        columns = [
            (c, city._sub(cell))
            for c, cell in enumerate(head)
            if c > 0 and c != total and c not in stray and cell not in (None, "")
        ]
        candidates: list[tuple[str, dict[str, int], int | None]] = []
        valid: dict[str, int] = {}
        ward = fallback
        i += 1
        while i < len(rows):
            row = rows[i]
            text = "" if row[0] is None else str(row[0]).strip()
            if not text or text == "None" or text in ("Subdivision", "Name"):
                break
            i += 1
            votes = {sub: v for c, sub in columns if (v := city._int(row[c])) is not None}
            if stray and re.search(r"(?i)\btotals?$", text) and city._int(row[stray[0]]) is not None:
                votes = {
                    sub: v
                    for c, sub in columns
                    if (v := city._int(row[c - sum(1 for x in stray if x < c)])) is not None
                }
            match = re.search(r"(?i)(?:city )?ward (\d+) totals?$", text)
            if match or re.search(r"(?i)\btotals?$", text):
                valid = votes
                if match:
                    ward = int(match.group(1))
                break
            if votes:
                candidates.append((_name(text), votes, None if total is None else city._int(row[total])))
        if ward is None:
            raise ValueError("a block names no city ward")
        if candidates:
            found.append(Block(ward, candidates, valid))
    return found


def _sheets(year: int) -> list[tuple[str, int, list[tuple[object, ...]], int | None]]:
    """Every trustee-ward sheet of the two boards: board, trustee ward, rows, and the city ward a sheet name gives."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, RESULTS[year]).path)
    out: list[tuple[str, int, list[tuple[object, ...]], int | None]] = []
    for member in archive.namelist():
        board = _board(member) if not member.endswith("/") else None
        if board is None:
            continue
        data = archive.read(member)
        sheets: list[tuple[str, list[tuple[object, ...]]]] = []
        if member.lower().endswith("xlsx"):
            book = fastexcel.read_excel(data)
            for name in book.sheet_names:
                if name != "Notice":
                    sheets.append(
                        (name, [tuple(r) for r in book.load_sheet_by_name(name, header_row=None).to_polars().rows()])
                    )
        else:
            wb = xlrd.open_workbook(file_contents=data)
            for sheet in wb.sheets():
                sheets.append((sheet.name, [tuple(sheet.row_values(r)) for r in range(sheet.nrows)]))
        for name, rows in sheets:
            old = re.search(r"School Ward (\d+) \((\d+)\)\s*$", name)
            if old:
                out.append((board, int(old.group(1)), rows, int(old.group(2))))
                continue
            number = re.search(r"(\d+)\s*$", name)
            if number:
                out.append((board, int(number.group(1)), rows, None))
    return out


@functools.cache
def results(year: int) -> dict[str, dict[int, list[Block]]]:
    """Each board's blocks of one election, by trustee ward."""
    found: dict[str, dict[int, list[Block]]] = {board: defaultdict(list) for board in BOARDS}
    for board, ward, rows, fallback in _sheets(year):
        found[board][ward] += _blocks(rows, fallback)
    return {board: dict(sorted(_regroup(wards).items())) for board, wards in found.items() if wards}


def _regroup(wards: dict[int, list[Block]]) -> dict[int, list[Block]]:
    """Move a block that a workbook files under the wrong trustee ward to the ward whose candidates it carries.

    The city's 2010 Toronto Catholic District School Board workbook has a few city-ward blocks on the sheet of a
    neighboring trustee ward; their candidates are those of the ward whose other blocks share them.
    """
    sets = {
        number: [frozenset(label for label, _, _ in block.candidates) for block in blocks]
        for number, blocks in wards.items()
    }
    major = {number: max(set(found), key=found.count) for number, found in sets.items()}
    moved: dict[int, list[Block]] = {number: [] for number in wards}
    for number, blocks in wards.items():
        for block, found in zip(blocks, sets[number], strict=True):
            target = number
            if found != major[number]:
                owners = [other for other, theirs in major.items() if theirs == found]
                if len(owners) != 1:
                    raise ValueError(f"ward {number}: city ward {block.city_ward} matches no trustee ward")
                target = owners[0]
            moved[target].append(block)
    return moved


def mapping(year: int, board: str) -> dict[int, tuple[int, ...]]:
    return {
        ward: tuple(sorted({block.city_ward for block in blocks}))
        for ward, blocks in results(year).get(board, {}).items()
    }


class Vintage(NamedTuple):
    board: str
    years: tuple[int, ...]
    model: str
    wards: dict[int, tuple[int, ...]]

    @property
    def first(self) -> int:
        return self.years[0]


@functools.cache
def vintages() -> list[Vintage]:
    """Runs of consecutive elections of a board whose trustee wards cover the same city wards."""
    found: list[Vintage] = []
    for board in BOARDS:
        for year in DAYS:
            wards = mapping(year, board)
            if not wards:
                continue
            last = next((v for v in reversed(found) if v.board == board), None)
            if last is not None and last.wards == wards and last.model == model(year):
                found[found.index(last)] = last._replace(years=(*last.years, year))
            else:
                found.append(Vintage(board, (year,), model(year), wards))
    return found


def vintage(board: str, year: int) -> Vintage:
    return next(v for v in vintages() if v.board == board and year in v.years)


def span(v: Vintage) -> tuple[date | None, date | None]:
    """The dates a vintage's wards are in force: from the first election it governed to the next vintage's first."""
    same = [other for other in vintages() if other.board == v.board]
    later = [other for other in same if other.first > v.years[-1]]
    start = None if v is same[0] else DAYS[v.first]
    return start, DAYS[later[0].first] if later else None


def ward_area(v: Vintage, number: int) -> str:
    return key("area", v.board, v.first, number)


def board_set(v: Vintage) -> str:
    return key("boundary_set", v.board, v.first)


@functools.cache
def _shapes(which: str) -> dict[int, shapely.Geometry]:
    archive = zipfile.ZipFile(raw.cached(WARDS, WARDS_25 if which == "25" else WARDS_44).path)
    shp = next(name for name in archive.namelist() if name.lower().endswith(".shp"))
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(shp)), dbf=io.BytesIO(archive.read(shp[:-4] + ".dbf")), encoding="utf8"
    )
    found: dict[int, shapely.Geometry] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        number = int(str(record["AREA_S_CD"])) if which == "25" else int(str(record["SCODE_NAME"]))
        geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
        found[number] = shapely.simplify(geometry, SIMPLIFY, preserve_topology=True)
    return found


def _multi(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def _union(which: str, numbers: tuple[int, ...]) -> shapely.Geometry:
    shapes = _shapes(which)
    missing = [n for n in numbers if n not in shapes]
    if missing:
        raise ValueError(f"city wards {missing} have no outline in the {which}-ward model")
    return _multi(shapely.union_all([shapes[n] for n in numbers]))


def _source(p: Package, source_id: str, title: str, url: str, publisher: str) -> None:
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=publisher,
        title=title,
        url=url,
        license="Open Government Licence – Toronto",
        license_url="https://open.toronto.ca/open-data-license/",
        attribution="Contains information licensed under the Open Government Licence – Toronto",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Toronto District School Board and the Toronto Catholic District School Board as areas, with their trustee "
        "wards as unions of the city's wards",
    )
    p.external |= {CANADA, ONTARIO_POLITY, ONTARIO_AREA}
    _source(p, WARD_SOURCE, "City Wards", WARDS_PAGE, "City of Toronto")
    for url in (WARDS_44, WARDS_25):
        p.artifact(raw.cached(WARDS, url), WARD_SOURCE)
    p.add(
        "scheme",
        code="ca-on.school_board",
        polity_id=ONTARIO_POLITY,
        kind="administrative",
        rank=3,
        noun="school board",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-on.school_ward",
        polity_id=ONTARIO_POLITY,
        kind="electoral_district",
        rank=4,
        noun="trustee ward",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-on.school_subdivision",
        polity_id=ONTARIO_POLITY,
        kind="precinct",
        rank=5,
        noun="voting subdivision",
        code_system=None,
    )
    outline = _union("25", tuple(sorted(_shapes("25"))))
    for board, (name, slug) in BOARDS.items():
        area = key("area", board)
        p.add("area", id=area, scheme_code="ca-on.school_board")
        p.add("area_parent", area_id=area, parent_id=ONTARIO_AREA)
        p.name(area, name, "en", "official")
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            valid_to=None,
            geom=_wkb(outline),
            label_point=_wkb(shapely.point_on_surface(outline)),
            quality="synthetic",
            source_id=WARD_SOURCE,
        )
        polity = key("polity", board)
        p.add("polity", id=polity, kind="school_district", iso_code=None)
        p.name(polity, name, "en", "official")
        p.add("polity_parent", polity_id=polity, parent_id=ONTARIO_POLITY)
        p.add("polity_territory", polity_id=polity, area_id=area)
        p.add("slug", path=f"ca/on/{slug}", entity_id=polity, is_current=True)
    for v in vintages():
        since, until = span(v)
        board_name = BOARDS[v.board][0]
        p.add(
            "boundary_set",
            id=board_set(v),
            scheme_code="ca-on.school_ward",
            vintage=f"{board_name} trustee wards, {v.years[0]} to {v.years[-1]}",
            valid_from=since,
            valid_to=until,
            as_of=DAYS[v.first],
        )
        for number, wards in sorted(v.wards.items()):
            place = ward_area(v, number)
            shape = _union(v.model, wards)
            p.add("area", id=place, scheme_code="ca-on.school_ward")
            p.add("area_code", area_id=place, code=f"{number:02d}", valid_from=since, valid_to=until)
            p.add("area_parent", area_id=place, parent_id=key("area", v.board), valid_from=since, valid_to=until)
            p.name(place, f"{board_name} Ward {number}", "en", "official")
            p.add(
                "area_geometry",
                area_id=place,
                valid_from=since,
                valid_to=until,
                geom=_wkb(shape),
                label_point=_wkb(shapely.point_on_surface(shape)),
                quality="synthetic",
                source_id=WARD_SOURCE,
            )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The boards, trustee bodies, and electoral system of Toronto's English-language school board elections, with "
        "the source of their results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, ONTARIO_POLITY}
    _source(p, CITY_SOURCE, "Election Results - Official", RESULTS_PAGE, "City of Toronto, City Clerk's Office")
    for url in RESULTS.values():
        p.artifact(raw.cached(SOURCE, url), CITY_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ONTARIO_POLITY,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Ontario school board plurality voting", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for board, (name, _) in BOARDS.items():
        body = key("body", board)
        p.add("body", id=body, polity_id=key("polity", board), kind="council")
        p.name(body, f"{name} trustees", "en", "official")
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Toronto school board trustee elections: every trustee ward of the English public and English "
        "Catholic boards, by voting subdivision",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, ONTARIO_POLITY}
    p.uses(raw.cached(SOURCE, RESULTS[year]))
    found = results(year)
    if year == 2006:
        p.accept(
            "accounting.declared",
            "In the city's 2006 Toronto Catholic District School Board workbook, the Total column of two candidates in "
            "Ward 9 (Clemens Karl, 460, and Leblanc-Miller Catherine, 1,047) exceeds the sum of their subdivision "
            "counts (454 and 1,026) in city ward 27; the archive keeps both figures as published.",
        )
    for board, wards in found.items():
        name, slug = BOARDS[board]
        v = vintage(board, year)
        polity = key("polity", board)
        vote = key("election", year, board)
        p.add("election", id=vote, polity_id=polity, kind="local", date_from=day, date_to=day, status="held")
        p.name(vote, f"{year} {name} trustee election", "en", "official")
        p.add("slug", path=f"ca/on/{slug}/{day}-local", entity_id=vote, is_current=True)
        units: dict[tuple[int, int, str], str] = {}
        for number, blocks in wards.items():
            for block in blocks:
                for sub in {*block.valid, *(s for _, votes, _ in block.candidates for s in votes)}:
                    if (number, block.city_ward, sub) in units:
                        continue
                    unit = units[(number, block.city_ward, sub)] = key("unit", day, board, block.city_ward, sub)
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=CANADA,
                        scheme_code="ca-on.school_subdivision",
                        code=f"{day}:{board}:{block.city_ward:02d}:{sub}",
                        area_relation="within",
                        area_id=ward_area(v, number),
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
        for number, blocks in wards.items():
            tag = f"{board}:{number}"
            contest = key("contest", year, tag)
            p.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="body_seats",
                office_id=None,
                body_id=key("body", board),
                district_area_id=ward_area(v, number),
                magnitude=1,
                round_number=1,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
                boundary_set_id=board_set(v),
            )
            p.name(contest, f"{name}: Ward {number} trustee", "en", "official")
            p.add(
                "slug", path=f"ca/on/{slug}/{day}-local/trustee-ward-{number:02d}", entity_id=contest, is_current=True
            )
            result_set = key("result_set", year, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=vote,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=CITY_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            options: dict[str, str] = {}
            declared: dict[str, int | None] = {}
            for block in blocks:
                for label, votes, total in block.candidates:
                    if label not in options:
                        position = len(options) + 1
                        seq = f"{tag}:{position}"
                        person = key("person", year, seq)
                        p.add("person", id=person, birth_precision=None)
                        p.name(person, label, "en", "official")
                        option = options[label] = key("option", year, seq)
                        p.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=None,
                            ballot_position=position,
                            ballot_label=label,
                        )
                        p.add(
                            "candidacy",
                            id=key("candidacy", year, seq),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                        )
                        declared[label] = 0
                    if declared[label] is not None and total is not None:
                        declared[label] = (declared[label] or 0) + total
                    else:
                        declared[label] = None
                    for sub, value in votes.items():
                        _value(p, result_set, units[(number, block.city_ward, sub)], options[label], 1, value)
                for sub, value in block.valid.items():
                    _value(p, result_set, units[(number, block.city_ward, sub)], None, 4, value)
            for label, total in declared.items():
                if total is not None:
                    p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=total)
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
