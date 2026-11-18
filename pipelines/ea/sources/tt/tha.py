"""Tobago House of Assembly elections, 1984 to 2026, from the Elections and Boundaries Commission's reports.

The commission publishes one report per election. Each gives every district's electorate, each candidate's votes with
their party, and the rejected ballots. The reports of 1984 to 2013 and of January 2021 are scans whose district tables
were read by hand from the page images (data files under ``tha_data/``); the reports of December 2021 and 2026 carry a text
layer, and the 2017 results sheet is a text table. Every election's district rows add up to the national totals the
commission prints in the comparison table of its later reports.
"""

import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "tt.tha"
SITE = "https://ebctt.com"
UPLOADS = f"{SITE}/wp-content/uploads"
LEGACY = "http://www.ebctt.com/wp-content/uploads"
TRINIDAD = str(uuid5(package.NAMESPACE, "polity\x1fTT"))
PUBLISHER = "Elections and Boundaries Commission of Trinidad and Tobago"
DATA = Path(__file__).parent / "tha_data"
REGISTERED, BALLOTS, VALID, INVALID, VOTES = 2, 3, 4, 5, 1

REPORTS = {
    1984: f"{UPLOADS}/Report-of-the-EBC-on-the-THA-Elections-1984-26th-November-1984.pdf",
    1988: f"{UPLOADS}/Report-of-the-EBC-on-the-THA-Elections-1988-29th-November-1988.pdf",
    1992: f"{UPLOADS}/Report-on-the-Tobago-House-of-Assembly-Eelections-held-on-Monday-7th-December-1992.pdf",
    1996: f"{UPLOADS}/Report-of-the-EBC-on-the-THA-Elections-1996-9th-December-1996.pdf",
    2001: f"{UPLOADS}/Report-of-the-EBC-on-the-THA-Elections-2001-29th-January-2001.pdf",
    2005: f"{UPLOADS}/Report-of-the-EBC-on-the-THA-Elections-2005-17th-January-2005.pdf",
    2009: f"{LEGACY}/Report-of-the-Elections-Boundaries-Commission-THA-Elections-2009.pdf",
    2013: f"{LEGACY}/Report-on-the-2013-THA-Election.pdf",
    2017: f"{LEGACY}/Report-on-the-THA-Elections-2017.pdf",
    2021: f"{UPLOADS}/2021-Report-of-the-Tobago-House-of-Assembly-Elections.pdf",
    2022: (
        f"{UPLOADS}/Report-of-the-Elections-and-Boundaries-Commission-of-the-Tobago-House-of-Assembly-Elections-held-on-"
        "Monday-6th-December-2021.pdf"
    ),
    2026: f"{UPLOADS}/EBC-Report-THA-Elections-2026.pdf",
}
RESULTS_2017 = f"{LEGACY}/THA-Results-2017-final-2.pdf"
FILES = [*REPORTS.values(), RESULTS_2017]


class Printed(NamedTuple):
    """The national figures of the commission's comparison table: electorate, ballots cast, rejected ballots."""

    registered: int
    ballots: int
    rejected: int


class Poll(NamedTuple):
    day: date
    report: int
    printed: Printed
    data: str
    stations: bool = False
    # Whether the station rows were read by hand from a scan (tha_data/<data>_stations.txt) rather than the text layer.
    scanned: bool = False


