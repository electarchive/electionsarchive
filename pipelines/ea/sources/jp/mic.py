import json
import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
import psycopg

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.jp.mlit import BLOCKS, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic"
ROOT = "https://www.soumu.go.jp"
DATA_ROOT = f"{ROOT}/senkyo/senkyo_s/data"
ELECTIONS = {
    45: "2009-08-30",
    46: "2012-12-16",
    47: "2014-12-14",
    48: "2017-10-22",
    49: "2021-10-31",
    50: "2024-10-27",
    51: "2026-02-08",
}
ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
SEATS_LABEL = "党派別議席配分表（比例代表）"
WINNERS_LABEL = "候補者別得票数（小選挙区）"
ELECTED_LABEL = "党派別当選人数（比例代表）"


def page(url: str) -> str:
    content = raw.cached(SOURCE, url).path.read_bytes()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp932")


def anchors(url: str) -> list[tuple[str, str]]:
    found = []
    for href, text in ANCHOR.findall(page(url)):
        label = re.sub(r"\s+|（別ウィンドウ）", "", unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", "", text)))
        label = label.replace("(", "（").replace(")", "）")
        if label:
            found.append((ROOT + href if href.startswith("/") else href, label))
    return found


def suffix(url: str) -> str:
    return url[url.rindex(".") :]


@dataclass
class Listing:
    index: str
    towns: str
    subpages: list[str]
    smd: list[tuple[str, str]]
    pr: list[tuple[str, str]]
    seats: str
    winners: str
    elected: str
    turnout: list[tuple[str, str]]


def listing(number: int) -> Listing:
    index = f"{DATA_ROOT}/shugiin{number}/index.html"
    towns = f"{DATA_ROOT}/shugiin{number}/shikuchouson.html"
    links = anchors(index)
    smd: list[tuple[str, str]] = []
    pr: list[tuple[str, str]] = []
    subpages = [(url, label) for url, label in anchors(towns) if re.search(r"shikuchouson_\d+\.html$", url)]
    if subpages:
        for url, prefecture in subpages:
            files = {label: href for href, label in anchors(url) if "/main_content/" in href}
            smd.append((prefecture, files["小選挙区"]))
            pr.append((prefecture, files["比例代表"]))
    else:
        for url, label in anchors(towns):
            if "/main_content/" not in url:
                continue
            (pr if label.endswith("選挙区") else smd).append((label, url))
    seats = next(url for url, label in links if SEATS_LABEL in label)
    winners = next(url for url, label in links if WINNERS_LABEL in label)
    elected = next(url for url, label in links if ELECTED_LABEL in label)
    turnout = [
        (label, url)
        for url, label in links
        if "/main_content/" in url and re.search(r"有権者数、投票者数|投票総数、有効投票数、無効投票数", label)
    ]
    return Listing(index, towns, [url for url, _ in subpages], smd, pr, seats, winners, elected, turnout)


def fetch() -> None:
    for number in ELECTIONS:
        raw.fetch(SOURCE, f"{DATA_ROOT}/shugiin{number}/index.html", suffix=".html")
        raw.fetch(SOURCE, f"{DATA_ROOT}/shugiin{number}/shikuchouson.html", suffix=".html")
        for url, _ in anchors(f"{DATA_ROOT}/shugiin{number}/shikuchouson.html"):
            if re.search(r"shikuchouson_\d+\.html$", url):
                raw.fetch(SOURCE, url, suffix=".html")
        found = listing(number)
        for url in [
            *(url for _, url in found.smd + found.pr + found.turnout),
            found.seats,
            found.winners,
            found.elected,
        ]:
            raw.fetch(SOURCE, url, suffix=suffix(url))


VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
INDEPENDENT = {"", "無所属"}
STATE_TOTAL = re.compile(r"^(?:[道都府県]計|.+[道都府県] 合計)$")
PARTY_LIKE = re.compile(r"(党|の会|組|連合|クラブ|みらい)$")
ERRATA = {("2017-10-22", "福井県", 1, "永平寺町"): "the town's row repeats the sum of the rows above it"}
STALE = {
    ("2021-10-31", "宮城県", 3): {1},
    ("2021-10-31", "埼玉県", 14): {1},
    ("2021-10-31", "静岡県", 7): {1, 2},
}
PARTIAL_PR = {
    ("2009-08-30", "栃木県"): "The ministry's Tochigi sheet has no columns for 民主党, 社会民主党, and 幸福実現党, "
    "so their Tochigi counting districts are missing and their Kita-Kanto sums fall short of the block totals."
}
RENAMED = {("2021-10-31", "NHK党"): "NHKと裁判してる党弁護士法72条違反で"}
SWAPPED = {("2017-10-22", "滋賀県", 3): (0, 2, 1)}
LISTED = {
    ("2012-12-16", "北関東", "小高 真由美"): "小高 まゆみ",
    ("2012-12-16", "南関東", "河野 敏久"): "かわの としひさ",
    ("2012-12-16", "東京都", "杉村 康之"): "すぎむら やすゆき",
    ("2012-12-16", "東海", "井桁 亮"): "いげた まこと",
    ("2012-12-16", "近畿", "三橋 真記"): "みはし まき",
    ("2021-10-31", "東京都", ""): "辻 清人",
    ("2021-10-31", "中国", "やじま 秀平"): "矢島 秀平",
}
ALIASES = {
    ("2017-10-22", "群馬県", 4, "福田 たつお"): "福田 達夫",
    ("2017-10-22", "岐阜県", 1, "浅井 里江"): "吉田 りえ",
}


def grid(path: Path, sheet: str) -> list[list[str]]:
    frame = fastexcel.read_excel(str(path)).load_sheet(sheet, header_row=None, dtypes="string").to_polars()
    return [[unicodedata.normalize("NFKC", v).strip() if v else "" for v in row] for row in frame.iter_rows()]


def sheets(path: Path) -> list[str]:
    return [sheet for sheet in fastexcel.read_excel(str(path)).sheet_names if sheet != "リスト"]


def amount(text: str) -> Decimal:
    text = text.replace(",", "").strip()
    return Decimal(text) if text not in {"", "-", "－"} else Decimal(0)


def person_name(text: str) -> str:
    text = re.sub(r"[ \u3000]+$", "", unicodedata.normalize("NFKC", text).replace("\n", ""))
    text = re.sub(r"^\((.*)\)$", r"\1", re.sub(r"\s*\((無所属|諸派)\)$", "", text))
    if re.search(r"\S {2,}\S", text):
        return " ".join(part.replace(" ", "") for part in re.split(r" {2,}", text.strip()))
    return re.sub(r" +", " ", text.strip())


def party_name(text: str) -> str:
    return re.sub(r"^\((.*)\)$", r"\1", re.sub(r"\s+", "", text))


def places(rows: list[list[str]], columns: list[int], start: int) -> tuple[list[tuple[str, list[Decimal]]], int]:
    found: list[tuple[int, str, list[Decimal]]] = []
    index = start
    while index < len(rows):
        row = rows[index]
        level = 0 if row[0] else 1 if len(row) > 1 and row[1] else None
        if level is None:
            index += 1
            continue
        name = re.sub(r"\s+", "", row[level])
        if name.endswith("計"):
            break
        if not any(row[column] for column in columns):
            index += 1
            continue
        found.append((level, name, [amount(row[column]) for column in columns]))
        index += 1
    leaves = []
    for position, (level, name, values) in enumerate(found):
        following = found[position + 1] if position + 1 < len(found) else None
        if following is not None and (
            (level == 0 and following[0] == 1) or (following[1].startswith(name) and following[1] != name)
        ):
            continue
        leaves.append((name, values))
    return leaves, index


def total_of(rows: list[list[str]], index: int, columns: list[int]) -> list[Decimal]:
    return [amount(rows[index][column]) for column in columns]


def smd_sheet(rows: list[list[str]], held: str, prefecture: str, sheet: str) -> tuple[list[dict], list[dict]]:
    district = int(re.findall(r"(\d+)区", sheet)[0])
    head = next(i for i, row in enumerate(rows) if row and row[0] == "候補者名")
    columns = [j for j, cell in enumerate(rows[head]) if j > 0 and cell and cell != "得票数計" and "率" not in cell]
    party_row = next(i for i in range(head + 1, len(rows)) if rows[i][0].startswith(("市区町村名", "開票区名")))
    people, labels = rows[head], rows[party_row] + [""] * len(rows[head])
    if all(PARTY_LIKE.search(people[column]) for column in columns):
        people, labels = labels, people
    leaves, end = places(rows, columns, party_row + 1)
    leaves = [(name, values) for name, values in leaves if (held, prefecture, district, name) not in ERRATA]
    order = SWAPPED.get((held, prefecture, district), tuple(range(len(columns))))
    leaves = [(name, [values[order[k]] for k in range(len(columns))]) for name, values in leaves]
    printed_row = [amount(rows[end][columns[order[k]]]) for k in range(len(columns))]
    kept = [k for k in range(len(columns)) if k + 1 not in STALE.get((held, prefecture, district), set())]
    leaves = [(name, [values[k] for k in kept]) for name, values in leaves]
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(kept))]
    candidates = []
    for position, k in enumerate(kept):
        name = person_name(people[columns[k]])
        candidates.append(
            {
                "held": held,
                "prefecture": prefecture,
                "district": district,
                "order": position + 1,
                "name": ALIASES.get((held, prefecture, district, name), name),
                "party": party_name(labels[columns[k]]),
                "votes": str(sums[position]),
                "printed": str(printed_row[k]),
            }
        )
    votes = [
        {"held": held, "prefecture": prefecture, "district": district, "place": name, "order": k + 1, "votes": str(v)}
        for name, values in leaves
        for k, v in enumerate(values)
    ]
    return candidates, votes


