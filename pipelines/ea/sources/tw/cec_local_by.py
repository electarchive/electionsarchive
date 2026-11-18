"""Taiwan's by-elections for local offices from the Central Election Commission's election database: county and city
mayors (subjects C1 and C2), township, town, and county-city mayors (D2), and municipal and county councillors (T1
and T2). One package holds each by-election. The 2023 and later township and council by-elections and the 2020
Kaohsiung mayoral by-election are published as workbooks by polling station. The county mayoral by-elections of 1997,
2003, 2006, and 2007 are published only as scanned tables by township, which are read here by eye, checked against the
printed totals, and stored with the page and box of every number. The packages share `tw.cec/geo` and
`tw.cec/registry`, which call `extend_geo` and `registry` here."""

import functools
import json
import re
import zipfile
from datetime import date
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import polars as pl

from ea import raw
from ea.package import NAMESPACE, Package
from ea.sources.tw import cec_by, cec_local, cec_local_by_scans, cec_township
from ea.sources.tw.cec import (
    BASE,
    CEC,
    CITIES,
    INDEPENDENT,
    LOCAL_SYSTEM,
    PLACES,
    SOURCE,
    TAIWAN,
    area,
    key,
    number,
    office,
    sheets,
    slugify,
    station_number,
    text,
)
from ea.sources.tw.cec_by import NO_PARTY, Cand, Data, Row

SUBJECTS = ("C1", "C2", "D2", "T1", "T2")
LIST = "https://db.cec.gov.tw/static/elections/list/BEL_{}.json"
MAYOR = re.compile(r"^(?P<county>.{2}[縣市])第(?P<session>\d+)屆[縣市]長(?:出缺|缺額)?補選$")
TOWN = re.compile(r"^(?P<county>.{2}[縣市])(?P<township>.+?[鄉鎮市區])第(?P<session>\d+)屆[鄉鎮市區]長(?:缺額)?補選$")
COUNCIL = re.compile(r"^(?P<county>.{2}[縣市])議會第(?P<session>\d+)屆議員第(?P<district>\d+)選舉區缺額補選$")


class Local(NamedTuple):
    subject: str
    day: str
    theme: str
    name: str
    session: int
    county: str
    township: str | None  # the township of a township mayoral by-election
    district: int | None  # the council constituency of a council by-election


@functools.cache
def elections() -> list[Local]:
    found: list[Local] = []
    for subject in SUBJECTS:
        artifact = raw.fetch(SOURCE, LIST.format(subject), suffix=".json")
        for entry in json.loads(artifact.path.read_text(encoding="utf-8")):
            for place in entry["time_items"]:
                for item in place["theme_items"]:
                    name = str(item["theme_name"])
                    pattern = {"C1": MAYOR, "C2": MAYOR, "D2": TOWN, "T1": COUNCIL, "T2": COUNCIL}[subject]
                    match = pattern.match(name)
                    if match is None:
                        raise ValueError(f"{name}: cannot read the name")
                    found.append(
                        Local(
                            subject,
                            str(item["vote_date"]),
                            str(item["theme_id"]),
                            name,
                            int(match["session"]),
                            match["county"],
                            match["township"] if subject == "D2" else None,
                            int(match["district"]) if subject in ("T1", "T2") else None,
                        )
                    )
    return sorted(found, key=lambda e: (e.day, e.subject, e.name))


def list_url(e: Local) -> str:
    return f"{BASE}attachments/BEL/{e.subject}/{e.theme}/list.json"


def file_url(e: Local, name: str) -> str:
    return f"{BASE}attachments/BEL/{e.subject}/{e.theme}/{quote(name)}"


@functools.cache
def file_names(e: Local) -> list[str]:
    data = json.loads(raw.fetch(SOURCE, list_url(e), suffix=".json").path.read_text(encoding="utf-8"))
    return [str(item["file_name"]) for item in data]


def urls(e: Local) -> list[str]:
    return [list_url(e), *(file_url(e, name) for name in file_names(e))]


def fetch() -> None:
    for e in elections():
        for address in urls(e):
            raw.fetch(SOURCE, address, suffix=address[address.rindex(".") :])
    fetch_districts()


