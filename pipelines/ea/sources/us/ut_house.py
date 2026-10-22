import html
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ut_sos
from ea.store import DATA, connect

SOURCE = "us.ut_house"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DPI = 100
TABLES = {2004: 3, 2006: 2, 2008: "U S House", 2010: "U.S. House"}
NAMES = {
    2004: [
        'Rob Bishop "R"', 'Charles Johnston "C"', 'Richard W. Soderberg "PC"', 'Steven Thompson "D"', 'Vena Finau "WI"',
        'Ronald R. Amos "PC"', 'Patrick S. Diehl "G"', 'Jim Matheson "D"', 'Jeremy Paul Petersen "C"', 'John Swallow "R"',
        'Beua Babka "D"', 'Chris Cannon "R"', 'Jim Dexter "L"', 'Curtis James "PC"', 'Darrell Ronald Winfield "C"',
        'Nicolle Rivetti "WI"',
    ],
    2006: [
        'Lynn Badler "L"', 'Rob Bishop "R"', 'Mark Hudson "C"', 'Steven Olsen "D"', 'Bob Brister "GP"',
        'Lavar Christensen "R"', 'Austin Sherwood Lett "L"', 'Jim Matheson "D"', 'W. David Perry "C"',
        'Christian Burridge "D"', 'Chris Cannon "R"', 'Philip Lear Hallman "L"', 'Jim Noorlander "C"',
    ],
}  # fmt: skip


def url(year: int) -> str:
    stamp, path = ut_sos.FILES[(year, "general")]
    return ut_sos.WAYBACK.format(stamp, ut_sos.SITE + path)


def fetch() -> None:
    ut_sos.fetch()


