"""Taiwan's municipal, county, and provincial councillor elections from the Central Election Commission's election
database: the six municipalities (subject T1), the counties and cities (T2), and the Taiwan Provincial Assembly of 1994
(T9). Each election is read at the finest unit the Commission publishes: polling stations from the bulk workbooks, and
villages or townships from the database's JSON files where no workbook exists. The 2005 to 2010 archives are .rar
files; reading them needs the `unar` command."""

import functools
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tw.cec import (
    COUNTY_EN,
    INDEPENDENT,
    PARTIES,
    PARTY_EN,
    archive_members,
    cjk,
    number,
    option_headers,
    sheets,
    similar,
    station_number,
    text,
)

SOURCE = "tw.cec_local"
HOST = "https://db.cec.gov.tw/static/elections"
BASE = f"{HOST}/data/"
SUBJECTS = {"T1": "municipal", "T2": "county", "T9": "province"}
ROOT = "00_000_00_000_0000"
# the bulk archive of a theme group, by subject and election day; a day without one is published as JSON only
BULK: dict[tuple[str, str], list[str]] = {
    ("T1", "2006-12-09"): ["第10屆台北市議員選舉得票概況.rar", "第07屆高雄市議員選舉得票概況.rar"],
    ("T1", "2010-11-27"): ["2010直轄市議員選舉得票概況.rar"],
    ("T1", "2014-11-29"): ["直轄市議員.zip"],
    ("T1", "2018-11-24"): ["直轄市議員.zip"],
    ("T1", "2022-11-26"): ["直轄市議員.zip"],
    ("T2", "2005-12-03"): ["2005縣市議員選舉得票概況.rar"],
    ("T2", "2009-12-05"): ["2009縣市議員選舉得票概況.rar"],
    ("T2", "2014-11-29"): ["縣市議員.zip"],
    ("T2", "2018-11-24"): ["縣市議員.zip"],
    ("T2", "2022-11-26"): ["縣市議員.zip"],
}


def list_url(subject: str) -> str:
    return f"{HOST}/list/ELC_{subject}.json"


@functools.cache
def themes(subject: str) -> dict[str, dict[str, dict[str, object]]]:
    """Day -> the Commission's contest type -> its theme entry, for one subject."""
    data = json.loads(raw.fetch(SOURCE, list_url(subject), suffix=".json").path.read_text(encoding="utf-8"))
    found: dict[str, dict[str, dict[str, object]]] = {}
    for area in data:
        for item in area["theme_items"]:
            type_ = str(item["legislator_type_id"])
            # the 1994 provincial plains constituencies are two contests of one type, north and south
            found.setdefault(item["vote_date"], {})[
                type_ + ":" + str(item["legislator_desc"]) if subject == "T9" and type_ == "T2" else type_
            ] = item
    return dict(sorted(found.items()))


def elections() -> list[tuple[str, str]]:
    """Every (subject, day) the pipeline builds, oldest first within a subject."""
    return [(subject, day) for subject in SUBJECTS for day in themes(subject)]


def group(subject: str, day: str) -> str:
    return str(next(iter(themes(subject)[day].values()))["theme_group"])


def bulk_urls(subject: str, day: str) -> list[str]:
    return [
        f"{BASE}attachments/ELC/{subject}/{group(subject, day)}/{quote(name)}" for name in BULK.get((subject, day), [])
    ]


def json_url(kind: str, subject: str, day: str, type_: str, level: str, code: str = ROOT) -> str:
    item = themes(subject)[day][type_]
    return f"{BASE}{kind}/ELC/{subject}/{item['legislator_type_id']}/{item['theme_id']}/{level}/{code}.json"


def levels(subject: str, day: str, type_: str, kind: str = "tickets") -> list[str]:
    """The levels the Commission publishes for a contest type: N nation, C county, A district, D township, L village,
    T polling station."""
    sequence = themes(subject)[day][type_]["data_tckt_seq" if kind == "tickets" else "data_prof_seq"]
    assert isinstance(sequence, list)
    return [str(level) for level in sequence]


def finest(subject: str, day: str, type_: str, kind: str = "tickets") -> str:
    """The finest level of village, township, or county whose figures come from JSON."""
    have = levels(subject, day, type_, kind)
    return "L" if "L" in have else "D" if "D" in have else "C" if "C" in have else "N"


def has_bulk(subject: str, day: str) -> bool:
    return (subject, day) in BULK


@functools.cache
def county_codes(subject: str, day: str, type_: str) -> dict[str, str]:
    """County code (`prv_city_00_000_0000`) -> county name, from the county-level tickets of a contest type."""
    data = json.loads(
        raw.fetch(SOURCE, json_url("tickets", subject, day, type_, "C"), suffix=".json").path.read_text(
            encoding="utf-8"
        )
    )
    rows = next(iter(data.values()))
    return {f"{row['prv_code']}_{row['city_code']}_00_000_0000": str(row["area_name"]) for row in rows}


