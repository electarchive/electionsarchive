"""Cortes elections of the Second Republic in Álava, Bizkaia, and Gipuzkoa, from the Basque Government's files.

The Departamento de Seguridad of the Basque Government publishes the results of the general elections of 28 June 1931,
19 November 1933, and 16 February 1936 (with a second round on 1 March 1936) for the constituencies in the three Basque
provinces: Álava, Gipuzkoa, and Bizkaia, which was split into a capital and a provincial constituency. For each
constituency it gives every candidate's votes by municipality and by electoral district and polling section (CSV files),
and a page of constituency totals with the candidates' full names and parties and the elected candidates in bold.

The files rest on the provincial official bulletins. Voters marked fewer candidates than there were seats, so
candidate votes do not add up to ballots. The CSV files give no valid-ballot totals, and only Álava's give registered
voters. The package holds the polling-section file's counts as the facts (the municipal file is the same counts summed),
the elected candidates from the constituency page, and the bulletin's printed totals as declared totals.

The 1931-1936 municipalities are their own areas: some were later merged or renamed, and no official crosswalk to
today's municipality codes exists.
"""

import csv
import html
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "es.euskadi_cortes"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
PUBLISHER = "Gobierno Vasco, Departamento de Seguridad"
TITLE = "Resultados Segunda República: elecciones a Cortes 1931, 1933 y 1936"
SITE = "https://hauteskundeak.euskadi.eus/"
PAGE = SITE + "webelec00-content/es/contenidos/informacion/w_em_contexto_historico_{year}/es_def/index.shtml"
TOTALS_PAGE = SITE + "webelec00-content/es/contenidos/informacion/w_em_result_circunsp_{year}/es_def/index.shtml"
FILE = SITE + "contenidos/informacion/w_em_contexto_historico_{year}/es_def/{folder}/csv/{kind}_{name}_c.csv"
FOLDERS = {1931: "result_1931", 1933: "result_1933_elecciones", 1936: "result_1936"}
DAYS = {1931: date(1931, 6, 28), 1933: date(1933, 11, 19), 1936: date(1936, 2, 16)}
SECOND_ROUND = date(1936, 3, 1)

PROVINCES = {
    "araba": ("01", "06aa63b2-1ccb-5884-a0fc-4e8948ca924d", "Álava"),
    "gipuzkoa": ("20", "166f0c18-3da9-59db-8117-923393a65ef1", "Gipuzkoa"),
    "bizkaia": ("48", "c2280933-72b9-5ceb-a8e2-368651361c0e", "Bizkaia"),
}
MUNICIPIO = "es.euskadi_cortes_municipio"
CIRCUNSCRIPCION = "es.euskadi_cortes_circunscripcion"
SECCION = "es.euskadi_cortes_seccion"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


@dataclass(frozen=True)
class Constituency:
    key: str
    province: str
    name: str
    english: str
    heading: str


CONSTITUENCIES = {
    c.key: c
    for c in (
        Constituency("araba", "araba", "Álava", "Álava", "ALAVA"),
        Constituency("bizkaia-capital", "bizkaia", "Vizcaya (capital)", "Bizkaia, capital", "VIZCAYA CAPITAL"),
        Constituency("bizkaia-provincia", "bizkaia", "Vizcaya (provincia)", "Bizkaia, province", "VIZCAYA PROVINCIA"),
        Constituency("gipuzkoa", "gipuzkoa", "Guipúzcoa", "Gipuzkoa", "GUIPUZCOA"),
    )
}
# The file name part for each constituency and round: (year, constituency, round) -> name inside `mesas_{}_{year}_c.csv`.
ROUNDS: dict[tuple[int, str, int], str] = {
    **{(y, "araba", 1): f"araba_{y}" for y in (1931, 1933)},
    **{(y, "bizkaia-capital", 1): f"bizkaia_capital_{y}" for y in (1931, 1933)},
    **{(y, "bizkaia-provincia", 1): f"bizkaia_provincia_{y}" for y in (1931, 1933)},
    **{(y, "gipuzkoa", 1): f"gipuzkoa_{y}" for y in (1931, 1933)},
    (1936, "araba", 1): "araba_1936_feb",
    (1936, "araba", 2): "araba_1936_mar",
    (1936, "bizkaia-capital", 1): "bizkaia_capital_1936_feb",
    (1936, "bizkaia-provincia", 1): "bizkaia_provincia_1936_feb",
    (1936, "bizkaia-provincia", 2): "bizkaia_1936_mar",
    (1936, "gipuzkoa", 1): "gipuzkoa_1936_feb",
    (1936, "gipuzkoa", 2): "gipuzkoa_1936_mar",
}