def pr_sheet(rows: list[list[str]], held: str, prefecture: str) -> list[dict]:
    if any(row[0] == "党派名" for row in rows):
        start = next(i for i, row in enumerate(rows) if row[0] == "党派名")
        names_row = next(i for i in range(start, len(rows)) if any(rows[i][1:]))
        data = next(i for i, row in enumerate(rows) if row[0] == "開票区名") + 1
    else:
        names_row = next(i for i, row in enumerate(rows) if row[0].startswith(("市区町村名", "開票区名")))
        data = names_row + 1
    columns = [
        j for j, cell in enumerate(rows[names_row]) if j > 0 and cell and cell != "得票数計" and "率" not in cell
    ]
    parties = [party_name(rows[names_row][j]) for j in columns]
    leaves: list[tuple[str, list[Decimal]]] = []
    index = data
    while True:
        found, index = places(rows, columns, index)
        leaves += found
        if index >= len(rows) or STATE_TOTAL.match(rows[index][0].replace("　", " ")):
            break
        index += 1
    if index >= len(rows):
        raise ValueError(f"{held} {prefecture}: no prefecture total row")
    total = total_of(rows, index, columns)
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
    if sums != total:
        print(f"MISMATCH {held} {prefecture} PR: places sum to {sums}, total row {total}")
    return [
        {"held": held, "prefecture": prefecture, "party": party, "place": name, "votes": str(values[k])}
        for name, values in leaves
        for k, party in enumerate(parties)
    ]


def turnout_tables(number: int) -> dict[tuple[str, str], tuple[Decimal, Decimal, Decimal]]:
    names = {native for native, _ in PREFECTURES}
    found: dict[tuple[str, str], dict[str, tuple[Decimal, ...]]] = {}
    for label, url in listing(number).turnout:
        match = re.search(r"（(小選挙区|比例代表)）", label)
        if match is None:
            continue
        kind = "smd" if match[1] == "小選挙区" else "pr"
        field = "voters" if "有権者数" in label else "invalid"
        path = raw.cached(SOURCE, url).path
        for row in grid(path, sheets(path)[0]):
            cells = [re.sub(r"\s+", "", cell) for cell in row]
            at = max((i for i, cell in enumerate(cells) if cell in names), default=None)
            if at is None or not re.fullmatch(r"[\d.]+", cells[at + 1] if at + 1 < len(cells) else ""):
                continue
            numbers = [amount(cell) for cell in cells[at + 1 :] if cell]
            found.setdefault((kind, cells[at]), {})[field] = tuple(numbers)
    result = {}
    for key, fields in found.items():
        registered, voters = fields["voters"][2], fields["voters"][5]
        total, valid, invalid = fields["invalid"][:3]
        if valid + invalid != total or abs(total - voters) > voters / 1000:
            raise ValueError(f"{number} {key}: voters {voters}, ballots {total}, valid {valid}, invalid {invalid}")
        result[key] = (registered, voters, invalid)
    if len(result) != 94:
        raise ValueError(f"{number}: {len(result)} turnout rows")
    return result


