import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.mi_microfiche_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Michigan Secretary of State"
TITLE = "State of Michigan General Election, {day:%B %-d, %Y}: Official Canvass"
LICENSE = "Public record"
DPI = 300
BOOKS = {1990: "micro_IA40706939_0062", 1994: "micro_IA40706952_0453"}
DAYS = {1990: date(1990, 11, 6), 1994: date(1994, 11, 8)}
OFFICES = {"secretary-of-state": "Secretary of State", "attorney-general": "Attorney General"}
PARTIES = {"D": "democratic-party", "R": "republican-party"}
URL = "https://archive.org/download/{0}/{0}.pdf"
DETAILS = "https://archive.org/details/{0}"


def fetch() -> None:
    for item in BOOKS.values():
        raw.fetch(SOURCE, URL.format(item), suffix=".pdf")


def parse() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    scan = pl.read_csv(
        Path(__file__).with_name("mi_microfiche_statewide.tsv"), separator="\t", schema_overrides={"party": pl.String}
    )
    for year, page in sorted(set(zip(scan["year"], scan["page"], strict=True))):
        book = raw.cached(SOURCE, URL.format(BOOKS[year]))
        image = PAGES / f"{book.sha256}-{page}.png"
        if not image.exists():
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
                    str(book.path),
                    str(image.with_suffix("")),
                ],
                check=True,
            )
    totals = scan.group_by("year", "office", "name").agg(pl.col("votes").sum().alias("printed"))
    frame = scan.join(totals, on=["year", "office", "name"])
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year", "office", "name").agg(pl.col("votes").sum(), pl.len()).sort("year", "office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan's 1990 and 1994 secretary of state and attorney general elections by county from the "
            "Secretary of State's official canvass",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        sources: dict[int, UUID] = {}
        artifacts: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, number in sorted(set(zip(frame["year"], frame["page"], strict=True))):
            if year not in sources:
                title = TITLE.format(day=DAYS[year])
                sources[year] = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=sources[year],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=DETAILS.format(BOOKS[year]),
                    license=LICENSE,
                )
            book = raw.cached(SOURCE, URL.format(BOOKS[year]))
            if year not in artifacts:
                artifacts[year] = cs.artifact(book, sources[year])
            image = PAGES / f"{book.sha256}-{number}.png"
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifacts[year], number)
            pages[(year, number)] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifacts[year],
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-MI'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            value: unit
            for value, unit in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '26%%'"
            ).fetchall()
        }
        if len(counties) != 83:
            raise ValueError("Michigan needs 83 counties")
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^(1990|1994)-11.*:MI:'"
            )
        }
        people: dict[str, UUID] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(plain(text), person)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        for (year, office), lines in frame.sort("year", "office").group_by("year", "office", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:MI:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            election = elections[f"{day}:MI"]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{office}:MI"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Michigan {OFFICES[str(office)]} election")
            cs.add("slug", path=f"us/mi/{day}-general/{office}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                person = people.get(plain(candidate)) or cs.key(f"{SOURCE}.person", f"MI:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people[plain(candidate)] = person
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[str(party)],
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
                totals[option] = int(cells["votes"].sum())
                for code, votes, number, x0, y0, x1, y1 in cells.select(
                    "code", "votes", "page", "x0", "y0", "x1", "y1"
                ).iter_rows():
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": counties[f"26{2 * code - 1:03d}"],
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                    page, width, height = pages[(year, number)]
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=page,
                        source_id=None,
                        x0=x0 / width,
                        y0=y0 / height,
                        x1=x1 / width,
                        y1=y1 / height,
                    )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
        return cs.write()
