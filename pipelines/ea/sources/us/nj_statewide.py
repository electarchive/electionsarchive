import re
from uuid import UUID

import polars as pl
from PIL import Image

from ea.changeset import Changeset
from ea.sources.us import nj_manual
from ea.store import DATA, connect

SOURCE = "us.nj_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DIGITIZED = 8
DERIVED = 1
VOLUMES = {
    1892: range(361, 363),
    1896: range(430, 432),
    1900: range(444, 445),
    1902: range(526, 527),
    1904: range(529, 532),
    1908: (562, 565),
    1910: range(543, 544),
    1912: range(653, 654),
    1914: range(628, 629),
    1916: range(618, 619),
    1920: range(540, 541),
}
DEM = r"(?<!Social )(?<!National )Democrat"
TABLES = [
    (
        1892,
        361,
        slice(0, 5),
        ("1892-11-08:NJ:president",),
        "president",
        [DEM, "Republican", "Prohibition", "Socialist Labor", "Populist"],
    ),
    (
        1892,
        362,
        slice(0, 5),
        ("1892:NJ:governor", "1892-11-08:NJ:governor"),
        "governor",
        [DEM, "Republican", None, None, None],
    ),
    (1896, 431, slice(0, 5), ("1895:NJ:governor",), "governor", ["Republican", DEM, None, None, None]),
    (
        1896,
        430,
        slice(2, 7),
        ("1896-11-03:NJ:president",),
        "president",
        ["Republican", DEM, "National Prohibition", "National Democrat", "Socialist Labor"],
    ),
    (
        1900,
        444,
        slice(2, 8),
        ("1900-11-06:NJ:president",),
        "president",
        ["Republican", DEM, "Prohibition", "Social Democratic", "Socialist Labor", "Populist"],
    ),
    (
        1904,
        529,
        slice(0, 6),
        ("1904-11-08:NJ:president",),
        "president",
        ["Republican", DEM, "Prohibition", r"Socialist(?! Labor)", "Socialist Labor", "Populist"],
    ),
    (
        1904,
        531,
        slice(0, 6),
        ("1904:NJ:governor", "1904-11-08:NJ:governor"),
        "governor",
        ["Republican", DEM, None, None, None, None],
    ),
    (
        1908,
        562,
        slice(0, 6),
        ("1908-11-03:NJ:president",),
        "president",
        ["Republican", DEM, r"Socialist(?! Labor)", "Prohibition", "Socialist Labor", "Independence"],
    ),
    (
        1912,
        653,
        slice(0, 6),
        ("1912-11-05:NJ:president",),
        "president",
        [DEM, "Progressive", "Republican", "Prohibition", r"Socialist(?! Labor)", "Socialist Labor"],
    ),
    (1902, 526, slice(2, 7), ("1901:NJ:governor",), "governor", ["Republican", DEM, None, None, None]),
    (1908, 565, slice(0, 5), ("1907:NJ:governor",), "governor", ["Republican", DEM, None, None, None]),
    (1910, 543, slice(0, 5), ("1910:NJ:governor", "1910-11-08:NJ:governor"), "governor", [DEM, "Republican", None, None, None]),
    (1914, 628, slice(0, 7), ("1913:NJ:governor",), "governor", [None, None, None, DEM, None, None, "Republican"]),
    (1916, 618, slice(0, 2), ("1916-11-07:NJ:president",), "president", ["Republican", DEM]),
    (1916, 618, slice(2, 4), ("1916-11-07:NJ:senate",), "senate", ["Republican", DEM]),
    (1916, 618, slice(4, 6), ("1916-11-07:NJ:governor", "1916:NJ:governor"), "governor", ["Republican", DEM]),
    (
        1920,
        540,
        slice(0, 7),
        ("1920-11-02:NJ:president",),
        "president",
        ["Republican", DEM, None, r"Socialist(?! Labor)", "Prohibition", "Farmer-Labor", "Socialist Labor"],
    ),
]


def prepare() -> None:
    models = PARSED / "model"
    for suffix, name in (("", "read1.txt"), (".v2", "read2.txt")):
        blocks: dict[str, list[tuple[str, list[tuple[str, list[str]]]]]] = {}
        for text in (models / name).read_text().split("@")[1:]:
            lines = text.strip().splitlines()
            rows = [tuple(part.strip() for part in line.split("|", 1)) for line in lines[1:]]
            title = next(rest for head, rest in rows if head == "title")
            data = [(head, rest.split()) for head, rest in rows if head not in ("title", "columns")]
            blocks.setdefault(lines[0].strip(), []).append((title, data))
        out: dict[int, list[str]] = {year: [] for year in VOLUMES}
        for tid, (volume, page, cols, _, _, patterns) in enumerate(TABLES, 1):
            title, data = next(
                block
                for block in blocks[f"{volume}_{page}"]
                if re.search("Governor|Elector|President", block[0], re.IGNORECASE)
            )
            out[volume].append(f"@{page}") if f"@{page}" not in out[volume] else None
            out[volume].append(f"{tid} | " + ", ".join(pattern or "Others" for pattern in patterns))
            for county, cells in data:
                out[volume].append(f"{county} | " + " ".join(cells[cols]))
        for volume, lines in out.items():
            (models / f"{volume}{suffix}.txt").write_text("\n".join(lines) + "\n")


