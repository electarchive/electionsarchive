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
    "1976-11-02": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201976.pdf",
        "pages": (70, 71, 72),
        "island": "The municipio rows also sum exactly to the island row printed on page 72. The report prints Cabo Rojo's precinct 52 total as 15,600 while its four candidate counts sum to 15,700, and the district and island rows follow the counts; the counts are used as printed.",
        "split": 1,
        "surnames": ("colon", "romero", "berrios", "mari"),
        "text": True,
        "cut": 0.36,
        "patch": {
            70: [(17946, [6979, 9467, 1396, 104]), (197417, [86156, 97282, 12684, 1295]), (9723, [4685, 4677, 264, 97]), (15887, [7452, 7830, 563, 42]),],
            71: [(15600, [7672, 6104, 1775, 149]), (182613, [88437, 83028, 10187, 1061]),],
            72: [(1677, [730, 910, 33, 4]), (20203, [10757, 8369, 928, 149]), (14117, [6183, 7123, 733, 78]), (562, [315, 184, 63, 0]), (0, [660401, 703968, 83037, 10728]),],
        },
        "names": {70: ["San Juan", "San Juan", "San Juan", "San Juan", "San Juan", "Guaynabo", "Bayamón", "Bayamón", "Guaynabo", "Bayamón", "Corozal", "Naranjito", "Bayamón", "Dorado", "Toa Alta", "Toa Baja", "Cataño", "Dorado", "Toa Baja", "Manatí", "Vega Alta", "Vega Baja", "Ciales", "Jayuya", "Morovis", "Orocovis", "Arecibo", "Barceloneta", "Florida", "Manatí", "Arecibo", "Hatillo", "Camuy", "Hatillo", "Isabela", "Quebradillas"], 71: ["Aguadilla", "Moca", "Aguada", "Añasco", "Mayagüez", "Moca", "Rincón", "Mayagüez", "Hormigueros", "Las Marías", "Maricao", "Mayagüez", "San Germán", "San Sebastián", "Cabo Rojo", "Lajas", "San Germán", "Guánica", "Guayanilla", "Sabana Grande", "Yauco", "Adjuntas", "Lares", "Utuado", "Adjuntas", "Guayanilla", "Peñuelas", "Ponce", "Ponce", "Ponce", "Juana Díaz", "Santa Isabel", "Villalba", "Aibonito", "Coamo", "Salinas", "Santa Isabel", "Aguas Buenas", "Barranquitas", "Cidra", "Comerío", "Aibonito", "Cayey", "Cidra", "Guayama", "Arroyo", "Guayama", "Maunabo", "Patillas"], 72: ["Caguas", "Caguas", "Juncos", "Gurabo", "Las Piedras", "San Lorenzo", "Yabucoa", "Ceiba", "Humacao", "Las Piedras", "Naguabo", "Ceiba", "Culebra", "Fajardo", "Luquillo", "Río Grande", "Vieques", "Canóvanas", "Carolina", "Loíza", "Carolina", "San Juan", "Trujillo Alto", "Carolina", "San Juan", "Trujillo Alto", "San Juan", "Guaynabo"]},
    },
    "1980-11-04": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201980.pdf",
        "pages": (56, 57),
        "island": "The municipio rows also sum exactly to the island row printed on page 57.",
        "split": 1,
        "surnames": ("romero", "colon", "berrios", "lausell"),
        "text": True,
        "cut": 0.21,
        "patch": {
            56: [(26538, [15573, 9572, 1310, 83]), (20397, [10402, 8227, 1727, 41]), (10419, [4702, 5233, 449, 35]), (15594, [7654, 7175, 651, 114]), (15265, [6641, 7845, 738, 41]),],
            57: [(757, [321, 398, 35, 3]), (7441, [3327, 3801, 291, 22]), (8974, [4193, 4134, 643, 4]), (18783, [8665, 8998, 1069, 51]), (7463, [4179, 3083, 184, 17]), (714, [240, 393, 80, 1]), (39636, [20378, 16063, 3084, 111]), (0, [759926, 756889, 87272, 5224]),],
        },
        "names": {56: ["San Juan", "San Juan", "San Juan", "San Juan", "San Juan", "Guaynabo", "Guaynabo", "Bayamón", "Bayamón", "Bayamón", "Bayamón", "Corozal", "Naranjito", "Toa Alta", "Toa Baja", "Toa Baja", "Dorado", "Dorado", "Cataño", "Vega Alta", "Vega Baja", "Manatí", "Manatí", "Morovis", "Orocovis", "Ciales", "Jayuya", "Barceloneta", "Florida", "Arecibo", "Arecibo", "Hatillo", "Hatillo", "Camuy", "Quebradillas", "Isabela", "Aguadilla", "Moca", "Moca", "Aguada", "Rincón", "Añasco", "Mayagüez", "Mayagüez", "Mayagüez", "San Sebastián", "Las Marías", "Maricao", "Hormigueros", "San Germán", "San Germán", "Cabo Rojo", "Lajas"], 57: ["Sabana Grande", "Guánica", "Yauco", "Guayanilla", "Guayanilla", "Lares", "Utuado", "Adjuntas", "Adjuntas", "Peñuelas", "Ponce", "Ponce", "Ponce", "Villalba", "Juana Díaz", "Santa Isabel", "Santa Isabel", "Coamo", "Salinas", "Aibonito", "Aibonito", "Barranquitas", "Comerío", "Aguas Buenas", "Cidra", "Cidra", "Cayey", "Guayama", "Guayama", "Maunabo", "Patillas", "Arroyo", "Caguas", "Caguas", "Juncos", "Gurabo", "San Lorenzo", "Yabucoa", "Las Piedras", "Las Piedras", "Humacao", "Naguabo", "Ceiba", "Ceiba", "Río Grande", "Luquillo", "Fajardo", "Culebra", "Vieques", "Canóvanas", "Loíza", "Carolina", "Carolina", "Carolina", "Trujillo Alto", "Trujillo Alto", "San Juan", "San Juan", "San Juan", "Guaynabo"]},
    },
    "1984-11-06": {
        "url": SITE + "Informe%20Estadstico/ESTADISTICAS%20DE%20LAS%20ELECCIONES%201984.pdf",
        "pages": (62, 63, 64),
        "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total.",
        "split": 2,
        "surnames": ("hernandez", "romero", "martin", "padilla"),
        "fixes": {(63, 30): (19658, [10000, 8245, 644, 769]), (63, 38): (10325, [5104, 4526, 387, 308])},
    },
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
COMMISSIONERS = {
    "1984-11-06": {"pages": (74, 75, 76), "split": 3, "triple": True, "surnames": ("fuster", "famadas", "catala", "viera")},
    "1992-11-03": {"pages": (83, 84, 85), "split": 3, "triple": True, "surnames": ("romero", "colorado", "garcia")},
    "1996-11-05": {"pages": (91, 92, 93), "split": 3, "triple": True, "surnames": ("romero", "benitez", "rodriguez")},
    "2000-11-07": {"pages": (91, 92, 93), "split": 3, "triple": True, "surnames": ("romero", "acevedo", "rodriguez")},
    "2004-11-02": {"pages": (90, 91, 92), "split": 3, "triple": True, "surnames": ("prats", "fortuno", "irizarry"), "settled": "The OCR misread a few cells, which were settled against the page image and the printed district rows: Moca 37 on page 90; Yauco 52, Juana Díaz 63, Villalba 65, Coamo 68, Aibonito 69, Naranjito 73, Cayey 77, Maunabo 92, and one printed district row on page 91; and Carolina 107 on page 92.", "fixes": {(90, 36): (2790, [986, 1765, 39]), (91, 5): (17857, [8688, 8644, 525]), (91, 21): (20856, [10645, 9861, 350]), (91, 30): (25744, [13788, 11006, 950]), (91, -2): (0, [132279, 131911, 6830]), (91, 45): (7685, [3647, 3768, 270]), (91, 16): (22981, [11187, 11353, 441]), (91, 18): (15616, [6991, 8369, 256]), (91, 22): (15717, [8831, 6358, 528]), (91, 26): (18266, [9858, 8028, 380])}},
    "2008-11-04": {
        "pages": (95, 96, 97),
        "island": "The municipio rows also sum exactly to the island row printed on page 95.",
        "surnames": ("salazar", "pierluisi", "martinez", "velazquez"),
    },
    "2012-11-06": {
        "pages": (35, 36, 37, 38),
        "island": "The municipio rows also sum exactly to the island row printed on page 35.",
        "surnames": ("pierluisi", "cox", "mercado", "guzman", "guillont", "cordova"),
    },
}
for day, election in COMMISSIONERS.items():
    ELECTIONS[f"{day}:commissioner"] = {key: value for key, value in ELECTIONS[day].items() if key not in ("pages", "surnames", "island", "fixes", "split")} | {"split": 2, "island": "The municipio rows sum exactly to the eight district rows, which together make up the island total."} | election


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


