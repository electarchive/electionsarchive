import json
import re
import struct
import subprocess
from datetime import UTC, date, datetime, timedelta

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_redbook"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "redbook"
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "New York State Legislature"
TAGS = ("sonnet", "opus")
COUNTY = {"New York": "36061", "Kings": "36047", "Queens": "36081", "Richmond": "36085"}
ONES = ("", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth")
TENS = {"twentieth": 20, "thirtieth": 30, "twenty": 20, "thirty": 30}
YEARS = {
    1912: {
        "volume": "manual1913",
        "item": "manualforuseofle1913newy",
        "title": "Manual for the Use of the Legislature of the State of New York, 1913 (vote for Governor, 1912, by election district)",
        "day": date(1912, 11, 5),
        "office": "governor",
        "pages": (*range(828, 845), *range(854, 880), *range(894, 898), 900),
        "columns": {
            0: ("William Sulzer", None),
            1: ("Job E. Hedges", None),
            2: ("Charles Edward Russell", "NY:socialist"),
            3: ("Oscar S. Straus", "NY:progressive-party"),
            4: ("T. Alexander MacNicholl", "prohibition-party"),
            6: ("John Hall", "NY:socialist-labor"),
        },
        "extra_party": {"Oscar S. Straus": "NY:independence-league"},
        "note": (
            "Vote for Governor on November 5, 1912, by assembly district and election district, from the Red Book's "
            "'Vote of State of New York' tables, read from page images by a vision model (Claude Sonnet 5.5, with "
            "Claude Opus 5.5 rereading any assembly district that missed a total). The Red Book prints the Independence "
            "League and National Progressive votes together under one column, which is Oscar S. Straus's. An assembly "
            "district is loaded only when its election district rows add up to the printed assembly district total in every "
            "column, and that total adds up to its own whole number of votes. Each value's locator is the whole "
            "page. The assembly districts are those of 1912, including the Bronx's 31 through 35 (printed under New York "
            "County), and have no shape in the archive. Blank, void, defective, and scattering ballots are not loaded."
        ),
    },
}


def ordinal(label: str) -> int | None:
    words = re.sub(r"[^a-z\- ]", "", label.lower()).replace("-", " ").split()
    total = 0
    for word in words:
        if word in ONES[1:]:
            total += ONES.index(word)
        elif word in TENS:
            total += TENS[word]
        else:
            return None
    return total or None


def number(cell: str) -> int | None:
    text = cell.replace(",", "").replace(".", "").strip()
    if text == "":
        return 0
    return int(text) if text.isdigit() else None


def read(year: int, tag: str) -> pl.DataFrame:
    spec = YEARS[year]
    rows = []
    current: dict[str, int | None] = {}
    for page in spec["pages"]:
        path = INBOX / f"{spec['volume']}-p{page}-{tag}.json"
        if not path.exists():
            path = INBOX / f"{spec['volume']}-p{page}-{TAGS[0]}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        county = next((name for name in COUNTY if name.lower() in data["county"].lower()), None)
        if county is None or len(data["columns"]) != 9:
            continue
        for row in data["rows"]:
            if row["district"] == 0:
                continue
            ad = ordinal(row["ad"]) if row["ad"].strip() else current.get(county)
            if ad is None:
                continue
            current[county] = ad
            cells = [number(cell) for cell in row["cells"]]
            rows.append({"county": county, "ad": ad, "ed": row["district"], "page": page, "cells": cells, "total": False})
        for row in data["totals"]:
            ad = ordinal(row["ad"]) if row["ad"].strip() else current.get(county)
            if ad is not None:
                rows.append({"county": county, "ad": ad, "ed": 0, "page": page, "cells": [number(cell) for cell in row["cells"]], "total": True})
    return pl.DataFrame(rows, schema={"county": pl.Utf8, "ad": pl.Int64, "ed": pl.Int64, "page": pl.Int64, "cells": pl.List(pl.Int64), "total": pl.Boolean}) if rows else pl.DataFrame()


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        wanted = list(spec["columns"])
        accepted: dict[tuple[str, int], tuple[str, list]] = {}
        failures: dict[tuple[str, int], str] = {}
        off: list[str] = []
        fixed: list[str] = []
        for tag in TAGS:
            frame = read(year, tag)
            if frame.is_empty():
                continue
            groups: dict[tuple[str, int], list] = {}
            for record in frame.iter_rows(named=True):
                groups.setdefault((record["county"], record["ad"]), []).append(record)
            for key, records in groups.items():
                if key in accepted:
                    continue
                eds = [r for r in records if not r["total"]]
                totals = [r for r in records if r["total"]]
                reason = None
                if len(totals) == 1 and not any(None in r["cells"] or len(r["cells"]) != 9 for r in eds + totals):
                    gaps = [sum(r["cells"][i] for r in eds) - totals[0]["cells"][i] for i in range(9)]
                    wrong = [i for i, gap in enumerate(gaps) if gap]
                    if len(wrong) == 1 and wrong[0] < 8:
                        column = wrong[0]
                        misses = [r for r in eds if sum(r["cells"][:8]) != r["cells"][8]]
                        if misses and sum(sum(r["cells"][:8]) - r["cells"][8] for r in misses) == gaps[column] and all(
                            r["cells"][column] - (sum(r["cells"][:8]) - r["cells"][8]) >= 0 for r in misses
                        ):
                            for r in misses:
                                excess = sum(r["cells"][:8]) - r["cells"][8]
                                fixed.append(f"{key[0]} assembly district {key[1]}, election district {r['ed']}, column {column + 1}: {r['cells'][column]} read, {r['cells'][column] - excess} used")
                                r["cells"][column] -= excess
                if len(totals) != 1:
                    reason = f"{len(totals)} total rows"
                elif any(None in r["cells"] or len(r["cells"]) != 9 for r in eds + totals):
                    reason = "an unreadable or misaligned cell"
                elif sum(totals[0]["cells"][:8]) != totals[0]["cells"][8]:
                    reason = "a printed total that does not add up to its whole number of votes"
                elif any(sum(r["cells"][i] for r in eds) != totals[0]["cells"][i] for i in range(9)):
                    reason = "rows that do not add up to the printed total"
                elif any(r["cells"][5] for r in eds):
                    reason = "a National Progressive cell"
                elif len({r["ed"] for r in eds}) != len(eds):
                    reason = "a repeated election district number"
                if reason is None:
                    accepted[key] = (tag, records)
                    for r in eds:
                        if sum(r["cells"][:8]) != r["cells"][8]:
                            off.append(f"{key[0]} assembly district {key[1]}, election district {r['ed']}")
                    failures.pop(key, None)
                else:
                    failures[key] = f"{reason} ({tag})"
        out = []
        for (county, ad), (tag, records) in accepted.items():
            for r in records:
                for index in wanted:
                    out.append({"county": county, "ad": ad, "ed": r["ed"], "option": index, "value": r["cells"][index], "page": r["page"], "tag": tag})
        pl.DataFrame(out).write_parquet(PARSED / f"{year}.parquet")
        (PARSED / f"{year}-failures.json").write_text(
            json.dumps({"left": {f"{c}:{a}": why for (c, a), why in sorted(failures.items())}, "off": sorted(set(off)), "fixed": sorted(set(fixed))}, indent=1)
        )
        counts = {c: sum(1 for k in accepted if k[0] == c) for c in COUNTY}
        print(year, "assembly districts loaded", counts, "left out", {f"{c}:{a}": why for (c, a), why in sorted(failures.items())})


def emit():
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's assembly district and election district returns from the Red Book",
            polity="US",
        )
        city_polity = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchone()[0]
        for year, spec in YEARS.items():
            day = spec["day"]
            frame = pl.read_parquet(PARSED / f"{year}.parquet")
            failures = json.loads((PARSED / f"{year}-failures.json").read_text())
            contest, election, contest_key = conn.execute(
                """select c.id, c.election_id, x.value from contest c
                   join slug s on s.entity_id = c.id and s.path = %s
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' limit 1""",
                [f"us/ny/{day}-general/{spec['office']}"],
            ).fetchone()
            roots = {
                label: option
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null and kind = 'candidate'",
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
            pages = {}
            for page in sorted(frame["page"].unique()):
                image = PAGES / f"{spec['volume']}-{page}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                pages[page] = (cs.page(artifact, page), width, height)
                cs.add(
                    "source_page",
                    id=pages[page][0],
                    artifact_id=artifact,
                    page_number=page,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            options = {}
            for index, (name, party_key) in spec["columns"].items():
                root = roots.get(name)
                if root is None:
                    root_key = f"{contest_key}:{slug(name)}"
                    root = cs.key("us.option", root_key)
                    person = cs.key(f"{SOURCE}.person", slug(name))
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                    cs.add("contest_option", id=root, contest_id=contest, kind="candidate", ballot_label=name)
                    candidacy = cs.key("us.candidacy", f"{root_key}:head")
                    cs.add("candidacy", id=candidacy, person_id=person, option_id=root, role="head", ballot_name=name, is_independent=False)
                    for key in (party_key, spec["extra_party"].get(name)):
                        if key is not None:
                            cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=cs.key("us.party", key), kind="nominated")
                options[index] = root
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
            left = "".join(f" Note: assembly district {key} is left out because of {why}." for key, why in failures["left"].items())
            if failures["fixed"]:
                left += (
                    " Note: where a misread digit made an election district row miss its printed whole number of votes and the "
                    "assembly district's column miss its printed total by the same amount, the cell was set to the value that "
                    "satisfies both (" + "; ".join(failures["fixed"]) + ")."
                )
            if failures["off"]:
                left += (
                    " Note: in " + "; ".join(failures["off"]) + ", the printed whole number of votes differs from its columns, "
                    "which are loaded as printed because the assembly district's columns add up to its printed total."
                )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=spec["note"] + left)
            ads: dict[str, object] = {}
            units: dict[str, object] = {}
            count = 0
            for county, ad, ed, option, value, page in frame.select("county", "ad", "ed", "option", "value", "page").iter_rows():
                fips = "36005" if county == "New York" and ad >= 31 else COUNTY[county]
                ad_code = f"{fips}:{ad:02d}"
                if ad_code not in ads:
                    ads[ad_code] = cs.unit("us.nyc_ad", ad_code, day)
                    if ads[ad_code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=ads[ad_code],
                            polity_id=city_polity,
                            scheme_code="us.nyc_ad",
                            code=ad_code,
                            parent_id=cs.unit("us.county", fips),
                            area_relation="unlocated",
                            area_id=None,
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                if ed == 0:
                    unit = ads[ad_code]
                else:
                    code = f"{ad_code}:{ed:03d}"
                    if code not in units:
                        units[code] = cs.unit("us.nyc_ed", code, day)
                        if units[code] in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=city_polity,
                                scheme_code="us.nyc_ed",
                                code=code,
                                parent_id=ads[ad_code],
                                area_relation="unlocated",
                                area_id=None,
                                kind="standard",
                                valid_from=day,
                                valid_to=day + timedelta(days=1),
                            )
                    unit = units[code]
                fact = {"result_set_id": result_set, "unit_id": unit, "option_id": options[option], "channel": 0, "measure": 1, "count_round": 0}
                cs.add("result_value", **fact, flags=0, value=value)
                page_id, width, height = pages[page]
                cs.add("value_locator", **fact, page_id=page_id, source_id=None, x0=0, y0=0, x1=width, y1=height)
                count += 1
            print(year, len(ads), "assembly districts,", len(units), "election districts,", count, "values")
        return cs.write()
