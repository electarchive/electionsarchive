import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie.referendums import act, local
from ea.store import DATA, connect

SOURCE = "ie.presidential"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BOOKLET = "https://www.presidentialelection.ie/wp-content/uploads/2026/01/Presidential-Elections-1938-2025.pdf"
PAGE = "https://www.presidentialelection.ie/previous-presidential-elections/index.html"
PUBLISHER = "Presidential Returning Officer"
TITLE = "Toghcháin Uachtaráin 1938–2025 Presidential Elections"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21
MALE, FEMALE = "male", "female"
PEOPLE = {
    "hyde": ("Douglas Hyde", "Q237951", None, MALE),
    "mccartan": ("Patrick McCartan", "Q7147125", None, MALE),
    "maceoin": ("Seán Mac Eoin", "Q1700553", None, MALE),
    "okelly": ("Seán T. O'Kelly", "Q327203", None, MALE),
    "devalera": ("Éamon de Valera", "Q173347", None, MALE),
    "ohiggins": ("Tom O'Higgins", "Q1770312", None, MALE),
    "childers": ("Erskine H. Childers", "Q356450", None, MALE),
    "odalaigh": ("Cearbhall Ó Dálaigh", "Q380894", None, MALE),
    "hillery": ("Patrick Hillery", "Q310994", None, MALE),
    "currie": ("Austin Currie", "Q3545632", "2002:dublin-mid-west:austincurrie", MALE),
    "lenihan": ("Brian Lenihan", "Q912542", None, MALE),
    "robinson": ("Mary Robinson", "Q188214", None, FEMALE),
    "banotti": ("Mary Banotti", "Q6779006", None, FEMALE),
    "mcaleese": ("Mary McAleese", "Q174662", None, FEMALE),
    "nally": ("Derek Nally", "Q5262220", None, MALE),
    "roche": ("Adi Roche", "Q4682618", None, FEMALE),
    "scallon": ("Dana Rosemary Scallon", "Q237615", "2002:galway-west:danarosemaryscallon", FEMALE),
    "davis": ("Mary Davis", "Q3296167", None, FEMALE),
    "gallagher": ("Seán Gallagher", "Q984574", None, MALE),
    "higgins": ("Michael D. Higgins", "Q57276", "2007:galway-west:michaeldhiggins", MALE),
    "mcguinness": ("Martin McGuinness", "Q57689", None, MALE),
    "mitchell": ("Gay Mitchell", "Q1405662", "2002:dublin-south-central:gaymitchell", MALE),
    "norris": ("David Norris", "Q1175932", None, MALE),
    "casey": ("Peter Casey", "Q16188918", "2020:donegal:petercasey", MALE),
    "duffy": ("Gavin Duffy", "Q19519903", None, MALE),
    "freeman": ("Joan Freeman", "Q25184305", None, FEMALE),
    "niriada": ("Liadh Ní Riada", "Q16980118", None, FEMALE),
    "connolly": ("Catherine Connolly", "Q5052598", "2024:galway-west:catherineconnolly", FEMALE),
    "gavin": ("Jim Gavin", "Q6195174", None, MALE),
    "humphreys": ("Heather Humphreys", "Q5693903", "2020:cavan-monaghan:heatherhumphreys", FEMALE),
}
TACIT = {
    1938: (
        date(1938, 5, 4),
        "hyde",
        "Dubhghlas de hÍde",
        (
            "The Presidential Returning Officer's certificate of 4 May 1938, read to Seanad Éireann on 11 May 1938 "
            "(https://www.oireachtas.ie/en/debates/debate/seanad/1938-05-11/2/), declares him elected as the only "
            "candidate duly nominated."
        ),
    ),
    1952: (
        date(1952, 5, 16),
        "okelly",
        "Seán T O'Kelly",
        (
            "The Ceann Comhairle told Dáil Éireann on 27 May 1952 that the Presidential Returning Officer had declared him "
            "elected on 16 May 1952 (https://www.oireachtas.ie/en/debates/debate/dail/1952-05-27/46/)."
        ),
    ),
    1974: (
        date(1974, 12, 3),
        "odalaigh",
        "Cearbhall Ó Dálaigh",
        (
            "The Cathaoirleach told Seanad Éireann on 4 December 1974 that the Presidential Returning Officer had declared "
            "him elected on 3 December 1974 (https://www.oireachtas.ie/en/debates/debate/seanad/1974-12-04/2/)."
        ),
    ),
    1976: (
        date(1976, 11, 9),
        "hillery",
        "Patrick Hillery",
        (
            "The Ceann Comhairle told Dáil Éireann on 9 November 1976 that the Presidential Returning Officer had that day "
            "declared him elected (https://www.oireachtas.ie/en/debates/debate/dail/1976-11-09/38/)."
        ),
    ),
    1983: (
        date(1983, 10, 21),
        "hillery",
        "Patrick Hillery",
        (
            "The Ceann Comhairle told Dáil Éireann on 25 October 1983 that the Presidential Returning Officer had declared "
            "him elected on 21 October 1983 (https://www.oireachtas.ie/en/debates/debate/dail/1983-10-25/18/)."
        ),
    ),
    2004: (
        date(2004, 10, 1),
        "mcaleese",
        "Mary McAleese",
        (
            "The Minister's order of 13 September 2004 fixed 1 October 2004 as the last day for receiving nominations, the "
            "day the Presidential Returning Officer rules on them and declares a sole candidate elected."
        ),
    ),
}
CONTESTED = {
    1945: (date(1945, 6, 14), ["mccartan", "maceoin", "okelly"], "okelly", {22: 1, 23: 2}, {2: ["mccartan"]}),
    1959: (date(1959, 6, 17), ["devalera", "maceoin"], "devalera", {25: 1}, {}),
    1966: (date(1966, 6, 1), ["devalera", "ohiggins"], "devalera", {27: 1}, {}),
    1973: (date(1973, 5, 30), ["childers", "ohiggins"], "childers", {29: 1}, {}),
    1990: (date(1990, 11, 7), ["currie", "lenihan", "robinson"], "robinson", {32: 1, 33: 2}, {2: ["currie"]}),
    1997: (
        date(1997, 10, 30),
        ["banotti", "mcaleese", "nally", "roche", "scallon"],
        "mcaleese",
        {35: 1, 36: 2},
        {2: ["nally", "roche", "scallon"]},
    ),
    2011: (
        date(2011, 10, 27),
        ["davis", "gallagher", "higgins", "mcguinness", "mitchell", "norris", "scallon"],
        "higgins",
        {39: 1, 40: 1, 41: 2, 42: 2, 43: 3, 44: 3, 45: 4, 46: 4},
        {2: ["davis", "scallon"], 3: ["norris"], 4: ["mitchell", "mcguinness"]},
    ),
    2018: (
        date(2018, 10, 26),
        ["casey", "duffy", "freeman", "gallagher", "higgins", "niriada"],
        "higgins",
        {49: 1, 50: 1},
        {},
    ),
    2025: (date(2025, 10, 24), ["connolly", "gavin", "humphreys"], "connolly", {53: 1}, {}),
}
SPECIAL = {1974, 1976}
TENURES = (
    ("hyde", 1938, date(1938, 6, 25), date(1945, 6, 25), "term_end"),
    ("okelly", 1945, date(1945, 6, 25), date(1959, 6, 25), "term_end"),
    ("devalera", 1959, date(1959, 6, 25), date(1973, 6, 25), "term_end"),
    ("childers", 1973, date(1973, 6, 25), date(1974, 11, 17), "died"),
    ("odalaigh", 1974, date(1974, 12, 19), date(1976, 10, 22), "resigned"),
    ("hillery", 1976, date(1976, 12, 3), date(1990, 12, 3), "term_end"),
    ("robinson", 1990, date(1990, 12, 3), date(1997, 9, 12), "resigned"),
    ("mcaleese", 1997, date(1997, 11, 11), date(2011, 11, 11), "term_end"),
    ("higgins", 2011, date(2011, 11, 11), date(2025, 11, 11), "term_end"),
    ("connolly", 2025, date(2025, 11, 11), None, None),
)
BALLOT = {
    "mccartan": "Patrick McCartan",
    "maceoin": "Seán MacEoin",
    "okelly": "Seán T O'Kelly",
    "devalera": "Eamon de Valera",
    "ohiggins": "Thomas F O'Higgins",
    "childers": "Erskine Childers",
    "currie": "Austin Currie",
    "lenihan": "Brian Lenihan",
    "robinson": "Mary Robinson",
    "banotti": "Mary Banotti",
    "mcaleese": "Mary McAleese",
    "nally": "Derek Nally",
    "roche": "Adi Roche",
    "scallon": "Dana Rosemary Scallon",
    "davis": "Mary Davis",
    "gallagher": "Seán Gallagher",
    "higgins": "Michael D. Higgins",
    "mcguinness": "Martin McGuinness",
    "mitchell": "Gay Mitchell",
    "norris": "David Norris",
    "casey": "Peter Joseph Casey",
    "duffy": "Gavin Duffy",
    "freeman": "Joan Freeman",
    "niriada": "Liadh Ní Riada",
    "connolly": "Catherine Martina Ann Connolly",
    "gavin": "Jim Gavin",
    "humphreys": "Heather Humphreys",
}
CORRECTIONS = {
    (1973, "South Tipperary"): (
        "poll",
        36544,
        (
            "The booklet prints South Tipperary's total poll as 36,554. Its votes for the two candidates and its spoilt "
            "votes add up to 36,544, as does its national total poll of 1,230,584, so 36,544 is used here."
        ),
    ),
}
COUNTY_NAMES = {
    "Laoighis": "Laois",
    "Tipperary North Riding": "North Tipperary",
    "Tipperary South Riding": "South Tipperary",
}
LIMERICK = "Limerick (including County Borough)"
DAIL_NAMES = {(1961, "Cork Borough"): "Cork", (1961, "Dublin County"): "Dublin"}
NUMBER = re.compile(r"-|\(?[+-]?\d[\d,]*(?:\.\d+)?%?\)?")
HEADER = re.compile(r"^\s*[\(\[]\d+[\)\]]")
SUMMARY = {
    "electorate": r"Electorate:\s+([\d,]+)",
    "valid": r"Total Valid Poll:\s+([\d,]+)",
    "invalid": r"(?:Invalid|Spoiled) votes:\s+([\d,]+)",
    "poll": r"Total poll:\s+([\d,]+)",
    "quota": r"Quota:\s+([\d,]+)",
}
METHODOLOGY = (
    "Each counting area's figures as the Presidential Returning Officer's booklet Toghcháin Uachtaráin 1938–2025 "
    "Presidential Elections prints them (pages {pages}): {measures}, and each candidate's running total at every count, "
    "with the non-transferable papers. Valid votes are the total poll less the {invalid} votes. The national quota was "
    "{quota:,}."
)
COUNTY_1945 = (
    "The 1945 poll was counted by county and county borough, because local elections were held the same day. The "
    "booklet gives no electorate below the national figure of 1,803,463. At the second count it reports Limerick "
    "county and Limerick county borough together, so that count is stored for the present-day Limerick City and County "
    "area, which covers both."
)
SYSTEM = (
    "The President is elected by direct vote of the people under the single transferable vote, which for a single seat "
    "works as an instant runoff (Article 12 of the Constitution). The quota is half the valid poll plus one, with any "
    "fraction ignored. When no candidate reaches the quota, the lowest candidate is excluded, or two or more of the "
    "lowest together when their combined votes cannot overtake the next candidate, and their ballots pass to the next "
    "preference. When only one candidate is nominated, the Presidential Returning Officer declares that candidate "
    "elected without a poll."
)
TACIT_NOTE = (
    "Only one candidate was nominated, and the Presidential Returning Officer declared them elected without a poll. "
)


