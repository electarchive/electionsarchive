import io
import json
import re
import zipfile
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.gt import shapes
from ea.sources.gt.shapes import fold

SOURCE = "gt.tse"
GUATEMALA = str(uuid5(package.NAMESPACE, "polity\x1fGT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGTM"))
SITE = "https://www.tse.org.gt"
TREP = "https://www.trep.gt"
DATA = "ext/jsonData_gtm2023"


class Day(NamedTuple):
    key: str
    day: date
    host: str
    cut: str
    archive: str


DAYS = (
    Day("2023-06-25", date(2023, 6, 25), "https://primeraeleccion.trep.gt", "1687736870/1688050101", "GTM-pruebas.zip"),
    Day(
        "2023-08-20",
        date(2023, 8, 20),
        "https://segundaeleccion.trep.gt",
        "1692574389/1692594771",
        "GTM2023-segundaeleccion-20230820-231251.zip",
    ),
)
FIRST, SECOND = DAYS

ABROAD = {23, 25}
"""Tables in the United States: department 23 in the first round's files, 25 in the second's."""
CENTRAL = 0
CAPITAL = (1, 1)
REGISTERED, CAST, VALID, INVALID, BLANK, VOTERS = 2, 3, 4, 5, 6, 14
ACCOUNTING = (
    (REGISTERED, "PADRÓN"),
    (CAST, "EMITIDOS_CALCULADO"),
    (VALID, "VALIDOS_CALCULADO"),
    (INVALID, "NULOS"),
    (BLANK, "BLANCO"),
    (VOTERS, "PRES-VOTAR"),
)
MONTHS: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip
COALITIONS = {"41", "57", "69", "87", "88", "104"}
SMALL = {"de", "del", "la", "las", "los", "y", "el", "en", "por", "para", "a", "al", "e", "o"}

SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1ftrep"))
LICENSE = (
    "Public information of the Tribunal Supremo Electoral of Guatemala, published under the Ley de Acceso a la "
    "Información Pública (Decreto 57-2008); reuse with the source acknowledged"
)
METHOD = (
    "Counted per polling table (junta receptora de votos, or mesa) from the Tribunal Supremo Electoral's "
    "preliminary results transmission (TREP), as published in the per-table files of its results site, the "
    "transcription of each table's Documento No. 4 at its final cut. Only the tables the TREP counted "
    "(contabilizadas) are stored; tables it did not receive, or set aside as illegible or as exceeding the roll, "
    "are left out, so the set's completeness is the share of expected tables counted. Registered voters are the "
    "table's roll (padrón); valid votes are the votes for the organizations, and ballots cast are the valid, null, "
    "and blank votes together, as the TREP computes them (válidos and emitidos calculados). Null votes are stored as "
    "invalid ballots and blank votes as blank ballots. The acta's separate count of invalid ballots (votos "
    "inválidos, ballots outside the valid, null, and blank count) and its count of challenges are not stored. An "
    "organization's cell left empty in the transcription is left out. Tables abroad, in the United States, count "
    "toward no department."
)
VOTERS_NOTE = " Voters are the citizens the table recorded as having come to vote (se presentaron a votar)."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(code: str) -> str:
    return key("party", code)


def url(d: Day, name: str) -> str:
    return f"{d.host}/{DATA}/{d.cut}/{name}"


def catalog_url(d: Day) -> str:
    return url(d, "gtm2023_datos.json")


def archive_url(d: Day) -> str:
    return url(d, d.archive)


def fetch() -> None:
    for d in DAYS:
        raw.fetch(SOURCE, catalog_url(d), suffix=".json")
        raw.fetch(SOURCE, archive_url(d), suffix=".zip")
    for outline in shapes.URLS:
        raw.fetch(SOURCE, outline, suffix=".geojson")


def title(text: str) -> str:
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(
        word if index and word in SMALL else word[:1].upper() + word[1:] for index, word in enumerate(words) if word
    )


class Sheet(NamedTuple):
    kind: str
    dep: int | None
    mun: int | None
    member: str
    as_of: datetime
    header: dict[str, str]
    parties: list[str]
    rows: pl.DataFrame


