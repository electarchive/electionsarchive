"""Taiwan's presidential elections by polling station, from the Central Election Commission's election database."""

import functools
import json
import re
import zipfile
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import fastexcel
import polars as pl
import xlrd

from ea import package, raw
from ea.package import Package

SOURCE = "tw.cec"
BASE = "https://db.cec.gov.tw/static/elections/data/"
TAIWAN = str(uuid5(package.NAMESPACE, "polity\x1fTW"))
LICENSE = "Open Government Data License, version 1.0 (Taiwan)"
LICENSE_URL = "https://data.gov.tw/license"

# date -> (theme id, session, layout, bulk archive name)
#   layout "single": one workbook with every polling station, a column per candidate holding votes and a share
#   layout "single_late": the same, but the accounting columns come before the candidates
#   layout "county": one workbook per county, holding the polling-station table (A05-4 or rpt06-4)
PRESIDENT: dict[str, tuple[str, int, str, str]] = {
    "1996-03-23": ("c818e628012243a0ea7eb3c3c3b81565", 9, "single", "第09任總統(副總統)得票概況.zip"),
    "2000-03-18": ("bc69efc4ab84479137e749aac2078116", 10, "single_late", "第10任總統(副總統)得票概況.zip"),
    "2004-03-20": ("38b0ef91b5866696f58891ecf69cb3c7", 11, "single", "第11任總統(副總統)得票概況.zip"),
    "2008-03-22": ("54deeba50d3cc135f45018059c0cb062", 12, "county", "第12任總統(副總統)得票概況.zip"),
    "2012-01-14": ("fddf766f2a250a2e3688d644fda346d2", 13, "county", "第13任總統(副總統)得票概況.zip"),
    "2016-01-16": ("61b4dda0ebac3332203ef3729a9a0ada", 14, "county", "總統-各投票所得票明細及概況(Excel檔).zip"),
    "2020-01-11": ("1f7d9f4f6bfe06fdaf4db7df2ed4d60c", 15, "county", "總統-各投票所得票明細及概況(Excel檔).zip"),
    "2024-01-13": ("4d83db17c1707e3defae5dc4d4e9c800", 16, "county", "總統-各投票所得票明細及概況(Excel檔).zip"),
}
PARTIES = {
    "中國國民黨": "Kuomintang",
    "民主進步黨": "Democratic Progressive Party",
    "新黨": "New Party",
    "親民黨": "People First Party",
    "台灣民眾黨": "Taiwan People's Party",
}
INDEPENDENT = {"連署", "無黨籍及未經政黨推薦"}
SYSTEM_START = date(1996, 1, 1)
METHOD = (
    "Voters choose one ticket of a president and a vice president on the ballot. The ticket with the most votes in "
    "one nationwide count is elected, with no runoff."
)
RESULT_NOTE = (
    "Valid votes for each ticket, valid and invalid ballots, ballots cast, and registered voters at each polling "
    "station, as published by the Central Election Commission. Voters registered is the number of voters on the "
    "station's roll (issued plus unused ballots). National totals are the Commission's own."
)
NATION_NAME = ("Taiwan", "臺灣")


def folder(day: str) -> str:
    return f"ELC/P0/00/{PRESIDENT[day][0]}"


def bulk_url(day: str) -> str:
    return f"{BASE}attachments/ELC/P0/{PRESIDENT[day][0]}/{quote(PRESIDENT[day][3])}"


def json_url(kind: str, day: str) -> str:
    return f"{BASE}{kind}/{folder(day)}/N/00_000_00_000_0000.json"


def fetch() -> None:
    for day in PRESIDENT:
        for url, suffix in (
            (bulk_url(day), ".zip"),
            (json_url("tickets", day), ".json"),
            (json_url("profiles", day), ".json"),
        ):
            raw.fetch(SOURCE, url, suffix=suffix)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, "|".join(parts))


NATION = key("area", "tw.nation", "TW")
CEC = key("source", SOURCE)
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president", 1996)


def packages() -> list[str]:
    return ["tw.cec/geo", "tw.cec/registry", *(f"tw.cec/prez-{day}" for day in PRESIDENT)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in PRESIDENT:
        president(day).write()


def emit() -> None:
    package.run("load", packages())


# --- reading the workbooks


def number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, float):
        return round(value)
    if isinstance(value, int):
        return value
    text = str(value).replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return round(float(text))
    except ValueError:
        return None


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"[\s　]+", "", str(value))


