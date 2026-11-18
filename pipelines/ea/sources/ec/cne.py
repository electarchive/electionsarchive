import functools
import re
import subprocess
import unicodedata
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import pyreadstat

from ea import package, raw
from ea.package import Package

SOURCE = "ec.cne"
ECUADOR = str(uuid5(package.NAMESPACE, "polity\x1fEC"))
PAGE = "https://www.cne.gob.ec/resultados-electorales/"
BASE = "https://www.cne.gob.ec/wp-content/uploads"
LOCAL_2019 = f"{BASE}/2022/05/Resultados-a-nivel-parroquial-1.sav"
RUNOFF_2021 = f"{BASE}/2022/05/Resultados-2021Segunda-vuelta.sav"
FIRST_2021 = f"{BASE}/2022/05/Resultados-2021Primera-vuelta.sav"
BOOK_2019 = f"{BASE}/2022/05/RESULTADOS-ELECTORALES-2019.pdf"
ARCHIVED = {BOOK_2019: "https://web.archive.org/web/20240714015955id_/" + BOOK_2019}
FILES = {LOCAL_2019: ".sav", FIRST_2021: ".sav", RUNOFF_2021: ".sav", BOOK_2019: ".pdf"}
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DAYS = {"2019-03-24": date(2019, 3, 24), "2021-02-07": date(2021, 2, 7), "2021-04-11": date(2021, 4, 11)}
# The first-round file lists its presidential rows first: 1,299 parishes, 16 tickets, men's and women's stations. The
# rows after the first 142,964 (part of the national assembly contest) do not read, so only the presidential rows are used.
FIRST_PRESIDENT_ROWS = 41600

METHOD = (
    "Counted per parish and published by the National Electoral Council in its parish-level results files. Each "
    "parish's polling stations are grouped into men's and women's stations, and the parish figures here add the two "
    "groups. Ballots cast are the voters who took part, invalid ballots are null votes, and, where one vote is cast "
    "for one option, valid votes are the votes the options received. A voter in a council or parish board contest "
    "casts several votes, so no valid-ballot figure is stored for those contests."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")
PRESIDENCY = key("office", "president")
PREFECTURE = key("office", "prefect")
MAYORALTY = key("office", "mayor")
COUNCIL = key("body", "municipal-council")
PARISH_BOARD = key("body", "parish-board")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SYSTEM_PLURALITY = key("electoral_system", "plurality")
SYSTEM_COUNCIL = key("electoral_system", "council", "webster")


def artifact(url: str) -> raw.Artifact:
    for candidate in (url, ARCHIVED.get(url)):
        if candidate is not None and candidate in raw.keys(SOURCE):
            return raw.cached(SOURCE, candidate)
    return raw.cached(SOURCE, url)


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)


@functools.cache
def sav(url: str, rows: int = 0) -> tuple[pl.DataFrame, dict[str, dict[float, str]]]:
    frame, meta = pyreadstat.read_sav(
        str(artifact(url).path), apply_value_formats=False, output_format="polars", row_limit=rows
    )
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(f"{url} did not read as a polars frame")
    return frame, {name: dict(labels) for name, labels in meta.variable_value_labels.items()}


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


SMALL = {"de", "del", "la", "las", "los", "el", "y", "e", "en"}


