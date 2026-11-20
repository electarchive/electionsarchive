"""São Paulo mayoral elections of 1982, 1985, 1988, and 1992, by municipality, from Fundação SEADE's electoral data.

Fundação SEADE, the São Paulo state statistics agency, published the votes of every candidate for mayor in each of the
state's municipalities from 1982 on. Its site no longer serves them, so the archive reads a compiled copy of that
product: one CSV of every candidate's votes, party, and result, which a researcher scraped from SEADE and published
with each municipality's electoral-court and IBGE codes. The copy is pinned to one commit.

Each year builds one package, with one election per municipality under a state-wide election, and one contest for
mayor per round. The 1992 runoffs are second-round elections under each municipality's first round. The 1982 contests
were decided under sublegendas: a party could run several candidates, their votes pooled, and the most-voted candidate
of the leading party won. Those contests carry one list option per party with its candidates under it.

The source lists candidate votes only, so the result sets carry no registered voters, turnout, or blank and null
votes. The council results in the same copy list only the elected councilors, so they are left out.

The archive already holds Brazil, São Paulo, every municipality with its mayor's office, and the municipal areas;
the packages refer to them by id. Parties and people are keyed within this source.
"""

import csv
import functools
import io
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "br.seade"
COMMIT = "ed94ed9cfe0f13e5b190aa6f059da2ef8b203668"
REPOSITORY = "https://github.com/marcofaga/scrap_eleicoes_seade"
URL = (
    f"https://raw.githubusercontent.com/marcofaga/scrap_eleicoes_seade/{COMMIT}"
    "/crawler_seade/crawler_seade/arquivos_finais/dados_captados_prefeito.csv"
)
SEADE = "http://produtos.seade.gov.br/produtos/moveleitoral/index.php"
# The archive's own rows for Brazil and the state of São Paulo
BRAZIL = "01a13ba1-cd2c-7065-8397-2903884f736e"
SAO_PAULO = "01a13ba1-cd85-748e-9cef-84f02dee35ac"
SCHEME = "br.municipality"
VOTES = 1
DAYS = {
    1982: (date(1982, 11, 15),),
    1985: (date(1985, 11, 15),),
    1988: (date(1988, 11, 15),),
    1992: (date(1992, 10, 3), date(1992, 11, 15)),
}
PARTIES = {
    "PCB": "Partido Comunista Brasileiro",
    "PC DO B": "Partido Comunista do Brasil",
    "PDC": "Partido Democrata Cristão",
    "PDS": "Partido Democrático Social",
    "PDT": "Partido Democrático Trabalhista",
    "PFL": "Partido da Frente Liberal",
    "PL": "Partido Liberal",
    "PMDB": "Partido do Movimento Democrático Brasileiro",
    "PMN": "Partido da Mobilização Nacional",
    "PRN": "Partido da Reconstrução Nacional",
    "PSB": "Partido Socialista Brasileiro",
    "PSC": "Partido Social Cristão",
    "PSDB": "Partido da Social Democracia Brasileira",
    "PT": "Partido dos Trabalhadores",
    "PTB": "Partido Trabalhista Brasileiro",
    "PV": "Partido Verde",
}
ABBREVIATIONS = {"PC DO B": "PCdoB", "PT DO B": "PTdoB"}
SOURCE_NOTE = (
    "Fundação SEADE published each candidate's votes for mayor in every São Paulo municipality from 1982. Its site no "
    "longer serves the tables, and the archive reads a copy compiled from them in 2018 and published at "
    f"{REPOSITORY}, pinned to commit {COMMIT[:12]}. The copy repeats the 1985 rows, which the archive reads once, "
    "and marks a few 1982 and 1988 candidates as passing to a second round, which those elections did not have; they "
    "are stored as not elected."
)
METHOD = (
    "Votes for each candidate for mayor in the municipality, as Fundação SEADE published them. The source gives no "
    "registered voters, turnout, or blank and null votes, so the result set holds only the candidates' votes."
)
SUBLEGENDA_CAVEAT = (
    "Under the 1982 sublegenda rules, the votes of a party's candidates and its legend votes counted for the party, "
    "and the most-voted candidate of the leading party won. The source lists the candidates' votes only, without the "
    "legend votes, so in some municipalities the declared winner differs from this reading of the listed votes; the "
    "winner is stored as declared."
)
SYSTEMS = {
    "sublegenda": (
        {"formula": "winner_take_all"},
        "Brazilian mayoral sublegenda system, 1982",
        (
            "Each party could run up to three candidates for mayor in sublegendas. Their votes and the party's legend "
            "votes counted for the party, the party with the most votes won, and the office went to its most-voted "
            "candidate. Blank votes counted as valid."
        ),
        date(1982, 11, 15),
        date(1982, 11, 15),
    ),
    "plurality": (
        {"formula": "fptp"},
        "Brazilian mayoral plurality system, 1985–1992",
        (
            "The candidate for mayor with the most votes wins in a single round. From 1992, municipalities with more than "
            "200,000 registered voters held a second round when no candidate won more than half of the valid votes; the "
            "archive records those that held one under the two-round system, and those whose mayor won a first-round "
            "majority under this one, which gives the same winner."
        ),
        date(1985, 11, 15),
        date(1992, 10, 3),
    ),
    "two-round": (
        {"formula": "two_round"},
        "Brazilian mayoral two-round system, 1992",
        (
            "In municipalities with more than 200,000 registered voters, a candidate for mayor wins in the first round "
            "with more than half of the valid votes, not counting blank and null votes. Otherwise the two leading "
            "candidates meet in a second round, and the one with more valid votes wins."
        ),
        date(1992, 10, 3),
        date(1992, 11, 15),
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def municipal(scheme: str, code: str) -> str:
    """The id another Brazilian pipeline keyed by the electoral court's municipality code."""
    return str(uuid5(NAMESPACE, f"external_id:{scheme}\x1f{code}"))


SOURCE_ID = key("source", "seade")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(character if character.isalnum() else " " for character in folded).split())


PARTICLES = {"da", "das", "de", "do", "dos", "e"}


def proper(text: str) -> str:
    words = text.strip().lower().split()
    return " ".join(
        word if index and word in PARTICLES else "'".join(part[:1].upper() + part[1:] for part in word.split("'"))
        for index, word in enumerate(words)
    )


def party_label(abbreviation: str) -> str:
    return ABBREVIATIONS.get(abbreviation, abbreviation)


@dataclass(frozen=True)
class Row:
    year: int
    round: int
    town: str
    code: str
    name: str
    party: str
    votes: int
    status: str


@functools.cache
def areas() -> dict[str, str]:
    """The archive's area for each municipality, by the electoral court's code."""
    with (Path(__file__).parent / "seade_areas.tsv").open(encoding="utf-8") as handle:
        return {row["tse_code"]: row["area_id"] for row in csv.DictReader(handle, delimiter="\t")}


def rows() -> list[Row]:
    """Every candidate row before 1996, read once: the copy repeats each 1985 row."""
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf-8")
    seen: set[tuple[str, ...]] = set()
    out = []
    for record in csv.DictReader(io.StringIO(text)):
        year = int(record["eleicao"])
        if year not in DAYS or record["cargo"] != "PREFEITO":
            continue
        signature = tuple(record.values())
        if signature in seen:
            continue
        seen.add(signature)
        out.append(
            Row(
                year,
                int(record["turno"]),
                record["municipio"].strip(),
                record["tse_cod"].strip(),
                " ".join(record["candidato"].split()),
                " ".join(record["partido"].split()),
                int(record["votosnom"] or 0),
                record["situacao"].strip(),
            )
        )
    return out


def outcome(row: Row) -> str | None:
    if row.status in ("ELEITO", "ELEITO 2 TURNO"):
        return "elected"
    if row.status == "PASSOU AO 2 TURNO" and row.year == 1992:
        return "advanced"
    return None


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/mayors-{year}" for year in DAYS)]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def parse() -> None:
    data = rows()
    registry(data).write()
    for year in DAYS:
        mayors(year, [row for row in data if row.year == year]).write()


def emit() -> None:
    package.run("load", packages())


def person(row: Row) -> str:
    return key("person", row.code, slug(row.name))


def registry(data: list[Row]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source, electoral systems, parties, and candidates for São Paulo's mayoral elections, 1982–1992",
    )
    p.external.add(BRAZIL)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="academic",
        publisher="Fundação Sistema Estadual de Análise de Dados (SEADE)",
        title="Eleições municipais paulistas, 1982–1996: votação dos candidatos a prefeito por município",
        url=SEADE,
        license="Public statistics of the São Paulo state government; the compiled copy is under the MIT License",
        license_url=f"{REPOSITORY}/blob/{COMMIT}/LICENSE",
        attribution="Fundação SEADE; compiled by Marco Antonio Faganello",
        notes=SOURCE_NOTE,
    )
    p.artifact(raw.cached(SOURCE, URL), SOURCE_ID)
    for name, (rules, label, body, start, end) in SYSTEMS.items():
        system = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=system,
            polity_id=BRAZIL,
            valid_from=start,
            valid_to=end,
            rules=rules,
            rules_version=1,
        )
        p.name(system, label, "en", "official")
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=body, lang="en")
    for abbreviation in sorted({row.party for row in data}):
        party = key("party", abbreviation)
        p.add("party", id=party, kind="party", polity_id=BRAZIL)
        p.name(party, PARTIES.get(abbreviation, party_label(abbreviation)), "pt", "official")
        p.name(party, party_label(abbreviation), "pt", "abbreviation", preferred=False)
    people: dict[str, str] = {}
    for row in data:
        people.setdefault(person(row), row.name)
    for id, name in sorted(people.items()):
        p.add("person", id=id)
        p.name(id, proper(name), "pt", "official")
    print(f"{p.key}: {len(people)} candidates, {len({row.party for row in data})} parties", flush=True)
    return p


