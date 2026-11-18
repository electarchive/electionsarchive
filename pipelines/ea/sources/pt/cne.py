"""Official results maps (mapas oficiais) of Portugal's National Elections Commission, 1975 to 1998.

The Commission's site, www.cne.pt, publishes the official results map of every election since 1975. The legislative
maps from 1976 to 1995 also come as spreadsheets by electoral circle; the other maps are PDFs, some with a text layer
and some scanned.
"""

import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pt import cne_ac1975, cne_al, mai

SOURCE = "pt.cne"
HOST = "https://www.cne.pt/sites/default/files/dl"

# File name under HOST for each election's map.
FILES: dict[str, str] = {
    **{
        f"ar{y}": f"eleicoes/{y}_ar/docs_geral/{y}_ar_quadro_resultados.zip"
        for y in (1976, 1979, 1980, 1983, 1985, 1987, 1991, 1995)
    },
    "ac1975": "resultados_ac_1975.pdf",
    "ac1975parties": "partidos_ac1975.pdf",
    "pe1987": "resultados_pe_1987.pdf",
    "pe1989": "resultados_pe_1989.pdf",
    "pe1994": "resultados_pe_1994.pdf",
    "pr1976": "resultados_pr_1976.pdf",
    "pr1980": "resultados_pr_1980.pdf",
    "pr1986a": "resultados_pr_1986_primeiravolta.pdf",
    "pr1986b": "resultados_pr_1986_segundavolta.pdf",
    "pr1991": "resultados_pr_1991.pdf",
    "pr1996": "resultados_pr_1996.pdf",
    "rn1998a": "resultados_rn_2_98.pdf",
    "rn1998b": "resultados_rn_4_98.pdf",
}


def url(name: str) -> str:
    return f"{HOST}/{FILES[name]}"


def fetch() -> None:
    for name, file in FILES.items():
        raw.fetch(SOURCE, url(name), suffix=".zip" if file.endswith(".zip") else ".pdf")
    cne_al.fetch()


def artifact(name: str) -> raw.Artifact:
    return raw.cached(SOURCE, url(name))


# ---------------------------------------------------------------------------------------------------------------------
# Spreadsheets


@dataclass
class Line:
    label: str
    votes: int | None
    seats: int | None


@dataclass
class Count:
    registered: int | None = None
    cast: int | None = None
    blank: int | None = None
    invalid: int | None = None
    seats: int | None = None
    lines: list[Line] = field(default_factory=list)


@dataclass
class Table:
    """One election's figures by place, with the national total the authority prints."""

    places: dict[str, Count]  # in the order printed
    total: Count | None
    legend: dict[str, str] = field(default_factory=dict)


def number(cell: object) -> int | None:
    text = "" if cell is None else str(cell).strip().replace(" ", "")
    text = text.removesuffix(".0")
    return int(text) if text.isdigit() else None


def sheet(name: str) -> list[list[object]]:
    with zipfile.ZipFile(artifact(name).path) as archive:
        book = next(n for n in archive.namelist() if n.endswith(".xlsx"))
        frame = fastexcel.read_excel(archive.read(book)).load_sheet(0, header_row=None).to_polars().cast(pl.String)
    return [list(row) for row in frame.rows()]


def spreadsheet(name: str) -> Table:
    rows = sheet(name)
    head = next(i for i, row in enumerate(rows) if row[0] == "Círculos Eleitorais")
    header = {j: str(c).strip() for j, c in enumerate(rows[head]) if c is not None}
    columns = {j: c for j, c in header.items() if j >= 5 and not c.startswith("Votos ") and c != "Brancos e Nulos"}
    ends = {j for j, c in header.items() if c.startswith("Número total")}
    parties = {j: c for j, c in columns.items() if j not in ends}
    blank_at = next((j for j, c in header.items() if c in ("Votos Brancos", "Brancos e Nulos")), None)
    null_at = next((j for j, c in header.items() if c == "Votos Nulos"), None)
    places: dict[str, Count] = {}
    total = None
    legend: dict[str, str] = {}
    for row in rows[head + 2 :]:
        first = row[0]
        if first is None:
            continue
        text = str(first).strip()
        if "=" in text:
            short, _, long = text.partition("=")
            legend[short.strip()] = long.split("=")[-1].strip()
            continue
        if not (text.isdigit() or text == "Totais"):
            continue
        count = Count(
            registered=number(row[2]),
            cast=number(row[3]),
            blank=number(row[blank_at]) if blank_at is not None else None,
            invalid=number(row[null_at]) if null_at is not None else None,
            seats=number(row[max(ends)]) if ends else None,
        )
        for j, label in parties.items():
            count.lines.append(Line(label, number(row[j]), number(row[j + 2]) or 0))
        if text == "Totais":
            total = count
        else:
            places[str(row[1]).strip()] = count
    return Table(places, total, legend)


