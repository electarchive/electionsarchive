"""Taiwan's Legislative Yuan by-elections (補選) from the Central Election Commission's election database. The 2009 to
2015 by-elections are published as JSON files by village and polling station, with the candidates' sex and birth year
and the elected flag. The 2019 to 2023 by-elections are published as workbooks (`.xls` or `.xlsx`): a polling station
table, a candidate totals table, and a summary. One package holds each by-election. The packages share `tw.cec/geo`
and `tw.cec/registry`, which call `extend_geo`, `extend_districts`, and `registry` here."""

import functools
import json
import re
from datetime import date
from typing import NamedTuple, cast
from urllib.parse import quote

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import (
    BASE,
    CEC,
    COUNTY_EN,
    FPTP_DISTRICT,
    INDEPENDENT,
    LEGISLATURE,
    SOURCE,
    TAIWAN,
    area,
    cjk,
    key,
    number,
    sheets,
    slugify,
    station_number,
    text,
)

LIST_URL = "https://db.cec.gov.tw/static/elections/list/BEL_L0.json"
NOTE = (
    "Valid votes for each candidate and the valid and invalid ballots, ballots cast, and registered voters at each "
    "polling station, as published by the Central Election Commission. Voters registered is the number of voters on "
    "the station's roll. Official totals are the Commission's own."
)
NAME = re.compile(r"^第(?P<term>\d+)屆立法委員(?P<county>.{2}[縣市])(?:(?:第(?P<district>\d+))?選舉區)?(?:缺額)?補選$")
NO_PARTY = "無黨籍及未經政黨推薦"
MEASURES = ((4, "valid"), (5, "invalid"), (3, "cast"), (2, "registered"))


class By(NamedTuple):
    day: str
    theme: str
    name: str
    session: int
    county: str
    district: int | None
    code: str  # the Commission's area code of the county, `prv_city_00_000_0000`
    files: bool  # whether the Commission publishes JSON files (else workbooks)
    level: str  # the level of the district's profile file: A for an area, C for a whole county


class Cand(NamedTuple):
    no: int
    name: str
    party: str
    sex: str
    birth: str
    victor: bool | None  # None when the files do not flag the elected candidate
    total: int  # the Commission's total for this candidate


class Row(NamedTuple):
    township: str
    village: str
    station: str
    votes: list[int | None]
    valid: int | None
    invalid: int | None
    cast: int | None
    registered: int | None


class Data(NamedTuple):
    cands: list[Cand]
    rows: list[Row]
    total: dict[str, int]
    seats: int | None
    summary: dict[str, int | None]
    inferred: bool  # whether the elected candidate is the one with the most votes, not one the files flag


def read(url: str) -> dict[str, list[dict[str, object]]]:
    return cast(dict[str, list[dict[str, object]]], json.loads(raw.cached(SOURCE, url).path.read_text("utf-8")))


def num(row: dict[str, object], field: str) -> int:
    return int(str(row[field]))


@functools.cache
def elections() -> list[By]:
    artifact = raw.fetch(SOURCE, LIST_URL, suffix=".json")
    found: list[By] = []
    for entry in json.loads(artifact.path.read_text(encoding="utf-8")):
        for place in entry["time_items"]:
            for item in place["theme_items"]:
                if item["legislator_type_id"] != "L1":
                    raise ValueError(f"{item['theme_name']}: not a district by-election")
                match = NAME.match(item["theme_name"])
                if match is None:
                    raise ValueError(f"{item['theme_name']}: cannot read the name")
                found.append(
                    By(
                        item["vote_date"],
                        item["theme_id"],
                        item["theme_name"],
                        int(item["session"]),
                        match["county"],
                        int(match["district"]) if match["district"] else None,
                        f"{item['prv_code']}_{item['city_code']}_00_000_0000",
                        bool(item["has_data"]),
                        str(item["data_level"]),
                    )
                )
    return sorted(found, key=lambda by: (by.day, by.name))


def table_url(kind: str, by: By, level: str, code: str | None = None) -> str:
    return f"{BASE}{kind}/BEL/L0/L1/{by.theme}/{level}/{code or by.code}.json"


def list_url(by: By) -> str:
    return f"{BASE}attachments/BEL/L0/{by.theme}/list.json"


def attachment_url(by: By, name: str) -> str:
    return f"{BASE}attachments/BEL/L0/{by.theme}/{quote(name)}"


