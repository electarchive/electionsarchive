import csv
import difflib
import io
import os
import re
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import pairwise, product
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.scan import CONFUSABLE
from ea.sources.us import il_sbe, medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.il_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Illinois State Board of Elections"
LICENSE = "Public domain"
TITLE = "Official Vote Cast at the General Election"
LANDING = "https://www.elections.il.gov/ElectionOperations/ElectionVoteTotals.aspx"
OFFICES = {
    "att": "Attorney General",
    "sos": "Secretary of State",
    "comp": "Comptroller",
    "treas": "Treasurer",
}
SPANS: dict[int, dict[str, tuple[int, int]]] = {
    1990: {"att": (19, 21), "sos": (22, 24), "comp": (25, 27), "treas": (28, 30)},
    1994: {"att": (21, 23), "sos": (24, 26), "comp": (27, 29), "treas": (30, 32)},
    1998: {"att": (21, 23), "sos": (24, 26), "comp": (27, 29), "treas": (30, 32)},
    2002: {"att": (15, 18), "sos": (19, 22), "comp": (23, 26), "treas": (27, 30)},
    2006: {"att": (21, 24), "sos": (25, 28), "comp": (29, 32), "treas": (33, 36)},
}
PASSES = ((300, "6"), (450, "6"), (300, "4"), (600, "6"), (300, "11"), (400, "6"), (500, "6"), (350, "4"), (400, "4"))
PARTIES = {
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "GRN": "GREEN",
    "REF": "REFORM",
    "SOL": "SOLIDARITY",
    "IND": "INDEPENDENT",
    "CON": "CONSTITUTION",
    "TAX": "U.S. TAXPAYERS",
    "BRN": "GREEN",
    "SRN": "GREEN",
}
FIXES: dict[tuple[int, str, str, int], int] = {}
MANUAL: dict[tuple[int, str, str], list[int]] = {
    (1994, "sos", "edwards"): [1291, 2008, 717, 24],
    (2006, "sos", "cass"): [752, 2548, 1796, 175, 0],
}
NAMES: dict[tuple[int, str, str], str] = {}
METHOD = (
    "County totals come from the State Board of Elections' official vote for the general election (scanned volumes), "
    "read by up to five OCR passes per page with disagreements settled by each row's printed plurality and each "
    "candidate's printed statewide total."
)
NUMBER = re.compile(r"[\dOoIl|!\]\[SBZzgD,.'’`]+")


def book(year: int) -> raw.Artifact:
    return raw.cached(il_sbe.RAW, il_sbe.VOLUMES[year][0])


def image(year: int, page: int) -> Path:
    target = PAGES / f"{book(year).sha256}-{page}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(book(year).path), str(target.with_suffix(""))],
            check=True,
        )
    return target


def read(year: int, page: int, dpi: int, psm: str) -> list[list[tuple[str, tuple[int, int, int, int]]]]:
    cache = PARSED / "ocr" / f"{book(year).sha256}-{page}-{dpi}-{psm}.tsv"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "page.png"
            if dpi == 300:
                target.write_bytes(image(year, page).read_bytes())
            else:
                subprocess.run(
                    ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(book(year).path), str(target.with_suffix(""))],
                    check=True,
                )
            done = subprocess.run(
                ["tesseract", str(target), "stdout", "--psm", psm, "tsv"],
                capture_output=True,
                text=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                check=False,
            )
            if done.returncode:
                raise RuntimeError(f"{year} page {page} {dpi} {psm}: {done.stderr}")
            cache.write_text(done.stdout)
    scale = 300 / dpi
    lines: dict[tuple[str, str, str], list[tuple[str, tuple[int, int, int, int]]]] = {}
    for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] != "5" or not row["text"].strip():
            continue
        left, top, width, height = (int(row[k]) for k in ("left", "top", "width", "height"))
        box = (round(left * scale), round(top * scale), round((left + width) * scale), round((top + height) * scale))
        lines.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append((row["text"], box))
    merged = []
    for words in lines.values():
        line: list[tuple[str, tuple[int, int, int, int]]] = []
        for text, box in words:
            if line and (text.startswith(",") or line[-1][0].endswith(",")):
                previous, before = line[-1]
                line[-1] = (
                    previous + text,
                    (min(before[0], box[0]), min(before[1], box[1]), max(before[2], box[2]), max(before[3], box[3])),
                )
            else:
                line.append((text, box))
        merged.append(line)
    return merged


