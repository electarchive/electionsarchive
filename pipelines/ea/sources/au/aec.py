import csv
import functools
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date
from html import unescape
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.aec"
ROOT = "https://results.aec.gov.au"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
STATES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")
STATE_NAMES = {
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "ACT": "Australian Capital Territory",
    "NT": "Northern Territory",
}


class Event(NamedTuple):
    id: int
    day: date
    kind: str  # general, by_election, senate, referendum
    title: str
    state: str | None = None
    division: str | None = None


EVENTS = [
    Event(12246, date(2004, 10, 9), "general", "2004 Australian federal election"),
    Event(12426, date(2005, 3, 19), "by_election", "2005 Werriwa by-election", "NSW", "Werriwa"),
    Event(13745, date(2007, 11, 24), "general", "2007 Australian federal election"),
    Event(13813, date(2008, 6, 28), "by_election", "2008 Gippsland by-election", "VIC", "Gippsland"),
    Event(13826, date(2008, 9, 6), "by_election", "2008 Mayo by-election", "SA", "Mayo"),
    Event(13827, date(2008, 9, 6), "by_election", "2008 Lyne by-election", "NSW", "Lyne"),
    Event(14357, date(2009, 12, 5), "by_election", "2009 Bradfield by-election", "NSW", "Bradfield"),
    Event(14358, date(2009, 12, 5), "by_election", "2009 Higgins by-election", "VIC", "Higgins"),
    Event(15508, date(2010, 8, 21), "general", "2010 Australian federal election"),
    Event(17496, date(2013, 9, 7), "general", "2013 Australian federal election"),
    Event(17552, date(2014, 2, 8), "by_election", "2014 Griffith by-election", "QLD", "Griffith"),
    Event(17875, date(2014, 4, 5), "senate", "2014 Western Australian Senate election", "WA"),
    Event(18126, date(2015, 9, 19), "by_election", "2015 Canning by-election", "WA", "Canning"),
    Event(19402, date(2015, 12, 5), "by_election", "2015 North Sydney by-election", "NSW", "North Sydney"),
    Event(20499, date(2016, 7, 2), "general", "2016 Australian federal election"),
    Event(21364, date(2017, 12, 2), "by_election", "2017 New England by-election", "NSW", "New England"),
    Event(21379, date(2017, 12, 16), "by_election", "2017 Bennelong by-election", "NSW", "Bennelong"),
    Event(21751, date(2018, 3, 17), "by_election", "2018 Batman by-election", "VIC", "Batman"),
    Event(22692, date(2018, 7, 28), "by_election", "2018 Braddon by-election", "TAS", "Braddon"),
    Event(22693, date(2018, 7, 28), "by_election", "2018 Fremantle by-election", "WA", "Fremantle"),
    Event(22694, date(2018, 7, 28), "by_election", "2018 Longman by-election", "QLD", "Longman"),
    Event(22695, date(2018, 7, 28), "by_election", "2018 Mayo by-election", "SA", "Mayo"),
    Event(22696, date(2018, 7, 28), "by_election", "2018 Perth by-election", "WA", "Perth"),
    Event(22844, date(2018, 10, 20), "by_election", "2018 Wentworth by-election", "NSW", "Wentworth"),
    Event(24310, date(2019, 5, 18), "general", "2019 Australian federal election"),
    Event(25820, date(2020, 7, 4), "by_election", "2020 Eden-Monaro by-election", "NSW", "Eden-Monaro"),
    Event(25881, date(2020, 11, 28), "by_election", "2020 Groom by-election", "QLD", "Groom"),
    Event(27966, date(2022, 5, 21), "general", "2022 Australian federal election"),
    Event(28791, date(2023, 4, 1), "by_election", "2023 Aston by-election", "VIC", "Aston"),
    Event(29422, date(2023, 7, 15), "by_election", "2023 Fadden by-election", "QLD", "Fadden"),
    Event(29581, date(2023, 10, 14), "referendum", "2023 Australian Indigenous Voice referendum"),
    Event(29778, date(2024, 3, 2), "by_election", "2024 Dunkley by-election", "VIC", "Dunkley"),
    Event(29807, date(2024, 7, 6), "by_election", "2024 Cook by-election", "NSW", "Cook"),
    Event(31496, date(2025, 5, 3), "general", "2025 Australian federal election"),
    Event(31633, date(2026, 5, 9), "by_election", "2026 Farrer by-election", "NSW", "Farrer"),
]
HEADER = re.compile(r'^"?(StateAb|DivisionI[dD]|PartyAb|QuestionNo|State)"?,')
# Published only as HTML pages without polling-place files; see the report.
HTML_ONLY = {10822, 12426}


def base(event: Event) -> str:
    return f"{ROOT}/{event.id}/{'results' if event.id == 12246 else 'Website'}"


def download(event: Event, name: str) -> str:
    return f"{base(event)}/Downloads/{name}-{event.id}.csv"


def state_download(event: Event, name: str, state: str) -> str:
    return f"{base(event)}/Downloads/{name}-{event.id}-{state}.csv"


