"""Czech local referendums (místní referenda) since 2006 from the Ministry of Interior's table of announced ones.

The table has one row per referendum with the municipality, the question, the date, the number of eligible persons, the
turnout, whether the vote was valid, the yes and no votes, and whether the decision binds. A referendum with several
questions is either one row with the answers written "ad 1) ... ad 2) ..." or a row followed by one row per further
question. Each referendum is one election, each question one `ballot_question` contest, and the whole electorate of the
municipality (or city district) is its one reporting unit. `cz.mvcr/registry` holds the source and the rules; each year
is one package, `cz.mvcr/mr-<year>`, built on `cz.volby/geo` for the municipalities.
"""

import functools
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date

import fastexcel
import shapefile

from ea import changeset, raw
from ea.package import Package
from ea.sources.cz.volby import CZECHIA, area, key

SOURCE = "cz.mvcr"
TABLE = "https://mv.gov.cz/documents/volby-referenda/mistnireferenda-tabulkahlaseni-20260708?disposition=attachment"
PAGE = "https://mv.gov.cz/volby-referenda/oznamena-referenda-2"
RUIAN = "https://services.cuzk.gov.cz/shp/stat/epsg-5514/1.zip"
MV_SOURCE = key("source", "cz.mvcr.mistni_referenda")
SCHEME = "cz.referendum"
UPDATED = date(2026, 11, 13)
REGISTRY = "cz.mvcr/registry"
YES, NO = 1, 2
REGISTERED, VOTES = 2, 1

SYSTEM_2004 = key("electoral_system", "mr", 2004)
SYSTEM_2008 = key("electoral_system", "mr", 2008)
CHANGE = date(2008, 7, 1)
METHOD = (
    "Counted by the municipality's referendum commission and reported to the Ministry of Interior, which publishes one "
    "line per referendum: the persons entitled to vote, the turnout, the votes for and against each question, and "
    "whether the vote was valid and its decision binding. Polling-district counts are not published. The valid and "
    "invalid ballots are not published either, so only the registered persons and the votes for each answer are stored."
)
RULES_2004 = (
    "Under Act 22/2004 on local referendums, a vote is valid when at least half of the persons entitled to vote "
    "take part, and its decision binds when more than half of the voters vote for it. A referendum on separating "
    "part of a municipality, merging municipalities, or attaching one to another is adopted only when more than half of "
    "all the persons entitled to vote are for it."
)
RULES_2008 = (
    "Since an amendment of Act 22/2004 took effect on 1 July 2008, a vote is valid when at least 35% of the persons "
    "entitled to vote take part, and its decision binds when more than half of the voters vote for it and those votes "
    "are at least 25% of all the persons entitled to vote. A referendum on separating part of a municipality, merging "
    "municipalities, or attaching one to another is adopted only when more than half of all the persons entitled to "
    "vote are for it. The archive records the rules as the turnout quorum and the majority; the 25% minimum is "
    "measured against the electorate, which the rules cannot express."
)

# Rows whose printed text cannot be read mechanically.
DATES = {
    "12. 12. 14:00 - 22:00": (date(2014, 12, 12), date(2014, 12, 12)),
    "7. 10. a 8. 10. 2016": (date(2016, 10, 7), date(2016, 10, 8)),
}
YES_OVERRIDE = {"ad) 1 260 ad 2) 76 (30 %; 8,8 %)": "ad 1) 260 ad 2) 76"}
# City districts: the statutory city each lies in, by a fragment of the district's folded name.
PARENTS = {
    "praha": "Praha",
    "velka chuchle": "Praha",
    "brno": "Brno",
    "ostrava": "Ostrava",
    "stara bela": "Ostrava",
    "plzen": "Plzeň",
    "usti nad labem": "Ústí nad Labem",
    "suche lazce": "Opava",
    "miskovice": "Kutná Hora",
}
PARTS = {"velka chuchle", "suche lazce", "miskovice", "stara bela"}
# Names the table prints with a typo or in full, and the names that several municipalities share.
NAMES = {
    "Abertamy a přidružená část Hřebečná": "Abertamy",
    "Valaššské Meziříčí": "Valašské Meziříčí",
    "Dolní Tošenovice": "Dolní Tošanovice",
    "Jammé": "Jamné",
    "Rohozná u Jihlavy": "Rohozná",
}
CODES = {
    "Dolany (Mělník, STČ)": "513130",
    "Horní Újezd (Litomyšl, PAR)": "572365",
    "Trstěnice (Litomyšl, PAR)": "578894",
    "Borová (Polička, PAR)": "577839",
    "Střížovice (Plzeň, PLK)": "558401",
}


