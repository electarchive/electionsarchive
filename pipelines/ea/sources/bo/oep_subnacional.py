"""Bolivia's subnational elections of 2010, 2015, 2021, and 2026 by polling table (mesa).

The Órgano Electoral Plurinacional publishes the count of every polling table. The OEP's digital Atlas Electoral
holds the municipal elections of 2010, 2015, and 2021, and its computation site holds the subnational election of
2026 (22 March, with the runoffs of 19 April). Neither host answers from outside Bolivia, so this source reads copies:

- 2026, both rounds and every office, from the OEP computation site's export (`api/v1/descargar`) as scraped by
  Mauricio Foronda into a public repository (`votos` and `participación` files for each department and office);
- the mayors and councils of 2010, 2015, and 2021 from the same repository's long-format copies of the Atlas's
  `votos_totales.csv` files (Atlas sub-processes 25, 26, 32, 120, and 121);
- the 2021 departmental assembly by population from the Atlas's `votos_totales.csv` of sub-process 112, read from a
  web.archive.org snapshot kept in the archive's inbox (`EA_INBOX_URL`, `EA_INBOX_USER`, `EA_INBOX_PASSWORD`).

Each polling table is a reporting unit inside its municipality. A contest is the vote for one office or body in one
place: a department for the governor and the population assembly, a province for the territory assembly and the
sub-governor, and a municipality for the mayor, the council, and the corregidor. The files name no candidates and
declare no winners, so the packages hold counts only.
"""

import csv
import functools
import os
from collections import defaultdict
from datetime import date, datetime
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.bo import oep_generales, oep_referendos
from ea.sources.bo.municipal_places import title
from ea.sources.bo.places import NAMES as DEPARTMENT_NAMES
from ea.sources.bo.places import fold

SOURCE = "bo.oep_subnacional"
BOLIVIA = oep_generales.BOLIVIA
COUNTRY = oep_generales.COUNTRY
MESA, MUNICIPALITY, DEPARTMENT, PROVINCE = (
    oep_generales.MESA,
    oep_generales.MUNICIPALITY,
    "bo.departamento",
    "bo.provincia",
)
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
COMMIT = "cd4330f979e4acb54d8e17d9fd7d6a68d2ffd81f"
REPO = f"https://raw.githubusercontent.com/mauforonda/subnacionales2026/{COMMIT}/"
REPO_PAGE = "https://github.com/mauforonda/subnacionales2026"
COMPUTO = "https://computo.oep.org.bo/"
ATLAS = "https://atlaselectoral.oep.org.bo/"
WAYBACK_112 = (
    "https://web.archive.org/web/20260824174031id_/https://atlaselectoral.oep.org.bo/descarga/112/votos_totales.csv"
)
FETCHED = "/fetched/4674/"
INBOX_FILE = "5d53e16ef07d11f6dfbd45ebe3a241d1674027c3da2c7ede82a9535ab08e0298.csv"
TOWNS = oep_generales.towns
MACHA = "050405"  # San Pedro de Macha, which the municipal tables use and the referendum source does not hold

