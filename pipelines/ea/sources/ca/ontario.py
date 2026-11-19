import csv
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, slug
from ea.package import Draft, Package
from ea.sources.licenses import PUBLIC_RECORD
from ea.store import DATA, connect

SOURCE = "ca.ontario"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://results.elections.on.ca/api"
EXPLORER = "https://results.elections.on.ca/en/data-explorer"
PAGE: dict[str, object] = {"pageIndex": 0, "pageSize": 20000}
CANDIDATES = f"{API}/election-explorer/candidates"
DISTRICTS = f"{API}/election-explorer/electoral-districts"
COMPOSITION = f"{API}/report-groups/48/report-outputs/1092/csv"
REFERENDUM = f"{API}/report-groups/43/report-outputs/723/csv"
SHAPEFILES = "https://www.elections.on.ca/content/dam/NGW/sitecontent/2017/preo/shapefiles/"
# Elections Ontario's site refuses scripted downloads, so the shapefiles come from Wayback captures; the 2022 district
# file is byte for byte the one first loaded from the site (sha256 70fd809a...).
BOUNDARIES = (
    f"https://web.archive.org/web/20250824185242id_/{SHAPEFILES}"
    "Electoral%20District%20Shapefile%20-%202022%20General%20Election.zip"
)
LAMBERT = "+proj=lcc +lat_1=44.5 +lat_2=54.5 +lat_0=0 +lon_0=-84 +x_0=1000000 +y_0=0 +ellps=GRS80 +units=m +no_defs"
# Each general's report group: its name, its Official Return from the Records (poll by poll, with the by-elections
# held since the general before it), and its Statistical Summary by Electoral District.
GROUPS_OF: dict[int, tuple[int, str, int, int]] = {
    1990: (8, "1990 General Election", 920, 914),
    1995: (7, "1995 General Election", 875, 869),
    1999: (6, "1999 General Election", 531, 525),
    2003: (5, "2003 General Election", 520, 514),
    2007: (4, "2007 General Election", 509, 503),
    2011: (3, "2011 General Election", 498, 492),
    2014: (2, "2014 General Election", 487, 481),
    2018: (1, "2018 General Election", 931, 925),
    2022: (45, "2022 Provincial General Election", 1049, 1043),
    2025: (48, "2025 Provincial General Election", 1095, 1089),
}
REFERENDUM_POLLS = f"{API}/report-groups/43/report-outputs/722/csv"
# The polling divisions of the three generals Elections Ontario publishes shapes for, each from the Wayback capture
# nearest after its election.
DIVISIONS = {
    2018: f"https://web.archive.org/web/20180623051519id_/{SHAPEFILES}"
    "Polling%20Division%20Shapefile%20-%202018%20General%20Election.zip",
    2022: f"https://web.archive.org/web/20221206040824id_/{SHAPEFILES}"
    "Polling%20Division%20Shapefile%20-%202022%20General%20Election.zip",
    2025: f"https://web.archive.org/web/20250801165230id_/{SHAPEFILES}"
    "Polling%20Division%20Shapefile%20-%202025%20General%20Election.zip",
}
POLL_SCHEME = "ca.on_pd"
POLLS_NOTE = (
    "Elections Ontario's Official Return from the Records gives these results poll by poll. Advance polls are "
    "reported on their own, and a poll combined with another for counting reports through the poll it was combined "
    "with. The polls add up to the district totals in every contest."
)
CURRENT = "2018"
REFERENDUM_DAY = date(2007, 10, 10)
PUBLISHER = "Elections Ontario"
SHAPES_LICENSE = "Elections Ontario Open Use Data Product Licence"
SHAPES_LICENSE_URL = (
    "https://www.elections.on.ca/en/voting-in-ontario/electoral-district-shapefiles/"
    "open-use-data-product-licence-agreement.html"
)
GROUPS = {
    "progressive-conservative": (
        "Progressive Conservative Party of Ontario",
        "Conservative",
        "Progressive Conservative",
        "Progressive-Conservative",
        "Progressive Conservative Party",
    ),
    "liberal": ("Ontario Liberal Party", "Liberal", "Liberal Party"),
    "new-democratic": (
        "New Democratic Party of Ontario",
        "New Democratic",
        "New Democratic Party",
        "New Democratic Part of Ontario",
    ),
    "ccf": ("Co-operative Commonwealth Federation (Ontario Section)", "Co-Operative Commonwealth", "C.C.F"),
    "green": ("Green Party of Ontario", "The Green Party of Ontario", "Green Party", "Green"),
    "communist": (
        "Communist Party of Canada (Ontario)",
        "Communist",
        "Communist Party",
        "Communist Party of Ontario",
    ),
    "freedom": (
        "Freedom Party of Ontario",
        "Unparty",
        "The Freedom Part of Ontario (Formerly Unpart Party*)",
        "Freedom Party of Ontario (Formerly Unparty Party*)",
        "Freedom Party",
        "Freedom",
    ),
    "family-coalition": ("Family Coalition Party of Ontario", "Family Coalition Party"),
    "libertarian": ("Ontario Libertarian Party", "Libertarian", "Libertarian Party"),
    "confederation-of-regions": (
        "Ontario Provincial Confederation of Regions Party",
        "Confederation of Regions Party",
    ),
    "none-of-the-above": ("None of the Above Direct Democracy Party", "None of the Above Party of Ontario"),
    "peoples-political": ("The People's Political Party", "The Peoples Political Party"),
    "special-needs": (
        "Party for People with Special Needs",
        "Party For People with Special Needs",
    ),
    "vegan": ("Vegan Environmental Party", "Go Vegan"),
    "northern-ontario-heritage": ("Northern Ontario Heritage Party", "Northern Ontario Heritage"),
    "liberal-labour": ("Liberal-Labour", "Liberal Labour"),
}
RENAMES = {"Conservative", "Unparty", "None of the Above Party of Ontario", "Go Vegan"}
CANON = {label: key for key, labels in GROUPS.items() for label in labels}
SHARED = {"United Farmers": "United Farmers of Ontario"}
UNAFFILIATED = {"Independent", "No Affiliation"}
UNKNOWN = "----"
NUANCE = re.compile(r"Independent|-Ind\.|Unendorsed")
COLORS = {
    "progressive-conservative": "#1a4782",
    "liberal": "#d71920",
    "new-democratic": "#f58220",
    "green": "#3d9b35",
    "ccf": "#f37021",
}
CORRECTED = {
    ("1914-06-29", "St. Catharines"): (
        {"Elisha Jessop"},
        (
            "Elections Ontario's file marks both candidates as elected in this single-member district. Elisha Jessop "
            "led James Wiley 2,863 to 1,334, and the composition table counts no Labour member in 1914, so only Jessop "
            "is loaded as elected."
        ),
    ),
    ("1914-06-29", "Sault Ste. Marie"): (
        {"William Hearst"},
        (
            "Elections Ontario's file marks Francis Crawford (Liberal, 1,431 votes) as elected over William Hearst "
            "(Conservative, 2,231 votes). Its own composition table counts the seat as Conservative, and Hearst sat for "
            "Sault Ste. Marie as premier, so Hearst is loaded as elected."
        ),
    ),
    ("1926-12-01", "Toronto Brockton"): (
        {"Frederick Mc Brien"},
        (
            "Elections Ontario's file marks Robert Stanley (Liberal, 5,435 votes) as elected over Frederick McBrien "
            "(Conservative, 11,245 votes). Its own composition table counts the seat as Conservative, so McBrien is "
            "loaded as elected."
        ),
    ),
    ("1993-04-01", "St. George-St. David"): (
        {"Tim Murphy"},
        (
            "Elections Ontario's file marks George Lamony (New Democratic Party, 1,451 votes) as elected alongside "
            "Tim Murphy (Liberal, 8,750 votes) with a plurality of -5,067. The by-election filled one seat, so only "
            "Murphy is loaded as elected."
        ),
    ),
}
KNOWN_DIFFERENCES = {1875, 1905, 1926}
COMPOSITION_NOTE = (
    "Elections Ontario's composition table for this election differs from the winners in its district results: {diffs}."
)
TRAILING = (
    "Elections Ontario's file declares {winners} elected with {their} votes, against {votes} for {loser}. "
    "The declared winners and the printed votes are both loaded as published."
)
LUMPED = (
    "Elections Ontario's results list the candidates for both of this district's seats in one contest with two "
    "winners, so they are loaded as one two-seat contest."
)
REFERENDUM_NOTE = (
    "The Electoral System Referendum Act, 2007 required mixed member proportional representation to win at least "
    "60 percent of valid votes province-wide and more than 50 percent in at least 64 of the 107 electoral "
    "districts to be binding."
)
PERSONS_NOTE = (
    "Elections Ontario's historical results carry no candidate identifiers, so each candidacy has its own person "
    "until candidates are matched across elections."
)


