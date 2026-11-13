import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie.dail import MEMBERS, fold
from ea.sources.ie.dail import PARSED as DAIL
from ea.sources.ie.dail_scans import PARSED as SCANS
from ea.sources.ie.dail_scans import fold as scan_fold
from ea.store import connect

SOURCE = "ie.dail_scans_people"
VERSION = "1"
HOUSES = range(14, 29)
GENERALS = [
    "1954-05-18",
    "1957-03-05",
    "1961-10-04",
    "1965-04-07",
    "1969-06-18",
    "1973-02-28",
    "1977-06-16",
    "1981-06-11",
    "1982-02-18",
    "1982-11-24",
    "1987-02-17",
    "1989-06-15",
    "1992-11-25",
    "1997-06-06",
]
NICKNAMES = {
    "patrick": "paddy pat paudge padraig paddie",
    "john": "jack johnny jackie",
    "thomas": "tom tommy",
    "james": "jim jimmy",
    "joseph": "joe",
    "gerard": "ger gerry",
    "richard": "dick",
    "william": "bill billy",
    "edward": "ted eddie ned",
    "lawrence": "larry",
    "timothy": "tim",
    "michael": "mick mike mickey",
    "daniel": "dan danny",
    "anthony": "tony",
    "francis": "frank",
    "robert": "bob bobby",
    "raphael": "ray",
    "augustine": "gus",
    "matthew": "matt",
    "christopher": "chris",
    "cornelius": "con",
    "jeremiah": "jerry",
    "bernard": "barney",
    "henry": "harry",
    "charles": "charlie",
    "philip": "phil",
    "nicholas": "nick",
    "benjamin": "ben",
    "andrew": "andy",
    "david": "dave",
    "denis": "dinny",
    "margaret": "peggy",
}
CANON = {nick: full for full, nicks in NICKNAMES.items() for nick in nicks.split()}
TITLES = {"dr", "captain", "capt", "colonel", "col", "general", "major", "professor", "prof", "sir", "mr", "mrs", "ms"}
SUFFIXES = {"jnr", "snr", "junior", "senior", "jr", "sr"}
PLACES = {"leix": "laois", "laoighis": "laois"}


def fetch() -> None:
    for house in HOUSES:
        raw.fetch(SOURCE, MEMBERS.format(house=house), suffix=".json")


@dataclass(frozen=True)
class Member:
    code: str
    first: str
    last: str
    place: frozenset[str]


@dataclass(frozen=True)
class Row:
    key: str
    scheme: str
    house: int
    contest: str
    first: str
    last: str
    full: str
    place: frozenset[str]
    party: str
    seated: bool = False
    outgoing: bool = False
    code: str | None = None


def tokens(text: str) -> list[str]:
    return [fold(word) for word in re.split(r"[\s.]+", text) if fold(word)]


def first(text: str) -> str:
    words = [word for word in tokens(text) if word not in TITLES]
    return CANON.get(words[0], words[0]) if words else ""


def surname(text: str) -> str:
    return "".join(word for word in tokens(text) if word not in SUFFIXES)