def external(event: Event, name: str, state: str) -> str:
    return f"{base(event)}/External/{name}-{event.id}-{state}.zip"


def states(event: Event) -> tuple[str, ...]:
    return (event.state,) if event.state else STATES


def has_house(event: Event) -> bool:
    return event.kind in ("general", "by_election")


def has_senate(event: Event) -> bool:
    return event.kind in ("general", "senate")


def house_urls(event: Event) -> list[str]:
    urls = [download(event, "HouseCandidatesDownload")]
    for state in states(event):
        urls.append(external(event, "HouseDopByPPDownload", state))
        urls.append(state_download(event, "HouseStateFirstPrefsByPollingPlaceDownload", state))
    if event.kind == "general":
        urls += [
            download(event, "HouseFirstPrefsByCandidateByVoteTypeDownload"),
            download(event, "HouseMembersElectedDownload"),
            download(event, "HouseDopByDivisionDownload"),
        ]
    return urls


def general_urls(event: Event) -> list[str]:
    if event.kind == "by_election":
        return []
    return [download(event, "GeneralEnrolmentByDivisionDownload"), download(event, "GeneralPollingPlacesDownload")]


def senate_urls(event: Event) -> list[str]:
    urls = [
        download(event, "SenateCandidatesDownload"),
        download(event, "SenateSenatorsElectedDownload"),
        download(event, "SenateFirstPrefsByDivisionByVoteTypeDownload"),
    ]
    if event.id != 31496:
        urls += [external(event, "SenateStateFirstPrefsByPollingPlaceDownload", state) for state in states(event)]
    return urls


def senate_division_urls(event: Event) -> list[str]:
    """The 2025 election publishes Senate first preferences by polling place once per division."""
    if event.id != 31496:
        return []
    ids = sorted(
        {row["DivisionID"] for row in csv_rows(download(event, "GeneralEnrolmentByDivisionDownload"))}, key=int
    )
    return [f"{base(event)}/Downloads/SenateDivisionFirstPrefsByPollingPlaceDownload-{event.id}-{i}.csv" for i in ids]


def referendum_urls(event: Event) -> list[str]:
    return [state_download(event, "ReferendumPollingPlaceResultsByStateDownload", state) for state in STATES]


def referendum_division_urls(event: Event) -> list[str]:
    ids = sorted(
        {row["DivisionID"] for row in csv_rows(download(event, "GeneralEnrolmentByDivisionDownload"))}, key=int
    )
    return [f"{base(event)}/ReferendumDivisionResults-{event.id}-{i}.htm" for i in ids]


def by_election_pages(event: Event, division: str) -> list[str]:
    return [
        f"{base(event)}/HouseDivisionPage-{event.id}-{division}.htm",
        f"{base(event)}/HouseDivisionFirstPrefs-{event.id}-{division}.htm",
    ]


def by_election_page(event: Event) -> str | None:
    known = raw.keys(SOURCE)
    return next((url for url in by_election_pages(event, _by_election_division(event)) if url in known), None)


def urls(event: Event) -> list[str]:
    found = general_urls(event)
    if has_house(event):
        found += house_urls(event)
    if has_senate(event):
        found += senate_urls(event)
    if event.kind == "referendum":
        found += referendum_urls(event)
    return found


def _suffix(url: str) -> str:
    return "." + url.rsplit(".", 1)[-1]


def fetch() -> None:
    for event in EVENTS:
        if event.id in HTML_ONLY:
            continue
        for url in urls(event):
            try:
                raw.fetch(SOURCE, url, suffix=_suffix(url))
            except httpx.HTTPStatusError as failure:
                print(f"{url}: {failure.response.status_code}", flush=True)
        if event.kind == "general":
            for url in senate_division_urls(event):
                raw.fetch(SOURCE, url, suffix=".csv")
        if event.kind == "referendum":
            for url in referendum_division_urls(event):
                raw.fetch(SOURCE, url, suffix=".htm")
        if event.kind == "by_election":
            for url in by_election_pages(event, _by_election_division(event)):
                try:
                    raw.fetch(SOURCE, url, suffix=".htm")
                    break
                except httpx.HTTPStatusError as failure:
                    print(f"{url}: {failure.response.status_code}", flush=True)


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _table(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if HEADER.search(line))
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


@functools.cache
def csv_rows(url: str) -> list[dict[str, str]]:
    return _table(_text(raw.cached(SOURCE, url).path.read_bytes()))


