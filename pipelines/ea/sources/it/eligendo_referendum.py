"""Italy's referendums, 1974-2026, from the Interior Ministry's open data, by municipality (by province where the
Ministry publishes no finer unit, and by polling section for 2026)."""

import io
import zipfile
from datetime import date, timedelta
from functools import cache
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.it.eligendo import REGION_ALIASES, fold
from ea.sources.it.referendum import CONSTITUTIONAL, CONSULTATIVE, ONE_DAY, QUESTIONS

SOURCE = "it.eligendo_referendum"
OPENDATA = "https://dait.interno.gov.it/documenti/opendata/referendum/referendum-{day:%Y%m%d}.zip"
PAGE = "https://elezionistorico.interno.gov.it/eligendo/opendata.php"
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
DAYS = list(QUESTIONS)
HEADERLESS = [
    "REGIONE",
    "PROVINCIA",
    "COMUNE",
    "NUM_REFERENDUM",
    "QUESITO",
    "ELETTORI",
    "ELETTORI_MASCHI",
    "VOTANTI",
    "VOTANTI_MASCHI",
    "NUMVOTISI",
    "NUMVOTINO",
    "SCHEDE_BIANCHE",
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def url(day: date) -> str:
    return OPENDATA.format(day=day)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")


NUMBERS = {"A": "10", "D": "11", "E": "12"}


def _count(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().str.replace(r",0+$", "").cast(pl.Int64)


def _csv(data: bytes, headed: bool = True) -> pl.DataFrame:
    text = data.decode("utf-8-sig") if data.startswith(b"\xef\xbb\xbf") else data.decode("latin-1")
    return pl.read_csv(
        io.StringIO(text),
        separator=";",
        infer_schema=False,
        has_header=headed,
        new_columns=None if headed else HEADERLESS,
    )


def _open(day: date) -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, url(day)).path)


@cache
def read(day: date) -> pl.DataFrame:
    """The day's Italian rows as one per unit and question, with the file's own counts."""
    archive = _open(day)
    names = archive.namelist()
    if day == date(2022, 6, 12):
        frames = [_csv(archive.read(name)) for name in names if "Scrutini_quesito" in name]
        frame = pl.concat(frames)
        return frame.select(
            region="REGIONE",
            province="PROVINCIA",
            comune="COMUNE",
            section=pl.lit(None, pl.String),
            question=_count("NUM_REFERENDUM"),
            text=pl.lit(None, pl.String),
            electors=_count("ELETTORI"),
            voters=_count("VOTANTI"),
            yes=_count("VOTI_SI"),
            no=_count("VOTI_NO"),
            blank=_count("SCHEDE_BIANCHE"),
            null=_count("SCHEDE_NULLE"),
            contested=_count("SCHEDE_CONTESTATE"),
        )
    if day == date(2026, 3, 22):
        name = next(name for name in names if name.endswith("LivSez.csv"))
        text = archive.read(name).decode("utf-8-sig").split("\n", 1)[1]
        frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema=False)
        return frame.select(
            region="REG",
            province="PROV",
            comune="COM",
            section="SEZ",
            question=pl.lit(1, pl.Int64),
            text=pl.lit(None, pl.String),
            electors=_count("ELETTORITOTALI"),
            voters=_count("NUMVOTANTITOTALI"),
            yes=_count("VOTISI"),
            no=_count("VOTIVALIDI") - _count("VOTISI"),
            blank=_count("SKBIANCHE"),
            null=pl.lit(None, pl.Int64),
            contested=pl.lit(None, pl.Int64),
        )
    (member,) = names
    data = archive.read(member)
    frame = _csv(data, headed=data.startswith(b'"REGIONE"'))
    blank = "SKBIANCHE" if "SKBIANCHE" in frame.columns else "SCHEDE_BIANCHE"
    return frame.select(
        region="REGIONE",
        province="PROVINCIA",
        comune=pl.col("COMUNE") if "COMUNE" in frame.columns else pl.lit(None, pl.String),
        section=pl.lit(None, pl.String),
        question=pl.col("NUM_REFERENDUM").str.strip_chars().str.replace(r",0+$", "").replace(NUMBERS).cast(pl.Int64),
        text=pl.col("QUESITO"),
        electors=_count("ELETTORI"),
        voters=_count("VOTANTI"),
        yes=_count("NUMVOTISI"),
        no=_count("NUMVOTINO"),
        blank=_count(blank),
        null=pl.lit(None, pl.Int64),
        contested=pl.lit(None, pl.Int64),
    )


