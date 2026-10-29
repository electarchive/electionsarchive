import json
import re
import struct
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import nyc_early_mayors as mayors
from ea.sources.us import nyc_wards_shapes as shapes
from ea.store import DATA, connect

SOURCE = "us.nyc_wards_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (1859, 1861, 1870)
TRIB = {1854: 56, 1857: 217, 1863: 509, 1865: 617, 1867: 712}
TRIB_URL = "https://archive.org/download/tribunealmanacfo00gree/tribunealmanacfo00gree.pdf"
TRIB_TITLE = "The Tribune almanac for the years 1838 to 1868, inclusive (New York City vote by wards)"
TRIB_NOTE = (
    "Scanned volume on the Internet Archive (item tribunealmanacfo00gree), whose tables of New York City's vote for mayor "
    "by ward sit on PDF pages 56 (1854), 217 (1857), 509 (1863), 617 (1865), and 712 (1867)."
)
TRIB_INBOX = DATA / "inbox" / "nyc_trib"
PAGES = DATA / "pages" / SOURCE
FIRST_WARD_DAY = date(1855, 1, 1)
JOHNSON = "http://bklyn-genealogy-info.stevemorse.org/Ward/1864.Man.html"
SHAPES_TITLE = "Manhattan ward boundaries 1859-1870, rebuilt from the ward ordinances"
SHAPES_NOTE = (
    "Approximate outlines of Manhattan's 22 wards, rebuilt by tracing the street lines in the ward descriptions of "
    "Johnson's 1864 Manhattan ward list (transcribed on Steve Morse's Brooklyn genealogy site, credit to the "
    "transcriber Sullivan) over OpenStreetMap street centerlines (ODbL) and today's shoreline. Approximations: modern "
    "shoreline and landfill, Cottage Place replaced by Sixth Avenue, chords across gaps in the street data, and Central "
    "Park split as the Johnson note describes. The Hayward 1851 map (Princeton) shows only 20 wards and serves as a check."
)
TITLE = "New York City mayoral election articles, ward tables"
NOTE = (
    "Ward tables from the English Wikipedia's articles on the 1859, 1861, and 1870 New York City mayoral elections. "
    "The 1859 and 1861 tables cite the Tribune Almanac (HathiTrust); the 1870 table cites the 1870 Manual of the "
    "Corporation."
)
METHOD_TRIB = (
    "Votes by ward from the Tribune Almanac's table of New York City's vote for mayor in {year}, read from the page "
    "image by two vision models (Claude Sonnet 5.5 and Opus 5.5). A candidate's column is loaded only where both reads "
    "agree in every ward and the 22 wards add up to that candidate's citywide total in the default set; columns "
    "that do not tie are left out. Johnson's ward descriptions are unchanged from 1855, so units from {year} on take "
    "the approximate ward shapes{unlocated}."
)
METHOD = (
    "Votes by ward from the ward table in the English Wikipedia's article on the {year} New York City mayoral election. "
    "Each ward's candidate votes add up to its printed total, and each candidate's ward votes add up to the citywide "
    "total in the default set. The city was Manhattan's 22 wards; their shapes are approximate (see the shape source)."
)