@functools.cache
def zip_rows(url: str) -> list[dict[str, str]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    rows: list[dict[str, str]] = []
    for name in sorted(n for n in archive.namelist() if n.lower().endswith(".csv")):
        rows += _table(_text(archive.read(name)))
    return rows


def _by_election_division(event: Event) -> str:
    rows = csv_rows(download(event, "HouseCandidatesDownload"))
    (division,) = {row["DivisionID"] for row in rows}
    return division


def field(row: dict[str, str], *names: str) -> str:
    lowered = {k.lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    raise KeyError(f"none of {names} in {sorted(row)}")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(abbreviation: str) -> str:
    return key("party", abbreviation)


AEC = key("source", SOURCE)
HOUSE = key("body", "house")
SENATE = key("body", "senate")
HOUSE_SYSTEM = key("electoral_system", "house", "irv")
SENATE_GVT = key("electoral_system", "senate", 1984)
SENATE_2016 = key("electoral_system", "senate", 2016)
REFERENDUM_SYSTEM = key("electoral_system", "referendum")
SENATE_REFORM = date(2016, 3, 1)
NO_PARTY = {"", "IND"}
ATL = re.compile(r"ticket votes|above-the-line", re.IGNORECASE)
MOBILE = re.compile(r"hospital team|mobile team|mobile polling|remote mobile", re.IGNORECASE)
DECLARATION = {
    "ABSENT": "AbsentVotes",
    "POSTAL": "PostalVotes",
    "PRE_POLL": "PrePollVotes",
    "PROVISIONAL": "ProvisionalVotes",
}
DECLARATION_NAMES = {
    "ABSENT": "absent votes",
    "POSTAL": "postal votes",
    "PRE_POLL": "declaration pre-poll votes",
    "PROVISIONAL": "provisional votes",
}
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        HOUSE_SYSTEM,
        date(1918, 12, 14),
        None,
        {"formula": "irv"},
        (
            "Each division elects one member by full preferential voting: voters must number every box, and the "
            "candidate with the fewest votes is excluded and their ballots passed on at full value until one candidate "
            "holds an absolute majority. The Australian Electoral Commission distributes preferences to the final two "
            "candidates in every division."
        ),
    ),
    (
        SENATE_GVT,
        date(1984, 1, 1),
        SENATE_REFORM,
        {"formula": "stv", "quota": "droop", "surplus": "gregory"},
        (
            "Each state elects six senators at a half-Senate election (twelve at a double dissolution) and each "
            "territory two, by the single transferable vote with a Droop quota. Voters marked one box above the line, "
            "adopting the group's registered voting ticket, or numbered every box below the line. Surpluses were "
            "transferred by the inclusive Gregory method."
        ),
    ),
    (
        SENATE_2016,
        SENATE_REFORM,
        None,
        {"formula": "stv", "quota": "droop", "surplus": "weighted_inclusive_gregory"},
        (
            "Each state elects six senators at a half-Senate election (twelve at a double dissolution) and each "
            "territory two, by the single transferable vote with a Droop quota. Since 2016 voters number at least six "
            "groups above the line or at least twelve candidates below the line, with no group voting tickets, and "
            "surpluses are transferred by the weighted inclusive Gregory method."
        ),
    ),
    (
        REFERENDUM_SYSTEM,
        date(1901, 1, 1),
        None,
        {"formula": "referendum", "majority": {"share": 0.5, "strict": True}, "doubleMajority": {"scheme": "au.state"}},
        (
            "A proposed alteration of the Constitution passes when a majority of voters nationally and a majority of "
            "voters in a majority of the states (four of six) approve it. Votes in the territories count only toward "
            "the national majority."
        ),
    ),
]
HOUSE_METHOD = (
    "First preferences and every count of the distribution of preferences per polling place, as the Australian "
    "Electoral Commission publishes them in its distribution of preferences by polling place files. Ordinary votes "
    "are reported per polling place, including pre-poll voting centres and mobile teams; absent, postal, "
    "declaration pre-poll, and provisional votes are reported once per division and stored as one unit each. "
    "Count round 0 holds first preferences and count round n the running totals after the n-th count; candidates "
    "already excluded are omitted from later counts. Informal votes come from the first preferences by polling "
    "place files{informal}. Enrolment is stored on a separate unit per division."
)
SENATE_METHOD = (
    "First preferences per polling place as the Australian Electoral Commission publishes them. Votes above the "
    "line are stored on one option per group and votes below the line on each candidate, so the options together "
    "hold every formal vote. Absent, postal, declaration pre-poll, and provisional votes are published only per "
    "division and are stored as one unit each. {informal}The distribution of preferences is not stored. "
    "Enrolment is stored on a separate unit per division."
)
REFERENDUM_METHOD = (
    "Yes, No, and informal votes per polling place from the Australian Electoral Commission's polling place "
    "files; absent, postal, declaration pre-poll, and provisional votes per division from its division results "
    "pages, stored as one unit each. Enrolment is stored on a separate unit per division."
)


def person_name(given: str, surname: str) -> str:
    surname = surname.title() if surname.isupper() else surname
    surname = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), surname)
    surname = re.sub(r"\bMac([a-z])(?=[a-z]{3})", lambda m: "Mac" + m.group(1), surname)
    return " ".join(f"{given} {surname}".split())


def _int(text: str) -> int:
    return int(text.replace(",", "") or 0)


# Geography ------------------------------------------------------------------------------------------------------