@cache
def abroad(day: date) -> pl.DataFrame | None:
    """The votes abroad by country, where the Ministry's files list them (2022 and 2026)."""
    archive = _open(day)
    if day == date(2022, 6, 12):
        name = next(name for name in archive.namelist() if name.endswith("ReferendumEstero_Scrutini.xlsx"))
        sheet = pl.read_excel(io.BytesIO(archive.read(name)), has_header=False)
        sheet = sheet.slice(1).rename(dict(zip(sheet.columns, sheet.row(0), strict=True)))
        return sheet.select(
            question=pl.col("NUMREFERENDUM").cast(pl.Int64),
            region="RIPARTIZIONE",
            nation="NAZIONE",
            electors=pl.col("ELETTORITOTALI").cast(pl.Int64),
            voters=pl.col("NUMVOTANTITOTALI").cast(pl.Int64),
            yes=pl.col("VOTIVALIDISI").cast(pl.Int64),
            no=pl.col("VOTIVALIDINO").cast(pl.Int64),
            blank=pl.col("SKBIANCHE").cast(pl.Int64),
        )
    if day == date(2026, 3, 22):
        name = next(name for name in archive.namelist() if name.endswith("EsteroNazione.csv"))
        text = archive.read(name).decode("utf-8-sig").split("\n", 1)[1]
        frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema=False)
        return frame.select(
            question=pl.lit(1, pl.Int64),
            region="RIPARTIZIONE",
            nation="NAZIONE",
            electors=_count("ELETTORITOTALI"),
            voters=_count("NUMVOTANTITOTALI"),
            yes=_count("VOTISI"),
            no=_count("VOTIVALIDI") - _count("VOTISI"),
            blank=_count("SKBIANCHE"),
        )
    return None


@cache
def national(day: date) -> pl.DataFrame | None:
    """The Ministry's own national totals, Italy and abroad together, where a file prints them (2022)."""
    if day != date(2022, 6, 12):
        return None
    archive = _open(day)
    name = next(name for name in archive.namelist() if name.endswith("ReferendumItaliaPiùEstero_Scrutini.xlsx"))
    sheet = pl.read_excel(io.BytesIO(archive.read(name)), has_header=False)
    sheet = sheet.slice(1).rename(dict(zip(sheet.columns, sheet.row(0), strict=True)))
    return sheet.select(
        question=pl.col("NUMREFERENDUM").cast(pl.Int64),
        electors=pl.col("ELETTORITOTALI").cast(pl.Int64),
        voters=pl.col("NUMVOTANTITOTALI").cast(pl.Int64),
        yes=pl.col("VOTIVALIDISI").cast(pl.Int64),
        no=pl.col("VOTIVALIDINO").cast(pl.Int64),
        blank=pl.col("SKBIANCHE").cast(pl.Int64),
    )


LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
MONTHS = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
CONSTITUTIONAL_METHOD = (
    "A constitutional referendum confirms an amendment that Parliament passed without a two-thirds majority. It "
    "needs no quorum: the amendment takes effect if Yes wins a majority of valid votes (art. 138 of the Constitution)."
)
CONSULTATIVE_METHOD = (
    "A one-off advisory referendum held under constitutional law 2/1989. It needed no quorum and bound no one."
)
ABROGATIVE_METHOD = (
    "An abrogative referendum repeals a law, or part of one, if more than half of the registered voters take part "
    "and Yes wins a majority of valid votes (art. 75 of the Constitution)."
)
SYSTEMS = {
    "abrogative": ({"formula": "referendum", "quorum": 0.5}, ABROGATIVE_METHOD),
    "constitutional": ({"formula": "referendum"}, CONSTITUTIONAL_METHOD),
    "consultative": ({"formula": "referendum"}, CONSULTATIVE_METHOD),
}
MEASURES = {"electors": 2, "voters": 3, "valid": 4, "invalid": 5, "blank": 6}


def kind(day: date) -> str:
    return "constitutional" if day in CONSTITUTIONAL else "consultative" if day in CONSULTATIVE else "abrogative"


def grain(day: date) -> str:
    """The finest unit of the day's file: a polling section, a municipality, or a province."""
    frame = read(day)
    if frame["section"].is_not_null().any():
        return "section"
    return "comune" if frame["comune"].is_not_null().any() else "province"


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, *parts)


def _clean(frame: pl.DataFrame) -> pl.DataFrame:
    clean = lambda column: pl.col(column).map_elements(fold, return_dtype=pl.String)
    return frame.with_columns(
        region_key=clean("region").replace(REGION_ALIASES),
        province_key=clean("province"),
        comune_key=pl.when(pl.col("comune").is_not_null()).then(clean("comune")).otherwise(pl.lit(None, pl.String)),
    )


@cache
def places() -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str, str], str]]:
    """Region, province, and municipality names as last printed, keyed by their folded place."""
    regions: dict[str, str] = {}
    provinces: dict[tuple[str, str], str] = {}
    towns: dict[tuple[str, str, str], str] = {}
    for day in DAYS:
        frame = _clean(read(day)).select("region", "region_key", "province", "province_key", "comune", "comune_key")
        for region, region_key, province, province_key, comune, comune_key in (
            frame.unique().sort("region_key", "province_key", "comune_key").iter_rows()
        ):
            regions[region_key] = region.strip()
            provinces[(region_key, province_key)] = province.strip()
            if comune is not None:
                towns[(region_key, province_key, comune_key)] = comune.strip()
    return regions, provinces, towns


def _title(text: str) -> str:
    return text if text != text.upper() else text.title().replace("'S", "'s")


