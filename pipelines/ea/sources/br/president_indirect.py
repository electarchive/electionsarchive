"""Brazil's indirect presidential elections of 1964 to 1985, by roll call of the electors.

Congress chose the president in 1964, 1966, and 1969 (Amendment 9 of 1964 and Institutional Act 2 of 1965 for the first
two, Institutional Act 16 of 1969 for the third), and an electoral college of Congress and state assembly delegates
chose in 1974, 1978, and 1985 (Constitutional Amendment 1 of 1969 and its 1977 revision). Each vote was called by name
and recorded in the congressional journal. The national totals come from the Portuguese Wikipedia article on each
election, whose infobox and text transcribe those journals; the article on 1964 cites the minutes of the joint session
of 11 April 1964. The files are pinned by hash in the raw cache.
"""

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "br.president_indirect"
BRAZIL = "01a13ba1-cd2c-7065-8397-2903884f736e"
NATION_AREA = "01a13ba1-cd80-7234-b7d9-32ca8179b966"
PRESIDENCY = "01a13ba1-d3a7-7589-a5ab-432323275643"
SCHEME = "br.electoral_college"
REGISTRY = f"{SOURCE}/registry"
VOTES, REGISTERED, BALLOTS, VALID, BLANK = 1, 2, 3, 4, 6


@dataclass(frozen=True)
class Candidate:
    name: str
    party: str | None
    votes: int


@dataclass(frozen=True)
class Vote:
    day: date
    body: str  # who voted
    candidates: tuple[Candidate, ...]
    needles: tuple[str, ...]  # text the article must hold, as the infobox and prose print it
    registered: int | None = None
    ballots: int | None = None
    blank: int | None = None
    note: str = ""


PARTIES = {
    "ARENA": "Aliança Renovadora Nacional",
    "MDB": "Movimento Democrático Brasileiro",
    "PMDB": "Partido do Movimento Democrático Brasileiro",
    "PDS": "Partido Democrático Social",
    "PDC": "Partido Democrata Cristão",
    "PSD": "Partido Social Democrático",
}
CONGRESS = "Congress (the Chamber of Deputies and the Senate in joint session)"
COLLEGE = "the electoral college of the members of Congress and delegates of the state assemblies"
ELECTIONS = [
    Vote(
        date(1964, 4, 11),
        CONGRESS,
        (
            Candidate("Humberto de Alencar Castelo Branco", None, 361),
            Candidate("Juarez Távora", "PDC", 3),
            Candidate("Eurico Gaspar Dutra", "PSD", 2),
        ),
        (
            "Castelo Branco: 361 votos",
            "Juarez Távora: 3 votos",
            "Eurico Gaspar Dutra: 2 votos",
            "109 abstenções e ausências",
        ),
        note="The article counts 72 abstentions and 37 members absent, 109 in all, who are not stored. The vice "
        "president was elected on a separate ballot the same day, which this package does not hold.",
    ),
    Vote(
        date(1966, 10, 3),
        CONGRESS,
        (Candidate("Artur da Costa e Silva", "ARENA", 294),),
        ("Costa e Silva: 294 votos", "177 abstenções e ausências"),
        note="The opposition (MDB) boycotted the vote, and the article counts 177 abstentions and absences, which are "
        "not stored.",
    ),
    Vote(
        date(1969, 10, 25),
        CONGRESS,
        (Candidate("Emílio Garrastazu Médici", "ARENA", 293),),
        ("Emílio Garrastazu Médici: 293 votos", "76 abstenções"),
        note="The article counts 76 abstentions, which are not stored.",
    ),
    Vote(
        date(1974, 1, 15),
        COLLEGE,
        (
            Candidate("Ernesto Geisel", "ARENA", 400),
            Candidate("Ulysses Guimarães", "MDB", 76),
        ),
        (
            "Ernesto Geisel: 400 votos",
            "Ulysses Guimarães: 76 votos",
            "21 votos em branco",
            "6 ausências",
            "composto de 503 eleitores",
            "comparecimento de 497 votantes",
        ),
        registered=503,
        ballots=497,
        blank=21,
        note="The article's table counts 21 blank votes, and its text calls the same 21 abstentions declared by "
        "dissident MDB members. Six electors were absent.",
    ),
    Vote(
        date(1978, 10, 15),
        COLLEGE,
        (
            Candidate("João Figueiredo", "ARENA", 355),
            Candidate("Euler Bentes Monteiro", "MDB", 226),
        ),
        ("João Figueiredo: 355 votos", "Euler Bentes Monteiro: 226 votos", "11 abstenções"),
        note="The article counts 11 abstentions, which are not stored.",
    ),
    Vote(
        date(1985, 1, 15),
        COLLEGE,
        (
            Candidate("Tancredo Neves", "PMDB", 480),
            Candidate("Paulo Maluf", "PDS", 180),
        ),
        (
            "Tancredo Neves: 480 votos",
            "Paulo Maluf: 180 votos",
            "26 abstenções",
            "686 votantes no Colégio Eleitoral",
        ),
        registered=686,
        note="The article counts 26 abstentions, including five PT members told to vote null, which are not stored. "
        "Tancredo Neves died before taking office, and José Sarney, elected vice president, became president.",
    ),
]
METHOD = (
    "National totals of the roll call by {body}, from the Portuguese Wikipedia article on the election, pinned to the "
    "revision fetched; the article transcribes the congressional journal. "
    "Abstentions and absences are not options and are not stored unless the article gives the electorate or the votes "
    "cast, as noted. {note}"
)
SYSTEM = (
    "The president was chosen by the electors in a roll call, with no popular vote. The vice president ran on a "
    "ticket with the president from 1966, and was elected on a separate ballot in 1964."
)


