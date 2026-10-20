import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ca_statewide"
RAW = "us.ca_sov"
VERSION = "1"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
STATE = "CA"
FIPS = "06"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLES = {
    "governor": "Governor",
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "controller": "Controller",
    "treasurer": "Treasurer",
    "attorney-general": "Attorney General",
    "surveyor-general": "Surveyor General",
    "clerk-of-the-supreme-court": "Clerk of the Supreme Court",
    "superintendent-of-public-instruction": "Superintendent of Public Instruction",
    "supreme-court-justice": "Associate Justice of the Supreme Court",
}
NOT_VOTES = {"Total", "Blank", "Void", "Scattering"}
ITEMS = {
    1882: (date(1882, 11, 7), (3, 4, 6, 7, 8, 9, 10, 11, 12)),
    1886: (date(1886, 11, 2), range(11, 20)),
}
VOLUMES = {
    year: (f"https://v3.californiatargetbook.com/docs/SoV/g{year}.pdf", "https://www.californiatargetbook.com/", day, pages)
    for year, (day, pages) in ITEMS.items()
}
PARTIES = {
    (1882, "governor", "M. M. Estee"): "republican-party",
    (1882, "governor", "Geo. Stoneman"): "democratic-party",
    (1882, "lieutenant-governor", "John Daggett"): "republican-party",
    (1882, "lieutenant-governor", "A. R. Conklin"): "democratic-party",
    (1882, "secretary-of-state", "Thos. L. Thompson"): "democratic-party",
    (1882, "controller", "John P. Dunn"): "democratic-party",
    (1882, "attorney-general", "A. L. Hart"): "democratic-party",
    (1886, "governor", "John F. Swift"): "republican-party",
    (1886, "governor", "Washington Bartlett"): "democratic-party",
    (1886, "governor", "Joel Russell"): "prohibition-party",
    (1886, "lieutenant-governor", "R. H. Waterman"): "republican-party",
    (1886, "lieutenant-governor", "Michael F. Tarpey"): "democratic-party",
    (1886, "lieutenant-governor", "A. D. Boren"): "prohibition-party",
    (1886, "secretary-of-state", "Walter S. Moore"): "republican-party",
    (1886, "secretary-of-state", "William C. Hendricks"): "democratic-party",
    (1886, "secretary-of-state", "Frank E. Kellogg"): "prohibition-party",
    (1886, "controller", "J. E. Denny"): "republican-party",
    (1886, "controller", "John P. Dunn"): "democratic-party",
    (1886, "controller", "J. A. Fairbanks"): "prohibition-party",
    (1886, "treasurer", "J. H. Neff"): "republican-party",
    (1886, "treasurer", "Adam Herold"): "democratic-party",
    (1886, "treasurer", "H. S. Graves"): "prohibition-party",
    (1886, "attorney-general", "W. H. H. Hart"): "republican-party",
    (1886, "attorney-general", "George A. Johnson"): "democratic-party",
    (1886, "attorney-general", "George Babcock"): "prohibition-party",
    (1886, "surveyor-general", "Theodore Reichert"): "republican-party",
    (1886, "surveyor-general", "George B. Tolman"): "prohibition-party",
    (1886, "clerk-of-the-supreme-court", "Julius Lyons"): "prohibition-party",
    (1886, "superintendent-of-public-instruction", "D. A. Mobley"): "prohibition-party",
}
NAMES: dict[str, str] = {}
ERRATA = {
    (1882, ("secretary-of-state", 0)),
    (1882, ("treasurer", 0)),
    (1882, ("surveyor-general", 0)),
    (1882, ("clerk-of-the-supreme-court", 0)),
    (1882, ("superintendent-of-public-instruction", 0)),
    (1886, ("governor", 0)),
    (1886, ("lieutenant-governor", 0)),
    (1886, ("secretary-of-state", 0)),
    (1886, ("controller", 0)),
    (1886, ("attorney-general", 0)),
    (1886, ("surveyor-general", 0)),
    (1886, ("clerk-of-the-supreme-court", 0)),
    (1886, ("superintendent-of-public-instruction", 0)),
}
PUBLISHED = {1882, 1886}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
COMMAS = True
TURNED: set[tuple[int, int]] = set()
TRANSPOSED = set(ITEMS)
THRESHOLDS = (150, 170, 190, 210, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def parse() -> None:
    house_books.parse(sys.modules[__name__])


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(
        ~pl.col("year").map_elements(
            lambda year: year in PUBLISHED, return_dtype=pl.Boolean
        )
    )
    plain = house_books._plain
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's statewide offices by county from the Secretary of State's Statements of Vote",
            polity="US",
        )
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        PAGES.mkdir(parents=True, exist_ok=True)
        for year, (url, landing, day, numbers) in VOLUMES.items():
            if year not in set(frame["year"]):
                continue
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
                    house_books._render(book, number, 100, image, False)
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
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        fptp = cs.key("us.electoral_system", "fptp")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':CA:'")
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':CA$'")
        )
        offices_known = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = 'us.office' and value ~ ':CA$'")
        }
        columns = (
            "id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness",
            "supersedes_id",
        )
        for (year, office), rows in frame.group_by("year", "state", maintain_order=True):
            year = int(year)
            day = VOLUMES[year][2]
            key = f"{day}:CA:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            office_key = f"{office}:CA"
            if f"{day}:CA" not in elections:
                raise ValueError(f"{key}: no election")
            election = elections[f"{day}:CA"]
            if contest is None:
                contest = cs.key("us.contest", key)
                if office_key not in offices_known:
                    cs.add("office", id=cs.key("us.office", office_key), polity_id=cs.polity("US-CA"), kind="other")
                    cs.add(
                        "name", entity_id=cs.key("us.office", office_key), text=TITLES[office], lang="en",
                        kind="official", is_preferred=True,
                    )
                    offices_known.add(office_key)
                ((area,),) = conn.execute(
                    """select district_area_id from contest where id = %s""", [contests[f"{day}:CA:house:1"]]
                ).fetchall() if f"{day}:CA:house:1" in contests else conn.execute(
                    "select area_id from polity where iso_code = 'US-CA'"
                ).fetchall()
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", office_key),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add(
                    "name", entity_id=contest, text=f"{year} California {TITLES[office]} election", lang="en",
                    kind="official", is_preferred=True,
                )
                cs.add("slug", path=f"us/ca/{day}-general/{office}", entity_id=contest)
            roots: list[tuple[UUID, str]] = []
            for option, kind, label, parent in conn.execute(
                "select id, kind, ballot_label, parent_option_id from contest_option where contest_id = %s", [contest]
            ).fetchall():
                if parent is None and kind in ("candidate", "ticket", "write_in"):
                    roots.append((option, label or ""))
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
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            targets: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for label in rows["label"].unique(maintain_order=True):
                if label in NOT_VOTES - {"Scattering"} or label == "Total":
                    continue
                text = NAMES.get(label, label)
                if label == "Scattering":
                    root = cs.key("us.option", f"{key}:scattering")
                    cs.add("contest_option", id=root, contest_id=contest, kind="others", ballot_label="Scattering")
                    targets[label] = root
                    continue
                found = [option for option, name in roots if plain(name) == plain(text)]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                if found:
                    root = found[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"CA:{slug(text)}")
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                    root = cs.key("us.option", f"{key}:{person}")
                    party = PARTIES.get((year, office, label))
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None if party is None else cs.key("us.party", party),
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=text,
                    )
                    roots.append((root, text))
                targets[label] = root
                totals[root] = int(rows.filter(pl.col("label") == label)["votes"].sum())
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            for county, label, votes, number, box in rows.select("county", "label", "votes", "page", "box").iter_rows():
                if label in NOT_VOTES - {"Total", "Scattering"}:
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[plain(county)],
                    "option_id": None if label == "Total" else targets[label],
                    "channel": 0,
                    "measure": 3 if label == "Total" else 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
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
        return cs.write()
