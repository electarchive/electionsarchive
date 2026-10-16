import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.medsl_state import DROPPED, EXPANDED, INDEPENDENT, NATIONAL, POOLED, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.medsl_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LISTING = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/NYTPDU"
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
TITLE = "Precinct-Level Returns 2024 by Individual State"
DAY = date(2024, 11, 5)
COVERED = {"AK", "CA", "CT", "DC", "ID", "NC", "NH", "NM", "VA", "VT"}
COUNTY = r"(?:COUNTY|PARISH)"
OFFICES = [
    ("sheriff", "Sheriff", False, r"(?:HIGH )?SHERIFF"),
    ("coroner", "Coroner", False, r"CORONER"),
    ("medical-examiner", "Medical Examiner", False, r"MEDICAL EXAMINER"),
    (
        "commission-chair",
        "Commission Chair",
        False,
        (
            r"CHAIR(?:MAN|PERSON)?,? (?:OF )?(?:THE )?(?:COUNTY )?COMMISSION|(?:COUNTY )?COMMISSION(?:ERS?)? CHAIR(?:MAN|PERSON)?"
            r"|CHAIR(?:MAN|PERSON)? (?:OF )?(?:THE )?BOARD OF (?:COUNTY )?COMMISSIONERS|BOARD OF COMMISSIONERS CHAIR(?:MAN)?"
        ),
    ),
    (
        "council-president",
        "Council President",
        False,
        r"(?:COUNTY |CITY )?COUNCIL PRESIDENT|PRESIDENT,? (?:OF )?(?:THE )?(?:COUNTY |CITY )?COUNCIL",
    ),
    ("council-chair", "Council Chair", False, r"COUNTY (?:COUNCIL )?CHAIR(?:MAN|PERSON)?"),
    ("sole-commissioner", "Sole Commissioner", False, r"SOLE COMMISSION(?:ER)?"),
    ("license-commissioner", "License Commissioner", False, r"(?:COUNTY )?LICENSE COMMISSIONER"),
    ("election-commission", "Election Commission", True, r"(?:COUNTY )?ELECTION COMMISSION(?:ERS?)?"),
    (
        "commission",
        "Commission",
        True,
        r"(?:COUNTY )?COMMISS?IO?N(?:ERS?)?|(?:MEMBERS? OF )?(?:THE )?BOARD OF (?:COUNTY )?COMMISSIONERS?",
    ),
    ("board-of-supervisors", "Board of Supervisors", True, r"(?:COUNTY )?(?:BOARD OF )?SUPERVISORS?"),
    ("council", "Council", True, r"COUNTY COUNCIL(?: ?(?:MEMBERS?|MAN|WOMAN|PERSON|ORS?))?|COUNTY COUNCILOR"),
    ("legislature", "Legislature", True, r"COUNTY LEGISLA(?:TURE|TORS?)"),
    ("county-board", "Board", True, r"COUNTY BOARD(?: MEMBERS?)?"),
    ("quorum-court", "Quorum Court", True, r"QUORUM COURT(?: JUSTICES?)?"),
    ("justice-of-the-peace", "Justice of the Peace", False, r"JUSTICES? OF THE PEACE"),
    ("magistrate", "Magistrate", False, r"MAGISTRATE"),
    ("constable", "Constable", False, r"CONSTABLE"),
    ("executive", "Executive", False, r"COUNTY EXECUTIVE|EXECUTIVE"),
    ("chief-executive-officer", "Chief Executive Officer", False, r"CHIEF EXECUTIVE OFFICER"),
    ("chief-executive", "Chief Executive", False, r"CHIEF EXECUTIVE"),
    ("mayor", "Mayor", False, r"COUNTY MAYOR"),
    ("judge-executive", "Judge/Executive", False, r"COUNTY JUDGE ?[/-] ?EXECUTIVE"),
    ("county-judge", "County Judge", False, r"COUNTY JUDGE"),
    ("probate-judge", "Probate Judge", False, r"JUDGE OF PROBATE|PROBATE JUDGE"),
    ("register-of-probate", "Register of Probate", False, r"REGISTER OF PROBATE"),
    ("register-of-wills", "Register of Wills", False, r"REGISTER OF WILLS"),
    ("surrogate", "Surrogate", False, r"SURROGATE"),
    (
        "circuit-clerk",
        "Circuit Clerk",
        False,
        r"CIRCUIT (?:COURT )?CLERK(?: (?:AND|&) RECORDER)?|CLERK OF (?:THE )?CIRCUIT COURT(?: (?:AND|&) COMPTROLLER)?",
    ),
    (
        "superior-court-clerk",
        "Clerk of Superior Court",
        False,
        r"SUPERIOR COURT CLERK|CLERK OF (?:THE )?SUPERIOR COURT",
    ),
    ("state-court-clerk", "Clerk of State Court", False, r"STATE COURT CLERK|CLERK OF (?:THE )?STATE COURT"),
    ("common-pleas-clerk", "Clerk of the Court of Common Pleas", False, r"CLERK OF (?:THE )?COURT OF COMMON PLEAS"),
    (
        "district-court-clerk",
        "Clerk of the District Court",
        False,
        r"(?:COUNTY )?CLERK OF (?:THE )?DISTRICT COURT|DISTRICT COURT CLERK",
    ),
    ("clerk-of-courts", "Clerk of Courts", False, r"(?:COUNTY )?CLERK OF (?:THE )?COURTS?|COURT CLERK"),
    ("county-and-district-clerk", "County and District Clerk", False, r"COUNTY (?:AND|&) DISTRICT CLERK"),
    ("district-clerk", "District Clerk", False, r"DISTRICT CLERK"),
    ("clerk-of-the-peace", "Clerk of the Peace", False, r"CLERK OF THE PEACE"),
    ("clerk-and-recorder", "Clerk and Recorder", False, r"(?:COUNTY )?CLERK (?:AND|&) RECORDER"),
    ("supervisor-of-elections", "Supervisor of Elections", False, r"SUPERVISOR OF ELECTIONS"),
    ("clerk", "Clerk", False, r"COUNTY CLERK|CLERK"),
    ("treasurer", "Treasurer", False, r"(?:COUNTY )?TREASURER"),
    ("fiscal-officer", "Fiscal Officer", False, r"(?:COUNTY )?FISCAL OFFICER"),
    ("tax-assessor-collector", "Tax Assessor-Collector", False, r"TAX ASSESSOR[- ]COLLECTOR"),
    ("tax-collector", "Tax Collector", False, r"(?:COUNTY )?TAX COLLECTOR"),
    ("tax-commissioner", "Tax Commissioner", False, r"TAX COMMISSIONER"),
    ("revenue-commissioner", "Revenue Commissioner", False, r"REVENUE COMMISSIONER"),
    ("property-appraiser", "Property Appraiser", False, r"PROPERTY APPRAISER"),
    (
        "property-valuation-administrator",
        "Property Valuation Administrator",
        False,
        r"PROPERTY VALUATION ADMINISTRATOR",
    ),
    ("assessor", "Assessor", False, r"(?:COUNTY )?(?:TAX )?ASSESSOR"),
    ("register-of-deeds", "Register of Deeds", False, r"REGISTER OF DEEDS"),
    ("recorder-of-deeds", "Recorder of Deeds", False, r"RECORDER OF DEEDS"),
    ("register", "Register", False, r"REGISTER"),
    ("recorder", "Recorder", False, r"(?:COUNTY )?RECORDER"),
    ("auditor-and-recorder", "Auditor and Recorder", False, r"(?:COUNTY )?AUDITOR (?:AND|&) RECORDER"),
    ("auditor", "Auditor", False, r"(?:COUNTY )?AUDITOR"),
    ("comptroller", "Comptroller", False, r"(?:COUNTY )?COMPTROLLER"),
    ("prosecuting-attorney", "Prosecuting Attorney", False, r"PROSECUTING ATTORNEY"),
    ("states-attorney", "State's Attorney", False, r"STATE'?S ATTORNEY"),
    ("district-attorney", "District Attorney", False, r"DISTRICT ATTORNEY"),
    ("county-attorney", "County Attorney", False, r"(?:COUNTY )?ATTORNEY"),
    ("prosecutor", "Prosecutor", False, r"(?:COUNTY )?PROSECUTOR"),
    ("solicitor", "Solicitor", False, r"SOLICITOR(?:[- ]GENERAL)?"),
    ("public-defender", "Public Defender", False, r"PUBLIC DEFENDER"),
    ("public-administrator", "Public Administrator", False, r"PUBLIC ADMINISTRATOR"),
    ("surveyor", "Surveyor", False, r"(?:COUNTY )?SURVEYOR"),
    ("engineer", "Engineer", False, r"(?:COUNTY )?ENGINEER"),
    ("jailer", "Jailer", False, r"(?:COUNTY )?JAILER"),
    (
        "superintendent-of-schools",
        "Superintendent of Schools",
        False,
        (
            r"(?:COUNTY )?(?:SCHOOL )?SUPERINTENDENT(?: OF (?:PUBLIC )?(?:SCHOOLS|EDUCATION|PUBLIC INSTRUCTION))?"
            r"(?:,? (?:OF )?(?:THE )?(?:COUNTY )?BOARD OF EDUCATION)?"
        ),
    ),
    (
        "board-of-education-chair",
        "Board of Education Chair",
        False,
        r"(?:COUNTY )?BOARD OF EDUCATION CHAIR(?:MAN|PERSON)?",
    ),
    (
        "board-of-education",
        "Board of Education",
        True,
        r"(?:COUNTY )?(?:BOARD OF EDUCATION|SCHOOL BOARD)(?: MEMBERS?| TRUSTEES?)?",
    ),
    (
        "soil-and-water",
        "Soil and Water Conservation District",
        True,
        (
            r"(?:(?:COUNTY )?SOIL (?:AND |& )?(?:WATER )?)?CONSERVATION(?: DISTRICT| DIST\.?)?"
            r"(?: (?:BOARD )?(?:SUPERVISORS?|DIRECTORS?|COMMISSIONERS?|BOARD))?"
            r"|(?:COUNTY )?SOIL (?:AND|&) WATER(?: (?:SUPERVISORS?|DIRECTORS?|COMMISSIONERS?))?"
        ),
    ),
]
UNIFIED = [
    ("mayor", "Mayor", False, r"(?:CITY )?MAYOR"),
    ("city-council", "City Council", True, r"(?:CITY )?COUNCIL ?(?:MEMBERS?|MAN|WOMAN|PERSON)?|COUNCILMEMBER"),
]
TOWNSHIPS = {"IL", "MA", "ME", "MI", "MN", "NJ", "NY", "PA", "RI", "WI"}
COUNTYWIDE = {
    "sheriff",
    "coroner",
    "register-of-probate",
    "register-of-wills",
    "surrogate",
    "district-attorney",
    "probate-judge",
    "soil-and-water",
    "county-and-district-clerk",
}
SCHOOLS = {"AL", "FL", "GA", "KY", "LA", "MD", "MS", "NV", "SC", "TN", "WV"}
SEAT = re.compile(
    r"(?:(?:DIST(?:RICT)?|PRECINCT|PCT|PLACE|POST|POSITION|POS|SEAT|WARD|ZONE|DIVISION|BEAT|AREA|NO|NUMBER|#)\.? ?#? ?[A-Z]?\d+[A-Z]?"
    r"|\d+(?:ST|ND|RD|TH)? (?:DIST(?:RICT)?\.?|PRECINCT|WARD|ZONE|DIVISION|BEAT|[A-Z]+ DISTRICT)|\d+|[A-Z]"
    r"|[A-Z][A-Z .'-]* (?:TOWNSHIP|DISTRICT|PRECINCT|DIVISION|WARD)(?: \d+)?|AT[- ]LARGE(?: [A-Z0-9])?"
    r"|NORTH|SOUTH|EAST|WEST|CENTRAL|TERM (?:COMMENCING|BEGINNING) \d\d?/\d\d?/\d{4})"
)
BANNED = re.compile(r"SCHOOL|INDEPENDENT|CITY|TOWN\b|VILLAGE|COLLEGE|JUDICIAL|CIRCUIT|SUPERVISOR|MEMBER|BOARD|DIRECTOR")
REMAINDER = re.compile(
    r",? ?-? ?\(?(?:UNEXPIRED(?: TERM(?: ENDING [\d/]+)?)?|TO FILL (?:A |THE )?VACANCY|SHORT TERM|SPECIAL)\)?$"
)
VACANT = {"NO CANDIDATE FILED", "NO CANDIDATE", "NO CANDIDATES", "NO NOMINATION", "VACANT", "NONE"}
EXCLUDED = re.compile(
    r"RECALL|PRESIDENT|CHAIR|VICE|JUDGE|CLERK|TREASURER|ATTORNEY|AUDITOR|BOARD|SCHOOL|QUESTION|PROPOSITION|AMENDMENT"
    r"|CHARTER|MEASURE|COUNTY|TOWNSHIP|ADVISORY|SHALL"
)
NONPARTISAN = {"NONPARTISAN", "NON-PARTISAN", "NP"}
METHOD = (
    "Totals are the sums of the precinct rows in MEDSL's 2024 precinct file for the state. The file does not mark "
    "winners, so the winners shown are the candidates with the most votes, as many as the contest elects."
)
MASKED = (
    "Some precincts in the source file hide small vote counts to protect voter privacy; "
    "these totals run slightly below the certified results."
)
DISTRICT = (
    "Only part of the county votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def fetch() -> None:
    listing = raw.fetch(SOURCE, LISTING, suffix=".json")
    for file in json.loads(listing.path.read_text())["data"]["latestVersion"]["files"]:
        data = file["dataFile"]
        raw.fetch(
            SOURCE,
            f"{ACCESS}/{data['id']}" + ("?format=original" if data["filename"].endswith(".tab") else ""),
            suffix=".csv",
        )


def _files() -> dict[str, str]:
    files = {}
    for file in json.loads(raw.cached(SOURCE, LISTING).path.read_text())["data"]["latestVersion"]["files"]:
        data = file["dataFile"]
        state = data["filename"].split("-")[1].upper()
        files[state] = f"{ACCESS}/{data['id']}" + ("?format=original" if data["filename"].endswith(".tab") else "")
    return files


def _match(state: str, text: str, unified: bool) -> tuple[str, str, bool, str | None] | None:
    for key, title, body, pattern in (UNIFIED if unified else []) + OFFICES:
        if state in TOWNSHIPS and key not in COUNTYWIDE and "COUNTY" not in text:
            continue
        if key == "board-of-education" and state not in SCHOOLS and "COUNTY" not in text:
            continue
        if key == "board-of-supervisors" and state not in {"AZ", "IA", "MS"} and not re.search(r"COUNTY|BOARD", text):
            continue
        found = re.fullmatch(rf"(?:MEMBERS?,? (?:OF )?(?:THE )?)?(?:COUNTY )?(?:{pattern})(?:\s*[-,:]?\s*(.*))?", text)
        if found is None:
            continue
        rest = re.sub(r"[()\"]", "", found[1] or "").strip(" ,-")
        if rest and (not SEAT.fullmatch(rest) or BANNED.search(rest)):
            continue
        return key, title, body, rest or None
    return None


def _classify(state: str, office: str, county: str) -> tuple[str, str, bool, str | None, bool] | None:
    text = re.sub(r"\s+", " ", office.upper()).strip()
    name = re.escape(county.upper())
    unified = state == "HI" or county.upper().endswith(" CITY") or county.upper() in {"DENVER", "BROOMFIELD"}
    text = re.sub(rf"\b{name} {COUNTY}\b", "COUNTY", text)
    text = re.sub(rf"\b{name}-(\d+)$", r"\1", text)
    for _ in range(2):
        text = re.sub(rf"^{name}\b|\b{name}$", "", text).strip(" ,-")
    text = re.sub(r"\b(COUNTY|COMMISSIONER|COUNCIL) \1\b", r"\1", text)
    text = re.sub(r"^(?:NONPARTISAN|SPECIAL ELECTION FOR) ", "", text)
    text = re.sub(r",? ?COUNTY$", "", re.sub(r"\s+", " ", text)).strip(" ,-")
    remainder = REMAINDER.search(text) is not None or "SPECIAL ELECTION" in office.upper()
    text = REMAINDER.sub("", text).strip(" ,-")
    found = _match(state, text, unified)
    if found is None and re.search(r"\s*/\s*|\s+-\s+", text):
        parts = [_match(state, part, unified) for part in re.split(r"\s*/\s*|\s+-\s+", text)]
        if all(part is not None and not part[2] and part[3] is None for part in parts):
            found = (
                "-".join(part[0] for part in parts if part),
                "/".join(part[1] for part in parts if part),
                False,
                None,
            )
    return None if found is None else (*found, remainder)


def _city(office: str, places: dict[str, tuple[str, str]]) -> dict[str, object] | None:
    text = re.sub(r"\s+", " ", office.upper()).strip()
    if not re.search(r"MAYOR|COUNCIL|ALDERM", text) or EXCLUDED.search(text):
        return None
    key = "mayor" if "MAYOR" in text else "council"
    remainder = REMAINDER.search(text) is not None
    text = REMAINDER.sub("", text)
    seats = re.findall(
        r"\b(?:WARD|DISTRICT|DIST\.?|POSITION(?: NO\.?)?|POS\.?|SEAT|PLACE) ?#? ?[A-Z]?\d+[A-Z]?\b|\bAT[- ]LARGE\b",
        text,
    )
    for seat in seats:
        text = text.replace(seat, " ")
    text = re.sub(r"\b(?:CITY|TOWN|VILLAGE) OF\b", " ", text)
    text = re.sub(
        r"\b(?:MAYOR|CITY COUNCIL|COUNCIL ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?)|COUNCILMEMBERS?|COUNCILORS?|COUNCIL|ALDERM[AE]N"
        r"|\d+ ?(?:YRS?|YEARS?)(?: TERM)?)\b",
        " ",
        text,
    )
    found = places.get(re.sub(r"[^A-Z]", "", text).removesuffix("CITY"))
    if found is None or len(seats) > 1:
        return None
    return {
        "office": office,
        "code": found[0],
        "place": found[1],
        "key": key,
        "title": "Mayor" if key == "mayor" else "City Council",
        "body": key == "council",
        "rest": seats[0] if seats else None,
        "remainder": remainder,
    }


def _seat(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\bDIST\b\.?", "DISTRICT", text.upper().strip())
    text = re.sub(r"\bPCT\b\.?", "PRECINCT", text)
    text = re.sub(r"\s*#\s*", " ", text).strip()
    text = re.sub(r"\bPOS\b\.?", "POSITION", text)
    text = re.sub(r"^(?:NO|NUMBER|#)\.? ?#? ?", "", text)
    text = re.sub(r"\b(?:NO|NUMBER)\.? (?=\d)", "", text)
    text = re.sub(r"\b0+(\d)", r"\1", text)
    if text.isdigit():
        text = f"DISTRICT {text}"
    text = re.sub(r"^(\d+)(?:ST|ND|RD|TH)? (.+)$", r"\2 \1", text)
    if re.fullmatch(r"[A-Z]", text):
        text = f"DISTRICT {text}"
    if re.fullmatch(r"AT[- ]LARGE", text):
        return "At-Large"
    return re.sub(
        r"\b([A-Z]+)\b",
        lambda word: word[1] if re.fullmatch(r"[IVX]+|[A-Z]", word[1]) else word[1].capitalize(),
        re.sub(r"\bAT[- ]LARGE\b", "AT-LARGE", text),
    )


def parse() -> None:
    with connect() as conn:
        places: dict[str, dict[str, tuple[str, str]]] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.polity_id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.place' and u.code ~ '^[0-9]{7}$'"""
        ).fetchall():
            places.setdefault(code[:2], {})[
                re.sub(r"[^A-Z]", "", re.sub(r"^(?:CITY|TOWN|VILLAGE) OF ", "", text.upper())).removesuffix("CITY")
            ] = (code, text)
    contests: list[pl.DataFrame] = []
    lines: list[pl.DataFrame] = []
    problems: list[str] = []
    for state, url in sorted(_files().items()):
        if state in COVERED:
            continue
        frame = (
            pl.scan_csv(raw.cached(SOURCE, url).path, infer_schema=False)
            .filter(pl.col("dataverse") == "LOCAL", pl.col("stage").is_in(["GEN", "GEN RECOUNT"]))
            .select(
                "state_po",
                "office",
                "district",
                "special",
                "county_fips",
                "county_name",
                "precinct",
                "jurisdiction_fips",
                "stage",
                "mode",
                pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " "),
                "party_detailed",
                "writein",
                pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                pl.col("magnitude").cast(pl.Float64, strict=False).cast(pl.Int32),
            )
            .filter(pl.col("candidate").is_not_null(), ~pl.col("candidate").is_in([*DROPPED, *VACANT]))
            .collect()
        )
        if frame.height == 0:
            continue
        frame = frame.with_columns(pl.col("county_fips").str.zfill(5), pl.col("special").fill_null("FALSE"))
        recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select("office", "district", "county_fips").unique()
        frame = pl.concat(
            [
                frame.join(recounted, on=["office", "district", "county_fips"], how="anti", nulls_equal=True),
                frame.filter(pl.col("stage") == "GEN RECOUNT"),
            ]
        )
        precinct = ["office", "district", "special", "county_fips", "jurisdiction_fips", "precinct"]
        frame = (
            frame.with_columns(total=(pl.col("mode") == "TOTAL").any().over(*precinct, "candidate", "party_detailed"))
            .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
            .with_columns(pooled=pl.col("candidate").is_in(list(POOLED)) | (pl.col("writein") == "TRUE"))
        )
        labels = []
        towns = {}
        for office, county, fips in frame.select("office", "county_name", "county_fips").unique().iter_rows():
            found = _classify(state, office, county or "")
            if found is not None:
                key, title, body, rest, remainder = found
                labels.append(
                    {
                        "office": office,
                        "county_fips": fips,
                        "code": fips,
                        "scheme": "us.county",
                        "place": county,
                        "key": key,
                        "title": title,
                        "body": body,
                        "rest": rest,
                        "remainder": remainder,
                    }
                )
            elif office not in towns:
                towns[office] = _city(office, places.get(fips[:2], {}))
            if found is None and (town := towns[office]) is not None:
                labels.append({**town, "county_fips": fips, "scheme": "us.place"})
        if not labels:
            continue
        frame = frame.join(pl.DataFrame(labels, infer_schema_length=None), on=["office", "county_fips"])
        chosen = (
            frame.group_by("county_fips", "code", "key", "rest", "remainder", "office")
            .agg(pl.struct("jurisdiction_fips", "precinct").n_unique().alias("width"))
            .sort("width", "office", descending=[True, False])
            .unique(["county_fips", "code", "key", "rest", "remainder"], keep="first", maintain_order=True)
            .select("county_fips", "office")
        )
        frame = frame.join(chosen, on=["county_fips", "office"])
        race = ["office", "district", "special"]
        spread = (
            frame.filter(~pl.col("pooled"), pl.col("scheme") == "us.county")
            .group_by(*race, "candidate")
            .agg(pl.col("county_fips").n_unique().alias("counties"))
            .filter(pl.col("counties") > 1)
            .select(race)
            .unique()
        )
        for office, district, _ in spread.iter_rows():
            problems.append(f"{state}: {office} {district or ''} spans counties")
        frame = frame.join(spread, on=race, how="anti", nulls_equal=True)
        frame = frame.with_columns(
            seat=pl.struct("rest", "district", "county_name").map_elements(
                lambda row: (
                    _seat(row["rest"])
                    or _seat(
                        district
                        if (
                            district := re.sub(
                                rf"^{re.escape((row['county_name'] or '').upper())}\b\s*",
                                "",
                                (row["district"] or "").upper(),
                            )
                        )
                        and SEAT.fullmatch(district)
                        and not BANNED.search(district)
                        else None
                    )
                ),
                return_dtype=pl.String,
            ),
            remainder=pl.col("remainder") | (pl.col("special") == "TRUE"),
        )
        contest = ["code", "key", "seat", "remainder"]
        summary = frame.group_by(contest).agg(
            pl.col("state_po").first(),
            pl.col("scheme").first(),
            pl.col("place").first(),
            pl.col("title").first(),
            pl.col("body").first(),
            pl.col("office").n_unique().alias("offices"),
            pl.col("office").first(),
            pl.col("magnitude").max().fill_null(1),
            pl.col("votes").is_null().any().alias("masked"),
            pl.struct("jurisdiction_fips", "precinct").n_unique().alias("precincts"),
        )
        doubled = summary.filter(pl.col("offices") > 1)
        for row in doubled.iter_rows(named=True):
            problems.append(f"{state}: {row['place']} {row['key']} {row['seat']} has several office labels")
        summary = summary.filter(pl.col("offices") == 1).with_columns(
            district=pl.col("seat").is_not_null() & (pl.col("precincts") < 0.9 * pl.col("precincts").max().over("code"))
        )
        contests.append(summary.drop("offices", "precincts"))
        lines.append(
            frame.join(summary.select(contest), on=contest, nulls_equal=True)
            .with_columns(
                candidate=pl.when("pooled").then(pl.lit("WRITE-IN")).otherwise("candidate"),
                party_detailed=pl.when("pooled").then(None).otherwise("party_detailed"),
            )
            .group_by(*contest, "candidate", "pooled")
            .agg(pl.col("party_detailed").drop_nulls().mode().first(), pl.col("votes").sum())
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(contests).sort("state_po", "code", "key", "seat", nulls_last=False).write_parquet(
        PARSED / "contests.parquet"
    )
    pl.concat(lines).write_parquet(PARSED / "lines.parquet")
    (PARSED / "problems.txt").write_text("\n".join(problems))


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        polities = {
            (scheme, code): (polity, text, path)
            for scheme, code, polity, text, path in conn.execute(
                """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code in ('us.county', 'us.place')"""
            ).fetchall()
        }
        parties_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County offices elected in November 2024, from MEDSL's state precinct files",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
            return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            label = None if label is None else label.strip().removesuffix(" PARTY")
            if not label or label in NONPARTISAN:
                return None, False
            label = EXPANDED.get(label, label)
            if label in INDEPENDENT or "PETITION" in label:
                return None, True
            if label in NATIONAL:
                return cs.key("us.party", NATIONAL[label]), False
            text = title(label.removesuffix(" PARTY")) + " Party"
            candidates = [f"{state}:{slug(text)}", f"{state}:{slug(text.removesuffix(' Party'))}"]
            key = next((key for key in candidates if key in parties_known), candidates[0])
            found = cs.key("us.party", key)
            if found in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(found, text)
            return found, False

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/NYTPDU",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        files = _files()
        for state in sorted(set(contests["state_po"])):
            cs.artifact(raw.cached(SOURCE, files[state]), source)
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")

        owners: dict[tuple[str, str], UUID] = {}
        elections: dict[str, UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        grouped = {keys: rows for keys, rows in lines.group_by("code", "key", "seat", "remainder", maintain_order=True)}
        for row in contests.iter_rows(named=True):
            state, scheme, code, key, seat = row["state_po"], row["scheme"], row["code"], row["key"], row["seat"]
            if (scheme, code) not in polities:
                continue
            polity, place, path = polities[(scheme, code)]
            parent = cs.key("us.election", f"{DAY}:{state}")
            if parent in cs.minted:
                raise ValueError(f"no election {DAY}:{state}")
            if code not in elections:
                election = elections[code] = cs.key("us.election", f"{DAY}:{state}:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    status="held",
                )
                name(election, f"2024 {place} general election")
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            election = elections[code]
            body = row["body"]
            city = place.removeprefix("City of ")
            office_title = (
                f"Mayor of {city}"
                if key == "mayor" and (city != place or scheme == "us.place")
                else f"{city} City Council"
                if key in ("city-council", "council") and (city != place or scheme == "us.place")
                else f"{place} Council"
                if key == "city-council"
                else f"{place} {row['title']}"
            )
            if (code, key) not in owners:
                owner = owners[(code, key)] = cs.key("us.body" if body else "us.office", f"{state}:{code}:{key}")
                if body:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="other")
                name(owner, office_title)
            whole, unit = cs.area(scheme, code), cs.unit(scheme, code)
            area = whole
            if row["district"]:
                district = f"{code[:2]}:{code}:{key}:{slug(seat)}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{office_title}, {seat}")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
            contest_key = (
                f"{DAY}:{state}:{code}:{key}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            magnitude = row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if body else "office",
                office_id=None if body else owners[(code, key)],
                body_id=owners[(code, key)] if body else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if row["remainder"] else "full",
            )
            name(
                contest,
                (f"2024 {city} mayoral election" if key == "mayor" else f"2024 {office_title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            contest_path = (
                f"{path}/{DAY}-general/{key}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else "")
            )
            if slugs.setdefault(contest_path, contest) != contest:
                raise ValueError(f"slug {contest_path} is taken")
            cs.add("slug", path=contest_path, entity_id=contest)
            if row["district"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT)
            if row["masked"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=MASKED)
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
                completeness=None if row["masked"] else 1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            rows = grouped[(code, key, seat, row["remainder"])].sort(
                "votes", "candidate", descending=[True, False], nulls_last=True
            )
            totals: list[tuple[int, UUID]] = []
            for candidate, pooled, label, votes in rows.select(
                "candidate", "pooled", "party_detailed", "votes"
            ).iter_rows():
                if pooled:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    ballot = title(candidate)
                    party_id, independent = party(state, label)
                    person_key = (code, slug(candidate))
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", f"{state}:{code}:{slug(candidate)}")
                        cs.add("person", id=persons[person_key])
                        name(persons[person_key], ballot)
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=independent,
                    )
                    totals.append((votes or 0, option))
                if votes is not None and result_set in cs.minted:
                    values.append((str(result_set), str(unit), str(option), votes))
            if len(totals) > magnitude and totals[magnitude - 1][0] == totals[magnitude][0]:
                continue
            for _, option in totals[:magnitude]:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
