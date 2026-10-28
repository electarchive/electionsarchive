import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import initials
from ea.sources.us import elstats
from ea.sources.us.ak_dor import CONTESTS as RANKED_ROUNDS
from ea.sources.us.ak_dor import MEDSL
from ea.sources.us.klarner import LOWER, UPPER
from ea.sources.us.wikidata_links import NICKNAMES
from ea.store import DATA, connect

SOURCE = "us.medsl_state"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
RETURNS = f"{ACCESS}/13730906"
CODEBOOK = f"{ACCESS}/13730907"
README = f"{ACCESS}/13730911"
PUBLISHER = "MIT Election Data and Science Lab"
TITLE = "State Precinct-Level Returns 2024"
WASHINGTON = "https://results.vote.wa.gov/results/20241105/export/20241105_Legislative.csv"
NEW_MEXICO = "https://raw.githubusercontent.com/openelections/openelections-data-nm/master/2024/20241105__nm__general__county.csv"
NM_PUBLISHER = "OpenElections"
NM_TITLE = "New Mexico 2024 general election results by county"
NM_PARTIES = {"DEM": "DEMOCRAT", "REP": "REPUBLICAN", "LIB": "LIBERTARIAN", "IND": "INDEPENDENT"}
NEW_YORK = elstats.SEARCH.format(tenant=elstats.TENANTS["NY"][0]) + urlencode(
    {
        "search": json.dumps(
            {
                "global": {"years": {"from": 2024, "to": 2024}},
                "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
                "contests": {"candidates": [], "divisions": [], "offices": [{"id": 14}]},
                "specialElectionsOnly": False,
                "voterStats": False,
                "stages": [],
            },
            separators=(",", ":"),
        )
    }
)
RHODE_ISLAND = "https://raw.githubusercontent.com/openelections/openelections-data-ri/977565f9d7cf7a32d3a2a454282b5fd291cef291/2024/20241105__ri__general__town.csv"
RI_TITLE = "Rhode Island 2024 general election results by town"
RI_PARTIES = {"DEM": "DEMOCRAT", "REP": "REPUBLICAN", "Ind": "INDEPENDENT"}
SUFFOLK = "https://apps2.suffolkcountyny.gov/boe/eleres/24ge/sd.htm"
SUFFOLK_PUBLISHER = "Suffolk County Board of Elections"
SUFFOLK_TITLE = "General election results, November 5, 2024: New York State Senate"
PIMA = "https://apps.azsos.gov/election/2024/ge/canvass/202411GeneralElectionCanvass-Pima.pdf"
AZ_CANVASS = "https://apps.azsos.gov/election/2024/ge/canvass/20241105_GeneralCanvass_Signed.pdf"
PIMA_PUBLISHER = "Pima County Elections"
PIMA_TITLE = "Official canvass, General Election, November 5, 2024"
AZ_PARTIES = {
    "NANCY GUTIERREZ": "DEMOCRAT",
    "CHRIS MATHIS": "DEMOCRAT",
    'LEONARD "LEN" ROSENBLUM': "REPUBLICAN",
    "ALMA HERNANDEZ": "DEMOCRAT",
    "BETTY VILLEGAS": "DEMOCRAT",
}
PIMA_NOTE = (
    "Votes are from Pima County's official canvass and parties from the Secretary of State's signed statewide "
    "canvass. The site answers scripts with a bot challenge, so a person saved both PDFs in a browser on "
    "October 23, 2026."
)
NY_PUBLISHER = "New York State Board of Elections"
NY_TITLE = "Election results, Member of Assembly, 2024"
WA_PUBLISHER = "Washington Secretary of State"
WA_TITLE = "November 5, 2024 General Election Results"
PREFERENCES = {"DEMOCRATIC": "DEMOCRAT", "GOP": "REPUBLICAN", "STATES NO": "NO PARTY"}
DAY = date(2024, 11, 5)
REGULAR = {("TX", "56")}
LEADERS = {"house:6", "house:28", "house:36", "house:38", "house:40", "senate:d", "senate:f", "senate:l"}
SWAPPED = {
    ("AZ", "YAVAPAI", "JAY MARCIA"): "MARCIA SMITH",
    ("AZ", "YAVAPAI", "RUBY SMITH"): "JAY RUBY",
    ("NY", "ESSEX", "BILLY D JONES"): "D BILLY JONES",
}
UNLABELED = {
    "AZ:KEVIN VOLK": "DEMOCRAT",
    "IA:CHRISTIAN HERMANSON": "REPUBLICAN",
    "NH:MARK PROULXR": "REPUBLICAN",
    "OR:SHANNON JONES ISADORE": "DEMOCRAT",
    "OR:APRIL DOBSON": "DEMOCRAT",
    "OR:WILLY CHOTZEN": "DEMOCRAT",
    "OR:KHANH PHAM": "DEMOCRAT",
}
POOLED = {"WRITE-IN", "SCATTER", "MISC", "OTHERS", "WRITE-INS", "WRITE IN"}
DROPPED = {
    "UNDERVOTES",
    "UNDERVOTE",
    "OVERVOTES",
    "OVERVOTE",
    "VOID",
    "UNDERVOTES-VOIDS",
    "SPOILED",
    "BLANKS",
    "BLANK BALLOTS",
    "TOTAL BALLOTS CAST",
    "TOTAL VOTES CAST",
    "CAST VOTES",
    "INVALID VOTES",
    "FOR",
    "PRO-LIFE",
}
NATIONAL = {
    "DEMOCRAT": "democratic-party",
    "DEMOCRATIC": "democratic-party",
    "DEMOCRATIC-NPL": "democratic-party",
    "DEMOCRATIC-FARMER-LABOR": "democratic-party",
    "REPUBLICAN": "republican-party",
    "LIBERTARIAN": "libertarian-party",
    "GREEN": "green-party",
    "CONSTITUTION": "constitution-party",
    "WE THE PEOPLE": "we-the-people",
    "VETERANS PARTY": "veterans-party-of-america",
    "AMERICAN INDEPENDENT": "american-independent-party",
}
INDEPENDENT = {
    "INDEPENDENT",
    "UNDECLARED",
    "NO PARTY AFFILIATION",
    "UNAFFILIATED",
    "NO PARTY",
    "UNENROLLED",
    "NON PARTY CANDIDATE",
    "WRITEIN",
}
CROSS_FILED = {("VT", "DEMOCRAT/REPUBLICAN"): "REPUBLICAN/DEMOCRAT"}
EXPANDED = {"PFP": "PEACE AND FREEDOM", "CSS": "COMMON SENSE", "LOC607": "LOCAL 607"}


