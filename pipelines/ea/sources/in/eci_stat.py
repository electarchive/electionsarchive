"""India's Lok Sabha general elections, 1951 to 2009, from the Election Commission of India's statistical reports.

For each general election the Commission published a statistical report. Volume I holds the national and state
abstracts (the list of participating parties, the list of successful candidates, and the party-wise summaries) and, in
its second part, the detailed results of every parliamentary constituency: each candidate's party and votes, the
electors, the voters, and the valid votes. Volume II holds a summary page per constituency with the date of the poll.
The reports are computer-typeset PDFs, and the module reads their text layer through `ea.eci_report`, which knows
nothing of the year, so the state assembly reports (same layout) can run through it too.

The 2004 and 2009 reports are laid out differently (`ea.eci_report_2000s`): volume I's detailed results print each
candidate's votes by channel, volume II's summary pages carry each constituency's electors, voters, valid votes, poll
date, and winner, and volume III holds each candidate's votes in every assembly segment of a constituency and the
postal ballots, which become reporting units under the constituency's.

The Commission's own site refuses scripted requests, so the reports are read from archived captures of the old site
(eci.nic.in/eci_main/StatisticalReports), and each package cites the capture it read. The 1985 report holds the
polls of Assam and Punjab that were adjourned in 1984; they go under the 1984 election with their own dates.
"""

import functools
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import eci_report as reader
from ea import eci_report_2000s as modern
from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "in.eci_stat"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
SITE = "https://www.eci.gov.in"
ARCHIVE = "https://web.archive.org/web"
OLD = "eci.nic.in/eci_main/StatisticalReports"
VOTES, REGISTERED, BALLOTS, VALID = 1, 2, 3, 4
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
BODY = key("body", "lok-sabha")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
SCHEME = "in.pc"
SEGMENT_SCHEME = "in.pc_segment"


@dataclass(frozen=True)
class Edition:
    """One general election's reports, as the archive holds them."""

    year: int
    ordinal: int
    volume1: str
    volume2: str | None
    later: tuple[str, str | None] | None = None  # the report of polls adjourned to the next year
    group: str = ""  # the places and parties package the election builds with ("" for the first one)
    volume3: str | None = None  # the assembly segments (2004 and later layout)
    check: str | None = None  # a table that is only compared with the parsed results, not cited
    layout: str = "classic"  # "classic" or "modern" (the 2004 and 2009 reports)


NINETIES = "1990s"
TWO_THOUSANDS = "2000s"


def capture(stamp: str, path: str, port: bool = True) -> str:
    return f"{ARCHIVE}/{stamp}id_/http://eci.nic.in{':80' if port else ''}/eci_main/StatisticalReports/{path}"


