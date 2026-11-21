"""Russia's direct elections of the heads of federal subjects (governors and republic heads), by precinct commission.

The Central Election Commission publishes the results on izbirkom.ru, whose hosts do not answer scripted requests
from outside Russia. The open-data releases of the Commission's site, mirrored at
gitlab.com/russian-electoral-open-data, hold each single voting day's results by precinct election commission (УИК)
as one CSV file per campaign, with the candidates' table beside them. This module reads the release of the single
voting day of 14 September 2025 at one pinned commit; the source of every figure is still the Commission, and the
mirror is where the file came from.

Each file's rows are the subject total (`org`), the total of remote electronic voting (`org_rev`), the territorial
commissions (`tik`), and the precinct commissions (`uik`, including a row for the special precincts, which belong to
no territorial commission). Precinct rows and the remote electronic voting row add up to the subject total; the
total is stored as the declared total of the result set.

The release for 2018 holds only links to the Commission's pages, 2024 holds only the remote electronic voting
protocols of seven governors' elections, and the releases for 2019 to 2023 are empty, so the other years are not
built here.

Sevastopol's precincts are placed within the Ukrainian region that `ua.cvk` loaded, since the archive counts that
territory under Ukraine.
"""

import csv
import io
import re
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package
from ea.sources.ru import cik_uik as base

SOURCE = "ru.cik_heads"
GEO = f"{base.SOURCE}/geo"
RUSSIA = base.RUSSIA
TERRITORY = base.TERRITORY
SEVASTOPOL = base.SEVASTOPOL
SUBJECTS = base.SUBJECTS
BY_NAME = base.BY_NAME

MIRROR = "https://gitlab.com/russian-electoral-open-data/edg-2025"
COMMIT = "5838715420c2d8ed4825330546b599e27feb510a"
FILES = f"{MIRROR}/-/raw/{COMMIT}/"
NOTES = f"{FILES}README.md"
INFO = f"{FILES}info.json"
CANDIDATES = f"{FILES}candidates.tsv"

SUBJECT = base.SUBJECT
TIK = base.TIK
UIK = base.UIK
DAY = date(2025, 9, 14)

REGISTERED, CAST, VALID, INVALID, VOTES = 2, 3, 4, 5, 1

# The elections of 14 September 2025: the subject as the Commission's files name it, and the file's name.
ELECTIONS = {
    "Архангельская область": "Выборы Губернатора Архангельской области.csv",
    "Брянская область": "Выборы Губернатора Брянской области.csv",
    "Еврейская автономная область": "Выборы губернатора Еврейской автономной области.csv",
    "Иркутская область": "Выборы Губернатора Иркутской области.csv",
    "Калужская область": "Выборы Губернатора Калужской области.csv",
    "Камчатский край": "Выборы Губернатора Камчатского края.csv",
    "Костромская область": "Выборы губернатора Костромской области.csv",
    "Краснодарский край": "Выборы Губернатора Краснодарского края.csv",
    "Курская область": "Досрочные выборы Губернатора Курской области.csv",
    "Ленинградская область": "Выборы Губернатора Ленинградской области.csv",
    "Новгородская область": "Досрочные выборы Губернатора Новгородской области.csv",
    "Оренбургская область": "Досрочные выборы Губернатора Оренбургской области.csv",
    "Пермский край": "Выборы губернатора Пермского края.csv",
    "Республика Коми": "Выборы Главы Республики Коми.csv",
    "Республика Татарстан (Татарстан)": "Выборы Главы _Раиса_ Республики Татарстан.csv",
    "Ростовская область": "Выборы Губернатора Ростовской области.csv",
    "Свердловская область": "Досрочные выборы Губернатора Свердловской области.csv",
    "Тамбовская область": "Досрочные выборы Главы Тамбовской области.csv",
    "Чувашская Республика - Чувашия": "Выборы Главы Чувашской Республики.csv",
    "город Севастополь": "Выборы Губернатора города Севастополя.csv",
}
SEVASTOPOL_NAME = "город Севастополь"


def file_url(name: str) -> str:
    return f"{FILES}results/csv/{quote(name)}"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


subject_area = base.subject_area


CIK = key("source", "cik")
SYSTEM = key("electoral_system", "head")

# ----------------------------------------------------------------------------------------------------------------
# Raw files


def _get(url: str, suffix: str) -> None:
    """Fetch one file, waiting out the host's rate limit."""
    for wait in (5, 15, 30, 60, 0):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
            return
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or not wait:
                raise
            time.sleep(wait)


