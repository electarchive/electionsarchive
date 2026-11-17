"""Taiwan's recall votes (罷免) from the Central Election Commission's election database: legislators, mayors, and
councilors from 2015 on. The Commission publishes the 2020 recall of the Kaohsiung mayor as profiles and tickets files
by village and polling station, and the other recalls as a results table and a polling station table in a workbook
(`.xlsx`, `.xls`, or `.ods`). One package per voting day holds every recall voted that day. The packages share
`tw.cec/geo` and `tw.cec/registry`, which call `extend_geo` and `registry` here."""

import functools
import json
import re
from datetime import date
from typing import NamedTuple
from urllib.parse import quote

import fastexcel
import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import CEC, SOURCE, TAIWAN, area, key, sheets
from ea.sources.tw.cec_township import TOWN, VILLAGE, at, fold

ROOT = "https://db.cec.gov.tw/static/elections/"
SUBJECTS = ("L0", "C1", "C2", "T1", "T2")


class Recall(NamedTuple):
    subject: str
    group: str
    theme: str
    name: str
    day: str
    level: str
    county: str
    area: str
    result: str
    detailed: bool


NAME = re.compile(
    r"^(?P<county>.{2}[縣市])(?:議會)?第(?P<term>[0-9一二三四五六七八九十]+)屆(?:第(?P<district>[0-9一二三四五六七八九十]+)選舉區)?"
)
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def county_of(name: str) -> str:
    found = NAME.match(name.strip())
    if not found:
        raise ValueError(f"{name}: no county")
    return found["county"]


def number(text: str) -> int:
    """A term or district number written in digits or in Chinese numerals up to ninety-nine."""
    if text.isdigit():
        return int(text)
    tens, ten, ones = text.partition("十")
    if not ten:
        return NUMERALS[text]
    return NUMERALS.get(tens, 1) * 10 + NUMERALS.get(ones, 0)


def list_url(subject: str) -> str:
    return f"{ROOT}list/RCL_{subject}.json"


def attachments_url(recall: Recall) -> str:
    return f"{ROOT}data/attachments/RCL/{recall.subject}/{recall.group}/list.json"


def data_url(kind: str, recall: Recall, level: str, code: str = "00_000_00_000_0000") -> str:
    return f"{ROOT}data/{kind}/RCL/{recall.subject}/00/{recall.theme}/{level}/{code}.json"


@functools.cache
def recalls() -> list[Recall]:
    found: list[Recall] = []
    for subject in SUBJECTS:
        data = json.loads(raw.fetch(SOURCE, list_url(subject), suffix=".json").path.read_text(encoding="utf-8"))
        for group in data:
            for place in group["time_items"]:
                for item in place["theme_items"]:
                    found.append(
                        Recall(
                            subject,
                            item["theme_group"],
                            item["theme_id"],
                            str(item["theme_name"]).strip(),
                            item["vote_date"],
                            item["data_level"],
                            county_of(str(item["theme_name"])),
                            item["area_code"],
                            item["vote_result"],
                            bool(item["has_data"]),
                        )
                    )
    return sorted(found, key=lambda r: (r.day, r.subject, r.name))


@functools.cache
def files(recall: Recall) -> list[tuple[str, str]]:
    """The names and URLs of the workbooks a recall lists."""
    data = json.loads(raw.fetch(SOURCE, attachments_url(recall), suffix=".json").path.read_text(encoding="utf-8"))
    return [
        (item["file_name"], "https://db.cec.gov.tw/static/" + quote(item["file_path"]))
        for item in data
        if not item["file_name"].endswith(".zip")
    ]


def detailed_urls(recall: Recall) -> list[str]:
    """The profile files of a recall that the Commission publishes as data: nation, county, and then the township,
    village, and polling station files of its county."""
    found = [data_url("profiles", recall, level) for level in ("N", "C")]
    top = json.loads(raw.fetch(SOURCE, found[1], suffix=".json").path.read_text(encoding="utf-8"))
    for row in next(iter(top.values())):
        code = f"{row['prv_code']}_{row['city_code']}_00_000_0000"
        found.extend(data_url("profiles", recall, level, code) for level in ("D", "L", "T"))
    return found


