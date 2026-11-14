import csv
import html
import io
import re
import subprocess
import unicodedata
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic, mic_hoc
from ea.sources.jp.mic import dhondt, party_label, plain
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_hoc_early"
RAW = mic.SOURCE
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
NEWS = f"{mic.ROOT}/news/pdf"
ELECTIONS = {
    19: "2001-07-29",
    20: "2004-07-11",
}
SIZES = {19: (73, 48), 20: (73, 48)}
WIKIDATA = {
    "自由党": ("Q60588", "Liberal Party"),
    "保守党": ("Q1807627", "New Conservative Party"),
    "無所属の会": ("Q11567376", "Independent Association"),
    "第二院クラブ": ("Q1157630", "Dainiin Club"),
    "自由連合": ("Q6148229", "Liberal League"),
    "維新政党・新風": ("Q6080363", "Ishin Seito Shimpu"),
    "女性党": ("Q11446444", None),
    "新社会党": ("Q1164105", "New Socialist Party"),
    "新党・自由と希望": ("Q11501396", None),
    "みどりの会議": ("Q9032570", None),
}
GAIJI = str.maketrans({"": "邦", "": "蓮", "": "花", "": "荒", "": "﨑", "": "髙", "": "弘"})
REPEATED = {"sangiin19_3_11_11": "維新政党・新風"}
NAMES = {"二ノ湯さとし": "二ノ湯 さとし"}
PREFECTURE_NAMES = [native for native, _ in PREFECTURES]
NUMBER = r"\d[\d,]*(?:\.\d+)?"
DISTRICT = re.compile(r"^(\S+?[都道府県])\s*[（(]定数(\d+)名[）)]")
TOTAL = re.compile(r"^(合計|計|全国計|総計)$")


def url_of(number: int, name: str) -> str:
    if number == 19:
        return f"{mic.DATA_ROOT}/sangiin19/{name}.html"
    return f"{NEWS}/040711_{name}.pdf"


def files(number: int) -> dict[str, str]:
    if number == 19:
        found = {"index": f"{mic.DATA_ROOT}/sangiin19/index.html"}
        names = {"smd": "sangiin19_3_13", "pr": "sangiin19_3_5", "lists": "sangiin19_3_10", "seats": "sangiin19_3_12"}
        found |= {key: url_of(number, name) for key, name in names.items()}
        for k in range(1, 15):
            found[f"people_{k}"] = url_of(number, f"sangiin19_3_11_{k}")
        for k in range(1, 48):
            found[f"towns_{k}"] = url_of(number, f"sangiin19_3_14_{k}")
        return found
    found = {"index": f"{mic.DATA_ROOT}/sangiin20/index.html"}
    found |= {key: url_of(number, name) for key, name in {"smd": "3-13", "pr": "3-05", "lists": "3-10"}.items()}
    found["seats"] = url_of(number, "3-12")
    for k in range(1, 9):
        found[f"people_{k}"] = url_of(number, f"3-11_{k}")
    return found


def fetch() -> None:
    for number in ELECTIONS:
        for key, url in files(number).items():
            raw.fetch(RAW, url, suffix=mic.suffix(url))
            if key.startswith("towns_"):
                for party in REPEATED.values():
                    raw.fetch(RAW, town_files(url)[party], suffix=".csv")


def town_files(url: str) -> dict[str, str]:
    return {
        label: urljoin(url, href) for href, label in mic.anchors(url) if href.endswith(".csv") and label != "党派別"
    }


def amount(text: str) -> Decimal:
    text = text.replace(",", "").strip()
    return Decimal(0) if text in {"", "-", "－", "―"} else Decimal(text)


