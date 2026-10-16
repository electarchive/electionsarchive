import io
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.fl_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOWNLOADS = Path.home() / "Downloads" / "florida"
EXTRACT = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate={day:%-m/%-d/%Y}&OfficialResults=Y"
PUBLISHER = "Florida Division of Elections"
TITLE = "Election results extracts"
DAYS = [date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in range(1982, 2015, 2)]
FEDERAL = {"PRE": "president", "USS": "senate", "USR": "house", "GOV": "governor"}
OFFICES = {
    "SEC": ("Secretary of State", "secretary-of-state"),
    "ATG": ("Attorney General", "attorney-general"),
    "AGR": ("Commissioner of Agriculture", "commissioner-of-agriculture"),
    "CMP": ("Comptroller", "comptroller"),
    "TRE": ("Treasurer", "treasurer"),
    "EDU": ("Commissioner of Education", "commissioner-of-education"),
    "CFO": ("Chief Financial Officer", "chief-financial-officer"),
}
MISREPORTED = {"1988-11-08:FL:house:15"}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
NOTE = (
    "County results come from the Florida Division of Elections' official results extract. Races with a single "
    "qualified candidate did not appear on the ballot and have no county results."
)


def fetch() -> None:
    for day in DAYS:
        url = EXTRACT.format(day=day)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = DOWNLOADS / f"{day:%m%d%Y}Election.txt"
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".txt",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="text/tab-separated-values",
            )


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        }
    fips["dade"] = fips["miamidade"]
    frames = []
    for day in DAYS:
        frame = pl.read_csv(
            io.BytesIO(raw.cached(SOURCE, EXTRACT.format(day=day)).path.read_bytes().decode("cp1252").encode()),
            separator="\t",
            infer_schema_length=0,
            quote_char=None,
        ).filter(pl.col("RaceCode").is_in([*FEDERAL, *OFFICES]))
        ticket = pl.col("CanNameMiddle").fill_null("").str.strip_chars() == "/"
        clean = [
            pl.col(column).fill_null("").str.replace_all(",", "").str.replace_all(r"\s+", " ").str.strip_chars()
            for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
        ]
        frames.append(
            frame.select(
                day=pl.lit(day),
                race="RaceCode",
                district=pl.when(pl.col("RaceCode") == "USR").then(pl.col("Juris1num").cast(pl.Int16)),
                code=pl.col("CountyName").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips),
                party="PartyCode",
                candidate=pl.when(ticket)
                .then(clean[0])
                .otherwise(pl.concat_str(clean, separator=" ").str.replace_all(r"\s+", " ").str.strip_chars()),
                mate=pl.when(ticket).then(clean[2]),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
    counties = frame.group_by("day", "race", "district").agg(pl.col("code").n_unique())
    if counties.filter((pl.col("race") != "USR") & (pl.col("code") != 67)).height:
        raise ValueError(f"statewide races missing counties: {counties.filter(pl.col('code') != 67)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's federal and statewide general elections by county, 1982–2014",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.elections.myflorida.com/",
            license="Public record",
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, EXTRACT.format(day=day)), source)

        filled = {
            key
            for (key,) in conn.execute(
                """select distinct e.value from external_id e
                   join result_set rs on rs.contest_id = e.entity_id and not rs.is_default
                   where e.scheme = 'us.contest' and e.value ~ '^(19|20)[0-9]{2}-11-..:FL:(president|senate|governor|house:[0-9]+)$'"""
            ).fetchall()
        }
        federal = (
            frame.filter(pl.col("race").is_in(list(FEDERAL)))
            .with_columns(chamber=pl.col("race").replace_strict(FEDERAL))
            .with_columns(
                key=pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit(":FL:"),
                    "chamber",
                    pl.when(pl.col("district").is_not_null())
                    .then(pl.lit(":") + pl.col("district").cast(pl.String))
                    .otherwise(pl.lit("")),
                )
            )
            .filter(~pl.col("key").is_in([*filled, *MISREPORTED]))
            .select(
                year=pl.col("day").dt.year(),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state=pl.lit("FL"),
                district="district",
                code="code",
                candidate=pl.col("candidate").str.to_uppercase(),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("party") == "WRI",
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("FL"),
            )
        )
        medsl.attach(cs, conn, federal, {"FL": source})

        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
            ).fetchall()
        )
        (state_area,) = conn.execute(
            "select area_id from reporting_unit where scheme_code = 'us.state' and code = '12'"
        ).fetchone() or (None,)
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        contests_known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:FL:%%'"
            )
        }
        people: dict[str, set[UUID]] = defaultdict(set)
        for person, ballot in conn.execute(
            """select distinct cd.person_id, cd.ballot_name from candidacy cd
               join contest_option o on o.id = cd.option_id join contest ct on ct.id = o.contest_id
               join election e on e.id = ct.election_id join polity p on p.id = e.polity_id
               where p.iso_code = 'US-FL' and cd.ballot_name is not null"""
        ).fetchall():
            words = [
                word
                for word in slug(re.sub(r'"[^"]*"|\([^)]*\)', "", ballot.replace(",", " "))).split("-")
                if word not in ("jr", "sr", "ii", "iii") and len(word) > 1
            ]
            if len(words) > 1:
                people[f"{words[0]} {words[-1]}"].add(person)
                people[f"{words[-1]} {words[0]}"].add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        persons: dict[str, UUID] = {}
        for (day, race), lines in frame.filter(pl.col("race").is_in(list(OFFICES))).group_by(
            "day", "race", maintain_order=True
        ):
            assert isinstance(day, date)
            office, office_slug = OFFICES[str(race)]
            election = cs.key("us.election", f"{day}:FL")
            if election in cs.minted:
                raise ValueError(f"{day}: the election must already exist")
            office_key = f"{office_slug}:FL"
            office_id = cs.key("us.office", office_key)
            if office_key not in offices_known:
                offices_known.add(office_key)
                cs.add("office", id=office_id, polity_id=cs.polity("US-FL"), kind="other")
                name(office_id, f"Florida {office}")
            contest_key = f"{day}:FL:{office_slug}"
            if contest_key in contests_known:
                raise ValueError(f"{contest_key} already exists")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=state_area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Florida {office} election")
            cs.add("slug", path=f"us/fl/{day}-general/{office_slug}", entity_id=contest)
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
            cs.add("note", entity_id=contest, kind="caveat", body_md=NOTE)
            chosen: dict[tuple[str, str], str] = {}
            for candidate, label in lines.select("candidate", "party").unique(maintain_order=True).iter_rows():
                ballot = str(candidate)
                if label == "WRI":
                    option = cs.key("us.option", f"{contest_key}:write_in:fl_sos")
                    if str(option) not in chosen.values():
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                    chosen[(ballot, label)] = str(option)
                    continue
                words = [
                    word
                    for word in slug(re.sub(r"'[^']*'|\([^)]*\)", "", ballot)).split("-")
                    if word not in ("jr", "sr", "ii", "iii") and len(word) > 1
                ]
                found = people.get(f"{words[0]} {words[-1]}", set())
                if ballot not in persons:
                    if len(found) == 1:
                        persons[ballot] = next(iter(found))
                    else:
                        persons[ballot] = cs.key(f"{SOURCE}.person", slug(ballot))
                        if persons[ballot] in cs.minted:
                            cs.add("person", id=persons[ballot])
                            name(persons[ballot], ballot)
                option = cs.key("us.option", f"{contest_key}:{persons[ballot]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[label]) if label in PARTIES else None,
                    ballot_label=ballot,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{persons[ballot]}:head"),
                    person_id=persons[ballot],
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=label == "NPA",
                )
                chosen[(ballot, label)] = str(option)
            summed = (
                lines.with_columns(
                    option=pl.concat_str("candidate", pl.lit("\x1f"), "party").replace_strict(
                        {f"{candidate}\x1f{label}": option for (candidate, label), option in chosen.items()}
                    )
                )
                .group_by("option", "code", maintain_order=True)
                .agg(pl.col("votes").sum())
            )
            for option, code, votes in summed.iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[str(code)],
                    option_id=UUID(option),
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            totals = summed.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
            cs.add("outcome", result_set_id=result_set, option_id=UUID(totals["option"][0]), kind="elected", seats=1)
        return cs.write()
