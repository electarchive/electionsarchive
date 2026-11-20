"""Elections of the President of the European Parliament from 1994 to 2024, one contest per ballot, from the minutes of
the sitting at which each was held (https://www.europarl.europa.eu/doceo/document/PV-<term>-<date>_EN).

Each election is one package, `eu.ep_president/<date>`, built on `eu.ep_president/registry`. The Parliament votes in
plenary by secret ballot (Rules 15 and 16): the first three ballots need an absolute majority of the votes cast, and a
fourth is confined to the two leaders of the third. Each ballot is its own contest, linked to the previous one by
`previous_round_id`, with one national-level reporting unit, the plenary. The minutes print Members voting, blank or
void ballot papers, votes cast, the absolute majority, and the votes of each candidate; they are stored as declared
figures and as the plenary's values.
"""

import re
from dataclasses import dataclass
from datetime import date
from html import unescape
from uuid import uuid5

import pymupdf

from ea import package, raw
from ea.package import Package

SOURCE = "eu.ep_president"
EU = "01a13bbb-1c84-70ed-9bf7-35f8ba6c2cdf"
"""The European Union's polity id in the archive."""
PARLIAMENT = "01a13bbb-1c8c-7074-850c-9efefd03a10a"
"""The European Parliament's body id in the archive."""
PV = "https://www.europarl.europa.eu/doceo/document"
WAYBACK = "https://web.archive.org/web"
RULES_URL = "https://www.europarl.europa.eu/rules/rules20240716/Rules20240716_EN.pdf"
RULES_SNAPSHOT = f"{WAYBACK}/20261030083014id_/{RULES_URL}"
PRESIDENTS_URL = "https://historicalarchives.europarl.europa.eu/en/presidents"
SCHEME = "eu.plenary"
FIRST_DIRECT = date(1979, 7, 17)


@dataclass(frozen=True)
class Election:
    """One election of the President, from the minutes of the sitting that held it."""

    day: date
    term: int
    ballots: int
    url: str
    """The address under which the cached file was stored."""
    suffix: str

    @property
    def minutes(self) -> str:
        return f"{PV}/PV-{self.term}-{self.day}_EN.{self.suffix}"


ELECTIONS = [
    Election(date(1994, 7, 19), 4, 1, f"{PV}/PV-4-1994-07-19_EN.pdf", "pdf"),
    Election(date(1997, 1, 14), 4, 1, f"{PV}/PV-4-1997-01-14_EN.pdf", "pdf"),
    Election(date(1999, 7, 20), 5, 1, f"{PV}/PV-5-1999-07-20_EN.pdf", "pdf"),
    Election(date(2002, 1, 15), 5, 3, f"{PV}/PV-5-2002-01-15_EN.pdf", "pdf"),
    Election(date(2004, 7, 20), 6, 1, f"{PV}/PV-6-2004-07-20_EN.html", "html"),
    Election(date(2007, 1, 16), 6, 1, f"{PV}/PV-6-2007-01-16_EN.html", "html"),
    Election(date(2009, 7, 14), 7, 1, f"{PV}/PV-7-2009-07-14_EN.html", "html"),
    Election(date(2012, 1, 17), 7, 1, f"{PV}/PV-7-2012-01-17_EN.html", "html"),
    Election(
        date(2014, 7, 1),
        8,
        1,
        f"{WAYBACK}/20190719230111id_/http://www.europarl.europa.eu/doceo/document/PV-8-2014-07-01_EN.html",
        "html",
    ),
    Election(
        date(2017, 1, 17),
        8,
        4,
        f"{WAYBACK}/20190827214155id_/http://www.europarl.europa.eu/doceo/document/PV-8-2017-01-17_EN.html",
        "html",
    ),
    Election(
        date(2019, 7, 3),
        9,
        2,
        f"{WAYBACK}/20240621044709id_/{PV}/PV-9-2019-07-03_EN.html",
        "html",
    ),
    Election(
        date(2022, 1, 18),
        9,
        1,
        f"{WAYBACK}/20220319204445id_/{PV}/PV-9-2022-01-18_EN.html",
        "html",
    ),
    Election(
        date(2024, 7, 16),
        10,
        1,
        f"{WAYBACK}/20240718012535id_/{PV}/PV-10-2024-07-16_EN.html",
        "html",
    ),
]

