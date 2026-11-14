import itertools
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image, ImageOps

from ea import scans

DPI = 300
HALF = 0.0065


def words(path: Path, page: int) -> list[tuple[str, float, float, float, float]]:
    text = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    size = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', text)
    if size is None:
        return []
    width, height = float(size[1]), float(size[2])
    return [
        (word, float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height)
        for x0, y0, x1, y1, word in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', text
        )
    ]


def rules(dark: np.ndarray, top: int, bottom: int, count: int) -> tuple[list[int], float] | None:
    share = dark[top:bottom].mean(axis=0)
    found: list[list[int]] = []
    for x in np.flatnonzero(share > 0.5):
        if found and x - found[-1][-1] <= 12:
            found[-1].append(int(x))
        else:
            found.append([int(x)])
    peaks = [round(sum(group) / len(group)) for group in found]
    gaps = [b - a for a, b in itertools.pairwise(peaks) if 60 <= b - a <= 110]
    if not gaps:
        return None
    pitch = float(np.median(gaps))

    def walk(start: float, low: float) -> list[float]:
        edges = [start]
        while len(edges) < count:
            near = [peak for peak in peaks if -low * pitch <= peak - edges[-1] - pitch <= 0.15 * pitch]
            edges.append(min(near, key=lambda peak: abs(peak - edges[-1] - pitch)) if near else edges[-1] + pitch)
        return edges

    def strength(edges: list[float]) -> float:
        return sum(float(share[max(0, round(x) - 8) : round(x) + 9].max(initial=0)) for x in edges)

    for low in (0.15, 0.25):
        best = max((walk(start, low) for peak in peaks for start in (peak, peak - pitch) if start >= 0), key=strength)
        if strength(best) >= 0.8 * count:
            break
    else:
        return None
    return [round(x) for x in (*best, best[-1] + pitch)], strength(best)


def band(dark: np.ndarray, edges: list[int], middle: int, reach: int) -> tuple[int, int] | None:
    inside = np.zeros(dark.shape[1], dtype=bool)
    for left, right in itertools.pairwise(edges):
        inside[left + 8 : right - 8] = True
    profile = dark[middle - reach : middle + reach][:, inside].mean(axis=1)
    runs: list[list[int]] = []
    for y in np.flatnonzero(profile > 0.01):
        if runs and y - runs[-1][-1] <= 2:
            runs[-1].append(int(y))
        else:
            runs.append([int(y)])
    runs = [run for run in runs if len(run) >= 8 and profile[run].max() < 0.5]
    if not runs:
        return None
    best = min(runs, key=lambda run: abs((run[0] + run[-1]) / 2 - reach))
    return middle - reach + best[0] - 3, middle - reach + best[-1] + 4


