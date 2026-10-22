import csv
import io
import json
import re
import statistics
import subprocess
import zipfile
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books, ne_books
from ea.store import DATA, connect

SOURCE = "us.ne_pri_books"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Official Report of the Board of State Canvassers, Primary Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
DAYS = {1994: date(1994, 5, 10), 1996: date(1996, 5, 14), 1998: date(1998, 5, 12)}
TURNED = {1996}
PARTIES = {
    "democrat": "democratic",
    "republican": "republican",
    "libertarian": "libertarian",
    "reform": "reform-congress",
}
FIXES: dict[tuple[int, int, str | None, str, int], int] = {
    (1994, 12, "REPUBLICANS VOTING", "cherry", 0): 1427,
    (1994, 10, "REPUBLICANS VOTING", "thomas", 0): 231,
    (1994, 10, "REPUBLICANS VOTING", "nemaha", 1): 173,
    (1994, 10, "INDEPENDENTS VOTING", "garden", 0): 3,
    (1994, 10, "INDEPENDENTS VOTING", "sarpy", 0): 533,
}
HELD = {(1998, p) for p in (7, 8, 13, 14, 20)}
SENATE = {1994: (4, 10), 1996: (8, 9)}


def member(year: int) -> str:
    return f"{ne_books.ZIP}#zip2/1990s Books/{year} Primary.pdf"


def fetch() -> None:
    raw.fetch(ne_books.SOURCE, ne_books.ZIP, suffix=".zip")
    archive = raw.cached(ne_books.SOURCE, ne_books.ZIP)
    with zipfile.ZipFile(archive.path) as z:
        for year in DAYS:
            try:
                raw.cached(SOURCE, member(year))
            except Exception:
                raw.store(
                    SOURCE,
                    member(year),
                    z.read(f"zip2/1990s Books/{year} Primary.pdf"),
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                )


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def render(year: int, page: int, dpi: int, path: Path) -> None:
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
        + [str(raw.cached(SOURCE, member(year)).path), str(path.with_suffix(""))],
        check=True,
    )
    if year in TURNED:
        with Image.open(path) as image:
            image.rotate(-90, expand=True).save(path)


def tokens(year: int, page: int) -> tuple[list[tuple[int, int, int, int, str]], tuple[int, int]]:
    PARSED.mkdir(parents=True, exist_ok=True)
    cache = PARSED / "ocr" / f"{year}-{page}.tsv"
    image = PARSED / "ocr" / f"{year}-{page}.png"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not image.exists():
        render(year, page, 300, image)
    with Image.open(image) as rendered:
        size = rendered.size
    if not cache.exists():
        cache.write_text(
            subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
            ).stdout
        )
    found = [
        (int(r["left"]), int(r["top"]), int(r["left"]) + int(r["width"]), int(r["top"]) + int(r["height"]), r["text"])
        for r in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
        if r["level"] == "5" and r["text"].strip()
    ]
    return found, size


def settle(year: int) -> list[dict[str, object]]:
    reads = [json.loads((PARSED / "model" / f"{year}.{which}.json").read_text()) for which in ("a", "b")]
    seconds = {(t["page"], t["district"], t["party"], t.get("group")): t for t in reads[1]}
    tables = []
    for first in reads[0]:
        second = seconds[(first["page"], first["district"], first["party"], first.get("group"))]
        where = (year, first["page"], first["district"], first["party"], first.get("group"))
        if len(first["columns"]) != len(second["columns"]) or first["total"] != second["total"]:
            raise ValueError(f"{where}: the reads disagree on the layout or the printed total")
        rows = {plain(k): (k, v) for k, v in second["rows"].items()}
        table: dict[str, list[int]] = {}
        for county, a in first["rows"].items():
            label, b = rows[plain(county)]
            merged = []
            for index, (x, y) in enumerate(zip(a, b, strict=True)):
                fix = FIXES.get((year, first["page"], first.get("group"), plain(county), index))
                if x != y and fix is None:
                    print(f"{where} {county} column {index}: {x} against {y}")
                if fix is not None:
                    merged.append(fix)
                else:
                    merged.append(x)
            table[county] = merged
        for index, printed in enumerate(first["total"]):
            summed = sum(r[index] for r in table.values())
            if summed != printed:
                print(f"{where} column {index}: counties sum to {summed}, printed {printed}")
        tables.append({"where": where, "columns": first["columns"], "rows": table, "total": first["total"]})
    return tables


