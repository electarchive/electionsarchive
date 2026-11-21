"""Tokyo Metropolitan Assembly general election of 2025-06-22 by district and municipality, read from the
election commission's workbooks.

The commission publishes, for each election, a candidate-by-municipality workbook (候補者別得票数), a turnout
workbook (投票結果) with the electorate of each counting municipality, a count-breakdown workbook (開票結果内訳)
with valid, invalid, and cast ballots, and a winners-by-party workbook (党派別当選人数). Every district is one
or more cities, wards, towns, or villages (the islands are one district); each is a counting unit of its own.
"""

import csv
import functools
import re
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid5

import fastexcel
import shapely

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.jp import mlit

SOURCE = "jp.pref_gikai_tokyo"
BASE = "https://www.senkyo.metro.tokyo.lg.jp"
SCHEME = "jp.pref_assembly_district"
PREFECTURE = "13"
JAPAN = str(uuid5(NAMESPACE, "polity\x1fJP"))
TOKYO = str(uuid5(NAMESPACE, "polity\x1fJP-13"))
# Rows production already holds: the prefecture's area (jp.mlit) and the assembly body (jp.diet).
PREFECTURE_AREA = "1a150949-b5a7-51a1-b179-19f7d463d29a"
ASSEMBLY = "6664c5c3-72eb-5ab2-adce-d7243ab0196c"
PLACES = Path(__file__).with_name("pref_gikai_tokyo_places.tsv")
MEASURES = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5, "voters": 14}
SYSTEM_RULES = {"formula": "sntv"}
PUBLISHER = "東京都選挙管理委員会"
TITLE = "東京都議会議員選挙（令和7年6月22日執行）投開票結果"
LICENSE = (
    "Copyright Tokyo Metropolitan Government (site terms); counts published as facts with attribution, raw files "
    "not redistributed"
)
SYSTEM_NOTE = (
    "Each district elects its seats by single non-transferable vote: every voter names one candidate, and the "
    "candidates with the most votes win the seats, a one-seat district being first past the post. A winner needs a "
    "quarter of the district's valid votes divided by its seats (法定得票数, Public Offices Election Act art. 95). "
    "Districts are wards, cities, towns, and villages, alone or joined, as the metropolitan ordinance sets them; "
    "the islands form one district."
)
METHOD = (
    "Candidate votes by counting municipality from the election commission's candidate-by-municipality workbook "
    "(候補者別得票数), with the electorate on election day from its turnout workbook (投票結果) and valid, invalid, "
    "and cast ballots from its count-breakdown workbook (開票結果内訳). Votes for candidates whose names other "
    "candidates share are apportioned (按分) and carry decimals. The candidates marked as elected are the ones "
    "with the most votes, and the commission's winners-by-party table counts the same seats for each party."
)
HELD = "2025-06-22"
PAGE = f"{BASE}/election/togikai-all/togikai-sokuhou2025"
TURNOUT = f"{BASE}/documents/d/senkyo/r07togi_touhyo-1"
CANDIDATES = f"{BASE}/documents/d/senkyo/r07togi_kaihyo_tokuhyo-1"
BREAKDOWN = f"{BASE}/documents/d/senkyo/r07togi_kaihyo_uchiwake-1"
WINNERS = f"{BASE}/documents/d/senkyo/r07togi_kaihyou_touhabetsutousen-1"
FILES = (TURNOUT, CANDIDATES, BREAKDOWN, WINNERS)
VINTAGE = "2013"
INDEPENDENT = "無所属"
PACKAGES = ["geo", "registry", HELD]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id(name: str) -> str:
    return key("source", name)


def area_id(n: int) -> str:
    return key("area", SCHEME, f"{PREFECTURE}{n:02d}")


def party_id(party: str) -> str:
    return key("party", party)


def person_id(name: str) -> str:
    return key("person", PREFECTURE, name)