def fetch() -> None:
    raw.fetch(SOURCE, RETURNS, suffix=".csv")
    raw.fetch(SOURCE, CODEBOOK, suffix=".md")
    raw.fetch(SOURCE, README, suffix=".md")
    raw.fetch(SOURCE, WASHINGTON, suffix=".csv")
    raw.fetch(SOURCE, NEW_MEXICO, suffix=".csv")
    raw.fetch(SOURCE, NEW_YORK, suffix=".csv")
    raw.fetch(SOURCE, RHODE_ISLAND, suffix=".csv")
    raw.fetch(SOURCE, SUFFOLK, suffix=".htm")
    for url in (PIMA, AZ_CANVASS):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (DATA / "inbox" / "manual" / "MF-037" / url.rsplit("/", 1)[1]).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime(2026, 10, 23, tzinfo=UTC),
                media_type="application/pdf",
            )


def _district(state: str, text: str) -> tuple[str, str | None]:
    text = text.strip().upper()
    if match := re.fullmatch(r"DISTRICT (\d+) (?:SEAT|POSITION) ([A-Z0-9])", text):
        return str(int(match[1])), match[2]
    if match := re.fullmatch(r"(\d+)([A-Z])", text):
        return f"{int(match[1])}-{match[2].lower()}", None
    if text.isdigit():
        return str(int(text)), None
    if state == "AK" and re.fullmatch(r"[A-Z]", text):
        return text.lower(), None
    number = None
    if match := re.match(r"(\d+)(?:ST|ND|RD|TH) (.+)", text):
        number, text = match[1], match[2]
    elif match := re.match(r"(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH) (.+)", text):
        number = str(
            ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH"].index(
                match[1]
            )
            + 1
        )
        text = match[2]
    elif match := re.match(r"(.+?) 0*(\d+)$", text):
        text, number = match[1], match[2]
    letters = "".join(word for word in re.findall(r"[A-Z]+", text) if word != "AND").lower()
    return letters + (f"-{int(number)}" if number else ""), None


def fellow(one: str, other: str) -> bool:
    one, other = re.sub(r"[^A-Z]", "", one).lower(), re.sub(r"[^A-Z]", "", other).lower()
    canon = {form: first for first, forms in NICKNAMES.items() for form in forms.split()}
    return (
        not one
        or not other
        or one.startswith(other)
        or other.startswith(one)
        or one[:3] == other[:3]
        or canon.get(one, one) == canon.get(other, other)
    )


def _surname(name: str) -> str:
    words = [word for word in re.sub(r'"[^"]*"', " ", name).split() if word not in {"JR", "SR", "II", "III", "IV"}]
    return words[-1] if words else name


def _tokens(text: str) -> list[str]:
    text = re.sub(r'"[^"]*"|\([^)]*\)', " ", text.upper())
    text = re.sub(r",? (?:JR|SR|II|III|IV)\.?(?= |$)|,? (?:DEM|REP|LIB|GRN|IND)$", " ", text)
    return [token for token in (re.sub(r"[^A-Z]", "", word) for word in text.split()) if token]


def finer(days: set[date]) -> dict[tuple[date, str], list[list[tuple[str, UUID | None]]]]:
    pools: dict[tuple[date, str], list[list[tuple[str, UUID | None]]]] = defaultdict(list)
    harris = pl.read_parquet(
        raw.parsed("us.tx_harris") / "lines.parquet", columns=["county", "election", "contest", "choice"]
    )
    harris = harris.with_columns(day=pl.col("election").str.slice(0, 10).str.to_date()).filter(
        pl.col("day").is_in(days)
    )
    for (county, day, _), rows in harris.unique(["county", "day", "contest", "choice"]).group_by(
        "county", "day", "contest"
    ):
        pools[(day, county)].append([(choice, None) for choice in rows["choice"].to_list()])
    return pools


