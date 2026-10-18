import io
import itertools
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from difflib import SequenceMatcher
from math import cos, radians, sin
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image, ImageFilter

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.cambridge_sheets"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Cambridge Election Commission"
RESULTS = "https://www.cambridgema.gov/-/media/Files/electioncommission/electionresults/"
INVALID, EXHAUSTED = "INVALID", "EXHAUSTED"
SHEETS = {
    "2001-11-06:council": {
        "file": "2001officialcitycouncilresults.pdf",
        "page": 1,
        "rotate": 0,
        "existing": "alged:2511000_2001_11_cambridge_MA_City Council_synthetic at-large b_9",
        "seats": 9,
        "ballots": 17688,
        "invalid": 562,
        "total": 17688,
        "rows": [
            "Condit, James E., III",
            "Davis, Henrietta",
            "Decker, Marjorie C.",
            "Dixon, Vincent Lawrence",
            "Galluccio, Anthony D.",
            "Hall, Robert L.",
            "Horowitz, Jacob",
            "Iskovitz, Steve",
            "Jens, Steven E.",
            "King, Ethridge A.",
            "Maher, David P.",
            "Murphy, Brian",
            "Peixoto, Helder",
            "Pitkin, John",
            "Reeves, Kenneth E.",
            "Simmons, Denise",
            "Sullivan, Michael A.",
            "Toomey, Timothy J., Jr.",
            "Williamson, James M.",
            "*Position 1",
            "*Other positions",
            INVALID,
            EXHAUSTED,
        ],
    },
    "2003-11-04:council": {
        "file": "2003citycouncil.pdf",
        "page": 1,
        "rotate": 0,
        "existing": "alged:2511000_2003_11_cambridge_MA_City Council_synthetic at-large d_9",
        "seats": 9,
        "ballots": 20958,
        "invalid": 878,
        "total": 20958,
        "rows": [
            "Bellew, Carole K.",
            "Davis, Henrietta",
            "DeBergalis, Matt S.",
            "Decker, Marjorie C.",
            "Dixon, Vincent Lawrence",
            "Galluccio, Anthony D.",
            "Greenwood, Dan J.",
            "Hall, Robert L., Sr.",
            "Kelley, Craig A.",
            "King, Ethridge A., Jr.",
            "LaTremouille, Robert J.",
            "Maher, David P.",
            "Murphy, Brian",
            "Pitkin, John",
            "Reeves, Kenneth E.",
            "Simmons, Denise",
            "Smith, Aimee Louise",
            "Sullivan, Michael A.",
            "Taymorberry, Laurie",
            "Toomey, Timothy J., Jr.",
            "*Position 1",
            "*Other positions",
            EXHAUSTED,
            INVALID,
        ],
    },
    "2003-11-04:school": {
        "file": "2003schoolcommitteeresults.pdf",
        "page": 1,
        "rotate": 0,
        "existing": None,
        "seats": 6,
        "ballots": 20908,
        "invalid": 2210,
        "total": 20908,
        "rows": [
            "Craig, Christopher",
            "Fantini, Alfred B.",
            "Grassi, Joseph G.",
            "Harding, Richard, Jr.",
            "Lummis, Ben",
            "McGovern, Marc C.",
            "Price, Alan C.",
            "Walser, Nancy",
            "*Position 1",
            "*Other positions",
            INVALID,
            EXHAUSTED,
        ],
    },
    "2005-11-08:council": {
        "file": "2005officialmunicipalelectionresults.pdf",
        "page": 1,
        "rotate": 0,
        "existing": "alged:2511000_2005_11_cambridge_MA_City Council_synthetic at-large b_9",
        "seats": 9,
        "ballots": 16202,
        "invalid": 132,
        "total": 16202,
        "rows": [
            "Adkins, Lawrence J.",
            "Condit, James E., III",
            "Davis, Henrietta",
            "Decker, Marjorie C.",
            "Galluccio, Anthony",
            "Gordon, Jesse A.",
            "Green, Andre Lerone",
            "Hall, Robert Lee, Sr.",
            "Hees, Bill",
            "Kelley, Craig A.",
            "LaTremouille, Robert J.",
            "Maher, David P.",
            "Murphy, Brian",
            "Reeves, Kenneth E.",
            "Seidel, Sam",
            "Simmons, Denise",
            "Sullivan, Michael A.",
            "Toomey, Timothy J., Jr.",
            "*Position 1",
            "*Other positions",
            EXHAUSTED,
            INVALID,
        ],
    },
    "2005-11-08:school": {
        "file": "2005officialmunicipalelectionresults.pdf",
        "page": 3,
        "rotate": 90,
        "existing": None,
        "seats": 6,
        "ballots": 16189,
        "invalid": 719,
        "total": 15470,
        "rows": [
            "Fantini, Alfred B.",
            "Grassi, Joseph G.",
            "Harding, Richard, Jr.",
            "Lummis, Ben",
            "McGovern, Marc C.",
            "Nolan, Patricia",
            "Schuster, Luc",
            "Walser, Nancy",
            "*Position 1",
            "*Other positions",
            INVALID,
            EXHAUSTED,
        ],
    },
    "2007-11-06:council": {
        "file": "2007officialmunicipalelectionresults.pdf",
        "page": 1,
        "rotate": 0,
        "existing": "alged:2511000_2007_11_cambridge_MA_City Council_synthetic at-large d_9",
        "seats": 9,
        "ballots": 13721,
        "invalid": 88,
        "total": 13721,
        "rows": [
            "Davis, Henrietta",
            "Decker, Marjorie C.",
            "Galluccio, Anthony D.",
            "Janik, Jonathan S.",
            "Kelley, Craig A.",
            "Maher, David P.",
            "Moore, M. Kevin",
            "Moree, Gregg J.",
            "Murphy, Brian",
            "Podgers, Kathy",
            "Reeves, Kenneth E.",
            "Seidel, Sam",
            "Simmons, E. Denise",
            "Sullivan, Edward J.",
            "Toomey, Timothy J., Jr.",
            "Ward, Larry W.",
            "*Position 1",
            "*Other positions",
            EXHAUSTED,
            INVALID,
        ],
    },
    "2007-11-06:school": {
        "file": "2007officialmunicipalelectionresults.pdf",
        "page": 3,
        "rotate": 90,
        "existing": None,
        "seats": 6,
        "ballots": 13709,
        "invalid": 433,
        "total": 13709,
        "rows": [
            "Fantini, Alfred B.",
            "Grassi, Joseph G.",
            "Harding, Richard, Jr.",
            "Lemily Wiggins, Gail",
            "Malner, Stefan",
            "McGovern, Marc C.",
            "Nolan, Patricia",
            "Schuster, Luc",
            "Tauber, Nancy",
            "*Position 1",
            "*Other positions",
            INVALID,
            EXHAUSTED,
        ],
    },
    "2009-11-03:council": {
        "file": "2009citycouncilresults.pdf",
        "page": 1,
        "rotate": 270,
        "existing": "alged:2511000_2009_11_cambridge_MA_City Council_synthetic at-large b_9",
        "seats": 9,
        "ballots": 16073,
        "invalid": 118,
        "total": 16073,
        "rows": [
            "Adkins, Lawrence J.",
            "Cheung, Leland",
            "Davis, Henrietta",
            "Flanagan, Mark F.",
            "Glick, Silvia P.",
            "Kelley, Craig A.",
            "Leavitt, Neal W.",
            "Maher, David P.",
            "Marquardt, Charles J.",
            "Moree, Gregg J.",
            "Podgers, Kathy",
            "Reeves, Kenneth E.",
            "Seidel, Sam",
            "Simmons, E. Denise",
            "Stohlman, Thomas J., Jr.",
            "Sullivan, Edward J.",
            "Toomey, Timothy J., Jr.",
            "vanBeuzekom, Minka Y.",
            "Ward, Larry W.",
            "Williamson, James",
            "Decker, Marjorie C.",
            "*Other write-ins",
            EXHAUSTED,
            INVALID,
        ],
    },
    "2009-11-03:school": {
        "file": "2009SchoolCommittee_results.pdf",
        "page": 1,
        "rotate": 270,
        "existing": None,
        "seats": 6,
        "ballots": 16009,
        "invalid": 584,
        "total": 16009,
        "rows": [
            "Fantini, Alfred B.",
            "Grassi, Joseph G.",
            "Harding, Richard, Jr.",
            "McGovern, Marc C.",
            "Nolan, Patricia M.",
            "Stead, Charles L., Sr.",
            "Steinert, Alan, Jr.",
            "Tauber, Nancy",
            "Turkel, Alice L.",
            "Decker, Marjorie",
            "*Other positions",
            INVALID,
            EXHAUSTED,
        ],
    },
}
DIGITS = "-0123456789*"
SIMILAR = {
    "0": "86",
    "1": "47",
    "2": "7",
    "3": "85",
    "4": "19",
    "5": "638",
    "6": "580",
    "7": "142",
    "8": "0365",
    "9": "84",
}
METHOD = "Data comes from the Cambridge Election Commission's certified proportional representation count sheet, signed by the Director of the Count and the Auditor. The sheet exists only as a scan. Each number was read via OCR and checked against the sheet's arithmetic: every candidate's result equals the previous count's result plus the transfer, and every count adds up to the sheet's total. Count 1 is the tally of first choices. The quota is calculated by dividing the number of valid ballots by the number of seats plus one, dropping any fraction, and adding one. Elected candidates keep the quota once their surplus is transferred, and eliminated candidates show 0 in the count that moves their ballots. Unnamed write-ins are combined into one option. Exhausted counts are valid ballots with no continuing choice; invalid ballots are excluded from the valid total."
DECLARED = (
    "Lists only the elected candidates, without vote counts. The full count from the certified sheet is the default."
)
FIRST = "Contains first-choice votes only, as compiled by the American Local Government Elections Database. The Election Commission's certified count, including every round, is the default."
BOX_2005 = "The sheet's summary box lists 16,196 ballots cast and 16,064 valid ballots, but its count columns total 16,202 ballots. The printed quota of 1,608 matches the count columns. This set follows the count columns."