def source_row(p: Package) -> str:
    source = key("source", SOURCE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministero dell'Interno",
        title="Eligendo: archivio storico delle elezioni, open data",
        url=PAGE,
        license=LICENSE,
        notes="The referendum files carry no ISTAT codes, so places are named as the files print them.",
    )
    return source


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The regions, provinces, and municipalities that appear in the Interior Ministry's referendum results, "
        "1974-2026, named as the files print them",
    )
    source = source_row(p)
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, url(day)), source)
    for code, rank, scheme_kind, noun in (
        ("it.referendum_regione", 1, "administrative", "region"),
        ("it.referendum_provincia", 2, "administrative", "province"),
        ("it.referendum_comune", 3, "administrative", "municipality"),
        ("it.referendum_sezione", 4, "precinct", "polling section"),
        ("it.referendum_estero", 5, "synthetic", "country abroad"),
    ):
        p.add("scheme", code=code, polity_id=ITALY, kind=scheme_kind, rank=rank, noun=noun)
    p.external.add(ITALY)
    regions, provinces, towns = places()

    def place(scheme: str, code: str, text: str, parent: str | None) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, _title(text), "it", "official")

    for region_key, text in sorted(regions.items()):
        place("it.referendum_regione", region_key, text, None)
    for (region_key, province_key), text in sorted(provinces.items()):
        place(
            "it.referendum_provincia",
            f"{region_key}|{province_key}",
            text,
            area("it.referendum_regione", region_key),
        )
    for (region_key, province_key, comune_key), text in sorted(towns.items()):
        place(
            "it.referendum_comune",
            f"{region_key}|{province_key}|{comune_key}",
            text,
            area("it.referendum_provincia", f"{region_key}|{province_key}"),
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral systems that applied to Italy's referendums 1974-2026",
    )
    p.external.add(ITALY)
    for day in DAYS:
        p.uses(raw.cached(SOURCE, url(day)))
    for name, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add("electoral_system", id=id, polity_id=ITALY, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


def _units(day: date) -> tuple[pl.DataFrame, str]:
    """The Italian units of the day: one row per unit, with its folded keys, code, and area."""
    frame = _clean(read(day))
    finest = grain(day)
    keys = ["region_key", "province_key"]
    if finest != "province":
        keys.append("comune_key")
    if finest == "section":
        keys.append("section")
    if frame.group_by("question", *keys).len().filter(pl.col("len") > 1).height:
        raise ValueError(f"{day}: folded place names collide")
    units = frame.select(*keys).unique().sort(keys)
    return units, finest


def _text(day: date) -> dict[int, str]:
    frame = read(day).filter(pl.col("text").is_not_null())
    return {
        question: max(set(texts), key=lambda text: (texts.count(text), text)).strip()
        for question, texts in frame.group_by("question").agg(pl.col("text")).iter_rows()
    }


def _outcome(rows: pl.DataFrame, quorum: bool) -> tuple[bool, str]:
    """Whether Yes won, from the stored totals, and the sentence that says why."""
    total = rows.select(pl.col("yes", "no", "voters", "electors").sum().cast(pl.Int64)).row(0, named=True)
    yes, no = total["yes"], total["no"]
    if quorum:
        turnout = total["voters"] / total["electors"]
        if turnout <= 0.5:
            return False, (
                f"Turnout was {100 * turnout:.2f} percent of registered voters, short of the quorum of more than "
                f"half, so the law stays in force whatever the answers: Yes won {100 * yes / (yes + no):.2f} "
                "percent of valid votes."
            )
    return yes > no, ""


def election(day: date) -> Package:
    level = grain(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Italy's referendum{'s' if len(QUESTIONS[day]) > 1 else ''} of {day} by "
        f"{ {'section': 'polling section', 'comune': 'municipality', 'province': 'province'}[level] } "
        "from the Interior Ministry's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(ITALY)
    source = key("source", SOURCE)
    p.uses(raw.cached(SOURCE, url(day)))
    questions = QUESTIONS[day]
    system = kind(day)
    plural = len(questions) > 1
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=ITALY,
        kind="referendum",
        date_from=day,
        date_to=day if day in ONE_DAY else day + timedelta(days=1),
        status="held",
    )
    month = f"{day:%B} " if day.year == 2016 else ""
    p.name(id, f"{month}{day.year} Italian {system} referendum{'s' if plural else ''}", "en", "official")
    italian = {
        "constitutional": "costituzionale",
        "consultative": "consultivo",
        "abrogative": "abrogativi" if plural else "abrogativo",
    }[system]
    p.name(id, f"Referendum {italian} del {day.day} {MONTHS[day.month - 1]} {day.year}", "it", "native", False)
    path = f"it/{day}-referendum-by-{level}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    natives = _text(day)
    if day == date(2026, 3, 22):
        natives[1] = "REFERENDUM COSTITUZIONALE – ORDINAMENTO GIURISDIZIONALE E ISTITUZIONE DELLA CORTE DISCIPLINARE"

    # The units: Italian units from the file, then the countries abroad where the file lists them.
    units, _ = _units(day)
    ids: list[str] = []
    rows: list[dict[str, object]] = []
    for row in units.iter_rows(named=True):
        region_key, province_key = row["region_key"], row["province_key"]
        parts = [region_key, province_key]
        if level == "province":
            scheme, place = "it.referendum_provincia", area("it.referendum_provincia", "|".join(parts))
            relation = "equals"
        else:
            parts.append(row["comune_key"])
            place = area("it.referendum_comune", "|".join(parts))
            scheme, relation = "it.referendum_comune", "equals"
            if level == "section":
                scheme, relation = "it.referendum_sezione", "within"
                parts.append(row["section"])
        unit = key("unit", day, *parts)
        ids.append(unit)
        rows.append(
            {
                "id": unit,
                "polity_id": ITALY,
                "scheme_code": scheme,
                "code": f"{day}:{'|'.join(parts)}",
                "area_relation": relation,
                "area_id": place if relation != "unlocated" else None,
                "kind": "standard",
            }
        )
    units = units.with_columns(unit=pl.Series(ids, dtype=pl.String))
    abroad_frame = abroad(day)
    abroad_units = pl.DataFrame()
    if abroad_frame is not None:
        abroad_units = abroad_frame.select("region", "nation").unique().sort("region", "nation")
        abroad_units = abroad_units.with_columns(
            unit=pl.struct("region", "nation").map_elements(
                lambda s: key("unit", day, "ESTERO", fold(s["region"]), fold(s["nation"])), return_dtype=pl.String
            )
        )
        for region, nation, unit in abroad_units.iter_rows():
            rows.append(
                {
                    "id": unit,
                    "polity_id": ITALY,
                    "scheme_code": "it.referendum_estero",
                    "code": f"{day}:ESTERO:{fold(region)}:{fold(nation)}",
                    "area_relation": "unlocated",
                    "area_id": None,
                    "kind": "abroad_mission",
                }
            )
            p.name(unit, _title(nation), "it", "official")
    p.frame("reporting_unit", pl.DataFrame(rows, schema_overrides={"area_id": pl.String}))

    frame = _clean(read(day))
    unit_keys = [column for column in units.columns if column != "unit"]
    frame = frame.join(units, on=unit_keys, how="left")
    frame = frame.with_columns(valid=pl.col("yes") + pl.col("no"))
    values: list[pl.DataFrame] = []
    negative = over = 0
    notes: list[str] = []
    for number, english in enumerate(questions, 1):
        contest_key = f"{day}:{number}"
        contest = key("contest", contest_key)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            round_number=1,
            electoral_system_id=key("electoral_system", system),
            default_result_kind="counted",
        )
        p.add(
            "ballot_question",
            contest_id=contest,
            type="plebiscite" if system == "consultative" else "optional_referendum",
        )
        p.name(contest, english, "en", "official")
        if number in natives:
            p.name(contest, natives[number], "it", "native", False)
        p.add("slug", path=f"{path}/question-{number}", entity_id=contest, is_current=True)
        options = {}
        for position, (answer, label) in enumerate((("yes", "Sì"), ("no", "No")), 1):
            options[answer] = key("option", contest_key, answer)
            p.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(options[answer], answer.title(), "en", "english")
        result_set = key("result_set", contest_key)
        rows_q = frame.filter(pl.col("question") == number)
        table = rows_q.select(
            "unit",
            "electors",
            "voters",
            "blank",
            "yes",
            "no",
            "valid",
            invalid=pl.col("voters") - pl.col("valid") - pl.col("blank"),
        )
        if abroad_frame is not None:
            part = abroad_frame.filter(pl.col("question") == number).join(abroad_units, on=["region", "nation"])
            table = pl.concat(
                [
                    table,
                    part.select(
                        "unit",
                        "electors",
                        "voters",
                        "blank",
                        "yes",
                        "no",
                        valid=pl.col("yes") + pl.col("no"),
                        invalid=pl.col("voters") - pl.col("yes") - pl.col("no") - pl.col("blank"),
                    ),
                ]
            )
        negative = max(negative, table.filter(pl.col("invalid") < 0).height)
        over = max(over, table.filter(pl.col("voters") > pl.col("electors")).height)
        table = table.with_columns(invalid=pl.when(pl.col("invalid") >= 0).then("invalid"))
        for measure, column in MEASURES.items():
            values.append(
                table.filter(pl.col(measure).is_not_null()).select(
                    result_set_id=pl.lit(result_set),
                    unit_id="unit",
                    option_id=pl.lit(None, pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(column, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col(measure),
                )
            )
        for answer in ("yes", "no"):
            values.append(
                table.select(
                    result_set_id=pl.lit(result_set),
                    unit_id="unit",
                    option_id=pl.lit(options[answer]),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col(answer),
                )
            )
        method = METHOD[level] + (ABROAD if abroad_frame is not None else NO_ABROAD if day.year >= 2003 else "")
        yes_won, why = _outcome(table, system == "abrogative")
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            **({"completeness": 1.0} if day.year < 2003 or abroad_frame is not None else {}),
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method + (" " + why if why else ""),
            lang="en",
        )
        p.add("outcome", result_set_id=result_set, option_id=options["yes"], kind="won" if yes_won else "lost")
        if not why:
            p.add("outcome", result_set_id=result_set, option_id=options["no"], kind="lost" if yes_won else "won")
        total = national(day)
        if total is not None:
            official = total.filter(pl.col("question") == number).row(0, named=True)
            mine = table.select(pl.col("electors", "voters", "yes", "no", "blank", "valid").sum()).row(0, named=True)
            for measure, option, column in (
                (3, None, "voters"),
                (6, None, "blank"),
                (4, None, None),
                (1, options["yes"], "yes"),
                (1, options["no"], "no"),
            ):
                theirs = official["yes"] + official["no"] if column is None else official[column]
                have = mine["valid"] if column is None else mine[column]
                if have == theirs:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=theirs)
                else:
                    notes.append(f"question {number} {column}: files {have:,}, national file {theirs:,}")
            if official["electors"] != mine["electors"]:
                notes.append(
                    f"question {number} electors: files {mine['electors']:,}, national file {official['electors']:,}"
                )
    p.frame("result_value", pl.concat(values))
    if negative:
        p.accept(
            "accounting.ballots_sum",
            f"In up to {negative} units (by question) the file's valid votes plus blank ballots exceed its voters, "
            "so no invalid-ballot figure is stored for them; the file's figures are stored as printed.",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"In up to {over} units (by question) the file prints more voters than registered electors; the "
            "figures are stored as printed.",
        )
    if notes:
        print(f"{p.key}: national file differs: " + "; ".join(notes), flush=True)
    return p