def _divisions() -> dict[str, tuple[str, list[tuple[date, str]]]]:
    """Division id -> (state, [(day, name)]) from every enrolment and candidate file."""
    seen: dict[str, tuple[str, list[tuple[date, str]]]] = {}
    for event in EVENTS:
        if event.id in HTML_ONLY:
            continue
        sources = []
        if event.kind == "by_election":
            sources.append(download(event, "HouseCandidatesDownload"))
        else:
            sources.append(download(event, "GeneralEnrolmentByDivisionDownload"))
        for url in sources:
            for row in csv_rows(url):
                id = row["DivisionID"]
                state = field(row, "StateAb")
                name = field(row, "DivisionNm")
                entry = seen.setdefault(id, (state, []))
                if entry[0] != state:
                    raise ValueError(f"division {id} is in {entry[0]} and {state}")
                entry[1].append((event.day, name))
    return seen


def _source_row(p: Package) -> None:
    p.add(
        "source",
        id=AEC,
        kind="official_body",
        publisher="Australian Electoral Commission",
        title="Tally Room archive of federal election and referendum results",
        url=f"{ROOT}/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Commonwealth of Australia (Australian Electoral Commission)",
    )


def _geo_files() -> list[str]:
    return [
        download(
            event, "HouseCandidatesDownload" if event.kind == "by_election" else "GeneralEnrolmentByDivisionDownload"
        )
        for event in EVENTS
        if event.id not in HTML_ONLY
    ]


def geo() -> Package:
    divisions = _divisions()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Australia's 8 states and territories and the {len(divisions)} federal electoral divisions that have "
        "returned results to the Australian Electoral Commission's Tally Room since 2004",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    _source_row(p)
    for url in _geo_files():
        p.artifact(raw.cached(SOURCE, url), AEC)
    for code, rank, kind, noun in (
        ("au.state", 1, "administrative", "state or territory"),
        ("au.division", 2, "electoral_district", "electoral division"),
        ("au.polling_place", 3, "precinct", "polling place"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for state in STATES:
        id = area("au.state", state)
        p.add("area", id=id, scheme_code="au.state")
        p.add("area_code", area_id=id, code=state)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, STATE_NAMES[state], "en", "official")
        p.name(id, state, "en", "abbreviation", False)
    for code, (state, names) in sorted(divisions.items(), key=lambda item: int(item[0])):
        id = area("au.division", code)
        p.add("area", id=id, scheme_code="au.division")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("au.state", state))
        latest = max(names)[1]
        p.name(id, latest, "en", "official")
        for old in sorted({name for _, name in names} - {latest}):
            p.name(id, old, "en", "historical", False)
    return p


# Registry -------------------------------------------------------------------------------------------------------


def _candidate_files(event: Event) -> list[str]:
    files = []
    if has_house(event):
        files.append(download(event, "HouseCandidatesDownload"))
    if has_senate(event):
        files.append(download(event, "SenateCandidatesDownload"))
    return files


def _party_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for event in EVENTS:
        if event.id in HTML_ONLY:
            continue
        for url in _candidate_files(event):
            for row in csv_rows(url):
                ab, name = field(row, "PartyAb"), field(row, "PartyNm")
                if ab not in NO_PARTY and name:
                    names[ab] = name
    return names


def registry() -> Package:
    names = _party_names()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"Australia's House of Representatives and Senate, their electoral systems, and the {len(names)} party "
        "abbreviations on the Australian Electoral Commission's candidate files since 2004, with the source files "
        "every election package cites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    staged = set(_geo_files())
    for event in EVENTS:
        if event.id not in HTML_ONLY:
            for url in dict.fromkeys([*_candidate_files(event), *_cited(event)]):
                if url not in staged:
                    staged.add(url)
                    p.artifact(raw.cached(SOURCE, url), AEC)
    p.add("body", id=HOUSE, polity_id=AUSTRALIA, kind="lower_house")
    p.name(HOUSE, "House of Representatives", "en", "official")
    p.add("body", id=SENATE, polity_id=AUSTRALIA, kind="upper_house")
    p.name(SENATE, "Senate", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for ab, name in sorted(names.items()):
        id = party(ab)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.add("external_id", entity_id=id, scheme="au.aec.party", value=ab)
        p.name(id, name, "en", "official")
        p.name(id, ab, "en", "abbreviation", False)
    return p


# Elections ------------------------------------------------------------------------------------------------------


class Values:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "count_round", "value")
        }

    def add(
        self, result_set: str, unit: str, option: str | None, measure: int, value: int, count_round: int = 0
    ) -> None:
        for column, item in zip(self.columns, (result_set, unit, option, measure, count_round, value), strict=True):
            self.columns[column].append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


