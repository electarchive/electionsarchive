"""Saitama Prefectural Assembly elections by district and municipality, built as packages.

The prefectural election commission publishes, for each general election, a workbook of candidate votes by
municipality (議開1表), a turnout workbook (議投2表), and workbooks of winners by party and by new, incumbent, and
former status. A district with no more candidates than seats holds no vote, and its workbook sheet lists no
candidates; those winners come from the commission's printed record of the 2023 elections.

Municipalities, wards, the prefecture, its assembly, and the country already exist in the archive, so the packages
reference their ids and mint only the districts, their shapes, the election, and its candidates.
"""

import csv
import functools
import itertools
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.jp import mlit
from ea.sources.jp.pref_gikai import PLACE, amount, grid, label

SOURCE = "jp.pref_gikai_saitama"
BASE = "https://www.pref.saitama.lg.jp"
SCHEME = "jp.pref_assembly_district"
PREFECTURE = "11"
JAPAN = str(uuid5(NAMESPACE, "polity\x1fJP"))
SAITAMA = str(uuid5(NAMESPACE, "polity\x1fJP-11"))
# Rows production already holds: the prefecture's area, and the assembly body jp.diet wrote.
PREFECTURE_AREA = "3bd3c750-d698-5c78-b87e-1c0bc3841fb8"
ASSEMBLY = "871caa1c-558d-58c2-a656-edae396b6a99"
PLACES = Path(__file__).with_name("pref_gikai_saitama_places.tsv")
DISTRICT = re.compile(r"^[東西南北]第\d+区$")
STATUS = re.compile(r"^(.*)\((新|現|元)\)$")
INDEPENDENT = {"無所属", "無所属(政治団体名)"}
MEASURES = {"registered": 2, "voters": 14}
SYSTEM_RULES = {"formula": "sntv"}
PUBLISHER = "埼玉県選挙管理委員会"
TITLE = "埼玉県議会議員一般選挙 選挙区別候補者別市区町村別得票調べ・投票調べ・党派別新現元別当選者数調べ"
LICENSE = (
    "Copyright Saitama Prefecture (site terms); counts published as facts with attribution, raw files not redistributed"
)
SYSTEM_NOTE = (
    "Each district elects its seats by single non-transferable vote: every voter names one candidate, and the "
    "candidates with the most votes win the seats, a one-seat district being first past the post. A winner needs a "
    "quarter of the district's valid votes divided by its seats (法定得票数, Public Offices Election Act art. 95). "
    "Districts are cities, towns, villages, and counties, alone or joined, as the prefecture's ordinance sets them. "
    "A district with no more candidates than seats holds no vote, and its candidates are elected unopposed."
)
METHOD = (
    "Candidate votes by municipality from the prefectural election commission's final count table "
    "(選挙区別候補者別市区町村別得票調べ, 確定), with the electorate and voters from its turnout table "
    "(投票調べ). Votes for candidates whose names other candidates share are apportioned (按分) and carry three "
    "decimals. In every district the municipalities add up to the printed district total, the printed legal "
    "minimum equals a quarter of the votes divided by the seats, the ranked winners are the candidates within the "
    "seats, and the winners by party and by new, incumbent, and former status equal the commission's tables."
)
TACIT = "No vote was held: the district had as many candidates as seats, and all were elected unopposed."


@dataclass(frozen=True)
class Round:
    page: str
    count: str
    turnout: str
    parties: str
    status: str
    vintage: str


