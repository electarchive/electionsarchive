import functools
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "py.tsje"
SITE = "https://www.tsje.gov.py"
FILES = f"{SITE}/resultados"
PARAGUAY = str(uuid5(package.NAMESPACE, "polity\x1fPY"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPRY"))
DAY = date(2026, 10, 4)
ELECTION = f"{SOURCE}/{DAY}"

MAYORS = f"{FILES}/resultado-muni-2026-intendentes.xlsx"
COUNCILS = f"{FILES}/resultado-muni-2026-junta-municipal.xlsx"
MAYORS_ELECTED = f"{FILES}/candidatos-electos-intendentes-2026.xlsx"
COUNCILS_ELECTED = f"{FILES}/candidatos-electos-junta-municipal-2026.xlsx"
ELECTED_SUMMARY = f"{FILES}/resumen-electos-por-organizacion-y-genero-2026.xlsx"
# The results page of the 2026 portal reads its district totals from these two modules.
SUMMARY = f"{SITE}/assets/resumen-DviwI-nn.js"
COUNCIL_SUMMARY = f"{SITE}/assets/juntas-o1DzBmUK.js"
WORKBOOKS = (MAYORS, COUNCILS, MAYORS_ELECTED, COUNCILS_ELECTED, ELECTED_SUMMARY)
MODULES = (SUMMARY, COUNCIL_SUMMARY)

TABLE = ("dep", "dis", "zona", "local", "mesa")
REGISTERED, CAST, VALID, INVALID, BLANK, PREFERENCE = 2, 3, 4, 5, 6, 7
PARTY_KINDS = {"Partido Político": "party", "Alianza Electoral": "bloc", "Movimiento Político": "independent_group"}

METHOD = (
    "Counted by the Tribunal Superior de Justicia Electoral per polling table (mesa receptora de votos), as published "
    "in its per-table results workbooks for the municipal elections of 4 October 2026. Registered voters are the "
    "table's roll; ballots cast are the table total the source prints; valid votes are the votes for lists; invalid "
    "and blank votes are the null and blank ballots. The table total also counts the ballots the source marks as not "
    "computed (no computados), which are not split by option, so in those tables ballots cast exceed valid, invalid, "
    "and blank votes together by that number. Tables are placed in their municipality (distrito)."
)
COUNCIL_METHOD = (
    METHOD + " Each list's votes are the sum of its candidates' preference votes, which the source prints per "
    "candidate and table; the preference votes are stored on each candidate under the list. The Tribunal also "
    "proclaims as many substitute councillors (suplentes) as members, from the same lists; they are listed in its "
    "file of elected candidates and are not marked here."
)


def fetch() -> None:
    for url in (*WORKBOOKS, *MODULES):
        artifact = raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
        # The portal answers every unknown path with its HTML shell, so a missing file arrives as a page.
        if artifact.media_type is not None and artifact.media_type.startswith("text/html"):
            raise LookupError(f"{url} is not published (the portal served its HTML shell)")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def normal(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


def title(text: str) -> str:
    small = {"de", "del", "la", "las", "los", "y", "el"}
    words = clean(text).lower().split()
    return " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


def party(name: str) -> str:
    return key("party", normal(name))


def office(dep: int, dis: int) -> str:
    return key("office", "mayor", dep, dis)


def body(dep: int, dis: int) -> str:
    return key("body", "council", dep, dis)


SOURCE_ID = key("source", SOURCE)
SUMMARY_ID = key("source", SOURCE, "summary")
MAYOR_SYSTEM = key("electoral_system", "mayor", 1991)
COUNCIL_SYSTEM = key("electoral_system", "council", 2021)
SYSTEMS = (
    (MAYOR_SYSTEM, date(1991, 1, 1), {"formula": "fptp"}, "The candidate with the most votes is elected mayor."),
    (
        COUNCIL_SYSTEM,
        date(2021, 1, 1),
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}},
        (
            "Council seats are divided among lists by D'Hondt; since 2021 each list's seats go to its candidates "
            "with the most preference votes (listas cerradas desbloqueadas)."
        ),
    ),
)


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Superior de Justicia Electoral",
        title="Resultados de las Elecciones Municipales 2026: archivos por mesa y candidatos electos",
        url=f"{SITE}/resultados-2026",
        license="Public official records of the Republic of Paraguay; reuse with the source acknowledged",
        license_url=f"{SITE}/resultados-2026",
        attribution="Tribunal Superior de Justicia Electoral (TSJE), Paraguay",
    )
    p.add(
        "source",
        id=SUMMARY_ID,
        kind="official_body",
        publisher="Tribunal Superior de Justicia Electoral",
        title="Resultados 2026: district totals shown on the results page",
        url=f"{SITE}/resultados-2026",
        license="Public official records of the Republic of Paraguay; reuse with the source acknowledged",
        license_url=f"{SITE}/resultados-2026",
        attribution="Tribunal Superior de Justicia Electoral (TSJE), Paraguay",
    )