def boxes(
    year: int, page: int, tables: list[dict[str, object]]
) -> dict[tuple[int, str, int], tuple[float, float, float, float]]:
    found, (width, height) = tokens(year, page)
    numbers = [(l, t, r, b, x.replace(",", "")) for l, t, r, b, x in found if x.replace(",", "").isdigit()]
    names = [(l, t, r, b, plain(x)) for l, t, r, b, x in found if plain(x)]
    out: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
    cursor = 0
    prior: dict[str, float] | None = None
    group_counties: list[str] = []
    for number, table in enumerate(tables):
        counties = list(table["rows"])
        rows_y: dict[str, float] = {}
        if number and group_counties == counties and prior is not None:
            rows_y = prior
        for county in () if rows_y else counties:
            wanted = plain(county)
            for index in range(cursor, len(names)):
                if (
                    names[index][4]
                    and SequenceMatcher(None, names[index][4], wanted[: len(names[index][4])]).ratio() > 0.85
                    and len(names[index][4]) >= min(4, len(wanted))
                ):
                    rows_y[county] = (names[index][1] + names[index][3]) / 2
                    cursor = index + 1
                    break
        if len(rows_y) < len(counties) // 2:
            raise ValueError(f"{table['where']}: only {len(rows_y)} county labels found")
        prior, group_counties = rows_y, counties
        pairs = [(i, rows_y[c]) for i, c in enumerate(counties) if c in rows_y]
        pitch = statistics.median(
            (y2 - y1) / (i2 - i1) for (i1, y1), (i2, y2) in zip(pairs, pairs[1:], strict=False) if i2 != i1
        )
        top = statistics.median(y - pitch * i for i, y in pairs)
        columns = len(table["columns"])
        edges: list[float] = []
        for column in range(columns):
            hits = []
            for minimum in (2, 1):
                for i, county in enumerate(counties):
                    value = str(table["rows"][county][column])
                    y = top + i * pitch
                    hits += [
                        t[2]
                        for t in numbers
                        if t[4] == value and len(value) >= minimum and abs((t[1] + t[3]) / 2 - y) < pitch / 2
                    ]
                if hits:
                    break
            if hits:
                common = statistics.mode(round(h / 8) for h in hits)
                edges.append(statistics.median(h for h in hits if round(h / 8) == common))
            else:
                edges.append(float("nan"))
        char = statistics.median((t[2] - t[0]) / len(t[4]) for t in numbers)
        for column in range(columns):
            if edges[column] != edges[column]:
                known = [e for e in edges if e == e]
                if not known:
                    band = [t[2] for t in numbers if top - pitch < (t[1] + t[3]) / 2 < top + len(counties) * pitch]
                    known = [statistics.median(band)]
                step = (
                    statistics.median(b - a for a, b in zip(known, known[1:], strict=False)) if len(known) > 1 else 90
                )
                edges[column] = (edges[column - 1] if column else known[0] - step) + step
        for column in range(columns):
            for i, county in enumerate(counties):
                value = str(table["rows"][county][column])
                y = top + i * pitch
                near = [
                    t
                    for t in numbers
                    if t[4] == value and abs(t[2] - edges[column]) < 14 and abs((t[1] + t[3]) / 2 - y) < pitch / 2
                ]
                if near:
                    box = near[0][:4]
                else:
                    w = char * len(value)
                    box = (edges[column] - w, y - pitch * 0.4, edges[column], y + pitch * 0.4)
                out[(number, county, column)] = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
        cursor = max(cursor, 0)
    return out