DOCS = f"{BASE}/documents"
ROUNDS = {
    "2023-04-09": Round(
        f"{BASE}/e1701/r5_kengisenkekka.html",
        f"{DOCS}/232922/es_kaihyo_ka.xls",
        f"{DOCS}/232922/es_tohyo_k_050414.xls",
        f"{DOCS}/232922/es_tohabetu_k.xls",
        f"{DOCS}/232922/es_shingenmotobetu_k.xls",
        "2013",
    ),
}
# The commission's record of the 2023 elections prints the winners of unopposed districts (page "二 候補者別得票数
# 及び当選人"); the count workbook lists no candidates for them. (name, party, status), in ballot order.
RECORD = f"{BASE}/documents/252720/05-2.pdf"
RECORD_PAGE = f"{BASE}/e1701/r05touitsusenkyonokiroku.html"
UNOPPOSED = {
    "2023-04-09": {
        "南第3区": [("くさかべ 伸三", "自由民主党", "現")],
        "南第4区": [("関根 信明", "自由民主党", "現"), ("小川 ひさし", "立憲民主党", "新")],
        "南第5区": [("藤井 たけし", "自由民主党", "現")],
        "南第15区": [("あらい 一徳", "自由民主党", "現")],
        "南第17区": [("鈴木 正人", "自由民主党", "現")],
        "南第18区": [("平松 だいすけ", "無所属", "現"), ("安藤 ともき", "公明党", "現")],
        "西第3区": [("内沼 ひろし", "自由民主党", "現")],
        "西第8区": [("こやの 五雄", "自由民主党", "現")],
        "西第11区": [("長峰 秀和", "自由民主党", "新")],
        "西第12区": [("松坂 よしひろ", "無所属", "現"), ("横川 まさや", "自由民主党", "現")],
        "西第13区": [("小久保 けんいち", "自由民主党", "現")],
        "北第1区": [("新井 ごう", "自由民主党", "現"), ("阿左美 けんじ", "自由民主党", "現")],
        "北第2区": [("斉藤 くにあき", "自由民主党", "現"), ("飯塚 としひこ", "自由民主党", "現")],
        "北第3区": [
            ("小川 しんいちろう", "自由民主党", "現"),
            ("神尾 たかよし", "自由民主党", "現"),
            ("江原 くみ子", "無所属", "現"),
        ],
        "東第9区": [("宇田川 ゆきお", "自由民主党", "現")],
        "東第11区": [("きら 英敏", "自由民主党", "現")],
    },
}
PACKAGES = ["geo", "registry", *ROUNDS]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id(name: str) -> str:
    return key("source", name)


@functools.cache
def places() -> dict[str, dict[str, str]]:
    with PLACES.open(encoding="utf-8") as file:
        return {row["code"]: row for row in csv.DictReader(file, delimiter="\t")}


@functools.cache
def codes() -> dict[str, str]:
    """The code each place name in the commission's tables stands for: a municipality by its name, a ward of
    Saitama City by the city's name before it."""
    found: dict[str, str] = {}
    for code, row in places().items():
        text = f"さいたま市{row['name']}" if row["scheme"] == "jp.ward" else row["name"]
        found[text] = code
    return found


def fetch() -> None:
    for record in ROUNDS.values():
        raw.fetch(SOURCE, record.page, suffix=".html")
        for url in (record.count, record.turnout, record.parties, record.status):
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)
    raw.fetch(SOURCE, RECORD_PAGE, suffix=".html")
    raw.fetch(SOURCE, RECORD, suffix=".pdf")
    for vintage in {record.vintage for record in ROUNDS.values()}:
        for url in mlit.urls(vintage):
            if f"_{PREFECTURE}_GML" in url:
                raw.fetch(mlit.SOURCE, url, suffix=".zip")