SHEET = re.compile(r"gtm2023_e(\d)(?:d(\d\d))?(?:m(\d\d))?\.csv$")
KINDS = {"1": "president", "2": "national", "3": "district", "4": "municipal", "5": "parlacen"}


def _as_of(text: str) -> datetime:
    found = re.match(r"(\d+) (\w+) (\d{4}) (\d\d):(\d\d) \(UTC ([+-]\d+)\)", text.strip())
    if found is None:
        raise ValueError(f"unreadable cut time: {text!r}")
    day, month, year, hour, minute, offset = found.groups()
    local = datetime(int(year), MONTHS[month.lower()], int(day), int(hour), int(minute))
    return (local - timedelta(hours=int(offset))).replace(tzinfo=UTC)


def _sheet(member: str, data: bytes) -> Sheet:
    found = SHEET.search(member)
    if found is None:
        raise ValueError(member)
    kind, dep, mun = found.groups()
    lines = data.decode("utf-8-sig").splitlines()
    header = dict(zip(lines[2].split(","), lines[3].split(","), strict=True))
    rows = pl.read_csv(io.StringIO("\n".join(lines[5:])), infer_schema_length=0)
    columns = rows.columns
    parties = columns[columns.index("PADRÓN") + 1 : columns.index("VÁLIDOS")]
    return Sheet(
        KINDS[kind],
        None if dep is None else int(dep),
        None if mun is None else int(mun),
        member,
        _as_of(lines[1]),
        header,
        parties,
        rows,
    )


def sheets(d: Day) -> list[Sheet]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, archive_url(d)).path)
    found = [_sheet(name, archive.read(name)) for name in archive.namelist() if SHEET.search(name)]
    return sorted(found, key=lambda s: (s.kind != "president", s.kind, s.dep or 0, s.mun or 0))


def catalog(d: Day) -> dict[str, dict[str, str]]:
    data = json.loads(raw.cached(SOURCE, catalog_url(d)).path.read_bytes())
    found: dict[str, dict[str, str]] = {}
    for kind in data["cands"].values():
        found |= kind.get("pidsInfo", {})
    return {
        str(info["codigo"]): {k: str(v) for k, v in info.items()} for info in found.values() if info["tipo"] == "PP"
    }


def organizations() -> dict[str, dict[str, str]]:
    """The organizations of both rounds' catalogs, keyed by their TSE code."""
    found: dict[str, dict[str, str]] = {}
    for d in DAYS:
        for code, info in catalog(d).items():
            if code in found and found[code]["siglas"] != info["siglas"]:
                raise ValueError(f"organization {code} is {found[code]['siglas']} and {info['siglas']}")
            found[code] = info
    return found


def by_sigla() -> dict[str, str]:
    return {fold(info["siglas"]): code for code, info in organizations().items()}


