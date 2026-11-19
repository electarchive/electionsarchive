"""Presidential elections of El Salvador from 1994 to 2009, from the Tribunal Supremo Electoral's consolidated tables.

The 1994 tables (both rounds) are typeset PDFs with a text layer, printing every municipality. The 1999, 2004, and 2009
department volumes (consolidado departamental) are page images with no text layer, one or two pages for each
department, printing every municipality. Their rows were read from the page images into `presidents_tables/{year}.json`,
with each figure's page and box. Each row is checked against its own printed valid votes, each department's columns
against the volume's printed department total, and each department total against the department row of the printed
national table (`SCANNED`), which is also the source of the 2009 row for residents abroad.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.sv import assembly, tse

SOURCE = tse.SOURCE
D = "https://transparencia.tse.gob.sv/laip_tse/documentos/estadisticos/"
LISTING = assembly.LISTING
REGISTRY = "sv.tse/pres-registry"
HIST_SOURCE = tse.key("source", "tse-presidential-history")
VOTES, VALID, INVALID, OBJECTED = tse.VOTES, tse.VALID, tse.INVALID, tse.OBJECTED
EL_SALVADOR, TERRITORY = tse.EL_SALVADOR, tse.TERRITORY

PARTY_NAMES = {
    "ARENA": "Alianza Republicana Nacionalista",
    "FMLN": "Frente Farabundo Martí para la Liberación Nacional",
    "PCN": "Partido de Conciliación Nacional",
    "PDC": "Partido Demócrata Cristiano",
    "MAC": "Movimiento Auténtico Cristiano",
    "MU": "Movimiento de Unidad",
    "MSN": "Movimiento de Solidaridad Nacional",
    "CDU": "Centro Democrático Unido",
    "CD-FMLN-MNR": "Coalición Convergencia Democrática, FMLN y Movimiento Nacional Revolucionario",
    "FMLN-USC": "Coalición FMLN y Unidad Social Cristiana",
    "CDU-PDC": "Coalición Centro Democrático Unido y Partido Demócrata Cristiano",
}
ALIASES = {
    **assembly.ALIASES,
    "CANDELARIA": "CANDELARIA DE LA FRONTERA",
    "NVO EDEN DE SAN JUAN": "NUEVO EDEN DE SAN JUAN",
    "STGO DE MARIA": "SANTIAGO DE MARIA",
    "SAN DIONICIO": "SAN DIONISIO",
    "DULDE NOMBRE DE MARIA": "DULCE NOMBRE DE MARIA",
    "YOLAIQUIN": "YOLOAIQUIN",
    "SESEMBRA": "SENSEMBRA",
    "SAN CAYETANO": "SAN CAYETANO ISTEPEQUE",
    "VILLA VICTORIA": "VICTORIA",
    "VILLA DOLORES": "DOLORES",
    "PASAISO DE OSORIO": "PARAISO DE OSORIO",
    "SAN JOSE LAS FUENTES": "LAS FLORES",
    "CONCEPCION ATACO": "CONCEPCION DE ATACO",
}
OWN_PARTIES = ("ARENA", "FMLN")  # already held by the 2014 registry

DEPARTMENT_ORDER = (
    "SAN SALVADOR", "SANTA ANA", "SAN MIGUEL", "LA LIBERTAD", "USULUTAN", "SONSONATE", "LA UNION", "LA PAZ",
    "CHALATENANGO", "CUSCATLAN", "AHUACHAPAN", "MORAZAN", "SAN VICENTE", "CABANAS", "RES EN EL EXTERIOR",
)  # fmt: skip


@dataclass(frozen=True)
class Election:
    day: date
    round_number: int
    parties: tuple[str, ...]
    files: tuple[str, ...]
    grain: str  # "municipality" or "municipality-scan"
    page: int = 0


ELECTIONS = {
    e.day: e
    for e in (
        Election(
            date(1994, 3, 20),
            1,
            ("ARENA", "CD-FMLN-MNR", "MAC", "MU", "MSN", "PCN", "PDC"),
            (D + "1994/votospres19941ra.pdf",),
            "municipality",
        ),
        Election(date(1994, 4, 24), 2, ("ARENA", "CD-FMLN-MNR"), (D + "1994/votospres19942da.pdf",), "municipality"),
        Election(
            date(1999, 3, 7),
            1,
            ("ARENA", "CDU", "FMLN-USC", "LIDER", "PCN", "PDC", "PUNTO"),
            (D + "1999/consolidado_depto_presid_1999.pdf", D + "1999/consolidado_nacional_presid_1999.pdf"),
            "municipality-scan",
        ),
        Election(
            date(2004, 3, 21),
            1,
            ("ARENA", "CDU-PDC", "FMLN", "PCN"),
            (D + "2004/consolidado_depto_presid2004.pdf", D + "2004/consolidadonacional_presi_2004.pdf"),
            "municipality-scan",
        ),
        Election(
            date(2009, 3, 15),
            1,
            ("ARENA", "FMLN"),
            (D + "2009/consolidadodeppresyvicepres2009.pdf", D + "2009/consolidadonacional_presidenciales2009.pdf"),
            "municipality-scan",
        ),
    )
}
SUPPORT = (D + "2009/consolidadoresenextranj_presidenciales2009.pdf",)
TABLES = Path(__file__).with_name("presidents_tables")
DIGITIZED = 8
# Cells where the department volume's printed department total differs from the department row of the national table:
# {day: {department: {column: (department volume, national table)}}}, columns as in the tables (null is the last).
DISAGREE: dict[date, dict[int, dict[int, tuple[int, int]]]] = {
    date(1999, 3, 7): {5: {9: (2464, 2463)}, 6: {8: (206, 205), 9: (3491, 3492)}},
}

# Department rows read from the printed national tables: votes for each party in ballot order, valid votes (printed),
# objected (impugnados) and null (nulos) ballots. Departments run in DEPARTMENT_ORDER.
SCANNED: dict[date, list[tuple[tuple[int, ...], int, int, int]]] = {
    date(1999, 3, 7): [
        ((185051, 44287, 118408, 7516, 6105, 16962, 1373), 379702, 427, 6202),
        ((59391, 8445, 28327, 2051, 4165, 7262, 406), 110047, 225, 4160),
        ((38639, 3676, 24154, 725, 3134, 5724, 205), 76257, 283, 2573),
        ((76238, 10664, 35673, 2595, 3132, 7064, 522), 135888, 408, 3723),
        ((32167, 1877, 20583, 646, 2480, 3069, 226), 61048, 280, 2463),
        ((44128, 6384, 25891, 1543, 5445, 5723, 340), 89454, 205, 3492),
        ((19175, 557, 7295, 231, 3594, 3346, 169), 34367, 112, 2079),
        ((32890, 2507, 16613, 1708, 3079, 3969, 164), 60930, 252, 1785),
        ((26138, 1525, 14976, 330, 1348, 1673, 160), 46150, 112, 1762),
        ((23849, 3529, 10391, 604, 2285, 1896, 180), 42734, 132, 1372),
        ((28388, 1663, 16591, 577, 4724, 4415, 220), 56578, 303, 2147),
        ((15927, 927, 7857, 107, 2670, 3548, 88), 31124, 189, 1524),
        ((16868, 1663, 10603, 277, 1126, 1250, 112), 31899, 5, 53),
        ((15419, 936, 6110, 359, 1853, 1306, 87), 26070, 146, 959),
    ],
    date(2004, 3, 21): [
        ((388684, 33599, 301328, 7942), 731553, 518, 5564),
        ((123521, 8443, 66912, 4154), 203030, 276, 3604),
        ((87262, 5320, 56136, 4031), 152749, 423, 2508),
        ((162648, 10615, 86087, 4780), 264130, 479, 3184),
        ((71314, 2170, 47390, 2597), 123471, 329, 2264),
        ((91103, 8847, 54039, 8143), 162132, 345, 3011),
        ((49760, 2026, 17961, 7819), 77566, 270, 2367),
        ((66322, 3941, 38046, 4627), 112936, 271, 1575),
        ((44800, 1835, 26359, 1866), 74860, 174, 1410),
        ((56797, 2408, 28769, 2952), 90926, 238, 1429),
        ((66942, 4186, 33142, 5772), 110042, 272, 2088),
        ((37060, 2682, 18896, 2667), 61305, 312, 1646),
        ((33083, 1409, 23288, 2374), 60154, 149, 963),
        ((35140, 1256, 14166, 2057), 52619, 198, 1053),
    ],
    date(2009, 3, 15): [
        ((377161, 452263), 829424, 333, 3565),
        ((118372, 112657), 231029, 273, 1860),
        ((76224, 105279), 181503, 122, 1099),
        ((158705, 144694), 303399, 249, 1877),
        ((66407, 74902), 141309, 171, 928),
        ((90297, 101771), 192068, 159, 1541),
        ((49197, 41918), 91115, 188, 743),
        ((62690, 69458), 132148, 109, 794),
        ((44831, 42174), 87005, 111, 652),
        ((58529, 49946), 108475, 177, 854),
        ((69958, 63855), 133813, 230, 989),
        ((39004, 35292), 74296, 141, 642),
        ((35154, 35406), 70560, 84, 499),
        ((37927, 24223), 62150, 188, 428),
        ((132, 162), 294, 0, 0),
    ],
}
# Printed national totals: votes for each party, valid, objected, and null ballots.
NATIONAL: dict[date, tuple[tuple[int, ...], int, int, int]] = {
    date(1999, 3, 7): ((614268, 88640, 343472, 19269, 45140, 67207, 4252), 1182248, 3079, 34294),
    date(2004, 3, 21): ((1314436, 88737, 812519, 61781), 2277473, 4254, 32666),
    date(2009, 3, 15): ((1284588, 1354000), 2638588, 2535, 16471),
}


def fetch() -> None:
    for election in ELECTIONS.values():
        for url in election.files:
            raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SUPPORT:
        raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, *(f"sv.tse/pres-{day}" for day in ELECTIONS)]


def build(name: str) -> Package:
    if name == REGISTRY:
        return registry()
    return president(ELECTIONS[date.fromisoformat(name.removeprefix("sv.tse/pres-"))])


def number(token: str) -> int:
    return int(token.replace(",", ""))


@dataclass
class Row:
    name: str
    code: str
    votes: tuple[int, ...]
    valid: int
    objected: int
    invalid: int
    page: int = 0
    boxes: tuple[tuple[float, float, float, float], ...] = ()


def read_1994(election: Election) -> list[Row]:
    """Each municipality's row of a 1994 table, checked against its printed valid votes and department total."""
    text = assembly.pdf_text(raw.cached(SOURCE, election.files[0]))
    width = len(election.parties)
    departments = assembly.departments()
    municipalities = assembly.municipalities()
    pattern = re.compile(r"^\s*(\d{1,4})\s+([A-ZÑ][A-ZÑ. ]*?)\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$")
    rows: list[Row] = []
    department = 0
    printed: dict[int, tuple[int, ...]] = {}
    seen: dict[int, list[Row]] = {}
    for line in text.replace("(", " ").replace(")", " ").splitlines():
        found = re.search(r"DEPARTAMENTO\s+([A-ZÑ ]+?)\s*$", line)
        if found and "RESULTADOS" not in line:
            department = departments[assembly.plain(found.group(1))]
            seen.setdefault(department, [])
            continue
        total = re.match(r"^\s*TOTAL\b.*?\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$", line)
        if total:
            printed[department] = tuple(number(t) for t in total.group(1).split())
            department = 0
            continue
        match = pattern.match(line)
        if not match or not department:
            continue
        values = [number(t) for t in match.group(3).split()]
        if len(values) < width + 4:
            raise ValueError(f"{election.day}: row {line!r} has {len(values)} numbers")
        votes, valid, objected, invalid = tuple(values[:width]), values[width], values[width + 1], values[width + 2]
        name = match.group(2).strip()
        label = assembly.plain(name)
        code = municipalities[department][label if label in municipalities[department] else ALIASES.get(label, label)]
        row = Row(name, code, votes, valid, objected, invalid)
        if sum(votes) != valid:
            raise ValueError(f"{election.day}: {name}: parties sum to {sum(votes)}, valid votes {valid}")
        seen[department].append(row)
        rows.append(row)
    for number_, group in seen.items():
        expected = printed.get(number_)
        if expected is None:
            raise ValueError(f"{election.day}: department {number_} has no printed total")
        got = tuple(sum(r.votes[i] for r in group) for i in range(width))
        if got != expected[:width] or sum(r.valid for r in group) != expected[width]:
            raise ValueError(f"{election.day}: department {number_} sums {got} differ from printed {expected}")
    if len({r.code for r in rows}) != len(rows):
        raise ValueError(f"{election.day}: a municipality appears twice")
    return rows


