"""Sicily's municipal elections from the Region's elections portal, 2009 to 2026.

The Regional Department of Local Authorities publishes, for each election day, one page per municipality with the
registered voters, the voters, the mayoral candidates and their votes, the lists linked to each candidate, and the
council seats each list won. A first-round page already names the two finalists of a runoff; the second round has its
own pages. A person crawled each day's pages and supplied the crawl as one HTML file per round, so the files enter
the raw cache through `raw.store` and are cited by the address of the index page they came from.

Production already holds every Sicilian municipality's area, reporting unit, polity, council, and mayoral office
(`sicilia_comuni.tsv`), so the packages attach to those rows and add the elections, contests, options, candidacies,
and results. The few municipalities production lacks a polity for get one from the registry package.
"""

import csv
import functools
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.it.interno import proper

SOURCE = "it.comunali_speciali"
BASE = "https://www.elezioni.regione.sicilia.it"
COMUNI = Path(__file__).with_name("sicilia_comuni.tsv")
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
SICILY = "a9de2e8f-2796-51fd-9fb7-895abe2feeb4"
PROVINCES = {
    "081": "TP",
    "082": "PA",
    "083": "ME",
    "084": "AG",
    "085": "CL",
    "086": "EN",
    "087": "CT",
    "088": "RG",
    "089": "SR",
}
VOTES, REGISTERED, VALID, INVALID, BLANK, VOTERS = 1, 2, 4, 5, 6, 14
MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}


