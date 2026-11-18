"""New Zealand's national referendums that the Electoral Commission's results site publishes by electorate: the citizens
initiated referendums of 2009 and 2013 and the two flag referendums of 2015 and 2016. Each is one package, read from
the results pages and the count sheet of the first flag referendum."""

import functools
from dataclasses import dataclass
from datetime import date

from ea.package import Package
from ea.sources.nz import ec

SOURCE = ec.SOURCE
SITE = ec.SITE
REGISTRY = "nz.ec/registry"
SYSTEM_PREFERENTIAL = ec.key("electoral_system", "preferential-referendum")


@dataclass(frozen=True)
class Referendum:
    day: str
    slug: str
    title: str
    question: str
    labels: tuple[str, str]
    page: str
    extra: tuple[str, ...] = ()

    @property
    def package(self) -> str:
        return f"{SOURCE}/{self.day}-{self.slug}"


CITIZENS = (
    Referendum(
        "2009-08-25",
        "referendum",
        "2009 New Zealand citizens initiated referendum",
        "Should a smack as part of good parental correction be a criminal offence in New Zealand?",
        ("Yes", "No"),
        f"{SITE}/2009_citizens_referendum/2009_referendum_results.html",
    ),
    Referendum(
        "2013-12-17",
        "referendum",
        "2013 New Zealand citizens initiated referendum",
        "Do you support the Government selling up to 49% of Meridian Energy, Mighty River Power, Genesis Power, "
        "Solid Energy and Air New Zealand?",
        ("Yes", "No"),
        f"{SITE}/2013_citizens_referendum/2013_referendum_results.html",
    ),
)
FLAG_1 = Referendum(
    "2015-12-15",
    "flag-referendum",
    "2015 New Zealand first referendum on the flag",
    "Which alternative flag do you prefer?",
    ("", ""),
    f"{SITE}/2015_flag_referendum1/turnout-by-electorate-report.html",
    (
        f"{SITE}/2015_flag_referendum1/results-by-count-report.xls",
        f"{SITE}/2015_flag_referendum1/results-by-count-report.html",
    ),
)
FLAG_2 = Referendum(
    "2016-03-30",
    "flag-referendum",
    "2016 New Zealand second referendum on the flag",
    "What is your choice for the New Zealand flag?",
    ("Silver Fern Flag", "Current New Zealand Flag"),
    f"{SITE}/2016_flag_referendum2/result-by-electorate.html",
)
ALL = (*CITIZENS, FLAG_1, FLAG_2)
# The pages that go with the results: the flag referendums' home pages, and the history pages that list every referendum.
CONTEXT = (
    f"{SITE}/2015_flag_referendum1/",
    f"{SITE}/2016_flag_referendum2/",
    "https://elections.nz/democracy-in-nz/historical-events/1995-2013-referendums",
)
OPTIONS_2015 = (
    ("A", "Silver Fern (Black, White and Blue)"),
    ("E", "Silver Fern (Red, White and Blue)"),
    ("B", "Red Peak"),
    ("D", "Silver Fern (Black and White)"),
    ("C", "Koru"),
)


def urls(each: Referendum) -> list[str]:
    return [each.page, *each.extra]


def fetch() -> None:
    for each in ALL:
        for url in urls(each):
            ec._try(url, ".xls" if url.endswith(".xls") else ".html")
    for url in CONTEXT:
        ec._try(url, ".html")


def held() -> list[Referendum]:
    return [each for each in ALL if all(ec.has(url) for url in urls(each))]


def packages() -> list[str]:
    return [each.package for each in held()]


def parse() -> None:
    for each in held():
        build(each).write()


def number(text: str) -> int | None:
    found = ec.clean(text).replace(",", "")
    return None if found in ("", "-") else int(found)


def table(url: str) -> list[list[str]]:
    cells = ec.Cells()
    cells.feed(ec.text(url))
    return [[ec.clean(cell) for cell in row] for row in cells.rows]


def electorate(label: str) -> tuple[int, str]:
    code, _, name = label.partition(" - ")
    return int(code), name.strip()