def fetch() -> None:
    for url, suffix in ((NOTES, ".md"), (INFO, ".json"), (CANDIDATES, ".csv")):
        _get(url, suffix)
    for name in ELECTIONS.values():
        _get(file_url(name), ".csv")
        time.sleep(2)


def table(url: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))))


# ----------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/2025-09-14"]


def parse() -> None:
    registry().write()
    day_2025().write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=CIK,
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Результаты выборов по участковым избирательным комиссиям (izbirkom.ru)",
        url="http://www.izbirkom.ru/region/izbirkom",
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации",
        notes=f"The Commission's tables as the open-data release of the single voting day of 14 September 2025 "
        f"keeps them, read from {MIRROR} at commit {COMMIT}; izbirkom.ru does not answer scripted requests from "
        "outside Russia.",
    )


# ----------------------------------------------------------------------------------------------------------------
# Parties and offices

# The national party each regional branch nominates for, by the words its name carries, and the party's name.
PARTIES = (
    (r"ЕДИНАЯ РОССИЯ", "Единая Россия"),
    (r"КОММУНИСТЫ РОССИИ", "Коммунистическая партия Коммунисты России"),
    (r"КОММУНИСТИЧЕСКАЯ ПАРТИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ", "Коммунистическая партия Российской Федерации"),
    (r"ЛДПР", "Либерально-демократическая партия России"),
    (r"СПРАВЕДЛИВАЯ РОССИЯ", "Справедливая Россия — За правду"),
    (r"НОВЫЕ ЛЮДИ", "Новые люди"),
    (r"ПЕНСИОНЕРОВ", "Российская партия пенсионеров за социальную справедливость"),
    (r"ЗЕЛ[ЕЁ]НАЯ АЛЬТЕРНАТИВА", "Зелёная альтернатива"),
    (r"ЗЕЛ[ЕЁ]НЫЕ", "Российская экологическая партия «Зелёные»"),
    (r"РОДИНА", "Родина"),
    (r"ГРАЖДАНСКАЯ ИНИЦИАТИВА", "Гражданская инициатива"),
    (r"РОССИЙСКИЙ ОБЩЕНАРОДНЫЙ СОЮЗ", "Российский общенародный союз"),
)
SELF = "самовыдвижение"


def nominator(text: str) -> str | None:
    """The national party's name behind a nominating association, or none for a self-nominated candidate."""
    if text.strip().lower() == SELF:
        return None
    for pattern, name in PARTIES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    raise LookupError(f"no party matches the nominating association {text!r}")


def party_id(name: str) -> str:
    return key("party", name)


def office_id(iso: str) -> str:
    return key("office", iso)


TITLES = {"губернатора": "Губернатор", "главы": "Глава", "(раиса)": "(Раис)"}


def office_name(title: str) -> str:
    """The title's office in the nominative: `Выборы Главы Республики Коми` is the `Глава Республики Коми`."""
    text = re.sub(r"^(досрочные\s+)?выборы\s+", "", " ".join(title.split()), flags=re.IGNORECASE)
    words = [TITLES.get(word.lower(), word) for word in text.split(" ")]
    name = " ".join(words)
    return name[0].upper() + name[1:]


def region_iso(subject: str) -> str | None:
    return BY_NAME.get(subject)


def subject_title(subject: str) -> str:
    rows = table(file_url(ELECTIONS[subject]))
    return rows[0]["election"]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The offices of the federal subjects' heads, their electoral system, and the parties that nominated "
        "candidates on 14 September 2025, from the Central Election Commission",
        depends=[GEO],
    )
    p.external.add(RUSSIA)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=RUSSIA,
        valid_from=date(2012, 5, 2),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The head of a federal subject is elected directly for five years (Law No. 414-FZ, art. 20). The "
        "subject's own law sets whether the winner needs more than half of the votes, with a second round of the "
        "two leaders otherwise, or the most votes. In all 20 elections of 14 September 2025 the winner held more "
        "than half of the valid votes, so the rule did not decide any of them and is recorded as the most votes.",
        lang="en",
    )
    for subject in ELECTIONS:
        iso = region_iso(subject)
        id = office_id(iso or "UA-40")
        p.add("office", id=id, polity_id=RUSSIA, kind="governor")
        p.name(id, office_name(subject_title(subject)), "ru", "official")
    seen: set[str] = set()
    for _, name in PARTIES:
        if name in seen:
            continue
        seen.add(name)
        p.add("party", id=party_id(name), kind="party", polity_id=RUSSIA)
        p.name(party_id(name), name, "ru", "official")
    return p


