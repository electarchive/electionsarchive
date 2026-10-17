import difflib
import io
import json
import re
import struct
import subprocess
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Secretary of State of Texas"
ITEM = "https://archive.org/details/{item}"
FILE = "https://archive.org/download/{item}/{item}.pdf"
DPI = 600
DIGITIZED = 8
DERIVED = 1
VOLUMES = {
    1888: ("biennialreports06texagoog", date(1888, 11, 6), [192, 193, 194]),
    1900: ("biennialreports05texagoog", date(1900, 11, 6), [86, 87, 88, 89]),
    1904: ("biennialreportof1903texa", date(1904, 11, 8), [210, 211, 212, 213]),
    1906: ("biennialreportof1903texa", date(1906, 11, 6), [401, 402, 403, 404]),
}
FORMER = {"greer": ("Greer", "40055", date(1896, 3, 16))}
CORRECTED: dict[tuple[int, int, str, int], int] = {}
UNBALANCED = {
    (1888, 5, 0),
    (1888, 5, 2),
    (1888, 7, 1),
    (1888, 11, 0),
    (1900, 2, 0),
    (1900, 6, 4),
    (1900, 8, 1),
    (1900, 10, 2),
    (1900, 11, 0),
    (1900, 11, 1),
    (1900, 12, 0),
    (1900, 12, 1),
    (1900, 13, 1),
    (1906, 9, 2),
    (1906, 14, 0),
}
GAP = (
    "The county figures printed for {candidate} sum to {counted} votes, but the report gives a district total of "
    "{printed}. We show the county figures as they were printed."
)


def _artifact(year: int) -> raw.Artifact:
    url = FILE.format(item=VOLUMES[year][0])
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return raw.fetch(SOURCE, url, suffix=".pdf")


def fetch() -> None:
    for year in VOLUMES:
        _artifact(year)


