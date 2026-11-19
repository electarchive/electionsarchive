"""Taiwan's presidential and legislative elections, special municipality mayors, and county magistrates and city mayors,
from the Central Election Commission's election database. The 2008 and 2012 legislative archives are .rar files; parsing
them needs the `unar` command."""

import functools
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast
from urllib.parse import quote
from uuid import uuid5

import fastexcel
import libarchive
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
    from ea.sources.tw import (
        cec_abolished,
        cec_by,
        cec_local_by,
        cec_recall,
        cec_referendum,
        cec_shapes,
        cec_township,
        cec_village,
    )

    for day in PRESIDENT:
        for url, suffix in (
            (bulk_url(day), ".zip"),
            (json_url("tickets", day), ".json"),
            (json_url("profiles", day), ".json"),
        ):
            raw.fetch(SOURCE, url, suffix=suffix)
    fetch_legislative()
    cec_by.fetch()
    cec_local_by.fetch()
    cec_abolished.fetch()
    local_fetch()
    cec_township.fetch()
    cec_village.fetch()
    cec_referendum.fetch()
    cec_recall.fetch()
    cec_shapes.fetch()


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, "|".join(parts))


NATION = key("area", "tw.nation", "TW")
CEC = key("source", SOURCE)
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president", 1996)
LOCAL_SYSTEM = key("electoral_system", "local executive", 1994)


def office(county: str) -> str:
    return key("office", "local executive", county)


def packages() -> list[str]:
    from ea.sources.tw import (
        cec_abolished,
        cec_by,
        cec_local_by,
        cec_recall,
        cec_referendum,
        cec_township,
        cec_village,
    )

    return [
        "tw.cec/geo",
        "tw.cec/registry",
        *(f"tw.cec/prez-{day}" for day in PRESIDENT),
        *(f"tw.cec/leg-{day}" for day in leg_days()),
        *cec_by.packages(),
        *cec_local_by.packages(),
        *cec_abolished.packages(),
        *(f"tw.cec/local-{day}" for day in LOCAL_DAYS),
        *cec_township.packages(),
        *cec_village.packages(),
        *cec_referendum.packages(),
        *cec_recall.packages(),
    ]


def parse() -> None:
    from ea.sources.tw import (
        cec_abolished,
        cec_by,
        cec_local_by,
        cec_recall,
        cec_referendum,
        cec_township,
        cec_village,
    )

    geo().write()
    registry().write()
    for day in PRESIDENT:
        president(day).write()
    for day in leg_days():
        legislative(day).write()
    cec_by.parse()
    cec_local_by.parse()
    cec_abolished.parse()
    for day in LOCAL_DAYS:
        local(day).write()
    cec_township.parse()
    cec_village.parse()
    cec_referendum.parse()
    cec_recall.parse()


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
    from ea.sources.tw import (
        cec_by,
        cec_local_by,
        cec_recall,
        cec_referendum,
        cec_shapes,
        cec_township,
        cec_village,
    )

    p = Package(
        "tw.cec/geo",
        SOURCE,
        "Taiwan's counties and cities, townships and districts, villages, and the polling stations that the Central "
        "Election Commission reports votes for",
    )
    p.external.add(TAIWAN)
    for code, rank, kind, noun in (
        ("tw.nation", 0, "administrative", "country"),
        ("tw.county", 1, "administrative", "county or city"),
        ("tw.township", 2, "administrative", "township or district"),
        ("tw.village", 3, "administrative", "village or neighborhood"),
        ("tw.station", 4, "precinct", "polling station"),
        ("tw.district", 1, "electoral_district", "legislative district"),
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
    districts: set[tuple[int, str, int]] = set()
    for day in leg_days():
        for row in leg_station_rows(day).iter_rows(named=True):
            counties.add(row["county"])
            townships.add((row["county"], row["township"]))
            if row["village"]:
                villages.add((row["county"], row["township"], row["village"]))
        districts.update(
            (session(day), county, district)
            for (type_, county, district) in leg_tables(day)
            if type_ == "L1" and county is not None and district is not None
        )
    for theme in THEMES:
        for contest in contests(theme.day, theme.subject):
            counties.add(contest.county)
            for row in contest.rows:
                townships.add((contest.county, str(row["district"])))
                if row["village"]:
                    villages.add((contest.county, str(row["district"]), str(row["village"])))
    cec_township.extend_geo(counties, townships, villages)
    cec_village.extend_geo(counties, townships, villages)
    cec_referendum.extend_geo(counties, townships, villages)
    cec_recall.extend_geo(counties, townships, villages)
    cec_recall.extend_districts(districts)
    cec_by.extend_geo(counties, townships, villages)
    cec_by.extend_districts(districts)
    cec_local_by.extend_geo(counties, townships, villages)
    for county in sorted(counties):
        id = area("tw.county", county)
        p.add("area", id=id, scheme_code="tw.county")
        p.add("area_code", area_id=id, code=county)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, county, "zh", "official")
        if county in PLACES:
            p.name(id, PLACES[county], "en", "official", False)
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
    for number_, county, district in sorted(districts):
        id = key("area", "tw.district", f"{number_}|{county}|{district}")
        p.add("area", id=id, scheme_code="tw.district")
        p.add("area_code", area_id=id, code=f"{number_}|{county}|{district}")
        p.add("area_parent", area_id=id, parent_id=area("tw.county", county))
        p.name(id, f"{county}第{district}選舉區", "zh", "official")
    cec_township.geo(p)
    cec_recall.geo(p)
    cec_shapes.geo(p, {name: area("tw.county", name) for name in counties})
    return p


def registry() -> Package:
    from ea.sources.tw import cec_by, cec_local_by, cec_recall, cec_referendum, cec_township, cec_village

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
    cited: set[str] = set()
    urls = [url for day in PRESIDENT for url in (bulk_url(day), json_url("tickets", day), json_url("profiles", day))]
    urls += [url for theme in THEMES for url in local_artifacts(theme)]
    for url in urls:
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in cited:
            cited.add(artifact.storage_key)
            p.artifact(artifact, CEC)
    for county in sorted({c.county for theme in THEMES for c in contests(theme.day, theme.subject)}):
        id = office(county)
        p.add("office", id=id, polity_id=TAIWAN, kind="mayor" if county in CITIES else "other")
        p.name(id, office_name(county), "en", "official")
        p.name(id, f"{county}{'市長' if county in CITIES else '縣長'}", "zh", "native", False)
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
    for day in leg_days():
        for artifact in leg_artifacts(day):
            p.artifact(artifact, CEC)
    p.add("body", id=LEGISLATURE, polity_id=TAIWAN, kind="unicameral")
    p.name(LEGISLATURE, "Legislative Yuan", "en", "official")
    p.name(LEGISLATURE, "立法院", "zh", "native", False)
    for system, start, end, rules, method in (
        (SNTV_DISTRICT, LEG_START, date(2007, 12, 31), {"formula": "sntv"}, LEG_METHOD["sntv"]),
        (FPTP_DISTRICT, MIXED_FROM, None, {"formula": "fptp"}, LEG_METHOD["fptp"]),
        (INDIGENOUS, LEG_START, None, {"formula": "sntv"}, LEG_METHOD["indigenous"]),
        (
            PARTY_LIST,
            MIXED_FROM,
            None,
            {"formula": "hare", "threshold": {"percent": 5, "level": "party", "scope": "national"}},
            LEG_METHOD["list"],
        ),
    ):
        p.add(
            "electoral_system",
            id=system,
            polity_id=TAIWAN,
            valid_from=start,
            valid_to=end,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=method, lang="en")
    p.add(
        "electoral_system",
        id=LOCAL_SYSTEM,
        polity_id=TAIWAN,
        valid_from=LOCAL_SYSTEM_START,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", LOCAL_SYSTEM),
        entity_id=LOCAL_SYSTEM,
        kind="methodology",
        body_md=LOCAL_METHOD,
        lang="en",
    )
    used = {str(t["party"]) for day in PRESIDENT for t in tickets(day)} | leg_parties()
    used |= {c.party for theme in THEMES for contest in contests(theme.day, theme.subject) for c in contest.cands}
    cec_township.registry(p)
    cec_village.registry(p)
    cec_referendum.registry(p)
    cec_recall.registry(p)
    cec_by.registry(p)
    cec_local_by.registry(p)
    used |= cec_township.parties() | cec_village.parties() | cec_by.parties() | cec_local_by.parties()
    for name in sorted(used - INDEPENDENT):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=TAIWAN)
        p.name(id, name, "zh", "official")
        if name in PARTIES or name in PARTY_EN:
            p.name(id, PARTIES.get(name) or PARTY_EN[name], "en", "official", False)
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


