from datetime import date
from pathlib import Path
import re
import subprocess
from datetime import datetime, timedelta
from uuid import UUID
from urllib.parse import quote, unquote

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ky_recaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://elect.ky.gov"
PAGES = {
    "2022-primary": "/results/2020-2029/Pages/2022recap-sheets.aspx",
    "2022-general": "/results/2020-2029/Pages/2022-General-Recap-Sheets.aspx",
    "2023-primary": "/results/2020-2029/Pages/2023Primary-Recap-Sheets.aspx",
    "2023-general": "/results/2020-2029/Pages/2023General-Recap-Sheets.aspx",
    "2024-primary": "/results/2020-2029/Pages/2024Primary-Recap-Sheets.aspx",
    "2024-general": "/results/2020-2029/Pages/2024General-Recap-Sheets.aspx",
    "2026-primary": "/results/2020-2029/Pages/2026Primary-Recap-Sheets.aspx",
}
COUNTIES = ("Jefferson",)


def links() -> dict[tuple[str, str], str]:
    found = {}
    for election, page in PAGES.items():
        text = raw.cached(SOURCE, HOST + page).path.read_text(errors="replace")
        for href in re.findall(r'href="([^"]*\.pdf)"', text):
            name = unquote(href).rsplit("/", 1)[1].removesuffix(".pdf").removesuffix(" County")
            if name in COUNTIES:
                found[(election, name)] = HOST + quote(unquote(href), safe="/")
    return found


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, HOST + page, suffix=".html")
    for url in links().values():
        raw.fetch(SOURCE, url, suffix=".pdf")


