"""Ecuador's sectional elections of 5 February 2023, from the National Electoral Council's parish-level results file.

The Council publishes one SPSS file for the day. It has one row per parish, sex group of polling stations, contest,
and option: the provincial prefects, the mayors, the urban and rural municipal councillors, the rural parish boards,
the three contests for the Council of Citizen Participation and Social Control, a parish popular consultation in
Sevilla Don Bosco, and the eight referendum questions. The referendum questions are loaded by `ec.referendums`, and
this module reads every other contest. The two sex groups are added here.

Councils and parish boards were elected from closed lists, and the file gives the list votes only. It names every
prefect, mayor, and Citizen Participation candidate, but declares no winners. The Council's list of elected
authorities does: the prefects, the mayors, and the urban and rural councillors and parish board members by seat.
Those declarations are stored as outcomes. The list does not cover the Citizen Participation Council, so those three
contests have none.
"""

import functools
import re
import unicodedata
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne, cne2017

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fECU"))
DAY = "2023-02-05"
DATE = date(2023, 2, 5)
FILE = "https://www.cne.gob.ec/download/resultados-electorales-2023/?wpdmdl=39834"
VOTES, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
area = cne.area
CNE_SOURCE = cne.CNE_SOURCE
WINNERS = "https://www.cne.gob.ec/wp-content/uploads/2024/07/Autoridades-electas-Elecciones-Seccionales-20231-1.xlsx"
SCHEMES = ("ec.provincia", "ec.canton", "ec.parroquia")
BASE = f"ec/{DAY}-local"

PREFECT, RURAL, MAYOR, URBAN, BOARD = 11, 12, 13, 14, 15
CPCCS_WOMEN, CPCCS_MEN, CPCCS_PEOPLES = 16, 17, 18
CONSULTATION = 30
# the file's referendum questions, which `ec.referendums` loads
REFERENDUM = range(19, 27)

CPCCS = key("body", "cpccs")
SYSTEM_CLOSED = key("electoral_system", "local", "webster", "closed")
SYSTEM_QUESTION = key("electoral_system", "local", "question")

METHOD = (
    "Counted by polling station and published by the National Electoral Council in its parish-level results file "
    "for the 2023 sectional elections. Each parish's stations are grouped into men's and women's stations, and the "
    "parish figures here add the two groups. Ballots cast are the voters who took part, and blank and null ballots "
    "are counted separately. Where each voter casts one vote, valid votes are the votes the options received."
)
METHOD_MULTI = (
    "Counted by polling station and published by the National Electoral Council in its parish-level results file "
    "for the 2023 sectional elections. Each parish's stations are grouped into men's and women's stations, and the "
    "parish figures here add the two groups. Ballots cast are the voters who took part, and blank and null ballots "
    "are counted separately. A voter could mark more than one candidate in this contest, so the candidates' votes "
    "add up to more than the ballots, and no valid-ballot figure is stored."
)
NOTE_CLOSED = (
    "Each voter chooses one list. Seats go to lists by the Webster method, which divides list totals by the odd "
    "numbers, and within a list the candidates take the seats in the order the list prints them."
)
NOTE_QUESTION = "The answer with more valid votes than the other carries the question."
NOTE_CPCCS = (
    "The Council of Citizen Participation and Social Control has seven members, elected by popular vote from "
    "candidates who run without party lists: one contest for women, one for men, and one for candidates from the "
    "indigenous peoples and nationalities, Afro-Ecuadorians, Montubios, and Ecuadorians abroad."
)


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".sav")
    raw.fetch(SOURCE, WINNERS, suffix=".xlsx")


def packages() -> list[str]:
    return [f"{SOURCE}/geo-2023", f"{SOURCE}/registry-2023", f"{SOURCE}/{DAY}"]


def parse() -> None:
    geo().write()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def sav() -> tuple[pl.DataFrame, dict[str, dict[float, str]]]:
    return cne.sav(FILE)


