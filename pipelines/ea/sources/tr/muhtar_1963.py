import difflib
import io
import itertools
import re
import struct
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from uuid import UUID

from PIL import Image

from ea import raw
from ea.sources.tr import die_local
from ea.sources.tr.ysk import DIGITIZED, PROVINCES, REGISTERED, VALID, VOTERS

SOURCE = "tr.muhtar_1963"
VERSION = "1"
PARSED = die_local.DATA / "parsed" / SOURCE
COLUMNS = ("population", "boxes", "registered", "voters", "valid")
FIRST, LAST = 16, 1794
REGISTER = Path(__file__).with_name("muhtar_1963_register.tsv")
HEAD = re.compile(r"sayı|nüfus|seçmen|muteber|sajn|ssy|nühı|kullanan|köylerde|listes", re.IGNORECASE)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("î", "i").replace("Î", "İ").replace("ı", "i").replace("İ", "I")
    return re.sub(r"[^a-zçğöşü ]", "", text.lower().replace("i̇", "i"))


def numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\d.,bilIİoOSı]+", text)) and sum(c.isdigit() for c in text) >= max(1, len(text) - 1)


def muhtar_page(height: float, words: list[tuple]) -> bool:
    head = "".join(t[4] for t in words if t[1] < height * 0.15).upper()
    return bool(re.search(r"M\w?\w?HTAR|HT[İIÎ]YAR|HEYET", head))


def forma(height: float, words: list[tuple]) -> int | None:
    head = " ".join(t[4] for t in sorted(words, key=lambda t: t[1]) if t[1] < height * 0.08)
    found = re.search(r"(?<!\d)(\d\s?\d)\s?/\s?\d", head)
    return int(found.group(1).replace(" ", "")) if found else None


