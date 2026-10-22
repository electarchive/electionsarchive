import re
import struct
import subprocess
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ms_scan
from ea.store import DATA, connect

SOURCE = "us.ms_pri_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
BASE = "https://www.sos.ms.gov/elections/electionResults/"
PUBLISHER = "Mississippi Secretary of State"
TITLE = "{party} primary certified results, {day:%B %-d, %Y}"
COUNTIES = 82
FILES: dict[tuple[int, str], tuple[str, date]] = {
    (2012, "democratic"): ("Democrat%20Certification_2012%20Primary.pdf", date(2012, 3, 13)),
}
GRIDDED = {
    (2012, "democratic"): {
        1: ("president", None, "OBAMA, BARACK"),
        3: ("senate", None, "GORE, ALBERT N., JR."),
        4: ("senate", None, "OATIS, WILL"),
        5: ("senate", None, "WEINER, ROGER"),
        7: ("house", 1, "MORRIS, BRAD"),
        9: ("house", 2, "MCTEER, HEATHER"),
        10: ("house", 2, "THOMPSON, BENNIE G."),
        12: ("house", 3, "BIGGS, CRYSTAL"),
    },
}
FOOTING = {
    "2012-03-13:MS:senate:democratic": "The sheet's printed state totals exceed the sum of its county cells for three candidates: Albert N. Gore Jr. printed 49,157, county sum 49,151 (6 short); Will Oatis printed 16,300, county sum 16,299 (1 short); Roger Weiner printed 21,131, county sum 21,127 (4 short). Every county cell was read twice and checked by eye, so the difference is in the sheet's own footing.",
    "2012-03-13:MS:house:3:democratic": "The sheet's printed state total for Crystal Biggs is 15,291, and the sum of its county cells is 15,283 (8 short). Every county cell was read twice and checked by eye, so the difference is in the sheet's own footing.",
}
FIXES: dict[tuple[int, str, int, int], int] = {(2012, "democratic", 4, 77): 27}


def url(year: int, party: str) -> str:
    return BASE + FILES[(year, party)][0]


def fetch() -> None:
    for year, party in FILES:
        raw.fetch(SOURCE, url(year, party), suffix=".pdf")


def geometry(image):
    ys, xs = ms_scan.rules(image)
    ys = [y for i, y in enumerate(ys) if not (i + 1 < len(ys) and ys[i + 1] - y < 30)]
    xs = [x for i, x in enumerate(xs) if not (i + 1 < len(xs) and xs[i + 1] - x < 70)]
    top = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i]) + 1
    return ys[top - 1 : top + 1], ys[top:], xs


def parse() -> None:
    with connect() as conn:
        codes = sorted(
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.county' and code like '28%%'"
            ).fetchall()
        )
    if len(codes) != COUNTIES:
        raise ValueError(f"{len(codes)} counties")
    out: list[dict[str, object]] = []
    for (year, party), layout in GRIDDED.items():
        pdf = raw.cached(SOURCE, url(year, party)).path
        pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)[1])
        county = 0
        reads: dict[tuple[int, int], Counter[int]] = {}
        sites: dict[tuple[int, int], tuple[int, tuple[int, int, int, int], tuple[int, int]]] = {}
        printed: dict[int, Counter[int]] = {r: Counter() for r in layout}
        for page in range(2, pages + 1):
            image = ms_scan.render(pdf, page, dpi=300)
            ys, xs = ms_scan.rules(image)
            ys = [y for i, y in enumerate(ys) if not (i + 1 < len(ys) and ys[i + 1] - y < 45)]
            xs = [x for i, x in enumerate(xs) if not (i + 1 < len(xs) and xs[i + 1] - x < 100)]
            top = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i]) + 1
            rows = ys[top:]
            first = 1 if page == 2 else 0
            columns = list(range(first, len(xs) - 1))
            last = page == pages
            clean = ms_scan.erase(image)
            for _, cx, cy, text in ms_scan.words(clean):
                c = next((i for i in columns if xs[i] <= cx < xs[i + 1]), None)
                r = next((i for i in layout if rows[i] <= cy < rows[i + 1]), None)
                if c is None or r is None:
                    continue
                if last and c == len(xs) - 2:
                    printed[r][int(text)] += 1
                else:
                    reads.setdefault((r, county + columns.index(c)), Counter())[int(text)] += 1
            for r in layout:
                for c in columns[: len(columns) - (1 if last else 0)]:
                    box = (int(xs[c]) + 8, int(rows[r]) + 8, int(xs[c + 1]) - 8, int(rows[r + 1]) - 8)
                    sites[(r, county + columns.index(c))] = (page, box, image.shape)
            county += len(columns) - (1 if last else 0)
        if county != COUNTIES:
            raise ValueError(f"{year} {party}: {county} counties")
        for r, (office, district, name) in layout.items():
            total = printed[r].most_common(1)[0][0]
            values = {k: v.most_common(1)[0][0] for k, v in reads.items() if k[0] == r}
            for key in values:
                values[key] = FIXES.get((year, party, r, key[1]), values[key])
            short = total - sum(values.values())
            print(year, party, name, total, "tied" if short == 0 else f"printed total exceeds the county sum by {short}")
            for (_, index), value in values.items():
                page, box, shape = sites[(r, index)]
                out.append(
                    {
                        "year": year,
                        "chamber": f"{office}:{party}",
                        "day": FILES[(year, party)][1],
                        "special": False,
                        "state": "MS",
                        "district": district,
                        "code": codes[index],
                        "candidate": name,
                        "measure": 1,
                        "writein": False,
                        "votes": value,
                        "kind": "county",
                        "source": f"MS{year}{party}",
                        "file": 0,
                        "page": page,
                        "x0": box[0] / shape[1],
                        "y0": box[1] / shape[0],
                        "x1": box[2] / shape[1],
                        "y1": box[3] / shape[0],
                    }
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
            summary="Mississippi's 2012 Democratic presidential, U.S. Senate, and House primaries by county from the Secretary of State's certified results",
            polity="US-MS",
        )
        sources: dict[str, object] = {}
        pages: dict[tuple[str, int], str] = {}
        for (year, party), (_, day) in FILES.items():
            title = TITLE.format(party=party.capitalize(), day=day)
            source = cs.source(PUBLISHER, title)
            sources[f"MS{year}{party}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url(year, party),
                license="Public record",
            )
            pdf = raw.cached(SOURCE, url(year, party))
            artifact = cs.artifact(pdf, source)
            PAGES.mkdir(parents=True, exist_ok=True)
            for number in sorted(set(frame.filter(pl.col("source") == f"MS{year}{party}")["page"])):
                image = PAGES / f"{pdf.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifact, number)
                pages[(f"MS{year}{party}", number)] = str(page)
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
            page_id=pl.struct("source", "page").map_elements(
                lambda s: pages[(s["source"], s["page"])], return_dtype=pl.String
            ),
        ).with_columns(page=pl.lit(1))
        medsl.attach(cs, conn, frame, sources, strict=True)
        for row in list(cs.rows.get("result_set", [])):
            key = conn.execute(
                "select value from external_id where scheme = 'us.contest' and entity_id = %s", [row["contest_id"]]
            ).fetchone()
            if key is not None and key[0] in FOOTING:
                cs.add("note", entity_id=row["id"], kind="caveat", body_md=FOOTING[key[0]])
        return cs.write()