@functools.cache
def places() -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Provinces, cantons, and parishes of the file, by code, with the names its labels give."""
    frame, labels = sav()
    provinces: dict[str, str] = {}
    cantons: dict[str, tuple[str, str]] = {}
    parishes: dict[str, tuple[str, str]] = {}
    for row in frame.select("PROVINCIA_CODIGO", "CANTON_CODIGO", "PARROQUIA_CODIGO").unique().iter_rows():
        province, canton, parish = (cne.code(v) for v in row)
        provinces.setdefault(province, cne.clean(labels["PROVINCIA_NOMBRE"][float(province)]))
        name = cne.clean(labels["CANTON_NOMBRE"][float(canton)])
        if cantons.setdefault(canton, (name, province))[1] != province:
            raise ValueError(f"canton {canton} sits in two provinces")
        name = cne.clean(labels["PARROQUIA_NOMBRE"][float(parish)])
        if parishes.setdefault(parish, (name, canton))[1] != canton:
            raise ValueError(f"parish {parish} sits in two cantons")
    return provinces, cantons, parishes


def province_name(code: str) -> str:
    return cne2017.PROVINCE_NAMES.get(code) or cne.title(places()[0][code])


def canton_name(code: str) -> str:
    return cne.title(places()[1][code][0])


def parish_name(code: str) -> str:
    return cne.title(places()[2][code][0])


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-2023",
        SOURCE,
        "The provinces, cantons, parishes, and overseas units that Ecuador's 2023 sectional results file lists",
    )
    p.external.update([ECUADOR, *SCHEMES])
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Election results files and books published by the National Electoral Council",
        url=cne.PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral del Ecuador",
    )
    p.artifact(cne.artifact(FILE), CNE_SOURCE)
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
            p.name(id, cne.title(text), "es", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-2023",
        SOURCE,
        "Ecuador's Council of Citizen Participation and Social Control, and the closed-list and question systems of "
        "the 2023 sectional elections",
    )
    p.external.add(ECUADOR)
    p.add("body", id=CPCCS, polity_id=ECUADOR, kind="council")
    p.name(CPCCS, "Council of Citizen Participation and Social Control", "en", "official")
    p.name(CPCCS, "Consejo de Participación Ciudadana y Control Social", "es", "native", False)
    p.add("note", id=key("note", CPCCS), entity_id=CPCCS, kind="context", body_md=NOTE_CPCCS, lang="en")
    for id, rules, text in (
        (
            SYSTEM_CLOSED,
            {"formula": "sainte_lague", "lists": {"order": "closed", "vacancy": "substitute"}},
            NOTE_CLOSED,
        ),
        (SYSTEM_QUESTION, {"formula": "referendum"}, NOTE_QUESTION),
    ):
        p.add("electoral_system", id=id, polity_id=ECUADOR, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


class Tally:
    """The ballot-accounting disagreements the file's own parish figures hold, over the single-vote contests."""

    def __init__(self) -> None:
        self.rows = 0
        self.most = 0
        self.total = 0
        self.of = 0

    def add(self, cast: int, valid: int, blank: int, null: int) -> None:
        self.of += 1
        gap = abs(valid + blank + null - cast)
        if gap:
            self.rows += 1
            self.most = max(self.most, gap)
            self.total += gap


def label(row: dict[str, object]) -> str:
    """A list's ballot label: its organization, initials, and list number, as the file gives them."""
    name = cne.clean(row["OP_NOMBRE"])
    initials = cne.clean(row["OP_SIGLAS"])
    number = cne.clean(row["OP_LISTA"])
    parts = [initials] if initials and initials != "SIN SIGLAS" else []
    if number:
        parts.append(f"list {number}")
    return f"{name} ({', '.join(parts)})" if parts else name