DAYS = (date(2010, 4, 4), date(2015, 3, 29), date(2021, 3, 7), date(2026, 3, 22))
RUNOFFS = {date(2026, 3, 22): date(2026, 4, 19)}
DEPARTMENTS = {
    "chuquisaca": 1,
    "la-paz": 2,
    "cochabamba": 3,
    "oruro": 4,
    "potosi": 5,
    "tarija": 6,
    "santa-cruz": 7,
    "beni": 8,
    "pando": 9,
}
# The offices of the 2026 export: the folder name, and the office it holds.
CARGOS = {
    "gobernador-a": "governor",
    "asambleista-poblacion": "population",
    "asambleista-territorio": "territory",
    "subgobernador-a": "subgovernor",
    "corregidor-a": "corregidor",
    "alcaldesaalcalde": "mayor",
    "concejales": "council",
    "ejecutivo-a-regional": "regional",
    "ejecutivo-a-desarrollo": "development",
    "asambleista-regional-poblacion": "regional_population",
    "asambleista-regional-territorio": "regional_territory",
}
COMMON = ("gobernador-a", "asambleista-poblacion", "asambleista-territorio", "alcaldesaalcalde", "concejales")
EXTRA_CARGOS = {
    "beni": ("subgobernador-a", "corregidor-a"),
    "tarija": (
        "ejecutivo-a-regional",
        "ejecutivo-a-desarrollo",
        "asambleista-regional-poblacion",
        "asambleista-regional-territorio",
    ),
}
# Beni elects no assembly members by population.
FIRST_ROUND = {
    dept: tuple(c for c in COMMON if (dept, c) != ("beni", "asambleista-poblacion")) + EXTRA_CARGOS.get(dept, ())
    for dept in DEPARTMENTS
}
SECOND_ROUND = {
    "beni": ("gobernador-a",),
    "chuquisaca": ("gobernador-a",),
    "oruro": ("asambleista-territorio", "gobernador-a"),
    "santa-cruz": ("gobernador-a",),
    "tarija": ("gobernador-a",),
}
HISTORIC = {
    date(2010, 4, 4): {"mayor": "2010/elección_de_alcaldes_2010", "council": "2010/elección_de_concejales_2010"},
    date(2015, 3, 29): {"mayor": "2015/elección_de_alcaldes_2015"},
    date(2021, 3, 7): {"mayor": "2021/elección_de_alcaldes_2021", "council": "2021/elección_de_concejales_2021"},
}
ACCOUNTS = ("VALIDOS", "BLANCOS", "NULOS", "EMITIDOS", "INSCRITOS")
LABELS = {  # the names of the offices and bodies, in English and Spanish, with the place they are held in
    "governor": ("Governor of {}", "Gobernación de {}"),
    "population": ("Departmental Assembly of {}, seats by population", "Asamblea Departamental de {}, por población"),
    "territory": ("Departmental Assembly of {}, seats by territory", "Asamblea Departamental de {}, por territorio"),
    "subgovernor": ("Sub-governor of {}", "Subgobernación de {}"),
    "corregidor": ("Corregidor of {}", "Corregimiento de {}"),
    "mayor": ("Mayor of {}", "Alcaldía de {}"),
    "council": ("Municipal Council of {}", "Concejo Municipal de {}"),
    "regional": ("Regional Executive of {}", "Ejecutivo Regional de {}"),
    "development": ("Development Executive of {}", "Ejecutivo de Desarrollo de {}"),
    "regional_population": ("Regional Assembly of {}, seats by population", "Asamblea Regional de {}, por población"),
    "regional_territory": ("Regional Assembly of {}, seats by territory", "Asamblea Regional de {}, por territorio"),
}
SINGLE = {"governor", "subgovernor", "corregidor", "mayor", "regional", "development"}
BODIES = {"population", "territory", "council", "regional_population", "regional_territory"}
GRAN_CHACO = "Gran Chaco"
METHOD = (
    "Counts of each polling table (mesa). Valid votes are the votes for the options the table lists; blank and null "
    "ballots are counted apart, and the ballots cast are the sum of the three. The options are named by the "
    "abbreviation of their party or alliance, as the files name them; the archive holds one option for each that took "
    "at least one vote in the contest. The files name no candidates and declare no winners, so the package declares "
    "no outcome or seat. {extra}"
)
EXTRA = {
    "computo": (
        "The counts are the OEP computation site's export for each department and office, as scraped by Mauricio "
        "Foronda. A department's territory assembly and sub-governor contests are held by province, so the archive "
        "groups the tables by their province."
    ),
    "atlas": (
        "The counts are the Atlas Electoral's votos_totales file of the process, as copied into the same public "
        "repository in long format."
    ),
    "wayback": (
        "The counts are the Atlas Electoral's votos_totales file of sub-process 112, read from a web.archive.org "
        "snapshot."
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party(label: str) -> str:
    return key("party", fold(label))


COMPUTO_ID = key("source", "computo")
ATLAS_ID = key("source", "atlas")


def url_of(*parts: str) -> str:
    return REPO + "/".join(quote(part) for part in parts)


def historic_url(name: str) -> str:
    return REPO + quote(f"resultados_historicos/{name}.parquet")


def export_urls(round_number: int, dept: str, cargo: str) -> tuple[str, str]:
    base = f"resultados/{'primera' if round_number == 1 else 'segunda'}_vuelta/{dept}/{cargo}/"
    return REPO + base + "validos.csv", REPO + base + "participacion.csv"


def all_urls() -> list[str]:
    out = []
    for dept, cargos in FIRST_ROUND.items():
        out += [u for c in cargos for u in export_urls(1, dept, c)]
    for dept, cargos in SECOND_ROUND.items():
        out += [u for c in cargos for u in export_urls(2, dept, c)]
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def fetch() -> None:
    for url in all_urls():
        raw.fetch(SOURCE, url, suffix=".csv")
    for names in HISTORIC.values():
        for name in names.values():
            raw.fetch(SOURCE, historic_url(name), suffix=".parquet")
    if WAYBACK_112 in raw.keys(SOURCE):
        return
    base = os.environ["EA_INBOX_URL"] + FETCHED
    auth = (os.environ["EA_INBOX_USER"], os.environ["EA_INBOX_PASSWORD"])
    index = httpx.get(base + "index.json", auth=auth, timeout=60).raise_for_status().json()
    entry = index[WAYBACK_112]
    content = httpx.get(base + entry["file"], auth=auth, timeout=600).raise_for_status().content
    stored = raw.store(
        SOURCE,
        entry.get("source", WAYBACK_112),
        content,
        suffix=".csv",
        retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
        media_type=entry["media_type"],
    )
    if stored.sha256 != entry["sha256"]:
        raise ValueError("the inbox file of sub-process 112 does not match its hash")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


# ---- places --------------------------------------------------------------------------------------------------------


ETA = "bo.eta"
# The tables that the Atlas and the 2026 export number like municipalities but that are not in the referendum source's
# municipalities: the indigenous territories and one municipality, with the scheme, the area code, the name, and the
# department. Raqaypampa is an area the referendum source holds.
OTHER_AREAS = {
    MACHA: (MUNICIPALITY, "5:san-pedro-de-macha", "San Pedro de Macha", 5),
    "031304": (ETA, "Raqaypampa", "Raqaypampa", 3),
    "051204": (ETA, "Jatun Ayllu Yura", "Jatun Ayllu Yura", 5),
    "080502": (ETA, "Territorio Indígena Multiétnico", "Territorio Indígena Multiétnico", 8),
}
HELD = {"031304"}  # the areas of OTHER_AREAS that the referendum source already holds


@functools.cache
def towns() -> dict[str, tuple[str, str]]:
    """The area code and the name of each municipality, by the six-digit code of the Atlas."""
    with oep_generales.TOWNS.open(encoding="utf-8", newline="") as file:
        out = {r["atlas_code"]: (r["area_code"], title(r["name"])) for r in csv.DictReader(file, delimiter="\t")}
    out.update({code: (area_code, name) for code, (_, area_code, name, _) in OTHER_AREAS.items()})
    return out


def town_area(code: str) -> str:
    scheme = OTHER_AREAS[code][0] if code in OTHER_AREAS else MUNICIPALITY
    return oep_referendos.area(scheme, towns()[code][0])


def department_area(dept: int | str) -> str:
    return oep_referendos.area(DEPARTMENT, str(int(dept)))


def province_area(code: str) -> str:
    return key("area", PROVINCE, code)


def slug_of(code: str) -> str:
    return towns()[code][0].split(":", 1)[1]


# ---- reading the tables --------------------------------------------------------------------------------------------


class Mesa(NamedTuple):
    code: str
    place: str
    dept: int
    province: str  # four digits
    town: str  # six digits
    counts: dict[str, int]
    registered: int | None
    cast: int
    valid: int
    blank: int
    null: int


class Block(NamedTuple):
    day: date
    cargo: str
    method: str  # computo, atlas, or wayback
    files: tuple[str, ...]
    mesas: tuple[Mesa, ...]


PROVINCE_NAMES: dict[str, str] = {}
PROBLEMS: dict[tuple[date, str], list[str]] = defaultdict(list)


def number(text: object) -> int:
    return 0 if text is None or str(text).strip() == "" else int(float(str(text)))


def check_mesa(block: tuple[date, str], mesa: Mesa) -> None:
    problems = PROBLEMS[block]
    if sum(mesa.counts.values()) != mesa.valid:
        problems.append(f"valid:{mesa.code}")
    if mesa.valid + mesa.blank + mesa.null != mesa.cast:
        problems.append(f"cast:{mesa.code}")
    if mesa.registered is not None and mesa.cast > mesa.registered:
        problems.append(f"over:{mesa.code}")


@functools.cache
def export(round_number: int, dept: str, cargo: str) -> tuple[Mesa, ...]:
    day = DAYS[-1] if round_number == 1 else RUNOFFS[DAYS[-1]]
    votes_url, turnout_url = export_urls(round_number, dept, cargo)
    votes = pl.read_csv(raw.cached(SOURCE, votes_url).path, infer_schema_length=0)
    turnout = pl.read_csv(raw.cached(SOURCE, turnout_url).path, infer_schema_length=0)
    labels = votes.columns[10:]
    by_mesa = {row["CodigoMesa"]: row for row in turnout.iter_rows(named=True)}
    if len(by_mesa) != turnout.height or set(by_mesa) != set(votes["CodigoMesa"]):
        raise ValueError(f"{dept} {cargo}: the vote and turnout files hold different tables")
    out = []
    for row in votes.iter_rows(named=True):
        t = by_mesa[row["CodigoMesa"]]
        province = f"{DEPARTMENTS[dept]:02d}{int(row['CodigoProvincia']):02d}"
        PROVINCE_NAMES[province] = row["NombreProvincia"]
        town = f"{province}{int(row['CodigoSeccion']):02d}"
        counts = {label: number(row[label]) for label in labels}
        mesa = Mesa(
            code=row["CodigoMesa"][:-1],  # the last digit tells the ballot of the table, not the table
            place=row["CodigoRecinto"],
            dept=DEPARTMENTS[dept],
            province=province,
            town=town,
            counts={k: v for k, v in counts.items()},
            registered=number(t["InscritosHabilitados"]),
            cast=number(t["VotoEmitido"]),
            valid=number(t["VotoValido"]),
            blank=number(t["VotoBlanco"]),
            null=number(t["VotoNulo"]),
        )
        check_mesa((day, cargo), mesa)
        out.append(mesa)
    if len({(m.town, m.place, m.code) for m in out}) != len(out):
        raise ValueError(f"{dept} {cargo}: a polling table twice")
    return tuple(out)


@functools.cache
def historic(name: str, day: date, cargo: str) -> tuple[Mesa, ...]:
    frame = pl.read_parquet(raw.cached(SOURCE, historic_url(name)).path)
    keys = ["departamento", "provincia", "municipio", "recinto", "mesa"]
    accounts = (
        frame.filter(pl.col("opcion").is_in(ACCOUNTS))
        .pivot(on="opcion", index=keys, values="conteo", aggregate_function="sum")
        .sort(keys)
    )
    columns = {c: pl.col(c) if c in accounts.columns else pl.lit(None, pl.Int64) for c in ACCOUNTS}
    accounts = accounts.select(*keys, **columns)
    options: dict[tuple[int, ...], dict[str, int]] = defaultdict(dict)
    for dept, province, town, place, mesa, label, count in (
        frame.filter(~pl.col("opcion").is_in(ACCOUNTS) & (pl.col("conteo") > 0))
        .select(*keys, "opcion", "conteo")
        .iter_rows()
    ):
        options[(dept, province, town, place, mesa)][label] = count
    out = []
    seen = set()
    for dept, province, town, place, mesa, valid, blank, null, cast, registered in accounts.iter_rows():
        index = (dept, province, town, place, mesa)
        code = f"{town}:{place}:{mesa}"
        if code in seen:
            raise ValueError(f"{name}: table {code} twice")
        seen.add(code)
        province_code = f"{int(province):04d}"
        PROVINCE_NAMES.setdefault(province_code, "")
        row = Mesa(
            code=str(mesa)[:-1] if day.year == 2021 else str(mesa),
            place=str(int(place)),
            dept=int(dept),
            province=province_code,
            town=f"{int(town):06d}",
            counts=dict(options.get(index, {})),
            registered=registered,
            cast=cast,
            valid=valid,
            blank=blank,
            null=null,
        )
        check_mesa((day, cargo), row)
        out.append(row)
    return tuple(out)


@functools.cache
def wayback() -> tuple[Mesa, ...]:
    text = raw.cached(SOURCE, WAYBACK_112).path.read_text(encoding="utf-8-sig")
    reader = csv.reader(text.splitlines(), delimiter="|", quoting=csv.QUOTE_NONE)
    header = next(reader)
    fixed = {c for c in header if c.startswith(("Codigo ", "Nombre "))} | set(ACCOUNTS)
    labels = [c for c in header if c not in fixed]
    out = []
    day = date(2021, 3, 7)
    for row in reader:
        cells = dict(zip(header, row, strict=True))
        mesa = Mesa(
            code=cells["Codigo MESA"][:-1],
            place=str(int(cells["Codigo RECINTO"])),
            dept=int(cells["Codigo DEPARTAMENTO"]),
            province=cells["Codigo PROVINCIA"],
            town=cells["Codigo MUNICIPIO"],
            counts={label: int(cells[label]) for label in labels if int(cells[label])},
            registered=int(cells["INSCRITOS"]),
            cast=int(cells["EMITIDOS"]),
            valid=int(cells["VALIDOS"]),
            blank=int(cells["BLANCOS"]),
            null=int(cells["NULOS"]),
        )
        PROVINCE_NAMES[mesa.province] = cells["Nombre PROVINCIA"]
        check_mesa((day, "population"), mesa)
        out.append(mesa)
    return tuple(out)


def blocks(day: date, round_number: int = 1) -> list[Block]:
    if day.year == 2026 or round_number == 2:
        table = FIRST_ROUND if round_number == 1 else SECOND_ROUND
        return [
            Block(
                day if round_number == 1 else RUNOFFS[DAYS[-1]],
                CARGOS[cargo],
                "computo",
                export_urls(round_number, dept, cargo),
                export(round_number, dept, cargo),
            )
            for dept, cargos in table.items()
            for cargo in cargos
        ]
    out = [
        Block(day, cargo, "atlas", (historic_url(name),), historic(name, day, cargo))
        for cargo, name in HISTORIC[day].items()
    ]
    if day.year == 2021:
        out.append(Block(day, "population", "wayback", (WAYBACK_112,), wayback()))
    return out


def merged(day: date, round_number: int = 1) -> dict[str, list[Mesa]]:
    """The tables of each office, merged across departments."""
    out: dict[str, list[Mesa]] = defaultdict(list)
    for block in blocks(day, round_number):
        out[block.cargo] += block.mesas
    return out


def group_of(cargo: str, mesa: Mesa) -> str:
    if cargo in ("mayor", "council", "corregidor", "development"):
        return mesa.town
    if cargo in ("territory", "subgovernor"):
        return mesa.province
    if cargo in ("regional", "regional_population", "regional_territory"):
        return "gran-chaco"
    return f"{mesa.dept:02d}"


def used_files(day: date) -> list[str]:
    out = []
    for round_number in (1, 2):
        if round_number == 2 and day not in RUNOFFS:
            continue
        for block in blocks(day, round_number):
            out += block.files
    return list(dict.fromkeys(out))


# ---- packages ------------------------------------------------------------------------------------------------------


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=COMPUTO_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Cómputo electoral de las elecciones subnacionales 2026 (api/v1/descargar), as scraped by Mauricio Foronda",
        url=REPO_PAGE,
        license=oep_referendos.LICENSE,
        attribution=oep_referendos.ATTRIBUTION,
    )
    p.add(
        "source",
        id=ATLAS_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Atlas Electoral de Bolivia (digital), counts by polling table",
        url=ATLAS,
        license=oep_referendos.LICENSE,
        attribution=oep_referendos.ATTRIBUTION,
    )


def source_of(method: str) -> str:
    return COMPUTO_ID if method == "computo" else ATLAS_ID


def all_blocks() -> list[Block]:
    out = []
    for day in DAYS:
        out += blocks(day)
        if day in RUNOFFS:
            out += blocks(day, 2)
    return out


@functools.cache
def province_names() -> dict[str, str]:
    """The name of each province, from the files that print it."""
    for block in all_blocks():
        for mesa in block.mesas:
            PROVINCE_NAMES.setdefault(mesa.province, "")
    names = {code: name for code, name in PROVINCE_NAMES.items() if name}
    frame = pl.read_parquet(
        raw.cached(SOURCE, historic_url(HISTORIC[date(2021, 3, 7)]["mayor"])).path,
        columns=["provincia", "provincia_nombre"],
    ).unique()
    for code, name in frame.iter_rows():
        names.setdefault(f"{int(code):04d}", name)
    return {code: title(name) for code, name in sorted(names.items())}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The provinces of Bolivia, San Pedro de Macha, and two indigenous territories, as the Atlas Electoral and the 2026 computation export "
        "number them",
    )
    p.external |= {BOLIVIA, COUNTRY, MESA, MUNICIPALITY, DEPARTMENT, ETA}
    p.external |= {department_area(d) for d in DEPARTMENTS.values()}
    p.add("scheme", code=PROVINCE, polity_id=BOLIVIA, kind="administrative", rank=1, noun="province")
    for code, name in province_names().items():
        id = province_area(code)
        p.add("area", id=id, scheme_code=PROVINCE)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=department_area(int(code[:2])))
        p.name(id, name, "es", "official")
    for code, (scheme, area_code, name, dept) in OTHER_AREAS.items():
        if code in HELD:
            continue
        id = town_area(code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=area_code)
        p.add("area_parent", area_id=id, parent_id=department_area(dept))
        p.name(id, name, "es", "official")
    return p