def read_count(path: Path) -> list[dict]:
    districts: list[dict] = []
    current: dict | None = None
    block: list[dict] = []
    for row in grid(path):
        if row.get(1, "").startswith("定数"):
            current = {
                "district": label(row),
                "seats": int(row[1].removeprefix("定数").strip()),
                "unopposed": "無投票" in row.get(2, ""),
                "candidates": [],
                "places": [],
                "subtotal": None,
                "threshold": None,
                "forfeit": None,
            }
            districts.append(current)
            block = []
            continue
        if current is None or current["unopposed"]:
            continue
        if row.get(2) == "届出番号":
            block = [
                {"number": int(row[i]), "column": i, "votes": {}} for i in sorted(row) if i >= 3 and row[i].isdigit()
            ]
            current["candidates"].extend(block)
        elif row.get(2) == "候補者氏名":
            for candidate in block:
                candidate["name"] = re.sub(r"\s+", " ", row[candidate["column"]])
        elif row.get(2) == "政党等名":
            for candidate in block:
                text = unicodedata.normalize("NFKC", row[candidate["column"]]).replace(" ", "")
                found = STATUS.match(text)
                if found is None:
                    raise ValueError(f"{current['district']}: no status in {text}")
                candidate["party"], candidate["status"] = found.groups()
        elif label(row) == "確定" and 1 in row:
            place = row[1]
            if place == "計":
                current["subtotal"] = {
                    candidate["number"]: str(amount(row, candidate["column"], None)) for candidate in block
                } | {0: str(amount(row, 13, None))}
            else:
                current["places"].append(place)
                for candidate in block:
                    candidate["votes"][place] = str(amount(row, candidate["column"], None))
                candidate_total = Decimal(str(amount(row, 13, None)))
                current.setdefault("totals", {})[place] = str(candidate_total)
        elif row.get(1) == "順位":
            for candidate in block:
                candidate["rank"] = int(row[candidate["column"]])
        elif row.get(9) == "法定得票数":
            current["threshold"] = row[10].strip("()").replace(",", "").strip()
            current["forfeit"] = row[13].strip("()").replace(",", "").strip()
    for district in districts:
        for candidate in district["candidates"]:
            candidate.pop("column")
    return districts


def read_turnout(path: Path) -> dict[str, tuple[str, str]]:
    """The electorate and voters of each place; a district of several municipalities prints its sum first."""
    found: dict[str, tuple[str, str]] = {}
    for row in grid(path):
        place = row.get(1, "")
        if not PLACE.match(place) or "計" in place or 4 not in row:
            continue
        found[place] = (row[4].replace(",", ""), row[7].replace(",", ""))
    return found


def read_members(path: Path) -> dict[str, list[str]]:
    """Each district's municipalities in order, from the turnout workbook. A district of several municipalities
    prints its sum first, then one row for each place."""
    members: dict[str, list[str]] = {}
    current = ""
    for row in grid(path):
        if DISTRICT.match(row.get(0, "")):
            current = row[0]
            members[current] = []
        place = row.get(1, "")
        if current and PLACE.match(place):
            members[current].append(place)
    return members


def read_status(path: Path) -> dict[str, dict[tuple[str, str], int]]:
    """Winners of each district by party and by status (新, 現, 元), as the commission's table prints them."""
    parties: dict[int, str] = {}
    out: dict[str, dict[tuple[str, str], int]] = {}
    current = ""
    for row in grid(path):
        if row.get(2) in ("新", "現", "元", "計"):
            if 0 in row:
                current = label(row)
            if row[2] != "計":
                for column, party in parties.items():
                    if (value := row.get(column)) and value != "0":
                        out.setdefault(current, {})[party, row[2]] = int(value)
        elif 3 in row and 0 not in row and not row[3].isdigit():
            parties = {i: v for i, v in row.items() if i >= 3 and v != "計"}
    return out


