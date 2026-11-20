"""The Rondônia plebiscites on municipal boundaries held in 2009, 2010, and 2012, from the Regional Electoral Court's reports.

The Electoral Court of Rondônia (TRE-RO) published the result of each poll as a PDF report from its totalization
system: 13 December 2009 (two municipalities voting on a transfer of territory), 28 February 2010 (the creation of
Extrema de Rondônia, voted in all of Porto Velho, with a report of every section), and 7 October 2012 (the creation of
Tarilândia, voted in Jaru and Governador Jorge Teixeira, and a transfer of territory between Nova Brasilândia do Oeste
and Castanheiras, voted in both). Each package builds one election per day under the state, with one ballot question
per municipality that voted. The 2010 poll is reported by section; the others by municipality.

The archive already holds the state, the municipalities, and the Brazil polity, and the packages refer to them by id.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "br.municipal_plebiscites"
BASE = "https://www.justicaeleitoral.jus.br/arquivos/"
HOME = "https://www.tre-ro.jus.br/"
# The archive's own rows for Brazil and Rondônia
BRAZIL = "01a13ba1-cd2c-7065-8397-2903884f736e"
RONDONIA = "01a13ba1-cd85-748e-9cef-84eaf91b2e42"
SCHEME = "br.municipality"
SECTIONS = "br.secao"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
YES, NO = "Sim", "Não"


@dataclass
class Town:
    code: str  # the electoral court's municipality code
    name: str
    area: str  # the archive's area for the municipality


TOWNS = {
    town.code: town
    for town in (
        Town("00035", "Porto Velho", "01a13ba1-cda8-738f-b4ec-530627a473f7"),
        Town("00159", "Jaru", "01a13ba1-cda8-738f-b4ec-530c5d8d4bfd"),
        Town("00370", "Nova Brasilândia do Oeste", "01a13ba1-cda8-738f-b4ec-5317c32e1b31"),
        Town("00418", "São Miguel do Guaporé", "01a13ba1-cda8-738f-b4ec-531935390825"),
        Town("00531", "Castanheiras", "01a13ba1-cda8-738f-b4ec-531f6c316ac3"),
        Town("00612", "Governador Jorge Teixeira", "01a13ba1-cda8-738f-b4ec-5326144e34d3"),
    )
}


@dataclass
class Poll:
    """One question put to the electors of one municipality, from one report."""

    slug: str  # a short key for the question, used in the URL
    title: str  # what the poll decided, in English
    town: str
    file: str  # the report's name under BASE
    sections: str | None = None  # the report of every section, when there is one
    question: str = ""  # the question as printed
    found: dict[str, int] = field(default_factory=dict)


@dataclass
class Day:
    date: date
    name: str
    polls: list[Poll]


DAYS = [
    Day(
        date(2009, 12, 13),
        "2009 Rondônia plebiscite on a transfer of territory",
        [
            Poll(
                "transfer-nova-brasilandia",
                "Transfer of territory from Nova Brasilândia do Oeste",
                "00370",
                "tre-ro-eleicao-2009-plebiscito-nova-brasilandia-resultado-final",
            ),
            Poll(
                "transfer-sao-miguel",
                "Transfer of territory from São Miguel do Guaporé",
                "00418",
                "tre-ro-eleicao-2009-plebiscito-sao-miguel-resultado-final",
            ),
        ],
    ),
    Day(
        date(2010, 2, 28),
        "2010 Rondônia plebiscite on the creation of Extrema de Rondônia",
        [
            Poll(
                "extrema-de-rondonia",
                "Creation of the municipality of Extrema de Rondônia",
                "00035",
                "tre-ro-eleicao-2010-plebiscito-extrema-resultado-de-votacao",
                sections="tre-ro-plebiscito-2010-resultado-por-secao",
            )
        ],
    ),
    Day(
        date(2012, 10, 7),
        "2012 Rondônia plebiscites on municipal boundaries",
        [
            Poll(
                "tarilandia-jaru",
                "Creation of the municipality of Tarilândia, voted in Jaru",
                "00159",
                "tre-ro-resultado-do-plebiscito-em-jaru-10-10-12",
            ),
            Poll(
                "tarilandia-jorge-teixeira",
                "Creation of the municipality of Tarilândia, voted in Governador Jorge Teixeira",
                "00612",
                "tre-ro-resultado-do-plebiscito-em-jorge-teixeira-10-10-12",
            ),
            Poll(
                "territory-castanheiras",
                "Transfer of part of Nova Brasilândia do Oeste to Castanheiras, voted in Castanheiras",
                "00531",
                "tre-ro-resultado-do-plebiscito-em-castanheiras-10-10-12",
            ),
            Poll(
                "territory-nova-brasilandia",
                "Transfer of part of Nova Brasilândia do Oeste to Castanheiras, voted in Nova Brasilândia do Oeste",
                "00370",
                "tre-ro-resultado-do-plebiscito-em-nova-brasilandia-10-10-12",
            ),
        ],
    ),
]
METHOD = (
    "Reported by the Electoral Court of Rondônia from the totalization of the ballot-box records. Registered voters "
    "are the electors able to vote in the sections that were counted, ballots cast are the votes counted (turnout), "
    "valid votes are the votes for yes and no, and invalid votes are the null votes. The 2012 reports are marked "
    "official but subject to change for appeals; the electoral court did not publish a later figure that the archive "
    "could reach."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "tre-ro")
SYSTEM = key("electoral_system", "plebiscite")


def url(name: str) -> str:
    return BASE + name


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/plebiscites-{day.date}" for day in DAYS)]


def fetch() -> None:
    for day in DAYS:
        for poll in day.polls:
            raw.fetch(SOURCE, url(poll.file), suffix=".pdf")
            if poll.sections:
                raw.fetch(SOURCE, url(poll.sections), suffix=".pdf")


def parse() -> None:
    registry().write()
    for day in DAYS:
        plebiscites(day).write()


def emit() -> None:
    package.run("load", packages())


def text(name: str) -> str:
    path = raw.cached(SOURCE, url(name)).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def number(value: str) -> int:
    return int(value.replace(".", ""))


def figures(poll: Poll) -> dict[str, int]:
    """The totals the electoral court printed for one question in one municipality."""
    body = text(poll.file)
    code = re.search(r"^\s*(\d{5}) - ", body, re.MULTILINE)
    if code is None or code.group(1) != poll.town:
        raise ValueError(f"{poll.file} is not the report of municipality {poll.town}")
    registered = re.search(r"Eleitores Aptos:\s+([\d.]+)", body)
    if registered is None:  # the system's own layout prints the electorate beside the municipality code
        registered = re.search(r"^\s*\d{5} - [^\n]*?\s([\d.]{1,9})\s+\d+\s+\d+\s+\d+\s*$", body, re.MULTILINE)
    pairs = {
        "yes": re.search(r"^\s*\*?\d*\s*-?\s*SIM\s+([\d.]+)", body, re.MULTILINE | re.IGNORECASE),
        "no": re.search(r"^\s*\*?\d*\s*-?\s*N[ÃA]O\s+([\d.]+)", body, re.MULTILINE | re.IGNORECASE),
        "ballots": re.search(r"Total de votos apurados:\s+([\d.]+)", body),
        "valid": re.search(r"Votos v[áa]lidos:\s+([\d.]+)", body),
        "blank": re.search(r"Votos em branco:\s+([\d.]+)", body),
        "invalid": re.search(r"Votos [Nn]ulos:\s+([\d.]+)", body),
    }
    missing = [name for name, hit in {"registered": registered, **pairs}.items() if hit is None]
    if missing or registered is None:
        raise ValueError(f"{poll.file}: cannot read {missing}")
    out = {name: number(hit.group(1)) for name, hit in pairs.items() if hit}
    out["registered"] = number(registered.group(1))
    question = re.search(r"Pergunta:\s+(.+?)(?:\n\s*\n|\n\s*(?:Resposta|Votável|\*?\d\d - ))", body, re.DOTALL)
    poll.question = re.sub(r"\s+", " ", question.group(1)).strip() if question else ""
    if out["yes"] + out["no"] != out["valid"] or out["valid"] + out["blank"] + out["invalid"] != out["ballots"]:
        raise ValueError(f"{poll.file}: the printed totals do not add up: {out}")
    return out


HEAD = re.compile(
    r"Zona:\s+(\d+)\s+Seção:\s+(\d+)\s+Eleitores aptos:\s+(\d+)\s+Comparecimento:\s+(\d+)\s+Faltosos:\s+(\d+)"
)
TAIL = re.compile(
    r"Votos em Branco:\s+(\d+)\s+Votos Nulos:\s+(\d+)\s+Total de Votos:\s+(\d+)\s+Total de Votos V[áa]lidos:\s+(\d+)"
)


class Section(NamedTuple):
    place: str
    n: dict[str, int]


def by_section(poll: Poll) -> list[Section]:
    """Every section of the 2010 poll: zone, number, place, electorate, and the votes."""
    assert poll.sections is not None
    body = text(poll.sections)
    # the page footers and headers sit between sections
    body = re.sub(r"^.*Página \d+ de \d+.*\n", "", body, flags=re.MULTILINE)
    body = re.sub(r"^Plebiscito - Cria.*\n", "", body, flags=re.MULTILINE)
    sections = []
    for chunk in re.split(r"(?=Zona:)", body)[1:]:
        head, tail = HEAD.search(chunk), TAIL.search(chunk)
        place = re.search(r"Local de Vota[^:]*:\s*(.*)", chunk)
        yes = re.search(r"^\s*SIM\s+(\d+)", chunk, re.MULTILINE)
        no = re.search(r"^\s*N[ÃA]O\s+(\d+)", chunk, re.MULTILINE)
        if not (head and tail and place and yes and no):
            raise ValueError(f"cannot read a section: {chunk[:300]!r}")
        zone, number_, registered, turnout, absent = head.groups()
        blank, null, total, valid = tail.groups()
        sections.append(
            Section(
                place.group(1).strip(),
                {
                    "zone": int(zone),
                    "number": int(number_),
                    "registered": int(registered),
                    "ballots": int(turnout),
                    "yes": int(yes.group(1)),
                    "no": int(no.group(1)),
                    "blank": int(blank),
                    "invalid": int(null),
                    "valid": int(valid),
                    "absent": int(absent),
                    "total": int(total),
                },
            )
        )
    expected = re.search(r"Seções processadas:\s+(\d+) de (\d+)", body)
    if expected is None or len(sections) != int(expected.group(1)):
        raise ValueError(f"read {len(sections)} sections, the report counts {expected and expected.group(1)}")
    for sec in sections:
        s = sec.n
        if (
            s["yes"] + s["no"] != s["valid"]
            or s["valid"] + s["blank"] + s["invalid"] != s["ballots"]
            or s["total"] != s["ballots"]
            or s["registered"] - s["absent"] != s["ballots"]
        ):
            raise ValueError(f"section {s['zone']}/{s['number']} does not add up: {s}")
    return sections


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source and electoral system for the Rondônia plebiscites on municipal boundaries",
    )
    p.external |= {BRAZIL, RONDONIA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Regional Eleitoral de Rondônia",
        title="Resultados dos plebiscitos em Rondônia, 2009, 2010 e 2012",
        url=HOME,
        license="Public record of the Electoral Court, reproduced as published",
        attribution="Tribunal Regional Eleitoral de Rondônia",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BRAZIL,
        valid_from=DAYS[0].date,
        valid_to=DAYS[-1].date,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.name(SYSTEM, "Plebiscite on municipal boundaries", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The electors of a municipality vote yes or no on a proposal to create a municipality or to move "
        "territory between municipalities, and the proposal is approved in that municipality when the votes for yes "
        "outnumber the votes for no. Blank and null votes are not valid votes.",
        lang="en",
    )
    return p


def plebiscites(day: Day) -> Package:
    p = Package(
        f"{SOURCE}/plebiscites-{day.date}",
        SOURCE,
        f"{day.name}, by municipality" + (" and section" if any(poll.sections for poll in day.polls) else ""),
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {BRAZIL, RONDONIA, SCHEME, SECTIONS}
    election = key("election", day.date)
    p.add(
        "election",
        id=election,
        polity_id=RONDONIA,
        kind="referendum",
        date_from=day.date,
        date_to=day.date,
        status="held",
    )
    p.name(election, day.name, "en", "official")
    base = f"br/{day.date}-rondonia-plebiscite"
    p.add("slug", path=base, entity_id=election, is_current=True)
    towns: dict[str, str] = {}

    def town_unit(code: str) -> str:
        if code not in towns:
            town = TOWNS[code]
            p.external.add(town.area)
            unit = towns[code] = key("unit", SCHEME, code, day.date)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BRAZIL,
                scheme_code=SCHEME,
                code=code,
                area_relation="equals",
                area_id=town.area,
                kind="standard",
                valid_from=day.date,
                valid_to=day.date,
            )
            p.name(unit, town.name, "pt", "official")
        return towns[code]

    for poll in day.polls:
        town = TOWNS[poll.town]
        p.artifact(raw.cached(SOURCE, url(poll.file)), SOURCE_ID)
        total = figures(poll)
        sections = by_section(poll) if poll.sections else []
        if poll.sections:
            p.artifact(raw.cached(SOURCE, url(poll.sections)), SOURCE_ID)
            for name in ("yes", "no", "blank", "invalid", "valid", "ballots", "registered"):
                got = sum(sec.n[name] for sec in sections)
                if got != total[name]:
                    raise ValueError(
                        f"{poll.file}: sections hold {got} for {name}, the municipality total is {total[name]}"
                    )
        contest = key("contest", day.date, poll.slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            office_id=None,
            body_id=None,
            district_area_id=town.area,
            magnitude=None,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, poll.title, "en", "official")
        if poll.question:
            p.name(contest, poll.question.capitalize() if poll.question.isupper() else poll.question, "pt", "official")
        p.add("slug", path=f"{base}/{poll.slug}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="plebiscite")
        result_set = key("result_set", day.date, poll.slug)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options = {}
        for position, label in enumerate((YES, NO), 1):
            option = options[label] = key("option", day.date, poll.slug, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, label, "pt", "official")
            p.name(option, "Yes" if label == YES else "No", "en", "official", preferred=False)

        def values(unit: str, row: dict[str, int] | dict[str, int | str]) -> None:
            facts = [
                (None, REGISTERED, row["registered"]),
                (None, BALLOTS, row["ballots"]),
                (None, VALID, row["valid"]),
                (None, INVALID, row["invalid"]),
                (None, BLANK, row["blank"]),
                (options[YES], VOTES, row["yes"]),
                (options[NO], VOTES, row["no"]),
            ]
            for option, measure, value in facts:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=int(value),
                )

        unit = town_unit(poll.town)
        values(unit, total)
        for sec in sections:
            s = sec.n
            code = f"{poll.town}:{s['zone']}:{s['number']}"
            section = key("unit", SECTIONS, code, day.date)
            p.add(
                "reporting_unit",
                id=section,
                polity_id=BRAZIL,
                scheme_code=SECTIONS,
                code=code,
                parent_id=unit,
                area_relation="within",
                area_id=town.area,
                kind="standard",
                valid_from=day.date,
                valid_to=day.date,
            )
            p.name(section, f"{town.name}, zona {s['zone']}, seção {s['number']}: {sec.place}", "pt", "official")
            values(section, s)
        winner = YES if total["yes"] > total["no"] else NO
        for label, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    print(f"{p.key}: {len(day.polls)} questions, {len(p.rows['reporting_unit'])} reporting units", flush=True)
    return p