def resolve(
    names: list[str], same_day: list[list[tuple[str, UUID | None]]], history: list[tuple[str, UUID | None]]
) -> dict[str, tuple[str, UUID | None]]:
    def hits(name: str, pool: list[tuple[str, UUID | None]]) -> dict[str, tuple[str, UUID | None]]:
        bare = "".join(_tokens(name))
        found: dict[str, tuple[str, UUID | None]] = {}
        for text, person in pool:
            tokens = _tokens(text)
            if bare and any("".join(tokens[start:]) == bare for start in range(1, len(tokens))):
                found.setdefault(" ".join(tokens), (text, person))
                if person is not None:
                    found[" ".join(tokens)] = (text, person)
        return found

    single = [name for name in names if re.fullmatch(r"[A-Z'-]+", name)]
    proposals: dict[str, dict[str, tuple[str, UUID | None]]] = defaultdict(dict)
    for pool in same_day:
        matched = {name: hits(name, pool) for name in names}
        if single and all(len(matched[name]) == 1 for name in single):
            for name, found in matched.items():
                if len(found) == 1:
                    proposals[name].update(found)
    for name in names:
        if name not in proposals:
            found = hits(name, history)
            if len(found) == 1:
                proposals[name].update(found)
    return {name: next(iter(found.values())) for name, found in proposals.items() if len(found) == 1}


