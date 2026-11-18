import csv
import functools
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "za.iec"
HOST = "https://results.elections.org.za/home"
SOUTH_AFRICA = str(uuid5(package.NAMESPACE, "polity\x1fZA"))
FILES = ("EC", "FS", "GP", "KN", "NP", "MP", "NW", "NC", "WP")
PROVINCES = {
    "Eastern Cape": "EC",
    "Free State": "FS",
    "Gauteng": "GP",
    "KwaZulu-Natal": "KZN",
    "Limpopo": "LP",
    "Mpumalanga": "MP",
    "North West": "NW",
    "Northern Cape": "NC",
    "Western Cape": "WC",
}
ABROAD = "Out of Country"
INDEPENDENT = "INDEPENDENT"
METROS = {"BUF", "NMA", "MAN", "JHB", "TSH", "EKU", "ETH", "CPT"}


@dataclass(frozen=True)
class Municipal:
    event: int
    day: date


@dataclass(frozen=True)
class Ballot:
    kind: str
    path: str
    totals: str
    seats: dict[str, int] | None


@dataclass(frozen=True)
class National:
    day: date
    ballots: tuple[Ballot, ...]
    vintage: date


MUNICIPAL = {
    "2011": Municipal(197, date(2011, 5, 18)),
    "2016": Municipal(402, date(2016, 8, 3)),
    "2021": Municipal(1091, date(2021, 11, 1)),
    "2026": Municipal(1887, date(2026, 11, 4)),
}
VINTAGES = sorted(item.day for item in MUNICIPAL.values())
NATIONAL = {
    "2014": National(
        date(2014, 5, 7),
        (
            Ballot(
                "national",
                "NPEPublicReports/291/Downloadable%20Results/National.csv",
                "NPEPublicReports/291/Results%20Report/National.xls",
                None,
            ),
            Ballot(
                "provincial",
                "NPEPublicReports/292/Downloadable%20Results/National.csv",
                "NPEPublicReports/292/Results%20Report/National.xls",
                {"EC": 63, "FS": 30, "GP": 73, "KZN": 80, "LP": 49, "MP": 30, "NW": 33, "NC": 30, "WC": 42},
            ),
        ),
        date(2011, 5, 18),
    ),
    "2019": National(
        date(2019, 5, 8),
        (
            Ballot(
                "national",
                "NPEPublicReports/699/Downloadable%20Results/National.zip",
                "NPEPublicReports/699/Results%20Report/National.xls",
                None,
            ),
            Ballot(
                "provincial",
                "NPEPublicReports/827/Downloadable%20Results/Provincial.zip",
                "NPEPublicReports/827/Results%20Report/National.xls",
                {"EC": 63, "FS": 30, "GP": 73, "KZN": 80, "LP": 49, "MP": 30, "NW": 33, "NC": 30, "WC": 42},
            ),
        ),
        date(2016, 8, 3),
    ),
    "2024": National(
        date(2024, 5, 29),
        (
            Ballot(
                "national",
                "NPEPublicReports/1334/National%20Ballot/Downloadable%20Results/National.zip",
                "NPEPublicReports/1334/National%20Ballot/Results%20Report/National.xls",
                None,
            ),
            Ballot(
                "regional",
                "NPEPublicReports/1334/Regional%20Ballot/Downloadable%20Results/Regional.zip",
                "NPEPublicReports/1334/Regional%20Ballot/Results%20Report/National.xls",
                {"EC": 25, "FS": 10, "GP": 47, "KZN": 41, "LP": 20, "MP": 15, "NW": 13, "NC": 5, "WC": 24},
            ),
            Ballot(
                "provincial",
                "NPEPublicReports/1335/Downloadable%20Results/Provincial.zip",
                "NPEPublicReports/1335/Results%20Report/National.xls",
                {"EC": 72, "FS": 30, "GP": 80, "KZN": 80, "LP": 64, "MP": 51, "NW": 38, "NC": 30, "WC": 42},
            ),
        ),
        date(2021, 11, 1),
    ),
}
NATIONAL_SEATS = {"2014": 400, "2019": 400, "2024": 200}
NATIONAL_METHOD = (
    "Counted by the Electoral Commission of South Africa (IEC) per voting district, as published in the IEC's "
    "downloadable detailed results. Registered voters are the voters' roll of the voting district, valid votes "
    "are the valid votes for parties, and invalid ballots are the ballots the IEC reports as spoilt. Votes cast "
    "abroad are reported per mission and are not placed in an area."
)
MUNICIPAL_METHOD = (
    "Counted by the Electoral Commission of South Africa (IEC) per voting district, as published in the IEC's "
    "downloadable detailed results for each province. Registered voters are the voters' roll of the voting district, "
    "and invalid ballots are the ballots the IEC reports as spoilt on this ballot. The file prints no valid-vote "
    "total per voting district, so valid votes are not stored; they are the sum of the parties' votes."
)
WARD_CAVEAT = (
    "The IEC's file reports every independent ward candidate under the single label INDEPENDENT, so where more "
    "than one independent stood in this ward their votes are combined in one line."
)
INDEPENDENTS_CAVEAT = (
    "The IEC's voting-district file reports every independent candidate on this ballot under the single label "
    "INDEPENDENT, one line per candidate, so their votes are summed in one line per voting district. The IEC's results report names each independent "
    "candidate; their declared totals are summed into that line here."
)
DISTRICT_METHOD = (
    "The district council ballot (40% of the district council's seats) is cast in each local municipality of the "
    "district. The district is the one the Municipal Demarcation Board's code of the local municipality names: "
    "EC101 is in district DC10."
)