METHOD = {
    "section": (
        "Counted by the polling-section offices and published by the Interior Ministry's Eligendo archive as one "
        "row per polling section. Registered electors, voters, blank ballots, and Yes votes are the file's figures; "
        "valid votes are Yes plus No, and the file's valid-vote total less its Yes votes gives No. Invalid ballots "
        "are voters less blank ballots less valid votes."
    ),
    "comune": (
        "Counted by the municipal electoral offices and published by the Interior Ministry's Eligendo archive as "
        "one row per municipality and question. The file has no ISTAT codes, so places are the municipality, "
        "province, and region names as the file prints them, folded for case, accents, and hyphens. Registered "
        "electors, voters, blank ballots, and Yes and No votes are the file's figures. Valid votes are Yes plus No. "
        "Invalid ballots are voters less blank ballots less valid votes."
    ),
    "province": (
        "Counted by the municipal electoral offices and published by the Interior Ministry's Eligendo archive as "
        "one row per province and question; the Ministry publishes no municipality file for this referendum. "
        "Places are the province and region names as the file prints them, folded for case, accents, and hyphens. "
        "Registered electors, voters, blank ballots, and Yes and No votes are the file's figures. Valid votes are "
        "Yes plus No. Invalid ballots are voters less blank ballots less valid votes."
    ),
}
ABROAD = (
    " The Ministry's file for votes cast abroad, by country, is stored as separate units that are not placed in "
    "any Italian area."
)
NO_ABROAD = (
    " The Ministry's open data file covers Italy only; the votes of Italians abroad, counted since 2003, are not "
    "in it and are not stored here."
)