def artifacts(e: Local) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, address) for address in urls(e)]


# --- the data of each by-election

TOWNSHIP_EN = {
    "草屯鎮": "Caotun Township",
    "新埤鄉": "Xinpi Township",
    "蘭嶼鄉": "Lanyu Township",
    "苗栗市": "Miaoli City",
    "虎尾鎮": "Huwei Township",
    "麥寮鄉": "Mailiao Township",
    "長治鄉": "Changzhi Township",
    "大武鄉": "Dawu Township",
    "北斗鎮": "Beidou Township",
    "富里鄉": "Fuli Township",
}
DIGITIZED = 8
COUNCIL_DAY = "2022-11-26"  # the general election of the councils' current terms


class Scanned(NamedTuple):
    cands: tuple[cec_local_by_scans.Cand, ...]
    page: int


def station_workbooks(e: Local) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in file_names(e):
        if "各投開票所" in name:
            found["stations"] = name
        elif "概況" in name:
            found["summary"] = name
        elif "得票數一覽表" in name:
            found["totals"] = name
    if set(found) != {"stations", "summary", "totals"}:
        raise ValueError(f"{e.name}: unexpected files {file_names(e)}")
    return found


def workbook(e: Local, kind: str) -> list[list[object]]:
    name = station_workbooks(e)[kind]
    return sheets(name, raw.cached(SOURCE, file_url(e, name)).path.read_bytes())[0][1]


def roc(year: int, month: int, day: int) -> str:
    return date(year + 1911, month, day).isoformat()


def kaohsiung(e: Local) -> Data:
    """The 2020 Kaohsiung mayoral by-election, from the workbooks in the Commission's zip file."""
    zip_name = next(name for name in file_names(e) if name.endswith(".zip"))
    archive = zipfile.ZipFile(raw.cached(SOURCE, file_url(e, zip_name)).path)
    members: dict[str, bytes] = {}
    for info in archive.infolist():
        # the archive names its files in Big5 without flagging it
        name = info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("cp950")
        if name.endswith(".xls"):
            members[name.rsplit("/", 1)[-1]] = archive.read(info)

    def table(prefix: str) -> list[list[object]]:
        name = next(name for name in members if name.startswith(prefix))
        return sheets(name, members[name])[0][1]

    roster = [row for row in table("表8-")[2:] if number(row[0])]
    cands: list[Cand] = []
    for row in sorted(roster, key=lambda row: number(row[0]) or 0):
        year, month, day = (int(part) for part in text(row[3]).split("/"))
        cands.append(
            Cand(
                number(row[0]) or 0,
                text(row[1]),
                text(row[4]) or NO_PARTY,
                {"男": "1", "女": "2"}.get(text(row[2]), ""),
                roc(year, month, day),
                text(row[6]) == "是",
                number(row[5]) or 0,
            )
        )
    rows = table("表3-")
    first = 3
    start = next(i for i, row in enumerate(rows) if text(row[0]) == "總計")
    count = len(cands)
    total_row = rows[start]
    total = {
        "valid": number(total_row[first + count]) or 0,
        "invalid": number(total_row[first + count + 1]) or 0,
        "cast": number(total_row[first + count + 2]) or 0,
        "registered": number(total_row[first + count + 6]) or 0,
    }
    out: list[Row] = []
    township = ""
    for row in rows[start + 1 :]:
        label, village, code = text(row[0]), text(row[1]), station_number(row[2])
        if village and code:
            out.append(
                Row(
                    township,
                    village,
                    code,
                    [number(row[first + index]) for index in range(count)],
                    number(row[first + count]),
                    number(row[first + count + 1]),
                    number(row[first + count + 2]),
                    number(row[first + count + 6]),
                )
            )
        elif label and not village and number(row[first]) is not None:
            township = label
    summary = next(row for row in table("表9-") if number(row[1]) and number(row[9]))
    return Data(
        cands,
        out,
        total,
        number(summary[5]),
        {
            "registered": number(summary[1]),
            "cast": number(summary[8]),
            "valid": number(summary[9]),
            "invalid": number(summary[10]),
        },
        False,
    )