def prefecture_of(label: str) -> str:
    match = re.match(r"(.+?[都道府県])", label)
    return match[1] if match else label


def parse() -> None:
    candidates: list[dict] = []
    smd_votes: list[dict] = []
    pr_votes: list[dict] = []
    seats: list[dict] = []
    lists: list[dict] = []
    turnout: list[dict] = []
    for number, held in ELECTIONS.items():
        for (kind, prefecture), (registered, voters, invalid) in turnout_tables(number).items():
            turnout.append(
                {
                    "held": held,
                    "kind": kind,
                    "prefecture": prefecture,
                    "registered": str(registered),
                    "voters": str(voters),
                    "invalid": str(invalid),
                }
            )
        found = listing(number)
        text = book_text(found.winners)
        for prefecture, url in found.smd:
            path = raw.cached(SOURCE, url).path
            for sheet in sheets(path):
                more, votes = smd_sheet(grid(path, sheet), held, prefecture, sheet)
                erratum = any(key[:3] == (held, prefecture, more[0]["district"]) for key in ERRATA)
                for candidate in more:
                    checked = Decimal(candidate["printed" if erratum else "votes"])
                    candidate["result"] = declared(text, candidate["name"], checked)
                marks = [candidate["result"] for candidate in more]
                if None in marks or marks.count("当") != 1:
                    raise ValueError(f"{held} {prefecture} {sheet}: the book does not confirm {more}")
                candidates += more
                smd_votes += votes
        block_votes: dict[tuple[str, str], Decimal] = {}
        for label, url in found.pr:
            path = raw.cached(SOURCE, url).path
            for sheet in sheets(path):
                rows = grid(path, sheet)
                if not any(row[0].startswith(("市区町村名", "開票区名", "党派名")) for row in rows):
                    continue
                prefecture = sheet if re.search(r"[都道府県]$", sheet) else prefecture_of(label)
                block = BLOCK_OF[prefecture]
                for row in pr_sheet(rows, held, prefecture):
                    row["party"] = RENAMED.get((held, row["party"]), row["party"])
                    pr_votes.append(row | {"block": block})
                    key = (block, row["party"])
                    block_votes[key] = block_votes.get(key, Decimal(0)) + Decimal(row["votes"])
        for block, parties in allocation(found.seats).items():
            for party, (votes, won) in parties.items():
                short = any(held == day and BLOCK_OF[prefecture] == block for day, prefecture in PARTIAL_PR)
                if votes and block_votes.get((block, party)) != votes and not short:
                    raise ValueError(
                        f"{held} {block} {party}: places sum to {block_votes.get((block, party))}, {votes}"
                    )
                votes = votes or block_votes.get((block, party), Decimal(0))
                seats.append({"held": held, "block": block, "party": party, "votes": str(votes), "seats": won})
        path = raw.cached(SOURCE, found.elected).path
        books = (
            list_pages(path)
            if path.suffix == ".pdf"
            else [x for sheet in sheets(path) for x in list_sheet(grid(path, sheet))]
        )
        won = {(row["block"], row["party"]): row["seats"] for row in seats if row["held"] == held}
        stood = [row for row in candidates if row["held"] == held]
        shares = ratios(stood)
        merged: dict[tuple[str, str], list[Member]] = {}
        for block, party, members in books:
            merged.setdefault((block, RENAMED.get((held, party), party)), []).extend(members)
        for (block, party), members in merged.items():
            ranks = sorted(member[2] for member in members if member[2])
            if (block, party) not in won or ranks != list(range(1, won[block, party] + 1)):
                raise ValueError(f"{held} {block} {party}: elected ranks {ranks}, declared {won.get((block, party))}")
            members = [(m[0], LISTED.get((held, block, m[1]), m[1]), *m[2:]) for m in members]
            pool = [row for row in stood if BLOCK_OF[row["prefecture"]] == block]
            rows = dual_rows(members, pool, party, shares)
            for index, (position, name, rank, smd, _) in enumerate(members):
                if smd and index not in rows:
                    raise ValueError(f"{held} {block} {party} {name}: no district row")
                row = rows.get(index)
                if not (row or name):
                    raise ValueError(f"{held} {block} {party}: list position {position} has no name")
                lists.append(
                    {
                        "held": held,
                        "block": block,
                        "party": party,
                        "position": position,
                        "name": row["name"] if row else name,
                        "rank": rank,
                        "prefecture": row["prefecture"] if row else None,
                        "district": row["district"] if row else None,
                    }
                )
        listed = {(block, party) for block, party in block_votes}
        if missing := {(row["block"], row["party"]) for row in seats if row["held"] == held} - listed:
            raise ValueError(f"{held}: allocation parties not in the vote tables: {missing}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(candidates).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    pl.DataFrame(smd_votes).write_parquet(PARSED / f"smd-v{VERSION}.parquet")
    pl.DataFrame(pr_votes).write_parquet(PARSED / f"pr-v{VERSION}.parquet")
    pl.DataFrame(seats).write_parquet(PARSED / f"seats-v{VERSION}.parquet")
    pl.DataFrame(lists).write_parquet(PARSED / f"lists-v{VERSION}.parquet")
    pl.DataFrame(turnout).write_parquet(PARSED / f"turnout-v{VERSION}.parquet")
    print(build())


def book_text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
        ).stdout
    else:
        text = "\n".join(" ".join(cell for cell in row if cell) for sheet in sheets(path) for row in grid(path, sheet))
    return plain(unicodedata.normalize("NFKC", text))


def printed(votes: Decimal) -> list[str]:
    whole = votes == votes.to_integral()
    forms = [f"{votes:,.0f}", f"{votes:.0f}"] if whole else [f"{votes:,.3f}", f"{votes:.3f}"]
    short = votes.normalize()
    return [*forms, f"{short:,f}", f"{short:f}"]


def plain(text: str) -> str:
    return re.sub(r"[\s\ufe00-\ufe0f\U000e0100-\U000e01ef]+", "", text)


def declared(text: str, name: str, votes: Decimal) -> str | None:
    key = plain(name)
    forms = printed(votes)
    for match in re.finditer(r"(当|落)" + re.escape(key), text):
        if any(form in text[match.end() : match.end() + 90] for form in forms):
            return match[1]
    for form in forms:
        for found in re.finditer(re.escape(form) + r"(?![\d.,])", text):
            before = text[max(0, found.start() - 90) : found.start()]
            mark = max(before.rfind("当"), before.rfind("落"))
            between = before[mark + 1 :]
            if mark >= 0 and (len(set(between) & set(key)) * 2 >= len(set(key)) or re.match(r"\d\d\D", between)):
                return before[mark]
    return None