def _code(column: str) -> pl.Expr:
    return pl.col(column).str.extract(r"^\s*(\d+)\s*-").cast(pl.Int64)


def _label(column: str) -> pl.Expr:
    return pl.col(column).str.replace(r"^\s*\d+\s*-\s*", "").str.strip_chars()


@functools.cache
def workbook(url: str, sheet: int = 0) -> pl.DataFrame:
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    return reader.load_sheet(reader.sheet_names[sheet], dtypes="string").to_polars()


@functools.cache
def votes(url: str) -> pl.DataFrame:
    """One row per table and option, with the table's accounting repeated on each row."""
    frame = workbook(url).with_columns(
        dep=_code("DEPARTAMENTO"),
        dep_name=_label("DEPARTAMENTO"),
        dis=_code("DISTRITO"),
        dis_name=_label("DISTRITO"),
        zona=_code("ZONA"),
        local=_code("LOCAL"),
        local_name=_label("LOCAL"),
        mesa=pl.col("NRO_MESA").cast(pl.Int64),
        lista=pl.col("NRO_LISTA").cast(pl.Int64),
        sigla=pl.col("SIGLA").str.strip_chars(),
        nombre=pl.col("NOMLISTA").map_elements(clean, return_dtype=pl.String),
        registered=pl.col("CANT_ELECTOR").cast(pl.Int64),
        list_votes=pl.col("VOTO_LISTA").cast(pl.Int64),
        blank=pl.col("VOT_BLANCO").cast(pl.Int64),
        invalid=pl.col("VOT_NULO").cast(pl.Int64),
        total=pl.col("TOTAL_MESA").cast(pl.Int64),
        uncounted=pl.col("NO_COMPUTADOS").cast(pl.Int64).fill_null(0),
        candidate=pl.col("NOMBRE_APELLIDO").map_elements(clean, return_dtype=pl.String),
    )
    if "ORDEN" in frame.columns:
        frame = frame.with_columns(orden=pl.col("ORDEN").cast(pl.Int64), preference=pl.col("VOTO_PREF").cast(pl.Int64))
    if frame.select(*TABLE).null_count().sum_horizontal().item():
        raise ValueError(f"{url}: rows without a table code")
    return frame


@functools.cache
def tables(url: str) -> pl.DataFrame:
    frame = votes(url)
    accounting = ["registered", "blank", "invalid", "total", "uncounted"]
    lists = frame.unique([*TABLE, "lista"])
    out = lists.group_by(*TABLE).agg(
        pl.col("dep_name").first(),
        pl.col("dis_name").first(),
        pl.col("local_name").first(),
        *(pl.col(c).n_unique().alias(f"{c}_n") for c in accounting),
        *(pl.col(c).first() for c in accounting),
        pl.col("list_votes").sum().alias("valid"),
    )
    if out.filter(pl.any_horizontal(pl.col(f"{c}_n") > 1 for c in accounting)).height:
        raise ValueError(f"{url}: a table prints two different totals")
    broken = out.filter(pl.col("valid") + pl.col("blank") + pl.col("invalid") + pl.col("uncounted") != pl.col("total"))
    if broken.height:
        raise ValueError(f"{url}: {broken.height} tables do not add up to their printed total: {broken.head()}")
    return out.drop(*(f"{c}_n" for c in accounting)).sort(*TABLE)


