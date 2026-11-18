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
    Event(29807, date(2024, 4, 13), "by_election", "2024 Cook by-election", "NSW", "Cook"),
    Event(31496, date(2025, 5, 3), "general", "2025 Australian federal election"),
    Event(31633, date(2026, 5, 9), "by_election", "2026 Farrer by-election", "NSW", "Farrer"),
]
HEADER = re.compile(r'^"?(StateAb|DivisionI[dD]|PartyAb|QuestionNo|State)"?,')
# Published as HTML pages only, without CSV downloads; election_html builds them.
HTML_ONLY = {10822, 12426}


def base(event: Event) -> str:
    folder = {12246: "results", 12426: ""}.get(event.id, "Website")
    return f"{ROOT}/{event.id}/{folder}".rstrip("/")


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
    from ea.sources.au import aec_cd, aec_pages

    aec_cd.fetch()
    aec_pages.fetch()
    fetch_html()
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
    "ORDINARY_OTHER": "ordinary votes the polling place pages do not show",
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
    "First preferences per polling place, as the Australian Electoral Commission publishes them in its "
    "distribution of preferences by polling place files. Ordinary votes are reported per polling place, including "
    "pre-poll voting centres and mobile teams; absent, postal, declaration pre-poll, and provisional votes are "
    "reported once per division and stored as one unit each. The later counts of the distribution of preferences "
    "are not stored here; the outcomes are the AEC's declared members. Informal votes come from the first "
    "preferences by polling place files{informal}. Enrolment is stored on a separate unit per division."
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


@functools.cache
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
    named = {(state, name.lower()): id for id, (state, names) in seen.items() for _, name in names}
    for url in pages_2001()[0]:
        state, name = _heading_2001(url)
        id = named.get((state, name.lower()), f"{state}-{name}")
        seen.setdefault(id, (state, []))[1].append((V2001.day, name))
    from ea.sources.au import aec_cd

    for year, state, name in aec_cd.division_names():
        id = named.get((state, name.lower()), f"{state}-{name}")
        entry = seen.setdefault(id, (state, []))
        if entry[0] != state:
            raise ValueError(f"division {id} is in {entry[0]} and {state}")
        entry[1].append((year.day, name))
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
    files = [
        download(
            event, "HouseCandidatesDownload" if event.kind == "by_election" else "GeneralEnrolmentByDivisionDownload"
        )
        for event in EVENTS
        if event.id not in HTML_ONLY
    ]
    from ea.sources.au import aec_cd

    return [*files, *pages_2001()[0], aec_cd.ZIP]


def geo() -> Package:
    divisions = _divisions()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Australia's 8 states and territories and the {len(divisions)} federal electoral divisions that have "
        "returned results to the Australian Electoral Commission since 1993",
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
    for code, (state, names) in sorted(divisions.items(), key=lambda item: (not item[0].isdigit(), item[0].zfill(6))):
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
    for row in cells(page(PARTY_CODES)):
        if len(row) == 2 and re.fullmatch(r"[A-Z0-9]{2,4}", row[0]) and row[0] not in NO_PARTY:
            names.setdefault(row[0], row[1])
    from ea.sources.au import aec_cd

    for ab, name in aec_cd.party_names().items():
        names.setdefault(ab, name)
    return names


def registry() -> Package:
    names = _party_names()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"Australia's House of Representatives and Senate, their electoral systems, and the {len(names)} party "
        "abbreviations on the Australian Electoral Commission's results since 1993, with the source files "
        "every election package cites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    staged = set(_geo_files())
    files = [PARTY_CODES, *html_cited(V2001), *html_cited(WERRIWA)]
    for event in EVENTS:
        if event.id not in HTML_ONLY:
            files += [*_candidate_files(event), *_cited(event)]
    for url in dict.fromkeys(files):
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


PAGE_DECLARATION = {
    "Absent": "ABSENT",
    "Provisional": "PROVISIONAL",
    "Declaration pre-poll": "PRE_POLL",
    "Postal": "POSTAL",
}


class ByVoteType(NamedTuple):
    candidates: dict[str, int]  # "SURNAME, Given" in upper case -> total first preferences
    formal: int
    informal: int
    declaration: dict[str, tuple[int, int]]  # DECLARATION kind -> (formal, informal)
    enrolment: int | None