class Builder:
    def __init__(self, p: Package) -> None:
        self.p = p
        self.election = key("election", DAY, "local")
        self.tally = Tally()
        self.paths: set[str] = set()
        self.units: dict[tuple[str, str], str] = {}
        self.contests = 0
        self.heads: dict[str, dict[str, dict[str, str]]] = {}
        self.sets: dict[str, str] = {}
        self.declared: dict[str, int] = {}

    def path(self, *candidates: str) -> str:
        for tail in candidates:
            path = f"{BASE}/{tail}"
            if path not in self.paths:
                self.paths.add(path)
                return path
        raise ValueError(f"no free path among {candidates}")

    def unit(self, parish: str, circ: str = "") -> str:
        token = (parish, circ)
        if token not in self.units:
            tail = f":{circ}" if circ else ""
            unit = self.units[token] = key("unit", DAY, "parish", parish + tail)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=ECUADOR,
                scheme_code="ec.parroquia",
                code=f"{DAY}:{parish}{tail}",
                area_relation="within" if circ else "equals",
                area_id=area("ec.parroquia", parish),
                kind="standard",
            )
            name = parish_name(parish)
            self.p.name(unit, f"{name}, circunscripción {circ}" if circ else name, "es", "official")
        return self.units[token]

    def contest(
        self, token: str, english: str, spanish: str, paths: tuple[str, ...], kind: str, **fields: object
    ) -> tuple[str, str]:
        p = self.p
        contest = key("contest", DAY, token)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            round_number=1,
            default_result_kind="counted",
            **fields,
        )
        p.name(contest, english, "en", "official")
        p.name(contest, spanish, "es", "native", False)
        p.add("slug", path=self.path(*paths), entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, token)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        multi = fields.get("body_id") == CPCCS and token != "cpccs/peoples"
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD_MULTI if multi else METHOD,
            lang="en",
        )
        self.contests += 1
        self.sets[token] = result_set
        return contest, result_set

    def values(
        self, result_set: str, frame: pl.DataFrame, column: str, options: dict[str, str], single: bool, circ: str = ""
    ) -> None:
        """Store each parish's votes and accounting from the file's rows of one contest.

        The rows are added up by circumscription first, so a parish that sits in two circumscriptions keeps each
        group's accounting once; with `circ`, the contest covers one circumscription, and a parish split across
        several gets a unit for its part.
        """
        p = self.p
        table = cne._table(frame, ["CIRCUNSCRIPCION_CODIGO"], column)
        table = table.group_by("PARROQUIA_CODIGO", column).agg(pl.col("VOTOS", "SUFRAGANTES", "BLANCOS", "NULOS").sum())
        for parish_rows in table.sort("PARROQUIA_CODIGO", column).partition_by("PARROQUIA_CODIGO", as_dict=False):
            row = parish_rows.row(0, named=True)
            parish = cne.code(row["PARROQUIA_CODIGO"])
            unit = self.unit(parish, circ if circ and parish in split() else "")
            cast, blank, null = int(row["SUFRAGANTES"]), int(row["BLANCOS"]), int(row["NULOS"])
            cne._put(p, result_set, unit, None, CAST, cast)
            cne._put(p, result_set, unit, None, BLANK, blank)
            cne._put(p, result_set, unit, None, INVALID, null)
            if single:
                valid = int(parish_rows["VOTOS"].sum())
                cne._put(p, result_set, unit, None, VALID, valid)
                self.tally.add(cast, valid, blank, null)
            for option, votes in zip(parish_rows[column].to_list(), parish_rows["VOTOS"].to_list(), strict=True):
                cne._put(p, result_set, unit, options[str(option)], VOTES, int(votes))


@functools.cache
def split() -> frozenset[str]:
    """Parishes the urban council file splits across more than one circumscription."""
    frame = sav()[0].filter(pl.col("DIGNIDAD_CODIGO") == URBAN)
    spread = frame.group_by("PARROQUIA_CODIGO").agg(pl.col("CIRCUNSCRIPCION_CODIGO").n_unique())
    return frozenset(cne.code(v) for v in spread.filter(pl.col("CIRCUNSCRIPCION_CODIGO") > 1)["PARROQUIA_CODIGO"])


def rows(dignity: int) -> pl.DataFrame:
    frame = sav()[0].filter(pl.col("DIGNIDAD_CODIGO") == float(dignity))
    return frame.with_columns(
        pl.col("OP_CODIGO").map_elements(cne.code, return_dtype=pl.String).alias("_option"),
        pl.col("CANDIDATO_NOMBRE").map_elements(cne.clean, return_dtype=pl.String),
    )