def fetch() -> None:
    for recall in recalls():
        if recall.detailed:
            for address in detailed_urls(recall):
                raw.fetch(SOURCE, address, suffix=".json")
            continue
        for name, address in files(recall):
            raw.fetch(SOURCE, address, suffix="." + name.rsplit(".", 1)[-1])


# --- reading the tables

SYSTEM = key("electoral_system", "recall")
METHOD = (
    "Voters on the roll of the official's constituency mark agree (同意) or disagree (不同意) with the recall. A recall "
    "passes when the agree votes outnumber the disagree votes and, since the 2016 amendment of the Civil Servants "
    "Election and Recall Act, are at least a quarter of the voters on the roll. Before the amendment the recall "
    "also needed a turnout above half of the voters on the roll. A recall that fails is rejected (否決)."
)
NOTE = (
    "Agree and disagree votes, valid and invalid ballots, ballots cast, and registered voters at each polling "
    "station, as published by the Central Election Commission. Voters registered is the number on the roll of the "
    "constituency. The totals are the Commission's own, from the total row of its polling station table."
)
ANSWERS = (("Agree", "同意罷免"), ("Disagree", "不同意罷免"))
FIELDS = ("agree", "disagree", "valid", "invalid", "cast", "registered")
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))
KINDS = {"L0": "legislator", "C1": "mayor", "C2": "mayor", "T1": "councilor", "T2": "councilor"}


class Station(NamedTuple):
    district: str
    village: str
    station: str
    agree: int
    disagree: int
    valid: int
    invalid: int
    cast: int
    registered: int


class Table(NamedTuple):
    total: dict[str, int]
    districts: dict[str, dict[str, int]]
    stations: list[Station]


def grid(name: str, artifact: raw.Artifact) -> list[tuple[str, list[list[object]]]]:
    data = artifact.path.read_bytes()
    if name.lower().endswith(".ods"):
        workbook = fastexcel.read_excel(data)
        return [
            (sheet, [list(row) for row in workbook.load_sheet(sheet, header_row=None).to_polars().rows()])
            for sheet in workbook.sheet_names
        ]
    return sheets(name, data)