@dataclass
class Question:
    text: str
    yes: int | None = None
    no: int | None = None
    binding: str | None = None
    registered: int | None = None


@dataclass
class Referendum:
    number: int
    place: str
    okres: str
    start: date | None
    end: date | None
    questions: list[Question] = field(default_factory=list)
    note: str = ""
    row: int = 0
    turnout: str = ""
    valid: str = ""
    overturned: bool = False


def fetch() -> None:
    raw.fetch(SOURCE, TABLE, suffix=".xlsx")
    raw.fetch("cz.cuzk", RUIAN, suffix=".zip")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", re.sub(r"[‐-―]", "-", text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean(text: object) -> str:
    return " ".join(("" if text is None else str(text)).split())


def parse_date(text: str) -> tuple[date, date] | None:
    if text in DATES:
        return DATES[text]
    if found := re.match(r"(\d{4})-(\d\d)-(\d\d)", text):
        day = date(int(found[1]), int(found[2]), int(found[3]))
        return day, day
    if found := re.match(r"(\d{1,2})\.\s*-\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*(\d{4})", text):
        return date(int(found[4]), int(found[3]), int(found[1])), date(int(found[4]), int(found[3]), int(found[2]))
    if found := re.match(r"(\d{1,2})\.\s*(\d{1,2})\.?\s*(\d{4})", text):
        day = date(int(found[3]), int(found[2]), int(found[1]))
        return day, day
    return None


def numbers(cell: str) -> list[int | None]:
    """The counts in a cell: one number with its percentage, or one per question marked "ad 1)" or "1."."""
    cell = YES_OVERRIDE.get(cell, cell)
    if cell == "" or "neozn" in cell.lower():
        return [None]
    bare = re.sub(r"\([^)]*\)?", "", cell).strip()
    parts = [part.strip() for part in re.split(r"(?i)\bad\)?\s*\d*\)|\b\d\.(?=\s)", bare) if part.strip()]
    return [int(re.sub(r"\s", "", part)) for part in parts]


def binding(cell: str, count: int) -> list[str | None]:
    """The decision on each question: ANO (binding), NEPŘIJATO (not adopted), or NE."""
    if cell == "" or "neozn" in cell.lower():
        return [None] * count
    found = [token.upper() for token in re.findall(r"(?i)NEPŘIJATO|ANO|NE(?![A-Za-zČŘŽ])", cell)]
    if len(found) == 1:
        return found * count
    if len(found) != count:
        raise ValueError(f"binding {cell!r} for {count} questions")
    return list(found)


def split_questions(text: str, count: int) -> list[str]:
    if count == 1:
        return [re.sub(r"^\d[.)]\s*", "", text).strip()]
    cuts: list[tuple[int, int]] = []
    at = 0
    for n in range(1, count + 1):
        found = re.compile(rf"(?:^|\s){n}[.)]\s*").search(text, at)
        if found is None:
            raise ValueError(f"cannot find question {n} in {text[:80]!r}")
        cuts.append((found.start(), found.end()))
        at = found.end()
    parts = []
    for index, (_, end) in enumerate(cuts):
        stop = cuts[index + 1][0] if index + 1 < count else len(text)
        parts.append(text[end:stop].strip())
    return parts


def strip_number(text: str) -> str:
    return re.sub(r"^\d{1,2}(?:[.)]\s*|\s+(?=[A-ZÁ-Ž]))", "", text).strip()