# ----------------------------------------------------------------------------------------------------------------
# Units


@dataclass(frozen=True)
class Region:
    code: str
    area: str
    equals: bool


def region(subject: str) -> Region:
    iso = region_iso(subject)
    if iso is not None:
        return Region(f"RU-{iso}", subject_area(iso), True)
    if subject == SEVASTOPOL_NAME:
        return Region("UA-40", SEVASTOPOL, False)
    raise LookupError(f"no federal subject matches {subject!r}")


class Units:
    """The reporting units of one election: subject, territorial commission, and precinct, each kept once."""

    def __init__(self, p: Package, where: Region) -> None:
        self.p = p
        self.where = where
        self.seen: set[str] = set()

    def _add(self, scheme: str, code: str, parent: str | None, kind: str = "standard", equals: bool = False) -> str:
        id = key("unit", scheme, f"{DAY}:{code}")
        if id in self.seen:
            return id
        self.seen.add(id)
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=RUSSIA,
            scheme_code=scheme,
            code=f"{DAY}:{code}",
            parent_id=parent,
            area_relation="equals" if equals else "within",
            area_id=self.where.area,
            kind=kind,
        )
        return id

    def subject(self) -> str:
        return self._add(SUBJECT, self.where.code, None, equals=self.where.equals)

    def tik(self, name: str) -> str:
        return self._add(TIK, f"{self.where.code}:{name}", self.subject())

    def uik(self, tik: str | None, number: str, name: str) -> str:
        parent = self.tik(tik) if tik else self.subject()
        label = f"{tik}:{number}" if tik else name
        return self._add(UIK, f"{self.where.code}:{label}", parent)

    def remote(self) -> str:
        return self._add(UIK, f"{self.where.code}:remote", self.subject(), kind="aggregate")


# ----------------------------------------------------------------------------------------------------------------
# 14 September 2025


def classify(header: str) -> str | None:
    """The accounting line a column holds, or none for a column the archive does not keep."""
    text = " ".join(header.lower().split())
    if text.startswith("число избирателей"):
        return "registered"
    if not text.startswith("число"):
        return None
    if "недействительн" in text:
        return "invalid"
    if "действительн" in text:
        return "valid"
    if "переносн" in text:
        return "portable"
    if "стационарн" in text:
        return "stationary"
    return None


def whole(text: str | None) -> int | None:
    return int(text) if text not in (None, "") else None


def norm(name: str) -> str:
    return " ".join(name.lower().replace("ё", "е").split())


def person_name(printed: str) -> str:
    return " ".join(printed.split())


METHOD = (
    "The Central Election Commission's results by precinct election commission (УИК) for the election of 14 "
    "September 2025, as the open-data release of that day keeps them. Registered are the voters on the lists at the "
    "close of voting, ballots cast are the ballots found in the portable and stationary boxes (for remote "
    "electronic voting, the valid and invalid ballots), and invalid and valid are the ballots so counted. Remote "
    "electronic voting is its own unit under the subject. Special precincts, which belong to no territorial "
    "commission, are one unit each under the subject. The release's totals for the subject are the declared totals, "
    "and the precinct and electronic units add up to them."
)


def day_2025() -> Package:
    p = Package(
        f"{SOURCE}/2025-09-14",
        SOURCE,
        "The direct elections of 14 September 2025 for the heads of 20 federal subjects, by precinct election "
        "commission, from the Central Election Commission",
        depends=[GEO, f"{SOURCE}/registry"],
    )
    p.external |= {RUSSIA, TERRITORY, SEVASTOPOL}
    _source(p)
    for url in (NOTES, INFO, CANDIDATES):
        p.artifact(raw.cached(SOURCE, url), CIK)
    path = "ru/2025-09-14-regional-heads"
    election = key("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=RUSSIA,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2025 Russian regional head elections", "en", "official")
    p.name(election, "Выборы высших должностных лиц субъектов Российской Федерации 2025 года", "ru", "native", False)
    people = candidates()
    gaps: list[str] = []
    for subject, name in ELECTIONS.items():
        p.artifact(raw.cached(SOURCE, file_url(name)), CIK)
        _contest(p, election, path, subject, table(file_url(name)), people, gaps)
    if gaps:
        p.accept(
            "accounting.declared",
            "The release's precinct rows fall short of the subject total it prints, because it leaves out some "
            "precincts that their territorial commissions' totals include: " + "; ".join(gaps),
        )
    return p