def listed(items: list[str]) -> str:
    return (
        items[0]
        if len(items) == 1
        else " and ".join(items)
        if len(items) == 2
        else f"{', '.join(items[:-1])}, and {items[-1]}"
    )


def english(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def fetch() -> None:
    raw.fetch(SOURCE, CANDIDATES, suffix=".json", body=PAGE)
    raw.fetch(SOURCE, DISTRICTS, suffix=".json", body=PAGE)
    for url in (COMPOSITION, REFERENDUM, REFERENDUM_POLLS):
        raw.fetch(SOURCE, url, suffix=".csv")
    for group, _, official, summary in GROUPS_OF.values():
        for output in (official, summary):
            raw.fetch(SOURCE, report(group, output), suffix=".csv")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")
    for url in DIVISIONS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def report(group: int, output: int) -> str:
    return f"{API}/report-groups/{group}/report-outputs/{output}/csv"


def party_key(label: str) -> str | None:
    if label in UNAFFILIATED or label == UNKNOWN:
        return None
    return CANON.get(label, slug(label))


def rows(url: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))))


def parse() -> None:
    candidates = json.loads(raw.cached(SOURCE, CANDIDATES, PAGE).path.read_text())
    districts = json.loads(raw.cached(SOURCE, DISTRICTS, PAGE).path.read_text())
    for data in (candidates, districts):
        if data["totalRecords"] != len(data["results"]):
            raise ValueError("the explorer returned more than one page")

    def key(r: dict) -> tuple[str, str, int]:
        return r["pollingDate"][:10], r["contestType"], r["electoralDistrictId"]

    def map_of(r: dict) -> str:
        found = re.fullmatch(r"Dis\. \d+ \((\d{4}) - (\d{4})?\)", r["electoralDistrictSetName"])
        if found is None:
            raise ValueError(f"district set {r['electoralDistrictSetName']}")
        return found[1]

    by_district: dict[tuple[str, str, int], list[dict]] = {}
    for c in candidates["results"]:
        by_district.setdefault(key(c), []).append(c)
    contests = []
    people = []
    for d in districts["results"]:
        day, kind, _ = key(d)
        number, _, label = d["electoralDistrictName"].partition(" - ")
        listed_candidates = by_district.pop(key(d))
        votes = [c["validBallotCount"] for c in listed_candidates]
        if sum(votes) != d["validBallotCount"]:
            raise ValueError(f"{day} {label}: candidates do not sum to valid ballots")
        elected = [
            c["candidateName"] in CORRECTED[day, label][0] if (day, label) in CORRECTED else c["isCandidateWinner"]
            for c in listed_candidates
        ]
        seats = sum(elected)
        tacit = len(listed_candidates) == seats
        if tacit and any(votes):
            raise ValueError(f"{day} {label}: acclaimed candidates with votes")
        trailing = None
        if not tacit:
            ahead = max(
                (c for c, e in zip(listed_candidates, elected, strict=True) if not e),
                key=lambda c: c["validBallotCount"],
            )
            behind = [
                c
                for c, e in zip(listed_candidates, elected, strict=True)
                if e and c["validBallotCount"] <= ahead["validBallotCount"]
            ]
            if behind:
                trailing = TRAILING.format(
                    winners=listed([c["candidateName"] for c in behind]),
                    loser=ahead["candidateName"],
                    votes=f"{ahead['validBallotCount']:,}",
                    their=listed([f"{c['validBallotCount']:,}" for c in behind]),
                )
        scheme_map = map_of(d)
        contests.append(
            {
                "date": date.fromisoformat(day),
                "general": kind == "GE",
                "map": scheme_map,
                "number": int(number),
                "area": f"{scheme_map}:{int(number)}",
                "name": label.strip(),
                "registered": d["registeredVoterCount"],
                "rejected": d["rejectedBallotCount"],
                "unmarked": d["unmarkedBallotCount"],
                "declined": d["declinedBallotCount"],
                "valid": d["validBallotCount"],
                "seats": seats,
                "tacit": tacit,
                "trailing": trailing,
            }
        )
        for index, (c, won) in enumerate(zip(listed_candidates, elected, strict=True)):
            people.append(
                {
                    "date": date.fromisoformat(day),
                    "general": kind == "GE",
                    "number": int(number),
                    "index": index,
                    "name": re.sub(r"\s+", " ", c["candidateName"]).strip(),
                    "label": c["partyName"].strip(),
                    "party": party_key(c["partyName"].strip()),
                    "votes": c["validBallotCount"],
                    "elected": won,
                }
            )
    if by_district:
        raise ValueError(f"{len(by_district)} candidate groups have no district")
    contests_frame = pl.DataFrame(contests, infer_schema_length=None)
    people_frame = pl.DataFrame(people)

    composition = rows(COMPOSITION)
    held = Counter()
    for r in composition:
        held[int(r["Year"]), party_key(r["PoliticalInterestEnglish"].strip()) or "independent"] += int(r["TotalSeats"])
    won = Counter()
    for r in people_frame.filter(pl.col("general") & pl.col("elected")).iter_rows(named=True):
        won[r["date"].year, r["party"] or "independent"] += 1
    sizes = Counter()
    for (year, _), seats in held.items():
        sizes[year] += seats
    counted = Counter()
    for (year, _), seats in won.items():
        counted[year] += seats
    if sizes != counted:
        raise ValueError(f"seats differ from the composition table: {sorted((sizes - counted) + (counted - sizes))}")
    labels = {
        (r["date"].year, r["party"]): r["label"]
        for r in people_frame.filter(pl.col("general") & pl.col("party").is_not_null()).iter_rows(named=True)
    }
    labels.update(
        {
            (int(r["Year"]), party_key(r["PoliticalInterestEnglish"].strip()) or "independent"): r[
                "PoliticalInterestEnglish"
            ].strip()
            for r in composition
        }
    )
    differences: dict[int, list[str]] = {}
    for year, party in sorted(k for k in held.keys() | won.keys() if held[k] != won[k]):
        differences.setdefault(year, []).append(
            f"{labels[year, party]} {held[year, party]} in the table, {won[year, party]} in the district results"
        )
    if set(differences) != KNOWN_DIFFERENCES:
        raise ValueError(f"seats by party differ from the composition table: {differences}")

    referendum = rows(REFERENDUM)
    current = contests_frame.filter(pl.col("date") == date(2007, 10, 10))
    names07 = {r["number"]: r["name"] for r in current.iter_rows(named=True)}
    ballots = []
    for r in referendum:
        number = int(r["ElectoralDistrictNumber"])
        if slug(names07[number]) != slug(r["ElectoralDistrictNameEnglish"]):
            raise ValueError(f"referendum district {number}: {r['ElectoralDistrictNameEnglish']}")
        yes, no = int(r["YesBallotCount"]), int(r["NoBallotCount"])
        rejected, unmarked, declined = int(r["RejectedBallots"]), int(r["UnmarkedBallots"]), int(r["DeclinedBallots"])
        if yes + no + rejected + unmarked + declined != int(r["VoterTurnoutCount"]):
            raise ValueError(f"referendum district {number}: ballots do not add up")
        ballots.append(
            {
                "number": number,
                "first": yes,
                "second": no,
                "rejected": rejected,
                "unmarked": unmarked,
                "declined": declined,
                "registered": int(r["RegisteredVoterCount"]),
            }
        )
    question = referendum[0]

    with zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path) as z:
        parts = {
            name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
            for name in z.namelist()
            if name.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
        }
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")
    records = [
        (str(record["ED_ID"]), record["ENGLISH_NA"], record["FRENCH_NAM"], json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.records(), reader.shapes(), strict=True)
        if record is not None and shape is not None
    ]
    with connect() as conn:
        shapes = pl.DataFrame(
            conn.execute(
                """with ed as (
                     select code, name, french, st_multi(st_collectionextract(st_makevalid(st_transform(
                              st_geomfromgeojson(geojson), %s, 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, french, geojson))
                   select code, name, french, encode(st_asewkb(geom), 'hex'),
                          encode(st_asewkb(st_pointonsurface(geom)), 'hex'), st_x(st_centroid(geom)), st_y(st_centroid(geom))
                   from ed order by code::int""",
                [LAMBERT, *[list(column) for column in zip(*records, strict=True)]],
            ).fetchall(),
            schema=["code", "name", "french", "geom", "label_point", "x", "y"],
            orient="row",
        )
    if not shapes.filter(
        (pl.col("x") < -95.2) | (pl.col("x") > -74.3) | (pl.col("y") < 41.6) | (pl.col("y") > 57)
    ).is_empty():
        raise ValueError("district centroids fall outside Ontario")
    latest = {r["number"]: r["name"] for r in contests_frame.filter(pl.col("map") == CURRENT).iter_rows(named=True)}
    if shapes.height != len(latest):
        raise ValueError(f"{shapes.height} shapes for {len(latest)} districts")
    for code, label in shapes.select("code", "name").iter_rows():
        if slug(latest[int(code)]) != slug(label):
            raise ValueError(f"district {code}: {label} in the boundaries, {latest[int(code)]} in the results")

    PARSED.mkdir(parents=True, exist_ok=True)
    contests_frame.write_parquet(PARSED / "contests.parquet")
    people_frame.write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(ballots).write_parquet(PARSED / "referendum.parquet")
    pl.DataFrame([{"year": year, "seats": seats} for year, seats in sorted(sizes.items())]).write_parquet(
        PARSED / "sizes.parquet"
    )
    (PARSED / "differences.json").write_text(json.dumps({str(year): diffs for year, diffs in differences.items()}))
    (PARSED / "question.json").write_text(json.dumps(question, ensure_ascii=False))
    shapes.drop("x", "y").write_parquet(PARSED / "shapes.parquet")
    summaries(contests_frame)
    candidate_polls, referendum_polls = polls(contests_frame, people_frame, pl.DataFrame(ballots))
    candidate_polls.write_parquet(PARSED / "polls.parquet")
    referendum_polls.write_parquet(PARSED / "referendum_polls.parquet")
    divisions().write_parquet(PARSED / "divisions.parquet")
    print(
        f"{contests_frame.select('date', 'general').n_unique()} elections, {contests_frame.height} contests, "
        f"{people_frame.height} candidates, {people_frame['party'].n_unique()} parties, {len(ballots)} referendum "
        f"districts, {shapes.height} shapes"
    )