def number(token: str) -> int | None:
    token = token.strip('“”"—–-_|=~')
    token = re.sub(r"\((?:[A-Z]{3}|OEM)[^)]*\)?$", "", token)
    token = re.sub(r"(?<=\d)\.(?=\d{3}\b)", ",", token)
    if not NUMBER.fullmatch(token) or not re.search(r"\d", token):
        return None
    digits = re.sub(r"\D", "", token.translate(CONFUSABLE))
    return int(digits) if digits else None


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def summary(lines: list[list[tuple[str, tuple[int, int, int, int]]]]) -> list[tuple[str, str | None, int]]:
    found = []
    for words in lines:
        text = " ".join(word for word, _ in words)
        if "COUNTY" in text and "PLURALITY" in text:
            break
        tail = re.search(r"(?:\b([A-Z]{3})\b[.,;|]?\s*)?[|]?\s*([\d,.]{1,12})\s+(?:\(-\)\s*|<\s*)?[.\d]*\d\s?%", text)
        if tail is None:
            continue
        body = re.sub(r"^\W*(?:\(?(?:won|win)\)\s*)?", "", text[: tail.start()], flags=re.IGNORECASE)
        name = re.split(r"\.{2,}|\s\.\s|_|[a-z]{4,}|\s{3,}|\s[a-z]{1,3}\s", body)[0].strip(" .,;|=-—")
        votes = number(tail[2].replace(".", ","))
        if name and votes is not None and len(name) > 3:
            found.append((name, tail[1], votes))
    return found


def header(lines: list[list[tuple[str, tuple[int, int, int, int]]]]) -> list[str]:
    for words in lines:
        texts = [word for word, _ in words]
        if "COUNTY" in texts and any(text.startswith("PLURALITY") for text in texts):
            after = texts[next(i for i, text in enumerate(texts) if text.startswith("PLURALITY")) + 1 :]
            return [
                text
                for text in after
                if re.fullmatch(r"[A-Za-z.,'\-]{2,}", text)
                and text.strip(".,").upper() not in {"II", "III", "IV", "JR", "SR"}
            ]
    return []


def codes(lines: list[list[tuple[str, tuple[int, int, int, int]]]]) -> list[str]:
    for before, words in pairwise(lines):
        texts = [word for word, _ in words]
        if "COUNTY" in texts and any(text.startswith("PLURALITY") for text in texts):
            return [word for word, _ in before if re.fullmatch(r"[A-Z]{3}", word)]
    return []


