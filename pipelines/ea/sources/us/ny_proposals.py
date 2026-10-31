import sys
from datetime import date
from pathlib import Path

import polars as pl
from PIL import Image

from ea.changeset import Changeset, slug
from ea.sources.us import house_books, ny_statewide
from ea.store import DATA, connect

SOURCE = "us.ny_proposals"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = ny_statewide.PUBLISHER
TITLE = "Vote for Proposal One, New York State, {day:%Y}"
LICENSE = "Public record"
STATE = "NY"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ITEMS = {
    1986: (ny_statewide.ITEMS[1986][0], date(1986, 11, 4), range(6, 7)),
    1988: ("micro_IA40706932_0313", date(1988, 11, 8), range(8, 9)),
    1990: (ny_statewide.ITEMS[1990][0], date(1990, 11, 6), range(3, 4)),
    1992: (ny_statewide.ITEMS[1992][0], date(1992, 11, 3), range(7, 8)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
PROPOSALS = {
    1986: "Environmental Quality Bond Act of 1986",
    1988: "Roads and Bridges Bond Act",
    1990: "Twenty-First Century Environmental Quality Bond Act",
    1992: "Jobs for the New, New York Bond Act",
}
BLANK = (
    "The Board's returns also print blank and void ballots, which are not stored here; the Total is the whole number "
    "of votes cast on the proposal and is stored as ballots cast."
)
ERRATA: set[tuple[int, object]] = set()
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
            summary="New York's statewide bond act proposals of 1986, 1988, 1990, and 1992 by county, from the State "
            "Board of Elections' vote books",
            polity="US",
        )
        plain = house_books._plain
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        system = cs.key("us.electoral_system", "referendum")
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, (url, landing, day, numbers) in VOLUMES.items():
            title = TITLE.format(day=day)
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=landing,
                license=LICENSE,
            )
            book = house_books._artifact(sys.modules[__name__], year)
            artifact = cs.artifact(book, source)
            (number,) = numbers
            image = PAGES / f"{book.sha256}-{number}.png"
            if not image.exists():
                house_books._render(book, number, 100, image, False)
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            ((election, area),) = conn.execute(
                """select c.election_id, c.district_area_id from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value in (%s, %s) limit 1""",
                [f"{day}:NY:governor", f"{day}:NY:senate"],
            ).fetchall()
            ((election_path,),) = conn.execute(
                "select path from slug where entity_id = %s and is_current", [election]
            ).fetchall()
            rows = frame.filter(pl.col("year") == year)
            key = f"{day}:NY:proposal:1"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name = f"{year} New York Proposal One, {PROPOSALS[year]}"
            cs.add("name", entity_id=contest, text=name, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"{election_path}/proposal-one-{slug(PROPOSALS[year])}", entity_id=contest)
            options = {}
            for position, answer in enumerate(("Yes", "No")):
                options[answer] = cs.key("us.option", f"{key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=BLANK)
            yes = int(rows.filter(pl.col("label") == "Yes")["votes"].sum())
            no = int(rows.filter(pl.col("label") == "No")["votes"].sum())
            cs.add("outcome", result_set_id=result_set, option_id=options["Yes" if yes > no else "No"], kind="won")
            for county, label, votes, box in rows.select("county", "label", "votes", "box").iter_rows():
                if label not in ("Yes", "No", "Total"):
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[plain(county)],
                    "option_id": None if label == "Total" else options[label],
                    "channel": 0,
                    "measure": 3 if label == "Total" else 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
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
        return cs.write()
