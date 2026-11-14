import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.jp import mic_chiji
from ea.store import DATA, connect

SOURCE = "jp.pref_chiji"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"
TOCHIGI = "https://www.pref.tochigi.lg.jp/senkyo"
IBARAKI = "https://www.pref.ibaraki.jp/somu/shichoson/senkyo/senkan"
WAKAYAMA = "https://www.pref.wakayama.lg.jp/prefg/010600/wsenkan"
TOKYO = "https://www.senkyo.metro.tokyo.lg.jp/documents/d/senkyo"
SAITAMA = "https://www.pref.saitama.lg.jp/documents"
CHIBA = "https://www.pref.chiba.lg.jp/senkan/kakokekka/documents"
MIYAGI = "https://www.pref.miyagi.jp/documents"
AOMORI = "https://www.pref.aomori.lg.jp/soshiki/senkan/files2"
IWATE = "https://www.pref.iwate.jp/_res/projects/default_project/_page_/001"
AKITA = "https://www.pref.akita.lg.jp/uploads/public"
YAMAGATA = "https://www.pref.yamagata.jp/documents"
FUKUSHIMA = "https://www.pref.fukushima.lg.jp/uploaded/attachment"


@dataclass(frozen=True)
class Commission:
    publisher: str
    title: str
    url: str
    license: str
    license_url: str | None
    attribution: str


