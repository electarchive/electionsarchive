import html
import re
import subprocess
from datetime import UTC, datetime
from statistics import median

import httpx
import polars as pl

from ea import raw, scan
from ea.store import DATA

SOURCE = "tr.die_local"
PARSED = DATA / "parsed" / SOURCE
LIBRARY = "https://kutuphane.tuik.gov.tr/pdf"
WAYBACK = "https://web.archive.org/web/2025id_"
BOOKS = {1963: "0015160", 1973: "0015468"}
TABLES_1973 = {"il_genel": (10, 47), "belediye_baskani": (52, 133), "belediye_meclisi": (138, 221)}
PARTY_WORDS = {"AP", "CHP", "CGP", "DP", "MP", "MHP", "MSP", "TBP", "Bağımsızlar", "CKMP", "TİP", "YTP"}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
DIGITS = str.maketrans({"b": "6", "i": "1", "İ": "1", "I": "1", "l": "1", "o": "0", "O": "0", "S": "5", ",": "."})
DASHES = {"—", "-", "_", "–", ".—", "—.", "•—", "—,", ".", "•", ",", "'", "^"}


def url(year: int) -> str:
    return f"{LIBRARY}/{BOOKS[year]}.pdf"


def fetch() -> None:
    for year in BOOKS:
        try:
            raw.cached(SOURCE, url(year))
            continue
        except LookupError:
            pass
        response = httpx.get(f"{WAYBACK}/{url(year)}", follow_redirects=True, timeout=900)
        response.raise_for_status()
        raw.store(
            SOURCE,
            url(year),
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def pages(
    path: str, first: int, last: int
) -> dict[int, tuple[float, float, list[tuple[float, float, float, float, str]]]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), path, "-"], capture_output=True, check=True
    ).stdout.decode()
    found = {}
    for number, chunk in enumerate(out.split("<page ")[1:], first):
        width, height = (float(value) for value in re.findall(r'(?:width|height)="([\d.]+)"', chunk)[:2])
        found[number] = (
            width,
            height,
            [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)],
        )
    return found


def lines(words: list[tuple[float, float, float, float, str]], top: float) -> list[list[tuple]]:
    rows: list[list[tuple]] = []
    for word in sorted((w for w in words if w[1] > top), key=lambda w: (w[1] + w[3]) / 2):
        center = (word[1] + word[3]) / 2
        if rows and abs(center - median((w[1] + w[3]) / 2 for w in rows[-1])) <= 3.2:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row) for row in rows]


def numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\d.,bilIİoOS]+", text)) and any(c.isdigit() for c in text)


def integer(tokens: list[tuple]) -> int | None:
    text = " ".join(t[4].translate(DIGITS) for t in tokens).replace(".", "")
    if re.fullmatch(r"\d{1,3}( \d{3})*|\d+", text):
        return int(text.replace(" ", ""))
    return None


def percent(tokens: list[tuple]) -> float | None:
    text = "".join(t[4].translate(DIGITS) for t in tokens).replace(".", "")
    if re.fullmatch(r"\d{2,4}", text) and int(text) <= 1000:
        return int(text) / 10
    return None


def header(words: list[tuple], top: float) -> tuple[dict[str, float], float]:
    labels = {}
    for x0, y0, x1, y1, text, *_ in words:
        if y1 < top and text.strip(".") in PARTY_WORDS and text not in labels:
            labels[text.strip(".")] = (x0 + x1) / 2
    return dict(sorted(labels.items(), key=lambda item: item[1])), top


def bands(labels: dict[str, float], right: float | None) -> dict[str, tuple[float, float]]:
    names = list(labels)
    centers = [labels[n] for n in names]
    out = {}
    for i, label in enumerate(names):
        left = (centers[i - 1] + centers[i]) / 2 if i else centers[i] - (centers[1] - centers[0]) / 2
        end = (
            (centers[i] + centers[i + 1]) / 2
            if i + 1 < len(names)
            else (right if right is not None else centers[i] + (centers[i] - centers[i - 1]) / 2)
        )
        out[label] = (left, end)
    return out