def file_names(by: By) -> dict[str, str]:
    """The workbooks of a by-election published as workbooks: station table, candidate totals, and summary."""
    data = json.loads(raw.cached(SOURCE, list_url(by)).path.read_text(encoding="utf-8"))
    names = [str(item["file_name"]) for item in data]
    found: dict[str, str] = {}
    for name in names:
        if "各投開票所" in name:
            found["stations"] = name
        elif "概況" in name:
            found["summary"] = name
        elif "得票數一覽表" in name and "各村里" not in name:
            found["totals"] = name
    if set(found) != {"stations", "summary", "totals"}:
        raise ValueError(f"{by.name}: unexpected files {names}")
    return found


NATION = "00_000_00_000_0000"


def district_urls(by: By) -> tuple[str, str]:
    """The files holding the district's candidate totals and its ballot totals."""
    return (
        table_url("tickets", by, "N", NATION),
        table_url("profiles", by, by.level, by.code if by.level == "A" else NATION),
    )


def urls(by: By) -> list[str]:
    if by.files:
        return [
            *district_urls(by),
            table_url("tickets", by, "T"),
            table_url("profiles", by, "T"),
            table_url("areas", by, "T"),
        ]
    return [list_url(by), *(attachment_url(by, name) for name in file_names(by).values())]


def fetch() -> None:
    for by in elections():
        if not by.files:
            raw.fetch(SOURCE, list_url(by), suffix=".json")
        for address in urls(by):
            raw.fetch(SOURCE, address, suffix=address[address.rindex(".") :])


def artifacts(by: By) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, address) for address in urls(by)]


# --- JSON files


def party_of(name: object) -> str:
    cleaned = text(name)
    return NO_PARTY if cleaned in ("", "無") else cleaned


def json_data(by: By) -> Data:
    official = next(iter(read(district_urls(by)[0]).values()))
    cands = [
        Cand(
            num(row, "cand_no"),
            str(row["cand_name"]),
            party_of(row["party_name"]),
            str(row.get("cand_sex") or ""),
            str(row.get("cand_birthyear") or "").strip(),
            row["is_victor"] == "*",
            num(row, "ticket_num"),
        )
        for row in official
    ]
    order = {cand.no: index for index, cand in enumerate(cands)}
    areas = read(table_url("areas", by, "T"))
    townships: dict[str, str] = {}
    villages: dict[tuple[str, str], str] = {}
    for rows in areas.values():
        for row in rows:
            if row["dept_code"] == "000":
                continue
            if row["li_code"] == "0000":
                townships[str(row["dept_code"])] = str(row["area_name"])
            else:
                villages[(str(row["dept_code"]), str(row["li_code"]))] = str(row["area_name"])
    votes: dict[tuple[str, str, str], list[int | None]] = {}
    for rows in read(table_url("tickets", by, "T")).values():
        for row in rows:
            cell = (str(row["dept_code"]), str(row["li_code"]), str(row["tbox_no"]))
            votes.setdefault(cell, [None] * len(cands))[order[num(row, "cand_no")]] = num(row, "ticket_num")
    out: list[Row] = []
    for rows in read(table_url("profiles", by, "T")).values():
        for row in rows:
            cell = (str(row["dept_code"]), str(row["li_code"]), str(row["tbox_no"]))
            out.append(
                Row(
                    townships[cell[0]],
                    villages[(cell[0], cell[1])],
                    cell[2],
                    votes[cell],
                    num(row, "valid_ticket"),
                    num(row, "invalid_ticket"),
                    num(row, "vote_ticket"),
                    num(row, "votable_population"),
                )
            )
    profile = next(iter(read(district_urls(by)[1]).values()))[0]
    total = {
        "valid": num(profile, "valid_ticket"),
        "invalid": num(profile, "invalid_ticket"),
        "cast": num(profile, "vote_ticket"),
        "registered": num(profile, "votable_population"),
    }
    inferred = not any(cand.victor for cand in cands)
    if inferred:
        top = max(cand.total for cand in cands)
        cands = [cand._replace(victor=cand.total == top) for cand in cands]
    return Data(cands, out, total, num(profile, "elected_num"), {}, inferred)


# --- workbooks


def cells(rows: list[list[object]]) -> tuple[int, dict[str, int], int, int]:
    """The header row, the accounting columns, the first option column, and the total row of a workbook sheet."""
    header = next(i for i, row in enumerate(rows) if any(text(cell).startswith("有效票數") for cell in row))
    columns: dict[str, int] = {}
    for index, label in enumerate(rows[header]):
        cleaned = text(label)
        if cleaned.startswith("有效票數"):
            columns["valid"] = index
        elif cleaned.startswith("無效票數"):
            columns["invalid"] = index
        elif cleaned.startswith("投票數"):
            columns["cast"] = index
        elif "選舉人數" in cleaned:
            columns["registered"] = index
    first = next(i for i, cell in enumerate(rows[header + 1]) if text(cell))
    total = next(i for i in range(header, len(rows)) if text(rows[i][0]) in ("總計", "合計"))
    return header, columns, first, total


