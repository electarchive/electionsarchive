import json
import re
import struct
import subprocess
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ny_trib_state"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "nyc_trib_state"
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "The Tribune Association"
YEARS = {
    1895: {
        "volume": "vol1896",
        "item": "vol1896tribuneal00unse",
        "title": "The Tribune Almanac and Political Register for 1896 (New York State election returns, 1895)",
        "day": date(1895, 11, 5),
        "office": "secretary-of-state",
        "label": "Secretary of State",
        "county_pages": (268, 269),
        "first_county_page": 12,
        "ad_page": 270,
        "columns": (
            ("John Palmer", "republican-party"),
            ("Horatio C. King", "democratic-party"),
            ("William W. Smith", "prohibition-party"),
            ("Erasmus Pellenz", "NY:socialist-labor"),
            ("Thaddeus B. Wakeman", "peoples-party"),
        ),
    },
}
COUNTY_NOTE = (
    "Vote for {label} on {day:%B} {day.day}, {day.year}, by county, from the Tribune Almanac's 'New-York State Election. Vote by Counties' "
    "table, read from the page image. A candidate's county lines are loaded when they add up to the printed Total row, and a candidate whose lines do not is "
    "left out. Fulton and Hamilton are printed as one line. Each value's locator is the whole page. Blank, defective, and scattering ballots are not loaded."
)
AD_NOTE = (
    "Vote for {label} on {day:%B} {day.day}, {day.year}, by assembly district for New York County, from the Tribune Almanac's "
    "table 'New-York County Vote by Assembly Districts', read from the page image. Each column's district lines, with the Annexed District, add up to the "
    "printed Total row, and the Total row matches the county table's New York County line. The table prints no assembly district line for the "
    "People's candidate, only his county total in a footnote, so his column is not loaded. The assembly districts are those of 1895 and have no shape in the "
    "archive. Each value's locator is the whole page. Blank, defective, and scattering ballots are not loaded."
)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        data = json.loads((INBOX / f"{year}.json").read_text())
        left = {}
        rows = []
        for column, (name, _) in enumerate(spec["columns"]):
            total = sum(values[column] for values in data["counties"].values())
            if total != data["county_totals"][column]:
                left[name] = f"its county lines add up to {total:,} against the printed {data['county_totals'][column]:,}"
                continue
            rows.extend((year, "county", county, column, values[column]) for county, values in data["counties"].items())
        for column in range(len(data["ad_totals"])):
            total = sum(values[column] for values in data["ads"].values())
            if total != data["ad_totals"][column]:
                raise ValueError(f"{year} assembly district column {column}: {total} against {data['ad_totals'][column]}")
            rows.extend((year, "ad", ad, column, values[column]) for ad, values in data["ads"].items())
        pl.DataFrame(rows, schema=["year", "level", "unit", "column", "value"], orient="row").write_parquet(PARSED / f"{year}.parquet")
        (PARSED / f"{year}-left.json").write_text(json.dumps(left))
        print(year, len(data["counties"]), "county lines,", len(data["ads"]), "assembly districts; left out:", left)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's state officers by county and New York County by assembly district from the Tribune Almanac",
            polity="US",
        )
        city_polity = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchone()[0]
        newyork = cs.polity("US-NY")
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        counties["fultonandhamilton"] = cs.unit("us.aggregate", "NY:fultonandhamilton")
        election_area, system = conn.execute(
            """select c.district_area_id, c.electoral_system_id from contest c
               join slug s on s.entity_id = c.id and s.path = 'us/ny/1894-11-06-general/governor'"""
        ).fetchone()
        for year, spec in YEARS.items():
            day = spec["day"]
            frame = pl.read_parquet(PARSED / f"{year}.parquet")
            left = json.loads((PARSED / f"{year}-left.json").read_text())
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
            pages = {}
            for number in (*spec["county_pages"], spec["ad_page"]):
                image = PAGES / f"{spec['volume']}-{number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile", str(pdf), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                pages[number] = (cs.page(artifact, number), width, height)
                cs.add(
                    "source_page",
                    id=pages[number][0],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            election = cs.key("us.election", f"{day}:NY")
            cs.add("election", id=election, polity_id=newyork, kind="general", date_from=day, date_to=day, date_precision="day", status="held")
            cs.add("name", entity_id=election, text=f"{year} New York general election", lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/ny/{day}-general", entity_id=election)
            (office_id,) = conn.execute(
                "select entity_id from external_id where scheme = 'us.office' and value = %s", [f"{spec['office']}:NY"]
            ).fetchone()
            contest_key = f"{day}:NY:{spec['office']}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=election_area,
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )
            cs.add("name", entity_id=contest, text=f"{year} New York {spec['label']} election", lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/ny/{day}-general/{spec['office']}", entity_id=contest)
            options = []
            for name, party_key in spec["columns"]:
                root_key = f"{contest_key}:{slug(name)}"
                root = cs.key("us.option", root_key)
                person = cs.key(f"{SOURCE}.person", slug(name))
                cs.add("person", id=person)
                cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=root, contest_id=contest, kind="candidate", ballot_label=name)
                candidacy = cs.key("us.candidacy", f"{root_key}:head")
                cs.add("candidacy", id=candidacy, person_id=person, option_id=root, role="head", ballot_name=name, is_independent=False)
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=cs.key("us.party", party_key), kind="nominated")
                options.append(root)
            sets = {}
            for level, note in (("county", COUNTY_NOTE), ("ad", AD_NOTE)):
                sets[level] = cs.key(f"{SOURCE}.result_set", f"{contest_key}:{level}")
                cs.add(
                    "result_set",
                    id=sets[level],
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=level == "county",
                    completeness=None,
                )
                cs.add(
                    "note",
                    entity_id=sets[level],
                    kind="methodology",
                    body_md=note.format(label=spec["label"], day=day) + ("".join(f" Note: {name}'s column is left out because {why}." for name, why in left.items()) if level == "county" else ""),
                )
            units: dict[str, object] = {}
            names = list(json.loads((INBOX / f"{year}.json").read_text())["counties"])
            for level, unit_name, column, value in frame.select("level", "unit", "column", "value").iter_rows():
                if level == "county":
                    plain = re.sub(r"[^a-z]", "", unit_name.lower())
                    unit = counties[plain]
                    page = pages[spec["county_pages"][0] if names.index(unit_name) < spec["first_county_page"] else spec["county_pages"][1]]
                else:
                    code = f"36061:{int(unit_name):02d}" if unit_name != "AN" else "36061:AN"
                    if code not in units:
                        units[code] = cs.unit("us.nyc_ad", code, day)
                        cs.add(
                            "reporting_unit",
                            id=units[code],
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
                    unit = units[code]
                    page = pages[spec["ad_page"]]
                fact = {"result_set_id": sets[level], "unit_id": unit, "option_id": options[column], "channel": 0, "measure": 1, "count_round": 0}
                cs.add("result_value", **fact, flags=0, value=value)
                cs.add("value_locator", **fact, page_id=page[0], source_id=None, x0=0, y0=0, x1=page[1], y1=page[2])
            print(year, len(units), "assembly districts,", frame.height, "values")
        return cs.write()