def groups(tokens: list[tuple]) -> list[list[tuple]]:
    out: list[list[tuple]] = []
    for token in tokens:
        text = token[4].translate(DIGITS)
        if out and token[0] - out[-1][-1][2] <= 6.5 and re.fullmatch(r"\d{3}", text) and "." not in out[-1][-1][4]:
            out[-1].append(token)
        else:
            out.append([token])
    return out


def box(tokens: list[tuple], width: float, height: float) -> list[float]:
    return [
        round(min(t[0] for t in tokens) / width, 6),
        round(min(t[1] for t in tokens) / height, 6),
        round(max(t[2] for t in tokens) / width, 6),
        round(max(t[3] for t in tokens) / height, 6),
    ]


HEADS = {"Aldığı", "oy", "%", "/o", "V", "sayısı", "Partisi", "Soyadı", "oranı", "Adı"}


def spread(left: tuple, right: tuple, table: str, pages: tuple[int, int], last: dict | None = None) -> list[dict]:
    rows = []
    sides = {}
    last = {} if last is None else last
    for which, (width, height, words) in (("left", left), ("right", right)):
        top = max((w[3] for w in words if w[4] in HEADS and w[3] < 230), default=150) + 2
        labels, _ = header(words, top)
        if len(labels) < 2 and which in last:
            top, labels, adi = last[which]
            words = [*words, *adi]
        else:
            last[which] = (top, labels, [w for w in words if w[4] == "Adı" and w[3] < top])
        named = next((w for w in words if w[4] == "Adı" and w[3] < top), None)
        cut = None if named is None else named[0] - 6
        parts = bands(labels, cut)
        if cut is not None:
            cut = parts["Bağımsızlar"][0] + 2 * (labels["Bağımsızlar"] - parts["Bağımsızlar"][0]) + 4
            parts["Bağımsızlar"] = (parts["Bağımsızlar"][0], cut)
        dotted: dict[str, list[float]] = {label: [] for label in parts}
        body = lines(words, top)
        for row in body:
            for token in row:
                if numeric(token[4]):
                    for label, (start, end) in parts.items():
                        if start <= (token[0] + token[2]) / 2 < end:
                            dotted[label].append(token[2])
        edges = {label: median(x for x in xs if x >= max(xs) - 6) if xs else None for label, xs in dotted.items()}
        previous = None
        for label, (begin, end) in list(parts.items()):
            if edges[label] is not None:
                parts[label] = (begin if previous is None else previous + 2, edges[label] + 3)
                previous = edges[label]
        sides[which] = (width, height, body, parts, edges, cut, top, labels)
    width, height, body, parts, edges, _, ltop, _ = sides["left"]
    if not parts or not sides["right"][3]:
        return rows
    start = min(begin for begin, _ in parts.values())
    entries = []
    for row in body:
        text = [t for t in row if not numeric(t[4]) and t[4] not in DASHES and t[2] < start]
        if not text:
            continue
        before = [t for t in row if numeric(t[4]) and t[2] < start]
        entries.append(((row[0][1] + row[0][3]) / 2, row, text, before))
    rbody, rtop, rlabels = sides["right"][2], sides["right"][6], sides["right"][7]
    rtokens = [t for r in rbody for t in r]
    rcenters = [(r[0][1] + r[0][3]) / 2 for r in rbody]
    best, offset = -1, 0.0
    for step in range(-32, 33):
        shift = rtop - ltop + step / 4
        hits = sum(any(abs(y + shift - c) <= 2.5 for c in rcenters) for y, *_ in entries)
        if hits > best:
            best, offset = hits, shift
    for y, row, text, before in entries:
        match = sorted(t for t in rtokens if abs((t[1] + t[3]) / 2 - y - offset) <= 4.5)
        record = {
            "table": table,
            "name": " ".join(t[4] for t in text),
            "indent": min(t[0] for t in text),
            "cells": [],
            "mayor": None,
            "mayor_party": None,
            "mayor_box": None,
        }
        metrics = ["boxes", "registered", "voters", "valid", "turnout"]
        for chosen in ([t for t in before if len(t) == 5], before):
            values = groups(chosen)
            ints = [g for g in values if "." not in "".join(t[4] for t in g)]
            pcts = [g for g in values if "." in "".join(t[4] for t in g)]
            if len(ints) == 4 or (len(ints) == 5 and not pcts):
                break
        if len(ints) == 5 and not pcts:
            pcts = [ints.pop()]
        for metric, group in zip(metrics, [*ints[:4], *pcts[:1]], strict=False):
            value = percent(group) if metric == "turnout" else integer(group)
            crop = [min(t[0] for t in group), y - 6, max(t[2] for t in group), y + 6]
            record["cells"].append((metric, value, None, pages[0], box(group, width, height), {"vote": crop}))
        if len(ints) != 4:
            record["cells"].append(("?pre", None, None, pages[0], box(before or text, width, height), {}))
        for side, tokens_row in (("left", row), ("right", match or [])):
            w, h, _, bands_, edges_, cut, _, _ = sides[side]
            page = pages[0] if side == "left" else pages[1]
            middle = y if side == "left" else y + offset
            for label, (begin, end) in bands_.items():
                inside = [t for t in tokens_row if begin <= (t[0] + t[2]) / 2 < end and numeric(t[4])]
                if not inside:
                    continue
                edge = edges_[label] if edges_[label] is not None else end - 4
                share = [t for t in inside if t[2] >= edge - 21]
                count = [t for t in inside if t[2] < edge - 21]
                record["cells"].append(
                    (
                        label,
                        integer(count) if count else None,
                        percent(share) if share else None,
                        page,
                        box(inside, w, h),
                        {
                            "vote": [begin, middle - 6, edge - 19, middle + 6],
                            "pct": [edge - 22, middle - 6, edge + 3, middle + 6],
                            "ocr": any(len(t) > 5 for t in count),
                        },
                    )
                )
            if side == "right" and cut is not None:
                tail = [
                    t
                    for t in tokens_row
                    if t[0] > rlabels["Bağımsızlar"] + 10 and not numeric(t[4]) and t[4] not in DASHES
                ]
                if tail:
                    words_ = [t[4] for t in tail]
                    if words_[-1] in PARTY_WORDS or words_[-1] == "Bağımsız":
                        record["mayor_party"] = words_.pop()
                    record["mayor"] = " ".join(words_)
                    record["mayor_box"] = (page, box(tail, w, h))
        rows.append(record)
    return rows