NOTE = r"(?:\(\s*\d+\s*\))?"
BLOCK = re.compile(
    rf"Members voting\s*{NOTE}\s*:?\s*(\d+)\s*{NOTE}\s*[-–S]?\s*"
    r"Blank or (?:void|spoiled)(?: ballot papers)?\s*:?\s*(\d+)(?:\s*\(including[^)]*\))?\s*[-–S]?\s*"
    r"Votes cast\s*:?\s*(\d+)(?:\s*[-–S]?\s*Absolute majority\s*:?\s*(\d+))?"
)
ANNEX = re.compile(r"\(\s*(?:The list of Members|minutes of)[^)]*\)")
HEAD = re.compile(
    r"^.*?(?:as follows|Votes received|Votes obtained(?: by each candidate)?|cast as follows)\s*:?", re.DOTALL
)
STOP = re.compile(
    r"\bhad obtained\b|\bhaving obtained\b|\bSince no\b|\bAs none\b|\bIN THE CHAIR\b|"
    r"\bNo candidate\b|\(\s*1\s*\)"
)
TITLES = re.compile(r"^(?:Mr|Mrs|Ms|Miss)\s+")


@dataclass
class Ballot:
    number: int
    voting: int
    invalid: int
    cast: int
    majority: int | None
    votes: list[tuple[str, int]]


def fetch() -> None:
    for found in ELECTIONS:
        raw.fetch(SOURCE, found.url, suffix=f".{found.suffix}")


def text(found: Election) -> str:
    path = raw.cached(SOURCE, found.url).path
    if found.suffix == "pdf":
        with pymupdf.open(path) as document:
            body = " ".join(page.get_text(sort=True) for page in document)
    else:
        body = unescape(re.sub(r"<[^>]+>", " ", path.read_text(errors="replace")))
    return re.sub(r"\s+", " ", body.replace("\xa0", " "))


def read(found: Election) -> list[Ballot]:
    body = text(found)
    starts = list(BLOCK.finditer(body))
    if len(starts) < found.ballots:
        raise LookupError(f"{found.day}: found {len(starts)} ballots, expected {found.ballots}")
    ballots: list[Ballot] = []
    for number, start in enumerate(starts[: found.ballots], start=1):
        rest = ANNEX.sub(" ", body[start.end() :])
        stop = STOP.search(rest)
        segment = rest[: stop.start()] if stop else rest[:600]
        head = HEAD.match(segment)
        if head is None:
            raise LookupError(f"{found.day} ballot {number}: no candidate list")
        votes: list[tuple[str, int]] = []
        for name, count in re.findall(r"([^\d:]+?)\s*:?\s*(\d+)(?:\s+votes)?", segment[head.end() :]):
            clean = TITLES.sub("", re.sub(r"^(?:[\s\-–•]|S\s+(?=M))+|[\s:\-–]+$", "", name))
            votes.append((clean, int(count)))
        voting, invalid, cast = (int(n) for n in start.groups()[:3])
        majority = int(start[4]) if start[4] else None
        ballots.append(Ballot(number, voting, invalid, cast, majority, votes))
    for ballot in ballots:
        label = f"{found.day} ballot {ballot.number}"
        if ballot.voting != ballot.invalid + ballot.cast:
            raise ValueError(f"{label}: members voting do not equal blank plus votes cast")
        if sum(count for _, count in ballot.votes) != ballot.cast:
            raise ValueError(f"{label}: candidate votes {ballot.votes} do not sum to {ballot.cast}")
        if ballot.majority is not None and ballot.majority != ballot.cast // 2 + 1:
            raise ValueError(f"{label}: majority {ballot.majority} is not half of {ballot.cast} plus one")
        if len({name for name, _ in ballot.votes}) != len(ballot.votes):
            raise ValueError(f"{label}: a candidate appears twice")
    last = ballots[-1]
    leader = max(count for _, count in last.votes)
    if leader <= last.cast // 2:
        raise ValueError(f"{found.day}: the last ballot has no majority")
    return ballots


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "minutes")
OFFICE = key("office", "president")
SYSTEM = key("electoral_system", "president")
PLENARY = key("unit", "plenary")

