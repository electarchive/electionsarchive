import difflib
import html
import re
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.oh_pri_canvass"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
LICENSE = "Public record"
STATE = "OH"
FIPS = "39"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
BOOK = "micro_IA40706953_0173"
VOLUMES = {
    1994: (
        f"https://archive.org/download/{BOOK}/{BOOK}.pdf",
        f"https://archive.org/details/{BOOK}",
        date(1994, 5, 3),
        (32, 33, 34, 35, 36, 37, 82, 83, 84, 85, 86, 87),
    )
}
BOOK_TITLE = "Ohio Election Statistics for 1993–94"
PAGES_1998 = {
    "house": (
        "20021228025043",
        "http://www.state.oh.us:80/sos/1998%20congress%20primary.htm",
        "1998 US House of Representatives Primary Results",
    ),
    "senate": (
        "20000829090531",
        "http://www.state.oh.us:80/sos/1998_us_senate_primary_election_.htm",
        "1998 U. S. Senate Primary Election Results",
    ),
}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CHECKED: set[tuple[str, str]] = set()
DERIVED = {
    ("1994-05-03:OH:house:16:republican", "Ashland", 6179),
    ("1994-05-03:OH:house:12:democratic", "Licking", 2523),
}


def wayback(stamp: str, url: str) -> str:
    return f"http://web.archive.org/web/{stamp}id_/{url}"


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])
    for stamp, url, _ in PAGES_1998.values():
        raw.fetch(SOURCE, wayback(stamp, url), suffix=".html")


def surname(text: str) -> str:
    words = [w for w in re.split(r"[\s,.]+", text) if house_books._plain(w) not in {"jr", "sr", "ii", "iii", ""}]
    return house_books._plain(words[-1]) if words else ""


