import json
import re
import statistics
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import numpy
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ar_sos_2006"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
WORK = DATA / "work" / "ar2006"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "2006 Election Results"
LANDING = "https://www.sos.arkansas.gov/elections/research/election-results"
URL = "https://www.sos.arkansas.gov/uploads/elections/2006_PP__GE_Election_Results.pdf"
LICENSE = "Public record"
DAY = date(2006, 11, 7)
OFFICES = {
    52: "lieutenant-governor",
    54: "attorney-general",
    56: "secretary-of-state",
    58: "treasurer",
    60: "auditor",
    62: "commissioner-of-state-lands",
}
LABELS = {
    "lieutenant-governor": "Lieutenant Governor",
    "attorney-general": "Attorney General",
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
    "auditor": "Auditor",
    "commissioner-of-state-lands": "Commissioner of State Lands",
}
PARTIES = {
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Green": "green-party",
}


READ_FIXES = {
    ("attorney-general", "Arkansas County", 1): 3967,
    ("secretary-of-state", "Yell County", 0): 153,
    ("treasurer", "Conway County", 1): 1660,
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    book = raw.cached(SOURCE, URL)
    PARSED.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for first, office in OFFICES.items():
        reads = {
            side: [json.loads((WORK / f"read{side}" / f"{first + n}.json").read_text()) for n in (0, 1)] for side in "AB"
        }
        candidates = reads["A"][0]["candidates"]
        merged: list[tuple[int, list[object]]] = []
        for n in (0, 1):
            by_a = {r[0]: r[1:] for r in reads["A"][n]["rows"]}
            by_b = {r[0]: r[1:] for r in reads["B"][n]["rows"]}
            if list(by_a) != list(by_b):
                raise ValueError(f"{office} page {first + n}: the two reads list different rows")
            for county in by_a:
                a, b = by_a[county], by_b[county]
                cells = []
                for i, (x, y) in enumerate(zip(a, b, strict=True)):
                    if x == y:
                        cells.append(x)
                        continue
                    options = [v for v in (x, y) if v is not None]
                    fits = []
                    for v in options:
                        trial = [*a[:i], v, *a[i + 1 :]] if v == x else [*b[:i], v, *b[i + 1 :]]
                        if sum(t for t in trial[:-1] if t is not None) == trial[-1]:
                            fits.append(v)
                    if len(fits) != 1:
                        print(f"{office} {county} column {i}: reads {x} and {y}, unsettled by the row sum")
                        cells.append(None)
                    else:
                        cells.append(fits[0])
                for i in range(len(cells)):
                    cells[i] = READ_FIXES.get((office, county, i), cells[i])
                merged.append((first + n, [county, *cells]))
        total = next(r for _, r in merged if r[0] == "Totals")
        body = [(p, r) for p, r in merged if r[0] != "Totals"]
        if len(body) != 75:
            raise ValueError(f"{office}: {len(body)} counties")
        for i, candidate in enumerate(candidates):
            summed = sum(r[1 + i] for _, r in body)
            if summed != total[1 + i]:
                raise ValueError(f"{office} {candidate['name']}: counties sum to {summed}, printed {total[1 + i]}")
        for p, r in body:
            if sum(r[1:-1]) != r[-1]:
                raise ValueError(f"{office} {r[0]}: {r[1:]} fails the row sum")
        for p, r in body:
            for i, candidate in enumerate(candidates):
                rows.append(
                    {
                        "office": office,
                        "name": candidate["name"],
                        "party": candidate["party"].removesuffix(" Party"),
                        "county": r[0],
                        "votes": r[1 + i],
                        "printed": total[1 + i],
                        "page": p,
                        "column": i,
                    }
                )
    frame = pl.DataFrame(rows)
    boxes: dict[tuple[int, str, int], list[int]] = {}
    for page in sorted(set(frame["page"])):
        image = PAGES / f"{book.sha256}-{page}.png"
        if not image.exists():
            subprocess.run(
                ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(book.path), str(image.with_suffix(""))],
                check=True,
            )
        with Image.open(image) as rendered:
            ink = numpy.asarray(rendered.convert("L")) < 110
        sheet = frame.filter(pl.col("page") == page)
        counties = list(dict.fromkeys(sheet["county"]))
        columns = sheet["column"].max() + 1
        def runs(lines: numpy.ndarray) -> numpy.ndarray:
            best = numpy.zeros(lines.shape[0], dtype=int)
            for index, line in enumerate(lines):
                edges = numpy.diff(numpy.r_[0, line.astype(int), 0])
                lengths = numpy.flatnonzero(edges == -1) - numpy.flatnonzero(edges == 1)
                best[index] = lengths.max() if len(lengths) else 0
            return best

        down = numpy.flatnonzero(runs(ink.T) > 500)
        left = [int(x) for x in down[numpy.r_[True, numpy.diff(down) > 8]]]
        across = numpy.flatnonzero(runs(ink) > 600)
        rules = [int(y) for y in across[numpy.r_[True, numpy.diff(across) > 8]]]
        gaps = [b - a for a, b in zip(rules, rules[1:], strict=False)]
        pitch = statistics.median(gaps)
        start = max(i for i, g in enumerate(gaps[:6]) if g > 1.5 * pitch) + 1 if max(gaps[:6]) > 1.5 * pitch else 0
        if len(rules) - start - 1 < len(counties) or len(left) < columns + 2:
            raise ValueError(f"page {page}: {len(rules)} rules, {len(left)} column rules")
        used = gaps[start : start + len(counties)]
        if max(used) > 1.25 * min(used):
            raise ValueError(f"page {page}: uneven row rules {used}")
        for column in range(columns):
            for i, county in enumerate(counties):
                boxes[(page, county, column)] = [
                    left[1 + column] + 3, rules[start + i] + 3, left[2 + column] - 3, rules[start + i + 1] - 3
                ]
    frame = frame.with_columns(
        pl.struct("page", "county", "column")
        .map_elements(lambda s: boxes[(s["page"], s["county"], s["column"])], return_dtype=pl.List(pl.Int64))
        .alias("box")
    )
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("office", "name").agg(pl.col("votes").sum()).sort("office"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    book = raw.cached(SOURCE, URL)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas's 2006 lieutenant governor, attorney general, secretary of state, treasurer, auditor, "
            "and commissioner of state lands by county from the Secretary of State's election results book",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [w for w in text.replace(",", " ").split() if plain(w) not in {"jr", "sr", "ii", "iii"}]
            return plain(words[-1])

        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=LANDING, license=LICENSE)
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
               join polity p on p.id = t.polity_id and p.iso_code = 'US-AR'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%'"""
            ).fetchall()
        }
        if len(counties) != 75:
            raise ValueError("Arkansas needs 75 counties")
        elections = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall())
        contests = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = 'us.contest' and value ~ '^2006-11-07:AR:'")
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
            key = f"{DAY}:AR:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[f"{DAY}:AR"],
                kind="office",
                office_id=cs.key("us.office", f"{office}:AR"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=f"2006 Arkansas {LABELS[str(office)]} election", lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/ar/{DAY}-general/{office}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[f"{DAY}:AR"],
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
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"AR:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
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
                        "unit_id": counties[plain(county.removesuffix(" County"))],
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
