import csv
import html
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.maricopa"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FOLDERS = [Path(__file__).parents[4] / "data/inbox/manual" / mf for mf in ("MF-081", "MF-082")]
URL = "https://elections.maricopa.gov/results-and-data/historic-results.html?year={year}#{name}"


SKIP = re.compile(
    r"PRESIDENT|CONGRESS|U\.?S\.? ?(?:SEN|REP|HOUSE)|UNITED STATES|STATE (?:REP|SENAT)|\bSENATOR\b|LEGISLAT|GOVERNOR|"
    r"SEC(?:RETARY|\.) OF STATE|ATTORNEY GENERAL|STATE TREASURER|MINE INSPECTOR|CORP(?:ORATION)? COMM|"
    r"PUBLIC INSTR|SUP OF PUBLIC|SUPREME COURT|APPEALS COURT|COURT OF APPEALS|CMTMN|PCCM|COMMITTEE|REGISTRATION|"
    r"TURNOUT|ELECTORS|^PROP(?:OSITION)?\s*\d+\b|^STATE |^US |^DISTRICT \d|^AMENDMENT|^REPRESENTATIVE|^UNITED|"
    r"PARTISAN NOTICE|^SENATE|^HOUSE"
)
NAMED_DISTRICTS = {
    "ACACIA": "PEORIA",
    "MESQUITE": "PEORIA",
    "PALO VERDE": "PEORIA",
    "PINE": "PEORIA",
    "WILLOW": "PEORIA",
    "IRONWOOD": "PEORIA",
    "BARREL": "GLENDALE",
    "CACTUS": "GLENDALE",
    "CHOLLA": "GLENDALE",
    "OCOTILLO": "GLENDALE",
    "SAHUARO": "GLENDALE",
    "YUCCA": "GLENDALE",
}
SURPRISE_DISTRICTS = {"ACACIA", "COTTONWOOD", "MESQUITE", "MULBERRY", "PALM", "PALO", "VERDE"}
INSTITUTIONS = {
    "MCCCD": ("school", "MARICOPA COUNTY COMMUNITY COLLEGE DISTRICT"),
    "MC COMMUNITY COLLEGE": ("school", "MARICOPA COUNTY COMMUNITY COLLEGE DISTRICT"),
    "WESTMEC": ("school", "WESTERN MARICOPA EDUCATION CENTER"),
    "WEST-MEC": ("school", "WESTERN MARICOPA EDUCATION CENTER"),
    "EVIT": ("school", "EAST VALLEY INSTITUTE OF TECHNOLOGY DISTRICT"),
    "MC SPHCD": ("special", "MARICOPA COUNTY SPECIAL HEALTH CARE DISTRICT"),
    "MC SPECIAL HEALTH": ("special", "MARICOPA COUNTY SPECIAL HEALTH CARE DISTRICT"),
    "MC SPECIAL HEALTHCARE": ("special", "MARICOPA COUNTY SPECIAL HEALTH CARE DISTRICT"),
    "MCSHCD": ("special", "MARICOPA COUNTY SPECIAL HEALTH CARE DISTRICT"),
    "CAWCD": ("special", "CENTRAL ARIZONA WATER CONSERVATION DISTRICT"),
}
SCHOOL_NUMBERS = {
    "60": "HIGLEY",
    "97": "DEER VALLEY",
    "4": "MESA",
    "90": "SADDLE MOUNTAIN",
    "69": "PARADISE VALLEY",
    "48": "SCOTTSDALE",
}
COUNTY_OFFICES = {
    "ASSESSOR": ("assessor", "Assessor"),
    "COUNTY ASSESSOR": ("assessor", "Assessor"),
    "COUNTY ATTORNEY": ("county-attorney", "County Attorney"),
    "COUNTY ATTORNEW": ("county-attorney", "County Attorney"),
    "COUNTY RECORDER": ("recorder", "Recorder"),
    "COUNTY SCH SUPERINTENDENT": ("school-superintendent", "School Superintendent"),
    "COUNTY SCHOOL SUPERINTENDENT": ("school-superintendent", "School Superintendent"),
    "SHERIFF": ("sheriff", "Sheriff"),
    "COUNTY SHERIFF": ("sheriff", "Sheriff"),
    "TREASURER": ("treasurer", "Treasurer"),
    "COUNTY TREASURER": ("treasurer", "Treasurer"),
    "CLERK OF THE SUPERIOR COURT": ("clerk-of-the-superior-court", "Clerk of the Superior Court"),
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LBT": "libertarian-party",
    "GRN": "green-party",
}
ABBREVIATIONS = (
    (r"\bELEM\b|\bESD\b", "ELEMENTARY"),
    (r"\bUNIF\b|\bUSD\b", "UNIFIED"),
    (r"\bUHSD\b|\bHSD\b", "UNION HIGH"),
    (r"\bFTN\b", "FOUNTAIN"),
    (r"\bMTN\b", "MOUNTAIN"),
    (r"\bPHX\b", "PHOENIX"),
    (r"\bCNTY\b", "COUNTY"),
    (r"\bMED\b", "MEDICAL"),
    (r"\bVALY\b|\bVLY\b", "VALLEY"),
    (r"\bDIST\b|\bDST\b", "DISTRICT"),
    (r"\bBRD\b", "BOARD"),
)
FILLER = {"DISTRICT", "SCHOOL", "OF", "THE", "AND", "NO", "MARICOPA", "CITY", "TOWN"}
ROLES = (
    ("recall", re.compile(r"\bRECALL\b")),
    ("measure", re.compile(r"\b(?:QUESTION|PROPS?(?:OSITION)?|PROPSITION)\b")),
    ("mayor", re.compile(r"\bMAYOR\b")),
    ("council", re.compile(r"\bCOUNCIL(?:MEMBER)?\b")),
    ("office", re.compile(r"\bFREEHOLDERS\b|\bCHIEF\b")),
    (
        "board",
        re.compile(
            r"\bGOV(?:ERNING)? ?BOARD\b|\bGBM\b|\bFIRE ?BOARD\b|\bBOARD MEMBER\b|\bBOARD OF DIRECTORS\b|\bBOARD\b"
        ),
    ),
)


