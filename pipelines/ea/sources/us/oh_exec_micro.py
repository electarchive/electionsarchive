import csv
import io
import json
import re
import statistics
import subprocess
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

SOURCE = "us.oh_exec_micro"
RAW = "us.sri"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Ohio Secretary of State"
TITLE = "Ohio Election Statistics, {day:%Y}"
LICENSE = "Public record"
DPI = 300
GRID = {1990: (26.5, 28.5, 25, 33), 1994: (35, 37, 34, 42)}
ITEMS = {
    1990: ("micro_IA40706942_0388", date(1990, 11, 6)),
    1994: ("micro_IA40706953_0173", date(1994, 11, 8)),
}
OFFICES = {
    "attorney-general": "Attorney General",
    "auditor": "Auditor",
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
}
PARTIES = {"D": "democratic-party", "R": "republican-party"}
CAVEAT = (
    "The county table's {name} column adds to {summed:,}, not the {printed:,} the Secretary of State prints as the "
    "statewide total; the counties are kept as printed."
)


def url(year: int) -> str:
    item = ITEMS[year][0]
    return f"https://archive.org/download/{item}/{item}.pdf"


def fetch() -> None:
    for year in ITEMS:
        raw.fetch(RAW, url(year), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        order = [
            text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%' order by u.code"""
            ).fetchall()
        ]
    rows: list[dict[str, object]] = []
    PAGES.mkdir(parents=True, exist_ok=True)
    for path in sorted((PARSED / "model").glob("*.json")):
        year = int(path.stem)
        book = raw.cached(RAW, url(year))
        for group, table in json.loads(path.read_text())[str(year)].items():
            columns = table["columns"]
            cut = order.index(table["split"]) + 1
            slices = [(table["pages"][0], order[:cut]), (table["pages"][1], order[cut:])]
            boxes: dict[tuple[str, int], tuple[int, int, int, int]] = {}
            at_page: dict[str, int] = {}
            matched = 0
            for page, counties in slices:
                image = PAGES / f"{book.sha256}-{page}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                        + [str(book.path), str(image.with_suffix(""))],
                        check=True,
                    )
                tsv = subprocess.run(
                    ["tesseract", str(image), "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
                ).stdout
                tokens = [
                    (int(r["left"]), int(r["top"]), int(r["left"]) + int(r["width"]), int(r["top"]) + int(r["height"]), re.sub(r"\D", "", r["text"]))
                    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
                    if r["level"] == "5" and re.fullmatch(r"[-\d,.]*\d[\d,.]*", r["text"].strip())
                ]
                char = statistics.median((t[2] - t[0]) / len(t[4]) for t in tokens if t[4]) if tokens else 0
                height = statistics.median(t[3] - t[1] for t in tokens) if tokens else 0
                found: dict[tuple[int, int], tuple[int, int, int, int]] = {}
                edges: list[float] = []
                for column in range(len(columns)):
                    expected = [str(table["rows"][c][column]) for c in counties]
                    rights = [t[2] for t in tokens if t[4] in expected and (len(t[4]) > 1 or max(map(len, expected)) == 1)]
                    if len(rights) < len(counties) / 3:
                        break
                    common = statistics.mode(round(r / 12) for r in rights)
                    edge = statistics.median(r for r in rights if round(r / 12) == common)
                    edges.append(edge)
                    mine = sorted((t for t in tokens if abs(t[2] - edge) < 30), key=lambda t: t[1])
                    match = SequenceMatcher(None, expected, [t[4] for t in mine], autojunk=False)
                    for a, b, size in match.get_matching_blocks():
                        for offset in range(size):
                            found[(a + offset, column)] = mine[b + offset][:4]
                centers: dict[int, float] = {}
                if len(edges) < len(columns):
                    with Image.open(image) as rendered:
                        gray = numpy.asarray(rendered.convert("L"))
                    top, bottom = round(gray.shape[0] * 0.15), round(gray.shape[0] * 0.9)
                    left, right = round(gray.shape[1] * 0.33), round(gray.shape[1] * 0.85)
                    names = gray[top:bottom, round(gray.shape[1] * 0.08) : round(gray.shape[1] * 0.27)]
                    profile = numpy.convolve((names < 180).sum(axis=1).astype(float), numpy.ones(5) / 5, mode="same")
                    best = (-1.0, 0, 0.0, 0.0)
                    index = numpy.arange(len(counties))
                    low, high, extra_low, extra_high = GRID[year]
                    for pitch in numpy.arange(low, high, 0.1):
                        for group in numpy.arange(5 * pitch + extra_low, 5 * pitch + extra_high, 0.5):
                            offsets = (index // 5) * group + (index % 5) * pitch
                            for y0 in range(0, 400):
                                score = profile[numpy.clip((y0 + offsets).astype(int), 0, len(profile) - 1)].sum()
                                if score > best[0]:
                                    best = (score, y0, pitch, group)
                    _, y0, pitch, group = best
                    centers = {i: top + y0 + (i // 5) * group + (i % 5) * pitch for i in range(len(counties))}
                    height = pitch * 0.55
                    band = gray[round(centers[0] - 10) : round(centers[len(counties) - 1] + 10), left:right] < 200
                    column_profile = band.sum(axis=0)
                    on = column_profile > column_profile.max() * 0.1
                    runs = []
                    start = None
                    for x, flag in enumerate(on):
                        if flag and start is None:
                            start = x
                        if not flag and start is not None:
                            runs.append((start, x))
                            start = None
                    merged: list[tuple[int, int]] = []
                    for lo, hi in runs:
                        if merged and lo - merged[-1][1] < 35:
                            merged[-1] = (merged[-1][0], hi)
                        else:
                            merged.append((lo, hi))
                    merged = sorted(
                        sorted(merged, key=lambda run: -int(column_profile[run[0] : run[1]].sum()))[: len(columns)]
                    )
                    if len(merged) != len(columns):
                        raise ValueError(f"{year} {group} page {page}: {len(merged)} column runs for {len(columns)} columns")
                    edges = [left + hi for _, hi in merged]
                    char = statistics.median(hi - lo for lo, hi in merged) / 5.5
                    found = {}
                for i in range(len(counties)):
                    if i in centers:
                        continue
                    ys = [(box[1] + box[3]) / 2 for (row, _), box in found.items() if row == i]
                    if ys:
                        centers[i] = statistics.median(ys)
                known = sorted(centers)
                for i in range(len(counties)):
                    if i in centers:
                        continue
                    below = [k for k in known if k < i]
                    above = [k for k in known if k > i]
                    if below and above:
                        lo, hi = below[-1], above[0]
                        centers[i] = centers[lo] + (centers[hi] - centers[lo]) * (i - lo) / (hi - lo)
                    else:
                        near, far = (known[:2] if above else known[-2:][::-1]) if len(known) > 1 else (known[0], known[0])
                        pitch = (centers[far] - centers[near]) / (far - near) if far != near else height * 1.5
                        centers[i] = centers[near] + pitch * (i - near)
                for column in range(len(columns)):
                    for i, county in enumerate(counties):
                        box = found.get((i, column))
                        if box is None or abs((box[1] + box[3]) / 2 - centers[i]) > height:
                            width = char * len(str(table["rows"][county][column]))
                            box = (round(edges[column] - width), round(centers[i] - height / 2), round(edges[column]), round(centers[i] + height / 2))
                        else:
                            matched += 1
                        boxes[(county, column)] = box
                        at_page[county] = page
            print(year, group, f"{matched}/{len(order) * len(columns)} cells matched to OCR tokens")
            printed = table["rows"]["TOTAL"]
            for column, candidate in enumerate(columns):
                for county in order:
                    rows.append(
                        {
                            "year": year,
                            "office": candidate["office"],
                            "name": candidate["name"],
                            "party": candidate["party"],
                            "county": county,
                            "votes": table["rows"][county][column],
                            "printed": printed[column],
                            "page": at_page[county],
                            "box": list(boxes[(county, column)]),
                        }
                    )
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "office", "name")
        .agg(pl.col("votes").sum(), pl.col("printed").first())
        .with_columns((pl.col("votes") - pl.col("printed")).alias("off"))
        .sort("year", "office")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's attorney general, auditor, secretary of state, and treasurer elections of 1990 and 1994 "
            "by county from the Secretary of State's Ohio Election Statistics on the SRI microfiche",
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
            title = TITLE.format(day=ITEMS[year][1])
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source", id=sources[year], kind="official_body", publisher=PUBLISHER, title=title,
                url=f"https://archive.org/details/{ITEMS[year][0]}", license=LICENSE,
            )
            book = raw.cached(RAW, url(year))
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
               join polity p on p.id = t.polity_id and p.iso_code = 'US-OH'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
        elections = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall())
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^(1990|1994)-11.*:OH:'"
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
            day = ITEMS[int(year)][1]
            key = f"{day}:OH:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            election = elections[f"{day}:OH"]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{office}:OH"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Ohio {OFFICES[str(office)]} election")
            cs.add("slug", path=f"us/oh/{day}-general/{office}", entity_id=contest)
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
            for printed_name, printed, summed in (
                lines.group_by("name").agg(pl.col("printed").first(), pl.col("votes").sum()).iter_rows()
            ):
                if printed != summed:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=CAVEAT.format(name=printed_name, summed=summed, printed=printed),
                    )
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = people.get(probe, set())
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"OH:{slug(candidate)}")
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
                    kind="write_in" if party == "WI" else "candidate",
                    party_id=parties.get(str(party)),
                    ballot_label=candidate,
                )
                if party != "WI":
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party not in PARTIES and party != "",
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
            cs.add("outcome", result_set_id=result_set, option_id=max(totals, key=totals.__getitem__), kind="elected", seats=1)
        return cs.write()