def _module(url: str) -> object:
    text = raw.cached(SOURCE, url).path.read_text()
    if url == SUMMARY:
        found = re.search(r"R=JSON\.parse\(`(.*?)`\)", text, re.DOTALL)
        if found is None:
            raise ValueError(f"{url}: no district table")
        return json.loads(found.group(1))
    found = re.search(r"const A=(\{.*\});export", text, re.DOTALL)
    if found is None:
        raise ValueError(f"{url}: no council table")
    return json.loads(found.group(1))


@functools.cache
def summary() -> dict[tuple[int, int], dict[str, object]]:
    rows = _module(SUMMARY)
    assert isinstance(rows, list)
    return {(int(row["dep"]), int(row["dis"])): row for row in rows}


def districts() -> pl.DataFrame:
    frame = tables(MAYORS).select("dep", "dep_name", "dis", "dis_name").unique().sort("dep", "dis")
    if frame.select("dep", "dis").n_unique() != frame.height:
        raise ValueError("a district has two names")
    return frame


def parse() -> None:
    """Rebuild every package from the raw cache, after checking the per-table files against the authority's totals."""
    compare()
    geo().write()
    registry().write()
    election().write()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", ELECTION]


def emit() -> None:
    package.run("load", packages())


def compare() -> dict[str, dict[str, int]]:
    """Sum the per-table files by district and compare them with the totals the results page prints."""
    national: dict[str, dict[str, int]] = {}
    for url, part in ((MAYORS, "intendente"), (COUNCILS, "junta")):
        lists = votes(url).unique([*TABLE, "lista"])
        by_list = {
            (dep, dis, sigla): n
            for dep, dis, sigla, n in lists.group_by("dep", "dis", "sigla").agg(pl.col("list_votes").sum()).iter_rows()
        }
        by_district = {
            (row["dep"], row["dis"]): row
            for row in tables(url)
            .group_by("dep", "dis")
            .agg(pl.len().alias("mesas"), *(pl.col(c).sum() for c in ("registered", "blank", "invalid", "valid")))
            .iter_rows(named=True)
        }
        totals = Counter[str]()
        problems: list[str] = []
        for (dep, dis), row in summary().items():
            printed = row[part]
            assert isinstance(printed, dict)
            ours = by_district[(dep, dis)]
            for field, column in (("electores", "registered"), ("blancos", "blank"), ("nulos", "invalid")):
                totals[field] += int(printed[field])
                totals[f"summed {field}"] += int(ours[column])
                if int(printed[field]) != ours[column]:
                    problems.append(f"{dep}:{dis} {field} {printed[field]} != {ours[column]}")
            for sigla, _, n in printed["listas"]:
                totals["lists"] += int(n)
                if by_list.get((dep, dis, sigla)) != int(n):
                    problems.append(f"{dep}:{dis} {sigla} {n} != {by_list.get((dep, dis, sigla))}")
            totals["summed lists"] += int(ours["valid"])
            totals["mesas"] += int(printed["mesas"])
            totals["summed mesas"] += int(ours["mesas"])
        if len(by_district) != len(summary()):
            problems.append(f"{len(by_district)} districts in the file, {len(summary())} on the results page")
        if problems:
            raise ValueError(f"{url}: {len(problems)} differences from the results page: {problems[:10]}")
        national[part] = dict(totals)
        print(f"{part}: {dict(totals)}", flush=True)
    shown = _module(COUNCIL_SUMMARY)
    assert isinstance(shown, dict)
    page = Counter(
        (district, int(rank), int(n)) for district, rows in shown.items() for tier, rank, _, _, n in rows if tier == 1
    )
    elected = workbook(COUNCILS_ELECTED).filter(pl.col("DESC_TITSUP") == "TITULARES")
    filed = Counter(
        (f"{dep}-{dis}", int(rank), int(n))
        for dep, dis, rank, n in elected.select("DEP_JUN", "DIS_JUN", "ORDEN_JUN", "TOT_VOTOS").iter_rows()
    )
    if page != filed:
        raise ValueError(
            f"the results page and the elected file disagree on {((page - filed) + (filed - page)).total()} councillors"
        )
    return national