def options_of(
    rows: list[list[object]], header: int, columns: dict[str, int], first: int
) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for column in range(first, columns["valid"]):
        label = re.search(r"\d+", text(rows[header + 1][column]))
        if label is None:
            raise ValueError(f"no ballot number above column {column}")
        found.append((int(label.group()), text(rows[header + 2][column]), party_of(rows[header + 3][column])))
    return found


def sheet(by: By, kind: str) -> list[list[object]]:
    name = file_names(by)[kind]
    artifact = raw.cached(SOURCE, attachment_url(by, name))
    return sheets(name, artifact.path.read_bytes())[0][1]


def workbook_data(by: By) -> Data:
    rows = sheet(by, "stations")
    header, columns, first, start = cells(rows)
    options = options_of(rows, header, columns, first)
    out: list[Row] = []
    township = ""
    for row in rows[start + 1 :]:
        label, village, code = text(row[0]), text(row[1]), station_number(row[2])
        if village and code:
            if not township:
                raise ValueError(f"{by.name}: a station row comes before any township")
            out.append(
                Row(
                    township,
                    village,
                    code,
                    [number(row[first + index]) for index in range(len(options))],
                    *(number(row[columns[field]]) for field in ("valid", "invalid", "cast", "registered")),
                )
            )
        elif label and not village and number(row[first]) is not None:
            township = label
    totals = sheet(by, "totals")
    t_header, t_columns, t_first, t_total = cells(totals)
    t_options = options_of(totals, t_header, t_columns, t_first)
    if [(no, cjk(name)) for no, name, _ in t_options] != [(no, cjk(name)) for no, name, _ in options]:
        raise ValueError(f"{by.name}: the totals table lists other candidates than the station table")
    row = totals[t_total]
    declared = [number(row[t_first + index]) for index in range(len(options))]
    top = max(value or 0 for value in declared)
    leaders = [index for index, value in enumerate(declared) if value == top]
    if len(leaders) != 1:
        raise ValueError(f"{by.name}: the leading candidates tie")
    cands = [
        Cand(no, name, party, "", "", index == leaders[0], declared[index] or 0)
        for index, (no, name, party) in enumerate(options)
    ]
    total = {field: number(row[t_columns[field]]) or 0 for field in ("valid", "invalid", "cast", "registered")}
    summary = sheet(by, "summary")
    figures = next(r for r in summary if number(r[1]) and number(r[9]))
    seats = number(figures[5])
    return Data(
        cands,
        out,
        total,
        seats,
        {
            "registered": number(figures[1]),
            "cast": number(figures[8]),
            "valid": number(figures[9]),
            "invalid": number(figures[10]),
        },
        True,
    )


@functools.cache
def data(by: By) -> Data:
    return json_data(by) if by.files else workbook_data(by)


# --- the geo and registry packages


