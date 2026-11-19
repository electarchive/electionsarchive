"""Ecuador's early general election of 20 August 2023 and its presidential runoff of 15 October 2023, by canton.

The Council's results book for the early elections and popular consultations of 2023 prints the presidential vote for
each province by canton and, in the runoff, for each overseas country. The first round's tables cover the national
territory only, and the runoff's add the overseas circumscriptions. The tables are read from the book's text layer,
and each is checked against the totals printed under or beside it before it is stored.
"""

import functools
import re
from collections import Counter
from datetime import date
from typing import NamedTuple

from ea import package, raw
from ea.package import Package
from ea.sources.ec import book2017, cne, cne2017

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = cne2017.COUNTRY
DAY1, DAY2 = "2023-08-20", "2023-10-15"
DATES = {DAY1: date(2023, 8, 20), DAY2: date(2023, 10, 15)}
BOOK = f"{cne.BASE}/2024/06/ResultadosElectoralesAnticipadasyConsultas2023.pdf"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.REGISTERED, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
area = cne.area

# the first-round tickets in the order the book prints them: the head of the ticket
FIRST = (
    "Yaku Pérez",
    "Daniel Noboa",
    "Luisa González",
    "Jan Topić",
    "Otto Sonnenholzner",
    "Bolívar Armijos",
    "Fernando Villavicencio",
    "Xavier Hervas",
)
RUNOFF = FIRST[1:3]
# the book's heading of each overseas circumscription and the Council's code for it
CIRCUMSCRIPTIONS = {
    "EUROPA ASIA Y OCEANIA": "26",
    "CANADA Y ESTADOS UNIDOS": "27",
    "LATINOAMERICA EL CARIBE Y AFRICA": "28",
}
# the book's spelling of a canton that the Council's files spell otherwise, as the files spell it
ALIASES = {
    "CORONEL MARCELINO MARIDUENAS": "CRNL MARCELINO MARIDUENAS",
    "GENERAL ANTONIO ELIZALDE": "GRAL A ELIZALDE",
}
METHOD = (
    "Counted by the National Electoral Council and published in its results book for the early elections of 2023, "
    "which prints each province's results by canton and the overseas vote by country. Registered voters are the "
    "electors on the roll, ballots cast are the voters who took part, and null and blank ballots are counted "
    "separately. Each voter casts one vote for a ticket, so valid votes are the votes the tickets received."
)


class Table(NamedTuple):
    heading: str
    units: list[book2017.Row]
    total: book2017.Row


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY1}", f"{SOURCE}/{DAY2}"]


def fetch() -> None:
    raw.fetch(SOURCE, BOOK, suffix=".pdf")


def parse() -> None:
    build(DAY1).write()
    build(DAY2).write()


def emit() -> None:
    package.run("load", packages())


def page_text(page: int) -> str:
    return "\n".join(" ".join(w.text for w in ws) for _, ws in book2017.lines(page, BOOK))


@functools.cache
def sections() -> dict[str, list[tuple[int, str]]]:
    """The pages of each round that hold results tables, with the section heading of each."""
    found: dict[str, list[tuple[int, str]]] = {DAY1: [], DAY2: []}
    for page in range(1, len(book2017.pages(BOOK)) + 1):
        lines = page_text(page).split("\n")
        head = " ".join(lines[:2])
        if "PRESIDENTE/A Y VICEPRESIDENTE/A" not in head or len(lines) < 4:
            continue
        day = DAY1 if "PRIMERA VUELTA" in head else DAY2 if "SEGUNDA VUELTA" in head else None
        if day is None:
            continue
        found[day].append((page, " ".join(lines[2:6])))
    return found


def tables(day: str, marker: str) -> list[Table]:
    """Every table of a round whose page says `marker`, each with the book's own total row."""
    width = 13 if day == DAY1 else 7
    result: list[Table] = []
    pending: list[book2017.Row] = []
    heading = ""
    for page, head in sections()[day]:
        if marker not in head:
            continue
        here = book2017.rows(page, width, book=BOOK)
        title = head.split(marker)[0].replace("PRIMERA VUELTA", "").replace("SEGUNDA VUELTA", "").strip()
        if not pending:
            heading = title
        pending += here
        total = [r for r in pending if r.name.startswith("TOTAL")]
        if total:
            result.append(Table(heading, [r for r in pending if r is not total[0]], total[0]))
            pending = []
    if pending:
        raise ValueError(f"{day}: a table under {heading!r} has no total row")
    return result


def nation() -> dict[str, int]:
    """The runoff's printed national figures: votes for each ticket, then blank, null, voters, and electors."""
    for page, _ in sections()[DAY2]:
        text = page_text(page)
        if "VOTACIÓN POR CANDIDATOS" in text and "ELECTORES" in text:
            break
    else:
        raise ValueError("no national summary for the runoff")
    figures: dict[str, int] = {}
    for label in ("ELECTORES", "SUFRAGANTES", "VOTOS EN BLANCO", "VOTOS NULOS"):
        found = re.search(rf"{label}\s+([\d.]+)", text)
        if found is None:
            raise ValueError(f"the runoff summary has no {label}")
        figures[label] = int(found[1].replace(".", ""))
    chart = [int(v.replace(" ", "")) for v in re.findall(r"\b\d \d{3} \d{3}\b", text)]
    if len(chart) != 2:
        raise ValueError(f"the runoff summary prints {len(chart)} ticket totals, not 2")
    return {"first": chart[0], "second": chart[1], **figures}


class Builder(cne2017.Builder):
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