SYSTEM_RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
SYSTEM_NOTE = (
    "Rule 16 of the Rules of Procedure: the President is elected by secret ballot, and the first three ballots need "
    "an absolute majority of the votes cast, with blank and void ballot papers not counted. If no candidate has "
    "obtained it after three ballots, the fourth is confined to the two candidates who had the most votes in the "
    "third; on a tie, the older candidate is elected. Candidates may withdraw between ballots."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The European Parliament's minutes as a source, the office of its President, the plenary as a reporting "
        "unit, and the rules of the election",
    )
    p.external.update({EU, PARLIAMENT})
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="European Parliament",
        title="Minutes of the sittings of the European Parliament",
        url="https://www.europarl.europa.eu/plenary/en/minutes.html",
        license="Reuse authorized with acknowledgement of the source (Decision 2011/833/EU)",
        license_url="https://www.europarl.europa.eu/legal-notice/en/",
        attribution="© European Union, European Parliament",
    )
    for url in (RULES_SNAPSHOT, PRESIDENTS_URL):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("office", id=OFFICE, polity_id=EU, body_id=PARLIAMENT, kind="speaker")
    p.name(OFFICE, "President of the European Parliament", "en", "official")
    p.add("scheme", code=SCHEME, polity_id=EU, kind="synthetic", rank=1, noun="plenary", code_system=None)
    p.add(
        "reporting_unit",
        id=PLENARY,
        polity_id=EU,
        scheme_code=SCHEME,
        code="plenary",
        area_relation="unlocated",
        kind="aggregate",
        valid_from=FIRST_DIRECT,
    )
    p.name(PLENARY, "European Parliament plenary", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=EU,
        valid_from=FIRST_DIRECT,
        valid_to=None,
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.name(SYSTEM, "Election of the President of the European Parliament", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def slug(found: Election) -> str:
    return f"{SOURCE}/{found.day}"


def election(found: Election) -> Package:
    ballots = read(found)
    p = Package(
        slug(found),
        SOURCE,
        f"The {found.day.year} election of the President of the European Parliament, "
        f"{len(ballots)} ballot{'s' if len(ballots) > 1 else ''}",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({EU})
    p.artifact(raw.cached(SOURCE, found.url), SOURCE_ID)
    election_id = key("election", found.day)
    title = f"{found.day.year} election of the President of the European Parliament"
    p.add(
        "election", id=election_id, polity_id=EU, kind="indirect", date_from=found.day, date_to=found.day, status="held"
    )
    p.name(election_id, title, "en", "official")
    p.add("slug", path=f"eu/{found.day}-parliament-president", entity_id=election_id, is_current=True)
    people: dict[str, str] = {}
    contests: list[str] = []
    for ballot in ballots:
        contest = key("contest", found.day, ballot.number)
        label = f"{title}, ballot {ballot.number}" if len(ballots) > 1 else title
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=OFFICE,
            magnitude=1,
            round_number=ballot.number,
            previous_round_id=contests[-1] if contests else None,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, label, "en", "official")
        p.add(
            "slug",
            path=f"eu/{found.day}-parliament-president/ballot-{ballot.number}"
            if len(ballots) > 1
            else f"eu/{found.day}-parliament-president/president",
            entity_id=contest,
            is_current=True,
        )
        contests.append(contest)
        result_set = key("result_set", found.day, ballot.number)
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
        later = {name for name, _ in ballots[ballot.number].votes} if ballot.number < len(ballots) else set()
        winner = max(ballot.votes, key=lambda item: item[1])[0] if ballot.number == len(ballots) else None
        options: dict[str, str] = {}
        for position, (name, count) in enumerate(ballot.votes, start=1):
            if name not in people:
                person = people[name] = key("person", found.day, name)
                p.add("person", id=person)
                p.name(person, name, "en", "official")
            option = options[name] = key("option", found.day, ballot.number, name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", found.day, ballot.number, name),
                person_id=people[name],
                option_id=option,
                role="head",
                ballot_name=name,
            )
            if winner is not None:
                kind = "elected" if name == winner else "lost"
            else:
                kind = "advanced" if name in later else "eliminated" if ballot.number + 1 == 4 else "withdrew"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        totals = ((3, ballot.voting), (5, ballot.invalid), (4, ballot.cast))
        for measure, value in totals:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=PLENARY,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for name, count in ballot.votes:
            p.add("declared", result_set_id=result_set, option_id=options[name], measure=1, value=count)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=PLENARY,
                option_id=options[name],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=count,
            )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(slug(found) for found in ELECTIONS)]


def parse() -> None:
    registry().write()
    for found in ELECTIONS:
        election(found).write()


def emit() -> None:
    package.run("load", packages())