def office_id(cargo: str, group: str) -> str:
    return key("office", cargo, group)


def body_id(cargo: str, group: str) -> str:
    return key("body", "assembly" if cargo in ("population", "territory") else cargo, group)


def place_name(cargo: str, group: str) -> str:
    if cargo in ("mayor", "council", "corregidor", "development"):
        return towns()[group][1]
    if cargo in ("territory", "subgovernor"):
        return province_names()[group]
    if group == "gran-chaco":
        return GRAN_CHACO
    return DEPARTMENT_NAMES[str(int(group))]


def combos() -> dict[tuple[str, str], str]:
    """Each office or body the packages use, by cargo and group, with the area it is held in (for the gobernador a
    department, for the assemblies the department too)."""
    out: dict[tuple[str, str], str] = {}
    for block in all_blocks():
        for mesa in block.mesas:
            out[(block.cargo, group_of(block.cargo, mesa))] = ""
    return out


def head(cargo: str) -> str:
    return "bodies" if cargo in BODIES else "offices"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The subnational offices and bodies of Bolivia and the lists of the elections of 2010, 2015, 2021, and 2026, "
        "from the Atlas Electoral and the OEP computation export",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BOLIVIA)
    _sources(p)
    labels: set[str] = set()
    made: set[str] = set()
    for block in all_blocks():
        for mesa in block.mesas:
            labels |= mesa.counts.keys()
    for cargo, group in sorted(combos()):
        en, es = LABELS[cargo]
        place = place_name(cargo, group)
        if cargo in BODIES:
            kind_cargo = "assembly" if cargo in ("population", "territory") else cargo
            id = key("body", kind_cargo, group)
            if id in made:
                continue
            made.add(id)
            p.add("body", id=id, polity_id=BOLIVIA, kind="council" if cargo == "council" else "unicameral")
            if cargo in ("population", "territory"):
                p.name(id, f"Departmental Assembly of {place}", "en", "official")
                p.name(id, f"Asamblea Departamental de {place}", "es", "native", False)
            elif cargo == "council":
                p.name(id, f"Municipal Council of {place}", "en", "official")
                p.name(id, f"Concejo Municipal de {place}", "es", "native", False)
            else:
                p.name(id, f"Regional Assembly of {place}", "en", "official")
                p.name(id, f"Asamblea Regional de {place}", "es", "native", False)
        else:
            id = office_id(cargo, group)
            kind = {"governor": "governor", "mayor": "mayor"}.get(cargo, "other")
            p.add("office", id=id, polity_id=BOLIVIA, kind=kind)
            p.name(id, en.format(place), "en", "official")
            p.name(id, es.format(place), "es", "native", False)
    for label in sorted(labels, key=fold):
        p.add("party", id=party(label), kind="party", polity_id=BOLIVIA)
        p.name(party(label), label, "es", "abbreviation")
    for url in dict.fromkeys(url for block in all_blocks() for url in block.files):
        p.artifact(raw.cached(SOURCE, url), COMPUTO_ID if url.startswith(REPO + "resultados/") else ATLAS_ID)
    return p


