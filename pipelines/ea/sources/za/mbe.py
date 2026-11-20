"""The IEC's municipal by-elections, read from its results portal's by-election form.

The form lists each municipal term, each by-election date within it, and the types held on that date. Its report
links are tied to the browsing session, so each Excel report is stored under a stable address made of the form's
address and the choices that produce it. A ward by-election has one detailed report per ward, a proportional
by-election (a dissolved council) has a results report and a seat report per municipality.
"""

import functools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from uuid import uuid5

import fastexcel
import httpx

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.za.iec import PROVINCES

SOURCE = "za.mbe"
HOST = "https://results.elections.org.za/home/Downloads/"
FORM = HOST + "MBE-Results"
FIELD = "ctl00$MainContent$"
USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"
REPORTS = {
    "ward": ("DETAILED_REPORT", 1, "WardsDetailedResults", "EXCELDetailedResultsReport"),
    "pr": ("PRELECTION_REPORT", 0, "MunicipalitiesPRElectionsResults", "EXCELPRElectionResult"),
    "seats": ("PRSEATCALCULATION_REPORT", 1, "MunicipalitiesSeatCalculation", "EXCELSeatCalculation"),
}
TYPES = {"WARD": ("ward",), "PR": ("pr", "seats")}
MONTHS = {
    m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)
}


def listing_url() -> str:
    return FORM + "#listing"


def report_url(term: str, event: str, kind: str, item: str) -> str:
    return f"{FORM}#term={term}&event={event}&report={kind}&item={item}"


def parse_day(label: str) -> date:
    found = re.match(r"(\d+) ([A-Z]{3}) (\d{4})", label)
    if found is None:
        raise ValueError(f"unreadable by-election date {label!r}")
    return date(int(found.group(3)), MONTHS[found.group(2)], int(found.group(1)))


@dataclass
class Session:
    client: httpx.Client
    page: str

    @classmethod
    def open(cls) -> Session:
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=180)
        for attempt in range(4):
            try:
                return cls(client, client.get(FORM).text)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
        raise RuntimeError("unreachable")

    def post(self, target: str, values: dict[str, str]) -> None:
        data = {
            name: unescape(value)
            for name, value in re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"', self.page)
        }
        data.update({FIELD + name: value for name, value in values.items()})
        data["__EVENTTARGET"] = FIELD + target
        for attempt in range(4):
            try:
                response = self.client.post(FORM, data=data)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
                continue
            if response.status_code == 200 and "Error 500" not in response.text:
                self.page = response.text
                return
        raise RuntimeError(f"the form refused {target} {values}")

    def options(self, name: str) -> list[tuple[str, str]]:
        start = self.page.find(f"MainContent_ddl{name}")
        if start < 0:
            return []
        end = self.page.find("</select>", start)
        found = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', self.page[start:end])
        return [(value, " ".join(unescape(label).split())) for value, label in found if value not in ("0", "-1", "-2")]

    def link(self, button: str) -> str | None:
        found = re.search(rf'<a href="([^"]*)" id="MainContent_imgbtn{button}[^"]*"', self.page)
        return unescape(found.group(1)) if found else None

    def download(self, button: str) -> bytes:
        link = self.link(button)
        if link is None:
            raise RuntimeError(f"no {button} link")
        for attempt in range(4):
            try:
                response = self.client.get(HOST + link)
            except httpx.HTTPError:  # the portal sometimes drops the connection; the link stays valid
                time.sleep(5 * (attempt + 1))
                continue
            if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/"):
                return response.content
        raise RuntimeError(f"{button} did not download")


def terms() -> list[tuple[str, str]]:
    return Session.open().options("MunicipalElections")


def events(term: str) -> list[tuple[str, str]]:
    session = Session.open()
    session.post("ddlMunicipalElections", {"ddlMunicipalElections": term})
    return session.options("ByelectionDate")


