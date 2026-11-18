"""The IEC's municipal elections of 2000 and 2006, read from its results portal.

The portal publishes no bulk file for these two elections the pipeline can reach, but its Detailed Results report
writes one Excel file per ward ballot and ward, one per municipality for the proportional ballot, and one per ward
for the district ballot. The portal's Municipal results form lists them, so a crawl of the form gives the file list.
"""

import functools
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from urllib.parse import quote

import fastexcel
import httpx
import polars as pl

from ea import raw

SOURCE = "za.iec"
FORM = "https://results.elections.org.za/home/Downloads/ME-Results"
REPORTS = "https://results.elections.org.za/home/LGEPublicReports"
FIELD = "ctl00$MainContent$"
USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"
EVENTS = {2000: (2, date(2000, 12, 5)), 2006: (95, date(2006, 3, 1))}
BALLOT_FILES = {"Ward": "Ward", "PR": "PR", "DC 40%": "DC40"}
DETAILED = 9


def listing_url(year: int) -> str:
    return f"{FORM}#event={EVENTS[year][0]}"


def report_url(event: int, *parts: str) -> str:
    return f"{REPORTS}/{event}/Detailed%20Results/" + "/".join(quote(part) for part in parts)


@dataclass
class Session:
    client: httpx.Client
    page: str

    @classmethod
    def open(cls) -> Session:
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=180)
        return cls(client, client.get(FORM).text)

    def post(self, target: str, values: dict[str, str]) -> str:
        data = {
            name: unescape(value)
            for name, value in re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"', self.page)
        }
        data.update({FIELD + name: value for name, value in values.items()})
        data["__EVENTTARGET"] = FIELD + target
        for attempt in range(2):
            response = self.client.post(FORM, data=data)
            if response.status_code == 200 and "Error 500" not in response.text:
                self.page = response.text
                return self.page
            if attempt == 1:
                raise RuntimeError(f"the form refused {target} {values}")
        raise RuntimeError("unreachable")

    def options(self, name: str) -> list[tuple[str, str]]:
        start = self.page.find(f"MainContent_ddl{name}")
        if start < 0:
            return []
        end = self.page.find("</select>", start)
        found = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', self.page[start:end])
        return [(value, unescape(label)) for value, label in found if value not in ("0", "-1", "-2")]

    def link(self) -> str | None:
        found = re.search(r'<a href="([^"]*)" id="MainContent_hypViewReportExcel"', self.page)
        return unescape(found.group(1)) if found else None


def _capture(session: Session, values: dict[str, str], wards: list[str]) -> str | None:
    """The report link of the first ward, which shows where the province's files sit."""
    if not wards:
        return None
    values["ddlWardsDetailedResults"] = wards[0]
    try:
        session.post("ddlWardsDetailedResults", values)
        for district in ("-1", *(value for value, _ in session.options("VDDetailedResults")[:1])):
            values["ddlVDDetailedResults"] = district
            try:
                session.post("ddlVDDetailedResults", values)
            except RuntimeError:
                continue
            if session.link():
                break
    finally:
        values.pop("ddlVDDetailedResults", None)
        values.pop("ddlWardsDetailedResults", None)
    return session.link()


