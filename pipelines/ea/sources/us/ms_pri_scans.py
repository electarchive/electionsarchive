import json
import re
import struct
import subprocess
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

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
READS: dict[str, dict[str, object]] = json.loads(Path(__file__).with_name("ms_pri_reads.json").read_text())
NAMED: dict[str, dict[str, object]] = json.loads(Path(__file__).with_name("ms_pri_named.json").read_text())
COUNTIES = 82
FILES: dict[tuple[int, str], tuple[str, date]] = {
    (2012, "democratic"): ("Democrat%20Certification_2012%20Primary.pdf", date(2012, 3, 13)),
    (2012, "republican"): ("Republican%20Certification_2012%20Primary.pdf", date(2012, 3, 13)),
    (2014, "democratic"): ("2014%20Democrat%20Primary%20Election%20Results_Certified%206%2013%202014.pdf", date(2014, 6, 3)),
    (2014, "republican"): ("2014%20Republican%20Primary%20Election%20Results_Certified%206%2013%202014.pdf", date(2014, 6, 3)),
    (2014, "democratic-runoff"): ("2014%20Democrat%20Primary%20Runoff%20Election%20Results_Certified%207%207%202014.pdf", date(2014, 6, 24)),
    (2022, "republican"): ("2022%20Republican%20Primary%20Official%20Results.pdf", date(2022, 6, 7)),
    (2018, "democratic"): ("2018%20Democratic%20Primary%20Certified%20Results.pdf", date(2018, 6, 5)),
    (2018, "republican"): ("2018%20Republican%20Primary%20Certified%20Results.pdf", date(2018, 6, 5)),
    (2018, "democratic-runoff"): ("2018%20State%20Democratic%20Party%20Certified%20Primary%20Runoff%20Results.pdf", date(2018, 6, 26)),
    (2018, "republican-runoff"): ("2018%20State%20Republican%20Party%20Certified%20Primary%20Runoff%20Results.pdf", date(2018, 6, 26)),
}
RULED = {(2014, "democratic"): range(2, 8)}
ROTATED = {(2012, "republican"): range(9, 15)}
TURNED = {(2022, "republican")}
EMIT = {
    (2012, "republican"): {("house", 1), ("house", 2)},
    (2014, "democratic"): {("house", 1), ("house", 2), ("house", 3)},
    (2014, "republican"): {("house", 1), ("house", 3)},
    (2014, "democratic-runoff"): {("house", 3)},
    (2022, "republican"): {("house", 1), ("house", 2), ("house", 3), ("house", 4)},
}
PARSE: set[tuple[int, str]] = set()
PARSE_NAMED = {"2018:democratic", "2018:democratic-runoff", "2018:republican", "2018:republican-runoff"}
GRIDDED = {
    (2022, "republican"): {
        0: ("house", 1, "KELLY, TRENT"),
        1: ("house", 1, "STRAUSS, MARK D."),
        3: ("house", 2, "CARSON, MICHAEL"),
        4: ("house", 2, "ELLER, RONALD"),
        5: ("house", 2, "FLOWERS, BRIAN"),
        6: ("house", 2, "JOHNSON, STANFORD"),
        8: ("house", 3, "CASSIDY, MICHAEL"),
        9: ("house", 3, "GRIFFIN, THOMAS B."),
        10: ("house", 3, "GUEST, MICHAEL"),
        12: ("house", 4, "BOYANTON, CARL"),
        13: ("house", 4, "BROOKS, RAYMOND N."),
        14: ("house", 4, "EZELL, MIKE"),
    },
    (2022, "republican+"): {
        0: ("house", 4, "PALAZZO, STEVEN M."),
        1: ("house", 4, "PETERSON, KIDRON"),
        2: ("house", 4, "WAGNER, CLAY"),
        3: ("house", 4, "WIGGINS, BRICE"),
    },
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
    (2014, "democratic"): {
        1: ("senate", None, "CHILDERS, TRAVIS W."),
        2: ("senate", None, "COMPTON, WILLIAM BOND, JR."),
        3: ("senate", None, "MARCY, BILL"),
        4: ("senate", None, "RAWL, JONATHAN"),
        7: ("house", 1, "DICKEY, RON E."),
        8: ("house", 1, "WEATHERS, REX N."),
        11: ("house", 2, "FAIRCLOTH, DAMIEN"),
        12: ("house", 2, "THOMPSON, BENNIE G."),
        15: ("house", 3, "LILJEBERG, JIM"),
        16: ("house", 3, "MAGEE, DOUGLAS MACARTHUR (DOUG)"),
        17: ("house", 3, "QUINN, DENNIS C."),
        20: ("house", 4, "CAUSEY, TRISH"),
        21: ("house", 4, "MOORE, MATT"),
    },
    (2014, "republican"): {
        1: ("senate", None, "CAREY, THOMAS L."),
        2: ("senate", None, "COCHRAN, THAD"),
        3: ("senate", None, "MCDANIEL, CHRIS"),
        5: ("house", 1, "NUNNELEE, ALAN"),
        7: ("house", 3, "CARAWAY, HARDY"),
        8: ("house", 3, "HARPER, GREGG"),
    },
    (2014, "democratic-runoff"): {
        1: ("house", 3, "MAGEE, DOUGLAS MACARTHUR (DOUG)"),
        2: ("house", 3, "QUINN, DENNIS C."),
    },
    (2012, "republican"): {
        1: ("president", None, "BACHMANN, MICHELE"),
        2: ("president", None, "GINGRICH, NEWT"),
        3: ("president", None, "HUNTSMAN, JON"),
        4: ("president", None, "JOHNSON, GARY"),
        5: ("president", None, "PAUL, RON"),
        6: ("president", None, "PERRY, RICK"),
        7: ("president", None, "ROMNEY, MITT"),
        8: ("president", None, "SANTORUM, RICK"),
        10: ("senate", None, "HATHCOCK, E. ALLEN"),
        11: ("senate", None, "MALONEY, ROBERT"),
        12: ("senate", None, "WICKER, ROGER F."),
        14: ("house", 1, "ESTES, ROBERT"),
        15: ("house", 1, "NUNNELEE, ALLEN"),
        16: ("house", 1, "ROSS, HENRY"),
        18: ("house", 2, "MARCY, BILL"),
        20: ("house", 3, "ALLEN, ROBERT"),
        21: ("house", 3, "HARPER, GREGG"),
        23: ("house", 4, "BURLESON, CINDY"),
        24: ("house", 4, "PALAZZO, STEVEN"),
        25: ("house", 4, "VINCENT, RON"),
    },
}
FOOTING = {
    "2012-03-13:MS:senate:democratic": "The sheet's printed state totals exceed the sum of its county cells for three candidates: Albert N. Gore Jr. printed 49,157, county sum 49,151 (6 short); Will Oatis printed 16,300, county sum 16,299 (1 short); Roger Weiner printed 21,131, county sum 21,127 (4 short). Every county cell was read twice and checked by eye, so the difference is in the sheet's own footing.",
    "2012-03-13:MS:house:3:democratic": "The sheet's printed state total for Crystal Biggs is 15,291, and the sum of its county cells is 15,283 (8 short). Every county cell was read twice and checked by eye, so the difference is in the sheet's own footing.",
}
FIXES: dict[tuple[int, str, int, int], int] = {
    (2012, "democratic", 4, 77): 27,
    (2012, "republican", 16, 52): 11,
    (2012, "republican", 16, 69): 597,
}


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
    for (year, tag), layout in GRIDDED.items():
        if (year, tag) not in PARSE:
            continue
        party = tag.rstrip("+")
        pdf = raw.cached(SOURCE, url(year, party)).path
        pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)[1])
        county = 0
        cleans: dict[int, np.ndarray | None] = {}
        reads: dict[tuple[int, int], Counter[int]] = {}
        sites: dict[tuple[int, int], tuple[int, tuple[int, int, int, int], tuple[int, int]]] = {}
        printed: dict[int, Counter[int]] = {r: Counter() for r in layout}
        turned = ROTATED.get((year, party))
        ruled = RULED.get((year, party))
        sheet = turned if turned is not None else ruled if ruled is not None else range(2, pages + 1)
        read = READS.get(f"{year}:{tag}")
        if read is not None:
            for page, count in zip(read["pages"], read["columns"]):
                image = ms_scan.render(pdf, page, dpi=300, turn=read.get("turn", 0))
                body = (image[read["band"][0] : read["band"][1]] < 225).mean(0)
                found = [x for x in ms_scan.clusters(np.where(body > read["share"])[0]) if read["xmin"].get(str(page), read["xmin"]["default"]) < x < read["xmax"].get(str(page), read["xmax"]["default"])]
                pitch = float(np.median(np.diff(found)))
                xs = [found[0]]
                for x in found[1:]:
                    gap = round((x - xs[-1]) / pitch)
                    xs.extend(xs[-1] + (x - xs[-1]) * k / gap for k in range(1, gap))
                    xs.append(x)
                xs = xs[: count + 1]
                if len(xs) != count + 1:
                    raise ValueError(f"{year} {party} page {page}: {len(xs) - 1} columns, expected {count}")
                rule = (image[read["anchor"][0] : read["anchor"][1], int(xs[0]) + 30 : int(xs[0]) + 400] < 200).mean(1)
                anchor = read["anchor"][0] + int(np.argmax(rule))
                bounds = read["bounds"]
                for r in layout:
                    for c in range(count):
                        box = (int(xs[c]) + 8, anchor + bounds[r] + 8, int(xs[c + 1]) - 8, anchor + bounds[r + 1] - 8)
                        sites[(r, county + c)] = (page, box, image.shape)
                county += count
            for r in layout:
                for index, value in enumerate(read["rows"][str(r)]):
                    if value is not None:
                        reads[(r, index)] = Counter({value: 2})
                printed[r][read["printed"][str(r)]] += 2
            sheet = range(0)
        for page in sheet:
            image = ms_scan.render(pdf, page, dpi=300, turn=-90 if turned is not None else 0)
            if turned is not None:
                body = (image[520:1700] < 220).mean(0)
                start = max(range(760, 840), key=lambda x0: sum(body[int(x0 + 161.8 * k) - 2 : int(x0 + 161.8 * k) + 3].sum() for k in range(15)))
                xs = [start + 161.8 * k for k in range(15)]
                found = ms_scan.words(ms_scan.erase(image))
                heights = np.array([cy for _, cx, cy, _ in found if cx > xs[0] and 480 < cy < 1660])
                top = min(
                    np.arange(448, 493, 0.5),
                    key=lambda t: np.abs((heights - t) / 45.35 - 0.5 - np.round((heights - t) / 45.35 - 0.5)).sum(),
                )
                rows = [top + 45.35 * k for k in range(27)]
                first = 0
                if page == sheet[-1]:
                    xs = xs[: max(next(i for i in range(14) if xs[i] <= cx < xs[i + 1]) for _, cx, _, _ in found if cx > xs[0]) + 2]
            elif ruled is not None:
                body = (image[480:1860] < 225).mean(0)
                xs = [x for x in ms_scan.clusters(np.where(body > 0.3)[0]) if x > 700]
                top = next(y for y in ms_scan.clusters(np.where((image[:, 100:3900] < 200).mean(1) > 0.45)[0]) if y > 400)
                rows = [top + 60.45 * k for k in range(24)]
                first = 0
                if page == sheet[-1]:
                    xs = xs[: COUNTIES - county + 2]
            else:
                ys, xs = ms_scan.rules(image)
                ys = [y for i, y in enumerate(ys) if not (i + 1 < len(ys) and ys[i + 1] - y < 45)]
                xs = [x for i, x in enumerate(xs) if not (i + 1 < len(xs) and xs[i + 1] - x < 100)]
                top = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i]) + 1
                rows = ys[top:]
                first = 1 if page == 2 else 0
            columns = list(range(first, len(xs) - 1))
            last = page == sheet[-1]
            for _, cx, cy, text in found if turned is not None else ms_scan.words(ms_scan.erase(image)):
                c = next((i for i in columns if xs[i] <= cx < xs[i + 1]), None)
                r = next((i for i in layout if rows[i] <= cy < rows[i + 1]), None)
                if c is None or r is None:
                    continue
                if last and ruled is not None:
                    if c < COUNTIES - county:
                        reads.setdefault((r, county + columns.index(c)), Counter())[int(text)] += 1
                elif last and c == len(xs) - 2:
                    printed[r][int(text)] += 1
                else:
                    reads.setdefault((r, county + columns.index(c)), Counter())[int(text)] += 1
            if ruled is not None and last:
                n = COUNTIES - county
                for r in layout:
                    crop = Image.fromarray(ms_scan.erase(image)[int(rows[r]) + 8 : int(rows[r + 1]) - 8, int(xs[n]) + 6 : int(xs[n]) + 232])
                    for scale, psm in ((1, 7), (2, 7), (1, 8), (2, 8), (1, 13), (2, 13)):
                        text = ms_scan.read(crop, scale, psm, whitelist="0123456789,").replace(" ", "").replace(",", "")
                        if text.isdigit():
                            printed[r][int(text)] += 1
            for r in layout:
                for c in columns[: len(columns) - (1 if last else 0)]:
                    box = (int(xs[c]) + 8, int(rows[r]) + 8, int(xs[c + 1]) - 8, int(rows[r + 1]) - 8)
                    sites[(r, county + columns.index(c))] = (page, box, image.shape)
            county += len(columns) - (1 if last else 0)
            cleans[page] = ms_scan.erase(image) if turned is not None or ruled is not None else None
        if turned is not None or ruled is not None:
            for key in list(sites):
                page, (x0, y0, x1, y1), _ = sites[key]
                crop = cleans[page][y0:y1, x0:x1]
                if (crop < 150).sum() < 25:
                    reads.pop(key, None)
                elif key not in reads or reads[key].most_common(1)[0][1] < 2:
                    votes: Counter[int] = Counter()
                    for scale, psm in ((1, 7), (2, 7), (1, 8), (2, 8), (1, 13), (2, 13)):
                        text = ms_scan.read(Image.fromarray(crop), scale, psm).replace(" ", "")
                        if text.isdigit():
                            votes[int(text)] += 1
                    if votes:
                        reads[key] = votes
        if county != COUNTIES:
            raise ValueError(f"{year} {party}: {county} counties")
        for r, (office, district, name) in layout.items():
            if not printed[r]:
                continue
            total = printed[r].most_common(1)[0][0]
            values = {k: v.most_common(1)[0][0] for k, v in reads.items() if k[0] == r}
            for key in values:
                values[key] = FIXES.get((year, party, r, key[1]), values[key])
            short = total - sum(values.values())
            print(year, party, name, total, "tied" if short == 0 else f"printed total exceeds the county sum by {short}")
            if (year, party) in EMIT and (office, district) not in EMIT[(year, party)]:
                continue
            for (_, index), value in values.items():
                page, box, shape = sites[(r, index)]
                out.append(
                    {
                        "year": year,
                        "chamber": f"{office}:{party.removesuffix('-runoff')}",
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
    for tag, sheet in NAMED.items():
        if tag not in PARSE_NAMED:
            continue
        year, party = int(tag[:4]), tag[5:]
        pdf = raw.cached(SOURCE, url(year, party)).path
        names = sorted({c for each in NAMED.values() for page in each["pages"] for c in page["counties"]})
        if len(names) != COUNTIES:
            raise ValueError(f"{len(names)} county names")
        code_of = dict(zip(names, codes))
        sums = [0] * len(sheet["rows"])
        for page in sheet["pages"]:
            image = ms_scan.render(pdf, page["page"], dpi=300, turn=sheet.get("turn", 0))
            if sheet["rules"]:
                ys, xs = ms_scan.rules(image, share=0.5)
            for index, name in enumerate(page["counties"]):
                for r, (office, district, candidate) in enumerate(sheet["rows"]):
                    value = page["values"][index][r]
                    if value is None:
                        continue
                    sums[r] += value
                    if sheet["rules"]:
                        x0, x1 = xs[page["first"] + index], xs[page["first"] + index + 1]
                        y0, y1 = (ys[0] + y for y in page["rows"][r])
                    else:
                        x0, y0, x1, y1 = page["boxes"][index]
                    out.append(
                        {
                            "year": year,
                            "chamber": f"{office}:{party.removesuffix('-runoff')}",
                            "day": FILES[(year, party)][1],
                            "special": False,
                            "state": "MS",
                            "district": district,
                            "code": code_of[name],
                            "candidate": candidate,
                            "measure": 1,
                            "writein": False,
                            "votes": value,
                            "kind": "county",
                            "source": f"MS{year}{party}",
                            "file": 0,
                            "page": page["page"],
                            "x0": (x0 + 8) / image.shape[1],
                            "y0": (y0 + 8) / image.shape[0],
                            "x1": (x1 - 8) / image.shape[1],
                            "y1": (y1 - 8) / image.shape[0],
                        }
                    )
        for r, (_, _, candidate) in enumerate(sheet["rows"]):
            print(year, party, candidate, sheet["printed"][r], "tied" if sheet["printed"][r] == sums[r] else f"printed total exceeds the county sum by {sheet['printed'][r] - sums[r]}")
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
            summary="Mississippi's presidential, U.S. Senate, and House primaries and runoffs by county from the Secretary of State's certified results",
            polity="US-MS",
        )
        sources: dict[str, object] = {}
        pages: dict[tuple[str, int], str] = {}
        for (year, party), (_, day) in FILES.items():
            title = f"{party.removesuffix('-runoff').capitalize()} primary {'runoff ' if party.endswith('-runoff') else ''}certified results, {day:%B %-d, %Y}"
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
                if (year, party) in ROTATED or (year, party) in TURNED:
                    Image.open(image).rotate(-90, expand=True).save(image)
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
