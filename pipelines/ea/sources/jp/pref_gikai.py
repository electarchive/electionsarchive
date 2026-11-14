import itertools
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
import shapely

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic, mlit
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.pref_gikai"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SCHEME = "jp.pref_assembly_district"
TOCHIGI = "https://www.pref.tochigi.lg.jp/senkyo"


@dataclass(frozen=True)
class Commission:
    publisher: str
    title: str
    url: str
    license: str
    license_url: str | None
    attribution: str


@dataclass(frozen=True)
class Record:
    page: str
    count: str
    turnout: str
    winners: str


COMMISSIONS = {
    "栃木県": Commission(
        "栃木県選挙管理委員会",
        "栃木県議会議員選挙 候補者別市町村別得票数調・投票に関する調・党派別新現元別当選者数調",
        f"{TOCHIGI}/",
        "Copyright Tochigi Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.tochigi.lg.jp/kensei/kouhou/hp/chosakuken/index.html",
        "出典：栃木県選挙管理委員会",
    ),
}
RECORDS = {
    f"{held}:栃木県": Record(
        f"{TOCHIGI}/{folder}/kekka.html",
        f"{TOCHIGI}/{folder}/file/ES_KAIHYO_K_51_1.xls",
        f"{TOCHIGI}/{folder}/file/ES_TOHYO_K_51.xls",
        f"{TOCHIGI}/{folder}/file/ES_KAIHYO_K_51_3.xls",
    )
    for held, folder in (
        ("2023-04-09", "r05kengi"),
        ("2019-04-07", "31kengi"),
        ("2015-04-12", "27kengi"),
        ("2011-04-10", "23kengi"),
    )
}
PLACE = re.compile(r"^\S+?[市区町村]$")
THRESHOLD = re.compile(r"法定得票数\(\s*([\d,.]*)\s*\)")
BOTTOM = {"total": (1, 5), "cut": (6, 9), "other": (10, 13), "valid": (14, None), "invalid": (18, None)}
BOTTOM_REST = {"cast": 22, "unreturned": 26, "voters": 30}
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}


def fetch() -> None:
    for record in RECORDS.values():
        raw.fetch(SOURCE, record.page, suffix=".html")
        for url in (record.count, record.turnout, record.winners):
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def grid(path: Path) -> list[dict[int, str]]:
    reader = fastexcel.read_excel(str(path))
    rows = []
    for sheet in reader.sheet_names:
        frame = reader.load_sheet(sheet, header_row=None, dtypes="string").to_polars()
        for row in frame.iter_rows():
            cells = {i: unicodedata.normalize("NFKC", v).strip() for i, v in enumerate(row) if v is not None}
            rows.append({i: v for i, v in cells.items() if v})
    return rows


def amount(row: dict[int, str], whole: int, fraction: int | None) -> Decimal:
    value = Decimal(row.get(whole, "0").replace(",", ""))
    if fraction is not None and fraction in row:
        value += Decimal(row[fraction])
    return value


def label(row: dict[int, str]) -> str:
    return re.sub(r"\s", "", row.get(0, ""))


def read_count(path: Path) -> list[dict]:
    districts: list[dict] = []
    current: dict | None = None
    block: list[dict] = []
    bottom = False
    for row in grid(path):
        first = label(row)
        if row.get(1) == "定数":
            current = {
                "district": first,
                "seats": int(row[3]),
                "unopposed": "無投票" in row.get(7, ""),
                "candidates": [],
                "places": {},
                "totals": {},
                "threshold": None,
            }
            districts.append(current)
            bottom = False
            continue
        if current is None:
            continue
        if first == "届出番号":
            block = [{"number": int(row[i]), "column": i, "votes": {}} for i in sorted(row) if i >= 2]
            current["candidates"].extend(block)
        elif first == "候補者氏名":
            for candidate in block:
                candidate["name"] = re.sub(r"\s+", " ", row[candidate["column"]])
        elif first == "党派名":
            for candidate in block:
                candidate["party"] = row[candidate["column"]]
        elif first == "順位":
            for candidate in block:
                if candidate["column"] in row:
                    candidate["rank"] = int(row[candidate["column"]])
        elif row.get(1) == "(A)":
            bottom = True
        elif first == "選挙区計" and not bottom:
            for candidate in block:
                if candidate["column"] in row:
                    candidate["total"] = str(amount(row, candidate["column"], candidate["column"] + 3))
        elif PLACE.match(first) and not bottom:
            current["places"].setdefault(first, {})
            if row.get(1) == "*":
                for candidate in block:
                    candidate["votes"][first] = str(amount(row, candidate["column"], candidate["column"] + 3))
        elif PLACE.match(first) and bottom and len(row) > 1:
            figures = {key: amount(row, *columns_) for key, columns_ in BOTTOM.items()}
            figures |= {key: amount(row, column, None) for key, column in BOTTOM_REST.items()}
            current["places"][first] = {key: str(value) for key, value in figures.items()}
        elif first == "選挙区計" and bottom and len(row) > 1:
            current["totals"] = {key: str(amount(row, *columns_)) for key, columns_ in BOTTOM.items()}
        elif (found := THRESHOLD.search(row.get(41, ""))) and found.group(1):
            current["threshold"] = found.group(1).replace(",", "")
    for district in districts:
        for candidate in district["candidates"]:
            candidate.pop("column")
    return districts


