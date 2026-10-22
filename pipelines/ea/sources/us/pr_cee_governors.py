import csv
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import get_close_matches
from io import BytesIO
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.pr_cee_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "Informe Estadístico, Elecciones Generales: votes for governor by municipio"
BASE = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Documents/"
SITE = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/"
ELECTIONS = {
    "1988-11-08": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201988.pdf",
        "pages": (62, 63, 64, 65),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("hernandez", "corrada", "berrios"),
        "text": True,
    },
    "1992-11-03": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201992.pdf",
        "pages": (71, 72, 73),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("rossello", "munoz", "martin"),
    },
    "1996-11-05": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201996.pdf",
        "pages": (79, 80, 81),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("rossello", "acevedo", "noriega"),
    },
    "2000-11-07": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%202000.pdf",
        "pages": (79, 80, 81),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("calderon", "pesquera", "berrios"),
    },
    "2004-11-02": {
        "url": BASE + "Estadisticas-de-las-Elecciones-2004.pdf",
        "pages": (78, 79, 80),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("acevedo", "rossello", "berrios"),
        "fixes": {
            (78, 36): (2804, [995, 1770, 39]),
            (79, 15): (39302, [20814, 17267, 1221]),
            (79, 18): (15714, [7024, 8421, 269]),
            (79, 33): (11025, [5084, 5579, 362]),
            (79, 35): (22851, [12447, 9545, 859]),
        },
    },
    "2008-11-04": {
        "url": BASE + "estadisticas-elecciones-2008.zip",
        "member": "ESTADISTICAS DE LAS ELECCIONES 2008 PARTE 1.pdf",
        "pages": (81, 82, 83),
        "island": "The municipio rows also sum exactly to the island row printed on page 81.",
        "split": 1,
        "shaded": True,
        "surnames": ("acevedo", "fortuno", "irizarry", "figueroa"),
    },
    "2012-11-06": {
        "url": BASE + "estadisticas-elecciones-2012.zip",
        "member": "ESTADISTICAS DE LAS ELECCIONES 2012 PARTE 2.pdf",
        "pages": (21, 22, 23, 24),
        "island": "The municipio rows also sum exactly to the island row printed on page 21.",
        "split": 1,
        "rotate": -90,
        "shaded": True,
        "surnames": ("fortuno", "padilla", "dalmau", "hernandez", "figueroa", "bernabe"),
    },
    "2016-11-08": {
        "url": BASE + "Informe%20Estadistico%20EG2016%20-%20Parte1.pdf",
        "pages": (82, 83),
        "dpi": 150,
        "surnames": ("bernier", "rossello", "santiago", "bernabe", "cidre", "lugaro"),
    },
    "2020-11-03": {
        "url": BASE + "Informe-estadistico-EG2020.pdf",
        "pages": (109, 110),
        "surnames": ("pierluisi", "delgado", "dalmau", "lugaro", "vazquez", "molina"),
        "manual": {"LARES": (14145, [4985, 6168, 917, 824, 1189, 62])},
    },
    "2024-11-05": {
        "url": BASE + "INFORME-ESTADISTICO-ELECCIONES-GENERALES-2024.pdf",
        "pages": (86, 87),
        "surnames": ("gonzalez", "ortiz", "cordova", "dalmau", "jimenez"),
    },
}


def fold(text: str) -> str:
    return re.sub("[^A-Z]", "", unicodedata.normalize("NFKD", text.upper()).encode("ascii", "ignore").decode())


def numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d.,]*", text)


def fetch() -> None:
    for election in ELECTIONS.values():
        raw.fetch(SOURCE, election["url"], suffix=election["url"][-4:])