# The comparison tables of the 2021 and 2026 reports print each election's electorate, ballots cast, and rejected
# ballots. The key of REPORTS that carries each election's own report is `report`.
POLLS = [
    Poll(date(1984, 11, 26), 1984, Printed(28220, 19786, 123), "1984"),
    Poll(date(1988, 11, 29), 1988, Printed(31224, 16720, 60), "1988"),
    Poll(date(1992, 12, 7), 1992, Printed(31503, 17860, 140), "1992"),
    Poll(date(1996, 12, 9), 1996, Printed(34245, 15034, 103), "1996"),
    Poll(date(2001, 1, 29), 2001, Printed(37616, 22619, 150), "2001"),
    Poll(date(2005, 1, 17), 2005, Printed(38608, 21013, 93), "2005"),
    Poll(date(2009, 1, 19), 2009, Printed(42623, 24034, 118), "2009"),
    Poll(date(2013, 1, 21), 2013, Printed(46601, 32657, 136), "2013"),
    Poll(date(2017, 1, 23), 2017, Printed(48980, 24343, 85), "2017"),
    Poll(date(2021, 1, 25), 2021, Printed(51062, 26474, 88), "2021", True, True),
    Poll(date(2021, 12, 6), 2022, Printed(51383, 29273, 98), "2021b", True),
    Poll(date(2026, 1, 12), 2026, Printed(53239, 26989, 103), "2026", True),
]

PARTIES = {
    "DAC": "Democratic Action Congress",
    "PNM": "People's National Movement",
    "NJAC": "National Joint Action Committee",
    "NAR": "National Alliance for Reconstruction",
    "UNC": "United National Congress",
    "TOP": "Tobago Organisation of the People",
    "PDP": "Progressive Democratic Patriots",
    "TPP": "Tobago People's Party",
    "IDA": "Innovative Democratic Alliance",
    "UTP": "Unity of the People",
    "CARM": "Class Action Reform Movement",
}
ACRONYMS = {
    "DAC": "D.A.C.",
    "PNM": "P.N.M.",
    "NJAC": "N.J.A.C.",
    "NAR": "N.A.R.",
    "UNC": "U.N.C.",
    "TOP": "T.O.P.",
    "PDP": "P.D.P.",
    "TPP": "T.P.P.",
    "IDA": "I.D.A.",
    "UTP": "U.T.P.",
    "CARM": "C.A.R.M.",
}
INDEPENDENT = "IND"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for url in FILES:
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=".pdf")
                break
            except httpx.HTTPError:
                if attempt == 5:
                    raise
                time.sleep(3 * (attempt + 1))


def party_code(text: str) -> str:
    code = re.sub(r"[^A-Z]", "", text.upper())
    return "UTP" if code == "UNITYOFTHEPEOPLE" else code


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def title(text: str) -> str:
    """A district's name as the reports print it, in ordinary case, with "Mount" as the other reports abbreviate it."""

    def word(w: str) -> str:
        return "-".join("'".join(q[:1].upper() + q[1:].lower() for q in part.split("'")) for part in w.split("-"))

    named = "/".join(" ".join(word(w) for w in piece.split()) for piece in text.split("/"))
    return re.sub(r"\bMount\b", "Mt.", named)


@dataclass
class Candidate:
    name: str
    party: str
    votes: int


@dataclass
class Station:
    code: str
    special: bool
    venue: str
    votes: list[int]
    valid: int
    rejected: int
    registered: int


@dataclass
class District:
    name: str
    registered: int
    rejected: int
    page: int
    candidates: list[Candidate] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)

    @property
    def valid(self) -> int:
        return sum(c.votes for c in self.candidates)

    @property
    def ballots(self) -> int:
        return self.valid + self.rejected

    @property
    def winner(self) -> int:
        votes = [c.votes for c in self.candidates]
        return votes.index(max(votes))