@dataclass
class Count:
    label: str
    roll: int | None
    cast: int | None
    first: int | None
    second: int | None
    informal: int | None
    invalid: int | None
    valid: int | None


@functools.cache
def counts(url: str) -> tuple[list[tuple[int | None, str, Count]], Count]:
    """The rows of a results page by electorate and its printed total. The columns are the roll, the votes received,
    the turnout, the valid votes (which include the informal ones), the two answers with their shares, the informal
    votes with their share, and the invalid votes that the count leaves out."""
    rows: list[tuple[int | None, str, Count]] = []
    total: Count | None = None
    for row in table(url):
        if len(row) != 12 or not (row[0][:1].isdigit() or row[0] in ("TOTAL", "Elector not identifiable")):
            continue
        got = Count(
            row[0], number(row[1]), number(row[2]), number(row[5]), number(row[7]), number(row[9]), number(row[11]),
            number(row[4]),
        )  # fmt: skip
        if row[0] == "TOTAL":
            total = got
        elif row[0][:1].isdigit():
            code, name = electorate(row[0])
            rows.append((code, name, got))
        else:
            rows.append((None, row[0], got))
    if total is None or not rows:
        raise ValueError(f"{url}: no table of results by electorate")
    return rows, total


def build(each: Referendum) -> Package:
    if each is FLAG_1:
        return build_2015(each)
    day = date.fromisoformat(each.day)
    p = Package(
        each.package,
        SOURCE,
        f"The {each.title.removeprefix('The ')} by electorate from the Electoral Commission's results",
        depends=[REGISTRY],
    )
    p.external.add(ec.NEW_ZEALAND)
    p.uses(ec.art(each.page))
    rows, total = counts(each.page)
    election = ec._election(p, f"referendum-{each.day}", day, "referendum", each.title, f"nz/{each.day}-referendum")
    made = units(p, each, rows)
    contest = ec._contest(
        p,
        f"referendum-{each.day}",
        election,
        "ballot_question",
        each.title,
        f"nz/{each.day}-referendum/question",
        electoral_system_id=ec.SYSTEM_REFERENDUM,
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.add(
        "note",
        id=ec.key("note", contest, "question"),
        entity_id=contest,
        kind="context",
        body_md=f"The referendum asked the question: “{each.question}” "
        + ("Voters answered Yes or No." if each.labels[0] == "Yes" else "Voters chose one of two flags."),
        lang="en",
    )
    result_set = ec._result_set(p, election, contest, f"referendum-{each.day}", METHOD)
    options = []
    for position, label in enumerate(each.labels, 1):
        option = ec.key("option", f"referendum-{each.day}", label)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )
    sums = dict.fromkeys(("roll", "cast", "first", "second", "informal", "invalid"), 0)
    for code, name, got in rows:
        unit = made[name if code is None else code]
        shown = [got.first, got.second]
        if code is not None:
            for option, value in zip(options, shown, strict=True):
                ec._put(p, result_set, unit, 0, option, ec.VOTES, value)
            ec._put(p, result_set, unit, 0, None, ec.REGISTERED, got.roll)
            ec._put(p, result_set, unit, 0, None, ec.VALID, (got.first or 0) + (got.second or 0))
        ec._put(p, result_set, unit, 0, None, ec.CAST, got.cast)
        ec._put(p, result_set, unit, 0, None, ec.INVALID, (got.informal or 0) + (got.invalid or 0))
        for field in sums:
            sums[field] += getattr(got, field) or 0
    printed = {
        "roll": total.roll,
        "cast": total.cast,
        "first": total.first,
        "second": total.second,
        "informal": total.informal,
        "invalid": total.invalid,
    }
    if sums != printed:
        raise ValueError(f"{each.page}: the electorates sum to {sums}, but the printed total is {printed}")
    if (total.first or 0) + (total.second or 0) + (total.informal or 0) != total.valid or (total.valid or 0) + (
        total.invalid or 0
    ) != total.cast:
        raise ValueError(f"{each.page}: the printed total does not add up: {total}")
    for option, value in zip(options, (total.first, total.second), strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=ec.VOTES, value=value)
    p.add(
        "declared",
        result_set_id=result_set,
        option_id=None,
        measure=ec.VALID,
        value=(total.first or 0) + (total.second or 0),
    )
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.CAST, value=total.cast)
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.REGISTERED, value=total.roll)
    p.add(
        "declared",
        result_set_id=result_set,
        option_id=None,
        measure=ec.INVALID,
        value=(total.informal or 0) + (total.invalid or 0),
    )
    return p