def _province(year: int, province: str) -> list[dict[str, object]]:
    event = str(EVENTS[year][0])
    session = Session.open()
    values = {"ddlElections": event}
    session.post("ddlElections", values)
    values["radReports"] = str(DETAILED)
    session.post("radReports$0", values)
    values["ddlProvinceDetailedResults"] = province
    session.post("ddlProvinceDetailedResults", values)
    found: list[dict[str, object]] = []
    for municipality, label in session.options("MunicipalityDetailedResults"):
        values["ddlMunicipalityDetailedResults"] = municipality
        session.post("ddlMunicipalityDetailedResults", values)
        record: dict[str, object] = {"municipality": municipality, "label": label, "ballots": {}}
        ballots = record["ballots"]
        assert isinstance(ballots, dict)
        for ballot, ballot_label in session.options("BallotTypeDetailedResults"):
            values["ddlBallotTypeDetailedResults"] = ballot
            session.post("ddlBallotTypeDetailedResults", values)
            entry: dict[str, object] = {"label": ballot_label}
            councils = session.options("LocalCouncilDetailedResults")
            if councils:
                entry["councils"] = {}
                for council, council_label in councils:
                    values["ddlLocalCouncilDetailedResults"] = council
                    session.post("ddlLocalCouncilDetailedResults", values)
                    wards = [value for value, _ in session.options("WardsDetailedResults")]
                    entry["councils"][council] = {  # type: ignore[index]
                        "label": council_label,
                        "wards": wards,
                        "vds": [value for value, _ in session.options("VDDetailedResults")],
                        "link": _capture(session, values, wards) if council == councils[0][0] else None,
                    }
                values.pop("ddlLocalCouncilDetailedResults", None)
            else:
                entry["wards"] = [value for value, _ in session.options("WardsDetailedResults")]
                entry["link"] = _capture(session, values, entry["wards"])  # type: ignore[arg-type]
            ballots[ballot_label] = entry
        values.pop("ddlBallotTypeDetailedResults", None)
        found.append(record)
    return found


def crawl(year: int) -> None:
    provinces = [str(number) for number in range(1, 10)]
    with ThreadPoolExecutor(9) as pool:
        results = list(pool.map(lambda province: _province(year, province), provinces))
    listing = {province: found for province, found in zip(provinces, results, strict=True)}
    raw.store(
        SOURCE,
        listing_url(year),
        json.dumps(listing, indent=1, sort_keys=True).encode(),
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type="application/json",
    )


PROVINCE_DIRS = {"1": "EC", "2": "FS", "3": "GP", "4": "KN", "5": "MP", "6": "NC", "7": "NP", "8": "NW", "9": "WP"}
PROVINCE_NAMES = {
    "1": "EC",
    "2": "FS",
    "3": "GP",
    "4": "KZN",
    "5": "MP",
    "6": "NC",
    "7": "LP",
    "8": "NW",
    "9": "WC",
}
TOTALS = {
    "Total Valid Votes": "valid",
    "Spoilt Votes": "spoilt",
    "Registered Voters (where results were expected)": "registered",
    "Percentage Poll": "poll",
}


def listing(year: int) -> dict[str, list[dict[str, object]]]:
    return json.loads(raw.cached(SOURCE, listing_url(year)).path.read_bytes())


def split_label(label: str) -> tuple[str, str]:
    code, _, name = label.partition(" - ")
    if not name:
        code, _, rest = label.partition(" [")
        return code.strip(), rest.rstrip("]").strip() or code.strip()
    return code.strip(), re.sub(r"\s*\[.*\]\s*$", "", name).strip()


@dataclass(frozen=True)
class Source:
    """One report file and what it covers."""

    year: int
    province: str
    ballot: str
    municipality: str
    path: tuple[str, ...]
    scope: str
    council: str | None = None
    dma: bool = False

    @property
    def url(self) -> str:
        return report_url(EVENTS[self.year][0], PROVINCE_DIRS[self.province], *self.path)


def municipality_directories(year: int) -> dict[str, str]:
    """The folder each municipality's files sit in, as the portal's own links name it."""
    found: dict[str, str] = {}
    for province, records in listing(year).items():
        for record in records:
            ballots = record["ballots"]
            assert isinstance(ballots, dict)
            for entry in ballots.values():
                links = [entry.get("link")] + [c.get("link") for c in entry.get("councils", {}).values()]
                for link in links:
                    if link:
                        parts = link.split("/Detailed Results/")[1].split("/")
                        assert parts[0] == PROVINCE_DIRS[province], link
                        found.setdefault(str(record["municipality"]), parts[1])
                        if "councils" in entry:
                            for council, details in entry["councils"].items():
                                if details.get("link"):
                                    found.setdefault(
                                        council, details["link"].split("/Detailed Results/")[1].split("/")[1]
                                    )
    return found