def fetch() -> None:
    for file in {str(sheet["file"]) for sheet in SHEETS.values()}:
        raw.fetch(SOURCE, RESULTS + file, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    def ocr(image: Image.Image, psm: str, scale: int) -> str:
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        padded = Image.new("L", (image.width + 40, image.height + 40), 255)
        padded.paste(image, (20, 20))
        buffer = io.BytesIO()
        padded.save(buffer, "PNG")
        config = ["-c", f"tessedit_char_whitelist={DIGITS}"] if psm != "label" else []
        return (
            subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "7" if psm == "label" else psm, *config],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )

    def readings(texts: list[str]) -> Counter[int]:
        found: Counter[int] = Counter()
        for text in texts:
            text = text.replace("*", "").replace(" ", "")
            if re.fullmatch(r"-?\d{1,6}", text):
                found[int(text)] += 1
        return found

    def variants(found: Counter[int]) -> set[int]:
        out = set()
        for value in list(found) + [-value for value in found]:
            digits, sign = str(abs(value)), "-" if value < 0 else ""
            for i, char in enumerate(digits):
                out |= {int(sign + digits[:i] + alt + digits[i + 1 :]) for alt in SIMILAR[char]}
                if len(digits) > 1:
                    out.add(int(sign + digits[:i] + digits[i + 1 :]))
            out.add(-value)
        return out - set(found)

    def digitize(key: str, sheet: dict) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            stem = Path(folder) / "page"
            subprocess.run(
                ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(sheet["page"]), "-l", str(sheet["page"])]
                + ["-singlefile", str(raw.cached(SOURCE, RESULTS + sheet["file"]).path), str(stem)],
                check=True,
            )
            upright = (
                Image.open(stem.with_suffix(".png")).convert("L").rotate(-sheet["rotate"], expand=True, fillcolor=255)
            )
            upright.load()
        best = None
        for tenth in range(-20, 21):
            turned = upright.rotate(tenth / 10, resample=Image.Resampling.BILINEAR, fillcolor=255)
            score = np.sort((np.asarray(turned) < 160).sum(axis=0))[-30:].sum()
            if best is None or score > best[0]:
                best = (score, tenth / 10, turned)
        _, angle, turned = best
        sharp = None
        for step in range(-40, 41):
            shear = step / 2000
            sheared = turned.transform(
                turned.size,
                Image.Transform.AFFINE,
                (1, 0, 0, shear, 1, -shear * turned.width / 2),
                resample=Image.Resampling.BILINEAR,
                fillcolor=255,
            )
            profile = (np.asarray(sheared)[:, 400:2400] < 160).sum(axis=1).astype(np.float64)
            if sharp is None or (profile**2).sum() > sharp[0]:
                sharp = ((profile**2).sum(), shear, sheared)
        _, shear, img = sharp
        dark = np.asarray(img) < 160
        height, width = dark.shape
        band = 60
        strength = {y: int((dark[y : y + band].sum(axis=0) > band * 0.85).sum()) for y in range(0, height - band, band)}
        typical = sorted(value for value in strength.values() if value >= 6)
        segments: list[list[int]] = []
        for y, value in strength.items():
            if value >= 6 and value >= typical[len(typical) // 2] / 2:
                if segments and y - segments[-1][1] <= band:
                    segments[-1][1] = y
                else:
                    segments.append([y, y])
        tables: list[tuple[list[int], list[tuple[int, int]], int, int]] = []
        for top, last in segments:
            bottom = last + band
            columns: list[list[int]] = []
            for x in np.flatnonzero(dark[top:bottom].sum(axis=0) > (bottom - top) * 0.8):
                if columns and x - columns[-1][-1] <= 4:
                    columns[-1].append(int(x))
                else:
                    columns.append([int(x)])
            rules: list[int] = []
            for x in (int(np.mean(column)) for column in columns):
                if not rules or x - rules[-1] >= 30:
                    rules.append(x)
            if len(rules) < 4:
                continue
            gaps = sorted(b - a for a, b in itertools.pairwise(rules) if b - a > 100)
            pitch = gaps[len(gaps) // 2]
            soft = dark[top:bottom].mean(axis=0)

            def snap(x: int, soft: np.ndarray = soft) -> int | None:
                low, high = max(0, x - 30), min(width, x + 30)
                found = low + int(np.argmax(soft[low:high]))
                return found if soft[found] > 0.35 else None

            filled = [rules[0]]
            for x in rules[1:]:
                while x - filled[-1] > pitch * 1.5:
                    filled.append(snap(filled[-1] + pitch) or filled[-1] + pitch)
                filled.append(x)
            rules = filled
            while rules[-1] + pitch < width - 20 and (x := snap(rules[-1] + pitch)) is not None:
                rules.append(x)
            if rules[1] - rules[0] > 200 and pitch > 200 and (x := snap(rules[0] - pitch)) is not None and x > 250:
                rules.insert(0, x)
            profile = dark[top:bottom, 20 : rules[0] - 8].sum(axis=1)
            lines = []
            y = 0
            while y < len(profile):
                if profile[y]:
                    end = y
                    while end < len(profile) and profile[end]:
                        end += 1
                    if end - y >= 12:
                        lines.append((top + y, top + end))
                    y = end
                else:
                    y += 1
            tables.append((rules, lines, top, bottom))
        pixels = img.load()
        if pixels is None:
            raise ValueError(key)
        for _, _, top, bottom in tables:
            for x in np.flatnonzero(dark[top:bottom].mean(axis=0) > 0.5):
                for xx in range(max(0, x - 2), min(width, x + 3)):
                    for yy in range(top, bottom):
                        pixels[xx, yy] = 255
        clean = np.asarray(img) < 160
        cells = []
        for t, (rules, lines, top, bottom) in enumerate(tables):
            centers = [(a + b) / 2 for a, b in lines]
            for column in range(len(rules) - 1):
                x0, x1 = rules[column], rules[column + 1]
                halves = [("r", x0, x1)] if x1 - x0 < 180 else [("t", x0, (x0 + x1) // 2), ("r", (x0 + x1) // 2, x1)]
                for kind, left, right in halves:
                    profile = clean[top:bottom, left + 5 : right - 2].sum(axis=1)
                    runs = []
                    y = 0
                    while y < len(profile):
                        if profile[y]:
                            end = y
                            while end < len(profile) and profile[end]:
                                end += 1
                            if end - y >= 8:
                                runs.append((top + y, top + end))
                            y = end
                        else:
                            y += 1
                    if not runs:
                        continue
                    middles = [(a + b) / 2 for a, b in runs]
                    offsets = sorted(min((m - c for m in middles), key=abs) for c in centers)
                    offset = offsets[len(offsets) // 2]
                    for row, center in enumerate(centers):
                        near = min(range(len(middles)), key=lambda i: abs(middles[i] - center - offset))
                        if abs(middles[near] - center - offset) < 11:
                            a, b = runs[near]
                            cells.append(((t, row, column, kind), (left + 2, a - 5, right + 1, b + 5)))

        def read(box: tuple[int, int, int, int]) -> list[str]:
            crop = img.crop(box)
            binary = crop.point(lambda level: 0 if level < 170 else 255)
            thin = crop.filter(ImageFilter.MaxFilter(3))
            return [ocr(crop, "7", 2), ocr(binary, "7", 3), ocr(binary, "8", 2), ocr(thin, "7", 3)]

        def label(t: int, line: tuple[int, int]) -> str:
            return ocr(img.crop((20, line[0] - 4, tables[t][0][0] - 6, line[1] + 4)), "label", 2)

        with ThreadPoolExecutor(12) as pool:
            texts = list(pool.map(read, [box for _, box in cells]))
            labels = [
                list(pool.map(lambda line, t=t: label(t, line), lines)) for t, (_, lines, _, _) in enumerate(tables)
            ]
        found = {where: readings(text) for (where, _), text in zip(cells, texts, strict=True)}
        boxes = dict(cells)
        rows = sheet["rows"]
        wanted = [norm(row.lstrip("*")) for row in rows]
        place: dict[tuple[int, int], int] = {}
        for t, names in enumerate(labels):
            filled = [sum(1 for where in found if where[:2] == (t, r) and found[where]) for r in range(len(names))]
            score = [[0.0] * (len(names) + 1) for _ in range(len(rows) + 1)]
            for i in range(len(rows) - 1, -1, -1):
                score[i][len(names)] = -1e9
                for j in range(len(names) - 1, -1, -1):
                    fit = SequenceMatcher(None, norm(names[j]), wanted[i]).ratio() + min(filled[j], 10) * 0.06
                    score[i][j] = max(score[i][j + 1], fit + score[i + 1][j + 1])
            i = j = 0
            while i < len(rows):
                fit = SequenceMatcher(None, norm(names[j]), wanted[i]).ratio() + min(filled[j], 10) * 0.06
                if score[i][j] == fit + score[i + 1][j + 1]:
                    place[(t, i)] = j
                    i += 1
                j += 1
        order = []
        for t, (rules, _, _, _) in enumerate(tables):
            singles = []
            for column in range(len(rules) - 1):
                if any(where[0] == t and where[2] == column and where[3] == "t" for where in boxes):
                    order.append((t, (column, "t"), (column, "r")))
                elif t == 0 and column == 0:
                    order.append((t, None, (column, "r")))
                else:
                    singles.append(column)
            order.extend((t, (a, "r"), (b, "r")) for a, b in zip(singles[0::2], singles[1::2], strict=False))

        def get(t: int, i: int, cell: tuple[int, str]) -> Counter[int]:
            return found.get((t, place[(t, i)], *cell), Counter())

        def support(counter: Counter[int], value: int) -> float:
            if counter.get(value):
                return counter[value]
            if not counter:
                return -1.0
            return -1.5 if value in variants(counter) else -4.0

        valid = sheet["ballots"] - sheet["invalid"]
        quota = valid // (sheet["seats"] + 1) + 1
        paths = []
        for i, row in enumerate(rows):
            beam: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]
            for t, transfer, result in order:
                current = get(t, i, result)
                moved = get(t, i, transfer) if transfer else None
                following: dict[tuple[int, ...], float] = {}
                for total, path in beam:
                    options = set(current) | variants(current) | ({0} if not path else set())
                    if not path:
                        ahead, step = get(order[1][0], i, order[1][2]), get(order[1][0], i, order[1][1] or order[1][2])
                        options |= {a - s for a in ahead for s in step}
                    if path:
                        options |= {path[-1] + x for x in (moved or {})} | {path[-1]}
                        options |= {path[-1] + x for x in variants(moved)} if moved else set()
                    for value in options:
                        if value < 0 or (path and row == INVALID and value != path[-1]):
                            continue
                        if path and row == EXHAUSTED and value < path[-1]:
                            continue
                        if path and row not in (INVALID, EXHAUSTED):
                            if (path[-1] == 0 and max(path) > 0 and value) or value - path[-1] > quota:
                                continue
                            if path[-1] == quota and value != quota:
                                continue
                        gained = support(current, value)
                        if path and moved is not None:
                            gained += support(moved, value - path[-1])
                        if following.get(path + (value,), -1e9) < total + gained:
                            following[path + (value,)] = total + gained
                beam = sorted(((s, p) for p, s in following.items()), key=lambda item: -item[0])[:300]
            paths.append(beam[:40])
        choice = [0] * len(rows)

        def sums() -> list[int]:
            return [sum(paths[i][choice[i]][1][k] for i in range(len(rows))) for k in range(len(order))]

        for _ in range(40):
            wrong = sum(1 for value in sums() if value != sheet["total"])
            if not wrong:
                break
            pick = None
            for i in range(len(rows)):
                keep = choice[i]
                for j in range(len(paths[i])):
                    choice[i] = j
                    fewer = sum(1 for value in sums() if value != sheet["total"])
                    loss = paths[i][keep][0] - paths[i][j][0]
                    if fewer < wrong and (pick is None or (fewer, loss) < pick[:2]):
                        pick = (fewer, loss, i, j)
                choice[i] = keep
            if pick is None:
                break
            choice[pick[2]] = pick[3]
        if any(value != sheet["total"] for value in sums()):
            raise ValueError(f"{key}: counts do not add up to {sheet['total']}: {sums()}")
        values = {row: list(paths[i][choice[i]][1]) for i, row in enumerate(rows)}
        candidates = [row for row in rows if not row.startswith("*") and row not in (INVALID, EXHAUSTED)]
        elected = [row for row in candidates if values[row][-1]]
        if len(elected) != sheet["seats"] or not any(values[row][-1] == quota for row in elected):
            raise ValueError(f"{key}: elected {elected} with quota {quota}")
        if values[INVALID][0] not in (0, sheet["invalid"]) or sheet["total"] - values[INVALID][0] != valid:
            raise ValueError(f"{key}: invalid {values[INVALID][0]} against {sheet['invalid']}")
        for k in range(len(order)):
            if sum(values[row][k] for row in rows if row != INVALID) != valid:
                raise ValueError(f"{key}: count {k + 1} does not add up to {valid} valid ballots")
        theta = radians(angle)
        cx, cy = width / 2, height / 2

        def back(x: float, y: float) -> tuple[float, float]:
            y += shear * x - shear * width / 2
            dx, dy = x - cx, y - cy
            return cx + dx * cos(theta) - dy * sin(theta), cy + dx * sin(theta) + dy * cos(theta)

        located: dict[str, list[list[float] | None]] = {}
        for i, row in enumerate(rows):
            located[row] = []
            for t, _, result in order:
                box = boxes.get((t, place[(t, i)], *result))
                if box is None:
                    located[row].append(None)
                    continue
                corners = [back(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
                located[row].append(
                    [
                        max(0.0, min(x for x, _ in corners) / width),
                        max(0.0, min(y for _, y in corners) / height),
                        min(1.0, max(x for x, _ in corners) / width),
                        min(1.0, max(y for _, y in corners) / height),
                    ]
                )
        return {"values": values, "boxes": located, "elected": elected, "quota": quota}

    sheets = {key: digitize(key, sheet) for key, sheet in SHEETS.items()}
    (PARSED / "sheets.json").write_text(json.dumps(sheets))


def emit() -> Path:
    sheets = json.loads((PARSED / "sheets.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cambridge's City Council elections of 2001 to 2009 and School Committee elections of 2003 to 2009, "
            "every count digitized from the Election Commission's certified count sheets",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr", "iii")]

        def proper(label: str) -> str:
            parts = [part.strip() for part in label.split(",")]
            return f"{parts[1]} {parts[0]}" + (f", {parts[2]}" if len(parts) > 2 else "")

        city = cs.area("us.place", "2511000")
        county = cs.area("us.county", "25017")
        whole = cs.unit("us.place", "2511000")
        school = cs.key("us.body", "MA:2511000:school-committee")
        stv = cs.key("us.electoral_system", "cambridge:stv")
        if {city, whole, school, stv} & cs.minted:
            raise ValueError("Cambridge, its School Committee, and cambridge:stv must already exist")
        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[city, county]],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, area == city))

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        pages: dict[tuple[str, int], UUID] = {}
        for key, entry in SHEETS.items():
            sheet: dict = entry
            day = date.fromisoformat(key[:10])
            kind = key[11:]
            record = sheets[key]
            values: dict[str, list[int]] = record["values"]
            boxes: dict[str, list[list[float] | None]] = record["boxes"]
            url = RESULTS + str(sheet["file"])
            pdf = raw.cached(SOURCE, url)
            if url not in sources:
                both = sum(other["file"] == sheet["file"] for other in SHEETS.values()) > 1
                seat = (
                    "City Council and School Committee"
                    if both
                    else "City Council"
                    if kind == "council"
                    else "School Committee"
                )
                title = (
                    f"{day:%B} {day.day}, {day.year} Cambridge municipal election: {seat}, certified proportional "
                    "representation count"
                )
                sources[url] = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=sources[url],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license="Public record",
                )
                artifacts[url] = cs.artifact(pdf, sources[url])
            artifact = artifacts[url]
            image = PAGES / f"{pdf.sha256}-{sheet['page']}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory() as folder:
                    stem = Path(folder) / "page"
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(sheet["page"]), "-l", str(sheet["page"])]
                        + ["-singlefile", str(pdf.path), str(stem)],
                        check=True,
                    )
                    Image.open(stem.with_suffix(".png")).rotate(-sheet["rotate"], expand=True).save(image)
            if (url, sheet["page"]) not in pages:
                page = cs.page(artifact, sheet["page"])
                pages[(url, sheet["page"])] = page
                with Image.open(image) as opened:
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact,
                        page_number=sheet["page"],
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=opened.width,
                        height=opened.height,
                    )
            page = pages[(url, sheet["page"])]
            election = cs.key("us.election", f"{day}:MA:2511000")
            if election in cs.minted:
                raise ValueError(f"{key}: the {day} Cambridge election must already exist")
            if sheet["existing"] is None:
                contest = cs.key("us.contest", f"{day}:MA:2511000:{kind}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=school,
                    district_area_id=city,
                    magnitude=sheet["seats"],
                    round_number=1,
                    electoral_system_id=stv,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, f"{day.year} Cambridge School Committee election")
                cs.add("slug", path=f"us/ma/cambridge/{day}-local/school-committee", entity_id=contest)
            else:
                contest = cs.key("us.contest", sheet["existing"])
                if contest in cs.minted:
                    raise ValueError(f"{key}: {sheet['existing']} must already exist")
                for values_row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id, (select count(*) from note n where n.entity_id = r.id and n.kind = 'caveat')
                       from result_set r where contest_id = %s and is_default
                         and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    fields = (
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
                    cs.add("result_set", **dict(zip(fields, values_row[:10], strict=True)), is_default=False)
                    cs.add(
                        "note",
                        entity_id=values_row[0],
                        kind="caveat",
                        body_md=DECLARED if values_row[8] == 0 else FIRST,
                    )
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            options: dict[str, UUID] = {}
            for row in values:
                if row in (INVALID, EXHAUSTED):
                    continue
                if row.startswith("*"):
                    options[row] = cs.key("us.option", f"{contest}:write_in:scattered")
                    if list(options.values()).count(options[row]) == 1:
                        cs.add(
                            "contest_option",
                            id=options[row],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    continue
                label = proper(row)
                words = spell(label)
                same = [option for (_, last), option in current.items() if last.replace("-", "") == words[-1]]
                if not same:
                    same = [
                        option
                        for (_, last), option in current.items()
                        if SequenceMatcher(None, last, words[-1]).ratio() >= 0.8
                    ]
                if len(same) > 1:
                    same = [current[(words[0], words[-1])]] if (words[0], words[-1]) in current else []
                if sheet["existing"] is not None:
                    if len(same) != 1:
                        raise ValueError(f"{key}: no single option for {label}")
                    options[row] = same[0]
                    continue
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _ in known}
                local = {person for person, here in known if here}
                if len(local) == 1:
                    matches = local
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, label)
                    people.setdefault((words[0], words[-1]), set()).add((person, True))
                options[row] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[row], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[row],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if sheet["existing"] is not None:
                if len(set(options.values())) != len(current) + 1:
                    raise ValueError(f"{key}: the sheet's candidates do not match the contest's options")
                declared = {
                    option
                    for (option,) in conn.execute(
                        """select o.option_id from outcome o join result_set r on r.id = o.result_set_id
                           where r.contest_id = %s and r.is_default and r.completeness = 0 and o.kind = 'elected'""",
                        [contest],
                    ).fetchall()
                }
                if declared and declared != {options[row] for row in record["elected"]}:
                    raise ValueError(f"{key}: elected candidates differ from the declared set")

            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            if key == "2005-11-08:council":
                cs.add("note", entity_id=result, kind="caveat", body_md=BOX_2005)
            valid = sheet["ballots"] - sheet["invalid"]
            rows: list[tuple[UUID | None, int, int, int, list[float] | None]] = [
                (None, 3, 0, sheet["ballots"], None),
                (None, 5, 0, sheet["invalid"], boxes[INVALID][0] if values[INVALID][0] else None),
                (None, 4, 1, valid, None),
            ]
            for number in range(1, len(values[EXHAUSTED]) + 1):
                rows.append((None, 21, number, values[EXHAUSTED][number - 1], boxes[EXHAUSTED][number - 1]))
            merged: dict[UUID, list[tuple[int, list[float] | None]]] = {}
            for row, option in options.items():
                cells = list(zip(values[row], boxes[row], strict=True))
                if option in merged:
                    merged[option] = [(a + b, None) for (a, _), (b, _) in zip(merged[option], cells, strict=True)]
                else:
                    merged[option] = cells
            for option, cells in merged.items():
                for number, (votes, box) in enumerate(cells, 1):
                    if number == 1 or cells[number - 2][0]:
                        rows.append((option, 1, number, votes, box))
            winners = {options[row] for row in record["elected"]}
            for option, cells in merged.items():
                for outcome in (
                    ("won", "elected") if option in winners else ("lost",) if cells[-1][0] else ("eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for option, measure, number, votes, box in rows:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=whole,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=8 if box is not None else 1 if measure in (4, 21) or option is not None else 0,
                    value=votes,
                )
                if box is not None:
                    cs.add(
                        "value_locator",
                        result_set_id=result,
                        unit_id=whole,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        page_id=page,
                        source_id=None,
                        x0=box[0],
                        y0=box[1],
                        x1=box[2],
                        y1=box[3],
                    )
        return cs.write()