def ocr(year: int, number: int, width: float, height: float) -> tuple[float, float, list[tuple]]:
    pdf = raw.cached(SOURCE, url(year)).path
    found = scan.words(pdf, number, PARSED / "ocr" / f"{year}-{number}.tsv", "tur")
    return (
        width,
        height,
        [(b[0] * 72 / 300, b[1] * 72 / 300, b[2] * 72 / 300, b[3] * 72 / 300, text) for text, b, _, _ in found],
    )


def merged(year: int, number: int, layer: tuple[float, float, list[tuple]]) -> tuple[float, float, list[tuple]]:
    width, height, words = layer
    extra = []
    for x0, y0, x1, y1, text in ocr(year, number, width, height)[2]:
        text = text.strip(")(|[]{}!,;:'\"‘’`©“”*»«")
        if any(c.isdigit() for c in text):
            text = re.sub(r"[)|İi]+$", "", text.replace(")", "").replace("|", ""))
            if not re.fullmatch(r"\d+|\d{1,3}\.\d", text):
                continue
        elif not (
            re.fullmatch(r"[A-ZÇĞİÖŞÜ][a-zçğıöşü]+|[A-ZÇĞİÖŞÜ]\.", text) or text in PARTY_WORDS or text == "Bağımsız"
        ):
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(a - 1.5 <= cx <= c + 1.5 and b - 1.5 <= cy <= d + 1.5 for a, b, c, d, *_ in words):
            continue
        if any(t == text and abs((b + d) / 2 - cy) < 6 and abs((a + c) / 2 - cx) < 16 for a, b, c, d, t, *_ in words):
            continue
        extra.append((x0, y0, x1, y1, text, 1))
    return width, height, [*words, *extra]


PARTIES_1973 = ("AP", "CHP", "CGP", "DP", "MP", "MHP", "MSP", "TBP", "Bağımsızlar")