def page_rows(width: float, height: float, words: list[tuple], before: list[float] | None = None) -> list[dict]:
    top = max((t[3] for t in words if t[1] < height * 0.3 and HEAD.search(t[4])), default=height * 0.2)
    body = sorted((t for t in words if t[1] > top + 2 and t[4].strip("•.,'<>|^-_—") != ""), key=lambda t: t[1] + t[3])
    lines: list[list[tuple]] = []
    for word in body:
        center = (word[1] + word[3]) / 2
        if lines and abs(center - median((w[1] + w[3]) / 2 for w in lines[-1])) <= 14:
            lines[-1].append(word)
        else:
            lines.append([word])
    ends = []
    for line in lines:
        numbers = sorted(t for t in line if numeric(t[4]) and t[0] > width * 0.3)
        for i, token in enumerate(numbers):
            later = numbers[i + 1] if i + 1 < len(numbers) else None
            if later is None or not (
                later[0] - token[2] <= 28 and re.fullmatch(r"\d{3}\.?", later[4].translate(die_local.DIGITS))
            ):
                ends.append(token[2])
    clusters: list[list[float]] = []
    for x in sorted(ends):
        if clusters and x - clusters[-1][-1] <= 12:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    rights = sorted(median(c) for c in sorted(clusters, key=len, reverse=True)[:5] if len(c) >= 2)
    if len(rights) == 4 and 360 < rights[1] - rights[0] < 430:
        rights.insert(1, rights[0] + 153)
    if len(rights) != 5:
        if before is None:
            raise ValueError(f"columns {rights}")
        rights = before
    out: list[dict] = []
    for line in lines:
        line = sorted(line)
        name = [t for t in line if t[2] < rights[0] - 150 and not (numeric(t[4]) and t[0] > width * 0.3)]
        numbers = [t for t in line if numeric(t[4]) and t[0] > width * 0.3]
        groups: list[list[tuple]] = []
        for token in numbers:
            text = token[4].translate(die_local.DIGITS)
            if groups and token[0] - groups[-1][-1][2] <= 28 and re.fullmatch(r"\d{3}\.?", text):
                groups[-1].append(token)
            else:
                groups.append([token])
        cells: dict[str, dict] = {}
        stray = []
        for group in groups:
            right = group[-1][2]
            index = min(range(5), key=lambda i: abs(rights[i] - right))
            if abs(rights[index] - right) > 30:
                stray.append(" ".join(t[4] for t in group))
                continue
            text = " ".join(t[4].translate(die_local.DIGITS).strip(".,") for t in group)
            value = int(text.replace(" ", "")) if re.fullmatch(r"\d{1,3}( \d{3})*|\d+", text) else None
            label = COLUMNS[index]
            if label in cells:
                stray.append(" ".join(t[4] for t in group))
                cells[label]["value"] = None
                continue
            cells[label] = {
                "value": value,
                "text": " ".join(t[4] for t in group),
                "box": [
                    min(t[0] for t in group),
                    min(t[1] for t in group),
                    max(t[2] for t in group),
                    max(t[3] for t in group),
                ],
            }
        label = " ".join(t[4] for t in name).strip(" •.,'<>|-_")
        if not cells and not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", label):
            continue
        out.append(
            {
                "name": unicodedata.normalize("NFC", label),
                "y": [min(t[1] for t in line), max(t[3] for t in line)],
                "cells": cells,
                "stray": stray,
                "rights": rights,
                "size": [width, height],
            }
        )
    merged: list[dict] = []
    for row in out:
        if (
            merged
            and not merged[-1]["cells"]
            and row["cells"]
            and not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", row["name"])
            and row["y"][0] - merged[-1]["y"][1] < 40
        ):
            merged[-1].update(
                cells=row["cells"], y=[merged[-1]["y"][0], row["y"][1]], stray=merged[-1]["stray"] + row["stray"]
            )
        elif (
            merged
            and merged[-1]["cells"]
            and not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", merged[-1]["name"])
            and not row["cells"]
            and row["y"][0] - merged[-1]["y"][1] < 40
        ):
            merged[-1].update(name=row["name"], y=[merged[-1]["y"][0], row["y"][1]])
        elif (
            merged
            and not merged[-1]["cells"]
            and (
                re.fullmatch(r"(?i)top\S*", row["name"])
                or merged[-1]["name"].count("(") > merged[-1]["name"].count(")")
            )
        ):
            merged[-1].update(
                name=f"{merged[-1]['name']} {row['name']}", cells=row["cells"], y=[merged[-1]["y"][0], row["y"][1]]
            )
        else:
            merged.append(row)
    return merged


def kind(name: str) -> str:
    text = fold(name)
    words = text.split()
    if re.search(r"ucakvek|bucakv", text.replace(" ", "")):
        return "rural"
    if re.search(r"buca[gğ]\w*\b", text) and (re.search(r"top", text) or re.search(r"buca[gğ][iı]\b", text)):
        return "bucak"
    if any(re.fullmatch(r"\S{0,2}[çc]es[iı]", word) for word in words[1:]) or re.search(r"[çc]es[iı] ?top", text):
        return "ilce"
    if difflib.SequenceMatcher(None, text.replace(" ", "")[:5], "sehir").ratio() >= 0.6 and len(words) <= 2:
        return "sehir"
    return "koy"


def book() -> str:
    return str(raw.cached(die_local.SOURCE, die_local.url(1963)).path)


