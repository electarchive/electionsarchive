import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ca_la_svc
from ea.store import DATA, connect

SOURCE = "us.ca_la_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FIPS = "06037"
RESULTS = "https://results.lavote.gov/ElectionResults"
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
PLACE = "LOS ANGELES"
NOTE = (
    "Precinct totals from the Los Angeles County Registrar-Recorder/County Clerk's Statement of Votes Cast, one "
    "Excel file per contest. The county leaves out the selections of precincts with a single ballot cast, so precinct "
    "sums can fall slightly short of the contest's totals. The file declares no winners: where a seat count is stated, "
    "the top vote-getters up to that count are recorded as elected, and in a nonpartisan primary a candidate with a "
    "majority is elected and otherwise the top two advance."
)
SEATS = ' The seat count is the Registrar\'s own "Vote for no more than" line for the contest on results.lavote.gov.'
NO_SEATS = " No seat count is stated for this at-large contest, so no outcomes are recorded."
PARTIAL = " The jurisdiction extends beyond Los Angeles County; these are the county's precincts only."

CITIES = {
    "AVALON": "Avalon",
    "BELL": "Bell",
    "BELL GARDENS": "Bell Gardens",
    "BEVERLY HILLS": "Beverly Hills",
    "CARSON": "Carson",
    "COMMERCE": "Commerce",
    "COMPTON": "Compton",
    "COVINA": "Covina",
    "GARDENA": "Gardena",
    "GLENDALE": "Glendale",
    "INGLEWOOD": "Inglewood",
    "LA CAÑADA FLINTRIDGE": "La Cañada Flintridge",
    "LA PUENTE": "La Puente",
    "LAKEWOOD": "Lakewood",
    "LOMITA": "Lomita",
    "LONG BEACH": "Long Beach",
    "LOS ANGELES": "Los Angeles",
    "LYNWOOD": "Lynwood",
    "MONTEREY PARK": "Monterey Park",
    "PALOS VERDES EST": "Palos Verdes Estates",
    "PARAMOUNT": "Paramount",
    "PASADENA": "Pasadena",
    "POMONA": "Pomona",
    "RANCHO PALOS VERDES": "Rancho Palos Verdes",
    "SAN FERNANDO": "San Fernando",
    "SAN MARINO": "San Marino",
    "SANTA MONICA": "Santa Monica",
    "SIERRA MADRE": "Sierra Madre",
    "SOUTH GATE": "South Gate",
    "TORRANCE": "Torrance",
}
CITY_SUFFIX = r"(?:CITY GEN(?: MUNI?)?|CITY|CTY GEN|CTY|CY)"
SCHOOLS = {
    "BONITA UNIFIED SCHOOL": "Bonita Unified School District",
    "COMPTON UNIF SCHOOL": "Compton Unified School District",
    "COMPTON USD": "Compton Unified School District",
    "EL MONTE CITY SCH": "El Monte City School District",
    "EL MONTE SCHOOL": "El Monte City School District",
    "EL RANCHO RECALL": "El Rancho Unified School District",
    "EL RANCHO USD": "El Rancho Unified School District",
    "FULLERTON UHSD": "Fullerton Joint Union High School District",
    "FULLERTON JOINT UN HI": "Fullerton Joint Union High School District",
    "FULLERTON JT UNION HI": "Fullerton Joint Union High School District",
    "FULLERTON JT": "Fullerton Joint Union High School District",
    "GLENDALE USD": "Glendale Unified School District",
    "INGLEWOOD USD": "Inglewood Unified School District",
    "L A UNIFIED SCH": "Los Angeles Unified School District",
    "LOS ANGELES USD": "Los Angeles Unified School District",
    "LA CAÑADA USD": "La Cañada Unified School District",
    "LA CANADA USD": "La Cañada Unified School District",
    "LAS VIRGENES SCH": "Las Virgenes Unified School District",
    "LAS VIRGENES USD": "Las Virgenes Unified School District",
    "LAWNDALE SCHOOL": "Lawndale Elementary School District",
    "LITTLE LAKE CITY SCH": "Little Lake City Elementary School District",
    "LITTLE LAKE CTY SCH": "Little Lake City Elementary School District",
    "LONG BEACH USD": "Long Beach Unified School District",
    "LOWELL JOINT SCHOOL": "Lowell Joint Elementary School District",
    "LOWELL JOINT SCH": "Lowell Joint Elementary School District",
    "PALOS VERDES PEN USD": "Palos Verdes Peninsula Unified School District",
    "SAN MARINO USD": "San Marino Unified School District",
    "SNOWLINE JOINT USD": "Snowline Joint Unified School District",
    "SNOWLINE JT UNIF SCH": "Snowline Joint Unified School District",
    "SO PASADENA USD": "South Pasadena Unified School District",
    "ANTELOPE VLY JNT UN HI": "Antelope Valley Union Joint High School District",
    "WHITTIER CITY SCHOOL": "Whittier City Elementary School District",
    "WISEBURN USD": "Wiseburn Unified School District",
    "WM S. HART UNION HIGH": "William S. Hart Union High School District",
}
COLLEGES = {
    "ANTELOPE VLY": "Antelope Valley",
    "CERRITOS": "Cerritos",
    "CITRUS": "Citrus",
    "COMPTON": "Compton",
    "EL CAMINO": "El Camino",
    "GLENDALE": "Glendale",
    "LONG BEACH": "Long Beach",
    "LOS ANGELES": "Los Angeles",
    "MT SAN ANTONIO": "Mount San Antonio",
    "NO ORANGE": "North Orange County",
    "PASADENA AREA": "Pasadena Area",
    "PASADENA  AREA": "Pasadena Area",
    "RIO HONDO": "Rio Hondo",
    "SANTA CLARITA": "Santa Clarita",
    "SANTA MONICA": "Santa Monica",
    "VICTOR VALLEY": "Victor Valley",
}
COLLEGE = re.compile(
    r"^("
    + "|".join(sorted(map(re.escape, COLLEGES), key=len, reverse=True))
    + r")\s+(?:CCD|CC|COMM\s*COLL?E?G?E?|COM\s*COL|COMM|CO(?=\s+SPC))\b"
)
DISTRICTS = {
    "ALTADENA LIBRARY": "Altadena Library District",
    "ALTADENA LIB": "Altadena Library District",
    "ANTELOPE VALLEY HEALTH": "Antelope Valley Healthcare District",
    "ANTELOPE VLY HEALTH": "Antelope Valley Healthcare District",
    "ANTELOPE VLY HC": "Antelope Valley Healthcare District",
    "ANTELOPE VLY HE CA": "Antelope Valley Healthcare District",
    "ANTELOPE VLY E KERN WTR": "Antelope Valley-East Kern Water Agency",
    "BEACH CITIES HEALTH": "Beach Cities Health District",
    "CASTAIC LAKE WTR ACY": "Castaic Lake Water Agency",
    "CENTRAL BASIN MUNI": "Central Basin Municipal Water District",
    "CENTRAL BASIN MWD": "Central Basin Municipal Water District",
    "CRESCENTA VALLEY WTR": "Crescenta Valley Water District",
    "KINNELOA IRRIGATION": "Kinneloa Irrigation District",
    "LA HABRA HGTS CO WATER": "La Habra Heights County Water District",
    "LA PUENTE VALLEY WATER": "La Puente Valley County Water District",
    "LAS VIRGENES MUN WTR": "Las Virgenes Municipal Water District",
    "MT REC AND CONS": "Mountains Recreation and Conservation Authority",
    "MT REC AND CON AUTHY": "Mountains Recreation and Conservation Authority",
    "MT REC AND CON": "Mountains Recreation and Conservation Authority",
    "NEWHALL COUNTY WATER": "Newhall County Water District",
    "ORCHARD DALE WATER": "Orchard Dale Water District",
    "PALMDALE WATER": "Palmdale Water District",
    "PALOS VERDES LIBRARY": "Palos Verdes Library District",
    "PICO WATER": "Pico Water District",
    "PUENTE HILLS HAB AUTHY": "Puente Hills Habitat Preservation Authority",
    "QUARTZ HILL WATER": "Quartz Hill Water District",
    "ROWLAND WATER": "Rowland Water District",
    "SAN GABRIEL CO WATER": "San Gabriel County Water District",
    "SAN GABRIEL VLY MUNI": "San Gabriel Valley Municipal Water District",
    "SANTA CLARITA VALLEY": "Santa Clarita Valley Water Agency",
    "SANTA CLARITA WTER": "Santa Clarita Valley Water Agency",
    "SANTA CLARITA": "Santa Clarita Valley Water Agency",
    "SATIVA-LA COUNTY WATER": "Sativa-Los Angeles County Water District",
    "SATIVA": "Sativa-Los Angeles County Water District",
    "THREE VLYS MUNI WTR": "Three Valleys Municipal Water District",
    "THREE VLYS MUN WTR": "Three Valleys Municipal Water District",
    "UPPER SAN GABRIEL VLY": "Upper San Gabriel Valley Municipal Water District",
    "VALLEY COUNTY WATER": "Valley County Water District",
    "WALNUT VALLEY WATER": "Walnut Valley Water District",
    "WATER REPLEN OF SO CAL": "Water Replenishment District of Southern California",
    "WEST BASIN MUNI WATER": "West Basin Municipal Water District",
}
PARTIAL_DISTRICTS = {
    "Antelope Valley Union Joint High School District",
    "Antelope Valley-East Kern Water Agency",
    "Fullerton Joint Union High School District",
    "Snowline Joint Unified School District",
    "North Orange County Community College District",
    "Victor Valley Community College District",
    "Mount San Antonio Community College District",
    "Antelope Valley Community College District",
    "Citrus Community College District",
}
JURISDICTIONS = sorted(
    [
        *((f"CITY OF {key}", ("city", name)) for key, name in CITIES.items()),
        *((f"CITY  OF {key}", ("city", name)) for key, name in CITIES.items()),
        *(
            (f"{key} {suffix}", ("city", name))
            for key, name in CITIES.items()
            for suffix in (
                "CITY GEN MUNI",
                "CITY GEN MUN",
                "CITY GEN",
                "CITY",
                "CTY GEN",
                "CTY",
                "CY SPC MUNI",
                "CY",
                "CTY GEN &",
            )
        ),
        ("CITY OF BELL GENERAL", ("city", "Bell")),
        ("CITY OF PALOS VERD EST", ("city", "Palos Verdes Estates")),
        ("PALOS VERDES EST CTY", ("city", "Palos Verdes Estates")),
        ("PALOS VERDES EST CITY", ("city", "Palos Verdes Estates")),
        ("LA CAÑADA FLINTRIDGE", ("city", "La Cañada Flintridge")),
        ("GLENDALE CTY GEN &", ("city", "Glendale")),
        ("LONG BEACH CY SPC MUNI", ("city", "Long Beach")),
        ("LA PUENTE CITY GEN MUN", ("city", "La Puente")),
        *((key, ("school", name)) for key, name in SCHOOLS.items()),
        *((key, ("special", name)) for key, name in DISTRICTS.items()),
    ],
    key=lambda pair: -len(pair[0]),
)
OFFICES = (
    (re.compile(r"\bMAYOR\b"), "mayor", "Mayor"),
    (re.compile(r"\b(?:CTY|CITY)\s*CLK\b|\bCLERK\b"), "city-clerk", "City Clerk"),
    (re.compile(r"\bTREA\w*\b"), "city-treasurer", "City Treasurer"),
    (re.compile(r"\bATTRNY\b"), "city-attorney", "City Attorney"),
    (re.compile(r"\bCONTRL\b"), "city-controller", "City Controller"),
    (re.compile(r"\bAUDITOR\b"), "city-auditor", "City Auditor"),
)
BOARD = re.compile(
    r"^(?:(?:BOT|GBM|BOE|BT|BOD|BD|SOS)\s*)?(?:(?:(?:TR|TA|DIV|DIVISION|DIST|DISTRICT|D)\s*([A-Z]?\d+|[A-Z]\b)|([IV]+|\d+)|(AT\s*LG)))?$"
)