METHOD = (
    "Counted by electorate and published by the Electoral Commission. Valid votes are the votes for the two answers; "
    "informal votes, which do not clearly show an answer, and invalid votes, which the count leaves out, are stored "
    "together as invalid. Votes from electors whom the Commission could not place in an electorate are one unit of "
    "their own."
)
METHOD_2015 = (
    "Counted nationally by preferential voting, as the Electoral Commission's report by count prints it: each "
    "option's progressive total after each count, from the first preferences to the last count between the two "
    "options left, with the votes that no longer transfer. Informal and invalid votes are stored together as invalid. "
    "The Commission published turnout by electorate, with the electors on the roll, the votes received, and the "
    "valid votes (which include informal ones), but no count of the options by electorate."
)


def units(p: Package, each: Referendum, rows: list[tuple[int | None, str, Count]]) -> dict[int | str, str]:
    made: dict[int | str, str] = {}
    for code, name, _ in rows:
        key = name if code is None else code
        unit = made[key] = ec.key("unit", f"referendum-{each.day}", key)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ec.NEW_ZEALAND,
            scheme_code=ec.ELECTORATE_SCHEME,
            code=f"{each.day}-referendum:{code if code is not None else 'unplaced'}",
            area_relation="unlocated",
            area_id=None,
            kind="standard" if code is not None else "aggregate",
        )
        p.name(unit, name, "en", "official")
    return made


