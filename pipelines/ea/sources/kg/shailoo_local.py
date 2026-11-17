"""Kyrgyzstan: the Central Commission's results of the local council elections of March 2016 and April 2021.

The Commission's open data holds one page per council, each linking one workbook (a few link two). A city council
workbook is a wide table with one column per polling station; an ayil (rural municipality) council workbook is a run
of blocks, one per electoral district, each with its polling stations as columns. Party-list councils carry party
rows, candidate councils carry candidate rows, and some 2016 workbooks hold one block per polling station. One
contest is built per council, with the polling station as the reporting unit.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import fastexcel

from ea import package, raw
from ea.package import Package
from ea.sources.kg import shailoo
from ea.sources.kg.shailoo import BASE, COUNTRY, KYRGYZSTAN, SOURCE, SOURCE_ID, against, clean, key, num

LOCAL = "kg.shailoo.local"
GEO = f"{LOCAL}/geo"
REGISTRY = f"{LOCAL}/registry"
OPEN = f"{BASE}/kg/otkrytye-dannye-cik"
MEASURES = {"3": 2, "5": 14, "7": 4, "8": 5}  # registered, voters, valid, invalid (see shailoo.ACCOUNTING["A"])
STATION = re.compile(r"(?:(?:УИК|ИУК|УК|УИ)\s*№?\s*(\d+)|^(\d{3,5})\s*шайлоо участ)", re.IGNORECASE)
HEADED = re.compile(r"^\D{0,8}№?\s*(\d{3,5})\s*$")
ACCOUNT = re.compile(r"^\s*(\d+[а-яa-z]?)\.?\s*(?:Число|Общее)", re.IGNORECASE)
TOTAL = re.compile(r"итого|итото|всего", re.IGNORECASE)
COUNCIL_LIST = re.compile(r"[\"«“]")


@dataclass(frozen=True)
class Round:
    id: str
    day: date
    precision: str
    month: str
    pages: tuple[tuple[str, str], ...]  # (kind, listing slug)


ROUNDS = (
    Round(
        "2016-03",
        date(2016, 3, 1),
        "month",
        "March 2016",
        (
            ("city", "itogi-vyborov-v-gorodskie-keneshi-mart-2016-goda"),
            ("ayil", "itogi-vyborov-v-ajylnye-keneshi-mart-2016-goda"),
        ),
    ),
    Round(
        "2021-04",
        date(2021, 4, 1),
        "month",
        "April 2021",
        (
            ("city", "mestnye-keneshi-itogi-vyborov-v-gorodskie-keneshi-aprel-2021-goda"),
            ("ayil", "mestnye-keneshi-itogi-vyborov-v-ajylnye-keneshi-aprel-2021-goda"),
        ),
    ),
)


@dataclass
class Row:
    pos: int
    label: str
    kind: str
    total: int | None
    seats: int | None
    votes: dict[str, int | None]


@dataclass
class Block:
    title: str
    total: bool = False
    stations: list[str] = field(default_factory=list)
    accounting: dict[int, dict[str, int | None]] = field(default_factory=dict)
    declared: dict[int, int | None] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    kind: str | None = None
    codes: dict[int, str] = field(default_factory=dict)  # column -> station code
    votes: dict[int, str] | None = None  # where the candidate rows sit when they differ from the accounting rows


@dataclass
class Council:
    round: Round
    scheme: str
    slug: str
    name: str
    page: str
    files: list[str]
    blocks: list[Block] = field(default_factory=list)

    @property
    def code(self) -> str:
        return f"{self.round.id}:{self.slug}"


def page_url(round_: Round, slug: str) -> str:
    return f"{OPEN}/{slug}/"


def discover() -> list[Council]:
    """Fetch every council page and workbook the four listings link, and return the councils."""
    councils: list[Council] = []
    for round_ in ROUNDS:
        for kind, listing in round_.pages:
            url = f"{OPEN}/{listing}/"
            index = raw.fetch(SOURCE, url, suffix=".html")
            slugs = [s for s in dict.fromkeys(re.findall(r'href="([a-z][a-z0-9-]*)"', index.path.read_text()))]
            slugs = [s for s in slugs if s != "static"]
            pages = raw.fetch_many(SOURCE, [f"{url}{slug}/" for slug in slugs], suffix=".html")
            missing = [slug for slug in slugs if f"{url}{slug}/" not in pages]
            for slug in missing:  # the site sometimes answers 5xx once; try the stragglers again
                try:
                    pages[f"{url}{slug}/"] = raw.fetch(SOURCE, f"{url}{slug}/", suffix=".html")
                except Exception:
                    continue
            for slug in slugs:
                page = pages.get(f"{url}{slug}/")
                if page is None:
                    continue
                text = page.path.read_text()
                links = [BASE + link for link in dict.fromkeys(re.findall(r'/media/[^"\' <)]*\.xlsx?', text))]
                head = re.search(r"<h1[^>]*>\s*([^<]*)", text)
                name = clean(head.group(1)) if head else slug
                councils.append(Council(round_, kind, slug, name, f"{url}{slug}/", links))
            for slug in missing:
                if f"{url}{slug}/" not in pages:
                    print(f"{LOCAL}: {url}{slug}/ is not served")
    files = [link for council in councils for link in council.files]
    raw.fetch_many(SOURCE, files, suffix=".xlsx")
    return councils


def grid(url: str) -> list[list[str]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None).to_polars()
    return [["" if cell is None else str(cell).strip() for cell in row] for row in frame.rows()]


def station_code(text: str) -> str | None:
    found = re.match(r"^\s*№?\s*(\d+)(?:\.0+)?(?:\s|$)", text)
    return found.group(1) if found else None


def blocks_of(rows: list[list[str]], title: str = "") -> list[Block]:
    blocks: list[Block] = []
    block: Block | None = None
    heading = title
    pending = False  # the wide layout's station numbers are on the row after the "polling stations" caption

    def cell(row: list[str], column: int) -> str:
        return row[column].strip() if 0 <= column < len(row) else ""

    for index, row in enumerate(rows):
        first = cell(row, 0)
        text = " ".join(cell for cell in row if cell)
        if not text:
            continue
        if pending:
            pending = False
            columns = {
                column: code
                for column, value in enumerate(row)
                if column >= 3 and (code := station_code(value)) and num(value) is not None
            }
            if columns:
                assert block is not None
                block.codes = columns
                block.stations = list(columns.values())
                for column, value in enumerate(row[:3]):
                    if TOTAL.search(value):
                        block.total = True
                continue
        if any("Избирательные участки" in value for value in row):
            block = Block(heading)
            blocks.append(block)
            pending = True
            for value in row:
                if TOTAL.search(value):
                    block.total = True
            continue
        stations = {
            column: found.group(1) or found.group(2)
            for column, value in enumerate(row)
            if (found := STATION.search(value))
        }
        if not stations and not first and any(TOTAL.search(value) for value in row[:3]):
            stations = {
                column: found.group(1)
                for column, value in enumerate(row)
                if column >= 3 and (found := HEADED.match(value))
            }
        if stations and min(stations) < 4 and not first.startswith("УИК"):  # the code sits over the total column
            stations = {4 + offset: code for offset, code in enumerate(stations.values())}
        if stations and not ACCOUNT.match(first):
            block = Block(heading, codes=stations, stations=list(stations.values()))
            block.total = any(TOTAL.search(value) for value in row) or min(stations) >= 4
            blocks.append(block)
            continue
        if first == "УИК" and len(row) > 1:
            code = station_code(row[1])
            if code:
                block = Block(f"УИК {code}", codes={5: code}, stations=[code], votes={3: code})
                blocks.append(block)
                continue
        only_first = first and not any(cell(row, column) for column in range(1, len(row)))
        if only_first and not ACCOUNT.match(first) and not re.fullmatch(r"\d+", first):
            heading = first
            continue
        if block is None:
            heading = first or heading
            continue
        label_cell = next((value for value in (first, cell(row, 1)) if ACCOUNT.match(value)), None)
        glued = re.fullmatch(r"\d+[а-яa-z]?", first) and re.match(r"^(Число|Общее)", cell(row, 1), re.IGNORECASE)
        if label_cell or glued:
            lead = re.match(r"^\s*(\d+[а-яa-z]?)", label_cell or "")
            number = (lead.group(1) if lead else first).lower()
            measure = MEASURES.get(number)
            if measure is not None:
                values = {code: num(cell(row, column)) for column, code in block.codes.items()}
                block.accounting[measure] = values
                if block.total:
                    block.declared[measure] = num(cell(row, 2))
            continue
        if re.fullmatch(r"\d+(?:\.0+)?", first) and cell(row, 1):
            label = clean(cell(row, 1))
            if ACCOUNT.match(label) or label.startswith("Число"):
                continue
            if block.total:
                total = num(cell(row, 2))
            else:
                total = None
            votes = {code: num(cell(row, column)) for column, code in (block.votes or block.codes).items()}
            wide = block.total and block.codes and min(block.codes) >= 5
            seats = num(cell(row, 4)) if wide else None
            if not block.total and len(block.stations) == 1 and not any(v is not None for v in votes.values()):
                continue
            if total is not None and len(block.stations) == 1 and votes.get(block.stations[0]) is None:
                votes[block.stations[0]] = total
            pos = int(float(first))
            kind = (
                "none_of_the_above"
                if against(label)
                else block.kind or ("list" if COUNCIL_LIST.search(label) else "candidate")
            )
            block.rows.append(Row(pos, label, kind, total, seats, votes))
            continue
        lowered = text.lower()
        if "кандидат" in lowered and block is not None:
            block.kind = "candidate"
        elif "партия" in lowered or "партий" in lowered:
            block.kind = "list"
    return blocks


def read(council: Council) -> None:
    for url in council.files:
        for block in blocks_of(grid(url), council.name):
            if block not in council.blocks:  # a few councils link the same table twice
                council.blocks.append(block)


# Packages.


def english(council: Council) -> str:
    core = re.sub(r"(-ajylnyj-kenesh|-ajylnyj-keesh|-gorodskoj-kenesh|^g-?)", "", council.slug)
    name = "-".join(part.capitalize() for part in core.split("-"))
    return f"{name} {'city' if council.scheme == 'city' else 'ayil'} council"


def area(code: str) -> str:
    return key("area", "kg.council", code)


def unit_id(round_: Round, code: str) -> str:
    return key("unit", "kg.uik", f"{round_.id}:{code}")


def councils() -> list[Council]:
    found = discover()
    for council in found:
        read(council)
    for council in found:
        if not council.blocks:
            print(f"{LOCAL}: {council.page} has no readable workbook ({len(council.files)} linked)")
    return [council for council in found if council.blocks]


def fetch() -> None:
    discover()


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{LOCAL}/{round_.id}" for round_ in ROUNDS)]


def parse() -> None:
    items = councils()
    geo(items).write()
    registry(items).write()
    for round_ in ROUNDS:
        build(round_, [item for item in items if item.round is round_]).write()


def emit() -> None:
    package.run("load", packages())


def geo(items: list[Council]) -> Package:
    p = Package(
        GEO,
        LOCAL,
        "Kyrgyzstan's city and ayil councils as the Central Commission names them in its 2016 and 2021 local election "
        "results, and the polling station scheme",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    for council in items:
        for url in council.files:
            p.uses(raw.cached(SOURCE, url))
    for code, rank, kind, noun in (
        ("kg.council", 1, "electoral_district", "local council"),
        ("kg.uik", 2, "electoral_district", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=KYRGYZSTAN, kind=kind, rank=rank, noun=noun, code_system=None)
    for council in items:
        id = area(council.code)
        p.add("area", id=id, scheme_code="kg.council")
        p.add("area_code", area_id=id, code=council.code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, english(council), "en", "official")
        p.name(id, council.name, "ky", "official", False)
    return p


def registry(items: list[Council]) -> Package:
    p = Package(
        REGISTRY,
        LOCAL,
        "The local councils and the parties on their ballots that the Central Commission's results add to the "
        "Supreme Council registry",
        depends=[f"{SOURCE}/registry", GEO],
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    known = {row["id"] for row in shailoo.registry().rows["party"]}
    parties: dict[str, str] = {}
    for council in items:
        for block in council.blocks:
            for row in block.rows:
                if row.kind == "list":
                    parties.setdefault(shailoo.party_key(row.label), row.label)
    for code, label in sorted(parties.items()):
        id = key("party", code)
        if id in known:
            continue
        p.add("party", id=id, kind="party", polity_id=KYRGYZSTAN)
        p.add("external_id", entity_id=id, scheme="kg.shailoo.party", value=code)
        p.name(id, label, "ky", "official")
    for council in items:
        body = key("body", "council", council.code)
        p.add("body", id=body, polity_id=KYRGYZSTAN, kind="council")
        p.name(body, english(council), "en", "official")
        p.name(body, council.name, "ky", "native", False)
    return p


def build(round_: Round, items: list[Council]) -> Package:
    p = Package(
        f"{LOCAL}/{round_.id}",
        LOCAL,
        f"The {round_.month} city and ayil council elections at the polling stations the Central Commission publishes",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", GEO, REGISTRY],
    )
    p.external |= {KYRGYZSTAN, COUNTRY}
    seen_files: set[str] = set()
    for url in dict.fromkeys(url for item in items for url in item.files):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in seen_files:  # two links can serve the same file
            seen_files.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    off = [item.slug for item in items if disagrees(item)]
    if off:
        p.accept(
            "accounting.declared",
            "In the workbooks of "
            + ", ".join(off)
            + " the totals the Commission prints for a district differ from the sum of its polling stations' figures",
        )
    election = key("election", LOCAL, round_.id)
    p.add(
        "election",
        id=election,
        polity_id=KYRGYZSTAN,
        kind="local",
        date_from=round_.day,
        date_to=None,
        date_precision=round_.precision,
        original_text=None,
        status="held",
    )
    path = f"kg/{round_.id}-local"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{round_.month} Kyrgyz local council elections", "en", "official")
    p.name(election, f"Жергиликтүү кеңештерге шайлоо, {round_.month}", "ky", "native", False)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="caveat",
        body_md=(
            "The Commission's pages give the month of the election, not the day. Each council's page links its own "
            "workbook, and the councils on the pages are those whose workbooks the Commission published."
        ),
        lang="en",
    )
    units: set[str] = set()
    for item in items:
        contest(p, round_, election, path, item, units)
    return p


def disagrees(item: Council) -> bool:
    for block in item.blocks:
        for measure, declared in block.declared.items():
            if declared is not None and sum(value or 0 for value in block.accounting[measure].values()) != declared:
                return True
        for row in block.rows:
            if row.total is not None and sum(value or 0 for value in row.votes.values()) != row.total:
                return True
    return False


def contest(p: Package, round_: Round, election: str, path: str, item: Council, units: set[str]) -> None:
    contest_id = key("contest", LOCAL, item.code)
    options = [(index, block, row) for index, block in enumerate(item.blocks) for row in block.rows]
    lists = sum(1 for _, _, row in options if row.kind == "list")
    kind = "list_allocation" if lists else "body_seats"
    many = len(item.blocks) > 1
    p.add(
        "contest",
        id=contest_id,
        election_id=election,
        kind=kind,
        office_id=None,
        body_id=key("body", "council", item.code),
        district_area_id=area(item.code),
        magnitude=None,
        round_number=None,
        electoral_system_id=None,
        default_result_kind="counted",
    )
    p.name(contest_id, f"{english(item)} election, {round_.month}", "en", "official")
    p.name(contest_id, f"{item.name}, {round_.month}", "ky", "native", False)
    p.add("slug", path=f"{path}/{item.slug}", entity_id=contest_id, is_current=True)
    result_set = key("result_set", LOCAL, item.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest_id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    note = shailoo.NOTE_ACCOUNTING
    if kind == "body_seats":
        note += (
            " The council's candidates stood in electoral districts, each with its own candidates; every candidate's "
            "votes are stored at the polling stations of their district. The Commission's tables do not mark the "
            "elected candidates, so no outcomes are stored."
        )
    p.add(
        "note",
        id=key("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=note,
        lang="en",
    )
    ids: dict[tuple[int, int], str] = {}
    for number, (index, block, row) in enumerate(options, 1):
        option_id = ids[(index, row.pos)] = key("option", LOCAL, item.code, number)
        label = f"{block.title}: {row.label}" if many and block.title else row.label
        p.add(
            "contest_option",
            id=option_id,
            contest_id=contest_id,
            kind=row.kind,
            party_id=shailoo.party(row.label) if row.kind == "list" else None,
            ballot_position=number,
            ballot_label=label,
        )
        if row.kind == "candidate":
            person = key("person", LOCAL, item.code, number)
            p.add("person", id=person)
            p.name(person, row.label, "ky", "official")
            p.add(
                "candidacy",
                id=key("candidacy", LOCAL, item.code, number),
                person_id=person,
                option_id=option_id,
                role="head",
                ballot_name=row.label,
            )
        if row.seats:
            p.add("outcome", result_set_id=result_set, option_id=option_id, kind="elected", seats=row.seats)
        if row.total is not None:
            p.add("declared", result_set_id=result_set, option_id=option_id, measure=shailoo.VOTES, value=row.total)
    totals: dict[int, int | None] = {}
    seen: set[str] = set()
    for position, block in enumerate(item.blocks):
        for code in block.stations:
            # a station that votes in two districts reports each district's figures as its own unit
            label = code if code not in seen else f"{code}/{position + 1}"
            seen.add(code)
            unit = unit_id(round_, label)
            if unit not in units:
                units.add(unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=KYRGYZSTAN,
                    scheme_code="kg.uik",
                    code=f"{round_.id}:{label}",
                    area_relation="within",
                    area_id=area(item.code),
                    kind="standard",
                )
            for measure, values in block.accounting.items():
                value = values.get(code)
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            for row in block.rows:
                value = row.votes.get(code)
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=ids[(position, row.pos)],
                        channel=0,
                        measure=shailoo.VOTES,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        for measure, value in block.declared.items():
            if measure in totals and totals[measure] is None:
                continue
            totals[measure] = None if value is None else (totals.get(measure) or 0) + value
    for measure, value in totals.items():
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)


def summary(items: list[Council]) -> Counter[str]:
    return Counter(f"{item.round.id}/{item.scheme}" for item in items)
