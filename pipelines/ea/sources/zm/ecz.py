"""Zambia: every election the Electoral Commission of Zambia publishes results for, at its finest published unit.

The Commission publishes its results as PDF public notices: presidential and National Assembly results by
constituency, mayoral and council chairperson results by district, councillor results by ward, and one notice for each
by-election date. Each notice's text layer is read in `ea.sources.zm.text`.

The `geo` package, built in `ea.sources.zm.shapes`, holds the shapes of the 156 constituencies contested from 2016 to
2021 and of the wards of 2021. Each unit of those plans is linked to its area; the 150 constituencies before 2016,
the 226 of 2026, and the wards before 2021 have no open shapes yet, so their units stay unlocated.
"""

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.zm import shapes, text
from ea.sources.zm.text import National, Unit

SOURCE = "zm.ecz"
ZAMBIA = str(uuid5(package.NAMESPACE, "polity\x1fZM"))
UPLOADS = "https://www.elections.org.zm/wp-content/uploads"
PAGE = "https://www.elections.org.zm/?page_id=4293"
BY_PAGE = "https://www.elections.org.zm/?page_id=4581"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

GENERAL = {
    "1996": f"{UPLOADS}/2023/07/1996-Presidential-Election-Results.pdf",
    "2001": f"{UPLOADS}/2023/07/2001-Presidential-Election-Results.pdf",
    "2006": f"{UPLOADS}/2023/07/2006-Presidential-Election-Results.pdf",
    "2008": f"{UPLOADS}/2023/07/2008-Presidential-Election-Results.pdf",
    "2011-president": f"{UPLOADS}/2022/09/2011-Presidential-Election-Results.pdf",
    "2011-assembly": f"{UPLOADS}/2022/09/2011-National-Assembly-Elections-Results.pdf",
    "2011-councillor": f"{UPLOADS}/2022/09/2011-LGE-Results.pdf",
    "2015": f"{UPLOADS}/2022/09/2015-Presidential-Election-Results-Public-Notice.pdf",
    "2016-president": f"{UPLOADS}/2022/09/2016-PresidentialResults2016.pdf",
    "2016-assembly": f"{UPLOADS}/2022/09/2016-ParliamentaryPublicNotice.pdf",
    "2016-mayor": f"{UPLOADS}/2022/11/2016MayoralResults.pdf",
    "2016-councillor": f"{UPLOADS}/2022/09/2016-LGE-Results.pdf",
    "2021-president": f"{UPLOADS}/2022/09/2021-PresidentialResultsPerConst.pdf",
    "2021-assembly": f"{UPLOADS}/2022/09/2021-ParliamentaryResults.pdf",
    "2021-mayor": f"{UPLOADS}/2022/09/2021-MayoralResults.pdf",
    "2021-councillor": f"{UPLOADS}/2022/09/2021-CouncilorResults.pdf",
    "2026": f"{UPLOADS}/2026-Presidential-Election-Results-By-Constituency-FINAL.pdf",
}
BY_ELECTIONS = [
    f"{UPLOADS}/2022/11/October2021Results.pdf",
    f"{UPLOADS}/2022/11/Jan2022.pdf",
    f"{UPLOADS}/2022/11/Feb2022.pdf",
    f"{UPLOADS}/2022/11/Apr2022.pdf",
    f"{UPLOADS}/2022/11/June2022.pdf",
    f"{UPLOADS}/2022/11/Sept2022.pdf",
    f"{UPLOADS}/2022/11/Oct2022.pdf",
    f"{UPLOADS}/2022/11/Nov2022.pdf",
    f"{UPLOADS}/2023/04/April2023v3.pdf",
    f"{UPLOADS}/2023_October.pdf",
    f"{UPLOADS}/2023_December.pdf",
    f"{UPLOADS}/2024_January.pdf",
    f"{UPLOADS}/2024_February.pdf",
    f"{UPLOADS}/2024_March.pdf",
    f"{UPLOADS}/2024_May.pdf",
    f"{UPLOADS}/July-2024.pdf",
    f"{UPLOADS}/August-2024.pdf",
    f"{UPLOADS}/October-2024.pdf",
    f"{UPLOADS}/December2024.pdf",
    f"{UPLOADS}/February-2025-1.pdf",
    f"{UPLOADS}/April-2025-1.pdf",
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECZ_SOURCE = key("source", "ecz")
ASSEMBLY = key("body", "national_assembly")
COUNCILS = key("body", "councils")
PRESIDENCY = key("office", "president")
MAYORALTY = key("office", "mayor")
SYSTEM_FPTP = key("electoral_system", "fptp")
SYSTEM_PLURALITY = key("electoral_system", "president", "plurality")
SYSTEM_MAJORITY = key("electoral_system", "president", "majority")
MAJORITY_FROM = date(2016, 1, 5)
INDEPENDENT = {"IND", "INDEP", "INDEPENDENT", "INDEPENDENTS"}

METHOD = (
    "Counted for each {unit} and published by the Electoral Commission of Zambia in a public notice. Total votes cast "
    "include the rejected ballot papers, and the candidates' votes are the valid votes. The notice lists the "
    "candidates in their order of performance, and it declares no winners, so none are stored."
)


@dataclass(frozen=True)
class Part:
    """One contest family of an election: which notice holds it and which unit it is counted in."""

    kind: str  # president, assembly, mayor, or ward
    name: str  # a GENERAL key, or a by-election URL
    which: str = ""  # which of a multi-part notice's tables


@dataclass(frozen=True)
class Day:
    day: date
    kind: str
    english: str
    parts: tuple[Part, ...]
    printed: str = ""


def general(year: int, day: date, *parts: Part, kind: str = "general", printed: str = "") -> Day:
    label = "presidential by-election" if kind == "by_election" else "general election"
    return Day(day, kind, f"{year} Zambian {label}", parts, printed)


DAYS: dict[date, Day] = {
    d.day: d
    for d in (
        general(1996, date(1996, 11, 18), Part("president", "1996")),
        general(2001, date(2001, 12, 27), Part("president", "2001", "president"), Part("assembly", "2001", "assembly")),
        general(
            2006,
            date(2006, 9, 28),
            Part("president", "2006"),
            printed="28th October 2006",
        ),
        general(2008, date(2008, 10, 30), Part("president", "2008"), kind="by_election"),
        general(
            2011,
            date(2011, 9, 20),
            Part("president", "2011-president"),
            Part("assembly", "2011-assembly"),
            Part("ward", "2011-councillor"),
        ),
        general(2015, date(2015, 1, 20), Part("president", "2015"), kind="by_election"),
        general(
            2016,
            date(2016, 8, 11),
            Part("president", "2016-president"),
            Part("assembly", "2016-assembly"),
            Part("mayor", "2016-mayor"),
            Part("ward", "2016-councillor"),
        ),
        general(
            2021,
            date(2021, 8, 12),
            Part("president", "2021-president"),
            Part("assembly", "2021-assembly"),
            Part("mayor", "2021-mayor"),
            Part("ward", "2021-councillor"),
        ),
        general(2026, date(2026, 8, 13), Part("president", "2026")),
    )
}
NATIONAL = {
    "1996": ("1996", ""),
    "2001": ("listed", "Votes Received per Candidate, NATIONAL TOTALS"),
    "2011-president": ("listed", "Candidate"),
    "2015": ("listed", "NATIONAL TOTALS"),
    "2021-president": ("listed", "NATIONAL TOTALS"),
    "2026": ("2026", ""),
}
UNIT_NOUN = {"president": "constituency", "assembly": "constituency", "mayor": "district", "ward": "ward"}
SCHEME = {"president": "zm.constituency", "assembly": "zm.constituency", "mayor": "zm.district", "ward": "zm.ward"}


def path(name: str) -> Path:
    url = GENERAL.get(name, name)
    return raw.cached(SOURCE, url).path


def urls() -> list[str]:
    return [*GENERAL.values(), *BY_ELECTIONS]


@cache
def by_days() -> dict[date, str]:
    found: dict[date, str] = {}
    for url in BY_ELECTIONS:
        year, month, day = text.by_election_day(raw.cached(SOURCE, url).path)
        if date(year, month, day) in found:
            raise ValueError(f"two by-election notices for {year}-{month:02d}-{day:02d}")
        found[date(year, month, day)] = url
    return dict(sorted(found.items()))


def packages() -> list[str]:
    return [
        f"{SOURCE}/registry",
        f"{SOURCE}/geo",
        *(f"{SOURCE}/{day}" for day in sorted([*DAYS, *by_days()])),
    ]


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, shapes.COD, suffix=".zip")


