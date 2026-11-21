"""Hyogo Prefectural Assembly elections by district and municipality, read from the commission's text PDFs.

The prefectural election commission publishes the record of the 2023 elections as one PDF per chapter. The count
chapter (開票) prints, for each district with a vote, every candidate's votes in each municipality (a ward of Kobe
City counts as a municipality) with the district's totals and legal minimum; the turnout chapter (投票) prints
each municipality's electorate on election day; the candidate chapter (候補者) lists the candidates of the
districts that held no vote. All three carry a text layer, which `pdftotext -layout` reads.
"""

import csv
import functools
import re
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.jp import mlit
from ea.sources.jp.pref_gikai import pdftext, squeeze

SOURCE = "jp.pref_gikai_hyogo"
BASE = "https://web.pref.hyogo.lg.jp/si01"
SCHEME = "jp.pref_assembly_district"
PREFECTURE = "28"
JAPAN = str(uuid5(NAMESPACE, "polity\x1fJP"))
HYOGO = str(uuid5(NAMESPACE, "polity\x1fJP-28"))
# Rows production already holds: the prefecture's area, and the assembly body jp.diet wrote.
PREFECTURE_AREA = "0949e62e-3085-5fc7-ba9d-12159221c365"
ASSEMBLY = "824dc412-54b2-59df-bd5b-29006c921044"
PLACES = Path(__file__).with_name("pref_gikai_hyogo_places.tsv")
MEASURES = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5, "voters": 14}
SYSTEM_RULES = {"formula": "sntv"}
PUBLISHER = "兵庫県選挙管理委員会"
TITLE = "令和5年執行兵庫県議会議員選挙の記録"
LICENSE = (
    "Copyright Hyogo Prefecture (site terms); counts published as facts with attribution, raw files not redistributed"
)
SYSTEM_NOTE = (
    "Each district elects its seats by single non-transferable vote: every voter names one candidate, and the "
    "candidates with the most votes win the seats, a one-seat district being first past the post. A winner needs a "
    "quarter of the district's valid votes divided by its seats (法定得票数, Public Offices Election Act art. 95). "
    "Districts are cities, wards, towns, and counties, alone or joined, as the prefectural ordinance sets them. "
    "A district with no more candidates than seats holds no vote, and its candidates are elected unopposed."
)
METHOD = (
    "Candidate votes by municipality from the count chapter of the prefectural election commission's record "
    "(開票区別候補者別得票数等), with the electorate on election day from its turnout chapter (投票). Votes for "
    "candidates whose names other candidates share are apportioned (按分) and carry decimals. In every district the "
    "municipalities add up to the printed district total, the printed legal minimum equals a quarter of the votes "
    "divided by the seats, and the candidates marked as elected are the ones with the most votes."
)
TACIT = "No vote was held: the district had as many candidates as seats, and all were elected unopposed."
HELD = "2023-04-09"
PAGE = f"{BASE}/r5kengikiroku.html"
COUNT = f"{BASE}/documents/06kaihyou_2.pdf"
TURNOUT = f"{BASE}/documents/05touhyou_1.pdf"
CANDIDATES = f"{BASE}/documents/04kouhosyatou_1.pdf"
OVERVIEW = f"{BASE}/documents/01gaiyou_2.pdf"
FILES = (COUNT, TURNOUT, CANDIDATES, OVERVIEW)
VINTAGE = "2013"
# The eight districts that held no vote (概要: 8選挙区の9名が無投票当選), from the candidate chapter, whose notes mark
# the elected with a circled number. (district, seats, members, [(name, party, status)]).
UNOPPOSED = [
    ("洲本市", ["洲本市"], [("浜田ともあき", "無所属", "現")]),
    ("相生市", ["相生市"], [("富山恵二", "無所属", "現")]),
    (
        "たつの市及び揖保郡",
        ["たつの市", "太子町"],
        [("松井重樹", "無所属", "現"), ("山口晋平", "自由民主党", "現")],
    ),
    ("赤穂市及び赤穂郡及び佐用郡", ["赤穂市", "上郡町", "佐用町"], [("長岡そうじゅ", "無所属", "現")]),
    ("西脇市及び多可郡", ["西脇市", "多可町"], [("内藤ひょうえ", "自由民主党", "現")]),
    ("朝来市及び養父市", ["朝来市", "養父市"], [("ふじた孝夫", "無所属", "現")]),
    ("丹波市", ["丹波市"], [("石川のりゆき", "自由民主党", "現")]),
    ("神崎郡", ["市川町", "福崎町", "神河町"], [("上野ひでかず", "無所属", "現")]),
]
PARTIES = {
    "自": "自由民主党",
    "立": "立憲民主党",
    "国": "国民民主党",
    "公": "公明党",
    "共": "日本共産党",
    "維": "日本維新の会",
}
INDEPENDENT = "無所属"
ENTRY = re.compile(r"(○)?\s*([^()○\d,.\-]+?)\s*\((.)\)\s*((?:-?[\d,]+(?:\.\d+)?)(?:\s+-?[\d,]+(?:\.\d+)?)*)")
LABELS = {
    "得票数計": "sum",
    "有効投票数": "valid",
    "無効投票数": "invalid",
    "投票総数": "cast",
    "持ち帰りその他": "unreturned",
    "持帰りその他": "unreturned",
    "不受理": "refused",
    "投票者総数": "voters",
    "法定得票数": "threshold",
    "供託物没収点": "forfeit",
}
PACKAGES = ["geo", "registry", HELD]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id(name: str) -> str:
    return key("source", name)