def read_scanned(election: Election) -> list[Row]:
    """Each municipality's row of a department volume, checked against its printed valid votes, the volume's printed
    department totals, and the department rows of the national table; 2009 adds the national table's row abroad."""
    day = election.day
    width = len(election.parties)
    table = json.loads((TABLES / f"{day.year}.json").read_text("utf-8"))
    _, municipalities, _ = tse.tree(1, with_centers=False)
    rows: list[Row] = []
    for item in table["rows"]:
        values = item["values"]
        votes, (valid, objected, invalid) = tuple(values[:width]), values[width:]
        name, _ = municipalities[item["code"]]
        if len(values) != width + 3 or sum(votes) != valid:
            raise ValueError(f"{day}: {name}: parties sum to {sum(votes)}, valid votes {valid}")
        boxes = tuple((b[0], b[1], b[2], b[3]) for b in item["boxes"])
        rows.append(Row(name, item["code"], votes, valid, objected, invalid, item["page"], boxes))
    inland = {code for code, (_, parent) in municipalities.items() if int(parent[3:]) <= 14}
    if {r.code for r in rows} != inland or len(rows) != len(inland):
        raise ValueError(f"{day}: {len(rows)} rows for {len(inland)} municipalities, or a repeat")
    disagree = DISAGREE.get(day, {})
    for number in range(1, 15):
        code = f"dep{number:02d}"
        group = [r for r in rows if municipalities[r.code][1] == code]
        got = [sum(r.votes[i] for r in group) for i in range(width)]
        got += [sum(r.valid for r in group), sum(r.objected for r in group), sum(r.invalid for r in group)]
        if got != table["departments"][code]["total"]:
            raise ValueError(f"{day}: {code} sums {got} differ from its printed total {table['departments'][code]}")
        votes, valid, objected, invalid = SCANNED[day][number - 1]
        national = [*votes, valid, objected, invalid]
        for column, (left, right) in enumerate(zip(got, national, strict=True)):
            if left != right and disagree.get(number, {}).get(column) != (left, right):
                raise ValueError(f"{day}: {code} column {column} is {left}, the national table prints {right}")
    for index in range(14, len(SCANNED[day])):
        votes, valid, objected, invalid = SCANNED[day][index]
        rows.append(Row(DEPARTMENT_ORDER[index], f"dep{index + 1:02d}", votes, valid, objected, invalid))
    return rows