def tree(rows: list[dict]) -> list[dict]:
    plates: list[dict] = []
    for index, row in enumerate(rows):
        following = rows[index + 1] if index + 1 < len(rows) else None
        if not plates or plates[-1]["plate"] != row["plate"]:
            plates.append({"plate": row["plate"], "total": row, "ilces": [], "summary": []})
            continue
        place = plates[-1]
        role = row["kind"]
        if (
            place["total"] is rows[index - 1]
            and not place["total"]["cells"]
            and row["cells"]
            and role not in ("ilce", "sehir")
        ):
            place["total"] = row
            continue
        after = rows[index + 2] if index + 2 < len(rows) else None
        if (
            role in ("bucak", "koy")
            and following is not None
            and after is not None
            and following["kind"] == "sehir"
            and after["kind"] == "rural"
        ):
            role = "ilce"
        if (
            role == "ilce"
            and place["ilces"]
            and following is not None
            and following["kind"] == "koy"
            and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", following["name"])
        ):
            role = "bucak"
        if role == "rural" and place["ilces"]:
            last = place["ilces"][-1]
            if (
                (last["rural"] is not None or last["bucaks"])
                and last["bucaks"]
                and len(last["bucaks"][-1]["koys"]) >= 2
            ):
                sehir = last["bucaks"][-1]["koys"].pop()
                total = last["bucaks"][-1]["koys"].pop()
                place["ilces"].append({"total": total, "sehir": sehir, "rural": row, "bucaks": []})
                continue
        if role == "bucak" and "top" not in fold(row["name"]):
            size = options(row, "registered")
            if not size or size[0] < 1500:
                role = "koy"
        if (
            role == "ilce"
            and following is not None
            and following["kind"] == "ilce"
            and following["plate"] == row["plate"]
        ):
            place["summary"].append(row)
            continue
        if role == "ilce":
            place["ilces"].append({"total": row, "sehir": None, "rural": None, "bucaks": []})
            continue
        if not place["ilces"]:
            place["summary"].append(row)
            continue
        ilce = place["ilces"][-1]
        if role == "rural" and ilce["rural"] is None and not ilce["bucaks"]:
            ilce["rural"] = row
        elif role == "rural":
            place["summary"].append(row)
        elif ilce["rural"] is None and ilce["sehir"] is None and not ilce["bucaks"] and role in ("sehir", "koy"):
            ilce["sehir"] = row
        elif role == "bucak":
            ilce["bucaks"].append({"total": row, "koys": []})
        else:
            if not ilce["bucaks"]:
                ilce["bucaks"].append({"total": None, "koys": []})
            ilce["bucaks"][-1]["koys"].append(row)
    return plates


SCALE = 96 / 72


def image(number: int) -> Image.Image:
    target = PARSED / "img" / f"{number}.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "96",
                "-gray",
                "-png",
                "-f",
                str(number),
                "-l",
                str(number),
                "-singlefile",
                book(),
                str(target.with_suffix("")),
            ],
            check=True,
        )
    return Image.open(target).convert("L")


def strip_reads(number: int, rows: list[dict]) -> None:
    if not rows:
        return
    gray = image(number)
    rights = rows[0]["rights"]
    top = min(r["y"][0] for r in rows) - 20
    bottom = max(r["y"][1] for r in rows) + 20
    for index, label in enumerate(COLUMNS):
        left = max(rights[index - 1] + 20, rights[index] - 140) if index else rights[0] - 160
        for scale, threshold in ((2, None), (3, 165)):
            crop = gray.crop(
                (int(left * SCALE), int(top * SCALE), int((rights[index] + 14) * SCALE), int(bottom * SCALE))
            )
            if threshold is not None:
                crop = crop.point(lambda v, t=threshold: 0 if v < t else 255)
            crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            crop.save(buffer, "PNG")
            tsv = subprocess.run(
                [
                    "tesseract",
                    "stdin",
                    "stdout",
                    "--psm",
                    "6",
                    "-l",
                    "eng",
                    "-c",
                    "tessedit_char_whitelist=0123456789",
                    "tsv",
                ],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
            ).stdout.decode()
            found: dict[tuple, list] = {}
            for line in tsv.splitlines()[1:]:
                f = line.split("\t")
                if len(f) >= 12 and f[0] == "5" and f[11].strip():
                    found.setdefault((f[2], f[3], f[4]), []).append((int(f[6]), int(f[7]), int(f[9]), f[11].strip()))
            centers = [(r["y"][0] + r["y"][1]) / 2 for r in rows]
            for words in found.values():
                y = top + sum(w[1] + w[2] / 2 for w in words) / len(words) / scale / SCALE
                nearest = min(range(len(rows)), key=lambda i: abs(centers[i] - y))
                if abs(centers[nearest] - y) > 18:
                    continue
                text = " ".join(w[3] for w in sorted(words))
                value = int(text.replace(" ", "")) if re.fullmatch(r"\d{1,3}( \d{3})*|\d+", text) else None
                rows[nearest].setdefault("reads", {}).setdefault(label, []).append(value)