@functools.cache
def places() -> dict[str, dict[str, str]]:
    with PLACES.open(encoding="utf-8") as file:
        return {row["name"]: row for row in csv.DictReader(file, delimiter="\t")}


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in mlit.urls(VINTAGE):
        if f"_{PREFECTURE}_GML" in url:
            raw.fetch(mlit.SOURCE, url, suffix=".zip")


def sheet(url: str) -> list[list[str]]:
    """The first sheet of a cached workbook as NFKC-normalized, stripped strings, each row padded to 24 cells."""
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = reader.load_sheet(reader.sheet_names[0], header_row=None, dtypes="string").to_polars()
    rows = [[unicodedata.normalize("NFKC", v).strip() if v else "" for v in row] for row in frame.iter_rows()]
    return [row + [""] * (24 - len(row)) for row in rows]


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def district_name(text: str) -> str:
    return text.lstrip("☆★").strip()


def read_candidates(url: str) -> list[dict[str, Any]]:
    """Each district's candidates, votes by counting municipality, seats, and legal minimum.

    A district prints twice: first with the candidates' names, parties, and votes (a header row of ballot numbers,
    surname, given-name, and party rows, a total row, then one row per municipality for a district of several),
    then again as a bare repeat that ends with the legal minimum, which is read for it."""
    rows = sheet(url)
    out: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        seats = next((re.search(r"\d+", c) for c in row if c.startswith("定数")), None)
        if row[0] and not row[1] and row[2].isdigit():
            columns = [c for c, v in enumerate(row) if c >= 2 and v.isdigit()]
            total = next((c for c, v in enumerate(rows[i + 3]) if v == "合計"), None)
            names = [rows[i + 1][c] + rows[i + 2][c] for c in columns]
            parties = [rows[i + 3][c] for c in columns]
            assert rows[i + 4][0] == "計", (i, rows[i + 4][:3])
            name = district_name(row[0])
            fresh: list[dict[str, Any]] = [
                {"name": n.replace(" ", ""), "party": party, "votes": {}}
                for n, party in zip(names, parties, strict=True)
            ]
            if row[2] == "1":
                district: dict[str, Any] = {
                    "district": name,
                    "seats": 0,
                    "candidates": [],
                    "places": [],
                    "total": Decimal(0),
                }
                out.append(district)
                by_name[name] = district
            else:
                district = by_name[name]
            district["candidates"] += fresh
            if seats:
                district["seats"] = int(seats.group())
            if total is not None and rows[i + 4][total]:
                district["total"] = number(rows[i + 4][total])
            j = i + 5
            found = False
            while j < len(rows) and rows[j][0]:
                label = rows[j][0]
                if "計" not in label:
                    found = True
                    if label not in district["places"]:
                        district["places"].append(label)
                    for c, candidate in zip(columns, fresh, strict=True):
                        candidate["votes"][label] = number(rows[j][c])
                j += 1
            if not found:
                if not district["places"]:
                    district["places"] = [name]
                for c, candidate in zip(columns, fresh, strict=True):
                    candidate["votes"][name] = number(rows[i + 4][c])
        elif "法定得票数" in row:
            name = district_name(row[row.index("法定得票数") - 2])
            by_name[name]["threshold"] = number(row[row.index("法定得票数") + 1])
            by_name[name]["forfeit"] = number(row[row.index("供託物没収点") + 1])
    return out


def read_turnout(url: str) -> dict[str, Decimal]:
    """The electorate on election day (20:00) of each counting municipality."""
    out: dict[str, Decimal] = {}
    for row in sheet(url):
        name = row[1].lstrip("☆★").strip()
        if name in places():
            out[name] = number(row[4])
    return out


def read_breakdown(url: str) -> dict[str, dict[str, Decimal]]:
    out: dict[str, dict[str, Decimal]] = {}
    for row in sheet(url):
        name = row[0].strip()
        if name in places():
            out[name] = {
                "valid": number(row[4]),
                "invalid": number(row[5]),
                "cast": number(row[6]),
                "voters": number(row[9]),
            }
    return out