def _number(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(",", "").str.strip_chars().cast(pl.Int64, strict=False)


def counted(sheet: Sheet) -> pl.DataFrame:
    return sheet.rows.filter(pl.col("CONTABILIZADA") == "1")


def compare() -> dict[str, object]:
    """Sum each file's counted tables and check them against the totals the file and the results site print."""
    report: dict[str, object] = {}
    sigla = by_sigla()
    for d in DAYS:
        data = json.loads(raw.cached(SOURCE, catalog_url(d)).path.read_bytes())
        shown = {name: kind for name, kind in data["cands"].items()}
        problems: list[str] = []
        for sheet in sheets(d):
            rows = counted(sheet)
            label = sheet.member.rsplit("/", 1)[-1]
            sums = rows.select(*(_number(c).sum().alias(c) for c in ["PADRÓN", "EMITIDOS_CALCULADO", *sheet.parties]))
            got = sums.row(0, named=True)
            if rows.height != int(sheet.header["ACTAS_CONTABILIZADAS"]):
                problems.append(f"{label}: {rows.height} counted tables, header {sheet.header['ACTAS_CONTABILIZADAS']}")
            if got["PADRÓN"] != int(sheet.header["LISTA_NOMINAL_ACTAS_CONTABILIZADAS"]):
                problems.append(
                    f"{label}: roll {got['PADRÓN']}, header {sheet.header['LISTA_NOMINAL_ACTAS_CONTABILIZADAS']}"
                )
            if got["EMITIDOS_CALCULADO"] != int(sheet.header["VOTOS_EMITIDOS_CONTABILIZADAS"]):
                problems.append(
                    f"{label}: cast {got['EMITIDOS_CALCULADO']}, header {sheet.header['VOTOS_EMITIDOS_CONTABILIZADAS']}"
                )
            if sheet.kind in ("president", "national", "parlacen"):
                tc = {"president": "tc1", "national": "tc2", "parlacen": "tc5"}[sheet.kind]
                printed = {
                    pid.removeprefix("OP"): int(v["num"])
                    for pid, v in shown[tc]["votosPA"].items()
                    if pid.startswith("OP")
                }
                mine = {sigla[fold(c)]: int(got[c] or 0) for c in sheet.parties}
                if printed != mine:
                    diff = {
                        k: (mine.get(k), printed.get(k))
                        for k in set(mine) | set(printed)
                        if mine.get(k) != printed.get(k)
                    }
                    problems.append(f"{label}: organizations differ from the results page: {diff}")
                report[f"{d.key} {sheet.kind}"] = {
                    "tables": rows.height,
                    "expected": int(sheet.header["ACTAS_ESPERADAS"]),
                    "registered": got["PADRÓN"],
                    "cast": got["EMITIDOS_CALCULADO"],
                    "organizations": {info: mine[sigla[fold(info)]] for info in sheet.parties},
                }
        if problems:
            raise ValueError(f"{d.key}: {len(problems)} differences: {problems[:10]}")
    return report


def places() -> dict[tuple[int, int], tuple[str, str]]:
    """Each (department, municipality) code pair the files use, with the department's and municipality's names."""
    found: dict[tuple[int, int], tuple[str, str]] = {}
    for d in DAYS:
        for sheet in sheets(d):
            for dep, dep_name, mun, mun_name in (
                sheet.rows.select("ID_DEPARTAMENTO", "DEPARTAMENTO", "ID_MUNICIPIO", "MUNICIPIO").unique().iter_rows()
            ):
                found.setdefault((int(dep), int(mun)), (" ".join(dep_name.split()), " ".join(mun_name.split())))
    return found


def municipality(dep: int, mun: int) -> str:
    """Guatemala City reports as department 0 (the Distrito Central); its municipality is 1 of department 1."""
    return f"{1 if dep == CENTRAL else dep:02d}{mun:02d}"


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Supremo Electoral",
        title="Transmisión de Resultados Electorales Preliminares (TREP), Elecciones Generales 2023: archivos por mesa",
        url=TREP,
        license=LICENSE,
        license_url=f"{SITE}/informacion-publica",
        attribution="Tribunal Supremo Electoral (TSE), Guatemala",
    )
    for d in DAYS:
        p.artifact(raw.cached(SOURCE, catalog_url(d)), SOURCE_ID)
        p.artifact(raw.cached(SOURCE, archive_url(d)), SOURCE_ID)


def geo() -> Package:
    found = places()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Guatemala's 22 departments, 340 municipalities, and the Guatemala electoral district as the Tribunal Supremo "
        "Electoral codes them, and the Tribunal's 2023 per-table results files",
    )
    p.external |= {GUATEMALA, COUNTRY}
    _sources(p)
    for code, rank, kind, noun in (
        ("gt.departamento", 1, "administrative", "department"),
        ("gt.municipio", 2, "administrative", "municipality"),
        ("gt.distrito", 1, "electoral_district", "electoral district"),
        ("gt.centro", 3, "precinct", "polling center"),
        ("gt.mesa", 4, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=GUATEMALA, kind=kind, rank=rank, noun=noun, code_system="tse")

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "es", "official")

    departments = {dep: names[0] for (dep, _), names in found.items() if dep != CENTRAL and dep not in ABROAD}
    for dep, text in sorted(departments.items()):
        place("gt.departamento", f"{dep:02d}", text, COUNTRY)
    for (dep, mun), (_, text) in sorted(found.items()):
        if dep in ABROAD:
            continue
        parent = area("gt.departamento", f"{1 if dep == CENTRAL else dep:02d}")
        place("gt.municipio", municipality(dep, mun), text, parent)
    place("gt.distrito", "01", "Distrito de Guatemala", area("gt.departamento", "01"))
    p.add(
        "note",
        id=key("note", area("gt.distrito", "01")),
        entity_id=area("gt.distrito", "01"),
        kind="context",
        body_md="The department of Guatemala without Guatemala City, which elects its deputies as the Distrito "
        "Central.",
        lang="en",
    )
    towns = {
        municipality(dep, mun): (f"{1 if dep == CENTRAL else dep:02d}", text)
        for (dep, mun), (_, text) in found.items()
        if dep not in ABROAD
    }
    shapes.add(p, area, {f"{dep:02d}": text for dep, text in departments.items()}, towns)
    return p