def cell(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return unicodedata.normalize("NFKC", text).strip()


def tables(url: str) -> list[list[list[str]]]:
    page = mic.page(url)
    return [
        [
            [cell(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)]
            for row in re.findall(r"<tr.*?</tr>", t, re.DOTALL)
        ]
        for t in re.findall(r"<table.*?</table>", page, re.DOTALL)
    ]


def caption(url: str) -> list[str]:
    return [cell(c) for c in re.findall(r"<caption>(.*?)</caption>", mic.page(url), re.DOTALL)]


def layout(url: str) -> list[str]:
    path = raw.cached(RAW, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [unicodedata.normalize("NFKC", page.translate(GAIJI)) for page in text.split("\f") if page.strip()]


def script(char: str) -> str:
    if "\u3041" <= char <= "\u309f":
        return "hiragana"
    if "\u30a0" <= char <= "\u30ff":
        return "katakana"
    return "kanji"


def spaced(text: str) -> str:
    text = text.strip()
    joined = text.replace(" ", "")
    if joined in NAMES:
        return NAMES[joined]
    parts = [part.replace(" ", "") for part in re.split(r" {2,}", text)]
    turns = [i for i in range(1, len(joined)) if script(joined[i - 1]) != script(joined[i])]
    gaps = [(len(m[0]), m.start(), m.end()) for m in re.finditer(r" +", text)]
    widest = max((g[0] for g in gaps), default=0)
    if len(parts) == 2:
        split = len(parts[0])
    elif len(turns) == 1:
        split = turns[0]
    elif widest > 1 and sum(1 for g in gaps if g[0] == widest) == 1:
        split = len(next(text[: g[1]] for g in gaps if g[0] == widest).replace(" ", ""))
    else:
        return joined
    head, tail = joined[:split], joined[split:]
    if any(len(part) == 1 and script(part) != "kanji" for part in (head, tail)):
        return joined
    return f"{head} {tail}"


def prefecture(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    if text not in PREFECTURE_NAMES:
        raise ValueError(f"not a prefecture: {text!r}")
    return text


def smd_html(held: str, url: str) -> list[dict]:
    found: list[dict] = []
    district, size = "", 0
    for row in tables(url)[0]:
        if len(row) == 1 and (match := DISTRICT.match(row[0])):
            district, size = prefecture(match[1]), int(match[2])
            continue
        if len(row) != 8 or row[0] not in {"当", "落"}:
            continue
        lines = [line.strip() for line in row[1].split("\n") if line.strip()]
        legal = next((line[1:-1] for line in lines if line.startswith("(")), None)
        ballot = [line for line in lines if not line.startswith("(")][-1]
        found.append(
            {
                "held": held,
                "district": district,
                "size": size,
                "order": sum(1 for f in found if f["district"] == district) + 1,
                "name": re.sub(r" +", " ", ballot),
                "legal": re.sub(r" +", " ", legal) if legal else None,
                "party": row[4],
                "votes": str(amount(row[7])),
                "result": row[0],
            }
        )
    return found


RECORD = re.compile(rf"([当落])\s*(\S.*?)\s+(男|女)\s+(\d+)\s+(\S+)\s+(新|現|元)\s+(\S+)\s+({NUMBER})(?=\s|$)")
DISTRICT_AT = re.compile(r"(\S+?[都道府県])\s*\(定数(\d+)名\)")
LEGAL = re.compile(r"\(((?!定数)[^()]+)\)")
MIDDLE = 55


def smd_pdf(held: str, url: str) -> list[dict]:
    found: list[dict] = []
    district, size = "", 0
    for page in layout(url):
        sides: list[list[tuple[str, re.Match[str]]]] = [[], []]
        for line in page.split("\n"):
            hits = [("district", m) for m in DISTRICT_AT.finditer(line)]
            hits += [("record", m) for m in RECORD.finditer(line)]
            covered = [(m.start(), m.end()) for _, m in hits]
            hits += [("legal", m) for m in LEGAL.finditer(line) if not any(a <= m.start() < b for a, b in covered)]
            for kind, match in sorted(hits, key=lambda hit: hit[1].start()):
                sides[match.start() >= MIDDLE].append((kind, match))
        for side in sides:
            last: dict | None = None
            for kind, match in side:
                if kind == "district":
                    district, size = prefecture(match[1]), int(match[2])
                    last = None
                elif kind == "record":
                    if not district:
                        raise ValueError(f"{url}: a record before any district")
                    last = {
                        "held": held,
                        "district": district,
                        "size": size,
                        "order": 0,
                        "name": spaced(match[2]),
                        "legal": None,
                        "party": match[5],
                        "votes": str(amount(match[8])),
                        "result": match[1],
                    }
                    found.append(last)
                elif last is not None and last["legal"] is None:
                    last["legal"] = spaced(match[1])
    for district in dict.fromkeys(row["district"] for row in found):
        for order, row in enumerate((row for row in found if row["district"] == district), 1):
            row["order"] = order
    return found


def lists_html(held: str, url: str) -> tuple[list[dict], list[dict]]:
    parties: dict[str, dict] = {}
    people: list[dict] = []
    pair: list[str] = []
    labels = {
        "得票総数(a)+(b)": "total",
        "政党等の得票総数(a)": "party_votes",
        "名簿登載者の得票総数(b)": "person_votes",
    }
    for table in tables(url):
        for row in table:
            if "政党等の名称" in row:
                pair = [row[i + 1] for i, c in enumerate(row) if c == "政党等の名称"]
                for name in pair:
                    parties.setdefault(name, {"held": held, "party": name})
                continue
            for label, field in [*labels.items(), ("当選人数", "seats")]:
                hits = [i for i, c in enumerate(row) if c == label]
                for side, i in enumerate(hits):
                    parties[pair[side]][field] = row[i + 1]
            if len(row) == 8 and (row[0].isdigit() or row[4].isdigit()):
                for side, group in enumerate((row[:4], row[4:])):
                    if group[0].isdigit():
                        people.append(
                            {
                                "held": held,
                                "party": pair[side],
                                "rank": int(group[0]),
                                "name": re.sub(r" +", " ", group[2]),
                                "votes": str(amount(group[3])),
                                "result": group[1] or "落",
                            }
                        )
    return finish(parties), people


PERSON = re.compile(rf"(\d{{1,3}})\s+(当\s+)?(\D+?)\s+({NUMBER})(?=\s|$)")


def lists_pdf(held: str, url: str) -> tuple[list[dict], list[dict]]:
    parties: dict[str, dict] = {}
    people: list[dict] = []
    for page in layout(url):
        lines = page.split("\n")
        header = next(line for line in lines if "政党等の名称" in line)
        starts = [m.start() for m in re.finditer("政党等の名称", header)]
        names = [
            re.sub(r"\s+", "", header[start:end].replace("政党等の名称", ""))
            for start, end in zip(starts, [*starts[1:], len(header)], strict=True)
        ]
        if names[0] == "合計":
            continue
        for name in names:
            parties.setdefault(name, {"held": held, "party": name})
        patterns = {
            "total": rf"得票総数\(a\)\+\(b\)\s+({NUMBER})",
            "party_votes": rf"政党等の得票総数\(a\)\s+({NUMBER})",
            "person_votes": rf"名簿登載者の得票総数\(b\)\s+({NUMBER})",
            "seats": r"当選人数\s+(\d+)",
        }
        for field, pattern in patterns.items():
            values = [m[1] for line in lines for m in re.finditer(pattern, line)]
            if len(values) != len(names):
                raise ValueError(f"{url}: {field} {values} for {names}")
            for name, value in zip(names, values, strict=True):
                parties[name][field] = value
        for line in lines:
            if not re.match(r"^\s*\d{1,3}\s", line):
                continue
            for match in PERSON.finditer(line):
                side = sum(1 for start in starts if start <= match.start() + 3) - 1
                people.append(
                    {
                        "held": held,
                        "party": names[side],
                        "rank": int(match[1]),
                        "name": spaced(match[3]),
                        "votes": str(amount(match[4])),
                        "result": "当" if match[2] else "落",
                    }
                )
    return finish(parties), people


def finish(parties: dict[str, dict]) -> list[dict]:
    rows = []
    for order, row in enumerate((row for row in parties.values() if row["party"] != "合計"), 1):
        total, party_votes, person_votes = (amount(row[k]) for k in ("total", "party_votes", "person_votes"))
        if party_votes + person_votes != total:
            raise ValueError(f"{row['held']} {row['party']}: {party_votes} + {person_votes} != {total}")
        rows.append(
            {**row, "order": order, "seats": int(row["seats"]), "total": str(total), "party_votes": str(party_votes)}
        )
    return rows


def pr_html(held: str, url: str) -> list[dict]:
    found: list[dict] = []
    for table in tables(url):
        names = [c for c in table[0][1:] if c]
        for row in table[2:]:
            if len(row) != 1 + 4 * len(names) or TOTAL.match(re.sub(r"\s+", "", row[0])):
                continue
            place = prefecture(row[0])
            for k, name in enumerate(names):
                if re.sub(r"\s+", "", name) == "合計":
                    continue
                total, _, party_votes, _ = row[1 + 4 * k : 5 + 4 * k]
                found.append(
                    {
                        "held": held,
                        "prefecture": place,
                        "party": name,
                        "total": str(amount(total)),
                        "party_votes": str(amount(party_votes)),
                    }
                )
    return found


def pr_pdf(held: str, url: str) -> list[dict]:
    found: list[dict] = []
    for page in layout(url):
        lines = page.split("\n")
        header = next(line for line in lines if "政党等の名称" in line)
        names = header.split()[1:]
        if "".join(names) == "合計":
            continue
        for line in lines:
            match = re.match(rf"^\s*(\D+?)\s+((?:{NUMBER}\s*)+)$", line)
            if not match or TOTAL.match(re.sub(r"\s+", "", match[1])):
                continue
            label = re.sub(r"\s+", "", match[1])
            if label not in PREFECTURE_NAMES:
                continue
            values = match[2].split()
            if len(values) != 4 * len(names):
                raise ValueError(f"{url} {label}: {len(values)} values for {names}")
            for k, name in enumerate(names):
                total, _, party_votes, _ = values[4 * k : 4 * k + 4]
                found.append(
                    {
                        "held": held,
                        "prefecture": label,
                        "party": name,
                        "total": str(amount(total)),
                        "party_votes": str(amount(party_votes)),
                    }
                )
    return found


def people_html(held: str, url: str) -> list[dict]:
    party = re.sub(r"^政党等の名称\s*", "", caption(url)[0])
    found: list[dict] = []
    for table in tables(url):
        if not table or table[0][0] != "得票順位":
            continue
        ranks = [(i, int(c)) for i, c in enumerate(table[0]) if c.isdigit()]
        for row in table[3:]:
            if len(row) != len(table[0]) or re.sub(r"\s+", "", row[0]) not in PREFECTURE_NAMES:
                continue
            place = prefecture(row[0])
            for i, rank in ranks:
                found.append(
                    {
                        "held": held,
                        "prefecture": place,
                        "party": party,
                        "rank": rank,
                        "votes": str(amount(row[i])),
                        "file": None,
                    }
                )
    return found


def people_csv(held: str, url: str, party: str, names: dict[str, int]) -> list[dict]:
    place = PREFECTURE_NAMES[int(url.rsplit("_", 1)[1].removesuffix(".html")) - 1]
    file = town_files(url)[party]
    content = raw.cached(RAW, file).path.read_bytes().decode("cp932")
    rows = list(csv.reader(io.StringIO(unicodedata.normalize("NFKC", content))))
    full = {plain(name): name for name in names}
    surnames = {name.split()[0]: name for name in names}
    columns: dict[int, str] = {}
    for row in rows[:12]:
        for j, text in enumerate(row):
            key = plain(text)
            if key in full or key in surnames:
                columns.setdefault(j, full.get(key) or surnames[key])
    if sorted(columns.values()) != sorted(names):
        raise ValueError(f"{file}: columns {columns} for {sorted(names)}")
    found = []
    for j, key in columns.items():
        values = [amount(row[j]) for row in rows if len(row) > j and re.fullmatch(NUMBER, row[j].strip())]
        found.append(
            {
                "held": held,
                "prefecture": place,
                "party": party,
                "rank": names[key],
                "votes": str(max(values)),
                "file": file,
            }
        )
    return found


def people_pdf(held: str, url: str) -> list[dict]:
    found: list[dict] = []
    party = ""
    for page in layout(url):
        ranks: list[int] = []
        for line in page.split("\n"):
            if match := re.match(r"^\s*政党等の名称\s+(\S+)", line):
                party = match[1]
            elif match := re.match(r"^\s*得票順位\s+([\d\s]+)$", line):
                ranks = [int(v) for v in match[1].split()]
            elif ranks and (match := re.match(rf"^\s*(\D+?)\s+((?:{NUMBER}\s*)+)$", line)):
                label = re.sub(r"\s+", "", match[1])
                if label not in PREFECTURE_NAMES:
                    continue
                values = match[2].split()
                if len(values) not in {len(ranks), len(ranks) + 3, len(ranks) + 4}:
                    raise ValueError(f"{url} {label}: {len(values)} values for ranks {ranks}")
                for rank, value in zip(ranks, values[: len(ranks)], strict=True):
                    found.append(
                        {
                            "held": held,
                            "prefecture": label,
                            "party": party,
                            "rank": rank,
                            "votes": str(amount(value)),
                            "file": None,
                        }
                    )
    return found


def check(
    number: int, held: str, smd: list[dict], parties: list[dict], people: list[dict], pr: list[dict], local: list[dict]
) -> None:
    districts, seats = SIZES[number]
    by_district: dict[str, list[dict]] = {}
    for row in smd:
        by_district.setdefault(row["district"], []).append(row)
    if sorted(by_district) != sorted(PREFECTURE_NAMES):
        raise ValueError(f"{held}: districts {sorted(set(PREFECTURE_NAMES) ^ set(by_district))}")
    won = 0
    for district, rows in by_district.items():
        size = rows[0]["size"]
        winners = sorted(rows, key=lambda r: -Decimal(r["votes"]))[:size]
        if {r["name"] for r in winners} != {r["name"] for r in rows if r["result"] == "当"}:
            raise ValueError(f"{held} {district}: the marked winners are not the top {size}")
        won += size
    if won != districts:
        raise ValueError(f"{held}: {won} district seats, expected {districts}")
    if sum(p["seats"] for p in parties) != seats:
        raise ValueError(f"{held}: {sum(p['seats'] for p in parties)} list seats, expected {seats}")
    for party in parties:
        listed = [p for p in people if p["party"] == party["party"]]
        if sum(1 for p in listed if p["result"] == "当") != party["seats"]:
            raise ValueError(f"{held} {party['party']}: elected members differ from seats")
        ranked = sorted(listed, key=lambda p: p["rank"])
        if [p["rank"] for p in ranked] != list(range(1, len(ranked) + 1)):
            raise ValueError(f"{held} {party['party']}: ranks {[p['rank'] for p in ranked]}")
        if any(Decimal(a["votes"]) < Decimal(b["votes"]) for a, b in pairwise(ranked)):
            raise ValueError(f"{held} {party['party']}: votes are not in rank order")
        person_total = sum((Decimal(p["votes"]) for p in listed), Decimal(0))
        if abs(Decimal(party["total"]) - Decimal(party["party_votes"]) - person_total) > Decimal("0.01"):
            raise ValueError(f"{held} {party['party']}: candidates sum {person_total}")
        places = [r for r in pr if r["party"] == party["party"]]
        if len(places) != 47:
            raise ValueError(f"{held} {party['party']}: {len(places)} prefectures")
        for field in ("total", "party_votes"):
            summed = sum((Decimal(r[field]) for r in places), Decimal(0))
            if abs(summed - Decimal(party[field])) > Decimal("0.01"):
                raise ValueError(f"{held} {party['party']} {field}: prefectures {summed}, nation {party[field]}")
        for person in listed:
            spread = [r for r in local if r["party"] == party["party"] and r["rank"] == person["rank"]]
            summed = sum((Decimal(r["votes"]) for r in spread), Decimal(0))
            if len(spread) != 47 or abs(summed - Decimal(person["votes"])) > Decimal("0.01"):
                raise ValueError(
                    f"{held} {party['party']} {person['rank']}: {len(spread)} prefectures sum {summed}, nation {person['votes']}"
                )
        for place in PREFECTURE_NAMES:
            summed = sum(
                (Decimal(r["votes"]) for r in local if r["party"] == party["party"] and r["prefecture"] == place),
                Decimal(0),
            )
            row = next(r for r in places if r["prefecture"] == place)
            if abs(Decimal(row["total"]) - Decimal(row["party_votes"]) - summed) > Decimal("0.01"):
                raise ValueError(f"{held} {party['party']} {place}: candidates {summed}")
    totals = {p["party"]: Decimal(p["total"]) for p in parties}
    declared = {p["party"]: p["seats"] for p in parties}
    if dhondt(totals, seats) != declared:
        print(f"{held}: declared seats differ from D'Hondt on the totals")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {"candidates": [], "parties": [], "people": [], "pr": [], "pr_people": []}
    for number, held in ELECTIONS.items():
        found = files(number)
        if number == 19:
            smd = smd_html(held, found["smd"])
            parties, people = lists_html(held, found["lists"])
            pr = pr_html(held, found["pr"])
            local = []
            for key, url in found.items():
                if key.startswith("people_") and not any(url.endswith(f"/{page}.html") for page in REPEATED):
                    local += people_html(held, url)
            for missing in REPEATED.values():
                names = {p["name"]: p["rank"] for p in people if p["party"] == missing}
                for key, url in found.items():
                    if key.startswith("towns_"):
                        local += people_csv(held, url, missing, names)
        else:
            smd = smd_pdf(held, found["smd"])
            parties, people = lists_pdf(held, found["lists"])
            pr = pr_pdf(held, found["pr"])
            local = [row for key, url in found.items() if key.startswith("people_") for row in people_pdf(held, url)]
        check(number, held, smd, parties, people, pr, local)
        print(
            f"{held}: {len(smd)} district candidates, {len(parties)} lists, {len(people)} list candidates, {len(local)} list votes by prefecture"
        )
        for key, rows in (
            ("candidates", smd),
            ("parties", parties),
            ("people", people),
            ("pr", pr),
            ("pr_people", local),
        ):
            out[key] += rows
    for key, rows in out.items():
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"{key}-v{VERSION}.parquet")


SMD_METHOD = (
    "Candidate votes for the whole prefecture, with each candidate's result mark, from the ministry's table of "
    "district candidates (選挙区候補者別得票数). Votes for candidates whose names other candidates share are "
    "apportioned (按分) and carry three decimals. The marked winners are the candidates with the most votes for the "
    "district's seats."
)
PR_NATIONAL_METHOD = (
    "Party totals, party-only votes, personal votes, and seats from the ministry's national table "
    "(比例代表党派別名簿登載者別得票数、当選人数). Personal votes for candidates whose names other candidates share "
    "are apportioned (按分)."
)
PR_PLACE_METHOD = (
    "Votes by prefecture from the ministry's tables 比例代表都道府県別党派別得票数 and "
    "比例代表都道府県別党派別名簿登載者別得票数: each party's total, its party-only votes, and each candidate's personal "
    "votes. The prefectures add up to the national totals."
)


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    parties = pl.read_parquet(PARSED / f"parties-v{VERSION}.parquet")
    people = pl.read_parquet(PARSED / f"people-v{VERSION}.parquet")
    pr = pl.read_parquet(PARSED / f"pr-v{VERSION}.parquet")
    pr_people = pl.read_parquet(PARSED / f"pr_people-v{VERSION}.parquet")
    english = dict(PREFECTURES)
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Councillors regular elections of 2001 and 2004 from the Ministry of Internal "
            "Affairs and Communications: prefectural district candidates and national list votes by prefecture, with "
            "declared winners and seats",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-councillors")
        smd_system = cs.key(f"{mic_hoc.SOURCE}.electoral_system", "hoc-sntv")
        pr_system = cs.key(f"{mic_hoc.SOURCE}.electoral_system", "hoc-pr")
        source = cs.source(mic.PUBLISHER, mic_hoc.TITLE)
        nation = cs.unit("jp.nation", "JP")
        if {japan, house, smd_system, pr_system, source, nation} & cs.minted:
            raise ValueError("run jp.mlit, jp.diet, and jp.mic_hoc first")

        keyed: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            label = party_label(text)
            if label in WIKIDATA:
                qid, english_name = WIKIDATA[label]
                found = ("wikidata", qid)
            else:
                found = mic.party_key(text, held)
                english_name = None
            if found is None:
                return None
            if found not in keyed:
                entity = keyed[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata" and label not in WIKIDATA:
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, label, "ja")
                    if english_name:
                        name(entity, english_name, "en", "english", False)
            return keyed[found]

        added: set[UUID] = set()

        def person(key: str, text: str, legal: str | None) -> UUID:
            entity = cs.key(f"{mic_hoc.SOURCE}.person", key)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("person", id=entity)
                name(entity, text, "ja")
                if legal and plain(legal) != plain(text):
                    name(entity, legal, "ja", "official", False)
            return entity

        def prefecture_unit(native: str) -> UUID:
            entity = cs.unit("jp.prefecture", codes[native])
            if entity in cs.minted:
                raise ValueError("run jp.mic_hoc first")
            return entity

        def result_set(key: str, election: UUID, contest: UUID, default: bool, method: str) -> UUID:
            entity = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=entity,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=default,
                completeness=1,
            )
            cs.add("note", entity_id=entity, kind="methodology", body_md=method)
            return entity

        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        for number, held in ELECTIONS.items():
            day = date.fromisoformat(held)
            local = pr_people.filter(pl.col("held") == held)
            for url in [*files(number).values(), *local["file"].drop_nulls().unique().sort()]:
                cs.artifact(raw.cached(RAW, url), source)
            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese House of Councillors election")
            name(election, f"第{number}回参議院議員通常選挙", "ja", "native", False)
            path = f"jp/{held}-sangiin"
            cs.add("slug", path=path, entity_id=election)

            for district, rows in candidates.filter(pl.col("held") == held).group_by("district", maintain_order=True):
                district = str(district[0])
                key = f"{held}:{district}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=int(rows["size"][0]),
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                label = english[district]
                name(contest, f"{label} district")
                name(contest, f"{district}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
                counted = result_set(key, election, contest, True, SMD_METHOD)
                place = prefecture_unit(district)
                for row in rows.iter_rows(named=True):
                    option = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    who = person(f"smd:{district}:{plain(row['name'])}", row["name"], row["legal"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=None,
                        ballot_label=row["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{key}:{row['order']}"),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    won = row["result"] == "当"
                    cs.add(
                        "outcome",
                        result_set_id=counted,
                        option_id=option,
                        kind="elected" if won else "lost",
                        seats=1 if won else None,
                    )
                    values.append((counted, place, option, Decimal(row["votes"])))

            national = parties.filter(pl.col("held") == held).sort("order")
            size = int(national["seats"].sum())
            contest = cs.key(f"{SOURCE}.contest", f"{held}:national")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=house,
                magnitude=size,
                electoral_system_id=pr_system,
                default_result_kind="counted",
            )
            name(contest, "National proportional representation")
            name(contest, "比例代表選出", "ja", "native", False)
            cs.add("slug", path=f"{path}/national", entity_id=contest)
            national_set = result_set(f"{held}:national", election, contest, True, PR_NATIONAL_METHOD)
            place_set = result_set(f"{held}:national:places", election, contest, False, PR_PLACE_METHOD)
            totals = {row["party"]: Decimal(row["total"]) for row in national.iter_rows(named=True)}
            declared = {row["party"]: row["seats"] for row in national.iter_rows(named=True)}
            engine = dhondt(totals, size)
            if engine != declared:
                moved = ", ".join(f"{p} {declared[p]} and {engine[p]}" for p in totals if engine[p] != declared[p])
                cs.add("note", entity_id=national_set, kind="caveat", body_md=mic_hoc.PR_ENGINE.format(moved=moved))
            options_by: dict[tuple[str, int], UUID] = {}
            for position, row in enumerate(national.iter_rows(named=True), 1):
                label = row["party"]
                member = party(label, held)
                listed = options_by[label, -1] = cs.key(f"{SOURCE}.option", f"{held}:national:{label}")
                cs.add(
                    "contest_option",
                    id=listed,
                    contest_id=contest,
                    kind="list",
                    party_id=member,
                    ballot_position=position,
                    ballot_label=party_label(label),
                )
                cs.add(
                    "outcome",
                    result_set_id=national_set,
                    option_id=listed,
                    kind="won" if row["seats"] else "lost",
                    seats=row["seats"],
                )
                legend = options_by[label, 0] = cs.key(f"{SOURCE}.option", f"{held}:national:{label}:party")
                cs.add(
                    "contest_option",
                    id=legend,
                    contest_id=contest,
                    kind="legend",
                    parent_option_id=listed,
                    party_id=member,
                    ballot_position=None,
                    ballot_label=None,
                )
                values.append((national_set, nation, listed, Decimal(row["total"])))
                values.append((national_set, nation, legend, Decimal(row["party_votes"])))
                for candidate in people.filter((pl.col("held") == held) & (pl.col("party") == label)).iter_rows(
                    named=True
                ):
                    key = f"{held}:national:{label}:{plain(candidate['name'])}"
                    option = options_by[label, candidate["rank"]] = cs.key(f"{SOURCE}.option", key)
                    who = person(f"pr:{party_label(label)}:{plain(candidate['name'])}", candidate["name"], None)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=listed,
                        party_id=member,
                        ballot_position=None,
                        ballot_label=candidate["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", key),
                        person_id=who,
                        option_id=option,
                        role="list_member",
                        ballot_name=candidate["name"],
                        member_of_party_id=member,
                        is_independent=False,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=national_set,
                        option_id=option,
                        kind="elected" if candidate["result"] == "当" else "lost",
                        seats=None,
                    )
                    values.append((national_set, nation, option, Decimal(candidate["votes"])))
            for row in pr.filter(pl.col("held") == held).iter_rows(named=True):
                place = prefecture_unit(row["prefecture"])
                values.append((place_set, place, options_by[row["party"], -1], Decimal(row["total"])))
                values.append((place_set, place, options_by[row["party"], 0], Decimal(row["party_votes"])))
            for row in local.filter(pl.col("votes").cast(pl.Float64) > 0).iter_rows(named=True):
                place = prefecture_unit(row["prefecture"])
                values.append((place_set, place, options_by[row["party"], row["rank"]], Decimal(row["votes"])))

        for result_set_id, place, option, value in values:
            cs.add(
                "result_value",
                result_set_id=result_set_id,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=mic.VOTES,
                count_round=0,
                flags=0,
                value=value,
            )
        return cs.write()
