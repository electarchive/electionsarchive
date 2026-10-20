import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ga_statewide"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "Georgia Secretary of State"
TITLE = "State of Georgia General Election: Nov. 8, 1994, Votes by County for Candidates, statewide offices"
LICENSE = "Public record"
STATE = "GA"
FIPS = "13"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ITEM = "micro_IA40706952_0290"
VOLUMES = {
    1994: (f"https://archive.org/download/{ITEM}/{ITEM}.pdf", f"https://archive.org/details/{ITEM}", date(1994, 11, 8), range(2, 62)),
}
OFFICES = {
    "governor": ("governor", None, "Governor"),
    "lieutenant-governor": ("lieutenant-governor", None, "Lieutenant Governor"),
    "secretary-of-state": ("secretary-of-state", None, "Secretary of State"),
    "attorney-general": ("attorney-general", None, "Attorney General"),
    "commissioner-of-agriculture": ("commissioner-of-agriculture", None, "Commissioner of Agriculture"),
    "commissioner-of-insurance": ("commissioner-of-insurance", None, "Commissioner of Insurance"),
    "school-superintendent": ("school-superintendent", None, "State School Superintendent"),
    "commissioner-of-labor": ("commissioner-of-labor", None, "Commissioner of Labor"),
    "public-service-pafford": ("public-service-commissioner", "pafford", "Public Service Commissioner (Pafford's seat)"),
    "public-service-rowan": ("public-service-commissioner", "rowan", "Public Service Commissioner (Rowan's seat)"),
}
NAMES: dict[tuple[str, str], str] = {}
FULL = {"THOMAS": "Hoke S. Thomas"}
ERRATA: set[tuple[int, int]] = set()
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
COMMAS = True
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 170, 190, 210, 230)
CAVEAT = (
    "The report prints each candidate's name without a party. Options that no other source holds carry no party. "
    "Write-in totals are not printed."
)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(pl.col("county") != "Totals")
    day = VOLUMES[1994][2]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 1994 lieutenant governor, secretary of state, attorney general, "
            "commissioners of agriculture, insurance, and labor, school superintendent, and two public service "
            "commissioner seats by county from the Secretary of State's official election returns",
            polity="US",
        )
        _, landing, _, numbers = VOLUMES[1994]
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=landing, license=LICENSE)
        book = house_books._artifact(sys.modules[__name__], 1994)
        artifact = cs.artifact(book, source)
        pages: dict[int, tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for number in numbers:
            image = PAGES / f"{book.sha256}-{number}.png"
            if not image.exists():
                house_books._render(book, number, 100, image, False)
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number)
            pages[number] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        plain = house_books._plain
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
        if len(counties) != 159:
            raise ValueError("Georgia needs 159 counties")
        fptp = cs.key("us.electoral_system", "fptp")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':GA:'")
        )
        ((election, area),) = conn.execute(
            "select election_id, district_area_id from contest where id = %s", [contests[f"{day}:GA:governor"]]
        ).fetchall()
        for office, rows in frame.group_by("state", maintain_order=True):
            office = str(office[0])
            if office != "governor":
                continue
            base, seat, label = OFFICES[office]
            key = f"{day}:GA:{base}" + ("" if seat is None else f":{seat}")
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if contest is None:
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{base}:GA"),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add("name", entity_id=contest, text=f"1994 Georgia {label} election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"us/ga/{day}-general/{base}" + ("" if seat is None else f"-{seat}"), entity_id=contest)
            roots = [
                (option, text or "")
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null "
                    "and kind in ('candidate', 'ticket', 'write_in')",
                    [contest],
                ).fetchall()
            ]
            for row in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall():
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
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
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT)
            targets: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for name in rows["label"].unique(maintain_order=True):
                text = FULL.get(name) or " ".join(name.title().split())
                surname = plain(text.split()[-1])
                found = [option for option, label_ in roots if surname in plain(label_)]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {name}")
                if found:
                    root = found[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"GA:{slug(text)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                    root = cs.key("us.option", f"{key}:{person}")
                    cs.add("contest_option", id=root, contest_id=contest, kind="candidate", ballot_label=text)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=text,
                    )
                    roots.append((root, text))
                targets[name] = root
                totals[root] = int(rows.filter(pl.col("label") == name)["votes"].sum())
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                cs.add("outcome", result_set_id=result_set, option_id=max(totals, key=totals.__getitem__), kind="elected", seats=1)
            for county, name, votes, number, box in rows.select("county", "label", "votes", "page", "box").iter_rows():
                unit = counties.get(plain(county)) or next(u for text, u in counties.items() if text.startswith(plain(county)))
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": targets[name],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
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
        return cs.write()