def read_winners(url: str) -> dict[str, int]:
    """The commission's seats of each district."""
    out: dict[str, int] = {}
    for row in sheet(url):
        if row[0] and row[0] not in ("党派名", "合計", "計") and row[1:2] and row[1].isdigit():
            out[district_name(row[0])] = int(row[1])
    return out


@functools.cache
def data() -> dict[str, Any]:
    districts = read_candidates(CANDIDATES)
    turnout, breakdown = read_turnout(TURNOUT), read_breakdown(BREAKDOWN)
    for n, district in enumerate(districts, 1):
        ranked = sorted(district["candidates"], key=lambda c: -sum(c["votes"].values()))
        for rank, candidate in enumerate(ranked):
            candidate["elected"] = rank < district["seats"]
        district["n"] = n
        district["figures"] = {
            place: {"registered": turnout[place], **breakdown[place]} for place in district["places"]
        }
    winners = read_winners(WINNERS)
    for district in districts:
        assert winners[district["district"]] == district["seats"], district["district"]
    return {"districts": districts, "winners": winners}


def parse() -> None:
    result = data()
    districts = result["districts"]
    print(
        f"{HELD}: {len(districts)} districts, {sum(d['seats'] for d in districts)} seats, "
        f"{sum(len(d['candidates']) for d in districts)} candidates"
    )
    build()


def commission_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id("commission"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license=LICENSE,
        license_url="https://www.senkyo.metro.tokyo.lg.jp/",
        attribution="出典：東京都選挙管理委員会",
        notes="Candidate-by-municipality, turnout, count-breakdown, and winners-by-party workbooks of the "
        "commission's results page for the 2025 election.",
    )


@functools.cache
def shapes() -> dict[str, shapely.Geometry]:
    links = [url for url in mlit.urls(VINTAGE) if f"_{PREFECTURE}_GML" in url]
    features = mlit.read(VINTAGE, links)
    simple = shapely.coverage_simplify([geometry for *_, geometry in features], mlit.TOLERANCE)
    return {code: shape for (code, *_), shape in zip(features, simple, strict=True)}


