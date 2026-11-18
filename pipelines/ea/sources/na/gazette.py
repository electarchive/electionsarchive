"""Namibia's presidential and National Assembly results of 1994 to 2014, from the Government Gazette notices.

The Electoral Commission of Namibia's site holds no results files for these elections. The Electoral Act, 1992 (section
92) and the Electoral Act, 2014 (section 113) require the Commission to publish the national result in the Government
Gazette, and the Legal Assistance Centre keeps scans of the gazettes. Each notice prints one national table for the
President (votes for each candidate) and one for the National Assembly (votes, seats, and the quota for each party).
The 1994 and 1999 notices are scans with an OCR text layer; the rest are born digital. Every file is read here with
``pdftotext -layout``, and every figure transcribed in this module is proved against that text and against the totals
the notice prints before a package is written.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Literal
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "na.gazette"
NAMIBIA = str(uuid5(package.NAMESPACE, "polity\x1fNA"))
COUNTRY_SCHEME = "na.national"
LAC = "https://www.lac.org.na/laws"
REPORT = "https://www.ecn.na/wp-content/uploads/2026/10/ECN-2026-Annual-Report_Final.pdf"
VOTES, CAST, VALID, INVALID = 1, 3, 4, 5

# Whether a notice's first column counts the rejected ballot papers: ``counted`` includes them, ``valid`` leaves them out.
Basis = Literal["counted", "valid"]

Party = tuple[str, int, int]  # label as printed, votes, seats
INDEPENDENT = "Independent Candidate"
Candidate = tuple[str, int, str | None]  # name as printed, votes, party as printed


@dataclass(frozen=True)
class Notice:
    day: str
    end: date
    gazette: int
    url: str
    published: str
    president_total: int
    president_rejected: int | None
    president_basis: Basis
    president: tuple[Candidate, ...]
    winner: str
    assembly_total: int
    assembly_rejected: int
    assembly_basis: Basis
    quota: int
    assembly: tuple[Party, ...]
    note: str = ""
    assembly_note: str = ""
    # A later notice that prints the National Assembly result, where the presidential notice does not.
    assembly_gazette: int = 0
    assembly_url: str = ""
    assembly_published: str = ""

    @property
    def urls(self) -> tuple[str, ...]:
        return (self.url, self.assembly_url) if self.assembly_url else (self.url,)

    @property
    def seats(self) -> int:
        return 96 if self.day >= "2014" else 72


NOTICES = (
    Notice(
        day="1994-12-07",
        end=date(1994, 12, 8),
        gazette=1003,
        url=f"{LAC}/1994/1003.pdf",
        published="23 December 1994",
        president_total=497508,
        president_rejected=12213,
        president_basis="counted",
        president=(("Mishake Muyongo", 114843, None), ("Sam Shafiishuna Nujoma", 370452, None)),
        winner="Sam Shafiishuna Nujoma",
        assembly_total=497499,
        assembly_rejected=7863,
        assembly_basis="counted",
        quota=6801,
        assembly=(
            ("Democratic Coalition of Namibia", 4058, 1),
            ("DTA of Namibia", 101748, 15),
            ("Federal Convention of Namibia", 1166, 0),
            ("Monitor Action Group", 4005, 1),
            ("SWANU of Namibia", 2598, 0),
            ("SWAPO-Party", 361800, 53),
            ("United Democratic Front of Namibia", 13309, 2),
            ("Workers Revolutionary Party", 952, 0),
        ),
    ),
    Notice(
        day="1999-11-30",
        end=date(1999, 12, 1),
        gazette=2261,
        url=f"{LAC}/2000/2261.pdf",
        published="20 January 2000",
        president_total=538525,
        president_rejected=6616,
        president_basis="valid",
        president=(
            ("Justus Garoëb", 16272, None),
            ("Katuutire Kaura", 51939, None),
            ("Samuel Shafiishuna Nujoma", 414096, None),
            ("Benjamin Ulenga", 56541, None),
        ),
        winner="Samuel Shafiishuna Nujoma",
        assembly_total=536036,
        assembly_rejected=5075,
        assembly_basis="valid",
        quota=7445,
        assembly=(
            ("Congress of Democrats", 53289, 7),
            ("Democratic Coalition of Namibia", 1797, 0),
            ("DTA of Namibia", 50824, 7),
            ("Federal Convention of Namibia", 764, 0),
            ("Monitor Action Group", 3618, 1),
            ("SWANU of Namibia", 1885, 0),
            ("SWAPO-Party of Namibia", 408174, 55),
            ("United Democratic Front of Namibia", 15685, 2),
        ),
        note=(
            "The notice prints 538,525 as the total number of votes in the presidential election, but the four "
            "candidates' votes add up to 538,848. The notice's National Assembly total equals its party votes."
        ),
    ),
    Notice(
        day="2004-11-15",
        end=date(2004, 11, 16),
        gazette=3366,
        url=f"{LAC}/2005/3366.pdf",
        published="3 January 2005",
        president_total=833165,
        president_rejected=14770,
        president_basis="counted",
        president=(
            ("Benjamin Ulenga", 59547, None),
            ("Katuutire Kaura", 41905, None),
            ("Jacobus Willem Francois Pretorius", 9378, None),
            ("Kuaima Riruako", 34651, None),
            ("Henry Ferdinand Mudge", 15955, None),
            ("Hifikepunye Pohamba", 625605, None),
            ("Justus Garoëb", 31354, None),
        ),
        winner="Hifikepunye Pohamba",
        assembly_total=827042,
        assembly_rejected=11405,
        assembly_basis="counted",
        quota=11305,
        assembly=(
            ("Congress of Democrats", 59465, 5),
            ("DTA of Namibia", 41714, 4),
            ("Monitor Action Group", 6920, 1),
            ("Namibia Democratic Movement for Change", 4138, 0),
            ("National Unity Democratic Organisation of Namibia", 33874, 3),
            ("Republican Party of Namibia", 15965, 1),
            ("SWANU of Namibia", 3438, 0),
            ("SWAPO-Party of Namibia", 620787, 55),
            ("United Democratic Front of Namibia", 29336, 3),
        ),
    ),
    Notice(
        day="2009-11-27",
        end=date(2009, 11, 28),
        gazette=4397,
        url=f"{LAC}/2009/4397.pdf",
        published="18 December 2009",
        president_total=799870,
        president_rejected=12363,
        president_basis="valid",
        president=(
            ("Attic Beukes", 1005, None),
            ("Justus //Garoeb", 19258, None),
            ("Frans Mikub /Goagoseb", 1760, None),
            ("Hidipo Livius Hamutenya", 88640, None),
            ("Salomon David Isaacs", 1859, None),
            ("Katuutire Kaura", 24186, None),
            ("Usutuaije Maamberua", 2968, None),
            ("Henry Ferdinand Mudge", 9425, None),
            ("Hifikepunye Pohamba", 611241, None),
            ("Kuiama Riruako", 23735, None),
            ("Ignatius Kotongo Shixwameni", 9981, None),
            ("Ulenga Benjamin Ulenga", 5812, None),
        ),
        winner="Hifikepunye Pohamba",
        assembly_total=800567,
        assembly_rejected=10576,
        assembly_basis="valid",
        quota=11118,
        assembly=(
            ("All People’s Party", 10795, 1),
            ("Communist Party", 810, 0),
            ("Congress of Democrats", 5375, 1),
            ("Democratic Party of Namibia", 1942, 0),
            ("DTA of Namibia", 25393, 2),
            ("Monitor Aksie Groep", 4718, 0),
            ("Namibia Democratic Movement for Change", 1770, 0),
            ("National Democratic Party", 1187, 0),
            ("National Unity Democratic Organisation of Namibia", 24422, 2),
            ("Rally for Democracy and Progress", 90556, 8),
            ("Republican Party of Namibia", 6541, 1),
            ("South West Africa National Union", 4989, 1),
            ("SWAPO Party of Namibia", 602580, 54),
            ("United Democratic Front of Namibia", 19489, 2),
        ),
    ),
    Notice(
        day="2019-11-27",
        end=date(2019, 11, 27),
        gazette=7126,
        url=f"{LAC}/2020/7126.pdf",
        published="21 February 2020",
        president_total=826198,
        president_rejected=None,
        president_basis="valid",
        president=(
            ("Apius Auchab", 22115, "United Democratic Front of Namibia"),
            ("Hage Gottfried Geingob", 464703, "SWAPO Party of Namibia"),
            ("Tangeni Cornelius Iijambo", 5959, "SWANU of Namibia"),
            ("Panduleni Filemon Bango Itula", 242657, INDEPENDENT),
            ("Kamboto Ratoveni Mike Kavekotora", 3515, "Rally for Democracy and Progress"),
            ("Henry Ferdinand Mudge", 4379, "Republican Party of Namibia"),
            ("Ester Muinjangue", 12039, "National Unity Democratic Organisation of Namibia"),
            ("Jan Mulinasho Epafras Mukwilongo", 1026, "Namibia Economic Freedom Fighters"),
            ("Ignatius Nkotongo Shixwameni", 3304, "All People’s Party"),
            ("Bernardus Swartbooi", 22542, "Landless People’s Movement"),
            ("McHenry Kanyonokere Venaani", 43959, "Popular Democratic Movement"),
        ),
        winner="Hage Gottfried Geingob",
        assembly_total=820227,
        assembly_rejected=0,
        assembly_basis="valid",
        quota=8544,
        assembly=(
            ("All People’s Party", 14644, 2),
            ("Christian Democratic Voice Party", 5841, 1),
            ("Congress of Democrats", 4654, 0),
            ("Landless People’s Movement", 38956, 4),
            ("Namibia Economic Freedom Fighters", 13580, 2),
            ("National Democratic Party of Namibia", 4559, 0),
            ("National Patriotic Front", 1785, 0),
            ("National Unity Democratic Organisation", 16066, 2),
            ("Popular Democratic Movement", 136576, 16),
            ("Rally for Democracy and Progress", 8953, 1),
            ("Republican Party of Namibia", 14546, 2),
            ("SWANU of Namibia", 5330, 1),
            ("SWAPO Party of Namibia", 536861, 63),
            ("United Democratic Front of Namibia", 14644, 2),
            ("Workers Revolutionary Party", 3212, 0),
        ),
        assembly_gazette=7149,
        assembly_url=f"{LAC}/2020/7149.pdf",
        assembly_published="18 March 2020",
    ),
    Notice(
        day="2014-11-28",
        end=date(2014, 11, 28),
        gazette=5641,
        url=f"{LAC}/2014/5641.pdf",
        published="30 December 2014",
        president_total=890738,
        president_rejected=None,
        president_basis="valid",
        president=(
            ("Hage Gottfried Geingob", 772528, "SWAPO Party of Namibia"),
            ("Hidipo Livius Hamutenya", 30197, "Rally for Democracy and Progress"),
            ("Usutuaije Maamberua", 5028, "SWANU of Namibia"),
            ("Asser Mbai", 16740, "National Unity Democratic Organisation of Namibia"),
            ("Henry Ferdinand Mudge", 8676, "Republican Party of Namibia"),
            ("Jan Epafras Mulinasho Mukwiilongo", 2514, "Namibia Economic Freedom Fighters"),
            ("Ignatius Nkotongo Shixwameni", 7266, "All People’s Party"),
            ("Benjamin Ulenga Ulenga", 3518, "Congress of Democrats"),
            ("McHenry Mike Kanjonokere Venaani", 44271, "DTA of Namibia"),
        ),
        winner="Hage Gottfried Geingob",
        assembly_total=893645,
        assembly_rejected=0,
        assembly_basis="valid",
        quota=9308,
        assembly=(
            ("All People’s Party", 20431, 2),
            ("Christian Democratic Voice", 2606, 0),
            ("Congress of Democrats", 3404, 0),
            ("Democratic Party of Namibia", 1131, 0),
            ("DTA of Namibia", 42933, 5),
            ("Monitor Action Group", 3073, 0),
            ("Namibia Economic Freedom Fighters", 3259, 0),
            ("National Democratic Party of Namibia", 1389, 0),
            ("National Unity Democratic Organisation of Namibia", 17942, 2),
            ("Rally for Democracy and Progress", 31372, 3),
            ("Republican Party of Namibia", 6099, 1),
            ("SWANU of Namibia", 6354, 1),
            ("SWAPO Party of Namibia", 715026, 77),
            ("United Democratic Front of Namibia", 18945, 2),
            ("The United People’s Movement of Namibia", 6353, 1),
            ("Workers Revolutionary Party", 13328, 2),
        ),
    ),
)
DAYS = {notice.day: notice for notice in sorted(NOTICES, key=lambda notice: notice.day)}

# What the Commission's 2025/26 annual report prints as the votes cast in each election, for comparison.
REPORTED = {
    "1994-12-07": (497508, 497499),
    "2019-11-27": (826181, 820227),
    "1999-11-30": (545465, 541114),
    "2004-11-15": (833165, 829269),
    "2009-11-27": (812233, 811143),
    "2014-11-28": (875232, 893643),
}

# One party of the archive for each association the notices spell in different ways.
KEYS = {
    "swapo party of namibia": "swapo",
    "swapo-party": "swapo",
    "swapo-party of namibia": "swapo",
    "dta of namibia": "dta",
    "popular democratic movement": "dta",
    "christian democratic voice party": "cdv",
    "christian democratic voice": "cdv",
    "national unity democratic organisation": "nudo",
    "national unity democratic organisation of namibia": "nudo",
    "south west africa national union": "swanu",
    "swanu of namibia": "swanu",
    "monitor action group": "mag",
    "monitor aksie groep": "mag",
    "national democratic party": "ndp",
    "national democratic party of namibia": "ndp",
    "the united people’s movement of namibia": "upm",
}
NAMES = {"swapo": "SWAPO Party of Namibia", "swanu": "SWANU of Namibia", "upm": "United People’s Movement of Namibia"}


def listed_votes(notice: Notice) -> int:
    return sum(votes for _, votes, _ in notice.assembly)


def party_key(label: str) -> str:
    folded = re.sub(r"\s+", " ", label.casefold()).strip()
    return KEYS.get(folded, re.sub(r"[^a-z0-9]+", "-", folded).strip("-"))


def party_name(label: str) -> str:
    return NAMES.get(party_key(label), label)


def ident(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GAZETTE_SOURCE = ident("source", "government-gazette")
REPORT_SOURCE = ident("source", "ecn-annual-report")
ASSEMBLY = ident("body", "national-assembly")
PRESIDENCY = ident("office", "president")
SYSTEM_ASSEMBLY = ident("electoral_system", "national-assembly")
SYSTEM_PRESIDENT = ident("electoral_system", "president")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def fetch() -> None:
    for notice in NOTICES:
        for url in notice.urls:
            raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


@cache
def text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return done.stdout


def printed(page: str, value: int) -> bool:
    """Whether the notice prints the figure, with a space or without one between its thousands."""
    spaced = re.sub(r"\s+", r"\\s+", f"{value:,}".replace(",", " "))
    return bool(re.search(rf"(?<![\d])({spaced}|{value})(?![\d])", page))


def prove(notice: Notice) -> None:
    """Check the transcription against the notice's text and against the totals the notice prints."""
    page = text(notice.url)
    others = text(notice.assembly_url) if notice.assembly_url else page
    figures = [notice.president_total, *(votes for _, votes, _ in notice.president)]
    if notice.president_rejected is not None:
        figures.append(notice.president_rejected)
    assembly = [
        notice.assembly_total,
        notice.assembly_rejected,
        notice.quota,
        *(votes for _, votes, _ in notice.assembly),
    ]
    missing = sorted(
        {value for value in figures if value and not printed(page, value)}
        | {value for value in assembly if value and not printed(others, value)}
    )
    if missing:
        raise ValueError(f"{notice.day}: the notice does not print {missing}")
    seats = sum(seats for _, _, seats in notice.assembly)
    if seats != notice.seats:
        raise ValueError(f"{notice.day}: {seats} seats")
    cast = sum(votes for _, votes, _ in notice.president)
    listed = sum(votes for _, votes, _ in notice.assembly)
    if notice.president_basis == "counted" and cast + (notice.president_rejected or 0) != notice.president_total:
        raise ValueError(f"{notice.day}: the presidential votes do not add up")
    if notice.assembly_basis == "counted" and listed + notice.assembly_rejected != notice.assembly_total:
        raise ValueError(f"{notice.day}: the party votes do not add up")
    if notice.assembly_basis == "valid" and listed != notice.assembly_total and notice.day != "2019-11-27":
        raise ValueError(f"{notice.day}: the party votes add up to {listed}, not {notice.assembly_total}")
    if notice.president_basis == "valid" and cast != notice.president_total and notice.day != "1999-11-30":
        raise ValueError(f"{notice.day}: the candidates' votes add up to {cast}, not {notice.president_total}")
    leader = max(notice.president, key=lambda candidate: candidate[1])[0]
    if leader != notice.winner:
        raise ValueError(f"{notice.day}: {notice.winner} is not the leading candidate")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The National Assembly, the presidency, their electoral systems, and the parties named in the Government "
        "Gazette notices of the national results of 1994 to 2014",
    )
    p.external.add(NAMIBIA)
    p.add(
        "source",
        id=GAZETTE_SOURCE,
        kind="official_body",
        publisher="Electoral Commission of Namibia",
        title="Notifications of results published in the Government Gazette of the Republic of Namibia",
        url=f"{LAC}/",
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission of Namibia, Government Gazette of the Republic of Namibia",
    )
    p.add(
        "source",
        id=REPORT_SOURCE,
        kind="official_body",
        publisher="Electoral Commission of Namibia",
        title="Electoral Commission of Namibia Annual Report 2025/26",
        url=REPORT,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission of Namibia",
    )
    for notice in NOTICES:
        for url in notice.urls:
            p.artifact(raw.cached(SOURCE, url), GAZETTE_SOURCE)
    p.artifact(raw.cached(SOURCE, REPORT), REPORT_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=NAMIBIA, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly of Namibia", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=NAMIBIA, body_id=None, kind="head_of_state")
    p.name(PRESIDENCY, "President of Namibia", "en", "official")
    systems = (
        (
            SYSTEM_ASSEMBLY,
            {"formula": "hare"},
            (
                "Closed party lists in one national constituency. Seats go to each party by largest remainder, "
                "using the quota of votes the Commission prints for the election."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with more than half of the votes counted is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
    )
    for id, rules, body in systems:
        p.add("electoral_system", id=id, polity_id=NAMIBIA, rules=rules, rules_version=1)
        p.add("note", id=ident("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    labels: dict[str, list[str]] = {}
    for notice in NOTICES:
        for label, _, _ in notice.assembly:
            labels.setdefault(party_key(label), []).append(label)
        for _, _, label in notice.president:
            if label and label != INDEPENDENT:
                labels.setdefault(party_key(label), []).append(label)
    for key, spellings in sorted(labels.items()):
        id = ident("party", key)
        p.add("party", id=id, kind="party", polity_id=NAMIBIA)
        names = {party_name(spelling): None for spelling in spellings}
        for position, name in enumerate(names):
            p.name(id, name, "en", "official", position == 0)
        p.add("external_id", entity_id=id, scheme="na.gazette.party", value=key)
    return p


def build(day: str) -> Package:
    notice = DAYS[day]
    prove(notice)
    first = date.fromisoformat(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Namibian presidential and National Assembly elections of {first.year}, national results from "
        "the Government Gazette",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update((NAMIBIA, COUNTRY_SCHEME))
    for url in notice.urls:
        p.uses(raw.cached(SOURCE, url))
    p.uses(raw.cached(SOURCE, REPORT))
    reported_president, reported_assembly = REPORTED[day]
    path = f"na/{day}-national"
    election = ident("election", day)
    p.add(
        "election", id=election, polity_id=NAMIBIA, kind="general", date_from=first, date_to=notice.end, status="held"
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{first.year} Namibian presidential and National Assembly elections", "en", "official")
    unit = ident("unit", day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NAMIBIA,
        scheme_code=COUNTRY_SCHEME,
        code=f"{SOURCE}:{day}:national",
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(unit, "Namibia", "en", "official")

    def cite(tag: str) -> str:
        number, published = (notice.gazette, notice.published)
        if tag == "assembly" and notice.assembly_url:
            number, published = notice.assembly_gazette, notice.assembly_published
        return f"Government Gazette No. {number} of {published}"

    def result_set(tag: str, contest: str, caveat: str) -> str:
        id = ident("result_set", day, tag)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=GAZETTE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=ident("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(
                f"National figures as the Electoral Commission published them in {cite(tag)}. The notice prints no "
                "figures below the national level."
            ),
            lang="en",
        )
        if caveat:
            p.add("note", id=ident("note", id, "caveat"), entity_id=id, kind="caveat", body_md=caveat, lang="en")
        return id

    def put(set_id: str, option: str | None, measure: int, value: int, declare: bool = True) -> None:
        p.add(
            "result_value",
            result_set_id=set_id,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
        if declare:
            p.add("declared", result_set_id=set_id, option_id=option, measure=measure, value=value)

    # the presidency
    contest = ident("contest", day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{first.year} Namibian presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    cast = sum(votes for _, votes, _ in notice.president)
    rows = notice.president_rejected
    caveat = notice.note
    if first.year == 1999:
        caveat += (
            f" The notice's printed total is stored and declared as printed. With the {rows:,} rejected ballot "
            f"papers it implies {notice.president_total + (rows or 0):,} ballots, and the annual report's "
            f"{reported_president:,} votes cast implies {reported_president - (rows or 0):,} votes for the "
            "candidates."
        )
    if first.year == 2019:
        caveat += (
            "The notice prints no rejected ballot papers. The Commission's 2025/26 annual report gives "
            f"{reported_president:,} votes cast in the presidential election, against the notice's "
            f"{notice.president_total:,}, which the candidates' votes add up to."
        )
    if first.year == 2014:
        caveat += (
            f"The notice prints no rejected ballot papers. The Commission's 2025/26 annual report gives "
            f"{reported_president:,} votes cast in the presidential election, which differs from the notice's "
            f"{notice.president_total:,}."
        )
    set_id = result_set("president", contest, caveat.strip())
    if notice.president_basis == "counted":
        put(set_id, None, CAST, notice.president_total)
        put(set_id, None, VALID, cast, declare=False)
    else:
        put(set_id, None, VALID, notice.president_total)
    if rows is not None:
        put(set_id, None, INVALID, rows)
    for position, (name, votes, label) in enumerate(notice.president, 1):
        person = ident("person", day, position, name)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        option = ident("option", day, "president", position)
        party = ident("party", party_key(label)) if label and label != INDEPENDENT else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=ident("candidacy", day, "president", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=party,
            **({"is_independent": True} if label == INDEPENDENT else {}),
        )
        put(set_id, option, VOTES, votes)
        p.add(
            "outcome",
            result_set_id=set_id,
            option_id=option,
            candidacy_id=None,
            kind="elected" if name == notice.winner else "lost",
            seats=None,
        )
    if cast != notice.president_total and notice.president_basis == "valid":
        p.accept(
            "accounting.votes_valid",
            f"The notice prints {notice.president_total:,} as the total number of votes, while the candidates' votes "
            f"add up to {cast:,}; both are stored as printed.",
        )
    # the National Assembly
    contest = ident("contest", day, "assembly")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=ASSEMBLY,
        magnitude=sum(seats for _, _, seats in notice.assembly),
        electoral_system_id=SYSTEM_ASSEMBLY,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{first.year} Namibian National Assembly election", "en", "official")
    p.add("slug", path=f"{path}/national-assembly", entity_id=contest, is_current=True)
    caveat = ""
    if first.year == 1999:
        caveat = (
            f"The annual report gives {reported_assembly:,} votes cast; the notice's {notice.assembly_total:,} votes "
            f"and {notice.assembly_rejected:,} rejected ballot papers make {notice.assembly_total + notice.assembly_rejected:,}."
        )
    if first.year == 2019:
        caveat = (
            "The notice prints no rejected ballot papers. The notice of 18 March 2020 replaces the one the Commission "
            "published on 21 February 2020 and withdraws it. The party votes add up to "
            f"{listed_votes(notice):,}, {notice.assembly_total - listed_votes(notice):,} fewer than the printed total "
            f"of {notice.assembly_total:,}, which the annual report also gives; every figure is stored as printed."
        )
    if first.year == 2014:
        caveat = (
            "The notice prints no rejected ballot papers. The Commission's 2025/26 annual report gives "
            f"{reported_assembly:,} votes cast, two fewer than the notice's {notice.assembly_total:,}, the figure "
            "that the party votes add up to."
        )
    set_id = result_set("assembly", contest, caveat)
    p.add(
        "note",
        id=ident("note", set_id, "quota"),
        entity_id=set_id,
        kind="methodology",
        body_md=f"The Commission prints a quota of {notice.quota:,} votes for the election.",
        lang="en",
    )
    listed = sum(votes for _, votes, _ in notice.assembly)
    if notice.assembly_basis == "counted":
        put(set_id, None, CAST, notice.assembly_total)
        put(set_id, None, VALID, listed, declare=False)
    else:
        put(set_id, None, VALID, notice.assembly_total)
    if notice.assembly_rejected:
        put(set_id, None, INVALID, notice.assembly_rejected)
    for position, (label, votes, seats) in enumerate(notice.assembly, 1):
        party = ident("party", party_key(label))
        option = ident("option", day, "assembly", position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=position,
            ballot_label=label,
        )
        put(set_id, option, VOTES, votes)
        p.add(
            "outcome",
            result_set_id=set_id,
            option_id=option,
            candidacy_id=None,
            kind="won" if seats else "lost",
            seats=seats,
        )
    if listed != notice.assembly_total:
        p.accept(
            "accounting.votes_valid",
            f"The notice prints {notice.assembly_total:,} as the total number of votes, while the parties' votes add "
            f"up to {listed:,}; both are stored as printed.",
        )
    return p