COMMISSIONS = {
    "茨城県": Commission(
        "茨城県選挙管理委員会",
        "茨城県知事選挙 開票速報（確定）・投票結果速報（確定）",
        "https://www.pref.ibaraki.jp/somu/shichoson/senkyo/senkan/index2.html",
        "No license stated: facts published with attribution, raw files not redistributed",
        None,
        "出典：茨城県選挙管理委員会",
    ),
    "栃木県": Commission(
        "栃木県選挙管理委員会",
        "栃木県知事選挙 候補者別市町村別得票数調・投票に関する調",
        "https://www.pref.tochigi.lg.jp/senkyo/",
        "Copyright Tochigi Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.tochigi.lg.jp/kensei/kouhou/hp/chosakuken/index.html",
        "出典：栃木県選挙管理委員会",
    ),
    "東京都": Commission(
        "東京都選挙管理委員会",
        "東京都知事選挙 候補者別得票数（全候補）・開票結果内訳・投票結果",
        "https://www.senkyo.metro.tokyo.lg.jp/election/tochiji-all",
        "Copyright Tokyo Metropolitan Government (site policy); counts published as facts with attribution, raw files "
        "not redistributed",
        "https://www.senkyo.metro.tokyo.lg.jp/policy",
        "出典：東京都選挙管理委員会",
    ),
    "埼玉県": Commission(
        "埼玉県選挙管理委員会",
        "埼玉県知事選挙 候補者別市区町村別得票調べ・市区町村別開票結果調べ・市町村別投票状況",
        "https://www.pref.saitama.lg.jp/e1601/",
        "Copyright Saitama Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.saitama.lg.jp/a0301/tyosaku-rinku.html",
        "出典：埼玉県選挙管理委員会",
    ),
    "千葉県": Commission(
        "千葉県選挙管理委員会",
        "千葉県知事選挙 開票調・投票調",
        "https://www.pref.chiba.lg.jp/senkan/kakokekka-kenchiji.html",
        "Copyright Chiba Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.chiba.lg.jp/homepage/about-site/link.html",
        "出典：千葉県選挙管理委員会",
    ),
    "宮城県": Commission(
        "宮城県選挙管理委員会",
        "宮城県知事選挙 開票結果（候補者別得票数一覧）・投票結果",
        "https://www.pref.miyagi.jp/site/miyagi-senkyo-kekka/senkyo_chiji.html",
        "Copyright Miyagi Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.miyagi.jp/soshiki/kohou/site-riyou.html",
        "出典：宮城県選挙管理委員会",
    ),
    "青森県": Commission(
        "青森県選挙管理委員会",
        "青森県知事選挙 候補者別得票数（市町村別）・開票調・投票状況（2019 は選挙の記録の開票に関する調）",
        "https://www.pref.aomori.lg.jp/soshiki/senkan/",
        "Copyright Aomori Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.aomori.lg.jp/contents/copyright.html",
        "出典：青森県選挙管理委員会",
    ),
    "岩手県": Commission(
        "岩手県選挙管理委員会",
        "岩手県知事選挙 開票結果・投票結果",
        "https://www.pref.iwate.jp/iinkai/senkyo/kekka/1074328.html",
        "Copyright Iwate Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.iwate.jp/about/link.html",
        "出典：岩手県選挙管理委員会",
    ),
    "秋田県": Commission(
        "秋田県選挙管理委員会",
        "秋田県知事選挙 開票結果（集計表4-1）・投票結果（集計表3-1）",
        "https://www.pref.akita.lg.jp/pages/archive/87917",
        "Copyright Akita Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.akita.lg.jp/pages/about-copyright",
        "出典：秋田県選挙管理委員会",
    ),
    "山形県": Commission(
        "山形県選挙管理委員会",
        "山形県知事選挙 開票結果・投票結果",
        "https://www.pref.yamagata.jp/910001/kensei/senkyo/kakushu_kekka/chijigiin/r7chijisenkengihosen.html",
        "Copyright Yamagata Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.yamagata.jp/kensei/shoukai/aboutthissite/mensekijikou.html",
        "出典：山形県選挙管理委員会",
    ),
    "福島県": Commission(
        "福島県選挙管理委員会",
        "福島県知事選挙 開票結果・確定投票率に関する調べ",
        "https://www.pref.fukushima.lg.jp/sec/62010a/23-chiji.html",
        "Copyright Fukushima Prefecture (site terms); counts published as facts with attribution, raw files not "
        "redistributed",
        "https://www.pref.fukushima.lg.jp/sec/01010d/koho-chosakuken.html",
        "出典：福島県選挙管理委員会",
    ),
    "和歌山県": Commission(
        "和歌山県選挙管理委員会",
        "和歌山県知事選挙 開票結果集計表・投票結果集計表",
        f"{WAKAYAMA}/deta/kekka/tiji/",
        "和歌山県ホームページ公開情報利用規約 (government standard terms, compatible with CC BY 4.0; numeric data "
        "outside copyright)",
        "https://www.pref.wakayama.lg.jp/prefg/000200/opendata/hpopendatakiyaku.html",
        "出典：和歌山県ホームページ",
    ),
}
RECORDS: dict[str, tuple[str, ...]] = {
    "2025-04-06:秋田県": (
        f"{AKITA}/archive_0000087917_00/2400kaihyou.xlsx",
        f"{AKITA}/archive_0000087917_00/touhyou1/touhyou1.xlsx",
    ),
    "2025-01-26:山形県": (f"{YAMAGATA}/44535/r7kaihyo-tiji.xls", f"{YAMAGATA}/44535/r7tohyo-tiji.xls"),
    "2023-09-03:岩手県": (f"{IWATE}/074/328/0903tizisennkaihyou.xlsx", f"{IWATE}/074/328/0906ctouhyoukekka.xlsx"),
    "2023-06-04:青森県": (f"{AOMORI}/kaihyoc_chi.pdf", f"{AOMORI}/tohyok_chi.pdf", f"{AOMORI}/kaihyok_chi.pdf"),
    "2022-10-30:福島県": (f"{FUKUSHIMA}/570567.xlsx", f"{FUKUSHIMA}/570559.xlsx"),
    "2021-04-04:秋田県": (
        f"{AKITA}/archive_0000056077_00/１kaihyou0000.xlsx",
        f"{AKITA}/archive_0000056077_00/１touhyou.xlsx",
    ),
    "2021-01-24:山形県": (f"{YAMAGATA}/19514/r3kaihyou-tiji.xls", f"{YAMAGATA}/19514/r3touhyou-tiji.xls"),
    "2019-09-08:岩手県": (f"{IWATE}/027/242/tkaihyokekka.xlsx", f"{IWATE}/027/242/ttohyokekka.xlsx"),
    "2019-06-02:青森県": (
        f"{AOMORI}/5_kohoshabetutokuhyou-sichouson.pdf",
        f"{AOMORI}/6_sichousonbetu-touhyoujoukyou.pdf",
        f"{AOMORI}/R01tijisennokiroku.pdf",
    ),
    "2018-10-28:福島県": (f"{FUKUSHIMA}/297315.xls", f"{FUKUSHIMA}/297313.xls"),
    "2017-04-09:秋田県": (
        f"{AKITA}/archive_0000067177_00/H29知事/4-1-kaihyoukekka.xls",
        f"{AKITA}/archive_0000067177_00/H29知事/3-1-touhyoukekka.xls",
    ),
    "2014-10-26:福島県": (f"{FUKUSHIMA}/89980.xls", f"{FUKUSHIMA}/89981.xls"),
    "2010-10-31:福島県": (f"{FUKUSHIMA}/47699.xls", f"{FUKUSHIMA}/47698.xls"),
    "2025-10-26:宮城県": (f"{MIYAGI}/62530/kaihyokekka_chiji.xlsx", f"{MIYAGI}/62530/tohyokekka_chiji.xlsx"),
    "2025-03-16:千葉県": (f"{CHIBA}/r70316-kaihyoukekka.xlsx", f"{CHIBA}/r70316-touhyoukekka.xlsx"),
    "2023-08-06:埼玉県": (f"{SAITAMA}/240322/ds_kaihyo_k.xls", f"{SAITAMA}/240322/r5_ds_tohyo_k.xls"),
    "2021-10-31:宮城県": (f"{MIYAGI}/35848/3kaihyok_chi.xlsx", f"{MIYAGI}/35848/1tohyok_chi.xlsx"),
    "2021-03-21:千葉県": (f"{CHIBA}/r30321-kaihyoukekka.xlsx", f"{CHIBA}/r30321-touhyoukekka.xlsx"),
    "2019-08-25:埼玉県": (f"{SAITAMA}/153457/tds_kaihyo_k.xls", f"{SAITAMA}/153457/teiseids_tohyo_k.xls"),
    "2017-10-22:宮城県": (f"{MIYAGI}/4379/653288.xlsx", f"{MIYAGI}/4379/653286.xlsx"),
    "2017-03-26:千葉県": (f"{CHIBA}/ck.xlsx", f"{CHIBA}/ct2000.xlsx"),
    "2015-08-09:埼玉県": (f"{SAITAMA}/54384/ds_kaihyo_k.xls", f"{SAITAMA}/54384/ds_tohyo_k.xls"),
    "2013-10-27:宮城県": (f"{MIYAGI}/4376/231478.pdf", f"{MIYAGI}/4376/231475.pdf"),
    "2013-03-17:千葉県": (f"{CHIBA}/ck2315.xls", f"{CHIBA}/ct2000.xls"),
    "2024-07-07:東京都": (
        f"{TOKYO}/r06tochiji_kaihyo_kouhoshatokuhyo_2",
        f"{TOKYO}/r06tochiji_touhyo_2",
        f"{TOKYO}/r06tochiji_kaihyo_uchiwake_2",
    ),
    "2020-07-05:東京都": (
        f"{TOKYO}/r02tochiji_kaihyo_kouhoshatokuhyo_2",
        f"{TOKYO}/r02tochiji_touhyo_2",
        f"{TOKYO}/r02tochiji_kaihyo_uchiwake_2",
    ),
    "2016-07-31:東京都": (
        f"{TOKYO}/h28tochiji_kaihyo_kouhoshatokuhyo_2",
        f"{TOKYO}/h28tochiji_touhyo_2",
        f"{TOKYO}/h28tochiji_kaihyo_uchiwake_2",
    ),
    "2014-02-09:東京都": (
        f"{TOKYO}/tochiji-result2014-detail_2",
        f"{TOKYO}/tochiji-turnout2014_2",
        f"{TOKYO}/tochiji-result2014-utiwake_2",
    ),
    "2025-09-07:茨城県": (
        f"{IBARAKI}/2025tizi/documents/d_kaihyo_k_50_99.xlsx",
        f"{IBARAKI}/2025tizi/documents/d_tohyo_k_50_1.xlsx",
    ),
    "2021-09-05:茨城県": (
        f"{IBARAKI}/2021chijisen/documents/d_kaihyo_k_50_05.xlsx",
        f"{IBARAKI}/2021chijisen/documents/d_tohyo_k_50_1.xlsx",
    ),
    "2017-08-27:茨城県": (
        f"{IBARAKI}/2017kenchiji/documents/d_kaihyo_k_50_05_teisei.xls",
        f"{IBARAKI}/2017kenchiji/documents/d_tohyo_k_50_1.xls",
    ),
    "2013-09-08:茨城県": (
        f"{IBARAKI}/2013chijisen/documents/d_kaihyo_k_50_02.xls",
        f"{IBARAKI}/2013chijisen/documents/d_tohyo_k_50_1.xls",
    ),
    "2024-11-17:栃木県": (
        f"{TOCHIGI}/r06chiji/file/DS_KAIHYO_K_50.xls",
        f"{TOCHIGI}/r06chiji/file/DS_TOHYO_K_50.xls",
    ),
    "2020-11-15:栃木県": (
        WAYBACK.format(stamp="20230308052226", url=f"{TOCHIGI}/r02chiji/file/DS_KAIHYO_K_50.xls"),
        WAYBACK.format(stamp="20230307040606", url=f"{TOCHIGI}/r02chiji/file/DS_TOHYO_K_50.xls"),
    ),
    "2016-11-20:栃木県": (
        WAYBACK.format(stamp="20260202233145", url=f"{TOCHIGI}/28chiji/file/DS_KAIHYO_K_50.xls"),
        WAYBACK.format(stamp="20260203001933", url=f"{TOCHIGI}/28chiji/file/DS_TOHYO_K_50.xls"),
    ),
    "2012-11-18:栃木県": (
        WAYBACK.format(stamp="20260203013350", url=f"{TOCHIGI}/24chiji/file/DS_KAIHYO_K_50_04.xls"),
        WAYBACK.format(stamp="20260203000418", url=f"{TOCHIGI}/24chiji/file/DS_TOHYO_K_50.xls"),
    ),
    "2025-06-01:和歌山県": (
        f"{WAKAYAMA}/R7_tiji/d00220093_d/fil/tiji_kaiketsu.pdf",
        f"{WAKAYAMA}/R7_tiji/d00220093_d/fil/tiji_touketsu.pdf",
    ),
    "2022-11-27:和歌山県": (
        f"{WAKAYAMA}/deta/kekka/tiji/R4_d/fil/kaiketsu.pdf",
        f"{WAKAYAMA}/deta/kekka/tiji/R4_d/fil/touketsu.pdf",
    ),
    "2018-11-25:和歌山県": (
        f"{WAKAYAMA}/deta/kekka/tiji/H30_d/fil/H30-chiji-kaihyou.pdf",
        f"{WAKAYAMA}/deta/kekka/tiji/H30_d/fil/H30-chiji-touhou.pdf",
    ),
    "2014-11-30:和歌山県": (
        f"{WAKAYAMA}/deta/kekka/tiji/26tiji/26index_d/fil/kaihyoukekka.pdf",
        f"{WAKAYAMA}/deta/kekka/tiji/26tiji/26index_d/fil/touhyoukekka_.pdf",
    ),
    "2010-11-28:和歌山県": (
        f"{WAKAYAMA}/deta/kekka/tiji/22tiji/22index_d/fil/22tikai.pdf",
        f"{WAKAYAMA}/deta/kekka/tiji/22tiji/22index_d/fil/22titou.pdf",
    ),
}
FIELDS = ("valid", "invalid", "cast", "unreturned", "voters")
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}
PLACE = re.compile(r"^\S+?[市町村]$")
WARD = re.compile(r"^\S+?[区市町村]$")
XLSX = {f"{TOKYO}/r06tochiji_{name}_2" for name in ("kaihyo_kouhoshatokuhyo", "kaihyo_uchiwake", "touhyo")} | {
    f"{TOKYO}/r02tochiji_touhyo_2"
}
TOTAL = re.compile(r"^県\s*計$")
VARIANTS = str.maketrans({"ヶ": "ケ", "鯵": "鰺", "﨑": "崎", "髙": "高", "桧": "檜"})
FIGURE = re.compile(r"^-?[\d,]+(\.\d+)?$")


