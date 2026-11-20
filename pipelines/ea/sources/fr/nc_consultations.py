"""New Caledonia's self-determination consultations of 4 November 2018, 4 October 2020, and 12 December 2021.

Voters on the special roll answered one question, "Voulez-vous que la Nouvelle-Calédonie accède à la pleine
souveraineté et devienne indépendante ?". The Haut-commissariat prints the control commission's proclaimed results
by polling station for all three votes in one document. The Interior Ministry's files on data.gouv.fr hold the 2018
and 2020 counts by polling station as it published them.
"""

import re
import subprocess
from datetime import date, timedelta
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "fr.nc_consultations"
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
PAGE = "https://www.nouvelle-caledonie.gouv.fr/Actions-de-l-Etat/Elections/Elections-2021/REFERENDUM-2021/Les-resultats"
RESULTS_URL = (
    "https://www.nouvelle-caledonie.gouv.fr/contenu/telechargement/9258/71413/file/"
    "R%C3%A9sultats%20d%C3%A9finitifs%20R%C3%A9f%C3%A9rendum%20NC%20du%2012%20d%C3%A9cembre%202021.pdf"
)
FILES = {
    date(2018, 11, 4): (
        "https://static.data.gouv.fr/resources/resultats-de-la-consultation-des-electeurs-de-la-nouvelle-caledonie-du-"
        "4-novembre-2018/20181108-150405/referendum-nc-bvt.xls"
    ),
    date(2020, 10, 4): (
        "https://static.data.gouv.fr/resources/consultation-des-electeurs-de-la-nouvelle-caledonie-du-4-octobre-2020/"
        "20201015-165701/consultation-nlle-caledonie-2020-resultats-par-bv.ods"
    ),
}
DAYS = (date(2018, 11, 4), date(2020, 10, 4), date(2021, 12, 12))
TITLES = {
    date(2018, 11, 4): "4 novembre 2018",
    date(2020, 10, 4): "4 octobre 2020",
    date(2021, 12, 12): "12 décembre 2021",
}
ORDINAL = {date(2018, 11, 4): "first", date(2020, 10, 4): "second", date(2021, 12, 12): "third"}
RANK = {date(2018, 11, 4): 1, date(2020, 10, 4): 2, date(2021, 12, 12): 3}
QUESTION = "Voulez-vous que la Nouvelle-Calédonie accède à la pleine souveraineté et devienne indépendante ?"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