BLOCK_OF = {PREFECTURES[number - 1][0]: block for block, _, numbers in BLOCKS for number in numbers}
BLOCK_HEADER = re.compile(r"^[<＜]\s*(.+?)選挙区\s*[>＞]$")


def allocation(url: str) -> dict[str, dict[str, tuple[Decimal, int]]]:
    path = raw.cached(SOURCE, url).path
    blocks: dict[str, dict[str, tuple[Decimal, int]]] = {}
    for sheet in sheets(path):
        block = None
        parties: dict[int, str] = {}
        pairs: dict[str, tuple[int, int]] = {}
        for row in grid(path, sheet):
            cells = [cell for cell in row if cell]
            header = next((found for cell in cells if (found := BLOCK_HEADER.match(cell))), None)
            if header:
                block = header[1]
                blocks.setdefault(block, {})
                parties, pairs = {}, {}
            elif block is None or not cells:
                continue
            elif "順位" in cells:
                pairs = {party: (j, j + 1) if row[j] == "順位" else (j - 1, j) for j, party in parties.items()}
            elif not row[0].isdigit() and any(not re.match(r"^[\d,.]+$", cell) for cell in cells if cell != "除数"):
                if pairs or row[0] == "除数":
                    parties, pairs = {}, {}
                for j, cell in enumerate(row):
                    if j > 0 and cell:
                        parties[j] = party_name(parties.get(j, "") + cell)
            elif row[0].isdigit() and pairs:
                for party, (rank, quotient) in pairs.items():
                    votes, seats = blocks[block].get(party, (Decimal(0), 0))
                    if row[0] == "1":
                        votes = amount(row[quotient])
                    blocks[block][party] = (votes, seats + (1 if row[rank] else 0))
    return blocks


Member = tuple[int, str, int | None, str | None, str | None]


def ratios(rows: list[dict]) -> dict[int, set[str]]:
    best: dict[tuple[str, int], Decimal] = {}
    for row in rows:
        place = (row["prefecture"], row["district"])
        best[place] = max(best.get(place, Decimal(0)), Decimal(row["votes"]))
    found = {}
    for row in rows:
        share = Decimal(row["votes"]) * 100 / best[row["prefecture"], row["district"]]
        found[id(row)] = {
            f"{share:.3f}",
            f"{share.quantize(Decimal('0.001'), rounding='ROUND_DOWN')}",
        }
    return found


def dual_rows(members: list[Member], pool: list[dict], party: str, shares: dict) -> dict[int, dict]:
    found: dict[int, dict] = {}
    free = list(pool)

    def settle(test: Callable[[Member, dict], bool]) -> None:
        for index, member in enumerate(members):
            if member[3] and index not in found:
                hits = [row for row in free if row["result"] == member[3] and test(member, row)]
                if len(hits) == 1:
                    found[index] = hits[0]
                    free.remove(hits[0])

    def ours(row: dict) -> bool:
        return party_name(row["party"]) == party

    def overlap(member: Member, row: dict) -> bool:
        return len(set(plain(member[1])) & set(plain(row["name"]))) * 2 >= len(set(plain(member[1]))) > 0

    settle(lambda member, row: plain(row["name"]) == plain(member[1]))
    settle(lambda member, row: member[3] == "落" and ours(row) and member[4] in shares[id(row)])
    settle(lambda member, row: ours(row) and overlap(member, row))
    return found


def list_sheet(rows: list[list[str]]) -> list[tuple[str, str, list[Member]]]:
    found: list[tuple[str, str, list[Member]]] = []
    block = ""
    columns: list[tuple[int, list[Member]]] = []
    layout: dict[int, tuple[int, int]] = {}
    for row in rows:
        if match := re.match(r"^(.+?)選挙区$", row[0]):
            block = match[1]
        elif "政党等名" in row:
            columns = []
            for j, cell in enumerate(row):
                if cell == "政党等名":
                    members: list[Member] = []
                    party = party_name(next(text for text in row[j + 1 :] if text))
                    found.append((block, party, members))
                    columns.append((j, members))
        elif "名簿" in row:
            layout = {j: (row.index("順位", j), row.index("小選挙区", j)) for j, _ in columns}
        elif layout:
            for j, members in columns:
                if row[j].isdigit():
                    rank, smd = layout[j]
                    members.append(
                        (
                            int(row[j]),
                            person_name(row[j + 1]),
                            int(row[rank]) if row[rank] else None,
                            row[smd] or None,
                            row[smd + 1] or None,
                        )
                    )
    return found


def list_pages(path: Path) -> list[tuple[str, str, list[Member]]]:
    tsv = subprocess.run(["pdftotext", "-tsv", str(path), "-"], check=True, capture_output=True, text=True).stdout
    pages: dict[str, list[tuple[float, float, float, str]]] = {}
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t")
        if cells[0] == "5" and cells[11].strip():
            word = unicodedata.normalize("NFKC", cells[11]).strip()
            pages.setdefault(cells[1], []).append((float(cells[6]), float(cells[7]), float(cells[8]), word))
    found: list[tuple[str, str, list[Member]]] = []
    for words in pages.values():
        block = next(match[1] for *_, word in words if (match := re.match(r"^(.+?)選挙区$", word)))
        heads = sorted((x, y, w) for x, y, w, word in words if word == "名簿")
        end = min((y for _, y, _, word in words if word.startswith(("(注)", "※"))), default=float("inf"))
        for index, (x0, header, width) in enumerate(heads):
            x1 = heads[index + 1][0] - 3 if index + 1 < len(heads) else float("inf")
            column = sorted(((y, x, w, word) for x, y, w, word in words if x0 - 3 <= x < x1), key=lambda item: item[:2])
            labels = [item for item in column if item[3] == "名" and item[0] < header - 5]
            if not labels:
                continue
            label = min(labels)
            rank_x = next(x for y, x, _, word in column if word == "順位" and abs(y - header) < 3)
            smd_x = next(x for y, x, _, word in column if word == "小選挙区" and abs(y - header) < 3)
            votes = min(y for _, y, _, word in words if "得" in word and label[0] < y < header)
            party = party_name(
                "".join(word for y, x, _, word in column if label[0] - 2 <= y < votes - 2 and x > label[1] + label[2])
            )
            lines: list[list[tuple[float, float, str]]] = []
            last = -10.0
            for y, x, w, word in column:
                if header + 4 < y < end - 2:
                    if y - last > 3:
                        lines.append([])
                        last = y
                    lines[-1].append((x, w, word))
            members: list[Member] = []
            for line in lines:
                line.sort()
                if not line[0][2].isdigit() or line[0][0] > x0 + width:
                    raise ValueError(f"{path.name} {block} {party}: no list number in {line}")
                glyphs = [item for item in line[1:] if item[0] < smd_x - 2 and not item[2].isdigit()]
                name = glyphs[0][2] if glyphs else ""
                for (x, w, _), (after, _, word) in pairwise(glyphs):
                    name += (" " if after - x - w > 10 else "") + word
                rank = next((int(word) for x, _, word in line if rank_x - 2 <= x < smd_x - 2 and word.isdigit()), None)
                smd = next((word for x, _, word in line if x >= smd_x - 2 and word in {"当", "落"}), None)
                ratio = next((word for x, _, word in line if x >= smd_x - 2 and word not in {"当", "落"}), None)
                members.append((int(line[0][2]), name, rank, smd, ratio))
            found.append((block, party, members))
    return found