def solve(rest: list[list[str]], candidates: int, slack: float = 0) -> tuple[int, list[int]] | None:
    totals = [int(re.sub(r"\D", "", found[0])) for reading in rest[0] if (found := numbers(reading))]
    cells = []
    for cell in rest[1 : candidates + 1]:
        pairs = []
        for reading in cell:
            found = numbers(reading)
            if len(found) >= 2:
                last = found[-1].replace(",", ".")
                if re.fullmatch(r"\d{3,4}", last):
                    last = last[:-2] + "." + last[-2:]
                pairs.append((int(re.sub(r"\D", "", found[0])), float(last) if re.fullmatch(r"\d+\.\d\d", last) else None))
            elif len(found) == 1 and re.fullmatch(r"\d\.\d\d", found[0].replace(",", ".")):
                pairs.append((None, float(found[0].replace(",", "."))))
        cells.append(pairs)
    totals += [round(v * 100 / pct) for pairs in cells for v, pct in pairs if v is not None and pct is not None and pct > 10]
    for total, _ in Counter(totals).most_common():
        values: list[int | None] = []
        for pairs in cells:
            good = [v for v, pct in pairs if v is not None and pct is not None and abs(v / total * 100 - pct) <= 0.0051]
            values.append(Counter(good).most_common(1)[0][0] if good else None)
        missing = [index for index, value in enumerate(values) if value is None]
        if len(missing) == 1 and not slack:
            values[missing[0]] = total - sum(value for value in values if value is not None)
        if None not in values and 0 <= total - sum(v for v in values if v is not None) <= slack * total:
            return total, [v for v in values if v is not None]
    return None

PASSES = ((1, None), (2, None), (2, 150), (1, 150))
EXTRA = ((3, None), (3, 150), (2, 120), (2, 180), (1, 180), (1, 120), (4, None), (3, 190))
CONFUSED = {"0": "8693", "1": "74", "2": "7", "3": "85", "4": "19", "5": "9368", "6": "580", "7": "12", "8": "306957", "9": "548"}


def ocr(image: Image.Image, box: tuple[int, int, int, int], digits: bool, scale: int, threshold: int | None) -> str:
    crop = image.crop((box[0] + 5, box[1], box[2] + 9, box[3]))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    if threshold is not None:
        crop = crop.point(lambda value: 255 if value > threshold else 0)
    padded = Image.new("L", (crop.width + 60, crop.height + 60), 255)
    padded.paste(crop, (30, 30))
    buffer = BytesIO()
    padded.save(buffer, "PNG")
    config = ["-c", "tessedit_char_whitelist=0123456789.,"] if digits else []
    return subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng", *config],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()


def read_cells(image: Image.Image, cells: list[tuple[tuple[int, int, int, int], bool]], passes: tuple[tuple[int, int | None], ...]) -> list[list[str]]:
    with ThreadPoolExecutor(12) as pool:
        return list(pool.map(lambda cell: [ocr(image, cell[0], cell[1], *each) for each in (passes if cell[1] else passes[:2])], cells))


def long_runs(dark: np.ndarray, length: int, axis: int) -> np.ndarray:
    lines = dark if axis == 1 else dark.T
    cumulative = np.concatenate([np.zeros((lines.shape[0], 1), np.int32), np.cumsum(lines, axis=1, dtype=np.int32)], axis=1)
    hit = (cumulative[:, length:] - cumulative[:, :-length]) >= length - 3
    delta = np.zeros((lines.shape[0], lines.shape[1] + 1), np.int32)
    delta[:, : hit.shape[1]] += hit
    delta[:, length : length + hit.shape[1]] -= hit[:, : lines.shape[1] + 1 - length]
    mask = np.cumsum(delta, axis=1)[:, : lines.shape[1]] > 0
    for shift in (1, 2):
        mask = mask | np.roll(mask, shift, axis=0) | np.roll(mask, -shift, axis=0)
    return mask if axis == 1 else mask.T


def parse_cell(reading: str) -> tuple[int | None, float | None]:
    tokens = re.findall(r"\d[\d.,]*", reading)

    def percent(token: str) -> float | None:
        token = token.replace(",", ".")
        if re.fullmatch(r"\d{1,3}\.\d\d", token):
            return float(token)
        digits = re.sub(r"\D", "", token)
        return float(digits[:-2] + "." + digits[-2:]) if re.fullmatch(r"\d{3,5}", digits) and "." not in token else None

    if len(tokens) >= 2:
        return int(re.sub(r"\D", "", tokens[0])), percent(tokens[-1])
    if len(tokens) == 1:
        if re.fullmatch(r"\d{1,3}[.,]\d\d", tokens[0]):
            return None, percent(tokens[0])
        return int(re.sub(r"\D", "", tokens[0])), None
    return None, None


def variants(text: str) -> set[str]:
    found = {text}
    for index, character in enumerate(text):
        found.update(text[:index] + alternative + text[index + 1 :] for alternative in CONFUSED.get(character, ""))
    return found


