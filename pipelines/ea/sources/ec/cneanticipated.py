"""Ecuador's early general election of 20 August 2023 and its presidential runoff of 15 October 2023, by canton.

The Council's results book for the early elections and popular consultations of 2023 prints the presidential vote for
each province by canton and, in the runoff, for each overseas country. The first round's tables cover the national
territory only, and the runoff's add the overseas circumscriptions. The tables are read from the book's text layer,
and each is checked against the totals printed under or beside it before it is stored. The Yasuní popular consultation
of 20 August 2023, which the book prints by province, is stored with the first round as a ballot question. The Chocó
Andino consultation of the same day is loaded by `ec.consultas`.

The first round's package also holds the National Assembly contests of the day: the 15 national seats by province and
overseas circumscription, the 116 provincial seats by canton (in ten circumscriptions of Guayas, Manabí, and
Pichincha, by the cantons each one holds), and the 6 overseas seats by country. ``book2023`` reads their tables, and
each is checked against the totals the book prints before it is stored. The book also prints the parishes of those
circumscriptions, with the canton each belongs to; each is matched to the Council's parish lists within its canton,
and a second result set per circumscription holds the parish rows and supersedes the one that keeps the canton rows.
"""

import functools
import re
from collections import Counter
from datetime import date
from typing import NamedTuple

from ea import package, raw
from ea.package import Package
from ea.sources.ec import book2017, book2023, cne, cne2017

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
    "ALFREDO BAQUERIZO MORENO": "A BAQUERIZO MORENO",
    "COREA SUR REPUBLICA DE COREA": "COREA SUR REPUBLICA DE",
}
METHOD = (
    "Counted by the National Electoral Council and published in its results book for the early elections of 2023, "
    "which prints each province's results by canton and the overseas vote by country. Registered voters are the "
    "electors on the roll, ballots cast are the voters who took part, and null and blank ballots are counted "
    "separately. Each voter casts one vote for a ticket, so valid votes are the votes the tickets received."
)
METHOD_LISTS = (
    "Counted by the National Electoral Council and published in its results book for the early elections of 2023, "
    "which prints each contest's votes by province, canton, or country, and by parish in the larger "
    "circumscriptions. Registered voters are the electors on the roll, ballots cast are the voters who took part, "
    "and null and blank ballots are counted separately. A voter in a legislative contest casts several votes, so no "
    "valid-ballot figure is stored for those contests; each list's votes are the sum of its candidates' votes."
)
# the book's heading of each overseas circumscription, without accents or punctuation, and the Council's code for it
EXTERIOR = {
    "CANADA Y ESTADOS UNIDOS": "27",
    "EUROPA OCEANIA Y ASIA": "26",
    "LATINOAMERICA EL CARIBE Y AFRICA": "28",
}


SYSTEM_QUESTION = key("electoral_system", "early-2023", "question")
YASUNI_METHOD = (
    "Counted by the National Electoral Council and published in its results book for the early elections of 2023, "
    "which prints the votes for yes and no in each province. Valid votes are the votes for the two answers; the "
    "book prints no figures for blank and null ballots on this question."
)
YASUNI_ES = (
    "¿Está usted de acuerdo con que el gobierno ecuatoriano mantenga el crudo del ITT, conocido como bloque 43, "
    "indefinidamente bajo el subsuelo?"
)
YASUNI_ROW = re.compile(r"^(?:([A-ZÁÉÍÓÚÑ ]+?) )?([\d.]+) \d+,\d+% ([\d.]+) \d+,\d+% ([\d.]+)$")


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


def yasuni_rows() -> tuple[list[tuple[str, int, int, int]], tuple[int, int, int]]:
    """The Yasuní consultation's rows by province (name, yes, no, valid) and the book's national row."""
    for page in range(len(book2017.pages(BOOK)), 0, -1):
        text = page_text(page)
        if "RESULTADOS ELECTORALES POR PROVINCIA" in text and "YASUN" in text:
            break
    else:
        raise ValueError("no Yasuní results by province")
    lines = [line.strip() for line in text.split("\n")]
    found: list[tuple[str, int, int, int]] = []
    total: tuple[int, int, int] | None = None
    for i, line in enumerate(lines):
        line = re.sub(r"^\d+,\d+% ", "", line)
        match = YASUNI_ROW.match(line)
        if match is None:
            continue
        name = match[1] or ""
        if not name:
            # a long name wraps around its figures: SANTO DOMINGO DE / ... / LOS TSÁCHILAS
            name = f"{lines[i - 1]} {lines[i + 1]}"
        numbers = [int(match[k].replace(".", "")) for k in (2, 3, 4)]
        if name.strip() == "TOTAL NACIONAL":
            total = (numbers[0], numbers[1], numbers[2])
        else:
            found.append((name.strip(), numbers[0], numbers[1], numbers[2]))
    if total is None:
        raise ValueError("the Yasuní table has no national row")
    return found, total