def urls(subject: str, day: str) -> list[str]:
    found = list(bulk_urls(subject, day))
    for type_ in themes(subject)[day]:
        for kind in ("tickets", "profiles"):
            have = levels(subject, day, type_, kind)
            # the nation and county files of the tickets hold every candidate with the district they ran in
            top = ("N", "C") if kind == "tickets" and subject != "T9" or type_ == "T1" else ("N",)
            found.extend(
                json_url(kind, subject, day, type_, level) for level in top if kind == "tickets" or level in have
            )
            # the workbooks hold the regional contests and, from 2010, the indigenous ones by polling station
            if has_bulk(subject, day) and (type_ == "T1" or "T" in levels(subject, day, type_)):
                continue
            level = finest(subject, day, type_, kind)
            if level in ("D", "L"):
                # village figures come with the township files that name the villages' townships
                for named in ("D", level) if level == "L" and kind == "tickets" else (level,):
                    found.extend(
                        json_url(kind, subject, day, type_, named, code) for code in county_codes(subject, day, type_)
                    )
    return found


def fetch() -> None:
    for subject, day in elections():
        files = urls(subject, day)
        for url in files:
            if url.endswith(".json"):
                continue
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)
        raw.fetch_many(SOURCE, [url for url in files if url.endswith(".json")], suffix=".json")


# --- the Commission's JSON files


@functools.cache
def cached() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


def rows_of(kind: str, subject: str, day: str, type_: str, level: str, code: str = ROOT) -> list[dict[str, object]]:
    """Every row of one JSON file, or none when the Commission does not publish that file."""
    url = json_url(kind, subject, day, type_, level, code)
    if url not in cached():
        return []
    data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
    return [row for rows in data.values() for row in rows]


Key = tuple[str, str, int | None]


def county_name(value: object) -> str:
    return str(value).replace("台", "臺")


@functools.cache
def candidates(subject: str, day: str) -> dict[Key, list[dict[str, object]]]:
    """Every contest of an election as (contest type, county, district number) -> the candidates the Commission lists
    with their totals, in ballot order."""
    found: dict[Key, list[dict[str, object]]] = {}
    for type_ in themes(subject)[day]:
        rows = rows_of("tickets", subject, day, type_, "C") or rows_of("tickets", subject, day, type_, "N")
        for row in rows:
            district = row.get("ori_area_code")
            number_ = int(str(district)) if district not in (None, "", "00") else 1
            found.setdefault((type_, county_name(row["area_name"]), number_), []).append(row)
    # a contest published by township or village is read from those files; the county file's candidates stand only
    # where they are the same candidates, which gives their declared totals
    fine = json_contests(subject, day)
    for key in [key for key in found if not has_bulk(subject, day) and json_level(subject, day, key[0]) is not None]:
        official = found.pop(key)
        if key in fine and {int(str(r["cand_no"])): plain(r["cand_name"]) for r in official} == {
            int(str(r["cand_no"])): plain(r["cand_name"]) for r in fine[key]
        }:
            found[key] = official
    for key, rows in fine.items():
        if key not in found:
            found[key] = [{**row, "derived": True} for row in rows]
    for key, rows in found.items():
        found[key] = one_each(rows)
    return dict(sorted(found.items(), key=lambda item: (item[0][1], item[0][0], item[0][2] or 0)))


