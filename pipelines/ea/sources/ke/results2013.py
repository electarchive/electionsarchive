"""The packages for Kenya's general election of 4 March 2013: the parties on its ballots and the election with its six
ballots, all read from the IEBC's "4th March 2013 General Election Data"."""

import difflib
import functools
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from typing import cast

import pymupdf

from ea import raw
from ea.package import Package
from ea.sources.ke import iebc, report2013
from ea.sources.ke.iebc import CONSTITUENCY, COUNTY, KENYA, SOURCE, TERRITORY, WARD, area, key, titled
from ea.sources.ke.report2013 import Block
from ea.sources.ke.results import (
    DIASPORA,
    NATIONAL,
    PRESIDENCY,
    PRESIDENTIAL,
    REGISTERED,
    REJECTED,
    SENATE,
    VALID,
    VOTES,
    Builder,
)

DATA = "https://www.iebc.or.ke/docs/4TH%20MARCH%202013%20GENERAL%20ELECTION%20DATA.pdf"
DATA_SOURCE = key("source", "data-2013")
DAY = date(2013, 3, 4)
PATH = "ke/2013-03-04-general"
CAST = 3
# The ward that the 2022 roll calls Sala, the 2013 tables call Bulla Mpya.
WARD_ALIASES = {"bullampya": "sala"}
COUNTY_ALIASES = {"nairobi": "nairobicity", "tharaka": "tharakanithi"}
# The constituencies whose names the 2022 roll spells differently from the 2013 tables.
RENAMED = {"251": "Mbita", "252": "Suba"}

METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed in its 4th March 2013 General Election "
    "Data. The book prints each candidate's votes in a constituency, county, or county assembly ward and the total of "
    "valid votes under each contest, and prints no registered voters or rejected ballots for these ballots. It "
    "prints no winners for them either, so the candidate with the most votes is stored as elected, and the only "
    "candidate of a ward, whom the book prints as unopposed or prints with no votes, is stored as elected without a "
    "vote count. The party strength tables at the end "
    "of the book count the seats each party won, and the elected candidates here add up to those counts. The book "
    "prints a candidate's party as a name, an abbreviation, or a misprint of either. Where the name is one party's "
    "and nothing else's, the candidate is stored under that party; an abbreviation that more than one party uses, or "
    "a misprint that cannot be read, is stored without a party. Lines whose votes the book's text layer does not "
    "carry are stored without a value. In two ward contests, Mbeti South and Bogusero, the printed total holds more votes "
    "than the candidates' lines add up to, by more than the lead of the first candidate, so no winner is stored for "
    "them. The book notes that three county assembly ward elections "
    "(Kuria East Gokeharaka, Nyabasi West, and Samburu North) were not held on 4 March, and counts 1,447 member "
    "seats; it still prints candidates and votes for 1,450 wards, which are stored as printed."
)
PRESIDENT_METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed per constituency in the book's "
    "summary of the presidential results, with a line for votes cast outside Kenya. Each candidate's votes, valid "
    "votes, rejected ballots, ballots cast, and registered voters are as printed, and the book's grand total is stored "
    "as declared. The book prints no party for the candidates in this table, so none is stored. Uhuru Kenyatta won "
    "more than half of the valid votes and the Supreme Court dismissed the petitions against the result on 30 March "
    "2013."
)


@functools.cache
def document() -> pymupdf.Document:
    return pymupdf.open(raw.cached(SOURCE, DATA).path)


@functools.cache
def tables() -> dict[str, tuple[list[Block], int | None]]:
    doc = document()
    return {ballot: report2013.blocks(ballot, report2013.read(doc, ballot)) for ballot in report2013.COLUMNS}


