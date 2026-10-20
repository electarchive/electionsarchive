import difflib
import re
import statistics
import subprocess
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.il_primaries"
VERSION = "1"
PUBLISHER = "Illinois State Board of Elections"
LICENSE = "Public domain"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLE = "State of Illinois Official Vote Cast at the General Primary Election, {day:%B %-d, %Y}"
ITEMS = {
    1994: ("stateofillinoimar1994illi", date(1994, 3, 15)),
    1996: ("stateofillinoiso1996illi", date(1996, 3, 19)),
    1998: ("stateofillinoiso1998illi", date(1998, 3, 17)),
}
PARTIES = {"REP": "republican", "DEM": "democratic"}
ORDINALS = {
    word: number
    for number, word in enumerate(
        [
            "FIRST",
            "SECOND",
            "THIRD",
            "FOURTH",
            "FIFTH",
            "SIXTH",
            "SEVENTH",
            "EIGHTH",
            "NINTH",
            "TENTH",
            "ELEVENTH",
            "TWELFTH",
            "THIRTEENTH",
            "FOURTEENTH",
            "FIFTEENTH",
            "SIXTEENTH",
            "SEVENTEENTH",
            "EIGHTEENTH",
            "NINETEENTH",
            "TWENTIETH",
        ],
        1,
    )
}
CHECKED: set[tuple[str, str]] = set()

Word = tuple[str, float, float, float, float]


class Block(TypedDict):
    heads: list[Word]
    rows: list[tuple[int, tuple[float, float], list[Word]]]


class Section(TypedDict):
    office: str
    district: int
    candidates: list[tuple[str, str, int, int]]
    blocks: list[Block]


def url(year: int) -> str:
    return f"https://archive.org/download/{ITEMS[year][0]}/{ITEMS[year][0]}.pdf"


def fetch() -> None:
    for year in ITEMS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def plain(text: str) -> str:
    return house_books._plain(text)


def surname(text: str) -> str:
    words = [w for w in re.split(r"[\s,.\"]+", text) if plain(w) not in {"jr", "sr", "ii", "iii", "iv", ""}]
    return plain(words[-1]) if words else ""


def number(text: str) -> int | None:
    return int(text.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})+|\d+", text) else None


def pages(year: int) -> list[tuple[float, float, list[tuple[str, float, float, float, float]]]]:
    book = raw.cached(SOURCE, url(year))
    cache = PARSED / f"{book.sha256}.xml"
    if not cache.exists():
        PARSED.mkdir(parents=True, exist_ok=True)
        subprocess.run(["pdftotext", "-bbox", str(book.path), str(cache)], check=True)
    root = ET.parse(cache).getroot()
    out = []
    for page in root.iter("{http://www.w3.org/1999/xhtml}page"):
        words = [
            (
                w.text or "",
                float(w.attrib["xMin"]),
                float(w.attrib["yMin"]),
                float(w.attrib["xMax"]),
                float(w.attrib["yMax"]),
            )
            for w in page.iter("{http://www.w3.org/1999/xhtml}word")
        ]
        out.append((float(page.attrib["width"]), float(page.attrib["height"]), words))
    return out


def rows(words: list[tuple[str, float, float, float, float]]) -> list[list[tuple[str, float, float, float, float]]]:
    lines: list[list[tuple[str, float, float, float, float]]] = []
    for word in sorted((w for w in words if w[2] > 5), key=lambda w: (w[2] + w[4]) / 2):
        mid = (word[2] + word[4]) / 2
        if lines and abs(statistics.mean((w[2] + w[4]) / 2 for w in lines[-1]) - mid) < 2.6:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w[1]) for line in lines]