# The fr.commune areas that the municipal pipeline loaded, by INSEE code.
COMMUNES = {
    "98801": ("01a13b8a-b236-7126-be61-86cf0a280dec", "Belep"),
    "98802": ("01a13b8a-b236-7126-be61-86d0ddba4f3e", "Bouloupari"),
    "98803": ("01a13b8a-b236-7126-be61-86d16b850ae1", "Bourail"),
    "98804": ("01a13b8a-b236-7126-be61-86d2ff559b38", "Canala"),
    "98805": ("01a13b8a-b236-7126-be61-86d3bd484faf", "Dumbéa"),
    "98806": ("01a13b8a-b236-7126-be61-86d42c60f9fd", "Farino"),
    "98807": ("01a13b8a-b236-7126-be61-86d51176befc", "Hienghène"),
    "98808": ("01a13b8a-b236-7126-be61-86d6254c7343", "Houaïlou"),
    "98809": ("01a13b8a-b236-7126-be61-86d74955fc6f", "L'Ile-des-Pins"),
    "98810": ("01a13b8a-b236-7126-be61-86d8240deb3a", "Kaala-Gomen"),
    "98811": ("01a13b8a-b236-7126-be61-86d947a318a5", "Koné"),
    "98812": ("01a13b8a-b236-7126-be61-86dae8e9a8e7", "Koumac"),
    "98813": ("01a13b8a-b236-7126-be61-86dbaa4695ba", "La Foa"),
    "98814": ("01a13b8a-b236-7126-be61-86dcea5054a7", "Lifou"),
    "98815": ("01a13b8a-b236-7126-be61-86dd07be0592", "Maré"),
    "98816": ("01a13b8a-b236-7126-be61-86de46dfd5f9", "Moindou"),
    "98817": ("01a13b8a-b236-7126-be61-86df4aacb0e5", "Le Mont-Dore"),
    "98818": ("01a13b8a-b236-7126-be61-86e0f0f4a436", "Nouméa"),
    "98819": ("01a13b8a-b236-7126-be61-86e13522ca24", "Ouégoa"),
    "98820": ("01a13b8a-b236-7126-be61-86e2151a4f1f", "Ouvéa"),
    "98821": ("01a13b8a-b236-7126-be61-86e3bd58a07b", "Païta"),
    "98822": ("01a13b8a-b236-7126-be61-86e4914608cb", "Poindimié"),
    "98823": ("01a13b8a-b236-7126-be61-86e52d885a3d", "Ponérihouen"),
    "98824": ("01a13b8a-b236-7126-be61-86e6cd42dee1", "Pouébo"),
    "98825": ("01a13b8a-b236-7126-be61-86e717d54167", "Pouembout"),
    "98826": ("01a13b8a-b236-7126-be61-86e8c038dfc3", "Poum"),
    "98827": ("01a13b8a-b236-7126-be61-86e99570b190", "Poya"),
    "98828": ("01a13b8a-b236-7126-be61-86ea47911777", "Sarraméa"),
    "98829": ("01a13b8a-b236-7126-be61-86eb9ade583a", "Thio"),
    "98830": ("01a13b8a-b236-7126-be61-86ec2b03faac", "Touho"),
    "98831": ("01a13b8a-b236-7126-be61-86ed053c31c2", "Voh"),
    "98832": ("01a13b8a-b236-7126-be61-86eee02ec0a2", "Yaté"),
    "98833": ("01a13b8a-b236-7126-be61-86efdfbd67bb", "Kouaoua"),
}
ALIASES = {
    "boulouparis": "98802",
    "ile des pins": "98809",
    "mont-dore": "98817",
    "poya nord": "98827",
    "poya sud": "98827",
}


def fold(text: str) -> str:
    plain = re.sub(r"[’'`]", " ", text.lower())
    for letters, replacement in (("àâä", "a"), ("éèêë", "e"), ("îï", "i"), ("ôö", "o"), ("ùûü", "u"), ("ç", "c")):
        plain = re.sub(f"[{letters}]", replacement, plain)
    return re.sub(r"[\s-]+", " ", plain).strip()


NAMES = {fold(name): code for code, (_, name) in COMMUNES.items()} | {
    fold(name.removeprefix("Le ").removeprefix("L'")): code for code, (_, name) in COMMUNES.items()
}
NAMES |= {fold(alias): code for alias, code in ALIASES.items()}


def commune_code(name: str) -> str:
    try:
        return NAMES[fold(name)]
    except KeyError:
        raise ValueError(f"unknown commune {name!r}") from None


class Station(NamedTuple):
    commune: str
    number: int
    name: str
    registered: int
    ballots: int
    invalid: int
    blank: int
    valid: int
    yes: int
    no: int


class Total(NamedTuple):
    registered: int
    ballots: int
    invalid: int
    blank: int
    valid: int
    yes: int
    no: int


PROVINCE = re.compile(r"^(Province (?:des Iles|Nord|Sud))\s+")
TITLE = re.compile(r"R[ée]f[ée]rendum du (\d{1,2}) (\w+) (\d{4})")
MONTHS = {"novembre": 11, "octobre": 10, "décembre": 12}


def figure(token: str) -> int:
    token = token.strip()
    return 0 if token == "-" else int(re.sub(r"\s", "", token))


def figures(tokens: list[str]) -> tuple[int, ...]:
    """Inscrits, Votants, Abst., Nuls, Blancs, Exprimés, OUI, and NON from a row's fifteen figure columns."""
    return tuple(figure(tokens[index]) for index in (0, 1, 3, 5, 7, 9, 11, 13))


def check_row(where: str, values: tuple[int, ...]) -> None:
    registered, ballots, abstained, invalid, blank, valid, yes, no = values
    if registered != ballots + abstained or ballots != invalid + blank + valid or valid != yes + no:
        raise ValueError(f"{where}: the row's figures do not add up: {values}")


def pdf_pages(artifact: raw.Artifact) -> list[list[str]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, text=True, check=True
    ).stdout
    return [page.splitlines() for page in text.split("\f")]


