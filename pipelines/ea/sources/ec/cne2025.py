"""Ecuador's 2025 general election and presidential runoff, from the National Electoral Council's parish-level files.

The Council publishes one SPSS file for the first round of 9 February 2025 (president, the national, provincial,
circumscription, and overseas members of the National Assembly, and the Andean Parliament) and one for the runoff of
13 April 2025. Each has one row per parish, sex group of polling stations, and option, and the two sex groups are added
here. A third file lists every candidate with the list votes and the seat the Council declared.
"""

import functools
from collections import Counter
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ec import cne, cne2017

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
DAY1, DAY2 = "2025-02-09", "2025-04-13"
DATES = {DAY1: date(2025, 2, 9), DAY2: date(2025, 4, 13)}
DOWNLOAD = "https://www.cne.gob.ec/download/"
FIRST = DOWNLOAD + "primera-vuelta-2/"
RUNOFF = DOWNLOAD + "segunda-vuelta-2/"
CANDIDATES = DOWNLOAD + "candidatos-4/"
FILES = (FIRST, RUNOFF, CANDIDATES)
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.REGISTERED, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
area = cne.area
CNE_SOURCE = cne.CNE_SOURCE
ASSEMBLY = cne2017.ASSEMBLY
ANDEAN = cne2017.ANDEAN
SYSTEM_LISTS = key("electoral_system", "assembly", "webster", "closed")
PRESIDENT, NATIONAL, PROVINCIAL, CIRCUMSCRIPTION, OVERSEAS, ANDEAN_CODE = 11, 12, 13, 14, 15, 16
SCHEMES = ("ec.provincia", "ec.canton", "ec.parroquia")

METHOD = (
    "Counted by polling station and published by the National Electoral Council in its parish-level results file. "
    "Each parish's stations are grouped into men's and women's stations, and the parish figures here add the two "
    "groups. Ballots cast are the voters who took part, and blank and null ballots are counted separately. Each "
    "voter casts one vote in each contest, for a ticket or for a list, so valid votes are the votes the options "
    "received."
)
NOTE_LISTS = (
    "Each voter chooses one list. Seats go to lists by the Webster method, which divides list totals by the odd "
    "numbers, and within a list the candidates take the seats in the order the list prints them."
)


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".sav")


def packages() -> list[str]:
    return [f"{SOURCE}/geo-2025", f"{SOURCE}/registry-2025", f"{SOURCE}/{DAY1}", f"{SOURCE}/{DAY2}"]


def parse() -> None:
    geo().write()
    registry().write()
    build(DAY1).write()
    build(DAY2).write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


def sav(url: str) -> tuple[pl.DataFrame, dict[str, dict[float, str]]]:
    return cne.sav(url)