@functools.cache
def president() -> tuple[list[report2013.Line], list[report2013.Line], report2013.Line, report2013.Line]:
    first, last = report2013.PAGES["president"]
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(raw.cached(SOURCE, DATA).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return report2013.president(text)


def similar(a: str, b: str, aliases: dict[str, str]) -> float:
    """How alike two printed area names are: the best ratio of the first against the whole of the second or the
    part of it before a slash."""
    x = aliases.get(iebc.fold(a), iebc.fold(a))
    options = [b, *b.split("/")[:1]]
    return max(difflib.SequenceMatcher(None, x, aliases.get(iebc.fold(o), iebc.fold(o))).ratio() for o in options)


@functools.cache
def wards() -> dict[str, iebc.Ward]:
    """Each ward block's code, mapped to the roll's ward with its name among the wards numbered near it.

    A few lines of the 2013 tables number neighboring wards differently from the 2022 roll."""
    roll = iebc.roll()
    by_code = {w.code: w for w in roll}
    out: dict[str, iebc.Ward] = {}
    for block in tables()["ward"][0]:
        listed = by_code[block.code]
        if similar(block.name, listed.name, WARD_ALIASES) >= 0.8:
            out[block.code] = listed
            continue
        pool = [w for w in roll if w.county == listed.county and abs(int(w.code) - int(listed.code)) <= 6]
        ranked = sorted(pool, key=lambda w: similar(block.name, w.name, WARD_ALIASES), reverse=True)
        if similar(block.name, ranked[0].name, WARD_ALIASES) < 0.5 or (
            len(ranked) > 1
            and similar(block.name, ranked[0].name, WARD_ALIASES) == similar(block.name, ranked[1].name, WARD_ALIASES)
        ):
            raise ValueError(f"ward {block.code} {block.name} is not one of {[w.name for w in pool]}")
        out[block.code] = ranked[0]
    if len({w.code for w in out.values()}) != len(out) or len(out) != 1450:
        raise ValueError("the ward blocks do not map one to one onto the roll's 1,450 wards")
    return out


@functools.cache
def counties(ballot: str) -> dict[str, iebc.Ward]:
    """Each county block's code, mapped by its name to a ward of the roll in that county."""
    roll = iebc.roll()
    first = {w.county: w for w in roll}
    out: dict[str, iebc.Ward] = {}
    for block in tables()[ballot][0]:
        ranked = sorted(first.values(), key=lambda w: similar(block.name, w.county_name, COUNTY_ALIASES), reverse=True)
        if similar(block.name, ranked[0].county_name, COUNTY_ALIASES) < 0.7:
            raise ValueError(f"{ballot}: {block.name} is not a county")
        out[block.code] = ranked[0]
    if len({w.county for w in out.values()}) != 47:
        raise ValueError(f"{ballot}: the county blocks are not the 47 counties")
    return out


@functools.cache
def constituencies() -> dict[str, iebc.Ward]:
    return {w.constituency: w for w in iebc.roll()}


def party_id(name: str) -> str:
    return key("party", "2013", name)


@functools.cache
def used_parties() -> list[str]:
    seen: set[str] = set()
    for blocks, _ in tables().values():
        for block in blocks:
            for entry in block.entries:
                printed = report2013.party(entry.party)
                if printed not in (None, "independent"):
                    seen.add(cast(str, printed))
    return sorted(seen)


def unsettled(block: Block) -> bool:
    """Whether the book's printed total holds more votes than its candidates' lines add up to, by more than the lead
    of the first candidate over the second, so that a candidate whose line is missing could have won."""
    votes = sorted((e.votes for e in block.entries if e.votes is not None), reverse=True)
    if block.total is None or len(votes) < 2:
        return False
    return block.total - sum(votes) > votes[0] - votes[1]


def held(block: Block) -> float:
    """1 for a contest with a printed vote or total, 0 for one the book prints without any, such as an unopposed one."""
    return 1.0 if block.total is not None or any(e.votes is not None for e in block.entries) else 0.0


def person_name(text: str) -> str:
    return titled(re.sub(r"\s+", " ", text.replace("’", "'")).strip(" .,"))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-2013",
        SOURCE,
        "The parties on Kenya's 2013 ballots",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {KENYA, TERRITORY}
    p.add(
        "source",
        id=DATA_SOURCE,
        kind="official_body",
        publisher="Independent Electoral and Boundaries Commission",
        title="4th March 2013 General Election Data",
        url=DATA,
    )
    p.artifact(raw.cached(SOURCE, DATA), DATA_SOURCE)
    for name in used_parties():
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=KENYA)
        p.name(id, name, "en", "official")
    return p


