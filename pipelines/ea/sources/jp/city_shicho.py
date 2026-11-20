import html
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw
from ea.package import Draft
from ea.sources.jp import mic_shicho
from ea.store import DATA, connect

SOURCE = "jp.city_shicho"
KEY = f"{SOURCE}/all"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
NO_LICENSE = "No license stated: facts published with attribution, raw files not redistributed"


@dataclass(frozen=True)
class Record:
    url: str
    # "wide" when the table prints one column per ward, "rows" when it prints one row per ward; for "rows", the
    # columns after the candidates, in printed order (None for a column that is not kept).
    layout: str = "wide"
    tail: tuple[str | None, ...] = ()
    title: str = "市長選挙 開票結果"


# Each designated-city mayoral election the ministry's summaries hold, with the city election commission's count by
# ward. The key is jp.mic_shicho's: the election day and the city.
RECORDS: dict[str, Record] = {
    "2026-10-25:新潟市": Record(
        "https://www.city.niigata.lg.jp/shisei/senkyo/oshirase/sityou/20261025_kaihyo.files/kaihyou2242.pdf"
    ),
    "2026-02-08:大阪市": Record(
        "https://www.city.osaka.lg.jp/contents/wdu240/sokuho/kaihyo_data_10.html", "rows", (), "市長選挙 開票速報"
    ),
    "2025-10-26:神戸市": Record(
        "https://www.city.kobe.lg.jp/documents/23740/r7shityousenkekkatyou.pdf", title="市長選挙 結果調"
    ),
    "2025-10-26:川崎市": Record(
        "https://www.city.kawasaki.jp/910/cmsfiles/contents/0000181/181605/K2502sc.pdf", title="市長選挙 候補者別得票数"
    ),
    "2025-08-03:仙台市": Record(
        "https://www.city.sendai.jp/senkyokanri-kanri/senkyo/r7sityou/kekka/documents/kaihyoukekka.pdf"
    ),
    "2021-08-01:仙台市": Record(
        "https://www.city.sendai.jp/senkyokanri-kanri/senkyo/r3sityou/kekka/documents/kaihyoukekka.pdf"
    ),
    "2025-05-25:さいたま市": Record(
        "https://www.city.saitama.lg.jp/006/009/kakonosenkyokekka/p121736_d/fil/R7tokuhyoutousenn.pdf",
        title="市長選挙 得票数及び当選人",
    ),
    "2025-03-16:千葉市": Record(
        "https://www.city.chiba.jp/senkyokanri/documents/r7shicho_toukaihyoukekka.pdf", title="市長選挙 区別開票結果"
    ),
    "2024-02-04:京都市": Record(
        "https://www.city.kyoto.lg.jp/senkyo/page/0000324183.html",
        "rows",
        ("valid", "invalid", "cast", None, None),
    ),
    "2023-06-04:堺市": Record(
        "https://www.city.sakai.lg.jp/shisei/senkyo/senkyo_d/data01/72163120230605100944323.files/kaihyoukekka.pdf"
    ),
    "2023-02-05:北九州市": Record(
        "https://www.city.kitakyushu.lg.jp/files/001034947.pdf", "rows", ("valid", None, "voters")
    ),
    "2022-11-20:福岡市": Record("https://www.city.fukuoka.lg.jp/senkan/senkyo/shisei/documents/103105/kaiketu.pdf"),
    "2022-11-13:熊本市": Record(
        "https://www.city.kumamoto.jp/kiji0032234/3_2234_468724_up_2p6hhaiw.pdf", title="市長選挙 選挙執行に関する諸調"
    ),
}
FIELDS = ("valid", "invalid", "cast", "voters")
MEASURES = {"voters": 14, "cast": 3, "valid": 4, "invalid": 5}
# Row labels that end a table's candidate rows: the candidates' total and the ballot breakdown that follows it.
SUMMARY = ("得票数計", "得票総数", "按分", "あん分", "属さない", "属しない", "合計")
NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$")


def suffix(url: str) -> str:
    return Path(url).suffix or ".html"


def fetch() -> None:
    for key, record in RECORDS.items():
        try:
            raw.fetch(SOURCE, record.url, suffix=suffix(record.url))
        except Exception as error:
            print(f"{key}: {record.url} failed: {error}", file=sys.stderr)


def squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def lines(path: Path) -> list[str]:
    """The file's text, one printed line per item: the PDF's text layer in layout order, or an HTML table's rows
    with their cells joined by spaces."""
    if path.suffix == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
        return unicodedata.normalize("NFKC", text.stdout).splitlines()
    body = path.read_bytes()
    text = body.decode("utf-8") if body[:3] == b"\xef\xbb\xbf" or b"charset=utf-8" in body[:2000].lower() else ""
    if not text:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("cp932")
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.DOTALL | re.IGNORECASE):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", " ", cell))
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        rows.append("   ".join(re.sub(r"\s+", " ", cell).strip() for cell in cells))
    return [unicodedata.normalize("NFKC", row) for row in rows]


def numbers(line: str) -> tuple[str, list[str]]:
    """Split a line into its label (the text before its first figure, without spaces) and its figures."""
    tokens = line.split()
    figures = [t for t in tokens if NUMBER.match(t)]
    first = next((i for i, t in enumerate(tokens) if NUMBER.match(t)), len(tokens))
    return squash("".join(tokens[:first])), [t.replace(",", "") for t in figures]


def wards_in(line: str, wards: list[str]) -> list[str]:
    """The ward names a header line prints, in order, reading the longest name first at each position so that
    東灘区 is not read as 灘区."""
    flat = squash(line)
    found, i = [], 0
    by_length = sorted(wards, key=len, reverse=True)
    while i < len(flat):
        name = next((w for w in by_length if flat.startswith(w, i)), None)
        if name is None:
            i += 1
        else:
            found.append(name)
            i += len(name)
    return found


def field_of(label: str) -> str | None:
    if "率" in label or "時刻" in label:
        return None
    if "投票者総数" in label:
        return "voters"
    if "無効投票" in label:
        return "invalid"
    if "有効投票" in label:
        return "valid"
    if "投票総数" in label:
        return "cast"
    return None


def wide(rows: list[str], wards: list[str], n: int) -> list[dict[str, Any]]:
    """Every table in the file that prints one column per ward: its candidate rows (label and figures by ward) and
    its ballot rows."""
    tables = []
    starts = [i for i, line in enumerate(rows) if sorted(wards_in(line, wards)) == sorted(wards)]
    for k, start in enumerate(starts):
        order = wards_in(rows[start], wards)
        flat = squash(rows[start])
        total_first = "計" in flat[: flat.index(order[0])]
        end = starts[k + 1] if k + 1 < len(starts) else len(rows)
        people: list[tuple[str, list[str]]] = []
        fields: dict[str, list[str]] = {}
        done = False
        i = start + 1
        while i < end:
            label, figures = numbers(rows[i])
            field = field_of(label)
            if field is not None and len(figures) < len(order) and i + 1 < end:
                after, more = numbers(rows[i + 1])
                if not after and len(more) >= len(order):
                    figures = more
                    i += 1
            i += 1
            if len(figures) < len(order):
                continue
            figures = figures[-(len(order) + 1) :] if len(figures) > len(order) else figures
            total = None
            if len(figures) == len(order) + 1:
                total, figures = (figures[0], figures[1:]) if total_first else (figures[-1], figures[:-1])
            done = done or field is not None or any(word in label for word in SUMMARY)
            if field is not None:
                fields.setdefault(field, figures)
                if total is not None and sum(map(Decimal, figures)) != Decimal(total):
                    raise ValueError(f"{label}: wards sum to {sum(map(Decimal, figures))}, the table prints {total}")
            elif not done:
                if total is not None and sum(map(Decimal, figures)) != Decimal(total):
                    raise ValueError(f"{label}: wards sum to {sum(map(Decimal, figures))}, the table prints {total}")
                people.append((label, figures))
        if len(people) == n:
            tables.append({"wards": order, "people": people, "fields": fields})
    return tables


def by_rows(rows: list[str], wards: list[str], n: int, tail: tuple[str | None, ...]) -> dict[str, Any]:
    """A table that prints one row per ward: the candidates' columns, then the tail's."""
    width = n + len(tail)
    order, people, fields = [], [[] for _ in range(n)], {f: [] for f in tail if f is not None}
    for line in rows:
        _, figures = numbers(line)
        flat = squash(line)
        name = next((w for w in sorted(wards, key=len, reverse=True) if flat.startswith(w)), None)
        if name is None or len(figures) != width or name in order:
            continue
        order.append(name)
        for k in range(n):
            people[k].append(figures[k])
        for k, field in enumerate(tail):
            if field is not None:
                fields[field].append(figures[n + k])
    if sorted(order) != sorted(wards):
        raise ValueError(f"rows for {sorted(set(wards) - set(order))} not found")
    return {"wards": order, "people": [("", p) for p in people], "fields": fields}