# --- legislative elections

LIST_URL = "https://db.cec.gov.tw/static/elections/list/ELC_L0.json"
# session -> bulk archive name in the theme group's folder
LEGISLATIVE: dict[int, str] = {
    3: "第03屆立法委員選舉得票概況.zip",
    4: "第04屆立法委員選舉得票概況.zip",
    5: "第05屆立法委員選舉得票概況.zip",
    6: "第06屆立法委員選舉得票概況.zip",
    7: "第07屆立法委員選舉得票概況.rar",
    8: "第08屆立法委員選舉得票概況.rar",
    9: "各投票所得票明細及概況(Excel檔).zip",
    10: "立委-各投票所得票明細及概況(Excel檔).zip",
    11: "立委-各投票所得票明細及概況(Excel檔).zip",
}
# the contest types of the Commission's legislative subject: district, plains and mountain indigenous, party list
TYPES = {"L1": "district", "L2": "plains", "L3": "mountain", "L4": "list"}
MIXED_START = 2008
COUNTY_EN = {
    "臺北市": "Taipei City",
    "高雄市": "Kaohsiung City",
    "臺北縣": "Taipei County",
    "新北市": "New Taipei City",
    "宜蘭縣": "Yilan County",
    "桃園縣": "Taoyuan County",
    "桃園市": "Taoyuan City",
    "新竹縣": "Hsinchu County",
    "新竹市": "Hsinchu City",
    "苗栗縣": "Miaoli County",
    "臺中縣": "Taichung County",
    "臺中市": "Taichung City",
    "彰化縣": "Changhua County",
    "南投縣": "Nantou County",
    "雲林縣": "Yunlin County",
    "嘉義縣": "Chiayi County",
    "嘉義市": "Chiayi City",
    "臺南縣": "Tainan County",
    "臺南市": "Tainan City",
    "高雄縣": "Kaohsiung County",
    "屏東縣": "Pingtung County",
    "臺東縣": "Taitung County",
    "花蓮縣": "Hualien County",
    "澎湖縣": "Penghu County",
    "基隆市": "Keelung City",
    "金門縣": "Kinmen County",
    "連江縣": "Lienchiang County",
}
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
TAIL = (
    ("有效票數", "valid"),
    ("無效票數", "invalid"),
    ("投票數", "cast"),
    ("選舉人數", "registered"),
)


@functools.cache
def themes() -> dict[str, dict[str, dict[str, object]]]:
    """Day -> contest type (L1 to L4) -> the Commission's theme entry for the legislative subject."""
    artifact = raw.fetch(SOURCE, LIST_URL, suffix=".json")
    found: dict[str, dict[str, dict[str, object]]] = {}
    for area in json.loads(artifact.path.read_text(encoding="utf-8")):
        for item in area["theme_items"]:
            found.setdefault(item["vote_date"], {})[item["legislator_type_id"]] = item
    return dict(sorted(found.items()))


def leg_days() -> list[str]:
    return list(themes())


def session(day: str) -> int:
    return int(str(next(iter(themes()[day].values()))["session"]))


def leg_bulk_url(day: str) -> str:
    group = str(next(iter(themes()[day].values()))["theme_group"])
    return f"{BASE}attachments/ELC/L0/{group}/{quote(LEGISLATIVE[session(day)])}"


def leg_json_url(kind: str, day: str, type_: str, level: str = "N", code: str = "00_000_00_000_0000") -> str:
    theme = themes()[day][type_]["theme_id"]
    return f"{BASE}{kind}/ELC/L0/{type_}/{theme}/{level}/{code}.json"


def township_level(day: str, type_: str) -> bool:
    """Whether the Commission publishes this contest by township only, with no polling-station workbook."""
    return type_ in ("L2", "L3") and session(day) <= 6


def county_files(day: str, type_: str) -> bool:
    """Whether the township figures come from the Commission's county files rather than a workbook."""
    return township_level(day, type_) and session(day) < 6


@functools.cache
def counties_of(day: str) -> list[str]:
    """The county codes (`prv_city_00_000_0000`) of a day's contests, from its county-level tickets."""
    url = leg_json_url("tickets", day, "L2", "C")
    data = json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text(encoding="utf-8"))
    rows = next(iter(data.values()))
    return sorted({f"{row['prv_code']}_{row['city_code']}_00_000_0000" for row in rows})


def leg_urls(day: str) -> list[str]:
    urls = [leg_bulk_url(day)]
    for type_ in themes()[day]:
        for kind in ("tickets", "profiles"):
            urls.append(leg_json_url(kind, day, type_, "N"))
    urls.append(leg_json_url("tickets", day, "L1", "C"))
    if county_files(day, "L2"):
        for type_ in ("L2", "L3"):
            urls.append(leg_json_url("tickets", day, type_, "C"))
            for kind in ("tickets", "profiles"):
                urls.extend(leg_json_url(kind, day, type_, "D", code) for code in counties_of(day))
    return urls


def fetch_legislative() -> None:
    for day in leg_days():
        for url in leg_urls(day):
            raw.fetch(SOURCE, url, suffix=Path(url.split("?")[0]).suffix)


@functools.cache
def official(
    kind: str, day: str, type_: str, level: str = "N", code: str = "00_000_00_000_0000"
) -> list[dict[str, object]]:
    data = json.loads(raw.cached(SOURCE, leg_json_url(kind, day, type_, level, code)).path.read_text(encoding="utf-8"))
    return list(next(iter(data.values())))


# --- reading the legislative workbooks


def archive_members(path: Path) -> list[tuple[str, bytes]]:
    """Every file of a .zip or .rar archive with its path inside the archive."""
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        found: list[tuple[str, bytes]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = (
                info.filename
                if info.flag_bits & 0x800
                else info.filename.encode("cp437").decode("cp950", errors="replace")
            )
            found.append((name, archive.read(info)))
        return found
    if shutil.which("unar") is None:
        raise RuntimeError("reading the .rar archives needs the `unar` command")
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(["unar", "-q", "-D", "-o", folder, str(path)], check=True, capture_output=True)
        root = Path(folder)
        return sorted((str(file.relative_to(root)), file.read_bytes()) for file in root.rglob("*") if file.is_file())


def sheets(name: str, data: bytes) -> list[tuple[str, list[list[object]]]]:
    if name.lower().endswith(".xlsx"):
        workbook = fastexcel.read_excel(data)
        found = []
        for sheet in workbook.sheet_names:
            frame = workbook.load_sheet(sheet, header_row=None).to_polars()
            found.append((sheet, [list(row) for row in frame.rows()]))
        return found
    book = xlrd.open_workbook(file_contents=data, encoding_override="cp950")
    return [(sheet.name, [sheet.row_values(r) for r in range(sheet.nrows)]) for sheet in book.sheets()]


def station_number(value: object) -> str:
    code = station_code(value)
    return str(int(float(code))) if re.fullmatch(r"\d+(\.0+)?", code) else code


def cjk(value: object) -> str:
    return re.sub(r"[^一-鿿]", "", str(value))


def similar(left: object, right: object) -> bool:
    """Whether two names are the same up to one variant character or a transliteration appended to one of them."""
    a, b = cjk(left), cjk(right)
    if a in b or b in a:
        return True
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) <= 1


def option_headers(rows: list[list[object]], first: int, start: int, stop: int) -> list[tuple[int, str, str]]:
    """The ballot number, name, and party the header rows give each option column."""
    options = []
    for column in range(start, stop):
        parts: list[str] = []
        for row in rows[2:first]:
            cell = row[column] if column < len(row) else None
            if isinstance(cell, float) and cell.is_integer():
                cell = int(cell)
            pieces = str(cell if cell is not None else "").split("\n")
            parts.extend(part for part in (text(piece) for piece in pieces) if part)
        found = re.search(r"\d+", parts[0]) if parts else None
        if found is None:
            raise ValueError(f"no ballot number above column {column}")
        rest = parts[1:]
        options.append((int(found.group()), rest[0] if rest else "", rest[-1] if len(rest) > 1 else ""))
    return options