def one_each(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """One row per ballot number. Where the Commission lists a candidate twice with different totals, the row keeps
    every total it gives in `alts`, and the unit figures say which one they add up to."""
    chosen: dict[int, dict[str, object]] = {}
    for row in rows:
        no = int(str(row["cand_no"]))
        if no not in chosen:
            chosen[no] = {**row, "alts": [int(str(row["ticket_num"]))]}
        else:
            alts = chosen[no]["alts"]
            assert isinstance(alts, list)
            if int(str(row["ticket_num"])) not in alts:
                alts.append(int(str(row["ticket_num"])))
            if row["is_victor"] == "*":
                chosen[no]["is_victor"] = "*"
    return [chosen[no] for no in sorted(chosen)]


@functools.cache
def profiles(subject: str, day: str) -> dict[tuple[str, str], dict[str, object]]:
    """(contest type, county) -> the Commission's county totals of ballots, for the contests that fill a county."""
    found: dict[tuple[str, str], dict[str, object]] = {}
    for type_ in themes(subject)[day]:
        for row in rows_of("profiles", subject, day, type_, "C") or rows_of("profiles", subject, day, type_, "N"):
            found[(type_, county_name(row["area_name"]))] = row
    return found


# --- reading the workbooks

TAIL = (
    ("有效票數", "valid"),
    ("無效票數", "invalid"),
    ("投票數", "cast"),
    ("選舉人數", "registered"),
)
TOTALS = ("總計", "合計")


class Table:
    """One contest's rows: the official ballot numbers of its columns and one row per reporting unit."""

    def __init__(self, key: Key, numbers: list[int | None], rows: list[dict[str, object]]) -> None:
        self.key = key
        self.numbers = numbers
        self.rows = rows

    def column(self, index: int) -> list[int | None]:
        return [cast_votes(row)[index] for row in self.rows]


def cast_votes(row: dict[str, object]) -> list[int | None]:
    votes = row["votes"]
    assert isinstance(votes, list)
    return votes


def clean_label(label: object) -> str:
    """A header label without the letter and formula the Commission prints around the accounting columns."""
    return re.sub(r"^[A-H]", "", text(label))


def read_grid(rows: list[list[object]]) -> tuple[list[tuple[int | None, str, str]], list[dict[str, object]]]:
    """A polling-station sheet in any of the layouts of 2005 to 2022: the options across the top and one row per
    station, with the township on its first row. Returns the options as (ballot number, name, party) in column order,
    and the stations with their votes in that order."""
    width = max(len(row) for row in rows)
    rows = [[*row, *([""] * (width - len(row)))] for row in rows]
    head = [text(cell) for cell in rows[1]]

    def column(*labels: str) -> int | None:
        return next((i for i, label in enumerate(head) if label.startswith(labels)), None)

    township_at = column("行政區別", "鄉鎮市區")
    village_at = column("村里別", "里別")
    station_at = column("投開票所")
    if township_at is None or village_at is None or station_at is None:
        raise ValueError(f"no township, village, and station columns in {head[:6]}")
    start = station_at + 1
    tail: dict[str, int] = {}
    for index, label in enumerate(rows[1]):
        for prefix, field in TAIL:
            if index >= start and clean_label(label).startswith(prefix) and field not in tail:
                tail[field] = index
    stop = min(tail.values()) if tail else len(head)
    while stop > start and not any(text(row[stop - 1]) for row in rows[1:3] if stop - 1 < len(row)):
        stop -= 1
    first = next(i for i in range(2, len(rows)) if number(rows[i][start]) is not None)
    paired = [i for i in range(start, stop) if head[i].startswith("得票數")]
    options: list[tuple[int | None, str, str]]
    if paired:
        columns = paired
        options = [(None, head[i].removeprefix("得票數"), "") for i in paired]
    elif "各候選人得票情形" in head[start]:
        columns = list(range(start, stop))
        options = [(no, name, party) for no, name, party in option_headers(rows, first, start, stop)]
    else:
        columns = list(range(start, stop))
        options = [(None, head[i], "") for i in columns]
    out: list[dict[str, object]] = []
    township = ""
    for row in rows[first:]:
        label, village, station = text(row[township_at]), text(row[village_at]), station_number(row[station_at])
        if label and label not in TOTALS:
            township = label
        if not (village and station) or label in TOTALS:
            continue
        out.append(
            {
                "township": township,
                "village": village,
                "station": station,
                "votes": [number(row[i]) for i in columns],
                **{field: number(row[tail[field]]) if field in tail else None for _, field in TAIL},
            }
        )
    return options, out


def text_rows(data: bytes) -> list[list[object]]:
    """The 2006 workbooks of Taipei and Kaohsiung are tab-separated text in the Big5 code page."""
    return [list(line.rstrip("\r").split("\t")) for line in data.decode("cp950", errors="replace").split("\n")]


def sheet_list(name: str, data: bytes) -> list[tuple[str, list[list[object]]]]:
    if data[:2] not in (b"\xd0\xcf", b"PK"):
        return [("", text_rows(data))]
    return sheets(name, data)


def members(subject: str, day: str) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    for url in bulk_urls(subject, day):
        found.extend(archive_members(raw.cached(SOURCE, url).path))
    return found


def plain(name: object) -> str:
    """A candidate's name for matching: its Chinese characters, or its letters where it has none."""
    return cjk(name) or re.sub(r"\W", "", str(name)).lower()


def match_names(options: list[tuple[int | None, str, str]], rows: list[dict[str, object]]) -> list[int | None] | None:
    """The ballot number of each column when the options are this contest's candidates by name, else none. A column
    with no name and no official candidate is a ballot number that was withdrawn, and has no number."""
    by_name: dict[str, list[int]] = {}
    for row in rows:
        by_name.setdefault(plain(row["cand_name"]), []).append(int(str(row["cand_no"])))
    official = {int(str(row["cand_no"])) for row in rows}
    numbers: list[int | None] = []
    for number_, name, _ in options:
        if not plain(name) and number_ not in official:
            numbers.append(None)
            continue
        named = by_name.get(plain(name), [])
        if len(named) != 1:
            return None
        numbers.append(named[0])
    return numbers if sorted(n for n in numbers if n is not None) == sorted(official) else None


def match_similar(options: list[tuple[int | None, str, str]], rows: list[dict[str, object]]) -> list[int | None] | None:
    """The same by names that differ by one variant character, for sheets that give neither numbers nor party."""
    if len(options) != len(rows):
        return None
    numbers: list[int | None] = []
    for _, name, _ in options:
        hits = [int(str(row["cand_no"])) for row in rows if similar(plain(row["cand_name"]), plain(name))]
        if len(hits) != 1:
            return None
        numbers.append(hits[0])
    return numbers if len(set(numbers)) == len(rows) else None


def match_numbers(options: list[tuple[int | None, str, str]], rows: list[dict[str, object]]) -> list[int | None] | None:
    """The same by ballot number, for a sheet that numbers its columns and spells a candidate's name with another
    variant of a character; the vote totals of every column are compared with the Commission's afterward."""
    official = {int(str(row["cand_no"])): row for row in rows}
    given = [number_ for number_, _, _ in options]
    if any(number_ is None for number_ in given):
        return None
    found = [number_ for number_ in given if number_ in official]
    if sorted(found) != sorted(official) or any(
        number_ not in official and plain(name) for number_, name, _ in options
    ):
        return None
    odd = sum(
        1
        for number_, name, _ in options
        if number_ in official and not similar(plain(official[number_]["cand_name"]), plain(name))
    )
    return [number_ if number_ in official else None for number_ in given] if odd <= max(1, len(rows) // 5) else None


def resolve(
    subject: str, day: str, county: str, options: list[tuple[int | None, str, str]]
) -> tuple[Key, list[int | None]]:
    """The contest of a county whose candidates are the options of a sheet, and the ballot number of each column."""
    for matcher in (match_names, match_similar, match_numbers):
        found = [
            (key, numbers)
            for key, rows in candidates(subject, day).items()
            if key[1] == county and (numbers := matcher(options, rows)) is not None
        ]
        if len(found) == 1:
            return found[0]
    raise ValueError(f"{day} {county}: no contest has the candidates {[o[1] for o in options]}")


def merge(tables: list[Table]) -> Table:
    first = tables[0]
    for table in tables[1:]:
        if table.numbers != first.numbers:
            raise ValueError(f"{first.key}: the files list the candidates in different orders")
    return Table(first.key, first.numbers, [row for table in tables for row in table.rows])


def find_county(title: str, subject: str, day: str) -> str:
    """The county whose name a sheet's title or file name contains."""
    named = county_name(title)
    counties = {key[1] for key in candidates(subject, day)}
    hits = [name for name in counties if name in named]
    if len(hits) != 1:
        raise ValueError(f"{day}: {len(hits)} counties in {title!r}")
    return hits[0]


def workbook_tables(subject: str, day: str) -> list[Table]:
    """Every polling-station table of an election's bulk archive."""
    tables: dict[Key, list[Table]] = {}
    for name, data in members(subject, day):
        base = name.rsplit("/", 1)[-1]
        if not base.lower().endswith((".xls", ".xlsx")):
            continue
        if not wanted_file(subject, day, name):
            continue
        for sheet, rows in sheet_list(name, data):
            try:
                options, units = read_grid(rows)
            except (ValueError, StopIteration) as problem:
                raise ValueError(f"{name} {sheet}: {problem}") from problem
            county = find_county(f"{name} {sheet} {text(rows[0][0])}", subject, day)
            key, numbers = resolve(subject, day, county, options)
            tables.setdefault(key, []).append(Table(key, numbers, units))
    return [merge(found) for found in tables.values()]


def wanted_file(subject: str, day: str, name: str) -> bool:
    """Whether a file of the archive holds polling-station figures, rather than a summary of them."""
    base = name.rsplit("/", 1)[-1]
    if day == "2006-12-09":
        if "臺北" in name or "台北" in name or "第10屆" in name:
            return base.startswith("表C-")
        return "各投開票所" in base or ("選舉區" not in base and "各行政區" not in base)
    return True


# --- contests the Commission publishes by township or village only


def json_level(subject: str, day: str, type_: str) -> str | None:
    """The level of the county files that hold a contest's figures, when no workbook does."""
    if has_bulk(subject, day) and (type_ == "T1" or "T" in levels(subject, day, type_)):
        return None
    level = finest(subject, day, type_)
    return level if level in ("D", "L") else None


def fine_rows(subject: str, day: str, type_: str, code: str, level: str) -> tuple[list[dict[str, object]], str]:
    """A county's vote rows at the village or township level, and the level they are at. Where the Commission
    publishes no village file for a county, its township file stands."""
    rows = rows_of("tickets", subject, day, type_, level, code)
    if not rows and level == "L":
        return rows_of("tickets", subject, day, type_, "D", code), "D"
    return rows, level


def json_contests(subject: str, day: str) -> dict[Key, list[dict[str, object]]]:
    """Contests listed in the township or village files, with each candidate's votes summed over the county."""
    found: dict[Key, dict[int, dict[str, object]]] = {}
    for type_ in themes(subject)[day]:
        level = json_level(subject, day, type_)
        if level is None:
            continue
        for code, county in county_codes(subject, day, type_).items():
            for row in fine_rows(subject, day, type_, code, level)[0]:
                number_ = int(str(row["area_code"])) if str(row["area_code"]) not in ("", "00") else 1
                by_no = found.setdefault((type_, county_name(county), number_), {})
                have = by_no.get(int(str(row["cand_no"])))
                if have is None:
                    by_no[int(str(row["cand_no"]))] = {**row, "ticket_num": int(str(row["ticket_num"]))}
                else:
                    have["ticket_num"] = int(str(have["ticket_num"])) + int(str(row["ticket_num"]))
                    if row["is_victor"] == "*":
                        have["is_victor"] = "*"
    return {key: list(by_no.values()) for key, by_no in found.items()}


def json_tables(subject: str, day: str) -> list[Table]:
    """The village or township tables of the contests that no workbook covers."""
    tables: list[Table] = []
    for type_ in themes(subject)[day]:
        level = json_level(subject, day, type_)
        if level is None:
            continue
        for code, county in county_codes(subject, day, type_).items():
            county = county_name(county)
            rows, used = fine_rows(subject, day, type_, code, level)
            towns = {
                (str(r["prv_code"]), str(r["city_code"]), str(r["dept_code"])): str(r["area_name"])
                for r in rows_of("tickets", subject, day, type_, "D", code)
            }
            by_contest: dict[int, dict[tuple[str, str], dict[str, object]]] = {}
            for row in rows:
                number_ = int(str(row["area_code"])) if str(row["area_code"]) not in ("", "00") else 1
                town = towns[(str(row["prv_code"]), str(row["city_code"]), str(row["dept_code"]))]
                village = str(row["area_name"]) if used == "L" else ""
                unit = by_contest.setdefault(number_, {}).setdefault(
                    (town, str(row["li_code"])),
                    {"township": town, "village": village, "station": "", "votes": {}, "vcode": str(row["li_code"])},
                )
                votes = unit["votes"]
                assert isinstance(votes, dict)
                votes[int(str(row["cand_no"]))] = int(str(row["ticket_num"]))
            accounts = accounting(subject, day, type_, used, code)
            for number_, units in by_contest.items():
                key = (type_, county, number_)
                numbers = [int(str(row["cand_no"])) for row in candidates(subject, day)[key]]
                out: list[dict[str, object]] = []
                for (town, li), unit in units.items():
                    votes = unit["votes"]
                    assert isinstance(votes, dict)
                    out.append({**unit, "votes": [votes.get(no, 0) for no in numbers], **accounts.get((town, li), {})})
                tables.append(Table(key, list(numbers), out))
    return tables


def accounting(subject: str, day: str, type_: str, level: str, code: str) -> dict[tuple[str, str], dict[str, int]]:
    """Valid, invalid, cast, and registered figures by (township, village code) from the profile file of a county,
    when the profiles are at the same level as the votes."""
    found: dict[tuple[str, str], dict[str, int]] = {}
    if level not in levels(subject, day, type_, "profiles"):
        return found
    names = {
        (str(r["prv_code"]), str(r["city_code"]), str(r["dept_code"])): str(r["area_name"])
        for r in rows_of("tickets", subject, day, type_, "D", code)
    }
    for row in rows_of("profiles", subject, day, type_, level, code):
        town = (
            str(row["area_name"])
            if level == "D"
            else names[(str(row["prv_code"]), str(row["city_code"]), str(row["dept_code"]))]
        )
        found[town, str(row["li_code"])] = {
            "valid": int(str(row["valid_ticket"])),
            "invalid": int(str(row["invalid_ticket"])),
            "cast": int(str(row["vote_ticket"])),
            "registered": int(str(row["votable_population"])),
        }
    return found


def top_tables(subject: str, day: str, covered: set[Key]) -> list[Table]:
    """Contests the Commission publishes only as county totals, or as one total for the province: one unit each."""
    tables: list[Table] = []
    sums = profiles(subject, day)
    types: dict[tuple[str, str], int] = {}
    for key in candidates(subject, day):
        types[key[0], key[1]] = types.get((key[0], key[1]), 0) + 1
    for key, rows in candidates(subject, day).items():
        if key in covered:
            continue
        numbers = [int(str(row["cand_no"])) for row in rows]
        figures = sums.get((key[0], key[1])) if types[key[0], key[1]] == 1 else None
        unit: dict[str, object] = {
            "township": "",
            "village": "",
            "station": "",
            "votes": [int(str(row["ticket_num"])) for row in rows],
        }
        if figures is not None:
            unit |= {
                "valid": int(str(figures["valid_ticket"])),
                "invalid": int(str(figures["invalid_ticket"])),
                "cast": int(str(figures["vote_ticket"])),
                "registered": int(str(figures["votable_population"])),
            }
        tables.append(Table(key, list(numbers), [unit]))
    return tables


@functools.cache
def tables(subject: str, day: str) -> dict[Key, Table]:
    """Every contest of an election with its rows at the finest unit the Commission publishes."""
    found: dict[Key, Table] = {}
    for table in workbook_tables(subject, day) if has_bulk(subject, day) else []:
        found[table.key] = table
    for table in json_tables(subject, day):
        found.setdefault(table.key, table)
    for table in top_tables(subject, day, set(found)):
        found[table.key] = table
    return dict(sorted(found.items(), key=lambda item: (item[0][1], item[0][0], item[0][2] or 0)))


# --- packages

LICENSE = "Open Government Data License, version 1.0 (Taiwan)"
LICENSE_URL = "https://data.gov.tw/license"
TAIWAN = str(uuid5(package.NAMESPACE, "polity\x1fTW"))
INDEPENDENTS = INDEPENDENT | {"無黨籍"}
PARTY_ENGLISH = {
    **PARTIES,
    **PARTY_EN,
    "台灣基進": "Taiwan Statebuilding Party",
    "社會民主黨": "Social Democratic Party",
    "勞動黨": "Labor Party",
    "中華統一促進黨": "Chinese Unification Promotion Party",
    "台灣民眾黨": "Taiwan People's Party",
    "建國黨": "Taiwan Independence Party",
    "台灣維新": "Taiwan Renewal Party",
}
ELECTION_NAMES = {
    "T1": ("municipal council elections", "municipal-councils"),
    "T2": ("county and city council elections", "county-councils"),
    "T9": ("Taiwan Provincial Assembly election", "provincial-assembly"),
}
SYSTEM_START = date(1950, 1, 1)
METHOD = (
    "Each voter casts one vote for one candidate in the constituency. The candidates with the most votes fill the "
    "constituency's seats, so the number of seats varies by constituency. Plains and mountain indigenous residents "
    "vote in constituencies of their own."
)
RESULT_NOTE = (
    "Valid votes for each candidate, as published by the Central Election Commission, at each {unit}. Valid and "
    "invalid ballots, ballots cast, and registered voters are given where the Commission publishes them at that "
    "level. Declared totals are the Commission's own."
)
PATH = "tw"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NATION = key("area", "nation")
CEC = key("source", SOURCE)
SYSTEM = key("electoral_system", "council")
PROVINCE = key("body", "provincial-assembly")


def council_body(county: str) -> str:
    return PROVINCE if county == "臺灣省" else key("body", "council", county)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def english(county: str) -> str:
    return "Taiwan Province" if county == "臺灣省" else COUNTY_EN[county]


def package_key(subject: str, day: str) -> str:
    return f"{SOURCE}/{ELECTION_NAMES[subject][1]}-{day}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(subject, day) for subject, day in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for subject, day in elections():
        election(subject, day).write()


def artifacts(subject: str, day: str) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in dict.fromkeys(urls(subject, day)) if url in cached()]


def unit_code(subject: str, day: str, row: dict[str, object], county: str) -> str:
    code = f"{subject}:{day}:{county}:{row['township']}:{row['village']}:{row['station']}"
    return f"{code}:{row['vcode']}" if row.get("vcode") not in (None, "", "0000") else code


def unit_scheme(row: dict[str, object]) -> str:
    return "tw.local_station" if row["station"] else "tw.local_village" if row["village"] else "tw.local_township"


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The counties and cities, townships and districts, villages, and polling stations that the Central Election "
        "Commission reports municipal, county, and provincial councillor votes for",
    )
    p.external.add(TAIWAN)
    for code, rank, kind, noun in (
        ("tw.local_nation", 0, "administrative", "country"),
        ("tw.local_county", 1, "administrative", "county or city"),
        ("tw.local_township", 2, "administrative", "township or district"),
        ("tw.local_village", 3, "administrative", "village or neighborhood"),
        ("tw.local_station", 4, "precinct", "polling station"),
        ("tw.local_district", 1, "electoral_district", "council constituency"),
    ):
        p.add("scheme", code=code, polity_id=TAIWAN, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="tw.local_nation")
    p.add("area_code", area_id=NATION, code="TW")
    p.name(NATION, "Taiwan", "en", "official")
    p.name(NATION, "臺灣", "zh", "native", False)
    counties: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    districts: set[tuple[str, str, str, int, str]] = set()
    for subject, day in elections():
        for contest, table in tables(subject, day).items():
            county = contest[1]
            counties.add(county)
            districts.add((subject, day, county, contest[2] or 1, contest[0]))
            for row in table.rows:
                if row["township"]:
                    townships.add((county, str(row["township"])))
                if row["village"]:
                    villages.add((county, str(row["township"]), str(row["village"])))
    for county in sorted(counties):
        id = key("area", "county", county)
        p.add("area", id=id, scheme_code="tw.local_county")
        p.add("area_code", area_id=id, code=county)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, county, "zh", "official")
        if county != "臺灣省":
            p.name(id, english(county), "en", "official", False)
    for county, township in sorted(townships):
        id = key("area", "township", county, township)
        p.add("area", id=id, scheme_code="tw.local_township")
        p.add("area_code", area_id=id, code=f"{county}|{township}")
        p.add("area_parent", area_id=id, parent_id=key("area", "county", county))
        p.name(id, township, "zh", "official")
    for county, township, village in sorted(villages):
        id = key("area", "village", county, township, village)
        p.add("area", id=id, scheme_code="tw.local_village")
        p.add("area_code", area_id=id, code=f"{county}|{township}|{village}")
        p.add("area_parent", area_id=id, parent_id=key("area", "township", county, township))
        p.name(id, village, "zh", "official")
    for subject, day, county, number_, type_ in sorted(districts):
        id = district_area(subject, day, county, type_, number_)
        p.add("area", id=id, scheme_code="tw.local_district")
        p.add("area_code", area_id=id, code=f"{subject}|{day}|{county}|{type_}|{number_}")
        p.add("area_parent", area_id=id, parent_id=key("area", "county", county))
        p.name(id, f"{county}第{number_}選舉區 ({day[:4]})", "zh", "official")
    return p