def extend_geo(counties: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    for by in elections():
        counties.add(by.county)
        for row in data(by).rows:
            townships.add((by.county, row.township))
            villages.add((by.county, row.township, row.village))


def extend_districts(districts: set[tuple[int, str, int]]) -> None:
    for by in elections():
        districts.add((by.session, by.county, by.district or 1))


def parties() -> set[str]:
    return {cand.party for by in elections() for cand in data(by).cands} - INDEPENDENT - {NO_PARTY}


def registry(p: Package) -> None:
    cited: dict[str, raw.Artifact] = {}
    artifact = raw.cached(SOURCE, LIST_URL)
    cited[artifact.storage_key] = artifact
    for by in elections():
        for found in artifacts(by):
            cited.setdefault(found.storage_key, found)
    for found in cited.values():
        p.artifact(found, CEC)


# --- the packages


def slug_of(by: By) -> str:
    return slugify(COUNTY_EN[by.county]) + (f"-{by.district}" if by.district else "")


def packages() -> list[str]:
    return [f"tw.cec/leg-by-{by.day}-{slug_of(by)}" for by in elections()]


def english(by: By) -> str:
    where = COUNTY_EN[by.county] + (f" District {by.district}" if by.district else "")
    return f"{by.day[:4]} {where} legislative by-election"


def native(by: By) -> str:
    return f"{by.county}第{by.district}選舉區" if by.district else f"{by.county}選舉區"


def election(by: By) -> Package:
    when = date.fromisoformat(by.day)
    found = data(by)
    slug = slug_of(by)
    p = Package(
        f"tw.cec/leg-by-{by.day}-{slug}",
        SOURCE,
        f"The {english(by)} in Taiwan by polling station from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for artifact in artifacts(by):
        p.uses(artifact)
    tag = f"leg-by-{by.day}-{slug}"
    elect = key("election", tag)
    path = f"tw/{by.day}-legislative-by-election-{slug}"
    p.add("election", id=elect, polity_id=TAIWAN, kind="by_election", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=elect, is_current=True)
    p.name(elect, english(by), "en", "official")
    p.name(elect, by.name, "zh", "native", False)
    contest = key("contest", tag)
    result_set = key("result_set", tag)
    p.add(
        "contest",
        id=contest,
        election_id=elect,
        kind="body_seats",
        body_id=LEGISLATURE,
        district_area_id=key("area", "tw.district", f"{by.session}|{by.county}|{by.district or 1}"),
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP_DISTRICT,
        default_result_kind="counted",
    )
    p.name(contest, english(by), "en", "official")
    p.name(contest, native(by), "zh", "native", False)
    p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=elect,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    body = NOTE
    if found.inferred:
        body += (
            " The Commission's files do not flag the elected candidate. They give the number elected, one, and the "
            "elected candidate here is the one with the most votes."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
    differences: list[str] = []
    options: list[str] = []
    for index, cand in enumerate(found.cands):
        independent = cand.party in INDEPENDENT or cand.party == NO_PARTY
        party_id = None if independent else key("party", cand.party)
        option = key("option", tag, cand.no)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_id,
            ballot_position=cand.no,
            ballot_label=cand.name,
        )
        person = key("person", tag, cand.no)
        p.add(
            "person",
            id=person,
            birth_date=date(int(cand.birth), 1, 1) if cand.birth.isdigit() else None,
            birth_precision="year" if cand.birth.isdigit() else None,
            gender={"1": "male", "2": "female"}.get(cand.sex),
        )
        p.name(person, cand.name, "zh", "official")
        p.add(
            "candidacy",
            id=key("candidacy", tag, cand.no),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=cand.name,
            member_of_party_id=party_id,
            is_independent=independent,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if cand.victor else "lost")
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=cand.total)
        added = sum(row.votes[index] or 0 for row in found.rows)
        if added != cand.total:
            differences.append(
                f"the polling station figures for {cand.name} add up to {added}, and the Commission's total is "
                f"{cand.total}"
            )
    if found.seats != 1 or sum(1 for cand in found.cands if cand.victor) != 1:
        raise ValueError(f"{by.name}: expected one seat and one elected candidate")
    for measure, field in MEASURES:
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=found.total[field])
        added = sum(getattr(row, field) or 0 for row in found.rows)
        if added != found.total[field]:
            differences.append(
                f"the polling station figures add up to {added} for {field} ballots, and the Commission's total is "
                f"{found.total[field]}"
            )
    for field, value in found.summary.items():
        if value != found.total[field]:
            differences.append(
                f"the summary gives {value} for {field} ballots and the candidate table {found.total[field]}"
            )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published totals differ from its own polling-station figures. " + "; ".join(differences),
        )
        p.add(
            "note",
            id=key("note", "differences", tag),
            entity_id=elect,
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit-level figures in this election: "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    units: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in found.rows:
        code = f"legby:{by.day}:{slug}:{row.township}:{row.village}:{row.station}"
        if code in seen:
            raise ValueError(f"{by.name}: polling station {code} repeats")
        seen.add(code)
        units.append(
            {
                "id": key("unit", code),
                "polity_id": TAIWAN,
                "scheme_code": "tw.station",
                "code": code,
                "area_relation": "within",
                "area_id": area("tw.village", by.county, row.township, row.village),
                "kind": "standard",
            }
        )
    p.frame("reporting_unit", pl.DataFrame(units))
    values: list[tuple[str, str, str | None, int, int | None]] = []
    for unit, row in zip(units, found.rows, strict=True):
        uid = str(unit["id"])
        for option, votes in zip(options, row.votes, strict=True):
            values.append((result_set, uid, option, 1, votes))
        for measure, field in MEASURES:
            values.append((result_set, uid, None, measure, getattr(row, field)))
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
    ).filter(pl.col("value").is_not_null() & (pl.col("value") >= 0))
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


def parse() -> None:
    for by in elections():
        election(by).write()