def read_turnout(path: Path) -> dict[str, tuple[str, str]]:
    return {
        label(row): (row[4].replace(",", ""), row[7].replace(",", ""))
        for row in grid(path)
        if PLACE.match(label(row)) and row.get(1) == "*"
    }


def read_winners(path: Path) -> dict[str, int]:
    parties: dict[int, str] = {}
    for row in grid(path):
        if label(row) == "区分":
            parties = {i: v for i, v in row.items() if i > 0 and v != "計"}
        elif label(row) == "計" and parties:
            return {party: int(row[i]) for i, party in parties.items()}
    raise ValueError(f"{path} has no winners by party")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    districts_, candidates_, votes_, places_ = [], [], [], []
    for key, record in RECORDS.items():
        districts = read_count(raw.cached(SOURCE, record.count).path)
        turnout = read_turnout(raw.cached(SOURCE, record.turnout).path)
        by_party = read_winners(raw.cached(SOURCE, record.winners).path)
        elected: dict[str, int] = {}
        for n, district in enumerate(districts, 1):
            name, seats, candidates = district["district"], district["seats"], district["candidates"]
            where = f"{key} {name}"
            if [c["number"] for c in candidates] != list(range(1, len(candidates) + 1)):
                raise ValueError(f"{where}: candidate numbers are not 1..{len(candidates)}")
            if district["unopposed"]:
                if len(candidates) != seats or any(c["votes"] for c in candidates):
                    raise ValueError(f"{where}: an unopposed district has {len(candidates)} for {seats} seats")
                winners = candidates
            else:
                valid = Decimal(0)
                for place, figures in district["places"].items():
                    total = sum(Decimal(c["votes"][place]) for c in candidates)
                    f = {k: Decimal(v) for k, v in figures.items()}
                    if total != f["total"] or f["total"] + f["cut"] + f["other"] != f["valid"]:
                        raise ValueError(f"{where} {place}: candidate votes do not sum to valid votes")
                    if f["valid"] + f["invalid"] != f["cast"] or f["cast"] + f["unreturned"] != f["voters"]:
                        raise ValueError(f"{where} {place}: ballots do not add up")
                    if turnout[place][1] != str(f["voters"]):
                        raise ValueError(f"{where} {place}: voters differ between the turnout and count files")
                    valid += f["valid"]
                for c in candidates:
                    if sum(Decimal(v) for v in c["votes"].values()) != Decimal(c["total"]):
                        raise ValueError(f"{where}: {c['name']}'s places do not sum to the district total")
                if valid != Decimal(district["totals"]["valid"]):
                    raise ValueError(f"{where}: valid votes sum to {valid}, the table prints the district total")
                threshold = (valid / seats / 4).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
                if district["threshold"] is None or abs(threshold - Decimal(district["threshold"])) > Decimal("0.001"):
                    raise ValueError(
                        f"{where}: the legal minimum is {district['threshold']}, the rule gives {threshold}"
                    )
                ranks = sorted(c["rank"] for c in candidates)
                if ranks != list(range(1, len(candidates) + 1)):
                    raise ValueError(f"{where}: ranks are not 1..{len(candidates)}")
                winners = [c for c in candidates if c["rank"] <= seats]
                if any(Decimal(c["total"]) < Decimal(district["threshold"]) for c in winners):
                    raise ValueError(f"{where}: a ranked winner falls below the legal minimum")
                ordered = sorted(candidates, key=lambda c: c["rank"])
                if any(Decimal(a["total"]) < Decimal(b["total"]) for a, b in itertools.pairwise(ordered)):
                    raise ValueError(f"{where}: ranks do not follow the votes")
            for c in winners:
                elected[c["party"]] = elected.get(c["party"], 0) + 1
            districts_.append(
                {
                    "key": key,
                    "order": n,
                    "district": name,
                    "seats": seats,
                    "unopposed": district["unopposed"],
                    "threshold": district["threshold"],
                    "members": list(district["places"]),
                }
            )
            for c in candidates:
                candidates_.append(
                    {
                        "key": key,
                        "district": name,
                        "number": c["number"],
                        "name": c["name"],
                        "party": c["party"],
                        "elected": c in winners,
                    }
                )
                votes_.extend(
                    {"key": key, "district": name, "number": c["number"], "place": place, "votes": votes}
                    for place, votes in c["votes"].items()
                )
            if not district["unopposed"]:
                places_.extend(
                    {"key": key, "district": name, "place": place, "registered": turnout[place][0], **figures}
                    for place, figures in district["places"].items()
                )
        if elected != {party: count for party, count in by_party.items() if count}:
            raise ValueError(f"{key}: winners by party {elected}, the commission's table prints {by_party}")
    pl.DataFrame(districts_).write_parquet(PARSED / f"districts-v{VERSION}.parquet")
    pl.DataFrame(candidates_).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    pl.DataFrame(votes_).write_parquet(PARSED / f"votes-v{VERSION}.parquet")
    pl.DataFrame(places_).write_parquet(PARSED / f"places-v{VERSION}.parquet")