def fetch() -> None:
    raw.fetch(SOURCE, BOOKLET, suffix=".pdf")


def number(text: str) -> int:
    if text == "-":
        return 0
    return int(text.strip("()+").replace(",", ""))


def summary(text: str) -> dict[str, int]:
    found = {}
    for key, pattern in SUMMARY.items():
        match = re.search(pattern, text)
        if match:
            found[key] = number(match[1])
    return found


def parse() -> None:
    path = raw.cached(SOURCE, BOOKLET).path
    pages = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
    ).stdout.split("\f")
    rows: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    overview: list[dict[str, Any]] = []
    for year, (day, names, _, tables, excluded) in CONTESTED.items():
        heading = next(
            i for i, page in enumerate(pages) if f"Presidential Election {year}\n" in page and "Voting took" in page
        )
        figures = summary(pages[heading])
        polled = re.search(r"Voting took place on \w+,? (\d+)\w* (\w+),? (\d{4})", re.sub(r"\s+", " ", pages[heading]))
        if polled is None or f"{polled[1]} {polled[2]} {polled[3]}" != f"{day.day} {day:%B %Y}":
            raise ValueError(f"{year}: the booklet gives polling day {polled and polled.group(0)}, not {day}")
        overview.append({"year": year, "page": heading + 1, **figures})
        running = list(names)
        for page_number, count in tables.items():
            for gone in excluded.get(count, []):
                if gone in running:
                    running.remove(gone)
            lines = pages[page_number - 1].splitlines()
            group = None
            for index, line in enumerate(lines):
                stripped = line.strip()
                if stripped in ("County Boroughs", "Counties"):
                    group = stripped
                    continue
                if HEADER.match(line):
                    continue
                tokens = line.split()
                cut = len(tokens)
                while cut and NUMBER.fullmatch(tokens[cut - 1]):
                    cut -= 1
                values = [t for t in tokens[cut:] if "." not in t and "%" not in t]
                label = " ".join(tokens[:cut])
                if len(values) < 3:
                    continue
                if not label:
                    before = lines[index - 1].strip()
                    after = lines[index + 1].strip() if index + 1 < len(lines) else ""
                    label = f"{before} {after}"
                elif (
                    index and (before := lines[index - 1].strip()).endswith(("-", "&")) and not re.search(r"\d", before)
                ):
                    label = f"{before} {label}"
                elif label.endswith((" and", "&", "-")) and index + 1 < len(lines):
                    label = f"{label} {lines[index + 1].strip()}"
                label = re.sub(r"\s+", " ", re.sub(r"-\s+", "-", label)).strip()
                if count == 1:
                    width = len(names) + (2 if year == 1945 else 3)
                else:
                    width = 2 * len(running) + 1
                if len(values) != width:
                    raise ValueError(f"{year} page {page_number}: {label!r} has {len(values)} figures, not {width}")
                numbers = [number(v) for v in values]
                if count == 1:
                    offset = 0 if year == 1945 else 1
                    record: dict[str, Any] = {
                        "electorate": numbers[0] if offset else None,
                        "poll": numbers[offset],
                        "invalid": numbers[-1],
                        "votes": dict(zip(names, numbers[offset + 1 : -1], strict=True)),
                    }
                else:
                    record = {
                        "transfers": dict(zip(running, numbers[0:-1:2], strict=True)),
                        "votes": dict(zip(running, numbers[1:-1:2], strict=True)),
                        "exhausted": numbers[-1],
                    }
                remark = None
                if count == 1 and (year, label) in CORRECTIONS:
                    column, value, remark = CORRECTIONS[year, label]
                    record[column] = value
                entry = {
                    "year": year,
                    "remark": remark,
                    "count": count,
                    "page": page_number,
                    "group": group,
                    "label": label,
                    **record,
                }
                if label.upper() == "TOTAL":
                    totals.append(entry)
                else:
                    rows.append(entry)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    for year, (_, names, _, tables, _) in CONTESTED.items():
        counts = sorted(set(tables.values()))
        printed = {t["count"]: t for t in totals if t["year"] == year}
        if sorted(printed) != counts:
            raise ValueError(f"{year}: totals for counts {sorted(printed)}, expected {counts}")
        figures = next(o for o in overview if o["year"] == year)
        first = [r for r in rows if r["year"] == year and r["count"] == 1]
        for name in names:
            if sum(r["votes"][name] for r in first) != printed[1]["votes"][name]:
                raise ValueError(f"{year}: {name}'s first count does not add up to the printed total")
        for key in ("poll", "invalid", "electorate"):
            if key == "electorate" and year == 1945:
                continue
            if sum(r[key] for r in first) != printed[1][key] or printed[1][key] != figures[key]:
                raise ValueError(f"{year}: {key} rows {sum(r[key] for r in first)}, printed {printed[1][key]}")
        if printed[1]["poll"] - printed[1]["invalid"] != figures["valid"]:
            raise ValueError(f"{year}: valid poll {figures['valid']} disagrees with the first count")
        valid = {(r["group"], r["label"]): r["poll"] - r["invalid"] for r in first}
        previous = {(r["group"], r["label"]): r["votes"] for r in first}
        if year == 1945:
            limerick = [r for r in first if r["label"] == "Limerick"]
            valid["Counties", LIMERICK] = sum(r["poll"] - r["invalid"] for r in limerick)
            previous["Counties", LIMERICK] = {n: sum(r["votes"][n] for r in limerick) for n in names}
        for count in counts[1:]:
            later = [r for r in rows if r["year"] == year and r["count"] == count]
            if len(later) != len(first) - (1 if year == 1945 else 0):
                raise ValueError(f"{year} count {count}: {len(later)} rows for {len(first)} areas")
            for row in [*later, printed[count]]:
                place = (row["group"], row["label"])
                total_row = row["label"].upper() == "TOTAL"
                before = printed[count - 1]["votes"] if total_row else previous[place]
                for name, total in row["votes"].items():
                    if before[name] + row["transfers"][name] != total:
                        raise ValueError(f"{year} count {count} {place}: {name} does not carry over")
                base = figures["valid"] if total_row else valid[place]
                if sum(row["votes"].values()) + row["exhausted"] != base:
                    raise ValueError(f"{year} count {count} {place}: totals and non-transferable miss the valid poll")
                if not total_row:
                    previous[place] = row["votes"]
            for name in names:
                if (
                    name in printed[count]["votes"]
                    and sum(r["votes"][name] for r in later) != printed[count]["votes"][name]
                ):
                    raise ValueError(f"{year} count {count}: {name} does not add up")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(overview).write_parquet(PARSED / "overview.parquet")
    print(frame.group_by("year", "count").agg(pl.len()).sort("year", "count"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    overview = {row["year"]: row for row in pl.read_parquet(PARSED / "overview.parquet").iter_rows(named=True)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ireland's presidential elections 1938–2025: nine polls by constituency, or by county and county borough "
                "in 1945, with every count, and six candidates declared elected without a poll, from the Presidential "
                "Returning Officer's booklet"
            ),
            polity="IE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        ireland = cs.polity("IE")
        nation = cs.area("ie.nation", "IE")
        if nation in cs.minted:
            raise ValueError("publish ie.returning_officer before the presidential elections")
        dail_codes = {
            code
            for (code,) in conn.execute(
                "select c.code from area_code c join area a on a.id = c.area_id where a.scheme_code = 'ie.dail'"
            ).fetchall()
        }
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Creative Commons Attribution 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution=(
                "Toghcháin Uachtaráin 1938–2025 Presidential Elections, prepared by the Presidential Returning Officer "
                "(presidentialelection.ie), reused under CC BY 4.0."
            ),
        )
        cs.artifact(raw.cached(SOURCE, BOOKLET), source)
        system = cs.key("ie.electoral_system", "president")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=ireland,
            rules={"formula": "irv", "batchElimination": True},
            rules_version=1,
        )
        name(system, "Irish presidential elections")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        office = cs.key("ie.office", "president")
        cs.add("office", id=office, polity_id=ireland, kind="head_of_state")
        name(office, "President of Ireland")
        name(office, "Uachtarán na hÉireann", "ga", "native")

        people: dict[str, UUID] = {}

        def person(key: str) -> UUID:
            if key in people:
                return people[key]
            label, item, dail, gender = PEOPLE[key]
            if dail is not None:
                found = cs.key("ie.dail.person", dail)
                if found in cs.minted:
                    raise ValueError(f"{dail} is not in the archive")
                if (
                    conn.execute(
                        "select 1 from external_id where scheme = 'wikidata' and value = %s", [item]
                    ).fetchone()
                    is None
                ):
                    cs.add("external_id", entity_id=found, scheme="wikidata", value=item)
            else:
                found = cs.key("wikidata", item)
                if found in cs.minted:
                    cs.add("person", id=found, gender=gender)
                    name(found, label)
            people[key] = found
            return found

        units: dict[tuple[str, str], UUID] = {}

        def unit(scheme: str, code: str, label: str) -> UUID:
            if (scheme, code) in units:
                return units[scheme, code]
            area = cs.area(scheme, code)
            if area in cs.minted:
                raise ValueError(f"{scheme} {code} ({label}) is not in the archive")
            found = cs.unit(scheme, code)
            if found in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=found,
                    polity_id=ireland,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(found, label)
            units[scheme, code] = found
            return found

        def dail_unit(day: date, label: str) -> UUID:
            year = act(day)
            label = DAIL_NAMES.get((year, label), label)
            base = slug(label.replace("&", "and"))
            for variant in (base, base.replace("laoighis", "laoghis"), base.replace("laoighis", "laois")):
                if f"{year}:{variant}" in dail_codes:
                    return unit("ie.dail", f"{year}:{variant}", label)
            raise ValueError(f"no {year} Dáil constituency for {label!r}")

        def county_unit(group: str | None, label: str) -> UUID:
            if label == LIMERICK:
                return unit("ie.local_authority", "limerick-city-and-county", "Limerick City and County")
            code, shown = local(group, COUNTY_NAMES.get(label, label))
            return unit("ie.local_authority", code, shown)

        def contest_for(year: int, day: date, tacit: bool) -> tuple[UUID, UUID, str]:
            election = cs.key("ie.election", f"{day}:president")
            cs.add(
                "election",
                id=election,
                polity_id=ireland,
                kind="special" if year in SPECIAL else "general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} Irish presidential election")
            path = f"ie/{day}-presidential"
            cs.add("slug", path=path, entity_id=election)
            contest_key = f"{day}:president"
            contest = cs.key("ie.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=nation,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{year} Irish presidential election")
            cs.add("slug", path=f"{path}/president", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            return contest, result_set, contest_key

        def candidate(contest: UUID, contest_key: str, key: str, ballot: str, position: int | None) -> UUID:
            option_key = f"{contest_key}:{key}"
            option = cs.key("ie.option", option_key)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=PEOPLE[key][0],
            )
            cs.add(
                "candidacy",
                id=cs.key("ie.candidacy", option_key),
                person_id=person(key),
                option_id=option,
                role="head",
                ballot_name=ballot,
                is_independent=False,
            )
            return option

        contests: dict[int, UUID] = {}
        for year, (day, key, ballot, declared) in TACIT.items():
            contest, result_set, contest_key = contest_for(year, day, True)
            contests[year] = contest
            option = candidate(contest, contest_key, key, ballot, None)
            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            cs.add("note", entity_id=result_set, kind="methodology", body_md=TACIT_NOTE + declared)

        values = []
        for year, (day, names, winner, tables, excluded) in CONTESTED.items():
            contest, result_set, contest_key = contest_for(year, day, False)
            contests[year] = contest
            options = {
                key: candidate(contest, contest_key, key, BALLOT[key], position)
                for position, key in enumerate(names, 1)
            }
            rows = frame.filter(pl.col("year") == year)
            figures = overview[year]
            measures = "total poll and spoilt votes"
            if year > 1945:
                measures = f"electorate, total poll, and {'invalid ballot papers' if year >= 2011 else 'spoilt votes'}"
            remarks = [
                METHODOLOGY.format(
                    pages=", ".join(str(p) for p in sorted({figures["page"], *tables})),
                    measures=measures,
                    invalid="invalid" if year >= 2011 else "spoilt",
                    quota=figures["quota"],
                )
            ]
            if year == 1945:
                remarks.append(COUNTY_1945)
            remarks.extend(rows["remark"].drop_nulls())
            cs.add("note", entity_id=result_set, kind="methodology", body_md=" ".join(remarks))
            gone = {key for dropped in excluded.values() for key in dropped}
            for row in rows.sort("count", "page").iter_rows(named=True):
                where = county_unit(row["group"], row["label"]) if year == 1945 else dail_unit(day, row["label"])
                cell = (str(result_set), str(where))
                count = row["count"]
                if count == 1:
                    for measure, value in (
                        (REGISTERED, row["electorate"]),
                        (BALLOTS, row["poll"]),
                        (VALID, row["poll"] - row["invalid"]),
                        (INVALID, row["invalid"]),
                    ):
                        if value is not None:
                            values.append((*cell, None, measure, 0, value))
                else:
                    values.append((*cell, None, EXHAUSTED, count, row["exhausted"]))
                    for key in excluded.get(count, []):
                        values.append((*cell, str(options[key]), VOTES, count, 0))
                for key, total in row["votes"].items():
                    if total is not None:
                        values.append((*cell, str(options[key]), VOTES, count, total))
            for key, option in options.items():
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if key == winner else ("eliminated" if key in gone else "lost"),
                )
        for key, year, start, end, cause in TENURES:
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{key}:{start}"),
                person_id=person(key),
                office_id=office,
                from_date=start,
                from_precision="day",
                to_date=end,
                to_precision="day",
                acquired_via="elected",
                contest_id=contests[year],
                ended_via=cause,
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