def cell(value: object) -> int | None:
    """A whole number from a cell that holds a number, or text of digits, and nothing otherwise."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else None
    text = str(value).strip().replace(",", "")
    return int(text) if re.fullmatch(r"\d+", text) else None


def text(value: object) -> str:
    return re.sub(r"[\s　]+", "", "" if value is None else str(value))


def code(value: object) -> str:
    """A polling station number as the Commission prints it, with its leading zeros dropped."""
    number_ = cell(value)
    return str(number_) if number_ is not None else text(value)


def station_table(rows: list[list[object]]) -> Table:
    total: dict[str, int] = {}
    districts: dict[str, dict[str, int]] = {}
    stations: list[Station] = []
    district = ""
    for row in rows:
        row = [*row, *[None] * (13 - len(row))]
        figures = [cell(value) for value in row[3:12]]
        if any(figure is None for figure in figures[:8]) or figures[8] is None:
            continue
        label, village, number_ = text(row[0]), text(row[1]), code(row[2])
        agree, disagree, valid, invalid, cast, _, _, _, registered = figures
        assert None not in (agree, disagree, valid, invalid, cast, registered)
        counts = {
            "agree": agree,
            "disagree": disagree,
            "valid": valid,
            "invalid": invalid,
            "cast": cast,
            "registered": registered,
        }
        if label.startswith("總"):
            total = counts  # ty: ignore[invalid-assignment]
        elif label and not village and not number_:
            district = label
            districts[district] = counts  # ty: ignore[invalid-assignment]
        elif village and number_:
            stations.append(Station(district, village, number_, agree, disagree, valid, invalid, cast, registered))  # ty: ignore[invalid-argument-type]
        else:
            raise ValueError(f"unreadable row {row}")
    if not total or not stations:
        raise ValueError("no total or no stations")
    return Table(total, districts, stations)


@functools.cache
def split(recall: Recall) -> tuple[Table, list[str]]:
    """The polling station table, and the names of the other files, which hold the results table."""
    found: list[Table] = []
    others: list[str] = []
    for name, address in files(recall):
        parsed: list[Table] = []
        for _, rows in grid(name, raw.cached(SOURCE, address)):
            try:
                parsed.append(station_table(rows))
            except ValueError:
                continue
        if parsed:
            found.extend(parsed)
        else:
            others.append(name)
    if len(found) != 1:
        raise ValueError(f"{recall.name}: {len(found)} polling station tables")
    return found[0], others


def table(recall: Recall) -> Table:
    return detailed_table(recall) if recall.detailed else split(recall)[0]


def detailed_table(recall: Recall) -> Table:
    def profile(level: str) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for address in detailed_urls(recall):
            if f"/{level}/" in address:
                data = json.loads(raw.cached(SOURCE, address).path.read_text(encoding="utf-8"))
                out.extend(row for part in data.values() for row in part)
        return out

    def counts(row: dict[str, object]) -> dict[str, int]:
        return {
            "agree": int(str(row["agree_ticket"])),
            "disagree": int(str(row["disagree_ticket"])),
            "valid": int(str(row["valid_ticket"])),
            "invalid": int(str(row["invalid_ticket"])),
            "cast": int(str(row["vote_ticket"])),
            "registered": int(str(row["votable_population"])),
        }

    township = {at(r, TOWN): fold(r["area_name"]) for r in profile("D")}
    village = {at(r, VILLAGE): fold(r["area_name"]) for r in profile("L")}
    stations = []
    for r in profile("T"):
        c = counts(r)
        stations.append(
            Station(
                township[at(r, TOWN)],
                village[at(r, VILLAGE)],
                code(r["tbox_no"]),
                c["agree"],
                c["disagree"],
                c["valid"],
                c["invalid"],
                c["cast"],
                c["registered"],
            )
        )
    return Table(counts(profile("N")[0]), {township[at(r, TOWN)]: counts(r) for r in profile("D")}, stations)


def summary(recall: Recall) -> dict[str, int] | None:
    """The totals of the Commission's results table, when the workbook has one."""
    if recall.detailed:
        return None
    others = split(recall)[1]
    for name, address in files(recall):
        if name not in others:
            continue
        for _, rows in grid(name, raw.cached(SOURCE, address)):
            for row in rows:
                cells = [cell(value) for value in row]
                if len(row) >= 13 and text(row[0]) == "總計":
                    return {
                        "agree": cells[1] or 0,
                        "disagree": cells[3] or 0,
                        "valid": cells[5] or 0,
                        "invalid": cells[6] or 0,
                        "cast": cells[7] or 0,
                        "registered": cells[11] or 0,
                    }
                if len(row) == 12 and cells[0] and cells[1] and cells[5]:
                    return {
                        "registered": cells[0],
                        "agree": cells[1],
                        "disagree": cells[3] or 0,
                        "valid": cells[5],
                        "invalid": cells[6] or 0,
                        "cast": cells[7] or 0,
                    }
                if len(row) >= 9 and cells[3] and cells[4] and cells[6] and cells[7] and text(row[0]):
                    return {"registered": cells[3], "cast": cells[4], "agree": cells[6], "disagree": cells[7]}
    return None


# --- geography and registry


def parts(recall: Recall) -> tuple[int, int | None]:
    found = NAME.match(recall.name)
    assert found is not None
    return number(found["term"]), number(found["district"]) if found["district"] else None


def artifacts(recall: Recall) -> list[raw.Artifact]:
    if recall.detailed:
        return [raw.cached(SOURCE, address) for address in detailed_urls(recall)]
    return [raw.cached(SOURCE, address) for _, address in files(recall)] + [raw.cached(SOURCE, attachments_url(recall))]


