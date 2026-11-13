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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"
TOCHIGI = "https://www.pref.tochigi.lg.jp/senkyo"
IBARAKI = "https://www.pref.ibaraki.jp/somu/shichoson/senkyo/senkan"
WAKAYAMA = "https://www.pref.wakayama.lg.jp/prefg/010600/wsenkan"


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
RECORDS: dict[str, tuple[str, str]] = {
    "2025-09-07:茨城県": (
        f"{IBARAKI}/2025tizi/documents/d_kaihyo_k_50_99.xlsx",
        f"{IBARAKI}/2025tizi/documents/d_tohyo_k_50_1.xlsx",
    ),
    "2021-09-05:茨城県": (
        f"{IBARAKI}/2021chijisen/documents/d_kaihyo_k_50_05.xlsx",
        f"{IBARAKI}/2021chijisen/documents/d_tohyo_k_50_1.xlsx",
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
TOTAL = re.compile(r"^県\s*計$")


def suffix(url: str) -> str:
    return Path(url).suffix


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
    cast = number(values[n + 5])
    unreturned = sum(number(v) for v in values[n + 6 : n + 9])
    return [x for v in values[:n] for x in (v, "")] + values[n : n + 6] + [str(unreturned), str(cast + unreturned)]


def ibaraki_votes(path: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    rows = cells(path)
    header = next(row for row in rows if row[:2] == ["市町村名", "確定"])
    n = header.index("得票総数") - 2
    places, total = [], None
    for row in rows:
        if len(row) > n + 10 and PLACE.match(row[0]) and row[1] == "*":
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
        if len(row) >= 7 and PLACE.match(row[0]) and row[1].isdigit()
    }


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


READERS = {
    ".xls": (excel_votes, excel_registered),
    ".xlsx": (ibaraki_votes, ibaraki_registered),
    ".pdf": (pdf_votes, pdf_registered),
}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    elections = pl.read_parquet(mic_chiji.PARSED / f"elections-v{mic_chiji.VERSION}.parquet")
    people = pl.read_parquet(mic_chiji.PARSED / f"candidates-v{mic_chiji.VERSION}.parquet")
    rows = []
    for key, (votes_url, turnout_url) in RECORDS.items():
        votes_path, turnout_path = raw.cached(SOURCE, votes_url).path, raw.cached(SOURCE, turnout_url).path
        votes_reader, turnout_reader = READERS[votes_path.suffix]
        names, places, total = votes_reader(votes_path)
        registered = turnout_reader(turnout_path)
        official = elections.filter(pl.col("key") == key).row(0, named=True)
        candidates = people.filter(pl.col("key") == key).sort("order")
        if len(names) != candidates.height:
            raise ValueError(f"{key}: {len(names)} candidates in the record, {candidates.height} in the summary")
        for name, mic in zip(names, candidates["name"], strict=True):
            if name and re.sub(r"\s", "", name) != re.sub(r"\s", "", mic):
                raise ValueError(f"{key}: candidate {name} is {mic} in the summary")
        if set(registered) != {p["place"] for p in places}:
            raise ValueError(f"{key}: turnout and count files list different municipalities")
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
        rows.extend({"key": key, "votes_url": votes_url, "turnout_url": turnout_url, **p} for p in places)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"municipalities-v{VERSION}.parquet")


METHOD = (
    "Votes by municipality from the prefectural election commission's final count table (確定), with the electorate "
    "from its turnout table. Candidate votes, valid, invalid, and cast ballots, and voters sum to the prefecture "
    "totals the ministry's summary prints."
)


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
            "commissions' count and turnout tables (Ibaraki, Tochigi, and Wakayama)",
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
                notes="Final count and turnout tables, read from the Excel workbooks or the PDFs' text layers; "
                "Tochigi's workbooks are the commission's own files as archived by the Wayback Machine.",
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
            codes = {
                text: code
                for code, text in conn.execute(
                    """select c.code, n.text from area a join area_code c on c.area_id = a.id
                       join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official'
                       where a.scheme_code = 'jp.municipality' and c.code like %s and n.valid @> %s::date""",
                    [f"{iso[3:]}%", held],
                )
            }
            source = sources[prefecture]
            first = rows.row(0, named=True)
            for url in (first["votes_url"], first["turnout_url"]):
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
                if row["place"] not in codes:
                    raise ValueError(f"{key}: no municipality named {row['place']} on {held}")
                code = codes[row["place"]]
                unit = cs.unit("jp.municipality", code)
                if unit in cs.minted and unit not in added:
                    added.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="jp.municipality",
                        code=code,
                        parent_id=prefecture_unit,
                        area_relation="equals",
                        area_id=cs.area("jp.municipality", code),
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