CHANNEL_PROFILES = {
    ("six", "mail-first"): (0, 3, 2, 2, 5, 1),
    ("six", "day-first"): (0, 1, 3, 2, 2, 5),
    ("five", "mail-first"): (0, 3, 2, 2, 1),
    ("one", "mail-first"): (0,),
}
SIX_MAIL_FIRST = {"2026-primary"}
ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)\b", re.I)
ROW = re.compile(r"^(.*?\S)\s+(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
DAYS = re.compile(r"^(\w+ \d{1,2}, \d{4})")
CITY_OFFICES = {"MAYOR": "mayor", "COUNCILMEMBER": "council", "COMMISSIONER": "council", "COUNCIL": "council"}
COUNTY_OFFICES = {
    "COUNTY JUDGE/EXECUTIVE": ("county-judge-executive", "County Judge/Executive"),
    "COUNTY ATTORNEY": ("county-attorney", "County Attorney"),
    "COUNTY CLERK": ("clerk", "County Clerk"),
    "SHERIFF": ("sheriff", "Sheriff"),
    "PROPERTY VALUATION ADMINISTRATOR": ("property-valuation-administrator", "Property Valuation Administrator"),
    "CORONER": ("coroner", "Coroner"),
    "COUNTY SURVEYOR": ("county-surveyor", "County Surveyor"),
    "CIRCUIT COURT CLERK": ("circuit-court-clerk", "Circuit Court Clerk"),
    "SOIL & WATER CONSERVATION DISTRICT SUPERVISOR": ("soil-water-supervisor", "Soil and Water Conservation District Supervisor"),
}
SKIP = re.compile(
    r"STRAIGHT PARTY|PRESIDENT|U\.?S\.? |STATE (SENAT|REP)|GOVERNOR|ATTORNEY GENERAL|SECRETARY OF|TREASURER|AUDITOR|AGRICULTURE|"
    r"COMMONWEALTH|SUPREME|APPEALS|AMENDMENT|JUDGE - |JUDGE\b.*DIVISION|FAMILY COURT|CIRCUIT JUDGE|DISTRICT JUDGE|^(DEM|REP) (U\.S|STATE)",
    re.I,
)


def titled(word: str) -> str:
    return word if not word.isupper() or len(word) < 2 else ("Mc" + word[2:].title() if word.startswith("MC") else word.title())


def candidate_name(raw_name: str) -> str:
    return " ".join(titled(w) for w in raw_name.split())


def classify(title: str, primary_party: str | None) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", title.strip())
    party = None
    match = re.match(r"^(DEM|REP) (.*)$", text)
    if match:
        party, text = match[1], match[2]
    if SKIP.search(text) and not re.search(r"SCHOOL BOARD|COUNCIL|MAYOR|COMMISSIONER|SHERIFF|COUNTY|CORONER|SURVEYOR|CONSTABLE|PEACE|PROPERTY", text, re.I):
        return None
    upper = text.upper().replace("LOUISVILLE METRO COUNCIL LOUISVILLE METRO COUNCIL", "LOUISVILLE METRO COUNCIL")
    out: dict[str, object] = {"party": party, "seat": None, "jur": None, "type": None, "label": text}
    council = re.match(r"^(?:LOUISVILLE )?METRO COUNCIL(?: - (\d+)(?:ST|ND|RD|TH) DISTRICT)?(?: DISTRICT (\d+))?", upper)
    if council:
        return {**out, "jur": "Louisville", "kind": "city", "type": "council", "seat": council[1] or council[2]}
    if upper in ("MAYOR", "LOUISVILLE MAYOR"):
        return {**out, "jur": "Louisville", "kind": "city", "type": "mayor"}
    if upper in COUNTY_OFFICES:
        office, shown = COUNTY_OFFICES[upper]
        return {**out, "jur": "Jefferson County", "kind": "county", "type": "office", "office": office, "title": shown}
    comm = re.match(r'^COUNTY COMMISSIONER - "([A-Z])" DISTRICT', upper)
    if comm:
        return {**out, "jur": "Jefferson County", "kind": "county", "type": "office", "office": "county-commissioner", "title": "County Commissioner", "seat": f"District {comm[1]}"}
    for prefix, office, shown in (("CONSTABLE", "constable", "Constable"), ("JUSTICE OF THE PEACE", "justice-of-the-peace", "Justice of the Peace")):
        found = re.match(rf"^{prefix} - DISTRICT (\d+)", upper)
        if found:
            return {**out, "jur": "Jefferson County", "kind": "county", "type": "office", "office": office, "title": shown, "seat": f"District {found[1]}"}
    school = re.match(r"^SCHOOL BOARD - DISTRICT (\d+)", upper)
    if school:
        return {**out, "jur": "Jefferson County School District", "kind": "school", "type": "board", "seat": school[1]}
    if upper.startswith("ANCHORAGE SCHOOL BOARD"):
        return {**out, "jur": "Anchorage Independent Schools", "kind": "school", "type": "board"}
    levy = re.match(r"^TAX LEVY QUESTION (.*)$", upper)
    if levy:
        return {**out, "jur": levy[1].title(), "kind": "city", "type": "measure"}
    for word, kind in CITY_OFFICES.items():
        found = re.match(rf"^(?:{word} CITY OF (.+)|(.+) {word} (?:\2))$", upper)
        if found:
            city = found[1] or found[2]
            if found[1] is None and not upper.startswith(city):
                continue
            return {**out, "jur": city.title().replace("Saint ", "Saint "), "kind": "city", "type": kind}
    return None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: dict[tuple[str, str], dict[str, object]] = {}
    rows: list[tuple[str, str, str, int, int]] = []
    for (election, _), url in links().items():
        path = raw.cached(SOURCE, url).path
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        lines = out.split("\n")
        if len(out) < 5000:
            print(f"{election}: no text layer")
            continue
        day = None
        primary = False
        precinct = ""
        i = 0
        while i < len(lines) - 1:
            line = lines[i].strip()
            if line.startswith(("Precinct Summary Results Report", "Summary Results Report")):
                primary = "Primary" in lines[i + 1]
                found = DAYS.match(lines[i + 2].strip())
                day = datetime.strptime(found[1], "%B %d, %Y").date()
                j = i + 3
                while not lines[j].strip():
                    j += 1
                precinct = lines[j].strip()
                i = j + 1
                continue
            if lines[i + 1].strip().startswith("Vote For") and line:
                title = line
                seats = int(re.search(r"\d+", lines[i + 1])[0])
                info = classify(title, None)
                i += 2
                keep = info is not None
                key = (election, title)
                if keep and key not in contests:
                    contests[key] = {"election": election, "day": day, "primary": primary, "title": title, "seats": seats, **info}
                while (
                    i < len(lines) - 1
                    and not lines[i].strip().startswith(("Contest Totals", "Total Votes Cast", "Precinct Summary Results Report", "Summary Results Report"))
                    and not (lines[i].strip() and lines[i + 1].strip().startswith("Vote For"))
                ):
                    stripped = lines[i].strip()
                    found = ROW.match(stripped) if stripped and keep else None
                    if found and not found[1].startswith(("Not Assigned", "Write-In:", "Overvotes", "Undervotes", "Over Votes", "Under Votes", "Precinct Summary", "Summary Results", "Page ")):
                        numbers = [int(n.replace(",", "")) for n in found[2].split()]
                        profile = {1: ("one", "mail-first"), 5: ("five", "mail-first"), 6: ("six", "mail-first" if election in SIX_MAIL_FIRST else "day-first")}.get(len(numbers))
                        channels = CHANNEL_PROFILES.get(profile) if profile else None
                        if channels is None:
                            raise ValueError(f"{election} {precinct} {title}: {len(numbers)} columns")
                        name = "Write-in" if found[1].startswith("Write-In Totals") else found[1]
                        total = numbers[0]
                        parts = numbers[1:]
                        if parts and total > 0 and sum(parts) == total:
                            for channel, count in zip(channels[1:], parts, strict=True):
                                rows.append((f"{election}|{title}", precinct, name, channel, count))
                        else:
                            rows.append((f"{election}|{title}", precinct, name, 0, total))
                    i += 1
                continue
            i += 1
    pl.DataFrame(
        rows, schema=["key", "precinct", "name", "channel", "votes"], orient="row"
    ).write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(
        [{**c, "key": f"{c['election']}|{c['title']}"} for c in contests.values()],
        infer_schema_length=None,
    ).write_parquet(PARSED / "contests.parquet")
    print(len(contests), "contests", len(rows), "rows")


FIPS = "21111"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "County recap sheets: precinct summary results"
NOTE = (
    "Precinct totals from the Kentucky State Board of Elections' recap sheet for Jefferson County, with election day, "
    "mail, and early votes recorded separately where the sheet prints them. The sheet does not mark winners, so the "
    "outcomes recorded here are the leading candidates by votes: the top vote-getters for the seats at stake, or the "
    "advancing candidates in a primary."
)
PARTY_KEYS = {"DEM": "democratic-party", "REP": "republican-party", "LIB": "libertarian-party", "GRN": "green-party"}
PARTY_TITLES = {"DEM": "Democratic", "REP": "Republican"}
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")
CODE = re.compile(r"^([A-Z]{3}) (.*)$")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort("day", "kind", "jur", "title")
    rows = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Jefferson County, Kentucky's Louisville Metro, county, school board, and small-city contests by precinct from the State Board of Elections' recap sheets",
            polity="US-KY",
        )

        def name(entity: UUID, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-KY")
        state_area = cs.area("us.state", "21")
        county_unit, county_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        parties = {code: cs.key("us.party", key) for code, key in PARTY_KEYS.items()}
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=HOST, license="Public record")
        for url in links().values():
            cs.artifact(raw.cached(SOURCE, url), source)

        existing = {
            text.upper(): (pid, code, path)
            for pid, text, code, path in conn.execute(
                """select p.id, n.text, x.value, s.path from polity p
                   join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id and s.is_current
                   where x.value in ('place:2148000', 'school:2102990', 'county:21111')""",
            ).fetchall()
        }
        county_polity = existing["JEFFERSON COUNTY"][0] if "JEFFERSON COUNTY" in existing else conn.execute(
            "select entity_id from external_id where scheme = 'us.polity' and value = 'county:21111'"
        ).fetchone()[0]
        existing["JEFFERSON COUNTY"] = (county_polity, "county:21111", "us/ky/jefferson-county")
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s where s.path like 'us/ky/%%' and not exists (
                     select from contest k join changeset c on c.id = k.changeset_id
                     where k.id = s.entity_id and c.pipeline = %s)""",
                [SOURCE],
            ).fetchall()
        }
        territories = dict(
            conn.execute(
                "select distinct on (polity_id) polity_id, area_id from polity_territory order by polity_id, valid_from desc nulls last"
            ).fetchall()
        )
        polity_units = dict(
            conn.execute(
                "select area_id, id from reporting_unit where area_relation = 'equals' and scheme_code in ('us.county', 'us.place', 'us.school_district')"
            ).fetchall()
        )
        council_areas = {
            district: area
            for district, area in conn.execute(
                """select substring(x.value from 'City Council_(\\d+)_'), c.district_area_id from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where c.body_id = 'e2ad5c3e-a37e-5428-a867-4d7d48db8809' and x.value like '%%City Council_%%'"""
            ).fetchall()
        }

        report: dict[str, int] = {}
        made: dict[tuple[str, str], tuple[UUID, str, str]] = {}
        for kind, jur in sorted({(r["kind"], r["jur"]) for r in contests.iter_rows(named=True)}):
            found = existing.get(jur.upper())
            if found is not None:
                made[(kind, jur)] = found
                continue
            if kind == "city":
                code, polity_kind, scheme = f"place:21:{slug(jur)}", "municipality", "us.place"
            else:
                code, polity_kind, scheme = f"school:21:{slug(jur)}", "school_district", "us.school_district"
            pid = cs.key("us.polity", code)
            path = f"us/ky/{slug(jur)}"
            if path in taken:
                path = f"{path}-{'school-district' if kind == 'school' else 'city'}"
            if path in taken:
                raise ValueError(f"{jur} would take {path}")
            taken.add(path)
            if pid in cs.minted:
                cs.add("polity", id=pid, kind=polity_kind, iso_code=None)
                name(pid, jur)
                cs.add("polity_parent", polity_id=pid, parent_id=state)
                cs.add("slug", path=path, entity_id=pid)
                area_code = code.partition(":")[2]
                area = cs.area(scheme, area_code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=area_code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=county_area)
                name(area, f"{jur}, KY")
                cs.add("polity_territory", polity_id=pid, area_id=area)
                unit = cs.unit(scheme, area_code)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=pid,
                    scheme_code=scheme,
                    code=area_code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
                territories[pid] = area
                polity_units[area] = unit
            made[(kind, jur)] = (pid, code, path)

        people: dict[tuple[str, str], list[UUID]] = {}
        for person_id, label in conn.execute(
            """select distinct p.id, n.text from person p
               join name n on n.entity_id = p.id and n.is_preferred
               join candidacy y on y.person_id = p.id
               join contest_option o on o.id = y.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join external_id x on x.entity_id = e.polity_id and x.scheme = 'us.polity'
               where x.value in ('place:2148000', 'school:2102990', 'county:21111')"""
        ).fetchall():
            parts = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")]
            if len(parts) >= 2:
                people.setdefault((parts[0], parts[-1]), []).append(person_id)

        precinct_units: dict[date, dict[str, UUID]] = {}
        elections: dict[tuple[date, UUID], tuple[UUID, str]] = {}
        owners: dict[tuple[UUID, str], UUID] = {}
        districts: set[str] = set()
        seen: set[str] = set()
        made_people: set[UUID] = set()
        known: dict[tuple[UUID, date], dict[UUID, list]] = {}
        taken_targets: set[UUID] = set()
        misses: list[str] = []
        values: list[pl.DataFrame] = []
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")

        for row in contests.iter_rows(named=True):
            pid, code, path = made[(row["kind"], row["jur"])]
            day = row["day"]
            shown = row["jur"]
            party = row["party"]
            frame = rows.filter(pl.col("key") == row["key"])
            prefixed = frame.with_columns(
                prefix=pl.col("name").map_elements(lambda n: (CODE.match(n) or [None, None])[1], return_dtype=pl.String),
                display=pl.col("name").map_elements(
                    lambda n: "Write-in" if n == "Write-in" else "Yes" if n.upper() == "YES" else "No" if n.upper() == "NO" else candidate_name((CODE.match(n) or [None, n])[2] if CODE.match(n) else n),
                    return_dtype=pl.String,
                ),
            )
            frame = prefixed
            sums = frame.group_by("display").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0)
            if sums.is_empty():
                report["no votes"] = report.get("no votes", 0) + 1
                continue

            pools = [pid] if row["kind"] != "school" else [pid, county_polity]
            if row["kind"] == "city" and row["jur"] == "Louisville":
                pools = [pid]
            target = None
            surnames = {
                [w for w in re.sub(r"[^a-z ]", " ", c.lower()).split() if w not in SUFFIXES][-1]
                for c in sums["display"]
                if c not in ("Yes", "No", "Write-in")
            }
            for pool in pools:
                if (pool, day) not in known:
                    entries: dict[UUID, list] = {}
                    for contest_id, contest_key_, label, kind, parent, option_id in conn.execute(
                        """select k.id, (select x.value from external_id x where x.entity_id = k.id and x.scheme = 'us.contest' limit 1),
                                  o.ballot_label, o.kind, o.parent_option_id, o.id from contest k
                           join election e on e.id = k.election_id
                           join changeset kc on kc.id = k.changeset_id and kc.pipeline <> %s
                           left join contest_option o on o.contest_id = k.id
                           where e.polity_id = %s and e.date_from = %s""",
                        [SOURCE, pool, day],
                    ).fetchall():
                        entry = entries.setdefault(contest_id, [contest_key_, []])
                        if label is not None:
                            entry[1].append((label, kind, parent, option_id))
                    known[(pool, day)] = entries
                if surnames and target is None:
                    best = None
                    for contest_id, (contest_key_, listed) in known[(pool, day)].items():
                        theirs = {
                            [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in SUFFIXES][-1]
                            for label, kind, parent, _ in listed
                            if kind in ("candidate", "ticket") and parent is None and label.split()
                        }
                        if surnames <= theirs and (best is None or len(theirs) < best[0]):
                            best = (len(theirs), contest_id, contest_key_, listed)
                    if best is not None and best[2] is not None:
                        target = best[1:]
            if target is not None and target[0] in taken_targets:
                report["duplicate target"] = report.get("duplicate target", 0) + 1
                continue

            existing_election = conn.execute(
                """select e.id, s.path from election e join slug s on s.entity_id = e.id and s.is_current
                   join changeset c on c.id = e.changeset_id and c.pipeline <> %s
                   where e.polity_id = %s and e.date_from = %s order by e.date_to limit 1""",
                [SOURCE, pid, day],
            ).fetchone()
            key_body = code.partition(":")[2].replace(":", "-")
            suffix = f":{party.lower()}" if party else ""
            seat_text = row["seat"]
            answers = row["type"] == "measure"
            magnitude = 1 if answers else row["seats"]
            contest_kind, office_id, body_id, area = "office", None, None, None
            body_title = None
            if row["type"] in ("council", "board") or row.get("office") in ("county-commissioner", "soil-water-supervisor"):
                if row["jur"] == "Louisville":
                    body_title, owner_key = "Louisville Metro Council", "e2ad5c3e-a37e-5428-a867-4d7d48db8809"
                    seat_label = "district"
                elif row["jur"] == "Jefferson County School District":
                    body_title, owner_key = "Jefferson County Board of Education", "9c031686-9489-5398-a55b-20f48b2542b0"
                    seat_label = "district"
                elif row["jur"] == "Jefferson County":
                    owner_key = "8fa6da4b-9128-5178-9dc9-1fb87f181094" if row["office"] == "county-commissioner" else "304f0c28-cefd-5129-bfea-3e2e71392f49"
                    body_title = "Jefferson County Commission" if row["office"] == "county-commissioner" else "Jefferson County Soil and Water Conservation District"
                    seat_label = "district"
                else:
                    body_title = f"{shown} {'Board of Education' if row['type'] == 'board' else 'City Council'}"
                    owner_key, seat_label = None, "seat"
                body_kind = "council" if row["type"] != "board" else "board"
                if (pid, body_kind, body_title) not in owners:
                    if owner_key is not None:
                        owners[(pid, body_kind, body_title)] = UUID(owner_key)
                    else:
                        found = conn.execute("select b.id from body b where b.polity_id = %s order by b.id limit 1", [pid]).fetchone()
                        owner = found[0] if found else cs.key("us.body", f"KY:{key_body}:{body_kind}")
                        if found is None:
                            cs.add("body", id=owner, polity_id=pid, kind="council")
                            name(owner, body_title)
                        owners[(pid, body_kind, body_title)] = owner
                body_id, contest_kind = owners[(pid, body_kind, body_title)], "body_seats"
                if row["jur"] == "Louisville" and seat_text is not None:
                    if seat_text not in council_areas:
                        district_code = f"21:2148000:council:district-{seat_text}"
                        council_areas[seat_text] = cs.area("us.local_district", district_code)
                        cs.add("area", id=council_areas[seat_text], scheme_code="us.local_district")
                        cs.add("area_code", area_id=council_areas[seat_text], code=district_code, valid_from=None)
                        cs.add("area_parent", area_id=council_areas[seat_text], parent_id=territories[pid])
                        name(council_areas[seat_text], f"Louisville City Council, District {seat_text}")
                    area = council_areas[seat_text]
                elif row["jur"] == "Jefferson County School District" and seat_text is not None:
                    area = cs.area("us.local_district", f"21:sd2102990:board-of-education:district-{seat_text}")
                else:
                    area = territories.get(pid)
                contest_key = f"{day}:KY:{key_body}:{body_kind}" + (f":{slug(str(seat_text))}" if seat_text else "") + suffix
                contest_slug = body_kind + (f"-{slug(str(seat_text))}" if seat_text else "") + (f"-{party.lower()}" if party else "")
                contest_title = f"{day.year} {body_title}" + (f", {seat_label} {seat_text}" if seat_text else "") + " election" + (f" ({PARTY_TITLES[party]} primary)" if party else "")
                if row["jur"] == "Jefferson County" and row.get("office") == "county-commissioner":
                    contest_key += f":{slug(row['seat'])}"
                    contest_slug += f"-{slug(row['seat'])}"
                    contest_title = f"{day.year} {body_title}, {row['seat']} election"
            elif row["type"] in ("office", "mayor"):
                office = "mayor" if row["type"] == "mayor" else row["office"]
                title_ = "Mayor" if row["type"] == "mayor" else row["title"]
                office_key = f"KY:{code.partition(':')[2].replace('place:', '')}:{office}" if row["type"] == "mayor" else f"KY:{FIPS}:{office}"
                if row["jur"] == "Louisville":
                    office_key = "KY:2148000:mayor"
                found = conn.execute("select entity_id from external_id where scheme = 'us.office' and value = %s", [office_key]).fetchone()
                if (pid, office) not in owners:
                    owner = found[0] if found else cs.key("us.office", office_key)
                    if found is None:
                        cs.add("office", id=owner, polity_id=pid, kind="mayor" if office == "mayor" else "other")
                        name(owner, f"{shown} {title_}")
                    owners[(pid, office)] = owner
                office_id = owners[(pid, office)]
                if seat_text is not None:
                    district_code = f"21:{key_body}:{office}:{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if area in cs.minted and district_code not in districts:
                        districts.add(district_code)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(area, f"{shown} {title_}, {seat_text}")
                else:
                    area = territories.get(pid)
                contest_key = f"{day}:KY:{key_body}:{office}" + (f":{slug(seat_text)}" if seat_text else "") + suffix
                contest_slug = office + (f"-{slug(seat_text)}" if seat_text else "") + (f"-{party.lower()}" if party else "")
                contest_title = (f"{day.year} {shown} mayoral election" if row["type"] == "mayor" else f"{day.year} {shown} {title_}" + (f", {seat_text}" if seat_text else "") + " election") + (f" ({PARTY_TITLES[party]} primary)" if party else "")
            else:
                contest_kind = "ballot_question"
                contest_key = f"{day}:KY:{key_body}:measure-tax-levy"
                contest_slug = "measure-tax-levy"
                contest_title = f"{day.year} {shown} tax levy question"
                magnitude = None
                area = territories.get(pid)

            if target is None and ((existing_election is not None and f"{existing_election[1]}/{contest_slug}" in taken) or contest_key in seen):
                report["exists"] = report.get("exists", 0) + 1
                continue
            seen.add(contest_key)

            if target is not None:
                contest, target_key, listed = target
                taken_targets.add(contest)
                contest_key = target_key
                election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            else:
                if (day, pid) not in elections:
                    if existing_election is not None:
                        elections[(day, pid)] = existing_election
                    else:
                        election = cs.key("us.election", f"{day}:KY:{key_body}")
                        word = "primary" if row["primary"] else "general" if day.month == 11 and day.year % 2 == 0 else "local"
                        cs.add("election", id=election, polity_id=pid, kind=word, date_from=day, date_to=day, status="held")
                        name(election, f"{day.year} {shown} {word} election")
                        elections[(day, pid)] = (election, f"{path}/{day}-{word}")
                        cs.add("slug", path=f"{path}/{day}-{word}", entity_id=election)
                election, election_path = elections[(day, pid)]
                taken.add(f"{election_path}/{contest_slug}")
                contest = cs.key("us.contest", contest_key)
                system = None if magnitude is None else systems["block" if magnitude > 1 else "fptp"]
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
                    party_scope_id=parties.get(party),
                    electoral_system_id=system,
                    default_result_kind="counted",
                    term=None if answers else "full",
                )
                name(contest, contest_title)
                cs.add("slug", path=f"{election_path}/{contest_slug}", entity_id=contest)
                listed = []

            ordered = ["Yes", "No"] if answers else sums.sort("votes", descending=True)["display"].to_list()
            ordered = [c for c in ordered if c in set(sums["display"])]
            ordered += [c for c in sums.sort("votes", descending=True)["display"] if c not in ordered]
            by_label = {}
            by_last: dict[str, list[UUID]] = {}
            for label, kind_, parent, option_id in listed:
                if parent is None and label.split():
                    words = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in SUFFIXES]
                    by_label.setdefault((words[-1], words[0][:1]), option_id)
                    by_last.setdefault(words[-1], []).append(option_id)
            options: dict[str, UUID] = {}
            prefixes = dict(frame.select("display", "prefix").unique(subset=["display"]).iter_rows())
            missed = []
            for position, candidate in enumerate(ordered, start=1):
                if target is not None and candidate not in ("Yes", "No", "Write-in"):
                    words = [w for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split() if w not in SUFFIXES]
                    match = by_label.get((words[-1], words[0][:1]))
                    if match is None and len(by_last.get(words[-1], [])) == 1:
                        match = by_last[words[-1]][0]
                    if match is None:
                        missed.append(candidate)
                    else:
                        options[candidate] = match
                    continue
                if target is not None:
                    wanted = {"Write-in": "write_in", "Yes": "answer", "No": "answer"}[candidate]
                    options[candidate] = next(
                        (o for label, k, parent, o in listed if k == wanted and (wanted != "answer" or label == candidate)),
                        None,
                    ) or cs.key("us.option", f"{contest_key}:{SOURCE}:{slug(candidate)}")
                    if options[candidate] in cs.minted:
                        cs.add(
                            "contest_option",
                            id=options[candidate],
                            contest_id=contest,
                            kind=wanted,
                            ballot_label=candidate,
                            ballot_position=position if wanted == "answer" else None,
                        )
                    continue
                option = options[candidate] = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                if answers:
                    cs.add("contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=candidate)
                    continue
                if candidate == "Write-in":
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=candidate)
                    continue
                option_party = prefixes.get(candidate) or party
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate, party_id=parties.get(option_party))
                parts = [w for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")]
                matched = people.get((parts[0], parts[-1])) if len(parts) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person_id = matched[0]
                else:
                    person_id = cs.key(f"{SOURCE}.person", f"KY:{slug(candidate)}:{key_body}")
                    if person_id in cs.minted and person_id not in made_people:
                        made_people.add(person_id)
                        cs.add("person", id=person_id)
                        name(person_id, candidate)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                    person_id=person_id,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=option_party in ("IND", "NPA"),
                )
            if missed:
                misses += [f"{contest_key}: {n}" for n in missed]
                report["supersede skipped"] = report.get("supersede skipped", 0) + 1
                taken_targets.discard(contest)
                continue
            if answers and target is None:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")

            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            prior = None
            replaced = None
            if target is not None:
                prior = conn.execute(f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]).fetchone()
                if prior is not None and prior[0] == result_set:
                    replaced = conn.execute("select supersedes_id from result_set where id = %s", [prior[0]]).fetchone()[0]
                    prior = None
                if prior is not None:
                    cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded")
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
                completeness=1,
                supersedes_id=prior[0] if prior else replaced,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)

            ranked = sums.filter(~pl.col("display").is_in(["Write-in"])).sort("votes", descending=True)
            tally = dict(sums.iter_rows())
            if answers:
                winners = ["Yes"] if tally.get("Yes", 0) > tally.get("No", 0) else ["No"] if tally.get("No", 0) > tally.get("Yes", 0) else []
                outcome = "won"
            else:
                advancing = row["seats"] * (2 if row["primary"] and party is None else 1)
                winners = ranked["display"].to_list()[:advancing]
                outcome = "advanced" if row["primary"] else "elected"
            for candidate in winners:
                if candidate in options:
                    cs.add("outcome", result_set_id=result_set, option_id=options[candidate], kind=outcome, seats=None)

            if day not in precinct_units:
                precinct_units[day] = {
                    unit_code.rsplit(":", 1)[1]: unit_id
                    for unit_code, unit_id in conn.execute(
                        "select code, id from reporting_unit where scheme_code = 'us.precinct' and code like '%%:KY:21111:%%' and valid @> %s::date",
                        [day],
                    ).fetchall()
                }
            unit_ids: dict[str, UUID] = {}
            for precinct in frame["precinct"].unique().to_list():
                if precinct in precinct_units[day]:
                    unit_ids[precinct] = precinct_units[day][precinct]
                    continue
                unit_code = f"{day}:KY:{FIPS}:JEFFERSON:{precinct}"
                unit_ids[precinct] = precinct_units[day][precinct] = cs.unit("us.precinct", unit_code)
                cs.add(
                    "reporting_unit",
                    id=unit_ids[precinct],
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=unit_code,
                    parent_id=county_unit,
                    area_relation="within",
                    area_id=county_area,
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
            lines = (
                frame.filter(pl.col("display").is_in(list(options)))
                .group_by("precinct", "display", "channel")
                .agg(pl.col("votes").sum())
            )
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                    option_id=pl.col("display").replace_strict({k: str(v) for k, v in options.items()}),
                    channel=pl.col("channel").cast(pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            report["superseded" if target is not None else "new"] = report.get("superseded" if target is not None else "new", 0) + 1

        cs.remainders("A page footer on the recap sheets read as a candidate", pattern=r"^Precinct Summary")
        (PARSED / "supersede-misses.txt").write_text("\n".join(misses))
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