def read_pdf(
    artifact: raw.Artifact,
) -> tuple[dict[date, Total], dict[date, list[Station]], dict[date, dict[str, Total]]]:
    """The proclaimed national totals, polling stations, and communes of each vote in the Haut-commissariat's file."""
    totals: dict[date, Total] = {}
    stations: dict[date, list[Station]] = {}
    communes: dict[date, dict[str, Total]] = {}
    day: date | None = None
    section = ""
    pending: list[str] | None = None
    for page in pdf_pages(artifact):
        for line in page:
            found = TITLE.search(line)
            if found is not None and "proclamés" not in line and "proclam" not in line:
                day = date(int(found[3]), MONTHS[found[2]], int(found[1]))
                section = "national"
                pending = None
                continue
            if "Communes de Nouvelle-Calédonie" in line:
                section = "communes"
                continue
            if "Évolution des résultats" in line:
                day, section = None, ""
                continue
            if "Bureaux de vote de Nouvelle-Calédonie" in line:
                section = "stations"
                continue
            if day is None or not line.strip():
                continue
            if section == "national" and line.lstrip().startswith("Nouvelle-Calédonie") and day not in totals:
                tokens = re.split(r"\s{2,}", line.strip())
                if len(tokens) == 16:
                    values = figures(tokens[1:])
                    check_row(f"{day} total", values)
                    registered, ballots, _, invalid, blank, valid, yes, no = values
                    totals[day] = Total(registered, ballots, invalid, blank, valid, yes, no)
                continue
            if section == "communes" and PROVINCE.match(line):
                tokens = split(line)
                if len(tokens) != 17:
                    raise ValueError(f"{day} commune row not understood: {line!r}")
                name, rest = tokens[1], tokens[2:]
                values = figures(rest)
                check_row(f"{day} {name}", values)
                registered, ballots, _, invalid, blank, valid, yes, no = values
                code = commune_code(name)
                previous = communes.setdefault(day, {}).get(code)
                total = Total(registered, ballots, invalid, blank, valid, yes, no)
                communes[day][code] = (
                    total if previous is None else Total(*(a + b for a, b in zip(previous, total, strict=True)))
                )
                continue
            if section != "stations":
                continue
            tokens = split(line)
            if pending is not None:
                tokens, pending = pending + tokens, None
            elif not PROVINCE.match(line):
                continue
            elif len(tokens) == 4:  # the name wrapped, and the figures follow on the next line
                pending = tokens
                continue
            stations.setdefault(day, []).append(station(day, tokens))
    return totals, stations, communes


def split(line: str) -> list[str]:
    tokens = re.split(r"\s{2,}", line.strip())
    province = PROVINCE.match(tokens[0])
    if province is not None and province.end() < len(tokens[0]):  # "Province des Iles" and a commune share one gap
        tokens = [province[1], tokens[0][province.end() :], *tokens[1:]]
    return tokens


def station(day: date, tokens: list[str]) -> Station:
    commune, number, name, *rest = tokens[1:]
    where = f"{day} {commune} {number}"
    if rest and not re.fullmatch(r"[\d ]+|-", rest[0]):  # the name's last words wrapped under the figures
        suffix, rest = rest[0], rest[1:]
    else:
        suffix = ""
    if len(rest) == 14:  # the name ran into the first figure, or a wrapped name's last digit into it
        target = figure(rest[0]) + figure(rest[2])
        for at in range(len(name)):
            if re.fullmatch(r"\d{1,3}(?: \d{3})+|\d+", name[at:]) and figure(name[at:]) == target:
                name, rest = name[:at], [name[at:], *rest]
                break
        else:
            raise ValueError(f"{where}: row not understood: {tokens}")
    name = f"{name} {suffix}"
    if len(rest) != 15:
        raise ValueError(f"{where}: row not understood: {tokens}")
    values = figures(rest)
    registered, ballots, abstained, *_ = values
    if registered != ballots + abstained:  # a wrapped name's last digit sits in front of the roll
        digits = re.sub(r"\s", "", rest[0])
        fixed = ballots + abstained
        if digits == f"{fixed}{digits[-1]}":
            name, values = f"{name} {digits[-1]}", (fixed, *values[1:])
    check_row(where, values)
    registered, ballots, _, invalid, blank, valid, yes, no = values
    name = re.sub(r"\s+", " ", name).strip()
    return Station(commune_code(commune), int(number), name, registered, ballots, invalid, blank, valid, yes, no)


