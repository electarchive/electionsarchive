import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "dz.joradp"
ALGERIA = str(uuid5(package.NAMESPACE, "polity\x1fDZ"))
SITE = "https://www.joradp.dz"
REGISTERED, CAST, VALID, INVALID, VOTES = 2, 3, 4, 5, 1

# The Journal officiel's French edition, by issue, with the page where the proclamation's table or figures begin.
ISSUES = {
    1995: ("1995", "F1995072", "No. 72 of 26 November 1995", 4),
    1999: ("1999", "F1999029", "No. 29 of 21 April 1999", 3),
    2004: ("2004", "F2004024", "No. 24 of 25 April 2004", 8),
    2009: ("2009", "F2009022", "No. 22 of 15 April 2009", 7),
    2014: ("2014", "F2014023", "No. 23 of 23 April 2014", 6),
    2019: ("2019", "F2019078", "No. 78 of 18 December 2019", 17),
    2024: ("2024", "F2024063", "No. 63 of 15 September 2024", 8),
}
DAYS = {
    1995: date(1995, 11, 16),
    1999: date(1999, 4, 15),
    2004: date(2004, 4, 8),
    2009: date(2009, 4, 9),
    2014: date(2014, 4, 17),
    2019: date(2019, 12, 12),
    2024: date(2024, 9, 7),
}
# Candidates in the order of the printed columns, with the vote totals the proclamation prints.
CANDIDATES = {
    1995: ["Nouredine Boukrouh", "Liamine Zeroual", "Saïd Sadi", "Mahfoud Nahnah"],
    1999: [
        "Mohand Oulhadj Aït Ahmed",
        "Abdelaziz Bouteflika",
        "Mouloud Hamrouche",
        "Youcef Khatib",
        "Abdallah Djaballah",
        "Mokdad Sifi",
        "Ahmed Taleb Ibrahimi",
    ],
    2004: [
        "Abdelaziz Bouteflika",
        "Ali Benflis",
        "Abdallah Djaballah",
        "Saïd Sadi",
        "Louisa Hanoune",
        "Ali Fawzi Rebaine",
    ],
    2009: [
        "Abdelaziz Bouteflika",
        "Louisa Hanoune",
        "Moussa Touati",
        "Mohammed Djahid Younsi",
        "Mohand Oussaïd Belaïd",
        "Ali Fawzi Rebaine",
    ],
    2014: [
        "Abdelaziz Bouteflika",
        "Ali Benflis",
        "Abdelaziz Belaïd",
        "Louisa Hanoune",
        "Ali Fawzi Rebaine",
        "Moussa Touati",
    ],
    2019: ["Abdelmadjid Tebboune", "Abdelkader Bengrina", "Ali Benflis", "Azzeddine Mihoubi", "Abdelaziz Belaïd"],
    2024: ["Abdelmadjid Tebboune", "Abdelaali Hassani Cherif", "Youcef Aouchiche"],
}
# National figures of the two elections whose proclamation prints no table by wilaya: registered, voters, valid, votes.
NATIONAL = {
    1995: (15_969_904, 12_087_281, 11_619_532, [443_144, 7_088_618, 1_115_796, 2_971_974]),
    1999: (17_488_759, 10_652_623, 10_093_611, [321_179, 7_445_045, 314_160, 121_414, 400_080, 226_139, 1_265_594]),
}
WILAYAS = {
    "01": "Adrar",
    "02": "Chlef",
    "03": "Laghouat",
    "04": "Oum El Bouaghi",
    "05": "Batna",
    "06": "Béjaïa",
    "07": "Biskra",
    "08": "Béchar",
    "09": "Blida",
    "10": "Bouira",
    "11": "Tamenghasset",
    "12": "Tébessa",
    "13": "Tlemcen",
    "14": "Tiaret",
    "15": "Tizi Ouzou",
    "16": "Alger",
    "17": "Djelfa",
    "18": "Jijel",
    "19": "Sétif",
    "20": "Saïda",
    "21": "Skikda",
    "22": "Sidi Bel Abbès",
    "23": "Annaba",
    "24": "Guelma",
    "25": "Constantine",
    "26": "Médéa",
    "27": "Mostaganem",
    "28": "M'Sila",
    "29": "Mascara",
    "30": "Ouargla",
    "31": "Oran",
    "32": "El Bayadh",
    "33": "Illizi",
    "34": "Bordj Bou Arréridj",
    "35": "Boumerdès",
    "36": "El Tarf",
    "37": "Tindouf",
    "38": "Tissemsilt",
    "39": "El Oued",
    "40": "Khenchela",
    "41": "Souk Ahras",
    "42": "Tipaza",
    "43": "Mila",
    "44": "Aïn Defla",
    "45": "Naâma",
    "46": "Aïn Témouchent",
    "47": "Ghardaïa",
    "48": "Relizane",
    "49": "Timimoun",
    "50": "Bordj Badji Mokhtar",
    "51": "Ouled Djellal",
    "52": "Béni Abbès",
    "53": "In Salah",
    "54": "In Guezzam",
    "55": "Touggourt",
    "56": "Djanet",
    "57": "El Meghaier",
    "58": "El Meniaâ",
}
ABROAD = "ABROAD"
NATION = "NATION"
UNIT_NAMES = {
    ABROAD: "Algerian community abroad",
    NATION: "National total",
}
METHOD = (
    "Final results proclaimed by the Constitutional Council (the Constitutional Court since 2020) and published in the "
    "Journal officiel. Registered voters are the electoral roll, ballots cast are the voters who took part, valid "
    "votes are the votes counted for candidates, and invalid ballots are the null ballots. "
)
METHOD_TABLE = (
    "The proclamation tabulates every figure by wilaya, with the voters registered at diplomatic and consular posts "
    "and counted abroad as a separate line. The wilayas are those of the day: 48 until 2019, and 58 in 2024. "
)
METHOD_NATIONAL = (
    "The proclamation prints only the national figures, not a table by wilaya, so the results are stored for the "
    "country as a whole. Invalid ballots are not printed. The figures were read from a scan of the printed page."
)