def parse() -> None:
    registry().write()
    geo().write()
    for day in sorted([*DAYS, *by_days()]):
        build(day).write()


def emit() -> None:
    package.run("load", packages())


@cache
def units(name: str, which: str, kind: str) -> list[Unit]:
    """A notice's units, read by the reader its layout needs."""
    file = path(name)
    if name == "1996":
        return text.read_1996(file)
    if name == "2001":
        president, assembly = text.read_2001(file)
        return president if which == "president" else assembly
    if name == "2006":
        return text.read_2006(file)
    if name == "2008":
        return text.read_2008(file)
    if name in ("2016-assembly", "2021-assembly", "2016-mayor", "2021-mayor"):
        return text.read_notice(file)
    if kind == "ward":
        return text.read_wards(file)
    return text.read_listed(file)


@cache
def national(name: str) -> National | None:
    spec = NATIONAL.get(name)
    if spec is None:
        return None
    kind, marker = spec
    if kind == "2026":
        return text.national_2026(path(name))
    if kind == "1996":
        found = text.national_1996(path(name))
        found.votes = {candidate_1996(who)[0]: votes for who, votes in found.votes.items()}
        return found
    return text.national_listed(path(name), marker)


@cache
def constituencies() -> dict[int, str]:
    """The 150 constituencies' full names, from the 2008 notice, which prints each in full."""
    return {int(u.code): u.name for u in text.read_2008(path("2008"))}