def candidates(b: Builder, dignity: int, token: str, frame: pl.DataFrame, contest: str) -> dict[str, str]:
    """One candidate option per organization, with its candidate as a person, in the order of the list numbers."""
    p = b.p
    heads = frame.group_by("_option").agg(
        pl.col("CANDIDATO_NOMBRE").first(),
        pl.col("CANDIDATO_NOMBRE").n_unique().alias("_names"),
        pl.col("OP_NOMBRE", "OP_SIGLAS", "OP_LISTA").first(),
    )
    if heads.filter(pl.col("_names") > 1).height:
        raise ValueError(f"{token}: an organization runs two candidates")
    options: dict[str, str] = {}
    index = b.heads[token] = {"name": {}, "number": {}, "org": {}}
    ordered = heads.sort(pl.col("OP_LISTA").str.extract(r"^(\d+)", 1).cast(pl.Int64), "_option")
    for position, row in enumerate(ordered.iter_rows(named=True), 1):
        org = row["_option"]
        option = options[org] = key("option", DAY, token, org)
        index["name"][fold(row["CANDIDATO_NOMBRE"])] = org
        index["number"][fold(row["OP_LISTA"])] = org
        index["org"][fold(row["OP_NOMBRE"])] = org
        name = cne.title(row["CANDIDATO_NOMBRE"])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=f"{name}, {label(row)}",
        )
        person = key("person", DAY, token, org)
        p.add("person", id=person)
        p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", DAY, token, org),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    b.values(contest_set(token), frame, "_option", options, True)
    return options


def contest_set(token: str) -> str:
    return key("result_set", DAY, token)


def lists(b: Builder, token: str, frame: pl.DataFrame, contest: str, circ: str = "") -> None:
    p = b.p
    heads = frame.group_by("_option").agg(
        pl.col("OP_NOMBRE", "OP_SIGLAS", "OP_LISTA").first(),
        pl.col("OP_LISTA").n_unique().alias("_lists"),
    )
    if heads.filter(pl.col("_lists") > 1).height:
        raise ValueError(f"{token}: an organization holds two lists")
    options: dict[str, str] = {}
    index = b.heads[token] = {"number": {}, "org": {}}
    ordered = heads.sort(pl.col("OP_LISTA").str.extract(r"^(\d+)", 1).cast(pl.Int64), "_option")
    for position, row in enumerate(ordered.iter_rows(named=True), 1):
        org = row["_option"]
        option = options[org] = key("option", DAY, token, org)
        index["number"][fold(row["OP_LISTA"])] = org
        index["org"][fold(row["OP_NOMBRE"])] = org
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=label(row),
        )
    b.values(contest_set(token), frame, "_option", options, True, circ)


def fold(text: object) -> str:
    """Upper case, without accents or punctuation, for matching names across the Council's two files."""
    ascii_text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", ascii_text.upper().replace("%", "PCT ")).strip()


@functools.cache
def winners() -> dict[str, list[dict[str, str]]]:
    """The elected authorities, by sheet, one dictionary per row keyed by the folded column names."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, WINNERS).path))
    out: dict[str, list[dict[str, str]]] = {}
    for name in book.sheet_names:
        sheet = book.load_sheet(name, header_row=None, dtypes="string").to_polars()
        header = [fold(v) for v in sheet.row(2)]
        out[name] = [
            {h: cne.clean(v) for h, v in zip(header, row, strict=True)}
            for row in sheet.slice(3).iter_rows()
            if any(row)
        ]
    return out


@functools.cache
def lookup() -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """Province codes by folded name, cantons by province and name, and parishes by canton and name."""
    provinces, cantons, parishes = places()
    by_province = {fold(n): c for c, n in provinces.items()}
    by_canton = {(p, fold(n)): c for c, (n, p) in cantons.items()}
    by_parish = {(k, fold(n)): c for c, (n, k) in parishes.items()}
    return by_province, by_canton, by_parish


def found(sheet: str, row: dict[str, str]) -> tuple[str, str, str]:
    """A winner row's province, canton, and parish codes; the codes it does not carry are empty."""
    by_province, by_canton, by_parish = lookup()
    province = by_province.get(fold(row["PROVINCIA"]))
    if province is None:
        raise ValueError(f"{sheet}: province {row['PROVINCIA']!r} is not in the results file")
    canton = parish = ""
    if "CANTON" in row:
        canton = by_canton.get((province, fold(row["CANTON"])), "")
        if not canton:
            raise ValueError(f"{sheet}: canton {row['CANTON']!r} is not in the results file")
    if "PARROQUIA" in row:
        parish = by_parish.get((canton, fold(row["PARROQUIA"])), "")
        if not parish:
            raise ValueError(f"{sheet}: parish {row['PARROQUIA']!r} is not in the results file")
    return province, canton, parish