def suffix(url: str) -> str:
    return Path(url).suffix or (".xlsx" if url in XLSX else ".xls")


def fetch() -> None:
    for urls in RECORDS.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=suffix(url))


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", "").strip())


def cells(path: Path) -> list[list[str]]:
    reader = fastexcel.read_excel(str(path))
    rows = []
    for sheet in reader.sheet_names:
        frame = reader.load_sheet(sheet, header_row=None, dtypes="string").to_polars()
        for row in frame.iter_rows():
            values = [unicodedata.normalize("NFKC", str(v)).strip() for v in row if v is not None]
            rows.append([v for v in values if v])
    return rows


def counts(values: list[str], n: int) -> dict[str, Any]:
    total, cut, other, valid, invalid, cast, unreturned, voters = values[2 * n : 2 * n + 8]
    return {
        "votes": [str(number(v)) for v in values[0 : 2 * n : 2]],
        "total": str(number(total)),
        "cut": str(number(cut)),
        "other": str(number(other)),
        **dict(zip(FIELDS, (str(number(v)) for v in (valid, invalid, cast, unreturned, voters)), strict=True)),
    }


def excel_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = cells(path)
    names = next(row[1:] for row in rows if row and row[0] == "候補者氏名")
    n = len(names)
    places, total = {}, None
    for row in rows:
        if len(row) > 2 and PLACE.match(row[0]) and row[1] == "*":
            places[row[0]] = {"place": row[0], **counts(row[2:], n)}
        elif row and TOTAL.match(row[0]):
            total = counts(row[1:], n)
    if total is None:
        raise ValueError(f"{path} has no prefecture total")
    return names, list(places.values()), total