def clean(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokens(page: str) -> list[str]:
    start = page.find("Fine Tabella di menu sx")
    end = page.find("<!-- footer")
    body = page[start : end if end > start else None]
    body = re.sub(r"(?is)<script.*?</script>", "", body)
    body = re.sub(r"(?i)</?(td|th|tr|br|table|p|div)[^>]*>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    return [clean(line) for line in body.split("\n") if clean(line)]


def number(text: str) -> int:
    return int(text.replace(".", ""))


def isnum(text: str) -> bool:
    return re.fullmatch(r"\d{1,3}(?:\.\d{3})*", text) is not None


class Void(Exception):
    """A page that holds no count, as when the Region reports a municipality's election void."""

    def __init__(self, place: str, note: str) -> None:
        super().__init__(place, note)
        self.place = place
        self.note = note


@dataclass
class Slate:
    position: int
    name: str
    candidates: int
    votes: int
    seats: int | None


@dataclass
class Candidate:
    position: int
    name: str
    elected: bool
    finalist: bool
    votes: int
    seat: int | None = None
    slates: list[Slate] = field(default_factory=list)
    total: int | None = None
    total_seats: int | None = None
    note: str | None = None


@dataclass
class Page:
    kind: str
    place: str
    province: str
    population: int | None
    sections: int
    electors: int
    seats: int
    voters: int
    mayor_votes: int
    council_votes: int
    barred: int | None
    invalid: int
    blank: int
    notes: list[str]
    candidates: list[Candidate]
    others: list[Slate]
    elected_text: bool


def read_page(items: list[str]) -> Page:
    title = items[0]
    match = re.fullmatch(r"Comune di (.+) \((\w\w)\)", items[1])
    if match is None:
        raise ValueError(f"no comune in {items[:3]}")
    place, province = match.groups()
    download = items.index("Download Report") if "Download Report" in items else 0
    notes = [t for t in items[2:download] if not t.startswith("Dati relativi") and not t.startswith("Risultati")]
    if "Schede e Voti non Validi" not in items or "Download Report" not in items:
        raise Void(items[1], items[2] if len(items) > 2 else "")
    at = items.index("Schede e Voti non Validi")
    sections, electors, seats = (number(items[at + k]) for k in (1, 2, 3))
    pop_at = items.index("Pop.Legale")
    population = number(items[pop_at + 1]) if isnum(items[pop_at + 1]) else None
    head = items.index("di cui bianche")
    barred_column = "Liste sbarrate" in items[at:head]
    figures = items[head + 1 : head + (8 if barred_column else 7)]
    if barred_column:
        voters, _, mayor, council, barred, invalid, blank = figures
    else:
        voters, _, mayor, council, invalid, blank = figures
        barred = None
    candidates: list[Candidate] = []
    others: list[Slate] = []
    current: Candidate | None = None
    group: list[Slate] | None = None
    i = head + len(figures) + 1
    seated = False
    while i < len(items):
        item = items[i]
        if item.startswith(("Trattasi", "I report", "* ")):
            break
        if item == "ALTRE LISTE O GRUPPI DI LISTE":
            current = None
            group = others
            i += 1
            continue
        start = i
        if re.fullmatch(r"N.", item) and i + 1 < len(items) and items[i + 1].isdigit():
            start = i + 2
        if (
            (re.fullmatch(r"N.", item) is None or start != i)
            and "VOTI" in items[start : start + 6]
            and items[start] != "Totale"
            and not isnum(items[start])
        ):
            end = items.index("VOTI", start)
            label = items[start:end]
            votes = number(items[end + 1])
            current = Candidate(
                position=int(items[i + 1]) if start != i else len(candidates) + 1,
                name=label[-1],
                elected="Eletto" in label,
                finalist=any("II Turno" in part for part in label),
                votes=votes,
            )
            candidates.append(current)
            group = current.slates
            i = end + 2
            while i < len(items) and (items[i] == "%" or re.fullmatch(r"[-\d,]+%", items[i])):
                i += 1
            continue
        if item.startswith("Al candidato sindaco") and current is not None:
            current.note = item
            if items[i + 1] == "Seggi":
                current.seat = number(items[i + 2])
                i += 3
            else:
                i += 1
            continue
        if re.fullmatch(r"N.", item) and i + 1 < len(items) and items[i + 1].startswith("Lista/e"):
            j = i
            while not items[j].startswith("%"):
                j += 1
            seated = items[j + 1] == "Seggi"
            i = j + (2 if seated else 1)
            continue
        if item.startswith("Nessuna lista"):
            i += 1
            continue
        if item == "Totale" and group is not None:
            if current is not None:
                current.total = number(items[i + 1])
                if i + 3 < len(items) and (seated and re.fullmatch(r"\d+|-", items[i + 3])):
                    current.total_seats = None if items[i + 3] == "-" else int(items[i + 3])
            i += 3 + (1 if seated else 0)
            continue
        if group is not None and isnum(item) and i + 4 < len(items):
            width = 6 if seated else 5
            row = items[i : i + width]
            if isnum(row[2]) and isnum(row[3]) and re.fullmatch(r"[-\d,]+%?", row[4]):
                if seated and not re.fullmatch(r"\d+|-", row[5]):
                    raise ValueError(f"{place}: list row {row}")
                group.append(
                    Slate(
                        int(row[0]),
                        row[1],
                        number(row[2]),
                        number(row[3]),
                        int(row[5]) if seated and row[5] != "-" else None,
                    )
                )
                i += width
                continue
        raise ValueError(f"{place}: cannot read {items[i : i + 8]} after {len(candidates)} candidates")
    return Page(
        kind=title,
        place=place,
        province=province,
        population=population,
        sections=sections,
        electors=electors,
        seats=seats,
        voters=number(voters),
        mayor_votes=number(mayor),
        council_votes=number(council),
        barred=None if barred is None else number(barred),
        invalid=number(invalid),
        blank=number(blank),
        notes=notes,
        candidates=candidates,
        others=others,
        elected_text=any(c.elected for c in candidates),
    )


def split(text: str) -> list[str]:
    return re.split(r"(?i)(?=<html)", text)[1:]


def read_file(key: str) -> tuple[tuple[date, date], dict[tuple[str, str], Page], dict[tuple[str, str], str]]:
    """The election day a crawl's pages name, each municipality's page from its results report, and the notes of the
    municipalities whose page holds no count."""
    text = raw.cached(SOURCE, key).path.read_bytes().decode("cp1252", errors="replace")
    found: dict[tuple[str, str], Page] = {}
    empty: dict[tuple[str, str], str] = {}
    heading: str | None = None
    for page in split(text):
        items = tokens(page)
        for item in items:
            if item.startswith("Elezioni Amministrative del "):
                heading = item
                break
        titled = next((t for t in items[:30] if t in ("Report Ripartizione Seggi", "Report Risultati")), None)
        if titled is None or not any(t.startswith("Comune di ") for t in items):
            continue
        at = items.index(titled)
        try:
            parsed = read_page(items[at:])
        except Void as void:
            named = re.fullmatch(r"Comune di (.+) \((\w\w)\)", void.place)
            if named is not None:
                empty[(named.group(2), fold(named.group(1)))] = void.note
            continue
        slot = (parsed.province, fold(parsed.place))
        if slot in found and found[slot].kind == "Report Ripartizione Seggi" and titled == "Report Risultati":
            continue
        found[slot] = parsed
    if heading is None:
        raise LookupError(f"{key} names no election day")
    return days_of(heading), found, empty


def days_of(heading: str) -> tuple[date, date]:
    """The first and last day of the voting a page heading names: "06-07 Maggio 2012", "31 Maggio - 01 Giugno 2015"."""
    year = int(re.findall(r"\d{4}", heading)[-1])
    words = [w for w in re.findall(r"[A-Za-z]+", heading) if w.lower() in MONTHS]
    numbers = [int(n) for n in re.findall(r"(?<!\d)\d{1,2}(?!\d)", heading)]
    if not words or not numbers:
        raise ValueError(heading)
    first = date(year, MONTHS[words[0].lower()], numbers[0])
    if len(numbers) == 1:
        return first, first
    return first, date(year, MONTHS[words[-1].lower()], numbers[1])


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    area: str
    unit: str
    polity: str | None
    body: str | None
    office: str | None


ALIASES = {("CT", "castel di judica"): "castel di iudica", ("ME", "mojo alcantara"): "moio alcantara"}
MISSING = "The Region's page for the municipality holds no count"


@functools.cache
def comuni() -> dict[str, Place]:
    with COMUNI.open(encoding="utf-8", newline="") as file:
        return {
            row["code"]: Place(
                row["code"],
                row["name"],
                row["area_id"],
                row["unit_id"],
                row["polity_id"] or None,
                row["body_id"] or None,
                row["office_id"] or None,
            )
            for row in csv.DictReader(file, delimiter="\t")
        }


@functools.cache
def by_name() -> dict[tuple[str, str], str]:
    return {(PROVINCES[code[:3]], fold(place.name)): code for code, place in comuni().items()}


def code_of(slot: tuple[str, str]) -> str:
    province, name = slot
    return by_name()[(province, ALIASES.get(slot, name))]


@dataclass(frozen=True)
class Day:
    first: date
    last: date
    keys: tuple[str, ...]
    runoff: str | None

    @property
    def sunday(self) -> date:
        return self.first if self.first.weekday() == 6 or self.last.weekday() != 6 else self.last

    @property
    def second(self) -> date:
        """The second round falls on the second Sunday after the first (art. 10 of regional law 7/1992)."""
        return self.sunday + timedelta(days=14)


FILES = {
    2009: ((f"{BASE}/comunali2009/primoTurno/",), f"{BASE}/comunali2009/"),
    2016: ((f"{BASE}/comunali2016/primoTurno/", f"{BASE}/comunali2016/primoTurno/ME/"), None),
    2025: ((f"{BASE}/comunali2025/",), None),
}


def _files(year: int) -> tuple[tuple[str, ...], str | None]:
    first, second = FILES.get(year, ((f"{BASE}/comunali{year}/primoTurno/",), None))
    if second is None and 2010 <= year <= 2024:
        second = f"{BASE}/comunali{year}/"
    return first, second


@functools.cache
def days() -> dict[date, Day]:
    out: dict[date, Day] = {}
    for year in range(2009, 2027):
        first, second = _files(year)
        (begin, end), _, _ = read_file(first[0])
        out[begin] = Day(begin, end, first, second)
    return out


@functools.cache
def rounds(day: date) -> tuple[dict[str, Page], dict[str, Page]]:
    """Each round's pages by municipality code."""
    plan = days()[day]
    firsts: dict[str, Page] = {}
    for key_ in plan.keys:
        _, pages, _ = read_file(key_)
        for slot, page in pages.items():
            firsts[code_of(slot)] = page
    seconds: dict[str, Page] = {}
    if plan.runoff is not None:
        _, pages, _ = read_file(plan.runoff)
        for slot, page in pages.items():
            code = code_of(slot)
            # The crawl of 2020 repeats the first-round pages of seven municipalities that elected a mayor outright.
            if code not in firsts or sent_to_runoff(firsts[code]):
                seconds[code] = page
    return firsts, seconds


def sent_to_runoff(page: Page) -> bool:
    """Whether a first-round page names two finalists for a second round."""
    return any(c.finalist for c in page.candidates) or any("ballottaggio" in note for note in page.notes)


def package_key(day: date, runoff: bool = False) -> str:
    return f"{SOURCE}/sicilia-{day.year}-runoff" if runoff else f"{SOURCE}/sicilia-{day.isoformat()}"


def packages() -> list[str]:
    out = [f"{SOURCE}/registry"]
    for day, plan in days().items():
        out.append(package_key(day))
        if plan.runoff is not None:
            out.append(package_key(day, True))
    return out


def fetch() -> None:
    """The crawled files are supplied through `raw.store`; this checks that each one is in the cache."""
    missing = []
    for plan in days().values():
        for key_ in (*plan.keys, *([plan.runoff] if plan.runoff else [])):
            try:
                raw.cached(SOURCE, key_)
            except LookupError:
                missing.append(key_)
    if missing:
        raise LookupError("not in the raw cache: " + ", ".join(missing))


def parse() -> None:
    registry().write()
    for day, plan in days().items():
        election(day).write()
        if plan.runoff is not None:
            runoff(day).write()


def emit() -> None:
    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
ITALIAN_MONTHS = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]  # fmt: skip
METHOD = (
    "Counted by the municipalities and published by the Region's Department of Local Authorities on one page per "
    "municipality. Registered voters are the electors in the municipality's lists, voters are the participants, and "
    "the mayoral candidates' votes are the valid mayoral votes. Each list's votes and seats are as the page prints "
    "them; the page leaves out lists that did not reach the threshold for seats and prints their votes together as "
    "barred lists. The page is the Region's provisional report of the municipalities' count."
)


def label_of(day: date) -> str:
    return f"{day.day} {ITALIAN_MONTHS[day.month - 1]} {day.year}"


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Regione Siciliana, Assessorato delle autonomie locali e della funzione pubblica",
        title="Elezioni comunali: risultati per comune",
        url=f"{BASE}/",
        license=LICENSE,
        license_url=f"{BASE}/",
        attribution="Regione Siciliana, Assessorato regionale delle autonomie locali e della funzione pubblica",
    )