def winner(b: Builder, token: str, row: dict[str, str]) -> str:
    """The organization a winner row names within one contest, by candidate, then list number, then name."""
    index = b.heads[token]
    number = row.get("LISTA") or row.get("OP LISTA")
    for field, text in (
        ("name", row.get("CANDIDATO NOMBRE")),
        ("number", number),
        ("org", row["ORGANIZACION POLITICA"]),
    ):
        if field in index and (found := index[field].get(fold(text))) is not None:
            return found
    raise ValueError(f"{token}: no option matches {row}")


def declare(b: Builder) -> None:
    """Store the Council's declared winners as outcomes of the contests built from its results file."""
    p = b.p
    book = winners()
    for sheet, level, prefix in (
        ("PREFECTA_PREFECTO", "province", "prefect"),
        ("ALCALDESA_ALCALDE", "canton", "mayor"),
    ):
        done: set[str] = set()
        for row in book[sheet]:
            province, canton, _ = found(sheet, row)
            token = f"{prefix}/{province if level == 'province' else canton}"
            if token in done or token not in b.heads:
                raise ValueError(f"{sheet}: {token} has no contest or two winners")
            done.add(token)
            org = winner(b, token, row)
            p.add(
                "outcome",
                result_set_id=b.sets[token],
                option_id=key("option", DAY, token, org),
                kind="elected",
            )
        b.declared[prefix] = len(done)
    for sheet, which in (
        ("CONCEJALES URBANOS", "urban"),
        ("CONCEJALES RURALES", "rural"),
        ("VOCALES JUNTAS PARROQUIALES", "board"),
    ):
        seats: dict[tuple[str, str], list[tuple[str, dict[str, str]]]] = {}
        for row in book[sheet]:
            province, canton, parish = found(sheet, row)
            if which == "board":
                token = f"parish-board/{parish}"
            else:
                circ = circumscription(canton, row.get("CIRCUNSCRIPCION", "")) if which == "urban" else "0"
                token = f"{which}-council/{canton}" + (f"/{circ}" if circ != "0" else "")
            if token not in b.heads:
                raise ValueError(f"{sheet}: {token} has no contest")
            seats.setdefault((token, winner(b, token, row)), []).append((token, row))
        for (token, org), members in sorted(seats.items()):
            option = key("option", DAY, token, org)
            p.add("outcome", result_set_id=b.sets[token], option_id=option, kind="won", seats=len(members))
            for token, row in sorted(members, key=lambda m: int(m[1].get("ESCANIO") or m[1]["ESCANO"])):
                number = int(row.get("ESCANIO") or row["ESCANO"])
                person = key("person", DAY, token, org, number)
                name = cne.title(row["CANDIDATO NOMBRE"])
                p.add("person", id=person)
                p.name(person, name, "es", "official")
                candidacy = key("candidacy", DAY, token, org, number)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=number,
                    ballot_name=name,
                )
                p.add(
                    "outcome",
                    result_set_id=b.sets[token],
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                )
        b.declared[which] = len(seats)


@functools.cache
def circumscriptions() -> dict[tuple[str, str], str]:
    """The urban circumscription codes of each canton, by the folded name the results file gives them."""
    frame = sav()[0].filter(pl.col("DIGNIDAD_CODIGO") == URBAN)
    out: dict[tuple[str, str], str] = {}
    for canton, circ, name in (
        frame.select("CANTON_CODIGO", "CIRCUNSCRIPCION_CODIGO", "CIRCUNSCRIPCION_NOMBRE").unique().iter_rows()
    ):
        out[(cne.code(canton), fold(name))] = cne.code(circ)
    return out


def circumscription(canton: str, name: str) -> str:
    if not name:
        return "0"
    try:
        return circumscriptions()[(canton, fold(name))]
    except KeyError:
        raise ValueError(f"canton {canton} has no circumscription {name!r}") from None


