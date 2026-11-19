"""Ecuador's presidential elections of 1978 to 1998, 2009, and 2013, from the National Electoral Council's presidents book.

The book, "Elecciones presidenciales del Ecuador 1948-2017", gives each round's national result: every ticket's votes,
the voters who took part, and the blank and null ballots. It prints each province's valid votes and each ticket's
share of them to one decimal, which does not pin down a province's votes, so only the national figures are stored. The
presidential rounds of 2002, 2006, and 2017 come from the Council's atlas and results book, and those of 2021 and 2025
from its parish-level files.
"""

import functools
import re
import subprocess
from datetime import date
from typing import NamedTuple

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne, cne2002

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = cne2002.COUNTRY
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.REGISTERED, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
BOOK = f"{cne.BASE}/2022/05/libro_presidentes_1948_2017-2.pdf"

# each round's day, and the day of the first round that a runoff follows
ROUNDS: dict[str, str | None] = {
    "1978-07-16": None,
    "1979-04-29": "1978-07-16",
    "1984-01-29": None,
    "1984-05-06": "1984-01-29",
    "1988-01-31": None,
    "1988-05-08": "1988-01-31",
    "1992-05-17": None,
    "1992-07-05": "1992-05-17",
    "1996-05-19": None,
    "1996-07-07": "1996-05-19",
    "1998-05-31": None,
    "1998-07-12": "1998-05-31",
    "2009-04-26": None,
    "2013-02-17": None,
}
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip
ROW = re.compile(r"^\s*(\d{1,3}(?:-\d{1,3})*)\s+([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ.\-' ]+?)\s{2,}([\d.]+)\s+(\d{1,3},\d)\s*%")
FOOT = re.compile(r"^\s*([\d.]+)\s+Votos en\s+([\d.]+)\s+Votos\s+([\d.]+)\s*$", re.MULTILINE)
DATE = re.compile(r"Fecha del proceso\s+(\d{1,2}) de (\w+) de (\d{4})")

METHOD = (
    "Counted by the Supreme Electoral Tribunal and published by the National Electoral Council in its book of "
    "Ecuador's presidential elections from 1948 to 2017, which prints each round's votes for the whole country. "
    "Registered voters are the electors on the roll, as the book gives them for the election, and ballots cast are the "
    "voters who took part. Null and blank ballots are counted separately, and valid votes are the votes the tickets "
    "received. The book prints each province's results as shares of its valid votes to one decimal only, so no "
    "provincial figures are stored."
)


class Entry(NamedTuple):
    number: str
    name: str
    votes: int


class Round(NamedTuple):
    day: date
    registered: int
    entries: list[Entry]
    cast: int
    blank: int
    null: int


def number(text: str) -> int:
    return int(text.replace(".", ""))


@functools.cache
def pages() -> list[str]:
    path = cne.artifact(BOOK).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return text.split("\f")


@functools.cache
def rounds() -> dict[str, Round]:
    """The book's rounds from 1978 on, by day: the page that gives a round's date and electors, then its results."""
    found: dict[str, Round] = {}
    texts = pages()
    for index, text in enumerate(texts):
        when = DATE.search(text)
        if when is None:
            continue
        day = date(int(when[3]), MONTHS[when[2]], int(when[1]))
        if day.isoformat() not in ROUNDS:
            continue
        registered = re.search(r"Electores\s+([\d.]+)", text)
        if registered is None:
            raise ValueError(f"no electors on the page of {day}")
        for here in texts[index : index + 4]:
            entries = [Entry(m[1], m[2].strip(), number(m[3])) for m in map(ROW.match, here.split("\n")) if m]
            foot = FOOT.search(here)
            if entries and foot:
                found[day.isoformat()] = Round(
                    day, number(registered[1]), entries, *(number(value) for value in foot.groups())
                )
                break
        else:
            raise ValueError(f"no results after the page of {day}")
    missing = sorted(set(ROUNDS) - set(found))
    if missing:
        raise ValueError(f"the book has no round on {missing}")
    return found


def packages() -> list[str]:
    return [f"{SOURCE}/{day}" for day in ROUNDS]


