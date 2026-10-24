import csv
import io
import re
import subprocess
import time
import unicodedata
from datetime import date
from uuid import UUID
from html import unescape
from pathlib import Path

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb.ni_assembly import party_of as assembly_party
from ea.store import DATA, connect

SOURCE = "gb.ni_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
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
    for path in links(PAGE_2023, r'href="(/media/[^"]+\.(?:xlsx|pdf))"'):
        raw.fetch(SOURCE, f"{EONI}{path}", suffix=Path(path).suffix)
    for url in links(OPENDATANI_2014, r'href="(https://admin\.opendatani\.gov\.uk/dataset/[^"]+/download/(?:2014-ni-councils-elections|local-council-elections-2014)-[^"]+\.csv)"'):
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
    sheet = pl.read_excel(raw.cached(SOURCE, DEAS).path, has_header=False, infer_schema_length=0)["column_0"].to_list()[1:]
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


def grid_sheet(grid: list[list[str | None]], dea: str) -> dict[str, object]:
    grid = [row[:26] for row in grid]
    def find(label: str, row: list[str | None]) -> int | None:
        return next((index for index, cell in enumerate(row) if cell is not None and cell.strip().lower().startswith(label)), None)

    facts: dict[str, float] = {}
    for row in grid[:14]:
        for index, cell in enumerate(row):
            if cell is None:
                continue
            label = cell.strip().lower()
            key = next(
                (
                    key
                    for key, words in (
                        ("electorate", ("eligible electorate",)),
                        ("polled", ("votes polled", "total votes polled")),
                        ("seats", ("number to be elected", "number of members to be elected")),
                        ("invalid", ("invalid votes",)),
                        ("valid", ("total valid votes", "valid votes")),
                        ("quota", ("electoral quota",)),
                    )
                    if any(label.startswith(word) for word in words)
                ),
                None,
            )
            if key is not None:
                value = next((number(c) for c in row[index + 1 :] if number(c) is not None), None)
                facts[key] = value
    header = next(index for index, row in enumerate(grid) if any(c is not None and c.strip().lower().startswith(("1st pref", "first pref")) for c in row))
    first = find("1st pref", grid[header])
    if first is None:
        first = find("first pref", grid[header])
    name_col = next(
        index
        for row in (grid[header], grid[header - 1])
        for index in [find("candidate", row) or find("names of candidate", row)]
        if index is not None
    )
    description_col = name_col + 1
    totals_row = next(index for index in range(header + 1, len(grid)) if (grid[index][description_col] or "").strip().lower().startswith("totals"))
    nontransferable_row = next(
        index for index in range(header + 1, totals_row) if (grid[index][description_col] or "").strip().lower().startswith("non")
    )
    rows = [
        row
        for row in grid[header + 1 : nontransferable_row]
        if (row[name_col] or "").strip() not in ("", "0") and number(row[name_col]) is None
    ]
    total_cols = []
    column = first
    while column < len(grid[0]) and sum(number(row[column]) or 0.0 for row in rows) > 0:
        total_cols.append(column)
        column += 2
    stage_totals = {
        stage: sum(number(row[col]) or 0.0 for row in rows) + (number(grid[nontransferable_row][col]) or 0.0)
        for stage, col in enumerate(total_cols, start=1)
    }
    tied = tie(stage_totals, facts["valid"], len(rows))
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
        for a, b, c, d, text in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', html)
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
            elif word[2] > description_x + 1 and word[0] < description_x and not any(abs(w[0] - description_x) < 0.6 for w in body):
                estimate = round((description_x - word[0]) / (word[2] - word[0]) * len(word[4]))
                tail = " ".join([word[4], *[w[4] for w in body if w[0] >= description_x - 0.5]])
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