def tokens(name: str) -> tuple[str, ...]:
    """A candidate's name as its sorted words, without accents or case, so 'SNOW, MARK' meets 'Mark Snow'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return tuple(sorted(re.findall(r"[a-z]+", text)))


def poll_code(column: pl.Expr) -> pl.Expr:
    """A poll number with its digits padded to three, as most returns print them ('8' and '008' are one poll, and
    'ADV 1' is 'ADV001')."""
    text = column.str.replace_all(" ", "")
    parts = text.str.extract_groups(r"^(ADV)?(\d+)(.*)$")
    return (
        pl.when(parts.struct.field("2").is_not_null())
        .then(
            parts.struct.field("1").fill_null("")
            + parts.struct.field("2").str.zfill(3)
            + parts.struct.field("3").fill_null("")
        )
        .otherwise(text)
    )


def returns(url: str) -> pl.DataFrame:
    """An Official Return from the Records, one row per poll and candidate (per poll for the referendum)."""
    frame = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0, encoding="utf8")
    for column, allowed in (("PollCategory", {"Standard", "Advance"}), ("IsNoPollTaken", {"0", "1"})):
        odd = set(frame[column].unique().to_list()) - allowed
        if odd:
            raise ValueError(f"{url}: {column} holds {sorted(odd)}")
    if set(frame["IsCombinedPoll"].unique().to_list()) - {"0", "1"}:
        raise ValueError(f"{url}: IsCombinedPoll is not 0 or 1")
    referendum = "YesBallotCount" in frame.columns
    number = pl.col("ElectoralDistrictNameEnglish").str.extract(r"^(\d+) ").cast(pl.Int32)
    out = frame.select(
        event=pl.col("EventNameEnglish").str.strip_chars(),
        number=number,
        poll=poll_code(pl.col("PollNumber")),
        # The referendum files a few advance polls under the standard category; their numbers say what they are.
        advance=(pl.col("PollCategory") == "Advance") | pl.col("PollNumber").str.starts_with("ADV"),
        no_poll=pl.col("IsNoPollTaken") == "1",
        combined_with=pl.when(pl.col("IsCombinedPoll") == "1").then(poll_code(pl.col("CombinedWith"))),
        electors=pl.col("NamesOnListOfElectors").cast(pl.Int64),
        rejected=pl.col("BallotsFromBoxesRejectedAsMarkings").cast(pl.Int64),
        unmarked=pl.col("BallotsFromBoxesUnmarkedByVoters").cast(pl.Int64),
        declined=pl.col("BallotsDeclinedByVoters").cast(pl.Int64),
        **(
            {"first": pl.col("YesBallotCount").cast(pl.Int64), "second": pl.col("NoBallotCount").cast(pl.Int64)}
            if referendum
            else {
                "candidate": pl.col("NameOfCandidates").str.strip_chars(),
                "votes": pl.col("AcceptedBallotCount").cast(pl.Int64),
            }
        ),
    )
    if out["number"].null_count():
        raise ValueError(f"{url}: a district name without its number")
    combined = out.filter(pl.col("combined_with").is_not_null())
    counts = ["electors", "rejected", "unmarked", "declined", *(["first", "second"] if referendum else ["votes"])]
    if not combined.filter(pl.any_horizontal(pl.col(c) != 0 for c in counts)).is_empty():
        raise ValueError(f"{url}: a combined poll holds counts of its own")
    hosts = combined.select("event", "number", poll="combined_with").unique()
    if not hosts.join(
        out.select("event", "number", "poll").unique(), on=["event", "number", "poll"], how="anti"
    ).is_empty():
        raise ValueError(f"{url}: a poll is combined with a poll the return does not list")
    key = ["event", "number", "poll", *([] if referendum else ["candidate"])]
    if out.select(key).is_duplicated().any():
        raise ValueError(f"{url}: a poll is listed twice")
    return out


def polls(contests: pl.DataFrame, people: pl.DataFrame, ballots: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Every poll of the official returns, matched to its contest and candidates and checked against the district
    totals already parsed: the polls of each contest must add up to its votes, its rejected, unmarked, and declined
    ballots, and its electors."""
    keyed: dict[tuple[int, bool, int], list[dict]] = {}
    for row in contests.iter_rows(named=True):
        keyed.setdefault((row["date"].year, row["general"], row["number"]), []).append(row)
    frames = []
    for group, _, official, _ in GROUPS_OF.values():
        for (event, number), rows in returns(report(group, official)).group_by("event", "number", maintain_order=True):
            general = "General" in str(event)
            found = re.search(r"\d{4}", str(event))
            if found is None:
                raise ValueError(f"{event}: no year")
            matches = keyed.get((int(found[0]), general, int(str(number))), [])
            if len(matches) != 1:
                raise ValueError(f"{event} district {number}: {len(matches)} contests match")
            contest = matches[0]
            day = contest["date"]
            listed = people.filter(
                (pl.col("date") == day) & (pl.col("general") == general) & (pl.col("number") == number)
            )
            totals = dict(rows.group_by("candidate").agg(pl.col("votes").sum()).iter_rows())
            index: dict[str, int] = {}
            by_tokens = {tokens(n): i for n, i in listed.select("name", "index").iter_rows()}
            for name in totals:
                if tokens(name) in by_tokens:
                    index[name] = by_tokens[tokens(name)]
            left = {i: v for i, v in listed.select("index", "votes").iter_rows() if i not in index.values()}
            for name in [n for n in totals if n not in index]:
                same = [i for i, v in left.items() if v == totals[name]]
                if len(same) != 1 or list(totals.values()).count(totals[name]) != 1:
                    raise ValueError(f"{day} district {number}: {name} matches no candidate")
                index[name] = same[0]
                del left[same[0]]
            if len(set(index.values())) != len(index) or len(index) != listed.height:
                raise ValueError(f"{day} district {number}: the return's candidates differ from the district's")
            votes = dict(listed.select("index", "votes").iter_rows())
            problems = [
                f"{name} {totals[name]:,} against {votes[i]:,}" for name, i in index.items() if votes[i] != totals[name]
            ]
            by_poll = rows.unique("poll")
            for column, printed in (
                ("rejected", contest["rejected"]),
                ("unmarked", contest["unmarked"]),
                ("declined", contest["declined"]),
                ("electors", contest["registered"]),
            ):
                if by_poll[column].sum() != printed:
                    problems.append(f"{column} {by_poll[column].sum():,} against {printed:,}")
            if problems:
                raise ValueError(f"{day} district {number}: the polls differ from the district: {problems}")
            frames.append(
                rows.with_columns(
                    date=pl.lit(day),
                    general=pl.lit(general),
                    group=pl.lit(group),
                    index=pl.col("candidate").replace_strict(index, return_dtype=pl.Int64),
                ).drop("event", "candidate")
            )
    candidate_polls = pl.concat(frames)
    covered = candidate_polls.select("date", "general", "number").unique()
    missing = (
        contests.filter(pl.col("date") >= date(1990, 9, 6))
        .select("date", "general", "number")
        .join(covered, on=["date", "general", "number"], how="anti")
    )
    if not missing.is_empty():
        raise ValueError(f"{missing.height} contests since 1990 have no polls: {missing.head(5).to_dicts()}")

    referendum = returns(REFERENDUM_POLLS)
    if set(referendum["event"].unique().to_list()) != {"2007 Referendum"}:
        raise ValueError("the referendum return holds another event")
    sums = (
        referendum.group_by("number")
        .agg(
            pl.col("first").sum(),
            pl.col("second").sum(),
            (pl.col("rejected") + pl.col("unmarked") + pl.col("declined")).sum().alias("rejected"),
            pl.col("electors").sum().alias("registered"),
        )
        .sort("number")
    )
    printed = ballots.select(
        "number",
        "first",
        "second",
        rejected=pl.col("rejected") + pl.col("unmarked") + pl.col("declined"),
        registered="registered",
    ).sort("number")
    if not sums.cast(printed.schema).equals(printed):
        raise ValueError("the referendum polls differ from the referendum's district results")
    return candidate_polls, referendum.drop("event").with_columns(date=pl.lit(REFERENDUM_DAY))