def districts(poll: Poll) -> list[District]:
    out: list[District] = []
    for line in (DATA / f"{poll.data}.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("|")
        if cells[0] == "D":
            out.append(District(title(cells[1]), int(cells[2]), int(cells[3]), int(cells[4])))
        else:
            out[-1].candidates.append(Candidate(cells[1], party_code(cells[2]), int(cells[3])))
    names = [norm(d.name) for d in out]
    if len(names) != len(set(names)):
        raise ValueError(f"{poll.day} repeats a district")
    for d in out:
        tops = sorted((c.votes for c in d.candidates), reverse=True)
        if len(tops) > 1 and tops[0] == tops[1]:
            raise ValueError(f"{poll.day} {d.name} has a tie")
    return out


def _numbers(text: str) -> list[int]:
    return [int(x.replace(",", "")) for x in text.split()]


def report_text(report: int) -> str:
    path = raw.cached(SOURCE, REPORTS[report]).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def station_rows(report: int) -> dict[str, tuple[list[tuple[str, bool, str, list[int]]], list[int]]]:
    """The polling station rows of the report's statements of the poll, and each statement's total row, by district."""
    found: dict[str, tuple[list[tuple[str, bool, str, list[int]]], list[int]]] = {}
    current = ""
    for line in report_text(report).splitlines():
        head = re.match(r"^\s*ELECTORAL DISTRICT OF\s+(.+?)\s*$", line)
        if head:
            current = norm(head.group(1))
            found.setdefault(current, ([], []))
            continue
        if not current:
            continue
        row = re.match(r"^\s*(\*?)\s*(\d{4}(?:-\d\d)?)\s+(\S.*)$", line)
        if row:
            cells = re.split(r"\s{2,}", row.group(3).strip())
            figures = [c for c in cells[1:] if re.fullmatch(r"[\d,]+", c)]
            if len(figures) == len(cells) - 1 and len(cells) > 1:
                found[current][0].append((row.group(2), bool(row.group(1)), cells[0], _numbers(" ".join(figures))))
            continue
        total = re.match(r"^\s*TOTALS?\s+([\d,\s]+)$", line)
        if total and not found[current][1]:
            found[current][1][:] = _numbers(total.group(1))
    return found


def attach_scanned(poll: Poll, found: list[District]) -> None:
    """Station rows read by hand from a scanned statement of the poll, whose columns follow the ballot."""
    rows: dict[str, tuple[list[str], list[list[str]]]] = {}
    current = ""
    for line in (DATA / f"{poll.data}_stations.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("|")
        if cells[0] == "D":
            current = norm(cells[1])
            rows[current] = (cells[2:], [])
        else:
            rows[current][1].append(cells[1:])
    if set(rows) != {norm(d.name) for d in found}:
        raise ValueError(f"{poll.day}: station rows cover {sorted(rows)}")
    for d in found:
        surnames, lines = rows[norm(d.name)]
        count = len(d.candidates)
        order: list[int] = []
        for surname in surnames:
            hits = [i for i, c in enumerate(d.candidates) if norm(c.name).endswith(norm(surname))]
            if len(hits) != 1:
                raise ValueError(f"{poll.day} {d.name}: {surname} matches {hits}")
            order.append(hits[0])
        if sorted(order) != list(range(count)):
            raise ValueError(f"{poll.day} {d.name}: columns {order}")
        for line in lines:
            if len(line) != count + 5:
                raise ValueError(f"{poll.day} {d.name}: {line}")
            numbers = [int(x) for x in line[2:]]
            votes = [0] * count
            for column, index in enumerate(order):
                votes[index] = numbers[column]
            code = line[0].lstrip("*")
            d.stations.append(
                Station(
                    code,
                    line[0].startswith("*"),
                    line[1],
                    votes,
                    numbers[count],
                    numbers[count + 1],
                    numbers[count + 2],
                )
            )
    verify_stations(poll, found)


def attach_stations(poll: Poll, found: list[District]) -> None:
    if poll.scanned:
        attach_scanned(poll, found)
        return
    rows = station_rows(poll.report)
    for d in found:
        got, total = rows[norm(d.name)]
        count = len(d.candidates)
        if len(total) != count + 3:
            raise ValueError(f"{poll.day} {d.name}: total row {total}")
        # The statement's columns follow the ballot, which is not the order of votes; each column is the candidate
        # whose district total it equals.
        order: list[int] = []
        for column in range(count):
            hits = [i for i, c in enumerate(d.candidates) if c.votes == total[column] and i not in order]
            if not hits:
                raise ValueError(f"{poll.day} {d.name}: column {column} matches no candidate")
            order.append(hits[0])
        for code, special, venue, numbers in got:
            if len(numbers) != count + 3:
                raise ValueError(f"{poll.day} {d.name} {code}: {numbers}")
            votes = [0] * count
            for column, index in enumerate(order):
                votes[index] = numbers[column]
            d.stations.append(
                Station(code, special, venue, votes, numbers[count], numbers[count + 1], numbers[count + 2])
            )
    verify_stations(poll, found)


def verify_stations(poll: Poll, found: list[District]) -> None:
    for d in found:
        count = len(d.candidates)
        sums = [sum(s.votes[i] for s in d.stations) for i in range(count)]
        if sums != [c.votes for c in d.candidates]:
            raise ValueError(f"{poll.day} {d.name}: stations add up to {sums}")
        if (sum(s.rejected for s in d.stations), sum(s.registered for s in d.stations)) != (d.rejected, d.registered):
            raise ValueError(f"{poll.day} {d.name}: stations disagree on rejected ballots or electorate")
        for s in d.stations:
            if sum(s.votes) != s.valid:
                raise ValueError(f"{poll.day} {d.name} {s.code}: votes do not add up to {s.valid}")


def checked(poll: Poll) -> list[District]:
    found = districts(poll)
    if poll.stations:
        attach_stations(poll, found)
    got = Printed(sum(d.registered for d in found), sum(d.ballots for d in found), sum(d.rejected for d in found))
    if got != poll.printed:
        raise ValueError(f"{poll.day}: districts add up to {got}, the commission prints {poll.printed}")
    return found


def packages() -> list[str]:
    return ["tt.tha/geo", "tt.tha/registry", *(f"tt.tha/{poll.day}" for poll in POLLS)]


def parse() -> None:
    geo().write()
    registry().write()
    for poll in POLLS:
        general(poll).write()


def emit() -> None:
    package.run("load", packages())


def area(code: str) -> str:
    return key("area", code)


def geo() -> Package:
    p = Package(
        "tt.tha/geo",
        SOURCE,
        "The electoral districts of the Tobago House of Assembly by name, from the Elections and Boundaries "
        "Commission's reports",
    )
    p.external.add(TRINIDAD)
    p.add("scheme", code="tt.tha_district", polity_id=TRINIDAD, kind="electoral_district", rank=1, noun="district")
    p.add("scheme", code="tt.tha_polling_station", polity_id=TRINIDAD, kind="precinct", rank=2, noun="polling station")
    seen: dict[str, str] = {}
    for poll in POLLS:
        for d in districts(poll):
            seen[norm(d.name)] = d.name
    for code, name in sorted(seen.items()):
        id = area(code)
        p.add("area", id=id, scheme_code="tt.tha_district")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
    return p


BODY = key("body", "tha")
SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "fptp")


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        "tt.tha/registry",
        SOURCE,
        "The Tobago House of Assembly, the plurality system, the commission as a source, and the parties that "
        "contested its elections",
        depends=["tt.tha/geo"],
    )
    p.external.add(TRINIDAD)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title="Reports on the Tobago House of Assembly elections",
        url=f"{SITE}/publications-and-reports/?_sft_category=tha-election-reports",
        license="Official public record, no license stated",
        attribution=PUBLISHER,
    )
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=BODY, polity_id=TRINIDAD, kind="unicameral")
    p.name(BODY, "Tobago House of Assembly", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TRINIDAD,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Single-member plurality", "en", "official")
    codes: set[str] = set()
    for poll in POLLS:
        for d in districts(poll):
            codes |= {c.party for c in d.candidates if c.party != INDEPENDENT}
    for code in sorted(codes):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=TRINIDAD)
        p.name(id, PARTIES.get(code, ACRONYMS.get(code, code)), "en", "official")
        if code in ACRONYMS:
            p.name(id, ACRONYMS[code], "en", "abbreviation", False)
    return p