def minted(code: str) -> tuple[str, str, str]:
    """A municipality's polity, council, and mayoral office: production's where it holds them, else the registry's."""
    place = comuni()[code]
    registry_ = Package(f"{SOURCE}/registry", SOURCE, "")
    return (
        place.polity or registry_.id("polity", code),
        place.body or registry_.id("body", code),
        place.office or registry_.id("office", code),
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The polities, councils, and mayoral offices of Sicilian municipalities that production lacks, with the source",
    )
    add_source(p)
    p.external |= {SICILY}
    for code, place in comuni().items():
        if place.polity and place.body and place.office:
            continue
        polity, body, office = minted(code)
        label = place.name
        if place.polity is None:
            p.add("polity", id=polity, kind="municipality", iso_code=None)
            p.name(polity, label, "it", "official")
            p.add("polity_parent", polity_id=polity, parent_id=SICILY)
            p.add("polity_territory", polity_id=polity, area_id=place.area)
            p.add("slug", path=f"it/sicilia/{slug(label)}", entity_id=polity, is_current=True)
            p.add("external_id", entity_id=polity, scheme=f"{SOURCE}.polity", value=code)
            p.external.add(place.area)
        else:
            p.external.add(place.polity)
        if place.body is None:
            p.add("body", id=body, polity_id=polity, kind="unicameral")
            p.name(body, f"{label} Municipal Council", "en", "english")
            p.name(body, f"Consiglio comunale di {label}", "it", "native", False)
        if place.office is None:
            p.add("office", id=office, polity_id=polity, body_id=None, kind="mayor")
            p.name(office, f"Mayor of {label}", "en", "english")
            p.name(office, f"Sindaco di {label}", "it", "native", False)
    return p


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def person_id(p: Package, day: date, code: str, ident: str) -> str:
    return p.id("person", day, code, ident)