def fetch() -> None:
    raw.fetch(SOURCE, BOOK, suffix=".pdf")


def parse() -> None:
    for day in ROUNDS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


class Builder(cne2002.Builder):
    def result_set(self, contest: str, token: str) -> str:
        result_set = key("result_set", self.day, token)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=cne.CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
        )
        return result_set


def build(day: str) -> Package:
    first = ROUNDS[day]
    runoff = first is not None
    result = rounds()[day]
    year = (date.fromisoformat(first) if first else result.day).year
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Ecuador's {year} presidential election, {'second' if runoff else 'first'} round, nationally, from the "
        "National Electoral Council's presidents book",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{first}"] if first else [])],
    )
    p.external.update([ECUADOR, COUNTRY, "world.country"])
    p.uses(cne.artifact(BOOK))
    round_name = "second round" if runoff else ("single round" if year >= 2009 else "first round")
    tail = "presidential-runoff" if runoff else "general"
    b = Builder(p, day, f"ec/{day}-{tail}")
    p.add(
        "election",
        id=b.election,
        polity_id=ECUADOR,
        kind="runoff" if runoff else "general",
        date_from=result.day,
        date_to=result.day,
        status="held",
    )
    p.add("slug", path=b.base, entity_id=b.election, is_current=True)
    english = (
        f"{year} Ecuadorian presidential election, {round_name}" if runoff else f"{year} Ecuadorian general election"
    )
    spanish = (
        f"Elecciones presidenciales del Ecuador {year}, segunda vuelta"
        if runoff
        else f"Elecciones generales del Ecuador {year}"
    )
    p.name(b.election, english, "en", "official")
    p.name(b.election, spanish, "es", "native", False)
    contest = b.contest(
        "president",
        f"{year} Ecuadorian presidential election, {round_name}",
        f"Elecciones presidenciales del Ecuador {year}, {'segunda vuelta' if runoff else 'primera vuelta'}"
        if year < 2009
        else f"Elecciones presidenciales del Ecuador {year}, vuelta única",
        "president",
        "office",
        office_id=cne.PRESIDENCY,
        magnitude=1,
        round_number=2 if runoff else 1,
        previous_round_id=key("contest", first, "president") if first else None,
        electoral_system_id=cne.SYSTEM_PRESIDENT,
    )
    result_set = b.result_set(contest, "president")
    unit = b.national()
    options: list[str] = []
    for position, entry in enumerate(result.entries, 1):
        option = key("option", day, "president", entry.number)
        options.append(option)
        name = cne.title(entry.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=position,
            ballot_label=f"{name} (list {entry.number})",
        )
        person = key("person", f"president-{year}", entry.name)
        if not runoff:
            p.add("person", id=person)
            p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", day, "president", entry.number),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        cne._put(p, result_set, unit, option, VOTES, entry.votes)
    valid = sum(entry.votes for entry in result.entries)
    cne._put(p, result_set, unit, None, VALID, valid)
    cne._put(p, result_set, unit, None, BLANK, result.blank)
    cne._put(p, result_set, unit, None, INVALID, result.null)
    cne._put(p, result_set, unit, None, CAST, result.cast)
    cne._put(p, result_set, unit, None, REGISTERED, result.registered)
    order = sorted(range(len(options)), key=lambda i: -result.entries[i].votes)
    decided = runoff or result.entries[order[0]].votes * 2 > valid
    for i, option in enumerate(options):
        if decided:
            kind = "elected" if i == order[0] else "lost"
        else:
            kind = "advanced" if i in order[:2] else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    for option, entry in zip(options, result.entries, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=entry.votes)
    p.add("declared", result_set_id=result_set, option_id=None, measure=BLANK, value=result.blank)
    p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=result.null)
    p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=result.cast)
    p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=result.registered)
    gap = result.cast - valid - result.blank - result.null
    if gap:
        p.accept(
            "accounting.ballots_sum",
            f"the book's own national figures disagree: it counts {result.cast:,} voters who took part, and its "
            f"valid, blank, and null votes add up to {result.cast - gap:,}; stored as published",
        )
    return p