def prefects(b: Builder) -> None:
    frame = rows(PREFECT)
    for code in sorted({cne.code(v) for v in frame["PROVINCIA_CODIGO"].to_list()}, key=int):
        name = province_name(code)
        token = f"prefect/{code}"
        contest, _ = b.contest(
            token,
            f"{name} prefect and vice prefect, 2023",
            f"Prefecto y viceprefecto de {name} 2023",
            (f"prefect/{cne.slug(name)}",),
            "office",
            office_id=cne.PREFECTURE,
            magnitude=1,
            district_area_id=area("ec.provincia", code),
            electoral_system_id=cne.SYSTEM_PLURALITY,
        )
        candidates(b, PREFECT, token, frame.filter(pl.col("PROVINCIA_CODIGO") == float(code)), contest)


def mayors(b: Builder) -> None:
    frame = rows(MAYOR)
    for code in sorted({cne.code(v) for v in frame["CANTON_CODIGO"].to_list()}, key=int):
        name = canton_name(code)
        province = province_name(places()[1][code][1])
        token = f"mayor/{code}"
        contest, _ = b.contest(
            token,
            f"{name} mayor, 2023",
            f"Alcalde de {name} 2023",
            (f"mayor/{cne.slug(name)}", f"mayor/{cne.slug(name)}-{cne.slug(province)}"),
            "office",
            office_id=cne.MAYORALTY,
            magnitude=1,
            district_area_id=area("ec.canton", code),
            electoral_system_id=cne.SYSTEM_PLURALITY,
        )
        candidates(b, MAYOR, token, frame.filter(pl.col("CANTON_CODIGO") == float(code)), contest)


def councils(b: Builder) -> None:
    for dignity, which, spanish_word in ((URBAN, "urban", "urbanos"), (RURAL, "rural", "rurales")):
        frame = rows(dignity)
        groups = sorted(
            {
                (cne.code(c), cne.code(n))
                for c, n in frame.select("CANTON_CODIGO", "CIRCUNSCRIPCION_CODIGO").iter_rows()
            },
            key=lambda item: (int(item[0]), int(item[1])),
        )
        for code, circ in groups:
            name = canton_name(code)
            province = province_name(places()[1][code][1])
            ward = circ != "0"
            token = f"{which}-council/{code}" + (f"/{circ}" if ward else "")
            part = frame.filter(
                (pl.col("CANTON_CODIGO") == float(code)) & (pl.col("CIRCUNSCRIPCION_CODIGO") == float(circ))
            )
            english = f"{name} {which} councillors" + (f", urban circumscription {circ}" if ward else "") + ", 2023"
            spanish = f"Concejales {spanish_word} de {name} 2023" + (f", circunscripción urbana {circ}" if ward else "")
            tail = f"{which}-council/{cne.slug(name)}"
            contest, _ = b.contest(
                token,
                english,
                spanish,
                (f"{tail}-{circ}", f"{tail}-{cne.slug(province)}-{circ}")
                if ward
                else (tail, f"{tail}-{cne.slug(province)}"),
                "list_allocation",
                body_id=cne.COUNCIL,
                district_area_id=area("ec.canton", code),
                electoral_system_id=SYSTEM_CLOSED,
            )
            lists(b, token, part, contest, circ if ward else "")


def boards(b: Builder) -> None:
    frame = rows(BOARD)
    for code in sorted({cne.code(v) for v in frame["PARROQUIA_CODIGO"].to_list()}, key=int):
        name = parish_name(code)
        canton = canton_name(places()[2][code][1])
        token = f"parish-board/{code}"
        contest, _ = b.contest(
            token,
            f"{name} parish board, {canton}, 2023",
            f"Junta parroquial de {name}, {canton} 2023",
            (f"parish-board/{cne.slug(canton)}-{cne.slug(name)}", f"parish-board/{cne.slug(name)}-{code}"),
            "list_allocation",
            body_id=cne.PARISH_BOARD,
            district_area_id=area("ec.parroquia", code),
            electoral_system_id=SYSTEM_CLOSED,
        )
        lists(b, token, frame.filter(pl.col("PARROQUIA_CODIGO") == float(code)), contest)