def geo() -> Package:
    frame = districts()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Paraguay's {frame['dep'].n_unique()} departments (with the capital) and {frame.height} municipalities as "
        "the Tribunal Superior de Justicia Electoral codes them, and the Tribunal's 2026 results files",
    )
    p.external |= {PARAGUAY, COUNTRY}
    _sources(p)
    for url in WORKBOOKS:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for url in MODULES:
        p.artifact(raw.cached(SOURCE, url), SUMMARY_ID)
    for code, rank, kind, noun in (
        ("py.departamento", 1, "administrative", "department"),
        ("py.distrito", 2, "administrative", "municipality"),
        ("py.mesa", 3, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=PARAGUAY, kind=kind, rank=rank, noun=noun, code_system=None)
    for dep, name in frame.select("dep", "dep_name").unique().sort("dep").iter_rows():
        id = area("py.departamento", str(dep))
        p.add("area", id=id, scheme_code="py.departamento")
        p.add("area_code", area_id=id, code=str(dep))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(name), "es", "official")
    for dep, dis, name in frame.select("dep", "dis", "dis_name").iter_rows():
        id = area("py.distrito", f"{dep}:{dis}")
        p.add("area", id=id, scheme_code="py.distrito")
        p.add("area_code", area_id=id, code=f"{dep}:{dis}")
        p.add("area_parent", area_id=id, parent_id=area("py.departamento", str(dep)))
        p.name(id, title(name), "es", "official")
    return p


def _organizations() -> dict[str, str]:
    sheet = workbook(ELECTED_SUMMARY, 1)
    if sheet.columns[:3] != ["Organización Política", "Siglas", "Tipo Agrupación"]:
        raise ValueError(f"{ELECTED_SUMMARY}: unexpected header {sheet.columns[:3]}")
    return {normal(row[0]): PARTY_KINDS.get(str(row[2]), "party") for row in sheet.rows() if row[0]}


def _party_kind(name: str, kinds: dict[str, str]) -> str:
    if normal(name) in kinds:
        return kinds[normal(name)]
    if normal(name).startswith("ALIANZA"):
        return "bloc"
    if normal(name).startswith("MOVIMIENTO"):
        return "independent_group"
    return "party"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Paraguay's municipal offices and councils, their electoral systems, and the parties, alliances, and "
        "movements on the Tribunal Superior de Justicia Electoral's 2026 municipal results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PARAGUAY}
    for url in (*WORKBOOKS, *MODULES):
        p.uses(raw.cached(SOURCE, url))
    for dep, dis, name in districts().select("dep", "dis", "dis_name").iter_rows():
        place = title(name)
        p.add("office", id=office(dep, dis), polity_id=PARAGUAY, kind="mayor")
        p.name(office(dep, dis), f"Mayor of {place}", "en", "official")
        p.name(office(dep, dis), f"Intendente municipal de {place}", "es", "native", False)
        p.add("body", id=body(dep, dis), polity_id=PARAGUAY, kind="council")
        p.name(body(dep, dis), f"{place} Municipal Council", "en", "official")
        p.name(body(dep, dis), f"Junta municipal de {place}", "es", "native", False)
    for id, since, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=PARAGUAY, valid_from=since, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    kinds = _organizations()
    spellings: dict[str, Counter[str]] = {}
    acronyms: dict[str, Counter[str]] = {}
    for url in (MAYORS, COUNCILS):
        listed = votes(url).unique(["dep", "dis", "lista"]).group_by("nombre", "sigla").len()
        for name, sigla, n in listed.iter_rows():
            spellings.setdefault(normal(name), Counter())[name] += n
            acronyms.setdefault(normal(name), Counter())[sigla] += n
    for normalized, names in sorted(spellings.items()):
        name = names.most_common(1)[0][0]
        id = party(name)
        p.add("party", id=id, kind=_party_kind(name, kinds), polity_id=PARAGUAY)
        p.add("external_id", entity_id=id, scheme="py.tsje.organizacion", value=normalized)
        p.name(id, name, "es", "official")
        sigla = acronyms[normalized].most_common(1)[0][0]
        if sigla:
            p.name(id, sigla, "es", "abbreviation", False)
    return p