def parse() -> None:
    with connect() as conn:
        codes = {
            name.removesuffix(", UT").removesuffix(" County"): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '49%%' and u.valid @> '2004-01-01'::date"""
            ).fetchall()
        }
    rows = []
    for year, table in TABLES.items():
        document = raw.cached(ut_sos.SOURCE, url(year)).path
        columns: list[tuple[str, dict[str, tuple[int, tuple[float, float, float, float] | None]], int]] = []
        if isinstance(table, str):
            cells = [
                [str(cell).strip() if cell is not None else "" for cell in row]
                for row in fastexcel.read_excel(document)
                .load_sheet(table, header_row=None, dtypes="string")
                .to_polars()
                .iter_rows()
            ]
            start = next(index for index, row in enumerate(cells) if row[0].upper() == "COUNTY")
            end = next(index for index, row in enumerate(cells) if row[0].upper() == "TOTAL")
            for column in range(4, len(cells[start])):
                if not cells[start][column]:
                    continue
                counties = {
                    row[0]: (int(float(row[column])), None)
                    for row in cells[start + 1 : end]
                    if column < len(row) and row[column]
                }
                columns.append((re.sub(r"\s+", " ", cells[start][column]), counties, int(float(cells[end][column]))))
        else:
            page = subprocess.run(
                ["pdftotext", "-bbox", "-f", str(table), "-l", str(table), str(document), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            width, height = (
                float(value) for value in re.search(r'<page width="([\d.]+)" height="([\d.]+)"', page).groups()
            )
            words = sorted(
                (
                    (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
                    for x0, y0, x1, y1, text in re.findall(
                        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', page
                    )
                ),
                key=lambda word: word[1],
            )
            lines: list[list[tuple[float, float, float, float, str]]] = []
            for word in words:
                if lines and word[1] - lines[-1][-1][1] < 2.5:
                    lines[-1].append(word)
                else:
                    lines.append([word])
            for line in lines:
                line.sort()
            header = next(index for index, line in enumerate(lines) if line[0][4] == "COUNTY")
            total = next(index for index, line in enumerate(lines) if line[0][4].upper() == "TOTAL")
            ends = [word for word in lines[total][1:] if re.fullmatch(r"[\d,]+", word[4])][2:]
            body: dict[str, dict[int, tuple[int, tuple[float, float, float, float]]]] = {}
            for line in lines[header + 1 : total]:
                label = " ".join(word[4] for word in line if not re.fullmatch(r"[\d,.%]+", word[4]))
                numbers = [word for word in line if re.fullmatch(r"[\d,]+", word[4])][2:]
                body[label] = {}
                for word in numbers:
                    index = min(range(len(ends)), key=lambda i: abs(ends[i][2] - word[2]))
                    if index in body[label]:
                        raise ValueError(f"{year} {label}: two numbers in column {index}")
                    body[label][index] = (
                        int(word[4].replace(",", "")),
                        (word[0] / width, word[1] / height, word[2] / width, word[3] / height),
                    )
            centers = []
            for index in range(len(ends)):
                boxes = [county[index][1] for county in body.values() if index in county]
                centers.append(
                    sum((box[0] + box[2]) / 2 for box in boxes) / len(boxes) * width
                    if boxes
                    else (ends[index][0] + ends[index][2]) / 2
                )
            names: dict[int, list[tuple[float, float, float, float, str]]] = {}
            top = max(index for index, line in enumerate(lines[:header]) if any(word[4] == "District" for word in line))
            for line in lines[top + 1 : header + 1]:
                for word in line:
                    if word[4] in ("COUNTY", "VOTERS", "REGISTERED", "NUMBER", "VOTING", "BALLOTS", "CAST", "PERCENT"):
                        continue
                    center = (word[0] + word[2]) / 2
                    names.setdefault(min(range(len(ends)), key=lambda i: abs(centers[i] - center)), []).append(word)
            for index in range(len(ends)):
                if index not in names:
                    continue
                name = " ".join(word[4] for word in sorted(names[index], key=lambda word: (round(word[1]), word[0])))
                counties = {label: county[index] for label, county in body.items() if index in county}
                columns.append((name, counties, int(ends[index][4].replace(",", ""))))
            if year in NAMES:
                if len(columns) != len(NAMES[year]):
                    raise ValueError(f"{year}: {len(columns)} columns for {len(NAMES[year])} names")
                columns = [(name, counties, printed) for name, (_, counties, printed) in zip(NAMES[year], columns, strict=True)]
        patterns = {frozenset(counties) for _, counties, _ in columns}
        widest = [pattern for pattern in patterns if not any(pattern < other for other in patterns)]
        if len(widest) != 3:
            raise ValueError(f"{year}: {len(widest)} county patterns, not 3 districts")
        groups: dict[frozenset[str], list[int]] = {pattern: [] for pattern in widest}
        for index, (name, counties, _) in enumerate(columns):
            owners = [pattern for pattern in widest if frozenset(counties) <= pattern]
            if len(owners) != 1:
                raise ValueError(f"{year} {name}: its counties fit {len(owners)} districts")
            groups[owners[0]].append(index)
        for district, members in enumerate(sorted(groups.values()), start=1):
            for index in members:
                name, counties, printed = columns[index]
                if sum(value for value, _ in counties.values()) != printed:
                    raise ValueError(f"{year} district {district} {name}: counties do not add up to {printed}")
                for county, (votes, box) in counties.items():
                    rows.append(
                        (year, district, codes[county], re.sub(r'\s*"[^"]*"', "", name), votes, '"WI"' in name, table if box else None, *(box or (None,) * 4))
                    )
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
            "page": pl.Int16,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("year", "district", "candidate", maintain_order=True).agg(pl.col("votes").sum(), pl.len()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2004–2010 U.S. House generals by county from the Office of Elections' official canvasses",
            polity="US-UT",
        )
        source = cs.source(ut_sos.PUBLISHER, ut_sos.TITLE)
        pages: dict[int, str] = {}
        for year, table in TABLES.items():
            cached = raw.cached(ut_sos.SOURCE, url(year))
            artifact = cs.artifact(cached, source)
            if isinstance(table, str):
                continue
            target = DATA / "pages" / SOURCE / f"{cached.path.stem}-{table}.png"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(table), "-l", str(table), "-singlefile"]
                    + [str(cached.path), str(target.with_suffix(""))],
                    check=True,
                )
            with Image.open(target) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, table)
            pages[year] = str(page)
            if UUID(str(page)) in cs.minted:
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=table,
                    image_key=f"pages/{SOURCE}/{target.name}",
                    width=width,
                    height=height,
                )
        frame = pl.read_parquet(PARSED / "counties.parquet")
        for located, part in ((True, frame.filter(pl.col("page").is_not_null())), (False, frame.filter(pl.col("page").is_null()))):
            medsl.attach(
                cs,
                conn,
                part.select(
                    year="year",
                    chamber=pl.lit("house"),
                    day=pl.col("year").map_elements(lambda year: ut_sos.DAYS[(year, "general")], return_dtype=pl.Date),
                    special=pl.lit(False),
                    state=pl.lit("UT"),
                    district="district",
                    code="code",
                    candidate="candidate",
                    measure=pl.lit(1, pl.Int16),
                    writein="writein",
                    votes="votes",
                    kind=pl.lit("county"),
                    source=pl.lit("UT"),
                    *(
                        [
                            pl.col("year").replace_strict(pages, return_dtype=pl.String).alias("page_id"),
                            "x0",
                            "y0",
                            "x1",
                            "y1",
                        ]
                        if located
                        else []
                    ),
                ),
                {"UT": source},
                strict=True,
            )
        return cs.write()
