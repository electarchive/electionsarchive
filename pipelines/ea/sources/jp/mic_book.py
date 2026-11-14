import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic
from ea.sources.jp.mic import BLOCK_OF
from ea.sources.jp.mlit import BLOCKS, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_book"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"


@dataclass(frozen=True)
class Book:
    held: str
    index: tuple[str, str]
    book: tuple[str, str]


BOOKS = {
    44: Book(
        "2005-09-11",
        ("20071230111711", "http://www.soumu.go.jp:80/senkyo/senkyo_s/data/shugiin44/index.html"),
        ("20110520101329", "http://www.soumu.go.jp/senkyo/senkyo_s/data/shugiin44/pdf/h17sousenkyo_050911_all.pdf"),
    ),
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
FIGURE = re.compile(r"^[\d,]+(?:\.\d+)?$")
DISTRICT = re.compile(r"^第(\d+)区$")
MARKS = {"当", "落"}
GLYPHS = str.maketrans({"\x08": "吉", "\x04": "隆"})
NAMES = {
    ("2005-09-11", "茨城県", 2, "ぬかが 8志郎"): "ぬかが 福志郎",
    ("2005-09-11", "高知県", 2, "谷ß 治之"): "谷﨑 治之",
    ("2005-09-11", "福岡県", 6, "はとやま 3夫"): "はとやま 邦夫",
    ("2005-09-11", "大分県", 1, "佐藤 !"): "佐藤 錬",
}
LIST_NAMES = {"江 てつま"}
LISTED = {
    ("2005-09-11", "江0 洋一郎"): "江﨑 洋一郎",
    ("2005-09-11", "とかしきなおみ"): "とかしき なおみ",
    ("2005-09-11", "仲野ひろ子"): "仲野 ひろ子",
}
LEGAL = {("2005-09-11", "福岡県", 6, "鳩山 3夫"): "鳩山 邦夫"}


def archived(stamp: str, url: str) -> str:
    return WAYBACK.format(stamp=stamp, url=url)


def fetch() -> None:
    for book in BOOKS.values():
        raw.fetch(SOURCE, archived(*book.index), suffix=".html")
        raw.fetch(SOURCE, archived(*book.book), suffix=".pdf")


@dataclass(frozen=True)
class Word:
    x: float
    right: float
    top: float
    bottom: float
    text: str


def pages(path: Path) -> list[list[Word]]:
    html = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    out = []
    for chunk in html.split("<page ")[1:]:
        out.append(
            [
                Word(float(x0), float(x1), float(y0), float(y1), unicodedata.normalize("NFKC", text))
                for x0, y0, x1, y1, text in WORD.findall(chunk)
            ]
        )
    return out


def lines(words: list[Word], tolerance: float = 2.0) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.bottom):
        if rows and word.bottom - rows[-1][0].bottom <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w.x) for row in rows]


def heading(words: list[Word]) -> str:
    return "".join(w.text for w in sorted(words, key=lambda w: (w.top, w.x))[:6])


def prefecture_of(text: str) -> str:
    return next(native for native, _ in PREFECTURES if native == text or native[:-1] == text)