def parse() -> None:
    lines = pl.read_parquet(raw.parsed(mayors.SOURCE) / "lines.parquet")
    rows: list[dict[str, object]] = []
    for year in YEARS:
        wikitext = json.loads(raw.cached(SOURCE, mayors.page(year)).path.read_text())["query"]["pages"][0][
            "revisions"
        ][0]["slots"]["main"]["content"]
        table = next(
            match.group(0)
            for match in re.finditer(r"\{\|.*?\n\|\}", wikitext, re.DOTALL)
            if re.search(r"Results by ward", match.group(0))
        )
        names = [
            re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " ", re.sub(r"^\s*colspan=\d\|", "", head))).strip()
            for head in re.split(r"!!", re.search(r"! rowspan=2\| Ward(.*?)\n", table).group(1))
            if "Total" not in head
        ]
        names = [name for name in names if name]
        citywide = lines.filter((pl.col("year") == year) & (pl.col("stage") == "general") & (pl.col("office") == "mayor"))
        candidates = []
        for header in names:
            surname = header.split()[0].lower()
            (found,) = [c for c in citywide["candidate"] if c.split()[-1].lower() == surname]
            candidates.append(found)
        wards = []
        for chunk in re.split(r"\n\|-[^\n]*", table):
            match = re.match(r"\s*!\s*(\d+)\s*\n(.*)", chunk, re.DOTALL)
            if match is None:
                continue
            body = re.sub(r"\{\{[^}]*\}\}\|?", "", match.group(2)).replace("'''", "")
            numbers = [
                int(number.replace(",", ""))
                for number in re.findall(r"(?<![\d.,])\d[\d,]*(?![\d,.]*%)(?!\d)", body)
            ]
            if len(numbers) != len(candidates) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"{year} ward {match.group(1)} does not add up: {numbers}")
            wards.append((int(match.group(1)), numbers))
        if [ward for ward, _ in wards] != list(range(1, 23)):
            raise ValueError(f"{year} wards are {[ward for ward, _ in wards]}")
        for position, candidate in enumerate(candidates):
            total = citywide.filter(pl.col("candidate") == candidate)["votes"].sum()
            if sum(numbers[position] for _, numbers in wards) != total:
                raise ValueError(f"{year} {candidate} wards add up to a different total than {total}")
            rows.extend(
                {"year": year, "ward": ward, "candidate": candidate, "votes": numbers[position]}
                for ward, numbers in wards
            )
    coverage: list[dict[str, object]] = []
    for year, page in TRIB.items():
        left: list[str] = []
        reads = [json.loads((TRIB_INBOX / f"p{page}-{reader}.json").read_text()) for reader in ("sonnet", "opus")]
        citywide = lines.filter((pl.col("year") == year) & (pl.col("stage") == "general") & (pl.col("office") == "mayor"))
        for position, head in enumerate(reads[0]["candidates"]):
            if head["name"] == "Sc't":
                continue
            surname = {"Her'ck": "Herrick", "Hoff": "Hoffman", "Robts": "Roberts"}.get(head["name"], head["name"]).lower()
            (found,) = [c for c in citywide["candidate"] if c.split()[-1].lower() == surname]
            cells = [[int(ward["votes"][position].replace(",", "")) for ward in read["wards"]] for read in reads]
            wards = [[ward["ward"] for ward in read["wards"]] for read in reads]
            total = citywide.filter(pl.col("candidate") == found)["votes"].sum()
            if cells[0] != cells[1] or wards[0] != list(range(1, 23)) or sum(cells[0]) != total:
                print(f"{year} {found}: left out, wards add up to {sum(cells[0])} and {sum(cells[1])} against {total}")
                left.append(found)
                continue
            rows.extend(
                {"year": year, "ward": ward, "candidate": found, "votes": votes, "page": page, "bbox": reads[0]["bbox"]}
                for ward, votes in zip(wards[0], cells[0], strict=True)
            )
        coverage.append(
            {
                "year": year,
                "share": 1 - citywide.filter(pl.col("candidate").is_in(left))["votes"].sum() / citywide["votes"].sum(),
                "left": ", ".join(left),
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(coverage).write_parquet(PARSED / "coverage.parquet")
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "wards.parquet")


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, mayors.page(year), suffix=".json")
    raw.fetch(SOURCE, shapes.OVERPASS, suffix=".json")
    raw.fetch(SOURCE, JOHNSON, suffix=".html")
    path = TRIB_INBOX / "trib1838_1868.pdf"
    raw.store(
        SOURCE,
        TRIB_URL,
        path.read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
        media_type="application/pdf",
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "wards.parquet")
    coverage = {year: (share, left) for year, share, left in pl.read_parquet(PARSED / "coverage.parquet").iter_rows()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City mayoral elections of 1854, 1857, 1859, 1861, 1863, 1865, 1867, and 1870 by ward, from the English Wikipedia's ward tables and the Tribune Almanac",
            polity="US",
        )
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/1859_New_York_City_mayoral_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        ((polity,),) = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchall()
        tribune = cs.source("New-York Tribune", TRIB_TITLE)
        cs.add(
            "source",
            id=tribune,
            kind="compendium",
            publisher="New-York Tribune",
            title=TRIB_TITLE,
            url="https://archive.org/details/tribunealmanacfo00gree",
            license="Public domain",
            notes=TRIB_NOTE,
        )
        trib_pdf = raw.cached(SOURCE, TRIB_URL)
        trib_artifact = cs.artifact(trib_pdf, tribune)
        trib_pages: dict[int, object] = {}
        for page in TRIB.values():
            image = PAGES / f"{trib_pdf.sha256}-{page}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(trib_pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            trib_pages[page] = cs.page(trib_artifact, page)
            cs.add(
                "source_page",
                id=trib_pages[page],
                artifact_id=trib_artifact,
                page_number=page,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        outline = cs.source("Johnson; OpenStreetMap contributors", SHAPES_TITLE)
        cs.add(
            "source",
            id=outline,
            kind="academic",
            publisher="Johnson; OpenStreetMap contributors",
            title=SHAPES_TITLE,
            url=JOHNSON,
            license="ODbL 1.0 (street lines)",
            notes=SHAPES_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, shapes.OVERPASS), outline)
        cs.artifact(raw.cached(SOURCE, JOHNSON), outline)
        if conn.execute("select 1 from scheme where code = 'us.nyc_ward'").fetchone() is None:
            cs.add("scheme", code="us.nyc_ward", polity_id=polity, kind="precinct", rank=1, noun="ward")
        added: set[object] = set()
        county = cs.unit("us.county", "36061")
        county_area = cs.area("us.county", "36061")
        built = shapes.build(raw.cached(SOURCE, shapes.OVERPASS).path.read_bytes(), conn)
        geoms = {
            ward: (geom, point)
            for ward, geom, point in conn.execute(
                """select f.ward, encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(
                        st_setsrid(st_geomfromgeojson(f.geojson), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_pointonsurface(st_makevalid(st_setsrid(st_geomfromgeojson(f.geojson), 4326)))), 'hex')
                   from unnest(%s::int[], %s::text[]) as f(ward, geojson)""",
                [list(built), list(built.values())],
            ).fetchall()
        }
        cs.add(
            "boundary_set",
            id=(conn.execute("select id from boundary_set where scheme_code = 'us.nyc_ward'").fetchone() or (uuid4(),))[0],
            scheme_code="us.nyc_ward",
            vintage="1855",
            valid_from=FIRST_WARD_DAY,
            valid_to=date(1871, 1, 1),
            as_of=FIRST_WARD_DAY,
        )
        areas = {ward: cs.area("us.nyc_ward", f"36061:{ward:02d}") for ward in range(1, 23)}
        for ward, area in areas.items():
            cs.add("area", id=area, scheme_code="us.nyc_ward")
            cs.add("area_code", area_id=area, code=f"36061:{ward:02d}", valid_from=FIRST_WARD_DAY, valid_to=date(1871, 1, 1))
            cs.add("name", entity_id=area, text=f"Ward {ward}", lang="en", kind="official", is_preferred=True)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=FIRST_WARD_DAY,
                valid_to=date(1871, 1, 1),
                geom=geoms[ward][0],
                label_point=geoms[ward][1],
                quality="academic",
                source_id=outline,
            )
            cs.add("area_parent", area_id=area, parent_id=county_area, valid_from=FIRST_WARD_DAY, valid_to=date(1871, 1, 1))
        for year in (*sorted(TRIB), *YEARS):
            held = tribune if year in TRIB else source
            if year not in TRIB:
                cs.artifact(raw.cached(SOURCE, mayors.page(year)), source)
            day = mayors.EARLY[year][0]
            contest_key = f"nyc_early_mayors:{day}:NY:3651000:mayor"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:NY:3651000")
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:wards")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=held,
                is_default=False,
                completeness=coverage.get(year, (1, ""))[0],
            )
            if coverage.get(year, (1, ""))[1]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"This set holds only part of the candidates. {coverage[year][1]} {'is' if ',' not in coverage[year][1] else 'are'} "
                        "missing because the ward column does not add up to the printed ward total or to the candidate's "
                        "citywide total in the default set. Ward totals and winners from these columns are not the election's; "
                        "the citywide set stays the default."
                    ),
                )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=(
                    METHOD_TRIB.format(year=year, unlocated="" if day >= FIRST_WARD_DAY else " except that the 1854 units are unlocated")
                    if year in TRIB
                    else METHOD.format(year=year)
                ),
            )
            for ward, candidate, votes, page, bbox in frame.filter(pl.col("year") == year).select(
                "ward", "candidate", "votes", "page", "bbox"
            ).iter_rows():
                code = f"36061:{ward:02d}"
                unit = cs.unit("us.nyc_ward", code, day)
                if unit not in added:
                    added.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.nyc_ward",
                        code=code,
                        parent_id=county,
                        area_relation="equals" if day >= FIRST_WARD_DAY else "unlocated",
                        area_id=areas[ward] if day >= FIRST_WARD_DAY else None,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": cs.key("us.option", f"{contest_key}:{slug(candidate)}"),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=votes)
                if page is not None:
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=trib_pages[page],
                        source_id=None,
                        x0=bbox[0],
                        y0=bbox[1],
                        x1=bbox[2],
                        y1=bbox[3],
                    )
        return cs.write()
