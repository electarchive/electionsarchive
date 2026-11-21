"""Assembly elections of Japan's designated cities (政令指定都市), counted by ward district.

Each ward is one district electing its seats by single non-transferable vote. The city election commission
publishes the candidates' votes by ward. One package per city and round; the shared registry holds each city's
council, the electoral system, and the parties.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.jp import mic

SOURCE = "jp.city_gikai"
HERE = Path(__file__).parent
LICENSE = "No license stated: facts published with attribution, raw files not redistributed"
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}
SYSTEM_NOTE = (
    "Each ward elects its seats by single non-transferable vote: every voter names one candidate, and the "
    "candidates with the most votes win the seats. A winner needs a quarter of the ward's valid votes divided by "
    "its seats (法定得票数, Public Offices Election Act art. 95). A ward with no more candidates than seats holds no "
    "vote and its candidates are elected unopposed."
)
METHOD = (
    "Candidate votes by ward from the city election commission's final count (開票確定), with the electorate and "
    "the ballot accounting from its turnout and breakdown tables. Votes for candidates whose names other "
    "candidates share are apportioned (あん分) and carry decimals. Winners are the candidates the commission marks "
    "elected (当)."
)


@dataclass(frozen=True)
class City:
    code: str
    english: str
    publisher: str
    page: str


@dataclass(frozen=True)
class Round:
    city: str
    held: date
    candidates: str
    turnout: str
    breakdown: str


CITIES = {
    "横浜市": City(
        "14100",
        "Yokohama",
        "横浜市選挙管理委員会",
        "https://www.city.yokohama.lg.jp/city-info/senkyo/data/chiho/2023toitsu.html",
    ),
}
Y = "https://www.city.yokohama.lg.jp/sakae/kusei/shikai-senkyo/senkyo/kekka.files/R5toitu_shikai.pdf"
ROUNDS = {
    "yokohama-2023": Round(
        "横浜市",
        date(2023, 4, 9),
        "https://www.city.yokohama.lg.jp/city-info/senkyo/data/chiho/2023toitsu.files/kaihyokakutei.pdf",
        Y,
        Y,
    ),
}


def wards() -> dict[str, dict[str, str]]:
    frame = pl.read_csv(HERE / "city_gikai_wards.tsv", separator="\t", schema_overrides={"code": pl.String})
    return {row["code"]: row for row in frame.iter_rows(named=True)}


def polities() -> dict[str, str]:
    frame = pl.read_csv(HERE / "city_gikai_polities.tsv", separator="\t")
    return dict(frame.iter_rows())


def fetch() -> None:
    for r in ROUNDS.values():
        for url in dict.fromkeys((r.candidates, r.turnout, r.breakdown)):
            raw.fetch(SOURCE, url, suffix=Path(url).suffix or ".pdf")
    for c in CITIES.values():
        raw.fetch(SOURCE, c.page, suffix=".html")


def text(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return unicodedata.normalize("NFKC", out.stdout).splitlines()


NUM = r"[\d,]+(?:\.\d+)?"
ROW = re.compile(rf"(\d+)\s+(当)?\s+({NUM})\s+(\d+\.\d+)\s+(.+?)\s{{2,}}(.+?)\s{{2,}}(現|新|元)")
HEAD = re.compile(r"【\s*(\S+?)\s*】")
SEATS = re.compile(r"定数\s+(\d+)")
TOTAL = re.compile(rf"({NUM})\s+\(合\s*計\)")
SPLIT = 50


def num(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def yokohama_candidates(url: str) -> dict[str, dict]:
    """Wards by name: seats, candidates (number, votes, elected, name, party, status), and the printed total."""
    out: dict[str, dict] = {}
    current: list[str | None] = [None, None]
    for line in text(url):
        for m in HEAD.finditer(line):
            side = 0 if m.start() < SPLIT else 1
            current[side] = m.group(1)
            out[m.group(1)] = {"seats": None, "candidates": [], "total": None}
        for m in SEATS.finditer(line):
            side = 0 if m.start() < SPLIT else 1
            if (ward := current[side]) and out[ward]["seats"] is None:
                out[ward]["seats"] = int(m.group(1))
        for m in ROW.finditer(line):
            side = 0 if m.start() < SPLIT else 1
            ward = current[side]
            if ward is None:
                continue
            number, won, votes, _, name, party, status = m.groups()
            out[ward]["candidates"].append(
                {
                    "number": int(number),
                    "votes": num(votes),
                    "elected": won == "当",
                    "name": re.sub(r"\s+", " ", name.strip()),
                    "party": party.strip(),
                    "status": status,
                }
            )
        for m in TOTAL.finditer(line):
            side = 0 if m.start() < SPLIT else 1
            if (ward := current[side]) is not None:
                out[ward]["total"] = num(m.group(1))
    return out


def yokohama_accounts(turnout: str, breakdown: str) -> dict[str, dict[str, Decimal]]:
    rows: dict[str, dict[str, Decimal]] = {}
    for line in text(turnout):
        tokens = line.split()
        if tokens and tokens[0].endswith("区") and len(tokens) >= 7:
            figures = [num(t) for t in tokens[1:] if re.fullmatch(NUM, t)]
            if len(figures) >= 9 and "." not in tokens[1] and tokens[0] not in rows:
                rows[tokens[0]] = {"registered": figures[2], "voters": figures[5]}
    seen: set[str] = set()
    for line in text(breakdown):
        tokens = line.split()
        if (
            tokens
            and tokens[0].endswith("区")
            and tokens[0] in rows
            and tokens[0] not in seen
            and "." in tokens[1][-4:-3]
        ):
            figures = [num(t) for t in tokens[1:] if re.fullmatch(NUM, t)]
            if len(figures) >= 10:
                seen.add(tokens[0])
                rows[tokens[0]] |= {
                    "valid": figures[3],
                    "invalid": figures[4],
                    "cast": figures[5],
                    "voters": figures[9],
                }
    return rows


def read(key: str) -> dict[str, dict]:
    r = ROUNDS[key]
    candidates = yokohama_candidates(r.candidates)
    accounts = yokohama_accounts(r.turnout, r.breakdown)
    ward_names = {w["name"]: code for code, w in wards().items() if code.startswith(CITIES[r.city].code[:2])}
    for ward, d in candidates.items():
        a = accounts[ward]
        where = f"{key} {ward}"
        seats = sum(c["elected"] for c in d["candidates"])
        if d["seats"] != seats:
            raise ValueError(f"{where}: {seats} marked elected for {d['seats']} seats")
        votes = sum(c["votes"] for c in d["candidates"])
        if votes != d["total"]:
            raise ValueError(f"{where}: candidates sum to {votes}, the table prints {d['total']}")
        if abs(votes - a["valid"]) > Decimal("0.01"):
            raise ValueError(f"{where}: candidates sum to {votes}, valid votes are {a['valid']}")
        if a["valid"] + a["invalid"] != a["cast"]:
            raise ValueError(f"{where}: valid and invalid votes do not add up to ballots cast")
        if ward not in ward_names:
            raise ValueError(f"{where}: unknown ward")
        ordered = sorted(d["candidates"], key=lambda c: -c["votes"])
        if not all(c["elected"] for c in ordered[: d["seats"]]):
            raise ValueError(f"{where}: winners are not the top {d['seats']} by votes")
        d["account"] = a
        d["code"] = ward_names[ward]
    return candidates


def source(p: Package, city: City) -> str:
    id = p.id("source", city.code)
    p.add(
        "source",
        id=id,
        kind="official_body",
        publisher=city.publisher,
        title=f"{city.english} city assembly election results by ward",
        url=city.page,
        license=LICENSE,
        license_url=None,
        attribution=f"出典：{city.publisher}",
        notes="Final count tables by ward, read from the commission's PDF files.",
    )
    return id


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "Japan's designated-city councils, electoral system, and parties")
    country = str(uuid5(package.NAMESPACE, "polity\x1fJP"))
    p.external.add(country)
    pol = polities()
    p.add("electoral_system", id=p.id("system", "sntv"), polity_id=country, rules={"formula": "sntv"}, rules_version=1)
    p.add(
        "note",
        id=p.id("note", "sntv"),
        entity_id=p.id("system", "sntv"),
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    for name, c in CITIES.items():
        polity = pol[name]
        p.external.add(polity)
        body = p.id("body", f"council-{c.code}")
        p.add("body", id=body, polity_id=polity, kind="unicameral")
        p.name(body, f"{c.english} City Council", "en", "official")
        p.name(body, f"{name}議会", "ja", "native", False)
    labels = set()
    for key in ROUNDS:
        for d in read(key).values():
            labels |= {mic.party_label(c["party"]) for c in d["candidates"]} - mic.INDEPENDENT
    for label in sorted(labels):
        party = p.id("party", label)
        p.add("party", id=party, kind="party", polity_id=country)
        p.name(party, label, "ja", "official")
    return p


def election(key: str) -> Package:
    r = ROUNDS[key]
    city = CITIES[r.city]
    p = Package(
        f"{SOURCE}/{key}",
        SOURCE,
        f"{city.english} City Council general election of {r.held}, by ward",
        depends=[f"{SOURCE}/registry"],
    )
    polity = polities()[r.city]
    p.external.add(polity)
    src = source(p, city)
    for url in dict.fromkeys((r.candidates, r.turnout, r.breakdown)):
        p.artifact(raw.cached(SOURCE, url), src)
    election = p.id("election", key)
    p.add("election", id=election, polity_id=polity, kind="general", date_from=r.held, date_to=r.held, status="held")
    p.name(election, f"{r.held.year} {city.english} City Council election", "en", "official")
    p.name(election, f"{r.city}議会議員選挙", "ja", "native", False)
    path = f"jp/{city.english.lower()}/{r.held}-council"
    p.add("slug", path=path, entity_id=election, is_current=True)
    body = p.id("body", f"council-{city.code}")
    system = p.id("system", "sntv")
    # these ids come from the registry package of this pipeline
    ward_rows = wards()
    for ward, d in read(key).items():
        row = ward_rows[d["code"]]
        p.external |= {row["area_id"], row["unit_id"]}
        contest = p.id("contest", key, d["code"])
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=row["area_id"],
            magnitude=d["seats"],
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{city.english} City Council, {row['name']}", "en", "official")
        p.name(contest, f"{r.city}議会議員選挙 {ward}", "ja", "native", False)
        p.add("slug", path=f"{path}/{d['code']}", entity_id=contest, is_current=True)
        rs = p.id("result_set", key, d["code"])
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=src,
            status="active",
            is_default=True,
            completeness=1,
        )
        p.add("note", id=p.id("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        for field, measure in MEASURES.items():
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=row["unit_id"],
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=d["account"][field],
            )
        for c in d["candidates"]:
            tag = f"{key}:{d['code']}:{c['number']}"
            option = p.id("option", tag)
            label = mic.party_label(c["party"])
            party = None if label in mic.INDEPENDENT else p.id("party", label)
            person = p.id("person", f"{city.code}:{re.sub(r'\s', '', c['name'])}")
            if not any(x["id"] == person for x in p.rows.get("person", [])):
                p.add("person", id=person)
                p.name(person, c["name"], "ja", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=c["number"],
                ballot_label=c["name"],
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=party,
                is_independent=party is None,
                is_incumbent=c["status"] == "現",
            )
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="won" if c["elected"] else "lost",
                seats=1 if c["elected"] else None,
            )
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=row["unit_id"],
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=c["votes"],
            )
    return p


def parse() -> None:
    registry().write()
    for key in ROUNDS:
        election(key).write()


def emit() -> None:
    package.run("load", [f"{SOURCE}/registry", *(f"{SOURCE}/{key}" for key in ROUNDS)])