def parse() -> None:
    with connect() as conn:
        counties = {
            plain(text): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
    if len(counties) != 102:
        raise ValueError(f"Illinois needs 102 counties, found {len(counties)}")
    jobs = [
        (year, page, dpi, psm)
        for year, offices in SPANS.items()
        for first, last in offices.values()
        for page in range(first, last + 1)
        for dpi, psm in PASSES
    ]
    for year, page in sorted({(year, page) for year, page, _, _ in jobs}):
        image(year, page)
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda job: read(*job), jobs))
    out: list[dict[str, object]] = []
    problems: list[str] = []
    for year, offices in SPANS.items():
        for office, (first, last) in offices.items():
            where = f"{year} {OFFICES[office]}"
            widths = Counter(
                len(header(read(year, page, dpi, psm))) for page in range(first, last + 1) for dpi, psm in PASSES[:3]
            )
            width = max((n for n in widths if n), key=lambda n: widths[n])
            surnames = Counter(
                tuple(header(read(year, first, dpi, psm)))
                for dpi, psm in PASSES
                if len(header(read(year, first, dpi, psm))) == width
            ).most_common(1)[0][0]
            found: Counter[tuple[str, str | None, int]] = Counter()
            for dpi, psm in PASSES:
                found.update(summary(read(year, first, dpi, psm)))
            columns: list[tuple[str, str | None, int | None]] = []
            labels = Counter(
                tuple(codes(read(year, first, dpi, psm)))
                for dpi, psm in PASSES
                if len(codes(read(year, first, dpi, psm))) == width
            ).most_common(1)
            for index, surname in enumerate(surnames):
                pool_ = [
                    (key, count)
                    for key, count in found.items()
                    if difflib.SequenceMatcher(None, plain(surname), plain(key[0].split()[-1])).ratio() >= 0.7
                    or plain(surname) in plain(key[0])
                ]
                if not pool_:
                    columns.append((surname, None, None))
                    continue
                names = Counter()
                parties = Counter()
                totals = Counter()
                for (name, party, votes), count in pool_:
                    names[name] += count
                    totals[votes] += count
                    if party:
                        parties[party] += count
                columns.append(
                    (
                        NAMES.get((year, office, surname), names.most_common(1)[0][0]),
                        labels[0][0][index] if labels else (parties.most_common(1)[0][0] if parties else None),
                        totals.most_common(1)[0][0],
                    )
                )
            reads: dict[str, list[tuple[int, list[tuple[int | None, tuple[int, int, int, int]]]]]] = {}
            for page in range(first, last + 1):
                for dpi, psm in PASSES:
                    for words in read(year, page, dpi, psm):
                        lead = []
                        for text, _ in words:
                            if re.fullmatch(r"[A-Za-z.'\-“\"]+", text):
                                lead.append(text)
                            else:
                                break
                        key = plain("".join(lead))
                        match = difflib.get_close_matches(key, counties, n=1, cutoff=0.8) if key else []
                        if not match:
                            continue
                        values = [(number(text), box) for text, box in words[len(lead) :]]
                        values = [(value, box) for value, box in values if value is not None]
                        if len(values) == width + 1:
                            reads.setdefault(match[0], []).append((page, values))
            for (m_year, m_office, county), values in MANUAL.items():
                if (m_year, m_office) == (year, office) and county in reads:
                    page, versions = reads[county][0]
                    reads[county] = [(page, [(value, versions[c][1]) for c, value in enumerate(values)])]
                elif (m_year, m_office) == (year, office):
                    seen = []
                    for page in range(first, last + 1):
                        for dpi, psm in PASSES[:3]:
                            for words in read(year, page, dpi, psm):
                                lead = []
                                for text, _ in words:
                                    if re.fullmatch(r"[A-Za-z.'\-“\"]+", text):
                                        lead.append(text)
                                    else:
                                        break
                                if lead and difflib.get_close_matches(plain("".join(lead)), [county], n=1, cutoff=0.8):
                                    seen.append((page, words))
                    if not seen:
                        raise ValueError(f"{year} {office}: no line for {county}")
                    page, words = seen[0]
                    y0 = min(box[1] for _, box in words)
                    y1 = max(box[3] for _, box in words)
                    others = [v for r in reads.values() for p_, v in r if p_ == page]
                    boxes = [
                        (
                            sorted(v[c][1][0] for v in others)[len(others) // 2],
                            y0,
                            sorted(v[c][1][2] for v in others)[len(others) // 2],
                            y1,
                        )
                        for c in range(width + 1)
                    ]
                    reads[county] = [(page, [(value, box) for value, box in zip(values, boxes, strict=True)])]
            if len(reads) != 102:
                problems.append(f"{where}: {len(reads)} counties, missing {sorted(set(counties) - set(reads))}")
            grid: dict[str, list[int]] = {}
            alternatives: dict[tuple[str, int], list[int]] = {}
            for county, versions in reads.items():
                if len({page for page, _ in versions}) != 1:
                    problems.append(f"{where}: {county} on several pages")
                settled = []
                for column in range(width + 1):
                    tally = Counter(values[column][0] for _, values in versions)
                    ranked = [value for value, _ in tally.most_common()]
                    alternatives[(county, column)] = ranked
                    settled.append(ranked[0])
                for column, value in enumerate(settled):
                    pass
                for ranked_choice in (None,):
                    top = sorted(settled[1:], reverse=True)
                    if settled[0] != top[0] - top[1]:
                        options = [alternatives[(county, c)][:3] for c in range(width + 1)]
                        fits = [
                            combo
                            for combo in product(*options)
                            if combo[0] == max(combo[1:]) - sorted(combo[1:], reverse=True)[1]
                        ]
                        if len(fits) == 1:
                            settled = list(fits[0])
                        else:
                            problems.append(
                                f"{where} {county}: plurality {settled[0]} does not fit {settled[1:]}; {len(fits)} fits"
                            )
                grid[county] = settled
            for column, (name, party, printed) in enumerate(columns, start=1):
                total = sum(row[column] for row in grid.values())
                if printed is None or total == printed:
                    continue
                delta = total - printed
                fixes = [
                    (county, alt)
                    for county in grid
                    for alt in alternatives[(county, column)][1:]
                    if grid[county][column] - alt == delta
                ]
                if len(fixes) == 1:
                    county, alt = fixes[0]
                    problems.append(
                        f"{where} {name}: {county} {grid[county][column]} -> {alt} to meet the printed total {printed}"
                    )
                    grid[county][column] = alt
                else:
                    problems.append(f"{where} {name}: counties sum to {total}, printed {printed}, {len(fixes)} repairs")
            for (name_year, name_office, county, column), value in FIXES.items():
                if (name_year, name_office) == (year, office):
                    grid[county][column] = value
            for county, versions in reads.items():
                page, values = max(versions, key=lambda version: version[0])
                for column in range(width + 1):
                    value = grid[county][column]
                    box = next((box for _, vs in versions for v, box in [vs[column]] if v == value), values[column][1])
                    out.append(
                        {
                            "year": year,
                            "office": office,
                            "county": counties[county],
                            "column": column,
                            "name": "Plurality" if column == 0 else columns[column - 1][0],
                            "party": None if column == 0 else columns[column - 1][1],
                            "votes": value,
                            "printed": None if column == 0 else columns[column - 1][2],
                            "page": page,
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                    )
            print(where, columns)
    PARSED.mkdir(parents=True, exist_ok=True)
    reads_frame = pl.DataFrame(out)
    reads_frame.write_parquet(PARSED / "reads.parquet")
    print("\n".join(problems))
    days = {
        1990: date(1990, 11, 6),
        1994: date(1994, 11, 8),
        1998: date(1998, 11, 3),
        2002: date(2002, 11, 5),
        2006: date(2006, 11, 7),
    }
    frame = reads_frame.filter(pl.col("column") > 0).select(
        year=pl.col("year").cast(pl.Int32),
        day=pl.col("year").replace_strict({y: str(d) for y, d in days.items()}),
        state=pl.lit("IL"),
        title=pl.col("office").replace_strict(OFFICES),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("name")
        .map_elements(format_name, return_dtype=pl.String)
        .str.to_uppercase()
        .replace({"WAIKAR": "ALAKA WAIKAR"}),
        party=pl.col("party").replace_strict(PARTIES, default=None, return_dtype=pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
        page=pl.col("year") * 1000 + pl.col("page"),
        x0="x0",
        y0="y0",
        x1="x1",
        y1="y1",
    )
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(102),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes")
        .rows()
    )


def format_name(text: str) -> str:
    text = re.sub(r"\s*,\s*", ", ", text.strip())
    text = re.sub(r",\s*(JR|SR)\b\.?", lambda m: ", " + m[1].title() + ".", text, flags=re.IGNORECASE)
    return " ".join(word if word[0].isupper() and not word.isupper() else word.title() for word in text.split())


def emit() -> Path:
    def pages(cs: Changeset) -> dict[int, tuple[UUID, int, int]]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license=LICENSE,
            )
        result: dict[int, tuple[UUID, int, int]] = {}
        frame = pl.read_parquet(PARSED / "returns.parquet")
        artifacts = {year: cs.artifact(book(year), source) for year in sorted({key // 1000 for key in frame["page"]})}
        for key in sorted(set(frame["page"])):
            year, number_ = divmod(key, 1000)
            artifact = artifacts[year]
            image_ = image(year, number_)
            with Image.open(image_) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number_)
            result[key] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number_,
                image_key=f"pages/{SOURCE}/{image_.name}",
                width=width,
                height=height,
            )
        return result

    def cite(cs: Changeset) -> dict[str, UUID]:
        return {"IL": cs.source(PUBLISHER, TITLE)}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Illinois's 1990, 1994, 1998, 2002, and 2006 attorney general, secretary of state, comptroller, and treasurer "
        "by county from the State Board of Elections' official vote",
        cite=cite,
        method=METHOD,
        pages=pages,
    )