SYSTEM = (
    "Each district elects its seats by single non-transferable vote: every voter names one candidate, and the "
    "candidates with the most votes win the seats, a one-seat district being first past the post. A winner needs a "
    "quarter of the district's valid votes divided by its seats (法定得票数, Public Offices Election Act art. 95). "
    "Districts are cities, towns, villages, and counties, alone or joined, as each prefecture's ordinance sets them. "
    "A district with no more candidates than seats holds no vote, and its candidates are elected unopposed."
)
METHOD = (
    "Candidate votes by municipality from the prefectural election commission's final count table "
    "(候補者別市町村別得票数調, 確定), with the electorate from its turnout table (投票に関する調). Votes for "
    "candidates whose names other candidates share are apportioned (按分) and carry three decimals. In every "
    "municipality the candidates sum to the valid votes, and the ballots add up; each district's winners are the "
    "candidates the commission ranks within its seats, all above the legal minimum, and the winners by party equal "
    "the commission's table of winners by party (党派別新現元別当選者数調)."
)
TACIT = "No vote was held: the district had as many candidates as seats, and all were elected unopposed."


def emit() -> Path:
    districts = pl.read_parquet(PARSED / f"districts-v{VERSION}.parquet")
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    votes = pl.read_parquet(PARSED / f"votes-v{VERSION}.parquet")
    places = pl.read_parquet(PARSED / f"places-v{VERSION}.parquet")
    english = dict(PREFECTURES)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's prefectural assembly elections by district and municipality from the prefectural "
            "election commissions' count and turnout tables, with district shapes as unions of municipalities",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        if japan in cs.minted:
            raise ValueError("run jp.mlit and jp.diet first")
        cs.add(
            "scheme",
            code=SCHEME,
            polity_id=japan,
            kind="electoral_district",
            rank=2,
            noun="prefectural assembly district",
        )
        system = cs.key(f"{SOURCE}.electoral_system", "sntv")
        cs.add("electoral_system", id=system, polity_id=japan, rules={"formula": "sntv"}, rules_version=1)
        name(system, "Prefectural assembly districts, single non-transferable vote with a legal minimum")
        name(system, "都道府県議会議員選挙（単記非移譲式、法定得票数あり）", "ja", "native", False)
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        sources = {}
        for prefecture, c in COMMISSIONS.items():
            sources[prefecture] = cs.source(c.publisher, c.title)
            cs.add(
                "source",
                id=sources[prefecture],
                kind="official_body",
                publisher=c.publisher,
                title=c.title,
                url=c.url,
                license=c.license,
                license_url=c.license_url,
                attribution=c.attribution,
                notes="Final count, turnout, and winners-by-party tables, read from the commission's Excel workbooks.",
            )

        keyed: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = mic.party_key(text, held)
            if found is None:
                return None
            if found not in keyed:
                entity = keyed[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return keyed[found]

        added: set[UUID] = set()
        values: list[tuple[UUID, UUID, UUID | None, int, str]] = []
        municipalities: dict[tuple[str, date], dict[str, tuple[str, bytes]]] = {}
        plan: dict[tuple[str, str], tuple[UUID, int, date | None]] = {}
        rounds: dict[str, list[date]] = {}
        for key in districts["key"].unique():
            held_text, prefecture = key.split(":")
            rounds.setdefault(prefecture, []).append(date.fromisoformat(held_text))
        for prefecture, days in rounds.items():
            days.sort()
            code = f"{[native for native, _ in PREFECTURES].index(prefecture) + 1:02d}"
            for held in days:
                municipalities[code, held] = {
                    text: (area_code, wkb)
                    for area_code, text, wkb in conn.execute(
                        """select c.code, n.text, st_asbinary(g.geom) from area a
                           join area_code c on c.area_id = a.id and c.valid @> %s::date
                           join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official'
                             and n.valid @> %s::date
                           join area_geometry g on g.area_id = a.id and g.valid @> %s::date
                           where a.scheme_code = 'jp.municipality' and c.code like %s""",
                        [held, held, held, f"{code}%"],
                    )
                }
            used: dict[str, list[tuple[date, tuple[str, ...]]]] = {}
            for held in reversed(days):
                for row in (
                    districts.filter(pl.col("key") == f"{held}:{prefecture}").sort("order").iter_rows(named=True)
                ):
                    missing = [m for m in row["members"] if m not in municipalities[code, held]]
                    if missing:
                        raise ValueError(f"{held} {prefecture} {row['district']}: no municipality named {missing}")
                    used.setdefault(row["district"], []).insert(0, (held, tuple(row["members"])))

            def following(day: date) -> date | None:
                later = [d for d in days if d > day]
                return later[0] if later else None

            for n, (district, runs) in enumerate(used.items(), 1):
                area_code = f"{code}{n:02d}"
                area = cs.area(SCHEME, area_code)
                span = {
                    "valid_from": runs[0][0],
                    "valid_from_precision": "day",
                    "valid_to": following(runs[-1][0]),
                    "valid_to_precision": "day",
                }
                cs.add("area", id=area, scheme_code=SCHEME)
                cs.add("area_code", area_id=area, code=area_code, **span)
                cs.add("area_parent", area_id=area, parent_id=cs.area("jp.prefecture", code), **span)
                name(area, f"{district}選挙区", "ja", "official")
                name(area, f"{english[prefecture]} Prefectural Assembly district {n}", "en", "english", False)
                changes = [i for i, (_, members) in enumerate(runs) if i == 0 or members != runs[i - 1][1]]
                for index, i in enumerate(changes):
                    held, members = runs[i]
                    ends = runs[changes[index + 1]][0] if index + 1 < len(changes) else span["valid_to"]
                    shape = mlit.polygons(
                        mlit.merge([shapely.from_wkb(municipalities[code, held][m][1]) for m in members])
                    )
                    shape = shapely.set_srid(shape, 4326)
                    cs.add(
                        "area_geometry",
                        area_id=area,
                        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                        label_point=shapely.to_wkb(
                            shapely.set_srid(
                                max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326
                            ),
                            hex=True,
                            include_srid=True,
                        ),
                        quality="official",
                        source_id=sources[prefecture],
                        valid_from=held,
                        valid_from_precision="day",
                        valid_to=ends,
                        valid_to_precision="day",
                    )
                for held, _ in runs:
                    plan[f"{held}:{prefecture}", district] = (area, n, following(held))
        for key in districts["key"].unique(maintain_order=True):
            held_text, prefecture = key.split(":")
            held = date.fromisoformat(held_text)
            code = f"{[native for native, _ in PREFECTURES].index(prefecture) + 1:02d}"
            iso = f"JP-{code}"
            polity = cs.polity(iso)
            assembly = cs.key("jp.body", f"assembly-{code}")
            if {polity, assembly} & cs.minted:
                raise ValueError(f"run jp.mlit and jp.diet first: {iso} is missing")
            record = RECORDS[key]
            source = sources[prefecture]
            for url in (record.page, record.count, record.turnout, record.winners):
                cs.artifact(raw.cached(SOURCE, url), source)
            prefecture_unit = cs.unit("jp.prefecture", code)
            election = cs.key(f"{SOURCE}.election", key)
            cs.add(
                "election", id=election, polity_id=polity, kind="general", date_from=held, date_to=held, status="held"
            )
            name(election, f"{held.year} {english[prefecture]} Prefectural Assembly election")
            name(election, f"{prefecture}議会議員選挙", "ja", "native", False)
            path = f"jp/{slug(english[prefecture])}/{held}-assembly"
            cs.add("slug", path=path, entity_id=election)
            rows = districts.filter(pl.col("key") == key).sort("order")
            if rows["members"].list.len().sum() != len({m for ms in rows["members"] for m in ms}):
                raise ValueError(f"{key}: a municipality sits in two districts")
            for row in rows.iter_rows(named=True):
                district = row["district"]
                area, n, ends = plan[key, district]
                span = {
                    "valid_from": held,
                    "valid_from_precision": "day",
                    "valid_to": ends,
                    "valid_to_precision": "day",
                }
                contest_key = f"{key}:{district}"
                contest = cs.key(f"{SOURCE}.contest", contest_key)
                kind = "tacit" if row["unopposed"] else "counted"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=area,
                    magnitude=row["seats"],
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind=kind,
                )
                name(contest, f"{english[prefecture]} Prefectural Assembly district {n}")
                name(contest, f"{district}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/district-{n}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind=kind,
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=TACIT if row["unopposed"] else METHOD)
                units: dict[str, UUID] = {}
                for member in row["members"]:
                    municipality = municipalities[code, held][member][0]
                    unit = units[member] = cs.unit("jp.municipality", municipality)
                    if unit in cs.minted and unit not in added:
                        added.add(unit)
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="jp.municipality",
                            code=municipality,
                            parent_id=prefecture_unit,
                            area_relation="equals",
                            area_id=cs.area("jp.municipality", municipality),
                            kind="standard",
                            valid_from=None,
                        )
                    cs.add("reporting_unit_area", unit_id=unit, area_id=area, method="official_list", **span)
                for place in places.filter((pl.col("key") == key) & (pl.col("district") == district)).iter_rows(
                    named=True
                ):
                    for field, measure in MEASURES.items():
                        values.append((result_set, units[place["place"]], None, measure, place[field]))
                for c in (
                    candidates.filter((pl.col("key") == key) & (pl.col("district") == district))
                    .sort("number")
                    .iter_rows(named=True)
                ):
                    option = cs.key(f"{SOURCE}.option", f"{contest_key}:{c['number']}")
                    member = party(c["party"], held_text)
                    person = cs.key(f"{SOURCE}.person", f"{code}:{re.sub(r'\s', '', c['name'])}")
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person)
                        name(person, c["name"], "ja")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=c["number"],
                        ballot_label=c["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{contest_key}:{c['number']}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=c["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if c["elected"] else "lost",
                        seats=1 if c["elected"] else None,
                    )
                    for v in votes.filter(
                        (pl.col("key") == key) & (pl.col("district") == district) & (pl.col("number") == c["number"])
                    ).iter_rows(named=True):
                        values.append((result_set, units[v["place"]], option, 1, v["votes"]))
        vintages = sorted({date.fromisoformat(key.split(":")[0]) for key in districts["key"]})
        for index, start in enumerate(vintages):
            cs.add(
                "boundary_set",
                scheme_code=SCHEME,
                vintage=start.isoformat(),
                valid_from=start,
                valid_to=vintages[index + 1] if index + 1 < len(vintages) else None,
                as_of=start,
            )
        for result_set, unit, option, measure, value in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=Decimal(value),
            )
        return cs.write()
