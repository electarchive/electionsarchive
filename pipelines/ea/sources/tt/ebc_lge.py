"""Trinidad and Tobago local government elections of 14 August 2023, by polling station, from the commission's report.

The Elections and Boundaries Commission's report gives each of the 141 electoral districts' electorate, valid votes,
rejected ballots, and winner (Appendix I), each candidate's votes at every polling station (Appendix II), each
candidate's district total and party (Appendix VII), and the aldermen allocated to each party in each corporation
(Table 3). The report has a text layer. Every district's stations add up to the candidate totals of Appendix VII and to
the electorate, valid votes, and rejected ballots of Appendix I, and the districts add up to the national figures and
party totals the report prints.
"""

import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5

import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "tt.ebc_lge"
SITE = "https://ebctt.com"
REPORT = f"{SITE}/wp-content/uploads/EBC-Local-Government-Elections-Report-2023.pdf"
TRINIDAD = str(uuid5(package.NAMESPACE, "polity\x1fTT"))
PUBLISHER = "Elections and Boundaries Commission of Trinidad and Tobago"
DAY = date(2023, 8, 14)
REGISTERED, BALLOTS, VALID, INVALID, VOTES, SEATS = 2, 3, 4, 5, 1, 16
INDEPENDENT = "IND"

# The report's own national figures: the electorate, valid votes, rejected ballots, and ballots cast of Appendix I, and
# the votes each party and the independents received, from Figure 3 and Appendix IV.
PRINTED = (1_091_935, 332_308, 1_272, 333_580)
PRINTED_PARTIES = {
    "IND": 667,
    "MSJ": 164,
    "NTA": 15_980,
    "PDP": 1_292,
    "PEP": 5_797,
    "PNM": 131_342,
    "RFA": 1_042,
    "THC": 234,
    "TNP": 45,
    "UNC": 175_635,
    "UTP": 110,
}
PARTIES = {
    "MSJ": "Movement for Social Justice",
    "NTA": "National Transformation Alliance",
    "PDP": "Progressive Democratic Patriots",
    "PEP": "Progressive Empowerment Party",
    "PNM": "People's National Movement",
    "RFA": "Re-United Farmers Alliance",
    "THC": "Trinidad Humanity Campaign",
    "TNP": "The National Party",
    "UNC": "United National Congress",
    "UTP": "Unity of the People",
}
ACRONYMS = {code: ".".join(code) + "." for code in PARTIES}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, REPORT, suffix=".pdf")
            return
        except httpx.HTTPError:
            if attempt == 5:
                raise
            time.sleep(3 * (attempt + 1))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def party_code(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def _word(w: str) -> str:
    if re.fullmatch(r"\(?(?:[A-Za-z]\.){2,}\)?", w):
        return w.upper()
    out = "-".join("'".join(q[:1].upper() + q[1:].lower() for q in part.split("'")) for part in w.split("-"))
    return re.sub(r"^(Mc|Mac(?=[a-z]{3,}))([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def title(text: str) -> str:
    """A name as the report prints it in capitals, in ordinary case."""
    return "/".join(" ".join(_word(w) for w in piece.split()) for piece in text.split("/"))


def district_title(text: str) -> str:
    named = title(text)
    return re.sub(r"\bSt\.", "St.", re.sub(r"\bNo\. ", "No. ", named))


@cache
def text() -> list[str]:
    path = raw.cached(SOURCE, REPORT).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def numbers(cells: str) -> list[int]:
    return [int(x.replace(",", "")) for x in cells.split()]


@dataclass
class Candidate:
    surname: str
    given: str
    party: str
    votes: int

    @property
    def name(self) -> str:
        return title(f"{self.given} {self.surname}")


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
    corporation: str
    registered: int
    valid: int
    rejected: int
    ballots: int
    winner: str | None
    candidates: list[Candidate] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)

    @property
    def code(self) -> str:
        return norm(self.name)


class Corporation(NamedTuple):
    name: str
    municipal: bool


ROW = re.compile(
    r"^([A-Z][A-Z0-9 .'/\-]*?)\s{2,}([\d,]+)\s+([\d,]+)\s+(\d+)\s+([\d,]+)(?:\s{2,}(.+?)\s{2,}([A-Z.]+))?\s*$"
)


def summary() -> tuple[list[Corporation], list[District], list[int]]:
    """Appendix I: each corporation's districts with electorate, valid votes, rejected ballots, and the winner."""
    lines = text()
    start = next(i for i, line in enumerate(lines) if "(SUMMARY OF APPENDIX II)" in line)
    end = next(i for i, line in enumerate(lines) if i > start and re.match(r"^Appendix II\b", line))
    corporations: list[Corporation] = []
    found: list[District] = []
    totals: list[int] = []
    current = ""
    pending: bool | None = None
    for line in lines[start:end]:
        stripped = line.strip()
        head = re.fullmatch(r"(MUNICIPAL|REGIONAL) CORPORATION OF", stripped)
        if head:
            pending = head.group(1) == "MUNICIPAL"
            continue
        if pending is not None and stripped and re.fullmatch(r"[A-Z][A-Z/\-. ]+", stripped):
            current = stripped
            corporations.append(Corporation(stripped, pending))
            pending = None
            continue
        total = re.match(r"^TOTALS\s+([\d,\s]+)$", stripped)
        if total:
            totals = numbers(total.group(1))
            continue
        row = ROW.match(stripped)
        if row and not stripped.startswith("TOTAL"):
            registered, valid, rejected, ballots = (int(row.group(i).replace(",", "")) for i in range(2, 6))
            winner = row.group(7)
            found.append(
                District(
                    row.group(1).strip(),
                    current,
                    registered,
                    valid,
                    rejected,
                    ballots,
                    party_code(winner) if winner else None,
                )
            )
    return corporations, found, totals


CANDIDATE = re.compile(r"^([A-Z][A-Z .'\-]*?),\s*(.+?)\s{2,}([A-Z][A-Z.]*)\s{2,}([\d,]+)\s+[\d.]+(?:\s+[\d,]+)?\s*$")
BANNER = {"LOCAL GOVERNMENT ELECTIONS, 2023", "APPENDIX VII", "VOTING RESULTS IDENTIFYING MAJORITY WINNERS"}


def candidates() -> dict[str, list[Candidate]]:
    """Appendix VII: each district's candidates, parties, and votes, in the order of the votes."""
    lines = text()
    start = next(i for i, line in enumerate(lines) if line.strip() == "VOTING RESULTS IDENTIFYING MAJORITY WINNERS")
    end = next(i for i, line in enumerate(lines) if i > start and line.strip() == "APPENDIX VIII")
    found: dict[str, list[Candidate]] = {}
    current = ""
    heading = ""
    for line in lines[start:end]:
        stripped = line.strip()
        if not stripped or stripped in BANNER or stripped.isdigit():
            continue
        match = CANDIDATE.match(stripped)
        if match:
            if heading:
                current = norm(heading)
                heading = ""
            found.setdefault(current, []).append(
                Candidate(
                    match.group(1).strip(),
                    match.group(2).strip(),
                    party_code(match.group(3)),
                    int(match.group(4).replace(",", "")),
                )
            )
        elif re.fullmatch(r"[A-Z][A-Z0-9 .'/\-]+", stripped) and not stripped.startswith(("REJECTED", "TOTAL")):
            heading = stripped
    return found


def station_rows() -> dict[str, tuple[list[str], list[tuple[str, bool, str, list[int]]], list[int]]]:
    """Appendix II: each district's party row, polling station rows, and total row."""
    found: dict[str, tuple[list[str], list[tuple[str, bool, str, list[int]]], list[int]]] = {}
    current = ""
    for line in text():
        head = re.match(r"^\s*ELECTORAL DISTRICT OF\s+(.+?)\s*$", line)
        if head:
            current = norm(head.group(1))
            found.setdefault(current, ([], [], []))
            continue
        if not current:
            continue
        got = found[current]
        if "POLLING STATION ADDRESS" in line and not got[0]:
            tail = re.split(r"\s{2,}", line.strip())
            got[0].extend(party_code(c) for c in tail if re.fullmatch(r"[A-Z]\.(?:[A-Z]\.)*|IND\.", c))
            continue
        row = re.match(r"^\s*(\*?)\s*(\d{4}(?:-\d\d)?)\s+(\S.*)$", line)
        if row:
            cells = re.split(r"\s{2,}", row.group(3).strip())
            figures = [c for c in cells[1:] if re.fullmatch(r"[\d,]+", c)]
            if len(figures) == len(cells) - 1 and len(cells) > 1:
                got[1].append((row.group(2), bool(row.group(1)), cells[0], numbers(" ".join(figures))))
            continue
        total = re.match(r"^\s*TOTALS?\s+([\d,\s]+)$", line)
        if total and not got[2]:
            got[2][:] = numbers(total.group(1))
    return found


def aldermen() -> dict[str, dict[str, int]]:
    """Table 3: the aldermen allocated to each party in each corporation, by party printed with a number."""
    lines = text()
    start = next(i for i, line in enumerate(lines) if "Table 3: Number of Aldermen" in line)
    out: dict[str, dict[str, int]] = {}
    header: list[str] = []
    for line in lines[start + 1 : start + 40]:
        stripped = line.strip()
        if not header:
            tokens = re.findall(r"[A-Z]\.[A-Z]\.[A-Z]\.", stripped)
            if len(tokens) >= 3:
                header = [party_code(t) for t in tokens]
            continue
        if stripped.startswith("TOTAL"):
            break
        row = re.match(r"^(.+?)\*?\s{2,}((?:[\d-]+\s+)+[\d-]+)\s*$", stripped)
        if not row:
            continue
        cells = row.group(2).split()
        if len(cells) != len(header) + 1:
            raise ValueError(f"Table 3 row {stripped!r}")
        got = {party: int(cell) for party, cell in zip(header, cells[:-1], strict=True) if cell != "-"}
        if cells[-1] != "4":
            raise ValueError(f"Table 3 row {stripped!r} has {cells[-1]} aldermen")
        out[norm(row.group(1))] = got
    return out


@cache
def districts() -> tuple[list[Corporation], list[District]]:
    corporations, found, totals = summary()
    if len(found) != 141 or len(corporations) != 14:
        raise ValueError(f"Appendix I holds {len(found)} districts in {len(corporations)} corporations")
    if tuple(totals) != (PRINTED[0], PRINTED[1], PRINTED[2], PRINTED[3]):
        raise ValueError(f"Appendix I prints national totals {totals}")
    names = [d.code for d in found]
    if len(names) != len(set(names)):
        raise ValueError("Appendix I repeats a district")
    ballots = candidates()
    rows = station_rows()
    for d in found:
        got = ballots.get(d.code)
        stations = rows.get(d.code)
        if not got or not stations:
            raise ValueError(f"{d.name}: candidates {bool(got)}, stations {bool(stations)}")
        # The statements of the poll list the candidates alphabetically by surname.
        ordered = sorted(got, key=lambda c: (c.surname, c.given))
        parties, station, total = stations
        if parties != [c.party for c in ordered]:
            raise ValueError(f"{d.name}: statement of the poll names parties {parties}, Appendix VII {ordered}")
        count = len(ordered)
        if [c.votes for c in ordered] != total[:count] or len(total) != count + 3:
            raise ValueError(f"{d.name}: total row {total} against candidates {[c.votes for c in ordered]}")
        d.candidates = ordered
        for code, special, venue, figures in station:
            if len(figures) != count + 3:
                raise ValueError(f"{d.name} {code}: {figures}")
            d.stations.append(Station(code, special, venue, figures[:count], *figures[count:]))
        check(d)
    return corporations, found


def check(d: District) -> None:
    count = len(d.candidates)
    sums = [sum(s.votes[i] for s in d.stations) for i in range(count)]
    if sums != [c.votes for c in d.candidates]:
        raise ValueError(f"{d.name}: stations add up to {sums}")
    if sum(c.votes for c in d.candidates) != d.valid:
        raise ValueError(f"{d.name}: candidates add up to {sum(c.votes for c in d.candidates)}, not {d.valid}")
    if (sum(s.valid for s in d.stations), sum(s.rejected for s in d.stations)) != (d.valid, d.rejected):
        raise ValueError(f"{d.name}: stations disagree on valid votes or rejected ballots")
    if sum(s.registered for s in d.stations) != d.registered:
        raise ValueError(f"{d.name}: stations add up to another electorate")
    if d.valid + d.rejected != d.ballots:
        raise ValueError(f"{d.name}: ballots cast are not valid plus rejected")
    for s in d.stations:
        if sum(s.votes) != s.valid:
            raise ValueError(f"{d.name} {s.code}: votes do not add up to {s.valid}")
    tops = sorted((c.votes for c in d.candidates), reverse=True)
    leaders = [c for c in d.candidates if c.votes == tops[0]]
    if d.winner is None:
        if len(leaders) < 2:
            raise ValueError(f"{d.name}: no winner declared without a tie")
    elif len(leaders) != 1 or leaders[0].party != d.winner:
        raise ValueError(f"{d.name}: declared winner's party {d.winner}, leader {leaders}")


def totals() -> dict[str, int]:
    """The national figures the districts add up to, and each party's votes."""
    _, found = districts()
    count: Counter[str] = Counter()
    for d in found:
        count["registered"] += d.registered
        count["valid"] += d.valid
        count["rejected"] += d.rejected
        count["ballots"] += d.ballots
        for c in d.candidates:
            count[c.party] += c.votes
    return dict(count)


def verify() -> None:
    got = totals()
    if (got["registered"], got["valid"], got["rejected"], got["ballots"]) != PRINTED:
        raise ValueError(f"the districts add up to {got}, the report prints {PRINTED}")
    parties = {k: v for k, v in got.items() if k in PRINTED_PARTIES or k.isupper()}
    if parties != PRINTED_PARTIES:
        raise ValueError(f"party votes {parties}, the report prints {PRINTED_PARTIES}")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    verify()
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def area(kind: str, code: str) -> str:
    return key("area", kind, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The local government electoral districts and corporations of Trinidad and Tobago by name, from the Elections "
        "and Boundaries Commission's report",
    )
    p.external.add(TRINIDAD)
    p.add(
        "scheme",
        code="tt.lge_corporation",
        polity_id=TRINIDAD,
        kind="administrative",
        rank=1,
        noun="corporation",
    )
    p.add(
        "scheme",
        code="tt.lge_district",
        polity_id=TRINIDAD,
        kind="electoral_district",
        rank=2,
        noun="electoral district",
    )
    p.add(
        "scheme",
        code="tt.lge_polling_station",
        polity_id=TRINIDAD,
        kind="precinct",
        rank=3,
        noun="polling station",
    )
    corporations, found = districts()
    for c in corporations:
        id = area("corporation", norm(c.name))
        p.add("area", id=id, scheme_code="tt.lge_corporation")
        p.add("area_code", area_id=id, code=norm(c.name))
        p.name(id, corporation_name(c), "en", "official")
    for d in found:
        id = area("district", d.code)
        p.add("area", id=id, scheme_code="tt.lge_district")
        p.add("area_code", area_id=id, code=d.code)
        p.name(id, district_title(d.name), "en", "official")
        p.add("area_parent", area_id=id, parent_id=area("corporation", norm(d.corporation)))
    return p


def corporation_name(c: Corporation) -> str:
    return f"{'Municipal' if c.municipal else 'Regional'} Corporation of {title(c.name)}"


def council(c: Corporation) -> str:
    return key("body", norm(c.name))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "fptp")


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils of the 14 corporations, the plurality system, the commission as a source, and the parties that "
        "contested the 2023 local government elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(TRINIDAD)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher=PUBLISHER,
        title="Report on the Local Government Elections held on 14th August 2023",
        url=REPORT,
        license="Official public record, no license stated",
        attribution=PUBLISHER,
    )
    p.artifact(raw.cached(SOURCE, REPORT), SOURCE_ID)
    corporations, found = districts()
    for c in corporations:
        id = council(c)
        p.add("body", id=id, polity_id=TRINIDAD, kind="council")
        p.name(id, f"Council of the {title(c.name)} Corporation", "en", "official")
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
    codes = {c.party for d in found for c in d.candidates if c.party != INDEPENDENT}
    for code in sorted(codes):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=TRINIDAD)
        p.name(id, PARTIES[code], "en", "official")
        p.name(id, ACRONYMS[code], "en", "abbreviation", False)
    return p