def figure(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def script(char: str) -> str:
    name = unicodedata.name(char, "")
    return "kana" if name.startswith(("HIRAGANA", "KATAKANA")) else "kanji"


def split_name(chars: list[Word]) -> str:
    chars = [w for w in chars if w.text]
    if len(chars) > 1:
        gaps = [(chars[i + 1].x - chars[i].right, i) for i in range(len(chars) - 1)]
        widest, at = max(gaps)
        others = [gap for gap, i in gaps if i != at]
        if not others or widest > max(others) + 3:
            return "".join(w.text for w in chars[: at + 1]) + " " + "".join(w.text for w in chars[at + 1 :])
    text = "".join(w.text for w in chars)
    changes = [i for i in range(1, len(text)) if script(text[i]) != script(text[i - 1])]
    if len(changes) == 1:
        return f"{text[: changes[0]]} {text[changes[0] :]}"
    return text


def smd_columns(words: list[Word]) -> list[tuple[float, list[Word]]]:
    origins = sorted(w.x for w in words if w.text == "当落")
    if len(origins) != 2:
        raise ValueError(f"expected two 当落 headers, found {origins}")
    cut = origins[1] - 3
    return [(origins[0], [w for w in words if w.x < cut]), (origins[1], [w for w in words if w.x >= cut])]


def smd(book: list[list[Word]], held: str) -> list[dict]:
    candidates: list[dict] = []
    current: tuple[str, int] | None = None
    last: dict | None = None
    for number, words in enumerate(book, 1):
        if "候補者別得票数" not in heading(words):
            continue
        for origin, column in smd_columns(words):
            for row in lines(column):
                rel = [(w.x - origin, w) for w in row]
                first = rel[0][1].text
                header = DISTRICT.match(row[1].text) if len(row) > 1 else None
                if header and rel[0][0] < 5:
                    current = (prefecture_of(first), int(header[1]))
                    forfeit = "".join(w.text for x, w in rel if x > 330 and re.match(r"^[\d,.]+$", w.text))
                    candidates.append(
                        {"held": held, "kind": "forfeit", "page": number, "value": forfeit} | key(current)
                    )
                    last = None
                    continue
                if first in MARKS and rel[0][0] < 10:
                    if current is None:
                        raise ValueError(f"page {number}: candidate before any district")
                    votes = ["".join(w.text for x, w in rel if 285 <= x <= 340 and re.match(r"^[\d,.]+$", w.text))]
                    if not FIGURE.match(votes[0]):
                        raise ValueError(f"page {number}: no vote figure on {[w.text for w in row]}")
                    chars = [w for x, w in rel if 12 <= x < 112]
                    party = "".join(w.text for x, w in rel if 140 <= x < 228)
                    last = {
                        "held": held,
                        "kind": "candidate",
                        "page": number,
                        "result": first,
                        "name": split_name(chars),
                        "gender": next((w.text for x, w in rel if 112 <= x < 125), ""),
                        "party": mic.party_name(party),
                        "status": next((w.text for x, w in rel if 228 <= x < 242), ""),
                        "value": votes[0],
                        "legal": None,
                    } | key(current)
                    last["name"] = last["name"].translate(GLYPHS)
                    last["name"] = NAMES.get((held, *current, last["name"]), last["name"])
                    candidates.append(last)
                    continue
                if last is not None and first.startswith("(") and rel[0][0] < 25:
                    chars = [w for x, w in rel if 12 <= x < 112]
                    text = "".join(w.text for w in chars).strip("()")
                    if text:
                        legal = split_name([Word(w.x, w.right, w.top, w.bottom, w.text.strip("()")) for w in chars])
                        legal = legal.translate(GLYPHS)
                        last["legal"] = LEGAL.get((held, last["prefecture"], last["district"], legal), legal)
    return candidates


def key(current: tuple[str, int]) -> dict:
    return {"prefecture": current[0], "district": current[1]}


def digits(row: list[tuple[float, Word]], low: float, high: float) -> str:
    return "".join(w.text for x, w in row if low <= x < high and re.match(r"^[\d,.]+$", w.text))


def pr(book: list[list[Word]], held: str) -> list[dict]:
    rows: list[dict] = []
    for number, words in enumerate(book, 1):
        if "比例代表党派別当選人数" not in "".join(w.text for w in words[:4]):
            continue
        block = next(w.text for w in words if w.text.endswith("選挙区")).removesuffix("選挙区")
        header = lines(words)
        origins: list[tuple[float, str]] = []
        for line in header:
            texts = [w.text for w in line]
            for i, w in enumerate(line):
                if texts[i : i + 4] == ["政", "党", "等", "名"]:
                    origins.append((w.x, line[i + 4].text))
        if not origins:
            raise ValueError(f"page {number}: no party headers")
        parties: dict[str, dict[str, Any]] = {name: {"votes": "", "seats": None, "list": []} for _, name in origins}
        for line in header:
            for origin, name in origins:
                rel = [(w.x - origin, w) for w in line if -4 <= w.x - origin < 184]
                texts = [w.text for _, w in rel]
                if texts[:3] == ["得", "票", "数"]:
                    parties[name]["votes"] = digits(rel, 40, 170)
                elif texts[:4] == ["当", "選", "人", "数"]:
                    parties[name]["seats"] = int(digits(rel, 100, 160))
                elif (
                    rel and rel[0][0] < 8 and re.match(r"^\d+$", rel[0][1].text) and any(14 <= x < 110 for x, _ in rel)
                ):
                    parties[name]["list"].append(
                        {
                            "rank": int(rel[0][1].text),
                            "name": split_name([w for x, w in rel if 14 <= x < 110]).translate(GLYPHS),
                            "order": int(digits(rel, 110, 135) or 0) or None,
                            "district": next((w.text for x, w in rel if 135 <= x < 150 and w.text in MARKS), None),
                            "ratio": "".join(w.text for x, w in rel if 150 <= x < 184) or None,
                        }
                    )
        for name, party in parties.items():
            if not FIGURE.match(party["votes"]) or party["seats"] is None:
                raise ValueError(f"page {number}: {block} {name} lacks votes or seats")
            elected = [row for row in party["list"] if row["order"]]
            if len(elected) != party["seats"]:
                raise ValueError(
                    f"page {number}: {block} {name} lists {len(elected)} elected for {party['seats']} seats"
                )
            rows.append({"held": held, "block": block, "party": name, "page": number} | party)
    return rows


def odd(text: str) -> bool:
    return any(
        not unicodedata.name(ch, "").startswith(("CJK", "HIRAGANA", "KATAKANA")) and ch not in " 々ヶ" for ch in text
    )


def dual(people: list[dict], block: str, party: str, row: dict) -> dict:
    pool = [
        person
        for person in people
        if BLOCK_OF[person["prefecture"]] == block and person["result"] == row["district"] and person["party"] == party
    ]
    wanted = mic.plain(row["name"])
    exact = [p for p in pool if wanted in {mic.plain(p["name"]), mic.plain(p["legal"] or "")}]
    if len(exact) == 1:
        return exact[0]
    scored = sorted(
        (
            (max(len(set(wanted) & set(mic.plain(p[k] or ""))) for k in ("name", "legal")), i)
            for i, p in enumerate(pool)
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < 2 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        raise ValueError(f"{block} {party} {row['name']}: no single district candidate matches")
    return pool[scored[0][1]]


def book_path(book: Book) -> Path:
    return raw.cached(SOURCE, archived(*book.book)).path


def parse() -> None:
    districts: list[dict] = []
    lists: list[dict] = []
    members: list[dict] = []
    for book in BOOKS.values():
        words = pages(book_path(book))
        found = smd(words, book.held)
        forfeits = {(r["prefecture"], r["district"]): figure(r["value"]) for r in found if r["kind"] == "forfeit"}
        people = [r for r in found if r["kind"] == "candidate"]
        for (prefecture, district), forfeit in forfeits.items():
            group = [r for r in people if (r["prefecture"], r["district"]) == (prefecture, district)]
            total = sum(figure(r["value"]) for r in group)
            if abs(total - forfeit * 10) > 1 or [r["result"] for r in group].count("当") != 1:
                raise ValueError(f"{book.held} {prefecture} {district}: {total} against forfeit {forfeit}")
            for order, row in enumerate(group, 1):
                districts.append(
                    {key: row[key] for key in ("held", "prefecture", "district", "name", "legal", "party", "status")}
                    | {"order": order, "page": row["page"], "votes": str(figure(row["value"])), "result": row["result"]}
                )
        for party in pr(words, book.held):
            if any(
                (row["held"], row["block"], row["party"]) == (book.held, party["block"], party["party"])
                for row in lists
            ):
                raise ValueError(f"{book.held} {party['block']} {party['party']} appears twice")
            lists.append(
                {key: party[key] for key in ("held", "block", "party", "page", "seats")}
                | {"votes": str(figure(party["votes"]))}
            )
            for row in party["list"]:
                row["name"] = LISTED.get((book.held, row["name"]), row["name"])
                match = dual(people, party["block"], party["party"], row) if row["district"] else None
                members.append(
                    {
                        "held": book.held,
                        "block": party["block"],
                        "party": party["party"],
                        "rank": row["rank"],
                        "name": match["name"]
                        if match and (odd(row["name"]) or row["name"] in LIST_NAMES)
                        else row["name"],
                        "prefecture": match["prefecture"] if match else None,
                        "district_number": match["district"] if match else None,
                        "elected": row["order"],
                        "district": row["district"],
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(districts).write_parquet(PARSED / f"districts-v{VERSION}.parquet")
    pl.DataFrame(lists).write_parquet(PARSED / f"lists-v{VERSION}.parquet")
    pl.DataFrame(members).write_parquet(PARSED / f"members-v{VERSION}.parquet")


SMD_METHOD = (
    "Candidate votes by district from the ministry's 結果調 book (候補者別得票数, 小選挙区). The book gives no "
    "breakdown below the district. Each district's candidate votes add up to ten times its printed deposit threshold "
    "(供託物没収点, a tenth of the valid votes). Votes for candidates whose names other candidates share are "
    "apportioned (按分) and carry three decimals. Result marks (当, 落) are the book's."
)
PR_METHOD = (
    "Party list votes, seats, and the members elected from each list, from the ministry's 結果調 book (比例代表党派別"
    "当選人数). List positions are the parties' ranks (名簿順位); dual candidates share a rank and are ordered by "
    "their district vote as a share of the district winner's (惜敗率). The book's tables of list votes by prefecture "
    "have no text layer and are not loaded."
)


def emit() -> Path:
    districts = pl.read_parquet(PARSED / f"districts-v{VERSION}.parquet")
    lists = pl.read_parquet(PARSED / f"lists-v{VERSION}.parquet")
    members = pl.read_parquet(PARSED / f"members-v{VERSION}.parquet")
    codes = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    english = {native: name for native, name in PREFECTURES}
    blocks = {native: (f"{number:02d}", name) for number, (native, name, _) in enumerate(BLOCKS, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Representatives general election of 2005 from the Ministry of Internal Affairs "
            "and Communications' 結果調 book: district candidates by district and party lists by block, with declared "
            "winners, block seats, and elected list members",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        house = cs.key("jp.body", "house-of-representatives")
        smd_system = cs.key(f"{mic.SOURCE}.electoral_system", "hor-smd")
        pr_system = cs.key(f"{mic.SOURCE}.electoral_system", "hor-pr")
        source = cs.source(mic.PUBLISHER, mic.TITLE)
        if {japan, house, smd_system, pr_system, source} & cs.minted:
            raise ValueError("run jp.mlit, jp.diet, and jp.mic first")

        parties: dict[tuple[str, str], UUID] = {}

        def party(text: str, held: str) -> UUID | None:
            found = mic.party_key(text, held)
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

        def person(code: str, ballot: str, legal: str | None) -> UUID:
            entity = cs.key(f"{mic.SOURCE}.person", f"{code}:{mic.plain(ballot)}")
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("person", id=entity)
                name(entity, ballot, "ja")
                if legal and mic.plain(legal) != mic.plain(ballot):
                    name(entity, legal, "ja", "official", False)
            return entity

        def prefecture_unit(code: str) -> UUID:
            entity = cs.unit("jp.prefecture", code)
            if entity in cs.minted:
                raise ValueError("run jp.mic first")
            return entity

        values: list[tuple[UUID, UUID, UUID, Decimal]] = []
        for number, book in BOOKS.items():
            held = book.held
            day = date.fromisoformat(held)
            for stamp, url in (book.index, book.book):
                cs.artifact(raw.cached(SOURCE, archived(stamp, url)), source)
            election = cs.key(f"{SOURCE}.election", held)
            cs.add("election", id=election, polity_id=japan, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} Japanese general election")
            name(election, f"第{number}回衆議院議員総選挙", "ja", "native", False)
            path = f"jp/{held}-general"
            cs.add("slug", path=path, entity_id=election)

            for (prefecture, district), rows in districts.filter(pl.col("held") == held).group_by(
                "prefecture", "district", maintain_order=True
            ):
                code = codes[str(prefecture)]
                key = f"{held}:{code}:{district}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=house,
                    magnitude=1,
                    electoral_system_id=smd_system,
                    default_result_kind="counted",
                )
                name(contest, f"{english[str(prefecture)]} {district}")
                name(contest, f"{prefecture}第{district}区", "ja", "native", False)
                cs.add("slug", path=f"{path}/{slug(english[str(prefecture)])}-{district}", entity_id=contest)
                unit = cs.unit("jp.hor_smd", f"{held}:{code}{district:02d}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.polity(f"JP-{code}"),
                        scheme_code="jp.hor_smd",
                        code=f"{held}:{code}{district:02d}",
                        parent_id=prefecture_unit(code),
                        area_relation="within",
                        area_id=cs.area("jp.prefecture", code),
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, f"{prefecture}第{district}区", "ja")
                    name(unit, f"{english[str(prefecture)]} {district}", "en", "english", False)
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
                for row in rows.iter_rows(named=True):
                    option = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                    member = party(row["party"], held)
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
                        person_id=person(code, row["name"], row["legal"]),
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        member_of_party_id=member,
                        is_independent=member is None,
                        is_incumbent=row["status"] == "前",
                    )
                    if row["result"] == "当":
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    values.append((result_set, unit, option, Decimal(row["votes"])))

            for block, (block_code, block_english) in blocks.items():
                key = f"{held}:pr:{block_code}"
                contest = cs.key(f"{SOURCE}.contest", key)
                allocated = lists.filter((pl.col("held") == held) & (pl.col("block") == block))
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
                cs.add(
                    "result_set",
                    id=block_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=block_set, kind="methodology", body_md=PR_METHOD)
                block_votes = {row["party"]: Decimal(row["votes"]) for row in allocated.iter_rows(named=True)}
                declared_seats = {row["party"]: row["seats"] for row in allocated.iter_rows(named=True)}
                engine = mic.dhondt(block_votes, size)
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
                        f"valid votes, so its seats passed to the next highest quotients. Declared, then D'Hondt "
                        f"alone: {moved}.",
                    )
                block_unit = cs.unit("jp.hor_block", block_code)
                if block_unit in cs.minted:
                    raise ValueError("run jp.mic first")
                for position, row in enumerate(allocated.sort("votes", descending=True).iter_rows(named=True), 1):
                    option = cs.key(f"{SOURCE}.option", f"{key}:{row['party']}")
                    member = party(row["party"], held)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=member,
                        ballot_position=position,
                        ballot_label=mic.party_label(row["party"]),
                    )
                    values.append((block_set, block_unit, option, Decimal(row["votes"])))
                    if row["seats"]:
                        cs.add("outcome", result_set_id=block_set, option_id=option, kind="won", seats=row["seats"])
                    for listed in members.filter(
                        (pl.col("held") == held) & (pl.col("block") == block) & (pl.col("party") == row["party"])
                    ).iter_rows(named=True):
                        code = codes[listed["prefecture"]] if listed["prefecture"] else f"pr:{block_code}"
                        smd = (
                            districts.filter(
                                (pl.col("held") == held)
                                & (pl.col("prefecture") == listed["prefecture"])
                                & (pl.col("district") == listed["district_number"])
                                & (pl.col("result") == listed["district"])
                            ).row(0, named=True)
                            if listed["prefecture"]
                            else None
                        )
                        who = person(code, smd["name"], smd["legal"]) if smd else person(code, listed["name"], None)
                        candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{row['party']}:{mic.plain(listed['name'])}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=who,
                            option_id=option,
                            role="list_member",
                            list_position=listed["rank"],
                            ballot_name=listed["name"],
                            member_of_party_id=member,
                            is_independent=False,
                        )
                        if listed["elected"]:
                            cs.add(
                                "outcome",
                                result_set_id=block_set,
                                option_id=option,
                                candidacy_id=candidacy,
                                kind="elected",
                            )

        for result_set, place, option, amount_ in values:
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