@functools.cache
def referendums() -> list[Referendum]:
    sheet = fastexcel.read_excel(str(raw.cached(SOURCE, TABLE).path)).load_sheet_by_idx(0, header_row=None)
    rows = [[clean(cell) for cell in row] for row in sheet.to_polars().rows()]
    end = next(i for i, row in enumerate(rows) if row[0].startswith("Aktualizováno"))
    groups: list[list[tuple[int, list[str]]]] = []
    for index in range(2, end):
        row = rows[index]
        if row[1]:
            groups.append([])
        if not any(row):
            continue
        groups[-1].append((index, row))
    found: list[Referendum] = []
    for group in groups:
        first = group[0][1]
        number = int(next(row[0] for _, row in group if row[0]))
        span = parse_date(first[3]) if first[3] else None
        referendum = Referendum(
            number,
            first[1],
            "",
            span[0] if span else None,
            span[1] if span else None,
            row=group[0][0] + 1,
            turnout=first[5],
            valid=first[6],
            overturned="Zrušeno KS" in first[9],
        )
        if span is None:
            referendum.note = f"{first[3]} {first[4]} {first[2]}".strip()
            referendum.questions = [Question(strip_number(row[2])) for _, row in group if row[2]]
            found.append(referendum)
            continue
        if not re.fullmatch(r"\d[\d ]*", first[4]) and not re.search(r"(?i)ad\)? ?\d", first[4]):
            referendum.note = " ".join([first[2], first[4]]) if "neozn" in first[4].lower() else first[4]
            referendum.questions = [Question(strip_number(row[2])) for _, row in group if row[2]]
            found.append(referendum)
            continue
        if re.search(r"(?i)ad\)? ?\d|^\d\. \d", first[7] + first[8]):
            count = len(numbers(first[7]))
            texts = split_questions(first[2], count)
            yes, no = numbers(first[7]), numbers(first[8])
            registered = numbers(first[4])
            if len(registered) == 1:
                registered *= count
            marks = binding(first[9], count)
            referendum.questions = [Question(texts[i], yes[i], no[i], marks[i], registered[i]) for i in range(count)]
        else:
            registered = numbers(first[4])[0]
            for _, row in group:
                if not row[2]:
                    continue
                (yes,) = numbers(row[7])
                (no,) = numbers(row[8])
                mark = binding(row[9], 1)[0] if row[9] else None
                referendum.questions.append(Question(strip_number(row[2]), yes, no, mark, registered))
            default = binding(first[9], 1)[0] if first[9] else None
            for question in referendum.questions:
                question.binding = question.binding or default
        found.append(referendum)
    return found


def locations() -> dict[str, list[tuple[str, str, str]]]:
    """Municipalities by folded name: (RUIAN code, name, district name)."""
    archive = zipfile.ZipFile(raw.cached("cz.cuzk", RUIAN).path)

    def read(name: str) -> list[dict[str, object]]:
        reader = shapefile.Reader(dbf=io.BytesIO(archive.read(f"1/{name}.dbf")), encoding="cp1250")
        return [dict(record.as_dict()) for record in reader.records() if record is not None]

    districts = {str(row["KOD"]): str(row["NAZEV"]) for row in read("OKRESY_P")}
    found: dict[str, list[tuple[str, str, str]]] = {}
    for row in read("OBCE_P"):
        found.setdefault(fold(str(row["NAZEV"])), []).append(
            (str(row["KOD"]), str(row["NAZEV"]), districts[str(row["OKRES_KOD"])])
        )
    return found


