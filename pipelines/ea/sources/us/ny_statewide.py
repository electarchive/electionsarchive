import re
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea.changeset import Changeset, slug
from ea.sources.us import house_books, ny_returns
from ea.store import DATA, connect

SOURCE = "us.ny_statewide"
RAW = "us.sri"
VERSION = "1"
PUBLISHER = "New York State Board of Elections"
TITLE = "General Election Vote, New York State, {day:%Y}"
LICENSE = "Public record"
STATE = "NY"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLES = {**ny_returns.TITLES, "governor": "Governor", "senate": "U.S. Senate", "president": "President"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "CON": "NY:conservative-party",
    "RTL": "NY:right-to-life",
    "LIB": "NY:liberal",
    "NAL": "NY:new-alliance",
    "WOR": "workers-world-party",
    "SWP": "socialist-workers-party",
    "LBT": "libertarian-party",
    "NLP": "natural-law-party",
    "IND": None,
    "WLP": None,
    "SOC": None,
    "IPR": None,
}
LINES = {
    "DEM": "Democratic",
    "REP": "Republican",
    "CON": "Conservative",
    "RTL": "Right to Life",
    "LIB": "Liberal",
    "NAL": "New Alliance",
    "WOR": "Workers World",
    "SWP": "Socialist Workers",
    "LBT": "Libertarian",
    "NLP": "Natural Law",
    "IND": "Independent",
    "WLP": "Workers League",
    "SOC": "Socialist",
    "IPR": "Independent Progressive",
}
NAMES = {
    "Fulani": "Lenora B. Fulani",
    "London": "Herbert London",
    "Bellamy": "Carol Bellamy",
    "Regan": "Edward V. Regan",
    "Badillo": "Herman Badillo",
    "King": "Peter T. King",
    "Abrams": "Robert Abrams",
    "Dillon": "Denis Dillon",
    "Kenyon": "Willa Kenoyer",
    "Mehdi": "Mohammad Mehdi",
}
NOT_VOTES = {"Total", "BlankVoidScattering", "Blank", "Void", "Scattering", "BlankScattering", "BlankVoid"}
ITEMS = {
    1986: ("micro_IA40706926_0008", date(1986, 11, 4), range(2, 6)),
    1990: ("micro_IA40706939_0195", date(1990, 11, 6), range(4, 10)),
    1992: ("micro_IA40706946_0089", date(1992, 11, 3), range(3, 7)),
}
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
}
ERRATA = {(1990, ("attorney-general", 0))}
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
            summary="New York's president, U.S. Senate, governor, comptroller, and attorney general by county and "
            "ballot line, from the State Board of Elections' general election vote books",
            polity="US",
        )
        plain = house_books._plain
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
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items() if key is not None}
        fptp = cs.key("us.electoral_system", "fptp")
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the parties and the fptp system must already exist")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':NY:'")
        )
        columns = (
            "id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness",
            "supersedes_id",
        )
        chosen: dict[str, UUID] = {}
        for (year, office), rows in frame.group_by("year", "state", maintain_order=True):
            year = int(year)
            day = VOLUMES[year][2]
            key = f"{day}:NY:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if contest is None:
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s",
                    [contests[f"{day}:NY:governor"]],
                ).fetchall()
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{office}:NY"),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add("name", entity_id=contest, text=f"{year} New York {TITLES[office]} election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"us/ny/{day}-general/{office}", entity_id=contest)
            else:
                ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            roots: list[tuple[UUID, str]] = []
            children: dict[tuple[UUID, UUID | None], UUID] = {}
            for option, kind, label, parent, party in conn.execute(
                "select id, kind, ballot_label, parent_option_id, party_id from contest_option where contest_id = %s",
                [contest],
            ).fetchall():
                if parent is None and kind in ("candidate", "ticket", "write_in"):
                    roots.append((option, label or ""))
                elif parent is not None:
                    children.setdefault((parent, party), option)
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
            cs.add("note", entity_id=result_set, kind="caveat", body_md=ny_returns.BLANK)
            heads: dict[str, list[str]] = {}
            for label in rows["label"].unique(maintain_order=True):
                if label not in NOT_VOTES:
                    head, code = label.split("/")
                    heads.setdefault(head, []).append(code)
            targets: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for head, codes in heads.items():
                found = [option for option, text in roots if plain(text).endswith(plain(head))]
                if len(found) != 1:
                    found = [option for option, text in roots if plain(head) in plain(text)] if not found else found
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {head}")
                if found:
                    root = found[0]
                    text = next(text for option, text in roots if option == root)
                else:
                    text = NAMES.get(head, head)
                    people = [] if head not in NAMES else conn.execute(
                        """select distinct cd.person_id from candidacy cd
                           join name n on n.entity_id = cd.person_id and n.is_preferred
                           join contest_option o on o.id = cd.option_id
                           join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                                and (x.value ~ ':NY:' or x.value ~ ':president$')
                           where regexp_replace(lower(n.text), '[^a-z]', '', 'g') = %s""",
                        [plain(text)],
                    ).fetchall()
                    person = chosen.get(plain(text)) or (
                        people[0][0] if len(people) == 1 else cs.key(f"{SOURCE}.person", f"NY:{slug(text)}")
                    )
                    if person not in chosen.values():
                        if person in cs.minted:
                            cs.add("person", id=person)
                            cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                        else:
                            print(f"{key}: {text} is joined to an existing person")
                    chosen[plain(text)] = person
                    root = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties.get(codes[0]) if len(codes) == 1 else None,
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
                text = " ".join(text.split())
                for code in codes:
                    label = f"{head}/{code}"
                    if len(codes) == 1:
                        targets[label] = root
                        continue
                    party = parties.get(code)
                    child = children.get((root, party)) if party is not None else None
                    if child is None:
                        child = cs.key("us.option", f"{key}:{root}:{code}")
                        cs.add(
                            "contest_option",
                            id=child,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=party,
                            ballot_label=f"{text} ({LINES[code]})",
                        )
                        children[(root, party)] = child
                    targets[label] = child
                totals[root] = sum(
                    int(rows.filter(pl.col("label") == f"{head}/{code}")["votes"].sum()) for code in codes
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            for county, label, votes, number, box in rows.select("county", "label", "votes", "page", "box").iter_rows():
                if label in NOT_VOTES - {"Total"}:
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