EDITIONS = [
    Edition(
        1951,
        1,
        capture("20180713075355", "LS_1951/VOL_1_51_LS.PDF"),
        capture("20180713135535", "LS_1951/VOL_11_51_LS.PDF"),
    ),
    Edition(
        1957,
        2,
        capture("20180613105431", "LS_1957/Vol_I_57_LS.pdf"),
        capture("20180613105418", "LS_1957/Vol_II_57_LS.pdf"),
    ),
    Edition(
        1962,
        3,
        capture("20180613105434", "LS_1962/Vol_I_LS_62.pdf"),
        capture("20180820034043", "LS_1962/Vol_II_LS_62.pdf"),
    ),
    Edition(
        1967,
        4,
        capture("20180713131715", "LS_1967/Vol_I_LS_67.pdf"),
        capture("20180713195018", "LS_1967/Vol_II_LS67.pdf"),
    ),
    Edition(
        1971,
        5,
        capture("20180712174028", "LS_1971/Vol_I_LS71.pdf"),
        capture("20180820042141", "LS_1971/Vol_II_LS71.pdf"),
    ),
    Edition(
        1977,
        6,
        f"{ARCHIVE}/2012id_/http://eci.nic.in/eci_main/StatisticalReports/LS_1977/Vol_I_LS_77.pdf",
        capture("20180613105445", "LS_1977/Vol_II_LS77.pdf"),
    ),
    Edition(
        1980,
        7,
        capture("20180712190232", "LS_1980/Vol_I_LS_80.pdf"),
        capture("20180613105450", "LS_1980/Vol_II_LS_80.pdf"),
    ),
    Edition(
        1984,
        8,
        f"{ARCHIVE}/20181113110341id_/https://www.eci.nic.in/eci_main/StatisticalReports/LS_1984/Vol_I_LS_84.pdf",
        capture("20180713195023", "LS_1984/Vol_II_LS84.pdf"),
        later=(
            capture("20180713113822", "LS_1985/Vol_I_LS_85.pdf"),
            capture("20180820034046", "LS_1985/Vol_II_LS85.pdf"),
        ),
    ),
    Edition(1989, 9, capture("20180713095333", "LS_1989/Vol_I_LS_89.pdf"), None),
    Edition(
        1996,
        11,
        capture("20180712190247", "LS_1996/Vol_I_LS_96.pdf"),
        None,
        group=NINETIES,
    ),
    Edition(
        1998,
        12,
        f"{ARCHIVE}/20180927113612id_/https://www.eci.nic.in/eci_main/StatisticalReports/LS_1998/Vol_I_LS_98.pdf",
        None,
        group=NINETIES,
    ),
    Edition(1999, 13, capture("20180712181210", "LS_1999/Vol_I_LS_99.pdf"), None, group=NINETIES),
    Edition(
        2004,
        14,
        capture("20180712165954", "LS_2004/Vol_I_LS_2004.pdf"),
        capture("20180713093401", "LS_2004/Vol_II_LS_2004.pdf"),
        group=TWO_THOUSANDS,
        volume3=capture("20180508070337", "LS_2004/Vol_III_LS_2004.pdf"),
        layout="modern",
    ),
    Edition(
        2009,
        15,
        f"{ARCHIVE}/20180915124500id_/https://www.eci.nic.in/eci_main/archiveofge2009/Stats/VOLI/25_ConstituencyWiseDetailedResult.pdf",
        f"{ARCHIVE}/20180713101706id_/http://eci.nic.in:80/eci_main/archiveofge2009/Stats/VOLII/VolII_ConstituencyDataSummary.pdf",
        group=TWO_THOUSANDS,
        volume3=f"{ARCHIVE}/20180713082152id_/http://eci.nic.in:80/eci_main/archiveofge2009/Stats/VOLIII/VolIII_DetailsOfAssemblySegmentsOfPC.pdf",
        check=f"{ARCHIVE}/20180712190437id_/http://eci.nic.in:80/eci_main/archiveofge2009/Stats/VOLII/STATE_PARTYWISESEATWONANDVOTES_LokSabha2009_24012014.pdf",
        layout="modern",
    ),
]
BY_YEAR = {e.year: e for e in EDITIONS}
ORDINALS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
    13: "thirteenth",
    14: "fourteenth",
    15: "fifteenth",
}

METHOD = (
    "Constituency results from the detailed results in volume I of the Election Commission of India's statistical "
    "report on the {year} general election, as the report prints them: for each candidate the party and the votes, "
    "and for the constituency the electors, the voters (votes polled) and the valid votes. The winners are those "
    "the report's list of successful candidates names."
)
METHOD_MODERN = (
    "Constituency results from the Election Commission of India's statistical report on the {year} general election. "
    "Each candidate's party and votes (general and postal together) are from the detailed results in volume I; the "
    "electors, the voters, and the valid votes are from the constituency's summary page in volume II, which also "
    "names the winner; the votes in each assembly segment, and the postal ballots, which are counted for the whole "
    "constituency, are from volume III."
)
TWO_MEMBER = (
    " This constituency elected {seats} members, and each voter had as many votes as there were seats, so the "
    "candidates' votes add up to more than the valid votes the report prints, which count the voters."
)
NOTE = {
    "fptp": "Each constituency elects one member by plurality of the votes.",
    "block": (
        "Some constituencies elected two or three members, and each voter had as many votes as there were seats; "
        "the candidates with the most votes were elected."
    ),
}


def package_key(year: int) -> str:
    return f"{SOURCE}/ls-{year}"


def groups() -> list[str]:
    return sorted({e.group for e in EDITIONS})


def group_key(kind: str, group: str) -> str:
    return f"{SOURCE}/{kind}-{group}" if group else f"{SOURCE}/{kind}"