def summaries(contests: pl.DataFrame) -> None:
    """Check each general's Statistical Summary by Electoral District against the district rows already parsed."""
    problems = []
    for group, _, _, summary in GROUPS_OF.values():
        for r in rows(report(group, summary)):
            day = datetime.strptime(r["PollingDate"], "%Y-%b-%d").date()
            found = contests.filter((pl.col("date") == day) & (pl.col("number") == int(r["ElectoralDistrictNumber"])))
            if found.height != 1:
                problems.append(f"{day} {r['ElectoralDistrictNumber']}: {found.height} contests")
                continue
            contest = found.row(0, named=True)
            for column, value in (
                ("ValidBallotsFromBoxes", contest["valid"]),
                ("RejectedBallotsFromBoxes", contest["rejected"]),
                ("UnmarkedBallotsFromBoxes", contest["unmarked"]),
                ("DeclinedBallots", contest["declined"]),
                ("TotalNumberOfNames", contest["registered"]),
            ):
                if int(r[column]) != value:
                    problems.append(f"{day} {contest['name']}: {column} {r[column]} against {value}")
    if problems:
        raise ValueError(f"the statistical summaries differ from the district results: {problems[:10]}")


def whole(value: object) -> int:
    """A shapefile number, which the 2018 file stores as a float."""
    number = float(str(value))
    if not number.is_integer():
        raise ValueError(f"{value} is not a whole number")
    return int(number)