def by_vote_type(event: Event) -> ByVoteType | None:
    """The first preferences by vote type table on a by-election's division page, which the downloads lack."""
    url = by_election_page(event)
    if url is None:
        return None
    html = page(url)
    rows = cells(html)
    begin = next((i for i, row in enumerate(rows) if row[:3] == ["Candidate", "Party", "Ordinary"]), None)
    if begin is None:
        return None
    header = rows[begin][2:-2]
    if "Total" not in rows[begin] or any(kind not in PAGE_DECLARATION for kind in header[1:]):
        return None
    candidates: dict[str, int] = {}
    lines: dict[str, list[int]] = {}
    for row in rows[begin + 2 :]:
        if len(row) < 2 * len(header) + 4:
            break
        numbers = [_int(row[2 + 2 * i]) for i in range(len(header) + 1)]
        if row[0] in ("Formal", "Informal"):
            lines[row[0]] = numbers
            if row[0] == "Informal":
                break
        else:
            candidates[row[0].upper()] = numbers[-1]
    if set(lines) != {"Formal", "Informal"}:
        return None
    declaration = {
        PAGE_DECLARATION[kind]: (lines["Formal"][i + 1], lines["Informal"][i + 1]) for i, kind in enumerate(header[1:])
    }
    match = re.search(r'lblEnrolment">([\d,]+)<', html)
    return ByVoteType(
        candidates, lines["Formal"][-1], lines["Informal"][-1], declaration, _int(match.group(1)) if match else None
    )


def _enrolment(event: Event) -> dict[str, tuple[str, int]]:
    if event.kind == "by_election":
        table = by_vote_type(event)
        if table is None or table.enrolment is None or event.state is None:
            return {}
        return {_by_election_division(event): (event.state, table.enrolment)}
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
    table = by_vote_type(event) if event.kind == "by_election" else None
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
                else " and, for declaration votes, from the division page's first preferences by vote type table"
                if table is not None
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
        if elected and elected[division] != winner:
            raise ValueError(f"{event.id} {name}: declared member {elected[division]} is not the count leader {winner}")
        for id, option in options.items():
            kind = "elected" if id == winner else "lost" if id in standing else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        valid: Counter[str] = Counter()
        for row in rows:
            count, candidate = int(row["CountNum"]), field(row, "CandidateId")
            if count > 0:
                continue
            place, place_name = field(row, "PPId"), field(row, "PPNm")
            unit = units.declaration(division, place_name) if place == "0" else units.place(division, place, place_name)
            value = _int(row["CalculationValue"])
            values.add(result_set, unit, options[candidate], 1, value)
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
        if table is not None:
            labels = {f"{c['Surname']}, {c['GivenNm']}".upper(): id for id, c in candidates.items() if id in options}
            if set(table.candidates) != set(labels):
                raise ValueError(f"{event.id} {name}: division page candidates differ from the candidates file")
            for label, total in table.candidates.items():
                p.add("declared", result_set_id=result_set, option_id=options[labels[label]], measure=1, value=total)
            p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=table.formal)
            p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=table.informal)
            for kind, (formal, invalid) in table.declaration.items():
                code = f"{event.day}:{division}:{kind}"
                if formal == 0 and invalid == 0 and code not in units.ids:
                    continue
                unit = units.declaration(division, kind)
                valid[unit] += 0
                if valid[unit] != formal:
                    raise ValueError(f"{event.id} {name}: {kind} formal votes {valid[unit]:,} against {formal:,}")
                bad[unit] = invalid
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
    from ea.sources.au import aec_cd, aec_pages

    return sorted({V2001.day, *(event.day for event in EVENTS), *aec_cd.DAYS, *aec_pages.DAYS})


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def _cited(event: Event) -> list[str]:
    found = list(urls(event))
    if event.kind == "by_election" and (division_page := by_election_page(event)):
        found.append(division_page)
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


def build(day: date) -> Package:
    from ea.sources.au import aec_cd, aec_pages

    if day in aec_pages.DAYS:
        return aec_pages.election(day)
    for year in aec_cd.YEARS:
        if day == year.day:
            return aec_cd.election(year)
    if day == V2001.day:
        return election_html(V2001)
    if day == WERRIWA.day:
        return election_html(WERRIWA)
    return election(day)


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())


