import difflib
import html
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import numpy as np
import polars as pl
from PIL import Image

from ea import raw
from ea.sources.tr.muhtar_1963_links import RENAMES, fold

SOURCE = "tr.koylerimiz_1933"
URL = "https://acikerisim.tbmm.gov.tr/server/api/core/bitstreams/4fa70aa0-9537-4c18-b776-fb75a93b2bdc/content"
REGISTER = Path(__file__).with_name("koylerimiz_1933.tsv")
FIRST, LAST = 8, 813
PROVINCES = {
    "Afyonkarahisar": ["03"],
    "Amasya": ["05"],
    "Ankara": ["06", "40"],
    "Antalya": ["07"],
    "Aydın": ["09"],
    "Balıkesir": ["10"],
    "Bayazıt": ["04", "36"],
    "Bilecik": ["11"],
    "Bolu": ["14"],
    "Burdur": ["15"],
    "Bursa": ["16"],
    "Çanakkale": ["17"],
    "Çankırı": ["18"],
    "Çoruh": ["08", "53"],
    "Çorum": ["19"],
    "Denizli": ["20"],
    "Diyarbekir": ["21"],
    "Edirne": ["22"],
    "Elâzız": ["23", "62", "12"],
    "Erzincan": ["24", "62"],
    "Erzurum": ["25", "12", "24"],
    "Eskişehir": ["26"],
    "Gaziantep": ["27", "31"],
    "Giresun": ["28"],
    "Gümüşane": ["29"],
    "İçel": ["33"],
    "Isparta": ["32"],
    "İstanbul": ["34"],
    "İzmir": ["35", "09"],
    "Kars": ["36"],
    "Kastamonu": ["37", "19"],
    "Kayseri": ["38", "50"],
    "Kırklareli": ["39"],
    "Kırşehir": ["40", "50", "06", "66"],
    "Kocaeli": ["41", "54"],
    "Konya": ["42"],
    "Kütahya": ["43", "64"],
    "Malatya": ["44", "02", "23", "24"],
    "Manisa": ["45"],
    "Maraş": ["46"],
    "Mardin": ["47"],
    "Muğla": ["48"],
    "Muş": ["49", "13", "12", "04", "56"],
    "Niğde": ["51", "50"],
    "Ordu": ["52"],
    "Samsun": ["55"],
    "Seyhan": ["01", "31"],
    "Siirt": ["56"],
    "Sinop": ["57"],
    "Sivas": ["58"],
    "Tekirdağ": ["59"],
    "Tokat": ["60"],
    "Trabzon": ["61"],
    "Urfa": ["63"],
    "Van": ["65", "30"],
    "Yozgat": ["66"],
    "Zonguldak": ["67"],
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE = re.compile(r"<page [^>]*>(.*?)</page>", re.DOTALL)
LETTERS = re.compile(r"[A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû]")
STAR = re.compile(r"^[=*!?»#¥^•■:;'\"i\-]*\*[=*!?»#¥^•■:;'\"i\-]*$|^=[!?i*]=?$|^[!?i]=$|^[»¥]$|^=$")
HYPHEN = ("-", "\u00ad", "—")
WINDOWS = ((22, 58), (92, 125), (130, 175), (176, 222), (248, 290), (286, 335))


def turkish_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


def key(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = text.translate(str.maketrans("ığüşöçâîû/", "igusocaiuz"))
    return re.sub(r"[^a-z]", "", text)


PROVINCE_KEYS = {key(name): name for name in PROVINCES} | {
    "gaziantep": "Gaziantep",
    "antep": "Gaziantep",
    "elaziz": "Elâzız",
    "diyarbakir": "Diyarbekir",
    "gumusane": "Gümüşane",
    "afyon": "Afyonkarahisar",
}


def variants(folded: str) -> set[str]:
    out = {folded, folded.replace("li", "h"), folded.replace("ii", "u"), re.sub(r"^[li]", "u", folded)}
    return out | {re.sub(r"^[a-z]", "", v) for v in out}


def ranked(text: str) -> list[tuple[float, str]]:
    folded = key(text)
    if len(folded) < 2:
        return []
    best: dict[str, float] = {}
    for variant in variants(folded):
        for k, name in PROVINCE_KEYS.items():
            score = 1.0 if variant == k else difflib.SequenceMatcher(None, variant, k).ratio()
            if variant != folded:
                score -= 0.03
            best[name] = max(best.get(name, 0), score)
    return sorted(((score, name) for name, score in best.items()), reverse=True)


def province(text: str) -> str | None:
    scored = ranked(text)
    if not scored:
        return None
    if key(text) in PROVINCE_KEYS:
        return PROVINCE_KEYS[key(text)]
    (best, name), runner = scored[0], scored[1][0]
    return name if best >= 0.62 and best - runner >= 0.08 else None


def pages(path: Path) -> list[list[tuple[float, float, float, float, str]]]:
    text = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [
        [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(body)]
        for body in PAGE.findall(text)
    ]


def joined(words: list[tuple]) -> str:
    out = ""
    for index, word in enumerate(words):
        if index and word[0] - words[index - 1][2] > 2.6:
            out += " "
        out += word[4]
    return out


def lines(words: list[tuple]) -> list[dict]:
    rows: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        middle = (word[1] + word[3]) / 2
        if rows and abs(middle - sum((w[1] + w[3]) / 2 for w in rows[-1]) / len(rows[-1])) < 3.2:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [
        {
            "y": sum((w[1] + w[3]) / 2 for w in row) / len(row),
            "x": min(w[0] for w in row),
            "words": sorted(row),
        }
        for row in rows
    ]


def peaks(words: list[tuple]) -> list[float] | None:
    counts = Counter(round(w[0]) for w in words if LETTERS.search(w[4]) or w[4] in ("—", "-", "_"))
    found = []
    for index, (lo, hi) in enumerate(WINDOWS):
        smooth = {x: sum(counts.get(x + d, 0) for d in range(-2, 3)) for x in range(lo, hi)}
        top = max(smooth.values())
        if top < 6:
            return None
        best = next(
            x
            for x in range(lo, hi)
            if smooth[x] == top or (index % 3 == 0 and smooth[x] >= top / 2 and smooth[x] >= smooth[min(x + 1, hi - 1)])
        )
        found.append(float(min(x for x in range(best - 2, best + 3) if counts.get(x))))
    return found


def align(left: list[float], right: list[float], skip: float = 7.0) -> list[tuple[int, int]]:
    offset = 0.0
    pairs: list[tuple[int, int]] = []
    for _ in range(2):
        n, m = len(left), len(right)
        cost = [[0.0] * (m + 1) for _ in range(n + 1)]
        move = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            cost[i][0], move[i][0] = cost[i - 1][0] + skip, 1
        for j in range(1, m + 1):
            cost[0][j], move[0][j] = cost[0][j - 1] + skip, 2
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                options = (
                    (cost[i - 1][j - 1] + min(abs(left[i - 1] - right[j - 1] - offset), 3 * skip), 0),
                    (cost[i - 1][j] + skip, 1),
                    (cost[i][j - 1] + skip, 2),
                )
                cost[i][j], move[i][j] = min(options)
        pairs = []
        i, j = n, m
        while i and j:
            if move[i][j] == 0:
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif move[i][j] == 1:
                i -= 1
            else:
                j -= 1
        pairs.reverse()
        if not pairs:
            break
        diffs = sorted(left[a] - right[b] for a, b in pairs)
        offset = diffs[len(diffs) // 2]
    return pairs


def lettered(words: list[tuple]) -> list[tuple]:
    return [w for w in words if LETTERS.search(w[4]) and not STAR.match(w[4])]


def entries(name_lines: list[dict], name_x: float, anchors: list[float]) -> list[dict]:
    out: list[dict] = []
    starts = [(line["y"], min(w[0] for w in lettered(line["words"]))) for line in name_lines if lettered(line["words"])]
    for line in name_lines:
        words = [w for w in line["words"] if not (w[0] < name_x - 2 and len(w[4]) <= 1)]
        text = joined(words).strip()
        named = lettered(words)
        previous = out[-1] if out else None
        if not named:
            if previous is not None:
                previous["marks"].append(text)
            continue
        window = sorted(x for y, x in starts if abs(y - line["y"]) < 60)
        base = window[len(window) // 4] if window else name_x
        indented = min(w[0] for w in named) > base + 5
        hyphened = previous is not None and previous["text"].rstrip(" *=!?»i").endswith(HYPHEN)
        waiting = previous is not None and not any(abs(y - previous["y"]) < 3.5 for y in anchors)
        if previous is not None and (hyphened or (indented and waiting)) and line["y"] - previous["y"] < 16:
            previous["parts"].append(words)
            previous["text"] += " " + text
            previous["y"] = line["y"]
            continue
        out.append({"parts": [words], "text": text, "y": line["y"], "marks": []})
    return out


def clean(entry: dict) -> tuple[str, str]:
    marks = list(entry["marks"])
    pieces: list[str] = []
    broken = False
    for index, part in enumerate(entry["parts"]):
        marks += [w[4] for w in part if STAR.match(w[4])] + [
            "*" * w[4].count("*") for w in part if LETTERS.search(w[4])
        ]
        named = lettered(part)
        last = index == len(entry["parts"]) - 1
        if last and len(named) > 1 and len(named[-1][4]) == 1 and named[-1][0] - named[-2][2] > 3:
            marks.append(named.pop()[4])
        text = joined(named).strip().rstrip("".join(HYPHEN)).rstrip()
        pieces.append(text if broken or not pieces else " " + text)
        broken = joined([w for w in part if not STAR.match(w[4])]).strip().rstrip(" =!?»i^").endswith(HYPHEN)
    name = re.sub(r"[^\w\s()-]|[\d_]", "", "".join(pieces))
    name = turkish_upper(re.sub(r"\s+", " ", name)).strip(" -")
    name = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", name))
    return name, "".join(marks)


def page_rows(number: int, words: list[tuple], layout: list[float]) -> list[dict]:
    header = [w for w in words if re.fullmatch(r"KAZ\s?AS\s?I|KAZASI|KÖYÜN", w[4].replace(" ", ""))]
    top = max((w[3] for w in header), default=95.0) + 3
    bottom = max(w[3] for w in words) - 12 if words else 0
    body = [w for w in words if w[1] > top and not (w[1] > bottom and re.fullmatch(r"[-—–\d\s.]+", w[4]))]
    split = (layout[2] + layout[3]) / 2
    rows = []
    for group, (name_x, province_x, kaza_x) in enumerate((layout[:3], layout[3:])):
        lo = name_x - 7
        hi = (split + 4) if group == 0 else 10_000
        mine = [w for w in body if lo <= w[0] < hi]
        if group == 0:
            mine = [w for w in mine if w[0] < layout[3] - 4]
        names = [w for w in mine if w[0] < province_x - 3]
        provinces = [w for w in mine if province_x - 3 <= w[0] < kaza_x - 3]
        kazas = [w for w in mine if w[0] >= kaza_x - 3 and not (len(w[4]) <= 1 and w[0] > kaza_x + 24)]
        province_lines = [line for line in lines(provinces) if LETTERS.search(joined(line["words"]))]
        found = entries(lines(names), name_x, [p["y"] for p in province_lines])
        kaza_lines: list[dict] = []
        for line in lines(kazas):
            text = joined(line["words"]).strip()
            if (
                kaza_lines
                and line["y"] - kaza_lines[-1]["y"] < 14
                and not any(abs(p["y"] - line["y"]) < 3 for p in province_lines)
                and (kaza_lines[-1]["text"].endswith(HYPHEN) or line["x"] > kaza_x + 6)
            ):
                last = kaza_lines[-1]["text"]
                kaza_lines[-1]["text"] = (
                    last.rstrip("".join(HYPHEN)) + text if last.endswith(HYPHEN) else f"{last} {text}"
                )
                continue
            kaza_lines.append({"y": line["y"], "text": text})
        kaza_of = {b: a for a, b in align([float(k["y"]) for k in kaza_lines], [p["y"] for p in province_lines], 5.0)}
        matched = dict(align([e["y"] for e in found], [p["y"] for p in province_lines]))
        for index, entry in enumerate(found):
            name, stars = clean(entry)
            if not LETTERS.search(name):
                continue
            target = matched.get(index)
            province_text = joined(province_lines[target]["words"]).strip() if target is not None else ""
            kaza_text = kaza_lines[kaza_of[target]]["text"] if target is not None and target in kaza_of else ""
            last = lettered(entry["parts"][-1])
            if len(last) > 1 and len(last[-1][4]) == 1 and last[-1][0] - last[-2][2] > 3:
                last = last[:-1]
            edge = province_lines[target]["x"] if target is not None else province_x
            rows.append(
                {
                    "page": number,
                    "group": group + 1,
                    "box": (
                        max(w[2] for w in last) + 1,
                        min(edge, province_x) - 1.5,
                        min(w[1] for w in entry["parts"][-1]) - 1.5,
                        max(w[3] for w in entry["parts"][-1]) + 1.5,
                    ),
                    "y": round(entry["y"]),
                    "span": (
                        name_x - 4,
                        max(w[2] for w in entry["parts"][-1]) + 2,
                        min(w[1] for w in entry["parts"][0]) - 2,
                        max(w[3] for w in entry["parts"][-1]) + 2,
                    ),
                    "single": len(entry["parts"]) == 1,
                    "name": name,
                    "stars": stars,
                    "province_printed": province_text,
                    "kaza_printed": kaza_text,
                    "line": province_lines[target]["y"] if target is not None else None,
                    "columns": (province_x, kaza_x, layout[3] - 2 if group == 0 else 377.0),
                }
            )
    return rows


def drawn(image: np.ndarray, box: tuple[float, float, float, float]) -> int:
    scale = image.shape[1] / 377
    x0, x1, y0, y1 = (round(v * scale) for v in box)
    profile = (image[max(y0, 0) : y1, x0 : max(x1, x0 + 1)] < 235).sum(axis=0)
    runs: list[list[int]] = []
    for index, dark in enumerate(profile):
        if not dark:
            continue
        if runs and index - runs[-1][1] <= 3:
            runs[-1][1] = index
            runs[-1][2] += int(dark)
        else:
            runs.append([index, index, int(dark)])
    count = 0
    for start, end, dark in runs:
        width = end - start + 1
        if width < 4 or end >= len(profile) - 2 or dark < 20:
            continue
        count += 1 if width <= 17 else 2 if width <= 34 and dark >= 60 else 0
    return count


def stars(marks: str) -> int:
    if not marks:
        return 0
    return 2 if marks.count("*") >= 2 or re.search(r"\*\s*\*|=\*\*|\*\*=", marks) else 1


def capitalized(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .,'’\"")
    return turkish_upper(text[:1]) + text[1:] if text else text


def read(image: Image.Image, box: tuple[float, float, float, float], folder: str) -> list[tuple]:
    scale = image.size[0] / 377
    left, top, right, bottom = (v * scale for v in box)
    crop = image.crop((left, top, right, bottom))
    crop.save(f"{folder}/crop.png")
    text = subprocess.run(
        ["tesseract", f"{folder}/crop.png", "-", "-l", "tur", "--psm", "6", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    words = []
    for line in text.splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) == 12 and cells[11].strip() and float(cells[10]) >= 0:
            left, top, width, height = (int(c) / scale for c in cells[6:10])
            words.append((box[0] + left, box[1] + top, box[0] + left + width, box[1] + top + height, cells[11]))
    return words


def doubtful(name: str) -> bool:
    return bool(re.search(r"[Iİ]{2}|\s\S{1,2}$", name))


def retyped(row: dict, words: list[tuple]) -> str | None:
    x0, x1, y0, y1 = row["span"]
    near = [
        w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1 and LETTERS.search(w[4])
    ]
    if not near:
        return None
    name = re.sub(r"[^\w\s()-]|[\d_]", "", " ".join(w[4] for w in sorted(near, key=lambda w: w[0])))
    name = turkish_upper(re.sub(r"\s+", " ", name)).strip(" -")
    if len(name) < 3 or doubtful(name):
        return None
    return name


def readings(row: dict) -> list[tuple[str, str]]:
    return [(row["province_printed"], row["kaza_printed"]), (row["province_alt"], row["kaza_alt"])]


def parse(path: Path) -> pl.DataFrame:
    book = pages(path)
    layouts: dict[int, list[float]] = {}
    for number in range(FIRST, LAST + 1):
        found = peaks(book[number - 1])
        if found is not None:
            layouts[number] = found
    default = {
        parity: [median(v[i] for n, v in layouts.items() if n % 2 == parity) for i in range(6)] for parity in (0, 1)
    }
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for number in range(FIRST, LAST + 1):
            found = page_rows(number, book[number - 1], layouts.get(number, default[number % 2]))
            if not found:
                continue
            stem = Path(folder) / str(number)
            subprocess.run(
                ["pdftoppm", "-f", str(number), "-l", str(number), "-r", "300", "-gray", "-singlefile", "-png"]
                + [str(path), str(stem)],
                check=True,
            )
            page = Image.open(f"{stem}.png").convert("L")
            image = np.asarray(page.resize((page.size[0] * 2 // 3, page.size[1] * 2 // 3)))
            tops = [r["box"][2] for r in found]
            seen: dict[tuple, list[tuple]] = {}
            names: dict[int, list[tuple]] = {}
            for row in found:
                row["drawn"] = drawn(image, row["box"])
                province_x = row["columns"][0]
                if row["group"] not in names:
                    names[row["group"]] = read(
                        page, (row["span"][0], min(tops) - 8, province_x - 3, max(tops) + 20), folder
                    )
                row["name_abbyy"] = row["name"]
                if row["single"] and doubtful(row["name"]):
                    row["name"] = retyped(row, names[row["group"]]) or row["name"]
                province_x, kaza_x, end = row["columns"]
                if row["columns"] not in seen:
                    seen[row["columns"]] = read(page, (province_x - 3, min(tops) - 8, end, max(tops) + 20), folder)
                near = [
                    w
                    for w in seen[row["columns"]]
                    if row["line"] is not None and abs((w[1] + w[3]) / 2 - row["line"]) < 4
                ]
                row["province_alt"] = " ".join(w[4] for w in near if w[0] < kaza_x - 3)
                row["kaza_alt"] = " ".join(w[4] for w in near if w[0] >= kaza_x - 3)
            rows += found
    for row in rows:
        exact = [PROVINCE_KEYS[key(p)] for p, _ in readings(row) if key(p) in PROVINCE_KEYS]
        row["vilayet"] = exact[0] if exact else province(row["province_printed"]) or province(row["province_alt"])
    spelled: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in rows:
        if row["vilayet"] and any(key(p) in PROVINCE_KEYS for p, _ in readings(row)):
            for _, kaza in readings(row):
                if len(key(kaza)) >= 3:
                    spelled[row["vilayet"], key(kaza)][capitalized(kaza)] += 1
    vocabulary: dict[str, dict[str, str]] = defaultdict(dict)
    for (name, folded), counts in sorted(spelled.items(), key=lambda item: -item[1].total()):
        if counts.total() < 4:
            continue
        heads = [k for k in vocabulary[name] if difflib.SequenceMatcher(None, folded, k).ratio() >= 0.72]
        if heads and spelled[name, heads[0]].total() >= 3 * counts.total():
            vocabulary[name][folded] = vocabulary[name][heads[0]]
        else:
            vocabulary[name][folded] = counts.most_common(1)[0][0]
    owners: dict[str, set[str]] = defaultdict(set)
    for name, words in vocabulary.items():
        for folded in words:
            owners[folded].add(name)

    def fit(name: str, folded: str) -> tuple[float, str]:
        if folded in vocabulary[name]:
            return 1.0, folded
        return max(((difflib.SequenceMatcher(None, folded, k).ratio(), k) for k in vocabulary[name]), default=(0.0, ""))

    for row in rows:
        kazas = [key(k) for _, k in readings(row) if len(key(k)) >= 3]
        exact = [PROVINCE_KEYS[key(p)] for p, _ in readings(row) if key(p) in PROVINCE_KEYS]
        if kazas and not any(fit(name, k)[0] >= 0.72 for name in exact for k in kazas):
            joint: dict[str, float] = {}
            for printed, _ in readings(row):
                for score, name in ranked(printed)[:10]:
                    if score >= 0.3:
                        best = max(fit(name, k)[0] for k in kazas)
                        joint[name] = max(joint.get(name, 0), score + best)
            ordered = sorted(((v, n) for n, v in joint.items()), reverse=True)
            if ordered and ordered[0][0] >= 1.2 and (len(ordered) == 1 or ordered[0][0] - ordered[1][0] >= 0.1):
                row["vilayet"] = ordered[0][1]
            elif row["vilayet"] is None:
                found = {n for k in kazas for n in owners.get(k, ())}
                if len(found) == 1:
                    row["vilayet"] = found.pop()
        hits = sorted((fit(row["vilayet"], k) for k in kazas), reverse=True) if row["vilayet"] else []
        row["kaza"] = vocabulary[row["vilayet"]][hits[0][1]] if hits and hits[0][0] >= 0.72 else ""
    return pl.DataFrame(
        [
            {
                "page": row["page"],
                "column": row["group"],
                "y": round(row["box"][2], 1),
                "koy": row["name"],
                "koy_abbyy": row["name_abbyy"],
                "vilayet": row["vilayet"] or "",
                "kaza": row["kaza"],
                "mark": "*" * min(max(row["drawn"], stars(row["stars"])), 2),
                "vilayet_printed": row["province_printed"],
                "kaza_printed": row["kaza_printed"],
                "vilayet_tesseract": row["province_alt"],
                "kaza_tesseract": row["kaza_alt"],
            }
            for row in rows
        ]
    )


def linked(frame: pl.DataFrame) -> pl.DataFrame:
    renames = pl.read_csv(RENAMES, separator="\t", infer_schema=False).fill_null("")
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in renames.iter_rows(named=True):
        by_name[fold(row["old_name"])].append(row)
    claims: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(frame.iter_rows(named=True)):
        plates = PROVINCES.get(row["vilayet"], [])
        options = [r for r in by_name.get(fold(row["koy"]), []) if r["plate"] in plates]
        if len(options) > 1 and row["kaza"]:
            options = [r for r in options if fold(r["ilce"]) == fold(row["kaza"])] or options
        if len(options) == 1:
            claims[id(options[0])].append(index)
    links: dict[int, dict] = {}
    for row_list in by_name.values():
        for rename in row_list:
            claimants = claims.get(id(rename), [])
            if len(claimants) == 1:
                links[claimants[0]] = rename
    return frame.with_columns(
        pl.Series("plate_1968", [links[i]["plate"] if i in links else "" for i in range(frame.height)]),
        pl.Series("ilce_1968", [links[i]["ilce"] if i in links else "" for i in range(frame.height)]),
        pl.Series("name_1968", [links[i]["new_name"] if i in links else "" for i in range(frame.height)]),
    )


def build() -> Path:
    frame = linked(parse(raw.fetch(SOURCE, URL, suffix=".pdf").path))
    frame.write_csv(REGISTER, separator="\t")
    return REGISTER