def registry() -> dict[str, list[tuple[str, str, frozenset[str], str | None]]]:
    cog = pl.read_parquet(raw.parsed("us.cog") / "governments.parquet")
    found: dict[str, list[tuple[str, str, frozenset[str], str | None]]] = {"city": [], "school": [], "special": []}
    for name, place, county in (
        cog.filter((pl.col("state_fips") == "04") & (pl.col("kind") == "municipality"))
        .select("name", "place_fips", "county_fips")
        .iter_rows()
    ):
        key = re.sub(r"^(?:CITY|TOWN) OF ", "", name)
        found["city"].append((place, key, frozenset(key.replace("-", " ").split()), county))
    for kind, label in (("school", "school"), ("special", "special")):
        for name, county in (
            cog.filter((pl.col("state_fips") == "04") & (pl.col("kind") == kind))
            .select("name", "county_fips")
            .iter_rows()
        ):
            if county != "013":
                continue
            words = re.sub(r"[()&#.,']", " ", normalize(name)).split()
            number = next((w for w in words if w.isdigit()), None)
            found[label].append(
                (number or "", name, frozenset(w for w in words if not w.isdigit() and w not in FILLER), number)
            )
    for (name,) in (
        connect()
        .execute(
            """select n.text from polity p join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
           join name n on n.entity_id = p.id and n.is_preferred where x.value like 'special:04013:%'"""
        )
        .fetchall()
    ):
        words = re.sub(r"[()&#.,']", " ", normalize(name)).split()
        tokens = frozenset(w for w in words if w not in FILLER)
        if all(tokens != known[2] for known in found["special"]):
            found["special"].append(("", name.upper(), tokens, None))
    return found


def normalize(text: str) -> str:
    text = html.unescape(text).upper().replace("N0.", "NO.")
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text)