def sections(year: int) -> list[Section]:
    found: list[Section] = []
    current: Section | None = None
    for index, (width, height, words) in enumerate(pages(year), 1):
        lines = rows(words)
        for n, line in enumerate(lines):
            text = " ".join(w[0] for w in line)
            office: tuple[str, int] | None = None
            if re.fullmatch(r"UNITED STATES SENATOR\W*", text):
                office = ("senate", 0)
            elif text.startswith("REPRESENTATIVE IN CONGRESS") and n + 1 < len(lines):
                nxt = " ".join(w[0] for w in lines[n + 1])
                if "CONGRESSIONAL DISTRICT" in nxt:
                    first = re.split(r"[\s-]+", nxt)
                    if first[0] in ORDINALS:
                        office = ("house", ORDINALS[first[0]])
                    else:
                        raise ValueError(f"{year} page {index}: {nxt}")
                else:
                    office = ("other", 0)
            elif (
                re.match(
                    r"(STATE|PRESIDENT|GOVERNOR|LIEUTENANT|ATTORNEY|SECRETARY|COMPTROLLER|TREASURER|DELEGATE)", text
                )
                and not any(c.isdigit() for c in text)
                and not text.startswith("STATE OF")
            ):
                office = ("other", 0)
            if office is not None:
                if office[0] == "other":
                    current = None
                elif office[0] == "house" or current is None or current["office"] != "senate":
                    current = {"office": office[0], "district": office[1], "candidates": [], "blocks": []}
                    found.append(current)
                continue
            if current is None:
                continue
            party = next((w for w in line if w[0] in PARTIES), None)
            totals = [w for w in line if number(w[0]) is not None and w[1] > (party[1] if party else 1e9)]
            if party is not None and totals and not any(w[0] == "COUNTY" for w in line):
                name = " ".join(w[0] for w in line if w[3] < party[1])
                current["candidates"].append((name, PARTIES[party[0]], int(number(totals[0][0]) or 0), index))
            elif line[0][0] == "COUNTY":
                current["blocks"].append({"heads": line[1:], "rows": []})
            elif current["blocks"] and number(line[0][0]) is None and re.match(r"[A-Z]", line[0][0]):
                current["blocks"][-1]["rows"].append((index, (width, height), line))
    return found