# The 2001 election and the 2005 Werriwa by-election: HTML pages only ------------------------------------------------

V2001 = Event(10822, date(2001, 11, 10), "general", "2001 Australian federal election")
WERRIWA = next(event for event in EVENTS if event.id == 12426)
PARTY_CODES = f"{ROOT}/10822/Website/party_codes.htm"
LINK = re.compile(r'href="?([^"\s>#]+\.htm)', re.IGNORECASE)


def page(url: str) -> str:
    return _text(raw.cached(SOURCE, url).path.read_bytes())


def _links(url: str, pattern: str) -> list[str]:
    folder = url.rsplit("/", 1)[0]
    found = [f"{folder}/{href}" for href in LINK.findall(page(url)) if re.fullmatch(pattern, href)]
    return list(dict.fromkeys(found))


def pages_2001() -> tuple[list[str], list[str], list[str]]:
    """Division pages, polling place pages, and Senate pages of the 2001 election."""
    folder = base(V2001)
    divisions = [
        url for state in STATES for url in _links(f"{folder}/div_{state.lower()}.htm", r"[A-Z]{2,3}[A-Z]{4}\.htm")
    ]
    places = [url for division in divisions for url in _links(division, r"\d+\.htm")[:1]]
    senate = [f"{folder}/SEN_{state}.htm" for state in STATES]
    return divisions, places, senate


def pages_werriwa() -> list[str]:
    folder = base(WERRIWA)
    return [
        f"{folder}/HouseDivisionFirstPrefsByVoteType-12426-153.htm",
        f"{folder}/HouseDivisionPollingPlaces-12426-153.htm",
        f"{folder}/HouseDivisionTcpByPollingPlace-12426-153.htm",
        f"{folder}/HouseDivisionFirstPrefs-12426-153.htm",
    ]


def fetch_html() -> None:
    folder = base(V2001)
    for state in STATES:
        raw.fetch(SOURCE, f"{folder}/div_{state.lower()}.htm", suffix=".htm")
    divisions = [
        url for state in STATES for url in _links(f"{folder}/div_{state.lower()}.htm", r"[A-Z]{2,3}[A-Z]{4}\.htm")
    ]
    for url in divisions:
        raw.fetch(SOURCE, url, suffix=".htm")
    _, places, senate = pages_2001()
    for url in [*places, *senate, f"{folder}/party_codes.htm"]:
        raw.fetch(SOURCE, url, suffix=".htm")
    for url in pages_werriwa():
        raw.fetch(SOURCE, url, suffix=".htm")
    for url in werriwa_places():
        raw.fetch(SOURCE, url, suffix=".htm")


def werriwa_places() -> list[str]:
    return _links(pages_werriwa()[1], r"HousePollingPlaceFirstPrefs-12426-\d+\.htm")


ROW = re.compile(r"<tr[^>]*>(.*?)(?=<tr[\s>]|</table|$)", re.IGNORECASE | re.DOTALL)
CELL = re.compile(r"<t[dh][^>]*>(.*?)(?=<t[dh][\s>]|</tr|$)", re.IGNORECASE | re.DOTALL)