def _render(pdf: Path, page: int, folder: Path) -> Path:
    image = folder / f"{page}"
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            str(DPI),
            "-gray",
            "-png",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            str(pdf),
            str(image),
        ],
        check=True,
    )
    return image.with_suffix(".png")


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def _read(image: Image.Image, box: list[int]) -> list[str]:
    texts = []
    for scale in (0.5, 0.33):
        crop = image.crop(tuple(box))
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
        padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        padded.paste(crop, (20, 20))
        buffer = io.BytesIO()
        padded.save(buffer, "PNG")
        texts.append(
            subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng", "-c", "tessedit_char_whitelist=0123456789"],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
    return texts


def _grid(dark: np.ndarray, grid: dict[str, object], rows: int) -> tuple[list[int], int | None, list[int]]:
    rules = [int(x) for x in grid["rules"]]
    top, bottom = int(grid["first"]), int(grid["total"] or grid["last"])
    names = dark[:, max(0, rules[0] - 700) : rules[0] - 10]
    profile = np.convolve(names.mean(axis=1), np.ones(15) / 15, mode="same")

    def snap(y: float) -> int:
        y = round(y)
        return y - 14 + int(np.argmax(profile[y - 14 : y + 15]))

    ys = [snap(y) for y in np.linspace(top, int(grid["last"]), rows)]
    share = dark[top - 20 : bottom + 20].mean(axis=0)
    snapped = []
    for x in rules:
        window = share[x - 12 : x + 13]
        snapped.append(x - 12 + int(np.argmax(window)) if window.max() > 0.2 else x)
    return ys, None if grid["total"] is None else snap(int(grid["total"])), snapped


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, (_, _, pages) in VOLUMES.items():
        book = _artifact(year)
        for page in pages:
            tables = json.loads((PARSED / "model" / f"{year}-{page}.json").read_text())["tables"]
            seconds = json.loads((PARSED / "model" / f"{year}-{page}.v2.json").read_text())["tables"]
            cache = PARSED / "ocr" / f"{book.sha256}-{page}.json"
            known: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
            with tempfile.TemporaryDirectory() as folder:
                image = _render(book.path, page, Path(folder))
                dark = np.asarray(Image.open(image).convert("L")) < 128
                cells: list[dict[str, object]] = []
                for table, second in zip(tables, seconds, strict=True):
                    ys, total, rules = _grid(dark, table["grid"], len(table["rows"]))
                    lines = [
                        (row["county"], row["votes"], again["votes"], y)
                        for row, again, y in zip(table["rows"], second["rows"], ys, strict=True)
                    ]
                    if total is not None:
                        lines.append(("Total", table["total"], second["total"], total))
                    for county, votes, agains, y in lines:
                        for column, (value, again) in enumerate(zip(votes, agains, strict=True)):
                            cells.append(
                                {
                                    "year": year,
                                    "page": page,
                                    "district": table["district"],
                                    "county": county,
                                    "column": column,
                                    "label": table["columns"][column],
                                    "model": value,
                                    "second": again,
                                    "box": [rules[column] + 6, y - 24, rules[column + 1] - 2, y + 24],
                                }
                            )
                missing = [cell for cell in cells if str(cell["box"]) not in known]
                if missing:
                    gray = Image.open(image).convert("L")
                    with ThreadPoolExecutor(12) as pool:
                        for cell, texts in zip(
                            missing,
                            pool.map(_read, [gray] * len(missing), [cell["box"] for cell in missing]),
                            strict=True,
                        ):
                            known[str(cell["box"])] = texts
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(known))
            for cell in cells:
                readings = Counter(
                    max(options, key=lambda n: options[n])
                    for text in known[str(cell["box"])]
                    if (options := scan.numbers(text))
                )
                ocr = readings.most_common(1)[0][0] if readings else None
                if cell["model"] is None and cell["second"] is None:
                    continue
                cell |= {
                    "ocr": ocr,
                    "readings": sorted({*readings, *([cell["second"]] if cell["second"] is not None else [])}),
                    "votes": cell["model"],
                    "status": "agreed"
                    if cell["second"] == cell["model"]
                    else "extra"
                    if cell["model"] is None
                    else "doubtful",
                }
                rows.append(cell)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    extra = frame.filter(pl.col("status") == "extra")
    if extra.height:
        raise ValueError(
            f"read as blank only once: {extra.select('page', 'district', 'county', 'column', 'second').rows()}"
        )
    for (year, district, county, column), votes in CORRECTED.items():
        match = (
            (pl.col("year") == year)
            & (pl.col("district") == district)
            & (pl.col("county") == county)
            & (pl.col("column") == column)
        )
        frame = frame.with_columns(
            votes=pl.when(match).then(votes).otherwise("votes"),
            status=pl.when(match).then(pl.lit("corrected")).otherwise("status"),
        )
    failed = []
    gaps: list[dict[str, object]] = []
    for (year, district, column), lines in frame.group_by("year", "district", "column", maintain_order=True):
        total = lines.filter(pl.col("county") == "Total")
        counties = lines.filter(pl.col("county") != "Total")
        if total.height == 0:
            failed.append((year, district, column, "no total"))
            continue
        printed = int(total["votes"][0])
        counted = int(counties["votes"].sum())
        if counted == printed:
            match = (pl.col("year") == year) & (pl.col("district") == district) & (pl.col("column") == column)
            frame = frame.with_columns(
                status=pl.when(match & (pl.col("status") == "doubtful")).then(pl.lit("balanced")).otherwise("status")
            )
            continue
        if (year, district, column) in UNBALANCED:
            gaps.append(
                {"year": year, "district": district, "label": lines["label"][0], "counted": counted, "printed": printed}
            )
            continue
        fixes = [
            (county, alternative)
            for county, votes, readings in counties.select("county", "votes", "readings").iter_rows()
            for alternative in set(readings) - {votes}
            if counted - votes + alternative == printed
        ]
        if len(fixes) == 1:
            county, alternative = fixes[0]
            match = (
                (pl.col("year") == year)
                & (pl.col("district") == district)
                & (pl.col("column") == column)
                & (pl.col("county") == county)
            )
            frame = frame.with_columns(
                votes=pl.when(match).then(alternative).otherwise("votes"),
                status=pl.when(match).then(pl.lit("balanced")).otherwise("status"),
            )
            continue
        failed.append((year, district, column, f"counties {counted}, printed {printed}, fixes {fixes}"))
    print(frame.group_by("status").len().sort("status"))
    print(
        "doubtful:",
        frame.filter(pl.col("status") == "doubtful")
        .select("page", "district", "county", "column", "votes", "readings")
        .rows(),
    )
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} columns disagree with their printed totals")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "values.parquet")
    pl.DataFrame(gaps, schema=["year", "district", "label", "counted", "printed"]).write_parquet(
        PARSED / "gaps.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(pl.col("county") != "Total")
    gaps = pl.read_parquet(PARSED / "gaps.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House results by county from the Texas Secretary of State's biennial reports",
            polity="US",
        )
        counties = {
            _plain(text): (code, unit)
            for code, unit, text in conn.execute(
                """select u.code, u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '48%%'"""
            ).fetchall()
        }
        units: dict[str, UUID] = {name: unit for name, (_, unit) in counties.items()}
        texas = cs.polity("US-TX")
        for plain, (label, successor, ended) in FORMER.items():
            unit = cs.unit("us.former_county", f"TX:{plain}")
            units[plain] = unit
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=texas,
                scheme_code="us.former_county",
                code=f"TX:{plain}",
                parent_id=cs.unit("us.state", "48"),
                area_relation="within",
                area_id=conn.execute(
                    "select area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [successor]
                ).fetchone()[0],
                kind="standard",
                valid_from=None,
                valid_to=ended,
            )
            cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)
        missing = sorted({county for county in frame["county"] if _plain(county) not in units})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like '%%:TX:house:%%'"
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, (item, _, numbers) in VOLUMES.items():
            title = f"Biennial Report of the Secretary of State of the State of Texas ({year} election returns)"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ITEM.format(item=item),
                license="Public domain",
            )
            book = _artifact(year)
            artifacts[year] = cs.artifact(book, sources[year])
            for number in numbers:
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(book.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifacts[year], number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifacts[year],
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
                )
        for (year, district), lines in frame.group_by("year", "district", maintain_order=True):
            held = VOLUMES[int(year)][1]
            key = f"{held}:TX:house:{district}"
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest")
                continue
            row = conn.execute(
                """select c.election_id, coalesce(min(v.count_round), 0),
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1))::bigint
                   from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
                   left join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                   where c.id = %s group by 1""",
                [contest],
            ).fetchone()
            if row is None:
                print(f"{key}: no default totals")
                continue
            election, first, official = row
            known = conn.execute(
                """select o.id, o.kind, coalesce(o.ballot_label, ''),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1)
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID] = {}
            for label in lines["label"].unique(maintain_order=True):
                if label.startswith("Scattering"):
                    match = [option for option, kind, _, _ in known if kind in ("others", "write_in")]
                else:
                    surname = _plain(label.split(",")[0])
                    match = [
                        option
                        for option, kind, name, _ in known
                        if kind == "candidate"
                        and difflib.SequenceMatcher(
                            None, surname, _plain(name.split()[-1] if name.split() else "")
                        ).ratio()
                        >= 0.8
                    ]
                if len(match) == 1:
                    picked[label] = match[0]
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if result_set not in cs.minted:
                continue
            if len(picked) < lines["label"].n_unique():
                pool = cs.key("us.option", f"{key}:others:tx_sos")
                cs.add("contest_option", id=pool, contest_id=contest, kind="others", ballot_label="Others")
                picked = {label: picked.get(label, pool) for label in lines["label"].unique()}
            lines = lines.with_columns(
                option=pl.col("label").replace_strict({label: str(option) for label, option in picked.items()})
            )
            counted = int(lines["votes"].sum())
            for option, kind, name, total in known:
                sum_ = int(lines.filter(pl.col("option") == str(option))["votes"].sum())
                if total and sum_ != total:
                    print(f"{key}: {name or kind} {sum_} by county, {total} in the default set")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[int(year)],
                is_default=False,
                completeness=round(min(counted / official, 1), 4) if official else None,
            )
            for label, summed, printed in (
                gaps.filter((pl.col("year") == year) & (pl.col("district") == district))
                .select("label", "counted", "printed")
                .iter_rows()
            ):
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=GAP.format(
                        candidate="scattered votes"
                        if str(label).startswith("Scattering")
                        else next(name for option, _, name, _ in known if option == picked[str(label)]),
                        counted=f"{summed:,}",
                        printed=f"{printed:,}",
                    ),
                )
            for (county, option), cells in lines.group_by("county", "option", maintain_order=True):
                unit = units[_plain(str(county))]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    flags=DIGITIZED | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                boxes = [list(box) for box in cells["box"] if box is not None]
                if not boxes or cells["page"].n_unique() > 1:
                    continue
                page, width, height = pages[(int(year), int(cells["page"][0]))]
                scale = DPI / 100
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    page_id=page,
                    source_id=None,
                    x0=min(box[0] for box in boxes) / width / scale,
                    y0=min(box[1] for box in boxes) / height / scale,
                    x1=max(box[2] for box in boxes) / width / scale,
                    y1=max(box[3] for box in boxes) / height / scale,
                )
        return cs.write()
