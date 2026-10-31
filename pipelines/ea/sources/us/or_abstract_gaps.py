import re
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.or_pri_abstract import NUMBER, lines_of
from ea.store import DATA, connect

SOURCE = "us.or_abstract_gaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-032"
PUBLISHER = "Oregon Secretary of State"
FILES = {
    2002: ("2002 Primary Election Official Results.PDF", 6873551, date(2002, 5, 21), False, "Primary Election", None),
    2008: ("2008 General Election Official Results.PDF", 6873598, date(2008, 11, 4), False, "General Election", {3}),
    2010: ("2010 Primary Election Official Results.PDF", 6873663, date(2010, 5, 18), False, "Primary Election", None),
    2012: (
        "2012 Special General Election Official Results.PDF",
        6873732,
        date(2012, 1, 31),
        True,
        "Special Election",
        None,
    ),
    2024: (
        "2024 November General Election Official Results(2).PDF",
        13735458,
        date(2024, 11, 5),
        False,
        "General Election",
        {5},
    ),
}
ONLY = {2008: 1, 2024: 5}
TITLE = re.compile(
    r"(UNITED STATES SENATOR|REPRESENTATIVE IN CONGRESS, (\d+)(?:ST|ND|RD|TH) DISTRICT)(?: (DEMOCRAT|REPUBLICAN))?"
)
PARTY = re.compile(r"(Democrat|Republican)", re.IGNORECASE)
TAG = re.compile(r"\s*\([A-Z]+\)$")


def url(year: int) -> str:
    return f"https://records.sos.state.or.us/ORSOSWebDrawer/Record/{FILES[year][1]}/File/document"