def members(group: str) -> list[Edition]:
    return [e for e in EDITIONS if e.group == group]


def packages() -> list[str]:
    keys = [group_key(kind, g) for g in groups() for kind in ("geo", "registry")]
    return [*keys, *(package_key(e.year) for e in EDITIONS)]


def known_parties() -> set[str]:
    """The party codes that the first packages (1951 to 1989) already minted, which later ones reference."""
    return set(json.loads(Path(__file__).with_name("eci_stat_parties.json").read_text()))


def urls() -> list[str]:
    found: list[str] = []
    for e in EDITIONS:
        found += [u for u in (e.volume1, e.volume2, e.volume3, e.check) if u]
        if e.later:
            found += [u for u in e.later if u]
    return found


def _pdf(path: Path) -> None:
    if not path.read_bytes().startswith(b"%PDF"):
        raise ValueError(f"{path.name} is not a PDF")


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for g in groups():
        geo(g).write()
        registry(g).write()
    for e in EDITIONS:
        election(e.year).write()


# The reports.


@dataclass
class Report:
    constituencies: list[reader.Constituency]
    winners: dict[tuple[str, int], list[reader.Winner]]
    summaries: dict[tuple[str, int], reader.Summary]
    parties: dict[str, str]
    totals: dict[str, reader.PartyTotal]
    assemblies: dict[tuple[str, int], modern.Assembly] = field(default_factory=dict)
    rejected: dict[tuple[str, int], int | None] = field(default_factory=dict)  # rejected postal votes, 2004 and later


@functools.cache
def read(url: str, volume2: str | None) -> Report:
    lines = reader.text(raw.cached(SOURCE, url).path)
    constituencies = reader.detailed(lines)
    known = {(c.state.upper(), c.number): c.name for c in constituencies}
    winners: dict[tuple[str, int], list[reader.Winner]] = {}
    for w in reader.successful(lines, known):
        winners.setdefault((w.state.upper(), w.number), []).append(w)
    summaries = reader.summaries(reader.text(raw.cached(SOURCE, volume2).path)) if volume2 else {}
    return Report(constituencies, winners, summaries, reader.parties(lines), reader.national(lines))


@functools.cache
def read_modern(e: Edition) -> Report:
    """The 2004 or 2009 report: detailed results, summaries, and assembly segments joined by state and number."""
    assert e.volume2 and e.volume3
    lines = reader.text(raw.cached(SOURCE, e.volume1).path)
    constituencies = modern.detailed(lines)
    entries = modern.assemblies(reader.text(raw.cached(SOURCE, e.volume3).path))
    state_of = {a.code: a.state.upper() for a in entries}
    pages = {(state_of[x.code], x.number): x for x in modern.summaries(reader.text(raw.cached(SOURCE, e.volume2).path))}
    summaries: dict[tuple[str, int], reader.Summary] = {}
    assemblies: dict[tuple[str, int], modern.Assembly] = {}
    rejected: dict[tuple[str, int], int | None] = {}
    for c in constituencies:
        k = (c.state.upper(), c.number)
        hits = [a for a in entries if a.number == c.number and modern.same_state(a.state, c.state)]
        if len(hits) != 1:
            raise ValueError(f"{c.state} {c.number} {c.name}: {len(hits)} entries in volume III")
        assembly = assemblies[k] = hits[0]
        page = pages.get((assembly.state.upper(), c.number))
        if page is None:
            raise ValueError(f"{c.state} {c.number} {c.name}: no summary page in volume II")
        c.electors, c.voters, c.valid = page.electors, page.voters, page.valid
        summaries[k] = reader.Summary(poll=page.poll, winners=page.winners)
        rejected[k] = page.rejected
        # Volume III spells a party code in full where the detailed results cut a long one at the column.
        paired = modern.match(c.candidates, assembly.parties, assembly.names)
        if paired is None:
            raise ValueError(f"{c.state} {c.number} {c.name}: volume III's candidates do not pair with volume I's")
        for number, candidate in (paired or {}).items():
            full = assembly.parties[number]
            if full != candidate.party and full.startswith(candidate.party):
                candidate.party = full
    # The 2004 report lists every participating party; the 2009 one the archive holds does not, so its parties take
    # the names the 2004 list gives them.
    named = reader.parties(reader.text(raw.cached(SOURCE, BY_YEAR[2004].volume1).path))
    totals = reader.national(lines) if e.year == 2004 else {}
    return Report(constituencies, {}, summaries, named, totals, assemblies, rejected)