def geo_sets() -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    counties_: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    for recall in recalls():
        counties_.add(recall.county)
        for station in table(recall).stations:
            townships.add((recall.county, station.district))
            villages.add((recall.county, station.district, station.village))
    return counties_, townships, villages


def extend_geo(counties_: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    more = geo_sets()
    counties_ |= more[0]
    townships |= more[1]
    villages |= more[2]


def extend_districts(districts: set[tuple[int, str, int]]) -> None:
    """Add the legislative districts of recalled legislators to the geo package's set."""
    for recall in recalls():
        if recall.subject == "L0":
            term, district = parts(recall)
            districts.add((term, recall.county, district or 1))


def council_districts() -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for recall in recalls():
        if recall.subject in ("T1", "T2"):
            term, district = parts(recall)
            code = f"{recall.county}|{term}|{district}"
            found[area("tw.council_district", code)] = (
                code,
                recall.county,
                f"{recall.county}議會第{term}屆第{district}選舉區",
            )
    return found


def geo(p: Package) -> None:
    """Add the electoral districts of recalled councilors to the geo package."""
    p.add(
        "scheme",
        code="tw.council_district",
        polity_id=TAIWAN,
        kind="electoral_district",
        rank=2,
        noun="county or city council electoral district",
        code_system=None,
    )
    for id, (code, county, name) in sorted(council_districts().items(), key=lambda item: item[1][0]):
        p.add("area", id=id, scheme_code="tw.council_district")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("tw.county", county))
        p.name(id, name, "zh", "official")