def title(text: str) -> str:
    words = clean(text).lower().split()
    done = " ".join(w if i and w in SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return done.replace("Ee.uu", "EE.UU")


def code(value: object) -> str:
    return str(int(float(str(value))))


def packages() -> list[str]:
    return ["ec.cne/geo", "ec.cne/registry", *(f"ec.cne/{day}" for day in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def area(scheme: str, value: str) -> str:
    return key("area", scheme, value)


def places(
    urls: tuple[str, ...] = (LOCAL_2019, RUNOFF_2021),
) -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Provinces, cantons, and parishes by code, from the files' value labels; the first file's spelling wins."""
    provinces: dict[str, str] = {}
    cantons: dict[str, tuple[str, str]] = {}
    parishes: dict[str, tuple[str, str]] = {}
    for url in urls:
        frame, labels = sav(url, FIRST_PRESIDENT_ROWS if url == FIRST_2021 else 0)
        for row in frame.select("PROVINCIA_CODIGO", "CANTON_CODIGO", "PARROQUIA_CODIGO").unique().iter_rows():
            province, canton, parish = (code(v) for v in row)
            provinces.setdefault(province, clean(labels["PROVINCIA_NOMBRE"][float(province)]))
            name = clean(labels["CANTON_NOMBRE"][float(canton)])
            if cantons.setdefault(canton, (name, province))[1] != province:
                raise ValueError(f"canton {canton} sits in two provinces")
            name = clean(labels["PARROQUIA_NOMBRE"][float(parish)])
            if parishes.setdefault(parish, (name, canton))[1] != canton:
                raise ValueError(f"parish {parish} sits in two cantons")
    return provinces, cantons, parishes


def geo() -> Package:
    p = Package(
        "ec.cne/geo",
        SOURCE,
        "Ecuador's provinces, cantons, and parishes, and the overseas circumscriptions, countries, and consulates, "
        "as the National Electoral Council's parish-level results files list them",
    )
    p.external.add(ECUADOR)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Election results files and books published by the National Electoral Council",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral del Ecuador",
    )
    for url in (LOCAL_2019, RUNOFF_2021):
        p.artifact(artifact(url), CNE_SOURCE)
    for scheme, rank, noun in (
        ("ec.provincia", 1, "province"),
        ("ec.canton", 2, "canton"),
        ("ec.parroquia", 3, "parish"),
    ):
        p.add("scheme", code=scheme, polity_id=ECUADOR, kind="administrative", rank=rank, noun=noun)
    provinces, cantons, parishes = places()
    for scheme, rows, parent in (
        ("ec.provincia", {c: (n, "") for c, n in provinces.items()}, None),
        ("ec.canton", cantons, "ec.provincia"),
        ("ec.parroquia", parishes, "ec.canton"),
    ):
        for value, (text, up) in sorted(rows.items(), key=lambda item: int(item[0])):
            id = area(scheme, value)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=value)
            if parent:
                p.add("area_parent", area_id=id, parent_id=area(parent, up))
            p.name(id, title(text), "es", "official")
    return p


def registry() -> Package:
    p = Package(
        "ec.cne/registry",
        SOURCE,
        "Ecuador's presidency, provincial prefecture, mayoralties, municipal councils, parish boards, and their "
        "electoral systems",
    )
    p.external.add(ECUADOR)
    p.add("body", id=COUNCIL, polity_id=ECUADOR, kind="council")
    p.name(COUNCIL, "Municipal and metropolitan councils of Ecuador", "en", "official")
    p.name(COUNCIL, "Concejos municipales y metropolitanos del Ecuador", "es", "native", False)
    p.add("body", id=PARISH_BOARD, polity_id=ECUADOR, kind="council")
    p.name(PARISH_BOARD, "Rural parish boards of Ecuador", "en", "official")
    p.name(PARISH_BOARD, "Juntas parroquiales rurales del Ecuador", "es", "native", False)
    for id, kind, english, spanish in (
        (PRESIDENCY, "head_of_state", "President of Ecuador", "Presidente de la República del Ecuador"),
        (PREFECTURE, "governor", "Prefect of a province of Ecuador", "Prefecto provincial del Ecuador"),
        (MAYORALTY, "mayor", "Mayor of a canton of Ecuador", "Alcalde cantonal del Ecuador"),
    ):
        p.add("office", id=id, polity_id=ECUADOR, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    systems = (
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate wins in the first round with more than half of the valid votes, or with at least 40% and "
                "a lead of ten points over the runner-up. Otherwise the two leading candidates go to a second round. "
                "The 40% rule is not stored in the rules."
            ),
        ),
        (SYSTEM_PLURALITY, {"formula": "fptp"}, "The candidate with the most votes wins."),
        (
            SYSTEM_COUNCIL,
            {"formula": "sainte_lague", "lists": {"order": "open", "vacancy": "substitute"}},
            (
                "Voters mark candidates across lists, and seats go to lists by the Webster method, which divides list "
                "totals by the odd numbers. Within a list, the most-voted candidates win the seats."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=ECUADOR, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _election(p: Package, name: str, day: date, kind: str, english: str, spanish: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=ECUADOR, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, spanish, "es", "native", False)
    return election


def _result_set(p: Package, election: str, contest: str, name: str) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CNE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _unit(p: Package, day: str, parish: str, names: dict[str, tuple[str, str]], seen: set[str]) -> str:
    unit = key("unit", f"{day}:{parish}")
    if parish not in seen:
        seen.add(parish)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code="ec.parroquia",
            code=f"{day}:{parish}",
            area_relation="equals",
            area_id=area("ec.parroquia", parish),
            kind="standard",
        )
        p.name(unit, title(names[parish][0]), "es", "official")
    return unit


def _table(frame: pl.DataFrame, group: list[str], option: str) -> pl.DataFrame:
    """One row per parish and option, adding every junta; each junta's accounting repeats on every option's row.

    A parish can hold several men's or women's juntas, each with its own accounting, and the file repeats a junta's
    accounting on one row for each option. So the accounting of a parish's men's or women's juntas is the sum over
    their rows divided by the number of distinct options those rows hold.
    """
    sexes = frame.group_by([*group, "PARROQUIA_CODIGO", "JUNTA_SEXO"]).agg(
        pl.len().alias("_rows"),
        pl.col(option).n_unique().alias("_options"),
        pl.col("SUFRAGANTES").sum().alias("_s"),
        pl.col("BLANCOS").sum().alias("_b"),
        pl.col("NULOS").sum().alias("_n"),
    )
    uneven = sexes.filter(
        (pl.col("_rows") % pl.col("_options") != 0)
        | (pl.col("_s") % pl.col("_options") != 0)
        | (pl.col("_b") % pl.col("_options") != 0)
        | (pl.col("_n") % pl.col("_options") != 0)
    )
    if uneven.height:
        raise ValueError(f"{uneven.height} parish groups repeat their accounting unevenly")
    accounting = sexes.group_by([*group, "PARROQUIA_CODIGO"]).agg(
        (pl.col("_s") // pl.col("_options")).sum().alias("SUFRAGANTES"),
        (pl.col("_b") // pl.col("_options")).sum().alias("BLANCOS"),
        (pl.col("_n") // pl.col("_options")).sum().alias("NULOS"),
    )
    votes = frame.group_by([*group, "PARROQUIA_CODIGO", option]).agg(pl.col("VOTOS").sum())
    return votes.join(accounting, on=[*group, "PARROQUIA_CODIGO"])


# ----- the 2019 book: list and organization labels, matched to the file's candidacy codes by their votes -----

NUMBER = r"\d{1,3}(?:\.\d{3})*"
WORD = r"[A-ZÁÉÍÓÚÑ0-9\./'\-]+(?: [A-ZÁÉÍÓÚÑ0-9\./'\-]+)*"
LINE = re.compile(
    rf"(?P<list>\d{{1,3}}(?:-\d{{1,3}})*) {{2,}}(?P<org>{WORD}) {{2,}}(?P<votes>{NUMBER}) +\d{{1,3}},\d\d%"
)
WINNER = re.compile(
    rf"^ *(?P<canton>{WORD}) {{2,}}(?P<org>{WORD}) {{2,}}(?P<name>{WORD}) {{2,}}(?P<votes>{NUMBER}) +\d{{1,3}},\d\d%"
)


@functools.cache
def book() -> tuple[dict[int, tuple[str, str]], dict[int, list[tuple[str, str, str]]]]:
    """Prefect lists by votes, and elected mayors by votes, as the 2019 book prints them."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(artifact(BOOK_2019).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "PREFECTOS")
    middle = next(i for i, line in enumerate(lines) if "< ALCALDES" in line)
    end = next(i for i, line in enumerate(lines) if "CONCEJALES URBANOS" in line and i > middle)
    prefects: dict[int, list[tuple[str, str]]] = {}
    for line in lines[start:middle]:
        for found in LINE.finditer(line):
            prefects.setdefault(int(found["votes"].replace(".", "")), []).append((found["list"], found["org"]))
    mayors: dict[int, list[tuple[str, str, str]]] = {}
    for line in lines[middle:end]:
        found = WINNER.match(line)
        if found:
            mayors.setdefault(int(found["votes"].replace(".", "")), []).append(
                (slug(found["canton"]), clean(found["name"]), found["org"])
            )
    return {v: rows[0] for v, rows in prefects.items() if len(rows) == 1}, mayors


SEAT = re.compile(
    rf"(?P<list>\d{{1,3}}(?:-\d{{1,3}})*) {{2,}}(?:(?P<org>{WORD}) {{2,}})?(?P<name>{WORD}) {{2,}}(?P<votes>{NUMBER}) +(?P<seat>\d{{1,2}}) *$"
)
# the heading above each table of councillors names its dignity: urban, by urban circumscription, or rural
COUNCIL_HEADS = (("CONCEJALES URBANOS", "5"), ("CONCEJALES POR CIRCUNSCRIPCI", "5"), ("CONCEJALES RURALES", "3"))


class Councillor(NamedTuple):
    dignity: str
    votes: int
    name: str
    org: str
    seat: int


@functools.cache
def councillors() -> list[tuple[list[Councillor], str]]:
    """The elected councillors of each canton as the 2019 book prints them: one group of seats per contest, in seat
    order, with the printed text around it, where the canton's name stands."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(artifact(BOOK_2019).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    lines = unicodedata.normalize("NFC", text).splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "CONCEJALES URBANOS")
    end = next(i for i, line in enumerate(lines) if "Composición de las juntas parroquiales" in line)
    groups: list[tuple[list[Councillor], str]] = []
    dignity, current, around = "", [], []
    for line in lines[start:end]:
        if "RESULTADOS ELECTORALES" in line or "<" in line:
            for head, value in COUNCIL_HEADS:
                if head in line:
                    dignity = value
        found = SEAT.search(line)
        if not found:
            around.append(line)
            continue
        row = Councillor(
            dignity, int(found["votes"].replace(".", "")), clean(found["name"]), found["org"] or "", int(found["seat"])
        )
        if row.seat == 1 and current:
            groups.append((current, " ".join(around)))
            current, around = [], []
        current.append(row)
        around.append(line[: found.start()])
    if current:
        groups.append((current, " ".join(around)))
    return groups


@functools.cache
def council_names() -> dict[tuple[str, str, int], dict[str, Councillor]]:
    """Elected councillors by (dignity, canton, circumscription) and the file's candidacy code.

    The book prints each elected councillor's votes. A group of seats belongs to the one contest of its dignity whose
    candidates include every printed vote total; when several fit, the canton named beside the seats decides. A
    councillor is named only where exactly one candidacy of the contest holds the printed votes."""
    frame, _ = sav(LOCAL_2019)
    _, cantons, _ = places((LOCAL_2019,))
    totals = (
        frame.filter(pl.col("DIGNIDAD_CODIGO").is_in([3.0, 5.0]))
        .group_by(["DIGNIDAD_CODIGO", "CANTON_CODIGO", "CIRCUNSCRIPCION_CODIGO", "CANDIDATO_CODIGO"])
        .agg(pl.col("VOTOS").sum())
    )
    index: dict[tuple[str, str, int], dict[int, list[str]]] = {}
    for dignity, canton, circumscription, candidacy, votes in totals.iter_rows():
        contest = (code(dignity), code(canton), int(circumscription))
        index.setdefault(contest, {}).setdefault(int(votes), []).append(code(candidacy))
    found: dict[tuple[str, str, int], dict[str, Councillor]] = {}
    for group, around in councillors():
        fits = [k for k, v in index.items() if k[0] == group[0].dignity and all(r.votes in v for r in group)]
        if len(fits) > 1:
            text = slug(around)
            fits = [k for k in fits if slug(cantons[k[1]][0]) in text] or fits
        if len(fits) != 1 or fits[0] in found:
            continue
        found[fits[0]] = {holders[0]: row for row in group if len(holders := index[fits[0]][row.votes]) == 1}
    return found


class Local:
    """Contest and option bookkeeping for one election's contests."""

    def __init__(self, p: Package, day: str, election: str) -> None:
        self.p = p
        self.day = day
        self.election = election
        self.paths: set[str] = set()

    def path(self, text: str, tail: str) -> str:
        base = f"ec/{self.day}-local/{slug(text)}"
        path = base if base not in self.paths else f"{base}-{tail}"
        self.paths.add(path)
        return path

    def contest(
        self, name: str, english: str, spanish: str, text: str, kind: str, district: str, **fields: object
    ) -> str:
        contest = key("contest", self.day, name)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            district_area_id=district,
            round_number=1,
            default_result_kind="counted",
            **fields,
        )
        self.p.name(contest, english, "en", "official")
        self.p.name(contest, spanish, "es", "native", False)
        self.p.add("slug", path=self.path(text, name.rsplit("/", 1)[-1]), entity_id=contest, is_current=True)
        return contest


def _local(p: Package, day: str) -> None:
    frame, labels = sav(LOCAL_2019)
    p.uses(artifact(LOCAL_2019))
    p.uses(artifact(BOOK_2019))
    provinces, cantons, parishes = places((LOCAL_2019,))
    election = _election(
        p,
        "local",
        DAYS[day],
        "local",
        "2019 Ecuadorian sectional elections",
        "Elecciones seccionales del Ecuador 2019",
        f"ec/{day}-local",
    )
    names = {code(k): clean(v) for k, v in labels["DIGNIDAD_NOMBRE"].items()}
    by_prefect, by_mayor = book()
    councillor = council_names()
    local = Local(p, day, election)
    seen: set[str] = set()
    people: set[str] = set()
    for dignity, kind, scheme_kind in (
        ("2", "office", "province"),
        ("4", "office", "canton"),
        ("5", "list_allocation", "canton"),
        ("3", "list_allocation", "canton"),
        ("6", "list_allocation", "parish"),
    ):
        part = frame.filter(pl.col("DIGNIDAD_CODIGO") == float(dignity))
        group = {
            "province": ["PROVINCIA_CODIGO"],
            "canton": ["CANTON_CODIGO", "CIRCUNSCRIPCION_CODIGO"],
            "parish": ["PARROQUIA_CODIGO"],
        }[scheme_kind]
        table = _table(part, [] if scheme_kind == "parish" else group, "CANDIDATO_CODIGO")
        for rows in table.partition_by(group, as_dict=False):
            first = rows.row(0, named=True)
            if scheme_kind == "province":
                province = code(first["PROVINCIA_CODIGO"])
                place = provinces[province]
                district = area("ec.provincia", province)
                token = province
                circumscription = 0
                english = f"{title(place)} prefect and vice prefect, 2019"
                spanish = f"Prefecto y viceprefecto de {title(place)} 2019"
                fields = {"office_id": PREFECTURE, "magnitude": 1, "electoral_system_id": SYSTEM_PLURALITY}
            elif scheme_kind == "canton":
                canton = code(first["CANTON_CODIGO"])
                circumscription = int(first["CIRCUNSCRIPCION_CODIGO"])
                place = cantons[canton][0]
                district = area("ec.canton", canton)
                token = f"{canton}.{circumscription}"
                if dignity == "4":
                    english = f"{title(place)} mayor, 2019"
                    spanish = f"Alcalde de {title(place)} 2019"
                    fields = {"office_id": MAYORALTY, "magnitude": 1, "electoral_system_id": SYSTEM_PLURALITY}
                else:
                    which = {"5": "urban", "3": "rural"}[dignity]
                    ward = f", urban circumscription {circumscription}" if dignity == "5" and circumscription else ""
                    english = f"{title(place)} {which} councillors{ward}, 2019"
                    spanish = f"Concejales {'urbanos' if dignity == '5' else 'rurales'} de {title(place)} 2019" + (
                        f", circunscripción urbana {circumscription}" if ward else ""
                    )
                    fields = {"body_id": COUNCIL, "electoral_system_id": SYSTEM_COUNCIL}
            else:
                parish = code(first["PARROQUIA_CODIGO"])
                circumscription = 0
                place = parishes[parish][0]
                district = area("ec.parroquia", parish)
                token = parish
                english = f"{title(place)} parish board, 2019"
                spanish = f"Junta parroquial de {title(place)} 2019"
                fields = {"body_id": PARISH_BOARD, "electoral_system_id": SYSTEM_COUNCIL}
            name = f"{dignity}/{token}"
            contest = local.contest(name, english, spanish, f"{names[dignity]} {place}", kind, district, **fields)
            result_set = _result_set(p, election, contest, name)
            options: dict[str, str] = {}
            single = dignity in ("2", "4")
            ordered = sorted({code(v) for v in rows["CANDIDATO_CODIGO"].to_list()}, key=int)
            totals = {
                code(k): int(v) for k, v in rows.group_by("CANDIDATO_CODIGO").agg(pl.col("VOTOS").sum()).iter_rows()
            }
            for position, candidacy in enumerate(ordered, 1):
                option = options[candidacy] = key("option", name, candidacy)
                label, person_name = f"Candidacy {candidacy}", None
                total = totals[candidacy]
                if dignity == "2" and total in by_prefect:
                    listed, org = by_prefect[total]
                    label = f"{org} (list {listed})"
                elif dignity == "4" and total == max(totals.values()):
                    # the book names each canton's winner, so its votes identify the winning candidacy
                    found = by_mayor.get(total, [])
                    named = [row for row in found if row[0] == slug(place)] or (found if len(found) == 1 else [])
                    if len(named) == 1:
                        _, person_name, org = named[0]
                        label = f"{person_name}, {org}"
                elif dignity in ("3", "5") and candidacy in (
                    seated := councillor.get((dignity, canton, circumscription), {})
                ):
                    # the book names each canton's elected councillors, so their votes identify the candidacies
                    elected = seated[candidacy]
                    person_name, org = elected.name, elected.org
                    label = f"{person_name}, {org}" if org else person_name
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if dignity in ("2", "4") else "list",
                    ballot_position=position,
                    ballot_label=label,
                )
                if person_name:
                    person = key("person", name, candidacy)
                    p.add("person", id=person)
                    p.name(person, title(person_name), "es", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", name, candidacy),
                        person_id=person,
                        option_id=option,
                        role="head" if single else "list_member",
                        ballot_name=title(person_name),
                    )
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected")
                    people.add(person)
            for parish_rows in rows.partition_by("PARROQUIA_CODIGO", as_dict=False):
                row = parish_rows.row(0, named=True)
                unit = _unit(p, day, code(row["PARROQUIA_CODIGO"]), parishes, seen)
                _put(p, result_set, unit, None, CAST, int(row["SUFRAGANTES"]))
                _put(p, result_set, unit, None, BLANK, int(row["BLANCOS"]))
                _put(p, result_set, unit, None, INVALID, int(row["NULOS"]))
                if single:
                    _put(p, result_set, unit, None, VALID, int(parish_rows["VOTOS"].sum()))
                for candidacy, votes in zip(
                    parish_rows["CANDIDATO_CODIGO"].to_list(), parish_rows["VOTOS"].to_list(), strict=True
                ):
                    _put(p, result_set, unit, options[code(candidacy)], VOTES, int(votes))
    p.accept(
        "accounting.ballots_sum",
        "the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots cast "
        "in 1,518 of 2,455 prefect and mayor parish rows, by 177 ballots at most and by under 1% of the ballots in "
        "every row, in both directions; stored as published",
    )


# organization code -> (head of ticket, running mate, party); the file carries no labels for these codes
RUNOFF = {
    "1021": ("Guillermo Lasso Mendoza", "Alfredo Borja Cabezas", "CREO-PSC"),
    "1030": ("Andrés Arauz Galarza", "Carlos Rabascall Villacís", "UNES"),
}
# first-round tickets named only where the file's vote totals match the published results
FIRST_NAMED = {**RUNOFF, "2": ("Yaku Pérez Guartambel", "", ""), "42": ("Xavier Hervas Morillo", "", "")}


def _president(p: Package, day: str, first: bool) -> None:
    url = FIRST_2021 if first else RUNOFF_2021
    frame, _ = sav(url, FIRST_PRESIDENT_ROWS if first else 0)
    if first:
        frame = frame.filter(pl.col("DIGNIDAD_NOMBRE") == 1.0)
    p.uses(artifact(url))
    _, _, parishes = places((url,)) if first else places()
    round_text = "first" if first else "second"
    election = _election(
        p,
        "first" if first else "runoff",
        DAYS[day],
        "general" if first else "runoff",
        f"2021 Ecuadorian presidential election, {round_text} round",
        f"Elecciones presidenciales del Ecuador 2021, {'primera' if first else 'segunda'} vuelta",
        f"ec/{day}-presidential" + ("" if first else "-runoff"),
    )
    contest = key("contest", day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=1 if first else 2,
        previous_round_id=None if first else key("contest", "2021-02-07", "president"),
        default_result_kind="counted",
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    p.name(contest, f"2021 Ecuadorian presidential election, {round_text} round", "en", "official")
    p.name(
        contest,
        f"Elecciones presidenciales del Ecuador 2021, {'primera' if first else 'segunda'} vuelta",
        "es",
        "native",
        False,
    )
    p.add(
        "slug",
        path=f"ec/{day}-presidential" + ("" if first else "-runoff") + "/president",
        entity_id=contest,
        is_current=True,
    )
    result_set = _result_set(p, election, contest, "president")
    codes = sorted({code(v) for v in frame["OP_CODIGO"].to_list()}, key=lambda c: (c not in RUNOFF, int(c)))
    if not first:
        codes = list(RUNOFF)
    options: dict[str, str] = {}
    for position, org in enumerate(codes, 1):
        option = options[org] = key("option", "president", org) if not first else key("option", day, "president", org)
        who = (FIRST_NAMED if first else RUNOFF).get(org)
        label = f"Organization {org}"
        if who:
            name, mate, party = who
            label = f"{name} and {mate} ({party})" if mate else name
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=position,
            ballot_label=label,
        )
        if not who:
            continue
        tag = "president-2021-first" if first else "president-2021"
        person = key("person", tag, org)
        p.add("person", id=person)
        p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", "president" if not first else f"{day}-president", org),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        if mate:
            mate_person = key("person", f"{tag}-running-mate", org)
            p.add("person", id=mate_person)
            p.name(mate_person, mate, "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", "president-running-mate" if not first else f"{day}-president-running-mate", org),
                person_id=mate_person,
                option_id=option,
                role="running_mate",
                ballot_name=mate,
            )
    table = _table(frame, [], "OP_CODIGO")
    seen: set[str] = set()
    for parish_rows in table.partition_by("PARROQUIA_CODIGO", as_dict=False):
        row = parish_rows.row(0, named=True)
        unit = _unit(p, day, code(row["PARROQUIA_CODIGO"]), parishes, seen)
        _put(p, result_set, unit, None, CAST, int(row["SUFRAGANTES"]))
        _put(p, result_set, unit, None, BLANK, int(row["BLANCOS"]))
        _put(p, result_set, unit, None, INVALID, int(row["NULOS"]))
        _put(p, result_set, unit, None, VALID, int(parish_rows["VOTOS"].sum()))
        for org, votes in zip(parish_rows["OP_CODIGO"].to_list(), parish_rows["VOTOS"].to_list(), strict=True):
            _put(p, result_set, unit, options[code(org)], VOTES, int(votes))
    if first:
        p.accept(
            "accounting.ballots_sum",
            "the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots "
            "cast in 740 of 1,299 parishes, by 78 ballots at most and 2,505 ballots in all; stored as published",
        )
    else:
        p.accept(
            "accounting.ballots_sum",
            "the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots "
            "cast in 528 of 1,299 parishes, by 83 ballots at most and 1,100 ballots in all; stored as published",
        )


def build(day: str) -> Package:
    p = Package(
        f"ec.cne/{day}",
        SOURCE,
        f"The Ecuadorian elections of {DAYS[day]} by parish, from the National Electoral Council's results files",
        depends=["ec.cne/geo", "ec.cne/registry", *(["ec.cne/2021-02-07"] if day == "2021-04-11" else [])],
    )
    p.external.add(ECUADOR)
    if day == "2019-03-24":
        _local(p, day)
    else:
        _president(p, day, day == "2021-02-07")
    return p