METHODS = {
    "scan": (
        "From the commission's report on the election, which gives each district's electorate, each candidate's votes "
        "and party, and the rejected ballots. The report is a scan; its district tables were read from the page by "
        "hand. Every district's candidate votes and rejected ballots add up to the total votes cast it prints, and "
        "the districts add up to the national electorate, valid votes, rejected ballots, and ballots cast of the "
        "report's own summary and of the comparison table of the commission's later reports. Ballots cast are valid "
        "votes plus rejected ballots."
    ),
    "stations": (
        "From the commission's report on the election, whose statements of the poll give each candidate's votes, the "
        "valid votes, the rejected ballots, and the names on the revised list at each polling station, and whose "
        "results table gives each district's candidates, parties, and totals. The report is a scan; the statements "
        "were read from the page by hand. Every station's votes add up to its valid votes, every district's "
        "stations add up to the district's candidate votes, rejected ballots, and electorate, and the districts add "
        "up to the national electorate, ballots cast, and rejected ballots of the report's own comparison table. "
        "Ballots cast are valid votes plus rejected ballots. Stations marked with an asterisk held the ballots of "
        "special electors."
    ),
    "text": (
        "From the commission's report on the election, whose statements of the poll give each candidate's votes, the "
        "valid votes, the rejected ballots, and the names on the revised list at each polling station, and whose "
        "results table gives each district's candidates, parties, and totals. Every district's stations add up to "
        "the district's candidate votes, rejected ballots, and electorate, and the districts add up to the national "
        "electorate, ballots cast, and rejected ballots of the report's own comparison table. Ballots cast are "
        "valid votes plus rejected ballots. Stations marked with an asterisk held the ballots of special electors."
    ),
}
TEXT_REPORTS = {2022, 2026}