FIXES = {
    "栗国村": "粟国村",
    "佐伯氏": "佐伯市",
    "始良市": "姶良市",
    "南薩摩市": "南さつま市",
    "清水町": "湧水町",
    "大崎市": "大崎町",
    "横浜市都築区": "横浜市都筑区",
    "稲敷市": "稲城市",
    "静岡市葵開票区": "静岡市葵区",
    "静岡市駿河開票区": "静岡市駿河区",
}
FIXED_IN = {"大崎市": "46", "清水町": "46", "稲敷市": "13"}
VARIANTS = str.maketrans({"ケ": "ヶ", "ヵ": "ヶ", "鯵": "鰺", "桧": "檜", "檮": "梼", "栗": "栗"})
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SPLIT = re.compile(r"^(.+?[市区町村])(.*)$")
PIECE = re.compile(r"^(?:第|\()*(?:第)?(\d+|[一二三四五六七八九])(?:区|開票区)?\)?(?:\((.+)\))?$")


def variant(text: str) -> str:
    return text.translate(VARIANTS)


class Places:
    def __init__(self, conn: psycopg.Connection, held: date) -> None:
        rows = conn.execute(
            """select a.scheme_code, c.code, n.text, upper(c.valid) is null or upper(c.valid) > %s::date
               from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official' and n.valid && c.valid
               where a.scheme_code in ('jp.municipality', 'jp.ward')
               and c.valid && daterange(%s::date - 1500, %s::date + 1500)""",
            [held, held, held],
        ).fetchall()
        rows.sort(key=lambda row: not row[3])
        self.towns: dict[tuple[str, str], str] = {}
        for scheme, code, text, _ in rows:
            if scheme == "jp.municipality":
                self.towns.setdefault((code[:2], variant(text)), code)
        cities = {code: text for (_, text), code in self.towns.items() if text.endswith("市")}
        self.wards: dict[tuple[str, str], str] = {}
        self.short: dict[tuple[str, str], list[str]] = {}
        for scheme, code, text, _ in rows:
            if scheme != "jp.ward":
                continue
            city = max(c for c in cities if c[:2] == code[:2] and c < code)
            self.wards.setdefault((code[:2], cities[city] + variant(text)), code)
            if code not in self.short.setdefault((code[:2], variant(text)), []):
                self.short[code[:2], variant(text)].append(code)

    def find(self, prefecture: str, base: str, previous: str | None) -> tuple[str, str] | None:
        base = re.sub(r"^[^市区町村]+?郡(?=.+[町村]$)", "", variant(base))
        if (prefecture, base) in self.wards:
            return "jp.ward", self.wards[prefecture, base]
        if (prefecture, base) in self.towns:
            return "jp.municipality", self.towns[prefecture, base]
        if prefecture == "13" and (prefecture, base + "区") in self.towns:
            return "jp.municipality", self.towns[prefecture, base + "区"]
        found = self.short.get((prefecture, base), [])
        if len(found) > 1 and previous is not None:
            found = [code for code in found if code[:4] == previous[:4]]
        return ("jp.ward", found[0]) if len(found) == 1 else None

    def resolve(self, prefecture: str, place: str, previous: str | None) -> tuple[str, str, str | None]:
        place = re.sub(r"\s+|" + "|".join(name for name, _ in PREFECTURES), "", place) or place
        for wrong, right in FIXES.items():
            if FIXED_IN.get(wrong, prefecture) == prefecture and place.startswith(wrong):
                place = right + place[len(wrong) :]
        if (whole := self.find(prefecture, place, previous)) is not None:
            return *whole, None
        if (
            prefecture == "13"
            and (match := re.match(r"^(\D+?)(\d+)区$", place))
            and (found := self.find(prefecture, match[1], previous)) is not None
        ):
            return *found, match[2]
        ends = [match.end() for match in re.finditer(r"[市区町村]", place)]
        for end in reversed(ends):
            head, rest = place[:end], place[end:]
            found = self.find(prefecture, head, previous)
            if found is None or not rest:
                continue
            if piece := PIECE.match(rest):
                number = piece[1]
                return *found, str(NUMERALS.get(number, number)) + (f"({piece[2]})" if piece[2] else "")
            if re.match(r"^(?:\(\d+区\))?[・、]", rest):
                city = found[1] if found[0] == "jp.municipality" else self.city_of(found[1])
                return "jp.municipality", city, place
            if rest.startswith("("):
                return *found, rest.strip("()")
        raise LookupError(f"{prefecture} {place}")

    def city_of(self, ward: str) -> str:
        return max(code for (prefecture, _), code in self.towns.items() if prefecture == ward[:2] and code < ward)