@functools.cache
def places() -> dict[str, dict[str, str]]:
    with PLACES.open(encoding="utf-8") as file:
        return {row["name"]: row for row in csv.DictReader(file, delimiter="\t")}


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in mlit.urls(VINTAGE):
        if f"_{PREFECTURE}_GML" in url:
            raw.fetch(mlit.SOURCE, url, suffix=".zip")


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def block(header: list[str], rows: list[str], stats: list[str]) -> list[dict]:
    """The districts one printed block holds: one or two side by side, or one district that counts several
    municipalities in columns of its own with a district sum last."""
    names = [
        [t for t in re.split(r"\s{2,}", squeeze(re.sub(r"選\s*挙\s*区", " ", line)).strip()) if t] for line in header
    ]
    names = [tokens for tokens in names if tokens]
    multi = names[-1][-1] == "計"
    groups: list[dict[str, Any]]
    if multi:
        columns = 1
        groups = [{"district": names[0][0], "places": names[-1][:-1]}]
        width = len(groups[0]["places"]) + 1
    else:
        columns, width = len(names[0]), 1
        if any(len(tokens) != columns for tokens in names):
            raise ValueError(f"header {names} has columns of different counts")
        groups = [{"district": "".join(tokens[c] for tokens in names), "places": []} for c in range(columns)]
        for group in groups:
            group["places"] = [group["district"].removeprefix("神戸市") if group["district"] != "神戸市" else "神戸市"]
    figures: dict[str, list[list[Decimal]]] = {}
    split = 0
    for line in stats:
        compact = squeeze(line)
        label = next(name for name in LABELS if name in re.sub(r"\s", "", compact))
        found = list(re.finditer(r"-?\d[\d,]*(?:\.\d+)?", line))
        values = [number(m[0]) for m in found]
        if LABELS[label] == "sum" and columns == 2:
            split = line.index("得", line.index("得") + 1) - 6
        figures[LABELS[label]] = [values]
    for group in groups:
        group["candidates"] = []
    for line in rows:
        found_entries = list(ENTRY.finditer(line))
        for order, m in enumerate(found_entries):
            if columns == 1:
                column = 0
            elif len(found_entries) == columns:
                column = order
            else:
                column = 0 if (m.start(1) if m[1] else m.start(2)) < split else 1
            votes = [number(v) for v in m[4].split()]
            if len(votes) != width:
                raise ValueError(f"{groups[column]['district']}: {m[2]} has {len(votes)} numbers, not {width}")
            party = m[3]
            groups[column]["candidates"].append(
                {
                    "name": re.sub(r"\s", "", m[2]),
                    "party": INDEPENDENT if party == "無" else PARTIES[party],
                    "elected": m[1] is not None,
                    "votes": votes,
                }
            )
    for c, group in enumerate(groups):
        group["figures"] = {}
        for field, [values] in figures.items():
            if field in ("threshold", "forfeit"):
                group[field] = values[-1] if multi else values[c]
            else:
                group["figures"][field] = values if multi else [values[c]]
    return groups