def read_ministry(day: date) -> tuple[Total, list[Station]]:
    """The Interior Ministry's file of a day's polling stations and its total row, as published on data.gouv.fr."""
    frame = pl.read_excel(raw.cached(SOURCE, FILES[day]).path, engine="calamine", has_header=False)
    if day.year == 2018:
        first, commune, number, name, columns, total = 6, 0, 1, 2, range(3, 11), frame.row(3)
    else:
        first, commune, number, name, columns, total = 3, 1, 2, 3, (4, 5, 7, 9, 11, 13, 15, 17), frame.row(-1)
    stations = []
    for row in frame.slice(first).iter_rows():
        if row[number] is None or row[commune] == "TOTAL":
            continue
        values = tuple(figure(row[index]) for index in columns)
        check_row(f"{day} {row[commune]} {row[number]}", values)
        registered, ballots, _, invalid, blank, valid, yes, no = values
        stations.append(
            Station(
                commune_code(row[commune]),
                int(str(row[number]).replace("N°", "")),
                re.sub(r"\s+", " ", row[name]).strip(),
                registered,
                ballots,
                invalid,
                blank,
                valid,
                yes,
                no,
            )
        )
    values = tuple(figure(total[index]) for index in columns)
    check_row(f"{day} Ministry total", values)
    registered, ballots, _, invalid, blank, valid, yes, no = values
    return Total(registered, ballots, invalid, blank, valid, yes, no), stations


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


HAUTCOM_SOURCE = key("source", "haut-commissariat")
MINISTRY_SOURCE = key("source", "interieur")
SYSTEM = key("electoral_system", "consultation")
HAUTCOM_NOTE = (
    "Results by polling station as the control commission for the consultation proclaimed them, printed by the "
    "Haut-commissariat de la République en Nouvelle-Calédonie. Registered voters are those on the special roll for "
    "the consultation (liste électorale spéciale pour la consultation). Invalid ballots are the ballots the source "
    "counts as null (nuls) and blank ballots are counted apart; Yes and No add up to the valid votes (exprimés). "
    "The station sums equal the proclaimed totals and each commune's printed row."
)
MINISTRY_NOTE = (
    "Results by polling station from the Interior Ministry's file on data.gouv.fr, published on 8 November 2018. "
    "Its totals differ from the control commission's proclaimed ones: it counts 830 more registered voters "
    "(174,995 against 174,165), 9 more null and 9 fewer blank ballots, and the registered voters, null, or blank "
    "counts of 29 stations differ. Votes for Yes and No equal the proclaimed ones in every station."
)
SYSTEM_NOTE = (
    "One question, answered Yes or No, put to the electors of the special roll for the consultation, which the Nouméa "
    "Accord of 1998 and the organic law of 19 March 1999 reserve to people with long ties to New Caledonia. The "
    'question was "Voulez-vous que la Nouvelle-Calédonie accède à la pleine souveraineté et devienne indépendante ?". '
    "A majority of valid votes decides, with no turnout quorum. The control commission, chaired by a judge of the "
    "Conseil d'État, oversees the vote and proclaims the result."
)