def registry(p: Package) -> None:
    """Add the recall electoral system and the cited files to the registry package."""
    cited: dict[str, raw.Artifact] = {}
    for subject in SUBJECTS:
        artifact = raw.cached(SOURCE, list_url(subject))
        cited.setdefault(artifact.storage_key, artifact)
    for recall in recalls():
        for artifact in artifacts(recall):
            cited.setdefault(artifact.storage_key, artifact)
    for artifact in cited.values():
        p.artifact(artifact, CEC)
    p.add("electoral_system", id=SYSTEM, polity_id=TAIWAN, rules={"formula": "referendum"}, rules_version=1)
    p.name(SYSTEM, "Taiwanese recall vote", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")


# --- the packages


def packages() -> list[str]:
    return [f"tw.cec/recall-{day}" for day in sorted({recall.day for recall in recalls()})]


def label(day: str) -> str:
    return date.fromisoformat(day).strftime("%-d %B %Y")


def english(recall: Recall) -> str:
    from ea.sources.tw.cec import PLACES

    place = PLACES[recall.county]
    _, district = parts(recall)
    year = recall.day[:4]
    if recall.subject == "L0":
        where = f"{place} District {district}" if district else place
        return f"{year} recall vote on the {where} legislator"
    if recall.subject in ("C1", "C2"):
        return f"{year} recall vote on the mayor of {place}"
    return f"{year} recall vote on the {place} Council member for District {district}"


def slug_of(recall: Recall) -> str:
    from ea.sources.tw.cec import PLACES

    _, district = parts(recall)
    base = re.sub(r"[^a-z]+", "-", PLACES[recall.county].lower()).strip("-")
    kind = {"L0": "legislator", "C1": "mayor", "C2": "mayor", "T1": "councilor", "T2": "councilor"}[recall.subject]
    return f"{base}-{kind}" + (f"-district-{district}" if district else "")


def district_area(recall: Recall) -> str:
    term, district = parts(recall)
    if recall.subject == "L0":
        return key("area", "tw.district", f"{term}|{recall.county}|{district or 1}")
    if recall.subject in ("T1", "T2"):
        return area("tw.council_district", f"{recall.county}|{term}|{district}")
    return area("tw.county", recall.county)


def election(day: str) -> Package:
    year = day[:4]
    when = date.fromisoformat(day)
    todays = [recall for recall in recalls() if recall.day == day]
    p = Package(
        f"tw.cec/recall-{day}",
        SOURCE,
        f"The {'recall votes' if len(todays) > 1 else 'recall vote'} Taiwan held on {label(day)}, by polling station "
        "from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for recall in todays:
        for artifact in artifacts(recall):
            p.uses(artifact)
    elect = key("election", f"RC-{day}")
    path = f"tw/{day}-recall"
    p.add("election", id=elect, polity_id=TAIWAN, kind="recall", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=elect, is_current=True)
    p.name(elect, f"{label(day)} Taiwanese recall vote{'s' if len(todays) > 1 else ''}", "en", "official")
    p.name(elect, "罷免投票", "zh", "native", False)
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    notes: list[str] = []
    for recall in todays:
        t = table(recall)
        contest = key("contest", f"RC-{day}", recall.theme)
        result_set = key("result_set", f"RC-{day}", recall.theme)
        p.add(
            "contest",
            id=contest,
            election_id=elect,
            kind="ballot_question",
            district_area_id=district_area(recall),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="recall_question")
        p.name(contest, english(recall), "en", "official")
        p.name(contest, recall.name, "zh", "native", False)
        p.add("slug", path=f"{path}/{slug_of(recall)}", entity_id=contest, is_current=True)
        options = []
        for position, (name_en, name_zh) in enumerate(ANSWERS, 1):
            option = key("option", f"RC-{day}", recall.theme, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=name_zh,
            )
            p.name(option, name_en, "en", "english")
            p.name(option, name_zh, "zh", "native", False)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=NOTE, lang="en")
        passed = recall.result == "Y"
        quorum = t.total["agree"] * 4 >= t.total["registered"] and t.total["agree"] > t.total["disagree"]
        if recall.result not in ("Y", "N") or (year >= "2016" and passed != quorum):
            raise ValueError(f"{recall.name}: the listed result {recall.result} disagrees with the votes")
        p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won" if passed else "lost")
        p.add("outcome", result_set_id=result_set, option_id=options[1], kind="lost" if passed else "won")
        for option, field in zip(options, ("agree", "disagree"), strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=t.total[field])
        for measure, field in MEASURES:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=t.total[field])
        seen: set[tuple[str, str, str]] = set()
        for s in t.stations:
            if (s.district, s.village, s.station) in seen:
                raise ValueError(f"{recall.name}: station {s} repeats")
            seen.add((s.district, s.village, s.station))
            ucode = f"RC:{recall.theme}:{s.district}:{s.village}:{s.station}"
            unit = key("unit", ucode)
            unit_rows[unit] = {
                "id": unit,
                "polity_id": TAIWAN,
                "scheme_code": "tw.station",
                "code": ucode,
                "area_relation": "within",
                "area_id": area("tw.village", recall.county, s.district, s.village),
                "kind": "standard",
            }
            for option, votes in zip(options, (s.agree, s.disagree), strict=True):
                values.append((result_set, unit, option, 1, votes))
            for measure, field in MEASURES:
                values.append((result_set, unit, None, measure, getattr(s, field)))
        other = summary(recall)
        gaps = {k: (v, t.total[k]) for k, v in (other or {}).items() if t.total[k] != v}
        print(
            f"{day} {recall.name[:16]}: stations {len(t.stations)}, agree {t.total['agree']:,}, disagree "
            f"{t.total['disagree']:,}, registered {t.total['registered']:,}, results table "
            f"{'absent' if other is None else 'differs ' + str(gaps) if gaps else 'matches'}"
        )
        if gaps:
            notes.append(f"{recall.name}: the results table differs from the polling station table in {gaps}")
    if notes:
        p.add(
            "note",
            id=key("note", "differences", f"RC-{day}"),
            entity_id=elect,
            kind="caveat",
            body_md="Differences within the Commission's own tables, stored as published from the polling station "
            "table: " + "; ".join(notes) + ".",
            lang="en",
        )
    p.frame("reporting_unit", pl.DataFrame(list(unit_rows.values())))
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
    ).filter(pl.col("value") >= 0)
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
    for day in sorted({recall.day for recall in recalls()}):
        election(day).write()