def fetch() -> None:
    for year, (name, *_) in FILES.items():
        try:
            raw.cached(SOURCE, url(year))
        except LookupError:
            raw.store(
                SOURCE,
                url(year),
                (INBOX / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%%'"""
            ).fetchall()
        }
    out: list[dict[str, object]] = []
    for year, (_, _, day, special, stage, pages) in FILES.items():
        primary = stage == "Primary Election"
        office = district = party = None
        collecting = after_total = False
        header: list[list[tuple[str, float, float]]] = []
        table: list[tuple[str, int, float, float, list[tuple[int, float, float, float, float]]]] = []
        rows: list[tuple[int, float, float, list[tuple[str, float, float, float, float]]]] = []
        for line in lines_of(raw.cached(SOURCE, url(year)).path):
            if (
                rows
                and all(NUMBER.fullmatch(w[0]) for w in line[3])
                and not any(NUMBER.fullmatch(w[0]) for w in rows[-1][3])
            ):
                rows[-1] = (line[0], line[1], line[2], sorted([*rows[-1][3], *line[3]], key=lambda w: w[1]))
            elif (
                rows
                and all(NUMBER.fullmatch(w[0]) for w in rows[-1][3])
                and not any(NUMBER.fullmatch(w[0]) for w in line[3])
            ):
                rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], sorted([*rows[-1][3], *line[3]], key=lambda w: w[1]))
            else:
                rows.append(line)
        for page, width, height, row in rows:
            if pages is not None and page not in pages:
                continue
            text = " ".join(w[0] for w in row)
            if not collecting:
                found = TITLE.fullmatch(text.upper())
                lone = PARTY.fullmatch(text)
                ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th) District", text)
                start = False
                if found:
                    office = "senate" if found[1].startswith("UNITED") else "house"
                    district = int(found[2]) if found[2] else None
                    party = found[3].lower().replace("democrat", "democratic") if found[3] else None
                    start = not primary or party is not None
                elif text == "US Representative":
                    office, district, party = "house", None, None
                elif ordinal and office == "house" and district is None:
                    district = int(ordinal[1])
                    start = not primary
                elif lone and office is not None and (after_total or party is None):
                    party = lone[1].lower().replace("democrat", "democratic")
                    start = True
                elif after_total:
                    office = None
                after_total = False
                if start:
                    collecting, header, table = True, [], []
                continue
            if row[0][0].lower() == "total":
                values = [w for w in row if NUMBER.fullmatch(w[0])]
                printed = [int(w[0].replace(",", "")) for w in values]
                for index, want in enumerate(printed):
                    got = sum(cells[index][0] for _, _, _, _, cells in table)
                    if got != want:
                        raise ValueError(f"{year} {office} {district} {party} column {index}: {got} against {want}")
                slots: list[list[str]] = [[] for _ in printed[:-1]]
                for line in header:
                    for text_, center, right in line:
                        slot = min(
                            range(len(slots)),
                            key=lambda i: min(
                                abs((values[i][1] + values[i][3]) / 2 - center), abs(values[i][3] - right)
                            ),
                        )
                        slots[slot].append(text_)
                names = []
                for index, found_words in enumerate(slots):
                    if not found_words:
                        raise ValueError(f"{year} {office} {district} {party}: no header for column {index}")
                    surname = found_words[0].strip("*")
                    given = TAG.sub("", " ".join(found_words[1:]))
                    names.append(f"{surname}, {given}".upper())
                for county, pg, w, h, cells in table if ONLY.get(year, district) == district else []:
                    for index, (value, x0, y0, x1, y1) in enumerate(cells):
                        misc = index == len(names)
                        out.append(
                            {
                                "year": year,
                                "chamber": f"{office}:{party}" if primary else office,
                                "day": day,
                                "special": special,
                                "state": "OR",
                                "district": district,
                                "code": codes[re.sub(r"[^a-z]", "", county.lower())],
                                "candidate": "WRITE-INS" if misc else names[index],
                                "measure": 1,
                                "writein": misc,
                                "votes": value,
                                "kind": "county",
                                "source": "OR",
                                "file": 0,
                                "page": pg,
                                "x0": x0 / w,
                                "y0": y0 / h,
                                "x1": x1 / w,
                                "y1": y1 / h,
                            }
                        )
                collecting, after_total = False, True
                continue
            numbers = [w for w in row if NUMBER.fullmatch(w[0])]
            if not numbers:
                line: list[list[tuple[str, float, float]]] = []
                for w in row:
                    if w[0].lower() in {"county", "misc.", "misc"}:
                        continue
                    if line and w[1] - line[-1][-1][2] < 6:
                        line[-1].append((w[0], w[1], w[3]))
                    else:
                        line.append([(w[0], w[1], w[3])])
                header.append([(" ".join(t[0] for t in g), (g[0][1] + g[-1][2]) / 2, g[-1][2]) for g in line])
                continue
            table.append(
                (
                    " ".join(w[0] for w in row if not NUMBER.fullmatch(w[0])),
                    page,
                    width,
                    height,
                    [(int(w[0].replace(",", "")), w[1], w[2], w[3], w[4]) for w in numbers],
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's 2002 and 2010 U.S. Senate and House primaries, 2008 and 2024 general House seats, and the 2012 special, by county from the Secretary of State's official results",
            polity="US-OR",
        )
        sources: dict[str, object] = {}
        pages: dict[tuple[int, int], str] = {}
        for year, (_, _, day, _, stage, _) in FILES.items():
            title = f"{day:%B %-d, %Y} {stage} Official Results"
            source = cs.source(PUBLISHER, title)
            sources[f"OR{year}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url(year),
                license="Public record",
            )
            pdf = raw.cached(SOURCE, url(year))
            artifact = cs.artifact(pdf, source)
            PAGES.mkdir(parents=True, exist_ok=True)
            for number in sorted(set(frame.filter(pl.col("year") == year)["page"])):
                image = PAGES / f"{pdf.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifact, number)
                pages[(year, number)] = str(page)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        frame = frame.with_columns(
            page_id=pl.struct("year", "page").map_elements(
                lambda s: pages[(s["year"], s["page"])], return_dtype=pl.String
            ),
            source=pl.format("OR{}", pl.col("year")),
        ).with_columns(page=pl.lit(1))
        medsl.attach(cs, conn, frame, sources, strict=True)
        return cs.write()