def solve_row(line: list[list[str]], slack: float = 0.02) -> tuple[int, list[int], list[int]] | None:
    totals = Counter(int(re.sub(r"\D", "", found[0])) for reading in line[0] if (found := re.findall(r"\d[\d.,]*", reading)))
    cells = [[parse_cell(reading) for reading in cell] for cell in line[1:]]
    implied = Counter(round(v * 100 / p) for cell in cells for v, p in cell if v and p and p > 10)
    tried: list[int] = []
    for total in [t for t, _ in totals.most_common(2)] + [t for t, n in implied.most_common(2) if n >= 2]:
        if total in tried:
            continue
        tried.append(total)
        values: list[int] = []
        weak: list[int] = []
        for cell in cells:
            votes = Counter(v for v, _ in cell if v is not None)
            percents = Counter(p for _, p in cell if p is not None)
            good = Counter({v: n for v, n in votes.items() if any(abs(v / total * 100 - p) <= 0.0051 for p in percents)})
            if not good:
                weak.append(len(values))
                for v in votes:
                    for alternative in variants(str(v)):
                        if any(abs(int(alternative) / total * 100 - p) <= 0.0051 for p in percents):
                            good[int(alternative)] += 1
                for p in percents:
                    for alternative in variants(f"{p:.2f}".replace(".", "")):
                        for v in votes:
                            if abs(v / total * 100 - float(alternative[:-2] + "." + alternative[-2:])) <= 0.0051:
                                good[v] += 1
            if not good:
                top = votes.most_common(1)
                if top and top[0][1] >= 3:
                    good[top[0][0]] += 1
            if not good:
                break
            values.append(good.most_common(1)[0][0])
        else:
            if 0 <= total - sum(values) <= slack * total:
                return total, values, weak
    return None


