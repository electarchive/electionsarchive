import csv
import html
import json
import re
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.clark_nv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Clark County Election Department"
HOST = "https://elections.clarkcountynv.gov/electionresultsTV"
PAGE = f"{HOST}/results.aspx"
LISTING = f"{PAGE}#listing"


def official_url(code: str) -> str:
    return f"{PAGE}#official-{code}"


def fetch() -> None:
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, timeout=120, follow_redirects=True)
    page = client.get(PAGE).text
    select = re.search(r'<select name="ddlElection".*?</select>', page, re.S)[0]
    elections = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)', select)[1:]
    listing: dict[str, dict[str, object]] = {}
    for code, label in elections:
        form = dict(re.findall(r'<input type="hidden" name="(\w+)"[^>]*value="([^"]*)"', client.get(PAGE).text))
        official = client.post(PAGE, data=form | {"ddlElection": code, "ddlCategory": "1"}).text
        precinct = client.post(PAGE, data=form | {"ddlElection": code, "ddlCategory": "3"}).text
        files = sorted(set(re.findall(r'href="([^"]*SOV/[^"]*\.txt)"', precinct)))
        listing[code] = {"label": label.strip(), "files": files}
        if "lblOffice" in official:
            raw.store(
                SOURCE,
                official_url(code),
                official.encode(),
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )
        for url in files:
            raw.fetch(SOURCE, url, suffix=".txt")
    raw.store(
        SOURCE,
        LISTING,
        json.dumps(listing, indent=1).encode(),
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type="application/json",
    )


CHANNELS = {"Election Day": 1, "Early Vote": 2, "Mail": 3}
PARTY = re.compile(r"^([A-Z]{2,4}) ?- ?(.*)$")
BLOCK = re.compile(r'<span id="GridView3_ctl\d+_lblOffice">')
ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td><td[^>]*>(.*?)</td>", re.S)


def text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).replace("\xa0", " ").strip()


def person(raw_name: str) -> str:
    return re.sub(r"[^a-z]", "", PARTY.sub(r"\2", raw_name).lower())


def official(code: str) -> list[dict[str, object]]:
    page = raw.cached(SOURCE, official_url(code)).path.read_text(errors="replace")
    contests = []
    for block in BLOCK.split(page)[1:]:
        label = text(block.split("</span>")[0])
        seats = re.search(r"lblContestMsg\">(.*?)</span>", block, re.S)
        seats = re.search(r"\d+", text(seats[1])) if seats else None
        candidates = []
        for name, votes in ROW.findall(block):
            body = text(name)
            tally = re.sub(r"[^\d]", "", text(votes))
            if body in ("Candidate", "Total") or not tally:
                continue
            candidates.append({"name": body, "votes": int(tally), "bold": "<b>" in name})
        if candidates:
            contests.append({"label": label, "seats": int(seats[0]) if seats else None, "candidates": candidates})
    return contests