def sources(year: int) -> list[Source]:
    """Every report file the portal's listing implies, by the folder layout its links show."""
    out: list[Source] = []
    folders = municipality_directories(year)
    for province, records in listing(year).items():
        for record in records:
            municipality = str(record["municipality"])
            ballots = record["ballots"]
            assert isinstance(ballots, dict)
            for label, entry in ballots.items():
                if label == "DC 40%":
                    for council, details in entry["councils"].items():
                        folder = folders.get(council) or split_label(details["label"])[0]
                        for ward in details["wards"]:
                            out.append(
                                Source(
                                    year, province, "DC40", municipality, (folder, f"{ward}_DC40.xls"), ward, council
                                )
                            )
                        if not details["wards"] and year == 2006:
                            out.append(
                                Source(
                                    year, province, "DC40", municipality, (f"{folder}_DC40.xls",), folder, council, True
                                )
                            )
                        for vd in details.get("vds", []) if not details["wards"] and year == 2000 else []:
                            out.append(
                                Source(
                                    year, province, "DC40", municipality, (folder, f"{vd}_DC40.xls"), vd, council, True
                                )
                            )
                    continue
                folder = folders.get(municipality) or split_label(str(record["label"]))[0]
                kind = BALLOT_FILES[label]
                if kind == "PR":
                    out.append(Source(year, province, "PRTOTAL", municipality, (f"{folder}_PR.xls",), "all"))
                for ward in entry["wards"]:
                    out.append(Source(year, province, kind, municipality, (folder, f"{ward}_{kind}.xls"), ward))
    return out


def _walk(source: Source) -> list[str]:
    """The files of a ward that the portal serves per voting district, found through the form."""
    event = str(EVENTS[source.year][0])
    session = Session.open()
    values = {"ddlElections": event}
    session.post("ddlElections", values)
    values["radReports"] = str(DETAILED)
    session.post("radReports$0", values)
    values["ddlProvinceDetailedResults"] = source.province
    session.post("ddlProvinceDetailedResults", values)
    values["ddlMunicipalityDetailedResults"] = source.council or source.municipality
    if source.ballot == "DC40":
        values["ddlMunicipalityDetailedResults"] = source.municipality
        session.post("ddlMunicipalityDetailedResults", values)
        choice = next(value for value, label in session.options("BallotTypeDetailedResults") if label == "DC 40%")
        values["ddlBallotTypeDetailedResults"] = choice
        session.post("ddlBallotTypeDetailedResults", values)
        values["ddlLocalCouncilDetailedResults"] = source.council or ""
        session.post("ddlLocalCouncilDetailedResults", values)
    else:
        session.post("ddlMunicipalityDetailedResults", values)
        ballot = {"Ward": "Ward", "PR": "PR"}[source.ballot]
        choice = next(value for value, label in session.options("BallotTypeDetailedResults") if label == ballot)
        values["ddlBallotTypeDetailedResults"] = choice
        session.post("ddlBallotTypeDetailedResults", values)
    if source.dma:
        values["ddlVDDetailedResults"] = source.scope
        session.post("ddlVDDetailedResults", values)
        link = session.link()
        return [link.replace(" ", "%20")] if link else []
    values["ddlWardsDetailedResults"] = source.scope
    session.post("ddlWardsDetailedResults", values)
    links = []
    for vd, _ in session.options("VDDetailedResults"):
        values["ddlVDDetailedResults"] = vd
        session.post("ddlVDDetailedResults", values)
        link = session.link()
        if link:
            links.append(link.replace(" ", "%20"))
    return links


def fetch(year: int) -> None:
    if listing_url(year) not in raw.keys(SOURCE):
        crawl(year)
    planned = sources(year)
    got = raw.fetch_many(SOURCE, [source.url for source in planned], suffix=".xls", workers=16)
    found: dict[str, list[str]] = {}
    missing: list[Source] = []
    for source in planned:
        if source.url in got:
            found[source.url] = [source.url]
        elif source.ballot == "PRTOTAL":
            raise LookupError(f"{source.url} is not on the portal")
        else:
            missing.append(source)
    with ThreadPoolExecutor(8) as pool:
        for source, links in zip(missing, pool.map(_walk, missing), strict=True):
            if not links:
                raise LookupError(f"the portal has no file for {source}")
            raw.fetch_many(SOURCE, links, suffix=".xls", workers=4)
            found[source.url] = links
    raw.store(
        SOURCE,
        f"{listing_url(year)}&files",
        json.dumps(found, indent=1, sort_keys=True).encode(),
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type="application/json",
    )