@functools.cache
def places() -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Provinces, cantons, and parishes of the first-round file, by code, with the names its labels give."""
    frame, labels = sav(FIRST)
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


def province_english(code: str) -> str:
    return cne2017.PROVINCE_ENGLISH.get(code) or province_name(code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-2025",
        SOURCE,
        "The provinces, cantons, parishes, and overseas units that Ecuador's 2025 results files list",
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
    for url in FILES:
        p.artifact(cne.artifact(url), CNE_SOURCE)
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
        f"{SOURCE}/registry-2025",
        SOURCE,
        "Ecuador's National Assembly, its members of the Andean Parliament, and the closed-list system that filled "
        "their seats in 2025",
    )
    p.external.add(ECUADOR)
    p.add("body", id=ASSEMBLY, polity_id=ECUADOR, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Ecuador", "en", "official")
    p.name(ASSEMBLY, "Asamblea Nacional del Ecuador", "es", "native", False)
    p.add("body", id=ANDEAN, polity_id=ECUADOR, kind="unicameral")
    p.name(ANDEAN, "Ecuador's members of the Andean Parliament", "en", "official")
    p.name(ANDEAN, "Parlamentarios andinos del Ecuador", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_LISTS,
        polity_id=ECUADOR,
        rules={"formula": "sainte_lague", "lists": {"order": "closed", "vacancy": "substitute"}},
        rules_version=1,
    )
    p.add(
        "note", id=key("note", SYSTEM_LISTS), entity_id=SYSTEM_LISTS, kind="methodology", body_md=NOTE_LISTS, lang="en"
    )
    return p


def named(text: str) -> str:
    return cne.title(text)


class Parishes:
    """Parish units of one day: one per parish, and one per circumscription for a parish split across several."""

    def __init__(self, p: Package, day: str) -> None:
        self.p, self.day = p, day
        self.names = places()[2]
        self.made: dict[tuple[str, str], str] = {}

    def unit(self, parish: str, circ: str = "") -> str:
        token = (parish, circ)
        if token not in self.made:
            tail = f":{circ}" if circ else ""
            unit = self.made[token] = key("unit", self.day, "parish", parish + tail)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=ECUADOR,
                scheme_code="ec.parroquia",
                code=f"{self.day}:{parish}{tail}",
                area_relation="within" if circ else "equals",
                area_id=area("ec.parroquia", parish),
                kind="standard",
            )
            name = named(self.names[parish][0])
            self.p.name(unit, f"{name}, circunscripción {circ}" if circ else name, "es", "official")
        return self.made[token]


class Tally:
    """The ballot-accounting disagreements the file's own parish figures hold, over every contest of a package."""

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


class Builder:
    def __init__(self, p: Package, day: str, base: str) -> None:
        self.p, self.day, self.base = p, day, base
        self.election = key("election", day)
        self.parishes = Parishes(p, day)
        self.tally = Tally()
        self.paths: set[str] = set()

    def path(self, tail: str) -> str:
        path = f"{self.base}/{tail}"
        if path in self.paths:
            raise ValueError(f"two contests at {path}")
        self.paths.add(path)
        return path

    def contest(self, token: str, english: str, spanish: str, tail: str, kind: str, **fields: object) -> str:
        contest = key("contest", self.day, token)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            round_number=fields.pop("round_number", 1),
            default_result_kind="counted",
            **fields,
        )
        self.p.name(contest, english, "en", "official")
        self.p.name(contest, spanish, "es", "native", False)
        self.p.add("slug", path=self.path(tail), entity_id=contest, is_current=True)
        return contest

    def result_set(self, contest: str, token: str) -> str:
        result_set = key("result_set", self.day, token)
        self.p.add(
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
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
        )
        return result_set

    def values(
        self,
        result_set: str,
        frame: pl.DataFrame,
        options: dict[str, str],
        circ_split: set[str] | None = None,
        circ: str = "",
    ) -> None:
        """Store each parish's votes and accounting from the file's rows of one contest."""
        p = self.p
        group = ["CIRCUNSCRIPCION_CODIGO"] if circ_split else []
        table = cne._table(frame, group, "OP_CODIGO")
        for parish_rows in table.partition_by([*group, "PARROQUIA_CODIGO"], as_dict=False):
            row = parish_rows.row(0, named=True)
            parish = cne.code(row["PARROQUIA_CODIGO"])
            split = circ_split is not None and parish in circ_split
            unit = self.parishes.unit(parish, circ if split else "")
            cast, blank, null = int(row["SUFRAGANTES"]), int(row["BLANCOS"]), int(row["NULOS"])
            valid = int(parish_rows["VOTOS"].sum())
            cne._put(p, result_set, unit, None, CAST, cast)
            cne._put(p, result_set, unit, None, BLANK, blank)
            cne._put(p, result_set, unit, None, INVALID, null)
            cne._put(p, result_set, unit, None, VALID, valid)
            self.tally.add(cast, valid, blank, null)
            for org, votes in zip(parish_rows["OP_CODIGO"].to_list(), parish_rows["VOTOS"].to_list(), strict=True):
                cne._put(p, result_set, unit, options[cne.code(org)], VOTES, int(votes))