def resolve(referendum: Referendum, found: dict[str, list[tuple[str, str, str]]]) -> tuple[str, bool]:
    """The RUIAN code of the municipality the vote was in and whether the whole municipality voted."""
    name, _, rest = referendum.place.partition(" (")
    okres = fold(rest.rstrip(")").split(",")[0].removeprefix("SM ").removeprefix("mč "))
    plain = re.sub(
        r"(?i)^(obec|město|městys|městská část|městský obvod|mč|mo|sm|statutární město)\s+", "", name
    ).strip()
    plain = re.sub(r"(?i)\s*-\s*mč$", "", plain)
    whole = plain == name.strip() or re.match(r"(?i)^(obec|město|městys|sm|statutární město)\s", name) is not None
    if referendum.place in CODES:
        return CODES[referendum.place], True
    folded = fold(plain)
    if not whole or folded.startswith("praha ") or folded in PARTS:
        parent = next((town for hint, town in PARENTS.items() if hint in folded), None)
        if parent is None:
            raise LookupError(f"{referendum.place}: no parent municipality")
        ((code, _, _),) = found[fold(parent)]
        return code, False
    options = found.get(fold(NAMES.get(plain, plain)), [])
    if len(options) > 1:
        options = [o for o in options if fold(o[2]).replace(" ", "") == okres.replace(" ", "")] or options
    if len(options) != 1:
        raise LookupError(f"{referendum.place}: {options}")
    return options[0][0], True


def years() -> list[int]:
    return sorted({status(r)[1].year for r in referendums() if status(r)[0] is not None})


def status(referendum: Referendum) -> tuple[str | None, date]:
    """The status the archive records for a referendum, and the date it was announced for."""
    if referendum.start is None:
        return None, UPDATED
    when = referendum.start
    counted = any(q.yes is not None or q.no is not None or q.registered is not None for q in referendum.questions)
    text = referendum.note
    if counted:
        return "held", when
    if re.search(r"(?i)neplatn", text):
        return "annulled", when
    if re.search(r"(?i)zruš|nekonán|zdržet se", text):
        return "canceled", when
    if when > UPDATED and not text:
        return "scheduled", when
    return None, when


def packages() -> list[str]:
    return [REGISTRY, *(f"cz.mvcr/mr-{year}" for year in years())]