PUBLISHER = "総務省"
TITLE = "衆議院議員総選挙・最高裁判所裁判官国民審査結果調"
INDEX = f"{DATA_ROOT}/shugiin/ichiran.html"
LICENSE = "Public Data License 1.0"
LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
ATTRIBUTION = "出典：総務省ホームページ"
VOTES = 1
PIECES = "jp.counting_district"
PARTY_ALIASES = {
    "社民党": "社会民主党",
    "公明": "公明党",
    "日本維新": "日本維新の会",
    "幸福実現党鹿児島県本部": "幸福実現党",
}
WIKIDATA = {
    "自由民主党": "Q232595",
    "日本共産党": "Q641600",
    "民主党": "Q200314",
    "幸福実現党": "Q1571486",
    "参政党": "Q113009668",
    "中道改革連合": "Q137787102",
    "希望の党": "Q7141081",
    "社会民主党": "Q835109",
    "日本未来の党": "Q194683",
    "公明党": "Q1061354",
    "みんなの党": "Q1355093",
    "維新の党": "Q18116909",
    "れいわ新選組": "Q64405573",
    "次世代の党": "Q17488856",
    "日本のこころ": "Q17488856",
    "日本のこころを大切にする党": "Q17488856",
    "民進党": "Q23055252",
    "国民怒りの声": "Q65563161",
    "NHKと裁判してる党弁護士法72条違反で": "Q39142381",
    "みんなでつくる党": "Q39142381",
    "日本保守党": "Q122639399",
    "生活の党": "Q2164308",
    "国民新党": "Q241325",
    "チームみらい": "Q134414127",
    "新党大地": "Q1247614",
    "新党日本": "Q60658",
    "新党改革": "Q2094225",
    "改革クラブ": "Q2094225",
}
DATED = {
    "日本維新の会": (("2014-01-01", "Q1027055"), ("9999-12-31", "Q21499745")),
    "立憲民主党": (("2020-01-01", "Q41532587"), ("9999-12-31", "Q99233271")),
    "国民民主党": (("2020-01-01", "Q52729635"), ("9999-12-31", "Q99527550")),
}
SMD_RULES = {"formula": "fptp"}
PR_RULES = {
    "formula": "dhondt",
    "lists": {
        "order": "closed",
        "constituencies": "skip",
        "tied": "best_loser",
        "forfeit": 0.1,
        "exhausted": "elsewhere",
        "vacancy": "next_on_list",
    },
}
PR_SYSTEM = (
    "Each block's seats go to the party lists by D'Hondt. A list may rank several candidates who also stand in "
    "single-member districts at the same place; among them, seats go in order of each one's district vote as a "
    "share of the district winner's (惜敗率). District winners are struck from the list, and so are dual candidates "
    "who fell below a tenth of their district's valid votes. A list that runs out of names gives up its remaining "
    "seats, which go to the next highest quotients of the other lists (Public Offices Election Act art. 95-2)."
)
SMD_METHOD = (
    "Candidate votes by counting district (開票区), from the ministry's tables by municipality, which the "
    "prefectural election commissions compile. Counting districts are municipalities or designated-city wards; a "
    "municipality or ward split between districts is counted in parts, each held here as a unit within it. Votes "
    "for candidates whose names other candidates share are apportioned (按分) and carry three decimals. Each "
    "candidate's total and result mark were checked against the ministry's 結果調 book."
)
PR_BLOCK_METHOD = (
    "Party list votes and seats for the block from the ministry's seat allocation table (党派別議席配分表) in the "
    "結果調 book. List members, their list ranks (名簿順位), and the members elected come from its table of "
    "winners by party (党派別当選人数). Dual candidates are matched to their district candidacy by name, or by "
    "their 惜敗率 where the list prints the name differently, and carry their district ballot name."
)
PR_PLACE_METHOD = (
    "Party list votes by counting district from the ministry's tables by municipality. The counting districts add "
    "up to the block totals in the seat allocation table."
)


def party_label(text: str) -> str:
    text = re.sub(r"^\[(.*)\]$", r"\1", text)
    text = re.sub(r"\((前|新|元)\)$", "", text)
    return PARTY_ALIASES.get(text, text)


def party_key(text: str, held: str) -> tuple[str, str] | None:
    text = party_label(text)
    if text in INDEPENDENT:
        return None
    if text in DATED:
        return "wikidata", next(qid for until, qid in DATED[text] if held < until)
    if text in WIKIDATA:
        return "wikidata", WIKIDATA[text]
    return f"{SOURCE}.party", text


def dhondt(votes: dict[str, Decimal], seats: int) -> dict[str, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda party: (votes[party] / (won[party] + 1), votes[party]))
        won[best] += 1
    return won


KEY = f"{SOURCE}/all"
SUMMARY = (
    "Japan's House of Representatives general elections 2009-2026 from the Ministry of Internal Affairs and "
    "Communications: district candidates and party lists by counting district, with declared winners, block seats, "
    "list members, and the list members elected"
)
DISTRICTS = "jp.hor_smd"


def district_code(prefecture: str, district: int) -> str:
    """The jp.hor_smd code of a single-member district: the prefecture's two digits and the district's two."""
    return f"{prefecture}{district:02d}"