def cells(html: str) -> list[list[str]]:
    """Every table row of a page as its cells' text, for the pre-2006 pages whose markup leaves tags unclosed."""
    return [
        [" ".join(unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ").split()) for cell in CELL.findall(row)]
        for row in ROW.findall(html)
    ]


TYPES_2001 = {
    "Absent": "ABSENT",
    "Postal": "POSTAL",
    "Pre-poll": "PRE_POLL",
    "Pre-Poll": "PRE_POLL",
    "Provisional": "PROVISIONAL",
}
LABEL = re.compile(r"^(.*?)(?: \(([A-Z0-9]+)\))?$")
HTML_METHOD = (
    "First preferences per polling place from the Australian Electoral Commission's {pages}; absent, postal, "
    "pre-poll, and provisional votes per division from its first preferences by vote type table, stored as one unit "
    "each. The distribution of preferences is not stored. Enrolment is stored on a separate unit per division."
)


def _heading_2001(url: str) -> tuple[str, str]:
    heading = next(row[0] for row in cells(page(url)) if row and " : " in row[0] and "(" in row[0])
    state, name = heading.split(" : ", 1)
    return state, name.split(" (")[0]


def division_ids() -> dict[tuple[str, str], str]:
    return {(state, name.lower()): id for id, (state, names) in _divisions().items() for _, name in names}


def _number(text: str) -> int | None:
    text = text.replace(",", "").strip()
    return int(text) if text.isdigit() else None


def _enrolled(rows: list[list[str]]) -> int | None:
    for row in rows:
        for cell in row:
            match = re.search(r"Enrol(?:led|ment) ?: ?([\d,]+)", cell)
            if match:
                return _int(match.group(1))
    return None


def _candidate(
    p: Package, event: Event, contest: str, tag: str, label: str, position: int, ab: str, known: set[str]
) -> str:
    option = key("option", event.id, "house", tag)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=_party_of(ab, known),
        ballot_position=position,
        ballot_label=label,
    )
    surname, _, given = label.partition(", ")
    person = key("person", event.id, tag)
    p.add("person", id=person)
    p.name(person, person_name(given, surname), "en", "official")
    p.add(
        "candidacy",
        id=key("candidacy", event.id, tag),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=label,
        member_of_party_id=_party_of(ab, known),
        is_independent=ab in NO_PARTY,
    )
    return option


def _house_contest(
    p: Package, event: Event, election: str, path: str, division: str, name: str, state: str, method: str
) -> tuple[str, str]:
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
    return contest, _result_set(p, election, contest, method)


def _accounts(values: Values, result_set: str, unit: str, valid: int, invalid: int | None) -> None:
    values.add(result_set, unit, None, 4, valid)
    if invalid is not None:
        values.add(result_set, unit, None, 5, invalid)
        values.add(result_set, unit, None, 3, valid + invalid)


GAPS: dict[str, int] = {}
OVER: dict[str, dict[str, int]] = {}


def _remainder(
    p: Package,
    values: Values,
    units: Units,
    result_set: str,
    division: str,
    options: dict[str, str],
    ordinary: dict[str, int],
    summed: Counter[str],
    valid: Counter[str],
    bad: dict[str, int],
    pages: str,
) -> None:
    """Ordinary votes the division prints but its polling place pages do not show go on one unit, so totals hold."""
    gaps = {
        label: n - summed[label]
        for label, n in ordinary.items()
        if label not in ("FORMAL", "TOTAL") and summed[label] != n
    }
    if not gaps:
        return
    over = {label: -gap for label, gap in gaps.items() if gap < 0}
    if over:
        OVER[f"{units.day}:{division}"] = over
        gaps = {label: gap for label, gap in gaps.items() if gap > 0}
        listed = ", ".join(f"{label} {n}" for label, n in over.items())
        p.add(
            "note",
            id=key("note", result_set, "over"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The Australian Electoral Commission's {pages} give more ordinary votes than its first "
            f"preferences by vote type table for this division ({listed} more). The polling place figures are "
            "stored as printed, so these totals exceed the table's by those votes.",
            lang="en",
        )
    unit = units.declaration(division, "ORDINARY_OTHER")
    for label, gap in gaps.items():
        if label == "INFORMAL":
            bad[unit] = gap
        else:
            values.add(result_set, unit, options[label], 1, gap)
            valid[unit] += gap
    GAPS[f"{units.day}:{division}"] = sum(gaps.values())
    p.add(
        "note",
        id=key("note", result_set, "remainder"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"The Australian Electoral Commission's {pages} show {sum(gaps.values()):,} fewer ordinary votes "
        "than its first preferences by vote type table for this division, because some rows of the pages are "
        "truncated or the votes were counted outside a listed polling place. The difference is stored on one unit "
        "for the division, so the division's totals match the table.",
        lang="en",
    )


def house_2001(p: Package, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    event = V2001
    divisions, places, _ = pages_2001()
    ids = division_ids()
    for url, place_url in zip(divisions, places, strict=True):
        rows = cells(page(url))
        state, name = _heading_2001(url)
        division = ids[(state, name.lower())]
        contest, result_set = _house_contest(
            p, event, election, path, division, name, state, HTML_METHOD.format(pages="division results pages")
        )
        start = next(i for i, row in enumerate(rows) if row[:2] == ["Candidate", "Party Code"])
        options: dict[str, str] = {}
        printed: dict[str, int] = {}
        for row in rows[start + 1 :]:
            if row[0] in ("FORMAL", "INFORMAL", "TOTAL"):
                printed[row[0]] = _int(row[2])
                if row[0] == "TOTAL":
                    break
                continue
            if row[0].startswith("....."):
                continue
            options[row[0]] = _candidate(
                p, event, contest, f"{division}:{row[0]}", row[0], len(options) + 1, row[1], known
            )
            p.add("declared", result_set_id=result_set, option_id=options[row[0]], measure=1, value=_int(row[2]))
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=printed["FORMAL"])
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=printed["INFORMAL"])
        tcp = next(i for i, row in enumerate(rows) if row and row[0] == "Two Candidate Preferred")
        pair = {row[0]: float(row[2].rstrip("%")) for row in rows[tcp + 1 : tcp + 3] if row[0] in options}
        if len(pair) != 2:
            votes: dict[str, int] = {}
            for row in rows[start + 1 :]:
                if row[0] == "TOTAL":
                    break
                if row[0] in options:
                    votes[row[0]] = _int(row[2])
            leader = max(votes, key=lambda c: votes[c])
            if 2 * votes[leader] <= printed["FORMAL"]:
                raise ValueError(f"2001 {name}: no two-candidate count and no first-preference majority")
            pair = {leader: 1.0}
        winner = max(pair, key=lambda c: pair[c])
        for label, option in options.items():
            kind = "elected" if label == winner else "lost" if label in pair else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        table = next(
            i
            for i, row in enumerate(rows)
            if i > start and len(row) == 6 and row[0] in options and _number(row[1]) is not None
        )
        kinds = ["Ordinary", "Absent", "Postal", "Pre-poll", "Provisional"]
        ordinary: dict[str, int] = {}
        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        for row in rows[table:]:
            if row[0] == "TOTAL":
                break
            for column, number in zip(kinds, row[1:6], strict=True):
                if column == "Ordinary":
                    ordinary[row[0]] = _int(number)
                    continue
                unit = units.declaration(division, TYPES_2001[column])
                if row[0] == "INFORMAL":
                    bad[unit] = _int(number)
                elif row[0] != "FORMAL":
                    values.add(result_set, unit, options[row[0]], 1, _int(number))
                    valid[unit] += _int(number)
        summed: Counter[str] = Counter()
        html = page(place_url)
        first = place_url.rsplit("/", 1)[1].removesuffix(".htm")
        parts = re.split(r'n?ame="(\d+)"', html)
        blocks = [(first, parts[0]), *zip(parts[1::2], parts[2::2], strict=True)]
        links = re.findall(r'href="?(\d+\.htm(?:#\d+)?)"?>([^<]*)', page(url))
        named = {href.split("#")[1]: unescape(text).strip() for href, text in links if "#" in href}
        if links:
            named.setdefault(first, unescape(links[0][1]).strip())
        by_name = {text.lower(): id for id, text in named.items()}
        used: set[str] = set()
        for place, block in blocks:
            unit_rows = [row for row in cells(block) if row and row[0]]
            headers = [i for i, row in enumerate(unit_rows) if row[:2] == ["Candidate", "Votes"]]
            for index, header in enumerate(headers):
                above = unit_rows[header - 1] if header else []
                heading = above[0] if len(above) == 1 and " : " not in above[0] and "Results" not in above[0] else None
                if index == 0:
                    here, label = place, named.get(place) or heading or f"Polling place {place}"
                else:
                    if heading is None:
                        continue  # the page lost both the anchor and the name; the remainder unit holds these votes
                    here, label = by_name.get(heading.lower(), f"{division}:{heading}"), heading
                    if here in used:
                        here = f"{division}:{heading}"
                used.add(here)
                unit = units.place(division, here, label)
                stop = headers[index + 1] if index + 1 < len(headers) else len(unit_rows)
                for row in unit_rows[header + 1 : stop]:
                    if len(row) < 2 or _number(row[1]) is None:
                        continue
                    match = LABEL.match(row[0])
                    assert match is not None
                    text = match.group(1)
                    if text == "INFORMAL":
                        bad[unit] = _int(row[1])
                        summed["INFORMAL"] += _int(row[1])
                    elif text in options:
                        values.add(result_set, unit, options[text], 1, _int(row[1]))
                        valid[unit] += _int(row[1])
                        summed[text] += _int(row[1])
        _remainder(p, values, units, result_set, division, options, ordinary, summed, valid, bad, "polling place pages")
        for unit in {*valid, *bad}:
            _accounts(values, result_set, unit, valid[unit], bad.get(unit))
        enrolled = _enrolled(rows)
        if enrolled is not None:
            values.add(result_set, units.roll(division), None, 2, enrolled)
    return len(divisions)


def senate_2001(p: Package, election: str, path: str, values: Values, known: set[str]) -> int:
    event = V2001
    for url in pages_2001()[2]:
        state = url.rsplit("_", 1)[1].removesuffix(".htm")
        rows = cells(page(url))
        contest = key("contest", event.id, "senate", state)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE,
            district_area_id=area("au.state", state),
            magnitude=2 if state in ("ACT", "NT") else 6,
            round_number=1,
            electoral_system_id=SENATE_GVT,
            default_result_kind="counted",
        )
        p.name(contest, f"Senate, {STATE_NAMES[state]}", "en", "official")
        p.add("slug", path=f"{path}/senate-{state.lower()}", entity_id=contest, is_current=True)
        result_set = _result_set(
            p,
            election,
            contest,
            "First preferences for the whole state or territory, the finest unit the Australian Electoral Commission "
            "publishes for the 2001 Senate election. Votes above the line are stored on one option per group and "
            "votes below the line on each candidate. The source does not mark the senators elected, so no outcomes "
            "are stored.",
        )
        unit = key("unit", f"{event.day}:{state}:SENATE")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRALIA,
            scheme_code="au.polling_place",
            code=f"{event.day}:{state}:SENATE",
            area_relation="within",
            area_id=area("au.state", state),
            kind="aggregate",
        )
        p.name(unit, f"{STATE_NAMES[state]}: all Senate votes", "en", "official")
        start = next(i for i, row in enumerate(rows) if row[:2] == ["Ticket", "Candidates"])
        group = ""
        valid = 0
        printed: dict[str, int] = {}
        for row in rows[start + 1 :]:
            total = row[0].removeprefix("STATE ")
            if total in ("FORMAL", "INFORMAL", "TOTAL"):
                printed[total] = _int(row[1])
                if total == "TOTAL":
                    break
                continue
            if len(row) < 5 or "Group Total" in row[:2] or "Ungrouped Total" in row[:2]:
                continue
            if len(row) == 6:
                group, row = row[0], row[1:]
            label, code, votes = row[0], row[1], _int(row[2])
            tag = f"{state}:{group}:{label}"
            if label == "TICKET VOTES":
                option = key("option", event.id, "senate", tag)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="legend",
                    party_id=_party_of(code, known),
                    ballot_label=f"Group {group} (above the line)",
                )
            else:
                option = _candidate_senate(p, event, contest, tag, label, code, known)
            values.add(result_set, unit, option, 1, votes)
            valid += votes
        if valid != printed["FORMAL"]:
            raise ValueError(f"2001 Senate {state}: options sum to {valid}, the page prints {printed['FORMAL']}")
        _accounts(values, result_set, unit, valid, printed["INFORMAL"])
        enrolled = _enrolled(rows)
        if enrolled is not None:
            values.add(result_set, unit, None, 2, enrolled)
    return 8