def excel_registered(path: Path) -> dict[str, tuple[str, str]]:
    return {
        row[0]: (str(number(row[4])), str(number(row[7])))
        for row in cells(path)
        if len(row) > 8 and PLACE.match(row[0]) and row[1] == "*"
    }


def ibaraki_spread(values: list[str], n: int) -> list[str]:
    if len(values) == n + 9:
        values = [*values[: n + 1], "0", *values[n + 1 :]]
    cast = number(values[n + 5])
    unreturned = sum(number(v) for v in values[n + 6 : n + 9])
    return [x for v in values[:n] for x in (v, "")] + values[n : n + 6] + [str(unreturned), str(cast + unreturned)]


def ibaraki_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = cells(path)
    header = next(row for row in rows if row[:2] == ["市町村名", "確定"])
    n = header.index("得票総数") - 2
    places, total = [], None
    for row in rows:
        if len(row) > n + 9 and PLACE.match(row[0]) and row[1] == "*":
            places.append({"place": row[0], **counts(ibaraki_spread(row[2:], n), n)})
        elif row and row[0] == "県計":
            total = counts(ibaraki_spread(row[1:], n), n)
    if total is None:
        raise ValueError(f"{path} has no prefecture total")
    return header[2 : 2 + n], places, total


def ibaraki_registered(path: Path) -> dict[str, tuple[str, str]]:
    return {
        row[0]: (str(number(row[3])), str(number(row[6])))
        for row in cells(path)
        if len(row) >= 7 and PLACE.match(row[0]) and re.match(r"^[\d,]+$", row[1])
    }


def grid(path: Path) -> list[list[str]]:
    reader = fastexcel.read_excel(str(path))
    rows = []
    for sheet in reader.sheet_names:
        frame = reader.load_sheet(sheet, header_row=None, dtypes="string").to_polars()
        rows.extend([unicodedata.normalize("NFKC", v or "").strip() for v in row] for row in frame.iter_rows())
    return rows