BODIES = {
    "congress": ("unicameral", "Congress of the Republic of Guatemala", "Congreso de la República"),
    "parlacen": (
        "unicameral",
        "Central American Parliament (deputies for Guatemala)",
        "Parlamento Centroamericano, diputados por Guatemala",
    ),
}
PRESIDENCY = key("office", "president")
SYSTEMS = {
    "president": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The President and Vice President are elected together. A ticket that wins more than half of the valid votes "
            "is elected; otherwise the two leading tickets go to a second election."
        ),
    ),
    "congress": (
        {"formula": "dhondt"},
        (
            "Deputies are elected from closed lists, by the national list and by electoral district (the departments, "
            "with the Distrito Central of Guatemala City apart from the rest of its department); seats are allocated by "
            "D'Hondt (representación de minorías)."
        ),
    ),
    "parlacen": (
        {"formula": "dhondt"},
        (
            "Guatemala's 20 deputies to the Central American Parliament, each with a substitute, are elected from closed "
            "national lists; seats are allocated by D'Hondt."
        ),
    ),
    "municipal": (
        {"formula": "dhondt"},
        (
            "Each organization presents one closed list for the municipal corporation. The list with the most votes "
            "takes the mayoralty; the trustees (síndicos) and councillors (concejales) are allocated among the lists by "
            "D'Hondt."
        ),
    ),
}


def body(name: str) -> str:
    return key("body", name)


def council(code: str) -> str:
    return key("body", "council", code)


def system(name: str) -> str:
    return key("electoral_system", name)


def _party_kind(code: str, name: str, national: set[str]) -> str:
    if code in COALITIONS:
        return "bloc"
    if code in national or fold(name).startswith("PARTIDO"):
        return "party"
    return "independent_group"


def used() -> tuple[set[str], set[str]]:
    """The organization codes on any results file, and those on a national ballot."""
    sigla = by_sigla()
    anywhere: set[str] = set()
    national: set[str] = set()
    for d in DAYS:
        for sheet in sheets(d):
            codes = {sigla[fold(c)] for c in sheet.parties}
            anywhere |= codes
            if sheet.kind != "municipal":
                national |= codes
    return anywhere, national


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Guatemala's political parties, coalitions, and civic committees on the 2023 ballots, the Congress, the "
        "Central American Parliament delegation, the presidency, and the municipal councils, with their electoral "
        "systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GUATEMALA}
    for name, (kind, english, spanish) in BODIES.items():
        p.add("body", id=body(name), polity_id=GUATEMALA, kind=kind)
        p.name(body(name), english, "en", "official")
        p.name(body(name), spanish, "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=GUATEMALA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Guatemala", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for (dep, mun), (_, text) in sorted(places().items()):
        if dep in ABROAD:
            continue
        code = municipality(dep, mun)
        p.add("body", id=council(code), polity_id=GUATEMALA, kind="council")
        p.name(council(code), f"Concejo Municipal de {text}", "es", "official")
    for name, (rules, method) in SYSTEMS.items():
        p.add("electoral_system", id=system(name), polity_id=GUATEMALA, rules=rules, rules_version=1)
        p.add(
            "note", id=key("note", system(name)), entity_id=system(name), kind="methodology", body_md=method, lang="en"
        )
    anywhere, national = used()
    for code, info in sorted(organizations().items(), key=lambda item: int(item[0])):
        if code not in anywhere:
            continue
        id = party(code)
        name = " ".join(info["nombre"].split())
        p.add("party", id=id, kind=_party_kind(code, name, national), polity_id=GUATEMALA)
        p.name(id, title(name), "es", "official")
        siglas = " ".join(info["siglas"].split())
        if fold(siglas) != fold(name):
            p.name(id, siglas, "es", "abbreviation", False)
        p.add("external_id", entity_id=id, scheme="gt.tse.organizacion", value=f"2023:{code}")
        color = info.get("color", "")
        if re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            p.add("party_color", party_id=id, color=f"#{color.lower()}", source_id=SOURCE_ID)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.key}" for d in DAYS)]