def solve_triples(cells: list[list[list[str]]]) -> tuple[int, list[int], list[int]] | None:
    values: list[int] = []
    weak: list[int] = []
    for index, columns in enumerate(cells):
        reads = [Counter(int(digits) for reading in column if (digits := re.sub(r"\D", "", reading))) for column in columns]
        good: Counter[int] = Counter()
        for a, na in reads[0].items():
            for b, nb in reads[1].items():
                if reads[2][a + b]:
                    good[a + b] += na + nb + reads[2][a + b]
                if reads[2][a - b]:
                    good[a - b] += na + nb + reads[2][a - b]
        if not good:
            weak.append(index)
            wide = [{int(alternative) for text in column for alternative in variants(str(text))} for column in reads]
            for a in wide[0]:
                for b in wide[1]:
                    if a + b in wide[2]:
                        good[a + b] += (a in reads[0]) + (b in reads[1]) + (a + b in reads[2])
        if not good:
            return None
        values.append(good.most_common(1)[0][0])
    return sum(values), values, weak


def read_page(image: Path, candidates: int, split: int, darkness: int = 215, triple: bool = False) -> list[dict[str, object]]:
    gray = np.asarray(Image.open(image).convert("L"))
    lead = 1 if triple else 2
    want = lead + 1 + split * candidates
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
            and (split >= 2 or re.search(r"\d\W{0,2}[iIl]?$", text[0].strip()))
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
        if not triple:
            cells.append(((cols[1], y0, cols[2], y1), True))
        row_boxes = []
        for candidate in range(candidates):
            for part in range(split):
                low = lead + split * candidate + part
                y0, y1 = band(cols[low], cols[low + 1], a, b)
                cells.append(((cols[low], y0, cols[low + 1], y1), True))
                if part == (split - 1 if triple else 0):
                    row_boxes.append((cols[low], y0, cols[low + (1 if triple else split)], y1))
        boxes.append(row_boxes)
    read = read_cells(cleaned, cells, PASSES)
    width = (0 if triple else 1) + split * candidates
    rows = []
    for index, (a, b, kind, text) in enumerate(kept):
        raw_cells = read[index * width : (index + 1) * width]
        if kind == "precinct" and not triple and not any(re.search(r"\d{3}", reading) for reading in raw_cells[0]):
            continue

        def arrange(source: list[list[str]]) -> list[list[str]]:
            if triple:
                return [source[split * candidate : split * (candidate + 1)] for candidate in range(candidates)]
            return [source[0]] + [
                [" ".join(source[1 + split * candidate + part][pass_] for part in range(split)) for pass_ in range(len(source[0]))]
                for candidate in range(candidates)
            ]

        solver = solve_triples if triple else solve_row
        solved = solver(arrange(raw_cells))
        if solved is None or solved[2]:
            more = read_cells(cleaned, cells[index * width : (index + 1) * width], EXTRA)
            again = solver(arrange([first + second for first, second in zip(raw_cells, more, strict=True)]))
            if again is not None and (solved is None or len(again[2]) <= len(solved[2])):
                solved = again
        if triple and kind == "precinct" and solved is None and not re.search(r"\d", text):
            continue
        rows.append({"kind": kind, "name": text, "solved": solved, "boxes": boxes[index], "y": (a, b)})
    return rows