def tokyo_votes(path: Path, breakdown: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = grid(path)
    names: dict[int, str] = {}
    votes: dict[str, dict[int, str]] = {}
    columns: dict[int, int] = {}
    for i, row in enumerate(rows):
        numbered = {c: int(v) for c, v in enumerate(row) if v.isdigit()}
        if numbered and sorted(numbered.values()) == list(range(len(names) + 1, len(names) + len(numbered) + 1)):
            columns = numbered
            for c, n in columns.items():
                names[n] = rows[i + 1][c] + rows[i + 2][c]
                if not names[n]:
                    raise ValueError(f"{path}: no name for candidate {n}")
        elif columns and WARD.match(row[0]):
            values = {n: row[c] for c, n in columns.items()}
            if not all(values.values()):
                raise ValueError(f"{path}: {row[0]} lacks votes for candidates {sorted(columns.values())}")
            votes.setdefault(row[0], {}).update(values)
    n = len(names)
    places, total = [], None
    for row in cells(breakdown):
        if row and (WARD.match(row[0]) or row[0] == "★都計"):
            got, cut, other, valid, invalid, cast, kept, refused, voters = row[1:10]
            unreturned = str(number(kept) + number(refused))
            spread = ["0", ""] * n + [got, cut, other, valid, invalid, cast, unreturned, voters]
            if row[0] == "★都計":
                total = counts(spread, n)
                continue
            if sorted(votes[row[0]]) != list(range(1, n + 1)):
                raise ValueError(f"{path}: {row[0]} lacks candidates")
            spread[0 : 2 * n : 2] = [votes[row[0]][k] for k in range(1, n + 1)]
            places.append({"place": row[0], **counts(spread, n)})
    if total is None or {p["place"] for p in places} != set(votes):
        raise ValueError(f"{breakdown} does not cover the candidate table")
    total["votes"] = [str(sum(Decimal(p["votes"][k]) for p in places)) for k in range(n)]
    return [names[k] for k in range(1, n + 1)], places, total


def tokyo_registered(path: Path) -> dict[str, tuple[str, str]]:
    return {row[0]: (str(number(row[3])), str(number(row[6]))) for row in cells(path) if row and WARD.match(row[0])}


def squash(text: str) -> str:
    return re.sub(r"[\s()]", "", text)


def unit(place: str) -> bool:
    return bool(WARD.match(place)) and "計" not in place


def total_of(*values: str) -> str:
    return str(sum((number(v or "0") for v in values), Decimal(0)))


def spread_of(votes: list[str], tail: list[str]) -> list[str]:
    return [x for v in votes for x in (v, "")] + tail


def lead(row: list[str]) -> int | None:
    return next((i for i, v in enumerate(row[:3]) if unit(squash(v)) or squash(v) == "県計"), None)


def registered(path: Path) -> dict[str, tuple[str, str]]:
    out = {}
    for row in cells(path):
        i = lead(row)
        if i is not None and len(row) > i + 6 and unit(squash(row[i])) and FIGURE.match(row[i + 1]):
            out.setdefault(squash(row[i]), (str(number(row[i + 3])), str(number(row[i + 6]))))
    return out


def voters_after(values: list[Decimal], c: int, start: int | None = None) -> int:
    start = c + 1 if start is None else start
    for j in range(start, len(values)):
        if values[j] == values[c] + sum(values[start:j]) or (j > start and values[j] == values[c] + values[j - 1]):
            return j
    raise ValueError("no voters column")


def columns(values: list[Decimal]) -> tuple[int, list[str]] | None:
    v = values
    for n in range(1, len(v) - 5):
        if sum(v[:n]) == v[n] and v[n] + v[n + 1] + v[n + 2] == v[n + 3] and v[n + 3] + v[n + 4] == v[n + 5]:
            j = voters_after(v, n + 5)
            return n, [*map(str, v[n : n + 6]), str(v[j] - v[n + 5]), str(v[j])]
        if sum(v[:n]) + v[n] == v[n + 1] and v[n + 1] + v[n + 2] == v[n + 3]:
            j = voters_after(v, n + 3)
            tail = [sum(v[:n]), v[n], Decimal(0), *v[n + 1 : n + 4], v[j] - v[n + 3], v[j]]
            return n, [str(x) for x in tail]
    if len(v) > 6 and v[0] == v[1] + v[2]:
        for n in range(1, len(v) - 5):
            if sum(v[4 : 4 + n]) == v[4 + n] == v[1]:
                j = voters_after(v, 0, 5 + n)
                return n, [str(x) for x in (v[1], Decimal(0), Decimal(0), v[1], v[2], v[0], v[j] - v[0], v[j])]
    return None


def auto_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    places, total = [], None
    for row in cells(path):
        i = lead(row)
        if i is None:
            continue
        if squash(row[i]) in {p["place"] for p in places}:
            break
        figures = []
        for value in row[i + 1 :]:
            if not FIGURE.match(value):
                break
            figures.append(number(value))
        found = columns(figures)
        if found is None:
            if unit(squash(row[i])) and len(figures) > 4:
                raise ValueError(f"{path}: unread row {row}")
            continue
        n, tail = found
        start = 4 if figures[0] == figures[1] + figures[2] and tail[5] == str(figures[0]) else 0
        entry = counts(spread_of([str(x) for x in figures[start : start + n]], tail), n)
        if squash(row[i]) == "県計":
            total = entry
        else:
            places.append({"place": squash(row[i]), **entry})
    if not places:
        raise ValueError(f"{path} has no rows")
    if total is None:
        n = len(places[0]["votes"])
        total = {
            "votes": [str(sum(Decimal(p["votes"][k]) for p in places)) for k in range(n)],
            **{f: str(sum(Decimal(p[f]) for p in places)) for f in ("total", "cut", "other", *FIELDS)},
        }
    return [""] * len(places[0]["votes"]), places, total


def saitama_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = [row for row in cells(path) if row]
    names = next(row[1:] for row in rows if row[0] == "候補者氏名")
    n = len(names)
    votes = {squash(row[1]): row[2 : 2 + n] for row in rows if row[0] == "確定" and len(row) > n + 1}
    places, total = [], None
    for row in rows:
        if len(row) >= 11 and row[0] != "確定" and FIGURE.match(row[1]):
            place = squash(row[0])
            got, cut, valid, invalid, _, cast, refused, kept, other, voters = row[1:11]
            entry = counts(
                spread_of(votes[place], [got, cut, "0", valid, invalid, cast, total_of(refused, kept, other), voters]),
                n,
            )
            if place == "県計":
                total = entry
            elif unit(place):
                places.append({"place": place, **entry})
    if total is None:
        raise ValueError(f"{path} has no prefecture total")
    return names, places, total


def chiba_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = grid(path)
    at = next(i for i, row in enumerate(rows) if row[1] == "姓")
    surnames, given = rows[at], rows[at + 1]
    c = surnames.index("計")
    n = next(i for i in range(2, c) if surnames[i] in ("0", "")) - 2
    names = [surnames[i] + given[i] for i in range(2, 2 + n)]
    places, total = [], None
    for row in rows:
        place = squash(row[0])
        if not (place == "県計" or (unit(place) and row[1] != "*")):
            continue
        if any(v not in ("0", "") for v in row[2 + n : c]):
            raise ValueError(f"{path}: {place} has votes beyond candidate {n}")
        got, cut, other, invalid, cast, kept = row[c], row[c + 2], row[c + 3], row[c + 4], row[c + 5], row[c + 6]
        entry = counts(
            spread_of(
                row[2 : 2 + n],
                [
                    got,
                    cut or "0",
                    other or "0",
                    total_of(got, cut, other),
                    invalid,
                    cast,
                    kept or "0",
                    total_of(cast, kept),
                ],
            ),
            n,
        )
        if place == "県計":
            total = entry
        else:
            places.append({"place": place, **entry})
    if total is None:
        raise ValueError(f"{path} has no prefecture total")
    return names, places, total


def miyagi_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    if path.suffix == ".pdf":
        return miyagi_pdf_votes(path)
    rows = grid(path)
    head = next(row for row in rows if row[1].endswith(("\\候補者", "\\候補者氏名")))
    a = head.index("得票総数")
    names = head[2:a]
    n = len(names)
    column = {
        label: next(i for i, h in enumerate(head) if h.startswith(label))
        for label in ("有効投票数", "無効投票数", "投票総数", "投票者総数")
    }
    places, total = [], None
    for row in rows:
        place = squash(row[1])
        if not (place == "県計" or unit(place)) or not FIGURE.match(row[2]):
            continue
        valid, invalid, cast, voters = (
            row[column[label]] for label in ("有効投票数", "無効投票数", "投票総数", "投票者総数")
        )
        unreturned = str(number(voters) - number(cast))
        entry = counts(
            spread_of(row[2 : 2 + n], [row[a], row[a + 1], row[a + 2], valid, invalid, cast, unreturned, voters]), n
        )
        if place == "県計":
            total = entry
        else:
            places.append({"place": place, **entry})
    if total is None:
        raise ValueError(f"{path} has no prefecture total")
    return names, places, total


def split_line(line: str) -> tuple[str, list[str]]:
    tokens = line.split()
    i = next((i for i, t in enumerate(tokens) if FIGURE.match(t)), len(tokens))
    return "".join(tokens[:i]), [t for t in tokens[i:] if FIGURE.match(t)]


def miyagi_pdf_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    places, total = [], None
    for line in layout(path):
        place, values = split_line(line)
        if len(values) < 9 or not (place == "県計" or unit(place)):
            continue
        n = len(values) - 8
        entry = counts(
            spread_of(
                values[:n], [*values[n : n + 6], str(number(values[n + 7]) - number(values[n + 5])), values[n + 7]]
            ),
            n,
        )
        if place == "県計":
            total = entry
        else:
            places.append({"place": place, **entry})
    if total is None or not places:
        raise ValueError(f"{path} has no rows")
    return [""] * len(places[0]["votes"]), places, total


def table_registered(path: Path) -> dict[str, tuple[str, str]]:
    if path.suffix != ".pdf":
        return registered(path)
    out = {}
    for line in layout(path):
        place, values = split_line(line)
        if len(values) >= 12 and unit(place):
            out[place] = (str(number(values[2])), str(number(values[5])))
    return out


def layout(path: Path) -> list[str]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return unicodedata.normalize("NFKC", text).splitlines()


def pdf_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    places, total = [], None
    for line in layout(path):
        tokens = line.split()
        if len(tokens) > 12 and tokens[0].isdigit() and PLACE.match(tokens[1]):
            values = tokens[2:]
            n = len(values) - 10
            if values[n + 1] != "100" or not re.match(r"^\d{1,2}:\d{2}$", values[-1]):
                raise ValueError(f"{path}: unread row {line!r}")
            spread = [x for v in values[:n] for x in (v, "")] + values[n : n + 1] + values[n + 2 : n + 9]
            places.append({"place": tokens[1], **counts(spread, n)})
        elif tokens[:1] == ["県計"] or tokens[:2] == ["県", "計"]:
            values = tokens[2:] if tokens[0] == "県" else tokens[1:]
            n = len(values) - 11
            spread = [x for v in values[:n] for x in (v, "")] + values[n : n + 1] + values[n + 4 : n + 11]
            total = counts(spread, n)
    if total is None or not places:
        raise ValueError(f"{path} has no rows")
    return [""] * len(places[0]["votes"]), places, total


def pdf_registered(path: Path) -> dict[str, tuple[str, str]]:
    out = {}
    for line in layout(path):
        tokens = line.split()
        if len(tokens) >= 14 and tokens[0].isdigit() and PLACE.match(tokens[1]):
            out[tokens[1]] = (str(number(tokens[4])), str(number(tokens[7])))
    return out


def aomori_votes(path: Path, count: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    votes = {}
    for line in layout(path):
        place, values = split_line(line)
        if (unit(place) or place == "県計") and len(values) >= 4:
            n = (len(values) - 2) // 2
            if sum(number(v) for v in values[0 : 2 * n : 2]) != number(values[2 * n]):
                raise ValueError(f"{path}: unread row {line!r}")
            votes[place] = values[0 : 2 * n : 2]
    lines = layout(count)
    start = next((i for i, line in enumerate(lines) if "開票に関する調" in line and "・" not in line), 0)
    places, total = [], None
    for line in lines[start:]:
        place, values = split_line(line)
        if not (unit(place) or place == "県計") or len(values) < 7:
            continue
        cast, valid, invalid, _, refused, kept, other = values[:7]
        cut, unclaimed = (values[7], values[8]) if len(values) >= 9 else ("0", "0")
        unreturned = total_of(refused, kept, other)
        tail = [total_of(*votes[place]), cut, unclaimed, valid, invalid, cast, unreturned, total_of(cast, unreturned)]
        entry = {**counts(spread_of(votes[place], tail), len(votes[place])), "unreturned": None, "voters": None}
        if place == "県計":
            total = entry
            break
        places.append({"place": place, **entry})
    if total is None or {p["place"] for p in places} | {"県計"} != set(votes):
        raise ValueError(f"{count} does not cover the candidate table")
    return [""] * len(places[0]["votes"]), places, total


READERS: dict[str, tuple[Any, Any]] = {
    "東京都": (tokyo_votes, tokyo_registered),
    "栃木県": (excel_votes, excel_registered),
    "茨城県": (ibaraki_votes, ibaraki_registered),
    "和歌山県": (pdf_votes, pdf_registered),
    "埼玉県": (saitama_votes, registered),
    "千葉県": (chiba_votes, registered),
    "宮城県": (miyagi_votes, table_registered),
    "秋田県": (auto_votes, registered),
    "山形県": (auto_votes, registered),
    "岩手県": (auto_votes, registered),
    "福島県": (auto_votes, registered),
    "青森県": (aomori_votes, table_registered),
}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    elections = pl.read_parquet(mic_chiji.PARSED / f"elections-v{mic_chiji.VERSION}.parquet")
    people = pl.read_parquet(mic_chiji.PARSED / f"candidates-v{mic_chiji.VERSION}.parquet")
    rows = []
    for key, urls in RECORDS.items():
        votes_path, turnout_path, *extra = (raw.cached(SOURCE, url).path for url in urls)
        votes_reader, turnout_reader = READERS[key.split(":")[1]]
        names, places, total = votes_reader(votes_path, *extra)
        registered = turnout_reader(turnout_path)
        official = elections.filter(pl.col("key") == key).row(0, named=True)
        candidates = people.filter(pl.col("key") == key).sort("order")
        extra = [Decimal(v) for p in [*places, total] for v in p["votes"][candidates.height :]]
        if len(names) > candidates.height and not any(extra):
            names = names[: candidates.height]
            for p in [*places, total]:
                p["votes"] = p["votes"][: candidates.height]
        if len(names) != candidates.height:
            raise ValueError(f"{key}: {len(names)} candidates in the record, {candidates.height} in the summary")
        for name, mic in zip(names, candidates["name"], strict=True):
            if name and re.sub(r"\s", "", name) != re.sub(r"\s", "", mic):
                raise ValueError(f"{key}: candidate {name} is {mic} in the summary")
        if set(registered) != {p["place"] for p in places}:
            raise ValueError(f"{key}: turnout and count files list different municipalities")
        for place in [p for p in places if p["voters"] is None]:
            place["voters"] = registered[str(place["place"])][1]
            place["unreturned"] = str(Decimal(place["voters"]) - Decimal(place["cast"]))
        if total["voters"] is None:
            total["voters"] = str(sum(Decimal(p["voters"]) for p in places))
        for place in places:
            votes = [Decimal(v) for v in place["votes"]]
            v = {k: Decimal(str(place[k])) for k in ("total", "cut", "other", *FIELDS)}
            if sum(votes) != v["total"] or v["total"] + v["cut"] + v["other"] != v["valid"]:
                raise ValueError(f"{key} {place['place']}: candidate votes do not sum to valid votes")
            if v["valid"] + v["invalid"] != v["cast"] or v["cast"] + v["unreturned"] != v["voters"]:
                raise ValueError(f"{key} {place['place']}: ballots do not add up")
            if registered[str(place["place"])][1] != place["voters"]:
                raise ValueError(f"{key} {place['place']}: voters differ between the turnout and count files")
            place["registered"] = registered[str(place["place"])][0]
        for i, mic in enumerate(candidates["votes"]):
            summed = sum(Decimal(p["votes"][i]) for p in places)
            if summed != Decimal(total["votes"][i]) or abs(summed - Decimal(mic)) >= 1:
                raise ValueError(f"{key}: candidate {i + 1} sums to {summed}, the summary prints {mic}")
        for field in (*FIELDS[:3], "voters", "registered"):
            summed = sum(Decimal(str(p[field])) for p in places)
            if field != "registered" and summed != Decimal(str(total[field])):
                raise ValueError(f"{key}: {field} sums to {summed}, the record's total is {total[field]}")
            if official[field] is not None and summed != Decimal(official[field]):
                raise ValueError(f"{key}: {field} sums to {summed}, the summary prints {official[field]}")
        rows.extend({"key": key, "urls": list(urls), **p} for p in places)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"municipalities-v{VERSION}.parquet")


NOTES = {
    "栃木県": "Final count and turnout tables, read from the Excel workbooks; the 2012-2020 workbooks are the "
    "commission's own files as archived by the Wayback Machine.",
    "青森県": "Candidate votes from the commission's candidate table, valid and invalid ballots from its count table "
    "(for 2019, the count table of its printed election record), and the electorate and voters from its turnout "
    "table, read from the PDFs' text layers.",
}
METHOD = (
    "Votes by municipality, or by ward in designated cities, from the prefectural election commission's final "
    "count table (確定), with the electorate from its turnout table. Candidate votes, valid, invalid, and cast "
    "ballots, and voters sum to the prefecture totals the ministry's summary prints."
)


def place_code(place: str, codes: dict[str, str], wards: list[tuple[str, str]]) -> tuple[str, str | None]:
    place = place.translate(VARIANTS)
    codes = {text.translate(VARIANTS): code for text, code in codes.items()}
    wards = [(code, text.translate(VARIANTS)) for code, text in wards]
    if place in codes:
        return "jp.municipality", codes[place]
    city = re.match(r"^(.+?市)(.+区)$", place)
    if city and city[1] in codes:
        found = [
            code
            for code, text in wards
            if text == city[2] and max(c for c in codes.values() if c < code) == codes[city[1]]
        ]
    else:
        found = [code for code, text in wards if text == place]
    return "jp.ward", found[0] if len(found) == 1 else None


def emit() -> Path:
    frame = pl.read_parquet(PARSED / f"municipalities-v{VERSION}.parquet")
    with connect() as conn:
        prefectures = {
            native: iso
            for iso, native in conn.execute(
                """select p.iso_code, ja.text from polity p
                   join name ja on ja.entity_id = p.id and ja.lang = 'ja' and ja.kind = 'official'
                   where p.iso_code like 'JP-__'"""
            )
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's prefectural governor elections by municipality from the prefectural election "
            "commissions' count and turnout tables",
            polity="JP",
        )
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
                notes=NOTES.get(
                    prefecture,
                    "Final count and turnout tables, read from the Excel workbooks or the PDFs' text layers.",
                ),
            )
        added: set[object] = set()
        values = []
        for key in frame["key"].unique(maintain_order=True):
            held_text, prefecture = key.split(":")
            held = date.fromisoformat(held_text)
            rows = frame.filter(pl.col("key") == key)
            iso = prefectures[prefecture]
            polity = cs.polity(iso)
            contest = cs.key(f"{mic_chiji.SOURCE}.contest", key)
            county_set = cs.key(f"{mic_chiji.SOURCE}.result_set", key)
            found = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
            if found is None:
                raise ValueError(f"{key}: run jp.mic_chiji first")
            prefecture_unit = cs.unit("jp.prefecture", iso[3:])
            names = {
                scheme: conn.execute(
                    """select c.code, n.text, n.valid @> %s::date from area a join area_code c on c.area_id = a.id
                       join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official'
                       where a.scheme_code = %s and c.code like %s""",
                    [held, scheme, f"{iso[3:]}%"],
                ).fetchall()
                for scheme in ("jp.municipality", "jp.ward")
            }
            codes = {text: code for code, text, current in names["jp.municipality"] if current}
            renamed = {}
            for code, text, current in names["jp.municipality"]:
                if not current and text not in codes:
                    renamed[text] = code if renamed.get(text, code) == code else None
            codes |= {text: code for text, code in renamed.items() if code is not None}
            source = sources[prefecture]
            first = rows.row(0, named=True)
            for url in first["urls"]:
                cs.artifact(raw.cached(SOURCE, url), source)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=found[0],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [county_set]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            for row in rows.iter_rows(named=True):
                scheme, code = place_code(
                    row["place"], codes, [(code, text) for code, text, current in names["jp.ward"] if current]
                )
                if code is None:
                    raise ValueError(f"{key}: no municipality or ward named {row['place']} on {held}")
                unit = cs.unit(scheme, code)
                if unit in cs.minted and unit not in added:
                    added.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code=scheme,
                        code=code,
                        parent_id=prefecture_unit,
                        area_relation="equals",
                        area_id=cs.area(scheme, code),
                        kind="standard",
                        valid_from=None,
                    )
                for field, measure in MEASURES.items():
                    values.append((result_set, unit, None, measure, row[field]))
                for order, votes in enumerate(row["votes"], 1):
                    option = cs.key(f"{mic_chiji.SOURCE}.option", f"{key}:{order}")
                    values.append((result_set, unit, option, 1, votes))
        for result_set, unit, option, measure, amount in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=Decimal(amount),
            )
        return cs.write()
