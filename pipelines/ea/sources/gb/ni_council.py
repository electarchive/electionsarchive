import csv
import io
import json
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import date
from html import unescape
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb.ni_assembly import party_of as assembly_party
from ea.store import DATA, connect

SOURCE = "gb.ni_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SCAN_PAGES = DATA / "pages" / "gb.ni_scans"
EONI = "https://www.eoni.org.uk"
PAGE_2023 = f"{EONI}/results-data/local-council-elections-2023-results/"
DEAS = f"{EONI}/media/aormmh5f/deas-by-district-website.xls"
OPENDATANI_2014 = "https://www.opendatani.gov.uk/dataset/northern-ireland-local-councils-election-22-may-2014"
WAYBACK = "https://web.archive.org/web/2022if_/"


def links(url: str, pattern: str) -> list[str]:
    page = raw.fetch(SOURCE, url, suffix=".html").path.read_text(encoding="utf-8", errors="replace")
    return sorted(set(re.findall(pattern, page)))


def fetch() -> None:
    raw.fetch(SOURCE, DEAS, suffix=".xls")
    for year in ARK:
        raw.fetch(
            SOURCE,
            f"https://web.archive.org/web/2020if_/https://www.ark.ac.uk/elections/xls/lgov/{year}.rar",
            suffix=".rar",
        )
    for path in links(PAGE_2023, r'href="(/media/[^"]+\.(?:xlsx|pdf))"'):
        raw.fetch(SOURCE, f"{EONI}{path}", suffix=Path(path).suffix)
    for url in links(
        OPENDATANI_2014,
        r'href="(https://admin\.opendatani\.gov\.uk/dataset/[^"]+/download/(?:2014-ni-councils-elections|local-council-elections-2014)-[^"]+\.csv)"',
    ):
        time.sleep(2)
        for attempt in range(4):
            try:
                raw.fetch(SOURCE, f"{WAYBACK}{url}", suffix=".csv")
                break
            except httpx.HTTPError as error:
                print(f"RETRY {attempt} {url}: {error}")
                time.sleep(60 * (attempt + 1))


DISTRICTS = {
    "N09000001": "Antrim and Newtownabbey",
    "N09000002": "Armagh City, Banbridge and Craigavon",
    "N09000003": "Belfast",
    "N09000004": "Causeway Coast and Glens",
    "N09000005": "Derry City and Strabane",
    "N09000006": "Fermanagh and Omagh",
    "N09000007": "Lisburn and Castlereagh",
    "N09000008": "Mid and East Antrim",
    "N09000009": "Mid Ulster",
    "N09000010": "Newry, Mourne and Down",
    "N09000011": "Ards and North Down",
}


def squeeze(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", plain.replace("&", "and"))


def number(cell: str | None) -> float | None:
    if cell is None:
        return None
    text = re.sub(r"[\s,]", "", cell)
    return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text, flags=re.IGNORECASE) else None


def area_table() -> dict[str, tuple[str, str, str]]:
    sheet = pl.read_excel(raw.cached(SOURCE, DEAS).path, has_header=False, infer_schema_length=0)["column_0"].to_list()[
        1:
    ]
    table: dict[str, tuple[str, str, str]] = {}
    pending: list[tuple[str, str]] = []
    for line in sheet:
        code, _, label = line.partition(" - ")
        if code.startswith("N10"):
            pending.append((code, label))
        else:
            for dea_code, dea_label in pending:
                table[squeeze(dea_label)] = (dea_code, dea_label.title(), code)
            pending = []
    return table


def tie(stage_totals: dict[int, float], valid: float, width: int = 12) -> list[int]:
    tied = []
    for stage in sorted(stage_totals):
        if abs(stage_totals[stage] - valid) > 0.01 * (width + 1):
            break
        tied.append(stage)
    return tied


def grid_sheet(grid: list[list[str | None]], dea: str, scan: int = 14, width: int = 26) -> dict[str, object]:
    grid = [row[:width] for row in grid]

    def find(label: str, row: list[str | None]) -> int | None:
        return next(
            (index for index, cell in enumerate(row) if cell is not None and cell.strip().lower().startswith(label)),
            None,
        )

    facts: dict[str, float] = {}
    for row in grid[:scan]:
        for index, cell in enumerate(row):
            if cell is None:
                continue
            label = cell.strip().lower()
            key = next(
                (
                    key
                    for key, words in (
                        ("electorate", ("eligible electorate", "total no of electors", "no. of electors")),
                        ("polled", ("votes polled", "total votes polled")),
                        ("seats", ("number to be elected", "number of members to be elected")),
                        ("invalid", ("invalid votes", "number of invalid votes")),
                        (
                            "valid",
                            (
                                "total valid votes",
                                "valid votes",
                                "total vailid votes",
                                "number of valid votes",
                                "vaild votes",
                            ),
                        ),
                        ("quota", ("electoral quota",)),
                    )
                    if any(label.startswith(word) for word in words)
                ),
                "seats" if re.match(r"number\b.*\bele(?:c|tc)", label) and "elector" not in label else None,
            )
            if key is not None:
                value = next((number(c) for c in row[index + 1 :] if number(c) is not None), None)
                facts[key] = value
    header = next(
        index
        for index, row in enumerate(grid)
        if any(
            c is not None and c.strip().lower().startswith(("1st pref", "first pref", "1st stage", "stage 1;"))
            for c in row
        )
    )
    first = find("1st pref", grid[header])
    if first is None:
        first = find("first pref", grid[header])
    if first is None:
        first = find("1st stage", grid[header])
    if first is None:
        first = find("stage 1;", grid[header])
    name_col = next(
        index
        for row in (grid[header], grid[header - 1])
        for index in [find("candidate", row) or find("names of candidate", row) or find("name of candidate", row)]
        if index is not None
    )
    description_col = name_col + 1
    totals_row = next(
        (
            index
            for index in range(header + 1, len(grid))
            if (grid[index][description_col] or "").strip().lower().startswith("totals")
        ),
        len(grid),
    )
    nontransferable_row = next(
        index
        for index in range(header + 1, totals_row)
        if (grid[index][description_col] or "").strip().lower().startswith("non")
        and "tran" in (grid[index][description_col] or "").lower()
    )
    rows = [
        row
        for row in grid[header + 1 : nontransferable_row]
        if (row[name_col] or "").strip() not in ("", "0") and number(row[name_col]) is None
    ]
    marks = [name_col, *(index for index in range(name_col + 1, len(rows[0])) if rows[0][index] == rows[0][name_col])]
    total_cols = []
    for block, mark in enumerate(marks):
        column = first if block == 0 else mark + 3
        bound = marks[block + 1] - 2 if block + 1 < len(marks) else len(grid[0])
        while column < bound and sum(number(row[column]) or 0.0 for row in rows) > 0:
            total_cols.append(column)
            column += 2
    stage_totals = {
        stage: sum(number(row[col]) or 0.0 for row in rows) + (number(grid[nontransferable_row][col]) or 0.0)
        for stage, col in enumerate(total_cols, start=1)
    }
    tied = tie(stage_totals, facts["valid"], len(rows))
    printed = next(
        (
            number(grid[index][first])
            for index in range(header + 1, len(grid))
            if (grid[index][description_col] or "").strip().lower() in ("total", "totals")
        ),
        None,
    )
    if (
        not tied
        and stage_totals
        and printed is not None
        and printed != facts["valid"]
        and abs(stage_totals[1] - printed) < 0.5
    ):
        tied = tie(stage_totals, printed, len(rows))
        facts["caveat"] = (
            f"The sheet prints {facts['valid']:,.0f} valid votes, but its own totals row and the candidates' first "
            f"preferences give {printed:,.0f}; the valid votes stored are {printed:,.0f}."
        )
        facts["valid"] = printed
    candidates = [
        {
            "name": row[name_col].strip(),
            "party": (row[description_col] or "").strip(),
            "flag": (row[0] or "").strip().lower(),
            "totals": [number(row[col]) or 0.0 for col in total_cols[: len(tied)]],
        }
        for row in rows
    ]
    exhausted = [number(grid[nontransferable_row][col]) or 0.0 for col in total_cols[: len(tied)]]
    return {
        "dea": dea,
        "facts": facts,
        "stages": len(total_cols),
        "tied": len(tied),
        "candidates": candidates,
        "exhausted": exhausted,
    }