def url(path: str) -> str:
    return f"{HOST}/{path}"


def municipal_url(event: int, province: str) -> str:
    return f"{HOST}/LGEPublicReports/{event}/Downloadable%20Party%20Results/{province}.csv"


def municipal_totals_url(event: int) -> str:
    return f"{HOST}/LGEPublicReports/{event}/Detailed%20Results/National.xls"


def fetch() -> None:
    for election in NATIONAL.values():
        for ballot in election.ballots:
            raw.fetch(SOURCE, url(ballot.path), suffix=Path(ballot.path).suffix)
            raw.fetch(SOURCE, url(ballot.totals), suffix=".xls")
    for election in MUNICIPAL.values():
        for province in FILES:
            raw.fetch(SOURCE, municipal_url(election.event, province), suffix=".csv")
        raw.fetch(SOURCE, municipal_totals_url(election.event), suffix=".xls")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def party_key(text: str) -> str:
    return clean(text).upper()


def party(text: str) -> str:
    return key("party", party_key(text))


def area(scheme: str, *parts: object) -> str:
    return key("area", scheme, *parts)


def vintage(day: date) -> date:
    return max(v for v in VINTAGES if v <= day)


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp437")


NATIONAL_COLUMNS = ["Province", "Municipality", "VD", "Station", "Registered", "Spoilt", "Valid", "Party", "Votes"]


@functools.cache
def national_table(path: str) -> pl.DataFrame:
    cached = raw.cached(SOURCE, url(path)).path
    if path.endswith(".zip"):
        archive = zipfile.ZipFile(cached)
        (member,) = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        data = archive.read(member)
        lead = 0
    else:
        data = cached.read_bytes()
        lead = 1  # the 2014 files lead each row with a generated-at stamp
    lines = csv.reader(io.StringIO(_decode(data)))
    next(lines)
    rows = [parsed for row in lines if (parsed := _national_row(row[lead:])) is not None]
    return pl.DataFrame(rows, schema=NATIONAL_COLUMNS, orient="row")


def _national_row(row: list[str]) -> list[str | None] | None:
    """Station names in the 2019 file hold unquoted commas, so the fields are found by their shape."""
    if len(row) < 9 or not row[2].strip() or row[0] == "Province":
        return None
    start = next(
        index for index in range(4, len(row) - 3) if all(re.fullmatch(r"\d*", row[index + k].strip()) for k in range(3))
    )
    end = max(index for index in range(start + 4, len(row)) if row[index].strip().isdigit())
    station = ",".join(row[3:start])
    party_name = ",".join(row[start + 3 : end])
    if not clean(party_name):
        if int(row[end]):
            raise ValueError(f"votes without a party: {row}")
        return None
    province = ABROAD if row[0].startswith(ABROAD) else row[0]
    return [
        clean(text) or None for text in (province, *row[1:3], station, *row[start : start + 3], party_name, row[end])
    ]