def tesseract(image: Image.Image) -> str:
    with tempfile.TemporaryDirectory() as folder:
        ImageOps.expand(image, border=20, fill=255).save(f"{folder}/c.png")
        out = subprocess.run(
            ["tesseract", f"{folder}/c.png", "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    return re.sub(r"\D", "", out)


def step(name: str) -> str:
    manifest = scans.load(name)
    columns = manifest["row_columns"]
    folder = scans.WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    overrides = json.loads((folder / "overrides.json").read_text()) if (folder / "overrides.json").exists() else {}
    fixed = {
        (item["page"], item.get("label", item["county"]), item["column"]): item["value"]
        for item in overrides.get("cells", [])
    }
    accepted: dict[str, str] = overrides.get("accept", {})
    codes = scans.counties(manifest)
    held = scans.official_totals({**manifest, "pages": [{"page": 0, "columns": columns}]})
    contests = {column["contest"] for column in columns if column["contest"] is not None}
    cells: dict[tuple[str, str], dict[str, object]] = {}
    review: list[dict[str, object]] = []
    disputed: list[dict[str, object]] = []
    for spec in manifest["pages"]:
        image = scans.render(manifest, spec, DPI)
        dark = np.asarray(image) < 140
        width, height = image.size
        layer = words(scans.artifact(manifest, spec).path, spec["page"])
        found = [
            rules(
                dark,
                round(row["y"] * height) - round(0.04 * height),
                round(row["y"] * height) - round(0.004 * height),
                len(columns),
            )
            for row in spec.get("rows", [])
        ]
        anchor = max((each for each in found if each is not None), key=lambda each: each[1], default=None)
        for place, row in enumerate(spec.get("rows", [])):
            code = codes.get(scans.slug(row["county"]).replace(" ", ""))
            if code is None:
                review.extend(
                    {
                        "series": f"{contest}|",
                        "page": spec["page"],
                        "county": row["county"],
                        "problem": "no county code",
                    }
                    for contest in contests
                )
                continue
            near = [
                (abs(other - place), each[0])
                for other, each in enumerate(found)
                if each is not None and anchor is not None and abs(each[0][0] - anchor[0][0]) <= 40
            ]
            edges = min(near)[1] if near else None
            if edges is None:
                review.extend(
                    {
                        "series": f"{contest}|",
                        "page": spec["page"],
                        "county": row["county"],
                        "problem": "no column rules",
                    }
                    for contest in contests
                )
                continue
            half = round(row.get("half", HALF) * height)
            found_band = band(dark, edges, round(row["y"] * height), half)
            y0, y1 = (
                (found_band[0] / height, found_band[1] / height)
                if found_band is not None
                else (row["y"] - row.get("half", HALF), row["y"] + row.get("half", HALF))
            )
            for index, column in enumerate(columns):
                if column["contest"] is None:
                    continue
                left, right = edges[index] + 4, edges[index + 1] - 4
                top, bottom = round(y0 * height), round(y1 * height)
                while left < right and dark[top:bottom, left].mean() > 0.7:
                    left += 1
                while right > left and dark[top:bottom, right - 1].mean() > 0.7:
                    right -= 1
                x0, x1 = left / width, right / width
                crop = image.crop((round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)))
                ocr = tesseract(crop)
                text = re.sub(
                    r"\D",
                    "",
                    "".join(w for w, a, b, c, d in layer if x0 <= (a + c) / 2 <= x1 and y0 <= (b + d) / 2 <= y1),
                )
                series = f"{column['contest']}|{column['candidate']}"
                key = (spec["page"], row.get("label", row["county"]), column["candidate"])
                if key in fixed:
                    value, how = fixed[key], "read"
                elif ocr == text:
                    value, how = (int(ocr) if ocr else None), "agreed"
                else:
                    value, how = (int(ocr) if ocr else None), "single"
                    disputed.append(
                        {
                            "series": series,
                            "page": spec["page"],
                            "county": row["county"],
                            "label": row.get("label", row["county"]),
                            "column": column["candidate"],
                            "tesseract": ocr,
                            "text": text,
                            "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                        }
                    )
                if value is None:
                    continue
                if (series, code) in cells:
                    cells[series, code]["votes"] = int(str(cells[series, code]["votes"])) + value
                    continue
                cells[series, code] = {
                    "series": series,
                    "contest": column["contest"],
                    "candidate": column["candidate"],
                    "writein": False,
                    "page": spec["page"],
                    "section": str(spec["page"]),
                    "code": code,
                    "votes": value,
                    "half": False,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "how": how,
                    "note": None,
                }
    frame = pl.DataFrame(list(cells.values()), infer_schema_length=None)
    lines = [f"{len(cells)} cells, {len(disputed)} read once"]
    for (series,), group in frame.group_by("series", maintain_order=True):
        target = held.get(str(series))
        total = group["votes"].sum()
        lines.append(
            f"{series}: {group.height} counties, {total} against {target}{' (accepted)' if series in accepted else ''}"
        )
        if target is not None and total != target and series not in accepted:
            review.append({"series": series, "sum": total, "held": target, "counties": group.height})
            review.extend(item for item in disputed if item["series"] == series)
    (folder / "review.json").write_text(json.dumps(review, indent=2))
    lines.append(f"{len(review)} in review")
    frame.write_parquet(folder / "tables.parquet")
    return "\n".join(lines)