class Units:
    def __init__(self, p: Package, day: date) -> None:
        self.p = p
        self.day = day
        self.ids: dict[str, str] = {}

    def _add(self, code: str, division: str, kind: str) -> str:
        if code in self.ids:
            return self.ids[code]
        id = self.ids[code] = key("unit", code)
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=AUSTRALIA,
            scheme_code="au.polling_place",
            code=code,
            area_relation="within",
            area_id=area("au.division", division),
            kind=kind,
        )
        return id

    def place(self, division: str, place: str, name: str) -> str:
        id = self._add(f"{self.day}:{place}", division, "mobile" if MOBILE.search(name) else "standard")
        if self.ids.get(f"name:{id}") is None:
            self.ids[f"name:{id}"] = name
            self.p.name(id, name, "en", "official")
        return id

    def declaration(self, division: str, kind: str) -> str:
        code = f"{self.day}:{division}:{kind}"
        new = code not in self.ids
        id = self._add(code, division, "postal_district" if kind == "POSTAL" else "aggregate")
        if new:
            self.p.name(id, f"Division {division}: {DECLARATION_NAMES[kind]}", "en", "official")
        return id

    def roll(self, division: str) -> str:
        code = f"{self.day}:{division}:ROLL"
        new = code not in self.ids
        id = self._add(code, division, "aggregate")
        if new:
            self.p.name(id, f"Division {division}: electoral roll", "en", "official")
        return id


def _result_set(p: Package, election: str, contest: str, method: str) -> str:
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=AEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def _enrolment(event: Event) -> dict[str, tuple[str, int]]:
    if event.kind == "by_election":
        return {}
    return {
        row["DivisionID"]: (field(row, "StateAb"), _int(row["Enrolment"]))
        for row in csv_rows(download(event, "GeneralEnrolmentByDivisionDownload"))
    }


def _parties() -> set[str]:
    path = package.folder(f"{SOURCE}/registry") / "build" / "party.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _party_of(ab: str, known: set[str]) -> str | None:
    id = party(ab)
    return id if ab not in NO_PARTY and id in known else None


def house(p: Package, event: Event, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    candidates = {row["CandidateID"]: row for row in csv_rows(download(event, "HouseCandidatesDownload"))}
    dop: list[dict[str, str]] = []
    first: list[dict[str, str]] = []
    for state in states(event):
        dop += zip_rows(external(event, "HouseDopByPPDownload", state))
        first += csv_rows(state_download(event, "HouseStateFirstPrefsByPollingPlaceDownload", state))
    enrolment = _enrolment(event)
    declared: dict[str, list[dict[str, str]]] = defaultdict(list)
    elected: dict[str, str] = {}
    final: dict[str, dict[str, int]] = defaultdict(dict)
    if event.kind == "general":
        for row in csv_rows(download(event, "HouseFirstPrefsByCandidateByVoteTypeDownload")):
            declared[row["DivisionID"]].append(row)
        elected = {
            row["DivisionID"]: row["CandidateID"] for row in csv_rows(download(event, "HouseMembersElectedDownload"))
        }
        counts = csv_rows(download(event, "HouseDopByDivisionDownload"))
        last: dict[str, int] = {}
        for row in counts:
            last[row["DivisionID"]] = max(last.get(row["DivisionID"], 0), int(row["CountNumber"]))
        for row in counts:
            if int(row["CountNumber"]) == last[row["DivisionID"]] and row["CalculationType"] == "Preference Count":
                final[row["DivisionID"]][row["CandidateID"]] = _int(row["CalculationValue"])
    by_division: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in dop:
        if row["CalculationType"] == "Preference Count":
            by_division[field(row, "DivisionId")].append(row)
    informal: dict[str, dict[str, int]] = defaultdict(dict)
    ordinary: dict[tuple[str, str], int] = {}
    for row in first:
        if row["CandidateID"] == "999" or row["Surname"] == "Informal":
            informal[row["DivisionID"]][row["PollingPlaceID"]] = _int(row["OrdinaryVotes"])
        else:
            ordinary[(row["PollingPlaceID"], row["CandidateID"])] = _int(row["OrdinaryVotes"])
    contests = 0
    for division, rows in sorted(by_division.items(), key=lambda item: int(item[0])):
        contests += 1
        name = rows[0]["DivisionNm"]
        state = rows[0]["StateAb"]
        contest = key("contest", event.id, "house", division)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area("au.division", division),
            magnitude=1,
            round_number=1,
            electoral_system_id=HOUSE_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{name} ({state})", "en", "official")
        p.add("slug", path=f"{path}/house-{changeset.slug(name)}", entity_id=contest, is_current=True)
        has_informal = bool(informal.get(division))
        result_set = _result_set(
            p,
            election,
            contest,
            HOUSE_METHOD.format(
                informal=" and, for declaration votes, from the first preferences by vote type file"
                if event.kind == "general"
                else "; the by-election files give no informal count for declaration votes"
            ),
        )
        options: dict[str, str] = {}
        for row in sorted({field(r, "CandidateId"): r for r in rows}.values(), key=lambda r: int(r["BallotPosition"])):
            id = field(row, "CandidateId")
            entry = candidates[id]
            option = options[id] = key("option", event.id, "house", id)
            label = f"{entry['Surname']}, {entry['GivenNm']}"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=_party_of(entry["PartyAb"], known),
                ballot_position=int(row["BallotPosition"]),
                ballot_label=label,
            )
            person = key("person", event.id, id)
            p.add("person", id=person)
            p.name(person, person_name(entry["GivenNm"], entry["Surname"]), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", event.id, id),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=_party_of(entry["PartyAb"], known),
                is_independent=entry["PartyAb"] in NO_PARTY,
            )
        totals: dict[tuple[int, str], int] = Counter()
        for row in rows:
            totals[(int(row["CountNum"]), field(row, "CandidateId"))] += _int(row["CalculationValue"])
        last_count = max(count for count, _ in totals)
        standing = {c: v for (n, c), v in totals.items() if n == last_count and v > 0}
        if len(standing) != 2 and len(options) > 1:
            raise ValueError(f"{event.id} {name}: {len(standing)} candidates at the final count")
        winner = max(standing, key=lambda c: standing[c])
        printed = {c: v for c, v in final.get(division, {}).items() if v}
        if printed and printed != standing:
            if set(printed) != set(standing):
                raise ValueError(f"{event.id} {name}: final two {sorted(standing)} differ from {sorted(printed)}")
            gaps = ", ".join(
                f"{candidates[c]['GivenNm']} {candidates[c]['Surname']} {standing[c]:,} against {printed[c]:,}"
                for c in sorted(printed)
                if printed[c] != standing[c]
            )
            print(f"{event.id} {name}: final count by polling place differs from the division file: {gaps}")
            p.add(
                "note",
                id=key("note", result_set, "final"),
                entity_id=result_set,
                kind="caveat",
                body_md="The Australian Electoral Commission's distribution of preferences by polling place sums "
                f"to a different final count than its distribution of preferences by division ({gaps}). The "
                "polling-place figures are stored as published; the winner is the same in both.",
                lang="en",
            )
        if elected and elected[division] != winner:
            raise ValueError(f"{event.id} {name}: declared member {elected[division]} is not the count leader {winner}")
        for id, option in options.items():
            kind = "elected" if id == winner else "lost" if id in standing else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        valid: Counter[str] = Counter()
        for row in rows:
            count, candidate = int(row["CountNum"]), field(row, "CandidateId")
            if count > 0 and totals[(count, candidate)] == 0:
                continue
            place, place_name = field(row, "PPId"), field(row, "PPNm")
            unit = units.declaration(division, place_name) if place == "0" else units.place(division, place, place_name)
            value = _int(row["CalculationValue"])
            values.add(result_set, unit, options[candidate], 1, value, count)
            if count == 0:
                valid[unit] += value
                if place != "0" and ordinary.get((place, candidate), value) != value:
                    raise ValueError(f"{event.id} {name} {place_name}: first preferences differ between files")
        bad: dict[str, int] = {}
        for row in declared.get(division, []):
            if row["CandidateID"] == "999" or row["Surname"] == "Informal":
                for kind, column in DECLARATION.items():
                    bad[units.declaration(division, kind)] = _int(row[column])
                p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=_int(row["TotalVotes"]))
            else:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[row["CandidateID"]],
                    measure=1,
                    value=_int(row["TotalVotes"]),
                )
        if declared.get(division):
            formal = sum(_int(r["TotalVotes"]) for r in declared[division] if r["CandidateID"] != "999")
            p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=formal)
        if has_informal:
            for place, n in informal[division].items():
                if units.ids.get(f"{event.day}:{place}"):
                    bad[units.ids[f"{event.day}:{place}"]] = n
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            if unit in bad:
                values.add(result_set, unit, None, 5, bad[unit])
                values.add(result_set, unit, None, 3, n + bad[unit])
        if division in enrolment:
            values.add(result_set, units.roll(division), None, 2, enrolment[division][1])
    return contests