def reports(e: Edition) -> list[Report]:
    if e.layout == "modern":
        return [read_modern(e)]
    found = [read(e.volume1, e.volume2)]
    if e.later:
        found.append(read(*e.later))
    return found


def area(year: int, state: str, number: int) -> str:
    return key("area", SCHEME, year, state.upper(), number)


def unit(year: int, state: str, number: int) -> str:
    return key("unit", year, state.upper(), number)


def party(code: str, group: str = "") -> str:
    """A party's id. A party the first packages hold keeps their id. The 2000s group mints its other parties under
    its own namespace, because the 1990s group mints parties from its own reports that this one cannot see."""
    if group == TWO_THOUSANDS and code not in known_parties():
        return key("party", group, code)
    return key("party", code)


SMALL = {"OF", "CUM", "AND", "THE", "IN", "TO", "BY", "ALIAS"}
KEEP = {"SC", "ST", "II", "III", "IV"}


def title(text: str) -> str:
    """Sentence-case an upper-case name; keep initials, reservation marks, and anything already in mixed case."""
    words: list[str] = []
    for word in text.split():
        bare = word.strip("()")
        if not word.isupper() or "." in word or bare in KEEP or len(bare) < 3 and bare not in SMALL:
            words.append(word)
        elif bare in SMALL and words:
            words.append(word.lower())
        else:
            words.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Election Commission of India",
        title="Statistical reports on the general elections to the Lok Sabha",
        url="https://www.eci.gov.in/statistical-reports/",
        license="Official publication of the Election Commission of India",
        license_url=f"{SITE}/",
        attribution="Election Commission of India, Statistical Report on General Elections to the Lok Sabha",
    )


# Places.


def span(group: str) -> str:
    years = [e.year for e in members(group)]
    return f"{min(years)} to {max(years)}"


def geo(group: str = "") -> Package:
    p = Package(
        group_key("geo", group),
        SOURCE,
        f"India's parliamentary constituencies in the Lok Sabha elections of {span(group)}, as the Election "
        "Commission's statistical reports list them",
    )
    p.external.add(INDIA)
    # Later groups restate the source and the scheme unchanged, so the package stands on its own.
    source_row(p)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=INDIA,
        kind="electoral_district",
        rank=1,
        noun="parliamentary constituency",
        code_system=None,
    )
    if any(e.layout == "modern" for e in members(group)):
        p.add(
            "scheme",
            code=SEGMENT_SCHEME,
            polity_id=INDIA,
            kind="precinct",
            rank=2,
            noun="assembly segment",
            code_system=None,
        )
    for e in members(group):
        for url in (e.volume1, e.volume2, e.volume3, *(e.later or ())):
            if url:
                p.uses(raw.cached(SOURCE, url))
        for report in reports(e):
            for c in report.constituencies:
                id = area(e.year, c.state, c.number)
                p.add("area", id=id, scheme_code=SCHEME)
                p.add("area_code", area_id=id, code=f"{e.year}:{slug(c.state)}:{c.number}")
                p.name(id, title(c.name), "en", "official")
    return p


# The Lok Sabha, its electoral systems, and the parties.