def despaced(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


def display(name: str) -> str:
    """A place printed in capitals, in title case; names already in mixed case stay as printed."""
    name = " ".join(name.split())
    if name != name.upper():
        return name
    out = name.title()
    return re.sub(r"'([A-Z])", lambda found: "'" + found[1].lower(), out)


def place(name: str, number: int | None, year: int) -> str:
    """A constituency's name. The 1996 and 2001 notices truncate or break some, so those match the 2008 list."""
    if year <= 2001 and number is not None and number in constituencies():
        full = constituencies()[number]
        short, target = despaced(name), despaced(full)
        if target.startswith(short) or (year == 1996 and SequenceMatcher(None, short, target).ratio() >= 0.8):
            return display(full)
    return display(name)


CANDIDATES_1996 = {
    "CHAKOMBOKA": ("CHAKOMBOKA Chama M.", "MDP"),
    "CHILUBA": ("CHILUBA Frederick J. T.", "MMD"),
    "MBIKUSITA": ("MBIKUSITA Lewanika A.", "AZ"),
    "MULEMBA": ("MULEMBA Humphrey", "NP"),
    "MUNGOMBA": ("MUNG'OMBA Dean N.", "ZDC"),
}


def candidate_1996(raw_name: str) -> tuple[str, str]:
    letters = despaced(raw_name)
    for surname, found in CANDIDATES_1996.items():
        if letters.startswith(surname):
            return found
    raise ValueError(f"unknown 1996 candidate {raw_name!r}")


def party_label(party: str) -> str:
    """A party as the notices abbreviate it; a footnote asterisk is dropped and `M M D` is read as `MMD`."""
    label = re.sub(r"\s+", " ", party.replace(".", "").replace("*", "").upper()).strip()
    return "MMD" if label == "M M D" else label


def independent(party: str) -> bool:
    return party_label(party) in INDEPENDENT


def party_id(party: str) -> str | None:
    return key("party", party_label(party)) if party_label(party) else None


def every_party() -> set[str]:
    found: set[str] = set()
    for day in [*DAYS, *by_days()]:
        for part, notice in parts(day):
            for unit in notice:
                for candidate in unit.candidates:
                    label = candidate_1996(candidate.name)[1] if part.name == "1996" else candidate.party
                    found.add(party_label(label))
    return found - INDEPENDENT - {""}


def parts(day: date) -> list[tuple[Part, list[Unit]]]:
    if day in DAYS:
        return [(part, units(part.name, part.which, part.kind)) for part in DAYS[day].parts]
    tables = text.read_by_election(path(by_days()[day]))
    pairs = (("chair", "mayor"), ("assembly", "assembly"), ("ward", "ward"))
    return [(Part(kind, by_days()[day], table), tables[table]) for table, kind in pairs if tables[table]]


@cache
def seats() -> dict[int, str]:
    """The 156 constituencies of the 2016 plan by number, named as the 2021 presidential notice prints them."""
    return {int(number(u, "president")): display(u.name) for u in units("2021-president", "", "president")}


def _ward_district(part: Part, u: Unit) -> str | None:
    """The dataset's p-code of the district a ward lies in: a general notice's ward code carries it, and a
    by-election notice names the district."""
    if part.name in GENERAL:
        return f"ZM{u.code[3:9]}"
    if u.district:
        return shapes.district_code(u.district)
    # the North-Western rows run the district into the province, as in "North-Western Mushindamo"
    words = u.province.split()
    return next((code for i in range(1, len(words)) if (code := shapes.district_code(" ".join(words[i:])))), None)


@cache
def ward_names() -> dict[str, str]:
    """The 2021 councillor notice's name for each ward it matches in the boundary dataset, by p-code."""
    found: dict[str, str] = {}
    part = Part("ward", "2021-councillor")
    for u in units(part.name, "", part.kind):
        district = _ward_district(part, u)
        place = shapes.ward(district, u.name, u.parent_name)
        if place is not None:
            found[place.pcode] = display(u.name)
    return found


def geo() -> Package:
    return shapes.geo(seats(), ward_names())


def area(day: date, part: Part, u: Unit, code: str) -> str | None:
    """The area of the 2016 constituency plan or the 2021 ward plan that a unit reports, if its plan has shapes."""
    if part.kind in ("president", "assembly") and shapes.PLAN_2016 <= day < shapes.PLAN_2026:
        found = shapes.constituency(u.name)
        expected = shapes.constituency(seats()[int(code)])
        if found is None or found != expected:
            raise ValueError(
                f"{day}: constituency {code} {u.name!r} does not match the 2016 plan's {seats()[int(code)]}"
            )
        return shapes.constituency_area(int(code))
    if part.kind == "ward" and shapes.WARDS_2021 <= day < shapes.PLAN_2026:
        district = _ward_district(part, u)
        place = shapes.ward(district, u.name, u.parent_name)
        return shapes.ward_area(place.pcode) if place else None
    return None


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Zambia's parties, National Assembly, councils, presidency, mayoralty, and electoral systems, from the "
        "Electoral Commission of Zambia's results notices",
    )
    p.external.add(ZAMBIA)
    p.add(
        "source",
        id=ECZ_SOURCE,
        kind="official_body",
        publisher="Electoral Commission of Zambia",
        title="Election results public notices published by the Electoral Commission of Zambia",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Electoral Commission of Zambia",
    )
    for url in urls():
        p.artifact(raw.cached(SOURCE, url), ECZ_SOURCE)
    for code, rank, kind, noun in (
        ("zm.district", 2, "administrative", "district"),
        ("zm.constituency", 3, "electoral_district", "constituency"),
        ("zm.ward", 4, "electoral_district", "ward"),
    ):
        p.add("scheme", code=code, polity_id=ZAMBIA, kind=kind, rank=rank, noun=noun)
    p.add("body", id=ASSEMBLY, polity_id=ZAMBIA, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Zambia", "en", "official")
    p.add("body", id=COUNCILS, polity_id=ZAMBIA, kind="council")
    p.name(COUNCILS, "Local government councils of Zambia", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=ZAMBIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Zambia", "en", "official")
    p.add("office", id=MAYORALTY, polity_id=ZAMBIA, kind="mayor")
    p.name(MAYORALTY, "Mayor or council chairperson", "en", "official")
    for id, since, until, rules, method in (
        (
            SYSTEM_FPTP,
            None,
            None,
            {"formula": "fptp"},
            "One member, mayor, or council chairperson for each constituency, ward, or district, elected by the most votes.",
        ),
        (
            SYSTEM_PLURALITY,
            None,
            MAJORITY_FROM,
            {"formula": "fptp"},
            "The candidate with the most votes in the single national count is elected President.",
        ),
        (
            SYSTEM_MAJORITY,
            MAJORITY_FROM,
            None,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "The candidate with more than half of the valid votes is elected President; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
    ):
        p.add(
            "electoral_system", id=id, polity_id=ZAMBIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for label in sorted(every_party()):
        id = key("party", label)
        p.add("party", id=id, kind="bloc" if "(" in label else "party", polity_id=ZAMBIA)
        p.name(id, label, "en", "abbreviation")
    return p


def number(u: Unit, kind: str, name: str = "") -> str:
    """A unit's code within its election: constituencies by their number, the others as printed.

    The 2016 mayoral notice numbers districts afresh in each province, so its codes carry the province.
    """
    if kind in ("president", "assembly"):
        return f"{int(u.code[-4:]):03d}"
    if name == "2016-mayor":
        return f"{changeset.slug(u.province)}-{u.code}"
    return u.code


class Counter:
    """Collects the accounting caveats of one package, so each is accepted with the units that cause it."""

    def __init__(self) -> None:
        self.valid: list[str] = []
        self.sums: list[str] = []
        self.over: list[str] = []
        self.declared: list[str] = []

    def unit(self, label: str, u: Unit, valid: int | None, rejected: int) -> None:
        total = sum(c.votes for c in u.candidates)
        if valid is not None and total != valid:
            self.valid.append(f"{label} (candidates {total:,}, valid votes {valid:,})")
        if valid is not None and u.cast is not None and valid + rejected != u.cast:
            self.sums.append(f"{label} (valid {valid:,} + rejected {rejected:,}, cast {u.cast:,})")
        if u.registered is not None and u.cast is not None and u.cast > u.registered:
            self.over.append(f"{label} (cast {u.cast:,}, registered {u.registered:,})")

    def accept(self, p: Package) -> None:
        for code, found, text_ in (
            ("accounting.votes_valid", self.valid, "The candidates' votes differ from the valid votes in"),
            (
                "accounting.ballots_sum",
                self.sums,
                "The valid votes and rejected ballots do not add up to the ballots cast in",
            ),
            ("accounting.turnout", self.over, "The ballots cast exceed the registered voters in"),
            ("accounting.declared", self.declared, "The notice's own national totals differ from its unit rows: "),
        ):
            if found:
                shown = "; ".join(found[:15]) + (f"; and {len(found) - 15} more" if len(found) > 15 else "")
                p.accept(code, f"{text_} {len(found)} units of the notice, each stored as printed: {shown}")


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def build(day: date) -> Package:
    election = DAYS.get(day)
    held_by = day not in DAYS
    kind = "by_election" if held_by else election.kind if election else "general"
    english = f"{day.day} {day:%B %Y} Zambian by-elections" if held_by else election.english if election else ""
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Zambian {'by-elections' if held_by else 'election'} of {day.day} {day:%B %Y}, by constituency, district, "
        "and ward, from the Electoral Commission of Zambia's public notices",
        depends=[f"{SOURCE}/registry", *([f"{SOURCE}/geo"] if shapes.PLAN_2016 <= day < shapes.PLAN_2026 else [])],
    )
    p.external.add(ZAMBIA)
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=ZAMBIA,
        kind=kind,
        date_from=day,
        date_to=day,
        status="held",
        original_text=election.printed or None if election else None,
    )
    p.add("slug", path=f"zm/{day}", entity_id=id, is_current=True)
    p.name(id, english, "en", "official")
    if election and election.printed:
        p.add(
            "note",
            id=key("note", id, "date"),
            entity_id=id,
            kind="caveat",
            body_md=f"The Commission's notice prints the polling date as {election.printed}. The election was held on "
            f"{day.day} {day:%B %Y}, and that date is stored.",
            lang="en",
        )
    counter = Counter()
    placed: dict[str, str] = {}
    for part, notice in parts(day):
        p.uses(raw.cached(SOURCE, GENERAL.get(part.name, part.name)))
        _part(p, day, id, part, notice, placed, counter)
    counter.accept(p)
    located = [row["area_id"] for row in p.rows.get("reporting_unit", []) if row["area_id"]]
    if len(located) != len(set(located)):
        raise ValueError(f"{day}: two units report the same area")
    if day == date(1996, 11, 18):
        p.accept(
            "accounting.declared",
            "The notice's national totals count 143 more valid votes and ballots cast (1,258,805 and 1,325,053) than "
            "the sum of its constituency rows (1,258,662 and 1,324,910). The difference is the Sesheke row, whose "
            "printed valid votes (5,691) fall 143 short of its own candidates' votes (5,834) while the national "
            "totals include the candidates' votes. Each is stored as printed.",
        )
    return p


def _unit(p: Package, day: date, part: Part, u: Unit, placed: dict[str, str]) -> str:
    scheme = SCHEME[part.kind]
    year = day.year
    code = number(u, part.kind, part.name)
    if part.kind in ("president", "assembly"):
        name = place(u.name, int(code), year)
    elif part.kind == "ward":
        name = (
            f"{display(u.name)}, {place(u.parent_name, None, year)} constituency" if u.parent_name else display(u.name)
        )
        if not u.parent_name and u.district:
            name = f"{display(u.name)}, {display(u.district)}"
    else:
        name = display(u.name)
    unit = key("unit", day, scheme, code)
    if unit not in placed:
        placed[unit] = name
        located = area(day, part, u, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ZAMBIA,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals" if located else "unlocated",
            area_id=located,
            kind="standard",
        )
        p.name(unit, name, "en", "official")
        if located:
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=located,
                method="source_code",
                approximate=False,
                partial=False,
            )
    return unit


def _contest(
    p: Package, day: date, election: str, part: Part, tag: str, name: str, slug: str, magnitude: int = 1
) -> tuple[str, str]:
    contest = key("contest", tag)
    president = part.kind == "president"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if part.kind in ("president", "mayor") else "body_seats",
        office_id=PRESIDENCY if president else MAYORALTY if part.kind == "mayor" else None,
        body_id=ASSEMBLY if part.kind == "assembly" else COUNCILS if part.kind == "ward" else None,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=(SYSTEM_MAJORITY if day >= MAJORITY_FROM else SYSTEM_PLURALITY)
        if president
        else SYSTEM_FPTP,
        default_result_kind="counted",
    )
    p.name(contest, name, "en", "official")
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ECZ_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD.format(unit="reporting unit" if president else UNIT_NOUN[part.kind]),
        lang="en",
    )
    return contest, result_set