def _group(row: dict[str, str]) -> str:
    return field(row, "Ticket", "Group")


def _senate_places(event: Event) -> list[dict[str, str]]:
    if event.id == 31496:
        rows: list[dict[str, str]] = []
        for url in senate_division_urls(event):
            rows += csv_rows(url)
        return rows
    rows = []
    for state in states(event):
        rows += zip_rows(external(event, "SenateStateFirstPrefsByPollingPlaceDownload", state))
    return [row for row in rows if row.get("StateAb")]


def senate(p: Package, event: Event, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    candidates = {row["CandidateID"]: row for row in csv_rows(download(event, "SenateCandidatesDownload"))}
    winners = csv_rows(download(event, "SenateSenatorsElectedDownload"))
    places = _senate_places(event)
    divisions = csv_rows(download(event, "SenateFirstPrefsByDivisionByVoteTypeDownload"))
    enrolment = _enrolment(event)
    system = SENATE_2016 if event.day >= SENATE_REFORM else SENATE_GVT
    contests = 0
    for state in states(event):
        here = [row for row in divisions if row["StateAb"] == state]
        if not here:
            continue
        contests += 1
        seats = [row for row in winners if row["StateAb"] == state]
        contest = key("contest", event.id, "senate", state)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE,
            district_area_id=area("au.state", state),
            magnitude=len(seats),
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"Senate, {STATE_NAMES[state]}", "en", "official")
        p.add("slug", path=f"{path}/senate-{state.lower()}", entity_id=contest, is_current=True)
        in_state = [row for row in places if row["StateAb"] == state]
        has_informal = any(_group(row) == "ZZ" for row in in_state)
        result_set = _result_set(
            p,
            election,
            contest,
            SENATE_METHOD.format(
                informal="Informal ballots are stored for polling places; the source gives no informal count for "
                "declaration votes. "
                if has_informal
                else "The source gives no informal count per polling place for this election. "
            ),
        )
        # Options: one per group above the line, one per candidate below it, in ballot order.
        groups: dict[str, str] = {}
        members: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for row in here:
            group = _group(row)
            if ATL.search(row["CandidateDetails"]):
                groups.setdefault(group, field(row, "PartyName"))
            elif group != "ZZ" and row["CandidateID"] in candidates:
                entry = (int(row["BallotPosition"]), row["CandidateID"])
                if entry not in members[group]:
                    members[group].append(entry)
        order = sorted(set(groups) | set(members), key=lambda g: (g == "UG", len(g), g))
        options: dict[str, str] = {}
        elected_names = {(r["Surname"].upper(), r["GivenNm"].upper()) for r in seats}
        position = 0
        for group in order:
            if group in groups:
                position += 1
                option = options[f"group:{group}"] = key("option", event.id, "senate", state, "group", group)
                abbreviations = {candidates[c]["PartyAb"] for _, c in members[group]}
                party_id = _party_of(abbreviations.pop(), known) if len(abbreviations) == 1 else None
                label = f"Group {group}" + (f": {groups[group]}" if groups[group] else "") + " (above the line)"
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="legend",
                    party_id=party_id,
                    ballot_position=position,
                    ballot_label=label,
                )
            for _, id in sorted(members[group]):
                position += 1
                entry = candidates[id]
                option = options[id] = key("option", event.id, "senate", id)
                label = f"{entry['Surname']}, {entry['GivenNm']}"
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=_party_of(entry["PartyAb"], known),
                    ballot_position=position,
                    ballot_label=label,
                )
                person = key("person", event.id, "senate", id)
                p.add("person", id=person)
                p.name(person, person_name(entry["GivenNm"], entry["Surname"]), "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", event.id, "senate", id),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    member_of_party_id=_party_of(entry["PartyAb"], known),
                    is_independent=entry["PartyAb"] in NO_PARTY,
                )
                won = (entry["Surname"].upper(), entry["GivenNm"].upper()) in elected_names
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        won_count = sum(
            1
            for (s, g) in elected_names
            if any(
                (c["Surname"].upper(), c["GivenNm"].upper()) == (s, g) and c["StateAb"] == state
                for c in candidates.values()
            )
        )
        if won_count != len(seats):
            raise ValueError(f"{event.id} {state}: matched {won_count} of {len(seats)} senators elected")

        def option_of(row: dict[str, str]) -> str:
            if ATL.search(row["CandidateDetails"]):
                return options[f"group:{_group(row)}"]
            return options[row["CandidateID"]]

        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        ordinary: Counter[tuple[str, str]] = Counter()
        for row in in_state:
            division = row["DivisionID"]
            unit = units.place(division, field(row, "PollingPlaceID"), row["PollingPlaceNm"])
            value = _int(row["OrdinaryVotes"])
            if _group(row) == "ZZ":
                bad[unit] = value
                continue
            option = option_of(row)
            values.add(result_set, unit, option, 1, value)
            valid[unit] += value
            ordinary[(division, option)] += value
        totals: Counter[str] = Counter()
        for row in here:
            division = row["DivisionID"]
            option = option_of(row)
            totals[option] += _int(row["TotalVotes"])
            if ordinary[(division, option)] != _int(row["OrdinaryVotes"]):
                raise ValueError(
                    f"{event.id} {state} {row['DivisionNm']} {row['CandidateDetails']}: polling places sum to "
                    f"{ordinary[(division, option)]}, the division file prints {row['OrdinaryVotes']}"
                )
            for kind, column in DECLARATION.items():
                unit = units.declaration(division, kind)
                value = _int(row[column])
                values.add(result_set, unit, option, 1, value)
                valid[unit] += value
        for option, total in totals.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=total)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=sum(totals.values()))
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            if unit in bad:
                values.add(result_set, unit, None, 5, bad[unit])
                values.add(result_set, unit, None, 3, n + bad[unit])
        for division, (where, n) in sorted(enrolment.items()):
            if where == state:
                values.add(result_set, units.roll(division), None, 2, n)
    return contests