def _slugs() -> dict[tuple[int, int], str]:
    frame = districts()
    counts = Counter(changeset.slug(title(name)) for name in frame["dis_name"])
    slugs: dict[tuple[int, int], str] = {}
    for dep, dep_name, dis, dis_name in frame.iter_rows():
        slug = changeset.slug(title(dis_name))
        slugs[(dep, dis)] = slug if counts[slug] == 1 else f"{slug}-{changeset.slug(title(dep_name))}"
    if len(set(slugs.values())) != len(slugs):
        raise ValueError("two municipalities share a slug")
    return slugs


def _unit_frame(units: pl.DataFrame) -> pl.DataFrame:
    return units.select(
        id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        polity_id=pl.lit(PARAGUAY),
        scheme_code=pl.lit("py.mesa"),
        code="code",
        area_relation=pl.lit("within"),
        area_id=pl.struct("dep", "dis").map_elements(
            lambda row: area("py.distrito", f"{row['dep']}:{row['dis']}"), return_dtype=pl.String
        ),
        kind=pl.lit("standard"),
    )


def _code_expr() -> pl.Expr:
    return pl.concat_str(pl.lit(str(DAY)), *(pl.col(c).cast(pl.String) for c in TABLE), separator=":")


def _accounting(frame: pl.DataFrame, result_set: str) -> pl.DataFrame:
    parts = [
        frame.select(unit_id="unit_id", measure=pl.lit(measure, pl.Int16), value=column)
        for measure, column in (
            (REGISTERED, "registered"),
            (CAST, "total"),
            (VALID, "valid"),
            (INVALID, "invalid"),
            (BLANK, "blank"),
        )
    ]
    return pl.concat(parts).with_columns(result_set_id=pl.lit(result_set), option_id=pl.lit(None, pl.String))