def _option(
    p: Package, tag: str, contest: str, order: int, name: str, party: str, gender: str, people: set[str]
) -> str:
    solo = independent(party)
    person = key("person", tag, name)
    if person not in people:
        people.add(person)
        p.add("person", id=person, gender={"M": "male", "F": "female"}.get(gender))
        p.name(person, name, "en", "official")
    option = key("option", tag, name, party)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=None if solo else party_id(party),
        ballot_label=name,
    )
    p.add(
        "candidacy",
        id=key("candidacy", tag, name, party),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=name,
        member_of_party_id=None if solo else party_id(party),
        is_independent=solo,
    )
    return option


def _values(
    p: Package,
    result_set: str,
    unit: str,
    options: dict[tuple[str, str], str],
    u: Unit,
    c: Counter,
    year: int,
    label: str,
    part: Part,
) -> None:
    rejected = u.rejected or 0
    valid = u.valid if u.valid is not None else (None if u.cast is None else u.cast - rejected)
    c.unit(label, u, valid, rejected)
    for candidate in u.candidates:
        name, party = candidate_1996(candidate.name) if part.name == "1996" else (candidate.name, candidate.party)
        _put(p, result_set, unit, options[(name, party_label(party))], VOTES, candidate.votes)
    _put(p, result_set, unit, None, REGISTERED, u.registered)
    _put(p, result_set, unit, None, CAST, u.cast)
    _put(p, result_set, unit, None, VALID, valid)
    _put(p, result_set, unit, None, INVALID, u.rejected)