def candidates() -> dict[tuple[str, str], dict[str, str]]:
    """The candidates' table by the election's name and the candidate's name."""
    return {(norm(row["name"]), norm(row["fullName"])): row for row in table(CANDIDATES) if row["fullName"]}


def _contest(
    p: Package,
    election: str,
    path: str,
    subject: str,
    rows: list[dict[str, str]],
    people: dict[tuple[str, str], dict[str, str]],
    gaps: list[str],
) -> None:
    where = region(subject)
    iso = region_iso(subject) or "UA-40"
    slug = iso.lower()
    title = " ".join(rows[0]["election"].split())
    headers = list(rows[0])
    columns: dict[str, list[str]] = {}
    for header in headers:
        kind = classify(header)
        if kind:
            columns.setdefault(kind, []).append(header)
    names = [h for h in headers[9:] if not h.startswith("Число") and not h.lower().startswith("в том числе")]
    contest = key("contest", DAY, iso)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=office_id(iso),
        district_area_id=where.area,
        magnitude=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
        term="full",
    )
    p.name(contest, title, "ru", "official")
    p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
    result_set = key("result_set", DAY, iso)
    declared = sum(_sum(r, columns["valid"]) or 0 for r in rows if r["line_type"] == "org")
    held = sum(_sum(r, columns["valid"]) or 0 for r in rows if r["line_type"] in ("uik", "org_rev"))
    completeness = round(held / declared, 6)
    if held != declared:
        gaps.append(f"{subject}: {held:,} of the subject's {declared:,} valid ballots")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CIK,
        status="active",
        is_default=True,
        completeness=completeness,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for position, printed in enumerate(names, 1):
        row = people.get((norm(title), norm(printed)))
        if row is None:
            raise LookupError(f"no candidate {printed!r} in the candidates' table for {title}")
        party = nominator(row["electionAssociation"])
        option = key("option", DAY, iso, printed)
        options[printed] = option
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_id(party) if party else None,
            ballot_position=position,
            ballot_label=person_name(printed),
        )
        person = key("person", DAY, iso, printed)
        p.add(
            "person",
            id=person,
            birth_date=date.fromisoformat(row["birthDate"]) if row["birthDate"] else None,
            birth_precision="day" if row["birthDate"] else None,
        )
        p.name(person, person_name(printed), "ru", "official")
        p.add(
            "candidacy",
            id=key("candidacy", DAY, iso, printed),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=person_name(printed),
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if row["election"].strip() == "избран" else "lost",
        )
    units = Units(p, where)

    def put(unit: str, row: dict[str, str]) -> None:
        for measure, kind in ((REGISTERED, "registered"), (VALID, "valid"), (INVALID, "invalid")):
            total = _sum(row, columns.get(kind, []))
            if total is not None:
                _value(p, result_set, unit, None, measure, total)
        cast = _sum(row, columns.get("portable", []) + columns.get("stationary", []))
        if row["line_type"] == "org_rev":
            valid, invalid = _sum(row, columns["valid"]), _sum(row, columns["invalid"])
            cast = (valid or 0) + (invalid or 0)
        if cast is not None:
            _value(p, result_set, unit, None, CAST, cast)
        for printed, option in options.items():
            _value(p, result_set, unit, option, VOTES, whole(row[printed]) or 0)

    for row in rows:
        kind = row["line_type"]
        if kind == "org":
            for measure, label in ((REGISTERED, "registered"), (VALID, "valid"), (INVALID, "invalid")):
                total = _sum(row, columns.get(label, []))
                if total is not None:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total)
            for printed, option in options.items():
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=VOTES,
                    value=whole(row[printed]) or 0,
                )
        elif kind == "org_rev":
            put(units.remote(), row)
        elif kind == "uik":
            number = row["uik_number"]
            if row["tik"]:
                put(units.uik(row["tik"], number, row["uik"]), row)
            else:
                put(units.uik(None, number, " ".join(row["uik"].split())), row)


def _sum(row: dict[str, str], headers: list[str]) -> int | None:
    values = [whole(row[h]) for h in headers]
    found = [v for v in values if v is not None]
    return sum(found) if found else None


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