def place(text: str) -> frozenset[str]:
    words = re.sub(r"[^a-z]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())
    return frozenset(PLACES.get(word, word) for word in words.split() if word not in ("borough", "county"))


def party(text: str | None) -> str:
    folded = fold(text or "").removeprefix("the")
    return "" if folded in ("", "nonparty", "independent") else folded


def house_of(day: str) -> int:
    return HOUSES.start + sum(general <= day for general in GENERALS)


def members() -> dict[int, list[Member]]:
    seated: dict[int, list[Member]] = defaultdict(list)
    for house in HOUSES:
        for result in json.loads(raw.cached(SOURCE, MEMBERS.format(house=house)).path.read_bytes())["results"]:
            member = result["member"]
            for entry in member["memberships"]:
                term = entry["membership"]
                if term["house"]["houseNo"] == str(house):
                    represents = term["represents"]
                    seated[house].append(
                        Member(
                            member["memberCode"],
                            first(member["firstName"] or ""),
                            surname(member["lastName"] or ""),
                            place(represents[0]["represent"]["showAs"]) if represents else frozenset(),
                        )
                    )
    return seated


def identify(pool: list[Member], row: Row) -> str | None:
    scored: list[tuple[int, str]] = []
    for member in pool:
        points = 2 if member.last == row.last else int(SequenceMatcher(None, member.last, row.last).ratio() >= 0.8)
        if not points:
            continue
        if member.first == row.first:
            points += 2
        elif member.first[:3] == row.first[:3] or min(len(member.first), len(row.first)) == 1 == (
            member.first[:1] == row.first[:1]
        ):
            points += 1
        points += 2 * (member.place == row.place)
        scored.append((points, member.code))
    best = max((points for points, _ in scored), default=0)
    found = {code for points, code in scored if points == best}
    return found.pop() if best >= 4 and len(found) == 1 else None


def candidates() -> list[Row]:
    found: list[Row] = []
    for item in json.loads((SCANS / "contests.json").read_text()):
        contest = f"{item['day']}:dail-eireann:{slug(item['constituency'])}"
        position = 0
        for row in item["rows"]:
            if row["role"] == "spoilt":
                continue
            position += not row["speaker"]
            key = f"{contest}:{scan_fold(row['name'])}"
            if sum(scan_fold(other["name"] or "") == scan_fold(row["name"]) for other in item["rows"]) > 1:
                key = f"{key}:{position}"
            label = re.sub(r"^[\s*†‡]+", "", row["label"])
            found.append(
                Row(
                    key,
                    "ie.dail_scans.person",
                    house_of(item["day"]),
                    contest,
                    first(row["name"]),
                    surname(label.split(",")[0].split("(")[0]),
                    fold(row["name"]),
                    place(item["constituency"]),
                    party(row["party"]),
                    bool(row["elected"] or row["speaker"]),
                    row["label"].lstrip().startswith("*") and not item["by"],
                )
            )
    return found


def emit() -> Path:
    seated = members()
    rows = candidates()
    roster = [
        Row(
            row["person"],
            "ie.dail.person",
            HOUSES.stop,
            f"2002:{row['constituency']}",
            "",
            "",
            row["name"],
            place(row["constituency"]),
            party(row["party"]),
            code=row["code"],
        )
        for row in pl.read_parquet(DAIL / "roster.parquet").filter(pl.col("year") == 2002).iter_rows(named=True)
    ]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Dáil candidates of 1951 to 1997 joined across elections and to the 2002 candidates on Oireachtas member codes (each Dáil's members and the outgoing TDs the printed results mark), or on the same name, constituency, and party in consecutive Dáil terms",
            polity="IE",
            kind="match",
        )
        people = {row.key: cs.key(row.scheme, row.key) for row in [*rows, *roster]}
        unknown = [key for key, person in people.items() if person in cs.minted]
        if unknown:
            raise ValueError(f"{len(unknown)} persons are not in the database, e.g. {unknown[0]}")
        codes: dict[str, str] = {row.key: row.code for row in roster if row.code is not None}
        clashes = 0
        for row in rows:
            found = {
                code
                for house, on in ((row.house, row.seated), (row.house - 1, row.outgoing))
                if on and (code := identify(seated[house], row)) is not None
            }
            if len(found) == 1:
                codes[row.key] = found.pop()
            clashes += len(found) > 1
        per_contest: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in rows:
            if row.key in codes:
                per_contest[row.contest, codes[row.key]].append(row.key)
        for keys in per_contest.values():
            if len(keys) > 1:
                for key in keys:
                    codes.pop(key)
        parent: dict[str, str] = {}
        owner: dict[str, str] = {}

        def find(node: str) -> str:
            while parent.setdefault(node, node) != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def join(a: str, b: str) -> int:
            a, b = find(a), find(b)
            if a == b or owner.get(a, owner.get(b)) != owner.get(b, owner.get(a)):
                return 0
            parent[a] = b
            if a in owner:
                owner[b] = owner.pop(a)
            return 1

        for key, code in codes.items():
            if find(f"code:{code}") == f"code:{code}":
                owner.setdefault(f"code:{code}", code)
            join(key, f"code:{code}")
        chained = 0
        terms: dict[int, dict[tuple[object, ...], list[Row]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            terms[row.house][row.first, row.last, row.place, row.party].append(row)
        for house in HOUSES:
            for same, before in terms[house].items():
                after = terms[house + 1].get(same, [])
                if len(before) == 1 and len(after) == 1:
                    chained += join(before[0].key, after[0].key)
        latest: dict[tuple[str, frozenset[str], str], list[Row]] = defaultdict(list)
        for row in rows:
            if row.house == HOUSES.stop - 1:
                latest[row.full, row.place, row.party].append(row)
        ahead: dict[tuple[str, frozenset[str], str], list[Row]] = defaultdict(list)
        for row in roster:
            ahead[row.full, row.place, row.party].append(row)
        for same, after in ahead.items():
            before = latest.get(same, [])
            if len(after) == 1 and len(before) == 1:
                chained += join(before[0].key, after[0].key)
        groups: dict[str, list[UUID]] = defaultdict(list)
        for row in [*rows, *roster]:
            groups[find(row.key)].append(people[row.key])
        merged = 0
        for group in groups.values():
            for person in group[:-1]:
                if cs.survivors.get(person, person) != cs.survivors.get(group[-1], group[-1]):
                    cs.merge(person, group[-1])
                    merged += 1
        print(
            f"{len(codes)} candidacies on member codes, {clashes} clashing, {chained} chained, {merged} people merged"
        )
        return cs.write()