def mayors(year: int, data: list[Row]) -> Package:
    days = DAYS[year]
    p = Package(
        f"{SOURCE}/mayors-{year}",
        SOURCE,
        f"The {year} mayoral elections in São Paulo's municipalities, by municipality",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {BRAZIL, SAO_PAULO, SCHEME}
    p.uses(raw.cached(SOURCE, URL))
    sublegenda = year == 1982
    umbrella = key("election", year)
    p.add(
        "election",
        id=umbrella,
        polity_id=SAO_PAULO,
        kind="local",
        date_from=days[0],
        date_to=days[-1],
        status="held",
    )
    p.name(umbrella, f"{year} São Paulo municipal elections", "en", "official")
    p.name(umbrella, f"Eleições municipais de {year} em São Paulo", "pt", "native")
    p.add("slug", path=f"br/sp/{days[0]}-local", entity_id=umbrella, is_current=True)

    contests: dict[tuple[str, int], list[Row]] = defaultdict(list)
    for row in data:
        contests[(row.code, row.round)].append(row)
    towns = {row.code: row.town for row in data}
    runoffs = {code for code, round in contests if round == 2}
    for code in sorted(towns):
        town, area = proper(towns[code]), areas()[code]
        polity = municipal("br.municipais.polity", code)
        p.external |= {area, polity, municipal("br.municipais.office", code)}
        unit = key("unit", code, year)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BRAZIL,
            scheme_code=SCHEME,
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=days[0],
            valid_to=days[-1] + timedelta(days=1),
        )
        p.name(unit, town, "pt", "official")
        prefix = f"br/sp/{slug(towns[code])}"
        for round in (1, 2) if code in runoffs else (1,):
            day = days[round - 1]
            election = key("election", day, code)
            p.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=umbrella if round == 1 else key("election", days[0], code),
                kind="local" if round == 1 else "runoff",
                date_from=day,
                date_to=day,
                status="held",
            )
            stage = "local" if round == 1 else "runoff"
            p.name(
                election,
                f"{year} {town} municipal election" + (", second round" if round == 2 else ""),
                "en",
                "official",
            )
            p.add("slug", path=f"{prefix}/{day}-{stage}", entity_id=election, is_current=True)
            contest(p, year, day, code, round, contests[(code, round)], election, unit, prefix, sublegenda, runoffs)
    count = sum(len(v) for v in contests.values())
    print(f"{p.key}: {len(towns)} municipalities, {len(contests)} contests, {count} candidates", flush=True)
    return p