def problems(cells: dict[str, list]) -> list[tuple[str, str]]:
    valid = cells["valid"][1] if "valid" in cells else None
    found = []
    for label, cell in cells.items():
        if label not in PARTIES_1973:
            continue
        vote, share = cell[1], cell[2]
        if vote is None and share is not None:
            found.append((label, "vote"))
        elif vote is not None and share is None:
            found.append((label, "pct"))
        elif vote is not None and valid and abs(vote * 100 / valid - share) > 0.15:
            found.append((label, "both"))
    return found


def settled(cells: dict[str, list]) -> bool:
    found = problems(cells)
    votes = [cell[1] for label, cell in cells.items() if label in PARTIES_1973]
    valid = cells["valid"][1] if "valid" in cells else None
    return not found or (None not in votes and sum(votes) == valid)


def reread(year: int, page: int, crops: list[list[float]]) -> list[list[str]]:
    image = PARSED / "img" / f"{year}-{page}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        scan.rendered(raw.cached(SOURCE, url(year)).path, page, image.parent).rename(image)
    return scan.read(
        image,
        [
            ((round(a * 300 / 72) - 6, round(b * 300 / 72) - 6, round(c * 300 / 72) + 6, round(d * 300 / 72) + 6), True)
            for a, b, c, d in crops
        ],
    )


def readings(texts: list[str], kind: str) -> list[float]:
    out = []
    for text in texts:
        digits = re.sub(r"[^0-9.]", "", text)
        if kind == "pct":
            digits = digits.replace(".", "")
            if re.fullmatch(r"\d{2,4}", digits) and int(digits) <= 1000:
                out.append(int(digits) / 10)
        elif re.fullmatch(r"\d+", digits.replace(".", "")):
            out.append(int(digits.replace(".", "")))
    return out


def resolve(year: int, record: dict) -> dict:
    cells = {cell[0]: list(cell) for cell in record["cells"]}
    if settled(cells) or "valid" not in cells:
        record["unresolved"] = [] if settled(cells) else [label for label, _ in problems(cells)]
        return record
    wanted = []
    for label, kind in problems(cells):
        cell = cells[label]
        for part in ("vote", "pct"):
            if kind in (part, "both") or (kind == "pct" and part == "vote"):
                wanted.append((label, part, cell[3], cell[5][part]))
    if len(problems(cells)) == len([c for c in cells if c in PARTIES_1973]) or all(
        k == "both" for _, k in problems(cells)
    ):
        wanted.append(("valid", "vote", cells["valid"][3], cells["valid"][5]["vote"]))
    read: dict[tuple[str, str], list[float]] = {}
    for page in {w[2] for w in wanted}:
        texts = reread(year, page, [w[3] for w in wanted if w[2] == page])
        for (label, part, _, _), got in zip([w for w in wanted if w[2] == page], texts, strict=True):
            read[(label, part)] = readings(got, part)
    valids = [cells["valid"][1], *read.get(("valid", "vote"), [])]
    for valid in dict.fromkeys(v for v in valids if v):
        trial = {label: list(cell) for label, cell in cells.items()}
        trial["valid"][1] = valid
        for label, kind in problems(trial):
            cell = trial[label]
            votes = [cell[1], *read.get((label, "vote"), [])] if kind != "pct" else [cell[1]]
            shares = [cell[2], *read.get((label, "pct"), [])]
            fitting = [
                (v, p)
                for v in votes
                if v is not None
                for p in shares
                if p is not None and abs(v * 100 / valid - p) <= 0.15
            ]
            if fitting:
                cell[1], cell[2] = fitting[0]
            elif kind == "pct" and (cell[5]["ocr"] or cell[1] < 10):
                cell[1] = None
        if not problems(trial) or settled(trial):
            cells = trial
            break
    record["cells"] = [tuple(cell) for cell in cells.values()]
    record["unresolved"] = [] if settled(cells) else [label for label, _ in problems(cells)]
    return record


NOISE = re.compile(r"[^\wçğıöşüÇĞİÖŞÜâîûÂÎÛ().\- ]")