def cpccs(b: Builder) -> None:
    p = b.p
    for dignity, tail, english, spanish in (
        (CPCCS_WOMEN, "women", "women's seats", "mujeres"),
        (CPCCS_MEN, "men", "men's seats", "hombres"),
        (
            CPCCS_PEOPLES,
            "peoples",
            "seats of peoples and nationalities and Ecuadorians abroad",
            "pueblos y nacionalidades y exterior",
        ),
    ):
        token = f"cpccs/{tail}"
        contest, result_set = b.contest(
            token,
            f"2023 Ecuadorian election of the Council of Citizen Participation and Social Control, {english}",
            f"Consejo de Participación Ciudadana y Control Social 2023, {spanish}",
            (f"cpccs-{tail}",),
            "body_seats",
            body_id=CPCCS,
            district_area_id=COUNTRY,
        )
        frame = rows(dignity)
        names = sorted(set(frame["CANDIDATO_NOMBRE"].to_list()))
        options: dict[str, str] = {}
        for position, text in enumerate(names, 1):
            option = options[text] = key("option", DAY, token, text)
            name = cne.title(text)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            person = key("person", DAY, token, text)
            p.add("person", id=person)
            p.name(person, name, "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, token, text),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
            )
        b.values(result_set, frame, "CANDIDATO_NOMBRE", options, dignity == CPCCS_PEOPLES)


def consultation(b: Builder) -> None:
    p = b.p
    frame = rows(CONSULTATION)
    parishes = {cne.code(v) for v in frame["PARROQUIA_CODIGO"].to_list()}
    if len(parishes) != 1:
        raise ValueError(f"the parish consultation covers {sorted(parishes)}")
    parish = parishes.pop()
    name = parish_name(parish)
    token = "consultation/sevilla-don-bosco"
    contest, result_set = b.contest(
        token,
        f"{name} parish popular consultation, 2023",
        f"Consulta popular parroquial de {name} 2023",
        (f"consultation/{cne.slug(name)}",),
        "ballot_question",
        district_area_id=area("ec.parroquia", parish),
        electoral_system_id=SYSTEM_QUESTION,
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    options: dict[str, str] = {}
    for position, (text, es, en) in enumerate((("SI", "Sí", "Yes"), ("NO", "No", "No")), 1):
        option = options[text] = key("option", DAY, token, text)
        p.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=es)
        p.name(option, es, "es", "ballot")
        p.name(option, en, "en", "english", False)
    if set(frame["CANDIDATO_NOMBRE"].to_list()) != set(options):
        raise ValueError("the parish consultation has answers other than yes and no")
    b.values(result_set, frame, "CANDIDATO_NOMBRE", options, True)


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Ecuador's 2023 sectional elections and Citizen Participation Council election by parish, from the National "
        "Electoral Council's results file",
        depends=[f"{SOURCE}/geo-2023", f"{SOURCE}/registry", f"{SOURCE}/registry-2023"],
    )
    p.external.update([ECUADOR, COUNTRY, *SCHEMES])
    p.uses(cne.artifact(FILE))
    known = {PREFECT, RURAL, MAYOR, URBAN, BOARD, CPCCS_WOMEN, CPCCS_MEN, CPCCS_PEOPLES, CONSULTATION, *REFERENDUM}
    found = {int(v) for v in sav()[0]["DIGNIDAD_CODIGO"].unique().to_list()}
    if found - known:
        raise ValueError(f"contests {sorted(found - known)} are not read")
    b = Builder(p)
    p.add("election", id=b.election, polity_id=ECUADOR, kind="local", date_from=DATE, date_to=DATE, status="held")
    p.add("slug", path=BASE, entity_id=b.election, is_current=True)
    p.name(b.election, "2023 Ecuadorian sectional elections", "en", "official")
    p.name(b.election, "Elecciones seccionales del Ecuador 2023", "es", "native", False)
    prefects(b)
    mayors(b)
    councils(b)
    boards(b)
    cpccs(b)
    consultation(b)
    p.uses(cne.artifact(WINNERS))
    declare(b)
    t = b.tally
    if t.rows:
        p.accept(
            "accounting.ballots_sum",
            f"the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots "
            f"cast in {t.rows:,} of {t.of:,} parish contest rows, by {t.most:,} ballots at most and {t.total:,} "
            "ballots in all; stored as published",
        )
    return p