def parse() -> None:
    with connect() as conn:
        counties = {
            plain(text): text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            )
        }
        options = conn.execute(
            """select x.value, o.id::text, o.ballot_label, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^(1994-03-15|1996-03-19|1998-03-17):IL:(house|senate):'
                 and o.kind in ('candidate', 'write_in')
               group by 1, 2, 3"""
        ).fetchall()
    if len(counties) != 102:
        raise ValueError("Illinois needs 102 counties")
    out: list[dict[str, object]] = []
    failed: list[str] = []
    done: set[str] = set()
    for year, (_, day) in ITEMS.items():
        for section in sections(year):
            office, district = section["office"], section["district"]
            columns: dict[
                tuple[str, str], list[tuple[str, int, int, tuple[float, float, float, float], tuple[float, float]]]
            ] = {}
            for block in section["blocks"]:
                heads = block["heads"]
                cells: list[tuple[float, str, int, int, tuple[float, float, float, float], tuple[float, float]]] = []
                known: list[tuple[int, tuple[float, float], list[Word], str | None]] = []
                for page, size, line in block["rows"]:
                    name = " ".join(w[0] for w in line if number(w[0]) is None).strip(" .,'")
                    key = plain(name)
                    if not any(number(w[0]) is not None for w in line) or "CONTINUED" in name:
                        continue
                    if all(
                        w[0] in {"REP", "DEM", "REF", "LIB", "IND", "NLP", "TAX", "SWP", "CON", "GRN"} for w in line
                    ):
                        continue
                    close = sorted(counties, key=lambda c, k=key: -difflib.SequenceMatcher(None, k, c).ratio())[:1]
                    match = (
                        key
                        if key in counties
                        else close[0]
                        if close and difflib.SequenceMatcher(None, key, close[0]).ratio() >= 0.85
                        else None
                    )
                    known.append((page, size, line, match))
                order = sorted(counties)
                for n, (page, size, line, match) in enumerate(known):
                    if match is None:
                        before = next((m for *_, m in reversed(known[:n]) if m is not None), None)
                        after = next((m for *_, m in known[n + 1 :] if m is not None), None)
                        gap = order[
                            (order.index(before) + 1 if before else 0) : (order.index(after) if after else len(order))
                        ]
                        if before and after and len(gap) == 1 and not any(m == gap[0] for *_, m in known):
                            match = gap[0]
                    if match is None:
                        failed.append(f"{year} {office} {district}: county on page {page}")
                        continue
                    for word in (w for w in line if number(w[0]) is not None):
                        cells.append(
                            (word[3], counties[match], int(number(word[0]) or 0), page, tuple(word[1:5]), size)
                        )
                edges = sorted({round(cell[0]) for cell in cells})
                clusters: list[list[int]] = []
                for edge in edges:
                    if clusters and edge - clusters[-1][-1] <= 7:
                        clusters[-1].append(edge)
                    else:
                        clusters.append([edge])
                if len(clusters) != len(heads):
                    failed.append(f"{year} {office} {district}: {len(clusters)} columns for {len(heads)} headings")
                    continue
                for cell in cells:
                    c = next(i for i, cluster in enumerate(clusters) if round(cell[0]) in cluster)
                    columns.setdefault((heads[c][0], heads[c][0]), []).append(cell[1:])
            for name, party, total, _ in section["candidates"]:
                pool = [
                    (contest, option, label, filed)
                    for contest, option, label, filed in options
                    if contest.startswith(f"{day}:IL:{office}:")
                    and contest.endswith(f":{party}")
                    and (office == "senate" or contest.split(":")[3] == str(district))
                ]
                scored = sorted(
                    (
                        (difflib.SequenceMatcher(None, surname(name), surname(lb)).ratio(), c, o, lb, f)
                        for c, o, lb, f in pool
                    ),
                    reverse=True,
                )
                if not scored or scored[0][0] < 0.8 or len(scored) > 1 and scored[1][0] == scored[0][0]:
                    failed.append(f"{year} {office} {district}: no option for {name}")
                    continue
                _, contest, option, label, filed = scored[0]
                heading = next(
                    (
                        key
                        for key in columns
                        if difflib.SequenceMatcher(None, plain(key[0]), surname(name)).ratio() >= 0.8
                        or surname(name) in plain(key[0])
                    ),
                    None,
                )
                if heading is None:
                    failed.append(f"{contest}: no column for {name}")
                    continue
                column = columns[heading]
                summed = sum(cell[1] for cell in column)
                if summed != total:
                    failed.append(f"{contest}: {label} sums to {summed:,}, printed {total:,}")
                    continue
                if summed != filed and (contest, surname(label)) not in CHECKED:
                    failed.append(f"{contest}: {label} sums to {summed:,}, the FEC has {filed:,}")
                    continue
                done.add(contest)
                for county, votes, page, box, size in column:
                    out.append(
                        {
                            "year": year,
                            "contest": contest,
                            "option": option,
                            "county": county,
                            "votes": votes,
                            "page": page,
                            "box": [box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1]],
                        }
                    )
    for line in failed:
        print(line)
    left = {contest for contest, *_ in options} - done
    print(f"{len(done)} contests read, {len(left)} left out")
    frame = pl.DataFrame(out, infer_schema_length=None)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's U.S. House and Senate primaries by county in 1994, 1996, and 1998, "
            "from the State Board of Elections' official vote",
            polity="US",
        )
        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        pages_by: dict[tuple[int, int], UUID] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, (item, day) in ITEMS.items():
            title = TITLE.format(day=day)
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"https://archive.org/details/{item}",
                license=LICENSE,
            )
            book = raw.cached(SOURCE, url(year))
            artifacts[year] = cs.artifact(book, sources[year])
            for number_ in frame.filter(pl.col("year") == year)["page"].unique().sort():
                image = PAGES / f"{book.sha256}-{number_}.png"
                if not image.exists():
                    house_books._render(book, number_, 100, image, False)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages_by[(year, number_)] = cs.page(artifacts[year], number_)
                cs.add(
                    "source_page",
                    id=pages_by[(year, number_)],
                    artifact_id=artifacts[year],
                    page_number=number_,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id
                   where scheme = 'us.contest' and value ~ '^(1994-03-15|1996-03-19|1998-03-17):IL:(house|senate):'"""
            ).fetchall()
        )
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
        for (key, year), lines in frame.group_by("contest", "year", maintain_order=True):
            result_set = cs.key(f"{SOURCE}.result_set", str(key))
            if result_set not in cs.minted:
                continue
            contest = contests[str(key)]
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[int(year)],
                is_default=True,
                completeness=1,
            )
            for option, county, votes, page, box in lines.select(
                "option", "county", "votes", "page", "box"
            ).iter_rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[plain(str(county))],
                    "option_id": UUID(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=pages_by[(int(year), page)],
                    source_id=None,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                )
            present = {UUID(option) for option in lines["option"].unique()}
            for option, kind, seats in outcomes:
                if option in present:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