def parse() -> None:
    house_books.parse(sys.modules[__name__])
    with connect() as conn:
        options = conn.execute(
            """select x.value, o.id::text, o.ballot_label, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^(1994-05-03|1998-05-05):OH:(house|senate):'
                 and o.kind in ('candidate', 'write_in')
               group by 1, 2, 3"""
        ).fetchall()
    tables: list[
        tuple[int, str, int, str, list[tuple[str, int]], int | None, list[tuple[int, list[float]] | None]]
    ] = []
    book = pl.read_parquet(PARSED / "values.parquet")
    for (where, district, label), cells in book.group_by("state", "district", "label", maintain_order=True):
        office = str(where).split("-")[1]
        tables.append(
            (
                1994,
                office,
                int(district),
                str(label),
                list(zip(cells["county"].to_list(), cells["votes"].to_list(), strict=True)),
                None,
                list(zip(cells["page"].to_list(), cells["box"].to_list(), strict=True)),
            )
        )
    for office, (stamp, url, _) in PAGES_1998.items():
        text = raw.cached(SOURCE, wayback(stamp, url)).path.read_text(encoding="cp1252")
        rows = [
            [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            ]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE)
        ]
        district = 0
        heads: list[str] = []
        body: list[tuple[str, list[int]]] = []
        for row in [*rows, ["DISTRICT # 99"]]:
            if not row or not row[0] and not any(row):
                continue
            found = re.match(r"DISTRICT # (\d+)", row[0])
            if found or row[0] == "TOTALS":
                if row[0] == "TOTALS":
                    printed = [int(cell.replace(",", "")) for cell in row[-len(heads) :]]
                    for c, head in enumerate(heads):
                        column = [(county, votes[c]) for county, votes in body]
                        if sum(v for _, v in column) != printed[c]:
                            raise ValueError(f"1998 {office} {district} {head}: the column does not add up")
                        tables.append((1998, office, district, head, column, printed[c], [None] * len(column)))
                if found:
                    district = int(found[1])
                heads, body = [], []
                continue
            if office == "senate" and row[0] in ("COUNTY",):
                continue
            if not row[0] or office == "senate" and not body and not re.search(r"\d", "".join(row)):
                cells = row[1:] if not row[0] else row
                heads = [f"{a} {b}".strip() for a, b in zip(heads, cells, strict=True)] if heads else cells
                continue
            if row[0].startswith(("PERCENTAGE", "*")):
                continue
            body.append((row[0].rstrip("*"), [int(cell.replace(",", "")) for cell in row[-len(heads) :]]))
    rows_out: list[dict[str, object]] = []
    failed: list[str] = []
    used: set[str] = set()
    for year, office, district, label, column, printed, places in tables:
        day = "1994-05-03" if year == 1994 else "1998-05-05"
        pool = [
            (contest, option, name, filed)
            for contest, option, name, filed in options
            if contest.startswith(f"{day}:OH:{office}:")
            and (office == "senate" or contest.split(":")[3] == str(district))
        ]
        scored = sorted(
            (
                (difflib.SequenceMatcher(None, surname(label), surname(name)).ratio(), contest, option, name, filed)
                for contest, option, name, filed in pool
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.75 or len(scored) > 1 and scored[1][0] == scored[0][0]:
            failed.append(f"{year} {office} {district}: no option for {label}")
            continue
        _, contest, option, name, filed = scored[0]
        if option in used:
            failed.append(f"{year} {office} {district}: {label} matches {name} twice")
            continue
        used.add(option)
        summed = sum(votes for _, votes in column)
        if summed != filed and (contest, surname(name)) not in CHECKED:
            failed.append(f"{contest}: {name} sums to {summed:,}, the FEC has {filed:,}, printed {printed}")
            continue
        for (county, votes), place in zip(column, places, strict=True):
            rows_out.append(
                {
                    "year": year,
                    "office": office,
                    "contest": contest,
                    "option": option,
                    "county": county,
                    "votes": votes,
                    "page": None if place is None else place[0],
                    "box": None if place is None else place[1],
                }
            )
    left = {contest for contest, *_ in options} - {str(row["contest"]) for row in rows_out}
    for line in failed:
        print(line)
    print(f"{len(left)} contests left out: {sorted(left)}")
    bad = {line.split(":")[0] for line in failed if line.startswith("199")}
    pl.DataFrame([row for row in rows_out if row["contest"] not in bad], infer_schema_length=None).write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's U.S. House and Senate primaries by county in 1994 and 1998, "
            "from the Secretary of State's Ohio Election Statistics and official results pages",
            polity="US",
        )
        url, landing, _, numbers = VOLUMES[1994]
        sources: dict[int, UUID] = {}
        sources[1994] = cs.source(PUBLISHER, BOOK_TITLE)
        cs.add(
            "source",
            id=sources[1994],
            kind="official_body",
            publisher=PUBLISHER,
            title=BOOK_TITLE,
            url=landing,
            license=LICENSE,
        )
        book = raw.cached(RAW, url)
        artifact = cs.artifact(book, sources[1994])
        pages: dict[int, tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for number in numbers:
            image = PAGES / f"{book.sha256}-{number}.png"
            if not image.exists():
                house_books._render(book, number, 100, image, False)
            with Image.open(image) as rendered:
                width, height = rendered.size
            pages[number] = (cs.page(artifact, number), width, height)
            cs.add(
                "source_page",
                id=pages[number][0],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        web: dict[str, UUID] = {}
        for office, (stamp, link, title) in PAGES_1998.items():
            web[office] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=web[office],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=wayback(stamp, link).replace("id_/", "/"),
                license=LICENSE,
            )
            cs.artifact(raw.cached(SOURCE, wayback(stamp, link)), web[office])
        counties: dict[str, UUID] = {
            house_books._plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
        if len(counties) != 88:
            raise ValueError("Ohio needs 88 counties")
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id
                   where scheme = 'us.contest' and value ~ '^(1994-05-03|1998-05-05):OH:(house|senate):'"""
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
        for (key, year, office), lines in frame.group_by("contest", "year", "office", maintain_order=True):
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
                source_id=sources[1994] if year == 1994 else web[str(office)],
                is_default=True,
                completeness=1,
            )
            for option, county, votes, number, box in lines.select(
                "option", "county", "votes", "page", "box"
            ).iter_rows():
                plain = house_books._plain(str(county))
                unit = counties.get(plain) or next(u for name, u in counties.items() if name.startswith(plain))
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": UUID(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add(
                    "result_value",
                    **fact,
                    flags=(0 if number is None else house_books.DIGITIZED)
                    | (house_books.DERIVED if (key, county, votes) in DERIVED else 0),
                    value=votes,
                )
                if number is not None:
                    page, width, height = pages[number]
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=page,
                        source_id=None,
                        x0=box[0] / width / 3,
                        y0=box[1] / height / 3,
                        x1=box[2] / width / 3,
                        y1=box[3] / height / 3,
                    )
            present = {UUID(option) for option in lines["option"].unique()}
            for option, kind, seats in outcomes:
                if option in present:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