def matched(key: str, table: dict[str, Any], mic: pl.DataFrame) -> list[int] | None:
    """The ministry's ballot order of each of the table's candidate rows: by name without spaces when the row prints
    one, otherwise by the candidate's total. None when the totals do not match the ministry's."""
    names = [squash(name) for name in mic["name"]]
    votes = [Decimal(v) for v in mic["votes"]]
    out = []
    for label, figures in table["people"]:
        summed = sum(map(Decimal, figures))
        hits = [k for k, name in enumerate(names) if name and name in label]
        if not hits:
            hits = [k for k, v in enumerate(votes) if v == summed]
        if len(hits) != 1 or votes[hits[0]] != summed:
            return None
        out.append(hits[0])
    return out if sorted(out) == list(range(len(names))) else None


def summaries() -> tuple[dict[str, dict[str, str | None]], dict[str, pl.DataFrame]]:
    """The ministry's city totals and candidates for each mayoral election, as jp.mic_shicho loaded them."""
    fields = {measure: field for field, measure in mic_shicho.MEASURES.items()}
    with connect() as conn:
        totals = conn.execute(
            """select x.value, v.measure, v.value::text from external_id x join result_set r on r.id = x.entity_id
               cross join lateral (select * from result_value v where v.result_set_k = r.k offset 0) v
               where x.scheme = %s and v.option_k = 0""",
            [f"{mic_shicho.SOURCE}.result_set"],
        ).fetchall()
        options = conn.execute(
            """select x.value, o.ballot_position, o.ballot_label,
                      (select v.value::text from result_value v join result_set r on r.k = v.result_set_k
                       where r.id = s.entity_id and v.option_k = o.k and v.measure = 1)
               from external_id x join contest_option o on o.contest_id = x.entity_id
               join external_id s on s.scheme = %s and s.value = x.value
               where x.scheme = %s""",
            [f"{mic_shicho.SOURCE}.result_set", f"{mic_shicho.SOURCE}.contest"],
        ).fetchall()
    official: dict[str, dict[str, str | None]] = {}
    for key, measure, value in totals:
        if measure in fields:
            official.setdefault(key, dict.fromkeys(mic_shicho.MEASURES))[fields[measure]] = value
    people = pl.DataFrame(options, schema=["key", "order", "name", "votes"], orient="row")
    return official, {key: frame.sort("order") for (key,), frame in people.group_by("key")}


