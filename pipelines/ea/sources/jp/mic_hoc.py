import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic
from ea.sources.jp.mic import Places, dhondt, grid, party_key, party_label, person_name, plain, sheets
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_hoc"
RAW = mic.SOURCE
ROOT = mic.DATA_ROOT
ELECTIONS = {
    21: ("2007-07-29", "sangiin21/index.html", "sangiin21/sangiin21_3_14.html"),
    22: ("2010-07-11", "sangiin22/index_1.html", "sangiin22/sangiin22_7.html"),
    23: ("2013-07-21", "sangiin23/index.html", "sangiin23/sangiin23_7.html"),
    24: ("2016-07-10", "sangiin24/index.html", "sangiin24/sangiin24_8.html"),
    25: ("2019-07-21", "sangiin25/index.html", "sangiin25/sangiin25_8.html"),
    26: ("2022-07-10", "sangiin26/index.html", "sangiin26/sangiin26_8.html"),
    27: ("2025-07-20", "sangiin27/index.html", "sangiin27/sangiin27_8.html"),
}
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BOOK = "候補者別得票数（選挙区）"
SEATS = "党派別議席配分表（比例代表）"
LISTS = "党派別名簿登載者別得票数、当選人数（比例代表）"
PARTY_TOTALS = "党派別得票数（比例代表）"
PARSED_ELECTIONS = [23, 24, 25, 26, 27]
PREFECTURE_NAMES = [native for native, _ in PREFECTURES]
TOTAL_ROW = re.compile(r"(合計|[都道府県]計|県計)$")
SUBTOTAL_ROW = re.compile(r"計$")
DROPPED: list[dict] = []
UNCONFIRMED: list[dict] = []
ALIASES = {
    "緑の党グリーンズジャパン": "緑の党",
    "民進": "民進党",
    "維新": "おおさか維新の会",
    "公明": "公明党",
    "社民党": "社会民主党",
    "自民党": "自由民主党",
    "共産党": "日本共産党",
    "幸福実現等": "幸福実現党",
    "幸福": "幸福実現党",
    "日本": "日本のこころを大切にする党",
    "支持なし": "支持政党なし",
    "分のことしか考えていな": "自分のことしか考えていない党",
    "日本のこころを": "日本のこころを大切にする党",
    "生活の党と": "生活の党と山本太郎となかまたち",
    "労働の解放をめざす": "労働の解放をめざす労働者党",
}
MERGED = ("鳥取県・島根県", "徳島県・高知県")
SIZE = re.compile(r"[（(]定数(\d+)(?:[（(](\d+)[）)])?名[）)]")


def party_name(text: str) -> str:
    name = mic.party_name(text)
    return ALIASES.get(name, name)


def amount(text: str) -> Decimal:
    text = text.replace(",", "").replace("\u3000", "").strip()
    if text in {"", "-", "－", "―", "｜", "|"}:
        return Decimal(0)
    try:
        return Decimal(text)
    except ArithmeticError:
        if re.fullmatch(r"[A-Za-z]", text):
            return Decimal(0)
        raise ValueError(f"not a number: {text!r}") from None


def close(left: list[Decimal], right: list[Decimal]) -> bool:
    return len(left) == len(right) and all(abs(a - b) < Decimal("0.01") for a, b in zip(left, right, strict=True))


def url_of(base: str, href: str) -> str:
    return urljoin(base, href)


@dataclass
class Listing:
    index: str
    pages: list[tuple[str, list[str]]]
    book: str
    seats: str
    lists: str
    totals: str