def member_codes(members: list[str]) -> list[str]:
    return sorted(places()[m]["code"] for m in members)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Tokyo Metropolitan Assembly districts, each the union of the wards, cities, towns, and villages the "
        "metropolitan ordinance gives it, with their shapes from MLIT N03",
    )
    p.external |= {JAPAN, TOKYO, PREFECTURE_AREA, SCHEME}
    commission_source(p)
    p.add(
        "source",
        id=source_id("mlit"),
        kind="official_body",
        publisher=mlit.PUBLISHER,
        title=mlit.TITLE,
        url=mlit.LANDING,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="「国土数値情報（行政区域データ）」（国土交通省）を加工して作成",
    )
    for url in mlit.urls(VINTAGE):
        if f"_{PREFECTURE}_GML" in url:
            p.artifact(raw.cached(mlit.SOURCE, url), source_id("mlit"))
    for url in FILES:
        p.uses(raw.cached(SOURCE, url))
    day = date.fromisoformat(HELD)
    p.add(
        "boundary_set",
        id=key("boundary_set", HELD),
        scheme_code=SCHEME,
        vintage=HELD,
        valid_from=day,
        valid_to=None,
        as_of=day,
    )
    for district in data()["districts"]:
        n = district["n"]
        area = area_id(n)
        p.add("area", id=area, scheme_code=SCHEME)
        p.add("area_code", area_id=area, code=f"{PREFECTURE}{n:02d}", valid_from=day, valid_to=None)
        p.add("area_parent", area_id=area, parent_id=PREFECTURE_AREA, valid_from=day, valid_to=None)
        p.name(area, district["district"], "ja", "official")
        p.name(area, f"Tokyo Metropolitan Assembly district {n}", "en", "english", False)
        merged = shapely.set_srid(
            mlit.polygons(mlit.merge([shapes()[code] for code in member_codes(district["places"])])), 4326
        )
        label_point = max(shapely.get_parts(merged), key=shapely.area).representative_point()
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=day,
            valid_to=None,
            geom=shapely.to_wkb(merged, hex=True, include_srid=True),
            label_point=shapely.to_wkb(shapely.set_srid(label_point, 4326), hex=True, include_srid=True),
            quality="official",
            source_id=source_id("mlit"),
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system, parties, and candidates of the 2025 Tokyo Metropolitan Assembly election",
    )
    p.external |= {JAPAN}
    system = key("electoral_system", "sntv")
    p.add("electoral_system", id=system, polity_id=JAPAN, rules=SYSTEM_RULES, rules_version=1)
    p.name(
        system,
        "Tokyo Metropolitan Assembly districts, single non-transferable vote with a legal minimum",
        "en",
        "official",
    )
    p.name(system, "東京都議会議員選挙（単記非移譲式、法定得票数あり）", "ja", "native", False)
    p.add(
        "note",
        id=key("note", system, "methodology"),
        entity_id=system,
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    candidates = [c for d in data()["districts"] for c in d["candidates"]]
    for party in sorted({c["party"] for c in candidates} - {INDEPENDENT}):
        p.add("party", id=party_id(party), kind="party", polity_id=JAPAN)
        p.name(party_id(party), party, "ja", "official")
    for person in sorted({c["name"] for c in candidates}):
        p.add("person", id=person_id(person))
        p.name(person_id(person), person, "ja", "official")
    return p


def election() -> Package:
    result = data()
    day = date.fromisoformat(HELD)
    p = Package(
        f"{SOURCE}/{HELD}",
        SOURCE,
        "The 2025 Tokyo Metropolitan Assembly election by district and municipality, from the election "
        "commission's candidate, turnout, and count-breakdown workbooks",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {JAPAN, TOKYO, ASSEMBLY}
    commission = source_id("commission")
    p.artifact(raw.cached(SOURCE, PAGE), commission)
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), commission)
    election_id = key("election", HELD)
    p.add("election", id=election_id, polity_id=TOKYO, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, "2025 Tokyo Metropolitan Assembly election", "en", "official")
    p.name(election_id, "東京都議会議員選挙", "ja", "native", False)
    path = f"jp/tokyo/{HELD}-assembly"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    system = key("electoral_system", "sntv")
    for district in result["districts"]:
        n, name, seats = district["n"], district["district"], district["seats"]
        tag = f"{HELD}:{name}"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=area_id(n),
            magnitude=seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
            boundary_set_id=key("boundary_set", HELD),
        )
        p.name(contest, f"Tokyo Metropolitan Assembly district {n}", "en", "official")
        p.name(contest, name, "ja", "native", False)
        p.add("slug", path=f"{path}/district-{n}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=commission,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set, "methodology"),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        units: dict[str, str] = {}
        for place in district["places"]:
            unit = units[place] = places()[place]["unit_id"]
            p.external.add(unit)
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=area_id(n),
                valid_from=day,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
                method="official_list",
            )
            for field, measure in MEASURES.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=district["figures"][place][field],
                )
        for position, c in enumerate(district["candidates"], 1):
            ctag = f"{tag}:{c['name']}"
            option, person = key("option", ctag), person_id(c["name"])
            party = None if c["party"] == INDEPENDENT else party_id(c["party"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=c["name"],
            )
            p.add(
                "candidacy",
                id=key("candidacy", ctag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=party,
                is_independent=party is None,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if c["elected"] else "lost",
                seats=1 if c["elected"] else None,
            )
            for place, votes in c["votes"].items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[place],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
    return p


def build() -> list[str]:
    return [str(p.write()) for p in (geo(), registry(), election())]


def emit() -> None:
    package.run("load", [f"{SOURCE}/{name}" for name in PACKAGES])