@functools.cache
def municipal_table(year: str) -> pl.DataFrame:
    election = MUNICIPAL[year]
    frames = []
    for province in FILES:
        data = raw.cached(SOURCE, municipal_url(election.event, province)).path.read_bytes()
        frames.append(pl.read_csv(io.StringIO(_decode(data)), infer_schema_length=0))
    frame = pl.concat(frames, how="diagonal_relaxed")
    return frame.filter(pl.col("VotingDistrict").is_not_null()).with_columns(
        pl.col(column).str.strip_chars() for column in frame.columns
    )


def split_municipality(text: str) -> tuple[str, str]:
    code, _, name = text.partition(" - ")
    name = re.sub(r"\s*\[.*\]\s*$", "", clean(name))
    if name.isupper():
        name = name.title()
    return clean(code), name


def district(code: str) -> str | None:
    if code in METROS:
        return None
    digits = re.search(r"(\d+)$", code)
    if digits is None:
        raise ValueError(f"municipality code {code} has no number")
    return f"DC{int(digits.group(1)[:-1])}"


def _uses(p: Package, url: str) -> None:
    p.uses(raw.cached(SOURCE, url))


def _all_urls() -> Iterator[str]:
    for election in NATIONAL.values():
        for ballot in election.ballots:
            yield url(ballot.path)
            yield url(ballot.totals)
    for election in MUNICIPAL.values():
        for province in FILES:
            yield municipal_url(election.event, province)
        yield municipal_totals_url(election.event)


SOURCE_ID = key("source", SOURCE)
NA = key("body", "national_assembly")
SYSTEM_NA = key("electoral_system", "national", 1994)
SYSTEM_PROVINCE = key("electoral_system", "provincial", 1994)
SYSTEM_WARD = key("electoral_system", "ward", 2000)
SYSTEM_COUNCIL = key("electoral_system", "council", 2000)
SYSTEM_DISTRICT = key("electoral_system", "district", 2000)


def legislature(province: str) -> str:
    return key("body", "legislature", province)


SYSTEMS: list[tuple[str, date, dict[str, object], str]] = [
    (
        SYSTEM_NA,
        date(1994, 4, 27),
        {"formula": "droop"},
        (
            "The 400 members of the National Assembly are elected by closed-list proportional representation. Seats "
            "are allocated by the Droop quota with largest remainders. Until 2019 one ballot elected 200 members from "
            "national lists and 200 from regional lists, one per province. From 2024 a regional ballot, open to "
            "parties and independent candidates, fills the regional seats, and the national ballot fills the rest so "
            "that the whole Assembly is proportional to the national ballot."
        ),
    ),
    (
        SYSTEM_PROVINCE,
        date(1994, 4, 27),
        {"formula": "droop"},
        (
            "Each provincial legislature is elected by closed-list proportional representation across its province, "
            "with seats allocated by the Droop quota and largest remainders."
        ),
    ),
    (
        SYSTEM_WARD,
        date(2000, 12, 5),
        {"formula": "fptp"},
        (
            "Half the councillors of a metropolitan or local municipality with wards are ward councillors, each "
            "elected by plurality in a single-member ward."
        ),
    ),
    (
        SYSTEM_COUNCIL,
        date(2000, 12, 5),
        {"formula": "droop"},
        (
            "The proportional ballot of a metropolitan or local council allocates the whole council by the Droop quota "
            "on the party votes, then deducts each party's ward seats, so the council as a whole is proportional."
        ),
    ),
    (
        SYSTEM_DISTRICT,
        date(2000, 12, 5),
        {"formula": "droop"},
        (
            "Forty percent of a district council is elected by proportional representation on a district ballot cast "
            "in its local municipalities; the other sixty percent are appointed by the local councils."
        ),
    ),
]