def listing(number: int) -> Listing:
    _, index_path, towns_path = ELECTIONS[number]
    index = f"{ROOT}/{index_path}"
    towns = f"{ROOT}/{towns_path}.html" if not towns_path.endswith(".html") else f"{ROOT}/{towns_path}"
    named: dict[str, str] = {}
    for href, label in mic.anchors(index):
        if label not in named or not href.endswith(".pdf"):
            named[label] = url_of(index, href)
    if number == 21:
        subpages = [url_of(towns, href) for href, _ in mic.anchors(towns) if re.search(r"_14_\d+\.html$", href)]
    else:
        subpages = [
            url_of(towns, href)
            for href, _ in mic.anchors(towns)
            if re.search(r"\d+_\d+_\d\d\.html$", href) and not href.endswith("_00.html")
        ]
    pages = []
    for page in dict.fromkeys(subpages):
        files = [url_of(page, href) for href, _ in mic.anchors(page) if "main_content" in href or "/pdf/" in href]
        pages.append((page, files))
    return Listing(index, pages, named[BOOK], named[SEATS], named[LISTS], named[PARTY_TOTALS])


def leaves_of(found: list[tuple[str, list[Decimal]]], keep: set[str] | None = None) -> list[tuple[str, list[Decimal]]]:
    rows = [row for row in found if not SUBTOTAL_ROW.search(row[0])]
    if keep is not None:
        return [row for row in rows if row[0] in keep]
    drop: set[int] = set()
    for index, (_, values) in enumerate(rows):
        if not any(values):
            continue
        for size in range(1, 40):
            below = rows[index + 1 : index + 1 + size]
            if len(below) < size:
                break
            if [sum((row[1][k] for row in below), Decimal(0)) for k in range(len(values))] == values:
                drop.add(index)
                break
    for index, (_, values) in enumerate(rows):
        if index in drop or not any(values):
            continue
        run: list[int] = []
        for before in range(index - 1, -1, -1):
            if before in drop:
                continue
            run.append(before)
            if len(run) >= 2 and [sum((rows[i][1][k] for i in run), Decimal(0)) for k in range(len(values))] == values:
                drop.add(index)
                break
            if len(run) > 40:
                break
    return [row for index, row in enumerate(rows) if index not in drop]


def smd_sheet(rows: list[list[str]], held: str, sheet: str, labels: list[str]) -> tuple[list[dict], list[dict]]:
    name_row = next(i for i, row in enumerate(rows) if "候補者名" in row[:2] or row[0].startswith("開票区名\\候補者名"))
    old = not {"候補者名"} & set(rows[name_row][:2])
    first = next(j for j, cell in enumerate(rows[name_row]) if j > 0 and cell and "得票数計" not in cell)
    party_row = name_row - 1 if old else next(i for i in range(name_row + 1, name_row + 4) if rows[i][first])
    offset = 1 if "・" in sheet else 0
    columns = [
        j
        for j, cell in enumerate(rows[name_row])
        if j > offset and cell and "得票数計" not in cell and "率" not in cell
    ]
    found: list[tuple[str, list[Decimal]]] = []
    total = None
    owner = ""
    for row in rows[max(name_row, party_row) + 1 :]:
        if offset and row[0]:
            owner = re.sub(r"\s+", "", row[0])
        label = re.sub(r"\s+", "", row[offset] if offset else next((c for c in reversed(row[:first]) if c), ""))
        if not label or not any(row[j] for j in columns):
            continue
        values = [amount(row[j]) for j in columns]
        if TOTAL_ROW.search(label):
            total = values
        else:
            found.append((owner + "|" + label if offset else label, values))
    leaves = leaves_of(found)
    sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
    if total is not None and not close(sums, total):
        print(f"MISMATCH {held} {sheet}: leaves {sums} total {total}")
    candidates = [
        {
            "held": held,
            "district": labels[0],
            "order": position + 1,
            "name": person_name(rows[name_row][j]),
            "party": party_name(rows[party_row][j]),
            "votes": str(sums[position]),
        }
        for position, j in enumerate(columns)
    ]
    votes = [
        {"held": held, "district": labels[0], "place": name, "order": k + 1, "votes": str(v)}
        for name, values in leaves
        for k, v in enumerate(values)
    ]
    return candidates, votes