def modern_table(rows: list[list[object]]) -> tuple[str, list[tuple[int, str, str]], list[dict[str, object]]]:
    """A 2008 to 2024 workbook sheet: options across the top, then a total row, townships, and polling stations."""
    title = text(rows[0][0])
    first = next(i for i, row in enumerate(rows) if text(row[0]) in ("總計", "合計"))
    columns: dict[str, int] = {}
    for index, label in enumerate(rows[1]):
        for prefix, field in TAIL:
            if text(label).startswith(prefix) and field not in columns:
                columns[field] = index
    options = option_headers(rows, first, 3, columns["valid"])
    out: list[dict[str, object]] = []
    township = ""
    for row in rows[first + 1 :]:
        label, village, code = text(row[0]), text(row[1]), station_number(row[2])
        if village and code:
            if not township:
                raise ValueError(f"{title}: a station row comes before any township")
            out.append(
                {
                    "township": township,
                    "village": village,
                    "station": code,
                    "votes": [number(row[3 + index]) for index in range(len(options))],
                    **{field: number(row[columns[field]]) for field in ("valid", "invalid", "cast", "registered")},
                }
            )
        elif label and not village and number(row[3]) is not None:
            township = label
    return title, options, out


def old_table(rows: list[list[object]]) -> tuple[list[tuple[int, str, str]], list[dict[str, object]]]:
    """A 1995 to 2004 workbook sheet: a column pair (votes, share) per candidate, then the accounting columns."""
    head = rows[1]
    start = next(index for index, label in enumerate(head) if "候選人得票情形" in text(label))
    township_at = next(index for index, label in enumerate(head) if text(label).startswith("鄉鎮"))
    by_township = start == township_at + 1
    numbers, names = rows[2], rows[3]
    options: list[tuple[int, str, str]] = []
    column = start
    while column < len(numbers) and number(numbers[column]) is not None:
        options.append((int(number(numbers[column]) or 0), text(names[column]), ""))
        column += 2
    out: list[dict[str, object]] = []
    for row in rows[5:]:
        county, township = text(row[0]), text(row[township_at])
        village, code = ("", "") if by_township else (text(row[start - 2]), station_number(row[start - 1]))
        if not (county and township and (village or by_township)) or number(row[start]) is None:
            continue
        out.append(
            {
                "county": county,
                "township": township,
                "village": village,
                "station": code,
                "votes": [number(row[start + 2 * index]) for index in range(len(options))],
                "valid": number(row[column]),
                "invalid": number(row[column + 1]),
                "cast": number(row[column + 2]),
                "registered": number(row[column + 4]),
            }
        )
    return options, out


class Table:
    """One contest's rows from the workbooks: the ballot options and one row per reporting unit."""

    def __init__(
        self,
        type_: str,
        county: str | None,
        district: int | None,
        options: list[tuple[int, str, str]],
        rows: list[dict[str, object]],
    ) -> None:
        self.type_ = type_
        self.county = county
        self.district = district
        self.options = options
        self.rows = rows

    def column(self, index: int) -> list[int | None]:
        """The votes of the option at this position, one per unit."""
        return [cast(list[int | None], row["votes"])[index] for row in self.rows]


TITLE = re.compile(r"(?:區域|委員|民)選舉(.+?)(?:候選人|各政黨)在")
LIST_TITLE = re.compile(r"(?:候選人|各政黨)在(.+?)各投開票所")


def modern_kind(number_: int, base: str) -> str | None:
    if not base.lower().endswith((".xls", ".xlsx")):
        return None
    if number_ == 7:
        return {"p07_T1": "L1", "p08_T2": "L2", "p08_T3": "L3", "p10_T4": "L4"}.get(base[:6])
    if "A05-2V" in base:
        return None
    if "A05-2" in base:
        return "L1"
    if "A05-4" in base:
        return "L3" if "山地" in base else "L2" if "平地" in base else None
    if "A05-6" in base:
        return "L4"
    return None


def split_title(title: str) -> tuple[str, int]:
    found = TITLE.search(title) or LIST_TITLE.search(title)
    if found is None:
        raise ValueError(f"no county in the title {title!r}")
    district = re.search(r"第(\d+)選舉區$", found.group(1))
    county = re.sub(r"(第\d+)?選舉區$", "", found.group(1))
    return county, int(district.group(1)) if district else 1


def old_sheet(name: str) -> tuple[str, str, int | None]:
    if name in ("平地原住民", "山地原住民"):
        return ("L2" if name.startswith("平地") else "L3"), "", None
    found = re.fullmatch(r"(.+?[縣市])(?:第(.)選區)?", name)
    if found is None:
        raise ValueError(f"unexpected sheet {name!r}")
    return "L1", found.group(1), NUMERALS[found.group(2)] if found.group(2) else 1


def merge(type_: str, tables: list[Table]) -> Table:
    first = tables[0]
    for table in tables[1:]:
        if [(n, cjk(party or name)) for n, name, party in table.options] != [
            (n, cjk(party or name)) for n, name, party in first.options
        ]:
            raise ValueError(f"{type_}: the county files list different options")
    return Table(type_, None, None, first.options, [row for table in tables for row in table.rows])


@functools.cache
def leg_members(day: str) -> list[tuple[str, bytes]]:
    return archive_members(raw.cached(SOURCE, leg_bulk_url(day)).path)


def official_options(day: str, type_: str) -> list[tuple[int, str, str]]:
    return [
        (int(str(row["cand_no"])), str(row.get("cand_name", "")), str(row["party_name"]))
        for row in sorted(official("tickets", day, type_), key=lambda row: int(str(row["cand_no"])))
    ]


def township_table(day: str, type_: str) -> Table:
    """Contests the Commission publishes by township only: one row per township, from the county files."""
    names = {
        f"{row['prv_code']}_{row['city_code']}_00_000_0000": str(row["area_name"])
        for row in official("tickets", day, type_, "C")
    }
    options = official_options(day, type_)
    rows: list[dict[str, object]] = []
    for code in counties_of(day):
        profile = {str(row["area_name"]): row for row in official("profiles", day, type_, "D", code)}
        votes: dict[str, dict[int, int]] = {}
        for row in official("tickets", day, type_, "D", code):
            votes.setdefault(str(row["area_name"]), {})[int(str(row["cand_no"]))] = int(str(row["ticket_num"]))
        for township, found in votes.items():
            figures = profile[township]
            rows.append(
                {
                    "county": names[code],
                    "township": township,
                    "village": "",
                    "station": "",
                    "votes": [found.get(no) for no, _, _ in options],
                    "valid": int(str(figures["valid_ticket"])),
                    "invalid": int(str(figures["invalid_ticket"])),
                    "cast": int(str(figures["vote_ticket"])),
                    "registered": int(str(figures["votable_population"])),
                }
            )
    return Table(type_, None, None, options, rows)


@functools.cache
def leg_tables(day: str) -> dict[tuple[str, str | None, int | None], Table]:
    """Every contest of the day: (type, county, district) for districts, (type, None, None) for national contests."""
    number_ = session(day)
    found: dict[tuple[str, str | None, int | None], Table] = {}
    national: dict[str, list[Table]] = {}
    for name, data in leg_members(day):
        base = name.rsplit("/", 1)[-1]
        if number_ < 7:
            if not base.lower().endswith(".xls"):
                continue
            for sheet, rows in sheets(name, data):
                type_, county, district = old_sheet(sheet)
                options, units = old_table(rows)
                if type_ == "L1":
                    found[(type_, county, district)] = Table(type_, county, district, options, units)
                else:
                    national.setdefault(type_, []).append(Table(type_, None, None, options, units))
            continue
        type_ = modern_kind(number_, base)
        if type_ is None:
            continue
        for _, rows in sheets(name, data):
            title, options, units = modern_table(rows)
            county, district = split_title(title)
            for unit in units:
                unit["county"] = county
            if type_ == "L1":
                found[(type_, county, district)] = Table(type_, county, district, options, units)
            else:
                national.setdefault(type_, []).append(Table(type_, None, None, options, units))
    for type_ in themes()[day]:
        if county_files(day, type_):
            found[(type_, None, None)] = township_table(day, type_)
        elif type_ != "L1":
            found[(type_, None, None)] = merge(type_, national[type_])
    return found


