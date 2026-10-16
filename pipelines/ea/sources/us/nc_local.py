import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.ncsbe import FILES, PUBLISHER, TITLE
from ea.sources.us.ncsbe import SOURCE as NCSBE
from ea.store import DATA, connect

SOURCE = "us.nc_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in range(2014, 2025, 2)]
COLUMNS = {
    "county": "county",
    "precinct": "precinct",
    "contest": "contest",
    "contest name": "contest",
    "choice": "choice",
    "party": "party",
    "choice party": "party",
    "total votes": "votes",
    "vote for": "vote_for",
}
OFFICES = {
    "board-of-commissioners-chair": (
        r"(?:COUNTY )?COMMISSION CHAIR(?:MAN)?|(?:BOARD OF )?COMMISSIONERS? CHAIR(?:MAN)?",
        "Chair of the Board of Commissioners",
    ),
    "board-of-commissioners": (
        r"(?:COUNTY )?(?:(?:BOARD|BD) OF ?(?:COUNTY )?COMMISS?I[O0]NERS?|COMMISS?I[O0]NERS?)",
        "Board of Commissioners",
    ),
    "board-of-education-chair": (
        r"(?:COUNTY )?SCHOOL BOARD CHAIR(?:MAN)?|BOARD OF EDUCATION CHAIR(?:MAN)?",
        "Chair of the Board of Education",
    ),
    "board-of-education": (
        r"(?:COUNTY[- ]?(?:WIDE )?)?(?:PUBLIC )?(?:SCHOOLS? )?(?:SYSTEM )?(?:(?:BOARD|BD) OF EDU\w*|SCHOOL BOARD)",
        "Board of Education",
    ),
    "soil-and-water": (
        r"(?:COUNTY )?SOIL (?:AND |& )?WATER CONSERVATI?ON DISTRICT SUPERVISO?R?S?",
        "Soil and Water Conservation District Board of Supervisors",
    ),
    "sheriff": (r"(?:COUNTY )?SHERIFF", "Sheriff"),
    "register-of-deeds": (r"(?:COUNTY )?REGISTER OF DEEDS", "Register of Deeds"),
    "clerk-of-superior-court": (r"(?:COUNTY )?CLERK OF (?:THE )?(?:SUPERIOR )?COURT", "Clerk of Superior Court"),
    "coroner": (r"(?:COUNTY )?CORONER", "Coroner"),
    "surveyor": (r"(?:COUNTY )?SURVEYOR", "Surveyor"),
}
BODIES = {"board-of-commissioners", "board-of-education", "soil-and-water"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRE": "green-party",
    "CST": "constitution-party",
}
METHOD = (
    "County totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file. The file does not mark winners, so the winners shown are the candidates with the most votes, as "
    "many as the contest elects."
)
DISTRICT = (
    "Only part of the county votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(NCSBE, FILES.format(day=day), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^A-Z]", "", text.upper()): (code, text)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    contests: list[dict[str, object]] = []
    results: list[pl.DataFrame] = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(NCSBE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        frame = frame.rename(
            {column: COLUMNS[column.strip().lower()] for column in frame.columns if column.strip().lower() in COLUMNS}
        )
        frame = frame.select(
            "county",
            "precinct",
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            pl.col("vote_for").cast(pl.Int16),
        ).filter(pl.col("choice").is_not_null())
        widths = dict(
            frame.group_by("county", "contest")
            .agg(pl.col("precinct").n_unique())
            .group_by("county")
            .agg(pl.col("precinct").max())
            .iter_rows()
        )
        for (county, contest), rows in frame.group_by("county", "contest"):
            fips, short = counties[re.sub(r"[^A-Z]", "", county.upper())]
            name = re.sub(r"\s*-?\s*\(?\bUNEX\w*\b.*$", "", contest)
            remainder = name != contest
            name = re.sub(rf"^{re.escape(short.upper())}( COUNTY)?( -)? ", "", name)
            office = next(
                (
                    (key, found)
                    for key, (pattern, _) in OFFICES.items()
                    if (found := re.fullmatch(rf"(?:{pattern})\b\s*-?\s*(.*)", name))
                ),
                None,
            )
            if office is None or re.search(r"REFERENDUM|STRUCTURE|METHOD", name) or office[1][1].startswith("OF "):
                continue
            seat = re.sub(r"\bTWP\b", "TOWNSHIP", re.sub(r"\bDIST\b", "DISTRICT", office[1][1]))
            seat = re.sub(r"\b0+(\d)", r"\1", seat).strip(" -")
            seat = re.sub(
                r"\b([A-Z]+)\b",
                lambda word: word[1] if re.fullmatch(r"[IVX]+|[A-Z]", word[1]) else word[1].capitalize(),
                seat,
            )
            seat = re.sub(r"^(Members?|Seats?)$", "", re.sub(r"(?i)^at[- ]large$", "At-Large", seat)) or None
            if seat in ("Chair", "Chairman") and f"{office[0]}-chair" in OFFICES:
                office, seat = (f"{office[0]}-chair", office[1]), None
            contests.append(
                {
                    "day": day,
                    "fips": fips,
                    "contest": contest,
                    "office": office[0],
                    "seat": seat,
                    "remainder": remainder,
                    "district": rows["precinct"].n_unique() < 0.9 * widths[county],
                    "magnitude": rows["vote_for"].max(),
                }
            )
            results.append(
                rows.group_by("choice", "party")
                .agg(pl.col("votes").sum())
                .with_columns(day=pl.lit(day), fips=pl.lit(fips), contest=pl.lit(contest))
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(contests)
    duplicated = frame.filter(pl.struct("day", "fips", "office", "seat", "remainder").is_duplicated())
    if duplicated.height:
        raise ValueError(f"contests that share a key: {duplicated.rows()[:10]}")
    frame.write_parquet(PARSED / "contests.parquet")
    pl.concat(results).write_parquet(PARSED / "results.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's county elections, 2014–2024",
            polity="US-NC",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        for day in DAYS:
            cs.artifact(raw.cached(NCSBE, FILES.format(day=day)), source)
        us = cs.polity("US")
        polities = {
            code: (polity, text, path)
            for code, polity, text, path in conn.execute(
                """select c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code = 'us.county' and c.code like '37%'"""
            ).fetchall()
        }
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        cs.add("electoral_system", id=systems["block"], polity_id=us, rules={"formula": "block"}, rules_version=1)
        name(systems["block"], "Block vote")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {systems["fptp"], *parties.values()}:
            raise ValueError("the fptp system and the parties must already exist")
        cs.add(
            "scheme",
            code="us.local_district",
            polity_id=us,
            kind="electoral_district",
            rank=3,
            noun="district",
        )

        owners: dict[tuple[str, str], UUID] = {}
        elections: dict[tuple[date, str], UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        for row in contests.sort("day", "fips", "office", "seat", nulls_last=True).iter_rows(named=True):
            day, fips, office = row["day"], row["fips"], row["office"]
            polity, county, path = polities[fips]
            label = OFFICES[office][1]
            title = f"{county.removesuffix(' County')} {label}" if office == "soil-and-water" else f"{county} {label}"
            if (fips, office) not in owners:
                owner = owners[(fips, office)] = cs.key(
                    "us.body" if office in BODIES else "us.office", f"NC:{fips}:{office}"
                )
                if office in BODIES:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="other")
                name(owner, title)
            if (day, fips) not in elections:
                election = elections[(day, fips)] = cs.key("us.election", f"{day}:NC:{fips}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{day}:NC"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {county} general election")
                cs.add("slug", path=f"{path}/{day}-general", entity_id=election)
            election = elections[(day, fips)]
            county_area = cs.area("us.county", fips)
            unit = cs.unit("us.county", fips)
            seat = row["seat"]
            if row["district"]:
                code = f"37:{fips}:{office}:{slug(seat or 'whole')}"
                area = cs.area("us.local_district", code)
                unit = cs.unit("us.local_district", code)
            if row["district"] and code not in districts:
                districts.add(code)
                cs.add("area", id=area, scheme_code="us.local_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=county_area, valid_from=None)
                name(area, f"{title}, {seat}" if seat else title)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="us.local_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
            contest_key = (
                f"{day}:NC:{fips}:{office}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            magnitude = row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if office in BODIES else "office",
                office_id=None if office in BODIES else owners[(fips, office)],
                body_id=owners[(fips, office)] if office in BODIES else None,
                district_area_id=area if row["district"] else county_area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if row["remainder"] else "full",
            )
            name(
                contest,
                f"{day.year} {title} election"
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            cs.add(
                "slug",
                path=f"{path}/{day}-general/{office}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else ""),
                entity_id=contest,
            )
            if row["district"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT)
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)

            lines = (
                results.filter(
                    (pl.col("day") == day) & (pl.col("fips") == fips) & (pl.col("contest") == row["contest"])
                )
                .with_columns(writein=pl.col("choice").str.contains(r"(?i)write-?\s*in"))
                .group_by(pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice").alias("choice"), "writein")
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            rank = 0
            for choice, writein, party, votes in lines.iter_rows():
                if writein:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    rank += 1
                    if (fips, slug(choice)) not in persons:
                        persons[(fips, slug(choice))] = cs.key(f"{SOURCE}.person", f"{fips}:{slug(choice)}")
                        cs.add("person", id=persons[(fips, slug(choice))])
                        name(persons[(fips, slug(choice))], choice)
                    person = persons[(fips, slug(choice))]
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=choice,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=choice,
                        is_independent=party == "UNA",
                    )
                    if rank <= magnitude:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
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