def yasuni(b: Builder) -> None:
    p = b.p
    token = "yasuni"
    english = "2023 Ecuadorian popular consultation on the Yasuní ITT oil block"
    contest = b.contest(
        token,
        english,
        "Consulta popular del Yasuní 2023",
        "consultation/yasuni",
        "ballot_question",
        district_area_id=COUNTRY,
        electoral_system_id=SYSTEM_QUESTION,
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.add(
        "note",
        id=key("note", contest, "question"),
        entity_id=contest,
        kind="context",
        body_md=YASUNI_ES,
        lang="es",
    )
    yes, no = (key("option", DAY1, token, name) for name in ("yes", "no"))
    for position, (option, es, en) in enumerate(((yes, "Sí", "Yes"), (no, "No", "No")), 1):
        p.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=es)
        p.name(option, es, "es", "ballot")
        p.name(option, en, "en", "english", False)
    result_set = key("result_set", DAY1, token)
    p.add(
        "result_set",
        id=result_set,
        election_id=b.election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=cne.CNE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=YASUNI_METHOD,
        lang="en",
    )
    rows, (total_yes, total_no, total_valid) = yasuni_rows()
    if len(rows) != 24:
        raise ValueError(f"the Yasuní table has {len(rows)} provinces, not 24")
    seen = [0, 0, 0]
    for name, y, n, valid in rows:
        if y + n != valid:
            raise ValueError(f"Yasuní {name}: {y:,} + {n:,} is not the printed {valid:,}")
        unit = b.province_unit(b.place.province(f"PROVINCIA - {name}"))
        cne._put(p, result_set, unit, yes, VOTES, y)
        cne._put(p, result_set, unit, no, VOTES, n)
        cne._put(p, result_set, unit, None, VALID, valid)
        seen = [seen[0] + y, seen[1] + n, seen[2] + valid]
    if seen != [total_yes, total_no, total_valid]:
        raise ValueError(f"Yasuní provinces add up to {seen}, not the printed {[total_yes, total_no, total_valid]}")
    p.add("outcome", result_set_id=result_set, option_id=yes if total_yes > total_no else no, kind="won")
    for option, measure, value in ((yes, VOTES, total_yes), (no, VOTES, total_no), (None, VALID, total_valid)):
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


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
    def result_set(self, contest: str, token: str, superseded: bool = False, supersedes: str | None = None) -> str:
        result_set = key("result_set", self.day, token + ("/parish" if supersedes else ""))
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=cne.CNE_SOURCE,
            status="superseded" if superseded else "active",
            is_default=not superseded,
            completeness=1.0,
            supersedes_id=supersedes,
        )
        method = METHOD if token == "president" else METHOD_LISTS
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        return result_set


def canton_name(name: str) -> str:
    text = book2017.norm(name)
    return ALIASES.get(text, text)


def circumscription_code(name: str) -> str:
    return EXTERIOR[book2017.norm(name)]


def check_group(group: book2017.Group) -> None:
    """Make sure a contest's rows add up to the totals the book prints for it."""
    count = len(group.entries)
    total = group.total
    if total is None:
        raise ValueError(f"{group.head}: the table has no total row")
    sums = [sum(r.numbers[i] for r in group.units) for i in range(count + 5)]
    printed = list(total.numbers)
    if sums != printed:
        raise ValueError(f"{group.head}: the rows add up to {sums}, not the printed {printed}")
    if printed[:count] != [e.votes for e in group.entries]:
        raise ValueError(f"{group.head}: the list totals differ from the summary page")
    box = (printed[count + 3], printed[count + 4], printed[count + 1], printed[count + 2])
    if box != (group.info["registered"], group.info["cast"], group.info["blank"], group.info["null"]):
        raise ValueError(f"{group.head}: the table's figures differ from the summary box")
    if sum(printed[:count]) != printed[count]:
        raise ValueError(f"{group.head}: valid votes differ from the lists' votes")