def idents(candidates: list[Candidate]) -> dict[int, str]:
    """A key for each candidate of a page: the folded name, and the ballot position too for two of the same name."""
    names = [fold(c.name) for c in candidates]
    return {
        c.position: name if names.count(name) == 1 else f"{name}#{c.position}"
        for c, name in zip(candidates, names, strict=True)
    }


class Build:
    """The rows every Sicilian package adds the same way: contests, options, values, and the outcomes."""

    def __init__(self, p: Package, day: date) -> None:
        self.p = p
        self.day = day
        self.values = Facts()
        self.over: list[str] = []
        self.short: list[str] = []
        self.uneven: list[str] = []

    def result_set(self, election: str, contest: str, extra: str = "") -> str:
        p = self.p
        id_ = p.id("result_set", contest)
        p.add(
            "result_set",
            id=id_,
            election_id=election,
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
            id=p.id("note", id_),
            entity_id=id_,
            kind="methodology",
            body_md=(METHOD + " " + extra).strip(),
            lang="en",
        )
        return id_

    def accounting(self, result_set: str, place: Place, page: Page, ballots: bool) -> None:
        p = self.p
        unit = place.unit
        for measure, value in ((REGISTERED, page.electors), (VOTERS, page.voters)):
            self.values.add(result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        if ballots:
            for measure, value in (
                (VALID, page.mayor_votes),
                (INVALID, page.invalid - page.blank),
                (BLANK, page.blank),
            ):
                self.values.add(result_set, unit, None, measure, value)
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            if page.voters > page.electors:
                self.over.append(f"{place.name} {self.day.year}")
            if page.voters != page.mayor_votes + page.invalid:
                self.short.append(f"{place.name} {self.day.year}")

    def candidates(
        self,
        election: str,
        place: Place,
        page: Page,
        round_: int,
        previous: str | None,
        final: bool,
        persons: set[str],
        first: date,
    ) -> str:
        p = self.p
        label = place.name
        _, _, office = minted(place.code)
        contest = p.id("contest", first, place.code, "mayor", round_)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=place.area,
            magnitude=1,
            round_number=round_,
            previous_round_id=previous,
            default_result_kind="counted",
        )
        suffix = (
            ""
            if round_ == 1 and not any(c.finalist for c in page.candidates) and final
            else (f", {'first' if round_ == 1 else 'second'} round")
        )
        on = first if round_ == 1 else self.day
        p.add("slug", path=f"{self.base(place)}/{self.stem(round_, on)}/sindaco", entity_id=contest, is_current=True)
        p.name(contest, f"{first.year} {label} mayoral election{suffix}", "en", "english")
        p.name(
            contest,
            f"Elezione del sindaco di {label} del {label_of(on)}" + (" (ballottaggio)" if round_ == 2 else ""),
            "it",
            "native",
            False,
        )
        result_set = self.result_set(election, contest)
        self.accounting(result_set, place, page, ballots=self.paired(round_))
        ident = idents(page.candidates)
        for candidate in sorted(page.candidates, key=lambda c: (-c.votes, c.name, c.position)):
            option = p.id("option", contest, ident[candidate.position])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=candidate.position,
                ballot_label=candidate.name,
            )
            person = person_id(p, first, place.code, ident[candidate.position])
            if person not in persons:
                persons.add(person)
                p.add("person", id=person)
                p.name(person, proper(candidate.name), "it", "official")
            p.add(
                "candidacy",
                id=p.id("candidacy", contest, ident[candidate.position]),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
            )
            self.values.add(result_set, place.unit, option, VOTES, candidate.votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
            if candidate.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            elif candidate.finalist and round_ == 1:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
        return contest

    def paired(self, round_: int) -> bool:
        """Whether the page's non-valid figures tie to the mayoral ballots, as the printed totals show everywhere but
        in the first rounds of 2012 to 2016, when a voter could mark a list and no candidate."""
        return not (round_ == 1 and 2012 <= self.day.year <= 2016)

    def base(self, place: Place) -> str:
        return f"it/sicilia/{slug(place.name)}"

    def stem(self, round_: int, on: date) -> str:
        return f"{on.isoformat()}-local" if round_ == 1 else f"{on.isoformat()}-runoff"


def election(day: date) -> Package:
    plan = days()[day]
    firsts, seconds = rounds(day)
    p = Package(
        package_key(day),
        SOURCE,
        f"The Sicilian municipal elections of {day.isoformat()}: first-round mayoral and council results by "
        "municipality from the Region",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {SICILY}
    for key_ in plan.keys:
        p.artifact(raw.cached(SOURCE, key_), SOURCE_ID)
    if plan.runoff:
        p.uses(raw.cached(SOURCE, plan.runoff))
    umbrella = p.id("election", day, "sicilia")
    p.add(
        "election",
        id=umbrella,
        polity_id=SICILY,
        kind="local",
        date_from=plan.first,
        date_to=plan.last,
        status="held",
    )
    p.name(umbrella, f"{day:%B} {day.day}, {day.year} Sicilian municipal elections", "en", "english")
    p.name(umbrella, f"Elezioni comunali in Sicilia del {label_of(day)}", "it", "native", False)
    p.add("slug", path=f"it/sicilia/{day.isoformat()}-local", entity_id=umbrella, is_current=True)
    build = Build(p, day)
    persons: set[str] = set()
    contests = 0
    for code in sorted(firsts):
        place = comuni()[code]
        page = firsts[code]
        polity, body, _ = minted(code)
        runoff_page = seconds.get(code)
        final = runoff_page is None
        election_id = p.id("election", day, code, 1)
        p.external |= {place.area, place.unit, polity, body}
        p.add(
            "election",
            id=election_id,
            polity_id=polity,
            parent_id=umbrella,
            kind="local",
            date_from=plan.first,
            date_to=plan.last,
            status="held",
        )
        p.name(election_id, f"{day.year} {place.name} municipal election", "en", "english")
        p.name(election_id, f"Elezioni comunali di {place.name} del {label_of(day)}", "it", "native", False)
        p.add("slug", path=f"{build.base(place)}/{day.isoformat()}-local", entity_id=election_id, is_current=True)
        council(build, election_id, place, page, runoff_page, body)
        contests += 1
        if page.candidates:
            _, _, office = minted(code)
            p.external.add(office)
            build.candidates(election_id, place, page, 1, None, final, persons, day)
            contests += 1
    p.frame("result_value", build.values.frame())
    notes(p, build, day)
    print(f"{package_key(day)}: {len(firsts)} municipalities, {contests} contests", flush=True)
    return p


def council(build: Build, election_id: str, place: Place, page: Page, later: Page | None, body: str) -> None:
    p = build.p
    day = build.day
    slates = [s for c in page.candidates for s in c.slates] + page.others
    if not slates and not page.barred:
        return
    seated = {s.position: s.seats for c in (later.candidates if later else []) for s in c.slates}
    seated |= {s.position: s.seats for s in (later.others if later else [])}
    contest = p.id("contest", day, place.code, "council")
    given = [s.seats if s.seats is not None else seated.get(s.position) for s in slates]
    allocated = all(seats is not None for seats in given) and bool(given)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=body,
        district_area_id=place.area,
        magnitude=sum(seats or 0 for seats in given) if allocated else page.seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{day.year} {place.name} municipal council election", "en", "english")
    p.name(
        contest,
        f"Elezione del consiglio comunale di {place.name} del {label_of(day)}",
        "it",
        "native",
        False,
    )
    p.add(
        "slug",
        path=f"{build.base(place)}/{day.isoformat()}-local/consiglio-comunale",
        entity_id=contest,
        is_current=True,
    )
    result_set = build.result_set(election_id, contest)
    build.accounting(result_set, place, page, ballots=False)
    for slate in sorted(slates, key=lambda s: (-s.votes, s.position)):
        option = p.id("option", contest, slate.position, fold(slate.name))
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=slate.position,
            ballot_label=slate.name,
        )
        build.values.add(result_set, place.unit, option, VOTES, slate.votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=slate.votes)
        seats = slate.seats if slate.seats is not None else seated.get(slate.position)
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
    if page.barred:
        option = p.id("option", contest, "barred")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="others",
            ballot_position=max(s.position for s in slates) + 1 if slates else 1,
            ballot_label="Liste sbarrate",
        )
        build.values.add(result_set, place.unit, option, VOTES, page.barred)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=page.barred)