# ---------------------------------------------------------------------------------------------------------------------
# Figures keyed from the maps that are printed pages: national tables, read off the scans or the text layer


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


PORTUGAL = mai.PORTUGAL
COUNTRY = mai.COUNTRY
CNE_SOURCE = key("source", "cne")
UNIT_SCHEME = "pt.cne_circulo"
REGION_SCHEME = "pt.cne_regiao"
VOTES, REGISTERED, CAST, INVALID, BLANK = mai.VOTES, mai.REGISTERED, mai.CAST, mai.INVALID, mai.BLANK


@dataclass
class Vote:
    """A national or regional count with candidates or answers rather than lists."""

    registered: int | None
    cast: int | None
    blank: int
    invalid: int
    lines: list[tuple[str, int, int]]  # label, votes, seats


# Presidential elections: the map's national table, candidates in ballot order with their full names.
PRESIDENTIAL: dict[str, tuple[date, str, Vote]] = {
    "pr1976": (
        date(1976, 6, 27),
        "pr1976",
        Vote(
            6467480,
            4881125,
            20253,
            43242,
            [
                ("António dos Santos Ramalho Eanes", 2967137, 0),
                ("José Baptista Pinheiro de Azevedo", 692147, 0),
                ("Octávio Floriano Rodrigues Pato", 365586, 0),
                ("Otelo Nuno Romão Saraiva de Carvalho", 792760, 0),
            ],
        ),
    ),
    "pr1980": (
        date(1980, 12, 7),
        "pr1980",
        Vote(
            6920869,
            5840332,
            16076,
            44014,
            [
                ("António da Silva Osório Soares Carneiro", 2325481, 0),
                ("António Elísio Capelo Pires Veloso", 45132, 0),
                ("Otelo Nuno Romão Saraiva de Carvalho", 85896, 0),
                ("António dos Santos Ramalho Eanes", 3262520, 0),
                ("Carlos Galvão de Melo", 48468, 0),
                ("António Jorge Oliveira Aires Rodrigues", 12745, 0),
            ],
        ),
    ),
    "pr1986a": (
        date(1986, 1, 26),
        "pr1986a",
        Vote(
            7617257,
            5742151,
            17709,
            46334,
            [
                ("Francisco de Almeida Salgado Zenha", 1185867, 0),
                ("Maria de Lourdes Ruivo da Silva Matos Pintasilgo", 418961, 0),
                ("Diogo Pinto de Freitas do Amaral", 2629597, 0),
                ("Mário Alberto Nobre Lopes Soares", 1443683, 0),
            ],
        ),
    ),
    "pr1986b": (
        date(1986, 2, 16),
        "pr1986b",
        Vote(
            7612733,
            5937100,
            20436,
            33844,
            [
                ("Diogo Pinto de Freitas do Amaral", 2872064, 0),
                ("Mário Alberto Nobre Lopes Soares", 3010756, 0),
            ],
        ),
    ),
    "pr1991": (
        date(1991, 1, 13),
        "pr1991",
        Vote(
            8202812,
            5098768,
            112877,
            68037,
            [
                ("Basílio Adolfo Mendonça Horta da Franca", 696379, 0),
                ("Mário Alberto Nobre Lopes Soares", 3459521, 0),
                ("Carlos Alberto do Vale Gomes Carvalhas", 635373, 0),
                ("Carlos Manuel Marques da Silva", 126581, 0),
            ],
        ),
    ),
    "pr1996": (
        date(1996, 1, 14),
        "pr1996",
        Vote(
            8693636,
            5762978,
            63463,
            69328,
            [
                ("Aníbal António Cavaco Silva", 2595131, 0),
                ("Jorge Fernando Branco de Sampaio", 3035056, 0),
            ],
        ),
    ),
}