# Appendix IX of the 2026 report compares the island totals of every election since 1980: districts, names on the
# revised list, votes cast, turnout, rejected votes, and their share of votes cast. The rows before 2021, as printed.
HISTORY = [
    ("1980", 12, "24,141", "15,990", "66.24%", 95, "0.59%"),
    ("1984", 12, "28,220", "19,786", "70.11%", 123, "0.62%"),
    ("1988", 12, "31,224", "16,720", "53.55%", 60, "0.36%"),
    ("1992", 12, "31,503", "17,860", "56.69%", 140, "0.78%"),
    ("1996", 12, "34,245", "15,034", "43.90%", 103, "0.69%"),
    ("2001", 12, "37,616", "22,619", "60.13%", 150, "0.66%"),
    ("2005", 12, "38,608", "21,013", "54.43%", 93, "0.44%"),
    ("2009", 12, "42,623", "24,034", "56.39%", 118, "0.49%"),
    ("2013", 12, "46,601", "32,657", "70.08%", 136, "0.42%"),
    ("2017", 12, "48,980", "24,343", "49.70%", 85, "0.35%"),
]


def history_note() -> str:
    head = (
        "The commission's comparison of the results of Tobago House of Assembly elections, as printed in Appendix IX "
        "of its report on the 2026 election, for the elections before 2021. These are island totals only.\n\n"
        "| Year | Districts | Names on the revised list | Votes cast | Votes cast against electorate | "
        "Votes rejected | Rejected against votes cast |\n|---|---|---|---|---|---|---|\n"
    )
    return head + "\n".join(f"| {y} | {n} | {r} | {v} | {t} | {j} | {q} |" for y, n, r, v, t, j, q in HISTORY)


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


def label(poll: Poll) -> str:
    extra = " (re-run)" if poll.day == date(2021, 12, 6) else ""
    return f"{poll.day.year} Tobago House of Assembly election{extra}"