def parse() -> None:
    report = compare()
    for name, row in report.items():
        print(f"{name}: {row}", flush=True)
    geo().write()
    registry().write()
    for d in DAYS:
        election(d).write()


def emit() -> None:
    package.run("load", packages())


def _contest_key(sheet: Sheet) -> str:
    if sheet.kind == "district":
        return f"district:{sheet.dep:02d}"
    if sheet.kind == "municipal":
        assert sheet.dep is not None and sheet.mun is not None
        return f"municipal:{municipality(sheet.dep, sheet.mun)}"
    return sheet.kind


def _district(sheet: Sheet, names: dict[tuple[int, int], tuple[str, str]]) -> tuple[str, str, str]:
    """The contest's area, Spanish title, and slug."""
    departments = {dep: text for (dep, _), (text, _) in names.items()}
    if sheet.kind == "president":
        return COUNTRY, "Presidente y Vicepresidente de la República", "presidente"
    if sheet.kind == "national":
        return COUNTRY, "Diputados al Congreso por Lista Nacional", "diputados-lista-nacional"
    if sheet.kind == "parlacen":
        return COUNTRY, "Diputados al Parlamento Centroamericano", "parlamento-centroamericano"
    assert sheet.dep is not None
    if sheet.kind == "district":
        if sheet.dep == CENTRAL:
            return (
                area("gt.municipio", "0101"),
                "Diputados al Congreso por el Distrito Central",
                "diputados-distrito-central",
            )
        if sheet.dep == 1:
            return (
                area("gt.distrito", "01"),
                "Diputados al Congreso por el Distrito de Guatemala",
                "diputados-guatemala",
            )
        text = departments[sheet.dep]
        return (
            area("gt.departamento", f"{sheet.dep:02d}"),
            f"Diputados al Congreso por {text}",
            f"diputados-{changeset.slug(text)}",
        )
    assert sheet.mun is not None
    code = municipality(sheet.dep, sheet.mun)
    town = names[(sheet.dep, sheet.mun)][1]
    dep = departments[1 if sheet.dep == CENTRAL else sheet.dep]
    return (
        area("gt.municipio", code),
        f"Corporación Municipal de {town}, {dep}",
        f"corporacion-{changeset.slug(dep)}-{changeset.slug(town)}",
    )


def _rerun() -> set[str]:
    return {_contest_key(s) for s in sheets(SECOND) if s.kind == "municipal"}


