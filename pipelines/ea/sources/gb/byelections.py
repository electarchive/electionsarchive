import re
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "gb.byelections"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://raw.githubusercontent.com/ukparliament/psephology/main/db/data/results-by-parliament/{}/by-elections/{}.csv"
PARLIAMENTS = range(55, 60)
TITLE = "Election results: by-elections in the 55th to 59th Parliaments"
UMIT = f"{umit.DATAVERSE}/6815697?format=original"
UMIT_README = f"{umit.DATAVERSE}/6811223"
UMIT_TITLE = "By-elections to the UK House of Commons, version 3"
UMIT_LANDING = "https://doi.org/10.7910/DVN/KN5Y7V"
OFFICIAL_FROM = date(2010, 5, 6)
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
ALIASES = {
    "Social and Liberal Democrats": "liberal-democrats",
    "The Greens": "green-party",
    "Workers Revolutonary": "workers-revolutionary-party",
    "Indpendent Labour Party": "independent-labour-party",
    "Captain Rainbows Universal Party": "captain-rainbow-s-universal-party",
    "Death off Road: Freight on Rail": "death-off-roads-freight-on-rail",
    "Death off Roads: Freight on Rails": "death-off-roads-freight-on-rail",
}
NAMED_TWO = re.compile(
    r"\bboth\b|\w(?:'s?|s'|') (?:and|&) [A-Z][\w-]+(?:'s?|s'|')? |[A-Z][\w-]+ (?:and|&) [A-Z][\w-]+(?:'s?|s'|') |"
    r"\w(?:'s?|s'|') (?:death|resignation|appointment|elevation|succession)[^;]*? and [A-Z][\w-]+(?:'s?|s'|') "
)
WHOLE = re.compile(r"(?:the|general|previous|\d{4}) election (?:being|was) declared void")
CAVEATS = {
    "derived": "Umit's by-election dataset marks no winners. The candidates shown as elected are those with the most "
    "votes, one for each seat filled.",
    "tie": "Two or more candidates tie for the last seat in Umit's by-election dataset, so the tied candidates are not "
    "shown as elected.",
    "missing": "Umit's by-election dataset lists no more candidates than seats in this contested race, so some "
    "candidates are missing and no candidate is shown as elected.",
    "unopposed": "Umit's by-election dataset marks every candidate here as unopposed but lists more candidates than "
    "seats, so no candidate is shown as elected.",
}


def fetch() -> None:
    for parliament in PARLIAMENTS:
        for table in ("constituencies", "candidacies"):
            raw.fetch(SOURCE, BASE.format(parliament, table), suffix=".csv")
    raw.fetch(SOURCE, UMIT, suffix=".csv")
    raw.fetch(SOURCE, UMIT_README, suffix=".txt")


def read(table: str) -> pl.DataFrame:
    return pl.concat(
        [
            pl.read_csv(raw.cached(SOURCE, BASE.format(parliament, table)).path, infer_schema_length=0)
            for parliament in PARLIAMENTS
        ],
        how="diagonal_relaxed",
    )