def build_2015(each: Referendum) -> Package:
    import xlrd

    day = date.fromisoformat(each.day)
    p = Package(
        each.package,
        SOURCE,
        "The first 2015 New Zealand flag referendum, with the preferential count and the turnout by electorate, "
        "from the Electoral Commission's results",
        depends=[REGISTRY],
    )
    p.external.add(ec.NEW_ZEALAND)
    for url in urls(each):
        p.uses(ec.art(url))
    system = SYSTEM_PREFERENTIAL
    p.add("electoral_system", id=system, polity_id=ec.NEW_ZEALAND, rules={"formula": "irv"}, rules_version=1)
    p.add(
        "note",
        id=ec.key("note", system),
        entity_id=system,
        kind="methodology",
        body_md=(
            "Each voter ranked the five alternative flags. The option with the fewest votes was excluded in turn "
            "and its votes passed to the next preference, until one option held a majority of the votes still in the "
            "count."
        ),
        lang="en",
    )
    election = ec._election(p, f"referendum-{each.day}", day, "referendum", each.title, f"nz/{each.day}-referendum")
    contest = ec._contest(
        p,
        f"referendum-{each.day}",
        election,
        "ballot_question",
        each.title,
        f"nz/{each.day}-referendum/question",
        electoral_system_id=system,
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.add(
        "note",
        id=ec.key("note", contest, "question"),
        entity_id=contest,
        kind="context",
        body_md=f"The referendum asked the question: “{each.question}” Voters ranked five alternative flags.",
        lang="en",
    )
    result_set = ec._result_set(p, election, contest, f"referendum-{each.day}", METHOD_2015)
    options = {}
    for position, (letter, name) in enumerate(OPTIONS_2015, 1):
        option = options[letter] = ec.key("option", f"referendum-{each.day}", letter)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=f"Option {letter}: {name}",
        )
    sheet = xlrd.open_workbook(str(ec.art(each.extra[0]).path)).sheet_by_index(0)
    lines = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    nation = ec.key("unit", f"referendum-{each.day}", "nation")
    p.add(
        "reporting_unit",
        id=nation,
        polity_id=ec.NEW_ZEALAND,
        scheme_code=ec.NATION_SCHEME,
        code=f"{each.day}-referendum:nation",
        area_relation="unlocated",
        area_id=None,
        kind="aggregate",
    )
    p.name(nation, "New Zealand", "en", "official")
    first = ending = None
    found = 0
    for line in lines:
        label = str(line[0]).strip()
        numbers = [value for value in line[1:] if value != ""]
        if label.startswith("Option "):
            letter = label.split(":")[0].split()[1]
            running = [v for v in numbers if isinstance(v, float)]
            if label.split(":", 1)[1].strip().split(" (")[0] != dict(OPTIONS_2015)[letter].split(" (")[0]:
                raise ValueError(f"{each.extra[0]}: {label} is not option {letter}")
            progressive = [running[0], *running[2::2]] if len(running) > 1 else running
            for count, value in enumerate(progressive, 1):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=nation,
                    option_id=options[letter],
                    channel=0,
                    measure=ec.VOTES,
                    count_round=count,
                    flags=0,
                    value=int(value),
                )
            found += 1
        elif label == "Non-transferable Votes" and numbers and isinstance(numbers[-1], float):
            cumulative = [v for v in line[1:] if isinstance(v, float)]
            for count, value in zip((2, 3, 4), (cumulative[1], cumulative[3], cumulative[5]), strict=False):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=nation,
                    option_id=None,
                    channel=0,
                    measure=21,
                    count_round=count,
                    flags=0,
                    value=int(value),
                )
        elif label == "Informal Votes" and numbers and isinstance(numbers[0], float):
            informal = int(numbers[0])
        elif label == "Invalid Votes" and numbers and isinstance(numbers[0], float):
            invalid = int(numbers[0])
        elif label == "Total Votes Received":
            first = int(numbers[0])
        elif label == "Total Votes in Count":
            ending = [int(v) for v in numbers]
    if found != len(OPTIONS_2015) or first is None or ending is None:
        raise ValueError(f"{each.extra[0]}: the count sheet is not laid out as expected")
    if ending[0] + informal != first - invalid:
        raise ValueError(
            f"{each.extra[0]}: first preferences, informal, and invalid votes do not add to the votes received"
        )
    ec._put(p, result_set, nation, 0, None, ec.VALID, ending[0])
    ec._put(p, result_set, nation, 0, None, ec.INVALID, informal + invalid)
    rows: list[tuple[int | None, str, list[int | None]]] = []
    total: list[int | None] | None = None
    for row in table(each.page):
        if len(row) != 5 or not (row[0][:1].isdigit() or row[0] in ("TOTAL", "Elector not identifiable")):
            continue
        values = [number(cell) for cell in (row[1], row[2], row[4])]
        if row[0] == "TOTAL":
            total = values
        elif row[0][:1].isdigit():
            rows.append((*electorate(row[0]), values))
        else:
            rows.append((None, row[0], values))
    if total is None:
        raise ValueError(f"{each.page}: no total row")
    sums = [0, 0, 0]
    for code, name, values in rows:
        unit = ec.key("unit", f"referendum-{each.day}", name if code is None else code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ec.NEW_ZEALAND,
            scheme_code=ec.ELECTORATE_SCHEME,
            code=f"{each.day}-referendum:{code if code is not None else 'unplaced'}",
            area_relation="unlocated",
            area_id=None,
            kind="standard" if code is not None else "aggregate",
        )
        p.name(unit, name, "en", "official")
        ec._put(p, result_set, unit, 0, None, ec.REGISTERED, values[0])
        ec._put(p, result_set, unit, 0, None, ec.CAST, values[1])
        sums = [a + (b or 0) for a, b in zip(sums, values, strict=True)]
    if sums[:2] != total[:2] or total[1] != first:
        raise ValueError(f"{each.page}: the electorates sum to {sums}, but the printed total is {total}")
    if total[2] != first - invalid:
        raise ValueError(f"{each.page}: the valid votes {total[2]} differ from the count sheet's {first - invalid}")
    for measure, value in ((ec.REGISTERED, total[0]), (ec.CAST, total[1])):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.VALID, value=ending[0])
    p.add("declared", result_set_id=result_set, option_id=None, measure=ec.INVALID, value=informal + invalid)
    return p