# --- legislative packages

LEGISLATURE = key("body", "legislative-yuan")
SNTV_DISTRICT = key("electoral_system", "legislature", "district", "sntv")
FPTP_DISTRICT = key("electoral_system", "legislature", "district", "fptp")
INDIGENOUS = key("electoral_system", "legislature", "indigenous")
PARTY_LIST = key("electoral_system", "legislature", "party-list")
LEG_START = date(1992, 1, 1)
MIXED_FROM = date(2008, 1, 1)
LEG_METHOD = {
    "sntv": (
        "Each voter casts one vote for one candidate in the district. The candidates with the most votes fill the "
        "district's seats, so the number of seats elected varies by district."
    ),
    "fptp": "Each voter casts one vote for one candidate in the district. The candidate with the most votes wins the seat.",
    "indigenous": (
        "Voters registered as plains or as mountain indigenous people each cast one vote for one candidate in a "
        "nationwide constituency of their group. The candidates with the most votes fill the group's seats."
    ),
    "list": (
        "Each voter casts one vote for one party's list. Parties with at least five percent of the valid votes "
        "share the seats by largest remainder on the Hare quota."
    ),
}
LEG_NOTE = (
    "Valid votes for each {what} and the valid and invalid ballots, ballots cast, and registered voters at each "
    "{unit}, as published by the Central Election Commission. Voters registered is the number of voters on the "
    "{unit}'s roll. Official totals are the Commission's own."
)
PARTY_EN = {
    "台灣團結聯盟": "Taiwan Solidarity Union",
    "時代力量": "New Power Party",
    "綠黨": "Green Party Taiwan",
    "建國黨": "Taiwan Independence Party",
    "民國黨": "Republic Party",
    "無黨團結聯盟": "Non-Partisan Solidarity Union",
}
LEG_NAMES = {
    "L2": ("Plains indigenous seats", "平地原住民"),
    "L3": ("Mountain indigenous seats", "山地原住民"),
    "L4": ("National party list", "全國不分區及僑居國外國民"),
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def leg_system(day: str, type_: str) -> str:
    if type_ == "L1":
        return FPTP_DISTRICT if date.fromisoformat(day) >= MIXED_FROM else SNTV_DISTRICT
    return PARTY_LIST if type_ == "L4" else INDIGENOUS


def leg_parties() -> set[str]:
    return {
        str(row["party_name"])
        for day in leg_days()
        for type_ in themes()[day]
        for row in official("tickets", day, type_, "N" if type_ != "L1" else "C")
    }


def leg_artifacts(day: str) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in leg_urls(day)]


def leg_station_rows(day: str) -> pl.DataFrame:
    """One row per reporting unit of a day, identified by county, township, and station number. The village is the
    one the district and party list files give, which name merged villages in full."""
    rank = {"L1": 0, "L4": 1, "L2": 2, "L3": 3}
    found: dict[tuple[str, str, str], dict[str, object]] = {}
    for (type_, _, _), table in sorted(leg_tables(day).items(), key=lambda item: rank[item[0][0]]):
        for row in table.rows:
            found.setdefault(
                (str(row["county"]), str(row["township"]), str(row["station"])),
                {
                    "county": row["county"],
                    "township": row["township"],
                    "village": row["village"],
                    "station": row["station"],
                },
            )
    return pl.DataFrame(list(found.values()))


def leg_unit_code(day: str, county: object, township: object, station: object) -> str:
    return f"leg:{day}:{county}:{township}:{station}"


def leg_units(day: str) -> pl.DataFrame:
    return leg_station_rows(day).with_columns(
        code=pl.format("leg:{}:{}:{}:{}", pl.lit(day), "county", "township", "station")
    )