METHOD = (
    "From the commission's report on the election. Its Appendix II gives each candidate's votes, the valid votes, the "
    "rejected ballots, and the names on the revised list at each polling station, Appendix I gives each district's "
    "electorate, valid votes, rejected ballots, and winner, and Appendix VII gives each candidate's party and total. "
    "Every district's stations add up to the district's candidate votes, valid votes, rejected ballots, and electorate, "
    "and the districts add up to the national electorate, valid votes, rejected ballots, ballots cast, and party "
    "votes the report prints. Ballots cast are valid votes plus rejected ballots. Stations marked with an asterisk "
    "held the ballots of special electors."
)
VOID = (
    "The two leading candidates received the same number of votes after two recounts, and the returning officer "
    "declared the election void. The commission held a fresh election in this district on 17 June 2024."
)
ALDERMEN = (
    "From Table 3 of the commission's report, which gives the number of aldermen it allocated to each party in each "
    "corporation from the votes the parties received. A dash in the table means the party was not allocated a seat "
    "and is not listed."
)


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


def election() -> Package:
    verify()
    corporations, found = districts()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        f"The {DAY:%-d %B %Y} local government elections by polling station, from the Elections and Boundaries "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(TRINIDAD)
    p.uses(raw.cached(SOURCE, REPORT))
    p.accept(
        "accounting.turnout",
        "Five polling stations that the report marks as holding the ballots of special electors (Election Rule 93) "
        "show more ballots cast than names on their revised list: Erin 4530, Otaheite/Rousillac 4417, Couva "
        "West/Roystonia 3122, Mayaro South/Guayaguayare 2506, and Valencia East/Toco 2105. The special electors are "
        "on a separate list, so the station's printed electorate does not count them.",
    )
    id = key("election", DAY)
    p.add("election", id=id, polity_id=TRINIDAD, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(id, "2023 Trinidad and Tobago local government elections", "en", "official")
    path = f"tt/lge-{DAY}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.add("citation", id=key("citation", id), entity_id=id, source_id=SOURCE_ID, locator=None)
    for d in found:
        contest = key("contest", DAY, d.code)
        corporation = next(c for c in corporations if c.name == d.corporation)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=council(corporation),
            district_area_id=area("district", d.code),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        name = district_title(d.name)
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/{slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, d.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if d.winner is None:
            p.add("note", id=key("void", result_set), entity_id=result_set, kind="caveat", body_md=VOID, lang="en")
        unit = key("unit", DAY, d.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=TRINIDAD,
            scheme_code="tt.lge_district",
            code=f"{DAY}:{d.code}",
            parent_id=None,
            area_relation="equals",
            area_id=area("district", d.code),
            kind="standard",
        )
        p.name(unit, name, "en", "official")
        options: list[str] = []
        for index, c in enumerate(d.candidates):
            option = key("option", DAY, d.code, index)
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
            person = key("person", DAY, d.code, index)
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, d.code, index),
                person_id=person,
                option_id=option,
                role="head",
                list_position=None,
                ballot_name=c.name,
                member_of_party_id=member,
                is_independent=independent,
            )
        top = max(c.votes for c in d.candidates)
        for option, c in zip(options, d.candidates, strict=True):
            if c.votes != top:
                continue
            kind = "elected" if d.winner else "tied"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        for station in d.stations:
            child = key("unit", DAY, d.code, station.code)
            p.add(
                "reporting_unit",
                id=child,
                polity_id=TRINIDAD,
                scheme_code="tt.lge_polling_station",
                code=f"{DAY}:{d.code}:{station.code}",
                parent_id=unit,
                area_relation="within",
                area_id=area("district", d.code),
                kind="standard",
            )
            p.name(child, f"{station.code} {district_title(station.venue)}", "en", "official")
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
    allocated = aldermen()
    for c in corporations:
        got = allocated.get(norm(c.name))
        if got is None:
            raise ValueError(f"Table 3 lacks {c.name}")
        if not got:
            continue
        add_aldermen(p, id, c, got)
    return p