def province_code(heading: str) -> str:
    text = book2017.norm(heading.replace("PROVINCIA:", ""))
    return CIRCUMSCRIPTIONS[text] if text in CIRCUMSCRIPTIONS else cne2017.places().province(f"PROVINCIA - {text}")


def build(day: str) -> Package:
    runoff = day == DAY2
    tickets = RUNOFF if runoff else FIRST
    count = len(tickets)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Ecuador's 2023 early {'presidential runoff' if runoff else 'general election'} by canton, from the National "
        "Electoral Council's results book",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/geo-2017",
            f"{SOURCE}/registry",
            *([f"{SOURCE}/{DAY1}"] if runoff else []),
        ],
    )
    p.external.update([ECUADOR, COUNTRY, "world.country"])
    p.uses(cne.artifact(BOOK))
    base = f"ec/{day}-{'presidential-runoff' if runoff else 'general'}"
    b = Builder(p, day, base)
    p.add(
        "election",
        id=b.election,
        polity_id=ECUADOR,
        kind="runoff" if runoff else "general",
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
    )
    p.add("slug", path=base, entity_id=b.election, is_current=True)
    english = (
        "2023 Ecuadorian presidential election, second round" if runoff else "2023 Ecuadorian early general election"
    )
    spanish = (
        "Elecciones presidenciales del Ecuador 2023, segunda vuelta"
        if runoff
        else "Elecciones generales anticipadas del Ecuador 2023"
    )
    p.name(b.election, english, "en", "official")
    p.name(b.election, spanish, "es", "native", False)
    contest = b.contest(
        "president",
        f"2023 Ecuadorian presidential election, {'second' if runoff else 'first'} round",
        f"Elecciones presidenciales del Ecuador 2023, {'segunda' if runoff else 'primera'} vuelta",
        "president",
        "office",
        office_id=cne.PRESIDENCY,
        magnitude=1,
        round_number=2 if runoff else 1,
        previous_round_id=key("contest", DAY1, "president") if runoff else None,
        electoral_system_id=cne.SYSTEM_PRESIDENT,
    )
    result_set = b.result_set(contest, "president")
    options: list[str] = []
    for position, name in enumerate(tickets, 1):
        option = key("option", day, "president", name)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=position,
            ballot_label=name,
        )
        person = key("person", "president-2023", name)
        if not runoff:
            p.add("person", id=person)
            p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", day, "president", name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    totals = [0] * (count + 5)
    gaps = Counter[int]()
    sections_ = [(t, False) for t in tables(day, "RESULTADOS POR CANTÓN")]
    if runoff:
        sections_ += [(t, True) for t in tables(day, "RESULTADOS POR PAÍS")]
    for table, overseas in sections_:
        province = province_code(table.heading)
        for row in table.units:
            name = book2017.norm(row.name)
            unit = b.canton_unit(province, ALIASES.get(name, name))
            n = row.numbers
            for option, votes in zip(options, n[:count], strict=True):
                cne._put(p, result_set, unit, option, VOTES, votes)
            valid = sum(n[:count])
            cne._put(p, result_set, unit, None, VALID, valid)
            cne2017.figures(p, result_set, unit, n[count + 3], n[count + 4], n[count + 1], n[count + 2])
            gap = n[count + 4] - valid - n[count + 1] - n[count + 2]
            if gap:
                gaps[gap] += 1
            for i, value in enumerate((*n[:count], valid, *n[count + 1 :])):
                totals[i] += value
        if sum(table.total.numbers[:count]) != sum(sum(r.numbers[:count]) for r in table.units):
            raise ValueError(f"{day} {table.heading}: votes do not add up to the printed total")
        del overseas
    leader = max(range(count), key=lambda i: totals[i])
    runner = sorted(range(count), key=lambda i: -totals[i])[:2]
    for i, option in enumerate(options):
        if runoff:
            kind = "elected" if i == leader else "lost"
        else:
            kind = "advanced" if i in runner else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    if runoff:
        printed = nation()
        declared = [printed["first"], printed["second"]]
        extras = {
            "blank": printed["VOTOS EN BLANCO"],
            "null": printed["VOTOS NULOS"],
            "cast": printed["SUFRAGANTES"],
            "registered": printed["ELECTORES"],
        }
    else:
        row = tables(day, "RESULTADOS POR PROVINCIA")[0].total.numbers
        declared = list(row[:count])
        extras = {"blank": row[count + 1], "null": row[count + 2], "cast": row[count + 4], "registered": row[count + 3]}
    if declared != totals[:count]:
        raise ValueError(f"{day}: canton votes {totals[:count]} differ from the printed totals {declared}")
    for option, value in zip(options, declared, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    for measure, name in ((REGISTERED, "registered"), (CAST, "cast"), (INVALID, "null"), (BLANK, "blank")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=extras[name])
    mine = dict(
        zip(
            ("blank", "null", "registered", "cast"),
            (totals[count + 1], totals[count + 2], totals[count + 3], totals[count + 4]),
        )
    )
    for name, value in mine.items():
        if value != extras[name]:
            raise ValueError(f"{day}: summed {name} {value:,} differs from the printed {extras[name]:,}")
    if gaps:
        p.accept(
            "accounting.ballots_sum",
            "the book's own canton figures disagree: valid votes plus blank and null ballots differ from the ballots "
            f"cast in {sum(gaps.values())} cantons and countries, by {max(map(abs, gaps)):,} ballots at most and by "
            f"{sum(abs(g) * c for g, c in gaps.items()):,} in all; stored as published",
        )
    return p