def station_code(value: object) -> str:
    if isinstance(value, float):
        return str(int(value))
    return text(value)


def members(day: str) -> list[tuple[str, bytes]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, bulk_url(day)).path)
    found: list[tuple[str, bytes]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = (
            info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("cp950", errors="replace")
        )
        found.append((name, archive.read(info)))
    return found


def grid(name: str, data: bytes) -> tuple[str, list[list[object]]]:
    if name.lower().endswith(".xlsx"):
        workbook = fastexcel.read_excel(data)
        sheet = workbook.sheet_names[0]
        frame = workbook.load_sheet(sheet, header_row=None).to_polars()
        return sheet, [list(row) for row in frame.rows()]
    book = xlrd.open_workbook(file_contents=data, encoding_override="cp950")
    sheet = book.sheet_by_index(0)
    return sheet.name, [sheet.row_values(r) for r in range(sheet.nrows)]


def county_rows(name: str, data: bytes) -> list[dict[str, object]]:
    sheet, rows = grid(name, data)
    title = text(rows[0][0])
    found = re.search(r"在(.+?)各投開票所", title)
    county = found.group(1) if found else text(sheet)
    out: list[dict[str, object]] = []
    district = ""
    width = max(len(row) for row in rows)
    candidates = width - 3 - 8
    for row in rows:
        label, village, code = text(row[0]), text(row[1]), station_code(row[2])
        if not (village and code) or village == "村里別":
            if label and label != "總計" and number(row[3]) is not None:
                district = label
            continue
        if not district:
            raise ValueError(f"{name}: a station row comes before any district")
        values = [number(row[3 + index]) for index in range(candidates)]
        tail = [number(row[3 + candidates + index]) for index in range(8)]
        out.append(
            {
                "county": county,
                "district": district,
                "village": village,
                "station": code,
                "votes": values,
                "valid": tail[0],
                "invalid": tail[1],
                "cast": tail[2],
                "registered": tail[6],
            }
        )
    return out


def single_rows(name: str, data: bytes, count: int, late: bool) -> list[dict[str, object]]:
    _, rows = grid(name, data)
    out: list[dict[str, object]] = []
    for row in rows:
        county, district, village = text(row[0]), text(row[1]), text(row[2])
        if not (county and district and village) or number(row[3]) is None:
            continue
        station = station_code(row[3])
        if late:
            valid, invalid, cast, _, registered = (number(row[4 + index]) for index in range(5))
            votes = [number(row[10 + 2 * index]) for index in range(count)]
        else:
            votes = [number(row[4 + 2 * index]) for index in range(count)]
            offset = 4 + 2 * count
            valid, invalid, cast, _, registered = (number(row[offset + index]) for index in range(5))
        out.append(
            {
                "county": county,
                "district": district,
                "village": village,
                "station": station,
                "votes": votes,
                "valid": valid,
                "invalid": invalid,
                "cast": cast,
                "registered": registered,
            }
        )
    return out


@functools.cache
def stations(day: str) -> pl.DataFrame:
    layout = PRESIDENT[day][2]
    files = members(day)
    rows: list[dict[str, object]] = []
    if layout == "county":
        for name, data in files:
            base = name.rsplit("/", 1)[-1]
            if re.search(r"A05-4", base) or re.fullmatch(r"rpt06-4_\d+\.xls", base):
                rows.extend(county_rows(name, data))
    else:
        count = len(tickets(day))
        ((name, data),) = [file for file in files if file[0].lower().endswith(".xls")]
        rows = single_rows(name, data, count, layout == "single_late")
    frame = pl.DataFrame(rows)
    if frame.is_empty():
        raise LookupError(f"no polling stations found for {day}")
    return frame


@functools.cache
def tickets(day: str) -> list[dict[str, object]]:
    """One entry per ballot number, with the candidate, the running mate, and the official national votes."""
    data = json.loads(raw.cached(SOURCE, json_url("tickets", day)).path.read_text(encoding="utf-8"))
    rows = next(iter(data.values()))
    result: list[dict[str, object]] = []
    for number_ in sorted({row["cand_no"] for row in rows}):
        pair = [row for row in rows if row["cand_no"] == number_]
        head = next(row for row in pair if row["is_vice"] != "Y")
        vice = next(row for row in pair if row["is_vice"] == "Y")
        result.append(
            {
                "no": number_,
                "head": head,
                "vice": vice,
                "votes": int(head["ticket_num"]),
                "won": any(row["is_victor"] == "*" for row in pair),
                "party": str(head["party_name"]),
            }
        )
    return result


@functools.cache
def profile(day: str) -> dict[str, object]:
    data = json.loads(raw.cached(SOURCE, json_url("profiles", day)).path.read_text(encoding="utf-8"))
    return dict(next(iter(data.values()))[0])


# --- packages


def geo() -> Package:
    p = Package(
        "tw.cec/geo",
        SOURCE,
        "Taiwan's counties and cities, townships and districts, villages, and the polling stations that the Central "
        "Election Commission reports presidential votes for",
    )
    p.external.add(TAIWAN)
    for code, rank, kind, noun in (
        ("tw.nation", 0, "administrative", "country"),
        ("tw.county", 1, "administrative", "county or city"),
        ("tw.township", 2, "administrative", "township or district"),
        ("tw.village", 3, "administrative", "village or neighborhood"),
        ("tw.station", 4, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=TAIWAN, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="tw.nation")
    p.add("area_code", area_id=NATION, code="TW")
    p.name(NATION, NATION_NAME[0], "en", "official")
    p.name(NATION, NATION_NAME[1], "zh", "native", False)
    counties: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    for day in PRESIDENT:
        for row in stations(day).select("county", "district", "village").unique().iter_rows(named=True):
            counties.add(row["county"])
            townships.add((row["county"], row["district"]))
            villages.add((row["county"], row["district"], row["village"]))
    for county in sorted(counties):
        id = area("tw.county", county)
        p.add("area", id=id, scheme_code="tw.county")
        p.add("area_code", area_id=id, code=county)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, county, "zh", "official")
    for county, district in sorted(townships):
        id = area("tw.township", county, district)
        p.add("area", id=id, scheme_code="tw.township")
        p.add("area_code", area_id=id, code=f"{county}|{district}")
        p.add("area_parent", area_id=id, parent_id=area("tw.county", county))
        p.name(id, district, "zh", "official")
    for county, district, village in sorted(villages):
        id = area("tw.village", county, district, village)
        p.add("area", id=id, scheme_code="tw.village")
        p.add("area_code", area_id=id, code=f"{county}|{district}|{village}")
        p.add("area_parent", area_id=id, parent_id=area("tw.township", county, district))
        p.name(id, village, "zh", "official")
    return p


def registry() -> Package:
    p = Package(
        "tw.cec/registry",
        SOURCE,
        "Taiwan's political parties, the presidency, and its electoral system from the Central Election Commission",
        depends=["tw.cec/geo"],
    )
    p.external.add(TAIWAN)
    p.add(
        "source",
        id=CEC,
        kind="official_body",
        publisher="Central Election Commission",
        title="Election Database (選舉資料庫)",
        url="https://db.cec.gov.tw/",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Central Election Commission, Taiwan (中央選舉委員會)",
    )
    for day in PRESIDENT:
        for url in (bulk_url(day), json_url("tickets", day), json_url("profiles", day)):
            p.artifact(raw.cached(SOURCE, url), CEC)
    p.add("office", id=PRESIDENCY, polity_id=TAIWAN, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of China (Taiwan)", "en", "official")
    p.name(PRESIDENCY, "總統", "zh", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TAIWAN,
        valid_from=SYSTEM_START,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")
    used = {str(t["party"]) for day in PRESIDENT for t in tickets(day)}
    for name in sorted(used - INDEPENDENT):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=TAIWAN)
        p.name(id, name, "zh", "official")
        if name in PARTIES:
            p.name(id, PARTIES[name], "en", "official", False)
    return p


def president(day: str) -> Package:
    year = day[:4]
    session = PRESIDENT[day][1]
    p = Package(
        f"tw.cec/prez-{day}",
        SOURCE,
        f"The {year} Taiwanese presidential election by polling station from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for url in (bulk_url(day), json_url("tickets", day), json_url("profiles", day)):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", day)
    path = f"tw/{day}-presidential"
    when = date.fromisoformat(day)
    p.add("election", id=election, polity_id=TAIWAN, kind="general", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Taiwanese presidential election", "en", "official")
    p.name(election, f"第{session}任總統副總統選舉", "zh", "native", False)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Taiwanese presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    negative = stations(day).filter(pl.col("invalid") < 0)
    caveat = "".join(
        f" The Commission publishes {row['invalid']} invalid ballots at {row['county']} {row['district']} "
        f"{row['village']} station {row['station']}; a negative count is not stored."
        for row in negative.iter_rows(named=True)
    )
    for row in negative.iter_rows(named=True):
        where = f"{row['county']} {row['district']} {row['village']} station {row['station']}"
        p.accept(
            "accounting.ballots_sum",
            f"The Commission publishes {row['valid']} valid votes and {row['cast']} ballots cast at {where}, so the "
            f"station's invalid count is {row['invalid']}; the other stations add up",
        )
        p.accept(
            "accounting.declared",
            f"The stored invalid ballots omit the {row['invalid']} the Commission prints at {where}, which is negative; "
            "the Commission's national invalid total includes it",
        )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=RESULT_NOTE + caveat,
        lang="en",
    )
    options: list[str] = []
    for ticket in tickets(day):
        head, vice = ticket["head"], ticket["vice"]
        assert isinstance(head, dict) and isinstance(vice, dict)
        no = int(str(ticket["no"]))
        party = str(ticket["party"])
        independent = party in INDEPENDENT
        party_id = None if independent else key("party", party)
        option = key("option", day, no)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            party_id=party_id,
            ballot_position=no,
            ballot_label=f"{head['cand_name']} / {vice['cand_name']}",
        )
        for role, row in (("head", head), ("running_mate", vice)):
            person = key("person", day, no, role)
            birth = str(row.get("cand_birthyear") or "").strip()
            p.add(
                "person",
                id=person,
                birth_date=date(int(birth), 1, 1) if birth.isdigit() else None,
                birth_precision="year" if birth.isdigit() else None,
                gender={"1": "male", "2": "female"}.get(str(row.get("cand_sex") or "")),
            )
            p.name(person, str(row["cand_name"]), "zh", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, no, role),
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=str(row["cand_name"]),
                member_of_party_id=party_id,
                is_independent=independent,
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if ticket["won"] else "lost")
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=int(str(ticket["votes"])))
    total = profile(day)
    for measure, field in ((4, "valid_ticket"), (5, "invalid_ticket"), (3, "vote_ticket"), (2, "votable_population")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=int(str(total[field])))
    frame = stations(day).with_row_index("n")
    units = frame.with_columns(
        code=pl.format("{}:{}:{}:{}:{}", pl.lit(day), "county", "district", "village", "station"),
    )
    if units["code"].n_unique() != units.height:
        raise ValueError(f"{day}: polling station codes repeat")
    unit_rows = units.select(
        id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        polity_id=pl.lit(TAIWAN),
        scheme_code=pl.lit("tw.station"),
        code="code",
        area_relation=pl.lit("within"),
        area_id=pl.struct("county", "district", "village").map_elements(
            lambda row: area("tw.village", row["county"], row["district"], row["village"]), return_dtype=pl.String
        ),
        kind=pl.lit("standard"),
    )
    p.frame("reporting_unit", unit_rows)
    values: list[pl.DataFrame] = []
    base = units.select(
        unit_id=unit_rows["id"], votes="votes", valid="valid", invalid="invalid", cast="cast", registered="registered"
    )
    for index, option in enumerate(options):
        values.append(
            base.select(
                unit_id="unit_id",
                option_id=pl.lit(option),
                measure=pl.lit(1),
                value=pl.col("votes").list.get(index),
            )
        )
    for measure, column in ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid")):
        values.append(
            base.select(unit_id="unit_id", option_id=pl.lit(None, pl.String), measure=pl.lit(measure), value=column)
        )
    facts = pl.concat(values).filter(pl.col("value").is_not_null() & (pl.col("value") >= 0))
    p.frame(
        "result_value",
        facts.select(
            result_set_id=pl.lit(result_set),
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    return p