def _event(term: str, event: str) -> dict[str, dict[str, dict[str, str]]]:
    """Store every report of one by-election date; the result lists what the form offers for it."""
    session = Session.open()
    values = {"ddlMunicipalElections": term}
    session.post("ddlMunicipalElections", values)
    values["ddlByelectionDate"] = event
    session.post("ddlByelectionDate", values)
    found: dict[str, dict[str, dict[str, str]]] = {}
    for type_value, type_label in session.options("ByelectionType"):
        values["ddlByelectionType"] = type_value
        session.post("ddlByelectionType", values)
        found[type_label] = {}
        for kind in TYPES.get(type_label, ()):
            radio, number, select, button = REPORTS[kind]
            values["radReports"] = radio
            session.post(f"radReports${number}", values)
            items = session.options(select)
            found[type_label][kind] = dict(items)
            if not items and kind == "seats":
                pdf = session.link("PDFSeatCalculation")
                if pdf is not None and "/" in pdf:  # a fixed address, shared by every municipality of the date
                    raw.fetch(SOURCE, HOST + pdf, suffix=".pdf")
                    found[type_label][kind] = {"pdf": HOST + pdf}
            for item, _ in items:
                url = report_url(term, event, kind, item)
                if url in raw.keys(SOURCE):
                    continue
                values["ddl" + select] = item
                session.post("ddl" + select, values)
                try:
                    content = session.download(button)
                except RuntimeError:
                    if kind == "seats":  # the portal serves no file for some seat reports
                        continue
                    raise
                raw.store(
                    SOURCE,
                    url,
                    content,
                    suffix=".xls",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/vnd.ms-excel",
                )
            values.pop("ddl" + select, None)
    return found


def crawl() -> None:
    jobs = [(term, event, label) for term, _ in terms() for event, label in events(term)]
    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(lambda job: _event(job[0], job[1]), jobs))
    listing = {
        f"{term}/{event}": {"label": label, "types": types}
        for (term, event, label), types in zip(jobs, found, strict=True)
    }
    raw.store(
        SOURCE,
        listing_url(),
        json.dumps(listing, indent=1, sort_keys=True).encode(),
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type="application/json",
    )


def fetch() -> None:
    crawl()


def listing() -> dict[str, dict[str, object]]:
    return json.loads(raw.cached(SOURCE, listing_url()).path.read_bytes())


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def count(text: str | None) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def sheet(url: str) -> list[list[str]]:
    reader = fastexcel.read_excel(raw.cached(SOURCE, url).path.read_bytes())
    frame = reader.load_sheet(0, header_row=None, dtypes="string").to_polars()
    return [[clean(cell) for cell in row] for row in frame.iter_rows()]


@dataclass
class Candidate:
    name: str
    party: str
    votes: dict[str, int]
    total: int | None = None


@dataclass
class Ward:
    province: str
    municipality: str
    ward: str
    registered: int | None
    districts: dict[str, tuple[int | None, int | None, int | None]]
    district_total: tuple[int | None, int | None, int | None]
    candidates: list[Candidate]
    winner: tuple[str, str, int | None] | None


def _cells(row: list[str]) -> list[tuple[int, str]]:
    return [(index, cell) for index, cell in enumerate(row) if cell]


def ward_report(url: str) -> Ward | None:
    """One ward's detailed report, or none when the portal prints no data for it."""
    rows = sheet(url)
    text = " ".join(cell for row in rows for cell in row)
    if "No data available" in text:
        return None
    head: dict[str, str] = {}
    section = ""
    districts: dict[str, tuple[int | None, int | None, int | None]] = {}
    district_total: tuple[int | None, int | None, int | None] = (None, None, None)
    candidates: list[Candidate] = []
    winner: tuple[str, str, int | None] | None = None
    vd_column = 14
    current: Candidate | None = None
    pending: str | None = None
    for row in rows:
        cells = _cells(row)
        if not cells:
            continue
        first = cells[0][1]
        if first.endswith(":") and len(cells) > 1:
            head[first.removesuffix(":")] = cells[1][1]
            continue
        if first in ("Ward Summary", "Candidate Summary", "By-Election Ward Winner"):
            section, current, pending = first, None, None
            continue
        if section == "Ward Summary":
            if first == "Voting District":
                continue
            numbers = [count(cell) for index, cell in cells[1:] if "%" not in cell][:3]
            numbers += [None] * (3 - len(numbers))
            found = (numbers[0], numbers[1], numbers[2])
            if first == "Total":
                district_total = found
            else:
                districts[first] = found
        elif section == "Candidate Summary":
            if first == "Candidate Name":
                vd_column = next((index for index, cell in cells if cell == "Voting District"), vd_column)
                continue
            if cells[0][0] == 0:
                current = Candidate(first, next((cell for index, cell in cells[1:] if index < vd_column), ""), {})
                candidates.append(current)
                cells = [item for item in cells[1:] if item[0] >= vd_column]
            if current is None:
                continue
            for index, cell in cells:
                if index == vd_column:
                    pending = cell
                elif index > vd_column and "%" not in cell and pending is not None:
                    value = count(cell)
                    if value is not None:
                        if pending == "Total":
                            current.total = value
                        else:
                            current.votes[pending] = value
        elif section == "By-Election Ward Winner":
            if first == "Candidate Name":
                continue
            numbers = [count(cell) for index, cell in cells[1:] if "%" not in cell and cell.replace(" ", "").isdigit()]
            party = cells[1][1] if len(cells) > 1 else ""
            winner = (first, party, numbers[0] if numbers else None)
    if not any(votes for candidate in candidates for votes in candidate.votes.values()):  # a report of zeros
        return None
    return Ward(
        head.get("Province", ""),
        head.get("Municipality", ""),
        head.get("Ward", ""),
        count(head.get("Total Registered Voters in Ward")),
        districts,
        district_total,
        candidates,
        winner,
    )