# The affiliation labels the headers print, folded, with the party each stands for. A label that names a group of
# supporters rather than an organized party (a "Catholic independent") is kept as a nuance.
PARTIES: dict[str, tuple[str, str, str]] = {
    "pnv": ("pnv", "Partido Nacionalista Vasco", "party"),
    "psoe": ("psoe", "Partido Socialista Obrero Español", "party"),
    "pce": ("pce", "Partido Comunista de España", "party"),
    "anv": ("anv", "Acción Nacionalista Vasca", "party"),
    "prrs": ("prrs", "Partido Republicano Radical Socialista", "party"),
    "prrsi": ("prrsi", "Partido Republicano Radical Socialista Independiente", "party"),
    "prr": ("prr", "Partido Republicano Radical", "party"),
    "prf": ("prf", "Partido Republicano Federal", "party"),
    "crs": ("crs", "Conjunción Republicano Socialista", "party"),
    "ceda": ("ceda", "Confederación Española de Derechas Autónomas", "party"),
    "ren esp": ("ren-esp", "Renovación Española", "party"),
    "com trad": ("ctrad", "Comunión Tradicionalista", "party"),
    "com tra": ("ctrad", "Comunión Tradicionalista", "party"),
    "ctrad": ("ctrad", "Comunión Tradicionalista", "party"),
    "ac rep": ("ac-rep", "Acción Republicana", "party"),
    "izq rep": ("izq-rep", "Izquierda Republicana", "party"),
    "ir": ("izq-rep", "Izquierda Republicana", "party"),
    "uni rep": ("uni-rep", "Unión Republicana", "party"),
    "cat ind": ("cat-ind", "Católico independiente", "nuance"),
    "cat fuer": ("cat-fuer", "Católico fuerista", "nuance"),
    "rep aut": ("rep-aut", "Republicano autónomo", "nuance"),
    "rep dis": ("rep-dis", "Republicano disidente", "nuance"),
    "rep ind": ("rep-ind", "Republicano independiente", "nuance"),
    "mon ind": ("mon-ind", "Monárquico independiente", "nuance"),
}
# A municipality the files spell differently from one election to another, folded, with the spelling that stands.
ALIASES = {
    "arbaceggui y guerricaiz": "arbacegui y guerricaiz",
    "arrazua de vizcaya": "arrazua",
    "axpe": "axpe y marzana",
    "bedia": "vedia",
    "nendeja": "mendeja",
    "s julian de musques": "musques",
    "s s del valle": "s salvador del valle",
    "olaberria": "olaverria",
    "salinas": "salinas de leniz",
    "villafranca": "villafranca de oria",
}
# Candidates whose header the page spells too differently to match by name.
CANDIDATE_PAGE = {
    "vicente fatras": "vicentefatras",
    "adolfo glez de careaga": "adolfogonzalezdecareaga",
    "santiago mnez de las rivas": "santiagomartinezdelasrivas",
    "jose m areilza": "josemaareilza",
}
OTHERS = {"diversos y en blanco", "varios", "votos sueltos"}
BLANKS = {"en blanco", "votos en blanco"}
INVALIDS = {"votos no computables"}
NOTES = {
    1931: "The general election of 28 June 1931 chose the Constituent Cortes of the Second Republic.",
    1933: "The general election of 19 November 1933 chose the second Cortes of the Republic.",
    1936: "The general election of 16 February 1936, with a second round on 1 March 1936 in the constituencies where "
    "seats remained to be filled, chose the last Cortes of the Republic.",
}
METHOD = (
    "The Basque Government's files give each candidate's votes by polling section for the constituencies of Álava, "
    "Gipuzkoa, and Bizkaia (capital and province); the other constituencies of Spain are not in this source. Voters "
    "marked fewer candidates than there were seats, so candidate votes do not add up to ballots. The files give no "
    "valid-ballot totals; only Álava's give registered voters, and the others give the electors who voted. The "
    "bulletin's printed constituency totals are stored as declared totals and in several places differ from the sum "
    "of the sections' own figures. The elected candidates are the ones the constituency page prints in bold."
)