def classify(
    label: str, vote_for: int, registry_: dict[str, list[tuple[str, str, frozenset[str], str | None]]]
) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", html.unescape(label)).strip()
    text = re.sub(r"-?Term Exp\w*\s+[A-Za-z]+ \d+, \d{4}(?=-|$)", "", text, flags=re.IGNORECASE).strip("- ")
    party = re.match(r"^(DEM|REP|LBT|GRN)(?: ?- ?| (?=[A-Za-z]))(.*)$", text)
    scope = None
    if party is not None:
        scope, text = party[1], party[2].strip()
    seats = re.search(r"\s*\(vote(?: for)? (\d+)\)?\s*$", text, flags=re.IGNORECASE)
    if seats is not None:
        vote_for = vote_for or int(seats[1])
        text = text[: seats.start()]
    upper = normalize(text)
    if SKIP.search(upper):
        return None
    row: dict[str, object] = {
        "party": scope,
        "term": "remainder" if re.search(r"\b2 ?YR\b|UNEXPIRED|VACANCY", upper) else "full",
        "seat": None,
        "code": None,
        "seats": vote_for or None,
    }
    judge = re.match(r"^SUPERIOR COURT (.+)$", upper)
    if judge is not None:
        return row | {
            "kind": "county",
            "jur": "MARICOPA",
            "type": "retention",
            "judge": text[len("SUPERIOR COURT ") :].strip(),
        }
    if upper in COUNTY_OFFICES:
        office, title = COUNTY_OFFICES[upper]
        return row | {"kind": "county", "jur": "MARICOPA", "type": "office", "office": office, "title": title}
    supervisor = re.search(r"\bSUPERVISORS?\b\D*?(\d)\b|\bCOUNTY SUPERVISOR DISTRICT ?(\d)", upper)
    if supervisor is not None:
        return row | {"kind": "county", "jur": "MARICOPA", "type": "board", "seat": supervisor[1] or supervisor[2]}
    precinct = re.match(r"^(JP|CONSTABLE) ?-+ ?(.+)$", upper)
    if precinct is not None:
        justice = precinct[1] == "JP"
        return row | {
            "kind": "county",
            "jur": "MARICOPA",
            "type": "office",
            "office": "justice-of-the-peace" if justice else "constable",
            "title": "Justice of the Peace" if justice else "Constable",
            "seat": precinct[2].title(),
        }
    role = next((name for name, pattern in ROLES if pattern.search(upper)), None)
    institution = next((i for i in INSTITUTIONS if re.search(rf"(?:^|(?<=\W)){re.escape(i)}\b", upper)), None)
    if institution is not None:
        kind, name = INSTITUTIONS[institution]
        district = re.search(r"\bDISTRICT ?(\d)\b", upper)
        measure = re.search(r"\b(?:QUESTION|PROPS?(?:OSITION)?)\b\s*#?\s*(\d+)?", upper)
        if measure is not None:
            return row | {"kind": kind, "jur": name, "type": "measure", "code": measure[1] or "question"}
        return row | {"kind": kind, "jur": name, "type": "board", "seat": district[1] if district else None}
    if role is None and vote_for and re.search(r"\bFIRE\b|\bSANITARY\b", upper):
        role = "board"
    if role is None:
        return None
    code = None
    rest = upper
    if role == "measure":
        measure = re.search(r"\b(?:QUESTION|PROPS?(?:OSITION)?|PROPSITION)\b\s*#?\s*(\d+)?", upper)
        code = measure[1] or "question"
        rest = upper[: measure.start()] + " " + upper[measure.end() :]
    elif role == "recall":
        rest, _, code = upper.partition("RECALL")
        code = code.strip(" -") or rest.strip(" -")
        rest = upper.replace("RECALL", " ")
    for _, pattern in ROLES:
        rest = pattern.sub(" ", rest)
    number = re.search(r"(?:#|NO\.?)\s*(\d+)\b", rest)
    ward = re.search(r"\bWARD (\d)\b", rest)
    seat = re.search(r"\bDISTRICT ?#?(\d+)\b", rest)
    rest = re.sub(
        r"(?:#|NO\.?)\s*\d+|\bWARD \d\b|\bDISTRICT ?#?\d+\b|\bGOV\b|\bSCH\b|\bMEMBER\b|\b[24] ?YR\b|-?\b[24]YR\b|[-#&(),]|\bTERM TO EXPIRE.*",
        " ",
        rest,
    )
    rest = re.sub(r"\bAT LARGE\b", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    named_seat = next((n for n in NAMED_DISTRICTS if re.search(rf"\b{n}\b", rest)), None)
    kind, jur = None, None
    if number is not None and role not in ("council", "mayor"):
        for entry in registry_["school"]:
            if entry[3] == number[1]:
                kind, jur = "school", entry[1]
        if jur is None and number[1] in SCHOOL_NUMBERS:
            for entry in registry_["school"]:
                if entry[1].startswith(SCHOOL_NUMBERS[number[1]]):
                    kind, jur = "school", entry[1]
    words = frozenset(re.sub(r"\b(?:CITY|TOWN) OF\b", " ", rest).replace("-", " ").split()) - {"DISTRICT"}
    special_words = words | ({"FIRE"} if "FIRE" in upper else set())
    if jur is None and role == "council" and seat is not None and words and words <= SURPRISE_DISTRICTS:
        kind, jur = "city", "SURPRISE"
    if (
        jur is None
        and role in ("council", "mayor")
        and words <= {*(named_seat or "").split()}
        and named_seat is not None
    ):
        kind, jur = "city", next(e[1] for e in registry_["city"] if e[1] == NAMED_DISTRICTS[named_seat])
    if jur is None:
        candidates = []
        for group in ("city", "school", "special"):
            for _, name, tokens, home in registry_[group]:
                if group == "special" and role not in ("board", "office", "measure", "recall"):
                    continue
                if group == "city" and named_seat and not words <= {named_seat, *named_seat.split(), *tokens}:
                    continue
                if tokens and (tokens <= words if group != "special" else tokens == (special_words - FILLER)):
                    candidates.append((len(tokens), home != "013" if group == "city" else False, group, name))
        if role in ("council", "mayor"):
            candidates = [c for c in candidates if c[2] == "city"]
        candidates.sort(key=lambda c: (-c[0], c[2] != "city", c[1]))
        if candidates and (
            len(candidates) == 1
            or (-candidates[0][0], candidates[0][1]) < (-candidates[1][0], candidates[1][1])
            or (candidates[0][2] == "city" and role == "measure")
        ):
            _, _, kind, jur = candidates[0]
    if jur is None:
        return None
    if kind == "city" and role in ("council", "board"):
        tail = " ".join(w for w in words if w not in next(e[2] for e in registry_["city"] if e[1] == jur))
        if seat is not None:
            row["seat"] = seat[1]
        elif tail:
            row["seat"] = tail.title()
    elif seat is not None:
        row["seat"] = seat[1]
    if ward is not None:
        row["seat"] = ward[1]
    kind_type = {
        "council": "council",
        "mayor": "mayor",
        "measure": "measure",
        "recall": "recall",
        "board": "board",
        "office": "office",
    }[role]
    if role == "office":
        office = "freeholders" if "FREEHOLDERS" in upper else "fire-chief"
        return row | {
            "kind": kind,
            "jur": jur,
            "type": "office",
            "office": office,
            "title": "Board of Freeholders" if office == "freeholders" else "Fire Chief",
        }
    return row | {"kind": kind, "jur": jur, "type": kind_type, "code": code}


def files() -> list[tuple[date, Path]]:
    found = []
    for folder in FOLDERS:
        for path in sorted(folder.glob("*.txt")):
            stamp = re.match(r"(\d+)-(\d+)-(\d{4})", path.name)
            if stamp is None or "Summary" in path.name or "Unofficial" in path.name or "Export" in path.name:
                continue
            found.append((date(int(stamp[3]), int(stamp[1]), int(stamp[2])), path))
    return found


def fetch() -> None:
    for day, path in files():
        raw.store(
            SOURCE,
            URL.format(year=day.year, name=path.name),
            path.read_bytes(),
            suffix=".txt",
            retrieved_at=datetime(2026, 10, 25, tzinfo=UTC),
            media_type="text/plain",
        )


def parse() -> None:
    csv.field_size_limit(10**9)
    PARSED.mkdir(parents=True, exist_ok=True)
    total = 0
    for day, path in files():
        rows = []
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            head = next(reader)
            if len(head) < 5:
                continue
            index = {name: i for i, name in enumerate(head)}
            if "CONTEST_FULL_NAME" in index:
                contest, precinct, candidate = (
                    index["CONTEST_FULL_NAME"],
                    index["PRECINCT_NAME"],
                    index["CANDIDATE_FULL_NAME"],
                )
                votes, kind, write_in, vote_for = (
                    index["TOTAL"],
                    index["CONTEST_TYPE"],
                    index["IS_WRITEIN"],
                    index["contest_vote_for"],
                )
                for line in reader:
                    if len(line) > votes and line[kind] != "-2":
                        rows.append(
                            (
                                day,
                                line[contest],
                                int(line[vote_for] or 0),
                                line[precinct],
                                line[candidate],
                                None,
                                int(line[votes] or 0),
                                line[write_in] == "1",
                            )
                        )
            else:
                contest, precinct, candidate = index["ContestName"], index["PrecinctName"], index["CandidateName"]
                votes, kind, party, vote_for = (
                    index["Votes"],
                    index["ContestType"],
                    index["CandidateAffiliation"],
                    index["ContestVoteFor"],
                )
                for line in reader:
                    if len(line) > votes and line[kind] in ("Candidacy", "Measure") and line[votes]:
                        rows.append(
                            (
                                day,
                                line[contest],
                                int(line[vote_for] or 0),
                                line[precinct],
                                line[candidate],
                                line[party] or None,
                                int(line[votes]),
                                line[candidate].lower().startswith("write"),
                            )
                        )
        frame = pl.DataFrame(
            rows,
            schema=["day", "contest", "vote_for", "precinct", "candidate", "party", "votes", "write_in"],
            orient="row",
        ).unique(subset=["day", "contest", "precinct", "candidate"], keep="first", maintain_order=True)
        frame.write_parquet(PARSED / f"{day}-{path.stem[:40].replace(' ', '_')}.parquet")
        total += frame.height
        print(path.name, frame.height, flush=True)
    print(total, "rows")
    frames = [
        pl.read_parquet(path).with_columns(pl.col("party").cast(pl.String))
        for path in sorted(PARSED.glob("2*.parquet"))
    ]
    everything = pl.concat(frames)
    index = registry()
    labels = []
    skipped = set()
    for day, contest, seats in everything.group_by("day", "contest").agg(pl.col("vote_for").max()).iter_rows():
        found = classify(contest, seats, index)
        if found is None:
            skipped.add(contest)
            continue
        labels.append({"day": day, "contest": contest, **found})
    table = pl.DataFrame(labels, infer_schema_length=None)
    table.write_parquet(PARSED / "labels.parquet")
    (PARSED / "skipped.txt").write_text("\n".join(sorted(skipped)))
    measures = set(table.filter(pl.col("type").is_in(["measure", "recall", "retention"]))["contest"].to_list())
    names: dict[tuple[str, bool], tuple[str | None, str, bool]] = {}
    for candidate in everything.join(table.select("day", "contest"), on=["day", "contest"])["candidate"].unique():
        for is_measure in (False, True):
            prefix = re.match(r"^([A-Z]{2,4})? ?- (.*)$", candidate)
            code, body = (prefix[1], prefix[2]) if prefix is not None else (None, candidate)
            body = body.strip()
            if is_measure:
                tail = re.search(r"\b(YES|FOR|NO|AGAINST)$", body.upper())
                body = (
                    "Yes"
                    if tail is not None and tail[1] in ("YES", "FOR")
                    else "No"
                    if tail is not None
                    else body.title()
                )
            elif body.lower().startswith("write") or re.search(
                r"print name of write|not qualified", body, re.IGNORECASE
            ):
                body = "Write-in"
            elif "," in body:
                last, _, first = body.partition(",")
                first, _, suffix = first.strip().partition(",")
                body = f"{first.strip()} {last.strip()}" + (f" {suffix.strip()}" if suffix else "")
                body = body.title()
            names[(candidate, is_measure)] = (code, body, body == "Write-in")
    keys = pl.DataFrame(
        [(c, m, *v) for (c, m), v in names.items()],
        schema=["candidate", "is_measure", "prefix", "name", "write_in_flag"],
        orient="row",
    )
    rows = (
        everything.join(table.select("day", "contest"), on=["day", "contest"])
        .with_columns(is_measure=pl.col("contest").is_in(list(measures)))
        .join(keys, on=["candidate", "is_measure"])
        .select(
            "day",
            "contest",
            "precinct",
            "name",
            "votes",
            "write_in_flag",
            party=pl.coalesce(pl.col("party"), pl.col("prefix")),
        )
        .unique(subset=["day", "contest", "precinct", "name", "party"], keep="first", maintain_order=True)
    )
    rows.write_parquet(PARSED / "rows.parquet")
    print(f"{table.height} labels classified, {len(skipped)} skipped, {rows.height} local rows")


PUBLISHER = "Maricopa County Elections Department"
TITLE = "Historic election results (precinct text files)"
LISTING = "https://elections.maricopa.gov/results-and-data/historic-results.html"
FIPS = "04013"
PARTY_TITLES = {"DEM": "Democratic", "REP": "Republican", "LBT": "Libertarian", "GRN": "Green"}
PARTIAL_CITIES = {"APACHE JUNCTION", "QUEEN CREEK", "WICKENBURG", "GILA BEND"}
NOTE = (
    "Precinct totals from the Maricopa County Elections Department's final precinct results text file for the "
    "election. The file declares no winners: where the file or the contest's name states how many seats are "
    "elected, the top vote-getters up to that count are recorded as elected, a ballot measure passes on a "
    "majority of Yes and No votes, a partisan primary advances its plurality winner, and in a nonpartisan contest "
    "that a later election of the same office follows, a majority winner is elected and otherwise the top two advance."
)
NO_SEATS = " The file states no seat count for this at-large contest, so no outcomes are recorded."


def display(name: str) -> str:
    words = (
        re.sub(r"\bELEM\b", "ELEMENTARY", re.sub(r"^(?:CITY|TOWN) OF ", "", name))
        .replace("(MARICOPA) ", "")
        .title()
        .split()
    )
    return " ".join(w.lower() if w in ("And", "Of", "The") else w for w in words)


def emit() -> Path:
    labels = pl.read_parquet(PARSED / "labels.parquet")
    rows = pl.read_parquet(PARSED / "rows.parquet")
    index = registry()
    cog_names = {e[1]: (group, e[0], e[3]) for group in ("city", "school", "special") for e in index[group]}
    towns = {
        name.removeprefix("TOWN OF ")
        for (name,) in pl.read_parquet(raw.parsed("us.cog") / "governments.parquet")
        .filter((pl.col("state_fips") == "04") & pl.col("name").str.starts_with("TOWN OF"))
        .select("name")
        .iter_rows()
    }
    days = sorted(set(labels["day"]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maricopa County's city, town, school, college, special district, and county contests by precinct from the Elections Department's historic results",
            polity="US-AZ",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-AZ")
        state_area = cs.area("us.state", "04")
        county = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        county_polity = conn.execute(
            "select entity_id from external_id where scheme = 'us.polity' and value = 'county:04013'"
        ).fetchone()[0]
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "retention")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, and retention systems must already exist")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LISTING,
                license="Public record",
            )
        for day, path in files():
            if day in days:
                cs.artifact(raw.cached(SOURCE, URL.format(year=day.year, name=path.name)), source)

        existing = {}
        for pid, kind, text, code, path in conn.execute(
            """select p.id, p.kind, n.text, x.value, s.path from polity p
               join external_id x on x.entity_id = p.id and x.scheme = 'us.polity'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where x.value like any(%s)""",
            [["place:04%", "school:04%", "special:04013:%", "county:04013"]],
        ).fetchall():
            existing[(code.partition(":")[0], text.upper())] = (pid, code, path)
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/az/%'").fetchall()}
        replaced = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s where s.is_current and s.entity_id in (
                       select o.contest_id from contest_option o join changeset c on c.id = o.changeset_id
                       where c.pipeline = %s and o.kind = 'candidate'
                         and o.ballot_label ~* 'print name of write|not qualified')""",
                [SOURCE],
            ).fetchall()
        }
        taken -= replaced
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

        report: dict[str, int] = {}
        made: dict[tuple[str, str], tuple[UUID, str, str, str]] = {}
        for kind, jur in sorted({(r["kind"], r["jur"]) for r in labels.iter_rows(named=True)}):
            if kind == "county":
                made[(kind, jur)] = (county_polity, "county:04013", "us/az/maricopa-county", "Maricopa County")
                continue
            group = {"city": "place", "school": "school", "special": "special"}[kind]
            found = existing.get((group, jur.upper()))
            if found is not None:
                made[(kind, jur)] = (*found, display(jur))
                continue
            known = cog_names.get(jur)
            if known is None:
                report[f"no polity: {kind} {jur}"] = 0
                continue
            shown = display(jur)
            if kind == "city":
                code = f"place:04{known[1]}"
                polity_kind = "municipality"
            elif kind == "school":
                code = f"school:04:{slug(shown)}"
                polity_kind = "school_district"
            else:
                code = f"special:04013:{'fire' if 'FIRE' in jur else 'water' if re.search('IRRIGATION|IWDD|WATER', jur) else 'other'}:{slug(shown)}"
                polity_kind = "special_district"
            pid = cs.key("us.polity", code)
            path = f"us/az/{slug(shown)}"
            if path in taken:
                path = (
                    f"{path}-{'school-district' if kind == 'school' else 'district' if kind == 'special' else 'city'}"
                )
            if path in taken:
                raise ValueError(f"{shown} would take {path}")
            taken.add(path)
            if pid in cs.minted:
                cs.add("polity", id=pid, kind=polity_kind, iso_code=None)
                name(pid, shown)
                cs.add("polity_parent", polity_id=pid, parent_id=state)
                cs.add("slug", path=path, entity_id=pid)
                scheme = {"city": "us.place", "school": "us.school_district", "special": "us.special_district"}[kind]
                area_code = code.partition(":")[2]
                area = cs.area(scheme, area_code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=area_code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=county[1] if kind == "special" else state_area)
                name(area, f"{shown}, AZ")
                cs.add("polity_territory", polity_id=pid, area_id=area)
                territories[pid] = area
                own_areas.add(area)
            made[(kind, jur)] = (pid, code, path, shown)

        people: dict[tuple[str, str], list[UUID]] = {}
        for person, label in conn.execute(
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
            [["place:04%", "school:04%", "special:04013:%", "county:04013"], SOURCE],
        ).fetchall():
            parts = [
                w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if len(parts) >= 2:
                people.setdefault((parts[0], parts[-1]), []).append(person)

        later: dict[tuple, list] = {}
        for r in labels.iter_rows(named=True):
            later.setdefault((r["kind"], r["jur"], r["type"], r["seat"], r.get("office"), r["party"]), []).append(
                r["day"]
            )

        elections: dict[tuple[date, UUID], tuple[UUID, str]] = {}
        owners: dict[tuple[UUID, str], UUID] = {}
        districts: set[str] = set()
        seen: set[str] = set()
        minted_units: set[str] = set()
        values: list[pl.DataFrame] = []
        known_names: dict[tuple[UUID, date], list[tuple[str, str]]] = {}
        made_people: set[UUID] = set()
        for row in labels.sort("day", "kind", "jur", "contest").iter_rows(named=True):
            polity = made.get((row["kind"], row["jur"]))
            if polity is None:
                continue
            pid, code, path, shown = polity
            day = row["day"]
            frame = rows.filter((pl.col("day") == day) & (pl.col("contest") == row["contest"]))
            sums = frame.group_by("name").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0)
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
                    known_names[(pid, day)] = conn.execute(
                        """select lower(coalesce(o.ballot_label, '')), lower(n.text) from contest k
                           join election e on e.id = k.election_id
                           join changeset kc on kc.id = k.changeset_id and kc.pipeline <> %s
                           join name n on n.entity_id = k.id and n.is_preferred
                           left join contest_option o on o.contest_id = k.id
                           where e.polity_id = %s and e.date_from = %s""",
                        [SOURCE, pid, day],
                    ).fetchall()
                tail = re.sub(r"\s+", " ", row["contest"]).lower()
                surnames = {c.split()[-1].lower() for c in sums["name"] if c not in ("Yes", "No", "Write-in")}
                present = any(title.split(": ", 1)[-1] == tail for _, title in known_names[(pid, day)]) or (
                    bool(surnames)
                    and any(label.split()[-1] in surnames for label, _ in known_names[(pid, day)] if label.split())
                )
                if present:
                    report["exists"] = report.get("exists", 0) + 1
                    continue
            if (day, pid) not in elections:
                if existing_election is not None:
                    elections[(day, pid)] = existing_election
                else:
                    election = cs.key("us.election", f"{day}:AZ:{code.partition(':')[2].replace(':', '-')}")
                    partisan = (
                        labels.filter(
                            (pl.col("day") == day) & (pl.col("kind") == row["kind"]) & (pl.col("jur") == row["jur"])
                        )["party"]
                        .is_not_null()
                        .any()
                    )
                    word = "general" if day.month == 11 and day.year % 2 == 0 else "primary" if partisan else "local"
                    cs.add("election", id=election, polity_id=pid, kind=word, date_from=day, date_to=day, status="held")
                    name(election, f"{day.year} {shown} {word} election")
                    election_path = f"{path}/{day}-{word}"
                    cs.add("slug", path=election_path, entity_id=election)
                    elections[(day, pid)] = (election, election_path)
            election, election_path = elections[(day, pid)]

            key_body = code.partition(":")[2].replace(":", "-")
            party = row["party"]
            partisan_title = f" ({PARTY_TITLES[party]} primary)" if party else ""
            suffix = f":{party.lower()}" if party else ""
            area = None
            contest_kind, office_id, body_id = "office", None, None
            magnitude = row["seats"]
            answers = row["type"] in ("measure", "recall", "retention")
            seat_text = row["seat"]
            if row["type"] in ("council", "board"):
                body_kind = "council" if row["type"] == "council" else "board"
                if row["kind"] == "county":
                    body_title, seat_label = "Maricopa County Board of Supervisors", "supervisorial district"
                elif row["type"] == "council":
                    body_title, seat_label = f"{shown} {'Town' if row['jur'] in towns else 'City'} Council", "district"
                else:
                    body_title, seat_label = (
                        (f"{shown} Governing Board" if row["kind"] != "special" else f"{shown} Board of Directors"),
                        "seat",
                    )
                existing_body = conn.execute(
                    "select b.id from body b where b.polity_id = %s order by (b.kind = 'council') desc, b.id limit 1",
                    [pid],
                ).fetchone()
                if (pid, body_kind) not in owners:
                    owner = (
                        existing_body[0]
                        if existing_body is not None
                        else cs.key("us.body", f"AZ:{key_body}:{body_kind}")
                    )
                    if existing_body is None:
                        cs.add("body", id=owner, polity_id=pid, kind="council")
                        name(owner, body_title)
                    owners[(pid, body_kind)] = owner
                body_id, contest_kind = owners[(pid, body_kind)], "body_seats"
                if seat_text is not None:
                    district_code = f"04:{key_body}:{body_kind}:district-{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if area in cs.minted and district_code not in districts:
                        districts.add(district_code)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        if pid in territories:
                            cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(area, f"{body_title}, {seat_label} {seat_text}")
                    magnitude = magnitude or 1
                contest_key = f"{day}:AZ:{key_body}:{body_kind}" + (f":{slug(seat_text)}" if seat_text else "") + suffix
                if row["term"] != "full":
                    contest_key += ":remainder"
                contest_slug = (
                    body_kind
                    + (f"-{slug(seat_text)}" if seat_text else "")
                    + (f"-{party.lower()}" if party else "")
                    + ("" if row["term"] == "full" else "-unexpired")
                )
                contest_title = (
                    f"{day.year} {body_title}"
                    + (f", {seat_label} {seat_text}" if seat_text else "")
                    + " election"
                    + partisan_title
                )
            elif row["type"] in ("office", "mayor", "retention"):
                office = (
                    row["office"]
                    if row["type"] == "office"
                    else "mayor"
                    if row["type"] == "mayor"
                    else "superior-court-judge"
                )
                title = (
                    "Mayor"
                    if row["type"] == "mayor"
                    else "Judge of the Superior Court"
                    if row["type"] == "retention"
                    else row["title"]
                )
                office_key = f"AZ:{key_body}:{office}"
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
                if seat_text is not None and row["type"] == "office":
                    district_code = f"04:{key_body}:{office}:district-{slug(seat_text)}"
                    area = cs.area("us.local_district", district_code)
                    if area in cs.minted and district_code not in districts:
                        districts.add(district_code)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district_code, valid_from=None)
                        if pid in territories:
                            cs.add("area_parent", area_id=area, parent_id=territories[pid])
                        name(area, f"{shown} {title}, {seat_text}")
                judge = row.get("judge")
                tail = f":{slug(judge)}" if judge else ""
                contest_key = (
                    f"{day}:AZ:{key_body}:{office}" + (f":{slug(seat_text)}" if seat_text else "") + tail + suffix
                )
                contest_slug = (
                    office
                    + (f"-{slug(seat_text)}" if seat_text else "")
                    + (f"-{slug(judge)}" if judge else "")
                    + (f"-{party.lower()}" if party else "")
                )
                contest_title = (
                    f"{day.year} {shown} mayoral election"
                    if row["type"] == "mayor"
                    else f"{day.year} {title}, Maricopa County, retention: {' '.join(reversed([w.strip() for w in judge.split(',', 1)])).title()}"
                    if judge
                    else f"{day.year} {shown} {title}" + (f", {seat_text}" if seat_text else "") + " election"
                ) + partisan_title
                magnitude = magnitude or 1
            else:
                code_text = row["code"] or "question"
                contest_kind = "ballot_question"
                contest_key = f"{day}:AZ:{key_body}:{row['type']}-{slug(code_text)}"
                contest_slug = f"{row['type']}-{slug(code_text)}"
                contest_title = f"{day.year} {shown} " + (
                    f"recall of {code_text.title()}"
                    if row["type"] == "recall"
                    else f"Measure {code_text}"
                    if code_text.isdigit()
                    else "ballot question"
                )
                magnitude = None
            if f"{election_path}/{contest_slug}" in taken or contest_key in seen:
                report["exists"] = report.get("exists", 0) + 1
                continue
            seen.add(contest_key)
            taken.add(f"{election_path}/{contest_slug}")
            contest = cs.key("us.contest", contest_key)
            if area is None and code.startswith("place:"):
                area = cs.area("us.place", code.partition(":")[2])
            system = (
                systems["retention"]
                if row["type"] == "retention"
                else None
                if magnitude is None or answers
                else systems["block" if magnitude > 1 else "fptp"]
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind=contest_kind,
                office_id=office_id,
                body_id=body_id,
                district_area_id=area,
                magnitude=None if answers and row["type"] != "retention" else magnitude,
                round_number=1,
                party_scope_id=parties.get(party),
                electoral_system_id=system,
                default_result_kind="counted",
                term=row["term"],
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
                completeness=1
                if row["kind"] in ("county",) or (row["kind"] == "city" and row["jur"] not in PARTIAL_CITIES)
                else None,
            )
            unnamed = row["type"] in ("council", "board") and magnitude is None
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE + (NO_SEATS if unnamed else ""))

            options: dict[str, UUID] = {}
            ranked = sums.sort("votes", descending=True)
            ordered = ["Yes", "No"] if answers else ranked["name"].to_list()
            ordered = [c for c in ordered if c in set(sums["name"])] if answers else ordered
            ordered += [c for c in ranked["name"] if c not in ordered]
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
                    if row["type"] == "retention" and candidate == "Yes":
                        last, _, first = row["judge"].partition(",")
                        judge_name = f"{first.strip()} {last.strip()}".title()
                        person = cs.key(f"{SOURCE}.person", f"AZ:{slug(judge_name)}:judge")
                        if person in cs.minted and person not in made_people:
                            made_people.add(person)
                            cs.add("person", id=person)
                            name(person, judge_name)
                        candidacies["Yes"] = cs.key("us.candidacy", f"{contest_key}:yes:head")
                        cs.add(
                            "candidacy",
                            id=candidacies["Yes"],
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=judge_name,
                            is_independent=False,
                        )
                    continue
                write_in = candidate == "Write-in"
                independent = (
                    bool(frame.filter(pl.col("name") == candidate)["party"].is_in(["IND"]).any())
                    if not write_in
                    else False
                )
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="write_in" if write_in else "candidate",
                    ballot_label=candidate,
                )
                if write_in:
                    continue
                parts = [
                    w
                    for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split()
                    if w not in ("jr", "sr", "ii", "iii", "iv")
                ]
                matched = people.get((parts[0], parts[-1])) if len(parts) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person = matched[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"AZ:{slug(candidate)}:{key_body}")
                    if person in cs.minted and person not in made_people:
                        made_people.add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                candidacies[candidate] = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head")
                cs.add(
                    "candidacy",
                    id=candidacies[candidate],
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=independent,
                )
            if row["type"] in ("measure", "recall"):
                cs.add(
                    "ballot_question",
                    contest_id=contest,
                    type="recall_question" if row["type"] == "recall" else "mandatory_referendum",
                )
            if answers:
                tally = dict(sums.iter_rows())
                yes, no = tally.get("Yes", 0), tally.get("No", 0)
                if row["type"] == "retention":
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options["Yes"],
                        candidacy_id=candidacies["Yes"],
                        kind="elected" if yes > no else "lost",
                        seats=1 if yes > no else None,
                    )
                elif yes != no and "Yes" in options and "No" in options:
                    cs.add(
                        "outcome", result_set_id=result_set, option_id=options["Yes" if yes > no else "No"], kind="won"
                    )
            else:
                named = [(c, v) for c, v in ranked.iter_rows() if c != "Write-in"]
                total = sums["votes"].sum()
                key = (row["kind"], row["jur"], row["type"], row["seat"], row.get("office"), row["party"])
                primary_phase = party is not None or any(0 < (d - day).days <= 200 for d in later[key])
                decided: list[tuple[str, str]] = []
                if party is not None:
                    n = magnitude or 1
                    if len(named) <= n or named[n - 1][1] > named[n][1]:
                        decided = [(c, "advanced") for c, _ in named[:n]]
                elif primary_phase and row["type"] in ("council", "mayor", "office") and (magnitude or 1) == 1:
                    if named[0][1] * 2 > total:
                        decided = [(named[0][0], "elected")]
                    elif len(named) >= 2 and (len(named) == 2 or named[1][1] > named[2][1]):
                        decided = [(c, "advanced") for c, _ in named[:2]]
                elif not primary_phase and magnitude is not None and not unnamed:
                    if len(named) <= magnitude or named[magnitude - 1][1] > named[magnitude][1]:
                        decided = [(c, "elected") for c, _ in named[:magnitude]]
                for candidate, outcome in decided:
                    cs.add("outcome", result_set_id=result_set, option_id=options[candidate], kind=outcome, seats=None)

            unit_ids: dict[str, UUID] = {}
            for precinct in frame["precinct"].unique().to_list():
                unit_code = f"{day}:AZ:{FIPS}:MARICOPA:{precinct}"
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
                frame.filter(pl.col("name").is_in(list(options)))
                .group_by("precinct", "name")
                .agg(pl.col("votes").sum())
            )
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                    option_id=pl.col("name").replace_strict({k: str(v) for k, v in options.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            report["new"] = report.get("new", 0) + 1
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