def parse() -> None:
    general = pl.read_parquet(DATA / "parsed" / umit.SOURCE / "results.parquet")
    held = read("constituencies").select(
        day=pl.col("By-election date").str.to_date("%d/%m/%Y"),
        ons=pl.col("ONS ID"),
        constituency=pl.col("Constituency name"),
        member=pl.concat_str(
            pl.col("Member first name").str.strip_chars(), pl.col("Member surname").str.strip_chars(), separator=" "
        ),
        electorate=pl.col("Electorate").cast(pl.Int64, strict=False),
        valid=pl.col("Valid votes").cast(pl.Int64, strict=False),
        invalid=pl.col("Invalid votes").cast(pl.Int64, strict=False),
    )
    stood = read("candidacies").select(
        day=pl.col("Election date").str.to_date("%d/%m/%Y"),
        ons=pl.coalesce("ONS ID", "ONS ID2"),
        party=pl.col("Party name"),
        first=pl.col("Candidate first name").str.strip_chars(),
        surname=pl.col("Candidate surname").str.strip_chars(),
        gender=pl.col("Candidate gender").str.to_lowercase(),
        mnis=pl.col("Member MNIS ID"),
        votes=pl.col("Votes").cast(pl.Int64),
    )
    seats = (
        pl.read_csv(raw.cached(umit.SOURCE, umit.RESULTS).path, infer_schema_length=0)
        .select(
            date_start=pl.col("date_start").str.to_date(),
            constituency=pl.col("constituency"),
            ons=pl.col("constituency_id"),
        )
        .unique()
        .join(
            general.select("date_start", "constituency", "unit", "country").unique(), on=["date_start", "constituency"]
        )
        .sort("date_start")
    )
    held = held.sort("day").join_asof(
        seats.rename({"date_start": "general"}), left_on="day", right_on="general", by="ons", strategy="backward"
    )
    if held["unit"].is_null().any():
        raise ValueError(
            f"no general election seat for {held.filter(pl.col('unit').is_null())['constituency'].to_list()}"
        )
    official = (
        stood.join(held.drop("constituency_right", strict=False), on=["day", "ons"], how="left")
        .with_columns(
            candidate=pl.concat_str("first", "surname", separator=" "),
        )
        .with_columns(
            identity=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit(":"),
                "unit",
                pl.lit(":"),
                pl.col("candidate").map_elements(slug, return_dtype=pl.String),
            )
        )
        .with_columns(
            elected=pl.col("candidate") == pl.col("member"),
            general=pl.concat_str(pl.col("general").cast(pl.String), pl.lit(":"), "unit"),
            vacancies=pl.lit(1),
            status=pl.lit("declared"),
            unopposed=pl.lit(False),
            day_to=pl.col("day"),
            cause=pl.lit(None, pl.String),
            origin=pl.lit("parliament"),
        )
    )
    if official["unit"].is_null().any():
        raise ValueError("a candidacy has no by-election")
    winners = official.group_by("day", "ons").agg(pl.col("elected").sum())
    if winners.filter(pl.col("elected") != 1).height:
        raise ValueError(f"no single declared member: {winners.filter(pl.col('elected') != 1).rows()}")

    contests: dict[str, list[tuple[date, str, int]]] = {}
    with connect() as conn:
        for day, place, key, magnitude in conn.execute(
            """select e.date_from, split_part(n.text, ' general election in ', 2), x.value, c.magnitude
               from contest c join election e on e.id = c.election_id
               join name n on n.entity_id = c.id and n.is_preferred
               join external_id x on x.entity_id = c.id and x.scheme = 'gb.contest'
               where e.kind = 'general' and c.body_id = (
                   select entity_id from external_id where scheme = 'gb.body' and value = 'house-of-commons')
               order by 1"""
        ).fetchall():
            contests.setdefault(place, []).append((day, key.split(":", 1)[1], magnitude))
    places = general.select("constituency", "date_start", "unit", "country").unique()
    rows = []
    missing: set[tuple[str, str]] = set()
    for line in (
        pl.read_csv(raw.cached(SOURCE, UMIT).path, infer_schema_length=0)
        .with_columns(day=pl.col("date").str.to_date())
        .filter(pl.col("day") < OFFICIAL_FROM)
        .with_columns(cause=pl.col("cause").unique(maintain_order=True).str.join("; ").over("day", "constituency"))
        .unique(["day", "constituency", "candidate"], keep="first", maintain_order=True)
        .iter_rows(named=True)
    ):
        place = line["constituency"]
        names = {place, place.replace(" Burghs", " Boroughs")}
        held = sorted(row for name in names for row in contests.get(name, []))
        found = next((row for row in reversed(held) if row[0] <= line["day"]), held[0] if held else None)
        if found is None:
            missing.add((line["election"], place))
            continue
        (day, unit_code, magnitude) = found
        nearest = (
            places.filter(pl.col("constituency").is_in(names))
            .with_columns(gap=(pl.col("date_start") - line["day"]).dt.total_days().abs())
            .sort("gap")
        )
        if nearest.height == 0:
            missing.add((line["election"], place))
            continue
        person_unit = unit_code if not unit_code.isdigit() else nearest["unit"][0]
        identity = line["candidate"]
        for code, text, since, until, mark in umit.NAMESAKES:
            if person_unit == code and identity == text and since <= line["day"].year <= until:
                identity += mark
        surname, _, given = line["candidate"].partition(", ")
        span = re.match(r"^\d+-(\d+) (\w+) (\d{4}) by-election$", line["election"])
        rows.append(
            {
                "day": line["day"],
                "day_to": datetime.strptime(f"{span[1]} {span[2][:3]} {span[3]}", "%d %b %Y").date()
                if span
                else line["day"],
                "constituency": place,
                "country": nearest["country"][0],
                "unit": unit_code,
                "general": f"{day}:{unit_code}",
                "magnitude": magnitude,
                "candidate": f"{given} {surname}".strip(),
                "identity": f"{person_unit}:{slug(line['candidate'])}{identity.removeprefix(line['candidate'])}",
                "party": line["party"],
                "votes": int(line["votes"]) if line["votes"] else None,
                "unopposed": line["unopposed"] == "1",
                "cause": line["cause"],
            }
        )
    if missing:
        print(f"{len(missing)} by-elections with no general election seat: {sorted(missing)[:40]}")
    frame = pl.DataFrame(rows)
    by = ["day", "unit"]
    candidates = pl.len().over(by)
    two = (
        (pl.col("magnitude") >= 2)
        & (candidates >= 3)
        & ~pl.col("unopposed")
        & (pl.col("cause").str.contains(NAMED_TWO.pattern) | pl.col("cause").str.contains(WHOLE.pattern))
    )
    frame = frame.with_columns(
        vacancies=pl.when(pl.col("unopposed").all().over(by))
        .then(pl.min_horizontal(candidates, pl.col("magnitude")))
        .when(two)
        .then(pl.lit(2))
        .otherwise(pl.lit(1))
    )
    rank = pl.col("votes").rank("max", descending=True).over(by)
    contested = ~pl.col("unopposed").all().over(by)
    frame = frame.with_columns(
        status=pl.when(~contested & (candidates > pl.col("vacancies")))
        .then(pl.lit("unopposed"))
        .when(~contested)
        .then(pl.lit("tacit"))
        .when(candidates <= pl.col("vacancies"))
        .then(pl.lit("missing"))
        .when((rank <= pl.col("vacancies")).sum().over(by) < pl.col("vacancies"))
        .then(pl.lit("tie"))
        .otherwise(pl.lit("derived"))
    ).with_columns(
        elected=pl.when(pl.col("status") == "tacit")
        .then(True)
        .when(pl.col("status").is_in(["derived", "tie"]))
        .then(rank <= pl.col("vacancies"))
        .otherwise(False),
        origin=pl.lit("umit"),
    )
    if frame.filter(pl.len().over(*by, "identity") > 1).height:
        raise ValueError(f"a candidate stands twice: {frame.filter(pl.len().over(*by, 'identity') > 1).rows()[:3]}")
    print(
        "two seats filled:",
        sorted(
            {
                (str(day), place)
                for day, place in frame.filter(pl.col("vacancies") == 2).select("day", "constituency").iter_rows()
            }
        ),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat([frame, official], how="diagonal_relaxed").sort(
        "day", "unit", pl.col("votes"), descending=[False, False, True], nulls_last=True
    ).write_parquet(PARSED / "results.parquet")
    print(
        f"{frame.select(by).n_unique()} by-elections from Umit, {official.select('day', 'ons').n_unique()} official, "
        f"{frame.height + official.height} candidacies"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons by-elections since 1833 by constituency, from Resul Umit's by-election dataset "
            "and, since 2010, Parliament's election results data",
            polity="GB",
        )
        sources = {
            "parliament": cs.source("UK Parliament", TITLE),
            "umit": cs.source(umit.PUBLISHER, UMIT_TITLE),
        }
        cs.add(
            "source",
            id=sources["parliament"],
            kind="official_body",
            publisher="UK Parliament",
            title=TITLE,
            url="https://electionresults.parliament.uk/elections.html",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Read from the House of Commons Library's psephology repository (github.com/ukparliament/psephology), "
            "the data behind electionresults.parliament.uk.",
        )
        cs.add(
            "source",
            id=sources["umit"],
            kind="academic",
            publisher=umit.PUBLISHER,
            title=UMIT_TITLE,
            url=UMIT_LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            notes="Compiled from electionhub.co.uk with corrections from the House of Commons, Wikipedia, and Eggers and "
            "Spirling. It marks no winners, and dates of older by-elections held over several days may be inexact.",
        )
        for parliament in PARLIAMENTS:
            for table in ("constituencies", "candidacies"):
                cs.artifact(raw.cached(SOURCE, BASE.format(parliament, table)), sources["parliament"])
        for url in (UMIT, UMIT_README):
            cs.artifact(raw.cached(SOURCE, url), sources["umit"])
        gb = cs.polity("GB")
        commons = cs.key("gb.body", "house-of-commons")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if commons in cs.minted or any(system in cs.minted for system in systems.values()):
            raise ValueError("the House of Commons and its electoral systems must already exist")
        known = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = 'gb.umit.person'").fetchall()
        }
        parties: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def party_of(label: str, year: int, country: str | None) -> UUID | None:
            if label in umit.NOT_PARTIES:
                return None
            key = next(
                (
                    key
                    for (era, since), key in sorted(umit.ERAS.items(), reverse=True)
                    if era == label and year >= since
                ),
                None,
            )
            if key is None and label == "Unionist":
                key = "scottish-unionist" if country == "SCT" else "conservative"
            key = key or umit.ALIASES.get(label) or ALIASES.get(label) or slug(label)
            if key not in parties:
                parties[key] = cs.key(*umit.EXISTING[key]) if key in umit.EXISTING else cs.key("gb.party", key)
                if parties[key] in cs.minted:
                    if key in umit.EXISTING:
                        raise ValueError(f"{key} is not in the database")
                    cs.add("party", id=parties[key], kind="party", polity_id=gb)
                    name(parties[key], umit.NAMES.get(key, label))
            return parties[key]

        for (day,), on_day in frame.group_by("day", maintain_order=True):
            election = cs.key(f"{SOURCE}.election", str(day))
            spoken = f"{day.day} {day:%B %Y}"
            places = on_day["constituency"].unique(maintain_order=True).to_list()
            cs.add(
                "election",
                id=election,
                polity_id=gb,
                kind="by_election",
                date_from=day,
                date_to=on_day["day_to"].max(),
                status="held",
            )
            name(
                election,
                f"{places[0]} by-election, {spoken}"
                if len(places) == 1
                else f"UK parliamentary by-elections, {spoken}",
            )
            path = f"gb/{day}-by-election"
            cs.add("slug", path=path, entity_id=election)
            for (unit_code,), lines in on_day.group_by("unit", maintain_order=True):
                first = lines.row(0, named=True)
                general = cs.key("gb.contest", first["general"])
                if general in cs.minted:
                    raise ValueError(f"no general election contest {first['general']}")
                (district,) = conn.execute("select district_area_id from contest where id = %s", [general]).fetchone()
                tacit = first["status"] in ("tacit", "unopposed")
                contest_key = f"{day}:{unit_code}"
                contest = cs.key(f"{SOURCE}.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=district,
                    magnitude=first["vacancies"],
                    round_number=1,
                    electoral_system_id=systems["fptp" if first["vacancies"] == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{day.year} by-election in {first['constituency']}")
                cs.add("slug", path=f"{path}/{slug(first['constituency'])}", entity_id=contest)
                if first["cause"]:
                    cs.add("note", entity_id=contest, kind="context", body_md=first["cause"])
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[first["origin"]],
                    is_default=True,
                    completeness=1,
                )
                if first["status"] in CAVEATS:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[first["status"]])
                unit = cs.unit("gb.pcon", unit_code)
                for measure, value in (
                    (REGISTERED, first["electorate"]),
                    (VALID, first["valid"]),
                    (INVALID, first["invalid"]),
                ):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{slug(line['candidate'])}"
                    option = cs.key(f"{SOURCE}.option", option_key)
                    identity = line["identity"]
                    if identity not in persons:
                        persons[identity] = (
                            cs.key("mnis", line["mnis"])
                            if line["mnis"]
                            else cs.key("gb.umit.person", identity)
                            if identity in known
                            else cs.key(f"{SOURCE}.person", identity)
                        )
                        if persons[identity] in cs.minted:
                            cs.add("person", id=persons[identity], gender=line["gender"])
                            name(persons[identity], line["candidate"])
                    party = party_of(line["party"], day.year, line["country"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=line["candidate"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] == "Independent",
                    )
                    if line["votes"] is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