def prepare(held: str) -> dict:
    """Read and check one election: every district's candidates, winners, and places."""
    record = ROUNDS[held]
    districts = read_count(raw.cached(SOURCE, record.count).path)
    turnout_path = raw.cached(SOURCE, record.turnout).path
    turnout = read_turnout(turnout_path)
    members = read_members(turnout_path)
    status = read_status(raw.cached(SOURCE, record.status).path)
    unopposed = UNOPPOSED.get(held, {})
    if [d["district"] for d in districts] != list(members):
        raise ValueError(f"{held}: the count and turnout workbooks list different districts")
    for district in districts:
        name, seats = district["district"], district["seats"]
        where = f"{held} {name}"
        district["members"] = members[name]
        for place in members[name]:
            if place not in codes():
                raise ValueError(f"{where}: unknown place {place}")
        if district["unopposed"]:
            given = unopposed.get(name)
            if given is None or len(given) != seats:
                raise ValueError(f"{where}: unopposed with no winners for {seats} seats")
            district["candidates"] = [
                {"number": i, "name": text, "party": party, "status": state, "votes": {}, "rank": None}
                for i, (text, party, state) in enumerate(given, 1)
            ]
            district["winners"] = district["candidates"]
            continue
        candidates = district["candidates"]
        if [c["number"] for c in candidates] != list(range(1, len(candidates) + 1)):
            raise ValueError(f"{where}: candidate numbers are not 1..{len(candidates)}")
        if district["places"] != members[name]:
            raise ValueError(f"{where}: the count and turnout workbooks list different places")
        for place in district["places"]:
            if place not in turnout:
                raise ValueError(f"{where}: {place} has no turnout row")
        valid = sum((Decimal(c["votes"][place]) for c in candidates for place in district["places"]), Decimal(0))
        printed = sum((Decimal(v) for v in district["totals"].values()), Decimal(0))
        if valid != printed:
            raise ValueError(f"{where}: candidates sum to {valid}, the places print {printed}")
        share = {c["number"]: sum((Decimal(v) for v in c["votes"].values()), Decimal(0)) for c in candidates}
        if district["subtotal"] is not None:
            if any(share[number] != Decimal(district["subtotal"][number]) for number in share):
                raise ValueError(f"{where}: the places do not sum to the printed district total")
            if Decimal(district["subtotal"][0]) != valid:
                raise ValueError(f"{where}: the printed district total is {district['subtotal'][0]}, not {valid}")
        for line, divisor, text in (
            (district["threshold"], 4, "legal minimum"),
            (district["forfeit"], 10, "deposit line"),
        ):
            rule = (valid / seats / divisor).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            if line is None or abs(rule - Decimal(line)) > Decimal("0.001"):
                raise ValueError(f"{where}: the {text} is {line}, the rule gives {rule}")
        ordered = sorted(candidates, key=lambda c: c["rank"])
        if [c["rank"] for c in ordered] != list(range(1, len(candidates) + 1)):
            raise ValueError(f"{where}: ranks are not 1..{len(candidates)}")
        if any(share[a["number"]] < share[b["number"]] for a, b in itertools.pairwise(ordered)):
            raise ValueError(f"{where}: ranks do not follow the votes")
        district["winners"] = ordered[:seats]
        if any(share[c["number"]] < Decimal(district["threshold"]) for c in district["winners"]):
            raise ValueError(f"{where}: a ranked winner falls below the legal minimum")
        for place in district["places"]:
            if Decimal(turnout[place][1]) < Decimal(district["totals"][place]):
                raise ValueError(f"{where} {place}: fewer voters than candidate votes")
    for district in districts:
        won: dict[tuple[str, str], int] = {}
        for c in district["winners"]:
            won[c["party"], c["status"]] = won.get((c["party"], c["status"]), 0) + 1
        printed_ = {k: v for k, v in status.get(district["district"], {}).items() if v}
        if won != printed_:
            raise ValueError(f"{held} {district['district']}: winners {won}, the commission's table prints {printed_}")
    # The turnout workbook's prefecture row is the sum of the places that voted.
    sums = [sum(int(turnout[place][i]) for place in turnout) for i in (0, 1)]
    return {"districts": districts, "turnout": turnout, "registered": sums[0], "voters": sums[1]}


@functools.cache
def rounds() -> dict[str, dict]:
    return {held: prepare(held) for held in ROUNDS}


def order() -> list[str]:
    """The districts in the order their areas are numbered: the latest election's, then any a former one added."""
    names: list[str] = []
    for held in sorted(ROUNDS, reverse=True):
        names += [d["district"] for d in rounds()[held]["districts"] if d["district"] not in names]
    return names


def following(held: str) -> str | None:
    later = sorted(day for day in ROUNDS if day > held)
    return later[0] if later else None


def area_id(n: int) -> str:
    return key("area", SCHEME, f"{PREFECTURE}{n:02d}")


def commission_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id("commission"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=f"{BASE}/e1701/kengi-top.html",
        license=LICENSE,
        license_url=f"{BASE}/a0301/tyosaku-rinku.html",
        attribution="出典：埼玉県選挙管理委員会",
        notes="Final count, turnout, and winners tables, read from the commission's Excel workbooks; the winners of "
        "unopposed districts from its printed record of the 2023 elections.",
    )