VOTE_TYPES = ("Ordinary", "Absent", "Provisional", "Declaration pre-poll", "Postal", "Total")


def _referendum_division(event: Event, url: str) -> dict[str, list[int]]:
    page = raw.cached(SOURCE, url).path.read_text(errors="replace")
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", page)))
    start = text.index("Vote Type Ordinary Absent Provisional Declaration pre-poll Postal Total")
    found: dict[str, list[int]] = {}
    for label in ("Yes", "No", "Informal"):
        match = re.compile(rf" {label} " + r"(?:([\d,]+) [\d.]+ ?){6}").search(text, start)
        if match is None:
            raise ValueError(f"{url}: no {label} row")
        numbers = re.findall(r"([\d,]+) [\d.]+", match.group(0))
        found[label] = [_int(n) for n in numbers]
    return found


def referendum(p: Package, event: Event, election: str, path: str, units: Units, values: Values) -> int:
    contest = key("contest", event.id, "referendum")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=REFERENDUM_SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="mandatory_referendum")
    p.name(
        contest,
        "Constitution Alteration (Aboriginal and Torres Strait Islander Voice) 2023",
        "en",
        "official",
    )
    p.add("slug", path=f"{path}/voice", entity_id=contest, is_current=True)
    result_set = _result_set(p, election, contest, REFERENDUM_METHOD)
    options = {}
    for position, answer in enumerate(("Yes", "No"), 1):
        option = options[answer] = key("option", event.id, "referendum", answer)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=answer,
        )
    ordinary: Counter[tuple[str, str]] = Counter()
    for state in STATES:
        for row in csv_rows(state_download(event, "ReferendumPollingPlaceResultsByStateDownload", state)):
            division = row["DivisionID"]
            unit = units.place(division, row["PollingPlaceId"], row["PollingPlaceNm"])
            yes, no, bad = _int(row["YesVotes"]), _int(row["NoVotes"]), _int(row["InformalVotes"])
            values.add(result_set, unit, options["Yes"], 1, yes)
            values.add(result_set, unit, options["No"], 1, no)
            values.add(result_set, unit, None, 4, yes + no)
            values.add(result_set, unit, None, 5, bad)
            values.add(result_set, unit, None, 3, _int(row["TotalVotes"]))
            ordinary[(division, "Yes")] += yes
            ordinary[(division, "No")] += no
            ordinary[(division, "Informal")] += bad
    totals: Counter[str] = Counter()
    for url in referendum_division_urls(event):
        division = url.rsplit("-", 1)[1].removesuffix(".htm")
        table = _referendum_division(event, url)
        for label, numbers in table.items():
            if numbers[0] != ordinary[(division, label)]:
                raise ValueError(
                    f"{url}: ordinary {label} {numbers[0]} but polling places sum to {ordinary[(division, label)]}"
                )
            totals[label] += numbers[5]
        for index, kind in enumerate(("ABSENT", "PROVISIONAL", "PRE_POLL", "POSTAL"), 1):
            unit = units.declaration(division, kind)
            yes, no, bad = table["Yes"][index], table["No"][index], table["Informal"][index]
            values.add(result_set, unit, options["Yes"], 1, yes)
            values.add(result_set, unit, options["No"], 1, no)
            values.add(result_set, unit, None, 4, yes + no)
            values.add(result_set, unit, None, 5, bad)
            values.add(result_set, unit, None, 3, yes + no + bad)
    for label in ("Yes", "No"):
        p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=totals[label])
    p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=totals["Informal"])
    p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=totals["Yes"] + totals["No"])
    winner = "Yes" if totals["Yes"] > totals["No"] else "No"
    for label, option in options.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    for division, (_, n) in sorted(_enrolment(event).items()):
        values.add(result_set, units.roll(division), None, 2, n)
    return 1