def read_text_page(image: Path, candidates: int, cut: float = 0, patch: list[tuple[int, list[int]]] | None = None) -> list[dict[str, object]]:
    rows = []
    for scale in (1, 0.75, 1.25, 1.5, 0.6, 2, 0.5):
        source = Image.open(image).convert("L")
        if cut:
            source.paste(255, (0, 0, int(source.width * cut), source.height))
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
            head = fold("".join(texts[:3]))
            kind = "district" if head.startswith(("TOTALESDEL", "TOTALPOR")) else "precinct"
            if kind == "precinct" and fold(texts[0]).startswith(("DISTRITO", "GRAN")):
                continue
            digits = [word for word in words if re.search(r"\d", word[0])]
            if kind == "precinct" and len(digits) < 4 + candidates:
                continue
            figures = [(word, re.sub(r"[^\d.,%]", "", word[0]).rstrip(".,")) for word in digits]
            votes = [(word, int(re.sub(r"\D", "", figure))) for word, figure in figures if re.fullmatch(r"\d{1,3}(,\d{3})*|\d{4,6}", figure)]
            if cut:
                votes = [(word, int(re.sub(r"\D", "", figure))) for word, figure in figures if re.fullmatch(r"\d{1,3}(,\d{3})*|\d{4,6}|\d{1,3}\.\d{3}", figure)]
                for start in range(len(votes) - candidates):
                    if votes[start][1] == sum(value for _, value in votes[start + 1 : start + 1 + candidates]):
                        votes = votes[start : start + 1 + candidates]
                        break
            elif len(votes) == 2 + candidates and votes[0][1] < 200:
                votes = votes[1:]
            tall = [word for word, _ in votes if 15 <= word[4] <= 40] or [word for word in digits if 15 <= word[4] <= 40] or digits or words
            first = votes[0][0] if votes else words[-1]
            name = "TOTALES" if kind == "district" else " ".join(word[0] for word in words if word[1] + word[3] <= first[1])
            if len(votes) != 1 + candidates:
                found.append({"kind": kind, "name": name, "solved": None, "boxes": [], "y": (min(w[2] for w in tall), max(w[2] + w[4] for w in tall)), "tokens": [value for _, value in votes]})
                continue
            total, counts = votes[0][1], [value for _, value in votes[1:]]
            percents = [float("0" + figure.rstrip("%")) for _, figure in figures if re.fullmatch(r"\d{0,2}\.\d\d%?", figure)]
            ok = sum(counts) == total and (len(percents) != candidates or all(abs(count / total * 100 - percent) <= (0.0101 if cut else 0.0051) for count, percent in zip(counts, percents, strict=True)))
            boxes = [(w[1], w[2], w[1] + w[3], w[2] + w[4]) for w, _ in votes[1:]]
            found.append({"kind": kind, "name": name, "solved": (total, counts) if ok else None, "boxes": boxes, "y": (min(w[2] for w in tall), max(w[2] + w[4] for w in tall)), "tokens": [total, *counts]})
        found.sort(key=lambda row: row["y"][0])
        if not rows:
            rows = found
        else:
            for index, row in enumerate(rows):
                if row["solved"] is None:
                    match = next((other for other in found if (cut or other["name"] == row["name"]) and other["solved"] is not None and abs(other["y"][0] - row["y"][0]) < 15), None)
                    if match is not None:
                        rows[index] = match
            if cut:
                rows += [other for other in found if all(abs(row["y"][0] - other["y"][0]) >= 15 for row in rows)]
                rows.sort(key=lambda row: row["y"][0])
        if not cut and all(row["solved"] is not None for row in rows):
            break
    if cut:
        kept: list[dict[str, object]] = []
        for row in rows:
            if kept and abs(kept[-1]["y"][0] - row["y"][0]) < 15:
                if kept[-1]["solved"] is None:
                    kept[-1] = row
            else:
                kept.append(row)
        rows = kept
    for total, signature in patch or []:
        best = max((row for row in rows if row["solved"] is None or total == 0), key=lambda row: len(set(row["tokens"]) & set(signature)), default=None)
        if best is None or len(set(best["tokens"]) & set(signature)) < 2:
            if any(row["solved"] == (total, signature) for row in rows):
                continue
            raise ValueError(f"patch {total} {signature} matches no unsolved row")
        if total == 0:
            best["kind"] = "skip"
        else:
            best["solved"] = (total, signature)
            best["boxes"] = [(int(source.width * cut), best["y"][0] - 8, int(source.width * 0.97), best["y"][1] + 8)] * candidates
    rows = [row for row in rows if row["kind"] != "skip"]
    if cut:
        running = [0] * candidates
        for row in rows:
            if any(running) and (row["solved"][1] == running if row["solved"] is not None else sum(token in running for token in row["tokens"]) >= candidates - 1):
                row["kind"] = "district"
                row["solved"] = (sum(running), running)
                running = [0] * candidates
            elif row["solved"] is not None:
                running = [a + b for a, b in zip(running, row["solved"][1], strict=True)]
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
            starts: list[int] = []
            for number in election["pages"]:
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    prepare(pdf, number, election, image)
                with Image.open(image) as opened:
                    width, height = opened.size
                ordinal = 0
                districts = 0
                rows_read = read_text_page(image, candidates, election.get("cut", 0), election.get("patch", {}).get(number)) if election.get("text") else read_page(image, candidates, election["split"], triple=election.get("triple", False))
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
                        if "names" in election:
                            row["name"] = election["names"][number][ordinal - 1]
                        district.append(votes)
                        starts.append(len(rows))
                        rows.extend(
                            {"day": day, "page": number, "name": row["name"], "candidate": index, "votes": value, "total": total, "box": list(row["boxes"][index]), "width": width, "height": height}
                            for index, value in enumerate(votes)
                        )
                    elif row["kind"] == "district":
                        sums = [sum(votes[index] for votes in district) for index in range(candidates)]
                        row["solved"] = election.get("fixes", {}).get((number, -1 - districts), row["solved"])
                        districts += 1
                        if row["solved"] is not None and election.get("triple"):
                            for index in range(candidates):
                                gap = row["solved"][1][index] - sums[index]
                                options = [
                                    (at, int(alternative))
                                    for at, votes in enumerate(district)
                                    for alternative in variants(str(votes[index]))
                                    if len(alternative) == len(str(votes[index])) and int(alternative) - votes[index] == gap
                                ]
                                if gap and len(options) == 1:
                                    at, value = options[0]
                                    print("settled", day, number, rows[starts[at]]["name"], index, district[at][index], "->", value)
                                    district[at][index] = value
                                    rows[starts[at] + index]["votes"] = value
                                    sums[index] += gap
                        if row["solved"] is None or sums != row["solved"][1]:
                            raise ValueError(f"{day} page {number}: district precinct sums {sums} against printed {row['solved']}: {district}")
                        printed = [a + b for a, b in zip(printed, row["solved"][1], strict=True)]
                        found = [a + b for a, b in zip(found, sums, strict=True)]
                        district = []
                        starts = []
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
            summary="Puerto Rico governor and Resident Commissioner general elections by municipio from the State Elections Commission's statistical reports, read from the reports' page images and checked against each row's printed total and percentages",
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
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':PR:(governor|house:0$)'")
        )
        days = [(day, frame.filter(pl.col("day") == day)) for day in frame["day"].unique(maintain_order=True)]
        days += [(day, precincts.filter(pl.col("day") == day)) for day in precincts["day"].unique(maintain_order=True)]
        pages: dict[tuple[str, int], UUID] = {}
        for day, lines in days:
            day = (day,)
            election = ELECTIONS[day[0]]
            key = f"{day[0][:10]}:PR:house:0" if day[0].endswith(":commissioner") else f"{day[0]}:PR:governor"
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
                body = f"Votes for {'Resident Commissioner' if day[0].endswith(':commissioner') else 'governor'} by municipio summed from the electoral-precinct rows of the State Elections Commission's Informe Estadístico for the {day[0][:4]} general election{' (' + election['member'].removesuffix('.pdf').title() + ')' if 'member' in election else ''}, pages {pages_text}, a table of page images read by OCR. {"Each precinct row's íntegros and otros votes sum to the printed total for each candidate" if election.get("triple") else "Each precinct row's candidate votes match its printed percentages and do not exceed its printed total"}, and the precinct rows of every senatorial district sum to that district's printed totals row for each candidate. {election['island']} Each value's locator boxes the candidate's cells for the municipio's precinct rows on the first page where they appear; the sum also includes precinct rows on later pages and in other districts. {election.get('settled', '')} Write-in and blank votes are not in the table."
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