def general(poll: Poll) -> Package:
    found = checked(poll)
    day = poll.day
    p = Package(
        f"tt.tha/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} Tobago House of Assembly election by district, from the Elections and Boundaries "
        "Commission",
        depends=["tt.tha/geo", "tt.tha/registry"],
    )
    p.external.add(TRINIDAD)
    urls = [REPORTS[poll.report]] + ([RESULTS_2017] if poll.report == 2017 else [])
    for url in urls:
        p.uses(raw.cached(SOURCE, url))
    method = METHODS["text" if poll.report in TEXT_REPORTS else "stations" if poll.scanned else "scan"]
    election = key("election", day)
    rerun = key("election", date(2021, 1, 25)) if day == date(2021, 12, 6) else None
    p.add(
        "election",
        id=election,
        polity_id=TRINIDAD,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
        rerun_of_id=rerun,
    )
    p.name(election, label(poll), "en", "official")
    path = f"tt/tha-{day}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("citation", id=key("citation", election), entity_id=election, source_id=SOURCE_ID, locator=None)
    if poll.report == 2026:
        p.add(
            "note",
            id=key("note", election, "history"),
            entity_id=election,
            kind="context",
            body_md=history_note(),
            lang="en",
        )
    for d in found:
        code = norm(d.name)
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area(code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, d.name, "en", "official")
        p.add("slug", path=f"{path}/{slug(d.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=TRINIDAD,
            scheme_code="tt.tha_district",
            code=f"{day}:{code}",
            parent_id=None,
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
        p.name(unit, d.name, "en", "official")
        options: list[str] = []
        for index, c in enumerate(d.candidates):
            option = key("option", day, code, index)
            options.append(option)
            independent = c.party == INDEPENDENT
            member = None if independent else party(c.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=None,
                ballot_label=c.name,
            )
            person = key("person", day, code, norm(c.name))
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, code, index),
                person_id=person,
                option_id=option,
                role="head",
                list_position=None,
                ballot_name=c.name,
                member_of_party_id=member,
                is_independent=independent,
            )
            if not d.stations:
                _value(p, result_set, unit, option, VOTES, c.votes)
        p.add("outcome", result_set_id=result_set, option_id=options[d.winner], kind="elected")
        if d.stations:
            for station in d.stations:
                child = key("unit", day, code, station.code)
                p.add(
                    "reporting_unit",
                    id=child,
                    polity_id=TRINIDAD,
                    scheme_code="tt.tha_polling_station",
                    code=f"{day}:{code}:{station.code}",
                    parent_id=unit,
                    area_relation="within",
                    area_id=area(code),
                    kind="standard",
                )
                p.name(child, f"{station.code} {station.venue}", "en", "official")
                for option, votes in zip(options, station.votes, strict=True):
                    _value(p, result_set, child, option, VOTES, votes)
                _value(p, result_set, child, None, REGISTERED, station.registered)
                _value(p, result_set, child, None, VALID, station.valid)
                _value(p, result_set, child, None, INVALID, station.rejected)
                _value(p, result_set, child, None, BALLOTS, station.valid + station.rejected)
            for option, c in zip(options, d.candidates, strict=True):
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
            for measure, value in (
                (REGISTERED, d.registered),
                (VALID, d.valid),
                (INVALID, d.rejected),
                (BALLOTS, d.ballots),
            ):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        else:
            _value(p, result_set, unit, None, REGISTERED, d.registered)
            _value(p, result_set, unit, None, BALLOTS, d.ballots)
            _value(p, result_set, unit, None, VALID, d.valid)
            _value(p, result_set, unit, None, INVALID, d.rejected)
    return p


def totals() -> dict[date, Counter[str]]:
    """Each election's national figures from its districts."""
    out: dict[date, Counter[str]] = {}
    for poll in POLLS:
        count: Counter[str] = Counter()
        for d in checked(poll):
            count["registered"] += d.registered
            count["valid"] += d.valid
            count["rejected"] += d.rejected
            count["ballots"] += d.ballots
        out[poll.day] = count
    return out