def days() -> list[date]:
    return sorted({event.day for event in EVENTS if event.id not in HTML_ONLY})


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def _cited(event: Event) -> list[str]:
    found = list(urls(event))
    if event.kind == "general":
        found = [u for u in found if "GeneralPollingPlaces" not in u] + senate_division_urls(event)
    if event.kind == "referendum":
        found = [u for u in found if "GeneralPollingPlaces" not in u] + referendum_division_urls(event)
    return found


def election(day: date) -> Package:
    held = [event for event in EVENTS if event.day == day and event.id not in HTML_ONLY]
    kind = held[0].kind
    if len(held) > 1:
        names = [event.division or "" for event in held]
        title = f"{day.year} {', '.join(names[:-1])} and {names[-1]} by-elections"
    else:
        title = held[0].title
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {title}, by polling place, from the Australian Electoral Commission's Tally Room",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    for event in held:
        for url in _cited(event):
            p.uses(raw.cached(SOURCE, url))
    election_id = key("election", day)
    word = {"general": "general", "by_election": "by-election", "senate": "senate", "referendum": "referendum"}[kind]
    path = f"au/{day}-{word}"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind={"senate": "special"}.get(kind, kind),
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, title, "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    if kind == "senate":
        p.add(
            "note",
            id=key("note", election_id),
            entity_id=election_id,
            kind="caveat",
            body_md="The High Court, sitting as the Court of Disputed Returns, voided the 2013 Senate election in "
            "Western Australia after 1,370 ballot papers were lost during a recount, and the state's six senators "
            "were elected again on 5 April 2014.",
            lang="en",
        )
    units = Units(p, day)
    values = Values()
    known = _parties()
    for event in held:
        if has_house(event):
            house(p, event, election_id, path, units, values, known)
        if has_senate(event):
            senate(p, event, election_id, path, units, values, known)
        if event.kind == "referendum":
            referendum(p, event, election_id, path, units, values)
    p.frame("result_value", values.frame())
    csv_rows.cache_clear()
    zip_rows.cache_clear()
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def emit() -> None:
    package.run("load", packages())
