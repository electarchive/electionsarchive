import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp.mic import (
    DATA_ROOT,
    ELECTIONS,
    PUBLISHER,
    TITLE,
    anchors,
    grid,
    sheets,
    suffix,
)
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_review"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ELECTORS = "都道府県別有権者数、投票者数"
DISMISSAL = "罷免を可とする投票数、可としない投票数"
BALLOTS = "都道府県別投票総数、有効投票数、無効投票数"
BOOK = "結果調全体版"
NATIVE = [native for native, _ in PREFECTURES]
NUMBER = re.compile(r"^-?[\d,]+$")
GAIJI = {"\ue042": "那", "\ue06e": "橋", "\ue050": "菅", "\ue051": "澤"}
MISMATCH = {("2014-12-14", "東京都"): 6}
JUSTICES = {
    "櫻井 龍子": ("Q3454098", "Ryuko Sakurai"),
    "竹内 行夫": ("Q6147609", "Yukio Takeuchi"),
    "涌井 紀夫": ("Q7051329", "Norio Wakui"),
    "田原 睦夫": ("Q11576280", "Mutsuo Tahara"),
    "金築 誠志": ("Q7446930", "Seishi Kanetsuki"),
    "那須 弘平": ("Q6425959", "Kohei Nasu"),
    "竹﨑 博允": ("Q2892819", "Hironobu Takesaki"),
    "近藤 崇晴": ("Q7677025", "Takaharu Kondo"),
    "宮川 光治": ("Q6426436", "Koji Miyakawa"),
    "山浦 善樹": ("Q11469997", "Yoshiki Yamaura"),
    "岡部 喜代子": ("Q11473824", "Kiyoko Okabe"),
    "須藤 正彦": ("Q11664736", "Masahiko Sudō"),
    "横田 尤孝": ("Q11543366", "Tomoyuki Yokota"),
    "大橋 正春": ("Q11436616", "Masaharu Ōhashi"),
    "千葉 勝美": ("Q11405941", "Katsumi Chiba"),
    "寺田 逸郎": ("Q11458035", "Itsurō Terada"),
    "白木 勇": ("Q11579804", "Yu Shiraki"),
    "大谷 剛彦": ("Q11439015", "Takehiko Ōtani"),
    "小貫 芳信": ("Q11463815", "Yoshinobu Onuki"),
    "鬼丸 かおる": ("Q11673400", "Kaoru Onimaru"),
    "木内 道祥": ("Q16770336", "Michiyoshi Kiuchi"),
    "池上 政幸": ("Q11551467", "Masayuki Ikegami"),
    "山本 庸幸": ("Q11469043", "Tsuneyuki Yamamoto"),
    "山﨑 敏充": ("Q17223198", "Toshimitsu Yamasaki"),
    "小池 裕": ("Q23984715", "Hiroshi Koike"),
    "戸倉 三郎": ("Q11496225", "Saburō Tokura"),
    "山口 厚": ("Q11466698", "Atsushi Yamaguchi"),
    "菅野 博之": ("Q28684567", "Hiroyuki Kanno"),
    "大谷 直人": ("Q11439136", "Naoto Ōtani"),
    "木澤 克之": ("Q27917910", "Katsuyuki Kizawa"),
    "林 景一": ("Q6383623", "Keiichi Hayashi"),
    "深山 卓也": ("Q55525092", "Takuya Miyama"),
    "岡 正晶": ("Q64784080", "Masaaki Oka"),
    "宇賀 克也": ("Q11449615", "Katsuya Uga"),
    "堺 徹": ("Q95164843", "Toru Sakai"),
    "林 道晴": ("Q27917947", "Michiharu Hayashi"),
    "岡村 和美": ("Q53764759", "Kazumi Okamura"),
    "三浦 守": ("Q50641924", "Mamoru Miura"),
    "草野 耕一": ("Q11618270", "Kōichi Kusano"),
    "渡邉 惠理子": ("Q108110183", "Eriko Watanabe"),
    "安浪 亮介": ("Q96712384", "Ryōsuke Yasunami"),
    "長嶺 安政": ("Q6147688", "Yasumasa Nagamine"),
    "尾島 明": ("Q114419537", "Akira Ojima"),
    "宮川 美津子": ("Q108481554", "Mitsuko Miyagawa"),
    "今崎 幸彦": ("Q27921045", "Yukihiko Imasaki"),
    "平木 正洋": ("Q11483040", "Masahiro Hiraki"),
    "石兼 公博": ("Q16769574", "Kimihiro Ishikane"),
    "中村 愼": ("Q111112240", "Makoto Nakamura"),
    "高須 順一": ("Q123282304", "Jun'ichi Takasu"),
    "沖野 眞已": ("Q109597341", "Masami Okino"),
}
RULES = {"formula": "referendum", "quorum": 0.01}
SYSTEM = (
    "Each justice is reviewed at the first House of Representatives general election after appointment, and again at "
    "the first after ten more years. The voter marks a cross over the name of each justice who should be dismissed "
    "and leaves the others blank. A justice is dismissed when the votes for dismissal outnumber those against, unless "
    "fewer than one in a hundred registered voters took part (Act on the National Review of Supreme Court Justices, "
    "art. 32). No justice has ever been dismissed."
)
METHOD = (
    "Votes for and against dismissal by prefecture, from the ministry's 結果調 table (罷免を可とする投票数、"
    "可としない投票数), with registered voters, voters, and ballots cast from its other review tables. A blank name "
    "counts against dismissal. Yes is the vote for dismissal. Valid votes are the two answers together; invalid "
    "votes are the invalid ballots plus the ballots whose mark for this justice was void (記載無効), which the "
    "ministry counts within its valid ballots. Each justice's national totals were checked against the 結果調 book."
)


