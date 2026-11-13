import csv
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
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
BOUNDARIES = (
    "https://www.elections.on.ca/content/dam/NGW/sitecontent/2017/preo/shapefiles/"
    "Electoral%20District%20Shapefile%20-%202022%20General%20Election.zip"
)
LAMBERT = "+proj=lcc +lat_1=44.5 +lat_2=54.5 +lat_0=0 +lon_0=-84 +x_0=1000000 +y_0=0 +ellps=GRS80 +units=m +no_defs"
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
    for url in (COMPOSITION, REFERENDUM):
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")


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
    print(
        f"{contests_frame.select('date', 'general').n_unique()} elections, {contests_frame.height} contests, "
        f"{people_frame.height} candidates, {people_frame['party'].n_unique()} parties, {len(ballots)} referendum "
        f"districts, {shapes.height} shapes"
    )


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    ballots = pl.read_parquet(PARSED / "referendum.parquet")
    sizes = pl.read_parquet(PARSED / "sizes.parquet")
    question = json.loads((PARSED / "question.json").read_text())
    differences = json.loads((PARSED / "differences.json").read_text())
    shapes = pl.read_parquet(PARSED / "shapes.parquet")
    generals = sorted(contests.filter(pl.col("general"))["date"].unique().to_list())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ontario's general elections from 1867 to 2025, its by-elections from 1992 to 2024, and the 2007 "
                "electoral system referendum, by electoral district, from Elections Ontario"
            ),
            polity="CA-ON",
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
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if row["tacit"] else "counted",
                    source_id=results,
                    status="active",
                    is_default=True,
                    completeness=1,
                )
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
            status="active",
            is_default=True,
            completeness=1,
        )
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
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[column],
                kind="won" if totals[column] == max(totals.values()) else "lost",
            )
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
        return cs.write()