def confirmed(text: str, votes: Decimal, mark: str) -> bool:
    for form in mic.printed(votes):
        for hit in re.finditer(re.escape(form) + r"(?![\d.,])", text):
            if mark in text[max(0, hit.start() - 110) : hit.start()]:
                return True
    return False


def book_for(number: int) -> str:
    return mic.book_text(listing(number).book)


def districts(held: str) -> list[str]:
    if held < "2016":
        return PREFECTURE_NAMES
    return [*MERGED, *(name for name in PREFECTURE_NAMES if not name.startswith(("鳥取", "島根", "徳島", "高知")))]


def sizes(text: str, held: str) -> dict[str, tuple[int, int]]:
    found = {}
    for name in districts(held):
        match = re.search(re.escape(name) + SIZE.pattern, text)
        if match:
            found[name] = (int(match[1]), int(match[2] or 0))
    return found


def fetch() -> None:
    for number in ELECTIONS:
        found = listing(number)
        for url in [found.index, found.book, found.seats, found.lists, found.totals]:
            raw.fetch(RAW, url, suffix=mic.suffix(url))
        for page, files in found.pages:
            raw.fetch(RAW, page, suffix=".html")
            for url in files:
                raw.fetch(RAW, url, suffix=mic.suffix(url))


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


LABELS = {"届出番号", "政党等名", "党派名", "政党等の名称", "開票区名"}
SUBHEAD = re.compile(r"^(得票総数|政党等の|名簿登載者|政党等|得票数|\(?[abc]\)?$)")


def is_name(cell: str) -> bool:
    return bool(cell) and cell not in LABELS and not SUBHEAD.match(norm(cell).replace(" ", "").replace("\n", ""))


def pr_party_sheet(rows: list[list[str]], held: str, prefecture: str) -> list[dict]:
    out: list[dict] = []
    index = 0
    while index < len(rows):
        if "届出番号" not in rows[index][:2]:
            index += 1
            continue
        start = next(i for i in range(index, len(rows)) if rows[i][0].startswith("開票区名"))
        name_row = max(
            range(index + 1, start),
            key=lambda i: sum(1 for cell in rows[i][1:] if is_name(cell)),
        )
        data = start + 1
        while data < len(rows) and (not rows[data][0] or not any(rows[data][1:])):
            data += 1
        offsets = [j for j, cell in enumerate(rows[name_row]) if j > 0 and is_name(cell)]
        stop = data
        while stop < len(rows) and "届出番号" not in rows[stop][:2]:
            stop += 1
        block = []
        base = offsets[0]
        for row in rows[data:stop]:
            label = re.sub(r"\s+", "", next((c for c in reversed(row[:base]) if c), ""))
            if label and any(row[base:]):
                block.append((label, [amount(cell) for cell in row[base:]]))
        total = next((values for label, values in block if TOTAL_ROW.search(label)), None)
        leaves = leaves_of([row for row in block if not TOTAL_ROW.search(row[0])])
        for column in offsets:
            party = party_name(rows[name_row][column])
            at = column - base
            sums = [sum((values[at + k] for _, values in leaves), Decimal(0)) for k in range(3)]
            if total is not None and not close(sums, total[at : at + 3]):
                print(f"MISMATCH {held} {prefecture} {party}: {sums} against {total[at : at + 3]}")
            for label, values in leaves:
                out.append(
                    {
                        "held": held,
                        "prefecture": prefecture,
                        "place": label,
                        "party": party,
                        "total": str(values[at]),
                        "party_votes": str(values[at + 1]),
                        "candidate_votes": str(values[at + 2]),
                    }
                )
        index = stop
    return out


