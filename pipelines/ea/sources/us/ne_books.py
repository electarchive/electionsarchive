import csv
import io
import json
import re
import statistics
import subprocess
import tempfile
import zipfile
from datetime import UTC, date, datetime
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

SOURCE = "us.ne_books"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Nebraska Secretary of State"
TITLE = "{year} General Election Official Report of the Board of State Canvassers"
LANDING = "https://sos.nebraska.gov/elections/previous-elections"
ZIP = "https://sos.nebraska.gov/sites/default/files/doc/zip2.zip"
LICENSE = "Public record"
DPI = 200
OFFICES = {
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
    "auditor": "Auditor of Public Accounts",
    "attorney-general": "Attorney General",
}
PARTIES = {"Republican": "republican-party", "Democrat": "democratic-party"}
EXCEPTIONS = {
    (1998, "secretary-of-state"): "the printed TOTAL row leaves out Howard County's row",
    (1998, "attorney-general", "Don Stenberg"): "the printed TOTAL is 2 votes above the sum of the printed county rows",
}
BOOKS = {
    1998: (date(1998, 11, 3), "1990s Books/1998 General.pdf"),
    1994: (date(1994, 11, 8), "1990s Books/1994 General.pdf"),
    1990: (date(1990, 11, 6), "1990s Books/1990 General.pdf"),
}
SPLIT = {1998: True}
LAYOUT = {
    1998: {
        "secretary-of-state": (4, None),
        "auditor": (5, None),
        "attorney-general": (6, None),
        "treasurer": (7, None),
    },
    1994: {
        "secretary-of-state": (9, (0.15, 0.38)),
        "auditor": (9, (0.38, 0.50)),
        "treasurer": (9, (0.50, 0.70)),
        "attorney-general": (9, (0.70, 1.0)),
    },
    1990: {
        "secretary-of-state": (7, (0.14, 0.38)),
        "auditor": (7, (0.38, 0.57)),
        "treasurer": (7, (0.57, 0.75)),
        "attorney-general": (7, (0.75, 0.94)),
    },
}


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")
    archive = raw.cached(SOURCE, ZIP)
    with zipfile.ZipFile(archive.path) as z:
        for _, member in BOOKS.values():
            url = f"{ZIP}#zip2/{member}"
            try:
                raw.cached(SOURCE, url)
            except Exception:
                raw.store(
                    SOURCE,
                    url,
                    z.read(f"zip2/{member}"),
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                )


def book(year: int) -> raw.Artifact:
    return raw.cached(SOURCE, f"{ZIP}#zip2/{BOOKS[year][1]}")


def parse() -> None:
    rows: list[dict[str, object]] = []
    PAGES.mkdir(parents=True, exist_ok=True)
    for path in sorted((PARSED / "model").glob("*.json")):
        year = int(path.stem)
        artifact = book(year)
        for office, table in json.loads(path.read_text()).items():
            page, band = LAYOUT[year][office]
            keys = list(table["rows"])
            counties = [k for k in keys if k != "TOTAL"]
            columns = len(table["candidates"])
            blocks = [(counties[:47], True), (counties[47:], False)] if SPLIT.get(year) else [(counties, None)]
            image = PAGES / f"{artifact.sha256}-{page}.png"
            if not image.exists():
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        str(300 if year == 1998 else DPI),
                        "-gray",
                        "-png",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                    ]
                    + [str(artifact.path), str(image.with_suffix(""))],
                    check=True,
                )
                with Image.open(image) as rendered:
                    small = rendered.convert("L").resize((rendered.width // 4, rendered.height // 4))
                    ink = Image.fromarray((numpy.asarray(small) < 128).astype("uint8") * 255)
                    angle = max(
                        numpy.arange(-3, 3.01, 0.1), key=lambda a: numpy.var(numpy.asarray(ink.rotate(a)).sum(axis=1))
                    )
                    rendered.convert("L").rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255).save(image)
            with Image.open(image) as rendered:
                width = rendered.size[0]
                clean = Path(tempfile.mkdtemp()) / "clean.png"
                rendered.convert("L").point(lambda v: 0 if v < (110 if year == 1998 else 140) else 255).save(clean)
            tsv = subprocess.run(
                ["tesseract", str(clean), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
            ).stdout
            tokens = [
                (
                    int(r["left"]),
                    int(r["top"]),
                    int(r["left"]) + int(r["width"]),
                    int(r["top"]) + int(r["height"]),
                    r["text"].replace(",", ""),
                )
                for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
                if r["level"] == "5" and r["text"].replace(",", "").strip().isdigit() and int(r["top"]) > 300
            ]
            if band is not None:
                tokens = [t for t in tokens if band[0] * width <= t[2] <= band[1] * width]
            boxes: dict[tuple[str, int], tuple[int, int, int, int]] = {}
            matched = 0
            for block_keys, side in blocks:
                block = [t for t in tokens if side is None or (((t[0] + t[2]) / 2 < width / 2) == side)]
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
                pairs = [(i, (box[1] + box[3]) / 2) for (i, _), box in found.items()]
                if len(pairs) < 0.1 * columns * len(block_keys):
                    raise ValueError(f"{year} {office}: only {len(pairs)} cells matched")
                pitch = statistics.median(
                    (yj - yi) / (j - i)
                    for n, (i, yi) in enumerate(pairs[::3])
                    for j, yj in pairs[::3][n + 1 :]
                    if j != i
                )
                top = statistics.median(y - pitch * i for i, y in pairs)
                for column in range(columns):
                    for i, key in enumerate(block_keys):
                        box = found.get((i, column))
                        if box is not None and abs((box[1] + box[3]) / 2 - (top + i * pitch)) < pitch / 3:
                            matched += 1
                        else:
                            y = top + i * pitch
                            w = char * len(str(table["rows"][key][column]))
                            box = (
                                round(edges[column] - w),
                                round(y - pitch * 0.3),
                                round(edges[column]),
                                round(y + pitch * 0.3),
                            )
                        boxes[(key, column)] = box
            print(year, office, f"{matched}/{len(counties) * columns} cells matched to OCR tokens")
            printed = table["rows"]["TOTAL"]
            for column, candidate in enumerate(table["candidates"]):
                summed = sum(table["rows"][k][column] for k in counties)
                if (
                    summed != printed[column]
                    and (year, office, candidate["name"]) not in EXCEPTIONS
                    and (year, office) not in EXCEPTIONS
                ):
                    raise ValueError(
                        f"{year} {office} {candidate['name']}: counties sum to {summed}, printed {printed[column]}"
                    )
                for key in counties:
                    rows.append(
                        {
                            "year": year,
                            "office": office,
                            "name": candidate["name"],
                            "party": candidate["party"],
                            "county": key,
                            "votes": table["rows"][key][column],
                            "printed": printed[column],
                            "page": page,
                            "box": list(boxes[(key, column)]),
                        }
                    )
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64), "party": pl.String})
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
            "general elections of 1990, 1994, and 1998 by county from the Board of State Canvassers' official reports",
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
            artifact = cs.artifact(book(year), sources[year])
            for number in sorted(set(frame.filter(pl.col("year") == year)["page"])):
                image = PAGES / f"{book(year).sha256}-{number}.png"
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
                "select value from external_id where scheme = 'us.contest' and value ~ '^(1990|1994|1998)-11.*:NE:'"
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