def _part(
    p: Package, day: date, election: str, part: Part, notice: list[Unit], placed: dict[str, str], c: Counter
) -> None:
    year = day.year
    people: set[str] = set()
    if part.kind == "president":
        tag = f"{day}:president"
        contest, result_set = _contest(
            p, day, election, part, tag, f"{year} Zambian presidential election", f"zm/{day}/president"
        )
        options: dict[tuple[str, str], str] = {}
        order = 0
        for u in notice:
            for candidate in u.candidates:
                name, party = (
                    candidate_1996(candidate.name) if part.name == "1996" else (candidate.name, candidate.party)
                )
                if (name, party_label(party)) not in options:
                    order += 1
                    options[(name, party_label(party))] = _option(
                        p, tag, contest, order, name, party, candidate.gender, people
                    )
        for u in notice:
            unit = _unit(p, day, part, u, placed)
            _values(
                p, result_set, unit, options, u, c, year, f"{placed[unit]} ({number(u, part.kind, part.name)})", part
            )
        _declared(p, result_set, part, options, c)
        return
    noun = UNIT_NOUN[part.kind]
    for u in notice:
        unit = _unit(p, day, part, u, placed)
        code = number(u, part.kind, part.name)
        title = {
            "assembly": f"{year} National Assembly election, {placed[unit]} constituency",
            "mayor": f"{year} mayoral or council chairperson election, {placed[unit]}",
            "ward": f"{year} councillor election, {placed[unit]} ward {code}",
        }[part.kind]
        if day not in DAYS:
            title = title.replace(str(year), f"{day.day} {day:%B %Y}", 1)
        tag = f"{day}:{part.kind}:{code}"
        slug = f"zm/{day}/{part.kind}/{code}-{changeset.slug(placed[unit])}"
        contest, result_set = _contest(p, day, election, part, tag, title, slug)
        options = {}
        for order, candidate in enumerate(u.candidates, 1):
            key_ = (candidate.name, party_label(candidate.party))
            options[key_] = _option(p, tag, contest, order, candidate.name, candidate.party, candidate.gender, people)
        _values(p, result_set, unit, options, u, c, year, f"{placed[unit]} ({noun} {code})", part)


def _declared(p: Package, result_set: str, part: Part, options: dict[tuple[str, str], str], c: Counter) -> None:
    printed = national(part.name)
    if printed is None:
        return
    by_name = {name: option for (name, _), option in options.items()}
    by_party = {party: option for (_, party), option in options.items()}
    for who, votes in printed.votes.items():
        option = by_name.get(who) or by_party.get(who)
        if option is None:
            raise ValueError(f"{part.name}: national total for {who!r} matches no candidate")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    valid = printed.valid if printed.valid is not None else None
    for measure, value in (
        (REGISTERED, printed.registered),
        (CAST, printed.cast),
        (VALID, valid),
        (INVALID, printed.rejected),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