def district_areas(conn: psycopg.Connection, day: date) -> dict[str, str]:
    """The jp.hor_smd area of each district code in force on an election day. Each annex 1 table of the Public
    Offices Election Act (1994, 2002, 2013, 2017, and 2022) has its own areas, so a district takes the shape it had
    when it voted."""
    found: dict[str, str] = {}
    for code, area in conn.execute(
        """select c.code, a.id::text from area a join area_code c on c.area_id = a.id
           where a.scheme_code = %s and c.valid @> %s::date""",
        [DISTRICTS, day],
    ).fetchall():
        if found.setdefault(str(code), str(area)) != str(area):
            raise ValueError(f"{DISTRICTS} {code} names two areas on {day}")
    return found


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package jp.mic/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    smd = pl.read_parquet(PARSED / f"smd-v{VERSION}.parquet")
    pr = pl.read_parquet(PARSED / f"pr-v{VERSION}.parquet")
    seats = pl.read_parquet(PARSED / f"seats-v{VERSION}.parquet")
    lists = pl.read_parquet(PARSED / f"lists-v{VERSION}.parquet")
    turnout = pl.read_parquet(PARSED / f"turnout-v{VERSION}.parquet")
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    english = {native: name for native, name in PREFECTURES}
    blocks = {native: (f"{number:02d}", name) for number, (native, name, _) in enumerate(BLOCKS, 1)}
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="JP")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-representatives")
        if {japan, house} & cs.minted:
            raise ValueError("run jp.mlit and jp.diet first")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=INDEX,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=ATTRIBUTION,
        )
        cs.add("scheme", code=PIECES, polity_id=japan, kind="precinct", rank=4, noun="counting district")
        smd_system = cs.key(f"{SOURCE}.electoral_system", "hor-smd")
        cs.add("electoral_system", id=smd_system, polity_id=japan, rules=SMD_RULES, rules_version=1)
        name(smd_system, "House of Representatives single-member districts, first past the post")
        name(smd_system, "小選挙区制", "ja", "native", False)
        pr_system = cs.key(f"{SOURCE}.electoral_system", "hor-pr")
        cs.add("electoral_system", id=pr_system, polity_id=japan, rules=PR_RULES, rules_version=1)
        name(pr_system, "House of Representatives proportional blocks, closed lists by D'Hondt")
        name(pr_system, "比例代表制（拘束名簿式、ドント式）", "ja", "native", False)
        cs.add("note", entity_id=pr_system, kind="methodology", body_md=PR_SYSTEM)

        parties: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = party_key(text, held)
            if found is None:
                return None
            if found not in parties:
                entity = parties[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return parties[found]

        added: set[UUID] = set()

        def unit(scheme: str, code: str, parent: UUID | None, area: UUID, relation: str, polity: UUID) -> UUID:
            entity = cs.unit(scheme, code)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add(
                    "reporting_unit",
                    id=entity,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    parent_id=parent,
                    area_relation=relation,
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            return entity

        def prefecture_unit(code: str) -> UUID:
            return unit("jp.prefecture", code, None, cs.area("jp.prefecture", code), "equals", cs.polity(f"JP-{code}"))

        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        accounting: list[tuple[UUID, UUID, int, Decimal]] = []
        measures = (("registered", 2), ("voters", 14), ("invalid", 5))
        for number, held in ELECTIONS.items():
            day = date.fromisoformat(held)
            found = listing(number)
            for url in [found.index, found.towns, *found.subpages, found.seats, found.winners, found.elected]:
                cs.artifact(raw.cached(SOURCE, url), source)
            for _, url in found.smd + found.pr + found.turnout:
                cs.artifact(raw.cached(SOURCE, url), source)
            places = Places(conn, day)
            areas = district_areas(conn, day)

            def place_unit(prefecture: str, place: str, previous: str | None) -> tuple[UUID, str]:
                code = codes[prefecture]
                scheme, area_code, piece = places.resolve(code, place, previous)
                parent = prefecture_unit(code)
                polity = cs.polity(f"JP-{code}")
                area = cs.area(scheme, area_code)
                whole = unit(scheme, area_code, parent, area, "equals", polity)
                if piece is None:
                    return whole, area_code
                return unit(PIECES, f"{held}:{area_code}:{piece}", whole, area, "within", polity), area_code

            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese general election")
            name(election, f"第{number}回衆議院議員総選挙", "ja", "native", False)
            path = f"jp/{held}-general"
            cs.add("slug", path=path, entity_id=election)

            for (prefecture, district), rows in candidates.filter(pl.col("held") == held).group_by(
                "prefecture", "district", maintain_order=True
            ):
                key = f"{held}:{codes[str(prefecture)]}:{district}"
                contest = cs.key(f"{SOURCE}.contest", key)
                code = district_code(codes[str(prefecture)], int(district))
                if code not in areas:
                    raise LookupError(f"{held}: the archive holds no {DISTRICTS} area {code} on election day")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    district_area_id=UUID(areas[code]),
                    magnitude=1,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                name(contest, f"{english[str(prefecture)]} {district}")
                name(contest, f"{prefecture}第{district}区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(english[str(prefecture)])}-{district}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                counted = sum(Decimal(v) for v in rows["votes"])
                printed = sum(Decimal(v) for v in rows["printed"])
                erratum = [text for (h, p, d, _), text in ERRATA.items() if (h, p, d) == (held, prefecture, district)]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=float(counted / printed) if erratum else 1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=SMD_METHOD)
                for (h, p, d, place), text in ERRATA.items():
                    if (h, p, d) == (held, prefecture, district):
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"{place} is left out: in the ministry's table {text}. The district totals in "
                            "the 結果調 book include it.",
                        )
                options: dict[int, UUID] = {}
                for row in rows.iter_rows(named=True):
                    option = options[row["order"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    person = cs.key(f"{SOURCE}.person", f"{codes[str(prefecture)]}:{plain(row['name'])}")
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person)
                        name(person, row["name"], "ja")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=member,
                        ballot_position=row["order"],
                        ballot_label=row["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{key}:{row['order']}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    if row["result"] == "当":
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                previous = None
                for row in smd.filter(
                    (pl.col("held") == held) & (pl.col("prefecture") == prefecture) & (pl.col("district") == district)
                ).iter_rows(named=True):
                    place, previous = place_unit(row["prefecture"], row["place"], previous)
                    values.append((result_set, place, options[row["order"]], Decimal(row["votes"])))

            day_turnout = turnout.filter(pl.col("held") == held)
            for kind, label in (("smd", "district"), ("pr", "proportional")):
                key = f"{held}:turnout:{kind}"
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=None,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=kind == "smd",
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"Registered voters on election day, voters, and invalid votes by prefecture for the "
                    f"{label} ballot, from the ministry's prefecture tables. Registered voters and voters include "
                    "overseas electors; invalid votes are the ballot total less valid votes.",
                )
                for row in day_turnout.filter(pl.col("kind") == kind).iter_rows(named=True):
                    where = prefecture_unit(codes[row["prefecture"]])
                    for field, measure in measures:
                        accounting.append((result_set, where, measure, Decimal(row[field])))

            for block, (block_code, block_english) in blocks.items():
                key = f"{held}:pr:{block_code}"
                contest = cs.key(f"{SOURCE}.contest", key)
                allocated = seats.filter((pl.col("held") == held) & (pl.col("block") == block))
                size = int(allocated["seats"].sum())
                area = cs.area("jp.hor_block", block_code)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=house,
                    district_area_id=area,
                    magnitude=size,
                    electoral_system_id=pr_system,
                    default_result_kind="counted",
                )
                name(contest, f"{block_english} proportional block")
                name(contest, f"比例代表{block}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(block_english)}-block", entity_id=contest)
                block_set = cs.key(f"{SOURCE}.result_set", key)
                place_set = cs.key(f"{SOURCE}.result_set", f"{key}:places")
                block_votes = {row["party"]: Decimal(row["votes"]) for row in allocated.iter_rows(named=True)}
                local = pr.filter((pl.col("held") == held) & (pl.col("block") == block))
                local_total = sum((Decimal(v) for v in local["votes"]), Decimal(0))
                for result_set, default, method, share in (
                    (block_set, True, PR_BLOCK_METHOD, 1.0),
                    (place_set, False, PR_PLACE_METHOD, float(local_total / sum(block_votes.values()))),
                ):
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        source_id=source,
                        status="active",
                        is_default=default,
                        completeness=share,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=method)
                for (day_key, prefecture), text in PARTIAL_PR.items():
                    if day_key == held and BLOCK_OF[prefecture] == block:
                        cs.add("note", entity_id=place_set, kind="caveat", body_md=text)
                engine = dhondt(block_votes, size)
                declared_seats = {row["party"]: row["seats"] for row in allocated.iter_rows(named=True)}
                if engine != declared_seats:
                    moved = ", ".join(
                        f"{label} {declared_seats[label]} and {engine[label]}"
                        for label in block_votes
                        if engine[label] != declared_seats[label]
                    )
                    cs.add(
                        "note",
                        entity_id=block_set,
                        kind="caveat",
                        body_md="The declared seats differ from a plain D'Hondt count because a list ran out of "
                        "eligible names: its candidates won their districts or fell below a tenth of their district's "
                        "valid votes, so its seats passed to the next highest quotients, and the ministry's table "
                        f"leaves its quotients blank. Declared, then D'Hondt alone: {moved}.",
                    )
                block_unit = unit("jp.hor_block", block_code, None, area, "equals", japan)
                in_block = day_turnout.filter(
                    (pl.col("kind") == "pr")
                    & pl.col("prefecture").is_in([n for n, b in BLOCK_OF.items() if b == block])
                )
                for field, measure in measures:
                    accounting.append(
                        (block_set, block_unit, measure, sum((Decimal(v) for v in in_block[field]), Decimal(0)))
                    )
                options = {}
                for position, row in enumerate(allocated.iter_rows(named=True), 1):
                    option = options[row["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party(row["party"], held),
                        ballot_position=position,
                        ballot_label=party_label(row["party"]),
                    )
                    values.append((block_set, block_unit, option, Decimal(row["votes"])))
                    if row["seats"]:
                        cs.add("outcome", result_set_id=block_set, option_id=option, kind="won", seats=row["seats"])
                for row in lists.filter((pl.col("held") == held) & (pl.col("block") == block)).iter_rows(named=True):
                    member = party(row["party"], held)
                    home = codes[row["prefecture"]] if row["prefecture"] else f"pr{block_code}"
                    person = cs.key(f"{SOURCE}.person", f"{home}:{plain(row['name'])}")
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person)
                        name(person, row["name"], "ja")
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{row['party']}:{plain(row['name'])}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[row["party"]],
                        role="list_member",
                        list_position=row["position"],
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    if row["rank"]:
                        cs.add(
                            "outcome",
                            result_set_id=block_set,
                            option_id=options[row["party"]],
                            candidacy_id=candidacy,
                            kind="elected",
                        )
                previous = None
                for row in local.iter_rows(named=True):
                    place, previous = place_unit(row["prefecture"], row["place"], previous)
                    values.append((place_set, place, options[row["party"]], Decimal(row["votes"])))

        totals: dict[tuple[UUID, UUID, UUID], Decimal] = {}
        for result_set, place, option, amount_ in values:
            totals[result_set, place, option] = totals.get((result_set, place, option), Decimal(0)) + amount_
        for (result_set, place, option), amount_ in totals.items():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=amount_,
            )
        for result_set, place, measure, amount_ in accounting:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount_,
            )
        return cs.write()