def precincts(url: str) -> list[tuple[str, int, int | None]]:
    lines = list(csv.reader(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    head = lines[0] if lines else []
    found = []
    if len(head) < 6:
        return found
    for line in lines[1:]:
        if len(line) != len(head):
            continue
        channel = 0 if line[1] == "Totals" else CHANNELS.get(line[1])
        if channel is None:
            continue
        for name, value in zip(head[5:], line[5:], strict=True):
            found.append((line[0], name, channel, int(value) if value.isdigit() else None))
    return found


SKIP = re.compile(
    r"^(PRESIDENT|UNITED STATES|US HOUSE|REPRESENTATIVE IN CONGRESS|GOVERNOR|LIEUTENANT|ATTORNEY GENERAL|SECRETARY OF STATE"
    r"|STATE (TREASURER|CONTROLLER|ASSEMBLY|SENATE|QUESTION|BOARD|BD)|MEMBER,? STATE|REGENT|BOARD OF REGENTS|CENTRAL NEVADA"
    r"|JUSTICE (OF (THE )?)?SUPREME|COURT OF APPEALS|STATE ASSEMBLY|MEMBERS? OF ASSEMBLY|INSPECTOR OF MINES|AMENDMENT|INITIATIVE|REFERENDUM"
    r"|CLERK OF (THE )?SUPREME|FISH AND GAME|BOARD OF EDUCATION|HOSPITAL TRUSTEE|COUNTY OFFICES NOT|FIRST POLITICAL|.*RESIDENCE -|LAS VEGAS VALLEY WATER|.*(ASSEMBLY|CONGRESS)|.*(TOWN ADVISORY|E-911|INCORPORATION))"
)
CITIES = {
    "NORTH LAS VEGAS": "North Las Vegas",
    "LAS VEGAS": "Las Vegas",
    "HENDERSON": "Henderson",
    "BOULDER CITY": "Boulder City",
    "MESQUITE": "Mesquite",
}
ABBREVIATIONS = (
    (r"\bT(WN)?SH?P\b", "TOWNSHIP"),
    (r"\bFAM DIV\b|\bFAMILY DIV\b", "FAMILY DIVISION"),
    (r"^MEMBER BOARD OF EDUCATION DISTRICT ", "TRUSTEE CLARK COUNTY SCHOOL DISTRICT "),
    (r"^CLARK COUNTY SCHOOL (DISTRICT )?TRUSTEE ", "TRUSTEE CLARK COUNTY SCHOOL "),
    (r"^CLARK COUNTY SCHOOL DISTRICT (?=[A-G]$)", "TRUSTEE CLARK COUNTY SCHOOL DISTRICT "),
    (r"^BOC ?- ?", "BOULDER CITY "),
    (r"\bCOMMISSI0N\b", "COMMISSION"),
    (r"^(CLARK COUNTY|COOUNTY) COMMISSION", "COUNTY COMMISSION"),
    (r"^TRUSTEE SCHOOL DISTRICT ", "TRUSTEE CLARK COUNTY SCHOOL DISTRICT "),
    (r"\bDEPT\b", "DEPARTMENT"),
    (r"\bDST\b", "DISTRICT"),
    (r"\bDIST\b", "DISTRICT"),
    (r"\bN LAS VEGAS\b", "NORTH LAS VEGAS"),
    (r"\bM V\b", "MOAPA VALLEY"),
    (r"\bV V\b", "VIRGIN VALLEY"),
    (r"\bFAMILY DIV\b", "FAMILY DIVISION"),
    (r"\bCOUNCILMAN\b", "COUNCIL MEMBER"),
    (r"\bCOUNCILMEMBER\b", "COUNCIL MEMBER"),
    (r"\bCOMMISSIONER\b", "COMMISSION"),
    (r"\bNUM\.?\b|\bNO\.?\b|\bNUMBER\b", "NUMBER"),
)
SPECIALS = (
    (r"MOAPA VALLEY WATER", "Moapa Valley Water District", "water"),
    (r"VIRGIN VALLEY WATER", "Virgin Valley Water District", "water"),
    (r"OVERTON POWER", "Overton Power District 5", "power"),
    (r"TV MAINTENANCE", "Moapa Valley Television Maintenance District", "other"),
    (r"BOULDER CITY LIBRARY", "Boulder City Library District", "library"),
    (r"HENDERSON LIBRAR", "Henderson District Public Libraries", "library"),
)
OFFICES = (
    (r"^(CLARK COUNTY )?SHERIFF", "sheriff", "Sheriff"),
    (r"^DISTRICT ATTORNEY", "district-attorney", "District Attorney"),
    (r"^COUNTY ASSESSOR", "assessor", "Assessor"),
    (r"^COUNTY CLERK", "clerk", "Clerk"),
    (r"^COUNTY RECORDER", "recorder", "Recorder"),
    (r"^COUNTY TREASURER", "treasurer", "Treasurer"),
    (r"^PUBLIC ADMINISTRATOR", "public-administrator", "Public Administrator"),
    (r"^COUNTY SURVEYOR", "surveyor", "Surveyor"),
)
PARTIES = {"DEM": "DEM", "REP": "REP", "LPN": "LBT", "LIB": "LBT", "IAP": "IAP", "NPP": "NPP", "GRN": "GRN"}


def classify(label: str, party: str | None) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", label.upper().replace(",", " ").replace(".", " ").replace("0N", "ON")).strip()
    lead = re.match(r"^(DEM|REP|NPP?|IAP|LPN) ?- ?", text)
    if lead:
        party = party or (lead[1] if lead[1] in ("DEM", "REP") else None)
        text = text[lead.end() :]
    text = re.sub(r" - (NP|DEM|REP)$| (NP|DEM|REP)$", "", text)
    text = re.sub(r"\s*\(UNOPPOSED\)", "", text)
    text = text.replace(" - ", " ").replace("-", " ").replace("  ", " ")
    term = "full"
    if re.search(r"UNEXPIRED|SHORT TERM|2[ -]?(YR|YEAR)", text):
        term = "remainder"
    text = re.sub(r"\s*\([^)]*(TERM|UNEXPIRED)[^)]*\)| (2|4)[ -]?(YR|YEAR) (UNEXPIRED )?TERM| (2|4)[ -]?(YR|YEAR)", "", text)
    text = re.sub(r"\s*\(\d+\)$", "", text).strip()
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)
    text = text.removeprefix("CITY OF ")
    if SKIP.match(text):
        return None
    base = {"party": party, "seat": None, "office": None, "city": None, "term": term}
    for pattern, shown, group in SPECIALS:
        if re.search(pattern, text):
            seat = re.search(r"(LOGANDALE|MOAPA GLENDALE|OVERTON|BUNKERVILLE|MESQUITE|AT LARGE|MOAPA|SEAT \d+)", text)
            kind = "measure" if re.search("QUESTION", text) else "board"
            return base | {"kind": "special", "jur": shown, "group": group, "type": kind, "seat": seat[1].title() if seat and kind == "board" else None}
    if re.search("QUESTION", text):
        number = re.search(r"(?:QUESTION|NUMBER) (?:NUMBER )?(\w+)$", text)
        for key, shown in CITIES.items():
            if key in text:
                return base | {"kind": "city", "jur": shown, "type": "measure", "code": number[1] if number else None}
        return base | {"kind": "county", "jur": "Clark County", "type": "measure", "code": number[1] if number else None}
    if text.startswith("TRUSTEE CLARK COUNTY SCHOOL"):
        seat = re.search(r"DISTRICT ([A-G])$", text)
        return base | {"kind": "school", "jur": "Clark County School District", "type": "board", "seat": seat[1] if seat else None}
    for pattern, office, title in OFFICES:
        if re.search(pattern, text):
            return base | {"kind": "county", "jur": "Clark County", "type": "office", "office": office, "title": title}
    commission = re.search(r"^COUNTY COMMISSION(?:ER)?(?: (?:DISTRICT|SEAT))? ?([A-G])?(?: .*)?$", text)
    if commission:
        return base | {"kind": "county", "jur": "Clark County", "type": "council", "seat": commission[1], "term": term}
    judge = re.search(r"^DISTRICT (?:COURT )?(?:JUDGE|COURT)(?: (FAMILY DIVISION))?(?: (DEPARTMENT|DISTRICT) (\w+))?$", text)
    if judge:
        return base | {
            "kind": "county",
            "jur": "Clark County",
            "type": "office",
            "office": "district-court-judge" + ("-family" if judge[1] else ""),
            "title": "District Court Judge" + (", Family Division" if judge[1] else ""),
            "seat": f"{judge[2].title()} {judge[3]}" if judge[2] else None,
            "term": term,
        }
    township = re.search(r"^(JUSTICE OF THE PEACE|CONSTABLE) ([A-Z ]+?)(?: TOWNSHIP)?(?: DEPARTMENT (\w+))?$", text)
    if township and township[2] not in ("", "TOWNSHIP"):
        shown = township[2].title()
        peace = township[1] != "CONSTABLE"
        return base | {
            "kind": "county",
            "jur": "Clark County",
            "type": "office",
            "office": ("justice-of-the-peace" if peace else "constable") + "-" + re.sub(r"\W+", "-", shown.lower()),
            "title": ("Justice of the Peace" if peace else "Constable") + f", {shown} Township",
            "seat": f"Department {township[3]}" if township[3] else None,
            "term": term,
        }
    for key, shown in CITIES.items():
        if re.search(rf"\b{key}\b", text):
            city = shown
            break
    else:
        city = None
    if re.search(r"\bMAYOR\b", text):
        return base | {"kind": "city", "jur": city, "type": "mayor"}
    if re.search(r"\bCOUNCIL\b", text):
        seat = re.search(r"(?:WARD|SEAT) (\w+)", text)
        return base | {
            "kind": "city",
            "jur": city,
            "type": "council",
            "seat": seat[1] if seat else ("At Large" if "AT LARGE" in text else None),
            "term": term,
        }
    court = re.search(r"MUNICIPAL (?:COURT )?JUDGE(?: DEPARTMENT (\w+))?", text)
    if court:
        return base | {
            "kind": "city",
            "jur": city,
            "type": "office",
            "office": "municipal-court-judge",
            "title": "Municipal Court Judge",
            "seat": f"Department {court[1]}" if court[1] else None,
            "term": term,
        }
    if city and re.search(r"BOND|TAX|IMPROVEMENT|ORDINANCE|CHARTER|ANNEX|CAPITAL|BALLOT|PROPOSITION|LIBRARY", text):
        return base | {"kind": "city", "jur": city, "type": "measure", "code": None}
    return {"unmatched": text}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    listing = json.loads(raw.cached(SOURCE, LISTING).path.read_text())
    contests = []
    values = []
    skipped: dict[str, int] = {}
    for code, entry in listing.items():
        stamp = re.search(r"\((\d\d)/(\d\d)/(\d{4})\)", entry["label"])
        if stamp is None:
            continue
        day = date(int(stamp[3]), int(stamp[1]), int(stamp[2]))
        try:
            blocks = official(code)
        except LookupError:
            blocks = []
        files = {}
        for url in entry["files"]:
            found = precincts(url)
            names = list(dict.fromkeys(name for _, name, _, _ in found))
            files[url] = (urllib.parse.unquote(url.rsplit("/", 1)[1][:-4]), found, names)
        taken: set[str] = set()
        entries = []
        for n, block in enumerate(blocks):
            sig = {person(c["name"]) for c in block["candidates"] if not re.match(r"(?i)none of these|write", c["name"])}
            best = None
            for url, (label, found, names) in files.items():
                if url in taken:
                    continue
                keys = {person(name) for name in names if not re.match(r"(?i)none of these|write", name)}
                if keys == sig:
                    tally: dict[str, int] = {}
                    for _, name, channel, value in found:
                        if channel == 0 and value is not None:
                            tally[name] = tally.get(name, 0) + value
                    gap = sum(
                        abs(tally.get(name, 0) - c["votes"])
                        for c in block["candidates"]
                        for name in names
                        if person(name) == person(c["name"])
                    )
                    if best is None or gap < best[0]:
                        best = (gap, url)
            if best is not None:
                taken.add(best[1])
            entries.append((f"{code}:{n}", block["label"], block, best[1] if best else None))
        for url, (label, found, names) in files.items():
            if url not in taken:
                entries.append((f"{code}:f{len(entries)}", label, None, url))
        for key, label, block, url in entries:
            file_label = files[url][0] if url else label
            prefixes = {m[1] for c in (block["candidates"] if block else []) if (m := PARTY.match(c["name"]))}
            suffix = re.search(r" - (DEM|REP)$| (DEM|REP)$", file_label.upper())
            party = PARTIES.get(next(iter(prefixes))) if "PRIMARY" in entry["label"] and len(prefixes) == 1 and next(iter(prefixes)) in ("DEM", "REP") else (suffix[1] or suffix[2] if suffix else None)
            found = classify(label if block else file_label, party)
            if found is None:
                continue
            if "unmatched" in found:
                skipped[label] = skipped.get(label, 0) + 1
                continue
            contests.append(
                {
                    "key": key,
                    "day": day,
                    "election": code,
                    "election_label": entry["label"],
                    "label": label,
                    "file_label": file_label,
                    "seats": block["seats"] if block else None,
                    "winners": [person(c["name"]) for c in block["candidates"] if c["bold"]] if block else None,
                    "has_precincts": url is not None,
                    **{k: v for k, v in found.items() if k != "group"},
                    "group": found.get("group"),
                }
            )
            if url is not None:
                for precinct, name, channel, value in files[url][1]:
                    if value is not None:
                        values.append((key, precinct, name, channel, value))
                if block is not None:
                    for c in block["candidates"]:
                        values.append((key, "OFFICIAL", c["name"], 0, c["votes"]))
            elif block is not None:
                for c in block["candidates"]:
                    values.append((key, "", c["name"], 0, c["votes"]))
        print(code, len(entries), flush=True)
    pl.DataFrame(contests, infer_schema_length=None).write_parquet(PARSED / "contests.parquet")
    pl.DataFrame(values, schema=["key", "precinct", "name", "channel", "votes"], orient="row").write_parquet(
        PARSED / "rows.parquet"
    )
    (PARSED / "skipped.txt").write_text("\n".join(f"{n}\t{label}" for label, n in sorted(skipped.items())))
    print(len(contests), "contests", len(values), "rows", len(skipped), "skipped labels")


TITLE = "Statement of Vote and Official Results"
FIPS = "32003"
PARTY_KEYS = {"DEM": "democratic-party", "REP": "republican-party", "LBT": "libertarian-party", "GRN": "green-party"}
PARTY_TITLES = {"DEM": "Democratic", "REP": "Republican"}
PLACE_CODES = {
    "Las Vegas": "3240000",
    "Henderson": "3231900",
    "North Las Vegas": "3251800",
    "Boulder City": "3206500",
    "Mesquite": "3246000",
}
SPECIAL_TYPES = {"water": "water", "power": "power", "library": "library", "other": "other"}
NOTE = (
    "Precinct totals from the Clark County Election Department's Statement of Vote, one text file per contest, with "
    "early, election day, and mail votes recorded separately for precincts where the county reports all three. The "
    "county suppresses the small counts of precincts with few ballots, so precinct sums can fall slightly short of "
    "the contest's totals. Outcomes are the county's official results, which print the winners in bold."
)
OFFICIAL_NOTE = (
    "County totals from the Clark County Election Department's official results page. The page prints the winners "
    "in bold, and those are recorded as the outcomes."
)


def candidate_name(raw_name: str) -> str:
    body = html.unescape(PARTY.sub(r"\2", raw_name)).strip()
    if "," not in body:
        return body
    last, _, first = body.partition(",")
    first, _, suffix = first.strip().partition(",")
    if first.strip().strip(".").upper() in ("JR", "SR", "II", "III", "IV"):
        first, suffix = suffix, first
    return " ".join(part for part in (first.strip(), last.strip(), suffix.strip()) if part)


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    rows = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Clark County's city, school, special district, judicial, and county contests from the Election Department's Statement of Vote and official results",
            polity="US-NV",
        )

        def name(entity: UUID, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-NV")
        state_area = cs.area("us.state", "32")
        county = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        county_polity = conn.execute(
            "select entity_id from external_id where scheme = 'us.polity' and value = 'county:32003'"
        ).fetchone()[0]
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")
        parties = {code: cs.key("us.party", key) for code, key in PARTY_KEYS.items()}
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=PAGE,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        for code in contests["election"].unique().to_list():
            try:
                cs.artifact(raw.cached(SOURCE, official_url(code)), source)
            except LookupError:
                pass
        for url in {u for entry in json.loads(raw.cached(SOURCE, LISTING).path.read_text()).values() for u in entry["files"]}:
            cs.artifact(raw.cached(SOURCE, url), source)

        existing = {}
        for pid, text_, code, path in conn.execute(
            """select p.id, n.text, x.value, s.path from polity p
               join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where x.value like any(%s)""",
            [["place:32%", "school:32%", "special:32003:%", "county:32003"]],
        ).fetchall():
            existing[text_.upper()] = (pid, code, path)
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s where s.path like 'us/nv/%%' and not exists (
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
                "select area_id, id from reporting_unit where area_relation = 'equals' and scheme_code in ('us.county', 'us.place', 'us.school_district', 'us.special_district')"
            ).fetchall()
        )

        unplaced = contests.filter((pl.col("kind") == "city") & pl.col("jur").is_null())
        if unplaced.height:
            footprints: dict[tuple[date, str], set[str]] = {}
            for row in contests.filter((pl.col("kind") == "city") & pl.col("jur").is_not_null()).iter_rows(named=True):
                if row["has_precincts"]:
                    footprints.setdefault((row["day"], row["jur"]), set()).update(
                        rows.filter(pl.col("key") == row["key"])["precinct"].to_list()
                    )
            placed = {}
            for row in unplaced.iter_rows(named=True):
                mine = set(rows.filter(pl.col("key") == row["key"])["precinct"].to_list())
                overlaps = sorted(
                    ((len(mine & precincts), city) for (day, city), precincts in footprints.items() if day == row["day"]),
                    reverse=True,
                )
                if overlaps and overlaps[0][0] > 0 and (len(overlaps) == 1 or overlaps[0][0] > overlaps[1][0]):
                    placed[row["key"]] = overlaps[0][1]
            contests = contests.with_columns(
                jur=pl.struct("key", "jur").map_elements(lambda r: r["jur"] or placed.get(r["key"]), return_dtype=pl.String)
            )

        report: dict[str, int] = {}
        made: dict[tuple[str, str], tuple[UUID, str, str]] = {}
        for kind, jur in sorted({(r["kind"], r["jur"]) for r in contests.iter_rows(named=True) if r["jur"]}):
            if kind == "county":
                made[(kind, jur)] = (county_polity, "county:32003", "us/nv/clark-county")
                continue
            found = existing.get(jur.upper())
            if found is not None:
                made[(kind, jur)] = found
                continue
            if kind == "city":
                code, polity_kind, scheme = f"place:{PLACE_CODES[jur]}", "municipality", "us.place"
            elif kind == "school":
                code, polity_kind, scheme = f"school:32:{slug(jur)}", "school_district", "us.school_district"
            else:
                group = next(g for pattern, shown, g in SPECIALS if shown == jur)
                code, polity_kind, scheme = f"special:{FIPS}:{group}:{slug(jur)}", "special_district", "us.special_district"
            pid = cs.key("us.polity", code)
            path = f"us/nv/{slug(jur)}"
            if path in taken:
                path = f"{path}-{'school-district' if kind == 'school' else 'district' if kind == 'special' else 'city'}"
            if path in taken:
                raise ValueError(f"{jur} would take {path}")
            taken.add(path)
            if pid in cs.minted:
                cs.add("polity", id=pid, kind=polity_kind, iso_code=None)
                name(pid, jur)
                cs.add("polity_parent", polity_id=pid, parent_id=county_polity if kind == "special" else state)
                cs.add("slug", path=path, entity_id=pid)
                area_code = code.partition(":")[2]
                area = cs.area(scheme, area_code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=area_code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=county[1] if kind == "special" else state_area)
                name(area, f"{jur}, NV")
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
               join polity pol on pol.id = e.polity_id
               join external_id x on x.entity_id = pol.id and x.scheme = 'us.polity'
               join changeset c on c.id = p.changeset_id
               where x.value like any(%s) and c.pipeline <> %s""",
            [["place:32%", "school:32%", "special:32003:%", "county:32003"], SOURCE],
        ).fetchall():
            parts = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")]
            if len(parts) >= 2:
                people.setdefault((parts[0], parts[-1]), []).append(person_id)

        elections: dict[tuple[date, UUID], tuple[UUID, str]] = {}
        owners: dict[tuple[UUID, str], UUID] = {}
        districts: set[str] = set()
        seen: set[str] = set()
        minted_units: set[str] = set()
        values: list[pl.DataFrame] = []
        known_names: dict[tuple[UUID, date], dict[UUID, list]] = {}
        superseding: list[tuple] = []
        made_people: set[UUID] = set()
        for row in contests.filter(pl.col("jur").is_not_null()).sort("day", "kind", "jur", "label").iter_rows(named=True):
            polity = made.get((row["kind"], row["jur"]))
            if polity is None:
                continue
            pid, code, path = polity
            shown = row["jur"]
            day = row["day"]
            frame = rows.filter(pl.col("key") == row["key"]).with_columns(
                display=pl.col("name").map_elements(
                    lambda n: "None of These Candidates"
                    if n.lower().startswith("none of these")
                    else "Write-in"
                    if n.lower().startswith("write")
                    else n.title()
                    if n.upper() in ("YES", "NO")
                    else candidate_name(n),
                    return_dtype=pl.String,
                )
            )
            official = frame.filter(pl.col("precinct") == "OFFICIAL")
            frame = frame.filter(pl.col("precinct") != "OFFICIAL")
            if not official.is_empty() and not set(official["display"]) <= set(frame["display"]):
                report["official names differ"] = report.get("official names differ", 0) + 1
                official = official.clear()
            sums = (official if not official.is_empty() else frame.filter(pl.col("channel") == 0)).group_by("display").agg(
                pl.col("votes").sum()
            ).filter(pl.col("votes") > 0)
            if sums.is_empty():
                sums = frame.group_by("display").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0)
            if sums.is_empty():
                report["no votes"] = report.get("no votes", 0) + 1
                continue
            existing_election = conn.execute(
                """select e.id, s.path from election e join slug s on s.entity_id = e.id and s.is_current
                   where e.polity_id = %s and e.date_from = %s order by e.date_to limit 1""",
                [pid, day],
            ).fetchone()
            if existing_election is not None:
                if (pid, day) not in known_names:
                    found = {}
                    for contest_id, contest_key_, label, kind, parent, option_id in conn.execute(
                        """select k.id, (select x.value from external_id x where x.entity_id = k.id and x.scheme = 'us.contest' limit 1),
                                  o.ballot_label, o.kind, o.parent_option_id, o.id from contest k
                           join election e on e.id = k.election_id
                           join changeset kc on kc.id = k.changeset_id and kc.pipeline <> %s
                           left join contest_option o on o.contest_id = k.id
                           where e.polity_id = %s and e.date_from = %s""",
                        [SOURCE, pid, day],
                    ).fetchall():
                        entry = found.setdefault(contest_id, [contest_key_, []])
                        if label is not None:
                            entry[1].append((label, kind, parent, option_id))
                    known_names[(pid, day)] = found
                surnames = {
                    c.split()[-1].lower()
                    for c in sums["display"]
                    if c not in ("Yes", "No", "Write-in", "None of These Candidates")
                }
                if surnames:
                    best = None
                    for contest_id, (contest_key_, listed) in known_names[(pid, day)].items():
                        theirs = {
                            re.sub(r"[^a-z]", "", label.split()[-1].lower())
                            for label, kind, parent, _ in listed
                            if kind in ("candidate", "ticket") and parent is None and label.split()
                        }
                        mine = {re.sub(r"[^a-z]", "", c) for c in surnames}
                        if mine <= theirs and (best is None or len(theirs) < best[0]):
                            best = (len(theirs), contest_id, contest_key_, listed)
                    if best is not None and row["has_precincts"] and best[2] is not None:
                        superseding.append((row, pid, sums, frame, official, best[1:]))
                        continue
                    if any(
                        label.split()[-1].lower() in surnames
                        for found in known_names[(pid, day)].values()
                        for label, _, _, _ in found[1]
                        if label.split()
                    ):
                        report["exists"] = report.get("exists", 0) + 1
                        continue
            if (day, pid) not in elections:
                if existing_election is not None:
                    elections[(day, pid)] = existing_election
                else:
                    election = cs.key("us.election", f"{day}:NV:{code.partition(':')[2].replace(':', '-')}")
                    word = (
                        "primary"
                        if "PRIMARY" in row["election_label"].upper() and "MUNICIPAL" not in row["election_label"].upper()
                        else "general"
                        if day.month == 11 and day.year % 2 == 0
                        else "local"
                    )
                    cs.add("election", id=election, polity_id=pid, kind=word, date_from=day, date_to=day, status="held")
                    name(election, f"{day.year} {shown} {word} election")
                    election_path = f"{path}/{day}-{word}"
                    cs.add("slug", path=election_path, entity_id=election)
                    elections[(day, pid)] = (election, election_path)
            election, election_path = elections[(day, pid)]

            key_body = code.partition(":")[2].replace(":", "-")
            party = row["party"]
            partisan_title = f" ({PARTY_TITLES[party]} primary)" if party in PARTY_TITLES else ""
            suffix = f":{party.lower()}" if party else ""
            area = None
            contest_kind, office_id, body_id = "office", None, None
            magnitude = row["seats"]
            answers = row["type"] == "measure"
            seat_text = row["seat"]
            if row["type"] in ("council", "board"):
                body_kind = "council" if row["type"] == "council" else "board"
                if row["kind"] == "county":
                    body_title, seat_label = "Clark County Commission", "district"
                elif row["type"] == "council":
                    body_title, seat_label = f"{shown} City Council", "ward"
                else:
                    body_title, seat_label = (
                        f"{shown} Board of Trustees" if row["kind"] == "school" else f"{shown} Board of Directors",
                        "seat",
                    )
                existing_body = conn.execute(
                    "select b.id from body b where b.polity_id = %s order by (b.kind = 'council') desc, b.id limit 1",
                    [pid],
                ).fetchone()
                if (pid, body_kind) not in owners:
                    owner = existing_body[0] if existing_body is not None else cs.key("us.body", f"NV:{key_body}:{body_kind}")
                    if existing_body is None:
                        cs.add("body", id=owner, polity_id=pid, kind="council")
                        name(owner, body_title)
                    owners[(pid, body_kind)] = owner
                body_id, contest_kind = owners[(pid, body_kind)], "body_seats"
                if seat_text is not None:
                    district_code = f"32:{key_body}:{body_kind}:district-{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if area in cs.minted and district_code not in districts:
                        districts.add(district_code)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        if pid in territories:
                            cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(area, f"{body_title}, {seat_label} {seat_text}")
                    magnitude = magnitude or 1
                contest_key = f"{day}:NV:{key_body}:{body_kind}" + (f":{slug(seat_text)}" if seat_text else "") + suffix
                contest_slug = body_kind + (f"-{slug(seat_text)}" if seat_text else "") + (f"-{party.lower()}" if party else "")
                contest_title = (
                    f"{day.year} {body_title}"
                    + (f", {seat_label} {seat_text}" if seat_text else "")
                    + " election"
                    + partisan_title
                )
            elif row["type"] in ("office", "mayor"):
                office = "mayor" if row["type"] == "mayor" else row["office"]
                title = "Mayor" if row["type"] == "mayor" else row["title"]
                office_key = f"NV:{key_body}:{office}"
                existing_office = conn.execute(
                    "select entity_id from external_id where scheme = 'us.office' and value = %s", [office_key]
                ).fetchone()
                if (pid, office) not in owners:
                    owner = existing_office[0] if existing_office is not None else cs.key("us.office", office_key)
                    if existing_office is None:
                        cs.add("office", id=owner, polity_id=pid, kind="mayor" if office == "mayor" else "other")
                        name(owner, f"{shown} {title}")
                    owners[(pid, office)] = owner
                office_id = owners[(pid, office)]
                if seat_text is not None:
                    district_code = f"32:{key_body}:{office}:district-{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if area in cs.minted and district_code not in districts:
                        districts.add(district_code)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        if pid in territories:
                            cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(area, f"{shown} {title}, {seat_text}")
                contest_key = f"{day}:NV:{key_body}:{office}" + (f":{slug(seat_text)}" if seat_text else "") + suffix
                contest_slug = office + (f"-{slug(seat_text)}" if seat_text else "") + (f"-{party.lower()}" if party else "")
                contest_title = (
                    f"{day.year} {shown} mayoral election"
                    if row["type"] == "mayor"
                    else f"{day.year} {shown} {title}" + (f", {seat_text}" if seat_text else "") + " election"
                ) + partisan_title
                magnitude = magnitude or 1
            else:
                code_text = row.get("code") or "question"
                contest_kind = "ballot_question"
                contest_key = f"{day}:NV:{key_body}:measure-{slug(code_text)}"
                contest_slug = f"measure-{slug(code_text)}"
                contest_title = f"{day.year} {shown} " + (f"Measure {code_text}" if code_text.isdigit() else "ballot question")
                magnitude = None
            if row["term"] != "full" and not answers:
                contest_key += ":remainder"
                contest_slug += "-unexpired"
            if f"{election_path}/{contest_slug}" in taken or contest_key in seen:
                report["exists"] = report.get("exists", 0) + 1
                continue
            seen.add(contest_key)
            taken.add(f"{election_path}/{contest_slug}")
            contest = cs.key("us.contest", contest_key)
            if area is None:
                area = territories.get(pid)
            system = None if magnitude is None or answers else systems["block" if magnitude > 1 else "fptp"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=contest_kind,
                office_id=office_id,
                body_id=body_id,
                district_area_id=area,
                magnitude=None if answers else magnitude,
                round_number=1,
                party_scope_id=parties.get(party),
                electoral_system_id=system,
                default_result_kind="counted",
                term=None if answers else row["term"],
            )
            name(contest, contest_title)
            cs.add("slug", path=f"{election_path}/{contest_slug}", entity_id=contest)

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
                is_default=True,
                completeness=1 if row["kind"] != "special" else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE if row["has_precincts"] else OFFICIAL_NOTE)

            options: dict[str, UUID] = {}
            ranked = sums.sort("votes", descending=True)
            ordered = ["Yes", "No"] if answers else ranked["display"].to_list()
            ordered = [c for c in ordered if c in set(sums["display"])]
            ordered += [c for c in ranked["display"] if c not in ordered]
            raw_names = dict(frame.select("display", "name").unique(subset=["display"]).iter_rows())
            candidacies: dict[str, UUID] = {}
            for position, candidate in enumerate(ordered, start=1):
                option = options[candidate] = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                if answers:
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=candidate,
                    )
                    continue
                if candidate in ("Write-in", "None of These Candidates"):
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate == "Write-in" else "none_of_the_above",
                        ballot_label=candidate,
                    )
                    continue
                prefix = PARTY.match(raw_names[candidate])
                option_party = PARTIES.get(prefix[1]) if prefix else party
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_label=candidate,
                    party_id=parties.get(option_party),
                )
                parts = [
                    w for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
                ]
                matched = people.get((parts[0], parts[-1])) if len(parts) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person_id = matched[0]
                else:
                    person_id = cs.key(f"{SOURCE}.person", f"NV:{slug(candidate)}:{key_body}")
                    if person_id in cs.minted and person_id not in made_people:
                        made_people.add(person_id)
                        cs.add("person", id=person_id)
                        name(person_id, candidate)
                candidacies[candidate] = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head")
                cs.add(
                    "candidacy",
                    id=candidacies[candidate],
                    person_id=person_id,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=option_party in ("IAP", "NPP"),
                )
            if answers:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            winners = row["winners"] or []
            if winners:
                by_person = {person(raw_names[c]): c for c in ordered if c in raw_names}
                chosen = [by_person[w] for w in winners if w in by_person]
                total = sums["votes"].sum()
                tally = dict(sums.iter_rows())
                if answers:
                    outcome = "won"
                elif party in PARTY_TITLES or (
                    "PRIMARY" in row["election_label"].upper()
                    and not (len(chosen) == 1 and tally.get(chosen[0], 0) * 2 > total)
                ):
                    outcome = "advanced"
                else:
                    outcome = "elected"
                for candidate in chosen:
                    cs.add("outcome", result_set_id=result_set, option_id=options[candidate], kind=outcome, seats=None)

            unit_ids: dict[str, UUID] = {}
            for precinct in frame["precinct"].unique().to_list():
                if precinct == "":
                    unit_ids[precinct] = polity_units.get(territories.get(pid)) or county[0]
                    continue
                unit_code = f"{day}:NV:{FIPS}:CLARK:{precinct}"
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
            lines = (
                frame.filter(pl.col("display").is_in(list(options)))
                .group_by("precinct", "display", "channel")
                .agg(pl.col("votes").sum())
            )
            complete = (
                lines.filter(pl.col("channel") > 0)
                .group_by("precinct", "display")
                .agg(pl.col("channel").n_unique().alias("channels"))
                .group_by("precinct")
                .agg(pl.col("channels").min().alias("least"), pl.col("display").n_unique().alias("options"))
                .filter((pl.col("least") == 3) & (pl.col("options") == len(options)))["precinct"]
                .to_list()
            )
            lines = lines.filter(
                pl.when(pl.col("precinct").is_in(complete)).then(pl.col("channel") > 0).otherwise(pl.col("channel") == 0)
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
            if not official.is_empty():
                residual = (
                    official.group_by("display")
                    .agg(pl.col("votes").sum())
                    .join(
                        frame.filter(pl.col("channel") == 0).group_by("display").agg(pl.col("votes").sum()),
                        on="display",
                        how="left",
                        suffix="_precincts",
                    )
                    .with_columns(votes=pl.col("votes") - pl.col("votes_precincts").fill_null(0))
                    .filter((pl.col("votes") > 0) & pl.col("display").is_in(list(options)))
                )
                if not residual.is_empty():
                    unit_code = f"{day}:NV:{FIPS}:CLARK:UNREPORTED:{slug(contest_key)}"
                    unit = cs.unit("us.precinct", unit_code)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=state,
                            scheme_code="us.precinct",
                            code=unit_code,
                            parent_id=county[0],
                            area_relation="within",
                            area_id=county[1],
                            kind="aggregate",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                    values.append(
                        residual.select(
                            result_set_id=pl.lit(str(result_set)),
                            unit_id=pl.lit(str(unit)),
                            option_id=pl.col("display").replace_strict({k: str(v) for k, v in options.items()}),
                            channel=pl.lit(0, pl.Int16),
                            measure=pl.lit(1, pl.Int16),
                            count_round=pl.lit(1, pl.Int16),
                            flags=pl.lit(32, pl.Int16),
                            value=pl.col("votes"),
                        )
                    )
            report["new"] = report.get("new", 0) + 1

        misses: list[str] = []
        taken_targets: set[UUID] = set()
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
        for row, pid, sums, frame, official, (contest, contest_key, listed) in superseding:
            day = row["day"]
            if contest in taken_targets:
                report["supersede duplicate target"] = report.get("supersede duplicate target", 0) + 1
                continue
            existing_options = {}
            for label, kind, parent, option_id in listed:
                if parent is None and label.split():
                    words = re.sub(r"[^a-z ]", " ", label.lower()).split()
                    existing_options.setdefault((words[-1], words[0][:1]), option_id)
            chosen: dict[str, UUID] = {}
            missed = []
            for candidate in sums["display"]:
                if candidate in ("Write-in", "None of These Candidates"):
                    kind = "write_in" if candidate == "Write-in" else "none_of_the_above"
                    found = next((option_id for label, k, parent, option_id in listed if k == kind), None)
                    chosen[candidate] = found or cs.key("us.option", f"{contest_key}:{SOURCE}:{slug(candidate)}")
                    continue
                words = re.sub(r"[^a-z ]", " ", candidate.lower()).split()
                match = existing_options.get((words[-1], words[0][:1]))
                if match is None:
                    missed.append(candidate)
                else:
                    chosen[candidate] = match
            if missed:
                misses += [f"{contest_key}: {name_}" for name_ in missed]
                report["supersede skipped"] = report.get("supersede skipped", 0) + 1
                continue
            taken_targets.add(contest)
            prior = conn.execute(f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]).fetchone()
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            replaced = None
            if prior is not None and prior[0] == result_set:
                replaced = conn.execute("select supersedes_id from result_set where id = %s", [prior[0]]).fetchone()[0]
                prior = None
            if prior is not None:
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded")
            cs.add(
                "result_set",
                id=result_set,
                election_id=conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1 if row["kind"] != "special" else None,
                supersedes_id=prior[0] if prior else replaced,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            for candidate, option in chosen.items():
                if candidate in ("Write-in", "None of These Candidates") and option in cs.minted:
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate == "Write-in" else "none_of_the_above",
                        ballot_label=candidate,
                    )
            raw_names = dict(frame.select("display", "name").unique(subset=["display"]).iter_rows())
            winners = row["winners"] or []
            if winners:
                by_person = {person(raw_names[c]): c for c in chosen if c in raw_names}
                picked = [by_person[w] for w in winners if w in by_person]
                total = sums["votes"].sum()
                tally = dict(sums.iter_rows())
                outcome = (
                    "advanced"
                    if row["party"] in PARTY_TITLES
                    or (
                        "PRIMARY" in row["election_label"].upper()
                        and not (len(picked) == 1 and tally.get(picked[0], 0) * 2 > total)
                    )
                    else "elected"
                )
                for candidate in picked:
                    cs.add("outcome", result_set_id=result_set, option_id=chosen[candidate], kind=outcome, seats=None)
            unit_ids = {}
            for precinct in frame["precinct"].unique().to_list():
                unit_code = f"{day}:NV:{FIPS}:CLARK:{precinct}"
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
            lines = frame.filter(pl.col("display").is_in(list(chosen))).group_by("precinct", "display", "channel").agg(pl.col("votes").sum())
            complete = (
                lines.filter(pl.col("channel") > 0)
                .group_by("precinct", "display")
                .agg(pl.col("channel").n_unique().alias("channels"))
                .group_by("precinct")
                .agg(pl.col("channels").min().alias("least"), pl.col("display").n_unique().alias("options"))
                .filter((pl.col("least") == 3) & (pl.col("options") == len(chosen)))["precinct"]
                .to_list()
            )
            lines = lines.filter(
                pl.when(pl.col("precinct").is_in(complete)).then(pl.col("channel") > 0).otherwise(pl.col("channel") == 0)
            )
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                    option_id=pl.col("display").replace_strict({k: str(v) for k, v in chosen.items()}),
                    channel=pl.col("channel").cast(pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            if not official.is_empty():
                residual = (
                    official.group_by("display")
                    .agg(pl.col("votes").sum())
                    .join(
                        frame.filter(pl.col("channel") == 0).group_by("display").agg(pl.col("votes").sum()),
                        on="display",
                        how="left",
                        suffix="_precincts",
                    )
                    .with_columns(votes=pl.col("votes") - pl.col("votes_precincts").fill_null(0))
                    .filter((pl.col("votes") > 0) & pl.col("display").is_in(list(chosen)))
                )
                if not residual.is_empty():
                    unit_code = f"{day}:NV:{FIPS}:CLARK:UNREPORTED:{slug(contest_key)}"
                    unit = cs.unit("us.precinct", unit_code)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=state,
                            scheme_code="us.precinct",
                            code=unit_code,
                            parent_id=county[0],
                            area_relation="within",
                            area_id=county[1],
                            kind="aggregate",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                    values.append(
                        residual.select(
                            result_set_id=pl.lit(str(result_set)),
                            unit_id=pl.lit(str(unit)),
                            option_id=pl.col("display").replace_strict({k: str(v) for k, v in chosen.items()}),
                            channel=pl.lit(0, pl.Int16),
                            measure=pl.lit(1, pl.Int16),
                            count_round=pl.lit(1, pl.Int16),
                            flags=pl.lit(32, pl.Int16),
                            value=pl.col("votes"),
                        )
                    )
            report["superseded"] = report.get("superseded", 0) + 1
        (PARSED / "supersede-misses.txt").write_text("\n".join(misses))
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
