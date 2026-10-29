import json
import struct
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_trib_ad"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "nyc_trib_ad"
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "The Tribune Association"
YEARS = {
    1885: {
        "volume": "vol1886",
        "item": "vol1886tribuneal00unse",
        "title": "The Tribune Almanac and Political Register for 1886 (vote of New York City by assembly districts, 1885)",
        "day": date(1885, 11, 3),
        "office": "governor",
        "page": 78,
        "columns": (
            ("Ira Davenport", "republican-party"),
            ("David B. Hill", "democratic-party"),
            ("W. Martin Jones", "greenback-party"),
            ("Henry Clay Bascom", "prohibition-party"),
        ),
    },
}
NOTE = (
    "Vote for {office} on {day:%B} {day.day}, {day.year}, by assembly district for New York County, from the Tribune Almanac's "
    "table 'Vote of New York City by Assembly Districts', read from the page image. Each column's district lines add up to the "
    "printed Total row. Each value's locator is the whole page. The assembly districts are those of {day.year} and have no "
    "shape in the archive. Blank, defective, and scattering ballots are not loaded."
)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        data = json.loads((INBOX / f"{year}.json").read_text())
        for column, printed in enumerate(data["totals"]):
            total = sum(values[column] for values in data["rows"].values())
            if total != printed:
                raise ValueError(f"{year} column {column}: districts add up to {total}, printed {printed}")
        pl.DataFrame(
            [(year, int(ad), column, value) for ad, values in data["rows"].items() for column, value in enumerate(values)],
            schema=["year", "ad", "column", "value"],
            orient="row",
        ).write_parquet(PARSED / f"{year}.parquet")
        print(year, len(data["rows"]), "assembly districts tie to the printed totals")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York County's returns by assembly district from the Tribune Almanac, 1885 on",
            polity="US",
        )
        city_polity = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchone()[0]
        for year, spec in YEARS.items():
            day = spec["day"]
            frame = pl.read_parquet(PARSED / f"{year}.parquet")
            contest, election, contest_key = conn.execute(
                """select c.id, c.election_id, x.value from contest c
                   join slug s on s.entity_id = c.id and s.path = %s
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' limit 1""",
                [f"us/ny/{year}-general/{spec['office']}"],
            ).fetchone()
            roots = {
                label: (option, key)
                for option, label, key in conn.execute(
                    """select o.id, o.ballot_label, x.value from contest_option o
                       join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
                       where o.contest_id = %s and o.parent_option_id is null and o.kind = 'candidate'""",
                    [contest],
                ).fetchall()
            }
            url = f"https://archive.org/download/{spec['item']}/{spec['item']}.pdf"
            source = cs.source(PUBLISHER, spec["title"])
            cs.add(
                "source",
                id=source,
                kind="compendium",
                publisher=PUBLISHER,
                title=spec["title"],
                url=f"https://archive.org/details/{spec['item']}",
                license="Public domain",
                notes="Scanned volume on the Internet Archive.",
            )
            pdf = INBOX / f"{spec['volume']}.pdf"
            artifact = cs.artifact(
                raw.store(SOURCE, url, pdf.read_bytes(), suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"),
                source,
            )
            image = PAGES / f"{spec['volume']}-{spec['page']}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(spec["page"]), "-l", str(spec["page"]), "-singlefile", str(pdf), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page = cs.page(artifact, spec["page"])
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=spec["page"],
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            options = []
            for name, party_key in spec["columns"]:
                if name not in roots:
                    root_key = f"{contest_key}:{slug(name)}"
                    root = cs.key("us.option", root_key)
                    person = cs.key(f"{SOURCE}.person", slug(name))
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                    cs.add("contest_option", id=root, contest_id=contest, kind="candidate", ballot_label=name)
                    candidacy = cs.key("us.candidacy", f"{root_key}:head")
                    cs.add("candidacy", id=candidacy, person_id=person, option_id=root, role="head", ballot_name=name, is_independent=False)
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=cs.key("us.party", party_key), kind="nominated")
                    roots[name] = (root, root_key)
                options.append(roots[name][0])
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE.format(office=spec["office"], day=day))
            units: dict[int, object] = {}
            for ad, column, value in frame.select("ad", "column", "value").iter_rows():
                if ad not in units:
                    code = f"36061:{ad:02d}"
                    units[ad] = cs.unit("us.nyc_ad", code, day)
                    if units[ad] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=units[ad],
                            polity_id=city_polity,
                            scheme_code="us.nyc_ad",
                            code=code,
                            parent_id=cs.unit("us.county", "36061"),
                            area_relation="unlocated",
                            area_id=None,
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                fact = {"result_set_id": result_set, "unit_id": units[ad], "option_id": options[column], "channel": 0, "measure": 1, "count_round": 0}
                cs.add("result_value", **fact, flags=0, value=value)
                cs.add("value_locator", **fact, page_id=page, source_id=None, x0=0, y0=0, x1=width, y1=height)
            print(year, len(units), "assembly districts,", frame.height, "values")
        return cs.write()