# European Parliament: the national table, lists in the order the map prints them, with seats.
EUROPEAN: dict[str, tuple[date, int, Vote]] = {
    "pe1987": (
        date(1987, 7, 19),
        24,
        Vote(
            None,
            None,
            68475,
            74240,
            [
                ("PDC", 40812, 0),
                ("PPD/PSD", 2111828, 10),
                ("CDS", 868718, 4),
                ("PCTP/MRPP", 19475, 0),
                ("PSR", 29009, 0),
                ("PRD", 250158, 1),
                ("MDP/CDE", 27678, 0),
                ("PC(R)", 24060, 0),
                ("PS", 1267672, 6),
                ("PPM", 155990, 0),
                ("CDU", 648700, 3),
                ("UDP", 52835, 0),
            ],
        ),
    ),
    "pe1989": (
        date(1989, 6, 18),
        24,
        Vote(
            8121564,
            4149756,
            66074,
            61682,
            [
                ("PCTP/MRPP", 26682, 0),
                ("MDP", 56900, 0),
                ("PSD", 1358958, 9),
                ("PSR", 31775, 0),
                ("FER", 7833, 0),
                ("UDP", 45017, 0),
                ("CDU", 597759, 4),
                ("PDC", 29745, 0),
                ("PPM", 84272, 0),
                ("CDS", 587497, 3),
                ("PS", 1184380, 8),
                ("POUS", 11182, 0),
            ],
        ),
    ),
    "pe1994": (
        date(1994, 6, 12),
        25,
        Vote(
            8565822,
            3044001,
            48916,
            45320,
            [
                ("MPT", 12955, 0),
                ("PSN", 11214, 0),
                ("CDU", 340725, 3),
                ("CDS-PP", 379044, 3),
                ("PSR", 17780, 0),
                ("MUT", 2893, 0),
                ("PCTP-MRPP", 24022, 0),
                ("P XXI", 12402, 0),
                ("PS", 1061560, 10),
                ("PDA", 7127, 0),
                ("PPD/PSD", 1046918, 9),
                ("PPM", 8300, 0),
                ("UDP", 18884, 0),
                ("PRD", 5941, 0),
            ],
        ),
    ),
}

# Referendums: the national question, and for the second the question put to each proposed region.
ABORTION = Vote(8496089, 2709503, 29057, 15562, [("Sim", 1308130, 0), ("Não", 1356754, 0)])
REGIONS = Vote(8640026, 4157447, 96501, 76395, [("Sim", 1453749, 0), ("Não", 2530802, 0)])
REGION_ROWS = re.compile(r"^\s+(Região .*\S)\s*$")


