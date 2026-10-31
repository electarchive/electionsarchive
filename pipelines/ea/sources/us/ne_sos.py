import csv
import io
import itertools
import json
import re
import statistics
import subprocess
import tempfile
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import numpy
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ne_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Nebraska Secretary of State"
TITLE = "{year} General Election Canvass"
LANDING = "https://sos.nebraska.gov/elections/previous-elections"
LICENSE = "Public record"
DPI = 200
BOOKS = {
    2006: (date(2006, 11, 7), "https://sos.nebraska.gov/sites/default/files/doc/2006%20General.pdf"),
    2002: (date(2002, 11, 5), "https://sos.nebraska.gov/sites/default/files/doc/2002%20General.pdf"),
}
OFFICES = {
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
    "auditor": "Auditor of Public Accounts",
    "attorney-general": "Attorney General",
}
PARTIES = {
    "Republican": "republican-party",
    "Democrat": "democratic-party",
    "Green": "green-party",
    "Libertarian": "libertarian-party",
    "Nebraska": "NE:nebraska-party",
}
BLOCKS = {2006: [(0, 47), (47, 94)], 2002: [(0, 51), (51, 94)]}
SIDES: dict[int, list[bool | None]] = {2006: [True, False], 2002: [None, None]}


def fetch() -> None:
    for _, url in BOOKS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    PAGES.mkdir(parents=True, exist_ok=True)
    for path in sorted((PARSED / "model").glob("*.json")):
        year = int(path.stem)
        book = raw.cached(SOURCE, BOOKS[year][1])
        for office, table in json.loads(path.read_text())[str(year)].items():
            keys = list(table["rows"])
            columns = len(table["candidates"])
            layout = [
                (page, side, block)
                for page, side, block in zip(
                    table["pages"] * len(BLOCKS[year]) if len(table["pages"]) == 1 else table["pages"],
                    SIDES[year],
                    [keys[start:stop] for start, stop in BLOCKS[year]],
                    strict=True,
                )
            ]
            boxes: dict[tuple[str, int], tuple[int, int, int, int]] = {}
            at_page: dict[str, int] = {}
            matched = 0
            for page, side, block_keys in layout:
                image = PAGES / f"{book.sha256}-{page}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                        + [str(book.path), str(image.with_suffix(""))],
                        check=True,
                    )
                    with Image.open(image) as rendered:
                        small = rendered.convert("L").resize((rendered.width // 4, rendered.height // 4))
                        ink = Image.fromarray((numpy.asarray(small) < 128).astype("uint8") * 255)
                        angle = max(
                            numpy.arange(-4, 4.01, 0.1),
                            key=lambda a: numpy.var(numpy.asarray(ink.rotate(a)).sum(axis=1)),
                        )
                        rendered.convert("L").rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255).save(
                            image
                        )
                with Image.open(image) as rendered:
                    clean = Path(tempfile.mkdtemp()) / "clean.png"
                    rendered.convert("L").point(lambda v: 0 if v < 110 else 255).save(clean)
                tsv = subprocess.run(
                    ["tesseract", str(clean), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
                ).stdout
                tokens = [
                    (
                        int(r["left"]),
                        int(r["top"]),
                        int(r["left"]) + int(r["width"]),
                        int(r["top"]) + int(r["height"]),
                        r["text"],
                    )
                    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
                    if r["level"] == "5"
                    and r["text"].strip().isdigit()
                    and int(r["top"]) > 340
                    and int(r["left"]) < 1580
                ]
                with Image.open(image) as rendered:
                    middle = rendered.size[0] / 2
                block = [t for t in tokens if side is None or (((t[0] + t[2]) / 2 < middle) == side)]
                char = statistics.median((t[2] - t[0]) / len(t[4]) for t in block)
                found: dict[tuple[int, int], tuple[int, int, int, int]] = {}
                edges: list[float] = []
                for column in range(columns):
                    expected = [str(table["rows"][k][column]) for k in block_keys]
                    rights = [t[2] for t in block if t[4] in expected and len(t[4]) > 1]
                    common = statistics.mode(round(r / 8) for r in rights)
                    edge = statistics.median(r for r in rights if round(r / 8) == common)
                    edges.append(edge)
                    mine = sorted((t for t in block if abs(t[2] - edge) < 14), key=lambda t: t[1])
                    match = SequenceMatcher(None, expected, [t[4] for t in mine], autojunk=False)
                    for a, b, size in match.get_matching_blocks():
                        for offset in range(size):
                            found[(a + offset, column)] = mine[b + offset][:4]
                pairs = [(i, (box[1] + box[3]) / 2) for (i, _), box in found.items() if block_keys[i] != "TOTAL"]
                if len(found) > 0.6 * columns * (len(block_keys) - 1):
                    pitch = statistics.median(
                        (yj - yi) / (j - i)
                        for n, (i, yi) in enumerate(pairs[::3])
                        for j, yj in pairs[::3][n + 1 :]
                        if j != i
                    )
                    top = statistics.median(y - pitch * i for i, y in pairs)
                else:
                    with Image.open(image) as rendered:
                        ink = numpy.asarray(rendered.convert("L")) < 140
                    span = ink[:, round(min(edges) - 300) : round(max(edges))]
                    dark = span.mean(axis=1) > 0.5
                    rules = [y for y in range(1, len(dark)) if dark[y] and not dark[y - 1]]
                    if len(rules) < 5:
                        raise ValueError(f"{year} {office} page {page}: {len(rules)} rules")
                    pitch = statistics.median(b - a for a, b in itertools.pairwise(rules)) / 2
                    guess = statistics.median(y - i * pitch for i, y in pairs)
                    near = min(range(-3, 4), key=lambda k: abs(rules[0] - pitch / 2 + k * pitch - guess))
                    top = rules[0] - pitch / 2 + near * pitch
                found = {
                    cell: box
                    for cell, box in found.items()
                    if block_keys[cell[0]] != "TOTAL"
                    and abs((box[1] + box[3]) / 2 - (top + cell[0] * pitch)) < pitch / 3
                }
                for column in range(columns):
                    for i, key in enumerate(block_keys):
                        if key == "TOTAL":
                            continue
                        box = found.get((i, column))
                        if box is None:
                            y = top + i * pitch
                            width = char * len(str(table["rows"][key][column]))
                            box = (
                                round(edges[column] - width),
                                round(y - pitch * 0.3),
                                round(edges[column]),
                                round(y + pitch * 0.3),
                            )
                        else:
                            matched += 1
                        boxes[(key, column)] = box
                        at_page[key] = page
            print(year, office, f"{matched}/{len(keys) * columns} cells matched to OCR tokens")
            printed = table["rows"]["TOTAL"]
            for column, candidate in enumerate(table["candidates"]):
                summed = sum(v[column] for k, v in table["rows"].items() if k != "TOTAL")
                if summed != printed[column]:
                    raise ValueError(
                        f"{year} {office} {candidate['name']}: counties sum to {summed}, printed {printed[column]}"
                    )
                for key in keys[1:]:
                    rows.append(
                        {
                            "year": year,
                            "office": office,
                            "name": candidate["name"],
                            "party": candidate["party"],
                            "county": key,
                            "votes": table["rows"][key][column],
                            "printed": printed[column],
                            "page": at_page[key],
                            "box": list(boxes[(key, column)]),
                        }
                    )
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year", "office", "name").agg(pl.col("votes").sum()).sort("year", "office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's secretary of state, treasurer, auditor of public accounts, and attorney general "
            "elections by county from the Secretary of State's general election canvass books",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.replace(",", " ").split() if plain(word) not in {"jr", "sr", "ii", "iii"}]
            return plain(words[-1])

        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year in sorted(set(frame["year"])):
            day, url = BOOKS[year]
            title = TITLE.format(year=year)
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=LANDING,
                license=LICENSE,
            )
            book = raw.cached(SOURCE, url)
            artifact = cs.artifact(book, sources[year])
            for number in sorted(set(frame.filter(pl.col("year") == year)["page"])):
                image = PAGES / f"{book.sha256}-{number}.png"
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
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-NE'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
        if len(counties) != 93:
            raise ValueError("Nebraska needs 93 counties")
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^(2002|2006)-11.*:NE:'"
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
        for (year, office), lines in frame.sort("year", "office").group_by("year", "office", maintain_order=True):
            day = BOOKS[int(year)][0]
            key = f"{day}:NE:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            election = elections[f"{day}:NE"]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{office}:NE"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Nebraska {OFFICES[str(office)]} election")
            cs.add("slug", path=f"us/ne/{day}-general/{office}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = people.get(probe, set())
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"NE:{slug(candidate)}")
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
                    is_independent=party not in ("Republican", "Democrat"),
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
                    page, width, height = pages[(int(year), number)]
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
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
        return cs.write()