def registry(group: str = "") -> Package:
    p = Package(
        group_key("registry", group),
        SOURCE,
        f"The Lok Sabha, its electoral systems, and the parties that fielded candidates in {span(group)}"
        if not group
        else f"The parties that fielded candidates in the Lok Sabha elections of {span(group)}, beyond those the "
        "earlier elections' registry holds",
        depends=[group_key("geo", group)] if group else [f"{SOURCE}/geo"],
    )
    p.external.add(INDIA)
    for e in members(group):
        p.uses(raw.cached(SOURCE, e.volume1))
        if e.later:
            p.uses(raw.cached(SOURCE, e.later[0]))
    if group:
        p.external.update({BODY, *(party(code) for code in known_parties())})
    else:
        _body(p)
    _systems(p)
    codes: dict[str, str] = {}
    for e in members(group):
        for report in reports(e):
            for c in report.constituencies:
                for x in c.candidates:
                    if x.party and x.party != "IND":
                        codes.setdefault(x.party, report.parties.get(x.party) or "")
            for code, name in report.parties.items():
                if code in codes and not codes[code]:
                    codes[code] = name
    for code, name in sorted(codes.items()):
        if group and code in known_parties():
            continue
        id = party(code, group)
        p.add("party", id=id, kind="party", polity_id=INDIA)
        p.add(
            "external_id",
            entity_id=id,
            scheme="in.eci_stat.party" if group != TWO_THOUSANDS else f"in.eci_stat.party.{group}",
            value=code,
        )
        p.name(id, title(name) if name else code, "en", "official" if name else "abbreviation")
        if name:
            p.name(id, code, "en", "abbreviation", False)
    return p


def _body(p: Package) -> None:
    p.add("body", id=BODY, polity_id=INDIA, kind="lower_house")
    p.name(BODY, "Lok Sabha", "en", "official")
    p.name(BODY, "लोक सभा", "hi", "native", False)


def _systems(p: Package) -> None:
    """The two electoral systems; a later package restates them unchanged, since the snapshot has no systems."""
    for id, rules, note in (
        (FPTP, {"formula": "fptp"}, NOTE["fptp"]),
        (BLOCK, {"formula": "block"}, NOTE["block"]),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=INDIA,
            valid_from=date(1951, 1, 1),
            valid_to=date(1962, 1, 1) if id == BLOCK else None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")


# The elections.


def norm(name: str) -> str:
    return re.sub(r"[^A-Z]+", "", name.upper())


def winner_for(c: reader.Constituency, report: Report) -> list[reader.Candidate]:
    """The candidates the report declares elected in a constituency."""
    named = report.winners.get((c.state.upper(), c.number), [])
    chosen: list[reader.Candidate] = []
    if named:
        for w in named:

            def same(code: str, w: reader.Winner = w) -> bool:
                # The list cuts a long party code at its column: "CPI(ML)(L)" prints as "CPI(ML)(".
                return code == w.party or (w.party.count("(") > w.party.count(")") and code.startswith(w.party))

            hits = [x for x in c.candidates if norm(x.name) == norm(w.name) and same(x.party)]
            if len(hits) != 1:
                hits = [x for x in c.candidates if same(x.party) and norm(x.name)[:12] == norm(w.name)[:12]]
            if len(hits) != 1:
                raise ValueError(f"{c.state} {c.number} {c.name}: winner {w.name} ({w.party}) is not one candidate")
            chosen.append(hits[0])
    else:
        summary = report.summaries.get((c.state.upper(), c.number))
        for party_code, name, votes in summary.winners if summary else []:
            hits = [x for x in c.candidates if x.votes == votes and x.party == party_code]
            if len(hits) != 1:
                raise ValueError(f"{c.state} {c.number} {c.name}: winner {name} ({party_code}) is not one candidate")
            chosen.append(hits[0])
    if 0 < len(chosen) < c.seats and c.seats > 1:
        rest = sorted((x for x in c.candidates if x not in chosen), key=lambda x: -(x.votes or 0))
        extra = rest[: c.seats - len(chosen)]
        note = (
            f"The report's lists name {len(chosen)} of the {c.seats} winners; the other "
            f"{'seat goes' if len(extra) == 1 else 'seats go'} to {', '.join(x.name.title() for x in extra)}, "
            "the next candidates by votes."
        )
        if note not in c.caveats:
            c.caveats.append(note)
        chosen += extra
    if len(chosen) != c.seats:
        raise ValueError(f"{c.state} {c.number} {c.name}: {len(chosen)} winners for {c.seats} seats")
    return chosen


def polls(e: Edition) -> dict[tuple[str, int], date | None]:
    """The poll date printed on each constituency's summary page, where it is a date of the election's own year."""
    found: dict[tuple[str, int], date | None] = {}
    for year, report in zip((e.year, e.year + 1), reports(e), strict=False):
        for k, s in report.summaries.items():
            found[k] = s.poll if s.poll and s.poll.year in (year, year + 1) else None
    return found


def election(year: int) -> Package:
    e = BY_YEAR[year]
    p = Package(
        package_key(year),
        SOURCE,
        f"The {year} Indian general election to the Lok Sabha, by parliamentary constituency, from the Election "
        "Commission's statistical report",
        depends=[group_key("geo", e.group), group_key("registry", e.group)],
    )
    p.external.add(INDIA)
    if e.group:
        p.external.update({BODY, *(party(code) for code in known_parties())})
    for url in (e.volume1, e.volume2, e.volume3, *(e.later or ())):
        if url:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    dates = polls(e)
    printed = sorted(d for d in dates.values() if d)
    election_id = key("election", year)
    if printed:
        first, last, precision = printed[0], printed[-1], "day"
    else:
        first, last, precision = date(year, 1, 1), date(year, 12, 31), "year"
    p.add(
        "election",
        id=election_id,
        polity_id=INDIA,
        kind="general",
        date_from=first,
        date_to=last,
        date_precision=precision,
        status="held",
    )
    ordinal = ORDINALS[e.ordinal]
    p.name(election_id, f"{year} Indian general election", "en", "official")
    p.name(election_id, f"General Elections, {year} ({ordinal.capitalize()} Lok Sabha)", "en", "short", False)
    path = f"in/{year}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    dated = (
        "The dates are the polling dates the report prints on each constituency's summary page in volume II."
        if printed
        else "The report prints no polling dates, so the election carries its year."
    )
    adjourned = (
        " The polls adjourned in 1984 in Assam and Punjab were held in 1985; they are part of this election, with "
        "the dates the 1985 report prints."
        if e.later
        else ""
    )
    p.add(
        "note",
        id=key("note", election_id),
        entity_id=election_id,
        kind="methodology",
        body_md=f"The {ordinal} Lok Sabha general election. {dated}{adjourned}",
        lang="en",
    )
    for report in reports(e):
        for c in report.constituencies:
            _contest(p, e, report, election_id, path, c, dates.get((c.state.upper(), c.number)))
    _accept(p, e)
    return p