def legislative(day: str) -> Package:
    year = day[:4]
    number_ = session(day)
    when = date.fromisoformat(day)
    p = Package(
        f"tw.cec/leg-{day}",
        SOURCE,
        f"The {year} Taiwanese legislative election by polling station from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for artifact in leg_artifacts(day):
        p.uses(artifact)
    election = key("election", f"leg-{day}")
    path = f"tw/{day}-legislative"
    p.add("election", id=election, polity_id=TAIWAN, kind="general", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Taiwanese legislative election", "en", "official")
    p.name(election, f"第{number_}屆立法委員選舉", "zh", "native", False)
    units = leg_units(day)
    unit_rows = units.select(
        id=pl.col("code").map_elements(lambda code: key("unit", code), return_dtype=pl.String),
        polity_id=pl.lit(TAIWAN),
        scheme_code=pl.when(pl.col("station") == "").then(pl.lit("tw.township")).otherwise(pl.lit("tw.station")),
        code="code",
        area_relation=pl.when(pl.col("station") == "").then(pl.lit("equals")).otherwise(pl.lit("within")),
        area_id=pl.struct("county", "township", "village", "station").map_elements(
            lambda row: (
                area("tw.township", row["county"], row["township"])
                if row["station"] == ""
                else area("tw.village", row["county"], row["township"], row["village"])
            ),
            return_dtype=pl.String,
        ),
        kind=pl.when(pl.col("station") == "").then(pl.lit("aggregate")).otherwise(pl.lit("standard")),
    )
    p.frame("reporting_unit", unit_rows)
    unit_of = dict(zip(units["code"], unit_rows["id"], strict=True))
    if len(unit_of) != units.height:
        raise ValueError(f"{day}: polling station codes repeat")
    tables = leg_tables(day)
    seats_by_district: dict[str, int] = {}
    for (type_, county, district), table in tables.items():
        if type_ == "L1":
            assert county is not None and district is not None
            seats_by_district[county] = max(seats_by_district.get(county, 0), district)
    values: list[pl.DataFrame] = []
    differences: list[str] = []
    for (type_, county, district), table in tables.items():
        values.extend(
            leg_contest(p, day, election, path, type_, county, district, table, seats_by_district, unit_of, differences)
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published totals differ from its own polling-station figures. " + "; ".join(differences),
        )
        p.add(
            "note",
            id=key("note", "differences", f"leg-{day}"),
            entity_id=election,
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit-level figures in this election. "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    facts = pl.concat(values).filter(pl.col("value").is_not_null() & (pl.col("value") >= 0))
    p.frame(
        "result_value",
        facts.select(
            result_set_id="result_set_id",
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


def unit_name(day: str, type_: str) -> str:
    return "township" if township_level(day, type_) else "polling station"


def dedupe(rows: list[dict[str, object]], table: Table) -> list[dict[str, object]]:
    """One official row per ballot number. Where the Commission's county file repeats a candidate with other totals,
    the row whose total matches the polling-station sum is the one it means."""
    sums = {no: sum(value or 0 for value in table.column(index)) for index, (no, _, _) in enumerate(table.options)}
    chosen: dict[int, dict[str, object]] = {}
    for row in rows:
        no = int(str(row["cand_no"]))
        if no not in chosen or (
            int(str(chosen[no]["ticket_num"])) != sums.get(no) and int(str(row["ticket_num"])) == sums.get(no)
        ):
            chosen[no] = row
    return [chosen[no] for no in sorted(chosen)]


def leg_contest(
    p: Package,
    day: str,
    election: str,
    path: str,
    type_: str,
    county: str | None,
    district: int | None,
    table: Table,
    districts: dict[str, int],
    unit_of: dict[str, str],
    differences: list[str],
) -> list[pl.DataFrame]:
    year = day[:4]
    tag = f"{type_}:{county or ''}:{district or ''}"
    contest = key("contest", f"leg-{day}", tag)
    result_set = key("result_set", f"leg-{day}", tag)
    if type_ == "L1":
        assert county is not None and district is not None
        many = districts[county] > 1
        suffix = f" {district}" if many else ""
        english = f"{COUNTY_EN[county]}{suffix}"
        native = f"{county}第{district}選舉區" if many else f"{county}選舉區"
        slug = slugify(COUNTY_EN[county]) + (f"-{district}" if many else "")
        area_id = key("area", "tw.district", f"{session(day)}|{county}|{district}")
        official_rows = [
            row
            for row in official("tickets", day, "L1", "C")
            if row["area_name"] == county and int(str(row["ori_area_code"])) == district
        ]
        official_rows = dedupe(official_rows, table)
        seats = sum(1 for row in official_rows if row["is_victor"] == "*")
    else:
        english, native = LEG_NAMES[type_]
        slug = {"L2": "plains-indigenous", "L3": "mountain-indigenous", "L4": "party-list"}[type_]
        area_id = NATION
        official_rows = official("tickets", day, type_)
        seats = (
            sum(int(str(row["elected_num"])) for row in official_rows)
            if type_ == "L4"
            else sum(1 for row in official_rows if row["is_victor"] == "*")
        )
    listed = type_ == "L4"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation" if listed else "body_seats",
        body_id=LEGISLATURE,
        district_area_id=area_id,
        magnitude=seats,
        round_number=1,
        electoral_system_id=leg_system(day, type_),
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Taiwanese legislative election, {english}", "en", "official")
    p.name(contest, native, "zh", "native", False)
    p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
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
    unit = unit_name(day, type_)
    what = "party list" if listed else "candidate"
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=LEG_NOTE.format(what=what, unit=unit),
        lang="en",
    )
    by_no = {int(str(row["cand_no"])): row for row in official_rows}
    options: list[str] = []
    for index, (no, name, party) in enumerate(table.options):
        row = by_no.get(no)
        if row is None:
            raise ValueError(f"{day} {tag}: ballot number {no} is not in the official tickets")
        if listed:
            if not similar(row["party_name"], party):
                raise ValueError(
                    f"{day} {tag}: option {no} is {row['party_name']} in the tickets and {party} in the file"
                )
        elif not similar(row["cand_name"], name) and sum(value or 0 for value in table.column(index)) != int(
            str(row["ticket_num"])
        ):
            raise ValueError(f"{day} {tag}: option {no} is {row['cand_name']} in the tickets and {name} in the file")
        party_name = str(row["party_name"])
        independent = party_name in INDEPENDENT
        party_id = None if independent else key("party", party_name)
        option = key("option", f"leg-{day}", tag, no)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list" if listed else "candidate",
            party_id=party_id,
            ballot_position=no,
            ballot_label=party_name if listed else str(row["cand_name"]),
        )
        if listed:
            seats_won = int(str(row["elected_num"]))
            if seats_won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats_won)
        else:
            person = key("person", f"leg-{day}", tag, no)
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
                id=key("candidacy", f"leg-{day}", tag, no),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=str(row["cand_name"]),
                member_of_party_id=party_id,
                is_independent=independent,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if row["is_victor"] == "*" else "lost",
            )
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=int(str(row["ticket_num"])))
        added = sum(value or 0 for value in table.column(index))
        if added != int(str(row["ticket_num"])):
            who = str(row["party_name"] if listed else row["cand_name"])
            missing = sum(1 for value in table.column(index) if value is None)
            gap = (
                f"; {missing} cell{'s' if missing > 1 else ''} for this {what} {'are' if missing > 1 else 'is'} blank "
                "in the Commission's workbook"
                if missing
                else ""
            )
            differences.append(
                f"{english}: the {unit_name(day, type_)} figures for {who} add up to {added}, and the Commission's "
                f"total is {row['ticket_num']}{gap}"
            )
    if type_ != "L1":
        total = official("profiles", day, type_)[0]
        for measure, field, column in (
            (4, "valid_ticket", "valid"),
            (5, "invalid_ticket", "invalid"),
            (3, "vote_ticket", "cast"),
            (2, "votable_population", "registered"),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=int(str(total[field])))
            added = sum(int(str(unit[column] or 0)) for unit in table.rows)
            if added != int(str(total[field])):
                differences.append(
                    f"{english}: the {unit_name(day, type_)} figures add up to {added} for {column} ballots, and the "
                    f"Commission's total is {total[field]}"
                )
    frame = pl.DataFrame(table.rows).with_columns(
        unit_id=pl.struct("county", "township", "station").map_elements(
            lambda row: unit_of[leg_unit_code(day, row["county"], row["township"], row["station"])],
            return_dtype=pl.String,
        )
    )
    out: list[pl.DataFrame] = []
    for index, option in enumerate(options):
        out.append(
            frame.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.lit(option),
                measure=pl.lit(1),
                value=pl.col("votes").list.get(index),
            )
        )
    for measure, column in ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid")):
        out.append(
            frame.select(
                result_set_id=pl.lit(result_set),
                unit_id="unit_id",
                option_id=pl.lit(None, pl.String),
                measure=pl.lit(measure),
                value=column,
            )
        )
    return out


# --- local executives: special municipality mayors and county magistrates and city mayors


@dataclass(frozen=True)
class Theme:
    day: str
    subject: str  # C1 for special municipality mayors, C2 for county magistrates and city mayors
    theme: str
    layout: str  # std, y2005, text2006, village, or township
    files: tuple[str, ...]  # bulk workbooks or archives under attachments/
    special: bool = False


THEMES: tuple[Theme, ...] = (
    Theme("2022-11-26", "C1", "05cc7b904c7a30cc7c88d5b10898c98e", "std", ("直轄市長.zip",)),
    Theme("2022-11-26", "C2", "63615098f5afa8ec53159c4a86fc01d3", "std", ("縣市長.zip",)),
    Theme(
        "2022-12-18",
        "C2",
        "1275d73551f2c0caf202e803b1766057",
        "std",
        ("表8-嘉義市第11屆市長重行選舉各投開票所得票數一覽表 .xls",),
        True,
    ),
    Theme("2018-11-24", "C1", "a0222f6529a3ff2192a1a04f209eaac3", "std", ("直轄市長.zip",)),
    Theme("2018-11-24", "C2", "f75e94cad2958f8764f9d090e72b5567", "std", ("縣市長.zip",)),
    Theme("2014-11-29", "C1", "45ca615ee3ff7b12238c79783524bd8f", "std", ("直轄市長.zip",)),
    Theme("2014-11-29", "C2", "4d5371ae79267a6fd9bb78199c636cfb", "std", ("縣市長.zip",)),
    Theme("2010-11-27", "C1", "e613c42ee1eddb1fc8c7a75a420ef033", "std", ("2010直轄市長選舉得票概況.rar",)),
    Theme("2009-12-05", "C2", "ca9356e2d1cdde43ef4f7115f4898bf5", "std", ("2009縣市長選舉得票概況.rar",)),
    Theme(
        "2006-12-09",
        "C1",
        "d2ce132760640611c410338a3136e810",
        "text2006",
        ("第04屆台北市長選舉得票概況.rar", "第04屆高雄市長選舉得票概況.rar"),
    ),
    Theme("2005-12-03", "C2", "0da76d11a1f55dded2bbc358b26cd293", "y2005", ("2005縣市長選舉得票概況.rar",)),
    Theme("2002-12-07", "C1", "a31daef43bd42a9613254e2d3d18fdbd", "village", ()),
    Theme("2001-12-01", "C2", "333346bf02c9949349a6735cebaddf4f", "township", ()),
    Theme("1998-12-05", "C1", "1cc537ea416941769d8e715a4c37ee6b", "village", ()),
    Theme("1997-11-29", "C2", "8a062c6eb5089dceb912a02713b136d8", "township", ()),
    Theme("1994-12-03", "C1", "81714e9f8f0072a65451bac5810796fa", "village", ()),
)
LOCAL_DAYS = sorted({theme.day for theme in THEMES}, reverse=True)
CITIES = {
    "臺北市": "Taipei City",
    "新北市": "New Taipei City",
    "桃園市": "Taoyuan City",
    "臺中市": "Taichung City",
    "臺南市": "Tainan City",
    "高雄市": "Kaohsiung City",
    "基隆市": "Keelung City",
    "新竹市": "Hsinchu City",
    "嘉義市": "Chiayi City",
}
COUNTIES = {
    "臺北縣": "Taipei County",
    "桃園縣": "Taoyuan County",
    "新竹縣": "Hsinchu County",
    "苗栗縣": "Miaoli County",
    "臺中縣": "Taichung County",
    "彰化縣": "Changhua County",
    "南投縣": "Nantou County",
    "雲林縣": "Yunlin County",
    "嘉義縣": "Chiayi County",
    "臺南縣": "Tainan County",
    "高雄縣": "Kaohsiung County",
    "屏東縣": "Pingtung County",
    "宜蘭縣": "Yilan County",
    "花蓮縣": "Hualien County",
    "臺東縣": "Taitung County",
    "澎湖縣": "Penghu County",
    "金門縣": "Kinmen County",
    "連江縣": "Lienchiang County",
}
PLACES = CITIES | COUNTIES
PARTIES |= {
    "台灣團結聯盟": "Taiwan Solidarity Union",
    "建國黨": "Taiwan Independence Party",
    "無黨團結聯盟": "Non-Partisan Solidarity Union",
    "綠黨": "Green Party Taiwan",
    "中華統一促進黨": "Chinese Unification Promotion Party",
    "時代力量": "New Power Party",
    "社會民主黨": "Social Democratic Party",
}
LOCAL_SYSTEM_START = date(1994, 1, 1)
LOCAL_METHOD = (
    "Voters choose one candidate for the chief executive of the municipality, county, or city. The candidate with the "
    "most votes in the one count is elected, with no runoff."
)
LOCAL_NOTE = (
    "Valid votes for each candidate, valid and invalid ballots, ballots cast, and registered voters at each {unit}, "
    "as published by the Central Election Commission. Voters registered is the number of voters on the roll. The "
    "totals for the contest are the Commission's own."
)
GRAIN = {
    "std": "polling station",
    "y2005": "polling station",
    "text2006": "polling station",
    "village": "village",
    "township": "township or district",
}