@functools.cache
def shapes(vintage: str) -> dict[str, shapely.Geometry]:
    if not mlit.VINTAGES[vintage][3]:
        raise ValueError(f"N03 {vintage} comes as one file for Japan; use a vintage published by prefecture")
    links = [url for url in mlit.urls(vintage) if f"_{PREFECTURE}_GML" in url]
    features = mlit.read(vintage, links)
    simple = shapely.coverage_simplify([geometry for *_, geometry in features], mlit.TOLERANCE)
    return {code: shape for (code, *_), shape in zip(features, simple, strict=True)}


def outline(p: Package, area: str, shape: shapely.Geometry, held: str, until: str | None) -> None:
    shape = shapely.set_srid(mlit.polygons(shape), 4326)
    label_point = max(shapely.get_parts(shape), key=shapely.area).representative_point()
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=date.fromisoformat(held),
        valid_to=date.fromisoformat(until) if until else None,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(shapely.set_srid(label_point, 4326), hex=True, include_srid=True),
        quality="official",
        source_id=source_id("mlit"),
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Saitama Prefectural Assembly districts, each the union of the municipalities and wards the prefectural "
        "ordinance gives it, with their shapes from MLIT N03",
    )
    p.external |= {JAPAN, SAITAMA, PREFECTURE_AREA, SCHEME}
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
    for vintage in sorted({record.vintage for record in ROUNDS.values()}):
        for url in mlit.urls(vintage):
            if f"_{PREFECTURE}_GML" in url:
                p.artifact(raw.cached(mlit.SOURCE, url), source_id("mlit"))
    for record in ROUNDS.values():
        p.uses(raw.cached(SOURCE, record.count))
        p.uses(raw.cached(SOURCE, record.turnout))
    days = sorted(ROUNDS)
    for held in days:
        later = following(held)
        p.add(
            "boundary_set",
            id=key("boundary_set", held),
            scheme_code=SCHEME,
            vintage=held,
            valid_from=date.fromisoformat(held),
            valid_to=date.fromisoformat(later) if later else None,
            as_of=date.fromisoformat(held),
        )
    for n, name in enumerate(order(), 1):
        runs = [
            (held, tuple(sorted(codes()[m] for m in d["members"])))
            for held in days
            for d in rounds()[held]["districts"]
            if d["district"] == name
        ]
        first, last = runs[0][0], runs[-1][0]
        until = following(last)
        area = area_id(n)
        p.add("area", id=area, scheme_code=SCHEME)
        p.add(
            "area_code",
            area_id=area,
            code=f"{PREFECTURE}{n:02d}",
            valid_from=date.fromisoformat(first),
            valid_to=date.fromisoformat(until) if until else None,
        )
        p.add(
            "area_parent",
            area_id=area,
            parent_id=PREFECTURE_AREA,
            valid_from=date.fromisoformat(first),
            valid_to=date.fromisoformat(until) if until else None,
        )
        p.name(area, name, "ja", "official")
        p.name(area, f"Saitama Prefectural Assembly district {n}", "en", "english", False)
        changes = [i for i, (_, members) in enumerate(runs) if i == 0 or members != runs[i - 1][1]]
        for index, i in enumerate(changes):
            held, members = runs[i]
            ends = runs[changes[index + 1]][0] if index + 1 < len(changes) else until
            vintage = ROUNDS[held].vintage
            outline(p, area, mlit.merge([shapes(vintage)[code] for code in members]), held, ends)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system, parties, and candidates of Saitama Prefectural Assembly elections",
    )
    p.external |= {JAPAN}
    system = key("electoral_system", "sntv")
    p.add("electoral_system", id=system, polity_id=JAPAN, rules=SYSTEM_RULES, rules_version=1)
    p.name(
        system,
        "Saitama Prefectural Assembly districts, single non-transferable vote with a legal minimum",
        "en",
        "official",
    )
    p.name(system, "埼玉県議会議員選挙（単記非移譲式、法定得票数あり）", "ja", "native", False)
    p.add(
        "note",
        id=key("note", system, "methodology"),
        entity_id=system,
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    parties: set[str] = set()
    people: set[str] = set()
    for data in rounds().values():
        for district in data["districts"]:
            for c in district["candidates"]:
                if c["party"] not in INDEPENDENT:
                    parties.add(c["party"])
                people.add(c["name"])
    for party in sorted(parties):
        id = party_id(party)
        p.add("party", id=id, kind="party", polity_id=JAPAN)
        p.name(id, party, "ja", "official")
    for person in sorted(people):
        id = person_id(person)
        p.add("person", id=id)
        p.name(id, person, "ja", "official")
    return p


def party_id(party: str) -> str:
    return key("party", party)


def person_id(name: str) -> str:
    return key("person", PREFECTURE, re.sub(r"\s", "", name))


def election(held: str) -> Package:
    data = rounds()[held]
    record = ROUNDS[held]
    day = date.fromisoformat(held)
    until = following(held)
    p = Package(
        f"{SOURCE}/{held}",
        SOURCE,
        f"The {day.year} Saitama Prefectural Assembly election by district and municipality, from the prefectural "
        "election commission's count, turnout, and winners tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {JAPAN, SAITAMA, ASSEMBLY}
    commission = source_id("commission")
    for url in (record.page, record.count, record.turnout, record.parties, record.status):
        p.artifact(raw.cached(SOURCE, url), commission)
    if UNOPPOSED.get(held):
        for url in (RECORD_PAGE, RECORD):
            p.artifact(raw.cached(SOURCE, url), commission)
    election_id = key("election", held)
    p.add("election", id=election_id, polity_id=SAITAMA, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Saitama Prefectural Assembly election", "en", "official")
    p.name(election_id, "埼玉県議会議員選挙", "ja", "native", False)
    path = f"jp/saitama/{held}-assembly"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    system = key("electoral_system", "sntv")
    numbers = {name: n for n, name in enumerate(order(), 1)}
    for district in data["districts"]:
        name, seats = district["district"], district["seats"]
        n = numbers[name]
        kind = "tacit" if district["unopposed"] else "counted"
        tag = f"{held}:{name}"
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
            default_result_kind=kind,
            boundary_set_id=key("boundary_set", held),
        )
        p.name(contest, f"Saitama Prefectural Assembly district {n}", "en", "official")
        p.name(contest, name, "ja", "native", False)
        p.add("slug", path=f"{path}/district-{n}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind=kind,
            source_id=commission,
            status="active",
            is_default=True,
            completeness=0.0 if district["unopposed"] else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set, "methodology"),
            entity_id=result_set,
            kind="methodology",
            body_md=TACIT if district["unopposed"] else METHOD,
            lang="en",
        )
        units: dict[str, str] = {}
        for member in district["members"]:
            row = places()[codes()[member]]
            unit = units[member] = row["unit_id"]
            p.external.add(unit)
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=area_id(n),
                valid_from=day,
                valid_from_precision="day",
                valid_to=date.fromisoformat(until) if until else None,
                valid_to_precision="day",
                method="official_list",
            )
        for place in district["places"]:
            for field, measure in MEASURES.items():
                value = data["turnout"][place][0 if field == "registered" else 1]
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[place],
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        winners = {c["number"] for c in district["winners"]}
        for c in district["candidates"]:
            ctag = f"{tag}:{c['number']}"
            option, person = key("option", ctag), person_id(c["name"])
            party = None if c["party"] in INDEPENDENT else party_id(c["party"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=c["number"],
                ballot_label=c["name"],
            )
            candidacy = key("candidacy", ctag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=party,
                is_independent=party is None,
                is_incumbent=c["status"] == "現",
            )
            elected = c["number"] in winners
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if elected else "lost",
                seats=1 if elected else None,
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
    built = [geo(), registry(), *(election(held) for held in ROUNDS)]
    return [str(p.write()) for p in built]


def parse() -> None:
    for held, data in rounds().items():
        districts = data["districts"]
        print(
            f"{held}: {len(districts)} districts, {sum(d['seats'] for d in districts)} seats, "
            f"{sum(len(d['winners']) for d in districts)} winners, {data['registered']:,} registered, "
            f"{data['voters']:,} voters"
        )
    build()


def emit() -> None:
    package.run("load", [f"{SOURCE}/{name}" for name in PACKAGES])