@dataclass
class Council:
    province: str
    municipality: str
    registered: int | None
    wards: dict[str, dict[str, int]]
    totals: dict[str, dict[str, int | None]]
    overall: dict[str, int | None]


def council_report(url: str) -> Council | None:
    """A proportional by-election's results report: votes per party for each ward of the municipality."""
    head: dict[str, str] = {}
    wards: dict[str, dict[str, int]] = {}
    totals: dict[str, dict[str, int | None]] = {}
    overall: dict[str, int | None] = {}
    ward = ""
    for row in sheet(url):
        cells = _cells(row)
        if not cells:
            continue
        first = cells[0][1]
        if first.endswith(":") and len(cells) > 1:
            head[first.removesuffix(":")] = cells[1][1]
        elif first.startswith("Ward ") and len(cells) > 2:
            ward = first.removeprefix("Ward ").strip()
            wards[ward] = {cells[1][1]: count(cells[2][1]) or 0}
            totals[ward] = {}
        elif first in ("Total Valid Votes", "Registered Voters", "Spoilt Votes") and ward:
            if first in totals[ward]:  # the block after the last ward repeats the totals for the whole municipality
                overall[first] = count(cells[1][1])
            else:
                totals[ward][first] = count(cells[1][1])
        elif ward and cells[0][0] > 2 and len(cells) >= 2:
            wards[ward][first] = count(cells[1][1]) or 0
    if not any(votes for items in wards.values() for votes in items.values()):
        return None
    return Council(
        head.get("Province", ""),
        head.get("Municipality", ""),
        count(head.get("Registered Voters")),
        wards,
        totals,
        overall,
    )