def _accept(p: Package, e: Edition) -> None:
    """Accept the accounting findings that the report's own printed figures cause, naming each constituency."""
    over: list[str] = []
    short: list[str] = []
    postal: list[str] = []
    for report in reports(e):
        for c in report.constituencies:
            where = f"{title(c.name)} ({title(c.state)}; {c.voters} voters, {c.electors} electors)"
            if c.voters is not None and c.electors is not None and c.voters > c.electors:
                over.append(where)
            if c.seats == 1 and c.valid is not None and c.candidates:
                total = sum(x.votes or 0 for x in c.candidates)
                if total != c.valid and not all(x.uncontested for x in c.candidates):
                    rejected = report.rejected.get((c.state.upper(), c.number))
                    if rejected is not None and total - c.valid == rejected:
                        postal.append(title(c.name))
                    else:
                        short.append(f"{title(c.name)} ({title(c.state)}; {total} votes, {c.valid} valid)")
    if over:
        p.accept(
            "accounting.turnout",
            "The report prints more voters than electors in " + "; ".join(over),
        )
    if short or postal:
        reason = "The report's candidate votes differ from its valid votes in "
        if postal:
            reason += (
                f"{len(postal)} constituencies by exactly the rejected postal votes its summary page prints, "
                "which the candidates' postal counts appear to include"
            )
            reason += "; and in " if short else ""
        if short:
            reason += "; ".join(short)
        reason += ". Where the page cuts off a candidate's votes, they are not stored."
        p.accept("accounting.votes_valid", reason)