def declare(p: Package, result_set: str, options: dict[str, str], votes: dict[str, int]) -> None:
    for org, value in votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[org], measure=VOTES, value=value)


def surname_first(text: str) -> str:
    return cne.title(text)


GENDER = {"1": "male", "2": "female"}


def president(b: Builder, first: bool) -> None:
    p = b.p
    cand = sav(CANDIDATES)[0].filter(pl.col("DIGNIDAD_CODIGO") == PRESIDENT)
    frame = sav(FIRST if first else RUNOFF)[0].filter(pl.col("DIGNIDAD_CODIGO") == PRESIDENT)
    p.uses(cne.artifact(FIRST if first else RUNOFF))
    p.uses(cne.artifact(CANDIDATES))
    round_text = "first" if first else "second"
    contest = b.contest(
        "president",
        f"2025 Ecuadorian presidential election, {round_text} round",
        f"Elecciones presidenciales del Ecuador 2025, {'primera' if first else 'segunda'} vuelta",
        "president",
        "office",
        office_id=cne.PRESIDENCY,
        magnitude=1,
        round_number=1 if first else 2,
        previous_round_id=None if first else key("contest", DAY1, "president"),
        electoral_system_id=cne.SYSTEM_PRESIDENT,
    )
    result_set = b.result_set(contest, "president")
    heads = {cne.code(o): n for o, n in frame.select("OP_CODIGO", "CANDIDATO_NOMBRE").unique().iter_rows()}
    votes_column = "VOTOS_1V" if first else "VOTOS_2V"
    rows = cand.filter(pl.col(votes_column).is_not_null()).sort("OP_LISTA")
    options: dict[str, str] = {}
    declared: dict[str, int] = {}
    for position, row in enumerate(rows.iter_rows(named=True), 1):
        org = cne.code(row["OP_CODIGO"])
        name = named(heads[org])
        label = f"{name} ({cne.clean(row['OP_SIGLAS'])}, list {cne.clean(row['OP_LISTA'])})"
        option = options[org] = (
            key("option", DAY1, "president", org) if first else key("option", DAY2, "president", org)
        )
        p.add(
            "contest_option", id=option, contest_id=contest, kind="ticket", ballot_position=position, ballot_label=label
        )
        person = key("person", "president-2025", org)
        if first:
            p.add("person", id=person, gender=GENDER[cne.clean(row["CANDIDATO_SEXO"])])
            p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", b.day, "president", org),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        declared[org] = int(row[votes_column])
    # a ticket the results file lists must be a ticket the candidate file lists
    missing = set(heads) - set(options)
    if missing:
        raise ValueError(f"{b.day}: tickets {sorted(missing)} are in the results and not in the candidate file")
    b.values(result_set, frame, options)
    ordered = sorted(declared, key=lambda org: -declared[org])
    for rank, org in enumerate(ordered):
        if first:
            kind = "advanced" if rank < 2 else "eliminated"
        else:
            kind = "elected" if rank == 0 else "lost"
        p.add("outcome", result_set_id=result_set, option_id=options[org], kind=kind)
    declare(p, result_set, options, declared)


