import json
import re
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import EV, FIPS, RUNOFF_SYSTEMS, THRESHOLDS, elstats, plain, town_counties, url
from ea.store import DATA, connect

SOURCE = "us.sld_primaries_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ROUNDS = [
    (
        "MI",
        date(2026, 8, 4),
        "Michigan Board of State Canvassers",
        "August 4, 2026 primary election official results by county",
        "https://mielections.us/election/results/2026PRI_CENR.html",
        "https://mielections.us/election/results/2026PRI_CENR.html#2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt",
    ),
    (
        "NV",
        date(2026, 6, 9),
        "Nevada Secretary of State",
        "June 9, 2026 primary election results by county",
        "https://silverstateelection.nv.gov/",
        url("nv", "nv-2026-primary-county-results.xml"),
    ),
    (
        "SC",
        date(2026, 6, 9),
        "South Carolina State Election Commission",
        "June 9, 2026 statewide primaries: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-09/detailxml.zip"),
    ),
    (
        "SC",
        date(2026, 6, 23),
        "South Carolina State Election Commission",
        "June 23, 2026 primary runoffs: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-23/detailxml.zip"),
    ),
    (
        "UT",
        date(2026, 6, 23),
        "Utah Lieutenant Governor's Office",
        "June 23, 2026 primary election official results",
        "https://electionresults.utah.gov/",
        f"{EV['ut'][0]}ballot-items",
    ),
    (
        "MN",
        date(2026, 8, 11),
        "Minnesota Secretary of State",
        "August 11, 2026 state primary official results by precinct",
        "https://electionresults.sos.mn.gov/",
        "https://electionresultsfiles.sos.mn.gov/20260811/allracesbyprecinct.txt",
    ),
    (
        "RI",
        date(2026, 9, 9),
        "Rhode Island Board of Elections",
        "September 9, 2026 statewide primary official results",
        "https://electionresults.ri.gov/",
        f"{EV['ri'][0]}ballot-items",
    ),
]
LEGISLATIVE = re.compile(
    r"(?P<party>REP|DEM) (?:State (?P<chamber>Senate|House)|(?P<body>Senator|Representative) in General Assembly) District (?P<district>\d+)"
)
PARTIES = {
    "dfl": ("dp", "MN:minnesota-democratic-farmer-labor-party", "Democratic–Farmer–Labor"),
    "democratic": ("dp", "democratic-party", "Democratic"),
    "republican": ("rp", "republican-party", "Republican"),
}


def fetch() -> None:
    for state, *_, address in ROUNDS:
        if state in ("UT", "MN", "RI"):
            artifact = raw.fetch(SOURCE, address, suffix=".json" if state != "MN" else ".txt")
            if state != "MN":
                for item in json.loads(artifact.path.read_bytes())["data"]:
                    if LEGISLATIVE.fullmatch(item["name"][0]["text"]):
                        raw.fetch(SOURCE, f"{address}/{item['id']}", suffix=".json")
            continue
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            held = raw.cached("us.oe_primaries_2026", address)
            raw.store(
                SOURCE,
                address,
                held.path.read_bytes(),
                suffix=held.path.suffix,
                retrieved_at=held.retrieved_at,
                media_type=held.media_type,
            )