def read_count(path: Path) -> list[dict]:
    """Every district that held a vote, in the order the chapter prints them."""
    lines = pdftext(path)
    start = next(i for i, line in enumerate(lines) if "開票区別候補者別得票数等" in line)
    districts: list[dict] = []
    header: list[str] = []
    rows: list[str] = []
    stats: list[str] = []
    for line in lines[start + 1 :]:
        text = re.sub(r"\s", "", line)
        if not text:
            continue
        if text.startswith("区分") or "党派別得票数" in text:
            break
        label = next((name for name in LABELS if name in text), None)
        if label is not None:
            stats.append(line)
            if label == "供託物没収点":
                districts += block(header, rows, stats)
                header, rows, stats = [], [], []
        elif ENTRY.search(line) and not stats:
            rows.append(line)
        elif not rows:
            header.append(line)
        else:
            raise ValueError(f"unread line in a block: {line!r}")
    return districts


def read_turnout(path: Path) -> dict[str, int]:
    """The electorate on election day (当日有権者数, men, women, and total) of each municipality that held a vote."""
    found: dict[str, int] = {}
    for line in pdftext(path):
        m = re.match(r"\s*([^\d\s][^\d]*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+\d+\.\d\d\s", line)
        if m is None:
            continue
        name = re.sub(r"\s", "", m[1])
        if name not in places() or name in found:
            continue
        if number(m[2]) + number(m[3]) != number(m[4]):
            raise ValueError(f"{name}: men and women do not add up to {m[4]}")
        found[name] = int(number(m[4]))
    return found


def read_totals(path: Path) -> dict[str, list[Decimal]]:
    """The printed totals of the count chapter: for the cities (市計), the towns (町計), and the prefecture (県計),
    the valid, invalid, cast, unreturned and refused, and voters, then the votes of each party."""
    out: dict[str, list[Decimal]] = {}
    lines = [line for line in pdftext(path) if line.strip()]
    for i, line in enumerate(lines):
        text = re.sub(r"\s", "", line)
        for name in ("市計", "町計", "県計"):
            if not text.startswith(name):
                continue
            values = [number(v) for v in re.findall(r"\d[\d,]*(?:\.\d+)?", line)]
            if not values and "(" not in text and i + 1 < len(lines):
                values = [number(v) for v in re.findall(r"\d[\d,]*", lines[i + 1])]
            if len(values) == 8:
                out[f"{name}:party"] = values
            elif len(values) in (5, 6):
                out[name] = values
    return out


