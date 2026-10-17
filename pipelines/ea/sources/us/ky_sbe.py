import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ky_sbe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
SITE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Election results by county"
FILES = [
    ("20120927165607", "1973-1979/76-79/76res_president2.txt"),
    ("20120927170214", "1973-1979/76-79/78res_ussenate2.txt"),
    ("20120927033726", "1973-1979/76-79/79pagriculutre.txt"),
    ("20120927033711", "1973-1979/76-79/79pauditor.txt"),
    ("20120927033708", "1973-1979/76-79/79pgov_ltgov.txt"),
    ("20120927042042", "1973-1979/76-79/79prisos.txt"),
    ("20120927042849", "1973-1979/76-79/79psuper.txt"),
    ("20120927033742", "1973-1979/76-79/79ptreasurer.txt"),
    ("20120927042842", "1973-1979/76-79/79res_agriculture.txt"),
    ("20120927033715", "1973-1979/76-79/79res_attgen1.txt"),
    ("20120927033740", "1973-1979/76-79/79res_auditor1.txt"),
    ("20120927033724", "1973-1979/76-79/79res_govltgov1.txt"),
    ("20120927033706", "1973-1979/76-79/79res_sos1.txt"),
    ("20120927042049", "1973-1979/76-79/79res_super1.txt"),
    ("20120927033719", "1973-1979/76-79/79res_tresurer.txt"),
    ("20120927071311", "1980-1989/1980/80pres_ussenate.txt"),
    ("20120927065619", "1980-1989/1980/80res_president1.txt"),
    ("20120927065606", "1980-1989/1980/80res_ussenate1.txt"),
    ("20120927125612", "1980-1989/1983/83_gov3.txt"),
    ("20120927125638", "1980-1989/1983/83agriculture4.txt"),
    ("20120927125635", "1980-1989/1983/83attgen4.txt"),
    ("20120927125616", "1980-1989/1983/83auditor4.txt"),
    ("20120927125650", "1980-1989/1983/83ltgov2.txt"),
    ("20120927125645", "1980-1989/1983/83priagriculture.txt"),
    ("20120927125643", "1980-1989/1983/83priauditor.txt"),
    ("20120927130937", "1980-1989/1983/83prigov.txt"),
    ("20120927125621", "1980-1989/1983/83priltgov.txt"),
    ("20120927125640", "1980-1989/1983/83prisos.txt"),
    ("20120927125618", "1980-1989/1983/83prisuper.txt"),
    ("20120927130505", "1980-1989/1983/83pritreasurer.txt"),
    ("20120927125653", "1980-1989/1983/83sos.txt"),
    ("20120927130925", "1980-1989/1983/83super4.txt"),
    ("20120927125609", "1980-1989/1983/83treasurer4.txt"),
    ("20120927071943", "1980-1989/1984/84president2.txt"),
    ("20120927072357", "1980-1989/1984/84priussenate.txt"),
    ("20120927071951", "1980-1989/1984/84ussenate5.txt"),
    ("20120927232238", "1980-1989/1986/86priussenate2.txt"),
    ("20120927232240", "1980-1989/1986/86ussenate3.txt"),
    ("20120927023501", "1980-1989/1987/87agriculture2.txt"),
    ("20120927024837", "1980-1989/1987/87attgen2.txt"),
    ("20120927024839", "1980-1989/1987/87auditor2.txt"),
    ("20120927023453", "1980-1989/1987/87govltgov.txt"),
    ("20120927023456", "1980-1989/1987/87pri_agriculture3.txt"),
    ("20120927032833", "1980-1989/1987/87pri_attgen3.txt"),
    ("20120927024331", "1980-1989/1987/87pri_auditor3.txt"),
    ("20120927024845", "1980-1989/1987/87pri_govltgov1.txt"),
    ("20120927024333", "1980-1989/1987/87pri_sos3.txt"),
    ("20120927023459", "1980-1989/1987/87pri_super3.txt"),
    ("20120927024843", "1980-1989/1987/87pri_treasurer3.txt"),
    ("20120927024848", "1980-1989/1987/87sos2.txt"),
    ("20120927024337", "1980-1989/1987/87super2.txt"),
    ("20120927023450", "1980-1989/1987/87treasurer2.txt"),
    ("20120927230607", "1980-1989/1988/88president.txt"),
    ("20120927234714", "1990-1999/1990/90priussenate1.txt"),
    ("20120927234706", "1990-1999/1990/90ussenate.txt"),
    ("20120927231209", "1990-1999/1991/91attgen.txt"),
    ("20120927231240", "1990-1999/1991/91auditor.txt"),
    ("20120927231237", "1990-1999/1991/91gov.txt"),
    ("20120927230709", "1990-1999/1991/91ltgov.txt"),
    ("20120927231223", "1990-1999/1991/91priagriculture1.txt"),
    ("20120927231233", "1990-1999/1991/91priattgen1.txt"),
    ("20120927231217", "1990-1999/1991/91priauditor1.txt"),
    ("20120927231152", "1990-1999/1991/91prigov.txt"),
    ("20120927231242", "1990-1999/1991/91prigov1.txt"),
    ("20120927231221", "1990-1999/1991/91prisos1.txt"),
    ("20120927231622", "1990-1999/1991/91prisuper1.txt"),
    ("20120927231212", "1990-1999/1991/91pritreasurer1.txt"),
    ("20120927231230", "1990-1999/1991/91sos.txt"),
    ("20120927231228", "1990-1999/1991/91super.txt"),
    ("20120927231207", "1990-1999/1991/91treasurer.txt"),
    ("20120927171957", "1990-1999/1992/92president.txt"),
    ("20120927172020", "1990-1999/1992/92priussenate1.txt"),
    ("20120927172023", "1990-1999/1992/92ussenate.txt"),
    ("20120927032908", "1990-1999/1995/95Gen_agriculture.txt"),
    ("20120927032923", "1990-1999/1995/95Gen_attgen.txt"),
    ("20120927032903", "1990-1999/1995/95Gen_auditor.txt"),
    ("20120927033659", "1990-1999/1995/95Gen_govltgov.txt"),
    ("20120927032919", "1990-1999/1995/95Gen_sos.txt"),
    ("20120927033650", "1990-1999/1995/95Gen_treasurer.txt"),
    ("20120927033656", "1990-1999/1995/95Pri_agriculture.txt"),
    ("20120927032913", "1990-1999/1995/95Pri_attgen1.txt"),
    ("20120927033653", "1990-1999/1995/95Pri_auditor1.txt"),
    ("20120927032916", "1990-1999/1995/95Pri_govltgov1.txt"),
    ("20120927032901", "1990-1999/1995/95Pri_sosrevise.txt"),
    ("20120927032911", "1990-1999/1995/95Pri_treasurer.txt"),
    ("20120927165135", "1990-1999/1996/96Gen_president.txt"),
    ("20120927165056", "1990-1999/1996/96Gen_ussenate1.txt"),
    ("20120927165139", "1990-1999/1996/96Pri_ussenate2.txt"),
    ("20120927170946", "1990-1999/1998/98Gen_ussenate.txt"),
]
DAYS = {
    (1976, "general"): date(1976, 11, 2),
    (1978, "primary"): date(1978, 5, 23),
    (1978, "general"): date(1978, 11, 7),
    (1979, "primary"): date(1979, 5, 29),
    (1979, "general"): date(1979, 11, 6),
    (1980, "primary"): date(1980, 5, 27),
    (1980, "general"): date(1980, 11, 4),
    (1983, "primary"): date(1983, 5, 24),
    (1983, "general"): date(1983, 11, 8),
    (1984, "primary"): date(1984, 5, 29),
    (1984, "general"): date(1984, 11, 6),
    (1986, "primary"): date(1986, 5, 27),
    (1986, "general"): date(1986, 11, 4),
    (1987, "primary"): date(1987, 5, 26),
    (1987, "general"): date(1987, 11, 3),
    (1988, "general"): date(1988, 11, 8),
    (1990, "primary"): date(1990, 5, 29),
    (1990, "general"): date(1990, 11, 6),
    (1991, "primary"): date(1991, 5, 28),
    (1991, "general"): date(1991, 11, 5),
    (1992, "primary"): date(1992, 5, 26),
    (1992, "general"): date(1992, 11, 3),
    (1995, "primary"): date(1995, 5, 23),
    (1995, "general"): date(1995, 11, 7),
    (1996, "primary"): date(1996, 5, 28),
    (1996, "general"): date(1996, 11, 5),
    (1998, "general"): date(1998, 11, 3),
}
OFFICES = [
    (r"PRESIDENT", "president"),
    (r"SENAT", "senate"),
    (r"LT\.? GOVERN|LIEUTEN", "lieutenant-governor"),
    (r"GOVERNOR", "governor"),
    (r"SECRETARY OF STATE", "secretary-of-state"),
    (r"ATTORNEY GENERAL|ATTGEN", "attorney-general"),
    (r"AUDITOR", "auditor"),
    (r"TREASURER|TRESURER", "treasurer"),
    (r"AGRICUL", "commissioner-of-agriculture"),
    (r"SUPER", "superintendent-of-public-instruction"),
]
NAMES = {
    "WALTER MONDALE GERALDINE A. FERRARO": "Walter Mondale",
    "MEL MASON MATILDE ZIMMERMAN": "Mel Mason",
    "LYNDON H. LAROUCHE, JR. BILLY M. DAVIS": "Lyndon H. LaRouche, Jr.",
    "DENNIS L. SERRETTE NANCY ROSS": "Dennis L. Serrette",
    "FORD & DOLE": "Gerald R. Ford",
    "CARTER & MONDALE": "Jimmy Carter",
    "ANDERSON & SHACKELFORD": "Thomas J. Anderson",
    "HALL & TYNER": "Gus Hall",
    "MADDOX & DYKE": "Lester Maddox",
    "LAROUCHE & EVANS": "Lyndon H. LaRouche, Jr.",
    "CAMEJO & REID": "Peter Camejo",
    "MACBRIDE & BERGLAND": "Roger MacBride",
    "MCCARTHY & BARTLETT": "Eugene McCarthy",
    "CARTER MONDALE": "Jimmy Carter",
    "REAGAN BUSH": "Ronald Reagan",
    "MCCORMACK DRISCOLL": "Ellen McCormack",
    "PULLEY ZIMMERMAN": "Andrew Pulley",
    "HALL DAVIS": "Gus Hall",
    "CLARK KOCH": "Ed Clark",
    "ANDERSON LUCEY": "John B. Anderson",
    "COMMONER HANSON": "Barry Commoner",
    "RAY I AM A DEMOCRAT ADKINS": 'Ray "I Am a Democrat" Adkins',
}
TICKETS = {"80res_president1.txt", "84president2.txt", "95Gen_govltgov.txt", "95Pri_govltgov1.txt"}
TYPOS = {"lloyd": "floyd"}
ALONE = {"John Y. Brown, Jr.", "John Y. Brown III", 'William "Will T" Scott'}
CODES = {"DEM": "democratic", "REP": "republican"}