def title(year: int) -> str:
    return f"Eleição presidencial no Brasil em {year}"


def wiki_url(year: int) -> str:
    return f"https://pt.wikipedia.org/w/index.php?title={quote(title(year).replace(' ', '_'))}&action=raw"


def key(v: Vote) -> str:
    return f"{SOURCE}/president-{v.day.year}"


def packages() -> list[str]:
    return [REGISTRY, *(key(v) for v in ELECTIONS)]


def fetch() -> None:
    for v in ELECTIONS:
        raw.fetch(SOURCE, wiki_url(v.day.year), suffix=".wiki")


def person(name: str) -> str:
    return str(uuid5(NAMESPACE, f"{SOURCE}\x1fperson\x1f{name}"))


def party(abbreviation: str) -> str:
    return str(uuid5(NAMESPACE, f"{SOURCE}\x1fparty\x1f{abbreviation}"))


def check(v: Vote) -> None:
    text = re.sub(r"\s+", " ", raw.cached(SOURCE, wiki_url(v.day.year)).path.read_text(encoding="utf-8"))
    for needle in v.needles:
        if needle not in text:
            raise ValueError(f"{v.day}: the article does not contain {needle!r}")
    for c in v.candidates:
        if f"{c.votes}" not in text:
            raise ValueError(f"{v.day}: the article does not print {c.votes}")
    if v.ballots is not None and v.ballots != sum(c.votes for c in v.candidates) + (v.blank or 0):
        raise ValueError(f"{v.day}: the votes do not add up to the ballots cast")


def parse() -> None:
    for v in ELECTIONS:
        check(v)
    registry().write()
    for v in ELECTIONS:
        election(v).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "Candidates, parties, and the electors' unit of Brazil's indirect presidential votes")
    p.external |= {BRAZIL, NATION_AREA}
    p.add(
        "scheme", code=SCHEME, polity_id=BRAZIL, kind="administrative", rank=1, noun="electoral body", code_system=None
    )
    unit = p.id("unit", "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BRAZIL,
        scheme_code=SCHEME,
        code="BR",
        area_relation="equals",
        area_id=NATION_AREA,
        kind="aggregate",
    )
    names = sorted({c.name for v in ELECTIONS for c in v.candidates})
    for name in names:
        id = person(name)
        p.add("person", id=id, gender="male")
        p.name(id, name, "pt", "official")
    for abbreviation, name in PARTIES.items():
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=BRAZIL)
        p.name(id, name, "pt", "official")
        p.name(id, abbreviation, "pt", "abbreviation", False)
    return p


def election(v: Vote) -> Package:
    year, day = v.day.year, v.day
    p = Package(
        key(v),
        SOURCE,
        f"The indirect presidential election of {day:%-d %B %Y} by roll call, from the Portuguese Wikipedia article",
        depends=[REGISTRY],
    )
    p.external |= {BRAZIL, NATION_AREA, PRESIDENCY}
    unit = p.id("unit", "national")
    source = p.id("source", "wikipedia", year)
    p.add(
        "source",
        id=source,
        kind="community",
        publisher="Wikipedia (pt)",
        title=title(year),
        url="https://pt.wikipedia.org/wiki/" + quote(title(year).replace(" ", "_")),
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
    )
    p.artifact(raw.cached(SOURCE, wiki_url(year)), source)
    election_id = p.id("election", year)
    p.add("election", id=election_id, polity_id=BRAZIL, kind="indirect", date_from=day, date_to=day, status="held")
    path = f"br/{day}-presidential-election"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Brazilian presidential election (indirect)", "en", "official")
    p.name(election_id, f"{title(year)} (eleição indireta)", "pt", "native", False)
    contest = p.id("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION_AREA,
        magnitude=1,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Brazilian presidential election (indirect)", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    p.add("note", id=p.id("note", contest, "system"), entity_id=contest, kind="context", body_md=SYSTEM, lang="en")
    result_set = p.id("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD.format(body=v.body, note=v.note),
        lang="en",
    )
    winner = max(v.candidates, key=lambda c: c.votes)
    rows: list[tuple[str | None, int, int]] = []
    for position, c in enumerate(v.candidates, 1):
        option = p.id("option", year, c.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(c.party) if c.party else None,
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=p.id("candidacy", year, c.name),
            person_id=person(c.name),
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party(c.party) if c.party else None,
            is_independent=c.party is None,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if c is winner else "lost")
        rows.append((option, VOTES, c.votes))
    if v.registered is not None:
        rows.append((None, REGISTERED, v.registered))
    if v.ballots is not None:
        rows.append((None, BALLOTS, v.ballots))
        rows.append((None, VALID, sum(c.votes for c in v.candidates)))
    if v.blank is not None:
        rows.append((None, BLANK, v.blank))
    p.frame(
        "result_value",
        pl.DataFrame(
            [(unit, opt, measure, n) for opt, measure, n in rows],
            schema={"unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    return p