def pr_candidate_sheets(path, held: str, prefecture: str, keep: set[str]) -> list[dict]:
    out: list[dict] = []
    for sheet in sheets(path):
        rows = grid(path, sheet)
        number_row = next(i for i, row in enumerate(rows) if row[0] == "整理番号")
        party = party_name(rows[next(i for i, row in enumerate(rows) if row[0] == "政党等の名称") + 1][0])
        columns = [j for j, cell in enumerate(rows[number_row]) if j > 0 and cell]
        block = []
        for row in rows[number_row + 2 :]:
            label = re.sub(r"\s+", "", row[0])
            if label.endswith("区") and label not in keep:
                label = next(iter(name for name in keep if name.endswith(label)), label)
            if label and any(row[j] for j in columns):
                block.append((label, [amount(row[j]) for j in columns]))
        total = next((values for label, values in block if TOTAL_ROW.search(label)), None)
        leaves = leaves_of([row for row in block if not TOTAL_ROW.search(row[0])], keep)
        sums = [sum((values[k] for _, values in leaves), Decimal(0)) for k in range(len(columns))]
        slack = Decimal(sum(1 for _, values in leaves if any(values))) / 2 + Decimal("0.01")
        if total is not None and not all(abs(a - b) <= slack for a, b in zip(sums, total, strict=True)):
            print(f"MISMATCH {held} {prefecture} {party} candidates: {sums} against {total}")
            DROPPED.append({"held": held, "prefecture": prefecture, "party": party})
            continue
        for position, column in enumerate(columns):
            number = int(rows[number_row][column])
            ballot = person_name(rows[number_row + 1][column])
            for label, values in leaves:
                out.append(
                    {
                        "held": held,
                        "prefecture": prefecture,
                        "place": label,
                        "party": party,
                        "number": number,
                        "name": ballot,
                        "votes": str(values[position]),
                    }
                )
    return out


def national_lists(path, held: str) -> tuple[list[dict], list[dict]]:
    parties: list[dict] = []
    people: list[dict] = []
    rows = grid(path, sheets(path)[0])
    for sheet in sheets(path)[1:]:
        rows += grid(path, sheet)
    index = 0
    while index < len(rows):
        starts = [j for j, cell in enumerate(rows[index]) if cell == "政党等の名称"]
        if not starts:
            index += 1
            continue
        end = next((i for i in range(index + 1, len(rows)) if "政党等の名称" in rows[i]), len(rows))
        band = rows[index:end]
        for c in starts:
            width = next((s - c for s in starts if s > c), len(rows[index]) - c)
            party = party_name(next(cell for row in band[1:5] for cell in row[c : c + width] if cell))
            if party.startswith("得票総数"):
                continue
            record = {"held": held, "party": party}
            for row in band:
                label = row[c].replace(" ", "")
                cells = [cell for cell in row[c + 1 : c + width] if re.fullmatch(r"[\d,.]+", cell)]
                if not cells and label != "当選人数":
                    continue
                if label.startswith("得票総数(a)+(b)"):
                    record["total"] = str(amount(cells[0]))
                elif label.startswith("政党等の得票総数(a)"):
                    record["party_votes"] = str(amount(cells[0]))
                elif label.startswith("名簿登載者数"):
                    record["listed"] = int(cells[0])
                elif label.startswith("名簿登載者"):
                    record["candidate_votes"] = str(amount(cells[0]))
                elif label.startswith("当選人数"):
                    record["seats"] = int(cells[0])
            heading = next(i for i, row in enumerate(band) if row[c] in ("順位", "得票順位"))
            for row in band[heading + 1 :]:
                if not row[c].isdigit():
                    continue
                people.append(
                    {
                        "held": held,
                        "party": party,
                        "number": int(row[c]),
                        "result": row[c + 1],
                        "name": person_name(row[c + 2]),
                        "votes": str(amount(next((cell for cell in row[c + 3 : c + width] if cell), ""))),
                        "special": next((cell for cell in row[c + 3 : c + width] if cell), "") == "-",
                    }
                )
            parties.append(record)
        index = end
    return parties, people