def lists(
    b: Builder,
    token: str,
    english: str,
    spanish: str,
    tail: str,
    body: str,
    frame: pl.DataFrame,
    cand: pl.DataFrame,
    circ_split: set[str] | None = None,
    circ: str = "",
) -> None:
    """One list-allocation contest: its lists, the seats and members each won, and its parishes' votes."""
    p = b.p
    seats = int(cand["ESTADO"].sum())
    contest = b.contest(
        token,
        english,
        spanish,
        tail,
        "list_allocation",
        body_id=body,
        magnitude=seats,
        electoral_system_id=SYSTEM_LISTS,
    )
    result_set = b.result_set(contest, token)
    lists_ = (
        cand.group_by("OP_CODIGO")
        .agg(
            pl.col("OP_NOMBRE").first(),
            pl.col("OP_SIGLAS").first(),
            pl.col("OP_LISTA").first(),
            pl.col("VOTOS_1V").first(),
            pl.col("VOTOS_1V").n_unique().alias("_distinct"),
        )
        .sort(pl.col("OP_LISTA").str.extract(r"^(\d+)", 1).cast(pl.Int64), "OP_CODIGO")
    )
    if lists_.filter(pl.col("_distinct") > 1).height:
        raise ValueError(f"{b.day} {token}: a list's candidates carry different list votes")
    options: dict[str, str] = {}
    declared: dict[str, int] = {}
    for position, row in enumerate(lists_.iter_rows(named=True), 1):
        org = cne.code(row["OP_CODIGO"])
        option = options[org] = key("option", b.day, token, org)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=f"{cne.clean(row['OP_NOMBRE'])} ({cne.clean(row['OP_SIGLAS'])}, list {cne.clean(row['OP_LISTA'])})",
        )
        declared[org] = int(row["VOTOS_1V"])
    elected = cand.filter(pl.col("ESTADO") == 1)
    for org, count in Counter(cne.code(o) for o in elected["OP_CODIGO"].to_list()).items():
        p.add("outcome", result_set_id=result_set, option_id=options[org], kind="won", seats=count)
    for row in elected.iter_rows(named=True):
        org = cne.code(row["OP_CODIGO"])
        name = named(cne.clean(row["CANDIDATO_NOMBRE"]))
        person = key("person", b.day, token, cne.code(row["CANDIDATO_CODIGO"]))
        p.add("person", id=person, gender=GENDER[cne.clean(row["CANDIDATO_SEXO"])])
        p.name(person, name, "es", "official")
        candidacy = key("candidacy", b.day, token, cne.code(row["CANDIDATO_CODIGO"]))
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[org],
            role="list_member",
            ballot_name=name,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=options[org],
            candidacy_id=candidacy,
            kind="elected",
        )
    missing = {cne.code(o) for o in frame["OP_CODIGO"].to_list()} - set(options)
    if missing:
        raise ValueError(f"{b.day} {token}: lists {sorted(missing)} are in the results and not in the candidate file")
    b.values(result_set, frame, options, circ_split, circ)
    declare(p, result_set, options, declared)