def classify(label: str) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", label.rsplit("/", 1)[-1].replace("_", " ")).strip()
    unexpired = "U-T" in text
    text = re.sub(r"\s\d\d-\d\d-\d\d\s.*$", "", text).replace("U-T", "").strip()
    college = COLLEGE.match(text)
    if college is not None:
        kind, name, rest = "college", f"{COLLEGES[college[1]]} Community College District", text[college.end() :]
    elif text == "SHERIFF":
        return {
            "kind": "county",
            "name": "Los Angeles County",
            "special": False,
            "term": "full",
            "type": "office",
            "office": "sheriff",
            "title": "Sheriff",
            "seat": None,
        }
    elif (supervisor := re.match(r"^SUPERVISOR (\d)(?:ST|ND|RD|TH) DISTRICT$", text)) is not None:
        return {
            "kind": "county",
            "name": "Los Angeles County",
            "special": False,
            "term": "full",
            "type": "board",
            "seat": supervisor[1],
        }
    else:
        for prefix, (kind, name) in JURISDICTIONS:
            if text.startswith(prefix) and not text[len(prefix) : len(prefix) + 1].isalnum():
                rest = text[len(prefix) :]
                break
        else:
            return None
    special = bool(re.search(r"\b(?:SPC|SPEC|SPECIAL|SP)\b", rest))
    rest = re.sub(r"\b(?:SPC|SPEC|SPECIAL|SP|GEN|GENERAL|MUNI|MUN|FULL)\b|&", " ", rest)
    term = "remainder" if unexpired or "2-YEAR" in rest else "full"
    rest = re.sub(r"2-YEAR", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" -")
    row: dict[str, object] = {"kind": kind, "name": name, "special": special, "term": term, "recall": None}
    if "RECALL" in text:
        row |= {"type": "recall", "code": rest.strip(" -")}
        return row
    council = re.match(r"^CNC\s*(\d+)?$", rest)
    if council is not None:
        return row | {"type": "council", "seat": council[1]}
    measure = re.match(r"^(?:MEAS(?:URE)?\s*)([A-Z0-9]+)$", rest)
    if measure is None and re.fullmatch(r"-?\s*[A-Z]{1,4}", rest) and not BOARD.match(rest.strip(" -")):
        measure = re.match(r"^-?\s*([A-Z]{1,4})$", rest)
    if measure is None and re.fullmatch(r"(?:-?\s*MEAS(?:URE)?\s*[A-Z0-9]+)", rest):
        measure = re.search(r"([A-Z0-9]+)$", rest)
    if measure is not None:
        return row | {"type": "measure", "code": measure[1]}
    for pattern, office, title in OFFICES:
        if pattern.search(rest):
            return row | {"type": "office", "office": office, "title": title, "seat": None}
    board = BOARD.match(rest)
    if board is not None and kind != "city":
        return row | {"type": "board", "seat": board[1] or board[2]}
    return None