def add_aldermen(p: Package, election_id: str, c: Corporation, got: dict[str, int]) -> None:
    code = norm(c.name)
    contest = key("contest", DAY, "aldermen", code)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=council(c),
        district_area_id=area("corporation", code),
        magnitude=4,
        round_number=1,
        electoral_system_id=None,
        default_result_kind="counted",
    )
    name = f"{corporation_name(c)} aldermen"
    p.name(contest, name, "en", "official")
    p.add("slug", path=f"tt/lge-{DAY}/aldermen-{slug(title(c.name))}", entity_id=contest, is_current=True)
    result_set = key("result_set", DAY, "aldermen", code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=ALDERMEN, lang="en")
    unit = key("unit", DAY, "aldermen", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=TRINIDAD,
        scheme_code="tt.lge_corporation",
        code=f"{DAY}:{code}",
        parent_id=None,
        area_relation="equals",
        area_id=area("corporation", code),
        kind="standard",
    )
    p.name(unit, name, "en", "official")
    for member, seats in sorted(got.items()):
        option = key("option", DAY, "aldermen", code, member)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(member),
            ballot_position=None,
            ballot_label=PARTIES[member],
        )
        _value(p, result_set, unit, option, SEATS, seats)
        p.add("declared", result_set_id=result_set, option_id=option, measure=SEATS, value=seats)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