def pdf_words(path: Path) -> list[tuple[float, float, float, float, str]]:
    html = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [
        (float(a), float(b), float(c), float(d), unescape(text))
        for a, b, c, d, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', html
        )
    ]


def pdf_sheet(path: Path, known: set[str]) -> dict[str, object]:
    words = pdf_words(path)
    layout = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    facts: dict[str, float] = {}
    for key, pattern in (
        ("electorate", r"Eligible Electorate\s+(\d+)"),
        ("seats", r"Number to be Elected\s+(\d+)"),
        ("invalid", r"Invalid Votes\s+(\d+)"),
        ("quota", r"Electoral Quota of\s+(\d+)"),
        ("polled", r"Votes Polled\s+(\d+)"),
        ("valid", r"Total Valid Votes\s+(\d+)"),
    ):
        facts[key] = float(re.search(pattern, layout).group(1))
    dea = re.search(r"District Electoral Area of\s+(.+?)\s{2,}", layout + "  ").group(1).strip()
    anchor = next(word for word in words if word[4] == "Candidate")
    description_x = next(word for word in words if word[4] == "Description" and abs(word[1] - anchor[1]) < 2)[0]
    stop = next(word[1] for word in words if word[4] == "Non" and word[1] > anchor[1])
    totals_y = next(word[1] for word in words if word[4] == "Totals" and word[1] > stop)
    edges = sorted(word[2] for word in words if abs(word[1] - totals_y) < 2.5 and number(word[4]) is not None)
    first_edge = edges[0]
    rows: list[list[tuple[float, float, float, float, str]]] = []
    for word in sorted((w for w in words if anchor[1] + 4 < w[1] < stop - 2), key=lambda w: w[1]):
        if rows and abs(rows[-1][0][1] - word[1]) < 2.5:
            rows[-1].append(word)
        else:
            rows.append([word])
    non_row = [w for w in words if abs(w[1] - stop) < 2.5]
    cells: list[dict[str, object]] = []
    clean: set[str] = set()

    def values(row: list[tuple[float, float, float, float, str]]) -> dict[int, float]:
        found: dict[int, float] = {}
        for _, _, xmax, _, text in row:
            amount = number(text)
            if amount is None or xmax < first_edge - 20:
                continue
            column = min(range(len(edges)), key=lambda index: abs(edges[index] - xmax))
            if abs(edges[column] - xmax) <= 4:
                found[column] = amount
        return found

    for row in rows:
        left = sorted((w for w in row if number(w[4]) is None or w[2] < first_edge - 20), key=lambda w: w[0])
        marker = [w for w in left if w[2] < anchor[0] - 2]
        body = [w for w in left if w[2] >= anchor[0] - 2]
        if not body or not any(w[4] for w in body):
            continue
        name_words = []
        description_words = []
        region = [word for word in body if word[0] >= description_x - 0.5]
        overlapping = [
            word
            for word in region
            if any(other is not word and other[0] < word[2] - 0.5 and word[0] < other[2] - 0.5 for other in region)
        ]
        dropped: list[tuple[float, float, float, float, str]] = []
        if overlapping and squeeze(" ".join(word[4] for word in region)) not in known:
            dropped = next(
                (
                    [word]
                    for word in overlapping
                    if squeeze(" ".join(other[4] for other in region if other is not word)) in known
                ),
                [],
            )
        for word in body:
            if word in dropped:
                name_words.append(word[4])
            elif word[0] >= description_x - 0.5:
                description_words.append(word[4])
            elif (
                word[2] > description_x + 1
                and word[0] < description_x
                and not any(abs(w[0] - description_x) < 0.6 for w in body)
            ):
                estimate = round((description_x - word[0]) / (word[2] - word[0]) * len(word[4]))
                options = []
                for cut in sorted(range(1, len(word[4])), key=lambda position: abs(position - estimate)):
                    candidate = f"{word[4][cut:]} " + " ".join(w[4] for w in body if w[0] >= description_x - 0.5)
                    options.append((cut, candidate.strip()))
                chosen = next(((cut, text) for cut, text in options if squeeze(text) in known), None)
                cut = chosen[0] if chosen else estimate
                name_words.append(word[4][:cut])
                description_words.append(word[4][cut:])
            else:
                name_words.append(word[4])
        flag = "".join(w[4] for w in marker)
        if any(abs(w[0] - description_x) < 0.6 for w in body):
            clean.add(squeeze(" ".join(description_words)))
        cells.append(
            {
                "name": " ".join(name_words).strip(),
                "party": " ".join(description_words).strip(),
                "marker": flag,
                "values": values(row),
            }
        )
    exhausted = values(non_row)
    columns = []
    for column in range(len(edges)):
        if sum(float(cell["values"].get(column, 0.0)) for cell in cells) > 0:
            columns.append(column)
        else:
            break
    return {"dea": dea, "facts": facts, "cells": cells, "columns": columns, "exhausted": exhausted, "clean": clean}