def finish(year: int, dea: str, facts: dict[str, float], candidates: list[dict[str, object]], exhausted: list[float], stages: int, url: str, areas: dict[str, tuple[str, str, str]]) -> tuple[dict[str, object], list[dict[str, object]]]:
    key = squeeze(dea)
    if key not in areas:
        raise ValueError(f"{year} {dea}: no DEA in the official list")
    dea_code, dea_name, council_code = areas[key]
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
    if stages > tied:
        notes.append(f"The sheet has {stages} stages, but stage {tied + 1} does not add up to the valid votes, so only stages 1 to {tied} are stored.")
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
        except (AttributeError, StopIteration):
            continue
    for url, artifact in index.items():
        if url.endswith(".xlsx"):
            for sheet_name in fastexcel.read_excel(artifact.path).sheet_names:
                grid = [list(row) for row in pl.read_excel(artifact.path, sheet_name=sheet_name, has_header=False, infer_schema_length=0).iter_rows()]
                sheet = grid_sheet(grid, sheet_name)
                name = next(
                    (
                        next(cell for cell in row[position + 1 :] if cell)
                        for row in grid[:4]
                        for position, label in enumerate(row)
                        if label is not None and label.strip().lower().startswith("district electoral area") and any(row[position + 1 :])
                    ),
                    sheet_name,
                )
                candidates = [
                    {**c, "elected": c["flag"] == "elected"} for c in sheet["candidates"]
                ]
                unit, rows = finish(2023, name.strip(), sheet["facts"], candidates, sheet["exhausted"], sheet["stages"], url, areas)
                units.append(unit)
                totals.extend(rows)
        else:
            try:
                sheet = pdf_sheet(artifact.path, known)
            except (AttributeError, StopIteration):
                print(f"IMAGE ONLY {url}")
                continue
            columns = sheet["columns"]
            stage_totals = {}
            for stage, column in enumerate(columns, start=1):
                stage_totals[stage] = sum(float(cell["values"].get(column, 0.0)) for cell in sheet["cells"]) + float(sheet["exhausted"].get(column, 0.0))
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
    urls = sorted(set(re.findall(r'href="(https://admin\.opendatani\.gov\.uk/dataset/[^"]+/download/[^"]+\.csv)"', page)))
    facts_url = next(url for url in urls if "vote-counts" in url)
    table = list(csv.DictReader(io.StringIO(raw.cached(SOURCE, f"{WAYBACK}{facts_url}").path.read_bytes().decode("cp1252"))))
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
        total_cols = [index for index, label in enumerate(header) if re.fullmatch(r"Stage \d+ - (?:Total|1st Preference)", label.strip())]
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
                    amount = 0.0 if (stage_excluded and position + 1 > int(stage_excluded.group(1))) or previous == 0.0 else previous
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
            stage: sum(candidate["totals"][stage - 1] for candidate in candidates) + exhausted[stage - 1] for stage in range(1, len(total_cols) + 1)
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
        later = [stage for stage in stage_totals if stage > tied + 1 and abs(stage_totals[stage] - valid) <= 0.01 * (len(candidates) + 1)]
        if tied == 0 or later:
            print(f"UNTIED 2014 {dea}: tied through stage {tied}, ties again at {later}, stage sums {[round(v, 2) for v in stage_totals.values()]} against {valid}")
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


DAYS = {
    2023: date(2023, 5, 18),
    2019: date(2019, 5, 2),
    2014: date(2014, 5, 22),
    2011: date(2011, 5, 5),
    2005: date(2005, 5, 5),
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
EXTRA_PARTIES = (
    (r"democratic ulster unionist - d\.u\.p", "Democratic Unionist Party"),
    (r"^all$", "Alliance Party"),
    (r"^sf$", "Sinn Féin"),
    (r"^grn$", "Green Party Northern Ireland"),
    (r"^con$", "Conservative"),
    (r"^pbpa$", "People Before Profit Alliance"),
    (r"^ind$|indepdendent", None),
    (r"^0$", None),
    (r"^ni2[12]$|northern ireland 21", "NI21"),
    (r"^-$|^valarie$|^upu$", None),
    (r"aspire to better", "Aspire to Better"),
    (r"^british national party$", "British National Party"),
    (r"community partnership", "Community Partnership (Northern Ireland)"),
    (r"^[eé]ir[ií]g[ií]$", "Éirígí"),
    (r"fermanagh against fracking", "Fermanagh Against Fracking"),
    (r"^gr+en party$", "Green Party Northern Ireland"),
    (r"^ni cons$", "Conservative"),
    (r"republican network for unity", "Republican Network for Unity"),
    (r"^ukip$", "UK Independence Party"),
    (r"^irsp|irish republican socialist", "Irish Republican Socialist Party"),
)


def party_of(description: str) -> str | None:
    text = re.sub(r"\s+", " ", description.strip().lower())
    for pattern, canonical in EXTRA_PARTIES:
        if re.search(pattern, text):
            return canonical
    return assembly_party(description)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))