def seat_table(path) -> dict[str, tuple[Decimal, int]]:
    seats: dict[str, list] = {}
    names: dict[int, str] = {}
    waiting = False
    for row in grid(path, sheets(path)[0]):
        if row[0] == "除数":
            waiting = True
        if waiting and any(row[1:]) and not (row[0] == "除数" and not any(row[1:])):
            names = {j: party_name(cell) for j, cell in enumerate(row) if j > 0 and cell}
            for name in names.values():
                seats.setdefault(name, [Decimal(0), 0])
            waiting = False
        elif row[0].isdigit() and names:
            for j, name in names.items():
                if row[0] == "1":
                    seats[name][0] = amount(row[j + 1])
                if row[j]:
                    seats[name][1] += 1
    return {name: (votes, won) for name, (votes, won) in seats.items()}


def prefecture_of_page(page: str) -> str:
    match = re.search(r"_(\d+)\.html$", page)
    if match is None:
        raise ValueError(page)
    return PREFECTURE_NAMES[int(match[1]) - 1]


def parse(numbers: list[int] | None = None) -> None:
    candidates: list[dict] = []
    smd_votes: list[dict] = []
    parties: list[dict] = []
    people: list[dict] = []
    seats: list[dict] = []
    pr_places: list[dict] = []
    pr_candidates: list[dict] = []
    for number in numbers or PARSED_ELECTIONS:
        held = ELECTIONS[number][0]
        found = listing(number)
        text = book_for(number)
        size = sizes(text, held)
        done: set[str] = set()
        parts: dict[str, tuple[list[dict], list[dict]]] = {}
        for page, files in found.pages:
            prefecture = prefecture_of_page(page)
            path = raw.cached(RAW, files[0]).path
            if files[0] not in done:
                done.add(files[0])
                names = sheets(path)
                sheet = next((x for x in names if "・" in x), names[0])
                pair = next((m for m in MERGED if prefecture in m and held >= "2016"), prefecture)
                district = sheet if "・" in sheet else pair
                more, votes = smd_sheet(grid(path, sheet), held, sheet, [district])
                if district in parts and "・" not in sheet:
                    for earlier, candidate in zip(parts[district][0], more, strict=True):
                        earlier["votes"] = str(Decimal(earlier["votes"]) + Decimal(candidate["votes"]))
                    parts[district][1].extend(votes)
                else:
                    parts[district] = (more, votes)
            party_path = raw.cached(RAW, files[1]).path
            parties_sheet = sheets(party_path)[0]
            places = pr_party_sheet(grid(party_path, parties_sheet), held, prefecture)
            pr_places += places
            keep = {row["place"] for row in places}
            pr_candidates += pr_candidate_sheets(raw.cached(RAW, files[2]).path, held, prefecture, keep)
        for district, (more, votes) in parts.items():
            won = sum(size[district])
            top = sorted(more, key=lambda c: Decimal(c["votes"]), reverse=True)
            if won < len(top) and Decimal(top[won - 1]["votes"]) == Decimal(top[won]["votes"]):
                raise ValueError(f"{held} {district}: tie at the last seat")
            for candidate in more:
                candidate["result"] = "当" if candidate in top[:won] else "落"
            if all(confirmed(text, Decimal(c["votes"]), c["result"]) for c in more):
                candidates += more
                smd_votes += votes
            else:
                print(f"UNCONFIRMED {held} {district}: the municipal file disagrees with the book")
                UNCONFIRMED.append({"held": held, "district": district})
        listed, ranked = national_lists(raw.cached(RAW, found.lists).path, held)
        allocation = seat_table(raw.cached(RAW, found.seats).path)
        for record in listed:
            if record["party"] == "合計":
                continue
            votes, won = allocation[record["party"]]
            if won != record["seats"] or votes != Decimal(record["total"]):
                raise ValueError(f"{held} {record['party']}: list table {record} against allocation {votes} {won}")
            parties.append(record)
            seats.append({"held": held, "party": record["party"], "seats": won})
        people += [row for row in ranked if row["party"] != "合計"]
    known = {*(row["party"] for row in parties), "無所属"}
    for candidate in candidates:
        if candidate["name"] in known and candidate["party"] not in known:
            candidate["name"], candidate["party"] = candidate["party"], candidate["name"]
    PARSED.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("candidates", candidates),
        ("smd", smd_votes),
        ("parties", parties),
        ("people", people),
        ("pr", pr_places),
        ("pr_candidates", pr_candidates),
    ):
        pl.DataFrame(rows).write_parquet(PARSED / f"{name}-v{VERSION}.parquet")


