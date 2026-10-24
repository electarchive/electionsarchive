from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "gb.byelections"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://raw.githubusercontent.com/ukparliament/psephology/main/db/data/results-by-parliament/{}/by-elections/{}.csv"
PARLIAMENTS = range(55, 60)
TITLE = "Election results: by-elections in the 55th to 59th Parliaments"
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5


def fetch() -> None:
    for parliament in PARLIAMENTS:
        for table in ("constituencies", "candidacies"):
            raw.fetch(SOURCE, BASE.format(parliament, table), suffix=".csv")


def read(table: str) -> pl.DataFrame:
    return pl.concat(
        [
            pl.read_csv(raw.cached(SOURCE, BASE.format(parliament, table)).path, infer_schema_length=0)
            for parliament in PARLIAMENTS
        ],
        how="diagonal_relaxed",
    )


def parse() -> None:
    held = read("constituencies").select(
        day=pl.col("By-election date").str.to_date("%d/%m/%Y"),
        ons=pl.col("ONS ID"),
        constituency=pl.col("Constituency name"),
        country=pl.col("Country name"),
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
        gender=pl.col("Candidate gender"),
        mnis=pl.col("Member MNIS ID"),
        votes=pl.col("Votes").cast(pl.Int64),
    )
    general = (
        pl.read_csv(raw.cached(umit.SOURCE, umit.RESULTS).path, infer_schema_length=0)
        .select(
            date_start=pl.col("date_start").str.to_date(),
            constituency=pl.col("constituency"),
            ons=pl.col("constituency_id"),
        )
        .unique()
        .join(
            pl.read_parquet(DATA / "parsed" / umit.SOURCE / "results.parquet")
            .select("date_start", "constituency", "unit")
            .unique(),
            on=["date_start", "constituency"],
        )
        .sort("date_start")
    )
    held = held.sort("day").join_asof(
        general.rename({"date_start": "general"}), left_on="day", right_on="general", by="ons", strategy="backward"
    )
    if held["unit"].is_null().any():
        raise ValueError(
            f"no general election seat for {held.filter(pl.col('unit').is_null())['constituency'].to_list()}"
        )
    frame = stood.join(held.drop("constituency_right", strict=False), on=["day", "ons"], how="left")
    if frame["unit"].is_null().any():
        raise ValueError("a candidacy has no by-election")
    frame = frame.with_columns(
        candidate=pl.concat_str("first", "surname", separator=" "),
        elected=pl.concat_str("first", "surname", separator=" ") == pl.col("member"),
    )
    winners = frame.group_by("day", "ons").agg(pl.col("elected").sum())
    if winners.filter(pl.col("elected") != 1).height:
        raise ValueError(f"no single declared member: {winners.filter(pl.col('elected') != 1).rows()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.sort("day", "ons", pl.col("votes"), descending=[False, False, True]).write_parquet(PARSED / "results.parquet")
    print(f"{held.height} by-elections, {frame.height} candidacies")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons by-elections since 2010 by constituency, from Parliament's election results data",
            polity="GB",
        )
        source = cs.source("UK Parliament", TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="UK Parliament",
            title=TITLE,
            url="https://electionresults.parliament.uk/elections.html",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Read from the House of Commons Library's psephology repository (github.com/ukparliament/psephology), "
            "the data behind electionresults.parliament.uk.",
        )
        for parliament in PARLIAMENTS:
            for table in ("constituencies", "candidacies"):
                cs.artifact(raw.cached(SOURCE, BASE.format(parliament, table)), source)
        gb = cs.polity("GB")
        commons = cs.key("gb.body", "house-of-commons")
        fptp = cs.key("gb.electoral_system", "fptp")
        if commons in cs.minted or fptp in cs.minted:
            raise ValueError("the House of Commons and its electoral system must already exist")
        parties: dict[str, UUID] = {}

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def party_of(label: str, year: int) -> UUID | None:
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
            key = key or umit.ALIASES.get(label) or slug(label)
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
            seats = on_day["constituency"].unique(maintain_order=True).to_list()
            cs.add("election", id=election, polity_id=gb, kind="by_election", date_from=day, date_to=day, status="held")
            name(
                election,
                f"{seats[0]} by-election, {spoken}" if len(seats) == 1 else f"UK parliamentary by-elections, {spoken}",
            )
            path = f"gb/{day}-by-election"
            cs.add("slug", path=path, entity_id=election)
            for (unit_code,), lines in on_day.group_by("unit", maintain_order=True):
                first = lines.row(0, named=True)
                general = cs.key("gb.contest", f"{first['general']}:{unit_code}")
                if general in cs.minted:
                    raise ValueError(f"no general election contest for {unit_code} on {first['general']}")
                (district,) = conn.execute("select district_area_id from contest where id = %s", [general]).fetchone()
                contest_key = f"{day}:{unit_code}"
                contest = cs.key(f"{SOURCE}.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=district,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} by-election in {first['constituency']}")
                cs.add("slug", path=f"{path}/{slug(first['constituency'])}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
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
                    option = cs.key(f"{SOURCE}.option", f"{contest_key}:{slug(line['candidate'])}")
                    person = (
                        cs.key("mnis", line["mnis"])
                        if line["mnis"]
                        else cs.key(f"{SOURCE}.person", f"{contest_key}:{slug(line['candidate'])}")
                    )
                    if person in cs.minted:
                        cs.add("person", id=person, gender=line["gender"].lower() if line["gender"] else None)
                        name(person, line["candidate"])
                    party = party_of(line["party"], day.year)
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
                        id=cs.key(f"{SOURCE}.candidacy", f"{contest_key}:{slug(line['candidate'])}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] == "Independent",
                    )
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