def attachment_url(theme: Theme, name: str) -> str:
    return f"{BASE}attachments/ELC/{theme.subject}/{theme.theme}/{quote(name)}"


def table_url(kind: str, theme: Theme, level: str, name: str) -> str:
    return f"{BASE}{kind}/ELC/{theme.subject}/00/{theme.theme}/{level}/{name}.json"


def old_cities(theme: Theme) -> list[tuple[str, str]]:
    """The (province code, city code, name) areas of a theme with no workbooks, read from its area list."""
    data = json.loads(raw.cached(SOURCE, table_url("areas", theme, "C", "00_000_00_000_0000")).path.read_text("utf-8"))
    return [(f"{row['prv_code']}_{row['city_code']}", str(row["area_name"])) for row in next(iter(data.values()))]


def local_urls(theme: Theme) -> list[str]:
    urls = [attachment_url(theme, name) for name in theme.files]
    if theme.layout in ("village", "township"):
        urls.append(table_url("areas", theme, "C", "00_000_00_000_0000"))
    if theme.layout != "village":
        urls += [table_url(kind, theme, "C", "00_000_00_000_0000") for kind in ("tickets", "profiles")]
    return urls


def local_urls_after_areas(theme: Theme) -> list[str]:
    urls: list[str] = []
    if theme.layout in ("village", "township"):
        for prefix, _ in old_cities(theme):
            for kind in ("tickets", "profiles"):
                urls.append(table_url(kind, theme, "D", f"{prefix}_00_000_0000"))
                if theme.layout == "village":
                    urls.append(table_url(kind, theme, "L", f"{prefix}_00_000_0000"))
    return urls


def local_fetch() -> None:
    for theme in THEMES:
        for url in local_urls(theme):
            suffix = ".json" if url.endswith(".json") else url[url.rindex(".") :]
            raw.fetch(SOURCE, url, suffix=suffix)
        for url in local_urls_after_areas(theme):
            raw.fetch(SOURCE, url, suffix=".json")


def local_artifacts(theme: Theme) -> list[str]:
    return [*local_urls(theme), *local_urls_after_areas(theme)]


# --- reading the local executive files


def decoded(name: str | bytes) -> str:
    return name.decode("cp950", errors="replace") if isinstance(name, bytes) else name


def local_members(url: str) -> list[tuple[str, bytes]]:
    """The files in one downloaded workbook, zip, or rar, with their names decoded."""
    path = raw.cached(SOURCE, url).path
    if url.lower().endswith(".xls"):
        return [(url.rsplit("/", 1)[-1], path.read_bytes())]
    found: list[tuple[str, bytes]] = []
    if url.lower().endswith(".zip"):
        archive = zipfile.ZipFile(path)
        for info in archive.infolist():
            if not info.is_dir():
                name = info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("cp950")
                found.append((name.rsplit("/", 1)[-1], archive.read(info)))
        return found
    with libarchive.file_reader(str(path)) as entries:
        for entry in entries:
            if not entry.isdir:
                found.append((decoded(entry.pathname).rsplit("/", 1)[-1], b"".join(entry.get_blocks())))
    return found


def serial(value: object) -> str:
    """A station or village number as a string without leading zeros or a decimal point."""
    if isinstance(value, float):
        return str(int(value))
    digits = text(value)
    return str(int(digits)) if digits.isdigit() else digits


def header_names(cells: list[object]) -> list[str]:
    """The candidate names in a header row, whether a cell holds a number, a name, and a party, or only a name."""
    names: list[str] = []
    for cell in cells:
        parts = [part for part in str(cell).split("\n") if part.strip()]
        names.append(text(parts[1] if len(parts) >= 2 else parts[0]))
    return names


@dataclass
class LocalTable:
    county: str
    names: list[str]
    rows: list[dict[str, object]]


def std_table(name: str, data: bytes) -> LocalTable:
    _, grid_rows = grid(name, data)
    head = next(index for index, row in enumerate(grid_rows) if any("有效票數" in str(cell) for cell in row))
    end = next(index for index, cell in enumerate(grid_rows[head]) if "有效票數" in str(cell))
    labels = grid_rows[head + 1][3:end]
    if all("\n" in str(cell) for cell in labels if str(cell).strip()):
        names, first = header_names(labels), head + 2
    else:
        names, first = header_names(grid_rows[head + 2][3:end]), head + 3
    found = re.search(r"\((.+?)\)", name)
    if found:
        county = found.group(1)
    else:
        title = next(text(row[0]) for row in grid_rows if re.search(r"[縣市]", text(row[0])) and len(text(row[0])) > 6)
        matched = re.match(r"(?:\d+年)?(.+?[縣市])", title)
        if matched is None:
            raise ValueError(f"{name}: no county in the title {title}")
        county = matched.group(1)
    rows: list[dict[str, object]] = []
    district = ""
    for row in grid_rows[first:]:
        label, village, station = text(row[0]), text(row[1]), serial(row[2])
        if station:
            if not district:
                raise ValueError(f"{name}: a station row comes before any district")
            rows.append(
                {
                    "district": district,
                    "village": village,
                    "unit": station,
                    "votes": [number(row[3 + index]) for index in range(len(names))],
                    "valid": number(row[end]),
                    "invalid": number(row[end + 1]),
                    "cast": number(row[end + 2]),
                    "registered": number(row[end + 6]),
                }
            )
        elif label and not village and "總" not in label and number(row[3]) is not None:
            district = label
    return LocalTable(county, names, rows)


def y2005_table(name: str, data: bytes) -> LocalTable:
    _, grid_rows = grid(name, data)
    end = next(index for index, cell in enumerate(grid_rows[1]) if "有效票數" in str(cell))
    names = [text(str(cell).replace("得票數", "")) for cell in grid_rows[1][4:end:2]]
    rows: list[dict[str, object]] = []
    county = ""
    for row in grid_rows[2:]:
        if not (text(row[1]) and number(row[3]) is not None):
            continue
        county = county or text(row[0])
        rows.append(
            {
                "district": text(row[1]),
                "village": text(row[2]),
                "unit": serial(row[3]),
                "votes": [number(row[4 + 2 * index]) for index in range(len(names))],
                "valid": number(row[end]),
                "invalid": number(row[end + 1]),
                "cast": number(row[end + 2]),
                "registered": number(row[end + 6]),
            }
        )
    return LocalTable(county, names, rows)


