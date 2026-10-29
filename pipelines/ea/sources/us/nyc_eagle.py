import itertools
import json
import re
import struct
import subprocess
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.nyc_redbook import number, ordinal
from ea.store import DATA, connect

SOURCE = "us.nyc_eagle"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "eagle"
COUNTY = {
    "MANHATTAN": "36061",
    "BRONX": "36005",
    "BROOKLYN": "36047",
    "QUEENS": "36081",
    "RICHMOND": "36085",
    "KINGS": "36047",
}
YEARS = {
    1901: {
        "volume": "eagle1902",
        "item": "brooklyndailyeag00unse_4",
        "pages": range(525, 528),
        "recap": None,
        "day": date(1901, 11, 5),
        "title": "Brooklyn Daily Eagle Almanac 1902",
    },
    1909: {
        "volume": "eagle1910",
        "item": "brooklyndailyeag00unse_0",
        "pages": range(554, 565),
        "recap": 569,
        "day": date(1909, 11, 2),
        "title": "Brooklyn Daily Eagle Almanac 1910",
    },
    1917: {
        "volume": "eagle1918",
        "item": "brooklyndailyeag00unse",
        "pages": range(511, 528),
        "recap": None,
        "day": date(1917, 11, 6),
        "title": "Brooklyn Daily Eagle Almanac 1918",
    },
}


def add_groups(groups: dict, path, page: int, borough: str) -> None:
    data = json.loads(path.read_text())
    for panel in data["panels"]:
        label = re.sub(r"(?i)assembly|dist(rict)?|continued|a\. ?d\.", "", panel["ad"])
        digits = re.search(r"\d+", label)
        ad = int(digits.group()) if digits else ordinal(label)
        if ad is None or not any(row["ed"] > 0 for row in panel["rows"]):
            continue
        group = groups.setdefault(
            (borough, ad),
            {
                "eds": {},
                "total": None,
                "width": len(data["columns"]),
                "page": page,
                "columns": data["columns"],
                "skip": {i for i, c in enumerate(data["columns"]) if "egistration" in c or "Reg." in c},
            },
        )
        for row in panel["rows"]:
            if row["ed"] == 0:
                continue
            group["eds"].setdefault(row["ed"], [number(cell) for cell in row["cells"]])
        if panel["total"]:
            group["total"] = [number(cell) for cell in panel["total"]]