def files(year: int) -> dict[str, list[str]]:
    return json.loads(raw.cached(SOURCE, f"{listing_url(year)}&files").path.read_bytes())


@dataclass
class Report:
    """What one file prints: party or candidate lines per place, and the totals."""

    blocks: dict[str, list[tuple[str, str | None, int]]]
    totals: dict[str | None, dict[str, str]]


def _cell(value: object) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def read(path: str | bytes) -> Report:
    reader = fastexcel.read_excel(path)
    frame = reader.load_sheet(0, header_row=None, dtypes="string").to_polars()
    blocks: dict[str, list[tuple[str, str | None, int]]] = {}
    totals: dict[str | None, dict[str, str]] = {}
    ward = False
    place: str | None = None
    started = False
    for row in frame.iter_rows():
        cells = [_cell(value) for value in row]
        cells = [value for value in cells if value]
        if not cells:
            continue
        if cells[0] in ("Voting District", "Ward"):
            started = True
            ward = "Candidate Name" in cells
            continue
        if not started:
            continue
        if cells[0] in TOTALS:
            totals.setdefault(place, {})[TOTALS[cells[0]]] = cells[-1]
            continue
        if re.fullmatch(r"\d{6,9}", cells[0]) and len(cells) > 2:
            place = cells[0]
            cells = cells[1:]
        if place is None or len(cells) < 3:
            raise ValueError(f"unreadable row {cells}")
        votes = cells[-2]
        if len(cells) >= (4 if ward else 3) and re.fullmatch(r"\d+", votes):
            if ward:
                blocks.setdefault(place, []).append((cells[1], cells[0], int(votes)))
            else:
                blocks.setdefault(place, []).append((cells[0], None, int(votes)))
        else:
            raise ValueError(f"unreadable row {cells}")
    return Report(blocks, totals)


@dataclass
class Early:
    """One election's votes by voting district, with what the files print about each voting district."""

    votes: pl.DataFrame
    stats: pl.DataFrame
    wards: pl.DataFrame
    municipal: pl.DataFrame


def _number(text: str | None) -> int | None:
    return int(text) if text is not None and re.fullmatch(r"\d+", text) else None


@functools.cache
def table(year: int) -> Early:
    """Every file of an election, read into four tables.

    `votes` has a row per voting district, ballot, and party (and candidate on a ward ballot). `stats` has the
    registered voters and spoilt ballots of each voting district the files print them for. `wards` has the totals a
    file prints for a whole ward, which the proportional and district ballots print when a ward has more than one
    voting district. `municipal` has the party votes the IEC's municipality-wide proportional report prints per ward.
    """
    votes: list[dict[str, object]] = []
    stats: list[dict[str, object]] = []
    wards: list[dict[str, object]] = []
    municipal: list[dict[str, object]] = []
    found = files(year)
    known = structure(year)[1]
    for source in sources(year):
        for link in found[source.url]:
            report = read(raw.cached(SOURCE, link).path.read_bytes())
            base = {
                "year": year,
                "province": source.province,
                "ballot": source.ballot,
                "municipality": source.municipality,
                "council": source.council,
            }
            if source.ballot == "PRTOTAL":
                for ward, lines in report.blocks.items():
                    for party, _, count in lines:
                        municipal.append({**base, "ward": ward, "party": party, "votes": count})
                        if ward not in known:  # a municipality without wards: the report lists voting districts
                            votes.append(
                                {
                                    **base,
                                    "ballot": "PR",
                                    "ward": None,
                                    "vd": ward,
                                    "party": party,
                                    "candidate": None,
                                    "votes": count,
                                }
                            )
                for figures in report.totals.values():
                    municipal.append(
                        {
                            **base,
                            "ward": None,
                            "party": None,
                            "votes": _number(figures.get("valid")),
                            "spoilt": _number(figures.get("spoilt")),
                            "registered": _number(figures.get("registered")),
                        }
                    )
                continue
            vds = list(report.blocks)
            per_vd = source.ballot == "Ward" or len(vds) == 1
            ward = None if source.dma else source.scope
            for vd, lines in report.blocks.items():
                for party, candidate, count in lines:
                    votes.append(
                        {**base, "ward": ward, "vd": vd, "party": party, "candidate": candidate, "votes": count}
                    )
            if per_vd:
                for vd, figures in report.totals.items():
                    stats.append(
                        {
                            **base,
                            "ward": ward,
                            "vd": vd,
                            "valid": _number(figures.get("valid")),
                            "spoilt": _number(figures.get("spoilt")),
                            "registered": _number(figures.get("registered")),
                        }
                    )
            else:
                for figures in report.totals.values():
                    wards.append(
                        {
                            **base,
                            "ward": ward,
                            "valid": _number(figures.get("valid")),
                            "spoilt": _number(figures.get("spoilt")),
                            "registered": _number(figures.get("registered")),
                        }
                    )
    return Early(
        pl.DataFrame(votes, infer_schema_length=None),
        pl.DataFrame(stats, infer_schema_length=None),
        pl.DataFrame(wards, infer_schema_length=None),
        pl.DataFrame(municipal, infer_schema_length=None),
    )


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    province: str
    kind: str  # metro, local, dma, district
    parent: str | None = None