def parse() -> None:
    frame = (
        pl.scan_csv(raw.cached(SOURCE, RETURNS).path, infer_schema_length=0)
        .filter(pl.col("office").is_in(["STATE HOUSE", "STATE SENATE"]), pl.col("stage").is_in(["GEN", "GEN RECOUNT"]))
        .with_columns(
            pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            pl.col("candidate").str.strip_chars(),
            pl.col("magnitude").cast(pl.Float64, strict=False).cast(pl.Int32),
        )
        .filter(~pl.col("candidate").is_in(list(DROPPED)), pl.col("candidate").is_not_null())
        .collect()
    )
    frame = frame.with_columns(
        candidate=pl.struct("state_po", "county_name", "candidate").map_elements(
            lambda row: SWAPPED.get((row["state_po"], row["county_name"], row["candidate"]), row["candidate"]),
            return_dtype=pl.String,
        )
    )
    frame = frame.with_columns(
        party_detailed=pl.coalesce(
            "party_detailed",
            pl.concat_str("state_po", "candidate", separator=":").replace_strict(UNLABELED, default=None),
        )
    )
    recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select("state_po", "office", "district").unique()
    frame = pl.concat(
        [
            frame.join(recounted, on=["state_po", "office", "district"], how="anti"),
            frame.filter(pl.col("stage") == "GEN RECOUNT"),
        ]
    )
    precinct = ["state_po", "office", "district", "special", "county_fips", "jurisdiction_fips", "precinct"]
    line = ["candidate", "party_detailed", "writein"]
    frame = (
        frame.with_columns(total=(pl.col("mode") == "TOTAL").any().over(*precinct, *line))
        .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
        .with_columns(pooled=pl.col("candidate").is_in(list(POOLED)) | (pl.col("writein") == "TRUE"))
    )
    named = frame.filter(~pl.col("pooled"), pl.col("district").is_not_null())
    homes: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for state, office, candidate, district, special in (
        named.select("state_po", "office", "candidate", "district", "special").unique().iter_rows()
    ):
        homes[(state, office, candidate)].add((district, special))
    orphans = frame.filter(pl.col("district").is_null(), ~pl.col("pooled"))
    placed = []
    for row in orphans.iter_rows(named=True):
        found = homes.get((row["state_po"], row["office"], row["candidate"]), set())
        if len(found) == 1:
            district, special = next(iter(found))
            placed.append({**row, "district": district, "special": special})
    frame = pl.concat(
        [frame.filter(pl.col("district").is_not_null()), pl.DataFrame(placed, schema=frame.schema)]
        if placed
        else [frame.filter(pl.col("district").is_not_null())]
    )
    frame = frame.filter(pl.col("district") != "STATEWIDE")
    contest = ["state_po", "office", "district", "special"]
    lines = frame.group_by(*contest, "candidate", "party_detailed", "pooled").agg(
        pl.col("votes").sum(),
        pl.col("votes").is_null().any().alias("masked"),
        pl.col("magnitude").max(),
        pl.col("state_fips").first(),
    )
    canonical: dict[tuple[str, str, str, str, str], str] = {}
    for keys, group in lines.filter(~pl.col("pooled")).group_by(*contest):
        weights = Counter()
        for candidate, votes in group.select("candidate", "votes").iter_rows():
            weights[candidate] += votes or 0
        spelled: dict[str, str] = {}
        for name in sorted(weights, key=lambda name: -weights[name]):
            spelled.setdefault(re.sub(r"[^A-Z]", "", name), name)
        names = sorted(set(spelled.values()), key=lambda name: (-len(name.split()), -weights[name]))
        groups: dict[str, list[str]] = defaultdict(list)
        for name in names:
            groups[_surname(name)].append(name)
        for members in groups.values():
            full = [name for name in members if len(name.split()) > 1]
            for name in members:
                words = name.split()
                initial = words[0][0] if len(words) > 1 else None
                matches = [other for other in full if initial is None or other.split()[0][0] == initial]
                target = matches[0] if len({other.split()[0][0] for other in matches}) == 1 and matches else name
                if initial is None and len({other.split()[0][0] for other in full}) == 1 and full:
                    target = full[0]
                for variant in weights:
                    if spelled[re.sub(r"[^A-Z]", "", variant)] == name:
                        canonical[(*keys, variant)] = target
    lines = (
        lines.with_columns(
            candidate=pl.struct(*contest, "candidate", "pooled").map_elements(
                lambda row: (
                    "WRITE-IN"
                    if row["pooled"]
                    else canonical.get(
                        (row["state_po"], row["office"], row["district"], row["special"], row["candidate"]),
                        row["candidate"],
                    )
                ),
                return_dtype=pl.String,
            ),
            party_detailed=pl.when(pl.col("pooled")).then(None).otherwise(pl.col("party_detailed")),
        )
        .group_by(*contest, "candidate", "party_detailed", "pooled")
        .agg(
            pl.col("votes").sum(),
            pl.col("masked").any(),
            pl.col("magnitude").max(),
            pl.col("state_fips").first(),
        )
        .with_columns(
            pl.struct("state_po", "district")
            .map_elements(
                lambda row: dict(zip(("code", "post"), _district(row["state_po"], row["district"]), strict=True)),
                return_dtype=pl.Struct({"code": pl.String, "post": pl.String}),
            )
            .alias("parsed")
        )
        .unnest("parsed")
        .with_columns(magnitude=pl.col("magnitude").max().over(*contest).fill_null(1))
        .sort(*contest, "votes", descending=[False, False, False, False, True], nulls_last=True)
    )
    washington = (
        pl.read_csv(raw.cached(SOURCE, WASHINGTON).path, infer_schema_length=0)
        .with_columns(
            office=pl.when(pl.col("Race").str.ends_with("Senator"))
            .then(pl.lit("STATE SENATE"))
            .otherwise(pl.lit("STATE HOUSE")),
            district=pl.when(pl.col("Race").str.contains("Pos")).then(pl.lit("DISTRICT ")).otherwise(pl.lit(""))
            + pl.col("Race").str.extract(r"(\d+) -")
            + pl.col("Race")
            .str.extract(r"Pos\. (\d)")
            .map_elements(lambda post: f" POSITION {post}", return_dtype=pl.String)
            .fill_null(""),
            pooled=pl.col("Candidate") == "WRITE-IN",
            party_detailed=pl.col("Party")
            .str.extract(r"\((?:Prefers )?(.+?)(?: Party| Party Preference)?\)")
            .str.to_uppercase()
            .replace(PREFERENCES),
        )
        .select(
            state_po=pl.lit("WA"),
            office="office",
            district="district",
            special=pl.lit("FALSE"),
            candidate=pl.when(pl.col("pooled"))
            .then(pl.lit("WRITE-IN"))
            .otherwise(pl.col("Candidate").str.to_uppercase()),
            party_detailed=pl.when(pl.col("pooled")).then(None).otherwise(pl.col("party_detailed")),
            pooled="pooled",
            votes=pl.col("Votes").cast(pl.Int64),
            masked=pl.lit(False),
            magnitude=pl.lit(1, pl.Int32),
            state_fips=pl.lit("53"),
        )
        .with_columns(
            pl.struct("state_po", "district")
            .map_elements(
                lambda row: dict(zip(("code", "post"), _district(row["state_po"], row["district"]), strict=True)),
                return_dtype=pl.Struct({"code": pl.String, "post": pl.String}),
            )
            .alias("parsed")
        )
        .unnest("parsed")
        .select(lines.columns)
    )
    new_mexico = (
        pl.read_csv(raw.cached(SOURCE, NEW_MEXICO).path, infer_schema_length=0)
        .filter(pl.col("office").is_in(["State House", "State Senate"]))
        .group_by("office", "district", "candidate", "party")
        .agg(pl.col("votes").cast(pl.Int64, strict=False).sum(), pl.col("votes").eq("*").any().alias("masked"))
        .select(
            state_po=pl.lit("NM"),
            office=pl.col("office").str.to_uppercase(),
            district="district",
            special=pl.lit("FALSE"),
            candidate="candidate",
            party_detailed=pl.col("party").replace(NM_PARTIES),
            pooled=pl.lit(False),
            votes="votes",
            masked="masked",
            magnitude=pl.lit(1, pl.Int32),
            state_fips=pl.lit("35"),
            code="district",
            post=pl.lit(None, pl.String),
        )
        .join(
            lines.filter(pl.col("state_po") == "NM").select("office", "code").unique(),
            on=["office", "code"],
            how="anti",
        )
    )
    new_york = (
        pl.read_csv(raw.cached(SOURCE, NEW_YORK).path, infer_schema=False)
        .filter(
            pl.col("election_type") == "General",
            pl.col("election_date").str.starts_with(str(DAY)),
            pl.col("candidate_party_name").is_not_null() | (pl.col("candidate_name") == "Scattering"),
        )
        .with_columns(pooled=pl.col("candidate_name") == "Scattering")
        .group_by("district_name", "candidate_name", "candidate_party_name", "pooled")
        .agg(pl.col("votes").cast(pl.Int64).sum())
        .select(
            state_po=pl.lit("NY"),
            office=pl.lit("STATE HOUSE"),
            district=pl.col("district_name").str.zfill(3),
            special=pl.lit("FALSE"),
            candidate=pl.when("pooled").then(pl.lit("WRITE-IN")).otherwise(pl.col("candidate_name").str.to_uppercase()),
            party_detailed=pl.col("candidate_party_name").str.to_uppercase().replace({"DEMOCRATIC": "DEMOCRAT"}),
            pooled="pooled",
            votes="votes",
            masked=pl.lit(False),
            magnitude=pl.lit(1, pl.Int32),
            state_fips=pl.lit("36"),
            code=pl.col("district_name"),
            post=pl.lit(None, pl.String),
        )
        .join(
            lines.filter(pl.col("state_po") == "NY").select("office", "code").unique(),
            on=["office", "code"],
            how="anti",
        )
    )
    rhode_island = (
        pl.read_csv(raw.cached(SOURCE, RHODE_ISLAND).path, infer_schema_length=0)
        .filter(pl.col("office").is_in(["State House", "State Senate"]))
        .with_columns(pooled=pl.col("party") == "NON")
        .group_by("office", "district", "candidate", "party", "pooled")
        .agg(pl.col("votes").cast(pl.Int64).sum())
        .select(
            state_po=pl.lit("RI"),
            office=pl.col("office").str.to_uppercase(),
            district=pl.col("district").str.zfill(3),
            special=pl.lit("FALSE"),
            candidate=pl.when("pooled").then(pl.lit("WRITE-IN")).otherwise(pl.col("candidate").str.to_uppercase()),
            party_detailed=pl.when("pooled").then(None).otherwise(pl.col("party").replace(RI_PARTIES)),
            pooled="pooled",
            votes="votes",
            masked=pl.lit(False),
            magnitude=pl.lit(1, pl.Int32),
            state_fips=pl.lit("44"),
            code="district",
            post=pl.lit(None, pl.String),
        )
        .join(
            lines.filter(pl.col("state_po") == "RI").select("office", "code").unique(),
            on=["office", "code"],
            how="anti",
        )
    )
    rows = []
    page = raw.cached(SOURCE, SUFFOLK).path.read_text(errors="replace")
    for district, section in re.findall(
        r"State Senator, (\d+)\w\w Senatorial District \(Elect 1\)(.*?)(?=State Senator, \d+\w\w Senatorial District \(Elect|$)",
        page,
        re.DOTALL,
    ):
        candidate = None
        for row in re.findall(r"<tr>(.*?)</tr>", section, re.DOTALL):
            cells = [
                re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            ]
            if len(cells) == 4:
                surname, given = cells.pop(0).split(", ", 1)
                candidate = f"{given} {surname}".upper()
            if len(cells) == 3 and cells[0] != "Total" and candidate is not None:
                rows.append((district, candidate, cells[0].upper(), int(cells[1].replace(",", ""))))
    suffolk = (
        pl.DataFrame(
            rows,
            schema={"code": pl.String, "candidate": pl.String, "party_detailed": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .filter(pl.col("code").is_in(["1", "2", "3", "4"]))
        .select(
            state_po=pl.lit("NY"),
            office=pl.lit("STATE SENATE"),
            district=pl.col("code").str.zfill(3),
            special=pl.lit("FALSE"),
            candidate="candidate",
            party_detailed=pl.col("party_detailed").replace({"DEMOCRATIC": "DEMOCRAT"}),
            pooled=pl.lit(False),
            votes="votes",
            masked=pl.lit(False),
            magnitude=pl.lit(1, pl.Int32),
            state_fips=pl.lit("36"),
            code="code",
            post=pl.lit(None, pl.String),
        )
        .join(
            lines.filter(pl.col("state_po") == "NY").select("office", "code").unique(),
            on=["office", "code"],
            how="anti",
        )
    )
    if suffolk.select("code").n_unique() != 4:
        raise ValueError(f"Suffolk: {suffolk['code'].unique().to_list()} of State Senate districts 1-4")
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "154", "-l", "155", str(raw.cached(SOURCE, PIMA).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = []
    for district, section in re.findall(
        r"State Representative Dist\. (18|20)\nVote For 2\n(.*?)Total Votes Cast", text, re.DOTALL
    ):
        for name, votes in re.findall(r"^(\S.*?)\s{2,}([\d,]+)\s", section, re.MULTILINE):
            pooled = name == "Write-In Totals"
            surname, _, given = name.partition(", ")
            rows.append((district, "WRITE-IN" if pooled else f"{given} {surname}", pooled, int(votes.replace(",", ""))))
    pima = pl.DataFrame(
        rows,
        schema={"code": pl.String, "candidate": pl.String, "pooled": pl.Boolean, "votes": pl.Int64},
        orient="row",
    ).select(
        state_po=pl.lit("AZ"),
        office=pl.lit("STATE HOUSE"),
        district=pl.col("code").str.zfill(3),
        special=pl.lit("FALSE"),
        candidate="candidate",
        party_detailed=pl.col("candidate").replace_strict(AZ_PARTIES, default=None),
        pooled="pooled",
        votes="votes",
        masked=pl.lit(False),
        magnitude=pl.lit(2, pl.Int32),
        state_fips=pl.lit("04"),
        code="code",
        post=pl.lit(None, pl.String),
    )
    if set(pima.filter(~pl.col("pooled"))["candidate"]) != set(AZ_PARTIES) or pima["pooled"].sum() != 2:
        raise ValueError(f"Pima: {pima['candidate'].to_list()}")
    if lines.filter(
        pl.col("state_po") == "AZ", pl.col("office") == "STATE HOUSE", pl.col("code").is_in(["18", "20"])
    ).height:
        raise ValueError("MEDSL now holds AZ House 18 or 20")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(
        [
            lines.filter(pl.col("state_po") != "WA").with_columns(origin=pl.lit("medsl")),
            washington.with_columns(origin=pl.lit("wa")),
            new_mexico.with_columns(origin=pl.lit("openelections")),
            new_york.with_columns(origin=pl.lit("ny")),
            rhode_island.with_columns(origin=pl.lit("ri")),
            suffolk.with_columns(origin=pl.lit("suffolk")),
            pima.with_columns(origin=pl.lit("pima")),
        ]
    ).sort(
        "state_po",
        "office",
        "district",
        "special",
        "votes",
        descending=[False, False, False, False, True],
        nulls_last=True,
    ).write_parquet(PARSED / "lines.parquet")


MASKED = (
    "Some precincts in the source file hide small vote counts to protect voter privacy; "
    "these totals run slightly below the certified results."
)
RANKED = (
    "Alaska uses ranked-choice voting for these races. These are first-choice votes; if no candidate won a "
    "majority, the winner was determined in later rounds not included in this source."
)


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        codes: dict[str, set[str]] = defaultdict(set)
        for scheme, code in conn.execute(
            "select a.scheme_code, c.code from area a join area_code c on c.area_id = a.id"
            " where a.scheme_code in ('us.sldu', 'us.sldl')"
        ).fetchall():
            codes[scheme].add(code)
        past: dict[tuple[str, str], dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))
        known_names: dict[UUID, str] = {}
        for body, code, person, ballot, known in conn.execute(
            """select distinct b.value, ac.code, cd.person_id, cd.ballot_name, pn.text
               from candidacy cd join contest_option o on o.id = cd.option_id
               join name pn on pn.entity_id = cd.person_id and pn.is_preferred and pn.lang = 'en'
               join contest ct on ct.id = o.contest_id join election e on e.id = ct.election_id
               join external_id b on b.entity_id = ct.body_id and b.scheme = 'us.body'
               join area_code ac on ac.area_id = ct.district_area_id
               where e.date_from >= '2014-01-01' and cd.role = 'head'
                 and b.value ~ '^[A-Z]{2}:(senate|house|assembly|house-of-delegates|general-assembly|legislature)$'"""
        ).fetchall():
            if ballot is None:
                continue
            known_names[person] = known
            words = re.sub(r"[^A-Z ]", "", ballot.upper().replace(",", " ")).split()
            words = [word for word in words if word not in {"JR", "SR", "II", "III", "IV"}]
            if not words:
                continue
            past[(body, words[-1])][code].add(person)
            past[(body, f"{words[0]} {words[-1]}")][code].add(person)
        surnamed = {
            person
            for (person,) in conn.execute(
                """select p.id from person p join changeset c on c.id = p.changeset_id and c.pipeline = %s
                   join name n on n.entity_id = p.id and n.is_preferred and n.text !~ ' '""",
                [SOURCE],
            ).fetchall()
        }
        shared: set[UUID] = {
            person
            for (person,) in conn.execute(
                """select distinct cd.person_id from candidacy cd join changeset c on c.id = cd.changeset_id
                   where c.pipeline <> %s and cd.person_id = any(%s)""",
                [SOURCE, list(surnamed)],
            ).fetchall()
        }
        harris = [pool for pools in finer({date(2024, 11, 5)}).values() for pool in pools]
        held: dict[UUID, UUID] = dict(
            conn.execute(
                """select cd.id, cd.person_id from candidacy cd join changeset c on c.id = cd.changeset_id
                   where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        taken: set[UUID] = {
            id
            for (id,) in conn.execute(
                """with mine as (select distinct r.contest_id from result_set r join changeset h on h.id = r.changeset_id
                                 where h.pipeline = %(source)s)
                   select o.id from contest_option o join mine m on m.contest_id = o.contest_id
                   join changeset c on c.id = o.changeset_id where c.pipeline <> %(source)s
                   union all
                   select cd.id from candidacy cd join contest_option o on o.id = cd.option_id
                   join mine m on m.contest_id = o.contest_id
                   join changeset c on c.id = cd.changeset_id where c.pipeline <> %(source)s""",
                {"source": SOURCE},
            ).fetchall()
        }
        ceded: set[UUID] = {
            id
            for (id,) in conn.execute(
                """select r.id from result_set r join election e on e.id = r.election_id
                   where not r.is_default and e.date_from = %s""",
                [DAY],
            ).fetchall()
        }
        last_party: dict[UUID, UUID] = dict(
            conn.execute(
                """select distinct on (cd.person_id) cd.person_id, o.party_id
                   from candidacy cd join contest_option o on o.id = cd.option_id
                   join contest ct on ct.id = o.contest_id join election e on e.id = ct.election_id
                   where cd.role = 'head' and o.party_id is not null and ct.kind = 'body_seats'
                     and e.date_from >= '2014-01-01' and e.date_from < '2024-11-05'
                   order by cd.person_id, e.date_from desc"""
            ).fetchall()
        )
        parties_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative general elections of 2024 by district",
            polity="US",
        )
        principals = {cs.key("us.party", "democratic-party"), cs.key("us.party", "republican-party")}

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/DODOBJ",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in (RETURNS, CODEBOOK, README):
            cs.artifact(raw.cached(SOURCE, url), source)
        secretary = cs.source(WA_PUBLISHER, WA_TITLE)
        cs.add(
            "source",
            id=secretary,
            kind="official_body",
            publisher=WA_PUBLISHER,
            title=WA_TITLE,
            url="https://results.vote.wa.gov/results/20241105/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, WASHINGTON), secretary)
        community = cs.source(NM_PUBLISHER, NM_TITLE)
        cs.add(
            "source",
            id=community,
            kind="community",
            publisher=NM_PUBLISHER,
            title=NM_TITLE,
            url="https://github.com/openelections/openelections-data-nm",
            license=None,
        )
        cs.artifact(raw.cached(SOURCE, NEW_MEXICO), community)
        board = cs.source(NY_PUBLISHER, NY_TITLE)
        cs.add(
            "source",
            id=board,
            kind="official_body",
            publisher=NY_PUBLISHER,
            title=NY_TITLE,
            url="https://results.elections.ny.gov",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, NEW_YORK), board)
        rhode = cs.source(NM_PUBLISHER, RI_TITLE)
        cs.add(
            "source",
            id=rhode,
            kind="community",
            publisher=NM_PUBLISHER,
            title=RI_TITLE,
            url="https://github.com/openelections/openelections-data-ri",
            license=None,
        )
        cs.artifact(raw.cached(SOURCE, RHODE_ISLAND), rhode)
        county = cs.source(SUFFOLK_PUBLISHER, SUFFOLK_TITLE)
        cs.add(
            "source",
            id=county,
            kind="official_body",
            publisher=SUFFOLK_PUBLISHER,
            title=SUFFOLK_TITLE,
            url="https://apps2.suffolkcountyny.gov/boe/eleres/24ge/default.htm",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, SUFFOLK), county)
        pima = cs.source(PIMA_PUBLISHER, PIMA_TITLE)
        cs.add(
            "source",
            id=pima,
            kind="official_body",
            publisher=PIMA_PUBLISHER,
            title=PIMA_TITLE,
            url="https://azsos.gov/elections/results-data",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PIMA), pima)
        cs.artifact(raw.cached(SOURCE, AZ_CANVASS), pima)
        sources = {
            "medsl": source,
            "wa": secretary,
            "openelections": community,
            "ny": board,
            "ri": rhode,
            "suffolk": county,
            "pima": pima,
        }
        fptp, block = cs.key("us.electoral_system", "fptp"), cs.key("us.electoral_system", "block")
        irv = cs.key("us.electoral_system", "irv")
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )

        def title(text: str) -> str:
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
            return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            if label is None:
                return None, False
            label = EXPANDED.get(label, label)
            if "/" in label:
                found = [party(state, part.strip()) for part in CROSS_FILED.get((state, label), label).split("/")]
                return next((id for id, _ in found if id is not None), None), all(alone for _, alone in found)
            if label == "NONPARTISAN":
                return None, False
            if label in INDEPENDENT:
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

        bodies: dict[tuple[str, str], tuple[UUID, str, str, str]] = {}
        persons: dict[tuple[str, str], UUID] = {}
        values: list[tuple[str, str, str, int]] = []
        problems: list[str] = []
        contest_columns = ["state_po", "office", "district", "special"]
        for keys, group in lines.group_by(*contest_columns, maintain_order=True):
            state, office, district, special = (str(value) for value in keys)
            senate = office == "STATE SENATE"
            if state == "NE":
                chamber, body_key, scheme = "Legislature", "NE:legislature", "us.sldu"
            elif senate:
                chamber, body_key, scheme = UPPER.get(state, "Senate"), f"{state}:senate", "us.sldu"
            else:
                chamber = LOWER.get(state, "House of Representatives")
                body_key = f"{state}:{slug(chamber.removeprefix('State ').removesuffix(' of Representatives'))}"
                scheme = "us.sldl"
            if (state, office) not in bodies:
                body = cs.key("us.body", body_key)
                if body in cs.minted:
                    raise ValueError(f"no body {body_key}")
                bodies[(state, office)] = (body, f"{states[state]} {chamber}", body_key.split(":")[1], scheme)
            body, body_name, body_slug, scheme = bodies[(state, office)]
            code, post = group["code"][0], group["post"][0]
            full = f"{state}:{code}"
            if full not in codes[scheme]:
                letters = sorted(code.split("-")[0])
                number = code.split("-")[1] if "-" in code else None
                near = [
                    known
                    for known in codes[scheme]
                    if known.startswith(f"{state}:")
                    and sorted(known.split(":")[1].split("-")[0]) == letters
                    and (known.split("-")[1] if "-" in known else None) == number
                ]
                if len(near) == 1:
                    full, code = near[0], near[0].split(":")[1]
            area, unit = cs.area(scheme, full), cs.unit(scheme, full)
            if area in cs.minted:
                problems.append(f"new district {scheme} {full} ({district})")
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=full, valid_from=None)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=cs.area("us.state", str(group["state_fips"][0])),
                    valid_from=None,
                )
                shown = f"District {district.title()}" if code[:1].isdigit() else district.title()
                name(area, f"{body_name}, {shown}")
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=cs.polity(f"US-{state}"),
                    scheme_code=scheme,
                    code=full,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
                codes[scheme].add(full)
            shown = f"District {code.upper().replace('-', '')}" if code[:1].isdigit() else district.title()
            election = cs.key("us.election", f"{DAY}:{state}")
            if election in cs.minted:
                raise ValueError(f"no election {DAY}:{state}")
            special_race = special == "TRUE" and (state, code) not in REGULAR
            contest_key = (
                f"{DAY}:{state}:{body_slug}:{code}"
                + (f":{post}" if post else "")
                + (":gs" if special == "TRUE" else ":g")
            )
            contest = cs.key("us.contest", contest_key)
            magnitude = int(group["magnitude"][0])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=magnitude,
                term="remainder" if special_race else "full",
                round_number=1,
                electoral_system_id=irv if state == "AK" else fptp if magnitude == 1 else block,
                default_result_kind="counted",
            )
            where = shown + (f", {'Post' if post and post.isdigit() else 'Seat'} {post}" if post else "")
            name(contest, f"2024 {body_name} {'special election' if special_race else 'election'}, {where}")
            path = (
                f"us/{state.lower()}/{DAY}-general/{'state-' if body_slug in ('senate', 'house') else ''}{body_slug}-{code}"
                + (f"-{'post' if post.isdigit() else 'seat'}-{post.lower()}" if post else "")
                + ("-special-election" if special_race else "")
            )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
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
                source_id=sources[group["origin"][0]],
                is_default=contest_key not in RANKED_ROUNDS and result_set not in ceded,
                completeness=None if group["masked"].any() else 1,
            )
            if group["masked"].any():
                cs.add("note", entity_id=contest, kind="caveat", body_md=MASKED)
            if group["origin"][0] == "pima":
                cs.add("note", entity_id=contest, kind="methodology", body_md=PIMA_NOTE)
            if state == "AK":
                cs.add(
                    "note", entity_id=contest, kind="caveat", body_md=MEDSL if contest_key in RANKED_ROUNDS else RANKED
                )
            totals: list[tuple[int, UUID]] = []
            full_names = resolve(group.filter(~pl.col("pooled"))["candidate"].unique().to_list(), harris, [])
            for (candidate, pooled), rows in group.group_by("candidate", "pooled", maintain_order=True):
                option_key = f"{contest_key}:write-ins" if pooled else f"{contest_key}:{slug(str(candidate))}"
                root = cs.key("us.option", option_key)
                labels = rows["party_detailed"].to_list()
                resolved = [party(state, label) for label in labels]
                ballot = "Write-ins" if pooled else initials(title(str(candidate)))
                majors = {id for id, _ in resolved if id in principals}
                named = [(votes or 0, id) for (id, _), votes in zip(resolved, rows["votes"], strict=True) if id]
                principal = next(iter(majors)) if len(majors) == 1 else max(named)[1] if named else None
                if not pooled:
                    surname = _surname(str(candidate))
                    first = str(candidate).split()[0]
                    found: set[UUID] = set()
                    single = re.fullmatch(r"[A-Z'-]+", str(candidate)) is not None
                    for key in (f"{first} {surname}", surname) if single else (f"{first} {surname}",):
                        by_code = past.get((body_key, key), {})
                        found = (
                            by_code.get(full, set()) or (set().union(*by_code.values()) if key != surname else set())
                        ) - surnamed
                        if len(found) == 1:
                            break
                    head = cs.key("us.candidacy", f"{option_key}:head")
                    if single and len(found) == 1:
                        ballot = initials(known_names[next(iter(found))])
                    elif single and candidate in full_names:
                        text = full_names[str(candidate)][0]
                        ballot = initials(title(text) if text.isupper() else text)
                    person_key = (state, ballot.upper() if single else str(candidate))
                    kept = known_names.get(held.get(head), "").upper().split()[:-1]
                    if (
                        head in held
                        and held[head] not in surnamed
                        and (single or not kept or any(fellow(word, first) for word in kept))
                    ):
                        person = held[head]
                    elif len(found) == 1:
                        person = next(iter(found))
                    else:
                        person = persons.get(person_key) or cs.key(
                            f"{SOURCE}.person", f"{state}:{slug(ballot if single else str(candidate))}"
                        )
                        if person in cs.minted and person_key not in persons:
                            cs.add("person", id=person)
                            name(person, ballot)
                            if single and " " not in ballot:
                                problems.append(f"surname only: {state} {office} {district} {candidate}")
                    persons[person_key] = person
                    if head not in taken:
                        cs.add(
                            "candidacy",
                            id=head,
                            person_id=person,
                            option_id=root,
                            role="head",
                            ballot_name=ballot,
                            is_independent=all(independent for _, independent in resolved),
                        )
                        for label in labels:
                            for part in (label or "").split("/") if "/" in (label or "") else ():
                                if (nominee := party(state, part.strip())[0]) is not None:
                                    cs.add(
                                        "candidacy_nomination", candidacy_id=head, party_id=nominee, kind="nominated"
                                    )
                    if (
                        principal is None
                        and "NONPARTISAN" not in labels
                        and not all(independent for _, independent in resolved)
                    ):
                        principal = last_party.get(person)
                if root not in taken:
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="write_in" if pooled else "candidate",
                        party_id=principal,
                        ballot_label=ballot,
                    )
                for label, (party_id, _), votes in zip(labels, resolved, rows["votes"], strict=True):
                    target = root
                    if len(rows) > 1:
                        target = cs.key("us.option", f"{option_key}:{slug(label or 'no party')}")
                        if target not in taken:
                            cs.add(
                                "contest_option",
                                id=target,
                                contest_id=contest,
                                parent_option_id=root,
                                kind="candidate",
                                party_id=party_id,
                                ballot_label=f"{ballot} ({title(label or 'no party')})",
                            )
                    if votes is not None and result_set in cs.minted:
                        values.append((str(result_set), str(unit), str(target), votes))
                if not pooled:
                    totals.append((int(rows["votes"].fill_null(0).sum()), root))
            totals.sort(key=lambda total: -total[0])
            if (
                state == "AK"
                and totals
                and totals[0][0] * 2 <= int(group["votes"].fill_null(0).sum())
                and f"{body_slug}:{code}" not in LEADERS
            ):
                problems.append(f"no majority: {contest_key}")
                continue
            if len(totals) > magnitude and totals[magnitude - 1][0] == totals[magnitude][0]:
                problems.append(f"tie: {contest_key}")
                continue
            for _, root in totals[:magnitude]:
                cs.add("outcome", result_set_id=result_set, option_id=root, kind="elected", seats=1)
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
        for person in surnamed - set(persons.values()) - shared:
            cs.add(
                "entity_retirement",
                entity_id=person,
                reason="A surname-only name in MEDSL's files, resolved to the full name",
            )
        (PARSED / "problems.txt").write_text("\n".join(problems))
        return cs.write()