def _candidate_senate(p: Package, event: Event, contest: str, tag: str, label: str, code: str, known: set[str]) -> str:
    option = key("option", event.id, "senate", tag)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=_party_of(code, known),
        ballot_label=label,
    )
    surname, _, given = label.partition(", ")
    person = key("person", event.id, "senate", tag)
    p.add("person", id=person)
    p.name(person, person_name(given, surname), "en", "official")
    p.add(
        "candidacy",
        id=key("candidacy", event.id, "senate", tag),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=label,
        member_of_party_id=_party_of(code, known),
        is_independent=code in NO_PARTY,
    )
    return option


def house_werriwa(p: Package, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    event = WERRIWA
    division = "153"
    by_type, _, _, summary = pages_werriwa()
    contest, result_set = _house_contest(
        p, event, election, path, division, "Werriwa", "NSW", HTML_METHOD.format(pages="polling place results pages")
    )
    rows = cells(page(by_type))
    start = next(i for i, row in enumerate(rows) if row[:2] == ["Candidate", "Party"])
    by_name = {name: ab for ab, name in _party_names().items()}
    options: dict[str, str] = {}
    ordinary: dict[str, int] = {}
    valid: Counter[str] = Counter()
    bad: dict[str, int] = {}
    for row in rows[start + 1 :]:
        numbers = [_int(cell) for cell in row[2::2]] if len(row) >= 13 else []
        if len(row) < 13 or row[0].startswith("....."):
            continue
        label = row[0]
        if label == "TOTAL":
            break
        if label == "FORMAL":
            continue
        ordinary[label] = numbers[0]
        for kind, value in zip(("ABSENT", "PROVISIONAL", "PRE_POLL", "POSTAL"), numbers[1:5], strict=True):
            unit = units.declaration(division, kind)
            if label == "INFORMAL":
                bad[unit] = value
            else:
                if label not in options:
                    options[label] = _candidate(
                        p,
                        event,
                        contest,
                        f"{division}:{label}",
                        label,
                        len(options) + 1,
                        by_name.get(row[1], ""),
                        known,
                    )
                values.add(result_set, unit, options[label], 1, value)
                valid[unit] += value
        if label != "INFORMAL":
            p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=numbers[5])
        else:
            p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=numbers[5])
    summed: Counter[str] = Counter()
    for url in werriwa_places():
        place = url.rsplit("-", 1)[1].removesuffix(".htm")
        place_rows = cells(page(url))
        heading = next(cell for row in place_rows for cell in row if cell.startswith("POLLING PLACE - "))
        name = next(
            (
                r[0]
                for r in cells(page(pages_werriwa()[1]))
                if r and r[0].upper() == heading.removeprefix("POLLING PLACE - ").split(" Division")[0].strip()
            ),
            heading.removeprefix("POLLING PLACE - ").split(" Division")[0].title(),
        )
        unit = units.place(division, place, name)
        begin = next(i for i, row in enumerate(place_rows) if row[:3] == ["Candidate", "Party", "Votes"])
        for row in place_rows[begin + 1 :]:
            if row[0] == "TOTAL":
                break
            if row[0].startswith(".....") or row[0] == "FORMAL":
                continue
            value = _int(row[2])
            if row[0] == "INFORMAL":
                bad[unit] = value
                summed["INFORMAL"] += value
            else:
                values.add(result_set, unit, options[row[0]], 1, value)
                valid[unit] += value
                summed[row[0]] += value
    _remainder(p, values, units, result_set, division, options, ordinary, summed, valid, bad, "polling place pages")
    for unit in {*valid, *bad}:
        _accounts(values, result_set, unit, valid[unit], bad.get(unit))
    first = cells(page(summary))
    tcp = next(i for i, row in enumerate(first) if "This Election (%)" in row)
    pair = {row[0]: _int(row[2]) for row in first[tcp + 1 :] if row and row[0] in options}
    winner = max(pair, key=lambda c: pair[c])
    for label, option in options.items():
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if label == winner else "lost" if label in pair else "eliminated",
        )
    enrolled = _enrolled(first)
    if enrolled is not None:
        values.add(result_set, units.roll(division), None, 2, enrolled)
    return 1


