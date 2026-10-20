import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.sc_report"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "South Carolina State Election Commission"
TITLE = "{year} Election Report"
LANDING = "https://scvotes.gov/election-results/"
LICENSE = "Public record"
DPI = 200
DAY = date(2002, 11, 5)
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_2002.pdf"
COUNTIES = [
    "Abbeville", "Aiken", "Allendale", "Anderson", "Bamberg", "Barnwell", "Beaufort", "Berkeley", "Calhoun",
    "Charleston", "Cherokee", "Chester", "Chesterfield", "Clarendon", "Colleton", "Darlington", "Dillon",
    "Dorchester", "Edgefield", "Fairfield", "Florence", "Georgetown", "Greenville", "Greenwood", "Hampton", "Horry",
    "Jasper", "Kershaw", "Lancaster", "Laurens", "Lee", "Lexington", "McCormick", "Marion", "Marlboro", "Newberry",
    "Oconee", "Orangeburg", "Pickens", "Richland", "Saluda", "Spartanburg", "Sumter", "Union", "Williamsburg", "York",
]
OFFICES = {
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
    "attorney-general": "Attorney General",
    "comptroller-general": "Comptroller General",
    "superintendent-of-education": "State Superintendent of Education",
    "commissioner-of-agriculture": "Commissioner of Agriculture",
}
PARTIES = {"D": "democratic-party", "R": "republican-party"}
TABLES = [
    (41, "lieutenant-governor", [(3, "Phil Leventis", "D"), (4, "André Bauer", "R"), (5, "Kenneth E. Curtis", "L")]),
    (42, "secretary-of-state", [(0, "Rick C. Wade", "D"), (1, "Mark Hammond", "R")]),
    (42, "treasurer", [(3, "Grady L. Patterson Jr.", "D"), (4, "Greg Ryberg", "R")]),
    (43, "attorney-general", [(0, "Steve Benjamin", "D"), (1, "Henry McMaster", "R")]),
    (43, "comptroller-general", [(3, "Jim Lander", "D"), (4, "Richard A. Eckstrom", "R")]),
    (
        44,
        "superintendent-of-education",
        [(0, "Inez Tenenbaum", "D"), (1, "Al Hafer", "C"), (2, "Dan Hiltgen", "R"), (3, "Marva Y. Manigault", "")],
    ),
    (45, "commissioner-of-agriculture", [(0, "John Long", "D"), (1, "Charles Sharpe", "R")]),
]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    path = raw.cached(SOURCE, URL).path
    PAGES.mkdir(parents=True, exist_ok=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for page in sorted({t[0] for t in TABLES}):
        image = PAGES / f"{raw.cached(SOURCE, URL).sha256}-{page}.png"
        if not image.exists():
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(path), str(image.with_suffix(""))],
                check=True,
            )
        xml = subprocess.run(
            ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        namespace = {"h": "http://www.w3.org/1999/xhtml"}
        words = [
            (float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")), w.text or "")
            for w in ElementTree.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xml)).findall(".//h:word", namespace)
        ]
        words.sort(key=lambda w: (w[1] + w[3]) / 2)
        lines: list[list[tuple[float, float, float, float, str]]] = []
        for word in words:
            if lines and abs((word[1] + word[3]) / 2 - sum((w[1] + w[3]) / 2 for w in lines[-1]) / len(lines[-1])) <= 3.5:
                lines[-1].append(word)
            else:
                lines.append([word])
        lines = [sorted(line) for line in lines]
        total = next(i for i, line in enumerate(lines) if any(w[4] == "TOTAL" for w in line))
        edges = [w[2] for w in lines[total] if w[4] not in ("STATE", "TOTAL")]
        printed = [int(re.sub(r"\D", "", w[4])) for w in lines[total] if w[4] not in ("STATE", "TOTAL")]
        cells: list[dict[int, tuple[int, list[float]]]] = []
        for line in lines[total - 46 : total]:
            merged: list[list[object]] = []
            for word in line:
                if word[0] < 130 and not re.search(r"\d", word[4]):
                    continue
                if merged and word[4][:1] in ",." and word[0] - float(merged[-1][2]) < 6:
                    merged[-1][4] = str(merged[-1][4]) + word[4]
                    merged[-1][2] = word[2]
                else:
                    merged.append(list(word))
            row: dict[int, tuple[int, list[float]]] = {}
            for x0, y0, x1, y1, text in merged:
                column = min(range(len(edges)), key=lambda i: abs(edges[i] - float(x1)))
                if abs(edges[column] - float(x1)) > 14 or not re.search(r"\d", str(text)):
                    continue
                row[column] = (int(re.sub(r"\D", "", str(text))), [float(x0), float(y0), float(x1), float(y1)])
            cells.append(row)
        for table_page, office, candidates in TABLES:
            if table_page != page:
                continue
            for column, name, party in candidates:
                summed = sum(row[column][0] for row in cells if column in row)
                if summed != printed[column]:
                    raise ValueError(f"{office} {name}: counties sum to {summed}, printed {printed[column]}")
                for county, row in zip(COUNTIES, cells, strict=True):
                    if column not in row:
                        continue
                    votes, box = row[column]
                    rows.append(
                        {
                            "office": office,
                            "name": name,
                            "party": party,
                            "county": county,
                            "votes": votes,
                            "printed": printed[column],
                            "page": page,
                            "box": [round(v * DPI / 72) for v in box],
                        }
                    )
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("office", "name").agg(pl.col("votes").sum(), pl.len()).sort("office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's lieutenant governor, secretary of state, treasurer, attorney general, comptroller general, "
            "superintendent of education, and commissioner of agriculture elections of 2002 by county from the "
            "State Election Commission's election report",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.replace(",", " ").split() if plain(word) not in {"jr", "sr", "ii", "iii"}]
            return plain(words[-1])

        title = TITLE.format(year=DAY.year)
        source = cs.source(PUBLISHER, title)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=LANDING, license=LICENSE)
        book = raw.cached(SOURCE, URL)
        artifact = cs.artifact(book, source)
        pages: dict[int, tuple[UUID, int, int]] = {}
        for number in sorted(set(frame["page"])):
            image = PAGES / f"{book.sha256}-{number}.png"
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
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-SC'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
        if len(counties) != 46:
            raise ValueError("South Carolina needs 46 counties")
        elections = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall())
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^2002-11.*:SC:'"
            )
        }
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        for (office,), lines in frame.sort("office").group_by("office", maintain_order=True):
            day = DAY
            key = f"{day}:SC:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            election = elections[f"{day}:SC"]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{office}:SC"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} South Carolina {OFFICES[str(office)]} election")
            cs.add("slug", path=f"us/sc/{day}-general/{office}", entity_id=contest)
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
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = people.get(probe, set())
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"SC:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people.setdefault(probe, set()).add(person)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(str(party)),
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=party not in ("R", "D"),
                )
                totals[option] = int(cells["votes"].sum())
                for county, votes, number, box in cells.select("county", "votes", "page", "box").iter_rows():
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": counties[plain(county)],
                        "option_id": option,
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
                        x0=box[0] / width,
                        y0=box[1] / height,
                        x1=box[2] / width,
                        y1=box[3] / height,
                    )
            cs.add("outcome", result_set_id=result_set, option_id=max(totals, key=totals.__getitem__), kind="elected", seats=1)
        return cs.write()