def settle(reads: list, width: int):
    eds: dict[int, list] = {}
    for read in reads:
        for ed in read["eds"]:
            eds.setdefault(ed, [])
    totals = [read["total"] for read in reads if read["total"] is not None and len(read["total"]) == width]
    if not totals:
        return None, "no total row"
    settled = {ed: [None] * width for ed in eds}
    total = [None] * width
    off = []
    skip = set().union(*(read["skip"] for read in reads))
    for column in range(width):
        if column in skip:
            continue
        options = {}
        for ed in eds:
            counts = Counter(
                read["eds"][ed][column]
                for read in reads
                if ed in read["eds"] and len(read["eds"][ed]) == width and read["eds"][ed][column] is not None
            )
            if not counts:
                return None, "an unreadable or misaligned cell"
            options[ed] = counts
        target = {t[column] for t in totals if t[column] is not None}
        if not target:
            return None, "an unreadable or misaligned cell"
        free = [ed for ed in eds if len(options[ed]) > 1]
        if len(free) > 8 or any(len(options[ed]) > 3 for ed in free):
            return None, "too many disagreeing cells"
        base = sum(next(iter(options[ed])) for ed in eds if ed not in free)
        found = []
        for combo in itertools.product(*(list(options[ed]) for ed in free)):
            subtotal = base + sum(combo)
            if subtotal in target:
                found.append(
                    (
                        {ed: value for ed, value in zip(free, combo)},
                        subtotal,
                        sum(options[ed][value] for ed, value in zip(free, combo)),
                    )
                )
        if len(found) > 1:
            best = max(item[2] for item in found)
            found = [item for item in found if item[2] == best]
        if not found:
            top = {ed: options[ed].most_common(2) for ed in eds}
            if all(len(pair) == 1 or pair[0][1] > pair[1][1] for pair in top.values()):
                sums = sum(pair[0][0] for pair in top.values())
                printed = min(target, key=lambda value: abs(value - sums))
                if abs(printed - sums) <= max(10, printed // 100):
                    for ed in eds:
                        settled[ed][column] = top[ed][0][0]
                    total[column] = printed
                    off.append(f"column {column + 1} adds up to {sums:,} against a printed {printed:,}")
                    continue
        if len(found) != 1:
            return None, "rows that do not add up to the printed total" if not found else "more than one reading ties"
        picks, t, _ = found[0]
        for ed in eds:
            settled[ed][column] = picks[ed] if ed in picks else next(iter(options[ed]))
        total[column] = t
    return (settled, total, off), None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        volume = spec["volume"]
        kinds: dict[str, dict] = {"page": {}, "c": {}, "d": {}, "e": {}}
        borough = None
        for page in spec["pages"]:
            first = INBOX / f"{volume}-p{page}-{volume}.json"
            files = [("page", path) for path in [first] if path.exists()] + [
                (letter, path)
                for letter in "cde"
                for k in range(3)
                if (path := INBOX / f"{volume}-p{page}-{letter}{k}-{volume}{letter}.json").exists()
            ]
            for _, path in files:
                try:
                    title = json.loads(path.read_text())["title"].upper()
                except json.JSONDecodeError:
                    continue
                found = next((name for name in COUNTY if name in title), None)
                if found:
                    borough = found
            if borough is None:
                continue
            for kind, path in files:
                try:
                    add_groups(kinds[kind], path, page, borough)
                except json.JSONDecodeError:
                    print(year, "page", page, path.name, "truncated")
        accepted = []
        failures = {}
        offs = {}
        for key in sorted(set().union(*kinds.values())):
            group_reads = [kinds[kind][key] for kind in kinds if key in kinds[kind]]
            borough, ad = key
            totaled = {read["page"] for read in group_reads if read["total"] is not None}
            if totaled:
                group_reads = [read for read in group_reads if read["page"] in totaled]
            result, why = settle(group_reads, max(read["width"] for read in group_reads))
            if result is None:
                failures[f"{borough}:{ad}"] = why
            else:
                accepted.append((borough, ad, result[0], result[1], group_reads[0]["page"], group_reads[0]["columns"]))
                if result[2]:
                    offs[f"{borough}:{ad}"] = result[2]
        out = []
        for borough, ad, eds, total, page, columns in accepted:
            for ed, cells in eds.items():
                for index, value in enumerate(cells):
                    out.append(
                        {
                            "county": borough,
                            "ad": ad,
                            "ed": ed,
                            "column": index,
                            "value": value,
                            "page": page,
                            "header": columns[index],
                        }
                    )
            for index, value in enumerate(total):
                out.append(
                    {
                        "county": borough,
                        "ad": ad,
                        "ed": 0,
                        "column": index,
                        "value": value,
                        "page": page,
                        "header": columns[index],
                    }
                )
        pl.DataFrame(
            out,
            schema={
                "county": pl.String,
                "ad": pl.Int64,
                "ed": pl.Int64,
                "column": pl.Int64,
                "value": pl.Int64,
                "page": pl.Int64,
                "header": pl.String,
            },
        ).write_parquet(PARSED / f"{year}.parquet")
        (PARSED / f"{year}-failures.json").write_text(json.dumps({"left": failures, "off": offs}, indent=1))
        print(year, "assembly districts loaded", len(accepted), "left out", len(failures), failures)


PUBLISHER = "Brooklyn Daily Eagle"
PAGES = DATA / "pages" / SOURCE
NICKNAMES = {
    "KINGS": "Brooklyn",
    "BROOKLYN": "Brooklyn",
    "MANHATTAN": "Manhattan",
    "BRONX": "Bronx",
    "QUEENS": "Queens",
    "RICHMOND": "Staten Island",
}
MAYORS = {"bannard", "gaynor", "hearst", "hylan", "mitchel", "low", "shepard"}
COMPTROLLERS = {"prendergast", "craig"}
GARBLES = {"prndrgst": "prendergast"}
OFFICE_TITLES = {
    "mayor": "Mayor",
    "comptroller": "Comptroller",
    "president-board-of-aldermen": "President of the Board of Aldermen",
}


def column_of(header: str, year: int) -> tuple[str, str, str] | None:
    text = re.sub(r"\([^)]*\)", " ", header)
    office = None
    if re.search(r"(?i)\bmayor", text):
        office = "mayor"
    elif re.search(r"(?i)comp", text):
        office = "comptroller"
    elif re.search(r"(?i)boro|bor\. pr", text):
        office = "borough-president"
    elif re.search(r"(?i)aldermen|pres\. b\. ?a", text):
        office = "president-board-of-aldermen"
    text = re.sub(
        r"(?i)mayor:?|compt?(?:'r|\.|roller)?:?|pres\. of boro:?|pres\. boro\.?|bor\.? ?pr\.?|pres\. b\. ?a\.?|,? ?$",
        "",
        text,
    ).strip(" :.,")
    if not text or "egistration" in text:
        return None
    text = re.sub(r",\s*Jr\.?", "", text)
    text = re.sub(r"^(Civic All\.?|Civ\. Al\.?)\s+(\w.*)$", r"\2, \1", text)
    name, _, party = text.partition(",")
    tail = re.match(r"(.+?)\.?\s+((?:Rep|Dem|Fus)\S*)$", name)
    if tail:
        name, party = tail.group(1), f"{tail.group(2)} {party}".strip()
    if not party:
        match = re.match(r"(.+?)\s+((?:Rep|Dem|Fus|Soc|Civ|Prog|Proh|Ind|B\.H)\S*(?:\s.*)?)$", text)
        name, party = (match.group(1), match.group(2)) if match else (text, "")
    name = re.sub(r"[.,]?\s*Jr\.?$", "", name.strip()).strip(" .,")
    party = party.strip(" .,")
    surname = re.sub(r"[^a-z]", "", name.split()[-1].lower()) if name else ""
    surname = GARBLES.get(surname, surname)
    if office is None:
        if surname in MAYORS or (surname == "cassidy" and party.lower().startswith("soc")):
            office = "mayor"
        elif surname in COMPTROLLERS:
            office = "comptroller"
        else:
            office = "borough-president"
    return office, name, party


def emit():
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's assembly district and election district returns from the Brooklyn Daily Eagle Almanac",
            polity="US",
        )
        city_polity = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchone()[0]
        place = cs.area("us.place", "3651000")
        system = cs.key("us.electoral_system", "fptp")
        county_areas = {code: cs.area("us.county", code) for code in COUNTY.values()}
        for year, spec in YEARS.items():
            day = spec["day"]
            frame = pl.read_parquet(PARSED / f"{year}.parquet").filter(pl.col("value").is_not_null())
            if frame.is_empty():
                continue
            failures = json.loads((PARSED / f"{year}-failures.json").read_text())
            election = conn.execute(
                "select c.election_id from contest c join slug s on s.entity_id = c.id and s.path = %s",
                [f"us/ny/new-york-city/{day}-local/mayor"],
            ).fetchone()[0]
            item = spec["item"]
            url = f"https://archive.org/details/{item}"
            source = cs.source(PUBLISHER, spec["title"])
            cs.add(
                "source",
                id=source,
                kind="compendium",
                publisher=PUBLISHER,
                title=spec["title"],
                url=url,
                license="Public domain",
                notes="Scanned volume on the Internet Archive.",
            )
            pdf = INBOX / f"{spec['volume']}.pdf"
            artifact = cs.artifact(
                raw.store(
                    SOURCE,
                    f"https://archive.org/download/{item}/{item}.pdf",
                    pdf.read_bytes(),
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                ),
                source,
            )
            pages = {}
            for page in sorted(frame["page"].unique()):
                image = PAGES / f"{spec['volume']}-{page}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(page),
                            "-l",
                            str(page),
                            "-singlefile",
                            str(pdf),
                            str(image.with_suffix("")),
                        ],
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
            contests = {}
            options = {}
            ads: dict[str, object] = {}
            units: dict[str, object] = {}
            count = 0
            columns = {}
            for county, column, header in (
                frame.group_by("county", "column").agg(pl.col("header").mode().first()).iter_rows()
            ):
                columns[(county, column)] = column_of(header, year)
            for county, ad, ed, column, value, page in frame.select(
                "county", "ad", "ed", "column", "value", "page"
            ).iter_rows():
                parsed = columns[(county, column)]
                if parsed is None:
                    continue
                office, name, party = parsed
                nickname = NICKNAMES[county]
                office_slug = f"borough-president-{slug(nickname)}" if office == "borough-president" else office
                if office_slug not in contests:
                    path = f"us/ny/new-york-city/{day}-local/{office_slug}"
                    found = conn.execute(
                        "select c.id, x.value from contest c join slug s on s.entity_id = c.id and s.path = %s join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'",
                        [path],
                    ).fetchone()
                    if found is not None:
                        contest, contest_key = found
                    else:
                        contest_key = f"{SOURCE}:{day}:NY:3651000:{office_slug}"
                        contest = cs.key("us.contest", contest_key)
                        office_key = (
                            f"new-york-city:borough-president:{slug(nickname)}"
                            if office == "borough-president"
                            else f"new-york-city:{office}"
                        )
                        title = (
                            f"{nickname} Borough President" if office == "borough-president" else OFFICE_TITLES[office]
                        )
                        cs.add(
                            "contest",
                            id=contest,
                            election_id=election,
                            kind="office",
                            office_id=cs.key("us.office", office_key),
                            district_area_id=county_areas[COUNTY[county]] if office == "borough-president" else place,
                            magnitude=1,
                            round_number=1,
                            electoral_system_id=system,
                            default_result_kind="counted",
                            term="full",
                        )
                        cs.add(
                            "name",
                            entity_id=contest,
                            text=f"{year} New York City {title}",
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                        cs.add("slug", path=path, entity_id=contest)
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
                    left = "".join(
                        f" Note: assembly district {key} is left out because of {why}."
                        for key, why in failures["left"].items()
                    )
                    left += "".join(
                        f" Note: in assembly district {key}, {'; '.join(why)}; the election district rows are loaded as printed."
                        for key, why in failures.get("off", {}).items()
                    )
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="methodology",
                        body_md=f"Election district and assembly district returns transcribed from the {spec['title']}, a compendium that prints only assembly district totals and omits minor-party lines. A district is loaded only when every column of its election districts adds up to the printed assembly district total, or to within a few votes of it where the almanac's own total does not add up and every reading of the page agrees."
                        + left,
                    )
                    contests[office_slug] = (contest, contest_key, result_set, {})
                contest, contest_key, result_set, roots = contests[office_slug]
                token = slug(re.sub(r"\W+", " ", name))
                if (office_slug, county, column) not in options:
                    surname = re.sub(r"[^a-z]", "", name.split()[-1].lower())
                    surname = GARBLES.get(surname, surname)
                    if not roots:
                        for option, label in conn.execute(
                            "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null and kind = 'candidate'",
                            [contest],
                        ).fetchall():
                            roots[re.sub(r"[^a-z]", "", label.split()[-1].lower())] = option
                    if surname not in roots:
                        root_key = f"{contest_key}:{token}"
                        root = cs.key("us.option", root_key)
                        person = cs.key(f"{SOURCE}.person", f"{day}:{token}")
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                        cs.add(
                            "contest_option",
                            id=root,
                            contest_id=contest,
                            kind="candidate",
                            ballot_label=f"{name} ({party})" if party else name,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{root_key}:head"),
                            person_id=person,
                            option_id=root,
                            role="head",
                            ballot_name=name,
                            is_independent=False,
                        )
                        roots[surname] = root
                    options[(office_slug, county, column)] = roots[surname]
                ad_code = f"{COUNTY[county]}:{ad:02d}"
                if ad_code not in ads:
                    ads[ad_code] = cs.unit("us.nyc_ad", ad_code, day)
                    if ads[ad_code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=ads[ad_code],
                            polity_id=city_polity,
                            scheme_code="us.nyc_ad",
                            code=ad_code,
                            parent_id=cs.unit("us.county", COUNTY[county]),
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
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": options[(office_slug, county, column)],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=value)
                page_id, width, height = pages[page]
                cs.add("value_locator", **fact, page_id=page_id, source_id=None, x0=0, y0=0, x1=width, y1=height)
                count += 1
            print(year, len(ads), "assembly districts,", len(units), "election districts,", count, "values")
        return cs.write()