def divisions() -> pl.DataFrame:
    """The polling divisions of the 2018, 2022, and 2025 generals, in WGS 84, one outline per district and number."""
    frames = []
    for year, url in DIVISIONS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
            parts = {
                name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
                for name in z.namelist()
                if name.rsplit(".", 1)[-1].lower() in ("shp", "shx", "dbf")
            }
        reader = shapefile.Reader(
            shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8", encodingErrors="replace"
        )
        records = [
            (whole(record["ED_ID"]), whole(record["PD_NUMBER"]), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and shape.points
        ]
        with connect() as conn:
            found = conn.execute(
                """with pd as (
                     select ed, pd, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(st_transform(
                              st_geomfromgeojson(geojson), %s, 4326)))), 3)) as geom
                     from unnest(%s::int[], %s::int[], %s::text[]) as f(ed, pd, geojson) group by ed, pd)
                   select ed, pd, upper(encode(st_asewkb(geom), 'hex')),
                          upper(encode(st_asewkb(st_pointonsurface(geom)), 'hex')), st_x(st_centroid(geom)),
                          st_y(st_centroid(geom)), st_isempty(geom)
                   from pd order by ed, pd""",
                [LAMBERT, *[list(column) for column in zip(*records, strict=True)]],
            ).fetchall()
        frame = pl.DataFrame(
            found, schema=["ed", "pd", "geom", "label_point", "x", "y", "empty"], orient="row"
        ).with_columns(year=pl.lit(year))
        if not frame.filter(
            pl.col("empty") | (pl.col("x") < -95.2) | (pl.col("x") > -74.3) | (pl.col("y") < 41.6) | (pl.col("y") > 57)
        ).is_empty():
            raise ValueError(f"{year}: polling divisions fall outside Ontario")
        if set(frame["ed"].unique().to_list()) != set(range(1, 125)):
            raise ValueError(f"{year}: the polling divisions do not cover the 124 districts")
        frames.append(frame.drop("x", "y", "empty"))
    return pl.concat(frames)


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    ballots = pl.read_parquet(PARSED / "referendum.parquet")
    sizes = pl.read_parquet(PARSED / "sizes.parquet")
    question = json.loads((PARSED / "question.json").read_text())
    differences = json.loads((PARSED / "differences.json").read_text())
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    candidate_polls = pl.read_parquet(PARSED / "polls.parquet")
    referendum_polls = pl.read_parquet(PARSED / "referendum_polls.parquet")
    polled = {
        f"{day}:{'general' if general else 'by-election'}:{number}"
        for day, general, number in candidate_polls.select("date", "general", "number").unique().iter_rows()
    }
    generals = sorted(contests.filter(pl.col("general"))["date"].unique().to_list())
    ids = Ids()
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ontario's general elections from 1867 to 2025, its by-elections from 1992 to 2024, and the 2007 "
                "electoral system referendum, by electoral district, from Elections Ontario"
            ),
            polity="CA-ON",
        )
        cs.accept(
            "result_set.empty",
            "the contests whose candidates were acclaimed have tacit result sets with no votes, as Elections Ontario "
            "prints none for them",
        )

        def name(
            entity: UUID,
            text: str,
            kind: str = "official",
            lang: str = "en",
            preferred: bool | None = None,
            valid: tuple[date, date] | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=(kind == "official") if preferred is None else preferred,
                **({"valid_from": valid[0], "valid_to": valid[1]} if valid else {}),
            )

        def note(entity: UUID, body: str) -> None:
            cs.add("note", entity_id=entity, kind="context", body_md=body)

        def values(frame: pl.DataFrame) -> None:
            cs.frame(
                "result_value",
                frame.select(
                    "result_set_id",
                    "unit_id",
                    "option_id",
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.col("measure").cast(pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("value").cast(pl.Int64),
                ),
            )

        ontario = cs.polity("CA-ON")
        province = cs.area("ca.province", "35")
        if {ontario, province} & cs.minted:
            raise ValueError("run ca.elections first: Ontario and its province area must exist")
        cs.add("scheme", code="ca.on", polity_id=ontario, kind="electoral_district", rank=2, noun="electoral district")
        cs.add("scheme", code=POLL_SCHEME, polity_id=ontario, kind="precinct", rank=3, noun="polling division")
        ids.polity = ontario

        results = cs.source(PUBLISHER, "Election Results Explorer: candidates and electoral districts, 1867 to 2025")
        cs.add(
            "source",
            id=results,
            kind="official_body",
            publisher=PUBLISHER,
            title="Election Results Explorer: candidates and electoral districts, 1867 to 2025",
            url=EXPLORER,
            license=PUBLIC_RECORD,
        )
        cs.artifact(raw.cached(SOURCE, CANDIDATES, PAGE), results)
        cs.artifact(raw.cached(SOURCE, DISTRICTS, PAGE), results)
        composition = cs.source(PUBLISHER, "Composition of the Legislature following General Elections")
        cs.add(
            "source",
            id=composition,
            kind="official_body",
            publisher=PUBLISHER,
            title="Composition of the Legislature following General Elections",
            url="https://results.elections.on.ca/en/reports",
            license=PUBLIC_RECORD,
        )
        cs.artifact(raw.cached(SOURCE, COMPOSITION), composition)
        referendum_source = cs.source(PUBLISHER, "2007 Referendum: Statistical Results by Electoral District")
        cs.add(
            "source",
            id=referendum_source,
            kind="official_body",
            publisher=PUBLISHER,
            title="2007 Referendum: Statistical Results by Electoral District",
            url="https://results.elections.on.ca/en/reports",
            license=PUBLIC_RECORD,
        )
        cs.artifact(raw.cached(SOURCE, REFERENDUM), referendum_source)
        boundaries = cs.source(PUBLISHER, "Electoral District Shapefile - 2022 General Election")
        cs.add(
            "source",
            id=boundaries,
            kind="official_body",
            publisher=PUBLISHER,
            title="Electoral District Shapefile - 2022 General Election",
            url="https://www.elections.on.ca/en/voting-in-ontario/electoral-district-shapefiles.html",
            license=SHAPES_LICENSE,
            license_url=SHAPES_LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, BOUNDARIES), boundaries)
        for year, (group, label, official, _) in GROUPS_OF.items():
            title = f"{label}: Official Return from the Records"
            ids.returns[group] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=ids.returns[group],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://results.elections.on.ca/en/reports",
                license=PUBLIC_RECORD,
            )
            cs.artifact(raw.cached(SOURCE, report(group, official)), ids.returns[group])
        title = "2007 Referendum: Official Return from the Records"
        ids.returns[43] = cs.source(PUBLISHER, title)
        cs.add(
            "source",
            id=ids.returns[43],
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url="https://results.elections.on.ca/en/reports",
            license=PUBLIC_RECORD,
        )
        cs.artifact(raw.cached(SOURCE, REFERENDUM_POLLS), ids.returns[43])
        for year, url in DIVISIONS.items():
            title = f"Polling Division Shapefile - {year} General Election"
            ids.divisions[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=ids.divisions[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://www.elections.on.ca/en/voting-in-ontario/electoral-district-shapefiles.html",
                license=SHAPES_LICENSE,
                license_url=SHAPES_LICENSE_URL,
            )
            cs.artifact(raw.cached(SOURCE, url), ids.divisions[year])

        assembly = cs.key(f"{SOURCE}.body", "legislative_assembly")
        cs.add("body", id=assembly, polity_id=ontario, kind="unicameral")
        name(assembly, "Legislative Assembly of Ontario")
        name(assembly, "Assemblée législative de l'Ontario", "native", "fr", False)
        by_year = dict(sizes.iter_rows())
        changes = [
            day for i, day in enumerate(generals) if i == 0 or by_year[day.year] != by_year[generals[i - 1].year]
        ]
        for start, end in zip(changes, [*changes[1:], None], strict=True):
            cs.add("body_size", body_id=assembly, seats=by_year[start.year], valid_from=start, valid_to=end)
        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=ontario, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")
        block = cs.key(f"{SOURCE}.electoral_system", "block")
        cs.add("electoral_system", id=block, polity_id=ontario, rules={"formula": "block"}, rules_version=1)
        name(block, "Plurality block voting")
        plebiscite = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=plebiscite, polity_id=ontario, rules={"formula": "referendum"}, rules_version=1)
        name(plebiscite, "Ontario referendum")

        maps = sorted(contests["map"].unique().to_list())
        starts = dict(contests.group_by("map").agg(pl.col("date").min()).iter_rows())
        ends = dict(zip(maps, [*(starts[m] for m in maps[1:]), None], strict=True))
        areas: dict[str, UUID] = {}
        for code, labels in (
            contests.sort("date")
            .group_by("area", maintain_order=True)
            .agg(pl.col("name").unique(maintain_order=True))
            .iter_rows()
        ):
            scheme_map = code.split(":")[0]
            area = areas[code] = cs.area("ca.on", code, starts[scheme_map])
            cs.add("area", id=area, scheme_code="ca.on")
            cs.add("area_code", area_id=area, code=code, valid_from=starts[scheme_map], valid_to=ends[scheme_map])
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, labels[-1])
            for label in labels[:-1]:
                name(area, label, "historical", "en", False)
        for code, _, french, geom, label_point in shapes.iter_rows():
            area = areas[f"{CURRENT}:{code}"]
            name(area, french, "official", "fr", False)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=starts[CURRENT],
                valid_to=None,
                geom=geom,
                label_point=label_point,
                quality="official",
                source_id=boundaries,
            )
        cs.add(
            "boundary_set",
            scheme_code="ca.on",
            vintage=CURRENT,
            valid_from=starts[CURRENT],
            valid_to=None,
            as_of=starts[CURRENT],
        )

        party_ids: dict[str, UUID] = {}
        nuances: set[str] = set()
        for (label,), _ in candidates.sort("date").group_by("label", maintain_order=True):
            key = party_key(label)
            if key is None:
                continue
            if label in SHARED:
                party_ids[key] = cs.key("ca.lop.party", SHARED[label])
                if party_ids[key] in cs.minted:
                    raise ValueError(f"{SHARED[label]} should already exist from ca.lop")
                continue
            if key in party_ids:
                continue
            party_ids[key] = cs.key(f"{SOURCE}.party", key)
            nuance = bool(NUANCE.search(label))
            if nuance:
                nuances.add(key)
            cs.add("party", id=party_ids[key], kind="nuance" if nuance else "party", polity_id=ontario)
            labels = GROUPS.get(key, (label,))
            name(party_ids[key], labels[0])
            for old in labels[1:]:
                seen = candidates.filter(pl.col("label") == old)["date"].to_list()
                if old in RENAMES and seen:
                    name(party_ids[key], old, "historical", "en", False, (min(seen), max(seen) + timedelta(days=1)))
            if key in COLORS:
                cs.add("party_color", party_id=party_ids[key], color=COLORS[key])

        for (day, general), day_contests in contests.sort("date", "number").group_by(
            "date", "general", maintain_order=True
        ):
            kind = "general" if general else "by-election"
            labels = day_contests["name"].to_list()
            election = cs.key(f"{SOURCE}.election", f"{day}:{kind}")
            cs.add(
                "election",
                id=election,
                polity_id=ontario,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            if general:
                name(election, f"{day.year} Ontario general election")
                name(election, f"Élections générales ontariennes de {day.year}", "native", "fr", False)
            elif len(labels) == 1:
                name(election, f"{labels[0]} provincial by-election, {english(day)}")
            else:
                name(election, f"{listed(labels)} provincial by-elections, {english(day)}")
            cs.add("slug", path=f"ca/on/{day}-{kind}", entity_id=election)
            note(election, PERSONS_NOTE)
            if general and str(day.year) in differences:
                note(election, COMPOSITION_NOTE.format(diffs="; ".join(differences[str(day.year)])))
            repeated = {s for s, n in Counter(slug(label) for label in labels).items() if n > 1}

            day_candidates = candidates.filter((pl.col("date") == day) & (pl.col("general") == general))
            frames = []
            for row in day_contests.iter_rows(named=True):
                number = row["number"]
                tag = f"{day}:{kind}:{number}"
                contest = cs.key(f"{SOURCE}.contest", tag)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[row["area"]],
                    magnitude=row["seats"],
                    electoral_system_id=fptp if row["seats"] == 1 else block,
                    default_result_kind="tacit" if row["tacit"] else "counted",
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
                path = slug(row["name"]) if slug(row["name"]) not in repeated else f"{slug(row['name'])}-{number}"
                cs.add("slug", path=f"ca/on/{day}-{kind}/{path}", entity_id=contest)
                if (str(day), row["name"]) in CORRECTED:
                    note(contest, CORRECTED[str(day), row["name"]][1])
                if row["trailing"]:
                    note(contest, row["trailing"])
                if row["seats"] > 1 and re.search(r" - A$", row["name"]) and day.year < 1914:
                    note(contest, LUMPED)
                unit = cs.unit("ca.on", f"{day}:{number}", day)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=ontario,
                    scheme_code="ca.on",
                    code=f"{day}:{number}",
                    area_relation="equals",
                    area_id=areas[row["area"]],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                result_set = cs.key(f"{SOURCE}.result_set", tag)
                # A contest the official return gives poll by poll takes that finer set as its default, and this
                # district set stays as superseded.
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if row["tacit"] else "counted",
                    source_id=results,
                    status="superseded" if tag in polled else "active",
                    is_default=tag not in polled,
                    completeness=1,
                )
                ids.elections[str(day), general] = election
                ids.contests[tag] = (contest, result_set, unit, areas[row["area"]])
                accounting = [] if row["tacit"] else [(5, row["rejected"] + row["unmarked"] + row["declined"])]
                if row["registered"]:
                    accounting.append((2, row["registered"]))
                frames.extend(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None,
                        "measure": m,
                        "value": v,
                    }
                    for m, v in accounting
                )
                for person_row in day_candidates.filter(pl.col("number") == number).iter_rows(named=True):
                    display = person_row["name"]
                    candidate_tag = f"{tag}:{person_row['index']}"
                    person = cs.key(f"{SOURCE}.person", candidate_tag)
                    cs.add("person", id=person)
                    name(person, display)
                    party = party_ids.get(person_row["party"]) if person_row["party"] else None
                    member = None if person_row["party"] in nuances else party
                    option = cs.key(f"{SOURCE}.option", candidate_tag)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=display,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", candidate_tag),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=display,
                        member_of_party_id=member,
                        is_independent=person_row["label"] in UNAFFILIATED or person_row["party"] in nuances,
                    )
                    ids.options[candidate_tag] = (option, bool(person_row["elected"]))
                    if person_row["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    if not row["tacit"]:
                        frames.append(
                            {
                                "result_set_id": str(result_set),
                                "unit_id": str(unit),
                                "option_id": str(option),
                                "measure": 1,
                                "value": person_row["votes"],
                            }
                        )
            values(
                pl.DataFrame(
                    frames,
                    schema={
                        "result_set_id": pl.String,
                        "unit_id": pl.String,
                        "option_id": pl.String,
                        "measure": pl.Int16,
                        "value": pl.Int64,
                    },
                )
            )

        day = REFERENDUM_DAY
        election = cs.key(f"{SOURCE}.election", f"{day}:referendum")
        cs.add("election", id=election, polity_id=ontario, kind="referendum", date_from=day, date_to=day, status="held")
        name(election, "2007 Ontario electoral system referendum")
        name(election, "Référendum de 2007 sur le système électoral de l'Ontario", "native", "fr", False)
        cs.add("slug", path=f"ca/on/{day}-referendum", entity_id=election)
        contest = cs.key(f"{SOURCE}.contest", f"{day}:referendum")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=plebiscite,
            round_number=1,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="optional_referendum")
        name(contest, "Electoral system")
        cs.add("slug", path=f"ca/on/{day}-referendum/electoral-system", entity_id=contest)
        note(contest, REFERENDUM_NOTE)
        cs.add(
            "ballot_question_text",
            id=uuid5(NAMESPACE, f"{SOURCE}\x1fballot_question_text\x1f{contest}\x1fen"),
            contest_id=contest,
            kind="text",
            lang="en",
            body=question["QuestionEnglish"],
            url="https://results.elections.on.ca/en/reports",
            source_id=referendum_source,
        )
        cs.add(
            "ballot_question_text",
            id=uuid5(NAMESPACE, f"{SOURCE}\x1fballot_question_text\x1f{contest}\x1ffr"),
            contest_id=contest,
            kind="text",
            lang="fr",
            body=question["QuestionFrench"],
            url="https://results.elections.on.ca/fr/reports",
            source_id=referendum_source,
        )
        result_set = cs.key(f"{SOURCE}.result_set", f"{day}:referendum")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=referendum_source,
            status="superseded",
            is_default=False,
            completeness=1,
        )
        ids.elections[str(day), None] = election
        ids.referendum = (contest, result_set)
        totals = {"first": ballots["first"].sum(), "second": ballots["second"].sum()}
        options = {}
        for position, column in enumerate(("first", "second"), 1):
            options[column] = cs.key(f"{SOURCE}.option", f"{day}:referendum:{position}")
            label = question["YesAnswerTextEnglish" if column == "first" else "NoAnswerTextEnglish"]
            cs.add(
                "contest_option",
                id=options[column],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            outcome = "won" if totals[column] == max(totals.values()) else "lost"
            cs.add("outcome", result_set_id=result_set, option_id=options[column], kind=outcome)
            ids.answers[column] = (options[column], outcome)
        frames = []
        for row in ballots.iter_rows(named=True):
            unit = cs.unit("ca.on", f"{day}:{row['number']}", day)
            for option_id, measure, value in (
                (str(options["first"]), 1, row["first"]),
                (str(options["second"]), 1, row["second"]),
                (None, 5, row["rejected"] + row["unmarked"] + row["declined"]),
                (None, 2, row["registered"]),
            ):
                frames.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": option_id,
                        "measure": measure,
                        "value": value,
                    }
                )
        values(pl.DataFrame(frames))
        out = cs.write()
    for day in sorted(candidate_polls["date"].unique().to_list()):
        polls_of_day(day, ids, candidate_polls, referendum_polls, generals).write()
    return out


