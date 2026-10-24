import html
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mn_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Minnesota Legislative Reference Library"
BASE = "https://www.lrl.mn.gov/archive/sessions/electionresults/"
DAYS = [date(1956, 11, 6), date(1958, 11, 4), date(1960, 11, 8)]
DPI = 150
GAPS = {date(1920, 11, 2): 6.0, date(1936, 11, 3): 6.0, date(1940, 11, 5): 6.0}
ONLY_PAGES = {date(1920, 11, 2): 174, date(1936, 11, 3): 189, date(1940, 11, 5): 186}
FIXES: dict[tuple[date, int, int], dict[int, int]] = {}


def url(day: date) -> str:
    return f"{BASE}{day}-g-man.pdf"


def fetch() -> None:
    for day in DAYS + list(GAPS):
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    rows = []
    parsed_days = (
        [date.fromisoformat(value) for value in os.environ["MN_DAYS"].split(",")] if "MN_DAYS" in os.environ else DAYS
    )
    with connect() as conn:
        for day in parsed_days:
            codes = [
                code
                for (code,) in conn.execute(
                    """select distinct u.code from reporting_unit u
                       where u.scheme_code = 'us.county' and u.code like '27%%' and u.valid @> %s::date
                       order by 1""",
                    [day],
                ).fetchall()
            ]
            size = len(codes)
            held = {
                total
                for (total,) in conn.execute(
                    """select sum(v.value)::bigint
                       from external_id x
                       join contest c on c.id = x.entity_id
                       join result_set rs on rs.contest_id = c.id and rs.is_default
                       join contest_option o on o.contest_id = c.id and o.parent_option_id is null
                       join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                       where x.scheme = 'us.contest' and x.value like %s and x.value not like %s
                       group by o.id""",
                    [f"{day}:MN:%", f"{day}:MN:%:%"],
                ).fetchall()
            }
            pdf = raw.cached(SOURCE, url(day)).path
            pages = subprocess.run(
                ["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True
            ).stdout.split("<page ")[1:]
            for number, page in enumerate(pages, 1):
                if day in ONLY_PAGES and number != ONLY_PAGES[day]:
                    continue
                width, height = (
                    float(value) for value in re.search(r'width="([\d.]+)" height="([\d.]+)"', page).groups()
                )
                words = [
                    (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
                    for x0, y0, x1, y1, text in re.findall(
                        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>',
                        page,
                    )
                ]
                if day in GAPS:
                    ends = [x1 for _, _, x1, _, text in words if re.fullmatch(r"\d{2,6}", text)]
                    edges = sorted(
                        {round(end, 0) for end in ends if sum(1 for other in ends if abs(other - end) < 1.5) >= 40}
                    )
                    split = []
                    for x0, y0, x1, y1, text in words:
                        if re.fullmatch(r"\d{8,12}", text):
                            char = (x1 - x0) / len(text)
                            cut = min(
                                (
                                    (abs(x0 + char * k - edge), k, edge)
                                    for k in range(2, len(text) - 1)
                                    for edge in edges
                                    if abs(x0 + char * k - edge) < char * 0.6 and edge < x1 - char
                                ),
                                default=None,
                            )
                            if cut is not None:
                                _, k, edge = cut
                                split.append((x0, y0, edge, y1, text[:k]))
                                split.append((edge + char * 0.2, y0, x1, y1, text[k:]))
                                continue
                        split.append((x0, y0, x1, y1, text))
                    words = split
                numbers = sorted(
                    (
                        word
                        for word in words
                        if re.fullmatch(r"[\d,~'$oO.\-]+", word[4]) and (re.search(r"\d", word[4]) or word[4] in "oO")
                    ),
                    key=lambda word: word[2],
                )
                columns: list[list[tuple[float, float, float, float, str]]] = []
                for word in numbers:
                    if columns and word[2] - columns[-1][-1][2] < GAPS.get(day, 12):
                        columns[-1].append(word)
                    else:
                        columns.append([word])
                columns = [sorted(column, key=lambda word: word[1]) for column in columns if len(column) >= size - 2]
                template = next(
                    (column for column in columns if len(column) == size + 1),
                    None,
                )
                for index, column in enumerate(columns):
                    if template is not None and len(column) != size + 1:
                        shifts = sorted(min((word[1] - ref[1] for ref in template), key=abs) for word in column)
                        shift = shifts[len(shifts) // 2]
                        slots: dict[int, list[tuple[float, float, float, float, str]]] = {}
                        for word in column:
                            row, gap = min(
                                ((row, abs(word[1] - shift - ref[1])) for row, ref in enumerate(template)),
                                key=lambda pair: pair[1],
                            )
                            if gap < (template[1][1] - template[0][1]) * 0.4:
                                slots.setdefault(row, []).append(word)
                        if len(slots) != size + 1 or any(len(slot) != 1 for slot in slots.values()):
                            continue
                        column = [slots[row][0] for row in range(size + 1)]
                    if len(column) != size + 1:
                        continue
                    values = [
                        int(re.sub(r"\D", "", word[4].replace("o", "0").replace("O", "0")) or 0) for word in column
                    ]
                    for row, value in FIXES.get((day, number, index), {}).items():
                        values[row] = value
                    if values[-1] < 10:
                        continue
                    tied = sum(values[:-1]) == values[-1]
                    if not tied and sum(values[:-1]) in held:
                        values[-1] = sum(values[:-1])
                        tied = True
                    rows.extend(
                        (
                            day,
                            number,
                            index,
                            row,
                            codes[row],
                            values[row],
                            values[-1],
                            tied,
                            word[0] / width,
                            word[1] / height,
                            word[2] / width,
                            word[3] / height,
                        )
                        for row, word in enumerate(column[:-1])
                    )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "page": pl.Int16,
            "column": pl.Int16,
            "row": pl.Int16,
            "code": pl.String,
            "votes": pl.Int64,
            "total": pl.Int64,
            "tied": pl.Boolean,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    if (PARSED / "columns.parquet").exists():
        frame = pl.concat(
            [pl.read_parquet(PARSED / "columns.parquet").filter(~pl.col("day").is_in(parsed_days)), frame]
        )
    frame.write_parquet(PARSED / "columns.parquet")
    print(
        frame.group_by("day", "page", "column", "total", "tied", maintain_order=True)
        .len()
        .sort("day", "page", "column")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "columns.parquet").filter("tied")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's general elections by county from the Legislative Manual's canvass tables",
            polity="US-MN",
        )
        sources: dict[date, UUID] = {}
        artifacts: dict[date, UUID] = {}
        pages: dict[tuple[date, int], str] = {}
        lines = []
        for day in frame["day"].unique(maintain_order=True):
            title = f"Minnesota Legislative Manual {day.year + 1}–{day.year + 2}, election results"
            sources[day] = cs.source(PUBLISHER, title)
            if sources[day] in cs.minted:
                cs.add(
                    "source",
                    id=sources[day],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url="https://www.lrl.mn.gov/mngov/electionresults",
                    license="Public record",
                )
            artifacts[day] = cs.artifact(raw.cached(SOURCE, url(day)), sources[day])
            options = conn.execute(
                """select split_part(x.value, ':', 3), o.id, o.ballot_label, sum(v.value)::bigint
                   from external_id x
                   join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join contest_option o on o.contest_id = c.id and o.parent_option_id is null
                   join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value like %s and x.value not like %s
                   group by 1, 2, 3""",
                [f"{day}:MN:%", f"{day}:MN:%:%"],
            ).fetchall()
            byoffice: dict[str, list[tuple[UUID, str, int]]] = {}
            for office, option, label, total in options:
                byoffice.setdefault(office, []).append((option, label, total))
            columns = (
                frame.filter(pl.col("day") == day)
                .group_by("page", "column", "total", maintain_order=True)
                .agg(pl.col("row").len())
            )
            for office, choices in byoffice.items():
                matched: dict[str, tuple[int, int]] = {}
                for _, label, total in choices:
                    hits = [
                        (page, column)
                        for page, column, column_total, _ in columns.iter_rows()
                        if column_total == total and total > 0
                    ]
                    if len(hits) == 1:
                        matched[label] = hits[0]
                if len([choice for choice in choices if choice[2] > 0]) != len(matched) or len(
                    set(matched.values())
                ) != len(matched):
                    print(f"{day} {office}: {len(matched)} of {len(choices)} options matched, skipped")
                    continue
                for label, (page, column) in matched.items():
                    pages.setdefault((day, page), "")
                    lines.append(
                        frame.filter(pl.col("day") == day, pl.col("page") == page, pl.col("column") == column).select(
                            year=pl.lit(day.year, pl.Int32),
                            chamber=pl.lit(office),
                            day=pl.col("day"),
                            district=pl.lit(None, pl.Int16),
                            code="code",
                            candidate=pl.lit(label),
                            special=pl.lit(False),
                            writein=pl.lit(False),
                            votes="votes",
                            state=pl.lit("MN"),
                            measure=pl.lit(1, pl.Int16),
                            kind=pl.lit("county"),
                            source=pl.lit(str(day)),
                            page=pl.lit(page, pl.Int16),
                            x0="x0",
                            y0="y0",
                            x1="x1",
                            y1="y1",
                        )
                    )
        for day, number in pages:
            pdf = raw.cached(SOURCE, url(day)).path
            target = DATA / "pages" / SOURCE / f"{pdf.stem}-{number}.png"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf), str(target.with_suffix(""))],
                    check=True,
                )
            with Image.open(target) as rendered:
                width, height = rendered.size
            page = cs.page(artifacts[day], number)
            pages[day, number] = str(page)
            if page in cs.minted:
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifacts[day],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{target.name}",
                    width=width,
                    height=height,
                )
        if not lines:
            raise ValueError("no contest has every option matched to a tied column")
        medsl.attach(
            cs,
            conn,
            pl.concat(lines).with_columns(
                page_id=pl.struct("day", "page").map_elements(
                    lambda row: pages[row["day"], row["page"]], return_dtype=pl.String
                )
            ),
            {str(day): source for day, source in sources.items()},
            strict=True,
        )
        return cs.write()