def links(number: int) -> dict[str, str]:
    found = anchors(f"{DATA_ROOT}/shugiin{number}/index.html")
    start = next(i for i, (_, label) in enumerate(found) if label == "市区町村別得票数")
    wanted: dict[str, str] = {}
    for url, label in found[start + 1 :]:
        for key in (ELECTORS, DISMISSAL, BALLOTS, BOOK):
            if key in label and key not in wanted:
                wanted[key] = url
    return wanted


def fetch() -> None:
    for number in ELECTIONS:
        for url in links(number).values():
            raw.fetch(SOURCE, url, suffix=suffix(url))


def prefecture(text: str) -> str | None:
    text = re.sub(r"^\d+|\s", "", unicodedata.normalize("NFKC", text))
    return text if text in NATIVE else None


def person(text: str) -> str:
    text = "".join(GAIJI.get(char, char) for char in text)
    parts = [re.sub(r"\s", "", part) for part in re.split(r"\s{2,}", text.strip())]
    return " ".join(part for part in parts if part)


def count(text: str) -> int:
    return int(text.replace(",", ""))


def pdf_rows(path: Path) -> list[list[str]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    rows: list[list[str]] = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        if not line.strip():
            continue
        found = re.match(r"^\s*(\d*\s*\D+?)\s+((?:[\d,]+\s+){3}(?:(?:[\d,]+\s+){4})?[\d,]+)\s*$", line)
        if found:
            rows.append([found.group(1), *found.group(2).split()])
        else:
            rows.append([cell for cell in re.split(r"\s{20,}", line.strip())])
    return rows


def xls_rows(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for sheet in sheets(path):
        for row in grid(path, sheet):
            cells = [cell for cell in row if cell != ""]
            if cells and cells[0].isdigit() and len(cells) > 1 and prefecture(cells[1]):
                cells = cells[1:]
            rows.append(cells)
    return rows


def dismissal(path: Path) -> list[dict[str, object]]:
    rows = pdf_rows(path) if path.suffix == ".pdf" else xls_rows(path)
    found: list[dict[str, object]] = []
    names: list[str] = []
    for index, cells in enumerate(rows):
        if cells and "罷免を可とす" in "".join(cells) and not cells[0].startswith("("):
            previous = next(row for row in reversed(rows[:index]) if row and not row[0].startswith("("))
            names = [person(cell) for cell in previous if cell and not re.search(r"罷免|（|\(", cell)]
            continue
        if not cells or (place := prefecture(cells[0])) is None:
            continue
        numbers = [count(cell) for cell in cells[1:] if NUMBER.match(cell)]
        if len(numbers) not in (4, 8) or len(numbers) // 4 > len(names):
            raise ValueError(f"{path.name}: {cells}")
        for slot in range(len(numbers) // 4):
            dismiss, keep, void, total = numbers[slot * 4 : slot * 4 + 4]
            if dismiss + keep + void != total:
                raise ValueError(f"{path.name}: {names[slot]} {place} does not add up")
            found.append({"justice": names[slot], "prefecture": place, "dismiss": dismiss, "keep": keep, "void": void})
    return found


def by_prefecture(path: Path, columns: dict[str, int]) -> dict[str, dict[str, int]]:
    rows = xls_rows(path)
    found: dict[str, dict[str, int]] = {}
    for cells in rows:
        if not cells or (place := prefecture(cells[0])) is None or place in found:
            continue
        numbers = [cell for cell in cells[1:] if NUMBER.match(cell) or re.match(r"^[\d.]+$", cell)]
        found[place] = {key: count(numbers[column]) for key, column in columns.items()}
    if len(found) != len(NATIVE):
        raise ValueError(f"{path.name}: {len(found)} prefectures")
    return found


def book_totals(path: Path) -> str:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", text))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for number, held in ELECTIONS.items():
        files = links(number)
        votes = dismissal(raw.cached(SOURCE, files[DISMISSAL]).path)
        electors = by_prefecture(raw.cached(SOURCE, files[ELECTORS]).path, {"registered": 2, "voters": 5})
        ballots = by_prefecture(raw.cached(SOURCE, files[BALLOTS]).path, {"cast": 0, "valid": 1, "invalid": 2})
        book = book_totals(raw.cached(SOURCE, files[BOOK]).path)
        justices = list(dict.fromkeys(str(row["justice"]) for row in votes))
        for justice in justices:
            own = [row for row in votes if row["justice"] == justice]
            if sorted(str(row["prefecture"]) for row in own) != sorted(NATIVE):
                raise ValueError(f"{held} {justice}: {len(own)} prefectures")
            dismiss = sum(int(str(row["dismiss"])) for row in own)
            keep = sum(int(str(row["keep"])) for row in own)
            if f"{dismiss}{keep}" not in book:
                raise ValueError(f"{held} {justice}: {dismiss} for dismissal is not in the book")
            for row in own:
                place = str(row["prefecture"])
                gap = int(str(row["dismiss"])) + int(str(row["keep"])) + int(str(row["void"])) - ballots[place]["valid"]
                if gap != MISMATCH.get((held, place), 0):
                    raise ValueError(f"{held} {justice} {place}: the answers are {gap} off the valid ballots")
                rows.append({"held": held, **row, **electors[place], **ballots[place]})
            print(held, justice, dismiss, keep, f"{dismiss / (dismiss + keep):.2%}")
    pl.DataFrame(rows).write_parquet(PARSED / f"review-v{VERSION}.parquet")


def emit() -> Path:
    review = pl.read_parquet(PARSED / f"review-v{VERSION}.parquet")
    codes = {native: f"{number:02d}" for number, native in enumerate(NATIVE, 1)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's national reviews of Supreme Court justices 2009-2026 by prefecture, from the Ministry of "
            "Internal Affairs and Communications' 結果調 tables",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        nation = cs.area("jp.nation", "JP")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("run jp.mic first")
        office = cs.key(f"{SOURCE}.office", "supreme-court-justice")
        cs.add("office", id=office, polity_id=japan, kind="other")
        name(office, "Justice of the Supreme Court of Japan")
        name(office, "最高裁判所裁判官", "ja", "native", False)
        system = cs.key(f"{SOURCE}.electoral_system", "review")
        cs.add("electoral_system", id=system, polity_id=japan, rules=RULES, rules_version=1)
        name(system, "National review of Supreme Court justices, dismissal by a majority of valid votes")
        name(system, "最高裁判所裁判官国民審査", "ja", "native", False)
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        people: set[UUID] = set()
        values: list[tuple[str, str, str | None, int, int]] = []
        for number, held in ELECTIONS.items():
            election = cs.key("jp.mic.election", held)
            if election in cs.minted:
                raise ValueError(f"run jp.mic first: no election on {held}")
            for url in links(number).values():
                cs.artifact(raw.cached(SOURCE, url), source)
            year = date.fromisoformat(held).year
            rows = review.filter(pl.col("held") == held)
            for justice in dict.fromkeys(rows["justice"].to_list()):
                item, english = JUSTICES[justice]
                key = f"{held}:{item}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=nation,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Supreme Court review: {english}")
                name(contest, f"最高裁判所裁判官国民審査 {justice}", "ja", "native", False)
                cs.add("slug", path=f"jp/{held}-general/supreme-court-review-{slug(english)}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source)
                result = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
                person = cs.key("wikidata", item)
                if person not in people:
                    people.add(person)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, justice, "ja")
                        name(person, english, "en", "transliteration", False)
                options = {}
                for order, answer in enumerate(("yes", "no"), 1):
                    options[answer] = cs.key(f"{SOURCE}.option", f"{key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=order,
                        ballot_label=answer.capitalize(),
                    )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", key),
                    person_id=person,
                    option_id=options["no"],
                    role="head",
                    ballot_name=justice,
                    is_independent=False,
                )
                dismiss = int(rows.filter(pl.col("justice") == justice)["dismiss"].sum())
                keep = int(rows.filter(pl.col("justice") == justice)["keep"].sum())
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options["no"],
                    kind="lost" if dismiss > keep else "elected",
                    seats=None if dismiss > keep else 1,
                )
                for row in rows.filter(pl.col("justice") == justice).iter_rows(named=True):
                    place = cs.unit("jp.prefecture", codes[row["prefecture"]])
                    for option, measure, value in (
                        (options["yes"], 1, row["dismiss"]),
                        (options["no"], 1, row["keep"]),
                        (None, 2, row["registered"]),
                        (None, 14, row["voters"]),
                        (None, 3, row["cast"]),
                        (None, 4, row["dismiss"] + row["keep"]),
                    ):
                        values.append(
                            (str(result), str(place), None if option is None else str(option), measure, value)
                        )
                    if (held, row["prefecture"]) in MISMATCH:
                        cs.add(
                            "note",
                            entity_id=result,
                            kind="caveat",
                            body_md=f"In {row['prefecture']} the ministry's review tables disagree by "
                            f"{MISMATCH[held, row['prefecture']]} ballots: each justice's answers and void marks add "
                            "up to more than the valid ballots in the ballots table, so its invalid count is left out.",
                        )
                    else:
                        values.append((str(result), str(place), None, 5, row["invalid"] + row["void"]))
        cs.frame(
            "result_value",
            pl.DataFrame(
                [(result, unit, option, 0, measure, 0, 0, value) for result, unit, option, measure, value in values],
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        return cs.write()