def text2006_table(county: str, name: str, data: bytes) -> LocalTable:
    lines = [line.split("\t") for line in data.decode("cp950", errors="replace").splitlines() if line.strip()]
    head = next(index for index, cells in enumerate(lines) if any("有效票數" in cell for cell in cells))
    end = next(index for index, cell in enumerate(lines[head]) if "有效票數" in cell)
    names = [text(cell) for cell in lines[head][3:end]]
    rows: list[dict[str, object]] = []
    district = ""
    for cells in lines[head + 1 :]:
        cells = [*cells, *([""] * (end + 7 - len(cells)))]
        if text(cells[0]) and "總" not in text(cells[0]):
            district = text(cells[0])
        if "總" in text(cells[0]) or not text(cells[2]).isdigit():
            continue
        rows.append(
            {
                "district": district,
                "village": text(cells[1]),
                "unit": serial(cells[2]),
                "votes": [number(cells[3 + index]) for index in range(len(names))],
                "valid": number(cells[end]),
                "invalid": number(cells[end + 1]),
                "cast": number(cells[end + 2]),
                "registered": number(cells[end + 6]),
            }
        )
    return LocalTable(county, names, rows)


@functools.cache
def workbook_tables(day: str, subject: str) -> dict[str, LocalTable]:
    (theme,) = [t for t in THEMES if (t.day, t.subject) == (day, subject)]
    tables: dict[str, LocalTable] = {}
    for file in theme.files:
        url = attachment_url(theme, file)
        for name, data in local_members(url):
            if theme.layout == "text2006":
                if "台北" in file and re.fullmatch(r"\w+區\.xls", name):
                    table = text2006_table("臺北市", name, data)
                elif "高雄" in file and "各投開票所開票結果表" in name:
                    table = text2006_table("高雄市", name, data)
                else:
                    continue
                if table.county in tables:
                    holder = tables[table.county]
                    if holder.names != table.names:
                        raise ValueError(f"{name}: the candidates differ between districts")
                    holder.rows.extend(table.rows)
                else:
                    tables[table.county] = table
                continue
            if not name.lower().endswith(".xls"):
                continue
            if theme.layout == "std":
                table = std_table(name, data)
            else:
                table = y2005_table(name, data)
            if table.county in tables:
                raise ValueError(f"{theme.day} {subject}: two files for {table.county}")
            tables[table.county] = table
    return tables


@dataclass
class Cand:
    no: int
    name: str
    sex: str | None
    birth: str | None
    party: str
    votes: int
    won: bool
    incumbent: bool | None


@dataclass
class Contest:
    county: str
    cands: list[Cand]
    totals: dict[int, int]  # measure -> the Commission's total
    rows: list[dict[str, object]]


def load(url: str) -> dict[str, list[dict[str, object]]]:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))


def cand(row: dict[str, object]) -> Cand:
    sex, birth = str(row.get("cand_sex") or "").strip(), str(row.get("cand_birthyear") or "").strip()
    return Cand(
        no=int(str(row["cand_no"])),
        name=text(row["cand_name"]),
        sex={"1": "male", "2": "female"}.get(sex),
        birth=birth if birth.isdigit() else None,
        party=str(row["party_name"]).strip(),
        votes=int(str(row["ticket_num"])),
        won=row["is_victor"] == "*",
        incumbent={"Y": True, "N": False}.get(str(row.get("is_current"))),
    )


def totals(row: dict[str, object]) -> dict[int, int]:
    return {
        2: int(str(row["votable_population"])),
        3: int(str(row["vote_ticket"])),
        4: int(str(row["valid_ticket"])),
        5: int(str(row["invalid_ticket"])),
    }


def plain(name: str) -> str:
    """A name with a character the Commission could not encode (a private-use glyph or an @code@ placeholder) as ?."""
    return re.sub(r"@[0-9A-F]+@|[\ue000-\uf8ff]", "?", name)


def same_name(left: str, right: str) -> bool:
    a, b = plain(left), plain(right)
    return len(a) == len(b) and all(x == y or "?" in (x, y) for x, y in zip(a, b, strict=True))


def check_names(county: str, table: list[str], cands: list[Cand]) -> None:
    if len(table) != len(cands) or not all(same_name(x, c.name) for x, c in zip(table, cands, strict=True)):
        raise ValueError(f"{county}: the workbook lists {table} but the candidate list has {[c.name for c in cands]}")


def stations_contests(theme: Theme) -> list[Contest]:
    tickets = load(table_url("tickets", theme, "C", "00_000_00_000_0000"))
    profiles = load(table_url("profiles", theme, "C", "00_000_00_000_0000"))
    by_county: dict[str, list[Cand]] = {}
    for row in next(iter(tickets.values())):
        by_county.setdefault(text(row["area_name"]), []).append(cand(row))
    sums = {text(row["area_name"]): totals(row) for row in next(iter(profiles.values()))}
    out: list[Contest] = []
    for county, table in workbook_tables(theme.day, theme.subject).items():
        cands = sorted(by_county.pop(county), key=lambda c: c.no)
        check_names(county, table.names, cands)
        out.append(Contest(county, cands, sums.pop(county), table.rows))
    if by_county:
        raise ValueError(f"{theme.day} {theme.subject}: no workbook for {sorted(by_county)}")
    return out


def rows_of(url: str) -> list[dict[str, object]]:
    return [row for group in load(url).values() for row in group]


def old_contests(theme: Theme) -> list[Contest]:
    """Village rows for the early special municipality mayors and township rows for the early county magistrates."""
    villages = theme.layout == "village"
    if not villages:
        whole = {
            text(row["area_name"]): row for row in rows_of(table_url("profiles", theme, "C", "00_000_00_000_0000"))
        }
        whole_cands: dict[str, list[Cand]] = {}
        for row in rows_of(table_url("tickets", theme, "C", "00_000_00_000_0000")):
            whole_cands.setdefault(text(row["area_name"]), []).append(cand(row))
    out: list[Contest] = []
    for prefix, county in old_cities(theme):
        district_cands = rows_of(table_url("tickets", theme, "D", f"{prefix}_00_000_0000"))
        district_profiles = rows_of(table_url("profiles", theme, "D", f"{prefix}_00_000_0000"))
        names = {str(row["dept_code"]): text(row["area_name"]) for row in district_cands}
        listed = {c.no: c for c in map(cand, district_cands)}
        if villages:
            sums: dict[int, int] = {}
            for row in district_cands:
                sums[int(str(row["cand_no"]))] = sums.get(int(str(row["cand_no"])), 0) + int(str(row["ticket_num"]))
            top = max(sums.values())
            cands = [
                Cand(c.no, c.name, c.sex, c.birth, c.party, sums[c.no], sums[c.no] == top, c.incumbent)
                for c in sorted(listed.values(), key=lambda c: c.no)
            ]
            total = {measure: sum(totals(row)[measure] for row in district_profiles) for measure in (2, 3, 4, 5)}
        else:
            cands = sorted(whole_cands[county], key=lambda c: c.no)
            total = totals(whole[county])
        profile_at = {(str(r["dept_code"]), str(r["li_code"])): r for r in district_profiles}
        rows: list[dict[str, object]] = []
        if villages:
            grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
            for row in rows_of(table_url("tickets", theme, "L", f"{prefix}_00_000_0000")):
                grouped.setdefault((str(row["dept_code"]), str(row["li_code"])), []).append(row)
            have_at = {
                (str(r["dept_code"]), str(r["li_code"])): r
                for r in rows_of(table_url("profiles", theme, "L", f"{prefix}_00_000_0000"))
            }
            for (dept, li), group in grouped.items():
                votes = {int(str(r["cand_no"])): int(str(r["ticket_num"])) for r in group}
                have = have_at[(dept, li)]
                rows.append(
                    {
                        "district": names[dept],
                        "village": text(group[0]["area_name"]),
                        "unit": serial(li),
                        "votes": [votes.get(c.no) for c in cands],
                        "valid": int(str(have["valid_ticket"])),
                        "invalid": int(str(have["invalid_ticket"])),
                        "cast": int(str(have["vote_ticket"])),
                        "registered": int(str(have["votable_population"])),
                    }
                )
        else:
            grouped = {}
            for row in district_cands:
                grouped.setdefault((str(row["dept_code"]), "0000"), []).append(row)
            for (dept, li), group in grouped.items():
                votes = {int(str(r["cand_no"])): int(str(r["ticket_num"])) for r in group}
                have = profile_at[(dept, li)]
                rows.append(
                    {
                        "district": names[dept],
                        "village": "",
                        "unit": "",
                        "votes": [votes.get(c.no) for c in cands],
                        "valid": int(str(have["valid_ticket"])),
                        "invalid": int(str(have["invalid_ticket"])),
                        "cast": int(str(have["vote_ticket"])),
                        "registered": int(str(have["votable_population"])),
                    }
                )
        out.append(Contest(county, cands, total, rows))
    return out