def runoff(day: date) -> Package:
    plan = days()[day]
    firsts, seconds = rounds(day)
    p = Package(
        package_key(day, True),
        SOURCE,
        f"The Sicilian municipal runoffs of {plan.second.isoformat()}: mayoral results by municipality from the Region",
        depends=[f"{SOURCE}/registry", package_key(day)],
    )
    assert plan.runoff is not None
    p.artifact(raw.cached(SOURCE, plan.runoff), SOURCE_ID)
    build = Build(p, plan.second)
    persons: set[str] = set()
    for code in sorted(seconds):
        place = comuni()[code]
        page = seconds[code]
        polity, _, office = minted(code)
        p.external |= {place.area, place.unit, polity, office}
        earlier = firsts.get(code)
        election_id = p.id("election", plan.second, code, 2)
        p.add(
            "election",
            id=election_id,
            polity_id=polity,
            parent_id=p.id("election", day, code, 1) if earlier else None,
            kind="runoff",
            date_from=plan.second,
            date_to=plan.second,
            status="held",
        )
        p.name(election_id, f"{day.year} {place.name} municipal election, second round", "en", "english")
        p.name(
            election_id,
            f"Elezioni comunali di {place.name} del {label_of(plan.second)} (ballottaggio)",
            "it",
            "native",
            False,
        )
        p.add(
            "slug",
            path=f"{build.base(place)}/{plan.second.isoformat()}-runoff",
            entity_id=election_id,
            is_current=True,
        )
        named = idents(earlier.candidates).values() if earlier else []
        for ident in idents(page.candidates).values():
            if ident in named:
                persons.add(person_id(p, day, code, ident))
        previous = p.id("contest", day, code, "mayor", 1) if earlier and earlier.candidates else None
        build.candidates(election_id, place, page, 2, previous, True, persons, day)
    p.frame("result_value", build.values.frame())
    notes(p, build, plan.second)
    print(f"{package_key(day, True)}: {len(seconds)} municipalities", flush=True)
    return p


def notes(p: Package, build: Build, day: date) -> None:
    if build.over:
        p.accept(
            "accounting.turnout",
            "The Region's page prints more voters than registered electors for " + "; ".join(sorted(build.over)),
        )
    if build.short:
        p.accept(
            "accounting.ballots_sum",
            "The Region's page prints voters that differ from the mayoral votes plus the non-valid ballots for "
            + "; ".join(sorted(build.short)),
        )