def parse() -> None:
    frames = []
    for state, day, *_, address in ROUNDS:
        path = raw.cached(SOURCE, address).path
        if state == "MI":
            frames.append(
                pl.read_csv(
                    path,
                    separator="\t",
                    skip_rows=1,
                    infer_schema=False,
                    quote_char=None,
                    truncate_ragged_lines=True,
                )
                .filter(pl.col("OfficeCode(text)").is_in(["9", "10"]))
                .select(
                    state=pl.lit(state),
                    day=pl.lit(day),
                    party=pl.col("PartyDescription").str.to_lowercase(),
                    chamber=pl.when(pl.col("OfficeCode(text)") == "9")
                    .then(pl.lit("senate"))
                    .otherwise(pl.lit("house")),
                    district=pl.col("DistrictCode(Text)").cast(pl.Int32).cast(pl.String),
                    candidate=pl.concat_str(
                        "CandidateFirstName",
                        "CandidateMiddleName",
                        "CandidateLastName",
                        separator=" ",
                        ignore_nulls=True,
                    ).str.replace_all(r"\s+", " "),
                    write_in=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("").is_in(["W", "Y"]),
                    county=pl.col("CountyName"),
                    votes=pl.col("CandidateVotes").str.replace_all(",", "").cast(pl.Int64),
                )
            )
            continue
        rows = []
        if state == "MN":
            totals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
            for line in path.read_text(encoding="latin-1").splitlines():
                cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
                found = (
                    re.fullmatch(r"State (Senator|Representative) District (\d+)([AB]?)", cells[4])
                    if len(cells) > 13
                    else None
                )
                if found is None or cells[10] not in ("DFL", "R"):
                    continue
                totals[
                    (
                        "senate" if found[1] == "Senator" else "house",
                        "-".join(filter(None, (found[2], found[3].lower()))),
                        "dfl" if cells[10] == "DFL" else "republican",
                        re.sub(r"\s+", " ", cells[7]),
                        f"27{2 * int(cells[1]) - 1:03}",
                    )
                ] += int(cells[13] or 0)
            rows = [(*key, votes) for key, votes in totals.items()]
        if state in ("UT", "RI"):
            for item in json.loads(path.read_bytes())["data"]:
                found = LEGISLATIVE.fullmatch(item["name"][0]["text"])
                if found is None:
                    continue
                detail = json.loads(raw.cached(SOURCE, f"{address}/{item['id']}").path.read_bytes())
                for county in detail["breakdownResults"]:
                    locality = county["locality"]["name"][0]["text"]
                    if state == "RI":
                        town = plain(re.sub(elstats.SUFFIXES, "", locality.lower()))
                        if town not in town_counties("RI"):
                            continue
                        locality = town_counties("RI")[town]
                    rows.extend(
                        (
                            "senate" if "Senat" in (found["chamber"] or found["body"]) else "house",
                            found["district"],
                            "republican" if found["party"] == "REP" else "democratic",
                            re.sub(
                                r'"(\w{1,3})"',
                                lambda match: f'"{match[1].upper()}"',
                                re.sub(r"\bMc(\w)", lambda match: f"Mc{match[1].upper()}", text.title()),
                            )
                            if text.isupper()
                            else text,
                            locality.removesuffix(" County"),
                            option["voteCount"],
                        )
                        for option in county["ballotOptions"]
                        if not option["isWriteIn"]
                        for text in [re.sub(r"\s*(\(I\)|\*)$", "", option["name"][0]["text"]).strip()]
                    )
        if state == "NV":
            for county in ElementTree.fromstring(path.read_text()).iter("County"):
                for race in county.iter("Race"):
                    found = re.fullmatch(r"State (Senate|Assembly), District\s+(\d+) \((\w+)\)", race.get("RaceTitle"))
                    if found is None:
                        continue
                    for candidate in race.iter("Candidate"):
                        last, _, first = candidate.get("CandidateName").partition(", ")
                        rows.append(
                            (
                                found[1].lower(),
                                int(found[2]),
                                found[3].lower(),
                                f"{first} {last}".strip(),
                                county.get("Name"),
                                int(candidate.get("Votes")),
                            )
                        )
        if state == "SC":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            for contest in root.iter("Contest"):
                found = re.fullmatch(
                    r"State (Senate|House of Representatives), District (\d+) - (REP|DEM)",
                    re.sub(r"\s+", " ", contest.get("text")),
                )
                if found is None:
                    continue
                for choice in contest.iter("Choice"):
                    if "*Decertified" in choice.get("text"):
                        continue
                    totals: dict[str, int] = defaultdict(int)
                    for vote_type in choice.iter("VoteType"):
                        for county in vote_type.iter("County"):
                            totals[county.get("name")] += int(county.get("votes"))
                    rows.extend(
                        (
                            "senate" if found[1] == "Senate" else "house",
                            found[2],
                            {"REP": "republican", "DEM": "democratic"}[found[3]],
                            choice.get("text"),
                            county,
                            votes,
                        )
                        for county, votes in totals.items()
                    )
        frames.append(
            pl.DataFrame(
                rows,
                schema=[
                    ("chamber", pl.String),
                    ("district", pl.String),
                    ("party", pl.String),
                    ("candidate", pl.String),
                    ("county", pl.String),
                    ("votes", pl.Int64),
                ],
                orient="row",
            ).select(
                state=pl.lit(state),
                day=pl.lit(day),
                party="party",
                chamber="chamber",
                district="district",
                candidate="candidate",
                write_in=pl.lit(False),
                county="county",
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).group_by(
        "state", "day", "party", "chamber", "district", "candidate", "write_in", "county", maintain_order=True
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 state legislative primaries and primary runoffs by county from the states' official results: Michigan, Minnesota, Nevada, Rhode Island, South Carolina, and Utah",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        counts = {"contests": 0, "candidates": 0, "matched": 0, "skipped": 0}
        for state, day, publisher, title, page, address in ROUNDS:
            first = min(when for other, when, *_ in ROUNDS if other == state)
            runoff = day != first
            system = cs.key("us.electoral_system", RUNOFF_SYSTEMS.get(state, "fptp"))
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=page,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, address), source)
            election = cs.key("us.election", f"{day}:{state}:{'runoff' if runoff else 'primary'}")
            if election in cs.minted:
                raise ValueError(f"the {day} {state} primary election must exist")
            templates = {
                value: row
                for value, *row in conn.execute(
                    """select x.value, co.body_id, co.district_area_id, co.magnitude, co.term, n.text
                       from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                       join name n on n.entity_id = co.body_id and n.is_preferred
                       where x.value ~ %s""",
                    [f"^2026-11-03:{state}:(house|senate|assembly):[0-9]+(-[ab])?:g$"],
                ).fetchall()
            }
            counties = {}
            for unit, code, text in conn.execute(
                """select u.id, u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{FIPS[state]}___"],
            ).fetchall():
                counties[code] = counties[re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))] = unit
            pool: dict[tuple[str, str], set[object]] = defaultdict(set)
            for person, text in conn.execute(
                """select distinct cy.person_id, n.text from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest co on co.id = o.contest_id
                   join name b on b.entity_id = co.body_id and b.is_preferred and b.text !~ 'United States'
                   join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where x.value ~ %s""",
                [f"^20[0-2][0-9]-[0-9-]+:{state}:(house|senate|assembly):"],
            ).fetchall():
                words = [
                    w for w in re.sub(r"[^a-z ]", "", text.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
                ]
                if len(words) > 1:
                    pool[(words[0], words[-1])].add(person)
            grouped = (
                frame.filter((pl.col("state") == state) & (pl.col("day") == day))
                .group_by("party", "chamber", "district", "candidate", "write_in")
                .agg(pl.col("county"), pl.col("votes"))
            )
            for (party, chamber, district), contest_rows in sorted(
                grouped.group_by("party", "chamber", "district"), key=lambda entry: entry[0]
            ):
                scope, party_key, label = PARTIES[party]
                template = templates.get(f"2026-11-03:{state}:{chamber}:{district}:g")
                if template is None:
                    print("no 2026 general contest for", state, chamber, district)
                    counts["skipped"] += 1
                    continue
                body, area, magnitude, term, chamber_name = template
                contest_key = f"{day}:{state}:{chamber}:{district}:{scope}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term=term,
                    round_number=2 if runoff else 1,
                    previous_round_id=cs.key("us.contest", f"{first}:{state}:{chamber}:{district}:{scope}")
                    if runoff
                    else None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    party_scope_id=cs.key("us.party", party_key),
                )
                stage = "primary runoff" if runoff else "primary"
                name(contest, f"2026 {chamber_name} {label} {stage}, District {district}")
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{stage.replace(' ', '-')}/state-{chamber}-{district}-{party}",
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
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"Results by county from the {publisher}'s official results."
                    + (
                        " Declared write-in candidates are listed by name; the candidate with the most votes is shown as the nominee."
                        if state == "MI"
                        else ""
                    ),
                )
                totals = []
                for candidate, write_in, by_county, votes in contest_rows.select(
                    "candidate", "write_in", "county", "votes"
                ).iter_rows():
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", candidate.lower()).split()
                        if w not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    found = pool.get((words[0], words[-1]), set())
                    if len(found) == 1:
                        person = next(iter(found))
                        counts["matched"] += 1
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, candidate)
                            pool[(words[0], words[-1])].add(person)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key),
                        ballot_label=f"{candidate} (write-in)" if write_in else candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    for county, value in zip(by_county, votes, strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties.get(county)
                            or counties[re.sub(r"[^a-z]", "", county.lower().replace("saint ", "st "))],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                    totals.append((sum(votes), option))
                    counts["candidates"] += 1
                totals.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in totals)
                if (
                    not runoff
                    and state in THRESHOLDS
                    and len(totals) > 1
                    and totals[0][0] * 100 <= THRESHOLDS[state] * everyone
                ):
                    if len(totals) == 2 or totals[1][0] > totals[2][0]:
                        for _, option in totals[:2]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                elif totals[0][0] > 0 and (len(totals) == 1 or totals[0][0] > totals[1][0]):
                    cs.add("outcome", result_set_id=result_set, option_id=totals[0][1], kind="won", seats=None)
                counts["contests"] += 1
        print(counts)
        return cs.write()
