import re
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.or_pri_abstract"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
BASE = "https://records.sos.state.or.us/ORSOSCMSearch/Search/RecordViewer.aspx?uri=13735456"
INBOX = DATA / "inbox" / "manual" / "MF-023"
PUBLISHER = "Oregon Secretary of State"
TITLE = "{day:%B %-d, %Y}, Primary Election Abstract of Votes"
FILES = {
    2016: ("2016 Primary Election Official Results.PDF", date(2016, 5, 17)),
    2018: ("2018 Primary Election Official Results.PDF", date(2018, 5, 15)),
    2022: ("2022 May Primary Election Official Results(2).PDF", date(2022, 5, 17)),
    2024: ("2024 May Primary Election Official Results(2).PDF", date(2024, 5, 21)),
}
OFFICES = {"President": "president", "US President": "president", "US Senator": "senate", "US Representative": "house"}
PARTIES = {
    "Democrat": "democratic",
    "Republican": "republican",
    "Independent": "independent",
    "Libertarian": "libertarian",
    "Constitution": "constitution",
    "Pacific Green": "pacific-green",
    "Progressive": "progressive",
    "Working Families": "working-families",
    "Independent Party": "independent",
}
NUMBER = re.compile(r"\d[\d,]*")


def url(year: int) -> str:
    return f"{BASE}#{year}"


def fetch() -> None:
    for year, (name, _) in FILES.items():
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


def lines_of(path: Path) -> list[tuple[int, float, float, list[tuple[str, float, float, float, float]]]]:
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    root = ElementTree.fromstring(
        re.sub(r"<!DOCTYPE[^>]*>", "", xml).replace('xmlns="http://www.w3.org/1999/xhtml"', "")
    )
    out = []
    for number, page in enumerate(root.iter("page"), 1):
        width, height = float(page.get("width")), float(page.get("height"))
        words = sorted(
            (
                (w.text or "", float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")))
                for w in page.iter("word")
            ),
            key=lambda w: (w[2], w[1]),
        )
        rows: list[list[tuple[str, float, float, float, float]]] = []
        for word in words:
            if rows and abs(rows[-1][0][2] - word[2]) < 3:
                rows[-1].append(word)
            else:
                rows.append([word])
        for row in rows:
            out.append((number, width, height, sorted(row, key=lambda w: w[1])))
    return out


def groups(row: list[tuple[str, float, float, float, float]]) -> list[tuple[str, float]]:
    found: list[list[tuple[str, float, float, float, float]]] = []
    for word in row:
        if found and word[1] - found[-1][-1][3] < 4.5:
            found[-1].append(word)
        else:
            found.append([word])
    return [(" ".join(w[0] for w in g), (g[0][1] + g[-1][3]) / 2) for g in found]


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
    for year, (_, day) in FILES.items():
        office = party = None
        district = None
        header: list[list[tuple[str, float]]] = []
        table: list[tuple[str, int, float, float, list[tuple[int, float, float, float, float]]]] = []
        after_banner = False
        for page, width, height, row in lines_of(raw.cached(SOURCE, url(year)).path):
            text = " ".join(w[0] for w in row)
            if "Primary Election Abstract of Votes" in text:
                after_banner = True
                continue
            if after_banner:
                after_banner = False
                office, district, party, header = OFFICES.get(text), None, None, []
                continue
            if office is None:
                continue
            if found := re.fullmatch(r"(\d+)(?:st|nd|rd|th) District", text):
                district, party, header = int(found[1]), None, []
                continue
            if text in PARTIES and party is None:
                party, header, table = PARTIES[text], [], []
                continue
            if party is None:
                continue
            if row[0][0] == "Total":
                values = [w for w in row if NUMBER.fullmatch(w[0])]
                printed = [int(w[0].replace(",", "")) for w in values]
                for index, want in enumerate(printed):
                    got = sum(cells[index][0] for _, _, _, _, cells in table)
                    if got != want:
                        raise ValueError(f"{year} {office} {district} {party} column {index}: {got} against {want}")
                names = columns(header, len(printed))
                for county, pg, w, h, cells in table:
                    for (value, x0, y0, x1, y1), (surname, given) in zip(cells, names, strict=True):
                        misc = surname == "Misc."
                        out.append(
                            {
                                "year": year,
                                "chamber": f"{office}:{party}",
                                "day": day,
                                "special": False,
                                "state": "OR",
                                "district": district,
                                "code": codes[re.sub(r"[^a-z]", "", county.lower())],
                                "candidate": "WRITE-INS" if misc else f"{surname.strip('*')}, {given}".upper(),
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
                party, header, table = None, [], []
                continue
            numbers = [w for w in row if NUMBER.fullmatch(w[0])]
            county = " ".join(w[0] for w in row if not NUMBER.fullmatch(w[0]))
            if not numbers or row[0][0] == "County":
                if body := [w for w in row if w[0] != "County"]:
                    header.append(groups(body))
                continue
            table.append(
                (
                    county,
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


def columns(header: list[list[tuple[str, float]]], size: int) -> list[tuple[str, str]]:
    main = next((i for i, line in enumerate(header) if len(line) == size), None)
    if main is None:
        raise ValueError(f"{size} columns, header {header}")
    centers = [center for _, center in header[main]]
    before: list[list[str]] = [[] for _ in range(size)]
    after: list[list[str]] = [[] for _ in range(size)]
    for index, line in enumerate(header):
        for text, center in line if index != main else []:
            slot = min(range(size), key=lambda i: abs(centers[i] - center))
            (before if index < main else after)[slot].append(text)
    out = []
    for slot, (text, _) in enumerate(header[main]):
        surname = "".join(f"{w}" if w.endswith("-") else f"{w} " for w in [*before[slot], text]).strip()
        out.append((surname, re.sub(r"\s*\(WI\)", "", " ".join(after[slot]))))
    return out


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's 2016, 2018, 2022, and 2024 presidential, U.S. Senate, and House primaries by county from the Secretary of State's abstracts of votes",
            polity="US-OR",
        )
        sources: dict[str, object] = {}
        pages: dict[tuple[int, int], str] = {}
        for year, (_, day) in FILES.items():
            title = TITLE.format(day=day)
            source = cs.source(PUBLISHER, title)
            sources[f"OR{year}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=BASE,
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