def packages() -> list[str]:
    return [
        "za.iec/geo",
        "za.iec/registry",
        *(f"za.iec/{item.day}" for item in NATIONAL.values()),
        *(f"za.iec/{item.day}" for item in MUNICIPAL.values()),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in NATIONAL:
        national(year).write()
    for year in MUNICIPAL:
        municipal(year).write()


def emit() -> None:
    package.run("load", packages())


def _municipalities() -> dict[tuple[date, str], tuple[str, str]]:
    found: dict[tuple[date, str], tuple[str, str]] = {}
    sources: list[tuple[date, pl.DataFrame]] = []
    for item in NATIONAL.values():
        for ballot in item.ballots:
            sources.append((item.vintage, national_table(ballot.path).select("Province", "Municipality")))
    for year, item in MUNICIPAL.items():
        sources.append((item.day, municipal_table(year).select("Province", "Municipality")))
    for since, frame in sources:
        for province, text in frame.unique().iter_rows():
            if province == ABROAD or text is None:
                continue
            code, name = split_municipality(text)
            prior = found.get((since, code))
            if prior is None or (prior[1].istitle() and not name.istitle()):
                found[(since, code)] = (PROVINCES[province], name)
    return found


def _wards() -> dict[tuple[date, str], str]:
    wards: dict[tuple[date, str], str] = {}
    for year, item in MUNICIPAL.items():
        frame = municipal_table(year).select("Municipality", "Ward").unique()
        for text, ward in frame.iter_rows():
            wards[(item.day, ward_code(ward))] = split_municipality(text)[0]
    return wards


def ward_code(text: str) -> str:
    return text.removeprefix("Ward ").strip()


def _until(since: date) -> date | None:
    later = [v for v in VINTAGES if v > since]
    return min(later) if later else None


def geo() -> Package:
    p = Package(
        "za.iec/geo",
        SOURCE,
        "South Africa's provinces, and the districts, municipalities, and wards of each municipal demarcation from "
        "2011, as the IEC's results name them",
    )
    p.external.add(SOUTH_AFRICA)
    for code, rank, kind, noun in (
        ("za.province", 1, "administrative", "province"),
        ("za.district", 2, "administrative", "district municipality"),
        ("za.municipality", 3, "administrative", "municipality"),
        ("za.ward", 4, "electoral_district", "ward"),
        ("za.vd", 5, "precinct", "voting district"),
    ):
        p.add("scheme", code=code, polity_id=SOUTH_AFRICA, kind=kind, rank=rank, noun=noun, code_system=None)
    for name, code in PROVINCES.items():
        id = area("za.province", code)
        p.add("area", id=id, scheme_code="za.province")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
    districts: set[tuple[date, str, str]] = set()
    for (since, code), (province, name) in sorted(_municipalities().items()):
        id = area("za.municipality", since, code)
        until = _until(since)
        p.add("area", id=id, scheme_code="za.municipality")
        p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
        p.name(id, name, "en", "official")
        dc = district(code)
        parent = area("za.province", province) if dc is None else area("za.district", since, dc)
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
        if dc is not None:
            districts.add((since, dc, province))
    for since, dc, province in sorted(districts):
        id = area("za.district", since, dc)
        until = _until(since)
        p.add("area", id=id, scheme_code="za.district")
        p.add("area_code", area_id=id, code=dc, valid_from=since, valid_to=until)
        p.name(id, f"District {dc}", "en", "official")
        p.add("area_parent", area_id=id, parent_id=area("za.province", province), valid_from=since, valid_to=until)
    for (since, ward), municipality in sorted(_wards().items()):
        id = area("za.ward", since, ward)
        until = _until(since)
        p.add("area", id=id, scheme_code="za.ward")
        p.add("area_code", area_id=id, code=ward, valid_from=since, valid_to=until)
        p.name(id, f"Ward {ward}", "en", "official")
        p.add(
            "area_parent",
            area_id=id,
            parent_id=area("za.municipality", since, municipality),
            valid_from=since,
            valid_to=until,
        )
    return p


def _abbreviations() -> dict[str, str]:
    found: dict[str, str] = {}
    for item in NATIONAL.values():
        for ballot in item.ballots:
            for row in _sheet(url(ballot.totals)):
                if len(row) > 2 and row[1] and row[0] not in ("Party Name", "Total Valid Votes", "Spoilt Votes"):
                    abbreviation = clean(row[1])
                    if abbreviation and abbreviation != "NO_ABBR" and not abbreviation.replace(".", "").isdigit():
                        found.setdefault(party_key(row[0]), abbreviation)
    return found


def _party_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for item in NATIONAL.values():
        for ballot in item.ballots:
            for text in national_table(ballot.path)["Party"].unique().drop_nulls():
                names.setdefault(party_key(text), clean(text))
    for year in MUNICIPAL:
        for text in municipal_table(year)["PartyName"].unique().drop_nulls():
            names.setdefault(party_key(text), clean(text))
    names.pop(INDEPENDENT, None)
    for text in _independents():
        names.pop(party_key(text), None)
    return names


@functools.cache
def _independents() -> frozenset[str]:
    found: set[str] = set()
    for item in NATIONAL.values():
        for ballot in item.ballots:
            for row in _sheet(url(ballot.totals)):
                if len(row) > 2 and row[0] and clean(row[1]).upper() in ("IND", "INDEPENDENT"):
                    found.add(party_key(row[0]))
    return frozenset(found)


def registry() -> Package:
    p = Package(
        "za.iec/registry",
        SOURCE,
        "South Africa's National Assembly, provincial legislatures, electoral systems, and the parties on the IEC's "
        "results from 2011",
        depends=["za.iec/geo"],
    )
    p.external.add(SOUTH_AFRICA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Electoral Commission of South Africa",
        title="IEC election results: downloadable detailed results by voting district",
        url="https://results.elections.org.za/home/",
        license="IEC copyright notice: results may be reproduced with the IEC acknowledged as the source",
        license_url="https://www.elections.org.za/pw/Copyright-Notice",
        attribution="Electoral Commission of South Africa (IEC)",
    )
    for link in _all_urls():
        p.artifact(raw.cached(SOURCE, link), SOURCE_ID)
    p.add("body", id=NA, polity_id=SOUTH_AFRICA, kind="lower_house")
    p.name(NA, "National Assembly", "en", "official")
    for name, code in PROVINCES.items():
        body = legislature(code)
        p.add("body", id=body, polity_id=SOUTH_AFRICA, kind="unicameral")
        p.name(body, f"{name} Provincial Legislature", "en", "official")
    for id, since, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=SOUTH_AFRICA,
            valid_from=since,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    abbreviations = _abbreviations()
    for name_key, text in sorted(_party_names().items()):
        id = party(text)
        p.add("party", id=id, kind="party", polity_id=SOUTH_AFRICA)
        p.add("external_id", entity_id=id, scheme="za.iec.party", value=name_key)
        p.name(id, text, "en", "official")
        if name_key in abbreviations and party_key(abbreviations[name_key]) != name_key:
            p.name(id, abbreviations[name_key], "en", "abbreviation", False)
    return p


def _sheet(link: str) -> list[list[str]]:
    reader = fastexcel.read_excel(raw.cached(SOURCE, link).path.read_bytes())
    frame = reader.load_sheet(0, header_row=None, dtypes="string").to_polars()
    rows = []
    for row in frame.iter_rows():
        cells = [_cell(value) for value in row]
        cells = [value for value in cells if value != ""]
        if cells:
            rows.append(cells)
    return rows


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean(str(value))


def _count(text: str) -> int | None:
    digits = re.sub(r"[,\s*]", "", text)
    return int(digits) if digits.isdigit() else None


def national_totals(link: str) -> dict[str, dict[str | None, dict[int, int]]]:
    rows = _sheet(link)
    (header,) = [row for row in rows if row[-1] == "Total" and "Eastern Cape" in row]
    totals: dict[str, dict[str | None, dict[int, int]]] = {column: {} for column in header}
    for row in rows:
        label = row[0]
        if label == "Total Valid Votes":
            values = row[1::2]
            measure, option = 4, None
        elif label == "Spoilt Votes":
            values, measure, option = row[1:], 5, None
        elif label == "Registered Population":
            values, measure, option = row[1:], 2, None
        elif len(row) == 2 * len(header) + 2 and label != "Party Name":
            values, measure, option = row[2::2], 1, party_key(label)
        else:
            continue
        if len(values) != len(header):
            raise ValueError(f"{link}: {label} has {len(values)} values for {len(header)} columns")
        for column, value in zip(header, values, strict=True):
            count = _count(value)
            if count is not None:
                totals[column].setdefault(option, {})[measure] = count
    return totals


def _election(p: Package, id: str, kind: str, day: date, path: str, english: str) -> None:
    p.add("election", id=id, polity_id=SOUTH_AFRICA, kind=kind, date_from=day, date_to=day, status="held")
    p.name(id, english, "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)


def _contest(
    p: Package,
    id: str,
    election: str,
    kind: str,
    name: str,
    path: str,
    *,
    body: str | None,
    district_area: str | None,
    magnitude: int | None,
    system: str,
    method: str,
) -> str:
    p.add(
        "contest",
        id=id,
        election_id=election,
        kind=kind,
        body_id=body,
        district_area_id=district_area,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(id, name, "en", "official")
    p.add("slug", path=path, entity_id=id, is_current=True)
    result_set = key("result_set", id)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


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


def _unit(p: Package, day: date, vd: str, place: str | None, seen: set[str]) -> str:
    unit = key("unit", day, vd)
    if unit not in seen:
        seen.add(unit)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SOUTH_AFRICA,
            scheme_code="za.vd",
            code=f"{day}:{vd}",
            area_relation="within" if place else "unlocated",
            area_id=place,
            kind="standard" if place else "abroad_mission",
        )
    return unit


def _option(
    p: Package, options: dict[tuple[str, str], str], contest: str, label: str, known: set[str], *, ward: bool = False
) -> str:
    name_key = party_key(label)
    found = options.get((contest, name_key))
    if found is not None:
        return found
    id = options[(contest, name_key)] = key("option", contest, name_key)
    party_id = party(label)
    if name_key == INDEPENDENT:
        kind, party_id = "independents", None
    else:
        kind = "candidate" if ward else "list"
        if party_id not in known:
            raise ValueError(f"{label} is not in the registry")
    p.add(
        "contest_option",
        id=id,
        contest_id=contest,
        kind=kind,
        party_id=party_id,
        ballot_position=None,
        ballot_label=clean(label),
    )
    return id


def _registered_parties() -> set[str]:
    return {party(text) for text in _party_names().values()}


def national(year: str) -> Package:
    item = NATIONAL[year]
    day = item.day
    p = Package(
        f"za.iec/{day}",
        SOURCE,
        f"South Africa's {year} National Assembly and provincial legislature elections by voting district from the IEC",
        depends=["za.iec/geo", "za.iec/registry"],
    )
    p.external.add(SOUTH_AFRICA)
    election = key("election", day)
    path = f"za/{day}-general"
    _election(p, election, "general", day, path, f"{year} South African general election")
    known = _registered_parties()
    since = item.vintage
    seen: set[str] = set()
    options: dict[tuple[str, str], str] = {}
    for ballot in item.ballots:
        _uses(p, url(ballot.path))
        _uses(p, url(ballot.totals))
        frame = national_table(ballot.path)
        declared = national_totals(url(ballot.totals))
        sets: dict[str, str] = {}
        contests: dict[str, str] = {}
        if ballot.kind == "national":
            contest = key("contest", day, "national")
            contests[""] = contest
            sets[""] = _contest(
                p,
                contest,
                election,
                "list_allocation",
                "National Assembly" if year != "2024" else "National Assembly, national ballot",
                f"{path}/national-assembly",
                body=NA,
                district_area=None,
                magnitude=NATIONAL_SEATS[year],
                system=SYSTEM_NA,
                method=NATIONAL_METHOD,
            )
        else:
            for name, code in PROVINCES.items():
                contest = key("contest", day, ballot.kind, code)
                contests[name] = contest
                regional = ballot.kind == "regional"
                sets[name] = _contest(
                    p,
                    contest,
                    election,
                    "list_allocation",
                    f"National Assembly, {name} regional ballot" if regional else f"{name} Provincial Legislature",
                    f"{path}/national-assembly-{changeset.slug(name)}"
                    if regional
                    else f"{path}/{changeset.slug(name)}-provincial-legislature",
                    body=NA if regional else legislature(code),
                    district_area=area("za.province", code),
                    magnitude=(ballot.seats or {})[code],
                    system=SYSTEM_NA if regional else SYSTEM_PROVINCE,
                    method=NATIONAL_METHOD,
                )
        accounted: set[tuple[str, str]] = set()
        votes: dict[tuple[str, str, str], int] = {}
        for row in frame.iter_rows(named=True):
            province = row["Province"]
            if province == ABROAD and ballot.kind != "national":
                if int(row["Votes"]) or _count(row["Valid"] or ""):
                    raise ValueError(f"{ballot.path}: votes cast abroad on a provincial ballot")
                continue  # the file lists the abroad station with no votes on a ballot cast only at home
            scope = "" if ballot.kind == "national" else province
            if scope not in sets:
                raise ValueError(f"{ballot.path}: {province} has no contest")
            result_set = sets[scope]
            place = (
                None
                if province == ABROAD
                else area("za.municipality", since, split_municipality(row["Municipality"])[0])
            )
            unit = _unit(p, day, row["VD"], place, seen)
            if (result_set, unit) not in accounted:
                accounted.add((result_set, unit))
                for measure, column in ((2, "Registered"), (4, "Valid"), (5, "Spoilt")):
                    value = _count(row[column] or "")
                    if value is not None:
                        _value(p, result_set, unit, None, measure, value)
            option = _option(p, options, contests[scope], row["Party"], known)
            votes[(result_set, unit, option)] = votes.get((result_set, unit, option), 0) + int(row["Votes"])
        for (result_set, unit, option), value in votes.items():
            _value(p, result_set, unit, option, 1, value)
        columns = {"": "Total"} if ballot.kind == "national" else {name: name for name in PROVINCES}
        for scope, column in columns.items():
            folded: dict[str | None, dict[int, int]] = {}
            for option_key, figures in declared[column].items():
                target = INDEPENDENT if option_key in _independents() else option_key
                for measure, value in figures.items():
                    if measure != 2:
                        folded.setdefault(target, {})[measure] = folded.get(target, {}).get(measure, 0) + value
            for option_key, figures in folded.items():
                option = None if option_key is None else options.get((contests[scope], option_key))
                if option_key is not None and option is None:
                    if any(figures.values()):
                        raise ValueError(f"{ballot.path}: {option_key} has declared votes but no rows")
                    continue
                if option_key == INDEPENDENT:
                    p.add(
                        "note",
                        id=key("note", sets[scope], "independents"),
                        entity_id=sets[scope],
                        kind="caveat",
                        body_md=INDEPENDENTS_CAVEAT,
                        lang="en",
                    )
                for measure, value in figures.items():
                    p.add("declared", result_set_id=sets[scope], option_id=option, measure=measure, value=value)
    _reconcile(p, year)
    return p


MEASURES = {1: "votes", 4: "valid votes", 5: "spoilt ballots"}


def _reconcile(p: Package, year: str) -> None:
    sums: dict[tuple[object, object, object], int] = {}
    valid: dict[tuple[object, object], list[int]] = {}
    for row in p.rows["result_value"]:
        index = (row["result_set_id"], row["option_id"], row["measure"])
        sums[index] = sums.get(index, 0) + int(str(row["value"]))
        if row["measure"] in (1, 4):
            pair = valid.setdefault((row["result_set_id"], row["unit_id"]), [0, -1])
            if row["measure"] == 1:
                pair[0] += int(str(row["value"]))
            else:
                pair[1] = int(str(row["value"]))
    gaps: dict[object, list[str]] = {}
    largest = 0
    for row in p.rows.get("declared", []):
        ours = sums.get((row["result_set_id"], row["option_id"], row["measure"]), 0)
        declared = int(str(row["value"]))
        if ours != declared:
            largest = max(largest, abs(ours - declared))
            gaps.setdefault(row["result_set_id"], []).append(
                f"{MEASURES[int(str(row['measure']))]} {ours:,} against {declared:,}"
            )
    if gaps:
        p.accept(
            "accounting.declared",
            f"the IEC's voting-district file for {year} sums to within {largest:,} of the IEC's own results report "
            f"in {sum(len(v) for v in gaps.values())} party or ballot totals; both are stored as published",
        )
        for result_set, lines in gaps.items():
            p.add(
                "note",
                id=key("note", result_set, "declared"),
                entity_id=result_set,
                kind="caveat",
                body_md="The voting-district file sums differ from the totals in the IEC's results report: "
                + "; ".join(lines[:12])
                + (" and more" if len(lines) > 12 else "")
                + ". The IEC generated the two at different times; both are stored as published.",
                lang="en",
            )
    off = sum(1 for votes, total in valid.values() if total >= 0 and votes != total)
    if off:
        p.accept(
            "accounting.votes_valid",
            f"in {off} voting districts the IEC's file prints a valid-vote total that differs from the sum of the "
            "parties' votes; both are stored as printed",
        )


def municipal(year: str) -> Package:
    item = MUNICIPAL[year]
    day = item.day
    p = Package(
        f"za.iec/{day}",
        SOURCE,
        f"South Africa's {year} municipal elections, every ward, council, and district council ballot, by voting "
        "district from the IEC",
        depends=["za.iec/geo", "za.iec/registry"],
    )
    p.external.add(SOUTH_AFRICA)
    for province in FILES:
        _uses(p, municipal_url(item.event, province))
    _uses(p, municipal_totals_url(item.event))
    election = key("election", day)
    path = f"za/{day}-local"
    _election(p, election, "local", day, path, f"{year} South African municipal elections")
    known = _registered_parties()
    frame = municipal_table(year)
    names = {code: name for (since, code), (_, name) in _municipalities().items() if since == day}
    seen: set[str] = set()
    options: dict[tuple[str, str], str] = {}
    sets: dict[str, str] = {}
    accounted: set[tuple[str, str]] = set()
    for row in frame.iter_rows(named=True):
        code = split_municipality(row["Municipality"])[0]
        ward = ward_code(row["Ward"])
        ballot = row["BallotType"]
        if ballot == "Ward":
            scope = f"ward:{ward}"
            if scope not in sets:
                number = int(ward[-3:])
                contest = key("contest", day, "ward", ward)
                sets[scope] = _contest(
                    p,
                    contest,
                    election,
                    "body_seats",
                    f"{names[code]} Ward {number}",
                    f"{path}/{code.lower()}-ward-{number}",
                    body=None,
                    district_area=area("za.ward", day, ward),
                    magnitude=1,
                    system=SYSTEM_WARD,
                    method=MUNICIPAL_METHOD + " " + WARD_CAVEAT,
                )
        elif ballot == "PR":
            scope = f"council:{code}"
            if scope not in sets:
                contest = key("contest", day, "council", code)
                sets[scope] = _contest(
                    p,
                    contest,
                    election,
                    "list_allocation",
                    f"{names[code]} council, proportional ballot",
                    f"{path}/{code.lower()}-{changeset.slug(names[code])}",
                    body=None,
                    district_area=area("za.municipality", day, code),
                    magnitude=None,
                    system=SYSTEM_COUNCIL,
                    method=MUNICIPAL_METHOD,
                )
        elif ballot == "DC 40%":
            dc = district(code)
            if dc is None:
                raise ValueError(f"{year}: metropolitan municipality {code} has a district ballot")
            scope = f"district:{dc}"
            if scope not in sets:
                contest = key("contest", day, "district", dc)
                sets[scope] = _contest(
                    p,
                    contest,
                    election,
                    "list_allocation",
                    f"District {dc} council, district ballot",
                    f"{path}/{dc.lower()}-district-council",
                    body=None,
                    district_area=area("za.district", day, dc),
                    magnitude=None,
                    system=SYSTEM_DISTRICT,
                    method=MUNICIPAL_METHOD + " " + DISTRICT_METHOD,
                )
        else:
            raise ValueError(f"{year}: unknown ballot {ballot}")
        result_set = sets[scope]
        contest = key("contest", day, *scope.split(":"))
        unit = _unit(p, day, row["VotingDistrict"], area("za.ward", day, ward), seen)
        if (result_set, unit) not in accounted:
            accounted.add((result_set, unit))
            for measure, column in ((2, "RegisteredVoters"), (5, "SpoiltVotes")):
                value = _count(row[column] or "")
                if value is not None:
                    _value(p, result_set, unit, None, measure, value)
        option = _option(p, options, contest, row["PartyName"], known, ward=ballot == "Ward")
        _value(p, result_set, unit, option, 1, int(row["TotalValidVotes"]))
    return p