class Ballots(Builder):
    """The 2013 builder: candidates arrive as printed names with a party's name."""

    def contenders(self, contest: str, result_set: str, unit: str, tag: str, block: Block) -> None:
        p = self.p
        options: list[tuple[str, int | None]] = []
        for position, e in enumerate(block.entries, 1):
            printed = report2013.party(e.party)
            independent = printed == "independent"
            member = party_id(printed) if printed not in (None, "independent") else None
            option = key("option", self.day, tag, position)
            person = key("person", self.day, tag, position)
            label = person_name(e.name)
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", self.day, tag, position, "head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=member,
                is_independent=True if independent else None,
            )
            self.put(result_set, unit, option, VOTES, e.votes)
            options.append((option, e.votes))
        if len(block.entries) == 1 and block.entries[0].votes is None and block.total is None:
            p.add("outcome", result_set_id=result_set, option_id=options[0][0], kind="elected", seats=1)
        elif not unsettled(block):
            self.outcomes(result_set, options)
        if block.total is not None:
            self.put(result_set, unit, None, VALID, block.total)


def general() -> Package:
    p = Package(
        f"{SOURCE}/general-2013",
        SOURCE,
        "The 4 March 2013 general election: president by constituency, and governor, senator, woman representative, "
        "member of the National Assembly, and member of the county assembly by their areas",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/registry-2013"],
    )
    p.external |= {KENYA, TERRITORY}
    p.uses(raw.cached(SOURCE, DATA))
    b = Ballots(p, DAY, DATA_SOURCE, PATH)
    election = key("election", DAY)
    p.add("election", id=election, polity_id=KENYA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2013 Kenyan general election", "en", "official")
    p.add("slug", path=b.path, entity_id=election, is_current=True)
    p.add("note", id=key("note", election), entity_id=election, kind="methodology", body_md=METHOD, lang="en")
    roll = iebc.roll()
    county_name = {w.county: titled(w.county_name) for w in roll}
    constituency_name = {w.constituency: titled(w.constituency_name) for w in roll}
    seen: list[str] = []
    president_table(b, election)
    for ballot, title, slug_name, kind, body in (
        ("governor", "Governor of {} County", "governor", "office", None),
        ("senate", "Senator for {} County", "senator", "body_seats", SENATE),
        ("woman", "Woman Representative for {} County", "woman-representative", "body_seats", NATIONAL),
    ):
        places = counties(ballot)
        for block in tables()[ballot][0]:
            county = places[block.code].county
            tag = f"{ballot}:{county}"
            contest, result_set = b.contest(
                election,
                tag,
                title.format(county_name[county]),
                b.slug(election, slug_name, county_name[county]),
                area(COUNTY, county),
                kind=kind,
                office=key("office", "governor", county) if ballot == "governor" else None,
                body=body,
                completeness=held(block),
            )
            b.contenders(contest, result_set, b.unit(COUNTY, county), tag, block)
            seen.append(tag)
    names = Counter(constituency_name.values())
    for block in tables()["assembly"][0]:
        w = constituencies()[block.code]
        name = RENAMED.get(block.code, constituency_name[block.code])
        label = name + (f" ({block.code})" if names[constituency_name[block.code]] > 1 else "")
        key_ = f"{w.county}/{w.constituency}"
        tag = f"assembly:{block.code}"
        contest, result_set = b.contest(
            election,
            tag,
            f"Member of the National Assembly for {label}",
            b.slug(election, "national-assembly", label),
            area(CONSTITUENCY, key_),
            body=NATIONAL,
            completeness=held(block),
        )
        b.contenders(contest, result_set, b.unit(CONSTITUENCY, key_), tag, block)
    places = wards()
    for block in tables()["ward"][0]:
        w = places[block.code]
        if block.unnamed:
            continue
        tag = f"ward:{w.code}"
        contest, result_set = b.contest(
            election,
            tag,
            f"Member of the {county_name[w.county]} County Assembly for {titled(w.name)} Ward",
            b.slug(election, "county-assembly", w.code, titled(w.name)),
            area(WARD, w.code),
            body=key("body", "county-assembly", w.county),
            completeness=held(block),
        )
        b.contenders(contest, result_set, b.unit(WARD, w.code), tag, block)
    accept(p, b)
    return p


def president_table(b: Ballots, election: str) -> None:
    p = b.p
    lines, county_lines, diaspora, grand = president()
    contest, result_set = b.contest(
        election,
        "president",
        "2013 Kenyan presidential election",
        f"{b.path}/president",
        TERRITORY,
        kind="office",
        office=PRESIDENCY,
        system=PRESIDENTIAL,
        method=PRESIDENT_METHOD,
    )
    options: list[str] = []
    for position, name in enumerate(report2013.CANDIDATES, 1):
        option = key("option", b.day, "president", position)
        person = key("person", b.day, "president", name)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", b.day, "president", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        options.append(option)
    places = constituencies()
    if sorted(line.code for line in lines) != sorted(places) or len(county_lines) != 47:
        raise ValueError("the presidential table is not the 290 constituencies in 47 counties")
    for line in lines:
        w = places[line.code]
        put_line(b, result_set, b.unit(CONSTITUENCY, f"{w.county}/{w.constituency}"), options, line)
    put_line(b, result_set, b.unit(DIASPORA, DIASPORA), options, diaspora)
    for measure, value in (
        (REGISTERED, grand.registered),
        (VALID, grand.valid),
        (REJECTED, grand.rejected),
        (CAST, grand.cast),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, votes in zip(options, grand.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    b.outcomes(result_set, list(zip(options, grand.votes, strict=True)))


def put_line(b: Ballots, result_set: str, unit: str, options: list[str], line: report2013.Line) -> None:
    b.put(result_set, unit, None, REGISTERED, line.registered)
    for option, votes in zip(options, line.votes, strict=True):
        b.put(result_set, unit, option, VOTES, votes)
    b.put(result_set, unit, None, VALID, line.valid)
    b.put(result_set, unit, None, REJECTED, line.rejected)
    b.put(result_set, unit, None, CAST, line.cast)


def mismatches() -> dict[str, list[str]]:
    """The contests whose printed total of valid votes is not the sum of their candidates' votes, by ballot."""
    out: dict[str, list[str]] = defaultdict(list)
    for ballot, (blocks, _) in tables().items():
        for block in blocks:
            summed = sum(e.votes or 0 for e in block.entries)
            if block.total is not None and block.total != summed and not block.unnamed:
                out[ballot].append(f"{block.name.title()} {block.total:,} against {summed:,}")
    return dict(out)


def accept(p: Package, b: Ballots) -> None:
    """Accept the findings that the book's own lines cause: a contest whose printed valid votes are not its
    candidates' sum, and the presidential figures that are not the sum of the constituency lines."""
    found = mismatches()
    if found:
        p.accept(
            "accounting.votes_valid",
            "The book prints a total of valid votes that is not the sum of the printed votes of its candidates, "
            + "; ".join(f"in {ballot} contests: " + ", ".join(items) for ballot, items in found.items()),
        )
    lines, _, diaspora, grand = president()
    pairs = [
        ("registered voters", grand.registered, sum(x.registered for x in lines) + diaspora.registered),
        ("ballots cast", grand.cast, sum(x.cast for x in lines) + diaspora.cast),
        ("rejected ballots", grand.rejected, sum(x.rejected for x in lines) + diaspora.rejected),
        ("valid votes", grand.valid, sum(x.valid for x in lines) + diaspora.valid),
    ]
    differ = [f"{label} {printed:,} against {summed:,}" for label, printed, summed in pairs if printed != summed]
    if differ:
        p.accept(
            "accounting.declared",
            "The book's grand total of the presidential table is not the sum of its constituency lines and its "
            "diaspora line: " + "; ".join(differ),
        )