def district_area(subject: str, day: str, county: str, type_: str, number_: int) -> str:
    return key("area", "district", subject, day, county, type_, number_)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Taiwan's municipal, county, and provincial councils, their electoral system, and the parties that ran "
        "candidates, from the Central Election Commission",
        depends=[f"{SOURCE}/geo"],
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
    for subject, day in elections():
        for artifact in artifacts(subject, day):
            p.artifact(artifact, CEC)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TAIWAN,
        valid_from=SYSTEM_START,
        valid_to=None,
        rules={"formula": "sntv"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")
    counties = sorted({contest[1] for subject, day in elections() for contest in candidates(subject, day)})
    for county in counties:
        body = council_body(county)
        p.add("body", id=body, polity_id=TAIWAN, kind="unicameral")
        if county == "臺灣省":
            p.name(body, "Taiwan Provincial Assembly", "en", "official")
            p.name(body, "臺灣省議會", "zh", "native", False)
        else:
            noun = "City Council" if county.endswith("市") else "County Council"
            p.name(body, f"{english(county).rsplit(' ', 1)[0]} {noun}", "en", "official")
            p.name(body, f"{county}議會", "zh", "native", False)
    names = {
        str(row["party_name"])
        for subject, day in elections()
        for rows in candidates(subject, day).values()
        for row in rows
    }
    for name in sorted(names - INDEPENDENTS):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=TAIWAN)
        p.name(id, name, "zh", "official")
        if name in PARTY_ENGLISH:
            p.name(id, PARTY_ENGLISH[name], "en", "official", False)
    return p