def url(year: int) -> str:
    folder, issue, _, _ = ISSUES[year]
    return f"{SITE}/FTP/jo-francais/{folder}/{issue}.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


JORADP_SOURCE = key("source", "joradp")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president", "two_round")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Row:
    code: str
    accounting: tuple[int, int, int, int]  # registered, voters, valid, invalid
    votes: list[int]


@dataclass
class Table:
    rows: list[Row]
    total: Row


LENIENT = {2004, 2009}  # these tables print each row with some percentage cells missing, so only whole counts are read
# Rows whose text layer drops cells, read from the page image; each sums to the row's printed valid votes.
IMAGE_VOTES = {(2009, "25"): [306_122, 28_495, 11_945, 6_672, 4_963, 6_391]}
INVALID_FIRST = {2024}  # the 2024 table prints the null ballots before the valid votes


def _numbers(tokens: list[str]) -> list[str]:
    tail: list[str] = []
    for token in reversed(tokens):
        if not re.fullmatch(r"\d+(,\d+)?", token):
            break
        tail.append(token)
    return tail[::-1]


def _figures(year: int, code: str, figures: list[str], width: int) -> Row | None:
    """A printed row's counts: bureaux, registered, voters, turnout %, valid, invalid, then votes and % per candidate."""
    if year in LENIENT:
        votes = [int(figure) for figure in figures[6:] if "," not in figure]
        if len(figures) < 6 or len(votes) != width:
            return None
        whole = [int(figure) if "," not in figure else -1 for figure in figures[:6]]
    else:
        if len(figures) != 6 + 2 * width:
            return None
        whole = [int(figure) if "," not in figure else -1 for figure in figures]
        votes = [whole[6 + 2 * index] for index in range(width)]
    valid, invalid = (whole[5], whole[4]) if year in INVALID_FIRST else (whole[4], whole[5])
    return Row(code, (whole[1], whole[2], valid, invalid), votes)


def read_table(year: int) -> Table:
    artifact = raw.cached(SOURCE, url(year))
    text = subprocess.run(
        ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True, text=True
    ).stdout
    width = len(CANDIDATES[year])
    rows: list[Row] = []
    total: Row | None = None
    for line in text.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if "TOTAL" in (tokens[0].upper(), tokens[1].upper() if len(tokens) > 1 else ""):
            total = _figures(year, "TOTAL", _numbers(tokens), width) or total
            continue
        if not (tokens[0].isdigit() and 1 <= int(tokens[0]) <= 59) and tokens[0].lower() != "emigration":
            continue
        row = _figures(year, tokens[0], _numbers(tokens[1:]), width)
        if row is None and (year, tokens[0]) in IMAGE_VOTES:
            head = _numbers(tokens[1:])[:6]
            row = _figures(year, tokens[0], [*head, *(str(v) for v in IMAGE_VOTES[(year, tokens[0])])], width)
        if row is None:
            if len(_numbers(tokens[1:])) >= 6:
                print(f"NOTE {year}: row {tokens[0]} does not hold {width} votes: {line.strip()[:120]}")
            continue
        rows.append(row)
    if total is None:
        raise ValueError(f"{year} has no total row")
    return Table(rows, total)