def uid(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def tidy(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def title(text: str) -> str:
    text = tidy(text)
    if any(c.islower() for c in text):
        return text
    small = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a", "o"}
    return " ".join(w.lower() if n and w.lower() in small else w.capitalize() for n, w in enumerate(text.split()))


def file_url(year: int, kind: str, name: str) -> str:
    return FILE.format(year=year, folder=FOLDERS[year], kind=kind, name=name)


def urls(year: int) -> list[str]:
    found = [PAGE.format(year=year), TOTALS_PAGE.format(year=year)]
    for (y, _, _), name in sorted(ROUNDS.items()):
        if y == year:
            found += [file_url(year, "mesas", name), file_url(year, "mun", name)]
    return found


def fetch() -> None:
    for year in DAYS:
        for url in urls(year):
            raw.fetch(SOURCE, url, suffix=".shtml" if url.endswith(".shtml") else ".csv")


def number(text: str) -> int | None:
    text = text.strip().replace(".", "")
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"not a count: {text!r}")
    return int(text)


@dataclass
class Column:
    kind: str  # registered, ballots, candidate, others, blank, invalid
    label: str
    name: str = ""
    party: str = ""


def column(header: str) -> Column:
    label = tidy(header)
    key = fold(label)
    if key.startswith("numero de electores de cada seccion"):
        return Column("registered", label)
    if key.startswith(
        ("numero de votos emitidos", "numero de electores que han votado", "no electores que han votado")
    ):
        return Column("ballots", label)
    if key in OTHERS:
        return Column("others", label)
    if key in BLANKS:
        return Column("blank", label)
    if key in INVALIDS:
        return Column("invalid", label)
    bare = re.sub(r"\s*\(\s*[A-Za-z0-9]+\s*\)\s*$", "", label)
    words = bare.split()
    for size in (3, 2, 1):
        if len(words) > size and fold(" ".join(words[-size:])) in PARTIES:
            return Column("candidate", label, tidy(" ".join(words[:-size])), fold(" ".join(words[-size:])))
    raise ValueError(f"unrecognized column {header!r}")


@dataclass
class Section:
    municipality: str
    shown: str
    district: str
    section: str
    values: dict[int, int | None]


@dataclass
class Round:
    year: int
    constituency: str
    number: int
    columns: list[Column]
    sections: list[Section]
    printed: list[int | None]
    shown_total: str


def municipality(cell: str, province: str) -> tuple[str, str]:
    """The folded key that identifies a municipality across elections, and the name as the file prints it."""
    shown = tidy(cell)
    key = fold(shown)
    if province == "araba" and key.startswith("vitoria"):
        key, shown = "vitoria", "Vitoria"
    return ALIASES.get(key, key), shown


def read_csv(year: int, kind: str, name: str) -> list[list[str]]:
    artifact = raw.cached(SOURCE, file_url(year, kind, name))
    with artifact.path.open(encoding="latin-1", newline="") as file:
        return list(csv.reader(file, delimiter=";"))


def read_round(year: int, constituency: str, number_: int) -> Round:
    name = ROUNDS[(year, constituency, number_)]
    province = CONSTITUENCIES[constituency].province
    rows = read_csv(year, "mesas", name)
    columns = [column(h) for h in rows[0][3:]]
    sections: list[Section] = []
    seen: set[tuple[str, str, str]] = set()
    for line in rows[1:]:
        if not any(cell.strip() for cell in line[1:]):
            continue
        key, shown = municipality(line[0], province)
        district, section = tidy(line[1]), tidy(line[2])
        if key == "vitoria":
            # The 1931 file names Vitoria's polling places in the municipality column.
            section = f"{section} ({tidy(line[0])})"
        unique = (key, fold(district), fold(section))
        if unique in seen:
            raise ValueError(f"{name}: section {unique} appears twice")
        seen.add(unique)
        sections.append(Section(key, shown, district, section, {i: number(cell) for i, cell in enumerate(line[3:])}))
    totals = [line for line in read_csv(year, "mun", name) if re.match(r"(?i)\s*total", line[0])]
    printed: list[int | None] = []
    shown_total = ""
    if totals:
        last = totals[-1]
        shown_total = tidy(last[0])
        printed = [number(cell) for cell in last[1:]]
    return Round(year, constituency, number_, columns, sections, printed, shown_total)


@dataclass
class Result:
    """One candidate row of the constituency page."""

    text: str
    votes: int
    elected: bool


def page_tables(year: int) -> dict[tuple[str, int], list[Result]]:
    """The constituency page's rows by (constituency, round)."""
    artifact = raw.cached(SOURCE, TOTALS_PAGE.format(year=year))
    text = artifact.path.read_text(encoding="latin-1")
    found: dict[tuple[str, int], list[Result]] = {}
    for table in re.findall(r"(?s)<table[^>]*>(.*?)</table>", text):
        heads = re.findall(r"(?s)<th[^>]*colspan[^>]*>(.*?)</th>", table)
        if not heads:
            continue
        head = fold(html.unescape(re.sub(r"<[^>]+>", " ", heads[0])))
        constituency = next(
            (
                key
                for key, c in CONSTITUENCIES.items()
                if head.startswith(fold(c.heading)) or (key == "araba" and head.startswith("alava"))
            ),
            None,
        )
        if constituency is None:
            continue
        number_ = 2 if "2a vuelta" in head or "2 vuelta" in head else 1
        rows = []
        for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", table):
            cells = re.findall(r"(?s)<td[^>]*>(.*?)</td>", tr)
            if len(cells) != 2:
                continue
            label = tidy(html.unescape(re.sub(r"<[^>]+>", " ", cells[0])))
            votes = number(tidy(html.unescape(re.sub(r"<[^>]+>", "", cells[1]))))
            if votes is None:
                raise ValueError(f"{year} {constituency}: no votes for {label}")
            rows.append(Result(label, votes, "<strong>" in cells[0] or "<b>" in cells[0]))
        found[(constituency, number_)] = rows
    return found


def candidate_words(text: str) -> set[str]:
    return {w for w in fold(re.sub(r"\([^)]*\)", " ", text)).split() if len(w) > 2 and w not in {"del", "los", "las"}}


UNMATCHED: list[str] = []


def match_page(r: Round, results: list[Result]) -> dict[int, Result]:
    """The page row of each candidate column, by the words of the name; a column the page lacks gets none."""
    columns = {i: c for i, c in enumerate(r.columns) if c.kind == "candidate"}
    pages = [x for x in results if fold(x.text) not in OTHERS | BLANKS | INVALIDS]
    taken: dict[int, Result] = {}
    used: set[int] = set()
    for i, c in columns.items():
        want = candidate_words(c.name)
        override = CANDIDATE_PAGE.get(fold(c.name))
        if override and not any(
            fold(re.sub(r"\([^)]*\)", " ", x.text)).replace(" ", "").startswith(override) for x in pages
        ):
            override = None
        best: tuple[float, int] | None = None
        pick = -1
        for n, x in enumerate(pages):
            if n in used:
                continue
            glued = fold(re.sub(r"\([^)]*\)", " ", x.text)).replace(" ", "")
            have = candidate_words(x.text)
            if override:
                score = 1.0 if glued.startswith(override) else 0.0
            else:
                score = sum(1 for w in want if w in have or w in glued) / len(want) if want else 0.0
            rank = (score, -len(have))
            if best is None or rank > best:
                best, pick = rank, n
        if best is None or best[0] < 0.99:
            UNMATCHED.append(f"{r.year} {r.constituency} round {r.number}: {c.name}")
            continue
        used.add(pick)
        taken[i] = pages[pick]
    leftover = [x.text for n, x in enumerate(pages) if n not in used]
    if leftover:
        UNMATCHED.append(f"{r.year} {r.constituency} round {r.number}: page rows without a column: {leftover}")
    return taken


def rounds() -> list[Round]:
    return [read_round(y, c, n) for (y, c, n) in sorted(ROUNDS)]


def party_key(label: str) -> str:
    return PARTIES[label][0]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Cortes constituencies of Bizkaia (capital and province) and the municipalities of Álava, Gipuzkoa, and "
        "Bizkaia as the Basque Government's files for the 1931, 1933, and 1936 elections name them, without boundaries",
    )
    p.external |= {SPAIN, *(row[1] for row in PROVINCES.values())}
    p.add("scheme", code=MUNICIPIO, polity_id=SPAIN, kind="administrative", rank=3, noun="municipality in 1931-1936")
    p.add(
        "scheme",
        code=CIRCUNSCRIPCION,
        polity_id=SPAIN,
        kind="electoral_district",
        rank=2,
        noun="Cortes constituency of the Second Republic",
    )
    p.add("scheme", code=SECCION, polity_id=SPAIN, kind="precinct", rank=5, noun="polling section of 1931-1936")
    for key in ("bizkaia-capital", "bizkaia-provincia"):
        c = CONSTITUENCIES[key]
        id = uid("area", CIRCUNSCRIPCION, key)
        p.add("area", id=id, scheme_code=CIRCUNSCRIPCION)
        p.add("area_code", area_id=id, code=key)
        p.add("area_parent", area_id=id, parent_id=PROVINCES[c.province][1])
        p.name(id, f"Vizcaya, {'capital' if key.endswith('capital') else 'provincia'}", "es", "official")
    names: dict[tuple[str, str], tuple[int, str]] = {}
    for r in rounds():
        province = CONSTITUENCIES[r.constituency].province
        for s in r.sections:
            if names.get((province, s.municipality), (0, ""))[0] <= r.year:
                names[(province, s.municipality)] = (r.year, title(s.shown))
    for (province, key), (_, name) in sorted(names.items()):
        id = uid("area", MUNICIPIO, province, key)
        p.add("area", id=id, scheme_code=MUNICIPIO)
        p.add("area_code", area_id=id, code=f"{PROVINCES[province][0]}:{key}")
        p.add("area_parent", area_id=id, parent_id=PROVINCES[province][1])
        p.name(id, name, "es", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Basque Government's results of the 1931, 1933, and 1936 Cortes elections as a source, the Cortes of the "
        "Second Republic, the limited-vote system, and the affiliations the files print",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(SPAIN)
    p.add(
        "source",
        id=uid("source", "results"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE + "webelec00-content/es/contenidos/informacion/w_em_republica/es_def/index.shtml",
        attribution="Origen de los datos: Gobierno Vasco, Departamento de Seguridad.",
    )
    body = uid("body", "cortes")
    p.add("body", id=body, polity_id=SPAIN, kind="unicameral")
    p.name(body, "Cortes of the Second Republic", "en", "official")
    p.name(body, "Cortes de la Segunda República", "es", "official")
    system = uid("system", "limited")
    p.add("electoral_system", id=system, polity_id=SPAIN, rules={"formula": "limited"}, rules_version=1)
    p.name(system, "Limited vote in provincial and city constituencies, with a second round", "en", "official")
    p.add(
        "note",
        id=uid("note", system),
        entity_id=system,
        kind="methodology",
        body_md=(
            "Each voter marked fewer candidates than there were seats, and the candidates with the most votes were "
            "elected. Where seats remained unfilled after the first round, a second round followed. The Basque "
            "Government's files give each candidate's votes and mark who was elected; they do not state the "
            "thresholds."
        ),
        lang="en",
    )
    seen = {key: (name, kind) for key, name, kind in PARTIES.values()}
    for key, (name, kind) in sorted(seen.items()):
        id = uid("party", key)
        p.add("party", id=id, kind=kind, polity_id=SPAIN)
        p.name(id, name, "es", "official")
        p.add("external_id", entity_id=id, scheme=f"{SOURCE}.label", value=key)
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Cortes election of {day} in the constituencies of Álava, Bizkaia, and Gipuzkoa from the Basque "
        "Government's files, by polling section, with the elected candidates",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {SPAIN, *(row[1] for row in PROVINCES.values())}
    source = uid("source", "results")
    for url in urls(year):
        p.artifact(raw.cached(SOURCE, url), source)
    last = SECOND_ROUND if year == 1936 else day
    eid = uid("election", day)
    p.add("election", id=eid, polity_id=SPAIN, kind="general", date_from=day, date_to=last, status="held")
    p.name(eid, f"{year} Spanish general election", "en", "official")
    p.name(eid, f"Elecciones generales de {year}", "es", "official")
    path = f"es/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    p.add(
        "note",
        id=uid("note", eid),
        entity_id=eid,
        kind="methodology",
        body_md=f"{NOTES[year]} {METHOD}",
        lang="en",
    )
    pages = page_tables(year)
    facts: list[dict[str, object]] = []
    units: dict[str, str] = {}
    contests: dict[tuple[str, int], str] = {}
    persons: dict[tuple[str, str], tuple[str, str]] = {}
    current = [r for r in rounds() if r.year == year]
    sections_by_municipality: dict[tuple[str, str], int] = defaultdict(int)
    for constituency, key, _, _ in {
        (r.constituency, s.municipality, s.district, s.section) for r in current for s in r.sections
    }:
        sections_by_municipality[(constituency, key)] += 1
    for r in current:
        c = CONSTITUENCIES[r.constituency]
        results = pages[(r.constituency, r.number)]
        matched = match_page(r, results)
        split = year == 1936 and r.constituency != "bizkaia-capital"
        elected_any = any(x.elected for x in results)
        seats = max(sum(x.elected for x in rows) for (k, _), rows in pages.items() if k == r.constituency)
        label = c.english + (", second round" if r.number == 2 else "")
        ckey = f"{day}:{r.constituency}:{r.number}"
        contest = uid("contest", ckey)
        contests[(r.constituency, r.number)] = contest
        area = (
            uid("area", CIRCUNSCRIPCION, r.constituency)
            if r.constituency.startswith("bizkaia")
            else PROVINCES[c.province][1]
        )
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="body_seats",
            body_id=uid("body", "cortes"),
            district_area_id=area,
            magnitude=seats,
            round_number=r.number,
            previous_round_id=contests.get((r.constituency, 1)) if r.number == 2 else None,
            electoral_system_id=uid("system", "limited"),
            default_result_kind="counted",
        )
        p.name(contest, f"Cortes of the Second Republic, {label}", "en", "official")
        p.name(
            contest,
            f"Cortes de la Segunda República, {c.name}" + (", segunda vuelta" if r.number == 2 else ""),
            "es",
            "official",
        )
        p.add(
            "slug",
            path=f"{path}/cortes/{c.key}" + ("-second-round" if r.number == 2 else ""),
            entity_id=contest,
            is_current=True,
        )
        result_set = uid("result_set", ckey)
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        options: dict[int, str] = {}
        for i, col in enumerate(r.columns):
            if col.kind == "candidate":
                page = matched.get(i)
                name = tidy(re.sub(r"\s*\([^)]*\)\s*$", "", page.text)) if page else title(col.name)
                pkey = (f"{day}:{r.constituency}", fold(col.name))
                person = uid("person", *pkey)
                if pkey not in persons or len(name) > len(persons[pkey][1]):
                    persons[pkey] = (person, name)
                option = options[i] = uid("option", ckey, fold(col.name))
                party = uid("party", party_key(col.party))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=i + 1,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=uid("candidacy", ckey, fold(col.name)),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=party,
                    is_independent=False,
                )
                if elected_any or not split:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if page and page.elected else "lost",
                    )
            elif col.kind == "others":
                option = options[i] = uid("option", ckey, fold(col.label))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others",
                    ballot_position=i + 1,
                    ballot_label=title(col.label),
                )
        for s in r.sections:
            unit_key = f"{day}:{r.constituency}:{s.municipality}:{fold(s.district)}:{fold(s.section)}"
            if unit_key not in units:
                unit = units[unit_key] = uid("unit", SECCION, unit_key)
                province = c.province
                single = sections_by_municipality[(r.constituency, s.municipality)] == 1
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=SPAIN,
                    scheme_code=SECCION,
                    code=unit_key,
                    area_relation="equals" if single else "within",
                    area_id=uid("area", MUNICIPIO, province, s.municipality),
                    kind="standard",
                )
            unit = units[unit_key]
            for i, col in enumerate(r.columns):
                value = s.values.get(i)
                if value is None:
                    continue
                measure, option = {
                    "registered": (REGISTERED, None),
                    "ballots": (BALLOTS, None),
                    "blank": (BLANK, None),
                    "invalid": (INVALID, None),
                    "candidate": (VOTES, options.get(i)),
                    "others": (VOTES, options.get(i)),
                }[col.kind]
                facts.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": measure,
                        "count_round": 0,
                        "flags": 0,
                        "value": value,
                    }
                )
        for i, col in enumerate(r.columns):
            printed = r.printed[i] if i < len(r.printed) else None
            if printed is None:
                continue
            measure, option = {
                "registered": (REGISTERED, None),
                "ballots": (BALLOTS, None),
                "blank": (BLANK, None),
                "invalid": (INVALID, None),
                "candidate": (VOTES, options.get(i)),
                "others": (VOTES, options.get(i)),
            }[col.kind]
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=printed)
    for person, shown in sorted(persons.values()):
        p.add("person", id=person)
        p.name(person, shown, "es", "official")
    p.frame("result_value", pl.DataFrame(facts, schema=package.FACTS["result_value"]))
    return p


def days() -> list[date]:
    return list(DAYS.values())


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


REASONS: dict[str, str] = {
    "accounting.declared": "The bulletin's printed constituency total differs from the sum of the polling sections' own "
    "figures for some candidates and for the ballots. Each declared value is the printed total, and each fact is the "
    "section's count; the Basque Government's total row and its constituency page agree with each other.",
    "accounting.turnout": "In a few Álava sections the file prints more votes cast than electors registered; the "
    "counts are stored as printed.",
}


def parse() -> None:
    geo().write()
    UNMATCHED.clear()
    registry().write()
    for year in DAYS:
        p = election(year)
        for code, reason in REASONS.items():
            p.accept(code, reason)
        out = p.write()
        for code, count, _sample in package.check(out):
            if code not in REASONS:
                raise ValueError(f"{p.key}: {code} ({count}) is not one the source's figures can explain")


def emit() -> None:
    package.run("load", packages())