def read_page(image: Path, candidates: int, split: int, darkness: int = 215) -> list[dict[str, object]]:
    gray = np.asarray(Image.open(image).convert("L"))
    want = 3 + split * candidates
    for threshold, tolerance in ((225, 60), (200, 12), (200, 60), (225, 12), (225, 100)):
        dark = gray < threshold
        cumulative = np.cumsum(dark, axis=0, dtype=np.int32)
        hit = ((cumulative[250:] - cumulative[:-250]) >= 250 - tolerance).any(axis=0)
        groups: list[list[int]] = []
        for x in np.flatnonzero(hit):
            if groups and x - groups[-1][-1] <= 8:
                groups[-1].append(int(x))
            else:
                groups.append([int(x)])
        cols = [(group[0] + group[-1]) // 2 for group in groups]
        if len(cols) == want:
            break
    if len(cols) != want:
        raise ValueError(f"{image}: columns {cols}")
    cleaned_array = gray.copy()
    cleaned_array[long_runs(gray < darkness, 150, 1) | long_runs(gray < darkness, 150, 0)] = 255
    cleaned = Image.fromarray(cleaned_array)
    ink = cleaned_array < 170
    profile = ink[:, cols[1] + 8 : cols[2] - 8].sum(axis=1) > 3
    runs: list[tuple[int, int]] = []
    start = None
    for y, on in enumerate(profile):
        if on and start is None:
            start = y
        if not on and start is not None:
            if y - start >= 14:
                runs.append((start, y))
            start = None
    names = read_cells(cleaned, [((cols[0], a - 3, cols[1], b + 3), False) for a, b in runs], PASSES)
    kept: list[tuple[int, int, str, str]] = []
    for (a, b), text in zip(runs, names, strict=True):
        folded = fold(text[0])
        if "ISTRITO" in folded and "SENATOR" not in folded and "DISTRITOS" not in folded:
            kept.append((a, b, "district", text[0]))
        elif "ISLA" in folded or "GRANTOTAL" in folded:
            kept.append((a, b, "island", text[0]))
        elif (
            len(folded) >= 4
            and (split == 2 or re.search(r"\d\W{0,2}[iIl]?$", text[0].strip()))
            and not any(word in folded for word in ("DISTR", "PRECINT", "TOTAL", "SENATOR"))
        ):
            kept.append((a, b, "precinct", text[0]))

    def band(x0: int, x1: int, a: int, b: int) -> tuple[int, int]:
        centre = (a + b) / 2
        found = np.flatnonzero(ink[a - 22 : b + 22, x0 + 8 : x1 - 3].sum(axis=1) > 1)
        pieces = np.split(found, np.flatnonzero(np.diff(found) > 3) + 1) if len(found) else []
        best = min((piece for piece in pieces if len(piece) > 8), key=lambda piece: abs(a - 22 + piece.mean() - centre), default=None)
        if best is None:
            return a - 6, b + 6
        return a - 22 + int(best[0]) - 4, a - 22 + int(best[-1]) + 5

    cells = []
    boxes = []
    for a, b, _, _ in kept:
        y0, y1 = band(cols[1], cols[2], a, b)
        cells.append(((cols[1], y0, cols[2], y1), True))
        row_boxes = []
        for candidate in range(candidates):
            for part in range(split):
                low = 2 + split * candidate + part
                y0, y1 = band(cols[low], cols[low + 1], a, b)
                cells.append(((cols[low], y0, cols[low + 1], y1), True))
                if part == 0:
                    row_boxes.append((cols[low], y0, cols[low + split], y1))
        boxes.append(row_boxes)
    read = read_cells(cleaned, cells, PASSES)
    width = 1 + split * candidates
    rows = []
    for index, (a, b, kind, text) in enumerate(kept):
        raw_cells = read[index * width : (index + 1) * width]
        if kind == "precinct" and not any(re.search(r"\d{3}", reading) for reading in raw_cells[0]):
            continue

        def arrange(source: list[list[str]]) -> list[list[str]]:
            return [source[0]] + [
                [" ".join(source[1 + split * candidate + part][pass_] for part in range(split)) for pass_ in range(len(source[0]))]
                for candidate in range(candidates)
            ]

        solved = solve_row(arrange(raw_cells))
        if solved is None or solved[2]:
            more = read_cells(cleaned, cells[index * width : (index + 1) * width], EXTRA)
            again = solve_row(arrange([first + second for first, second in zip(raw_cells, more, strict=True)]))
            if again is not None and (solved is None or len(again[2]) <= len(solved[2])):
                solved = again
        rows.append({"kind": kind, "name": text, "solved": solved, "boxes": boxes[index], "y": (a, b)})
    return rows


def read_text_page(image: Path, candidates: int) -> list[dict[str, object]]:
    rows = []
    for scale in (1, 0.75, 1.25):
        source = Image.open(image).convert("L")
        scaled = source.resize((int(source.width * scale), int(source.height * scale)), Image.Resampling.LANCZOS)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "page.png"
            scaled.save(target)
            tsv = subprocess.run(
                ["tesseract", str(target), "-", "--psm", "6", "-l", "eng", "tsv"], capture_output=True, text=True, check=True
            ).stdout
        lines: dict[tuple[str, str, str], list[tuple[str, int, int, int, int]]] = {}
        for record in tsv.splitlines()[1:]:
            fields = record.split("\t")
            if len(fields) == 12 and fields[11].strip() and fields[0] == "5":
                lines.setdefault((fields[2], fields[3], fields[4]), []).append(
                    (fields[11].strip(), *(round(int(value) / scale) for value in fields[6:10]))
                )
        found = []
        for words in lines.values():
            merged: list[tuple[str, int, int, int, int]] = []
            for word in words:
                if merged and (
                    (word[0][0] in ",." and merged[-1][0][-1].isdigit())
                    or (merged[-1][0][-1] in ",." and merged[-1][0][:-1].isdigit() and word[0][0].isdigit())
                ):
                    last = merged.pop()
                    merged.append((last[0] + word[0], last[1], min(last[2], word[2]), word[1] + word[3] - last[1], max(last[4], word[4])))
                else:
                    merged.append(word)
            words = [(re.sub(r"^[^A-Za-zñÑ\d]+", "", word[0]), *word[1:]) if i == 0 else word for i, word in enumerate(merged)]
            texts = [word[0] for word in words]
            kind = "district" if fold("".join(texts[:2])).startswith("TOTALESDEL") else "precinct"
            if kind == "precinct" and fold(texts[0]).startswith(("DISTRITO", "GRAN")):
                continue
            if kind == "precinct" and not (re.fullmatch(r"[A-Za-z.ñÑ]+", texts[0]) and any(re.fullmatch(r"\d{1,3}", t) for t in texts)):
                continue
            position = 0 if kind == "district" else next(i for i, t in enumerate(texts) if re.fullmatch(r"\d{1,3}", t)) + 1
            name = " ".join(texts[:position]) if kind == "precinct" else "TOTALES"
            tail = [word for word in words[position:] if re.search(r"\d", word[0])]
            figures = [(word, re.sub(r"[^\d.,%]", "", word[0]).rstrip(".,")) for word in tail]
            votes = [(word, int(re.sub(r"\D", "", figure))) for word, figure in figures if re.fullmatch(r"\d{1,3}(,\d{3})*", figure)]
            if len(votes) != 1 + candidates:
                found.append({"kind": kind, "name": name, "solved": None, "boxes": [], "y": (words[0][2], words[0][2] + words[0][4])})
                continue
            total, counts = votes[0][1], [value for _, value in votes[1:]]
            percents = [float(re.sub(r"[^\d.]", "", figure[:-1]).replace(",", ".")) for _, figure in figures if figure.endswith("%") and re.fullmatch(r"\d+\.\d\d%", figure)]
            ok = sum(counts) == total and (len(percents) != candidates or all(abs(count / total * 100 - percent) <= 0.0051 for count, percent in zip(counts, percents, strict=True)))
            boxes = [(w[1], w[2], w[1] + w[3], w[2] + w[4]) for w, _ in votes[1:]]
            found.append({"kind": kind, "name": name, "solved": (total, counts) if ok else None, "boxes": boxes, "y": (words[0][2], words[0][2] + words[0][4])})
        found.sort(key=lambda row: row["y"][0])
        if not rows:
            rows = found
        else:
            for index, row in enumerate(rows):
                if row["solved"] is None:
                    match = next((other for other in found if other["name"] == row["name"] and other["solved"] is not None and abs(other["y"][0] - row["y"][0]) < 15), None)
                    if match is not None:
                        rows[index] = match
        if all(row["solved"] is not None for row in rows):
            break
    return rows


def prepare(pdf: Path, number: int, election: dict[str, object], target: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-f", str(number), "-l", str(number), "-png", "-singlefile", str(pdf), str(stem)], check=True)
        image = Image.open(stem.with_suffix(".png")).convert("L")
    image = image.rotate(election.get("rotate", 0), expand=True)
    coarse = image.resize((image.width // 4, image.height // 4), Image.Resampling.BILINEAR)
    best = max(
        np.arange(-1.5, 1.51, 0.1),
        key=lambda angle: (np.asarray(coarse.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 200).sum(axis=1).astype(float).var(),
    )
    half = image.resize((image.width // 2, image.height // 2), Image.Resampling.BILINEAR)
    best = max(
        np.arange(best - 0.1, best + 0.101, 0.02),
        key=lambda angle: (np.asarray(half.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 200).sum(axis=1).astype(float).var(),
    )
    image.rotate(best, resample=Image.Resampling.BICUBIC, fillcolor=255).save(target)


def parse() -> None:
    parse_precincts()
    rows: list[dict[str, object]] = []
    for day, election in ELECTIONS.items():
        if "split" in election:
            continue
        candidates = len(election["surnames"])
        with tempfile.TemporaryDirectory() as folder:
            for page in election["pages"]:
                subprocess.run(
                    ["pdftoppm", "-r", str(election.get("dpi", 300)), "-f", str(page), "-l", str(page), "-png", str(raw.cached(SOURCE, election["url"]).path), f"{folder}/p"],
                    check=True,
                )
                image = next(Path(folder).glob("p-*.png"))
                rows_y, cols_x = scan.tables(image)[-1]
                width = len(cols_x) - 1
                cells = [
                    ((cols_x[j], rows_y[i], cols_x[j + 1], rows_y[i + 1]), j > 0)
                    for i in range(len(rows_y) - 1)
                    for j in range(width)
                ]
                read = scan.read(image, cells, "eng")
                for i in range(len(rows_y) - 1):
                    line = read[i * width : (i + 1) * width]
                    names = Counter(fold(text) for text in line[0] if len(fold(text)) >= 3).most_common(1)
                    if not names:
                        continue
                    name = names[0][0]
                    solved = solve(line[1:], candidates)
                    if solved is None:
                        raise ValueError(f"{day} {name}: row does not reconcile: {line}")
                    for index, votes in enumerate(solved[1]):
                        rows.append({"day": day, "municipio": name, "candidate": index, "votes": votes, "total": solved[0]})
                Path(image).unlink()
    PARSED.mkdir(parents=True, exist_ok=True)
    for day, election in ELECTIONS.items():
        for name, (total, votes) in election.get("manual", {}).items():
            rows.extend({"day": day, "municipio": name, "candidate": i, "votes": v, "total": total} for i, v in enumerate(votes))
    frame = pl.DataFrame(rows)
    island = frame.filter(pl.col("municipio").str.starts_with("TOTALES"))
    frame = frame.filter(~pl.col("municipio").str.starts_with("TOTALES"))
    summed = frame.group_by("day", "candidate").agg(pl.col("votes").sum()).sort("day", "candidate")
    printed = island.select("day", "candidate", "votes").sort("day", "candidate")
    print(summed.join(printed, on=["day", "candidate"], how="left", suffix="_printed").filter(pl.col("votes") != pl.col("votes_printed")))
    print(frame.group_by("day").agg(pl.col("municipio").n_unique()))
    frame.write_parquet(PARSED / "municipios.parquet")


def parse_precincts() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    kept = pl.read_parquet(PARSED / "precincts.parquet") if (PARSED / "precincts.parquet").exists() else None
    rows: list[dict[str, object]] = kept.to_dicts() if kept is not None else []
    for day, election in ELECTIONS.items():
        if "split" not in election or (kept is not None and day in kept["day"].unique()):
            continue
        artifact = raw.cached(SOURCE, election["url"])
        candidates = len(election["surnames"])
        printed = [0] * candidates
        found = [0] * candidates
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(zipfile.ZipFile(artifact.path).extract(election["member"], folder)) if "member" in election else artifact.path
            district: list[list[int]] = []
            for number in election["pages"]:
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    prepare(pdf, number, election, image)
                with Image.open(image) as opened:
                    width, height = opened.size
                ordinal = 0
                rows_read = read_text_page(image, candidates) if election.get("text") else read_page(image, candidates, election["split"])
                if election.get("shaded"):
                    rows_read += [
                        row
                        for row in read_page(image, candidates, election["split"], 170)
                        if row["kind"] == "district" and all(abs(row["y"][0] - other["y"][0]) > 15 for other in rows_read)
                    ]
                    rows_read.sort(key=lambda row: row["y"][0])
                for row in rows_read:
                    if row["kind"] == "precinct":
                        solved = election.get("fixes", {}).get((number, ordinal), row["solved"])
                        ordinal += 1
                        if solved is None:
                            raise ValueError(f"{day} page {number} {row['name']!r}: row does not reconcile")
                        total, votes = solved[0], solved[1]
                        district.append(votes)
                        rows.extend(
                            {"day": day, "page": number, "name": row["name"], "candidate": index, "votes": value, "total": total, "box": list(row["boxes"][index]), "width": width, "height": height}
                            for index, value in enumerate(votes)
                        )
                    elif row["kind"] == "district":
                        sums = [sum(votes[index] for votes in district) for index in range(candidates)]
                        if row["solved"] is None or sums != row["solved"][1]:
                            raise ValueError(f"{day} page {number}: district precinct sums {sums} against printed {row['solved']}")
                        printed = [a + b for a, b in zip(printed, row["solved"][1], strict=True)]
                        found = [a + b for a, b in zip(found, sums, strict=True)]
                        district = []
        print(day, "precincts", Counter(row["day"] for row in rows)[day] // candidates, "district sums", found, "match the printed district rows")
    pl.DataFrame(rows).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "municipios.parquet")
    precincts = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico governor general elections by municipio from the State Elections Commission's statistical reports, read from the reports' page images and checked against each row's printed total and percentages",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://ww2.ceepur.org/Home/Estadisticas",
            license="Public record",
        )
        artifacts = {election["url"]: cs.artifact(raw.cached(SOURCE, election["url"]), source) for election in ELECTIONS.values()}
        units = {
            fold(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '72%%'"""
            ).fetchall()
        }
        if len(units) != 78:
            raise ValueError("Puerto Rico needs 78 municipios")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':PR:governor'")
        )
        days = [(day, frame.filter(pl.col("day") == day)) for day in frame["day"].unique(maintain_order=True)]
        days += [(day, precincts.filter(pl.col("day") == day)) for day in precincts["day"].unique(maintain_order=True)]
        pages: dict[tuple[str, int], UUID] = {}
        for day, lines in days:
            day = (day,)
            election = ELECTIONS[day[0]]
            key = f"{day[0]}:PR:governor"
            contest = contests[key]
            ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if "split" in election:
                lines = lines.with_columns(municipio=pl.col("name").map_elements(lambda text: fold(re.sub(r"\d+\W*[iIl]?$", "", text.strip())), return_dtype=pl.String))
            canonical = {
                name: get_close_matches(name, units, n=1, cutoff=0.7)[0]
                for name in set(lines["municipio"])
            }
            lines = lines.with_columns(municipio=pl.col("municipio").replace_strict(canonical))
            if "split" in election:
                lines = (
                    lines.with_columns(
                        x0=pl.col("box").list.get(0), y0=pl.col("box").list.get(1), x1=pl.col("box").list.get(2), y1=pl.col("box").list.get(3)
                    )
                    .sort("page", maintain_order=True)
                    .group_by("municipio", "candidate", maintain_order=True)
                    .agg(
                        pl.col("votes").sum(),
                        pl.col("page").first(),
                        pl.col("width").first(),
                        pl.col("height").first(),
                        pl.col("x0").filter(pl.col("page") == pl.col("page").first()).min(),
                        pl.col("y0").filter(pl.col("page") == pl.col("page").first()).min(),
                        pl.col("x1").filter(pl.col("page") == pl.col("page").first()).max(),
                        pl.col("y1").filter(pl.col("page") == pl.col("page").first()).max(),
                    )
                )
            names = set(lines["municipio"])
            if names - units.keys() or len(names) != 78:
                raise ValueError(f"{key}: municipios unmatched {sorted(names - units.keys())} or missing {sorted(units.keys() - names)}")
            options = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
            picked: list[UUID] = []
            for surname in election["surnames"]:
                matches = [option for option, label in options if surname in fold(label).lower()]
                if len(matches) != 1:
                    raise ValueError(f"{key}: {len(matches)} options for {surname}")
                picked.append(matches[0])
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if "split" in election:
                pages_text = ", ".join(str(number) for number in election["pages"])
                body = f"Votes by municipio summed from the electoral-precinct rows of the State Elections Commission's Informe Estadístico for the {day[0][:4]} general election{' (' + election['member'].removesuffix('.pdf').title() + ')' if 'member' in election else ''}, pages {pages_text}, a table of page images read by OCR. Each precinct row's candidate votes match its printed percentages and do not exceed its printed total, and the precinct rows of every senatorial district sum to that district's printed totals row for each candidate. {election['island']} Each value's locator boxes the candidate's cells for the municipio's precinct rows on the first page where they appear; the sum also includes precinct rows on later pages and in other districts. Write-in and blank votes are not in the table."
            else:
                body = f"Votes by municipio read from pages {election['pages'][0]}–{election['pages'][1]} of the State Elections Commission's Informe Estadístico for the {day[0][:4]} general election, a table of page images. Each row's candidate votes sum to its printed total and match its printed percentages. Write-in and blank votes are not in the table. The municipio rows sum to within five votes per candidate of the island totals printed on the report's last row."
            published = conn.execute("select from result_set where id = %s", [result_set]).fetchone() is not None
            if published:
                continue
            cs.add("note", entity_id=result_set, kind="methodology", body_md=body)
            if "split" in election:
                artifact = artifacts[election["url"]]
                for number in election["pages"]:
                    image = PAGES / f"{raw.cached(SOURCE, election['url']).sha256}-{number}.png"
                    page_id = cs.page(artifact, number)
                    pages[day[0], number] = page_id
                    with Image.open(image) as opened:
                        cs.add("source_page", id=page_id, artifact_id=artifact, page_number=number, image_key=f"pages/{SOURCE}/{image.name}", width=opened.width, height=opened.height)
            for row in lines.iter_rows(named=True):
                fact = {
                    "result_set_id": result_set,
                    "unit_id": units[row["municipio"]],
                    "option_id": picked[row["candidate"]],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                located = "split" in election
                cs.add("result_value", **fact, flags=house_books.DIGITIZED | house_books.DERIVED if located else 0, value=row["votes"])
                if located:
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=pages[day[0], row["page"]],
                        source_id=None,
                        x0=row["x0"] / row["width"],
                        y0=row["y0"] / row["height"],
                        x1=row["x1"] / row["width"],
                        y1=row["y1"] / row["height"],
                    )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