def table_rows(year: int) -> tuple[list[tuple[str, Row]], Row]:
    """Units in print order: the wilayas by their sequence, then the line of voters abroad."""
    table = read_table(year)
    wilayas = 58 if year >= 2021 else 48
    if len(table.rows) != wilayas + 1:
        raise ValueError(f"{year}: {len(table.rows)} rows")
    units = [(f"{index:02d}", row) for index, row in enumerate(table.rows[:wilayas], 1)]
    for code, row in units:
        if row.code.isdigit() and int(row.code) != int(code):
            print(f"NOTE {year}: row printed as {row.code} read as wilaya {code}")
    return [*units, (ABROAD, table.rows[-1])], table.total


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Algeria's wilayas, from the Journal officiel's presidential proclamations",
    )
    p.external.add(ALGERIA)
    p.add("scheme", code="dz.wilaya", polity_id=ALGERIA, kind="administrative", rank=1, noun="wilaya")
    p.add("scheme", code="dz.count", polity_id=ALGERIA, kind="precinct", rank=2, noun="counting area")
    for code, name in WILAYAS.items():
        id = key("area", code)
        p.add("area", id=id, scheme_code="dz.wilaya")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "fr", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Algeria's presidency and its electoral system, from the Journal officiel's proclamations",
    )
    p.external.add(ALGERIA)
    p.add(
        "source",
        id=JORADP_SOURCE,
        kind="official_body",
        publisher="Journal officiel de la République algérienne démocratique et populaire",
        title="Proclamations of the final results of the presidential election, French edition of the Journal officiel",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Journal officiel de la République algérienne démocratique et populaire",
    )
    for year in DAYS:
        p.artifact(raw.cached(SOURCE, url(year)), JORADP_SOURCE)
    p.add("office", id=PRESIDENCY, polity_id=ALGERIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Algeria", "en", "official")
    p.name(PRESIDENCY, "Président de la République algérienne démocratique et populaire", "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ALGERIA,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "A candidate who wins an absolute majority of the votes cast is elected in the first round; otherwise the "
            "two leading candidates go to a second round."
        ),
        lang="en",
    )
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


def build(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Algerian presidential election, from the Constitutional Council's final results in the Journal officiel",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ALGERIA)
    p.uses(raw.cached(SOURCE, url(year)))
    election = key("election", year)
    p.add("election", id=election, polity_id=ALGERIA, kind="general", date_from=day, date_to=day, status="held")
    path = f"dz/{day}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Algerian presidential election", "en", "official")
    p.name(election, f"Élection présidentielle algérienne de {year}", "fr", "native", False)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Algerian presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=JORADP_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    issue, page = ISSUES[year][2], ISSUES[year][3]
    tabulated = year not in NATIONAL
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD + (METHOD_TABLE if tabulated else METHOD_NATIONAL) + f" Journal officiel {issue}, page {page}.",
        lang="en",
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=JORADP_SOURCE,
        locator={"issue": issue, "page": page},
    )
    options: list[str] = []
    for position, name in enumerate(CANDIDATES[year], 1):
        option = key("option", year, position)
        options.append(option)
        person = key("person", year, position)
        p.add("person", id=person)
        p.name(person, name, "fr", "official")
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
            id=key("candidacy", year, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    if tabulated:
        units, total = table_rows(year)
        data = units
        declared = total
    else:
        registered, voters, valid, votes = NATIONAL[year]
        declared = Row("TOTAL", (registered, voters, valid, 0), votes)
        data = [(NATION, declared)]
    for code, row in data:
        unit = key("unit", f"{day}:{code}")
        if code == ABROAD:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ALGERIA,
                scheme_code="dz.count",
                code=f"{day}:{code}",
                area_relation="unlocated",
                kind="abroad_mission",
            )
        elif code == NATION:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ALGERIA,
                scheme_code="dz.count",
                code=f"{day}:{code}",
                area_relation="unlocated",
                kind="aggregate",
            )
        else:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ALGERIA,
                scheme_code="dz.count",
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=key("area", code),
                kind="standard",
            )
        p.name(unit, UNIT_NAMES.get(code) or WILAYAS[code], "fr" if code in WILAYAS else "en", "official")
        registered, voters, valid, invalid = row.accounting
        for measure, value in ((REGISTERED, registered), (CAST, voters), (VALID, valid)):
            _put(p, result_set, unit, None, measure, value)
        if tabulated:
            _put(p, result_set, unit, None, INVALID, invalid)
        for option, value in zip(options, row.votes, strict=True):
            _put(p, result_set, unit, option, VOTES, value)
    registered, voters, valid, invalid = declared.accounting
    for measure, value in ((REGISTERED, registered), (CAST, voters), (VALID, valid)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    if tabulated:
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=invalid)
    for option, value in zip(options, declared.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    order = sorted(range(len(options)), key=lambda index: -declared.votes[index])
    for rank, index in enumerate(order):
        p.add("outcome", result_set_id=result_set, option_id=options[index], kind="elected" if rank == 0 else "lost")
    return p