def parse() -> None:
    nj_manual.parse(
        {year: (*nj_manual.VOLUMES[year][:4], pages, False) for year, pages in VOLUMES.items()},
        PARSED / "model",
        PARSED / "values.parquet",
        set(),
    )


def emit():
    frame = pl.read_parquet(PARSED / "values.parquet").filter(pl.col("county") != "Totals")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's president, governor, and Senate results by county from the Manual of the Legislature",
            polity="US",
        )
        counties = {
            nj_manual._plain(text): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%%'"""
            ).fetchall()
        }
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':NJ:'")
        )
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, numbers in VOLUMES.items():
            item, title, publisher, *_ = nj_manual.VOLUMES[year]
            sources[year] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher=publisher,
                title=title,
                url=nj_manual.ITEM.format(item=item),
                license="Public domain",
            )
            book = nj_manual._artifact(year)
            artifact = cs.artifact(book, sources[year])
            for number in numbers:
                image = nj_manual.PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    nj_manual._upright(book, number, 100, image, False)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{nj_manual.SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
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
        for tid, (volume, _, _, keys, _, patterns) in enumerate(TABLES, 1):
            lines = frame.filter((pl.col("year") == volume) & (pl.col("district") == tid))
            key = next(k for k in keys if k in contests)
            contest = contests[key]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is not None:
                continue
            options = conn.execute(
                """select o.id, o.kind, coalesce(o.ballot_label, '') || ' ' || coalesce(n.text, '')
                   from contest_option o left join name n on n.entity_id = o.party_id and n.is_preferred
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            others = next((o for o, kind, _ in options if kind == "others"), None)
            if others is None:
                others = cs.key("us.option", f"{key}:others:nj_statewide")
                cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Others")
            picked: dict[int, UUID] = {}
            for column, pattern in enumerate(patterns):
                if pattern is None:
                    picked[column] = others
                    continue
                named = [o for o, kind, text in options if kind != "others" and re.search(pattern, text)]
                if len(named) != 1:
                    raise ValueError(f"{key}: {len(named)} options for {pattern}")
                picked[column] = named[0]
            old = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall()
            for row in old:
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            official = conn.execute(
                """select sum(v.value)::bigint from result_value v join result_set rs on rs.k = v.result_set_k
                   where rs.contest_id = %s and rs.is_default and v.measure = 1""",
                [contest],
            ).fetchone()[0]
            lines = lines.with_columns(option=pl.col("column").replace_strict({c: str(o) for c, o in picked.items()}))
            totals = {option: int(group["votes"].sum()) for (option,), group in lines.group_by("option")}
            counted = sum(totals.values())
            for option, kind, _ in options:
                known = conn.execute(
                    """select sum(v.value)::bigint from result_value v join result_set rs on rs.k = v.result_set_k
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and rs.is_default and v.measure = 1 and o.id = %s""",
                    [contest, option],
                ).fetchone()[0]
                if known and totals.get(str(option)) not in (None, known):
                    print(f"{key}: {totals[str(option)]} by county, {known} in the default set")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[volume],
                is_default=True,
                completeness=round(min(counted / official, 1), 4) if official else None,
                supersedes_id=old[0][0] if old and old[0][0] != result_set else None,
            )
            for (county, option), cells in lines.group_by("county", "option", maintain_order=True):
                unit = counties[nj_manual._plain(str(county))]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                boxes = [list(box) for box in cells["box"]]
                page, width, height = pages[(volume, int(cells["page"][0]))]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=min(box[0] for box in boxes) / width / 3,
                    y0=min(box[1] for box in boxes) / height / 3,
                    x1=max(box[2] for box in boxes) / width / 3,
                    y1=max(box[3] for box in boxes) / height / 3,
                )
            top = max(totals, key=totals.__getitem__)
            kept = [(o, k, n) for o, k, n in outcomes if k not in {"elected", "won"} or str(o) == top]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not any(k in {"elected", "won"} for _, k, _ in kept):
                cs.add("outcome", result_set_id=result_set, option_id=UUID(top), kind="elected", seats=1)
        return cs.write()