def parse() -> None:
    lines: list[dict[str, object]] = []
    for year in (1994, 1998):
        tables = settle(year)
        by_page: dict[int, list[dict[str, object]]] = {}
        for table in tables:
            by_page.setdefault(table["where"][1], []).append(table)
        for page, group in by_page.items():
            if (year, page) in HELD:
                continue
            placed = boxes(year, page, group)
            for number, table in enumerate(group):
                _, _, district, party, _ = table["where"]
                office = "senate" if district is None and page in SENATE.get(year, ()) else "house"
                key = f"{DAYS[year]}:NE:{office}" + (f":{district}" if district is not None else "")
                name = next(v for k, v in PARTIES.items() if k in party.lower())
                for county, values in table["rows"].items():
                    for column, label in enumerate(table["columns"]):
                        lines.append(
                            {
                                "year": year,
                                "key": f"{key}:{name}",
                                "label": label,
                                "county": county,
                                "votes": values[column],
                                "page": page,
                                "x0": placed[(number, county, column)][0],
                                "y0": placed[(number, county, column)][1],
                                "x1": placed[(number, county, column)][2],
                                "y1": placed[(number, county, column)][3],
                            }
                        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(lines).write_parquet(PARSED / "lines.parquet")
    print(len(lines), "cells")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 1994 and 1998 House and Senate primaries by county from the Board of State Canvassers' official reports",
            polity="US",
        )
        counties = {
            plain(text): (unit, area)
            for text, unit, area in conn.execute(
                """select n.text, u.id, u.area_id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
        state = cs.polity("US-NE")
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ '^199[468]-05-[0-9]+:NE:'"
            ).fetchall()
        )
        multi = (
            frame.filter(pl.col("key").str.contains(":house:"))
            .with_columns(district=pl.col("key").str.extract(r":house:(\d)", 1))
            .group_by("year", "county")
            .agg(pl.col("district").n_unique().alias("n"))
            .filter(pl.col("n") > 1)
        )
        shared = {(int(y), c) for y, c in multi.select("year", "county").iter_rows()}
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year in sorted(set(frame["year"])):
            title = TITLE.format(day=DAYS[year])
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ne_books.LANDING,
                license=LICENSE,
            )
            book = raw.cached(SOURCE, member(year))
            artifact = cs.artifact(book, sources[year])
            PAGES.mkdir(parents=True, exist_ok=True)
            for number in sorted(set(frame.filter(pl.col("year") == year)["page"])):
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    render(year, number, 100, image)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        parts: set[str] = set()
        for key, lines in frame.group_by("key", maintain_order=True):
            key = key[0]
            year = int(lines["year"][0])
            day = DAYS[year]
            contest = contests[key]
            election, district_area = conn.execute(
                "select election_id, district_area_id from contest where id = %s", [contest]
            ).fetchone()
            known = conn.execute(
                """select o.id, o.kind, coalesce(o.ballot_label, ''),
                          (select sum(v.value)::bigint from result_value v where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1)
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID] = {}
            for label, cells in lines.group_by("label", maintain_order=True):
                label = label[0]
                if "write" in label.lower():
                    options = [o for o, k, _, _ in known if k == "write_in"]
                else:
                    surname = plain(label.split()[-1])
                    options = [o for o, k, n, _ in known if k == "candidate" and surname in plain(n)]
                if len(options) != 1:
                    raise ValueError(f"{key}: no single option for {label}")
                picked[label] = options[0]
            for option, kind, name, total in known:
                got = sum(
                    int(cells["votes"].sum())
                    for label, option_id in picked.items()
                    if option_id == option
                    for cells in [lines.filter(pl.col("label") == label)]
                )
                if total != got:
                    raise ValueError(f"{key}: {name or kind} {got} by county, {total} in the default set")
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if result_set not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=False,
                supersedes_id=None,
                completeness=1,
            )
            held = {
                code
                for (code,) in conn.execute(
                    "select code from reporting_unit where scheme_code = 'us.county_part' and code like %s and valid_from <> %s",
                    [f"{year}:%", day],
                ).fetchall()
            }
            units: dict[str, UUID] = {}
            for county in lines["county"].unique(maintain_order=True):
                unit, area = counties[plain(county)]
                if (year, county) in shared:
                    district = key.split(":")[3]
                    part = f"{year}:{conn.execute('select code from reporting_unit where id = %s', [unit]).fetchone()[0]}:{district}"
                    if part in held:
                        part = f"{day}:{part.split(':', 1)[1]}"
                    units[county] = cs.unit("us.county_part", part)
                    if part in parts:
                        continue
                    parts.add(part)
                    cs.add(
                        "reporting_unit",
                        id=units[county],
                        polity_id=state,
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=unit,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=units[county],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                else:
                    units[county] = unit
            merged = lines.with_columns(option=pl.col("label").replace_strict({k: str(v) for k, v in picked.items()}))
            for (county, option), cells in merged.group_by("county", "option", maintain_order=True):
                fact = {
                    "result_set_id": result_set,
                    "unit_id": units[str(county)],
                    "option_id": UUID(str(option)),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add(
                    "result_value",
                    **fact,
                    flags=house_books.DIGITIZED | (house_books.DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                page, _, _ = pages[(year, int(cells["page"][0]))]
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=page,
                    source_id=None,
                    x0=float(cells["x0"].min()),
                    y0=float(cells["y0"].min()),
                    x1=float(cells["x1"].max()),
                    y1=float(cells["y1"].max()),
                )
        return cs.write()