def parse() -> None:
    registry().write()
    for year in years():
        year_package(year).write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Ministry of Interior's table of local referendums, and the rules the Local Referendums Act sets",
    )
    p.external.add(CZECHIA)
    p.add(
        "source",
        id=MV_SOURCE,
        kind="official_body",
        publisher="Ministerstvo vnitra České republiky",
        title="Místní referenda v České republice (od r. 2006): tabulka hlášení",
        url=PAGE,
        license="Public information published by the Ministry of Interior; free reuse with the source acknowledged",
        license_url=PAGE,
        attribution="Ministerstvo vnitra České republiky",
    )
    p.artifact(raw.cached(SOURCE, TABLE), MV_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=CZECHIA,
        kind="precinct",
        rank=5,
        noun="referendum electorate",
        code_system=None,
    )
    for id, since, until, rules, text in (
        (
            SYSTEM_2004,
            date(2006, 1, 1),
            CHANGE,
            {"formula": "referendum", "quorum": 0.5, "majority": {"share": 0.5, "strict": True}},
            RULES_2004,
        ),
        (
            SYSTEM_2008,
            CHANGE,
            None,
            {"formula": "referendum", "quorum": 0.35, "majority": {"share": 0.5, "strict": True}},
            RULES_2008,
        ),
    ):
        p.add(
            "electoral_system", id=id, polity_id=CZECHIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def month(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def year_package(year: int) -> Package:
    found = [r for r in referendums() if status(r)[0] is not None and status(r)[1].year == year]
    p = Package(
        f"cz.mvcr/mr-{year}",
        SOURCE,
        f"The {len(found)} Czech local referendums of {year} from the Ministry of Interior's table",
        depends=["cz.volby/geo", REGISTRY],
    )
    p.external.add(CZECHIA)
    p.uses(raw.cached(SOURCE, TABLE))
    towns = locations()
    merged: dict[tuple[date | None, str], Referendum] = {}
    for referendum in found:
        # Several rows for one municipality on one day are one vote with several questions.
        same = merged.setdefault(
            (referendum.start, referendum.place), replace(referendum, questions=list(referendum.questions))
        )
        if same.questions != referendum.questions:
            same.questions += referendum.questions
    for referendum in merged.values():
        add_referendum(p, referendum, towns)
    return p


def add_referendum(p: Package, referendum: Referendum, towns: dict[str, list[tuple[str, str, str]]]) -> None:
    state, start = status(referendum)
    assert state is not None and referendum.end is not None
    code, whole = resolve(referendum, towns)
    plain = re.sub(
        r"(?i)^(obec|město|městys|městská část|městský obvod|mč|mo|sm|statutární město)\s+",
        "",
        referendum.place.partition(" (")[0],
    ).strip()
    tag = f"{start}:{code}:{changeset.slug(plain)}"
    election = key("election", tag)
    path = f"cz/{start}-referendum-{changeset.slug(plain)}"
    p.add(
        "election",
        id=election,
        polity_id=CZECHIA,
        kind="referendum",
        date_from=start,
        date_to=referendum.end,
        status=state,
    )
    p.name(election, f"{plain} local referendum, {month(start)}", "en", "official")
    p.name(election, f"Místní referendum {plain}, {start.day}. {start.month}. {start.year}", "cs", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    unit = key("unit", tag)
    if state == "held":
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CZECHIA,
            scheme_code=SCHEME,
            code=tag,
            area_relation="equals" if whole else "within",
            area_id=area("cz.obec", code),
            kind="standard",
        )
        p.name(unit, plain, "cs", "official")
    system = SYSTEM_2008 if start >= CHANGE else SYSTEM_2004
    many = len(referendum.questions) > 1
    for number, question in enumerate(referendum.questions, 1):
        contest = key("contest", tag, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area("cz.obec", code) if whole else None,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(
            contest,
            f"{plain} local referendum, question {number}" if many else f"{plain} local referendum",
            "en",
            "official",
        )
        p.name(contest, question.text, "cs", "native", False)
        p.add(
            "slug",
            path=f"{path}/question-{number}" if many else f"{path}/question",
            entity_id=contest,
            is_current=True,
        )
        options = [key("option", tag, number, label) for label in ("yes", "no")]
        for position, (label, option) in enumerate(zip(("Yes", "No"), options, strict=True), 1):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        if state != "held":
            caveat(p, contest, state, referendum)
            continue
        result_set = key("result_set", tag, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=MV_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for option, measure, value in (
            (None, REGISTERED, question.registered),
            (options[0], VOTES, question.yes),
            (options[1], VOTES, question.no),
        ):
            if value is not None:
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
        decide(p, result_set, options, question)
        caveat(p, contest, state, referendum, question, result_set)


def decide(p: Package, result_set: str, options: list[str], question: Question) -> None:
    """Record the decision the Ministry's table declares: a binding answer, or a proposal that was not adopted."""
    if (
        question.binding == "ANO"
        and question.yes is not None
        and question.no is not None
        and question.yes != question.no
    ):
        winner = 0 if question.yes > question.no else 1
        p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
        p.add("outcome", result_set_id=result_set, option_id=options[1 - winner], kind="lost")
    elif question.binding == "NEPŘIJATO":
        p.add("outcome", result_set_id=result_set, option_id=options[0], kind="lost")


def caveat(
    p: Package,
    contest: str,
    state: str,
    referendum: Referendum,
    question: Question | None = None,
    result_set: str | None = None,
) -> None:
    texts: list[str] = []
    if state == "annulled":
        texts.append("A regional court ruled the vote invalid, and the Ministry's table prints no counts for it.")
    elif state == "canceled":
        texts.append("The referendum was called off before it took place.")
    elif state == "scheduled":
        texts.append("The referendum is announced for this date; the table does not hold a result yet.")
    if question is not None:
        if question.binding == "NEPŘIJATO":
            texts.append(
                "The proposal was not adopted: a vote to separate part of a municipality, to merge municipalities, or to "
                "attach one to another needs more than half of all the persons entitled to vote."
            )
        if question.yes is None and question.no is None:
            texts.append("The Ministry's table does not print the votes for and against.")
        if referendum.overturned:
            texts.append(
                "The Regional Court in Brno later set the decision aside; the counts are as the Ministry's table prints them."
            )
    for index, text in enumerate(texts):
        if text:
            p.add(
                "note",
                id=key("note", contest, "caveat", index),
                entity_id=contest,
                kind="caveat",
                body_md=text,
                lang="en",
            )