def _contest(
    p: Package,
    e: Edition,
    report: Report,
    election_id: str,
    path: str,
    c: reader.Constituency,
    poll: date | None,
) -> None:
    tag = f"{e.year}:{c.state.upper()}:{c.number}"
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=BODY,
        district_area_id=area(e.year, c.state, c.number),
        magnitude=c.seats,
        round_number=1,
        electoral_system_id=FPTP if c.seats == 1 else BLOCK,
        default_result_kind="counted",
    )
    p.name(contest, f"{title(c.name)}, {title(c.state)}", "en", "official")
    p.add(
        "slug",
        path=f"{path}/{slug(c.state)}/{c.number}-{slug(c.name)}",
        entity_id=contest,
        is_current=True,
    )
    result_set = key("result_set", tag)
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
    method = (METHOD_MODERN if e.layout == "modern" else METHOD).format(year=e.year)
    if c.seats > 1:
        method += TWO_MEMBER.format(seats=c.seats)
    if poll:
        method += f" Poll date printed in volume II: {poll:%-d %B %Y}."
    if any(x.uncontested for x in c.candidates):
        method += " A candidate was returned uncontested; the report prints no votes for the candidate."
    method += "".join(f" {caveat}" for caveat in c.caveats)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    winners = {id(x) for x in winner_for(c, report)}
    options: list[tuple[reader.Candidate, str]] = []
    for x in c.candidates:
        who = key("person", tag, x.position)
        option = key("option", tag, x.position)
        coded = x.party not in ("", "IND")
        p.add("person", id=who, gender={"M": "male", "F": "female"}.get(x.sex or ""))
        p.name(who, title(x.name), "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(x.party, e.group) if coded else None,
            ballot_position=x.position,
            ballot_label=title(x.name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, x.position),
            person_id=who,
            option_id=option,
            role="head",
            ballot_name=title(x.name),
            member_of_party_id=party(x.party, e.group) if coded else None,
            is_independent=True if x.party == "IND" else None,
        )
        won = id(x) in winners
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost", seats=int(won))
        options.append((x, option))
    unit_id = unit(e.year, c.state, c.number)
    p.add(
        "reporting_unit",
        id=unit_id,
        polity_id=INDIA,
        scheme_code=SCHEME,
        code=f"{tag}",
        area_relation="equals",
        area_id=area(e.year, c.state, c.number),
        kind="standard",
    )
    for measure, value in ((REGISTERED, c.electors), (BALLOTS, c.voters), (VALID, c.valid)):
        _value(p, result_set, unit_id, None, measure, value)
    for x, option in options:
        _value(p, result_set, unit_id, option, VOTES, x.votes)
    if e.layout == "modern":
        _segments(p, e, report, c, tag, result_set, unit_id, options)


def _segments(
    p: Package,
    e: Edition,
    report: Report,
    c: reader.Constituency,
    tag: str,
    result_set: str,
    parent: str,
    options: list[tuple[reader.Candidate, str]],
) -> None:
    """The constituency's assembly segments and its postal ballots, as units under the constituency's."""
    assembly = report.assemblies[(c.state.upper(), c.number)]
    paired = modern.match(c.candidates, assembly.parties, assembly.names)
    if paired is None:
        raise ValueError(f"{c.state} {c.number} {c.name}: volume III's candidates do not pair with volume I's")
    option_of = {number: option for number, candidate in paired.items() for x, option in options if x is candidate}
    home = area(e.year, c.state, c.number)

    def child(code: str, kind: str) -> str:
        id = key("unit", tag, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=INDIA,
            scheme_code=SEGMENT_SCHEME,
            code=f"{tag}:{code}",
            parent_id=parent,
            area_relation="within",
            area_id=home,
            kind=kind,
        )
        return id

    for segment in assembly.segments:
        unit_id = child(str(segment.number), "standard")
        _value(p, result_set, unit_id, None, VALID, segment.total)
        for number, votes in segment.votes.items():
            _value(p, result_set, unit_id, option_of[number], VOTES, votes)
    if assembly.postal:
        unit_id = child("postal", "postal_district")
        _value(p, result_set, unit_id, None, VALID, assembly.postal_total)
        for number, votes in assembly.postal.items():
            _value(p, result_set, unit_id, option_of[number], VOTES, votes)