def _caveats(p: Package, result_set: str, frame: pl.DataFrame, counts: Counter[str]) -> None:
    uncounted = frame.filter(pl.col("uncounted") > 0)
    over = frame.filter(pl.col("total") > pl.col("registered"))
    if uncounted.height:
        counts["uncounted"] += uncounted.height
        p.add(
            "note",
            id=key("note", result_set, "uncounted"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {uncounted.height} table{'s' if uncounted.height > 1 else ''}, the source marks "
            f"{uncounted['uncounted'].sum():,} ballot{'s' if uncounted['uncounted'].sum() > 1 else ''} as not "
            "computed (no computados). They are part of ballots cast but of no option, blank, or null count.",
            lang="en",
        )
    if over.height:
        counts["over"] += over.height
        p.add(
            "note",
            id=key("note", result_set, "over"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {over.height} table{'s' if over.height > 1 else ''}, the source prints more ballots than "
            "voters on the table's roll. The figures are stored as printed.",
            lang="en",
        )


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "Paraguay's municipal elections of 4 October 2026 (mayors and municipal councils in all 263 "
        "municipalities), by polling table, from the Tribunal Superior de Justicia Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PARAGUAY}
    for url in (*WORKBOOKS, *MODULES):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", DAY)
    path = f"py/{DAY}-municipal"
    p.add(
        "election",
        id=election_id,
        polity_id=PARAGUAY,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, "2026 Paraguayan municipal elections", "en", "official")
    p.name(election_id, "Elecciones municipales 2026", "es", "native", False)

    mayor_tables = tables(MAYORS).with_columns(code=_code_expr())
    council_tables = tables(COUNCILS).with_columns(code=_code_expr())
    if set(mayor_tables["code"]) != set(council_tables["code"]):
        raise ValueError("the mayor and council files list different tables")
    units = _unit_frame(mayor_tables)
    p.frame("reporting_unit", units)
    unit_of = units.select("code", unit_id="id")
    mayor_tables = mayor_tables.join(unit_of, on="code")
    council_tables = council_tables.join(unit_of, on="code")
    mayor_votes = votes(MAYORS).with_columns(code=_code_expr()).join(unit_of, on="code")
    council_votes = votes(COUNCILS).with_columns(code=_code_expr()).join(unit_of, on="code")

    slugs = _slugs()
    places = {(dep, dis): title(name) for dep, _, dis, name in districts().iter_rows()}
    elected_mayors = {
        (int(row["COD_DEPARTAMENTO"]), int(row["COD_DISTRITO"])): row
        for row in workbook(MAYORS_ELECTED).iter_rows(named=True)
    }
    councillors = workbook(COUNCILS_ELECTED).filter(pl.col("DESC_TITSUP") == "TITULARES")
    seats = Counter((int(row["DEP_JUN"]), int(row["DIS_JUN"])) for row in councillors.iter_rows(named=True))
    winners = {
        (int(row["DEP_JUN"]), int(row["DIS_JUN"]), int(row["LISTA_JUN"]), row["CEDULA_JUN"]): int(row["TOT_VOTOS"])
        for row in councillors.iter_rows(named=True)
    }
    counts: Counter[str] = Counter()
    values: list[pl.DataFrame] = []

    for (dep, dis), place in sorted(places.items()):
        printed = summary()[(dep, dis)]
        district = area("py.distrito", f"{dep}:{dis}")
        # Mayor.
        contest = key("contest", DAY, "mayor", dep, dis)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office(dep, dis),
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=MAYOR_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Mayor of {place}", "en", "official")
        p.name(contest, f"Intendente municipal de {place}", "es", "native", False)
        p.add("slug", path=f"{path}/{slugs[(dep, dis)]}-mayor", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, "mayor", dep, dis)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        here = mayor_votes.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        running = (
            here.group_by("lista", "sigla", "nombre", "candidate")
            .agg(pl.col("list_votes").sum())
            .sort("list_votes", "lista", descending=[True, False])
        )
        top = running["list_votes"].max()
        leaders = running.filter(pl.col("list_votes") == top)
        winner = elected_mayors[(dep, dis)]
        if int(winner["NRO_LISTA"]) not in set(leaders["lista"]) or int(winner["TOT_VOTO"]) != top:
            raise ValueError(f"{dep}:{dis}: the elected mayor is not the most voted candidate")
        by_sigla: dict[str, str] = {}
        for lista, sigla, nombre, candidate, _ in running.sort("lista").iter_rows():
            option = key("option", DAY, "mayor", dep, dis, lista)
            if sigla in by_sigla:
                raise ValueError(f"{dep}:{dis}: two mayoral candidates use {sigla}")
            by_sigla[sigla] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party(nombre),
                ballot_position=lista,
                ballot_label=candidate,
            )
            person = key("person", DAY, "mayor", dep, dis, lista)
            p.add("person", id=person)
            p.name(person, title(candidate), "es", "official")
            candidacy = key("candidacy", DAY, "mayor", dep, dis, lista)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=party(nombre),
                is_independent=False,
            )
            elected = lista == int(winner["NRO_LISTA"])
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if elected else "lost")
        _declared(p, result_set, printed["intendente"], by_sigla)
        tables_here = mayor_tables.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        values.append(_accounting(tables_here, result_set))
        values.append(
            here.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.col("lista").map_elements(
                    lambda n, dep=dep, dis=dis: key("option", DAY, "mayor", dep, dis, n), return_dtype=pl.String
                ),
                measure=pl.lit(1, pl.Int16),
                value="list_votes",
            )
        )
        _caveats(p, result_set, tables_here, counts)

        # Council.
        contest = key("contest", DAY, "council", dep, dis)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(dep, dis),
            district_area_id=district,
            magnitude=seats[(dep, dis)],
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{place} Municipal Council", "en", "official")
        p.name(contest, f"Junta municipal de {place}", "es", "native", False)
        p.add("slug", path=f"{path}/{slugs[(dep, dis)]}-council", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, "council", dep, dis)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        here = council_votes.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        lists = here.select("lista", "sigla", "nombre").unique().sort("lista")
        won = Counter(lista for (d1, d2, lista, _) in winners if (d1, d2) == (dep, dis))
        by_sigla = {}
        for lista, sigla, nombre in lists.iter_rows():
            option = key("option", DAY, "council", dep, dis, lista)
            if sigla in by_sigla:
                raise ValueError(f"{dep}:{dis}: two council lists use {sigla}")
            by_sigla[sigla] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(nombre),
                ballot_position=lista,
                ballot_label=nombre,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if won[lista] else "lost",
                seats=won[lista],
            )
        candidates = here.select("lista", "nombre", "orden", "candidate", "CEDULA").unique().sort("lista", "orden")
        for lista, nombre, orden, name, cedula in candidates.iter_rows():
            tag = (DAY, "council", dep, dis, lista, orden)
            option = key("option", *tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=key("option", DAY, "council", dep, dis, lista),
                party_id=party(nombre),
                ballot_position=orden,
                ballot_label=name,
            )
            person = key("person", *tag)
            p.add("person", id=person)
            p.name(person, title(name), "es", "official")
            candidacy = key("candidacy", *tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=key("option", DAY, "council", dep, dis, lista),
                role="list_member",
                list_position=orden,
                ballot_name=name,
                member_of_party_id=party(nombre),
                is_independent=False,
            )
            total = winners.get((dep, dis, lista, cedula))
            if total is not None:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=key("option", DAY, "council", dep, dis, lista),
                    candidacy_id=candidacy,
                    kind="elected",
                )
                p.add("declared", result_set_id=result_set, option_id=option, measure=PREFERENCE, value=total)
        _declared(p, result_set, printed["junta"], by_sigla)
        tables_here = council_tables.filter(pl.col("dep") == dep, pl.col("dis") == dis)
        values.append(_accounting(tables_here, result_set))
        list_rows = here.unique(["unit_id", "lista"])
        values.append(
            list_rows.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.col("lista").map_elements(
                    lambda n, dep=dep, dis=dis: key("option", DAY, "council", dep, dis, n), return_dtype=pl.String
                ),
                measure=pl.lit(1, pl.Int16),
                value="list_votes",
            )
        )
        values.append(
            here.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.struct("lista", "orden").map_elements(
                    lambda row, dep=dep, dis=dis: key("option", DAY, "council", dep, dis, row["lista"], row["orden"]),
                    return_dtype=pl.String,
                ),
                measure=pl.lit(PREFERENCE, pl.Int16),
                value="preference",
            )
        )
        _caveats(p, result_set, tables_here, counts)

    facts = pl.concat([frame.select("result_set_id", "unit_id", "option_id", "measure", "value") for frame in values])
    p.frame(
        "result_value",
        facts.with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    if counts["uncounted"]:
        p.accept(
            "accounting.ballots_sum",
            f"{counts['uncounted']} table counts (mayor and council) include ballots the TSJE marks as not computed "
            "(no computados), which are part of the table total but of no option, blank, or null count; stored as "
            "printed, with a caveat on each result set",
        )
    if counts["over"]:
        p.accept(
            "accounting.turnout",
            f"the TSJE prints more ballots than voters on the roll in {counts['over']} table counts (mayor and "
            "council); stored as printed, with a caveat on each result set",
        )
    return p


def _declared(p: Package, result_set: str, printed: object, by_sigla: dict[str, str]) -> None:
    assert isinstance(printed, dict)
    for measure, field in ((REGISTERED, "electores"), (BLANK, "blancos"), (INVALID, "nulos")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=int(printed[field]))
    valid = 0
    for sigla, _, n in printed["listas"]:
        valid += int(n)
        p.add("declared", result_set_id=result_set, option_id=by_sigla[sigla], measure=1, value=int(n))
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)
