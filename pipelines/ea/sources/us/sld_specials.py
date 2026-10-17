import csv
import io
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.klarner import LOWER, UPPER
from ea.store import DATA, connect

SOURCE = "us.sld_specials"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FLORIDA = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={0.month}/{0.day}/{0.year}&OfficialResults=Y"
IOWA = "https://electionresults.iowa.gov/IA/{}/json/en/summary.json"
SOUTH_CAROLINA = "https://www.enr-scvotes.org/SC/{}/json/en/summary.json"
PENNSYLVANIA = (
    "https://www.electionreturns.pa.gov/api/ElectionReturn/GetOfficeData?officeId=0&methodName=GetOfficeDetails"
    "&electionid={}&electiontype=S&isactive=0"
)
WASHINGTON = "https://results.vote.wa.gov/results/{0:%Y%m%d}/export/{0:%Y%m%d}_legislative.csv"
CONNECTICUT = "https://ct.elstats.civera.com/api/download_contest/{}_table.csv?split_party=false"
GEORGIA = "https://results.sos.ga.gov/results/public/api/elections/Georgia/{}/ballot-items"
RHODE_ISLAND = "https://electionresults.ri.gov/results/public/api/elections/rhodeisland/{}/ballot-items"
LOUISIANA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={:%Y%m%d}/Votes_Multiparish.htm"
MASSACHUSETTS = "https://electionstats.state.ma.us/elections/download/{}/precincts_include:0/"
VIRGINIA = "https://va2.elstats.civera.com/api/download_contest/{}_table.csv?split_party=false"
PUBLISHERS = {
    "MA": "Massachusetts Secretary of the Commonwealth",
    "NH": "New Hampshire Secretary of State",
    "VA": "Virginia Department of Elections",
    "AL": "Alabama Secretary of State",
    "CA": "California Secretary of State",
    "DE": "Delaware Department of Elections",
    "GA": "Georgia Secretary of State",
    "LA": "Louisiana Secretary of State",
    "ME": "Maine State Law and Legislative Reference Library",
    "RI": "Rhode Island Board of Elections",
    "CT": "Connecticut Secretary of the State",
    "FL": "Florida Division of Elections",
    "IA": "Iowa Secretary of State",
    "NJ": "New Jersey Division of Elections",
    "NY": "New York City Board of Elections",
    "PA": "Pennsylvania Department of State",
    "SC": "South Carolina Election Commission",
    "WA": "Washington Secretary of State",
}
SYSTEMS = {"GA": "two_round", "LA": "two_round", "MS": "two_round", "TX": "two_round", "CA": "top_two", "WA": "top_two"}
SPECIALS: dict[str, tuple[str, str, str | None]] = {
    "2025-01-28:IA:senate:35": ("clarity", IOWA.format("123074/356352"), "State Senate District 35"),
    "2025-03-11:IA:house:100": ("clarity", IOWA.format("123279/356897"), "State Representative District 100"),
    "2025-04-29:IA:house:78": (
        "transcribed",
        "https://sos.iowa.gov/sites/default/files/2025-05/CanvassSummaryReport%202025_HD78.pdf",
        None,
    ),
    "2025-08-26:IA:senate:1": ("clarity", IOWA.format("124080/359928"), "State Senate District 1"),
    "2025-12-09:IA:house:7": ("clarity", IOWA.format("125502/367702"), "State Representative District 7"),
    "2025-12-30:IA:senate:16": ("clarity", IOWA.format("125665/367717"), "Senate District 16"),
    "2025-03-25:SC:house:113": ("clarity", SOUTH_CAROLINA.format("123437/357172"), "District  113"),
    "2025-06-03:SC:house:50": ("clarity", SOUTH_CAROLINA.format("123663/358891"), "District 50"),
    "2025-12-23:SC:senate:12": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Senate, District  12"),
    "2025-12-23:SC:house:21": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Representatives, District  21"),
    "2025-12-23:SC:house:88": ("clarity", SOUTH_CAROLINA.format("125770/367661"), "Representatives, District  88"),
    "2025-06-10:FL:house:3": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-06-10:FL:house:32": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-06-10:FL:senate:19": ("florida", FLORIDA.format(date(2025, 6, 10)), None),
    "2025-09-02:FL:house:40": ("florida", FLORIDA.format(date(2025, 9, 2)), None),
    "2025-09-02:FL:senate:15": ("florida", FLORIDA.format(date(2025, 9, 2)), None),
    "2025-12-09:FL:house:90": ("florida", FLORIDA.format(date(2025, 12, 9)), None),
    "2025-12-09:FL:senate:11": ("florida", FLORIDA.format(date(2025, 12, 9)), None),
    "2025-03-25:PA:house:35": ("pennsylvania", PENNSYLVANIA.format(110), None),
    "2025-03-25:PA:senate:36": ("pennsylvania", PENNSYLVANIA.format(109), None),
    "2025-02-25:CT:house:40": ("elstats", CONNECTICUT.format(38457), None),
    "2025-02-25:CT:senate:21": ("elstats", CONNECTICUT.format(38459), None),
    "2025-04-22:CT:house:113": ("elstats", CONNECTICUT.format(38460), None),
    "2025-11-04:WA:senate:5": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 5 - state senator"),
    "2025-11-04:WA:senate:26": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 26 - state senator"),
    "2025-11-04:WA:senate:33": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 33 - state senator"),
    "2025-11-04:WA:senate:34": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 34 - state senator"),
    "2025-11-04:WA:senate:48": ("washington", WASHINGTON.format(date(2025, 11, 4)), "district 48 - state senator"),
    "2025-11-04:WA:house:33:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 33 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:34:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 34 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:41:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 41 - state representative pos. 1",
    ),
    "2025-11-04:WA:house:48:1": (
        "washington",
        WASHINGTON.format(date(2025, 11, 4)),
        "district 48 - state representative pos. 1",
    ),
    "2025-11-04:NJ:senate:35": (
        "transcribed",
        "https://www.nj.gov/state/elections/assets/pdf/election-results/2025/2025-official-general-results-state-senate-35ld.pdf",
        None,
    ),
    "2025-05-20:NY:senate:22": (
        "transcribed",
        (
            "https://vote.nyc/sites/default/files/pdf/election_results/2025/20250520Special%20Election/"
            "00302000022Kings%20State%20Senator%2022nd%20Senatorial%20District%20Recap.csv"
        ),
        None,
    ),
    "2025-06-24:AL:senate:5": (
        "transcribed",
        "https://www.sos.alabama.gov/sites/default/files/election-2025/StateCertificationofSpecialGeneralElection-SD-5.pdf",
        None,
    ),
    "2025-08-26:AL:house:11": (
        "transcribed",
        "https://www.sos.alabama.gov/sites/default/files/election-2025/StateCertificationofGeneralElectionResultsHD11.pdf",
        None,
    ),
    "2025-02-25:CA:assembly:32": (
        "transcribed",
        "https://elections.cdn.sos.ca.gov/special-elections/2025-ad32/ad32-official-canvass.xlsx",
        None,
    ),
    "2025-02-25:CA:senate:36": (
        "transcribed",
        "https://elections.cdn.sos.ca.gov/special-elections/2025-sd36/sd36-official-canvass.xlsx",
        None,
    ),
    "2025-02-15:DE:senate:1": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250215_sd01/2025SpecialElection_SD01_OfficialResults.pdf",
        None,
    ),
    "2025-02-15:DE:senate:5": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250215_sd05/2025SpecialElection_SD05_OfficialResults.pdf",
        None,
    ),
    "2025-08-05:DE:house:20": (
        "transcribed",
        "https://elections.delaware.gov/elections/special/20250805_rd20/XB2025_OfficialResults.pdf",
        None,
    ),
    "2025-02-25:ME:house:24": (
        "transcribed",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2025/20250225/elec_20250225b.pdf",
        None,
    ),
    "2025-08-26:GA:senate:21": ("enhanced", GEORGIA.format("SpecialElection2625"), "Senate - District 21"),
    "2025-09-23:GA:senate:21:runoff": (
        "enhanced",
        GEORGIA.format("SpecialElectionDistrict21Runoff92325"),
        "Senate - District 21",
    ),
    "2025-11-04:GA:house:106": (
        "enhanced",
        GEORGIA.format("MunicipalGeneralSpecialElectionPSC11042025"),
        "Representatives - District 106",
    ),
    "2025-11-18:GA:senate:35": (
        "enhanced",
        GEORGIA.format("November18-2025-SD35SpecialElection"),
        "Senate - District 35",
    ),
    "2025-12-02:GA:house:106:runoff": (
        "enhanced",
        GEORGIA.format("December2nd2025Runoffs"),
        "Representatives - District 106",
    ),
    "2025-12-09:GA:house:23": (
        "enhanced",
        GEORGIA.format("December9th2025SpecialElectoinStateHouse23and121"),
        "Representatives - District 23",
    ),
    "2025-12-09:GA:house:121": (
        "enhanced",
        GEORGIA.format("December9th2025SpecialElectoinStateHouse23and121"),
        "Representatives - District 121",
    ),
    "2025-12-16:GA:senate:35:runoff": (
        "enhanced",
        GEORGIA.format("12162025SD35SpecialElectionRunoff"),
        "Senate - District 35",
    ),
    "2026-01-06:GA:house:23:runoff": ("enhanced", GEORGIA.format("Jan6HD23SERunoff"), "Representatives - District 23"),
    "2025-08-05:RI:senate:4": ("enhanced", RHODE_ISLAND.format("specaug25"), "Senator in General Assembly District 4"),
    "2025-02-15:LA:senate:14": ("louisiana", LOUISIANA.format(date(2025, 2, 15)), "14th Senatorial District"),
    "2025-02-15:LA:senate:23": ("louisiana", LOUISIANA.format(date(2025, 2, 15)), "23rd Senatorial District"),
    "2025-05-03:LA:house:67": ("louisiana", LOUISIANA.format(date(2025, 5, 3)), "67th Representative District"),
    "2025-05-13:MA:house:essex-6": ("elstats", MASSACHUSETTS.format(171339), None),
    "2025-06-10:MA:house:bristol-3": ("elstats", MASSACHUSETTS.format(171342), None),
    "2025-06-24:NH:house:strafford-12": (
        "transcribed",
        "https://www.sos.nh.gov/strafford-county-district-12-june-24-2025",
        None,
    ),
    "2025-11-04:NH:house:coos-5": (
        "transcribed",
        "https://www.sos.nh.gov/coos-county-district-5-november-4-2025",
        None,
    ),
    "2025-01-07:VA:house:26": ("elstats", VIRGINIA.format(161237), None),
    "2025-01-07:VA:senate:10": ("elstats", VIRGINIA.format(161235), None),
    "2025-01-07:VA:senate:32": ("elstats", VIRGINIA.format(161236), None),
}
TRANSCRIBED: dict[str, list[tuple[str | None, str | None, int]]] = {
    "2025-06-24:AL:senate:5": [
        ("Matt Woods", "Republican", 7715),
        ("Ryan Cagle", "Democratic", 1240),
        (None, None, 14),
    ],
    "2025-08-26:AL:house:11": [
        ("Heath Allbright", "Republican", 2783),
        ("Alexandria Braswell", "Democratic", 352),
        (None, None, 15),
    ],
    "2025-02-25:CA:assembly:32": [
        ("Stan W. Ellis", "Republican", 39410),
        ("Chris Cruz-Boone", "Democratic", 17474),
        ("Holli Willibey", "Republican", 2665),
        ("William Brown Jr.", "Libertarian", 1427),
    ],
    "2025-02-25:CA:senate:36": [
        ("Tony Strickland", "Republican", 81133),
        ("Jimmy D. Pham", "Democratic", 43730),
        ("Julie Diep", "Democratic", 22647),
        ("John Briscoe", "Republican", 10588),
    ],
    "2025-02-15:DE:senate:1": [
        ("Dan Cruce", "Democratic", 3654),
        ("Steven Washington", "Republican", 990),
        ('Riley "Liv" Figliola', "Nonpartisan", 88),
        (None, None, 7),
    ],
    "2025-02-15:DE:senate:5": [
        ("Ray Seigfried", "Democratic", 3780),
        ("Brent Burdge", "Republican", 2015),
        (None, None, 12),
    ],
    "2025-08-05:DE:house:20": [
        ("Alonna Berry", "Democratic", 4653),
        ("Nikki Miller", "Republican", 4536),
        (None, None, 4),
    ],
    "2025-02-25:ME:house:24": [("Sean Faircloth", "Democratic", 1431), ("Carolyn A. Fish", "Republican", 567)],
    "2025-06-24:NH:house:strafford-12": [("Billie Butler", "Democratic", 964), ("Ken Hilton", "Republican", 774)],
    "2025-11-04:NH:house:coos-5": [("Corinne Cascadden", "Democratic", 873), ("Marc D. Tremblay", "Republican", 878)],
    "2025-04-29:IA:house:78": [("Angel Ramirez", "DEM", 2747), ("Bernie Hayes", "REP", 721), (None, None, 6)],
    "2025-11-04:NJ:senate:35": [
        ("Benjie E. Wimberly", "Democratic", 31676),
        ("Frank G. Filippelli", "Republican", 11957),
    ],
    "2025-05-20:NY:senate:22": [
        ("Sam Sutton", "Democratic", 8970),
        ("Nachman Carl Caller", "Republican", 3736),
        ("Nachman Carl Caller", "Conservative", 536),
        (None, None, 140),
    ],
}
PARTIES = {
    "democratic": "democratic-party",
    "democrat": "democratic-party",
    "dem": "democratic-party",
    "dfl": "democratic-party",
    "democratic-farmer-labor": "democratic-party",
    "republican": "republican-party",
    "rep": "republican-party",
    "gop": "republican-party",
    "libertarian": "libertarian-party",
    "lib": "libertarian-party",
    "lpf": "libertarian-party",
    "green": "green-party",
    "grn": "green-party",
    "gre": "green-party",
    "NY:conservative": "NY:conservative-party",
    "NY:working families": "NY:working-families-party",
}
INDEPENDENT = {"", "npa", "non", "ind", "np", "nop", "una", "independent", "nonpartisan", "no party preference"}
SUFFIXES = {
    "clarity": ".json",
    "florida": ".txt",
    "pennsylvania": ".json",
    "washington": ".csv",
    "elstats": ".csv",
    "enhanced": ".json",
    "louisiana": ".json",
    "transcribed": "",
}
WRITE_INS = re.compile(r"^(write-?ins?|scatter(ing)?|write in)$", re.IGNORECASE)