def assembly(b: Builder) -> None:
    results = sav(FIRST)[0]
    cand_all = sav(CANDIDATES)[0]
    # parishes split across circumscriptions, by province, in the circumscription contests
    circ_rows = results.filter(pl.col("DIGNIDAD_CODIGO") == CIRCUMSCRIPTION)
    spread = circ_rows.group_by("PARROQUIA_CODIGO").agg(pl.col("CIRCUNSCRIPCION_CODIGO").n_unique())
    split = {cne.code(v) for v in spread.filter(pl.col("CIRCUNSCRIPCION_CODIGO") > 1)["PARROQUIA_CODIGO"].to_list()}

    def votes(dignity: int, province: int | None = None, circ: int | None = None) -> pl.DataFrame:
        frame = results.filter(pl.col("DIGNIDAD_CODIGO") == dignity)
        if province is not None:
            frame = frame.filter(pl.col("PROVINCIA_CODIGO") == province)
        if circ is not None:
            frame = frame.filter(pl.col("CIRCUNSCRIPCION_CODIGO") == circ)
        return frame

    def people(dignity: int, province: int | None = None, circ: int | None = None) -> pl.DataFrame:
        frame = cand_all.filter(pl.col("DIGNIDAD_CODIGO") == dignity)
        if province is not None:
            frame = frame.filter(pl.col("CANDIDATO_PROVINCIA_CODIGO") == province)
        if circ is not None:
            frame = frame.filter(pl.col("CANDIDATO_CIRCUNSCRIPCION_CODIGO") == circ)
        return frame

    lists(
        b,
        "national",
        "2025 Ecuadorian National Assembly election, national seats",
        "Asambleístas nacionales del Ecuador 2025",
        "national-assembly",
        ASSEMBLY,
        votes(NATIONAL),
        people(NATIONAL),
    )
    for code in sorted(votes(PROVINCIAL)["PROVINCIA_CODIGO"].unique().to_list()):
        province = cne.code(code)
        name = province_name(province)
        lists(
            b,
            f"province/{province}",
            f"2025 Ecuadorian National Assembly election, {name} provincial seats",
            f"Asambleístas provinciales de {name} 2025",
            f"provincial/{cne.slug(name)}",
            ASSEMBLY,
            votes(PROVINCIAL, int(code)),
            people(PROVINCIAL, int(code)),
        )
    for code, number in sorted(circ_rows.select("PROVINCIA_CODIGO", "CIRCUNSCRIPCION_CODIGO").unique().iter_rows()):
        province, circ = cne.code(code), cne.code(number)
        name = province_name(province)
        lists(
            b,
            f"province/{province}/{circ}",
            f"2025 Ecuadorian National Assembly election, {name} provincial seats, circumscription {circ}",
            f"Asambleístas provinciales de {name} 2025, circunscripción {circ}",
            f"provincial/{cne.slug(name)}-{circ}",
            ASSEMBLY,
            votes(CIRCUMSCRIPTION, int(code), int(number)),
            people(CIRCUMSCRIPTION, int(code), int(number)),
            split,
            circ,
        )
    for code in sorted(votes(OVERSEAS)["PROVINCIA_CODIGO"].unique().to_list()):
        province = cne.code(code)
        lists(
            b,
            f"overseas/{province}",
            f"2025 Ecuadorian National Assembly election, overseas seats for {province_english(province)}",
            f"Asambleístas del exterior de {province_name(province)} 2025",
            f"overseas/{cne.slug(province_name(province))}",
            ASSEMBLY,
            votes(OVERSEAS, int(code)),
            people(OVERSEAS, int(code)),
        )
    lists(
        b,
        "andean",
        "2025 Ecuadorian election of members of the Andean Parliament",
        "Parlamentarios andinos del Ecuador 2025",
        "andean-parliament",
        ANDEAN,
        votes(ANDEAN_CODE),
        people(ANDEAN_CODE),
    )


def build(day: str) -> Package:
    first = day == DAY1
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Ecuador's {'2025 general election' if first else '2025 presidential runoff'} by parish, from the National "
        "Electoral Council's results files",
        depends=[
            f"{SOURCE}/geo-2025",
            f"{SOURCE}/registry",
            f"{SOURCE}/registry-2025",
            *([] if first else [f"{SOURCE}/{DAY1}"]),
        ],
    )
    p.external.update([ECUADOR, *SCHEMES])
    base = "ec/2025-02-09-general" if first else "ec/2025-04-13-presidential-runoff"
    b = Builder(p, day, base)
    if first:
        english, spanish, kind = "2025 Ecuadorian general election", "Elecciones generales del Ecuador 2025", "general"
    else:
        english = "2025 Ecuadorian presidential election, second round"
        spanish = "Elecciones presidenciales del Ecuador 2025, segunda vuelta"
        kind = "runoff"
    p.add(
        "election",
        id=b.election,
        polity_id=ECUADOR,
        kind=kind,
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
    )
    p.add("slug", path=base, entity_id=b.election, is_current=True)
    p.name(b.election, english, "en", "official")
    p.name(b.election, spanish, "es", "native", False)
    president(b, first)
    if first:
        assembly(b)
    t = b.tally
    if t.rows:
        p.accept(
            "accounting.ballots_sum",
            f"the file's own parish figures disagree: valid votes plus blank and null votes differ from the ballots "
            f"cast in {t.rows:,} of {t.of:,} parish contest rows, by {t.most:,} ballots at most and {t.total:,} "
            "ballots in all; stored as published",
        )
    return p