def options(row: dict | None, label: str) -> list[int]:
    if row is None:
        return []
    if label in row.get("final", {}):
        return [row["final"][label][0]]
    found = [row["cells"].get(label, {}).get("value"), *row.get("reads", {}).get(label, [])]
    tally = Counter(v for v in found if v is not None)
    return [v for v, _ in tally.most_common()]


def agreed(row: dict, label: str) -> int | None:
    found = {
        v for v in [row["cells"].get(label, {}).get("value"), *row.get("reads", {}).get(label, [])] if v is not None
    }
    reads = [
        v for v in [row["cells"].get(label, {}).get("value"), *row.get("reads", {}).get(label, [])] if v is not None
    ]
    return reads[0] if len(found) == 1 and len(reads) >= 2 else None


def balance(total: dict | None, parts: list[dict], label: str) -> tuple[int | None, list[int | None]] | None:
    heads = options(total, label)
    parts = [part for part in parts if any(options(part, column) for column in COLUMNS)]
    choices = [options(part, label) for part in parts]
    if not heads or any(not c for c in choices):
        return None
    loose = [i for i, c in enumerate(choices) if len(c) > 1]
    if len(loose) > 10:
        return None
    hits = set()
    for picks in itertools.product(*(choices[i][:3] for i in loose)):
        values = [c[0] for c in choices]
        for i, value in zip(loose, picks, strict=True):
            values[i] = value
        for head in heads[:3]:
            if sum(values) == head:
                hits.add((head, tuple(values)))
        if len(hits) > 1:
            return None
    if len(hits) != 1:
        return None
    head, values = next(iter(hits))
    return head, list(values)


def check(total: dict | None, parts: list[dict]) -> None:
    for label in COLUMNS:
        settled = balance(total, parts, label)
        if settled is None:
            continue
        head, values = settled
        if total is not None:
            total.setdefault("final", {})[label] = (head, "sum")
        for part, value in zip(
            [part for part in parts if any(options(part, column) for column in COLUMNS)], values, strict=True
        ):
            if value is not None and label not in part.get("final", {}):
                part.setdefault("final", {})[label] = (value, "sum")


def settle(plates: list[dict]) -> None:
    for place in plates:
        for ilce in place["ilces"]:
            groups = ilce["bucaks"]
            for group in groups:
                check(group["total"] if group["total"] is not None or len(groups) > 1 else ilce["rural"], group["koys"])
            if all(group["total"] is not None for group in groups):
                check(ilce["rural"], [group["total"] for group in groups])
            check(ilce["total"], [row for row in (ilce["sehir"], ilce["rural"]) if row is not None])
        check(place["total"], [ilce["total"] for ilce in place["ilces"]])
        rows = [place["total"]] + [
            row
            for ilce in place["ilces"]
            for row in (
                ilce["total"],
                ilce["sehir"],
                ilce["rural"],
                *(g["total"] for g in ilce["bucaks"]),
                *(k for g in ilce["bucaks"] for k in g["koys"]),
            )
            if row is not None
        ]
        for row in rows:
            for label in COLUMNS:
                if label not in row.get("final", {}) and (value := agreed(row, label)) is not None:
                    row.setdefault("final", {})[label] = (value, "agreed")
            final = row.get("final", {})
            counts = [final.get(label, (None, None))[0] for label in ("registered", "voters", "valid")]
            if None not in counts and not counts[0] >= counts[1] >= counts[2]:
                for label in ("registered", "voters", "valid"):
                    if final[label][1] == "agreed":
                        del final[label]


def name_read(number: int, rows: list[dict]) -> None:
    gray = image(number)
    right = rows[0]["rights"][0] - 165
    for row in rows:
        crop = gray.crop(
            (int(150 * SCALE), int((row["y"][0] - 8) * SCALE), int(right * SCALE), int((row["y"][1] + 8) * SCALE))
        )
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
        text = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "tur"],
            input=buffer.getvalue(),
            capture_output=True,
            check=True,
        ).stdout.decode()
        row["read"] = unicodedata.normalize("NFC", " ".join(text.split()))