def fetch() -> None:
    for year in json.loads(raw.fetch(SOURCE, f"{RESULTS}/GetMenuYears", suffix=".json").path.read_bytes())["Data"]:
        listing = raw.fetch(SOURCE, f"{RESULTS}/GetMenuElections?year={year}", suffix=".json").path
        for found in json.loads(listing.read_bytes())["Data"]:
            raw.fetch(SOURCE, f"{RESULTS}/GetElectionData?electionID={found['ID']}", suffix=".json")
            time.sleep(1)


def seat_counts() -> tuple[dict[tuple[date, bool], list[tuple[str, int]]], set[tuple[date, str]], list[raw.Artifact]]:
    counts: dict[tuple[date, bool], list[tuple[str, int]]] = {}
    primaries: set[tuple[date, str]] = set()
    artifacts = []
    for year in json.loads(raw.cached(SOURCE, f"{RESULTS}/GetMenuYears").path.read_bytes())["Data"]:
        listing = raw.cached(SOURCE, f"{RESULTS}/GetMenuElections?year={year}").path
        for found in json.loads(listing.read_bytes())["Data"]:
            artifact = raw.cached(SOURCE, f"{RESULTS}/GetElectionData?electionID={found['ID']}")
            artifacts.append(artifact)
            data = json.loads(artifact.path.read_bytes())["Data"]
            day = date.fromisoformat(data["Date"][:10])
            for group in data["ContestGroups"]:
                for contest in group["Contests"]:
                    nominating = re.match(
                        r"(?:CITY OF )?(.+?)(?: CITY)?(?: COUNCIL DISTRICT \d+)?(?: SPECIAL)? PRIMARY\b(?!.*MEASURE)",
                        contest["Title"],
                    )
                    if nominating is not None:
                        primaries.add((day, nominating.group(1).replace(" ", "")))
                    stated = re.fullmatch(r"Vote for no more than (\w+)", contest["VoteFor"] or "")
                    if stated is None or stated.group(1).lower() not in NUMBERS:
                        continue
                    unexpired = re.search(r"unexpired|U/T|special election", contest["Title"], flags=re.IGNORECASE)
                    counts.setdefault((day, unexpired is not None), []).append(
                        (contest["Title"].upper(), NUMBERS[stated.group(1).lower()])
                    )
    return counts, primaries, artifacts