SMD_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d{2}):(\d+)$")


def package_from_export() -> Path:
    """Build jp.mic/all from the archive's published rows (`ea package export`) with the one change `build` makes
    to them: every single-member contest's district_area_id names the jp.hor_smd area of its district in force on
    election day, where the changeset path linked only the contests held under the 2022 table. It reads no raw files,
    so it runs where the ministry's tables are not cached."""
    out = package.export(SOURCE)
    manifest = json.loads((out / "package.json").read_text())
    before = {code: n for code, n, _ in package.check(out)}
    contests = pl.read_parquet(out / "contest.parquet")
    keys = pl.read_parquet(out / "external_id.parquet").filter(pl.col("scheme") == f"{SOURCE}.contest")
    smd = {
        str(entity): found
        for value, entity in keys.select("value", "entity_id").iter_rows()
        if (found := SMD_KEY.match(str(value)))
    }
    with connect() as conn:
        areas = {day: district_areas(conn, date.fromisoformat(day)) for day in ELECTIONS.values()}
        # The export cites its files by storage key alone; as a Draft package does, the manifest cites each with its
        # url and sha256, which the inbox verifies the cache against.
        artifacts = pl.read_parquet(out / "source_artifact.parquet")
        cited = package._cited(conn, package._adopted(conn, SOURCE)[1], artifacts)
    linked: dict[str, str] = {}
    for contest, found in smd.items():
        held, prefecture, district = found[1], found[2], int(found[3])
        code = district_code(prefecture, district)
        if code not in areas[held]:
            raise LookupError(f"{held}: the archive holds no {DISTRICTS} area {code} on election day")
        linked[contest] = areas[held][code]
    kinds = dict(contests.select("id", "kind").iter_rows())
    if sorted(kinds[contest] for contest in linked) != ["body_seats"] * len(linked):
        raise ValueError("a jp.mic.contest key of the single-member form names a contest that is not body_seats")
    if len(linked) != contests.filter(pl.col("kind") == "body_seats").height:
        raise ValueError("a single-member contest has no jp.mic.contest key")
    before_links = dict(contests.select("id", "district_area_id").iter_rows())
    if moved := [c for c, area in linked.items() if before_links[c] is not None and before_links[c] != area]:
        raise ValueError(f"{len(moved)} contests already name another district area, such as {moved[0]}")
    contests = contests.with_columns(
        district_area_id=pl.col("id").replace_strict(linked, default=pl.col("district_area_id"), return_dtype=pl.String)
    )
    contests.write_parquet(out / "contest.parquet", statistics=False)
    counts = dict(manifest["tables"])
    built = package._seal(KEY, SOURCE, SUMMARY, out, counts, manifest["adopts"], cited, None)
    after = {code: n for code, n, _ in package.check(out)}
    if after != before:
        raise ValueError(f"{KEY}: the change moves the check findings from {before} to {after}")
    built["accepted"] = manifest["accepted"]
    (out / "package.json").write_text(json.dumps(built, indent=1) + "\n")
    added = sum(1 for contest in linked if before_links[contest] is None)
    print(f"{KEY}: {len(linked)} single-member contests name their district area, {added} newly linked", flush=True)
    return out