def scan_names(e: Local) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in file_names(e):
        found["stations" if "各投開票所" in name else "candidates" if "候選人" in name else "summary"] = name
    return found


def scan_path(e: Local) -> raw.Artifact:
    return raw.cached(SOURCE, file_url(e, scan_names(e)["stations"]))


def scanned(e: Local) -> Data:
    scan = cec_local_by_scans.SCANS[e.day]
    cands = [
        Cand(
            cand.no,
            cand.name,
            cand.party or NO_PARTY,
            cand.sex,
            roc(*cand.birth),
            cand.elected,
            cand.total,
        )
        for cand in scan.cands
    ]
    rows = [
        Row(line.name, "", "", list(line.votes), line.valid, line.invalid, line.cast, line.registered)
        for line in scan.lines
    ]
    total = {
        "valid": scan.total.valid,
        "invalid": scan.total.invalid,
        "cast": scan.total.cast,
        "registered": scan.total.registered,
    }
    return Data(cands, rows, total, 1, dict(total), False)


@functools.cache
def data(e: Local) -> Data:
    if e.subject == "C1":
        return kaohsiung(e)
    if e.subject == "C2":
        return scanned(e)
    return cec_by.workbook_rows(e.name, lambda kind: workbook(e, kind))


# --- the geo and registry packages


def single(village: str) -> bool:
    """Whether a station row names one village, not the list of villages that vote at one station."""
    return "," not in village and "、" not in village