@functools.cache
def contests(day: str, subject: str) -> list[Contest]:
    (theme,) = [t for t in THEMES if (t.day, t.subject) == (day, subject)]
    return old_contests(theme) if theme.layout in ("village", "township") else stations_contests(theme)


# --- the local executive packages


def ballots(row: dict[str, object]) -> list[int | None]:
    votes = row["votes"]
    if not isinstance(votes, list):
        raise TypeError("a row's votes are not a list")
    return votes


def slug_of(county: str) -> str:
    return re.sub(r"[^a-z]+", "-", PLACES[county].lower()).strip("-")


def office_name(county: str) -> str:
    return f"Mayor of {PLACES[county]}" if county in CITIES else f"Magistrate of {PLACES[county]}"


def contest_name(year: str, county: str) -> str:
    return f"{year} {PLACES[county]} " + ("mayoral election" if county in CITIES else "magistrate election")


def contest_name_zh(year: str, county: str) -> str:
    return f"{int(year) - 1911}年{county}{'市長' if county in CITIES else '縣長'}選舉"


def election_names(day: str, subjects: set[str], special: bool) -> tuple[str, str]:
    year = day[:4]
    roc = int(year) - 1911
    if special:
        return f"{year} Chiayi City mayoral re-run election", "嘉義市第11屆市長重行選舉"
    if subjects == {"C1", "C2"}:
        return f"{year} Taiwanese local elections", f"{roc}年地方公職人員選舉"
    if subjects == {"C1"}:
        return f"{year} Taiwanese special municipality mayoral elections", f"{roc}年直轄市長選舉"
    return f"{year} Taiwanese county magistrate and city mayor elections", f"{roc}年縣市長選舉"


def local(day: str) -> Package:
    themes = [theme for theme in THEMES if theme.day == day]
    year = day[:4]
    special = any(theme.special for theme in themes)
    p = Package(
        f"tw.cec/local-{day}",
        SOURCE,
        f"The {year} Taiwanese "
        + ("Chiayi City mayoral re-run" if special else "county magistrate and mayor elections")
        + " by the finest unit the Central Election Commission reports",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for theme in themes:
        for url in local_artifacts(theme):
            p.uses(raw.cached(SOURCE, url))
    election = key("election", day, "local")
    path = f"tw/{day}-chiayi-city-mayoral" if special else f"tw/{day}-local"
    when = date.fromisoformat(day)
    p.add(
        "election",
        id=election,
        polity_id=TAIWAN,
        kind="special" if special else "local",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    english, native = election_names(day, {theme.subject for theme in themes}, special)
    p.name(election, english, "en", "official")
    p.name(election, native, "zh", "native", False)
    unit_frames: list[pl.DataFrame] = []
    value_frames: list[pl.DataFrame] = []
    for theme in themes:
        note = LOCAL_NOTE.format(unit=GRAIN[theme.layout])
        for found in contests(theme.day, theme.subject):
            county = found.county
            contest = key("contest", day, county)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office(county),
                district_area_id=area("tw.county", county),
                magnitude=1,
                round_number=1,
                electoral_system_id=LOCAL_SYSTEM,
                default_result_kind="counted",
            )
            p.name(contest, contest_name(year, county), "en", "official")
            p.name(contest, contest_name_zh(year, county), "zh", "native", False)
            head = "mayor" if county in CITIES else "magistrate"
            p.add("slug", path=f"{path}/{slug_of(county)}-{head}", entity_id=contest, is_current=True)
            result_set = key("result_set", day, county)
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
            p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
            options: dict[int, str] = {}
            for c in found.cands:
                independent = c.party in INDEPENDENT
                party_id = None if independent else key("party", c.party)
                option = key("option", day, county, c.no)
                options[c.no] = option
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_position=c.no,
                    ballot_label=c.name,
                )
                person = key("person", day, county, c.no)
                p.add(
                    "person",
                    id=person,
                    birth_date=date(int(c.birth), 1, 1) if c.birth else None,
                    birth_precision="year" if c.birth else None,
                    gender=c.sex,
                )
                p.name(person, c.name, "zh", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day, county, c.no),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=c.name,
                    member_of_party_id=party_id,
                    is_independent=independent,
                    is_incumbent=c.incumbent,
                )
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if c.won else "lost")
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=c.votes)
            for measure, value in found.totals.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            if sum(1 for c in found.cands if c.won) != 1:
                raise ValueError(f"{day} {county}: the Commission does not name exactly one winner")
            frame = pl.DataFrame(
                {
                    "district": [str(row["district"]) for row in found.rows],
                    "village": [str(row["village"]) for row in found.rows],
                    "unit": [str(row["unit"]) for row in found.rows],
                    "valid": [row["valid"] for row in found.rows],
                    "invalid": [row["invalid"] for row in found.rows],
                    "cast": [row["cast"] for row in found.rows],
                    "registered": [row["registered"] for row in found.rows],
                    **{f"v{c.no}": [ballots(row)[index] for row in found.rows] for index, c in enumerate(found.cands)},
                },
                schema_overrides={"valid": pl.Int64, "invalid": pl.Int64, "cast": pl.Int64, "registered": pl.Int64},
            )
            codes = [
                ":".join((day, theme.subject, county, str(row["district"]), str(row["village"]), str(row["unit"])))
                for row in found.rows
            ]
            if len(set(codes)) != len(codes):
                raise ValueError(f"{day} {county}: reporting unit codes repeat")
            if theme.layout == "township":
                scheme, relation = "tw.township", "equals"
            elif theme.layout == "village":
                scheme, relation = "tw.village", "equals"
            else:
                scheme, relation = "tw.station", "within"
            unit_ids = [key("unit", code) for code in codes]
            areas = [
                area("tw.village", county, str(row["district"]), str(row["village"]))
                if row["village"]
                else area("tw.township", county, str(row["district"]))
                for row in found.rows
            ]
            unit_frames.append(
                pl.DataFrame(
                    {
                        "id": unit_ids,
                        "polity_id": [TAIWAN] * len(codes),
                        "scheme_code": [scheme] * len(codes),
                        "code": codes,
                        "area_relation": [relation] * len(codes),
                        "area_id": areas,
                        "kind": ["standard"] * len(codes),
                    }
                )
            )
            base = frame.with_columns(unit_id=pl.Series(unit_ids))
            parts = [
                base.select(
                    unit_id="unit_id",
                    option_id=pl.lit(options[c.no]),
                    measure=pl.lit(1),
                    value=pl.col(f"v{c.no}"),
                )
                for c in found.cands
            ]
            for measure, column in ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid")):
                parts.append(
                    base.select(
                        unit_id="unit_id", option_id=pl.lit(None, pl.String), measure=pl.lit(measure), value=column
                    )
                )
            facts = pl.concat(parts).filter(pl.col("value").is_not_null() & (pl.col("value") >= 0))
            value_frames.append(
                facts.select(
                    result_set_id=pl.lit(result_set),
                    unit_id="unit_id",
                    option_id="option_id",
                    channel=pl.lit(0),
                    measure="measure",
                    count_round=pl.lit(0),
                    flags=pl.lit(0),
                    value="value",
                )
            )
    p.frame("reporting_unit", pl.concat(unit_frames))
    p.frame("result_value", pl.concat(value_frames))
    return p