def _value(p: Package, result_set: str, unit_id: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit_id,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


# Checks against the report's own summaries.


def totals(year: int) -> dict[str, object]:
    """Compare the parsed constituencies with the report's party-wise national summary."""
    e = BY_YEAR[year]
    votes: Counter[str] = Counter()
    seats: Counter[str] = Counter()
    contested: Counter[str] = Counter()
    for report in reports(e):
        for c in report.constituencies:
            for x in winner_for(c, report):
                seats[x.party] += 1
            for x in c.candidates:
                votes[x.party] += x.votes or 0
                contested[x.party] += 1
    printed: dict[str, reader.PartyTotal] = {}
    for report in reports(e):
        for code, t in report.totals.items():
            old = printed.get(code)
            printed[code] = (
                t
                if old is None
                else reader.PartyTotal(
                    old.contested + t.contested, old.won + t.won, old.forfeited + t.forfeited, old.votes + t.votes
                )
            )
    differ = {
        code: {"printed": [t.won, t.votes, t.contested], "parsed": [seats[code], votes[code], contested[code]]}
        for code, t in printed.items()
        if (t.won, t.votes) != (seats[code], votes[code])
    }
    return {
        "year": year,
        "parties": len(printed),
        "seats": sum(seats.values()),
        "printed_seats": sum(t.won for t in printed.values()),
        "votes": sum(votes.values()),
        "printed_votes": sum(t.votes for t in printed.values()),
        "differ": differ,
        "unlisted": sorted(set(votes) - set(printed) - {""}),
    }


def state_totals(year: int) -> dict[str, object]:
    """Compare the parsed constituencies with the report's party-wise summary by state: votes and seats won."""
    e = BY_YEAR[year]
    printed: dict[tuple[str, str], reader.PartyTotal] = {}
    for url in (e.volume1, *([e.later[0]] if e.later else [])):
        printed.update(reader.by_state(reader.text(raw.cached(SOURCE, url).path)))
    got: dict[tuple[str, str], list[int]] = {}
    for report in reports(e):
        for c in report.constituencies:
            wins = {id(x) for x in winner_for(c, report)}
            for x in c.candidates:
                g = got.setdefault((x.party, c.state.upper()), [0, 0])
                g[0] += x.votes or 0
                g[1] += id(x) in wins
    checked, differ = 0, []
    for (code, state), t in sorted(printed.items()):
        found = [k for k in got if k[0] == code and (k[1] == state or norm(k[1])[:8] == norm(state)[:8])]
        if len(found) != 1:
            if t.contested:
                differ.append(f"{code} in {state}: not matched")
            continue
        checked += 1
        if tuple(got[found[0]]) != (t.votes, t.won):
            differ.append(f"{code} in {state}: printed {t.votes} votes and {t.won} seats, parsed {got[found[0]]}")
    return {"year": year, "rows": len(printed), "checked": checked, "differ": differ}


def table_2009() -> dict[str, object]:
    """Compare the parsed 2009 constituencies with the Commission's party-wise table of seats won and votes polled
    by state, which lists each party that won a seat (and the independents) with its seats and votes in the state."""
    e = BY_YEAR[2009]
    assert e.check
    rows = modern.state_parties(reader.text(raw.cached(SOURCE, e.check).path))
    report = read_modern(e)
    seats: dict[tuple[str, str], int] = {}
    votes: dict[tuple[str, str], int] = {}
    valid: dict[str, int] = {}
    for c in report.constituencies:
        wins = {id(x) for x in winner_for(c, report)}
        for x in c.candidates:
            code = "IND" if x.party == "IND" else x.party
            k = (c.state.upper(), code)
            votes[k] = votes.get(k, 0) + (x.votes or 0)
            seats[k] = seats.get(k, 0) + (id(x) in wins)
    differ: list[str] = []
    matched = 0
    for row in rows:
        state = row.state.upper()
        found = [k for k in votes if modern.same_state(k[0], state) and votes[k] == row.votes]
        if len(found) != 1:
            differ.append(f"{row.party} in {row.state}: no party with {row.votes} votes")
            continue
        matched += 1
        if seats[found[0]] != row.seats:
            differ.append(f"{row.party} in {row.state}: printed {row.seats} seats, parsed {seats[found[0]]}")
    for row in rows:
        valid.setdefault(row.state.upper(), row.state_valid)
    for state, printed in sorted(valid.items()):
        got = sum(v for (st, _), v in votes.items() if modern.same_state(st, state))
        if got != printed:
            differ.append(f"{state}: printed {printed} valid votes, the candidates' votes add up to {got}")
    return {
        "rows": len(rows),
        "matched": matched,
        "seats": sum(r.seats for r in rows),
        "parsed_seats": sum(seats.values()),
        "differ": differ,
    }