def tidy(text: str) -> tuple[str, str | None, bool]:
    bucak = "B.M" in text or "B. M" in text
    text = re.sub(r"\(\s*B\s*\.\s*M\s*\.?\s*\)", "", text)
    text = NOISE.sub(" ", text)
    alias = None
    found = re.search(r"\(([^)]+)\)", text)
    if found:
        alias = found.group(1).strip()
        text = text[: found.start()] + text[found.end() :]
    words = [w for w in text.split() if len(w) > 2 or w[:1].isupper()]
    words = [w[0].upper().replace("i", "İ") + w[1:] if w[0].islower() else w for w in words]
    return " ".join(words), alias, bucak


def parse() -> None:
    pdf = str(raw.cached(SOURCE, url(1973)).path)
    rows, values, mayors = [], [], []
    for table, (first, last) in TABLES_1973.items():
        got = {n: merged(1973, n, layer) for n, layer in pages(pdf, first, last).items()}
        plate, district, seq = 0, None, 0
        headers: dict = {}
        for left in range(first, last, 2):
            records = [
                resolve(1973, record) for record in spread(got[left], got[left + 1], table, (left, left + 1), headers)
            ]
            plain = [r["indent"] for r in records if "toplam" not in r["name"].lower()]
            base = min(plain) if plain else 0
            for record in records:
                lowered = record["name"].lower().replace(" ", "")
                cells = {cell[0]: cell for cell in record["cells"]}
                if "valid" not in cells and not any(label in cells for label in PARTIES_1973):
                    continue
                if record["name"].lstrip()[:1] in "(*" or lowered.startswith(("kısaltma", "not")):
                    continue
                if "toplam" in lowered or "topl" in lowered:
                    if "genel" in lowered or "türkiye" in lowered:
                        level, name, alias, bucak = "national", "Türkiye", None, False
                    else:
                        plate += 1
                        district = None
                        level, name, alias, bucak = "province", "", None, False
                else:
                    name, alias, bucak = tidy(record["name"])
                    if table == "il_genel" or record["indent"] <= base + 7:
                        level, district = "district", name
                    else:
                        level = "belde"
                seq += 1
                confirmed = not record["unresolved"]
                valid = cells["valid"][1] if "valid" in cells else None
                pre_ok = "?pre" not in cells and all(
                    cells.get(m, (0, None))[1] is not None for m in ("registered", "voters", "valid")
                )
                if pre_ok:
                    pre_ok = cells["registered"][1] >= cells["voters"][1] >= cells["valid"][1]
                rows.append(
                    {
                        "year": 1973,
                        "table": table,
                        "seq": seq,
                        "plate": plate,
                        "level": level,
                        "district": district if level == "belde" else None,
                        "name": name,
                        "alias": alias,
                        "bucak": bucak,
                        "unresolved": record["unresolved"],
                        "accounting": pre_ok,
                        "printed": record["name"],
                        "page": left,
                    }
                )
                for label, cell in cells.items():
                    if label in ("turnout", "?pre", "boxes") or cell[1] is None:
                        continue
                    if label in ("registered", "voters", "valid"):
                        if not pre_ok:
                            continue
                    elif not confirmed and not (
                        cell[2] is not None and valid and abs(cell[1] * 100 / valid - cell[2]) <= 0.15
                    ):
                        continue
                    values.append(
                        {
                            "year": 1973,
                            "table": table,
                            "seq": seq,
                            "column": label,
                            "value": cell[1],
                            "page": cell[3],
                            "x0": cell[4][0],
                            "y0": cell[4][1],
                            "x1": cell[4][2],
                            "y1": cell[4][3],
                        }
                    )
                if table == "belediye_baskani" and record["mayor"]:
                    mayors.append(
                        {
                            "year": 1973,
                            "seq": seq,
                            "mayor": record["mayor"],
                            "party": record["mayor_party"],
                            "page": record["mayor_box"][0],
                            "x0": record["mayor_box"][1][0],
                            "y0": record["mayor_box"][1][1],
                            "x1": record["mayor_box"][1][2],
                            "y1": record["mayor_box"][1][3],
                        }
                    )
        if plate != 67:
            print(f"{table}: {plate} province totals found, not 67")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(values, infer_schema_length=None).write_parquet(PARSED / "values.parquet")
    pl.DataFrame(mayors, infer_schema_length=None).write_parquet(PARSED / "mayors.parquet")