def contest(
    p: Package,
    year: int,
    day: date,
    code: str,
    round: int,
    standing: list[Row],
    election: str,
    unit: str,
    prefix: str,
    sublegenda: bool,
    runoffs: set[str],
) -> None:
    town = proper(standing[0].town)
    id = key("contest", day, code)
    two_round = code in runoffs
    system = "sublegenda" if sublegenda else "two-round" if two_round else "plurality"
    p.add(
        "contest",
        id=id,
        election_id=election,
        kind="office",
        office_id=municipal("br.municipais.office", code),
        body_id=None,
        district_area_id=areas()[code],
        magnitude=1,
        round_number=round,
        previous_round_id=key("contest", DAYS[year][0], code) if round == 2 else None,
        electoral_system_id=key("electoral_system", system),
        default_result_kind="counted",
    )
    suffix = f", {'second' if round == 2 else 'first'} round" if two_round else ""
    p.name(id, f"{year} {town} mayoral election{suffix}", "en", "official")
    p.add("slug", path=f"{prefix}/{day}-{'local' if round == 1 else 'runoff'}/mayor", entity_id=id, is_current=True)
    if sublegenda:
        p.add("note", id=key("note", id), entity_id=id, kind="caveat", body_md=SUBLEGENDA_CAVEAT, lang="en")
    result_set = key("result_set", day, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    lists: dict[str, str] = {}
    if sublegenda:
        for position, abbreviation in enumerate(sorted({row.party for row in standing}), 1):
            option = lists[abbreviation] = key("option", day, code, "list", abbreviation)
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="list",
                party_id=key("party", abbreviation),
                ballot_label=party_label(abbreviation),
            )
    names = Counter(row.name for row in standing)
    used: set[str] = set()
    won: Counter[str] = Counter()
    for row in sorted(standing, key=lambda r: (-r.votes, r.name)):
        suffix = slug(row.name) if names[row.name] == 1 else f"{slug(row.name)}:{slug(row.party)}"
        while suffix in used:
            suffix += ":2"
        used.add(suffix)
        option = key("option", day, code, suffix)
        party = key("party", row.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=id,
            kind="candidate",
            parent_option_id=lists.get(row.party),
            party_id=party,
            ballot_label=proper(row.name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", day, code, suffix),
            person_id=person(row),
            option_id=option,
            role="list_member" if sublegenda else "head",
            ballot_name=row.name,
            member_of_party_id=party,
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=row.votes,
        )
        kind = outcome(row)
        if kind is not None:
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            if sublegenda and kind == "elected":
                won[row.party] += 1
    for abbreviation, seats in won.items():
        p.add("outcome", result_set_id=result_set, option_id=lists[abbreviation], kind="won", seats=seats)