def prepare() -> dict:
    """Read and check the election: every district's candidates, winners, and places."""
    districts = read_count(raw.cached(SOURCE, COUNT).path)
    turnout = read_turnout(raw.cached(SOURCE, TURNOUT).path)
    totals = read_totals(raw.cached(SOURCE, COUNT).path)
    for district in districts:
        name, members = district["district"], district["places"]
        where = f"{HELD} {name}"
        for place in members:
            if place not in places():
                raise ValueError(f"{where}: unknown place {place}")
            if place not in turnout:
                raise ValueError(f"{where}: {place} has no electorate")
        candidates = district["candidates"]
        seats = sum(c["elected"] for c in candidates)
        district["seats"] = seats
        district["members"] = members
        district["unopposed"] = False
        if not 0 < seats < len(candidates):
            raise ValueError(f"{where}: {seats} marked elected of {len(candidates)}")
        columns = len(members) + 1 if len(members) > 1 else 1
        for c in candidates:
            c["votes"] = dict(zip(members, c["votes"][: len(members)], strict=True))
            c["total"] = sum(c["votes"].values(), Decimal(0))
        figures = district["figures"]
        valid = sum((c["total"] for c in candidates), Decimal(0))
        for field, values in figures.items():
            if len(values) != columns:
                raise ValueError(f"{where}: {field} has {len(values)} figures, not {columns}")
        for index, place in enumerate(members):
            if sum((c["votes"][place] for c in candidates), Decimal(0)) != figures["sum"][index]:
                raise ValueError(f"{where} {place}: candidates do not sum to the printed total")
            if abs(figures["valid"][index] - figures["sum"][index]) >= 1:
                raise ValueError(f"{where} {place}: valid votes differ from the candidates' total")
            if figures["valid"][index] + figures["invalid"][index] != figures["cast"][index]:
                raise ValueError(f"{where} {place}: valid and invalid votes do not make the ballots cast")
            extra = figures["unreturned"][index] + figures["refused"][index]
            if figures["cast"][index] + extra != figures["voters"][index]:
                raise ValueError(f"{where} {place}: ballots cast and the rest do not make the voters")
            if figures["voters"][index] > turnout[place]:
                raise ValueError(f"{where} {place}: more voters than electors")
        if len(members) > 1:
            for field in ("sum", "valid", "invalid", "cast", "voters", "unreturned", "refused"):
                if sum(figures[field][:-1], Decimal(0)) != figures[field][-1]:
                    raise ValueError(f"{where}: the places' {field} do not sum to the district's")
        if valid != figures["sum"][-1]:
            raise ValueError(f"{where}: candidates sum to {valid}, the district prints {figures['sum'][-1]}")
        threshold = (valid / seats / 4).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        forfeit = -((-valid * 100) // (seats * 10)) / 100
        if abs(threshold - district["threshold"]) > Decimal("0.01"):
            raise ValueError(f"{where}: legal minimum {district['threshold']}, the rule gives {threshold}")
        if abs(Decimal(forfeit) - district["forfeit"]) > Decimal("0.01"):
            raise ValueError(f"{where}: deposit line {district['forfeit']}, the rule gives {forfeit}")
        ranked = sorted(candidates, key=lambda c: -c["total"])
        if {c["name"] for c in ranked[:seats]} != {c["name"] for c in candidates if c["elected"]}:
            raise ValueError(f"{where}: the candidates marked elected are not the ones with the most votes")
        if any(c["total"] < district["threshold"] for c in candidates if c["elected"]):
            raise ValueError(f"{where}: an elected candidate falls below the legal minimum")
        if len({c["name"] for c in candidates}) != len(candidates):
            raise ValueError(f"{where}: two candidates share a name")
        for index, c in enumerate(ranked, 1):
            c["rank"] = index
        district["status"] = {}
    for name, members, winners in UNOPPOSED:
        for place in members:
            if place not in places():
                raise ValueError(f"{name}: unknown place {place}")
        districts.append(
            {
                "district": name,
                "places": [],
                "members": members,
                "seats": len(winners),
                "unopposed": True,
                "candidates": [
                    {"name": n, "party": party, "elected": True, "votes": {}, "status": state, "rank": None}
                    for n, party, state in winners
                ],
            }
        )
    seats = sum(d["seats"] for d in districts)
    if seats != 86 or len(districts) != 38:
        raise ValueError(f"{len(districts)} districts and {seats} seats, the overview says 38 and 86")
    # The commission's printed totals: valid, invalid, cast, unreturned and refused, and voters for the counted
    # places, and the votes of each party.
    counted = [d for d in districts if not d["unopposed"]]
    summed = {
        field: sum(
            (v for d in counted for v in (d["figures"][field][:-1] if len(d["places"]) > 1 else d["figures"][field])),
            Decimal(0),
        )
        for field in ("valid", "invalid", "cast", "unreturned", "refused", "voters")
    }
    printed = totals["県計"]
    if [summed["valid"], summed["invalid"], summed["cast"]] != printed[:3] or summed["voters"] != printed[-2]:
        raise ValueError(f"prefecture totals {summed}, the chapter prints {printed}")
    by_party: dict[str, Decimal] = {}
    for d in counted:
        for c in d["candidates"]:
            by_party[c["party"]] = by_party.get(c["party"], Decimal(0)) + c["total"]
    party_row = totals["県計:party"]
    expected = [
        by_party.get(p, Decimal(0)).quantize(Decimal(1))
        for p in ("自由民主党", "立憲民主党", "国民民主党", "公明党", "日本共産党", "日本維新の会", INDEPENDENT)
    ]
    if expected != party_row[:-1] or sum(expected) != party_row[-1]:
        raise ValueError(f"party votes {expected}, the chapter prints {party_row}")
    return {
        "districts": districts,
        "turnout": turnout,
        "totals": totals,
        "registered": sum(turnout[p] for d in counted for p in d["places"]),
    }


@functools.cache
def data() -> dict:
    return prepare()


def parse() -> None:
    result = data()
    districts = result["districts"]
    print(
        f"{HELD}: {len(districts)} districts, {sum(d['seats'] for d in districts)} seats, "
        f"{sum(len(d['candidates']) for d in districts)} candidates, {result['registered']:,} registered"
    )


def area_id(n: int) -> str:
    return key("area", SCHEME, f"{PREFECTURE}{n:02d}")


def party_id(party: str) -> str:
    return key("party", party)


def person_id(name: str) -> str:
    return key("person", PREFECTURE, name)


def commission_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id("commission"),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license=LICENSE,
        license_url="https://web.pref.hyogo.lg.jp/kn01/index.html",
        attribution="出典：兵庫県選挙管理委員会",
        notes="Count, turnout, and candidate chapters of the commission's record of the 2023 elections, read from "
        "their PDF text layers with pdftotext; the winners of unopposed districts from the candidate chapter.",
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
        "Hyogo Prefectural Assembly districts, each the union of the municipalities and wards the prefectural "
        "ordinance gives it, with their shapes from MLIT N03",
    )
    p.external |= {JAPAN, HYOGO, PREFECTURE_AREA, SCHEME}
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
    for url in (COUNT, TURNOUT, CANDIDATES):
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
    for n, district in enumerate(data()["districts"], 1):
        area = area_id(n)
        p.add("area", id=area, scheme_code=SCHEME)
        p.add("area_code", area_id=area, code=f"{PREFECTURE}{n:02d}", valid_from=day, valid_to=None)
        p.add("area_parent", area_id=area, parent_id=PREFECTURE_AREA, valid_from=day, valid_to=None)
        p.name(area, district["district"], "ja", "official")
        p.name(area, f"Hyogo Prefectural Assembly district {n}", "en", "english", False)
        merged = shapely.set_srid(
            mlit.polygons(mlit.merge([shapes()[code] for code in member_codes(district["members"])])), 4326
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
        "The electoral system, parties, and candidates of Hyogo Prefectural Assembly elections",
    )
    p.external |= {JAPAN}
    system = key("electoral_system", "sntv")
    p.add("electoral_system", id=system, polity_id=JAPAN, rules=SYSTEM_RULES, rules_version=1)
    p.name(
        system,
        "Hyogo Prefectural Assembly districts, single non-transferable vote with a legal minimum",
        "en",
        "official",
    )
    p.name(system, "兵庫県議会議員選挙（単記非移譲式、法定得票数あり）", "ja", "native", False)
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
        "The 2023 Hyogo Prefectural Assembly election by district and municipality, from the prefectural election "
        "commission's count, turnout, and candidate chapters",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {JAPAN, HYOGO, ASSEMBLY}
    commission = source_id("commission")
    for url in (PAGE, COUNT, TURNOUT, CANDIDATES, OVERVIEW):
        p.artifact(raw.cached(SOURCE, url), commission)
    election_id = key("election", HELD)
    p.add("election", id=election_id, polity_id=HYOGO, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, "2023 Hyogo Prefectural Assembly election", "en", "official")
    p.name(election_id, "兵庫県議会議員選挙", "ja", "native", False)
    path = f"jp/hyogo/{HELD}-assembly"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    system = key("electoral_system", "sntv")
    for n, district in enumerate(result["districts"], 1):
        name, seats = district["district"], district["seats"]
        kind = "tacit" if district["unopposed"] else "counted"
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
            default_result_kind=kind,
            boundary_set_id=key("boundary_set", HELD),
        )
        p.name(contest, f"Hyogo Prefectural Assembly district {n}", "en", "official")
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
            unit = units[member] = places()[member]["unit_id"]
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
        for index, place in enumerate(district["places"]):
            figures = district["figures"]
            values = {
                "registered": Decimal(result["turnout"][place]),
                "cast": figures["cast"][index],
                "valid": figures["valid"][index],
                "invalid": figures["invalid"][index],
                "voters": figures["voters"][index],
            }
            for field, measure in MEASURES.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[place],
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=values[field],
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
                is_incumbent=True if district["unopposed"] else None,
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