def fetch() -> None:
    for stamp, path in FILES:
        address = WAYBACK.format(stamp, SITE + path)
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            for attempt in range(6):
                time.sleep(2 + 30 * attempt)
                try:
                    raw.fetch(SOURCE, address, suffix=".txt")
                    break
                except httpx.HTTPError:
                    if attempt == 5:
                        raise


def title(text: str) -> str:
    words = []
    for word in text.replace("C.P.A.", "").split():
        word = re.sub(r"(^|[\"(.-])([a-z])", lambda m: m[1] + m[2].upper(), word.lower())
        word = re.sub(
            r"^(\"?)(Mc|O'|Mac(?=[bcdfghjklmnpqrstvwz]{2}))([a-z])", lambda m: m[1] + m[2] + m[3].upper(), word
        )
        words.append(word.upper() if re.fullmatch(r"i{1,3}|iv", word, re.IGNORECASE) else word)
    return " ".join(words)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%'"""
            ).fetchall()
        }
    if len(counties) != 120:
        raise ValueError("Kentucky needs 120 counties")
    rows: list[dict[str, object]] = []
    for stamp, path in FILES:
        name = path.rsplit("/", 1)[1]
        year = 1900 + int(name[:2])
        lines = [
            line.rstrip()
            for line in raw.cached(SOURCE, WAYBACK.format(stamp, SITE + path))
            .path.read_text(errors="replace")
            .replace("\r", "")
            .split("\n")
        ]
        dashes = [index for index, line in enumerate(lines) if re.fullmatch(r"\s*-{20,}", line)]
        start = 0
        for total_at in [index for index, line in enumerate(lines) if line.startswith("TOTAL VOTES")]:
            below = [index for index in dashes if index < total_at]
            if len(below) < 2:
                raise ValueError(f"{name}: no table above line {total_at}")
            top, bottom = below[-2], below[-1]
            header = [line for line in lines[start:top] if line.strip()]
            body = [line for line in lines[top + 1 : bottom] if line.strip()]
            start = total_at + 1
            titles = " / ".join(line.strip() for line in header if len(line) - len(line.lstrip()) < 14).upper()
            stage = "primary" if "PRIMARY" in titles or re.search(r"pri|^79p|^80pres", name) else "general"
            office = next((key for pattern, key in OFFICES if re.search(pattern, titles)), None) or next(
                (key for pattern, key in OFFICES if re.search(pattern, name.upper())), None
            )
            if (
                office is None
                or "RAILROAD" in titles
                or len(body) != 120
                or (stage, office) == ("primary", "president")
            ):
                print(f"{name}: skipped the table above line {total_at} ({titles}, {len(body)} rows)")
                continue
            totals = [int(value) for value in lines[total_at][11:].split()]
            width = len(totals)
            codes = [""] * width
            names: list[list[str]] = [[] for _ in range(width)]
            for line in header:
                indent = len(line) - len(line.lstrip())
                if indent < 14:
                    continue
                for column in range(width):
                    piece = line[14 + 18 * column : 32 + 18 * column].strip()
                    if not piece:
                        continue
                    if (indent - 14) % 18 == 2 and re.fullmatch(r"[A-Z]{2,4}", piece):
                        codes[column] = piece
                    else:
                        names[column].append(piece)
            party = "democratic" if "DEMOCRAT" in titles else "republican" if "REPUBLICAN" in titles else None
            sums = [0] * width
            keys = [TYPOS.get(key, key) for key in (re.sub(r"[^a-z]", "", line[:14].lower()) for line in body)]
            missing = sorted(set(counties) - set(keys))
            if keys.count("") == 1 and len(missing) == 1:
                print(f"{name}: the unnamed row is {counties[missing[0]]}")
                keys[keys.index("")] = missing[0]
            if sorted(keys) != sorted(counties):
                raise ValueError(f"{name}: counties {sorted(set(keys) ^ set(counties))}")
            for key, line in zip(keys, body, strict=True):
                cells = [0] * width
                for match in re.finditer(r"\d+", line[14:]):
                    column = (match.end() + 14 - 15) // 18
                    if column >= width:
                        if int(match[0]):
                            print(f"{name}: dropped {match[0]} past the last column in {line[:14].strip()}")
                        continue
                    if cells[column]:
                        raise ValueError(f"{name}: {line!r} does not fit {width} columns")
                    cells[column] = int(match[0])
                for column, value in enumerate(cells):
                    sums[column] += value
                    label = " ".join(names[column])
                    head = (
                        label.split("&")[0]
                        if "&" in label
                        else " ".join(names[column][:-1] if name in TICKETS else names[column])
                    )
                    head = NAMES.get(label) or title(head)
                    rows.append(
                        {
                            "day": DAYS[(year, stage)],
                            "stage": stage,
                            "office": office,
                            "party": party or CODES.get(codes[column]) if stage == "primary" else None,
                            "code": codes[column] or None,
                            "candidate": "Write-ins" if re.match(r"WRITE", label) else head,
                            "label": label,
                            "county": counties[key],
                            "votes": value,
                        }
                    )
            if sums != totals:
                print(
                    f"{name}: skipped the {office} table above line {total_at}, its columns sum to {sums}, printed {totals}"
                )
                del rows[len(rows) - 120 * width :]
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if frame.filter(pl.col("stage") == "primary", pl.col("party").is_null()).height:
        raise ValueError("a primary has no party")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's president, U.S. Senate, governor, and statewide offices by county 1976–1998, "
            "with the Senate and statewide primaries, from the State Board of Elections",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def parts(text: str) -> tuple[list[str], set[str]]:
            words = [plain(word) for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text).split()]
            suffixes = {"jr", "sr", "ii", "iii", "iv"}
            return [word for word in words if word and word not in suffixes], set(words) & suffixes

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if candidate in ALONE or min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, mark = parts(candidate)
            theirs, their_mark = parts(texts[one])
            if mark & {"ii", "iii", "iv"} != their_mark & {"ii", "iii", "iv"}:
                return False
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elect.ky.gov/results/",
            license="Public record",
        )
        for stamp, path in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, SITE + path)), source)
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%%'"""
            ).fetchall()
        }
        if len(counties) != 120:
            raise ValueError("Kentucky needs 120 counties")
        kentucky = cs.polity("US-KY")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-KY'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        texts: dict[UUID, str] = {}
        chosen: dict[str, UUID] = {}
        for person, text, statewide, known, year in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':KY:[a-z-]+(:special|:democratic|:republican)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:KY:|:president$)' and x.value !~ ':KY:21\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            people.setdefault(f"{plain(text)[:1]}.|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, year)
            texts[person] = text
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            "DEM": cs.key("us.party", "democratic-party"),
            "REP": cs.key("us.party", "republican-party"),
            "LIB": cs.key("us.party", "libertarian-party"),
            "REF": cs.key("us.party", "reform-party"),
            "SOC": cs.key("us.party", "socialist-workers-party"),
            "AMI": cs.key("us.party", "american-independent-party"),
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        scopes = {"democratic": parties["DEM"], "republican": parties["REP"]}
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':KY:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':KY'")
        )
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        titles = {
            "president": "President",
            "senate": "U.S. Senate",
            "governor": "Governor",
            "lieutenant-governor": "Lieutenant Governor",
            "secretary-of-state": "Secretary of State",
            "attorney-general": "Attorney General",
            "auditor": "Auditor of Public Accounts",
            "treasurer": "State Treasurer",
            "commissioner-of-agriculture": "Commissioner of Agriculture",
            "superintendent-of-public-instruction": "Superintendent of Public Instruction",
        }
        made: set[UUID] = set()
        for (day, stage, office, party), lines in frame.sort("day").group_by(
            "day", "stage", "office", "party", maintain_order=True
        ):
            year = day.year
            general = next(
                (
                    value
                    for key, value in sorted(contests.items(), reverse=True)
                    if re.fullmatch(rf"{year}(-1[01]-\d\d)?:KY:{office}", key)
                ),
                None,
            )
            if stage == "general":
                key = f"{day}:KY:{office}"
                election = elections.get(f"{day}:KY") or elections[f"{year}:KY"]
            else:
                key = f"{day}:KY:{office}:{party}"
                found = elections.get(f"{day}:KY:primary") or elections.get(f"{day:%Y-%m}:KY:primary")
                if found is None:
                    found = cs.key("us.election", f"{day}:KY:primary")
                    elections[f"{day}:KY:primary"] = found
                    cs.add(
                        "election",
                        id=found,
                        polity_id=kentucky,
                        kind="primary",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(found, f"{year} Kentucky primary election")
                    cs.add("slug", path=f"us/ky/{day}-primary", entity_id=found)
                election = found
            contest = contests.get(key) or (general if stage == "general" else None)
            if contest is None:
                contest = cs.key("us.contest", key)
                contests[key] = contest
                if office == "senate":
                    if general is None:
                        raise ValueError(f"{key}: no general election contest")
                    ((body, seat, area),) = conn.execute(
                        "select body_id, seat_id, district_area_id from contest where id = %s", [general]
                    ).fetchall()
                    office_id = None
                else:
                    body = seat = None
                    area = state
                    office_key = f"{office}:KY"
                    office_id = cs.key("us.office", office_key)
                    if office_key not in offices:
                        offices.add(office_key)
                        cs.add("office", id=office_id, polity_id=kentucky, kind="other")
                        name(office_id, f"{titles[office]} of Kentucky")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if office == "senate" else "office",
                    office_id=office_id,
                    body_id=body,
                    seat_id=seat,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    party_scope_id=scopes[party] if stage == "primary" else None,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                if stage == "primary":
                    name(
                        contest,
                        f"{year} U.S. Senate {party.title()} primary in Kentucky"
                        if office == "senate"
                        else f"{year} Kentucky {titles[office]} {party.title()} primary",
                    )
                else:
                    name(contest, f"{year} Kentucky {titles[office]} election")
                cs.add("slug", path=f"us/ky/{day}-{stage}/{office}" + (f"-{party}" if party else ""), entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
                [contest],
            ).fetchall()
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, code, votes in (
                lines.group_by("candidate", "code", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                if candidate == "Write-ins":
                    found = [option for option, kind, _ in options if kind == "write_in"]
                    option = found[0] if len(found) == 1 else cs.key("us.option", f"{key}:write-ins")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                else:
                    matches = [
                        option
                        for option, kind, text in options
                        if kind != "write_in" and surname(text or "") == surname(candidate)
                    ]
                    if len(matches) > 1:
                        raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                    if matches:
                        option = matches[0]
                    else:
                        first = plain(candidate.split()[0])
                        known = {
                            one
                            for one in people.get(plain(candidate))
                            or people.get(f"{first}|{surname(candidate)}")
                            or (
                                people.get(f"{first}.|{surname(candidate)}", set())
                                if len(first) == 1 and len(candidate.split()) == 2
                                else set()
                            )
                            if one in made or fits(one, candidate, year)
                        }
                        person = chosen.get(plain(candidate)) or (
                            max(
                                known,
                                key=lambda one: (
                                    ranks.get(one, (False, False, []))[0],
                                    ranks.get(one, (False, False, []))[1],
                                    -min(
                                        (abs(other - year) for other in ranks.get(one, (False, False, [year]))[2]),
                                        default=0,
                                    ),
                                ),
                            )
                            if known
                            else cs.key(f"{SOURCE}.person", f"KY:{slug(candidate)}")
                        )
                        chosen[plain(candidate)] = person
                        if person in cs.minted and person not in made:
                            made.add(person)
                            people.setdefault(plain(candidate), set()).add(person)
                            people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                            cs.add("person", id=person)
                            name(person, candidate)
                        elif person not in made:
                            print(f"{key}: {candidate} is joined to an existing person")
                        option = cs.key("us.option", f"{key}:{person}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=scopes[party] if stage == "primary" else parties.get(code or ""),
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=code == "IND",
                        )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
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
            )
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[plain(county)],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max((option for option in totals if option != picked.get("Write-ins")), key=totals.__getitem__)
                if stage == "general":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
                else:
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
        return cs.write()