def national(election: Election) -> tuple[tuple[int, ...], int, int, int]:
    """The printed national row, checked against the department rows of the national table."""
    day = election.day
    width = len(election.parties)
    table = SCANNED[day]
    for index, (votes, valid, _, _) in enumerate(table):
        if len(votes) != width or sum(votes) != valid:
            raise ValueError(f"{day}: department {index + 1}: parties sum to {sum(votes)}, valid {valid}")
    votes, valid, objected, invalid = NATIONAL[day]
    got = tuple(sum(r[0][i] for r in table) for i in range(width))
    if got != votes or sum(r[1] for r in table) != valid or sum(r[2] for r in table) != objected:
        raise ValueError(f"{day}: department rows {got} do not match the printed national row {votes}")
    if sum(r[3] for r in table) != invalid:
        raise ValueError(f"{day}: null ballots do not match the printed national row")
    return NATIONAL[day]


def sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def read(election: Election) -> list[Row]:
    return read_1994(election) if election.grain == "municipality" else read_scanned(election)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The parties of the 1994 to 2009 Salvadoran presidential elections and the Tribunal Supremo Electoral's tables",
        depends=["sv.tse/geo", "sv.tse/registry"],
    )
    p.external |= {EL_SALVADOR, TERRITORY}
    p.add(
        "source",
        id=HIST_SOURCE,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de El Salvador",
        title="Escrutinio final de las elecciones presidenciales 1994, 1999, 2004 y 2009",
        url=LISTING,
        license="Official public results of the Tribunal Supremo Electoral",
        license_url="https://transparencia.tse.gob.sv/",
        attribution="Tribunal Supremo Electoral de El Salvador",
    )
    for election in ELECTIONS.values():
        for url in election.files:
            p.artifact(raw.cached(SOURCE, url), HIST_SOURCE)
        if election.grain != "municipality-scan":
            continue
        stored = raw.cached(SOURCE, election.files[0])
        artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{HIST_SOURCE}\x1f{stored.storage_key}"))
        pages = sorted({r.page for r in read(election) if r.page})
        for number, (width, height) in sizes(stored.path).items():
            if number in pages:
                p.add(
                    "source_page",
                    id=tse.key("page", election.day, number),
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
                    width=round(width * 100 / 72),
                    height=round(height * 100 / 72),
                )
    codes = sorted({code for e in ELECTIONS.values() for code in e.parties} - set(OWN_PARTIES))
    for code in codes:
        id = tse.key("party", code)
        p.add("party", id=id, kind="party", polity_id=EL_SALVADOR)
        p.add("external_id", entity_id=id, scheme="sv.tse.party", value=code)
        if code in PARTY_NAMES:
            p.name(id, PARTY_NAMES[code], "es", "official")
            p.name(id, code, "es", "abbreviation", False)
        else:
            p.name(id, code, "es", "abbreviation")
    return p