def assembly(b: Builder) -> None:
    """The National Assembly contests: the national seats, each province's or circumscription's, and the overseas."""
    place = b.place
    national, won = book2023.national()
    nation_rows = []
    for row in national.units:
        text = book2017.norm(row.name)
        code = EXTERIOR[text] if text in EXTERIOR else place.province(row.name)
        nation_rows.append((b.province_unit(code), row))
    count = len(national.entries)
    sums = [sum(r.numbers[i] for r in national.units) for i in range(count + 1)]
    if sums != list(national.total.numbers if national.total else ()):
        raise ValueError(f"the national table adds up to {sums}, not to the printed total")
    if sums[:count] != [e.votes for e in national.entries]:
        raise ValueError("the national table differs from the national summary")
    cne2017.lists(
        b,
        national,
        "national",
        "2023 Ecuadorian National Assembly election, national seats",
        "Asambleístas nacionales del Ecuador 2023",
        "national-assembly",
        cne2017.ASSEMBLY,
        nation_rows,
        national=True,
        seats_won=won,
    )
    groups = book2023.groups()

    def where(head: str) -> str:
        return place.province("PROVINCIA - " + head.removeprefix("PROVINCIA "))

    seen: Counter[tuple[str, str]] = Counter()
    for group in groups:
        if group.head.startswith("PROVINCIA") and cne2017.circumscription(group.head):
            province = where(group.head)
            for row in group.units:
                seen[(province, place.canton(province, canton_name(row.name)))] += 1
    shared: Counter[str] = Counter()  # the circumscriptions each parish appears in
    for group in groups:
        if group.head.startswith("PROVINCIA") and group.parishes:
            province = where(group.head)
            shared.update(
                place.parish_code(province, canton_name(c.row.name), r.name)
                for c in book2017.parish_tables(group)
                for r in c.parishes
                if not place.is_zone(c.row.name)
            )
    for group in groups:
        check_group(group)
        head = group.head
        if head.startswith("PROVINCIA"):
            province = where(head)
            circ = cne2017.circumscription(head)
            name = place.province_name(province)
            rows = [
                (
                    b.canton_unit(
                        province,
                        canton_name(r.name),
                        circ,
                        partial=seen[(province, place.canton(province, canton_name(r.name)))] > 1,
                    ),
                    r,
                )
                for r in group.units
            ]
            if circ:
                english = f"2023 Ecuadorian National Assembly election, {name} provincial seats, circumscription {circ}"
                spanish = f"Asambleístas provinciales de {name} 2023, circunscripción {circ}"
                tail = f"provincial/{cne.slug(name)}-{circ}"
            else:
                english = f"2023 Ecuadorian National Assembly election, {name} provincial seats"
                spanish = f"Asambleístas provinciales de {name} 2023"
                tail = f"provincial/{cne.slug(name)}"
            finer = (
                cne2017.parish_rows(b, group, province, circ, rows, shared, canton_name)
                if circ and group.parishes
                else None
            )
            cne2017.lists(
                b, group, f"province/{province}/{circ}", english, spanish, tail, cne2017.ASSEMBLY, rows, parishes=finer
            )
        else:
            province = circumscription_code(head.split(":")[1])
            rows = [(b.canton_unit(province, canton_name(r.name)), r) for r in group.units]
            where_ = cne2017.PROVINCE_ENGLISH[province]
            cne2017.lists(
                b,
                group,
                f"overseas/{province}",
                f"2023 Ecuadorian National Assembly election, overseas seats for {where_}",
                f"Asambleístas del exterior de {cne2017.PROVINCE_NAMES[province]} 2023",
                f"overseas/{cne.slug(cne2017.PROVINCE_NAMES[province])}",
                cne2017.ASSEMBLY,
                rows,
            )


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
            *([f"{SOURCE}/{DAY1}"] if runoff else [f"{SOURCE}/registry-2017"]),
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
    if not runoff:
        p.add(
            "electoral_system", id=SYSTEM_QUESTION, polity_id=ECUADOR, rules={"formula": "referendum"}, rules_version=1
        )
        p.add(
            "note",
            id=key("note", SYSTEM_QUESTION),
            entity_id=SYSTEM_QUESTION,
            kind="methodology",
            body_md="The answer with more valid votes than the other carries the question.",
            lang="en",
        )
        yasuni(b)
        assembly(b)
    if gaps:
        p.accept(
            "accounting.ballots_sum",
            "the book's own canton figures disagree: valid votes plus blank and null ballots differ from the ballots "
            f"cast in {sum(gaps.values())} cantons and countries, by {max(map(abs, gaps)):,} ballots at most and by "
            f"{sum(abs(g) * c for g, c in gaps.items()):,} in all; stored as published",
        )
    return p
