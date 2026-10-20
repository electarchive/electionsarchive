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

SOURCE = "us.oh_statewide"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Ohio Secretary of State"
TITLE = "Ohio Election Statistics, {day:%Y}"
LICENSE = "Public record"
STATE = "OH"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ITEMS = {
    1986: ("micro_IA40706926_0051", date(1986, 11, 4), (116, 117, 118, 122, 123, 124)),
    1990: ("micro_IA40706942_0388", date(1990, 11, 6), (93, 94)),
    1988: ("micro_IA40706932_0362", date(1988, 11, 8), (111, 112, 113, 114, 115)),
    1992: ("micro_IA40706946_0141", date(1992, 11, 3), (115, 118, 119, 120, 121)),
    1994: ("micro_IA40706953_0173", date(1994, 11, 8), (124, 129, 130, 139, 140)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
OFFICES = {"president": "president", "senate": "senate", "governor": "governor"}
ERRATA = {(1986, ("governor", 0)), (1990, ("governor", 0)), (1988, ("turnout", 0)), (1992, ("turnout", 0)), (1994, ("turnout", 0))}
DERIVED = {(1986, "Clermont", "Celeste"): 14246, (1994, "Guernsey", "Registered Voters"): 14166, (1992, "Henry", "Electors Voting"): 13538}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's president, U.S. Senate, and governor by county, with registered voters and electors "
            "voting, from the Secretary of State's Ohio Election Statistics",
            polity="US",
        )
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, (url, landing, day, numbers) in VOLUMES.items():
            title = TITLE.format(day=day)
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source", id=sources[year], kind="official_body", publisher=PUBLISHER, title=title, url=landing,
                license=LICENSE,
            )
            book = house_books._artifact(sys.modules[__name__], year)
            artifact = cs.artifact(book, sources[year])
            for number in numbers:
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    house_books._render(book, number, 100, image, (year, number) in TURNED)
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
        counties: dict[str, UUID] = {
            house_books._plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
        if len(counties) != 88:
            raise ValueError("Ohio needs 88 counties")
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        columns = (
            "id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness",
            "supersedes_id",
        )

        def values(result_set: UUID, year: int, rows: pl.DataFrame, option_of: dict[str, UUID | None], measures: dict[str, int]) -> None:
            for county, label, votes, number, box in rows.select("county", "label", "votes", "page", "box").iter_rows():
                plain = house_books._plain(county)
                unit = counties.get(plain) or next(u for name, u in counties.items() if name.startswith(plain))
                option = option_of.get(label)
                if label not in measures and option is None:
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measures.get(label, 1),
                    "count_round": 0,
                }
                derived = DERIVED.get((year, county, label))
                cs.add(
                    "result_value",
                    **fact,
                    flags=house_books.DIGITIZED | (house_books.DERIVED if derived is not None else 0),
                    value=votes if derived is None else derived,
                )
                page, width, height = pages[(year, number)]
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

        for (year, kind), rows in frame.group_by("year", "state", maintain_order=True):
            day = VOLUMES[int(year)][2]
            if kind == "turnout":
                key = f"{day:%Y-%m-%d}:OH"
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county:turnout")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[key],
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[int(year)],
                    is_default=True,
                    completeness=1,
                )
                values(result_set, int(year), rows, {}, {"Registered Voters": 2, "Electors Voting": 14})
                continue
            key = f"{day:%Y-%m-%d}:OH:{OFFICES[str(kind)]}"
            ((contest, election),) = conn.execute(
                """select c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            options = conn.execute(
                """select id, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            option_of: dict[str, UUID | None] = {}
            for label, cells in rows.group_by("label", maintain_order=True):
                label = str(label[0])
                first = house_books._plain(label.split("/")[0].split()[-1])
                matches = [
                    option
                    for option, text in options
                    if house_books._plain((text or "").split(" and ")[0].replace(" Jr.", "").split()[-1]) == first
                ]
                votes = int(cells["votes"].sum())
                if not matches:
                    made = cs.key("us.option", f"{key}:{label}")
                    cs.add(
                        "contest_option",
                        id=made,
                        contest_id=contest,
                        kind="candidate" if votes > 1000 else "write_in",
                        ballot_label=label,
                    )
                    option_of[label] = made
                    continue
                if len(matches) != 1:
                    raise ValueError(f"{key}: {len(matches)} options for {label}")
                option_of[label] = matches[0]
                if matches[0] in on_file and on_file[matches[0]] != votes:
                    print(f"{key}: {label} sums to {votes:,}, on file {on_file[matches[0]]:,}")
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
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
                source_id=sources[int(year)],
                is_default=True,
                completeness=1,
            )
            values(result_set, int(year), rows, option_of, {})
            present = set(option_of.values())
            for option, outcome_kind, seats in outcomes:
                if option in present:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
        return cs.write()