def extend_geo(counties: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    for e in elections():
        counties.add(e.county)
        for row in data(e).rows:
            townships.add((e.county, row.township))
            if row.village and single(row.village):
                villages.add((e.county, row.township, row.village))


def parties() -> set[str]:
    return {cand.party for e in elections() for cand in data(e).cands} - INDEPENDENT - {NO_PARTY}


def registry(p: Package) -> None:
    cited: dict[str, raw.Artifact] = {}
    for subject in SUBJECTS:
        artifact = raw.cached(SOURCE, LIST.format(subject))
        cited[artifact.storage_key] = artifact
    for e in elections():
        for found in artifacts(e):
            cited.setdefault(found.storage_key, found)
    for found in cited.values():
        p.artifact(found, CEC)


# --- the packages


def slug_of(e: Local) -> str:
    place = slugify(PLACES[e.county])
    if e.township:
        return f"{place}-{slugify(TOWNSHIP_EN[e.township])}"
    if e.district:
        return f"{place}-council-{e.district}"
    return place


def package_key(e: Local) -> str:
    return f"tw.cec/local-by-{e.day}-{slug_of(e)}"


def packages() -> list[str]:
    return [package_key(e) for e in elections()]


def contest_names(e: Local) -> tuple[str, str]:
    """The English and Chinese names of the contest."""
    year = e.day[:4]
    where = PLACES[e.county]
    if e.township:
        return (
            f"{year} {TOWNSHIP_EN[e.township]}, {where}, mayoral by-election",
            f"{e.county}{e.township}{e.name[e.name.index('屆') + 1 : e.name.index('長') + 1]}",
        )
    if e.district:
        body = "City Council" if e.county.endswith("市") else "County Council"
        return (
            f"{year} {where.rsplit(' ', 1)[0]} {body} District {e.district} by-election",
            f"{e.county}議會議員第{e.district}選舉區",
        )
    city = e.county in CITIES
    return (
        f"{year} {where} {'mayoral' if city else 'magistrate'} by-election",
        f"{e.county}{'市長' if city else '縣長'}",
    )


def method(e: Local) -> str:
    if e.township:
        return (
            "Each voter casts one vote for one candidate for the mayoralty of the township, town, or county-city. "
            "The candidate with the most votes wins the vacant office."
        )
    if e.district:
        return (
            "Each voter casts one vote for one candidate in the council's electoral area, and the candidate with the "
            "most votes fills the vacant seat."
        )
    return (
        "Each voter casts one vote for one candidate for the mayoralty or magistracy. The candidate with the most "
        "votes wins the vacant office."
    )


def council_area(e: Local) -> tuple[str, str]:
    """The council body and the electoral area of the current term that a council by-election fills."""
    assert e.district is not None
    cec_local.cached.cache_clear()
    types = {
        contest[0]
        for contest in cec_local.candidates(e.subject, COUNCIL_DAY)
        if contest[1] == e.county and contest[2] == e.district
    }
    if len(types) != 1:
        raise ValueError(f"{e.name}: expected one council constituency {e.district} in {e.county}, found {types}")
    return (
        cec_local.council_body(e.county),
        cec_local.district_area(e.subject, COUNCIL_DAY, e.county, next(iter(types)), e.district),
    )


def fetch_districts() -> None:
    """The 2022 councils' county files, which say which of a county's constituencies each by-election fills."""
    for subject in ("T1", "T2"):
        urls_ = [
            url
            for url in cec_local.urls(subject, COUNCIL_DAY)
            if url.endswith(".json") and ("/tickets/" in url) and ("/N/" in url or "/C/" in url)
        ]
        raw.fetch_many(cec_local.SOURCE, urls_, suffix=".json")


def election(e: Local) -> Package:
    when = date.fromisoformat(e.day)
    found = data(e)
    slug = slug_of(e)
    tag = f"local-by-{e.day}-{slug}"
    p = Package(
        package_key(e),
        SOURCE,
        f"The {contest_names(e)[0]} in Taiwan from the Central Election Commission",
        depends=[
            "tw.cec/geo",
            "tw.cec/registry",
            *(["tw.cec_local/geo", "tw.cec_local/registry"] if e.district else []),
        ],
    )
    p.external.add(TAIWAN)
    for artifact in artifacts(e):
        p.uses(artifact)
    english, native = contest_names(e)
    elect = key("election", tag)
    path = f"tw/{e.day}-local-by-election-{slug}"
    p.add("election", id=elect, polity_id=TAIWAN, kind="by_election", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=elect, is_current=True)
    p.name(elect, english, "en", "official")
    p.name(elect, e.name, "zh", "native", False)
    contest = key("contest", tag)
    result_set = key("result_set", tag)
    row: dict[str, object] = {
        "id": contest,
        "election_id": elect,
        "magnitude": 1,
        "round_number": 1,
        "default_result_kind": "counted",
    }
    if e.district:
        body, district = council_area(e)
        row |= {
            "kind": "body_seats",
            "body_id": body,
            "district_area_id": district,
            "electoral_system_id": cec_local.SYSTEM,
        }
    elif e.township:
        row |= {
            "kind": "office",
            "office_id": cec_township.LOCAL_OFFICE["D2"],
            "district_area_id": area("tw.township", e.county, e.township),
            "electoral_system_id": cec_township.LOCAL_SYSTEM["D2"],
        }
    else:
        row |= {
            "kind": "office",
            "office_id": office(e.county),
            "district_area_id": area("tw.county", e.county),
            "electoral_system_id": LOCAL_SYSTEM,
        }
    p.add("contest", **row)
    p.name(contest, english, "en", "official")
    p.name(contest, native, "zh", "native", False)
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
    scanned_here = e.subject == "C2"
    unit_word = "township or district" if scanned_here else "polling station"
    body_text = (
        f"Valid votes for each candidate and the valid and invalid ballots, ballots cast, and registered voters at each "
        f"{unit_word}, as published by the Central Election Commission. Voters registered is the number of voters on "
        f"the roll. Official totals are the Commission's own. {method(e)}"
    )
    if scanned_here:
        body_text += (
            " The Commission publishes this by-election only as a scanned table, and its figures were read from the "
            "page image. Every row and column of the table agrees with the totals printed on the page."
        )
    if found.inferred:
        body_text += (
            " The Commission's files do not flag the elected candidate. They give the number elected, one, and the "
            "elected candidate here is the one with the most votes."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body_text, lang="en")
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
        full = len(cand.birth) == 10
        p.add(
            "person",
            id=person,
            birth_date=date.fromisoformat(cand.birth) if full else None,
            birth_precision="day" if full else None,
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
        added = sum(r.votes[index] or 0 for r in found.rows)
        if added != cand.total:
            differences.append(
                f"the unit figures for {cand.name} add up to {added}, and the Commission's total is {cand.total}"
            )
    if found.seats != 1 or sum(1 for cand in found.cands if cand.victor) != 1:
        raise ValueError(f"{e.name}: expected one seat and one elected candidate")
    for measure, field in cec_by.MEASURES:
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=found.total[field])
        added = sum(getattr(r, field) or 0 for r in found.rows)
        if added != found.total[field]:
            differences.append(
                f"the unit figures add up to {added} for {field} ballots, and the Commission's total is "
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
            "The Commission's published figures disagree with each other. " + "; ".join(differences),
        )
        p.add(
            "note",
            id=key("note", "differences", tag),
            entity_id=elect,
            kind="caveat",
            body_md="The Commission's published figures disagree with each other in this election: "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    units: list[dict[str, object]] = []
    seen: set[str] = set()
    for r in found.rows:
        if scanned_here:
            code = f"localby:{e.day}:{slug}:{r.township}"
            where = {
                "scheme_code": "tw.township",
                "area_relation": "equals",
                "area_id": area("tw.township", e.county, r.township),
                "kind": "aggregate",
            }
        else:
            village = r.village if single(r.village) else ""
            code = f"localby:{e.day}:{slug}:{r.township}:{village}:{r.station}"
            where = {
                "scheme_code": "tw.station",
                "area_relation": "within",
                "area_id": area("tw.village", e.county, r.township, village)
                if village
                else area("tw.township", e.county, r.township),
                "kind": "standard",
            }
        if code in seen:
            raise ValueError(f"{e.name}: unit {code} repeats")
        seen.add(code)
        units.append({"id": key("unit", code), "polity_id": TAIWAN, "scheme_code": None, "code": code} | where)
    p.frame("reporting_unit", pl.DataFrame(units))
    values: list[tuple[str, str, str | None, int, int | None, int]] = []
    for unit, r in zip(units, found.rows, strict=True):
        uid = str(unit["id"])
        for option, votes in zip(options, r.votes, strict=True):
            values.append((result_set, uid, option, 1, votes, 0))
        for measure, field in cec_by.MEASURES:
            values.append((result_set, uid, None, measure, getattr(r, field), 0))
    boxes: list[tuple[str, str, str | None, int, tuple[float, float, float, float]]] = []
    if scanned_here:
        values = [(rs, u, o, m, v, DIGITIZED) for rs, u, o, m, v, _ in values]
        located = cec_local_by_scans.locate(scan_path(e).path, e.day)
        count = len(found.cands)
        columns = {
            **{("v", index): index for index in range(count)},
            ("m", 4): count,
            ("m", 5): count + 1,
            ("m", 3): count + 2,
            ("m", 2): count + 6,
        }
        page = p.id("page", 1)
        artifact = scan_path(e)
        p.add(
            "source_page",
            id=page,
            artifact_id=str(uuid5(NAMESPACE, f"source_artifact\x1f{CEC}\x1f{artifact.storage_key}")),
            page_number=1,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-1.png",
            width=round(cec_local_by_scans.PAGE_WIDTH * 100 / cec_local_by_scans.DPI),
            height=round(cec_local_by_scans.PAGE_HEIGHT * 100 / cec_local_by_scans.DPI),
        )
        for index, (unit, r) in enumerate(zip(units, found.rows, strict=True), start=1):
            uid = str(unit["id"])
            for option_index, option in enumerate(options):
                boxes.append((result_set, uid, option, 1, located[index, columns["v", option_index]]))
            for measure in (4, 5, 3, 2):
                boxes.append((result_set, uid, None, measure, located[index, columns["m", measure]]))
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value", "flags"], orient="row"
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
            flags=pl.col("flags"),
            value="value",
        ),
    )
    if boxes:
        p.frame(
            "value_locator",
            pl.DataFrame(
                [(rs, u, o, m, page, *box) for rs, u, o, m, box in boxes],
                schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                source_id=pl.lit(None, pl.String),
            ),
        )
    return p


def parse() -> None:
    for e in elections():
        election(e).write()