SOUTH_AFRICA = str(uuid5(package.NAMESPACE, "polity\x1fZA"))
VINTAGES = {
    "2": date(2000, 12, 5),
    "95": date(2006, 3, 1),
    "197": date(2011, 5, 18),
    "402": date(2016, 8, 3),
    "1091": date(2021, 11, 1),
}
INDEPENDENT = "INDEPENDENT"
SCHEMES = ("za.province", "za.municipality", "za.ward", "za.vd")
PROVINCE_CODES = {name.upper(): code for name, code in PROVINCES.items()} | {"NORTHERN PROVINCE": "LP"}
PROVINCE_NAMES = {name.upper(): name for name in PROVINCES} | {"NORTHERN PROVINCE": "Limpopo"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source")
SYSTEM_WARD = key("electoral_system", "ward")
SYSTEM_COUNCIL = key("electoral_system", "council")
WARD_METHOD = (
    "A ward by-election fills one vacant ward seat of a municipal council. The ward councillor is elected by "
    "plurality in the single-member ward. The Electoral Commission of South Africa (IEC) reports each candidate's "
    "votes for every voting district of the ward, with the ward's valid and spoilt ballots per voting district. "
    "The registered voters are those of the whole ward, so they sit on a ward total that has no votes of its own."
)
COUNCIL_METHOD = (
    "A proportional by-election elects a council that was dissolved. The IEC reports each party's votes for every "
    "ward of the municipality, which is the finest unit its results report gives for this ballot. The registered "
    "voters, valid votes, and spoilt votes are those of the ward."
)


def party_key(text: str) -> str:
    return clean(text).upper()


def party(text: str) -> str:
    return key("party", party_key(text))


def area(scheme: str, *parts: object) -> str:
    return key("area", scheme, *parts)


def title(text: str) -> str:
    text = re.sub(r"\s*\[.*\]\s*$", "", clean(text))
    return text.title() if text.isupper() else text


def split_label(label: str) -> tuple[str, str]:
    code, _, name = clean(label).partition(" - ")
    return code.strip(), title(name or code)


@dataclass(frozen=True)
class Event:
    term: str
    event: str
    day: date
    wards: tuple[str, ...]
    councils: tuple[str, ...]
    pdf: str | None


def events_by_day() -> dict[date, list[Event]]:
    found: dict[date, list[Event]] = {}
    for name, entry in sorted(listing().items()):
        term, event = name.split("/")
        types = entry["types"]
        assert isinstance(types, dict)
        day = parse_day(str(entry["label"]))
        wards = tuple(types.get("WARD", {}).get("ward", {}))
        councils = tuple(types.get("PR", {}).get("pr", {}))
        pdf = types.get("PR", {}).get("seats", {}).get("pdf")
        found.setdefault(day, []).append(Event(term, event, day, wards, councils, pdf))
    return found


def _url(event: Event, kind: str, item: str) -> str:
    return report_url(event.term, event.event, kind, item)


@functools.cache
def ward_data(term: str, event: str, item: str) -> Ward | None:
    return ward_report(report_url(term, event, "ward", item))


@functools.cache
def council_data(term: str, event: str, item: str) -> Council | None:
    return council_report(report_url(term, event, "pr", item))


def reports(day: date) -> tuple[list[tuple[Event, Ward]], list[tuple[Event, Council]]]:
    """Every ward and council report with data for one date."""
    wards: list[tuple[Event, Ward]] = []
    councils: list[tuple[Event, Council]] = []
    for event in events_by_day()[day]:
        for item in event.wards:
            found = ward_data(event.term, event.event, item)
            if found is not None:
                wards.append((event, found))
        for item in event.councils:
            found_council = council_data(event.term, event.event, item)
            if found_council is not None:
                councils.append((event, found_council))
    return wards, councils


def days() -> list[date]:
    """The dates whose reports carry results."""
    return [day for day in sorted(events_by_day()) if any(reports(day))]


def vintage(term: str) -> date:
    return VINTAGES[term]


def until(since: date) -> date | None:
    later = [v for v in VINTAGES.values() if v > since]
    return min(later) if later else None


def cited(event: Event) -> list[str]:
    marker = f"term={event.term}&event={event.event}&"
    found = sorted(url for url in raw.keys(SOURCE) if marker in url)
    return [*found, event.pdf] if event.pdf else found


def uses(p: Package, urls: list[str]) -> None:
    for url in urls:
        p.uses(raw.cached(SOURCE, url))


@functools.cache
def _all() -> tuple[dict[tuple[date, str], tuple[str, str]], dict[tuple[date, str], str], set[str]]:
    municipalities: dict[tuple[date, str], tuple[str, str]] = {}
    wards: dict[tuple[date, str], str] = {}
    parties: set[str] = set()
    for day in days():
        found_wards, found_councils = reports(day)
        for event, ward in found_wards:
            since = vintage(event.term)
            code, name = split_label(ward.municipality)
            municipalities.setdefault((since, code), (ward.province.upper(), name))
            wards[(since, ward.ward)] = code
            parties.update(c.party for c in ward.candidates)
        for event, council in found_councils:
            since = vintage(event.term)
            code, name = split_label(council.municipality)
            municipalities.setdefault((since, code), (council.province.upper(), name))
            for ward_code, votes in council.wards.items():
                wards[(since, ward_code)] = code
                parties.update(votes)
    return municipalities, wards, parties


def packages() -> list[str]:
    return ["za.mbe/geo", "za.mbe/registry", *(f"za.mbe/{day}" for day in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "za.mbe/geo",
        SOURCE,
        "The provinces, municipalities, and wards that South Africa's municipal by-elections used, as the IEC's "
        "reports name them, by the demarcation in force",
    )
    p.external.add(SOUTH_AFRICA)
    p.external.update(SCHEMES)
    municipalities, wards, _ = _all()
    for code in sorted({PROVINCE_CODES[province] for province, _ in municipalities.values()}):
        id = area("za.province", code)
        p.add("area", id=id, scheme_code="za.province")
        p.add("area_code", area_id=id, code=code)
        p.name(id, next(name for name, short in PROVINCES.items() if short == code), "en", "official")
    for (since, code), (province, name) in sorted(municipalities.items()):
        id = area("za.municipality", since, code)
        end = until(since)
        p.add("area", id=id, scheme_code="za.municipality")
        p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=end)
        p.name(id, name, "en", "official")
        p.add(
            "area_parent",
            area_id=id,
            parent_id=area("za.province", PROVINCE_CODES[province]),
            valid_from=since,
            valid_to=end,
        )
    for (since, ward), code in sorted(wards.items()):
        id = area("za.ward", since, ward)
        end = until(since)
        p.add("area", id=id, scheme_code="za.ward")
        p.add("area_code", area_id=id, code=ward, valid_from=since, valid_to=end)
        p.name(id, f"Ward {ward}", "en", "official")
        p.add("area_parent", area_id=id, parent_id=area("za.municipality", since, code), valid_from=since, valid_to=end)
    return p


def registry() -> Package:
    p = Package(
        "za.mbe/registry",
        SOURCE,
        "The parties, electoral systems, and report files of South Africa's municipal by-elections",
    )
    p.external.add(SOUTH_AFRICA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Electoral Commission of South Africa",
        title="IEC municipal by-election results: detailed ward reports and proportional results reports",
        url=FORM,
        license="IEC copyright notice: results may be reproduced with the IEC acknowledged as the source",
        license_url="https://www.elections.org.za/pw/Copyright-Notice",
        attribution="Electoral Commission of South Africa (IEC)",
    )
    for since, rules, method in (
        (
            SYSTEM_WARD,
            {"formula": "fptp"},
            "Each ward councillor is elected by plurality in a single-member ward, including at a by-election.",
        ),
        (
            SYSTEM_COUNCIL,
            {"formula": "droop"},
            (
                "The proportional ballot of a council allocates the whole council by the Droop quota on the party "
                "votes, then deducts each party's ward seats."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=since,
            polity_id=SOUTH_AFRICA,
            valid_from=date(2000, 12, 5),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", since), entity_id=since, kind="methodology", body_md=method, lang="en")
    _, _, parties = _all()
    names: dict[str, str] = {}
    for text in sorted(parties):
        names.setdefault(party_key(text), clean(text))
    names.pop(INDEPENDENT, None)
    for name_key, text in sorted(names.items()):
        id = party(text)
        p.add("party", id=id, kind="party", polity_id=SOUTH_AFRICA)
        p.add("external_id", entity_id=id, scheme="za.mbe.party", value=name_key)
        p.name(id, clean(text), "en", "official")
    stored: set[str] = set()
    for url in sorted(raw.keys(SOURCE)):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in stored:
            stored.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
        else:
            p.uses(artifact)
    return p


def _set(p: Package, contest: str, election: str, kind: str, name: str, path: str, **fields: object) -> str:
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind=kind,
        round_number=1,
        default_result_kind="counted",
        **fields,
    )
    p.name(contest, name, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    return result_set


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
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


def ward_unit(
    p: Package, seen: set[str], day: date, since: date, ward: str, kind: str, by_election_wards: set[str]
) -> str:
    """The one reporting unit of a ward on a by-election date, shared by its ward and proportional contests.

    A ward that holds a ward by-election and also votes in its council's proportional by-election on the same date
    is one place where votes are counted, so both contests report on the same unit; the unit's scheme and code may
    occur once for its validity.
    """
    # A ward with a ward by-election keeps the id its ward total was first loaded under.
    unit = key("unit", day, "ward", ward, "total") if ward in by_election_wards else key("unit", day, "ward", ward)
    if unit not in seen:
        seen.add(unit)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SOUTH_AFRICA,
            scheme_code="za.ward",
            code=f"{day}:{ward}",
            area_relation="equals",
            area_id=area("za.ward", since, ward),
            kind=kind,
        )
    return unit


def election(day: date) -> Package:
    p = Package(
        f"za.mbe/{day}",
        SOURCE,
        f"South Africa's municipal by-elections of {day}, by voting district for ward by-elections and by ward for "
        "proportional by-elections, from the IEC",
        depends=["za.mbe/geo", "za.mbe/registry"],
    )
    p.external.add(SOUTH_AFRICA)
    p.external.update(SCHEMES)
    for event in events_by_day()[day]:
        uses(p, cited(event))
    id = key("election", day)
    path = f"za/{day}-municipal-by-election"
    p.add("election", id=id, polity_id=SOUTH_AFRICA, kind="by_election", date_from=day, date_to=day, status="held")
    p.name(id, f"{day} South African municipal by-elections", "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)
    wards, councils = reports(day)
    ward_codes = {ward.ward for _, ward in wards}
    council_wards = {ward_code for _, council in councils for ward_code in council.wards}
    seen: set[str] = set()
    off = 0
    for event, ward in sorted(wards, key=lambda item: item[1].ward):
        since = vintage(event.term)
        code, name = split_label(ward.municipality)
        number = int(ward.ward[-3:])
        contest = key("contest", day, "ward", ward.ward)
        if contest in seen:
            raise ValueError(f"{day}: ward {ward.ward} is reported twice")
        seen.add(contest)
        result_set = _set(
            p,
            contest,
            id,
            "body_seats",
            f"{name} Ward {number} by-election",
            f"{path}/ward-{ward.ward}",
            district_area_id=area("za.ward", since, ward.ward),
            magnitude=1,
            electoral_system_id=SYSTEM_WARD,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=WARD_METHOD,
            lang="en",
        )
        options: dict[tuple[str, str], str] = {}
        for candidate in ward.candidates:
            label = party_key(candidate.party)
            option = options[(label, candidate.name)] = key("option", contest, label, candidate.name)
            independent = label == INDEPENDENT or not label
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent else party(candidate.party),
                ballot_position=None,
                ballot_label=clean(candidate.name),
            )
        total = ward_unit(
            p, seen, day, since, ward.ward, "standard" if ward.ward in council_wards else "aggregate", ward_codes
        )
        if ward.registered:
            _value(p, result_set, total, None, 2, ward.registered)
        for vd, (valid, spoilt, cast) in sorted(ward.districts.items()):
            unit = key("unit", day, vd)
            if unit not in seen:
                seen.add(unit)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=SOUTH_AFRICA,
                    scheme_code="za.vd",
                    code=f"{day}:{vd}",
                    parent_id=total,
                    area_relation="within",
                    area_id=area("za.ward", since, ward.ward),
                    kind="standard",
                )
            if valid is not None and valid != sum(c.votes.get(vd, 0) for c in ward.candidates):
                off += 1
            _value(p, result_set, unit, None, 4, valid)
            _value(p, result_set, unit, None, 5, spoilt)
            _value(p, result_set, unit, None, 3, cast)
            for candidate in ward.candidates:
                if vd in candidate.votes:
                    _value(
                        p,
                        result_set,
                        unit,
                        options[(party_key(candidate.party), candidate.name)],
                        1,
                        candidate.votes[vd],
                    )
        if ward.winner is not None:
            name_winner, party_winner, _ = ward.winner
            found = options.get((party_key(party_winner), name_winner))
            if found is not None:
                p.add("outcome", result_set_id=result_set, option_id=found, kind="elected", seats=1)
    if off:
        p.accept(
            "accounting.votes_valid",
            f"in {off} voting districts the IEC's ward summary prints a valid-vote count that differs from the sum of "
            "the candidate votes its candidate summary lists for that voting district; both are stored as printed",
        )
    by_municipality: dict[str, list[tuple[Event, Council]]] = {}
    for event, council in councils:
        by_municipality.setdefault(split_label(council.municipality)[0], []).append((event, council))
    for code, items in sorted(by_municipality.items()):
        (event, council), *rest = items
        if rest:
            raise ValueError(f"{day}: council {code} is reported twice")
        since = vintage(event.term)
        name = split_label(council.municipality)[1]
        contest = key("contest", day, "council", code)
        result_set = _set(
            p,
            contest,
            id,
            "list_allocation",
            f"{name} council by-election, proportional ballot",
            f"{path}/council-{changeset.slug(code)}",
            district_area_id=area("za.municipality", since, code),
            magnitude=None,
            electoral_system_id=SYSTEM_COUNCIL,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        options = {}
        for ward_code, votes in sorted(council.wards.items()):
            unit = ward_unit(p, seen, day, since, ward_code, "standard", ward_codes)
            figures = council.totals.get(ward_code, {})
            _value(p, result_set, unit, None, 2, figures.get("Registered Voters"))
            _value(p, result_set, unit, None, 4, figures.get("Total Valid Votes"))
            _value(p, result_set, unit, None, 5, figures.get("Spoilt Votes"))
            for label, number_votes in sorted(votes.items()):
                name_key = party_key(label)
                if (name_key, "") not in options:
                    option = options[(name_key, "")] = key("option", contest, name_key)
                    independent = name_key == INDEPENDENT
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="independents" if independent else "list",
                        party_id=None if independent else party(label),
                        ballot_position=None,
                        ballot_label=clean(label),
                    )
                _value(p, result_set, unit, options[(name_key, "")], 1, number_votes)
    return p