def election(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} subnational elections of Bolivia by polling table"
        + (", first round and runoff" if day in RUNOFFS else ""),
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {BOLIVIA, COUNTRY, MESA, MUNICIPALITY, DEPARTMENT}
    p.external |= {department_area(d) for d in DEPARTMENTS.values()}
    p.external |= {town_area(code) for code in towns() if code not in OTHER_AREAS or code in HELD}
    for url in used_files(day):
        p.uses(raw.cached(SOURCE, url))
    values: list[tuple[str, str, str | None, int, int]] = []
    units: set[str] = set()
    election_ids: dict[int, str] = {}
    for round_number in (1, 2):
        if round_number == 2 and day not in RUNOFFS:
            continue
        held = day if round_number == 1 else RUNOFFS[day]
        election_id = election_ids[round_number] = key("election", held)
        base = f"bo/{held}-subnational" + ("-runoff" if round_number == 2 else "")
        p.add(
            "election",
            id=election_id,
            polity_id=BOLIVIA,
            kind="local" if round_number == 1 else "runoff",
            date_from=held,
            date_to=held,
            date_precision="day",
            status="held",
            parent_id=election_ids.get(1) if round_number == 2 else None,
        )
        en = f"{held.year} Bolivian subnational election" + (" runoff" if round_number == 2 else "")
        es = f"Elecciones subnacionales {held.year}" + (", segunda vuelta" if round_number == 2 else "")
        p.name(election_id, en, "en", "official")
        p.name(election_id, es, "es", "native", False)
        p.add("slug", path=base, entity_id=election_id, is_current=True)
        for block in blocks(day, round_number):
            _contests(p, block, election_id, base, round_number, values, units, election_ids.get(1))
    for (block_day, cargo), problems in sorted(PROBLEMS.items()):
        if block_day not in (day, RUNOFFS.get(day)) or not problems:
            continue
        for code, name in (
            ("valid", "valid votes that differ from the votes for the options"),
            ("cast", "ballots cast that differ from the sum of valid, blank, and null ballots"),
            ("over", "ballots cast above the registered voters"),
        ):
            bad = sorted(x.split(":", 1)[1] for x in problems if x.startswith(code + ":"))
            if bad:
                p.accept(
                    "accounting.turnout" if code == "over" else "accounting.ballots",
                    f"The {cargo} file of {block_day} has {len(bad)} polling tables with {name}, among them "
                    + ", ".join(bad[:6])
                    + "; the figures are stored as published.",
                )
    p.frame(
        "result_value",
        pl.DataFrame(
            values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p


def _contests(
    p: Package,
    block: Block,
    election_id: str,
    base: str,
    round_number: int,
    values: list[tuple[str, str, str | None, int, int]],
    units: set[str],
    first_election: str | None,
) -> None:
    cargo = block.cargo
    groups: dict[str, list[Mesa]] = defaultdict(list)
    for mesa in block.mesas:
        groups[group_of(cargo, mesa)].append(mesa)
    en, es = LABELS[cargo]
    for group, members in sorted(groups.items()):
        if not any(m.valid or m.blank or m.null for m in members):
            continue
        shown = sorted({label for m in members for label, n in m.counts.items() if n}, key=fold)
        place = place_name(cargo, group)
        tag = f"{election_id}:{cargo}:{group}"
        contest = key("contest", tag)
        if cargo in BODIES:
            holder = {"body_id": body_id(cargo, group)}
            kind = "body_seats"
        else:
            holder = {"office_id": office_id(cargo, group)}
            kind = "office"
        district = (
            town_area(group)
            if cargo in ("mayor", "council", "corregidor", "development")
            else province_area(group)
            if cargo in ("territory", "subgovernor")
            else department_area(group)
            if group != "gran-chaco"
            else oep_referendos.area("bo.eta", GRAN_CHACO)
        )
        if group == "gran-chaco":
            p.external.add(district)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind=kind,
            district_area_id=district,
            magnitude=1 if cargo in SINGLE else None,
            round_number=round_number,
            previous_round_id=(
                key("contest", f"{first_election}:{cargo}:{group}") if round_number == 2 and first_election else None
            ),
            default_result_kind="counted",
            **holder,
        )
        p.name(contest, en.format(place), "en", "official")
        p.name(contest, es.format(place), "es", "native", False)
        dept = DEPARTMENT_NAMES[str(members[0].dept)]
        suffix = f"{fold(dept).replace(' ', '-')}/{cargo.replace('_', '-')}/{slug_of(group) if len(group) == 6 else fold(place).replace(' ', '-')}"
        p.add("slug", path=f"{base}/{suffix}", entity_id=contest, is_current=True)
        options = {}
        for position, label in enumerate(shown, 1):
            options[label] = key("option", tag, label)
            p.add(
                "contest_option",
                id=options[label],
                contest_id=contest,
                kind="ticket" if cargo == "governor" else ("candidate" if cargo in SINGLE else "list"),
                party_id=party(label),
                ballot_position=position,
                ballot_label=label,
            )
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_of(block.method),
            status="active",
            is_default=True,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(extra=EXTRA[block.method]),
            lang="en",
        )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=source_of(block.method),
            locator={"files": list(block.files)},
        )
        for mesa in members:
            code = f"{DAY_OF[election_id]}:{mesa.town}:{mesa.place}:{mesa.code}"
            unit = key("unit", MESA, code)
            if unit not in units:
                units.add(unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=BOLIVIA,
                    scheme_code=MESA,
                    code=code,
                    area_relation="within",
                    area_id=town_area(mesa.town),
                    kind="standard",
                )
            values += [(result_set, unit, options[label], VOTES, mesa.counts.get(label, 0)) for label in shown]
            values += [
                (result_set, unit, None, BALLOTS, mesa.cast),
                (result_set, unit, None, VALID, mesa.valid),
                (result_set, unit, None, INVALID, mesa.null),
                (result_set, unit, None, BLANK, mesa.blank),
            ]
            if mesa.registered is not None:
                values.append((result_set, unit, None, REGISTERED, mesa.registered))


DAY_OF = {key("election", d): d for d in (*DAYS, *RUNOFFS.values())}