def finish(
    year: int,
    dea: str,
    facts: dict[str, float],
    candidates: list[dict[str, object]],
    exhausted: list[float],
    stages: int,
    url: str,
    areas: dict[str, tuple[str, str, str]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    key = squeeze(dea)
    if key not in areas:
        raise ValueError(f"{year} {dea}: no DEA in the official list")
    dea_code, dea_name, council_code = areas[key]
    completed = 0
    listed = 0
    for candidate in candidates:
        if year == 2011 and not candidate["party"].strip():
            words = re.sub(r"^[^A-Za-z]+", "", candidate["name"]).replace(",", " ").split()
            surname = squeeze(
                words[0]
                if len(words) > 1 and len(words[-1]) == 1
                else words[-1]
                if "," not in candidate["name"]
                else words[0]
            )
            found = [
                (full, label)
                for full, label in WP_PARTIES.get(f"{year}|{round(candidate['totals'][0])}", [])
                if squeeze(full.split()[-1])[:3] == surname[:3]
            ]
            if len(found) == 1:
                listed += 1
                candidate["name"] = f"{found[0][0].split()[-1]}, {' '.join(found[0][0].split()[:-1])}"
                candidate["party"] = "" if found[0][1].startswith("Independent") else found[0][1]
        candidate["name"] = re.sub(r"^[^A-Za-z]+", "", candidate["name"])
        words = candidate["name"].split()
        given = candidate["name"].partition(",")[2].strip()
        if (not given and (len(words) == 1 or year == 2014)) or (given and len(given) <= 2):
            keys = {squeeze(word) for word in re.split(r"[\s,]+", candidate["name"]) if word}
            found = [
                full
                for full in FULL_NAMES.get(f"{year}|{round(candidate['totals'][0])}", [])
                if squeeze(full.split()[-1]) in keys
            ]
            if len(found) == 1:
                candidate["name"] = found[0]
                completed += 1
                words = []
        if "," not in candidate["name"] and len(words) > 1:
            lead = 0
            while lead < len(words) - 1 and (
                words[lead].isupper()
                and len(words[lead]) > 1
                or (lead == 0 and year == 2011 and not any(w.isupper() for w in words[1:]))
            ):
                lead += 1
            if (
                year == 2011
                and lead == 1
                and words[0].lower() in ("o", "ni", "mac", "de", "van", "nic", "mag", "ó")
                and len(words) > 2
            ):
                lead = 2
            candidate["name"] = f"{' '.join(words[:lead])}, {' '.join(words[lead:])}"
    seats = int(facts["seats"])
    tied = len(candidates[0]["totals"])
    last = [float(candidate["totals"][-1]) for candidate in candidates]
    flagged = [bool(candidate["elected"]) for candidate in candidates]
    derived: list[bool] = [False] * len(candidates)
    notes: list[str] = []
    if sum(flagged) > seats:
        raise ValueError(f"{year} {dea}: {sum(flagged)} marked elected for {seats} seats")
    if sum(flagged) < seats:
        vacancies = seats - sum(flagged)
        continuing = [index for index, amount in enumerate(last) if amount > 0 and not flagged[index]]
        if len(continuing) < vacancies or len(continuing) > vacancies + 1:
            raise ValueError(f"{year} {dea}: {len(continuing)} continuing for {vacancies} vacancies")
        if len(continuing) == vacancies + 1:
            continuing.remove(min(continuing, key=lambda index: last[index]))
        for index in continuing:
            flagged[index] = True
            derived[index] = True
        notes.append(
            "The published sheet stops before the last stage. The candidates continuing at its last tied stage are treated as "
            "elected when they equal the vacancies, after excluding the lowest where one too many continues."
        )
    if listed:
        notes.append(
            f"The ARK sheet prints surnames only and no party for {listed} candidates; their names and parties come from Wikipedia's council election tables, matched on surname and first-preference votes."
        )
    if completed:
        notes.append(
            f"The result sheet prints surnames only for {completed} candidates; their given names come from Wikipedia's council election tables, matched on surname and first-preference votes."
        )
    if stages > tied:
        notes.append(
            f"The sheet has {stages} stages, but stage {tied + 1} does not add up to the valid votes, so only stages 1 to {tied} are stored."
        )
    unit = {
        "year": year,
        "council_code": council_code,
        "dea_code": dea_code,
        "dea": dea_name,
        "seats": seats,
        "electorate": facts.get("electorate"),
        "polled": facts.get("polled"),
        "valid": facts.get("valid"),
        "invalid": facts.get("invalid"),
        "quota": facts.get("quota"),
        "stages": tied,
        "source_url": url,
        "note": " ".join([*notes, *([facts["caveat"]] if facts.get("caveat") else [])]) or None,
    }
    rows = []
    for position, candidate in enumerate(candidates, start=1):
        eliminated = not flagged[position - 1] and last[position - 1] == 0
        for stage, amount in enumerate(candidate["totals"], start=1):
            rows.append(
                {
                    "year": year,
                    "dea_code": dea_code,
                    "number": position,
                    "name": candidate["name"],
                    "party": candidate["party"],
                    "outcome": "elected" if flagged[position - 1] else ("eliminated" if eliminated else "lost"),
                    "derived": derived[position - 1],
                    "stage": stage,
                    "votes": round(float(amount), 2),
                }
            )
    for stage, amount in enumerate(exhausted, start=1):
        rows.append(
            {
                "year": year,
                "dea_code": dea_code,
                "number": 0,
                "name": None,
                "party": None,
                "outcome": None,
                "derived": False,
                "stage": stage,
                "votes": round(float(amount), 2),
            }
        )
    return unit, rows


def parse_2023() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    areas = area_table()
    index = {
        f"{EONI}{path}": raw.cached(SOURCE, f"{EONI}{path}")
        for path in links(PAGE_2023, r'href="(/media/[^"]+\.(?:xlsx|pdf))"')
        if "result-sheet" in path
    }
    units: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    pdfs = [(url, artifact) for url, artifact in index.items() if url.endswith(".pdf") and "result-sheet" in url]
    known = {squeeze(text) for text in ("SINN FÉIN", "ALLIANCE", "INDEPENDENT")}
    for _, artifact in pdfs:
        try:
            known |= pdf_sheet(artifact.path, known)["clean"]
        except AttributeError, StopIteration:
            continue
    for url, artifact in index.items():
        if url.endswith(".xlsx"):
            for sheet_name in fastexcel.read_excel(artifact.path).sheet_names:
                grid = [
                    list(row)
                    for row in pl.read_excel(
                        artifact.path, sheet_name=sheet_name, has_header=False, infer_schema_length=0
                    ).iter_rows()
                ]
                sheet = grid_sheet(grid, sheet_name)
                name = next(
                    (
                        next(cell for cell in row[position + 1 :] if cell)
                        for row in grid[:4]
                        for position, label in enumerate(row)
                        if label is not None
                        and label.strip().lower().startswith("district electoral area")
                        and any(row[position + 1 :])
                    ),
                    sheet_name,
                )
                candidates = [{**c, "elected": c["flag"] == "elected"} for c in sheet["candidates"]]
                unit, rows = finish(
                    2023, name.strip(), sheet["facts"], candidates, sheet["exhausted"], sheet["stages"], url, areas
                )
                units.append(unit)
                totals.extend(rows)
        else:
            try:
                sheet = pdf_sheet(artifact.path, known)
            except AttributeError, StopIteration:
                print(f"IMAGE ONLY {url}")
                continue
            columns = sheet["columns"]
            stage_totals = {}
            for stage, column in enumerate(columns, start=1):
                stage_totals[stage] = sum(float(cell["values"].get(column, 0.0)) for cell in sheet["cells"]) + float(
                    sheet["exhausted"].get(column, 0.0)
                )
            tied = len(tie(stage_totals, sheet["facts"]["valid"], len(sheet["cells"])))
            candidates = [
                {
                    "name": cell["name"],
                    "party": cell["party"],
                    "elected": "Elected" in cell["marker"]
                    or (cell["marker"].isdigit() and float(cell["values"].get(columns[tied - 1], 0.0)) > 0),
                    "totals": [float(cell["values"].get(column, 0.0)) for column in columns[:tied]],
                }
                for cell in sheet["cells"]
            ]
            exhausted = [float(sheet["exhausted"].get(column, 0.0)) for column in columns[:tied]]
            unit, rows = finish(2023, sheet["dea"], sheet["facts"], candidates, exhausted, len(columns), url, areas)
            units.append(unit)
            totals.extend(rows)
    return units, totals


def parse_2014(areas: dict[str, tuple[str, str, str]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    page = raw.cached(SOURCE, OPENDATANI_2014).path.read_text(encoding="utf-8", errors="replace")
    urls = sorted(
        set(re.findall(r'href="(https://admin\.opendatani\.gov\.uk/dataset/[^"]+/download/[^"]+\.csv)"', page))
    )
    facts_url = next(url for url in urls if "vote-counts" in url)
    table = list(
        csv.DictReader(io.StringIO(raw.cached(SOURCE, f"{WAYBACK}{facts_url}").path.read_bytes().decode("cp1252")))
    )
    facts = {squeeze(row["District Electoral Area"]): row for row in table}
    units: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    for url in urls:
        filename = url.rsplit("/", 1)[-1]
        if not filename.startswith("2014-ni-councils-elections-") or "vote-counts" in filename:
            continue
        tokens = filename.removeprefix("2014-ni-councils-elections-").removesuffix(".csv").split("-")
        dea = next((" ".join(tokens[k:]) for k in range(len(tokens)) if squeeze("".join(tokens[k:])) in areas), None)
        if dea is None:
            raise ValueError(f"{filename}: no DEA")
        row = facts[squeeze(dea)]
        grid = list(csv.reader(io.StringIO(raw.cached(SOURCE, f"{WAYBACK}{url}").path.read_bytes().decode("cp1252"))))
        header = grid[0]
        total_cols = [
            index
            for index, label in enumerate(header)
            if re.fullmatch(r"Stage \d+ - (?:Total|1st Preference)", label.strip())
        ]
        body = [line for line in grid[1:] if line and line[0].strip().isdigit()]
        non_row = next(line for line in grid if line and line[0].strip().lower().startswith("non"))
        candidates = []
        for line in body:
            status = line[4].strip()
            stage_excluded = re.search(r"Excluded - Stage (\d+)", status)
            totals_list: list[float] = []
            previous = 0.0
            for position, column in enumerate(total_cols):
                amount = number(line[column]) if column < len(line) else None
                if amount is None:
                    amount = (
                        0.0
                        if (stage_excluded and position + 1 > int(stage_excluded.group(1))) or previous == 0.0
                        else previous
                    )
                totals_list.append(amount)
                previous = amount
            candidates.append(
                {
                    "name": f"{line[1].strip()}, {line[2].strip()}" if line[2].strip() else line[1].strip(),
                    "party": line[3].strip(),
                    "elected": status.startswith("Elected"),
                    "totals": totals_list,
                }
            )
        exhausted = [number(non_row[column]) or 0.0 for column in total_cols]
        stage_totals = {
            stage: sum(candidate["totals"][stage - 1] for candidate in candidates) + exhausted[stage - 1]
            for stage in range(1, len(total_cols) + 1)
        }
        totals_row = next(line for line in grid if line and line[0].strip().lower().startswith("totals"))
        valid = float(number(totals_row[total_cols[0]]))
        printed = float(row["Total Valid Votes"])
        discrepancy = None
        if abs(valid - printed) > 0.5:
            discrepancy = (
                f"The summary file prints {printed:,.0f} valid votes, but the result sheet's first preferences total {valid:,.0f}. "
                "The summary's valid and total poll figures are left out because they contradict the count."
            )
        tied = len(tie(stage_totals, valid, len(candidates)))
        later = [
            stage
            for stage in stage_totals
            if stage > tied + 1 and abs(stage_totals[stage] - valid) <= 0.01 * (len(candidates) + 1)
        ]
        if tied == 0 or later:
            print(
                f"UNTIED 2014 {dea}: tied through stage {tied}, ties again at {later}, stage sums {[round(v, 2) for v in stage_totals.values()]} against {valid}"
            )
            continue
        for candidate in candidates:
            candidate["totals"] = candidate["totals"][:tied]
        unit, rows = finish(
            2014,
            dea,
            {
                "seats": float(row["Number Elected"]),
                "electorate": float(row["Eligible Electorate"]),
                "polled": None if discrepancy else float(row["Votes Polled"]),
                "valid": None if discrepancy else valid,
                "caveat": discrepancy,
                "invalid": float(row["Invalid Votes"]),
                "quota": float(row["Electoral Quota"]),
            },
            candidates,
            exhausted[:tied],
            len(total_cols),
            f"{WAYBACK}{url}",
            areas,
        )
        units.append(unit)
        totals.extend(rows)
    return units, totals


ARK = {
    2011: "lg11",
    2005: "lg05",
    2001: "lg01",
    1997: "lg97",
    1993: "lg93",
    1989: "lg89",
    1985: "lg85",
    1981: "lg81",
    1977: "lg77",
    1973: "lg73",
}
WP_PARTIES: dict[str, list[tuple[str, str]]] = json.loads((Path(__file__).parent / "ni_council_wp.json").read_text())
FULL_NAMES: dict[str, list[str]] = json.loads((Path(__file__).parent / "ni_council_names.json").read_text())


def parse_ark(year: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    url = f"https://web.archive.org/web/2020if_/https://www.ark.ac.uk/elections/xls/lgov/{year}.rar"
    folder = PARSED / f"ark-{year}"
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run(["unar", "-q", "-o", str(folder), str(raw.cached(SOURCE, url).path)], check=True)
    units: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    failed: list[str] = []
    for path in sorted(path for path in folder.rglob("*.xls*") if path.name.lower().startswith(f"{ARK[year]}-")):
        _, abbr, *rest = path.stem.split("-")
        rest = [word for word in rest if word.lower() != "corrected"]
        abbr = {code.upper(): code for code in LEGACY_NAMES}.get(abbr.upper(), abbr)
        era = "1973:" if year < 1985 else "1985:" if year < 1993 else ""
        grid = [list(row) for row in pl.read_excel(path, has_header=False, infer_schema_length=0).iter_rows()]
        dea = None
        for row in grid:
            for text in (cell for cell in row if isinstance(cell, str)):
                if text.lower().startswith("district electo") and dea is None:
                    dea = re.sub(r"^district electo\w* area\W*(?:of\s+|for\s+)?", "", text, flags=re.IGNORECASE).strip()
        dea = dea or " ".join(rest)
        if era == "1973:" and len(rest) == 2 and rest[0].lower() == "area":
            dea = f"Area {rest[1].upper()}"
        try:
            sheet = grid_sheet(grid, dea, 40, 80)
            if sheet["tied"] < sheet["stages"]:
                raise ValueError(f"tied through stage {sheet['tied']} of {sheet['stages']}")
            candidates = [
                {**c, "elected": re.fullmatch(r"e|elected|e\d+|e'.*|.*stage e", c["flag"]) is not None}
                for c in sheet["candidates"]
                if re.search("[A-Za-z]", c["name"])
            ]
            unit, rows = finish(
                year,
                dea,
                sheet["facts"],
                candidates,
                sheet["exhausted"],
                sheet["stages"],
                url,
                {squeeze(dea): (f"{era}{abbr}/{slug('-'.join(rest))}", dea, abbr)},
            )
        except (ValueError, KeyError, StopIteration, IndexError, TypeError) as error:
            print(f"FAILED {year} {path.name}: {error!r}")
            failed.append(path.name)
            continue
        unit["note"] = " ".join(filter(None, [unit["note"], f"Workbook {path.name} in the ARK archive."]))
        units.append(unit)
        totals.extend(rows)
    for unit in units:
        unit["council_name"] = LEGACY_NAMES[unit["council_code"]]
    (PARSED / f"ark-{year}-failed.json").write_text(json.dumps(failed))
    return units, totals


LEGACY = {
    "antrim": "ANT",
    "ards": "ARD",
    "armagh": "ARM",
    "ballymena": "BMA",
    "ballymoney": "BMY",
    "banbridge": "BRG",
    "belfast": "BT",
    "carrickfergus": "CAR",
    "castlereagh": "CAS",
    "coleraine": "COL",
    "cookstown": "COO",
    "craigavon": "CRA",
    "derry": "DE",
    "down": "DOW",
    "dungannon": "DUN",
    "fermanagh": "FER",
    "larne": "LAR",
    "limavady": "LIM",
    "lisburn": "LIS",
    "magherafelt": "MAG",
    "moyle": "MOY",
    "newry": "NaM",
    "newtownabbey": "NEW",
    "north": "NoD",
    "omagh": "OMA",
    "strabane": "STR",
}


LEGACY_NAMES = {
    "ANT": "Antrim Borough Council",
    "ARD": "Ards Borough Council",
    "ARM": "Armagh City and District Council",
    "BMA": "Ballymena Borough Council",
    "BMY": "Ballymoney Borough Council",
    "BRG": "Banbridge District Council",
    "BT": "Belfast City Council",
    "CAR": "Carrickfergus Borough Council",
    "CAS": "Castlereagh Borough Council",
    "COL": "Coleraine Borough Council",
    "COO": "Cookstown District Council",
    "CRA": "Craigavon Borough Council",
    "DE": "Derry City Council",
    "DOW": "Down District Council",
    "DUN": "Dungannon and South Tyrone Borough Council",
    "FER": "Fermanagh District Council",
    "LAR": "Larne Borough Council",
    "LIM": "Limavady Borough Council",
    "LIS": "Lisburn City Council",
    "MAG": "Magherafelt District Council",
    "MOY": "Moyle District Council",
    "NaM": "Newry and Mourne District Council",
    "NEW": "Newtownabbey Borough Council",
    "NoD": "North Down Borough Council",
    "OMA": "Omagh District Council",
    "STR": "Strabane District Council",
}


def parse_2011_official() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sheets = json.loads((Path(__file__).parent / "ni_council_sheets.json").read_text())["2011"]
    ark = {
        (path.stem.split("-")[1], squeeze("".join(path.stem.split("-")[2:]))): "-".join(path.stem.split("-")[2:])
        for path in (PARSED / "ark-2011").rglob("lg11-*.xls*")
    }
    units: list[dict[str, object]] = []
    totals: list[dict[str, object]] = []
    for sheet_url, page, label in sheets:
        url = f"https://web.archive.org/web/2022if_/{sheet_url}"
        try:
            artifact = raw.cached(SOURCE, url)
        except KeyError, FileNotFoundError:
            print(f"MISSING {label}")
            continue
        try:
            abbr = LEGACY[page.split()[1].lower()]
            lines = [
                line
                for line in subprocess.run(
                    ["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, text=True, check=True
                ).stdout.splitlines()
                if line.strip()
            ]
            if not lines or not any(re.match(r"\s*Elected\s+No\s+Candidate", line) for line in lines):
                print(f"SCAN {label} {artifact.path.name}")
                continue
            dea = (
                re.sub(
                    r"^(?:District Electo\w+ Area|Constituency)(?:\s+of)?\s+", "", lines[1].strip(), flags=re.IGNORECASE
                )
                .title()
                .replace("'S", "'s")
            )
            facts: dict[str, float] = {}
            for line in lines[1:10]:
                for key, pattern in (
                    ("electorate", r"Eligible Electorate\s+(\d+)"),
                    ("seats", r"Number to be Elected\s+(\d+)"),
                    ("invalid", r"Invalid Votes\s+(\d+)"),
                    ("quota", r"Electoral Quota of\s+(\d+)"),
                    ("polled", r"Votes Polled\s+(\d+)"),
                    ("valid", r"Total Valid Votes\s+(\d+)"),
                ):
                    found = re.search(pattern, line)
                    if found:
                        facts[key] = float(found.group(1))
            header = next((line for line in lines if re.match(r"\s*Elected\s+No\s+Candidate", line)), None)
            if header is None:
                print(f"NOHEADER {label}: {len(lines)} lines {artifact.path.name}")
                continue
            _left, right = header.index("Candidate"), header.index("Description")
            ends = [m.end() for m in re.finditer(r"1st Preference|Total", header)]

            def by_stage(line: str, start: int = 0) -> list[float]:
                cells: list[float | None] = [None] * len(ends)
                for m in re.finditer(r"(?<![\d.])-?\d+\.\d\d(?![\d.])", line[start:]):
                    column = min(range(len(ends)), key=lambda k: abs(ends[k] - (start + m.end())))
                    if abs(ends[column] - (start + m.end())) <= 6:
                        cells[column] = float(m.group())
                return [0.0 if cell is None or cell < 0 else cell for cell in cells]

            candidates = []
            exhausted: list[float] = []
            for line in lines:
                row = re.match(r"\s*(Elected|Excluded)?\s*(\d+)\s", line)
                if line.lstrip().startswith("Non transferable"):
                    exhausted = by_stage(line)
                    exhausted[0] = 0.0
                elif (
                    row
                    and row.group(2) != "0"
                    and re.search(r"[A-Za-z]{2}", line[row.end() : row.end() + 30])
                    and re.search(r"(?<![\d.])-?\d+\.\d\d(?![\d.])", line)
                ):
                    body = line[row.end() :]
                    name = re.split(r"\s{2,}", body.lstrip(), maxsplit=1)[0]
                    first = re.search(
                        r"(?<![\d.])-?\d+\.\d\d(?![\d.])",
                        line[row.end() + len(body) - len(body.lstrip()) + len(name) :],
                    )
                    candidates.append(
                        {
                            "name": name.strip(),
                            "party": line[row.end() + len(body) - len(body.lstrip()) + len(name) :][
                                : first.start() if first else 0
                            ].strip(),
                            "elected": row.group(1) == "Elected",
                            "totals": by_stage(line, right),
                        }
                    )
            if not exhausted or not candidates:
                print(f"UNREADABLE {label}")
                continue
            for candidate in candidates:
                candidate["totals"] = (candidate["totals"] + [0.0] * len(exhausted))[: len(exhausted)]
            stage_totals = {
                stage: sum(c["totals"][stage - 1] for c in candidates) + exhausted[stage - 1]
                for stage in range(1, len(exhausted) + 1)
            }
            if "valid" not in facts or "seats" not in facts:
                print(f"FACTS {label}: {facts}")
                continue
            tied = len(tie(stage_totals, facts["valid"], len(candidates)))
            if tied < len(exhausted):
                print(
                    f"UNTIED OFFICIAL {label}: tied through stage {tied} of {len(exhausted)} cands {len(candidates)} sums {[round(v, 2) for v in stage_totals.values()][:4]} valid {facts['valid']}"
                )
                continue
            wanted = squeeze(label.removesuffix(" Result Sheet"))
            matches = sorted((key for (member, key) in ark if member == abbr and wanted.endswith(key)), key=len)
            code = f"{abbr}/{slug(ark[(abbr, matches[-1])] if matches else dea)}"
            try:
                unit, rows = finish(
                    2011, dea, facts, candidates, exhausted, len(exhausted), url, {squeeze(dea): (code, dea, abbr)}
                )
            except ValueError as error:
                print(f"FAILED OFFICIAL {label}: {error}")
                continue
            unit["council_name"] = LEGACY_NAMES[abbr]
            units.append(unit)
            totals.extend(rows)
        except (AttributeError, IndexError, KeyError, ValueError, StopIteration) as error:
            print(f"UNREADABLE {label}: {error!r}")

    return units, totals


DAYS = {
    2023: date(2023, 5, 18),
    2019: date(2019, 5, 2),
    2014: date(2014, 5, 22),
    2011: date(2011, 5, 5),
    2005: date(2005, 5, 5),
    2001: date(2001, 6, 7),
    1997: date(1997, 5, 21),
    1993: date(1993, 5, 19),
    1989: date(1989, 5, 17),
    1985: date(1985, 5, 15),
    1981: date(1981, 5, 20),
    1977: date(1977, 5, 18),
    1973: date(1973, 5, 30),
}
COUNCILS = {
    "N09000001": "Antrim and Newtownabbey Borough Council",
    "N09000002": "Armagh City, Banbridge and Craigavon Borough Council",
    "N09000003": "Belfast City Council",
    "N09000004": "Causeway Coast and Glens Borough Council",
    "N09000005": "Derry City and Strabane District Council",
    "N09000006": "Fermanagh and Omagh District Council",
    "N09000007": "Lisburn and Castlereagh City Council",
    "N09000008": "Mid and East Antrim Borough Council",
    "N09000009": "Mid Ulster District Council",
    "N09000010": "Newry, Mourne and Down District Council",
    "N09000011": "Ards and North Down Borough Council",
}
BODY_SIZES = {"N09000003": 60, "N09000002": 41, "N09000010": 41}
SHORT_NAMES = ((r"green party", "Green"), (r"people before profit", "PBP"), (r"progressive unionist", "PUP"))
EXTRA_PARTIES = (
    (r"democratic ulster unionist - d\.u\.p", "Democratic Unionist Party"),
    (r"^all$", "Alliance Party"),
    (r"^sf$", "Sinn Féin"),
    (r"^grn$", "Green Party Northern Ireland"),
    (r"^con$", "Conservative"),
    (r"^pbpa$", "People Before Profit Alliance"),
    (r"^ind$|indepdendent", None),
    (r"^democratic union", "Democratic Unionist Party"),
    (r"^social$", "Social Democratic and Labour Party"),
    (r"^gre$", "Green Party Northern Ireland"),
    (r"^cons\. ?& ?unionist", "Conservative"),
    (r"^cross[- ]community la|^ccla$", "Cross-Community Labour Alignment"),
    (r"^democrats (and|&) vetera", "Democrats and Veterans Party"),
    (r"^0$", None),
    (r"^ni2[12]$|northern ireland 21", "NI21"),
    (r"^-$|^upu$|^aa$|^up$|^0\s+0$|^official anti-trimble", None),
    (r"^valarie$", "Sinn Féin"),
    (r"^ap$", "Alliance Party"),
    (r"^gp$", "Green Party Northern Ireland"),
    (r"^spni$", "Socialist Party"),
    (r"^i$|^ind\.$|^indepentent$", None),
    (r"^newtownabbey ratepayers", "Newtownabbey Ratepayers Association"),
    (r"^social democratic labour party$", "Social Democratic and Labour Party"),
    (r"aspire to better", "Aspire to Better"),
    (r"^british national party$", "British National Party"),
    (r"community partnership", "Community Partnership (Northern Ireland)"),
    (r"^[eé]ir[ií]g[ií]$", "Éirígí"),
    (r"fermanagh against fracking", "Fermanagh Against Fracking"),
    (r"^gr+en party$", "Green Party Northern Ireland"),
    (r"^ni cons$", "Conservative"),
    (r"republican network for unity", "Republican Network for Unity"),
    (r"^ukip|united king\w* independence", "UK Independence Party"),
    (r"^irsp|irish republican socialist", "Irish Republican Socialist Party"),
)


HISTORIC_PARTIES = (
    (r"^unitedunionistassemblyparty$", "United Unionist Assembly Party"),
    (r"^nor?thernirelandunionistparty$", "Northern Ireland Unionist Party"),
    (r"^(?:ul)?uuup$|^unitedu[nl]\w*uu[uc]|^unitedulun$|^untiedunuuuc$", "United Ulster Unionist"),
    (r"^van|^vupp$", "Vanguard"),
    (
        r"^(?:o|of|off|on)(?:ul|u)?u?n?(?:uuuc)?$|^ouu|^ou$|^uu(?!uc$)|^ulun?$|^ulu$|^offic?i?al(?:unionist|ulster)|^ulster(?:uni|unio|llninni)|ulsterun|^mayoruup$|^farmerunionist$",
        "Ulster Unionist Party",
    ),
    (
        r"^(?:d|du|dun|dulun|loydu|condu)(?:uuuc|uuc)?$|^dup$|^democratic,?uni|^ulsterdemocracticunionist|demoraticunion",
        "Democratic Unionist Party",
    ),
    (r"^a$|^ap$|^alliance|alliance$|^allliance", "Alliance Party"),
    (r"^(?:the)?wp(?:r)?(?:ep)?clubs|^repclubswp$|^wp$|^twp$", "Workers' Party"),
    (r"^repclubs?$|^therepclubs$", "Republican Clubs"),
    (r"^replp$", "Republican Labour"),
    (r"^sf$", "Sinn Féin"),
    (r"^sdl[po]$|^spdl$", "Social Democratic and Labour Party"),
    (
        r"^n?ilp$|^nlip$|^nllp$|^nilabourparty$|^northernirelandlabourparty|^labourpartyofnorthernireland$|^lpni$|^newtownabbeylabourpartynilp$",
        "Northern Ireland Labour Party",
    ),
    (r"^upni|^upn$", "Unionist Party of Northern Ireland"),
    (r"^iip", "Irish Independence"),
    (r"^irsp$|^irishrepsocp$", "Irish Republican Socialist Party"),
    (
        r"^pro(?:g|gressive|gressuve|grssive|eressive)?u(?:n|nionist|party)?\w*$|^progun|^prou$|^unionistprogressiveparty$",
        "Progressive Unionist Party",
    ),
    (r"^ulsterpopularunionist", "Ulster Popular Unionist"),
    (r"^ulsterloyalistdemocratic|^uldp", "Ulster Democratic"),
    (r"^comm(?:unist)?p(?:arty)?ofir|^cpi$", "Communist Party of Ireland"),
    (r"^peoplesdemocracy$|^pd$", "People's Democracy"),
    (r"^ecolog|^ep$", "Ecology Party"),
    (r"labourparty87$|^lp87$", "Labour '87"),
    (r"^democraticleft|^dl$", "Democratic Left"),
    (r"^militantlabour$", "Militant Labour"),
    (r"^nationalfront$|^nf$|britishnatfront", "National Front"),
    (r"^ulsterliberalparty$|^lib$", "Ulster Liberal"),
    (r"^nat$", "Nationalist Party"),
)


def party_of(description: str, year: int | None = None) -> str | None:
    text = re.sub(r"\s+", " ", description.strip().lower())
    for pattern, canonical in EXTRA_PARTIES:
        if re.search(pattern, text):
            return canonical
    try:
        return assembly_party(description)
    except ValueError:
        if year is None or year >= 2005:
            raise
    squashed = re.sub(r"[^a-z,]", "", text)
    return next((canonical for pattern, canonical in HISTORIC_PARTIES if re.search(pattern, squashed)), None)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


def display_word(word: str) -> str:
    if re.fullmatch(r"(?:[A-Z]\.)+|[A-Z]", word):
        return word
    parts = []
    for part in word.split("-"):
        if re.search(r"[A-Z]{2}", part):
            found = re.fullmatch(r"(MC|Mc|O['’])(.+)", part)
            part = found.group(1).capitalize() + found.group(2).capitalize() if found else part.capitalize()
        parts.append(part)
    return "-".join(parts)


def display(name: str) -> str:
    surname, comma, given = name.partition(",")
    words = (f"{given.strip()} {surname.strip()}" if comma else surname.strip()).split()
    return " ".join(display_word(word) for word in words)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    areas = area_table()
    units, totals = parse_2023()
    more_units, more_totals = parse_2014(areas)
    units += more_units
    totals += more_totals
    official_units, official_totals = parse_2011_official()
    ark_units, ark_totals = parse_ark(2011)
    covered = {unit["dea_code"] for unit in official_units}
    units += official_units + [unit for unit in ark_units if unit["dea_code"] not in covered]
    totals += official_totals + [row for row in ark_totals if row["dea_code"] not in covered]
    for year in (2005, 2001, 1997, 1993, 1989, 1985, 1981, 1977, 1973):
        ark_units, ark_totals = parse_ark(year)
        units += ark_units
        totals += ark_totals
    scanned = DATA / "parsed" / "gb.ni_scans"
    units = pl.DataFrame(units, infer_schema_length=None)
    totals = pl.DataFrame(totals, infer_schema_length=None)
    if (scanned / "units.parquet").exists():
        read = pl.read_parquet(scanned / "units.parquet")
        keys = read.select("year", "dea_code")
        units = pl.concat(
            [units.join(keys, on=["year", "dea_code"], how="anti"), read.drop("reader")], how="diagonal_relaxed"
        )
        totals = pl.concat(
            [totals.join(keys, on=["year", "dea_code"], how="anti"), pl.read_parquet(scanned / "totals.parquet")],
            how="diagonal_relaxed",
        )
    units.write_parquet(PARSED / "units.parquet")
    totals.write_parquet(PARSED / "totals.parquet")
    print(units.group_by("year").len().sort("year"))


VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
DERIVED = 1
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"


def emit() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland council elections by District Electoral Area with every STV count, from the Electoral Office's result sheets",
            polity="GB-NIR",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        nir = cs.polity("GB-NIR")
        if conn.execute("select 1 from scheme where code = 'gb.dea'").fetchone() is None:
            cs.add(
                "scheme", code="gb.dea", polity_id=gb, kind="electoral_district", rank=3, noun="district electoral area"
            )
        if conn.execute("select 1 from scheme where code = 'gb.ni_dea_1993'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.ni_dea_1993",
                polity_id=gb,
                kind="electoral_district",
                rank=3,
                noun="district electoral area (1993 to 2014)",
            )
        if conn.execute("select 1 from scheme where code = 'gb.ni_dea_1985'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.ni_dea_1985",
                polity_id=gb,
                kind="electoral_district",
                rank=3,
                noun="district electoral area (1985 to 1989)",
            )
        if conn.execute("select 1 from scheme where code = 'gb.ni_dea_1973'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.ni_dea_1973",
                polity_id=gb,
                kind="electoral_district",
                rank=3,
                noun="district electoral area (1973 to 1981)",
            )
        if conn.execute("select 1 from scheme where code = 'gb.ni_lgd_1973'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.ni_lgd_1973",
                polity_id=gb,
                kind="administrative",
                rank=2,
                noun="local government district (1973 to 2015)",
            )
        councils = dict(COUNCILS)
        for legacy_code, legacy_name in (
            units.filter(pl.col("council_name").is_not_null())
            .select("council_code", "council_name")
            .unique()
            .iter_rows()
        ):
            councils[legacy_code] = legacy_name
        lad_scheme = lambda code: "gb.lad" if code.startswith("N09") else "gb.ni_lgd_1973"
        country = cs.area("gb.country", "NIR")
        if country in cs.minted:
            raise ValueError("publish gb.umit first: the countries must exist")
        system = cs.key("gb.electoral_system", "stv")
        if system in cs.minted:
            raise ValueError("publish the stv electoral system first")

        known: dict[str, UUID] = {}
        for text, party in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[fold(text)] = party
        found = conn.execute(
            "select p.id from party p join polity y on y.id = p.polity_id join name n on n.entity_id = p.id"
            " where y.iso_code = 'IE' and n.text = 'Sinn Féin' order by p.id limit 1"
        ).fetchone()
        if found is not None:
            known[fold("Sinn Féin")] = found[0]
        parties: dict[str, UUID] = {}
        shorted: set[str] = set()

        def party_id(canonical: str | None) -> UUID | None:
            if canonical is None:
                return None
            if canonical not in parties:
                if fold(canonical) in known:
                    parties[canonical] = known[fold(canonical)]
                else:
                    parties[canonical] = cs.key("gb.party", slug(canonical))
                    if parties[canonical] in cs.minted:
                        cs.add("party", id=parties[canonical], kind="party", polity_id=gb)
                        name(parties[canonical], canonical)
            for pattern, short in SHORT_NAMES:
                if canonical not in shorted and re.search(pattern, fold(canonical)):
                    shorted.add(canonical)
                    if (
                        conn.execute(
                            "select 1 from name where entity_id = %s and kind = 'short'", (parties[canonical],)
                        ).fetchone()
                        is None
                    ):
                        name(parties[canonical], short, kind="short")
            return parties[canonical]

        council_areas: dict[str, UUID] = {}
        polities: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        paths: dict[str, str] = {}
        for code, council_name in councils.items():
            label = DISTRICTS.get(code) or re.sub(r" (?:Borough |District |City )?Council$", "", council_name)
            council_areas[code] = cs.area(lad_scheme(code), code)
            if council_areas[code] in cs.minted:
                cs.add("area", id=council_areas[code], scheme_code=lad_scheme(code))
                cs.add("area_code", area_id=council_areas[code], code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=council_areas[code], parent_id=country, valid_from=None, valid_to=None)
            name(council_areas[code], label)
            polities[code] = cs.key("gb.polity", code)
            paths[code] = f"gb/{slug(label)}" if code.startswith("N09") else f"gb/{slug(council_name)}"
            if polities[code] in cs.minted:
                cs.add("polity", id=polities[code], kind="municipality", iso_code=None)
                cs.add("polity_parent", polity_id=polities[code], parent_id=nir)
                cs.add("polity_territory", polity_id=polities[code], area_id=council_areas[code])
            name(polities[code], council_name)
            cs.add("slug", path=paths[code], entity_id=polities[code])
            bodies[code] = cs.key("gb.body", code)
            if bodies[code] in cs.minted:
                cs.add("body", id=bodies[code], polity_id=polities[code], kind="council")
            name(bodies[code], council_name)
            if code.startswith("N09"):
                cs.add(
                    "body_size",
                    body_id=bodies[code],
                    seats=BODY_SIZES.get(code, 40),
                    valid_from=DAYS[2014],
                    valid_from_precision="day",
                    valid_to=None,
                    valid_to_precision="day",
                )
                cs.add(
                    "note",
                    entity_id=bodies[code],
                    kind="context",
                    body_md="Council size from the 2014 elections, as set by the Local Government Act (Northern Ireland) 2014 and the Local Government Boundaries Commissioner's recommendations: Belfast 60, Armagh City, Banbridge and Craigavon 41, Newry, Mourne and Down 41, and 40 for the other eight councils.",
                )

        dea_areas: dict[str, UUID] = {}
        dea_units: dict[str, UUID] = {}
        council_elections: dict[tuple[int, str], UUID] = {}
        sources: dict[str, UUID] = {}
        files: dict[object, object] = {}
        for year, day in DAYS.items():
            held = units.filter(pl.col("year") == year)
            if held.is_empty():
                continue
            national = cs.key("gb.election", f"{day}:local:ni")
            cs.add("election", id=national, polity_id=nir, kind="local", date_from=day, date_to=day, status="held")
            name(national, f"{year} Northern Ireland local elections")
            cs.add("slug", path=f"gb/northern-ireland/{day}-local", entity_id=national)
            for unit_row in held.sort("dea_code").iter_rows(named=True):
                code = unit_row["council_code"]
                dea_code = unit_row["dea_code"]
                label = unit_row["dea"]
                if (year, code) not in council_elections:
                    council_elections[(year, code)] = cs.key("gb.election", f"{day}:local:{code}")
                    cs.add(
                        "election",
                        id=council_elections[(year, code)],
                        polity_id=polities[code],
                        parent_id=national,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(council_elections[(year, code)], f"{year} {councils[code]} election")
                    cs.add("slug", path=f"{paths[code]}/{day}-local", entity_id=council_elections[(year, code)])
                election = council_elections[(year, code)]
                dea_scheme = (
                    "gb.dea"
                    if dea_code.startswith("N10")
                    else "gb.ni_dea_1985"
                    if dea_code.startswith("1985:")
                    else "gb.ni_dea_1973"
                    if dea_code.startswith("1973:")
                    else "gb.ni_dea_1993"
                )
                if dea_code not in dea_areas:
                    dea_areas[dea_code] = cs.area(dea_scheme, dea_code)
                    if dea_areas[dea_code] in cs.minted:
                        cs.add("area", id=dea_areas[dea_code], scheme_code=dea_scheme)
                        cs.add("area_code", area_id=dea_areas[dea_code], code=dea_code, valid_from=None, valid_to=None)
                        cs.add(
                            "area_parent",
                            area_id=dea_areas[dea_code],
                            parent_id=council_areas[code],
                            valid_from=None,
                            valid_to=None,
                        )
                    name(dea_areas[dea_code], label)
                    dea_units[dea_code] = cs.unit(dea_scheme, dea_code)
                    if dea_units[dea_code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=dea_units[dea_code],
                            polity_id=polities[code],
                            scheme_code=dea_scheme,
                            code=dea_code,
                            area_relation="equals",
                            area_id=dea_areas[dea_code],
                            kind="standard",
                            valid_from=None,
                        )
                        name(dea_units[dea_code], label)
                unit = dea_units[dea_code]
                url = unit_row["source_url"]
                publisher = (
                    "ARK Northern Ireland Elections"
                    if year == 2005 or "ark.ac.uk" in url
                    else "Electoral Office for Northern Ireland"
                )
                if url not in sources:
                    sources[url] = cs.source(publisher, f"Local council elections {year}: {url.rsplit('/', 1)[-1]}")
                    cs.add(
                        "source",
                        id=sources[url],
                        kind="academic" if publisher.startswith("ARK") else "official_body",
                        publisher=publisher,
                        title=f"Local council elections {year}: "
                        + (
                            "count spreadsheets"
                            if publisher.startswith("ARK")
                            else f"result sheet {url.rsplit('/', 1)[-1]}"
                        ),
                        url=url,
                        license="Open Government Licence v3.0" if year == 2014 else None,
                        license_url=OGL if year == 2014 else None,
                        attribution=publisher,
                    )
                    try:
                        stored = raw.cached(SOURCE, url)
                    except LookupError:
                        stored = raw.cached("gb.ni_scans", url)
                    files[url] = cs.artifact(stored, sources[url])
                    files[url, "sha"] = stored.sha256
                seats = int(unit_row["seats"])
                contest_key = f"{day}:local:{dea_code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[code],
                    district_area_id=dea_areas[dea_code],
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, f"{label} district electoral area, {councils[code]}, {year}")
                cs.add("slug", path=f"{paths[code]}/{day}-local/{slug(label)}", entity_id=contest)
                result_set = cs.key("gb.ni_council.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[url],
                    is_default=True,
                    completeness=1,
                )
                if unit_row["note"]:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=unit_row["note"])
                for measure, amount in (
                    (REGISTERED, unit_row["electorate"]),
                    (BALLOTS, unit_row["polled"]),
                    (VALID, unit_row["valid"]),
                    (INVALID, unit_row["invalid"]),
                    (QUOTA, unit_row["quota"]),
                ):
                    if amount is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=amount,
                        )
                lines = totals.filter((pl.col("year") == year) & (pl.col("dea_code") == dea_code))
                for candidate in (
                    lines.filter(pl.col("number") > 0)
                    .select("number", "name", "party", "outcome", "derived")
                    .unique(maintain_order=True, subset=["number"])
                    .iter_rows(named=True)
                ):
                    series = {
                        row["stage"]: row["votes"]
                        for row in lines.filter(pl.col("number") == candidate["number"]).iter_rows(named=True)
                    }
                    canonical = party_of(candidate["party"], year)
                    owner = party_id(canonical)
                    independent = canonical is None and (
                        re.sub(r"[\s0\-]+", "", candidate["party"]) == ""
                        or re.match(r"ind|i$", candidate["party"].strip().lower()) is not None
                    )
                    full = display(candidate["name"])
                    option_key = f"{contest_key}:{candidate['number']}"
                    option = cs.key("gb.ni_council.option", option_key)
                    person = cs.key("gb.ni_council.person", option_key)
                    cs.add("person", id=person)
                    name(person, full)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.ni_council.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate["name"],
                        member_of_party_id=owner,
                        is_independent=independent,
                    )
                    eliminated = False
                    located = (
                        {
                            row["stage"]: row
                            for row in lines.filter(
                                (pl.col("number") == candidate["number"]) & pl.col("page").is_not_null()
                            ).iter_rows(named=True)
                        }
                        if "page" in lines.columns
                        else {}
                    )
                    for stage in sorted(series):
                        if eliminated:
                            break
                        amount = series[stage]
                        if stage in located:
                            box = located[stage]
                            number = int(box["page"])
                            if (url, number) not in files:
                                image = SCAN_PAGES / f"{files[url, 'sha']}-{number}.png"
                                shutil.copy(
                                    sorted((SCAN_PAGES / re.sub(r"\W+", "-", url)[-80:]).glob("p-*.png"))[number - 1],
                                    image,
                                )
                                width, height = Image.open(image).size
                                files[url, number] = cs.page(files[url], number)
                                cs.add(
                                    "source_page",
                                    id=files[url, number],
                                    artifact_id=files[url],
                                    page_number=number,
                                    image_key=f"pages/gb.ni_scans/{image.name}",
                                    width=width,
                                    height=height,
                                )
                                files[url, number, "size"] = (width, height)
                            width, height = files[url, number, "size"]
                            cs.add(
                                "value_locator",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=VOTES,
                                count_round=stage,
                                page_id=files[url, number],
                                source_id=None,
                                x0=box["x0"] / width,
                                y0=box["y0"] / height,
                                x1=box["x1"] / width,
                                y1=box["y1"] / height,
                            )
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=stage,
                            flags=0,
                            value=amount,
                        )
                        eliminated = amount == 0 and stage > 1 and candidate["outcome"] != "elected"
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=candidate["outcome"])
                for row in lines.filter(pl.col("number") == 0).iter_rows(named=True):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=EXHAUSTED,
                        count_round=row["stage"],
                        flags=DERIVED if row["stage"] == 1 else 0,
                        value=row["votes"],
                    )
        return cs.write()
