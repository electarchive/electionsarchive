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
    1903: {
        "volume": "eagle1905",
        "item": "brooklyndailyeag00unse_5",
        "pages": [636, 638, 645],
        "recap": None,
        "day": date(1903, 11, 3),
        "title": "Brooklyn Daily Eagle Almanac 1905",
        "table": [
            {"page": 636, "county": "BROOKLYN", "ads": 21, "columns": range(1, 9)},
            {"page": 645, "county": "QUEENS", "ads": 3, "columns": range(1, 9)},
        ],
    },
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


def settle(reads: list, width: int, strict: bool = False):
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
        if not found and not strict:
            tied = [
                ed
                for ed in eds
                if len(options[ed]) > 1 and options[ed].most_common(2)[0][1] == options[ed].most_common(2)[1][1]
            ]
            if len(tied) <= 8:
                fixed = sum(options[ed].most_common(1)[0][0] for ed in eds if ed not in tied)
                printed = min(target)
                scored = []
                for combo in itertools.product(
                    *(sorted(k for k, v in options[ed].items() if v == options[ed].most_common(1)[0][1]) for ed in tied)
                ):
                    sums = fixed + sum(combo)
                    scored.append((abs(sums - printed), sums, dict(zip(tied, combo))))
                scored.sort(key=lambda item: item[0])
                if (
                    scored
                    and abs(scored[0][1] - printed) <= max(10, printed // 100)
                    and (len(scored) == 1 or scored[1][0] > scored[0][0])
                ):
                    picks = scored[0][2]
                    for ed in eds:
                        settled[ed][column] = picks[ed] if ed in picks else options[ed].most_common(1)[0][0]
                    total[column] = printed
                    off.append(f"column {column + 1} adds up to {scored[0][1]:,} against a printed {printed:,}")
                    continue
        if len(found) != 1:
            return None, "rows that do not add up to the printed total" if not found else "more than one reading ties"
        picks, t, _ = found[0]
        for ed in eds:
            settled[ed][column] = picks[ed] if ed in picks else next(iter(options[ed]))
        total[column] = t
    return (settled, total, off), None


HAND_READ = {
    1903: [("MANHATTAN", 638, {
        1: (
            "Mayor S. Low Frs. 1903",
            [949,1521,1998,2297,4243,1765,2616,1498,3003,2429,1953,1730,1724,2137,2340,2399,2313,2125,6916,2710,11271,2449,9203,2261,4153,2517,3852,2265,6122,3172,10296,3470,2935,6581,11160],
        ),
        2: (
            "Mayor G.B. McClellan Dem. 1903",
            [3200,4918,4869,3565,3715,5614,5031,2716,4465,3812,3910,3190,3977,4699,4694,3928,4609,6561,5264,5050,8117,5563,8782,4940,2762,3928,2565,5123,3841,6304,7740,6904,5246,11522,14783],
        ),
        3: (
            "Comptroller F.W. Hinrichs Frs. 1903",
            [932,1502,1989,2237,4172,1736,2633,1452,3005,2380,1949,1736,1720,2122,2320,2339,2298,2111,6832,2725,11184,2449,9116,2208,4127,2476,3842,2237,6045,3121,10114,3407,2910,6528,10986],
        ),
        4: (
            "Comptroller E.M. Grout Dem. 1903",
            [3200,4912,4857,3564,3758,5606,5011,2715,4468,3792,3903,3152,3944,4687,4704,3930,4598,6520,5288,5023,8174,5554,8814,4972,2807,3931,2562,5113,3901,6293,7835,6903,5258,11510,14863],
        ),
    })],
    1901: [("QUEENS", 534, {
        1: ("Mayor S. Low Fus.", [6495, 6086, 537]),
        2: ("Mayor E.M. Shepard Dem.", [8465, 4486, 728]),
        3: ("Comptroller E.M. Grout Fus.", [6459, 6109, 538]),
        4: ("Comptroller W.W. Ladd Dem.", [8390, 4385, 727]),
        5: ("Pres. Bd. of Aldermen C.V. Fornes Fus.", [6248, 5974, 521]),
        6: ("Pres. Bd. of Aldermen G.M. Van Hoesen Dem.", [8557, 4495, 739]),
        7: ("Borough Pres. J. Cassidy Dem.", [9114, 4456, 814]),
    }), ("RICHMOND", 535, {
        1: ("Mayor S. Low Fus.", [2229, 1313, 1437, 873, 920]),
        2: ("Mayor E.M. Shepard Dem.", [1947, 1315, 1154, 864, 729]),
        3: ("Comptroller E.M. Grout Fus.", [2245, 1329, 1432, 901, 925]),
        4: ("Pres. Bd. of Aldermen C.V. Fornes Fus.", [2210, 1308, 1420, 890, 904]),
        5: ("Pres. Bd. of Aldermen G.M. Van Hoesen Dem.", [1940, 1306, 1158, 846, 721]),
        6: ("Borough Pres. G. Cromwell Fus.", [2110, 1284, 1406, 949, 884]),
    })],
}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year, spec in YEARS.items():
        volume = spec["volume"]
        if spec.get("table"):
            out = []
            for table in spec["table"]:
                reads = []
                for path in sorted(INBOX.glob(f"{volume}-p{table['page']}-*.json")):
                    try:
                        data = json.loads(path.read_text())
                    except json.JSONDecodeError:
                        continue
                    ads = {}
                    total = None
                    for panel in data["panels"]:
                        label = panel["ad"].strip()
                        if label.isdigit() and len(panel["rows"]) == 1:
                            ads[int(label)] = [number(cell) for cell in panel["rows"][0]["cells"]]
                        elif re.match(r"(?i)(totals?|tls)", label) and panel["rows"]:
                            total = [number(cell) for cell in panel["rows"][0]["cells"]]
                        else:
                            for row in panel["rows"]:
                                ads[row["ed"] or 99] = [number(cell) for cell in row["cells"]]
                            if panel["total"]:
                                total = [number(cell) for cell in panel["total"]]
                    width = len(data["columns"])
                    ads = {ad: cells for ad, cells in ads.items() if len(cells) == width}
                    if total is not None and len(total) == width and len(ads) == table["ads"]:
                        reads.append(
                            {
                                "eds": ads,
                                "total": total,
                                "skip": {i for i in range(width) if i not in table["columns"]},
                                "width": width,
                                "page": table["page"],
                                "columns": data["columns"],
                            }
                        )
                if not reads:
                    print(year, table["page"], "no complete reads")
                    continue
                result, why = settle(reads, max(read["width"] for read in reads), strict=True)
                if result is None:
                    print(year, table["page"], why)
                    continue
                header = [Counter(column).most_common(1)[0][0] for column in zip(*[read["columns"] for read in reads])]
                for ad, row in result[0].items():
                    if ad == 99:
                        continue
                    county = table["county"]
                    for index in table["columns"]:
                        if row[index] is not None:
                            out.append(
                                {
                                    "county": county,
                                    "ad": ad,
                                    "ed": 0,
                                    "column": index,
                                    "value": row[index],
                                    "page": table["page"],
                                    "header": header[index],
                                }
                            )
                print(year, table["page"], "assembly districts loaded", len(result[0]), "from", len(reads), "reads")
            for hand_county, hand_page, hand_columns in HAND_READ.get(year, []):
                out.extend(
                    {"county": hand_county, "ad": ad, "ed": 0, "column": column, "value": value, "page": hand_page, "header": header}
                    for column, (header, values) in hand_columns.items()
                    for ad, value in enumerate(values, 1)
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
            (PARSED / f"{year}-failures.json").write_text(json.dumps({"left": {}, "off": {}}))
            continue
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
        for hand_county, hand_page, hand_columns in HAND_READ.get(year, []):
            out.extend(
                {"county": hand_county, "ad": ad, "ed": 0, "column": column, "value": value, "page": hand_page, "header": header}
                for column, (header, values) in hand_columns.items()
                for ad, value in enumerate(values, 1)
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
ALL_ADS = {1909: 64, 1917: 74}
PAGES = DATA / "pages" / SOURCE
NICKNAMES = {
    "KINGS": "Brooklyn",
    "BROOKLYN": "Brooklyn",
    "MANHATTAN": "Manhattan",
    "BRONX": "Bronx",
    "QUEENS": "Queens",
    "RICHMOND": "Staten Island",
}
MAYORS = {"bannard", "gaynor", "hearst", "hylan", "mitchel", "low", "shepard", "mclellan"}
COMPTROLLERS = {"prendergast", "craig", "hinrichs", "hinricks", "grout", "ladd"}
ALDERMEN = {"mcguire", "fornes", "pornes"}
GARBLES = {"prndrgst": "prendergast"}
OFFICE_TITLES = {
    "mayor": "Mayor",
    "comptroller": "Comptroller",
    "president-board-of-aldermen": "President of the Board of Aldermen",
}


def column_of(header: str, year: int) -> tuple[str, str, str] | None:
    text = re.sub(r"\s+(?:18|19)\d\d$", "", re.sub(r"\([^)]*\)", " ", header).strip())
    office = None
    if re.search(r"(?i)\bmayor", text):
        office = "mayor"
    elif re.search(r"(?i)comp", text):
        office = "comptroller"
    elif re.search(r"(?i)boro|bor\. pr", text):
        office = "borough-president"
    elif re.search(r"(?i)aldermen|pres\. b[.d]", text):
        office = "president-board-of-aldermen"
    text = re.sub(
        r"(?i)mayor:?|compt?(?:'r|\.|roller)?:?|borough pres\.?|pres\. of boro:?|pres\. boro\.?|bor\.? ?pr\.?|pres\. bd\. of ald(?:ermen|\.)?|pres\. b\. ?a\.?|,? ?$",
        "",
        text,
    ).strip(" :.,")
    if not text or "egistration" in text:
        return None
    text = re.sub(r",\s*Jr\.?", "", text)
    text = re.sub(r"^(Civic All\.?|Civ\. Al\.?)\s+(\w.*)$", r"\2, \1", text)
    name, _, party = text.partition(",")
    tail = re.match(r"(.+?)\.?\s+((?:Rep|Dem|Fus|Frs)\S*)$", name)
    if tail:
        name, party = tail.group(1), f"{tail.group(2)} {party}".strip()
    if not party:
        match = re.match(r"(.+?)\s+((?:Rep|Dem|Fus|Frs|Soc|Civ|Prog|Proh|Ind|B\.H)\S*(?:\s.*)?)$", text)
        name, party = (match.group(1), match.group(2)) if match else (text, "")
    name = re.sub(r"[.,]?\s*Jr\.?$", "", name.strip()).strip(" .,").replace("Pornes", "Fornes")
    party = party.strip(" .,").replace("Frs", "Fus")
    surname = re.sub(r"[^a-z]", "", name.split()[-1].lower()) if name else ""
    surname = GARBLES.get(surname, surname)
    if office is None:
        if surname in MAYORS or (surname == "cassidy" and party.lower().startswith("soc")):
            office = "mayor"
        elif surname in COMPTROLLERS:
            office = "comptroller"
        elif surname in ALDERMEN:
            office = "president-board-of-aldermen"
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
            if not (PARSED / f"{year}.parquet").exists():
                continue
            frame = pl.read_parquet(PARSED / f"{year}.parquet").filter(pl.col("value").is_not_null())
            if frame.is_empty():
                continue
            failures = json.loads((PARSED / f"{year}-failures.json").read_text())
            found = conn.execute(
                "select c.election_id from contest c join slug s on s.entity_id = c.id and s.path = %s",
                [f"us/ny/new-york-city/{day}-local/mayor"],
            ).fetchone()
            if found is not None:
                election = found[0]
            else:
                parent = cs.key("us.election", f"{day}:NY")
                election = cs.key("us.election", f"{day}:NY:3651000")
                ((polity, polity_path),) = conn.execute(
                    "select o.polity_id, s.path from office o join slug s on s.entity_id = o.polity_id and s.is_current "
                    "where o.id = %s",
                    [cs.key("us.office", "NY:3651000:mayor")],
                ).fetchall()
                if parent in cs.minted:
                    cs.add(
                        "election",
                        id=parent,
                        polity_id=cs.polity("US-NY"),
                        kind="local",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    cs.add(
                        "name",
                        entity_id=parent,
                        text=f"{year} New York local elections",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    cs.add("slug", path=f"us/ny/{day}-local", entity_id=parent)
                if election in cs.minted:
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=parent,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    cs.add(
                        "name",
                        entity_id=election,
                        text=f"{year} New York City local election",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    cs.add("slug", path=f"{polity_path}/{day}-local", entity_id=election)
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
            loaded = {
                county: count for county, count in frame.unique(["county", "ad"]).group_by("county").len().iter_rows()
            }
            attempted_left = {
                county: sum(1 for key in failures["left"] if key.split(":")[0] == county) for county in loaded
            }
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
                            else "NY:3651000:mayor"
                            if office == "mayor"
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
                        completeness=None
                        if office != "borough-president" and year not in ALL_ADS
                        else round(
                            loaded[county] / (loaded[county] + attempted_left[county])
                            if office == "borough-president"
                            else min(1, sum(loaded.values()) / ALL_ADS[year]),
                            3,
                        ),
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
                        body_md=(
                            f"Assembly district returns for {county.title()} transcribed from the {spec['title']}, which prints one row per assembly district and a borough total row. Every column loaded adds up exactly to the printed borough total across three independent readings of the page; the almanac prints only the Fusion and Democratic lines for each office, so minor-party candidates are absent."
                            if spec.get("table")
                            else f"Election district and assembly district returns transcribed from the {spec['title']}, a compendium that prints only assembly district totals and omits minor-party lines. A district is loaded only when every column of its election districts adds up to the printed assembly district total, or to within a few votes of it where the almanac's own total does not add up and every reading of the page agrees."
                        )
                        + (
                            " Only the Brooklyn tables, the Queens assembly district totals (every column but Dohl for borough president adds up exactly to the printed borough total), and the Richmond ward totals were read, so the citywide sets carry no completeness. Richmond is loaded by ward; the columns that add up to the printed borough total are in, and Seth Low's first-ward total is the 2,229 that the twelve election district rows and the borough total both give, where the ward row prints 2,269."
                            if year == 1901
                            else " The Brooklyn and Queens tables tie to their printed totals on every column. The Manhattan and Bronx table (35 assembly districts of New York County, the annexed district left out) was read by eye on the page and loaded only for the mayor and comptroller columns, each of which adds up exactly to the printed total; its other columns do not tie and are left out, so the citywide sets carry no completeness."
                            if year == 1903
                            else ""
                        )
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
                ad_scheme = "us.nyc_ward" if county == "RICHMOND" else "us.nyc_ad"
                if ad_code not in ads:
                    ads[ad_code] = cs.unit(ad_scheme, ad_code, day)
                    if ads[ad_code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=ads[ad_code],
                            polity_id=city_polity,
                            scheme_code=ad_scheme,
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