def election_html(event: Event) -> Package:
    p = Package(
        f"{SOURCE}/{event.day}",
        SOURCE,
        f"The {event.title}, by polling place, from the Australian Electoral Commission's results pages",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    for url in html_cited(event):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", event.day)
    path = f"au/{event.day}-{'general' if event is V2001 else 'by-election'}"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind=event.kind,
        date_from=event.day,
        date_to=event.day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, event.title, "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = Units(p, event.day)
    values = Values()
    known = _parties()
    if event is V2001:
        house_2001(p, election_id, path, units, values, known)
        senate_2001(p, election_id, path, values, known)
    else:
        house_werriwa(p, election_id, path, units, values, known)
    p.frame("result_value", values.frame())
    over = {k: v for k, v in OVER.items() if k.startswith(str(event.day))}
    if over:
        p.accept(
            "accounting.declared",
            f"in {len(over)} divisions the AEC's 2001 polling place pages give 1 to 8 more ordinary votes for a "
            "candidate than its own first preferences by vote type table; stored as printed, with a caveat note",
        )
    return p


def html_cited(event: Event) -> list[str]:
    if event is V2001:
        divisions, places, senate = pages_2001()
        return [*divisions, *places, *senate]
    return [*pages_werriwa(), *werriwa_places()]