def parse() -> None:
    rows = pl.read_parquet(ca_la_svc.PARSED / "rows.parquet")
    labels = []
    skipped = set()
    for day, url, contest in rows.select("day", "url", "contest").unique().iter_rows():
        found = classify(contest)
        if found is None:
            skipped.add(contest.rsplit("/", 1)[-1].split("_by_Precinct")[0])
            continue
        labels.append({"day": day, "url": url, "contest": contest, **{k: v for k, v in found.items()}})
    table = pl.DataFrame(labels, infer_schema_length=None)
    PARSED.mkdir(parents=True, exist_ok=True)
    table.write_parquet(PARSED / "labels.parquet")
    rows.join(table.select("day", "url", "contest"), on=["day", "url", "contest"]).write_parquet(
        PARSED / "rows.parquet"
    )
    print(f"{table.height} classified, {len(skipped)} other labels")
    (PARSED / "skipped.txt").write_text("\n".join(sorted(skipped)))


def emit() -> Path:
    labels = pl.read_parquet(PARSED / "labels.parquet")
    rows = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Los Angeles County's city, school, college, and special district contests by precinct from the Statements of Votes Cast",
            polity="US-CA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-CA")
        county = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        if county is None:
            raise ValueError("Los Angeles County has no unit")
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")
        counts, primaries, results = seat_counts()
        results_source = cs.source(ca_la_svc.PUBLISHER, "Election results (results.lavote.gov)")
        if results_source in cs.minted:
            cs.add(
                "source",
                id=results_source,
                kind="official_body",
                publisher=ca_la_svc.PUBLISHER,
                title="Election results (results.lavote.gov)",
                url="https://results.lavote.gov/",
                license="Public record",
            )
        for artifact in results:
            cs.artifact(artifact, results_source)
        source = cs.source(ca_la_svc.PUBLISHER, ca_la_svc.TITLE)
        if source in cs.minted:
            raise ValueError("the Statement of Votes Cast source must already exist")
        for url in sorted(set(rows["url"])):
            cs.artifact(raw.cached(ca_la_svc.SOURCE, url), source)

        polities: dict[tuple[str, str], tuple[UUID, str, str]] = {}
        for pid, kind, text, code, path in conn.execute(
            """select p.id, p.kind, n.text, x.value, s.path from polity p
               join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where x.value like any(%s)""",
            [["place:06%", "school:06%", "special:06%", "county:06037"]],
        ).fetchall():
            polities[
                (
                    {"municipality": "city", "school_district": "school", "special_district": "special"}.get(
                        kind, kind
                    ),
                    text,
                )
            ] = (pid, code, path)
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/ca/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        own_areas = {
            area
            for (area,) in conn.execute(
                "select a.id from area a join changeset c on c.id = a.changeset_id where c.pipeline = %s", [SOURCE]
            ).fetchall()
        }
        territories = dict(
            conn.execute(
                "select distinct on (polity_id) polity_id, area_id from polity_territory order by polity_id, valid_from desc nulls last"
            ).fetchall()
        )
        state_area = cs.area("us.state", "06")

        report: dict[str, int] = {}
        made: dict[tuple[str, str], tuple[UUID, str, str]] = {}
        for kind, text in sorted({(r["kind"], r["name"]) for r in labels.iter_rows(named=True)}):
            if kind == "college":
                kind_key = "school"
            else:
                kind_key = kind
            found = polities.get((kind_key, text)) or polities.get(("special", text))
            if found is None and kind in ("college", "special"):
                code = f"school:06:{slug(text)}" if kind == "college" else f"special:06037:{slug(text)}"
                pid = cs.key("us.polity", code)
                path = f"us/ca/{slug(text)}"
                if path in taken:
                    raise ValueError(f"{text} would take {path}")
                cs.add(
                    "polity", id=pid, kind="school_district" if kind == "college" else "special_district", iso_code=None
                )
                name(pid, text)
                cs.add("polity_parent", polity_id=pid, parent_id=state)
                cs.add("slug", path=path, entity_id=pid)
                found = (pid, code, path)
            if found is None:
                report[f"no polity: {kind} {text}"] = 0
                continue
            made[(kind, text)] = found

        for (kind, text), (pid, code, path) in made.items():
            if pid in territories or not code.startswith(("special:06037:", "school:06:")):
                continue
            scheme = "us.school_district" if kind == "college" else "us.special_district"
            area = cs.area(scheme, code.partition(":")[2])
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code.partition(":")[2], valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area if kind == "college" else county[1])
            name(area, text)
            cs.add("polity_territory", polity_id=pid, area_id=area)
            territories[pid] = area
            own_areas.add(area)
        for pid, text, short in conn.execute(
            """select p.id, n.text, case n.text when 'Los Angeles Unified School District' then 'LAUSD'
                   when 'Los Angeles Community College District' then 'LACCD' end
               from polity p join name n on n.entity_id = p.id and n.is_preferred
               where n.text in ('Los Angeles Unified School District', 'Los Angeles Community College District')"""
        ).fetchall():
            if not conn.execute(
                "select 1 from name where entity_id = %s and kind = 'abbreviation' and text = %s", [pid, short]
            ).fetchone():
                cs.add("name", entity_id=pid, text=short, lang="en", kind="abbreviation", is_preferred=False)

        people: dict[tuple[UUID, str, str], UUID] = {}
        for person, label, election_polity in conn.execute(
            """select distinct p.id, n.text, e.polity_id from person p
               join name n on n.entity_id = p.id and n.is_preferred
               join candidacy y on y.person_id = p.id
               join contest_option o on o.id = y.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join changeset c on c.id = p.changeset_id
               where e.polity_id = any(%s) and c.pipeline <> %s
               order by p.id""",
            [[found[0] for found in made.values()], SOURCE],
        ).fetchall():
            parts = [
                w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if len(parts) >= 2:
                people.setdefault((election_polity, parts[0], parts[-1]), person)

        elections: dict[tuple[date, UUID], tuple[UUID, str]] = {}
        owners: dict[tuple[UUID, str], UUID] = {}
        districts: set[str] = set()
        seen: set[str] = set()
        minted_units: set[str] = set()
        values: list[pl.DataFrame] = []
        ordered = labels.sort("day", "name", "contest")
        for row in ordered.iter_rows(named=True):
            polity = made.get((row["kind"], row["name"]))
            if polity is None:
                continue
            pid, code, path = polity
            day = row["day"]
            frame = rows.filter(
                (pl.col("day") == day) & (pl.col("url") == row["url"]) & (pl.col("contest") == row["contest"])
            )
            sums = frame.group_by("candidate").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0)
            if sums.is_empty():
                report["no votes"] = report.get("no votes", 0) + 1
                continue
            existing_election = conn.execute(
                """select e.id, s.path from election e join slug s on s.entity_id = e.id and s.is_current
                   where e.polity_id = %s and e.date_from = %s order by e.date_to limit 1""",
                [pid, day],
            ).fetchone()
            if existing_election is not None:
                known = conn.execute(
                    """select lower(coalesce(o.ballot_label, '')), lower(coalesce(n.text, '')) from contest k
                       join election e on e.id = k.election_id
                       join changeset kc on kc.id = k.changeset_id and kc.pipeline <> %s
                       left join contest_option o on o.contest_id = k.id
                       left join name n on n.entity_id = k.id and n.is_preferred
                       where e.polity_id = %s and e.date_from = %s""",
                    [SOURCE, pid, day],
                ).fetchall()
                labels_known = {label for label, _ in known}
                names_known = " ".join({text for _, text in known})
                if row["type"] == "measure":
                    present = re.search(rf"\b(?:measure )?{re.escape(row['code'].lower())}\b", names_known) is not None
                else:
                    surnames = {c.split()[-1].lower() for c in sums["candidate"] if c.upper() not in ("YES", "NO")}
                    present = any(label.split()[-1] in surnames for label in labels_known if label.split())
                if present:
                    report["exists"] = report.get("exists", 0) + 1
                    continue
            if (day, pid) not in elections:
                if existing_election is not None:
                    elections[(day, pid)] = existing_election
                else:
                    election = cs.key("us.election", f"{day}:CA:{code.partition(':')[2]}")
                    general = day.month == 11 and day.year % 2 == 0
                    cs.add(
                        "election",
                        id=election,
                        polity_id=pid,
                        kind="general" if general else "special" if row["special"] else "local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    ptext = row["name"]
                    name(
                        election,
                        f"{day.year} {ptext} {'general' if general else 'special' if row['special'] else 'local'} election",
                    )
                    election_path = f"{path}/{day}-{'general' if general else 'special' if row['special'] else 'local'}"
                    cs.add("slug", path=election_path, entity_id=election)
                    elections[(day, pid)] = (election, election_path)
            election, election_path = elections[(day, pid)]

            key_body = code.partition(":")[2].replace(":", "-")
            if row["type"] in ("council", "board"):
                body_kind = (
                    "council"
                    if row["type"] == "council"
                    else "board-of-supervisors"
                    if row["kind"] == "county"
                    else "board"
                )
                seat_label = (
                    "district"
                    if row["type"] == "council"
                    else "supervisorial district"
                    if row["kind"] == "county"
                    else "division"
                    if row["kind"] == "special"
                    else "seat"
                    if row["name"] == "Los Angeles Community College District"
                    else "trustee area"
                )
                body_title = (
                    f"{row['name']} City Council"
                    if row["type"] == "council"
                    else f"{row['name']} Board of Supervisors"
                    if row["kind"] == "county"
                    else f"{row['name']} Board of {'Trustees' if row['kind'] == 'college' else 'Directors' if row['kind'] == 'special' else 'Education'}"
                )
                body_key = f"CA:{key_body}:{body_kind}"
                existing_body = conn.execute(
                    """select b.id from body b where b.polity_id = %s order by (b.kind = 'council') desc, b.id limit 1""",
                    [pid],
                ).fetchone()
                if (pid, body_kind) not in owners:
                    owner = existing_body[0] if existing_body is not None else cs.key("us.body", body_key)
                    if existing_body is None:
                        cs.add("body", id=owner, polity_id=pid, kind="council")
                        name(owner, body_title)
                    owners[(pid, body_kind)] = owner
                owner = owners[(pid, body_kind)]
                seat_text = row["seat"]
                area = None
                if seat_text is not None:
                    district_code = f"06:{key_body}:{body_kind}:district-{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if (area in cs.minted or area in own_areas) and district_code not in districts:
                        districts.add(district_code)
                        if area in cs.minted:
                            cs.add("area", id=area, scheme_code="us.local_district")
                            cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        if pid in territories and (
                            area in cs.minted
                            or not conn.execute("select 1 from area_parent where area_id = %s", [area]).fetchone()
                        ):
                            cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(
                            area,
                            f"{body_title}, {seat_label} {seat_text}",
                        )
                contest_key = (
                    f"{day}:CA:{key_body}:{body_kind}"
                    + (f":{slug(seat_text)}" if seat_text else "")
                    + ("" if row["term"] == "full" else ":remainder")
                )
                contest_slug = (
                    f"{body_kind}"
                    + (f"-{slug(seat_text)}" if seat_text else "")
                    + ("" if row["term"] == "full" else "-unexpired")
                )
                contest_title = (
                    f"{day.year} {body_title}" + (f", {seat_label} {seat_text}" if seat_text else "") + " election"
                )
                contest_kind, office_id, body_id = "body_seats", None, owner
                magnitude = 1 if seat_text is not None else None
                if magnitude is None:
                    jurisdictions = {}
                    for title, seats in counts.get((day, row["term"] != "full"), []):
                        position = re.split(r" (?:MEMBER|GOVERNING BOARD|CITY COUNCILMEMBER)\b", title)[0]
                        position = re.sub(r"^CITY OF ", "", position)
                        position = re.sub(
                            r"(?: CITY)?(?: (?:GENERAL|REGULAR))?(?: AND SPECIAL)?(?: MUNICIPAL)?(?: SPECIAL)? ELECTIONS?$",
                            "",
                            position,
                        )
                        if "RENT CONTROL" not in title and "REGULAR AND UNEXPIRED" not in title:
                            jurisdictions.setdefault(position.replace(" ", ""), set()).add(seats)
                    found = jurisdictions.get(row["name"].upper().replace(" ", ""), set())
                    magnitude = next(iter(found)) if len(found) == 1 else None
            elif row["type"] == "office":
                office_key = f"CA:{key_body}:{row['office']}"
                existing_office = conn.execute(
                    "select entity_id from external_id where scheme = 'us.office' and value = %s", [office_key]
                ).fetchone()
                if (pid, row["office"]) not in owners:
                    owner = existing_office[0] if existing_office is not None else cs.key("us.office", office_key)
                    if existing_office is None:
                        cs.add("office", id=owner, polity_id=pid, kind="mayor" if row["office"] == "mayor" else "other")
                        name(owner, f"{row['name']} {row['title']}")
                    owners[(pid, row["office"])] = owner
                owner = owners[(pid, row["office"])]
                area = None
                contest_key = f"{day}:CA:{key_body}:{row['office']}"
                contest_slug = row["office"]
                contest_title = (
                    f"{day.year} {row['name']} mayoral election"
                    if row["office"] == "mayor"
                    else f"{day.year} {row['name']} {row['title']} election"
                )
                contest_kind, office_id, body_id = "office", owner, None
                magnitude = 1
            else:
                area = None
                code_text = row["code"]
                contest_key = f"{day}:CA:{key_body}:measure-{slug(code_text)}"
                contest_slug = f"measure-{slug(code_text)}"
                contest_title = f"{day.year} {row['name']} {'recall of ' + code_text.title() if row['type'] == 'recall' else 'Measure ' + code_text}"
                contest_kind, office_id, body_id = "ballot_question", None, None
                magnitude = None
            if f"{election_path}/{contest_slug}" in taken or contest_key in seen:
                report["exists"] = report.get("exists", 0) + 1
                continue
            seen.add(contest_key)
            taken.add(f"{election_path}/{contest_slug}")
            contest = cs.key("us.contest", contest_key)
            if area is None:
                area = cs.area("us.place", code.partition(":")[2]) if code.startswith("place:") else None
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=contest_kind,
                office_id=office_id,
                body_id=body_id,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=None if magnitude is None else systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
                term=row["term"],
            )
            name(contest, contest_title)
            cs.add("slug", path=f"{election_path}/{contest_slug}", entity_id=contest)

            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            partial = row["name"] in PARTIAL_DISTRICTS
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=None if partial else 1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=NOTE
                + (
                    SEATS
                    if row["type"] in ("council", "board") and row["seat"] is None and magnitude is not None
                    else ""
                )
                + (NO_SEATS if row["type"] in ("council", "board") and magnitude is None else "")
                + (PARTIAL if partial else ""),
            )
            options: dict[str, UUID] = {}
            for position, candidate in enumerate(sums.sort("votes", descending=True)["candidate"], start=1):
                option = options[candidate] = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                if row["type"] in ("measure", "recall"):
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=candidate.title(),
                    )
                else:
                    label = candidate.title()
                    write_in = "WRITE" in candidate.upper()
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if write_in else "candidate",
                        ballot_label=label,
                    )
                    if not write_in:
                        parts = [
                            w
                            for w in re.sub(r"[^a-z ]", " ", label.lower()).split()
                            if w not in ("jr", "sr", "ii", "iii", "iv")
                        ]
                        person = cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(candidate)}")
                        matched = people.get((pid, parts[0], parts[-1])) if len(parts) >= 2 else None
                        if matched is not None and matched != person:
                            if person not in cs.minted:
                                cs.merge(person, matched)
                            person = matched
                        elif person in cs.minted:
                            cs.add("person", id=person)
                            name(person, label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            is_independent=False,
                        )
            if row["type"] in ("measure", "recall"):
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            elif magnitude is not None:
                named = [
                    (candidate, votes)
                    for candidate, votes in sums.sort("votes", descending=True).iter_rows()
                    if "WRITE" not in candidate.upper()
                ]
                primary = (
                    (day, row["name"].upper().replace(" ", "")) in primaries
                    or (
                        row["name"] in ("Los Angeles Unified School District", "Los Angeles Community College District")
                        and day.month in (3, 6)
                    )
                    or (row["kind"] == "county" and day.month in (3, 6) and day.year % 2 == 0)
                )
                if primary:
                    if named[0][1] * 2 > sums["votes"].sum():
                        decided = [(named[0][0], "elected")]
                    elif len(named) >= 2 and (len(named) == 2 or named[1][1] > named[2][1]):
                        decided = [(candidate, "advanced") for candidate, _ in named[:2]]
                    else:
                        decided = []
                elif len(named) <= magnitude or named[magnitude - 1][1] > named[magnitude][1]:
                    decided = [(candidate, "elected") for candidate, _ in named[:magnitude]]
                else:
                    decided = []
                for candidate, outcome in decided:
                    cs.add("outcome", result_set_id=result_set, option_id=options[candidate], kind=outcome, seats=None)

            frame = frame.group_by("precinct", "candidate", maintain_order=True).agg(
                pl.col("votes").sum(), pl.col("registered").sum(), pl.col("ballots").sum()
            )
            unit_ids: dict[str, UUID] = {}
            for precinct in frame["precinct"].unique().to_list():
                unit_code = f"{day}:CA:{FIPS}:{PLACE}:{precinct}"
                unit_ids[precinct] = cs.unit("us.precinct", unit_code)
                if unit_ids[precinct] in cs.minted and unit_code not in minted_units:
                    minted_units.add(unit_code)
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[precinct],
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=unit_code,
                        parent_id=county[0],
                        area_relation="within",
                        area_id=county[1],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            mapped = {k: str(v) for k, v in unit_ids.items()}
            values.append(
                frame.filter(pl.col("candidate").is_in(list(options))).select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict(mapped),
                    option_id=pl.col("candidate").replace_strict({k: str(v) for k, v in options.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            values.append(
                frame.select("precinct", "registered", "ballots")
                .unique("precinct")
                .unpivot(index="precinct", on=["registered", "ballots"], variable_name="name", value_name="value")
                .drop_nulls("value")
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict(mapped),
                    option_id=pl.lit(None, pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.when(pl.col("name") == "registered").then(2).otherwise(3).cast(pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("value"),
                )
            )
            report["new"] = report.get("new", 0) + 1
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