def packages() -> list[str]:
    return ["fr.nc_consultations/registry", *(f"fr.nc_consultations/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    totals, stations, communes = read_pdf(raw.cached(SOURCE, RESULTS_URL))
    for day in DAYS:
        consultation(day, totals[day], stations[day], communes[day]).write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS_URL, suffix=".pdf")
    for day, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=".xls" if day.year == 2018 else ".ods")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The sources and the electoral system of New Caledonia's self-determination consultations",
    )
    p.external.add(FRANCE)
    p.add(
        "source",
        id=HAUTCOM_SOURCE,
        kind="official_body",
        publisher="Haut-commissariat de la République en Nouvelle-Calédonie",
        title="Référendum 2021 - Résultats définitifs, with the results of the 2018 and 2020 consultations",
        url=PAGE,
        license="Licence Ouverte v2.0",
        license_url="https://github.com/etalab/licence-ouverte/blob/master/LO.md",
        attribution="Haut-commissariat de la République en Nouvelle-Calédonie",
    )
    p.add(
        "source",
        id=MINISTRY_SOURCE,
        kind="official_body",
        publisher="Ministère de l'Intérieur",
        title="Consultations des électeurs de la Nouvelle-Calédonie, 2018 and 2020",
        url="https://www.data.gouv.fr/datasets/resultats-de-la-consultation-des-electeurs-de-la-nouvelle-caledonie-du-4-novembre-2018",
        license="Licence Ouverte v1.0 (2018 file); none stated on data.gouv.fr (2020 file)",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence",
        attribution="Ministère de l'Intérieur",
    )
    p.artifact(raw.cached(SOURCE, RESULTS_URL), HAUTCOM_SOURCE)
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), MINISTRY_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=FRANCE,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.name(SYSTEM, "New Caledonia self-determination consultation", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def consultation(day: date, total: Total, stations: list[Station], communes: dict[str, Total]) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"New Caledonia's {ORDINAL[day]} self-determination consultation, {day:%-d %B %Y}, by polling station",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(FRANCE)
    p.uses(raw.cached(SOURCE, RESULTS_URL))
    ministry = day in FILES
    if ministry:
        p.uses(raw.cached(SOURCE, FILES[day]))
    election = p.id("election", day)
    p.add("election", id=election, polity_id=FRANCE, kind="referendum", date_from=day, date_to=day, status="held")
    p.name(election, f"{day.year} New Caledonia independence referendum", "en", "official")
    p.name(election, f"Consultation du {TITLES[day]} sur l'accession à la pleine souveraineté", "fr", "native", False)
    path = f"fr/{day}-new-caledonia-referendum"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = p.id("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, "Should New Caledonia attain full sovereignty and become independent?", "en", "official")
    p.name(contest, QUESTION, "fr", "native", False)
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    options = {}
    for position, (answer, english, label) in enumerate((("yes", "Yes", "Oui"), ("no", "No", "Non")), 1):
        options[answer] = p.id("option", day, answer)
        p.add(
            "contest_option",
            id=options[answer],
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
    p.external.update(area for area, _ in COMMUNES.values())
    p.external.add("fr.bureau")
    units = {}
    for station in stations:
        code = f"{station.commune}:{station.number:04d}"
        units[(station.commune, station.number)] = unit = p.id("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=FRANCE,
            scheme_code="fr.bureau",
            code=code,
            area_relation="within",
            area_id=COMMUNES[station.commune][0],
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(1),
        )
        p.name(unit, station.name, "fr", "official", False)
    sets = [("proclaimed", True, HAUTCOM_SOURCE, HAUTCOM_NOTE, total, stations)]
    if ministry:
        sets.append(("ministry", False, MINISTRY_SOURCE, MINISTRY_NOTE, *read_ministry(day)))
    for name, default, source, note, declared, rows in sets:
        if not default and {(x.commune, x.number) for x in rows} != set(units):
            raise ValueError(f"{day}: the Ministry's polling stations differ from the proclaimed ones")
        if not default and rows == stations:
            continue
        result_set = p.id("result_set", day, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=default,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        winner = "yes" if declared.yes > declared.no else "no"
        for answer, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if answer == winner else "lost")
        for measure, value in (
            (REGISTERED, declared.registered),
            (BALLOTS, declared.ballots),
            (INVALID, declared.invalid),
            (BLANK, declared.blank),
            (VALID, declared.valid),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        p.add("declared", result_set_id=result_set, option_id=options["yes"], measure=VOTES, value=declared.yes)
        p.add("declared", result_set_id=result_set, option_id=options["no"], measure=VOTES, value=declared.no)
        for station in rows:
            unit = units[(station.commune, station.number)]
            for option, measure, value in (
                (None, REGISTERED, station.registered),
                (None, BALLOTS, station.ballots),
                (None, INVALID, station.invalid),
                (None, BLANK, station.blank),
                (None, VALID, station.valid),
                (options["yes"], VOTES, station.yes),
                (options["no"], VOTES, station.no),
            ):
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
    return p