def _title(name: str) -> str:
    return name.title() if name.isupper() else name


@functools.cache
def structure(year: int) -> tuple[list[Place], dict[str, str]]:
    """The municipalities, district municipalities, and district management areas of an election, and each ward's
    municipality."""
    folders = municipality_directories(year)
    places: dict[str, Place] = {}
    wards: dict[str, str] = {}
    parents: dict[str, str] = {}
    records = [(province, record) for province, found in listing(year).items() for record in found]
    named: dict[str, tuple[str, str]] = {}
    for _, record in records:
        label = str(record["label"])
        code, name = split_label(label)
        key = str(record["municipality"])
        named[key] = (code if key.endswith("~3") else folders.get(key) or code, name)
    for province, record in records:
        key = str(record["municipality"])
        code, name = named[key]
        ballots = record["ballots"]
        assert isinstance(ballots, dict)
        if key.endswith("~3"):
            places[code] = Place(code, _title(name), PROVINCE_NAMES[province], "district")
            for council, details in ballots["DC 40%"]["councils"].items():
                if council.endswith("~4"):
                    dma_code, dma_name = split_label(details["label"])
                    places[dma_code] = Place(dma_code, _title(dma_name), PROVINCE_NAMES[province], "dma", code)
                else:
                    parents[council] = code
            continue
        kind = "metro" if key.endswith("~1") else "local"
        places[code] = Place(code, _title(name), PROVINCE_NAMES[province], kind)
        for entry in ballots.values():
            for ward in entry.get("wards", []):
                wards[ward] = code
    resolved = {
        code: Place(place.code, place.name, place.province, place.kind, parents.get(key))
        for key, (code, _) in named.items()
        if (place := places.get(code)) is not None and place.kind == "local"
    }
    return [resolved.get(place.code, place) for place in places.values()], wards


@functools.cache
def codes(year: int) -> dict[str, str]:
    """The municipality code of each id the portal's form uses for municipalities, districts, and councils."""
    folders = municipality_directories(year)
    found: dict[str, str] = {}
    for _, record in ((province, record) for province, records in listing(year).items() for record in records):
        key = str(record["municipality"])
        code = split_label(str(record["label"]))[0]
        found[key] = code if key.endswith("~3") else folders.get(key) or code
        ballots = record["ballots"]
        assert isinstance(ballots, dict)
        for entry in ballots.values():
            for council, details in entry.get("councils", {}).items():
                found.setdefault(council, folders.get(council) or split_label(details["label"])[0])
    return found


def cited(year: int) -> list[str]:
    """Every raw file an election's package cites: the portal's listing, the file list, and the reports."""
    links = {link for found in files(year).values() for link in found}
    return [listing_url(year), f"{listing_url(year)}&files", *sorted(links)]