def display_word(word: str) -> str:
    if re.fullmatch(r"(?:[A-Z]\.)+|[A-Z]", word):
        return word
    parts = []
    for part in word.split("-"):
        if re.search(r"[A-Z]{2}", part):
            found = re.fullmatch(r"(MC|Mc|O')(.+)", part)
            part = found.group(1).capitalize().replace("O'", "O'") + found.group(2).capitalize() if found else part.capitalize()
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
    pl.DataFrame(units, infer_schema_length=None).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    print(pl.DataFrame(units, infer_schema_length=None).group_by("year").len().sort("year"))


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
            cs.add("scheme", code="gb.dea", polity_id=gb, kind="electoral_district", rank=3, noun="district electoral area")
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
            return parties[canonical]

        council_areas: dict[str, UUID] = {}
        polities: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        paths: dict[str, str] = {}
        for code, council_name in COUNCILS.items():
            label = DISTRICTS[code]
            council_areas[code] = cs.area("gb.lad", code)
            if council_areas[code] in cs.minted:
                cs.add("area", id=council_areas[code], scheme_code="gb.lad")
                cs.add("area_code", area_id=council_areas[code], code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=council_areas[code], parent_id=country, valid_from=None, valid_to=None)
            name(council_areas[code], label)
            polities[code] = cs.key("gb.polity", code)
            paths[code] = f"gb/{slug(label)}"
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

        dea_areas: dict[str, UUID] = {}
        dea_units: dict[str, UUID] = {}
        council_elections: dict[tuple[int, str], UUID] = {}
        sources: dict[str, UUID] = {}
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
                    name(council_elections[(year, code)], f"{year} {COUNCILS[code]} election")
                    cs.add("slug", path=f"{paths[code]}/{day}-local", entity_id=council_elections[(year, code)])
                election = council_elections[(year, code)]
                if dea_code not in dea_areas:
                    dea_areas[dea_code] = cs.area("gb.dea", dea_code)
                    if dea_areas[dea_code] in cs.minted:
                        cs.add("area", id=dea_areas[dea_code], scheme_code="gb.dea")
                        cs.add("area_code", area_id=dea_areas[dea_code], code=dea_code, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=dea_areas[dea_code], parent_id=council_areas[code], valid_from=None, valid_to=None)
                    name(dea_areas[dea_code], label)
                    dea_units[dea_code] = cs.unit("gb.dea", dea_code)
                    if dea_units[dea_code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=dea_units[dea_code],
                            polity_id=polities[code],
                            scheme_code="gb.dea",
                            code=dea_code,
                            area_relation="equals",
                            area_id=dea_areas[dea_code],
                            kind="standard",
                            valid_from=None,
                        )
                        name(dea_units[dea_code], label)
                unit = dea_units[dea_code]
                url = unit_row["source_url"]
                if url not in sources:
                    sources[url] = cs.source("Electoral Office for Northern Ireland", f"Local council elections {year}: {url.rsplit('/', 1)[-1]}")
                    cs.add(
                        "source",
                        id=sources[url],
                        kind="official_body",
                        publisher="Electoral Office for Northern Ireland",
                        title=f"Local council elections {year}: result sheet {url.rsplit('/', 1)[-1]}",
                        url=url,
                        license="Open Government Licence v3.0" if year == 2014 else None,
                        license_url=OGL if year == 2014 else None,
                        attribution="Electoral Office for Northern Ireland",
                    )
                    cs.artifact(raw.cached(SOURCE, url), sources[url])
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
                name(contest, f"{label} district electoral area, {COUNCILS[code]}, {year}")
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
                for candidate in lines.filter(pl.col("number") > 0).select("number", "name", "party", "outcome", "derived").unique(
                    maintain_order=True, subset=["number"]
                ).iter_rows(named=True):
                    series = {
                        row["stage"]: row["votes"]
                        for row in lines.filter(pl.col("number") == candidate["number"]).iter_rows(named=True)
                    }
                    canonical = party_of(candidate["party"])
                    owner = party_id(canonical)
                    independent = canonical is None and "independ" in candidate["party"].lower()
                    full = display(candidate["name"])
                    option_key = f"{contest_key}:{candidate['number']}"
                    option = cs.key("gb.ni_council.option", option_key)
                    person = cs.key("gb.ni_council.person", option_key)
                    cs.add("person", id=person)
                    name(person, full)
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=owner, ballot_label=full)
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
                    for stage in sorted(series):
                        if eliminated:
                            break
                        amount = series[stage]
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