def packages() -> list[str]:
    days = pl.read_parquet(PARSED / "polls.parquet")["date"].unique().sort().to_list()
    return [f"{SOURCE}/all", *(f"{SOURCE}/{day}" for day in days)]


@dataclass
class Ids:
    """The ids the Draft resolves for rows the poll packages point at."""

    polity: UUID | None = None
    returns: dict[int, UUID] = field(default_factory=dict)
    divisions: dict[int, UUID] = field(default_factory=dict)
    elections: dict[tuple[str, bool | None], UUID] = field(default_factory=dict)
    # contest tag: the contest, its district result set, its district unit, and its district area
    contests: dict[str, tuple[UUID, UUID, UUID, UUID]] = field(default_factory=dict)
    # candidate tag: the option and whether it was elected
    options: dict[str, tuple[UUID, bool]] = field(default_factory=dict)
    referendum: tuple[UUID, UUID] | None = None
    answers: dict[str, tuple[UUID, str]] = field(default_factory=dict)


def polls_of_day(
    day: date, ids: Ids, candidate_polls: pl.DataFrame, referendum_polls: pl.DataFrame, generals: list[date]
) -> Package:
    """One election day's polls: their units, the polling divisions of the generals Elections Ontario publishes
    shapes for, and a poll-by-poll result set for each contest that supersedes its district set."""
    polls = candidate_polls.filter(pl.col("date") == day)
    general = bool(polls["general"][0])
    if polls["general"].n_unique() != 1:
        raise ValueError(f"{day}: a general and a by-election on one day")
    kind = "general" if general else "by-election"
    referendum = referendum_polls.filter(pl.col("date") == day)
    summary = (
        f"The {day.year} Ontario general election" if general else f"Ontario provincial by-elections of {english(day)}"
    )
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"{summary}{' and the electoral system referendum' if not referendum.is_empty() else ''} poll by poll, "
        "from Elections Ontario's Official Return from the Records",
        depends=[f"{SOURCE}/all"],
    )
    if ids.polity is None:
        raise ValueError("the Draft did not resolve Ontario")
    p.external.add(str(ids.polity))
    group = int(polls["group"][0])
    official = {g: output for g, _, output, _ in GROUPS_OF.values()}
    p.uses(raw.cached(SOURCE, report(group, official[group])))

    def contest_of(number: int) -> tuple[UUID, UUID, UUID, UUID]:
        return ids.contests[f"{day}:{kind}:{number}"]

    # Polling divisions, for the generals whose shapes Elections Ontario publishes.
    divisions: dict[tuple[int, int], str] = {}
    if general and day.year in DIVISIONS:
        shapes = pl.read_parquet(PARSED / "divisions.parquet").filter(pl.col("year") == day.year)
        p.uses(raw.cached(SOURCE, DIVISIONS[day.year]))
        later = [g for g in generals if g > day]
        until = later[0] if later else None
        vintage = p.id("boundary_set", day.year)
        p.add(
            "boundary_set",
            id=vintage,
            scheme_code=POLL_SCHEME,
            vintage=str(day.year),
            valid_from=day,
            valid_to=until,
            as_of=day,
        )
        for ed, pd, geom, label_point, _ in shapes.iter_rows():
            area = divisions[ed, pd] = p.id("area", day.year, ed, pd)
            p.add("area", id=area, scheme_code=POLL_SCHEME)
            p.add("area_code", area_id=area, code=f"{day.year}:{ed}:{pd:03d}", valid_from=day, valid_to=until)
            p.add("area_parent", area_id=area, parent_id=str(contest_of(ed)[3]), valid_from=day, valid_to=until)
            p.name(area, f"Polling division {pd:03d}", "en", "official")
            p.name(area, f"Section de vote {pd:03d}", "fr", "official", False)
            p.add(
                "area_geometry",
                area_id=area,
                valid_from=day,
                valid_to=until,
                geom=geom,
                label_point=label_point,
                quality="official",
                source_id=str(ids.divisions[day.year]),
            )

    # Units: every poll either return lists, shared by the general and the referendum held the same day.
    listed = pl.concat(
        [
            frame.select("number", "poll", "advance", "combined_with").unique()
            for frame in (polls, referendum)
            if not frame.is_empty()
        ]
    ).unique()
    if listed.select("number", "poll", "advance").unique().select("number", "poll").is_duplicated().any():
        raise ValueError(f"{day}: a poll is an advance poll in one return and not in the other")
    # A poll counted with another in one return and on its own in the other reports on its own.
    listed = (
        listed.group_by("number", "poll", "advance", maintain_order=True)
        .agg(pl.col("combined_with").first(), pl.col("combined_with").n_unique().alias("ways"))
        .with_columns(combined_with=pl.when(pl.col("ways") == 1).then("combined_with"))
        .drop("ways")
        .with_columns(division=pl.col("poll").str.extract(r"^(\d+)").cast(pl.Int64))
    )
    units: dict[tuple[int, str], str] = {
        (number, poll): p.id("unit", day, number, poll) for number, poll, *_ in listed.iter_rows()
    }
    for number, poll, advance, host, base in listed.iter_rows():
        _, _, district_unit, district_area = contest_of(number)
        division = None if advance or base is None else divisions.get((number, base))
        p.add(
            "reporting_unit",
            id=units[number, poll],
            polity_id=str(ids.polity),
            scheme_code=POLL_SCHEME,
            code=f"{day}:{number}:{poll}",
            parent_id=str(district_unit),
            area_relation="equals" if division is not None and poll.isdigit() else "within",
            area_id=division or str(district_area),
            kind="standard",
            counted_with_id=units[number, host] if host is not None else None,
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )

    # Result sets and their values.
    sets: dict[int, str] = {}
    for number in sorted(polls["number"].unique().to_list()):
        tag = f"{day}:{kind}:{number}"
        contest, district_set, _, _ = ids.contests[tag]
        sets[number] = p.id("result_set", tag)
        p.add(
            "result_set",
            id=sets[number],
            election_id=str(ids.elections[str(day), general]),
            contest_id=str(contest),
            stage="official",
            kind="counted",
            source_id=str(ids.returns[group]),
            status="active",
            is_default=True,
            completeness=1,
            supersedes_id=str(district_set),
        )
        p.add("note", id=p.id("note", tag), entity_id=sets[number], kind="methodology", body_md=POLLS_NOTE, lang="en")
    options = {tag: str(option) for tag, (option, _) in ids.options.items() if tag.startswith(f"{day}:{kind}:")}
    for tag, (option, elected) in ids.options.items():
        if elected and tag.startswith(f"{day}:{kind}:"):
            number = int(tag.split(":")[-2])
            p.add("outcome", result_set_id=sets[number], option_id=str(option), kind="elected", seats=1)
    unit_ids = pl.DataFrame(
        [(number, poll, id) for (number, poll), id in units.items()],
        schema={"number": pl.Int32, "poll": pl.String, "unit_id": pl.String},
        orient="row",
    )
    located = (
        polls.filter(pl.col("combined_with").is_null())
        .join(unit_ids, on=["number", "poll"])
        .with_columns(
            result_set_id=pl.col("number").replace_strict(sets, return_dtype=pl.String),
            option_id=(
                pl.lit(f"{day}:{kind}:") + pl.col("number").cast(pl.String) + ":" + pl.col("index").cast(pl.String)
            ).replace_strict(options, return_dtype=pl.String),
            channel=pl.when("advance").then(2).otherwise(1).cast(pl.Int16),
        )
    )
    frames = [poll_values(located, "votes", 1)]
    by_poll = located.unique(["number", "poll"]).with_columns(
        spoilt=pl.col("rejected") + pl.col("unmarked") + pl.col("declined")
    )
    frames += [poll_values(by_poll.with_columns(option_id=pl.lit(None, pl.String)), "spoilt", 5)]
    frames += [
        poll_values(by_poll.with_columns(option_id=pl.lit(None, pl.String), channel=pl.lit(0, pl.Int16)), "electors", 2)
    ]

    if not referendum.is_empty():
        if ids.referendum is None:
            raise ValueError("the Draft did not resolve the referendum")
        contest, district_set = ids.referendum
        question = p.id("result_set", f"{day}:referendum")
        p.add(
            "result_set",
            id=question,
            election_id=str(ids.elections[str(day), None]),
            contest_id=str(contest),
            stage="official",
            kind="counted",
            source_id=str(ids.returns[43]),
            status="active",
            is_default=True,
            completeness=1,
            supersedes_id=str(district_set),
        )
        p.add(
            "note", id=p.id("note", "referendum"), entity_id=question, kind="methodology", body_md=POLLS_NOTE, lang="en"
        )
        p.uses(raw.cached(SOURCE, REFERENDUM_POLLS))
        for option, outcome in ids.answers.values():
            p.add("outcome", result_set_id=question, option_id=str(option), kind=outcome)
        located = (
            referendum.filter(pl.col("combined_with").is_null())
            .join(unit_ids, on=["number", "poll"])
            .with_columns(
                result_set_id=pl.lit(question),
                channel=pl.when("advance").then(2).otherwise(1).cast(pl.Int16),
                spoilt=pl.col("rejected") + pl.col("unmarked") + pl.col("declined"),
            )
        )
        for column in ("first", "second"):
            option = str(ids.answers[column][0])
            frames.append(poll_values(located.with_columns(option_id=pl.lit(option)), column, 1))
        frames.append(poll_values(located.with_columns(option_id=pl.lit(None, pl.String)), "spoilt", 5))
        frames.append(
            poll_values(
                located.with_columns(option_id=pl.lit(None, pl.String), channel=pl.lit(0, pl.Int16)), "electors", 2
            )
        )
    p.frame("result_value", pl.concat(frames))
    return p


def poll_values(frame: pl.DataFrame, column: str, measure: int) -> pl.DataFrame:
    """Result values from one count column. A poll where no poll was taken keeps only the counts it reports, and
    registered electors are left out where the return lists none (advance polls)."""
    kept = frame.filter((pl.col(column) != 0) | (~pl.col("no_poll") & pl.lit(measure != 2)))
    return kept.select(
        "result_set_id",
        "unit_id",
        "option_id",
        channel=pl.col("channel").cast(pl.Int16),
        measure=pl.lit(measure, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        value=pl.col(column).cast(pl.Int64),
    )