def ward_names(conn: Any, city: str, held: date) -> dict[str, str]:
    """The city's wards on the election day, by their Japanese name, from the jp.ward areas."""
    code = int(mic_shicho.CITIES[city][0])
    bound = min([int(c) for c, _ in mic_shicho.CITIES.values() if int(c) > code] + [code + 30])
    found = conn.execute(
        """select c.code, n.text from area a join area_code c on c.area_id = a.id
           join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official'
           where a.scheme_code = 'jp.ward' and c.code > %s and c.code < %s and n.valid @> %s::date""",
        [f"{code:05d}", f"{bound:05d}", held],
    ).fetchall()
    names: dict[str, str] = {}
    for ward, text in found:
        if names.get(text, ward) != ward:
            raise ValueError(f"{city}: two wards named {text} on {held}")
        names[text] = ward
    return names


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    official, people = summaries()
    out = []
    with connect() as conn:
        for key, record in RECORDS.items():
            held, city = key.split(":")
            try:
                artifact = raw.cached(SOURCE, record.url)
            except LookupError:
                print(f"{key}: {record.url} is not cached; skipped")
                continue
            names = ward_names(conn, city, date.fromisoformat(held))
            mic = people[key]
            rows = lines(artifact.path)
            if record.layout == "wide":
                tables = [t for t in wide(rows, list(names), mic.height) if matched(key, t, mic) is not None]
                if len(tables) != 1:
                    raise ValueError(f"{key}: {len(tables)} tables match the ministry's candidates")
                table = tables[0]
            else:
                table = by_rows(rows, list(names), mic.height, record.tail)
            order = matched(key, table, mic)
            if order is None:
                raise ValueError(f"{key}: candidate totals differ from the ministry's {mic['votes'].to_list()}")
            fields = {f: v for f, v in table["fields"].items() if f in FIELDS}
            for field, values in fields.items():
                summed = sum(map(Decimal, values))
                printed = official[key][field]
                if printed is not None and summed != Decimal(printed):
                    raise ValueError(f"{key}: {field} sums to {summed}, the summary prints {printed}")
            for w, ward in enumerate(table["wards"]):
                votes = [Decimal(0)] * mic.height
                for (_, figures), k in zip(table["people"], order, strict=True):
                    votes[k] = Decimal(figures[w])
                row = {f: fields[f][w] if f in fields else None for f in FIELDS}
                if row["valid"] is not None and sum(votes) > Decimal(row["valid"]):
                    raise ValueError(f"{key} {ward}: candidate votes exceed valid votes")
                valid, invalid, cast = row["valid"], row["invalid"], row["cast"]
                complete = valid is not None and invalid is not None and cast is not None
                if complete and Decimal(str(valid)) + Decimal(str(invalid)) != Decimal(str(cast)):
                    raise ValueError(f"{key} {ward}: valid and invalid ballots do not sum to ballots cast")
                out.append(
                    {
                        "key": key,
                        "url": record.url,
                        "ward": ward,
                        "code": names[ward],
                        **row,
                        "votes": [str(v) for v in votes],
                    }
                )
            print(f"{key}: {len(table['wards'])} wards, {sorted(fields)}; sums match the summary")
    pl.DataFrame(out, infer_schema_length=None).write_parquet(PARSED / f"wards-v{VERSION}.parquet")


METHOD = (
    "Votes by ward from the city election commission's count table, read from the PDF's text layer or the HTML "
    "table. Candidate votes, and the ballot figures the table prints, sum to the city totals of the ministry's "
    "summary."
)
NOTES = {
    "大阪市": "The commission's count page (開票速報) in its final state (確定): candidate votes by ward, without "
    "ballot figures.",
    "熊本市": "The commission's election record prints candidate votes by ward, without ballot figures.",
    "北九州市": "The commission's count table prints invalid ballots together with other ballots, so only valid "
    "ballots and voters are kept by ward.",
}


def emit() -> Path:
    return build()


def build() -> Path:
    """Write the package jp.city_shicho/all from the parsed table. It reads the database, through Draft, so its
    result sets attach to jp.mic_shicho's contests and options and its units are the jp.ward units already loaded."""
    frame = pl.read_parquet(PARSED / f"wards-v{VERSION}.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's designated-city mayoral elections by ward from the city election commissions' count "
            "tables",
            polity="JP",
        )
        sources = {}
        for key in frame["key"].unique(maintain_order=True):
            city = key.split(":")[1]
            if city in sources:
                continue
            publisher, title = f"{city}選挙管理委員会", f"{city}長選挙 開票結果"
            sources[city] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[city],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=RECORDS[key].url,
                license=NO_LICENSE,
                license_url=None,
                attribution=publisher,
                notes=NOTES.get(city, "Count tables by ward, read from the PDFs' text layers or the HTML tables."),
            )
        values = []
        for key in frame["key"].unique(maintain_order=True):
            city = key.split(":")[1]
            rows = frame.filter(pl.col("key") == key)
            contest = cs.key(f"{mic_shicho.SOURCE}.contest", key)
            city_set = cs.key(f"{mic_shicho.SOURCE}.result_set", key)
            found = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
            if found is None:
                raise ValueError(f"{key}: run jp.mic_shicho first")
            source = sources[city]
            cs.artifact(raw.cached(SOURCE, RECORDS[key].url), source)
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
                "select option_id, kind, seats from outcome where result_set_id = %s", [city_set]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            for row in rows.iter_rows(named=True):
                unit = cs.unit("jp.ward", row["code"])
                if unit in cs.minted:
                    raise ValueError(f"{key}: no jp.ward unit {row['code']} ({row['ward']})")
                for field, measure in MEASURES.items():
                    if row[field] is not None:
                        values.append((result_set, unit, None, measure, row[field]))
                for order, votes in enumerate(row["votes"], 1):
                    option = cs.key(f"{mic_shicho.SOURCE}.option", f"{key}:{order}")
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