def label(subject: str, day: str, type_: str, county: str, number_: int, count: int) -> tuple[str, str, str]:
    """The English name, the Chinese name, and the slug suffix of a contest."""
    desc = str(themes(subject)[day][type_]["legislator_desc"])
    many = count > 1
    if type_ == "T1":
        return (f"District {number_}", f"第{number_}選舉區", f"district-{number_}")
    if subject == "T9" and "北區" in desc:
        return ("plains indigenous, north", "平地原住民北區", "plains-indigenous-north")
    if subject == "T9" and "南區" in desc:
        return ("plains indigenous, south", "平地原住民南區", "plains-indigenous-south")
    kind, slug = {
        "平地原住民": ("plains indigenous", "plains-indigenous"),
        "山地原住民": ("mountain indigenous", "mountain-indigenous"),
        "原住民": ("indigenous", "indigenous"),
    }[desc]
    return (
        f"{kind} constituency{f' {number_}' if many else ''}",
        f"{desc}選舉區" + (f"{number_}" if many else ""),
        f"{slug}-{number_}" if many else slug,
    )


def election(subject: str, day: str) -> Package:
    year = day[:4]
    when = date.fromisoformat(day)
    noun, slug_name = ELECTION_NAMES[subject]
    p = Package(
        package_key(subject, day),
        SOURCE,
        f"The {year} Taiwanese {noun} by {'polling station' if has_bulk(subject, day) else 'village or township'} "
        "from the Central Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(TAIWAN)
    for artifact in artifacts(subject, day):
        p.uses(artifact)
    id = key("election", subject, day)
    path = f"{PATH}/{day}-{slug_name}"
    p.add("election", id=id, polity_id=TAIWAN, kind="general", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{year} Taiwanese {noun}", "en", "official")
    theme = str(next(iter(themes(subject)[day].values()))["theme_name"])
    p.name(id, theme, "zh", "native", False)
    found = tables(subject, day)
    cands = candidates(subject, day)
    counts: dict[tuple[str, str], int] = {}
    for contest in found:
        counts[contest[0], contest[1]] = counts.get((contest[0], contest[1]), 0) + 1
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[pl.DataFrame] = []
    differences: list[str] = []
    mismatched: list[str] = []
    for contest, table in found.items():
        for row in table.rows:
            code = unit_code(subject, day, row, contest[1])
            if code not in unit_rows:
                scheme = unit_scheme(row)
                county = contest[1]
                area_id = (
                    key("area", "village", county, row["township"], row["village"])
                    if row["village"]
                    else key("area", "township", county, row["township"])
                    if row["township"]
                    else key("area", "county", county)
                )
                unit_rows[code] = {
                    "id": key("unit", code),
                    "polity_id": TAIWAN,
                    "scheme_code": scheme,
                    "code": code,
                    "area_relation": "within" if row["station"] else "equals",
                    "area_id": area_id,
                    "kind": "standard" if row["station"] else "aggregate",
                }
            elif unit_rows[code]["id"] != key("unit", code):
                raise ValueError(code)
        values.extend(
            contest_values(
                p, subject, day, id, path, contest, table, cands[contest], counts, unit_rows, differences, mismatched
            )
        )
    p.frame("reporting_unit", pl.DataFrame(list(unit_rows.values())))
    over = sum(
        1
        for table in found.values()
        for row in table.rows
        if row.get("cast") is not None
        and row.get("registered") is not None
        and int(str(row["cast"])) > int(str(row["registered"]))
    )
    if over:
        p.accept(
            "accounting.turnout",
            f"In {over} unit rows the Commission's own figures give more ballots cast than registered voters.",
        )
    unbalanced = sum(
        1
        for table in found.values()
        for row in table.rows
        if None not in (row.get("valid"), row.get("invalid"), row.get("cast"))
        and int(str(row["valid"])) + int(str(row["invalid"])) != int(str(row["cast"]))
    )
    if unbalanced:
        p.accept(
            "accounting.ballots_sum",
            f"In {unbalanced} unit rows the Commission's own valid and invalid ballots do not add up to the ballots "
            "cast.",
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published totals differ from its own figures by unit. " + "; ".join(differences),
        )
        p.add(
            "note",
            id=key("note", "differences", subject, day),
            entity_id=id,
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit-level figures in this election. "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    frame = pl.concat(values).filter(pl.col("value").is_not_null() & (pl.col("value") >= 0))
    p.frame(
        "result_value",
        frame.select(
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


def contest_values(
    p: Package,
    subject: str,
    day: str,
    election_id: str,
    path: str,
    contest_key: Key,
    table: Table,
    rows: list[dict[str, object]],
    counts: dict[tuple[str, str], int],
    unit_rows: dict[str, dict[str, object]],
    differences: list[str],
    mismatched: list[str],
) -> list[pl.DataFrame]:
    type_, county, number_ = contest_key
    number_ = number_ or 1
    year = day[:4]
    tag = f"{type_}:{county}:{number_}"
    contest = key("contest", subject, day, tag)
    result_set = key("result_set", subject, day, tag)
    name_en, name_zh, slug = label(subject, day, type_, county, number_, counts[type_, county])
    seats = sum(1 for row in rows if row["is_victor"] == "*")
    area = district_area(subject, day, county, type_, number_)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=council_body(county),
        district_area_id=area,
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} {english(county)} council election, {name_en}", "en", "official")
    p.name(contest, f"{county}議員選舉{name_zh}", "zh", "native", False)
    p.add("slug", path=f"{path}/{slugify(english(county))}-{slug}", entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    unit_name = (
        "polling station"
        if any(r["station"] for r in table.rows)
        else (
            "village"
            if any(r["village"] for r in table.rows)
            else "township"
            if any(r["township"] for r in table.rows)
            else "county"
        )
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=RESULT_NOTE.format(unit=unit_name),
        lang="en",
    )
    by_no = {int(str(row["cand_no"])): row for row in rows}
    options: list[str | None] = []
    for index, no in enumerate(table.numbers):
        if no is None:
            options.append(None)
            continue
        row = by_no[no]
        party_name = str(row["party_name"])
        independent = party_name in INDEPENDENTS
        party_id = None if independent else key("party", party_name)
        option = key("option", subject, day, tag, no)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_id,
            ballot_position=no,
            ballot_label=str(row["cand_name"]),
        )
        person = key("person", subject, day, tag, no)
        birth = str(row.get("cand_birthyear") or "").strip()
        p.add(
            "person",
            id=person,
            birth_date=date(int(birth), 1, 1) if birth.isdigit() and int(birth) > 1800 else None,
            birth_precision="year" if birth.isdigit() and int(birth) > 1800 else None,
            gender={"1": "male", "2": "female"}.get(str(row.get("cand_sex") or "")),
        )
        p.name(person, str(row["cand_name"]), "zh", "official")
        p.add(
            "candidacy",
            id=key("candidacy", subject, day, tag, no),
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
        added = sum(value or 0 for value in table.column(index))
        alts = row["alts"]
        assert isinstance(alts, list)
        declared = added if added in alts else int(alts[0])
        if not row.get("derived"):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=declared)
        if added != declared:
            missing = sum(1 for value in table.column(index) if value is None)
            gap = f"; {missing} cells are blank in the Commission's file" if missing else ""
            differences.append(
                f"{english(county)} {name_en}: the {unit_name} figures for {row['cand_name']} add up to {added}, and "
                f"the Commission's total is {declared}{gap}"
            )
    figures = profiles(subject, day).get((type_, county)) if counts[type_, county] == 1 else None
    if figures is not None and not any(row.get("derived") for row in rows):
        for measure, field, column in (
            (4, "valid_ticket", "valid"),
            (5, "invalid_ticket", "invalid"),
            (3, "vote_ticket", "cast"),
            (2, "votable_population", "registered"),
        ):
            total = int(str(figures[field]))
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total)
            if any(row.get(column) is not None for row in table.rows):
                added = sum(int(str(row.get(column) or 0)) for row in table.rows)
                if added != total:
                    differences.append(
                        f"{english(county)} {name_en}: the {unit_name} figures add up to {added} for {column} "
                        f"ballots, and the Commission's total is {total}"
                    )
    frame = pl.DataFrame(
        {
            "unit_id": [unit_rows[unit_code(subject, day, row, county)]["id"] for row in table.rows],
            "votes": [row["votes"] for row in table.rows],
            **{column: [row.get(column) for row in table.rows] for _, column in TAIL},
        },
        schema_overrides={"votes": pl.List(pl.Int64), **{column: pl.Int64 for _, column in TAIL}},
    )
    out: list[pl.DataFrame] = []
    for index, option in enumerate(options):
        if option is None:
            continue
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
        if frame[column].null_count() == frame.height:
            continue
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