def _units(p: Package, d: Day, found: list[Sheet]) -> pl.DataFrame:
    tables = (
        pl.concat(
            [
                s.rows.select(
                    mesa=pl.col("MESA").cast(pl.Int64),
                    dep=pl.col("ID_DEPARTAMENTO").cast(pl.Int64),
                    mun=pl.col("ID_MUNICIPIO").cast(pl.Int64),
                    centro=pl.col("CENTRO_DE_VOTACIÓN").cast(pl.Int64),
                )
                for s in found
            ]
        )
        .unique()
        .sort("mesa")
    )
    if tables["mesa"].is_duplicated().any():
        raise ValueError(f"{d.key}: a table is placed in two polling centers")
    centers = tables.select("dep", "mun", "centro").unique().sort("centro")
    if centers["centro"].is_duplicated().any():
        raise ValueError(f"{d.key}: a polling center code is in two municipalities")
    names = {(dep, mun): text for (dep, mun), (_, text) in places().items()}
    rows: list[dict[str, object]] = []
    for dep, mun, centro in centers.iter_rows():
        abroad = dep in ABROAD
        id = key("unit", "gt.centro", d.day, centro)
        rows.append(
            {
                "id": id,
                "polity_id": GUATEMALA,
                "scheme_code": "gt.centro",
                "code": f"{d.day}:{centro}",
                "area_relation": "unlocated" if abroad else "within",
                "area_id": None if abroad else area("gt.municipio", municipality(dep, mun)),
                "kind": "abroad_mission" if abroad else "standard",
            }
        )
        if abroad:
            p.name(id, f"Centro de votación {centro}, {title(names[(dep, mun)])}", "es", "official")
    units = tables.with_columns(
        unit_id=pl.struct("mesa").map_elements(
            lambda r: key("unit", "gt.mesa", d.day, r["mesa"]), return_dtype=pl.String
        ),
        parent_id=pl.col("centro").map_elements(lambda c: key("unit", "gt.centro", d.day, c), return_dtype=pl.String),
        abroad=pl.col("dep").is_in(sorted(ABROAD)),
        area_id=pl.struct("dep", "mun").map_elements(
            lambda r: None if r["dep"] in ABROAD else area("gt.municipio", municipality(r["dep"], r["mun"])),
            return_dtype=pl.String,
        ),
    )
    p.frame("reporting_unit", pl.DataFrame(rows, infer_schema_length=None))
    p.frame(
        "reporting_unit",
        units.select(
            id="unit_id",
            polity_id=pl.lit(GUATEMALA),
            scheme_code=pl.lit("gt.mesa"),
            code=pl.concat_str(pl.lit(f"{d.day}:"), pl.col("mesa").cast(pl.String)),
            parent_id="parent_id",
            area_relation=pl.when("abroad").then(pl.lit("unlocated")).otherwise(pl.lit("within")),
            area_id="area_id",
            kind=pl.when("abroad").then(pl.lit("abroad_mission")).otherwise(pl.lit("standard")),
        ),
    )
    return units.select("mesa", "unit_id")


ELECTIONS = {
    "general": ("general", "2023 Guatemalan general election", "Elecciones Generales de 2023", "general"),
    "runoff": (
        "runoff",
        "2023 Guatemalan presidential election, second round",
        "Segunda Elección Presidencial de 2023",
        "runoff",
    ),
    "rerun": (
        "local",
        "2023 Guatemalan repeated municipal elections",
        "Repetición de elecciones municipales de 2023",
        "municipal-repeat",
    ),
}