CLEAN = re.compile(r"[A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû]+(?:[ .()\-][A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû()]*)*")


def tidy(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("î", "i").replace("Î", "İ").replace("\xad", "")
    text = re.sub(r"\(\s*[*x]+\s*[)J]|\[\s*x\s*\]|\*+|\bB\s*[.,]\s*M\w?\b\.?", " ", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"(\s+[^\sA-Za-zÇĞİÖŞÜçğıöşü(]+|\s+[a-zçğıöşüA-Z]\b)+$", "", " ".join(text.split()))
    text = text.strip(" .,'^~=<>|-_")
    if re.match(r"t[a-zçğıöşü]", text):
        text = "İ" + text[1:]
    if text[:1] in "iıöüçşğ" and text[:1]:
        text = {"i": "İ", "ı": "I"}.get(text[0], text[0].upper()) + text[1:]
    return text


def clean(text: str) -> bool:
    return bool(CLEAN.fullmatch(text)) and not re.search(r"(^| )[a-zçğıöşü]|[a-zçğıöşü][A-ZÇĞİÖŞÜ]", text)


def label(row: dict) -> str:
    for text in (row["name"], row.get("read", "")):
        if clean(tidy(text)):
            return tidy(text)
    return tidy(re.sub(r"[^A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû ()\-]", "", row["name"]))


def stem(text: str) -> str:
    words = re.sub(r"[^A-Za-zÇĞİÖŞÜÎçğıöşüî' ]", " ", text).split()
    words = [w for w in words if not re.match(r"top|ton|.{0,3}[çc]e[sc][iı]|buca|i?l[iı]?$|[şs]eh[iı]r", fold(w))]
    kept = [re.sub(r"[iİı]?[lt][çc]es[iı]$", "", w) if len(w) > 8 else w for w in words if len(w) > 1]
    return " ".join(kept).strip()


def parse() -> None:
    import json
    from concurrent.futures import ThreadPoolExecutor

    import polars as pl

    got = die_local.pages(book(), FIRST, LAST)
    plate, plates, spans = 0, {}, {}
    for number in sorted(got):
        width, height, words = got[number]
        found = forma(height, words)
        if found is not None and found in (plate, plate + 1):
            plate = found
        plates[number] = plate
        if muhtar_page(height, words):
            spans.setdefault(plate, []).append(number)
    cache = PARSED / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    jobs = []
    rights = None
    for plate, numbers in spans.items():
        for number in range(min(numbers), max(numbers) + 1):
            width, height, words = got[number]
            found = page_rows(width, height, words, rights)
            if found:
                rights = found[0]["rights"]
            jobs.append((plate, number, found))

    def read(job: tuple[int, int, list[dict]]) -> list[dict]:
        plate, number, found = job
        target = cache / f"{number}.json"
        if target.exists():
            return json.loads(target.read_text())
        for row in found:
            row.update(page=number, plate=plate, kind=kind(row["name"]))
        if found:
            strip_reads(number, found)
            name_read(number, found)
        target.write_text(json.dumps(found, ensure_ascii=False))
        return found

    with ThreadPoolExecutor(10) as pool:
        rows = [row for found in pool.map(read, jobs) for row in found]
    places = tree(rows)
    settle(places)
    out, register = [], []
    for place in places:
        listed = [("il", None, None, place["total"])]
        for ilce in place["ilces"]:
            district = stem(label(ilce["total"])) or "Merkez"
            listed += [("ilce", district, None, ilce["total"])]
            listed += [(kind_, district, None, ilce[kind_]) for kind_ in ("sehir", "rural") if ilce[kind_] is not None]
            for group in ilce["bucaks"]:
                bucak = stem(label(group["total"])) if group["total"] is not None else None
                if group["total"] is not None:
                    listed.append(("bucak", district, bucak, group["total"]))
                listed += [("koy", district, bucak, row) for row in group["koys"]]
        for seq, (level, district, bucak, row) in enumerate(listed):
            width, height = row["size"]
            final = row.get("final", {})
            record = {
                "plate": place["plate"],
                "seq": seq,
                "level": level,
                "ilce": district,
                "bucak": bucak,
                "name": label(row) if level == "koy" else None,
                "printed": row["name"],
                "page": row["page"],
            }
            for column, index in zip(COLUMNS, range(5), strict=True):
                value, status = final.get(column, (None, None))
                record[column] = value
                record[f"{column}_status"] = status
                cell = row["cells"].get(column)
                rights = row["rights"]
                left = (
                    cell["box"][0]
                    if cell
                    else (max(rights[index - 1] + 20, rights[index] - 140) if index else rights[0] - 160)
                )
                right = cell["box"][2] if cell else rights[index] + 6
                record[f"{column}_box"] = [
                    round(max(left - 4, 0) / width, 6),
                    round(max(row["y"][0] - 4, 0) / height, 6),
                    round(min(right + 4, width) / width, 6),
                    round(min(row["y"][1] + 4, height) / height, 6),
                ]
            out.append(record)
            if level == "koy":
                register.append(record)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(register, infer_schema_length=None).with_columns(
        il=pl.col("plate").map_elements(lambda n: PROVINCES[n - 1], return_dtype=pl.String)
    ).select("plate", "il", "ilce", "bucak", "name", "printed", "page", *COLUMNS).write_csv(REGISTER, separator="\t")


DAY = date(1963, 11, 17)
CONTEST = f"{DAY}:muhtars"
MEASURES = {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}
METHOD = (
    "From section D of the State Institute of Statistics' book of the 17 November 1963 local elections, which prints "
    "for each province, ilçe, bucak, and köy the registered voters, voters, and valid votes of the muhtar and "
    "ihtiyar heyeti elections, with no candidates' names or votes. Each city is one row for all its mahalles. Every "
    "count was read from the book's text layer and twice more by optical recognition of the page image. A count is "
    "taken where the köy counts add up to their bucak's printed total, the bucak totals to the ilçe's, and the ilçe "
    "totals to the province's, or else where all three readings agree; any other count is left out."
)
UNREAD = "{count:,} counts in this table could not be confirmed against the printed totals and are left out."
LEVELS = {"il": "by-province", "ilce": "by-district", "sehir": "by-village", "koy": "by-village"}


def emit() -> Path:
    import polars as pl

    from ea.changeset import Changeset
    from ea.sources.tr.tuik_secim import norm
    from ea.store import connect

    rows = pl.read_parquet(PARSED / "rows.parquet")
    old_rows = pl.read_parquet(die_local.PARSED / "rows.parquet").filter(pl.col("year") == 1963)
    old_values = (
        pl.read_parquet(die_local.PARSED / "values.parquet")
        .filter(pl.col("year") == 1963)
        .with_columns(pl.col("x0", "y0").round(3))
        .unique(["page", "x0", "y0"])
        .join(old_rows.select("table", "seq", "plate", "name", "level"), on=["table", "seq"])
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 17 November 1963 muhtar elections: the köys and their turnout by province, ilçe, and "
            "köy, from the State Institute of Statistics",
            polity="TR",
        )
        turkey = cs.polity("TR")
        book_file = raw.cached(die_local.SOURCE, die_local.url(1963))
        source = cs.source(die_local.PUBLISHER, die_local.TITLE_1963)
        if source in cs.minted:
            raise ValueError("publish tr.die_local before tr.muhtar_1963")
        held = conn.execute(
            "select id from source_artifact where source_id = %s and sha256 = %s", [source, book_file.sha256]
        ).fetchone()
        if held is None:
            raise ValueError("the 1963 book is not in the archive")
        artifact = held[0]
        located = pl.DataFrame(
            conn.execute(
                """select p.page_number, round(l.x0::numeric, 3)::float8, round(l.y0::numeric, 3)::float8,
                          u.id::text, u.area_id::text, x.value
                   from value_locator l
                   join result_set rs on rs.k = l.result_set_k
                   join external_id x on x.entity_id = rs.id and x.scheme = 'tr.die_local.result_set'
                   join source_page p on p.id = l.page_id
                   join reporting_unit u on u.k = l.unit_k
                   where x.value in ('1963-11-17:provincial-councils:by-district', '1963-11-17:mayors:by-municipality')""",
            ).fetchall(),
            schema=["page", "x0", "y0", "unit", "area", "set"],
            orient="row",
        ).join(old_values, on=["page", "x0", "y0"])

        def key(text: str) -> str:
            return norm(re.sub(r"\s*[İIi]l[cç]es[iı]\s*$", "", text or ""))

        units: dict[str, dict[tuple[int, str], tuple[str, str | None]]] = {"ilce": {}, "sehir": {}}
        order: dict[tuple[str, int], list[tuple[str, str | None]]] = defaultdict(list)
        for record in located.filter(pl.col("level") == "district").sort("seq").iter_rows(named=True):
            role = "ilce" if record["table"] == "il_genel" else "sehir"
            held = (record["unit"], record["area"])
            if held not in order[role, record["plate"]]:
                order[role, record["plate"]].append(held)
                units[role].setdefault((record["plate"], "merkez"), held)
            units[role][record["plate"], key(record["name"])] = held
        wanted = rows.filter(pl.col("level").is_in(["ilce", "sehir"])).sort("plate", "seq")
        ordinal = {
            (role, plate, seq): index
            for (role, plate), part in wanted.group_by("level", "plate")
            for index, seq in enumerate(part.sort("seq")["seq"])
        }
        counts = {(role, plate): part.height for (role, plate), part in wanted.group_by("level", "plate")}

        def find(role: str, plate: int, text: str, seq: int) -> tuple[str, str | None] | None:
            table = units[role]
            if (plate, key(text)) in table:
                return table[plate, key(text)]
            scored = sorted(
                (difflib.SequenceMatcher(None, key(text), other).ratio(), other) for at, other in table if at == plate
            )
            if scored and scored[-1][0] >= 0.8 and (len(scored) < 2 or scored[-2][0] < scored[-1][0]):
                return table[plate, scored[-1][1]]
            listed = order[role, plate]
            if counts.get((role, plate)) == len(listed):
                return listed[ordinal[role, plate, seq]]
            return None

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        election = cs.key("tr.election", f"{DAY}:local")
        if election in cs.minted:
            raise ValueError("publish tr.ysk_local before tr.muhtar_1963")
        office = cs.key("tr.office", "local-muhtars")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=turkey, kind="other")
            name(office, "Muhtars", "en")
            name(office, "Muhtarlık", kind="native")
        contest = cs.key("tr.contest", CONTEST)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            office_id=office,
            kind="body_seats",
            district_area_id=cs.area("tr.nation", "TR"),
            magnitude=None,
            round_number=1,
            default_result_kind="counted",
        )
        name(contest, "Muhtars", "en")
        name(contest, "Muhtar ve İhtiyar Heyeti Seçimi", kind="native")
        cs.add("slug", path=f"tr/{DAY}-local/muhtars", entity_id=contest)
        cs.add("note", entity_id=contest, kind="methodology", body_md=METHOD)
        sets = {}
        for level in ("by-province", "by-district", "by-village"):
            sets[level] = cs.key(f"{SOURCE}.result_set", f"{CONTEST}:{level}")
            cs.add(
                "result_set",
                id=sets[level],
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=level == "by-province",
                completeness=1,
            )
            cs.add("note", entity_id=sets[level], kind="methodology", body_md=METHOD)
        page_ids: dict[int, UUID] = {}
        cells, locators = [], []
        unread = Counter()
        missing: list[str] = []
        codes: set[str] = set()
        current: dict[str, tuple[str, str | None] | None] = {}
        placed = {
            (row["level"], row["plate"], row["seq"]): find(row["level"], row["plate"], row["ilce"], row["seq"])
            for row in wanted.iter_rows(named=True)
        }
        claims = Counter(found for found in placed.values() if found is not None)
        placed = {at: found if found is not None and claims[found] == 1 else None for at, found in placed.items()}
        for row in rows.sort("plate", "seq").iter_rows(named=True):
            level, plate = row["level"], row["plate"]
            if level not in LEVELS:
                continue
            if level == "il":
                unit = cs.unit("tr.il", f"{plate:02}")
            elif level == "ilce":
                current["ilce"] = placed["ilce", plate, row["seq"]]
                if current["ilce"] is None:
                    missing.append(f"{plate:02} {row['ilce']} (ilçe)")
                    continue
                unit = UUID(current["ilce"][0])
            elif level == "sehir":
                found = placed["sehir", plate, row["seq"]]
                if found is None:
                    missing.append(f"{plate:02} {row['ilce']} (şehir)")
                    continue
                unit = UUID(found[0])
            else:
                text = row["name"] or row["printed"]
                if all(row[column] is None for column in COLUMNS):
                    continue
                if not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{2}", text or ""):
                    missing.append(f"{plate:02} {row['ilce']} p. {row['page']} (no name)")
                    continue
                base = f"1963:{plate:02}:{row['ilce']}:{row['bucak'] or '-'}:{text}"
                code, copy = base, 1
                while code in codes:
                    copy += 1
                    code = f"{base}:{copy}"
                codes.add(code)
                unit = cs.unit("tr.mahalle", code)
                held = current.get("ilce")
                area = UUID(held[1]) if held is not None and held[1] is not None else cs.area("tr.il", f"{plate:02}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=turkey,
                        scheme_code="tr.mahalle",
                        code=code,
                        parent_id=None,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                    )
                    name(unit, text)
            for column, measure in MEASURES.items():
                value = row[column]
                if value is None:
                    unread[LEVELS[level]] += 1
                    continue
                number = row["page"]
                if number not in page_ids:
                    page_ids[number] = cs.page(artifact, number)
                    if page_ids[number] in cs.minted:
                        image = die_local.PAGES / f"{book_file.sha256}-{number}.png"
                        if not image.exists():
                            die_local.PAGES.mkdir(parents=True, exist_ok=True)
                            subprocess.run(
                                [
                                    "pdftoppm",
                                    "-r",
                                    "100",
                                    "-png",
                                    "-f",
                                    str(number),
                                    "-l",
                                    str(number),
                                    "-singlefile",
                                    str(book_file.path),
                                    str(image.with_suffix("")),
                                ],
                                check=True,
                            )
                        width, height = struct.unpack(">II", image.read_bytes()[16:24])
                        cs.add(
                            "source_page",
                            id=page_ids[number],
                            artifact_id=artifact,
                            page_number=number,
                            image_key=f"pages/{die_local.SOURCE}/{image.name}",
                            width=width,
                            height=height,
                        )
                fact = {
                    "result_set_id": str(sets[LEVELS[level]]),
                    "unit_id": str(unit),
                    "option_id": None,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                }
                box = row[f"{column}_box"]
                cells.append({**fact, "flags": DIGITIZED, "value": value})
                locators.append(
                    {**fact, "page_id": str(page_ids[number]), "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]}
                )
        for level, count in unread.items():
            cs.add("note", entity_id=sets[level], kind="caveat", body_md=UNREAD.format(count=count))
        print("not placed:", *missing, sep="\n  ")
        cs.frame(
            "result_value",
            pl.DataFrame(cells, infer_schema_length=None).with_columns(
                pl.col("channel", "measure", "count_round", "flags").cast(pl.Int16)
            ),
        )
        cs.frame(
            "value_locator",
            pl.DataFrame(locators, infer_schema_length=None).with_columns(
                pl.col("channel", "measure", "count_round").cast(pl.Int16), source_id=pl.lit(None, pl.String)
            ),
        )
        return cs.write()