def fetch() -> None:
    for system, url, _ in SPECIALS.values():
        raw.fetch(SOURCE, url, suffix=SUFFIXES[system] or Path(url.split("?")[0]).suffix)
        if system == "louisiana":
            raw.fetch(SOURCE, url.replace("Votes_", "RacesCandidates_"), suffix=".json")


def _title(text: str) -> str:
    text = " ".join(text.split())
    if text.isupper() or text.islower():
        text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
    return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)


def _party(state: str, text: str | None) -> str | None:
    label = re.sub(r"^\((?:prefers )?(.+?)(?: party)?\)$", r"\1", (text or "").strip().lower()).removesuffix(" party")
    if label in INDEPENDENT:
        return None
    found = PARTIES.get(f"{state}:{label}") or PARTIES.get(label)
    if found is None:
        raise ValueError(f"{state}: unknown party {text!r}")
    return found


def parse() -> None:
    rows: list[dict[str, object]] = []
    for key, (system, url, select) in SPECIALS.items():
        state = key.split(":")[1]
        found: list[tuple[str | None, str | None, int]] = []
        path = raw.cached(SOURCE, url).path
        if system == "clarity":
            contests = [contest for contest in json.loads(path.read_text()) if select and select in contest["C"]]
            if len(contests) != 1:
                raise ValueError(f"{key}: {len(contests)} contests match {select!r}")
            for label, party, votes in zip(contests[0]["CH"], contests[0]["P"], contests[0]["V"], strict=True):
                label = re.sub(r"^(DEM|REP|LIB|GRN|CON|IND|NON|PET) ", "", label)
                found.append((None, None, int(votes)) if WRITE_INS.match(label) else (_title(label), party, int(votes)))
        elif system == "florida":
            code = "STS" if ":senate:" in key else "STR"
            totals: dict[tuple[str, str], int] = defaultdict(int)
            for row in csv.DictReader(io.StringIO(path.read_text()), delimiter="\t"):
                if row["RaceCode"] == code and int(row["Juris1num"]) == int(key.split(":")[3]):
                    label = " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split())
                    totals[(label, row["PartyCode"])] += int(row["CanVotes"])
            found = [(label, party, votes) for (label, party), votes in totals.items() if party != "WRI"]
            if writes := sum(votes for (_, party), votes in totals.items() if party == "WRI"):
                found.append((None, None, writes))
        elif system == "pennsylvania":
            for offices in json.loads(json.loads(path.read_text()))["Election"].values():
                for office in offices:
                    for districts in office.values():
                        for district in districts:
                            for candidate in district["Candidates"]:
                                found.append(
                                    (
                                        _title(candidate["CandidateName"]),
                                        candidate["PartyName"],
                                        int(candidate["Votes"]),
                                    )
                                )
        elif system == "washington":
            assert select is not None
            for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
                if row["Race"].lower().endswith(select) and row["Race"].lower().startswith("legislative district"):
                    written = row["Candidate"].upper() == "WRITE-IN"
                    found.append(
                        (None, None, int(row["Votes"]))
                        if written
                        else (row["Candidate"], row["Party"], int(row["Votes"]))
                    )
        elif system == "elstats":
            table = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
            totals_row = next(row for row in table[2:] if row[0].startswith(("State ", "TOTALS")))
            writes = 0
            parties = table[1] + [""] * (len(table[0]) - len(table[1]))
            for label, party, votes in zip(table[0][1:], parties[1:], totals_row[1:], strict=False):
                if label in ("Same Day Registration (SDR)", "Total Votes Cast", "Total Ballots Cast", "Blanks", ""):
                    continue
                if party:
                    found.append((label, party, int(votes.replace(",", ""))))
                else:
                    writes += int(votes.replace(",", ""))
            if writes:
                found.append((None, None, writes))
        elif system == "enhanced":
            assert select is not None
            items = [
                item
                for item in json.loads(path.read_text())["data"]
                if item["name"][0]["text"].split("/")[0].strip().endswith(select)
            ]
            if len(items) != 1:
                raise ValueError(f"{key}: {len(items)} contests match {select!r}")
            for option in items[0]["summaryResults"]["ballotOptions"]:
                label = re.sub(
                    r"^(DEM|REP|LIB|GRN|IND|NON) | \((Dem|Rep|Lib|Grn|Ind|Non)\)$", "", option["name"][0]["text"]
                )
                party = (option.get("party") or {}).get("abbreviation")
                found.append(
                    (None, None, option["voteCount"])
                    if option["isWriteIn"] or WRITE_INS.match(label)
                    else (label, party, option["voteCount"])
                )
        elif system == "louisiana":
            assert select is not None
            races = json.loads(raw.cached(SOURCE, url.replace("Votes_", "RacesCandidates_")).path.read_text())
            race = next(race for race in races["Races"]["Race"] if race["SpecificTitle"].endswith(select))
            choices = {choice["ID"]: choice["Desc"] for choice in race["Choice"]}
            counted = next(item for item in json.loads(path.read_text())["Races"]["Race"] if item["ID"] == race["ID"])
            for choice in counted["Choice"]:
                match = re.fullmatch(r"(.+) \((\w+)\)", choices[choice["ID"]])
                assert match is not None
                found.append((match[1], match[2], int(choice["VoteTotal"])))
        else:
            found = TRANSCRIBED[key]
        if not found:
            raise ValueError(f"{key}: no candidates")
        for label, party, votes in found:
            rows.append({"key": key, "candidate": label, "party": _party(state, party), "votes": votes})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"key": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64}
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        elections_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")
        }
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        known: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for body, person, ballot in conn.execute(
            """select distinct b.value, cd.person_id, cd.ballot_name
               from candidacy cd join contest_option o on o.id = cd.option_id
               join contest ct on ct.id = o.contest_id join election e on e.id = ct.election_id
               join external_id b on b.entity_id = ct.body_id and b.scheme = 'us.body'
               where e.date_from >= '2014-01-01' and cd.role = 'head' and cd.ballot_name is not null
                 and b.value ~ '^[A-Z]{2}:(senate|house|assembly|house-of-delegates|general-assembly|legislature)$'"""
        ).fetchall():
            known[(body.split(":")[0], slug(ballot))].add(person)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative special elections from each state's official results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def unique(path: str, entity: UUID) -> str:
            if slugs.setdefault(path, entity) != entity:
                raise ValueError(f"slug {path} is taken")
            return path

        systems = {code: cs.key("us.electoral_system", code) for code in ("fptp", "two_round", "top_two")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, two-round, and top-two systems must already exist")
        sources: dict[str, UUID] = {}
        cited: set[str] = set()
        persons: dict[tuple[str, str], UUID] = {}
        contests: dict[str, UUID] = {}
        for (key,), group in lines.group_by("key", maintain_order=True):
            assert isinstance(key, str)
            url = SPECIALS[key][1]
            parts = key.split(":")
            day, state, chamber, code = date.fromisoformat(parts[0]), parts[1], parts[2], parts[3]
            post = parts[4] if len(parts) > 4 and parts[4] != "runoff" else None
            runoff = key.endswith(":runoff")
            if f"{state}:{day}" not in sources:
                publisher = PUBLISHERS[state]
                title = f"Official results, {day:%B} {day.day}, {day.year} special election"
                sources[f"{state}:{day}"] = cs.source(publisher, title)
                cs.add(
                    "source",
                    id=sources[f"{state}:{day}"],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            source = sources[f"{state}:{day}"]
            if url not in cited:
                cited.add(url)
                cs.artifact(raw.cached(SOURCE, url), source)
            if chamber == "senate":
                body_name, body_key, scheme = UPPER.get(state, "Senate"), f"{state}:senate", "us.sldu"
            else:
                body_name = LOWER.get(state, "House of Representatives")
                body_key = f"{state}:{slug(body_name.removeprefix('State ').removesuffix(' of Representatives'))}"
                scheme = "us.sldl"
            body = cs.key("us.body", body_key)
            if body in cs.minted:
                raise ValueError(f"{key}: no body {body_key}")
            body_slug = body_key.split(":")[1]
            full = f"{state}:{code}"
            area, unit = cs.area(scheme, full), cs.unit(scheme, full)
            if area in cs.minted or unit in cs.minted:
                raise ValueError(f"{key}: no district {scheme} {full}")
            general = f"{day}:{state}"
            on_general = general in elections_known
            election_key = general if on_general else f"{day}:{state}:{'runoff' if runoff else 'special'}"
            election = cs.key("us.election", election_key)
            if election in cs.minted and election_key not in elections_known:
                elections_known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="runoff" if runoff else "special",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, {day.year} {states[state]} {'runoff' if runoff else 'special'} election",
                )
                cs.add(
                    "slug",
                    path=unique(f"us/{state.lower()}/{day}-{'runoff' if runoff else 'special'}", election),
                    entity_id=election,
                )
            etype = ("gs" if on_general else "s") + ("runoff" if runoff else "")
            contest_key = f"{day}:{state}:{body_slug}:{code}" + (f":{post}" if post else "") + f":{etype}"
            contest = contests[key] = cs.key("us.contest", contest_key)
            previous = None
            if runoff:
                first = next(
                    (other for other in contests if other.split(":")[1:] == key.removesuffix(":runoff").split(":")[1:]),
                    None,
                )
                previous = contests[first] if first else None
            electoral_system = SYSTEMS.get(state, "fptp")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=1,
                term="remainder",
                round_number=2 if previous else 1,
                previous_round_id=previous,
                electoral_system_id=systems[electoral_system],
                default_result_kind="counted",
            )
            shown = (
                f"District {code.upper().replace('-', '')}" if code[:1].isdigit() else _title(code.replace("-", " "))
            )
            where = shown + (f", Position {post}" if post else "")
            name(
                contest,
                f"{day.year} {states[state]} {body_name} special election{' runoff' if runoff else ''}, {where}",
            )
            cs.add(
                "slug",
                path=unique(
                    f"us/{state.lower()}/{election_key.split(':', 2)[0]}-{'general' if on_general else 'runoff' if runoff else 'special'}"
                    f"/{'state-' if body_slug in ('senate', 'house') else ''}{body_slug}-{code}"
                    + (f"-post-{post}" if post else "")
                    + f"-special-election{'-runoff' if runoff else ''}",
                    contest,
                ),
                entity_id=contest,
            )
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
                completeness=1,
            )
            total = int(group["votes"].sum())
            ranked: list[tuple[int, UUID]] = []
            values: list[tuple[UUID, int]] = []
            for (candidate,), rows in group.group_by("candidate", maintain_order=True):
                if candidate is None:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    values.append((option, int(rows["votes"].sum())))
                    continue
                assert isinstance(candidate, str)
                lines_ = list(rows.select("party", "votes").iter_rows())
                parties = [None if party is None else cs.key("us.party", party) for party, _ in lines_]
                matches = known.get((state, slug(candidate)), set())
                person = persons.get((state, slug(candidate))) or (
                    next(iter(matches))
                    if len(matches) == 1
                    else cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                )
                if person in cs.minted and (state, slug(candidate)) not in persons:
                    cs.add("person", id=person)
                    name(person, candidate)
                persons[(state, slug(candidate))] = person
                option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[0],
                    ballot_label=candidate,
                )
                head = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head")
                cs.add(
                    "candidacy",
                    id=head,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=all(party is None for party in parties),
                )
                if len(lines_) > 1:
                    for (party_key, votes), party in zip(lines_, parties, strict=True):
                        line = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{party_key or 'no-party'}")
                        if party is not None:
                            cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                        cs.add(
                            "contest_option",
                            id=line,
                            contest_id=contest,
                            parent_option_id=option,
                            kind="candidate",
                            party_id=party,
                            ballot_label=f"{candidate} ({_title((party_key or 'no party').split(':')[-1].replace('-', ' '))})",
                        )
                        values.append((line, votes))
                else:
                    values.append((option, lines_[0][1]))
                ranked.append((int(rows["votes"].sum()), option))
            ranked.sort(key=lambda pair: -pair[0])
            if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
                raise ValueError(f"{key}: tie")
            if electoral_system == "two_round" and not runoff and ranked[0][0] * 2 <= total:
                for _, option in ranked[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
            else:
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="elected", seats=1)
            for option, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