TITLE = "参議院議員通常選挙結果調"
TYPOS = {"泰野市": "秦野市"}
SMD_RULES = {"formula": "sntv"}
PR_RULES = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}}
SMD_SYSTEM = (
    "Each prefectural district elects its seats by single non-transferable vote: every voter names one candidate, "
    "and the candidates with the most votes win the seats. Half the House is up every three years, so a district "
    "elects half of its seats at each election. Tottori and Shimane, and Tokushima and Kochi, have formed one "
    "district each since 2016."
)
PR_SYSTEM = (
    "The national list tier fills its seats by D'Hondt on each party's total, which is the votes written for the "
    "party plus the votes written for its candidates. Within a party, seats go to the candidates with the most "
    "personal votes. Since 2019, a party may place candidates in a priority quota (特定枠) who fill its seats "
    "first, in the order the party fixed, whatever their personal votes; those candidates receive no personal "
    "votes."
)
SMD_METHOD = (
    "Candidate votes by counting district (開票区), from the ministry's tables by municipality, which the "
    "prefectural election commissions compile. Counting districts are municipalities or designated-city wards. "
    "Votes for candidates whose names other candidates share are apportioned (按分) and carry three decimals. "
    "The winners are the candidates with the most votes for the district's seats, and each candidate's total and "
    "result mark were checked against the ministry's 結果調 book."
)
PR_NATIONAL_METHOD = (
    "Party totals, party-only votes, personal votes, and seats from the ministry's national tables (党派別議席配分表 "
    "and 党派別名簿登載者別得票数、当選人数) in the 結果調 book. Personal votes for candidates whose names other "
    "candidates share are apportioned (按分). Candidates in a party's priority quota are listed with no votes."
)
PR_PLACE_METHOD = (
    "Votes by counting district from the ministry's tables by municipality: each party's total, its party-only "
    "votes, and each candidate's personal votes. A candidate with no personal votes in a district has no value "
    "there."
)
PR_ENGINE = (
    "The declared seats differ from a plain D'Hondt count on the party totals. Declared, then D'Hondt alone: {moved}."
)
BY_ELECTION = (
    "{count} of this district's seats fill a vacancy for the remainder of a term, and the ministry's tables count "
    "them with the district's regular seats."
)