def election(d: Day) -> Package:
    found = sheets(d)
    first = d == FIRST
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([] if first else [f"{SOURCE}/{FIRST.key}"])
    p = Package(
        f"{SOURCE}/{d.key}",
        SOURCE,
        f"Guatemala's elections of {d.day:%-d %B %Y} by polling table from the Tribunal Supremo Electoral's "
        "preliminary results transmission (TREP)",
        depends,
    )
    p.external |= {GUATEMALA, COUNTRY}
    p.uses(raw.cached(SOURCE, catalog_url(d)))
    p.uses(raw.cached(SOURCE, archive_url(d)))
    names = places()
    sigla = by_sigla()
    rerun = _rerun()
    elections: dict[str, str] = {}

    def held(family: str) -> str:
        if family not in elections:
            kind, english, spanish, word = ELECTIONS[family]
            id = elections[family] = key("election", d.day, family)
            p.add(
                "election",
                id=id,
                polity_id=GUATEMALA,
                kind=kind,
                date_from=d.day,
                date_to=d.day,
                status="held",
                rerun_of_id=key("election", FIRST.day, "general") if family == "rerun" else None,
            )
            p.name(id, english, "en", "official")
            p.name(id, spanish, "es", "native", False)
            p.add("slug", path=f"gt/{d.day}-{word}", entity_id=id, is_current=True)
        return elections[family]

    units = _units(p, d, found)
    values: list[pl.DataFrame] = []
    tally: Counter[str] = Counter()
    for sheet in found:
        family = "general" if first else "runoff" if sheet.kind == "president" else "rerun"
        election_id = held(family)
        ck = _contest_key(sheet)
        place, text, slug = _district(sheet, names)
        contest = key("contest", d.day, ck)
        president = sheet.kind == "president"
        body_id = {
            "national": body("congress"),
            "district": body("congress"),
            "parlacen": body("parlacen"),
            "municipal": council(ck.split(":")[1]) if sheet.kind == "municipal" else None,
        }.get(sheet.kind)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office" if president else "list_allocation",
            office_id=PRESIDENCY if president else None,
            body_id=body_id,
            district_area_id=place,
            magnitude={"president": 1, "national": 32, "parlacen": 20}.get(sheet.kind),
            round_number=(1 if first else 2) if president else 1,
            previous_round_id=key("contest", FIRST.day, ck) if president and not first else None,
            electoral_system_id=system(
                {"president": "president", "parlacen": "parlacen", "municipal": "municipal"}.get(sheet.kind, "congress")
            ),
            default_result_kind="counted",
        )
        p.name(contest, text, "es", "official")
        word = ELECTIONS[family][3]
        p.add("slug", path=f"gt/{d.day}-{word}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", d.day, ck)
        annulled = first and ck in rerun
        expected = int(sheet.header["ACTAS_ESPERADAS"])
        rows = counted(sheet)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=SOURCE_ID,
            status="annulled" if annulled else "active",
            is_default=True,
            completeness=round(rows.height / expected, 6) if expected else None,
            as_of=sheet.as_of,
        )
        method = METHOD + (VOTERS_NOTE if "PRES-VOTAR" in sheet.rows.columns else "")
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        if annulled:
            p.add(
                "note",
                id=key("note", result_set, "annulled"),
                entity_id=result_set,
                kind="caveat",
                body_md="The Tribunal Supremo Electoral annulled this municipal election and held it again on 20 "
                "August 2023, with the second presidential election.",
                lang="en",
            )
        options: dict[str, str] = {}
        for position, column in enumerate(sheet.parties, start=1):
            code = sigla[fold(column)]
            option = options[column] = key("option", d.day, ck, code)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="ticket" if president else "list",
                party_id=party(code),
                ballot_position=position,
                ballot_label=" ".join(column.split()),
            )
        joined = rows.with_columns(mesa=pl.col("MESA").cast(pl.Int64)).join(units, on="mesa", how="left")
        if joined["unit_id"].null_count():
            raise ValueError(f"{sheet.member}: tables without a unit")
        votes = joined.select("unit_id", *(_number(c).alias(options[c]) for c in sheet.parties)).unpivot(
            index="unit_id", variable_name="option_id", value_name="value"
        )
        accounting = pl.concat(
            [
                joined.select("unit_id", value=_number(column), measure=pl.lit(measure, pl.Int16))
                for measure, column in ACCOUNTING
                if column in joined.columns
            ]
        )
        frame = pl.concat(
            [
                votes.with_columns(measure=pl.lit(1, pl.Int16)),
                accounting.with_columns(option_id=pl.lit(None, pl.String)).select(
                    "unit_id", "option_id", "value", "measure"
                ),
            ]
        ).filter(pl.col("value").is_not_null())
        tally["empty cells"] += votes.filter(pl.col("value").is_null()).height
        over = joined.filter(_number("EMITIDOS_CALCULADO") > _number("PADRÓN")).height
        if over:
            tally["over roll"] += over
            p.add(
                "note",
                id=key("note", result_set, "over"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"In {over} counted table{'s' if over > 1 else ''}, the transcribed votes exceed the table's "
                "roll. The figures are stored as the TREP published them.",
                lang="en",
            )
        values.append(frame.with_columns(result_set_id=pl.lit(result_set)))
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    if tally["over roll"]:
        p.accept(
            "accounting.turnout",
            f"{tally['over roll']} counted tables whose transcribed votes exceed the roll, stored as the TREP published "
            "them and noted on each result set",
        )
    print(f"{d.key}: {facts.height:,} values, {dict(tally)}", flush=True)
    return p