def regional_questions() -> dict[str, Vote]:
    """The November 1998 map's result for each of the eight proposed regions, read from the PDF's text layer."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(artifact("rn1998b").path), "-"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    out: dict[str, Vote] = {}
    for i, line in enumerate(text):
        found = REGION_ROWS.match(line)
        if found is None:
            continue
        row = next(text[j] for j in range(i + 1, i + 12) if re.match(r"\s*\d", text[j]))
        cells = [int(cell.replace(" ", "")) for cell in re.split(r"\s{2,}", row.strip()) if "," not in cell]
        registered, cast, _, blank, invalid, _, yes, no = cells
        out[found.group(1)] = Vote(registered, cast, blank, invalid, [("Sim", yes, 0), ("Não", no, 0)])
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Legislative elections by electoral circle

LEGISLATIVE = {
    "ar1976": date(1976, 4, 25),
    "ar1979": date(1979, 12, 2),
    "ar1980": date(1980, 10, 5),
    "ar1983": date(1983, 4, 25),
    "ar1985": date(1985, 10, 6),
    "ar1987": date(1987, 7, 19),
    "ar1991": date(1991, 10, 6),
    "ar1995": date(1995, 10, 1),
}
DISTRICTS = {
    mai.fold(name): f"{n:02d}"
    for n, name in enumerate(
        [
            "Aveiro",
            "Beja",
            "Braga",
            "Bragança",
            "Castelo Branco",
            "Coimbra",
            "Évora",
            "Faro",
            "Guarda",
            "Leiria",
            "Lisboa",
            "Portalegre",
            "Porto",
            "Santarém",
            "Setúbal",
            "Viana do Castelo",
            "Vila Real",
            "Viseu",
        ],
        1,
    )
}
# Circles that lie within a region: the Azores (three circles in 1976, one after) and Madeira.
REGION_CIRCLES = {
    "acores": "40",
    "angra do heroismo": "40",
    "horta": "40",
    "ponta delgada": "40",
    "madeira": "30",
    "funchal": "30",
}


# Circles of 1975 outside the districts: Macau, Mozambique, and the emigrants, none of which has a shape here.
ABROAD_1975 = {"macau", "mocambique", "emigracao"}


def circle_area(name: str) -> tuple[str | None, str]:
    """The pt.mai area a circle of the Assembly lies in or equals, and how."""
    folded = mai.fold(name)
    if folded in DISTRICTS:
        return mai.district_area(DISTRICTS[folded]), "equals"
    if folded in REGION_CIRCLES:
        same = folded in ("acores", "madeira")
        return mai.district_area(REGION_CIRCLES[folded]), "equals" if same else "within"
    if folded in ABROAD_1975:
        return None, "unlocated"
    if folded == "europa":
        return mai.circle_area("europa"), "equals"
    if folded == "fora da europa":
        return mai.circle_area("fora-da-europa"), "equals"
    raise LookupError(f"no area for circle {name}")


# ---------------------------------------------------------------------------------------------------------------------
# Registry

BLOCS = {
    "AD",
    "APU",
    "CDU",
    "PCP/PEV",
    "PCP-PEV",
    "PPM MPT",
    "UDP/PSR",
    "FRS",
    "PDC-MIRN/PDP-FN",
    "POUS/PST",
    "UDA/PDA",
    "PS/CDS-PP",
    "PS/PCP/PEV/PSR/UDP",
    "PS-PCP-PEV-UDP-PDA",
    "PS/PCP/PEV/UDP",
    "PPD/PSD.CDS-PP",
    "CDS-PP.PPD/PSD",
}
NAMES = {
    "AD": "Aliança Democrática",
    "APU": "Aliança Povo Unido",
    "CDS": "Partido do Centro Democrático Social",
    "CDS/PP": "Partido Popular",
    "CDS-PP": "Partido do Centro Democrático Social - Partido Popular",
    "CDU": "Coligação Democrática Unitária",
    "ADIM": "Associação para a Defesa dos Interesses de Macau",
    "CDM": "Centro Democrático de Macau",
    "FEC": "Frente Eleitoral de Comunistas (Marxistas-Leninistas)",
    "FER": "Frente da Esquerda Revolucionária",
    "FSP": "Frente Socialista Popular",
    "LCI": "Liga Comunista Internacionalista",
    "MES": "Movimento de Esquerda Socialista",
    "MDP": "Movimento Democrático Português",
    "MDP/CDE": "Movimento Democrático Português / Comissão Democrática Eleitoral",
    "MPT": "Movimento O Partido da Terra",
    "MUT": "Movimento Unitário dos Trabalhadores",
    "PC(R)": "Partido Comunista (Reconstruído)",
    "PCP": "Partido Comunista Português",
    "PCP-PEV": "Coligação Democrática Unitária",
    "PCTP/MRPP": "Partido Comunista dos Trabalhadores Portugueses",
    "PCTP-MRPP": "Partido Comunista dos Trabalhadores Portugueses",
    "PDA": "Partido Democrático do Atlântico",
    "PDC": "Partido da Democracia Cristã",
    "PG": "Partido da Gente",
    "POUS": "Partido Operário de Unidade Socialista",
    "PPD": "Partido Popular Democrático",
    "PPD/PSD": "Partido Social-Democrata",
    "PPM": "Partido Popular Monárquico",
    "PRD": "Partido Renovador Democrático",
    "PS": "Partido Socialista",
    "PSD": "Partido Social-Democrata",
    "PSN": "Partido de Solidariedade Nacional",
    "PUP": "Partido de Unidade Popular",
    "PSR": "Partido Socialista Revolucionário",
    "UDP": "União Democrática Popular",
}
CONSTITUENT = key("body", "assembleia-constituinte")
SYSTEM_LIST, SYSTEM_PRESIDENT, SYSTEM_REFERENDUM = mai.SYSTEM_LIST, mai.SYSTEM_PRESIDENT, mai.SYSTEM_REFERENDUM


def party(label: str) -> str:
    return key("party", label)


def person(name: str) -> str:
    return key("person", mai.fold(name))


def list_labels() -> dict[str, str | None]:
    """Every list label the legislative and European maps print, with the full name where the map gives one."""
    out: dict[str, str | None] = {}
    for name in LEGISLATIVE:
        table = spreadsheet(name)
        for label in (line.label for line in (table.total.lines if table.total else [])):
            out.setdefault(label, None)
        for short, long in table.legend.items():
            if short in out and short != "Md":
                out[short] = long
    for _, _, vote in EUROPEAN.values():
        for label, _, _ in vote.lines:
            out.setdefault(label, None)
    for label in cne_ac1975.AC1975_ORDER:
        out.setdefault(label, None)
    for label in cne_al.labels():
        out.setdefault(label, None)
    return out


def candidate_names() -> list[str]:
    seen: dict[str, str] = {}
    for _, _, vote in PRESIDENTIAL.values():
        for name, _, _ in vote.lines:
            seen.setdefault(mai.fold(name), name)
    return sorted(seen.values())


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}-{tail}" for _, day, tail, _, _ in ELECTIONS),
        *cne_al.packages(),
    ]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The reporting-unit scheme for the National Elections Commission's circle and national tables, and the eight "
        "regions put to the 1998 referendum",
    )
    p.external.add(PORTUGAL)
    p.external.add(COUNTRY)
    p.add(
        "scheme",
        code=UNIT_SCHEME,
        polity_id=PORTUGAL,
        kind="electoral_district",
        rank=1,
        noun="electoral circle",
        code_system=None,
    )
    p.add(
        "scheme",
        code=REGION_SCHEME,
        polity_id=PORTUGAL,
        kind="administrative",
        rank=1,
        noun="proposed administrative region",
        code_system=None,
    )
    for name, code in REGION_AREAS.items():
        id = region_area(code)
        p.add("area", id=id, scheme_code=REGION_SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name.removeprefix("Região "), "pt", "official")
    return p


REGION_AREAS = {
    "Região de Entre Douro e Minho": "entre-douro-e-minho",
    "Região de Trás-os-Montes e Alto Douro": "tras-os-montes-e-alto-douro",
    "Região da Beira Litoral": "beira-litoral",
    "Região da Beira Interior": "beira-interior",
    "Região da Estremadura e Ribatejo": "estremadura-e-ribatejo",
    "Região de Lisboa e Setúbal": "lisboa-e-setubal",
    "Região do Alentejo": "alentejo",
    "Região do Algarve": "algarve",
}


def region_area(code: str) -> str:
    # Keyed by the region scheme's first code so the area ids stay the same across the rename.
    return key("area", "pt.cne.regiao", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, coalitions, and presidential candidates on the National Elections Commission's official "
        "results maps from 1975 to 1998",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Comissão Nacional de Eleições",
        title="Mapas oficiais dos resultados eleitorais (www.cne.pt)",
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais",
        license="Official publication; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for name in FILES:
        p.artifact(artifact(name), CNE_SOURCE)
    for spec in cne_al.MAPS:
        p.artifact(cne_al.artifact(spec), CNE_SOURCE)
    p.add("body", id=CONSTITUENT, polity_id=PORTUGAL, kind="constituent_assembly")
    p.name(CONSTITUENT, "Constituent Assembly", "en", "official")
    p.name(CONSTITUENT, "Assembleia Constituinte", "pt", "native", False)
    for label, long in sorted(list_labels().items()):
        id = party(label)
        p.add("party", id=id, kind="bloc" if label in BLOCS else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.cne.party", value=label)
        full = long or NAMES.get(label)
        if full:
            p.name(id, full, "pt", "official")
            if full != label:
                p.name(id, label, "pt", "abbreviation", False)
        else:
            p.name(id, label, "pt", "official")
    for name in candidate_names():
        id = person(name)
        p.add("person", id=id)
        p.name(id, name, "pt", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Elections

# name, day, path tail, election kind, family
ELECTIONS: list[tuple[str, date, str, str, str]] = [
    *((n, d, "general", "general", "ar") for n, d in LEGISLATIVE.items()),
    ("ac1975", date(1975, 4, 25), "constituent", "general", "ac"),
    *((n, v[0], "presidential", "runoff" if n == "pr1986b" else "general", "pr") for n, v in PRESIDENTIAL.items()),
    *((n, v[0], "european", "general", "pe") for n, v in EUROPEAN.items()),
    ("rn1998a", date(1998, 6, 28), "referendum", "referendum", "rn"),
    ("rn1998b", date(1998, 11, 8), "referendum", "referendum", "rn"),
]
TITLES = {
    "ac": ("{year} Portuguese Constituent Assembly election", "Eleição para a Assembleia Constituinte de {year}"),
    "ar": ("{year} Portuguese legislative election", "Eleições legislativas de {year}"),
    "pr": ("{year} Portuguese presidential election", "Eleição presidencial de {year}"),
    "pe": ("{year} European Parliament election in Portugal", "Eleições europeias de {year}"),
}
METHOD = (
    "The National Elections Commission's official results map (mapa oficial), published in the Diário da República. "
    "{unit} Blank and invalid (nulos) ballots are printed apart from the votes for each option; valid votes are not "
    "stored. The figures are stored as printed."
)
NULL_ONLY = "The 1975 map prints one figure for null ballots (votos nulos); it is stored here as invalid ballots."
JOINED = "The 1976 map prints blank and invalid ballots as one figure; it is stored here as invalid ballots."


def slug(path: str, text: str, slugs: set[str]) -> str:
    base = f"{path}/{changeset.slug(text) or 'contest'}"
    candidate, n = base, 2
    while candidate in slugs:
        candidate = f"{base}-{n}"
        n += 1
    slugs.add(candidate)
    return candidate


class Builder:
    def __init__(self, p: Package, name: str, day: date, tail: str) -> None:
        self.p, self.name, self.day, self.path = p, name, day, f"pt/{day}-{tail}"
        self.slugs: set[str] = {self.path}
        self.built = mai.Built()
        self.election_id = key("election", name)

    def unit(self, code: str, label: str, area: str | None, relation: str, scheme: str = UNIT_SCHEME) -> str:
        if code in self.built.units:
            return self.built.units[code]
        unit = self.built.units[code] = key("unit", self.name, code)
        self.p.add(
            "reporting_unit",
            id=unit,
            polity_id=PORTUGAL,
            scheme_code=scheme,
            code=f"{self.day}:{self.name}:{code}",
            area_relation=relation,
            area_id=area,
            kind="aggregate",
        )
        self.p.name(unit, label, "pt", "official")
        return unit

    def contest(
        self,
        node: str,
        kind: str,
        english: str,
        portuguese: str,
        slug_text: str,
        count: Count | Vote,
        unit: str,
        labels: list[str],
        magnitude: int | None = None,
        **fields: object,
    ) -> tuple[str, str, dict[str, str]]:
        p = self.p
        contest = key("contest", self.name, node)
        p.add(
            "contest",
            id=contest,
            election_id=self.election_id,
            kind=kind,
            round_number=fields.pop("round_number", 1),
            default_result_kind="counted",
            magnitude=magnitude,
            **fields,
        )
        p.name(contest, english, "en", "official")
        if portuguese != english:
            p.name(contest, portuguese, "pt", "native", False)
        p.add("slug", path=slug(self.path, slug_text, self.slugs), entity_id=contest, is_current=True)
        result_set = key("result_set", self.name, node)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        unit_text = "The figures are by electoral circle." if isinstance(count, Count) else "The figures are national."
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(unit=unit_text),
            lang="en",
        )
        options = {label: key("option", self.name, node, label) for label in labels}
        for position, label in enumerate(labels, 1):
            options[label] = key("option", self.name, node, label)
        return contest, result_set, options

    def figures(self, result_set: str, unit: str, count: Count | Vote, options: dict[str, str]) -> None:
        for measure, value in (
            (REGISTERED, count.registered),
            (CAST, count.cast),
            (BLANK, count.blank),
            (INVALID, count.invalid),
        ):
            if value is not None:
                self.built.values.append((result_set, unit, None, measure, value))
        for line in count.lines:
            label = line.label if isinstance(line, Line) else line[0]
            votes = line.votes if isinstance(line, Line) else line[1]
            if votes is not None:
                self.built.values.append((result_set, unit, options[label], VOTES, votes))


def legislative(b: Builder, name: str) -> None:
    table = spreadsheet(name)
    p = b.p
    year = LEGISLATIVE[name].year
    for circle, placed in table.places.items():
        count = Count(
            placed.registered,
            placed.cast,
            placed.blank,
            placed.invalid,
            placed.seats,
            [line for line in placed.lines if line.votes is not None],
        )
        area, relation = circle_area(circle)
        unit = b.unit(mai.fold(circle).replace(" ", "-"), circle, area, relation)
        labels = [line.label for line in count.lines]
        seats = count.seats if count.seats is not None else sum(line.seats or 0 for line in count.lines)
        contest, result_set, options = b.contest(
            mai.fold(circle),
            "list_allocation",
            f"{circle} ({year})" if False else circle,
            circle,
            circle,
            count,
            unit,
            labels,
            seats or None,
            body_id=mai.ASSEMBLY,
            electoral_system_id=SYSTEM_LIST,
            district_area_id=area,
        )
        for position, label in enumerate(labels, 1):
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_position=position,
                ballot_label=label,
            )
        b.figures(result_set, unit, count, options)
        for line in count.lines:
            if line.seats:
                p.add("outcome", result_set_id=result_set, option_id=options[line.label], kind="won", seats=line.seats)
        off = (
            sum(line.votes or 0 for line in count.lines) + (count.blank or 0) + (count.invalid or 0) - (count.cast or 0)
        )
        if count.cast is not None and off:
            p.add(
                "note",
                id=key("note", result_set, "sum"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The printed figures for this circle do not add up: the votes for the lists, blank ballots, and "
                    f"invalid ballots come to {abs(off):,} {'more' if off > 0 else 'fewer'} than the ballots cast. "
                    "They are stored as printed."
                ),
                lang="en",
            )
        if name == "ar1976":
            p.add(
                "note",
                id=key("note", result_set, "joined"),
                entity_id=result_set,
                kind="caveat",
                body_md=JOINED,
                lang="en",
            )


def constituent(b: Builder, name: str) -> None:
    p = b.p
    order = cne_ac1975.AC1975_ORDER
    registered, cast, null, votes = cne_ac1975.AC1975_TOTAL
    rows = cne_ac1975.AC1975
    if (sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows)) != (registered, cast, null) or [
        sum(r[4][i][0] for r in rows) for i in range(len(order))
    ] != list(votes):
        raise ValueError("the 1975 circles do not add to the national totals the map prints")
    for circle, registered, cast, null, figures in cne_ac1975.AC1975:
        area, relation = circle_area(circle)
        lines = [Line(label, votes, seats) for label, (votes, seats) in zip(order, figures, strict=True) if votes]
        count = Count(registered, cast, None, null, sum(line.seats or 0 for line in lines), lines)
        unit = b.unit(mai.fold(circle).replace(" ", "-"), circle, area, relation)
        labels = [line.label for line in lines]
        fields: dict[str, object] = {"body_id": CONSTITUENT, "electoral_system_id": SYSTEM_LIST}
        if area:
            fields["district_area_id"] = area
        contest, result_set, options = b.contest(
            mai.fold(circle), "list_allocation", circle, circle, circle, count, unit, labels, count.seats, **fields
        )
        for position, label in enumerate(labels, 1):
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_position=position,
                ballot_label=label,
            )
        b.figures(result_set, unit, count, options)
        for line in lines:
            if line.seats:
                p.add("outcome", result_set_id=result_set, option_id=options[line.label], kind="won", seats=line.seats)
        p.add(
            "note",
            id=key("note", result_set, "null"),
            entity_id=result_set,
            kind="caveat",
            body_md=NULL_ONLY,
            lang="en",
        )


def presidential(b: Builder, name: str) -> None:
    p = b.p
    vote = PRESIDENTIAL[name][2]
    unit = b.unit("national", "Portugal", COUNTRY, "equals")
    labels = [label for label, _, _ in vote.lines]
    runoff = name == "pr1986b"
    fields: dict[str, object] = {
        "office_id": mai.PRESIDENCY,
        "electoral_system_id": SYSTEM_PRESIDENT,
    }
    if runoff:
        fields.update(round_number=2, previous_round_id=key("contest", "pr1986a", "president"))
    contest, result_set, options = b.contest(
        "president",
        "office",
        "President",
        "Presidente da República",
        "president",
        vote,
        unit,
        labels,
        1,
        **fields,
    )
    for position, label in enumerate(labels, 1):
        p.add(
            "contest_option",
            id=options[label],
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, label),
            person_id=person(label),
            option_id=options[label],
            role="head",
            ballot_name=label,
            is_independent=True,
        )
    b.figures(result_set, unit, vote, options)
    total = sum(votes for _, votes, _ in vote.lines)
    ranked = sorted(vote.lines, key=lambda line: -line[1])
    if ranked[0][1] * 2 > total:
        p.add("outcome", result_set_id=result_set, option_id=options[ranked[0][0]], kind="elected")
    else:
        for label, _, _ in ranked[:2]:
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="advanced")


def european(b: Builder, name: str) -> None:
    p = b.p
    _, seats, vote = EUROPEAN[name]
    unit = b.unit("national", "Portugal", COUNTRY, "equals")
    labels = [label for label, _, _ in vote.lines]
    contest, result_set, options = b.contest(
        "european-parliament",
        "list_allocation",
        "European Parliament",
        "Parlamento Europeu",
        "european-parliament",
        vote,
        unit,
        labels,
        seats,
        electoral_system_id=SYSTEM_LIST,
    )
    for position, label in enumerate(labels, 1):
        p.add(
            "contest_option",
            id=options[label],
            contest_id=contest,
            kind="list",
            party_id=party(label),
            ballot_position=position,
            ballot_label=label,
        )
    b.figures(result_set, unit, vote, options)
    for label, _, won in vote.lines:
        if won:
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=won)
    if vote.registered is None:
        p.add(
            "note",
            id=key("note", result_set, "registered"),
            entity_id=result_set,
            kind="caveat",
            body_md="The map prints no count of registered voters or ballots cast.",
            lang="en",
        )


def referendum(b: Builder, name: str) -> None:
    p = b.p
    questions: list[tuple[str, str, str, Vote, str | None]]
    if name == "rn1998a":
        questions = [
            (
                "question",
                "Referendum on decriminalizing abortion",
                "Referendo sobre a despenalização da interrupção voluntária da gravidez",
                ABORTION,
                None,
            )
        ]
    else:
        questions = [
            (
                "question",
                "Referendum on instituting administrative regions",
                "Referendo sobre a instituição em concreto das regiões administrativas",
                REGIONS,
                None,
            )
        ]
        for region, vote in regional_questions().items():
            text = region.removeprefix("Região ")
            questions.append(
                (
                    mai.fold(text).replace(" ", "-"),
                    f"Regional question: {text}",
                    f"Pergunta de alcance regional: {text}",
                    vote,
                    REGION_AREAS[region],
                )
            )
    for node, english, portuguese, vote, region in questions:
        if region is None:
            unit = b.unit("national", "Portugal", COUNTRY, "equals")
        else:
            unit = b.unit(region, region, region_area(region), "equals", REGION_SCHEME)
        labels = [label for label, _, _ in vote.lines]
        contest, result_set, options = b.contest(
            node,
            "ballot_question",
            english,
            portuguese,
            node,
            vote,
            unit,
            labels,
            None,
            electoral_system_id=SYSTEM_REFERENDUM,
            **({"district_area_id": region_area(region)} if region else {}),
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        for position, label in enumerate(labels, 1):
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        b.figures(result_set, unit, vote, options)


def election(entry: tuple[str, date, str, str, str]) -> Package:
    name, day, tail, kind, family = entry
    p = Package(
        f"{SOURCE}/{day}-{tail}",
        SOURCE,
        f"The Portuguese {'referendum' if family == 'rn' else 'election'} of {day}, from the National Elections "
        "Commission's official results map",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            "pt.mai/geo",
            "pt.mai/registry",
            *([f"{SOURCE}/{PRESIDENTIAL['pr1986a'][0]}-{tail}"] if name == "pr1986b" else []),
        ],
    )
    p.external.add(PORTUGAL)
    p.external.add(COUNTRY)
    p.uses(artifact(name))
    if name == "ac1975":
        p.uses(artifact("ac1975parties"))
    if name == "ar1983":
        p.accept(
            "outcomes.seats_magnitude",
            "the 1983 spreadsheet gives the Fora da Europa circle two seats but marks one each for CDS, PPD/PSD, and "
            "PS; PS had 3,384 votes there, and the map's national total gives PS 101 seats where the circles add to 102",
        )
    b = Builder(p, name, day, tail)
    if family == "rn":
        english = (
            "1998 Portuguese abortion referendum"
            if name == "rn1998a"
            else "1998 Portuguese administrative regions referendum"
        )
        portuguese = (
            "Referendo nacional sobre a interrupção voluntária da gravidez de 1998"
            if name == "rn1998a"
            else "Referendo nacional sobre as regiões administrativas de 1998"
        )
    else:
        english, portuguese = (text.format(year=day.year) for text in TITLES[family])
        if kind == "runoff":
            english, portuguese = f"{english}, second round", f"{portuguese}, segunda volta"
    p.add("election", id=b.election_id, polity_id=PORTUGAL, kind=kind, date_from=day, date_to=day, status="held")
    p.name(b.election_id, english, "en", "official")
    p.name(b.election_id, portuguese, "pt", "native", False)
    p.add("slug", path=b.path, entity_id=b.election_id, is_current=True)
    {"ac": constituent, "ar": legislative, "pr": presidential, "pe": european, "rn": referendum}[family](b, name)
    mai._values(p, b.built)
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for entry in ELECTIONS:
        election(entry).write()
    for spec in cne_al.MAPS:
        cne_al.election(spec).write()


def emit() -> None:
    package.run("load", packages())