def codes_of(district: str) -> list[str]:
    return [f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1) if native in district]


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    smd = pl.read_parquet(PARSED / f"smd-v{VERSION}.parquet")
    parties = pl.read_parquet(PARSED / f"parties-v{VERSION}.parquet")
    people = pl.read_parquet(PARSED / f"people-v{VERSION}.parquet")
    pr = pl.read_parquet(PARSED / f"pr-v{VERSION}.parquet")
    pr_people = pl.read_parquet(PARSED / f"pr_candidates-v{VERSION}.parquet")
    english = dict(PREFECTURES)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Councillors regular elections 2013-2025 from the Ministry of Internal Affairs "
            "and Communications: prefectural district candidates and national list votes by counting district, with "
            "declared winners and seats",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-councillors")
        if {japan, house} & cs.minted:
            raise ValueError("run jp.mlit and jp.diet first")
        source = cs.source(mic.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=mic.PUBLISHER,
            title=TITLE,
            url=f"{ROOT}/sangiin/index.html",
            license=mic.LICENSE,
            license_url=mic.LICENSE_URL,
            attribution=mic.ATTRIBUTION,
        )
        smd_system = cs.key(f"{SOURCE}.electoral_system", "hoc-sntv")
        cs.add("electoral_system", id=smd_system, polity_id=japan, rules=SMD_RULES, rules_version=1)
        name(smd_system, "House of Councillors prefectural districts, single non-transferable vote")
        name(smd_system, "選挙区選出（単記非移譲式）", "ja", "native", False)
        cs.add("note", entity_id=smd_system, kind="methodology", body_md=SMD_SYSTEM)
        pr_system = cs.key(f"{SOURCE}.electoral_system", "hoc-pr")
        cs.add("electoral_system", id=pr_system, polity_id=japan, rules=PR_RULES, rules_version=1)
        name(pr_system, "House of Councillors national open lists by D'Hondt")
        name(pr_system, "比例代表選出（非拘束名簿式、ドント式）", "ja", "native", False)
        cs.add("note", entity_id=pr_system, kind="methodology", body_md=PR_SYSTEM)

        keyed: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = party_key(text, held)
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

        def person(key: str, text: str) -> UUID:
            entity = cs.key(f"{SOURCE}.person", key)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("person", id=entity)
                name(entity, text, "ja")
            return entity

        nation = unit("jp.nation", "JP", None, cs.area("jp.nation", "JP"), "equals", japan)
        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        for number, (held, index_path, _) in ELECTIONS.items():
            if held not in set(candidates["held"]):
                continue
            day = date.fromisoformat(held)
            found = listing(number)
            for url in [found.index, found.book, found.seats, found.lists, found.totals]:
                cs.artifact(raw.cached(RAW, url), source)
            for page_url, files in found.pages:
                cs.artifact(raw.cached(RAW, page_url), source)
                for url in files:
                    cs.artifact(raw.cached(RAW, url), source)
            by_election = {district: extra for district, (_, extra) in sizes(book_for(number), held).items() if extra}
            places = Places(conn, day)

            def place_unit(prefectures: list[str], place: str, previous: str | None) -> tuple[UUID, str]:
                owner, _, label = place.rpartition("|")
                label = TYPOS.get(label, label)
                hit = [code for code in prefectures if owner and english.get(owner) and codes_of(owner) == [code]]
                resolved = []
                for code in hit or prefectures:
                    try:
                        resolved.append((code, *places.resolve(code, label, previous)))
                    except LookupError:
                        continue
                if len(resolved) != 1:
                    raise LookupError(f"{held} {prefectures} {place}: {len(resolved)} matches")
                code, scheme, area_code, piece = resolved[0]
                parent = prefecture_unit(code)
                polity = cs.polity(f"JP-{code}")
                area = cs.area(scheme, area_code)
                whole = unit(scheme, area_code, parent, area, "equals", polity)
                if piece is None:
                    return whole, area_code
                return unit(mic.PIECES, f"{held}:{area_code}:{piece}", whole, area, "within", polity), area_code

            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese House of Councillors election")
            name(election, f"第{number}回参議院議員通常選挙", "ja", "native", False)
            path = f"jp/{held}-sangiin"
            cs.add("slug", path=path, entity_id=election)

            for district, rows in candidates.filter(pl.col("held") == held).group_by("district", maintain_order=True):
                district = str(district[0])
                key = f"{held}:{district}"
                prefectures = codes_of(district)
                seats_won = int((rows["result"] == "当").sum())
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=seats_won,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                label = (
                    " and ".join(english[native] for native in district.split("・"))
                    if "・" in district
                    else english[district]
                )
                name(contest, f"{label} district")
                name(contest, f"{district}選挙区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", key)
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
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=SMD_METHOD)
                if district in by_election:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=BY_ELECTION.format(count=by_election[district]),
                    )
                options: dict[int, UUID] = {}
                for row in rows.iter_rows(named=True):
                    option = options[row["order"]] = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
                    who = person(f"smd:{district}:{plain(row['name'])}", row["name"])
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
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if row["result"] == "当" else "lost",
                        seats=1 if row["result"] == "当" else None,
                    )
                previous = None
                for row in smd.filter((pl.col("held") == held) & (pl.col("district") == district)).iter_rows(
                    named=True
                ):
                    place, previous = place_unit(prefectures, row["place"], previous)
                    values.append((result_set, place, options[row["order"]], Decimal(row["votes"])))

            national = parties.filter(pl.col("held") == held)
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
            national_set = cs.key(f"{SOURCE}.result_set", f"{held}:national")
            place_set = cs.key(f"{SOURCE}.result_set", f"{held}:national:places")
            local = pr.filter(pl.col("held") == held)
            local_total = sum((Decimal(v) for v in local["total"]), Decimal(0))
            declared_total = sum((Decimal(v) for v in national["total"]), Decimal(0))
            for result_set, default, method, share in (
                (national_set, True, PR_NATIONAL_METHOD, 1.0),
                (place_set, False, PR_PLACE_METHOD, float(local_total / declared_total)),
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
            totals = {row["party"]: Decimal(row["total"]) for row in national.iter_rows(named=True)}
            declared = {row["party"]: row["seats"] for row in national.iter_rows(named=True)}
            engine = dhondt(totals, size)
            if engine != declared:
                moved = ", ".join(f"{p} {declared[p]} and {engine[p]}" for p in totals if engine[p] != declared[p])
                cs.add("note", entity_id=national_set, kind="caveat", body_md=PR_ENGINE.format(moved=moved))
            options_by: dict[tuple[str, int | None], UUID] = {}
            for position, row in enumerate(national.iter_rows(named=True), 1):
                label = row["party"]
                member = party(label, held)
                listed = cs.key(f"{SOURCE}.option", f"{held}:national:{label}")
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
                options_by[label, -1] = listed
                legend = options_by[label, None] = cs.key(f"{SOURCE}.option", f"{held}:national:{label}:party")
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
                    option = options_by[label, candidate["number"]] = cs.key(
                        f"{SOURCE}.option", f"{held}:national:{label}:{candidate['number']}"
                    )
                    who = person(f"pr:{party_label(label)}:{plain(candidate['name'])}", candidate["name"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=listed,
                        party_id=member,
                        ballot_position=candidate["number"],
                        ballot_label=candidate["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{held}:national:{label}:{candidate['number']}"),
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
                    if not candidate["special"]:
                        values.append((national_set, nation, option, Decimal(candidate["votes"])))
            for row in local.iter_rows(named=True):
                code = codes_of(row["prefecture"])
                place, _ = place_unit(code, row["place"], None)
                values.append((place_set, place, options_by[row["party"], -1], Decimal(row["total"])))
                values.append((place_set, place, options_by[row["party"], None], Decimal(row["party_votes"])))
            for row in pr_people.filter((pl.col("held") == held) & (pl.col("votes").cast(pl.Float64) > 0)).iter_rows(
                named=True
            ):
                place, _ = place_unit(codes_of(row["prefecture"]), row["place"], None)
                values.append((place_set, place, options_by[row["party"], row["number"]], Decimal(row["votes"])))

        sums: dict[tuple[UUID, UUID, UUID], Decimal] = {}
        for result_set, place, option, amount_ in values:
            sums[result_set, place, option] = sums.get((result_set, place, option), Decimal(0)) + amount_
        for (result_set, place, option), amount_ in sums.items():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=mic.VOTES,
                count_round=0,
                flags=0,
                value=amount_,
            )
        return cs.write()