def president(election: Election) -> Package:
    day = election.day
    runoff = election.round_number == 2
    scanned = election.grain == "municipality-scan"
    first = date(1994, 3, 20)
    p = Package(
        f"sv.tse/pres-{day}",
        SOURCE,
        f"The {'second' if runoff else 'first'} round of the {day.year} Salvadoran presidential election, "
        "by municipality from the Tribunal Supremo Electoral",
        depends=["sv.tse/geo", "sv.tse/registry", REGISTRY] + (["sv.tse/pres-1994-03-20"] if runoff else []),
    )
    p.external |= {EL_SALVADOR, TERRITORY}
    for url in election.files:
        p.uses(raw.cached(SOURCE, url))
    rows = read(election)
    parties = election.parties
    totals = [sum(r.votes[i] for r in rows) for i in range(len(parties))]
    valid_total = sum(r.valid for r in rows)
    objected_total, invalid_total = sum(r.objected for r in rows), sum(r.invalid for r in rows)
    if scanned:
        printed, valid_total, objected_total, invalid_total = national(election)
        if list(printed) != totals:
            raise ValueError(f"{day}: municipality rows {totals} differ from the printed national row {printed}")
    ranked = sorted(range(len(parties)), key=lambda i: -totals[i])
    won_outright = not runoff and totals[ranked[0]] * 2 > valid_total
    election_id = tse.key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=EL_SALVADOR,
        kind="runoff" if runoff else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"sv/{day}-presidential"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    advancing = not runoff and not won_outright
    title = f"{day.year} Salvadoran presidential election" + (", second round" if runoff else "")
    p.name(election_id, title, "en", "official")
    p.name(
        election_id,
        f"Elección presidencial {day.year}" + (", segunda vuelta" if runoff else ""),
        "es",
        "native",
        False,
    )
    contest = tse.key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=tse.PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=election.round_number,
        previous_round_id=tse.key("contest", first) if runoff else None,
        electoral_system_id=tse.SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, title.replace("election", "election, first round") if advancing else title, "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = tse.key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=HIST_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = (
        f"Final scrutiny (escrutinio final) of the {day.year} presidential election as published by the Tribunal "
        "Supremo Electoral, one row per municipality. "
        + (
            "The rows were read from the page images of the department volume (consolidado departamental), and each "
            "figure keeps its page and box. Every row's ticket votes sum to its printed valid votes, every "
            "department's rows sum to the volume's printed department total, and those totals agree with the "
            "department rows of the printed national table, whose national row gives the declared totals. "
            if scanned
            else "The table is a typeset report with a text layer. "
        )
        + (
            "The 2009 national table also prints the votes of residents abroad, stored as their own unit. "
            if day.year == 2009
            else ""
        )
        + (
            "In Usulután the department volume prints 2,464 null ballots where the national table prints 2,463, and in "
            "Sonsonate 206 objected and 3,491 null ballots where it prints 205 and 3,492; the municipal rows follow the "
            "department volume, so they sum to one objected ballot more than the declared national total. "
            if day.year == 1999
            else ""
        )
        + "Votes are valid votes for each ticket, nulos are invalid ballots, and impugnados are ballots objected to. "
        "The source also lists abstentions and ballot-paper counts; they are not stored."
    )
    p.add("note", id=tse.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: list[str] = []
    for position, code in enumerate(parties, 1):
        option = tse.key("option", day, code)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            party_id=tse.key("party", code),
            ballot_position=position,
            ballot_label=code,
        )
        rank = ranked.index(position - 1)
        if runoff or won_outright:
            kind = "elected" if rank == 0 else "lost"
        else:
            kind = "advanced" if rank < 2 else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=totals[position - 1])
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid_total)
    p.add("declared", result_set_id=result_set, option_id=None, measure=OBJECTED, value=objected_total)
    p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=invalid_total)
    if day == date(1999, 3, 7):
        p.accept(
            "accounting.declared",
            "The TSE's department volume prints 206 objected ballots in Sonsonate where its national table prints 205; "
            "the municipal rows follow the department volume, the declared total the national table",
        )
    facts: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []

    def value(unit: str, option: str | None, measure: int, amount: int, row: Row, index: int) -> None:
        located = bool(row.boxes)
        facts.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": DIGITIZED if located else 0,
                "value": amount,
            }
        )
        if located:
            x0, y0, x1, y1 = row.boxes[index]
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "page_id": tse.key("page", day, row.page),
                    "source_id": None,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                }
            )

    departments, _, _ = tse.tree(1, with_centers=False) if scanned else ({}, {}, {})
    _, municipalities, _ = tse.tree(1, with_centers=False)
    parents: dict[str, str] = {}
    for code, label in departments.items():
        if int(code[3:]) > 14:
            continue
        # The department units that carried these results before they were read by municipality.
        parents[code] = tse.key("unit", day, code)
        p.add(
            "reporting_unit",
            id=parents[code],
            polity_id=EL_SALVADOR,
            scheme_code="sv.departamento",
            code=f"{day}:{code.removeprefix('dep')}",
            area_relation="equals",
            area_id=tse.area("sv.departamento", code),
            kind="aggregate",
        )
        p.name(parents[code], label.title(), "es", "official")
    for row in rows:
        unit = tse.key("unit", day, row.code)
        scheme = "sv.municipio" if row.code.startswith("mun") else "sv.departamento"
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=EL_SALVADOR,
            scheme_code=scheme,
            code=f"{day}:{row.code.removeprefix('mun').removeprefix('dep')}",
            parent_id=parents.get(municipalities[row.code][1]) if row.code in municipalities else None,
            area_relation="equals",
            area_id=tse.area(scheme, row.code),
            kind="aggregate",
        )
        p.name(unit, row.name.title(), "es", "official")
        width = len(options)
        for index, (option, amount) in enumerate(zip(options, row.votes, strict=True)):
            value(unit, option, VOTES, amount, row, index)
        value(unit, None, VALID, row.valid, row, width)
        value(unit, None, OBJECTED, row.objected, row, width + 1)
        value(unit, None, INVALID, row.invalid, row, width + 2)
    p.frame("result_value", pl.DataFrame(facts))
    if locators:
        p.frame("value_locator", pl.DataFrame(locators, schema=package.FACTS["value_locator"]))
    return p
