import io
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.ncsbe import COLUMNS, FILES, HEADERLESS, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.state_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
NC_DAYS = {
    date(2002, 9, 10): None,
    date(2004, 7, 20): None,
    date(2004, 8, 17): date(2004, 7, 20),
    date(2006, 5, 2): None,
    date(2006, 5, 30): date(2006, 5, 2),
    date(2008, 5, 6): None,
    date(2010, 5, 4): None,
    date(2010, 6, 22): date(2010, 5, 4),
    date(2012, 5, 8): None,
    date(2012, 7, 17): date(2012, 5, 8),
    date(2014, 5, 6): None,
    date(2014, 7, 15): date(2014, 5, 6),
    date(2016, 3, 15): None,
    date(2018, 5, 8): None,
    date(2020, 3, 3): None,
    date(2020, 6, 23): date(2020, 3, 3),
    date(2022, 5, 17): None,
}
OFFICES = {
    "GOVERNOR": ("governor", "gubernatorial"),
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "Lieutenant Governor"),
    "ATTORNEY GENERAL": ("attorney-general", "Attorney General"),
    "SECRETARY OF STATE": ("secretary-of-state", "Secretary of State"),
    "AUDITOR": ("auditor", "State Auditor"),
    "TREASURER": ("treasurer", "State Treasurer"),
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": (
        "superintendent-of-public-instruction",
        "Superintendent of Public Instruction",
    ),
    "COMMISSIONER OF AGRICULTURE": ("commissioner-of-agriculture", "Commissioner of Agriculture"),
    "COMMISSIONER OF INSURANCE": ("commissioner-of-insurance", "Commissioner of Insurance"),
    "COMMISSIONER OF LABOR": ("commissioner-of-labor", "Commissioner of Labor"),
}
LEGISLATIVE = re.compile(r"NC (?:STATE )?(HOUSE|SENATE)(?: OF REPRESENTATIVES)?(?: DISTRICT 0*(\d+)| \(0*(\d+)\))")
STATEWIDE = re.compile(rf"(?:NC )?(?:STATE )?({'|'.join(OFFICES)})")
SUFFIX = re.compile(r"\s*(?:- |\()(DEM|REP|LIB|GRE|CST)\)?$")
PSEUDO = re.compile(r"ABSENTEE.*|PROV.*|TRANSFERS?|CURBSIDE|ONE ?STOP.*|ONESTOP.*|OS|OS[ _-].*")
PARTIES = {
    "DEM": ("democratic-party", "democratic", "dp", "Democratic"),
    "REP": ("republican-party", "republican", "rp", "Republican"),
    "LIB": ("libertarian-party", "libertarian", "liberp", "Libertarian"),
    "GRE": ("green-party", "green", "greenp", "Green"),
    "CST": ("constitution-party", "constitution", "constp", "Constitution"),
}
UNALLOCATED = 32
METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file; votes the file reports only county-wide are kept at the county level. The file does not mark "
    "winners, so the winner shown is the leading candidate when that candidate passed the {threshold} percent a "
    "primary needs, or when no runoff followed; when a runoff followed, the top two are shown as advancing."
)


def fetch() -> None:
    for day in NC_DAYS:
        raw.fetch(SOURCE, FILES.format(day=day), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    frames = []
    for day in NC_DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        headed = first.lower().lstrip(b'"').startswith(b"county")
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            has_header=headed,
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        if not headed:
            frame.columns = (
                HEADERLESS
                if frame.width == 9
                else [
                    "county",
                    "date",
                    "precinct",
                    "contest",
                    "choice",
                    "party",
                    "eday",
                    "absentee",
                    "provisional",
                    "votes",
                ]
            )
        frame = frame.rename(
            {
                column: {"party_cd": "party", "choice party": "party", "precinct": "precinct"}.get(
                    column.strip().lower(), COLUMNS.get(column.strip().lower(), column)
                )
                for column in frame.columns
            }
        )
        frame = frame.select(
            pl.col("county").str.strip_chars(),
            pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " "),
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars().str.to_uppercase(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        ).filter(pl.col("choice").is_not_null(), pl.col("votes").is_not_null())
        labels = []
        for contest in frame["contest"].unique():
            suffix = SUFFIX.search(contest)
            bare = SUFFIX.sub("", contest)
            if found := LEGISLATIVE.fullmatch(bare):
                office, code = found[1].lower(), str(int(found[2] or found[3]))
            elif found := STATEWIDE.fullmatch(bare):
                office, code = OFFICES[found[1]][0], None
            else:
                continue
            labels.append({"contest": contest, "office": office, "code": code, "named": suffix[1] if suffix else None})
        if not labels:
            continue
        frame = frame.join(pl.DataFrame(labels, schema_overrides={"code": pl.String, "named": pl.String}), on="contest")
        writein = pl.col("choice").str.contains(r"(?i)write-?\s*in")
        frame = frame.with_columns(
            party=pl.coalesce("named", pl.when(pl.col("party").is_in(list(PARTIES))).then("party")),
            writein=writein,
        )
        sole = frame.filter(pl.col("party").is_not_null()).group_by("contest").agg(sole=pl.col("party").unique())
        frame = (
            frame.join(sole, on="contest", how="left")
            .with_columns(
                party=pl.coalesce(
                    "party",
                    pl.when(pl.col("writein") & (pl.col("sole").list.len() == 1)).then(pl.col("sole").list.first()),
                )
            )
            .drop("sole")
        )
        lost = frame.filter(pl.col("party").is_null())
        if lost.height:
            print(
                day, "rows without a party:", lost.group_by("contest", "choice").agg(pl.col("votes").sum()).rows()[:10]
            )
        frames.append(
            frame.filter(pl.col("party").is_not_null()).select(
                day=pl.lit(day),
                office="office",
                code="code",
                party="party",
                fips=pl.col("county").map_elements(
                    lambda county: counties[re.sub(r"[^a-z]", "", county.lower())], return_dtype=pl.String
                ),
                county=pl.col("county").str.to_uppercase(),
                precinct=pl.when(pl.col("precinct").str.to_uppercase().str.contains(f"^(?:{PSEUDO.pattern})$"))
                .then(None)
                .otherwise("precinct"),
                choice=pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice"),
                writein="writein",
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames)
        .group_by("day", "office", "code", "party", "fips", "county", "precinct", "choice", "writein")
        .agg(pl.col("votes").sum())
        .sort("day", "office", "code", "party", "fips", "precinct", "choice")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's state legislative and Council of State primaries and runoffs by precinct, 2002–2022",
            polity="US-NC",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        for day in NC_DAYS:
            cs.artifact(raw.cached(SOURCE, FILES.format(day=day)), source)
        state = cs.polity("US-NC")
        keys = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '20%%:NC:%%'"
            ).fetchall()
        }
        slugs = {path for (path,) in conn.execute("select path from slug where path like 'us/nc/%%'").fetchall()}
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '37%%'"
            ).fetchall()
        }
        nominees: dict[tuple[int, str, str | None, UUID, str, str], set[UUID]] = {}
        for value, ballot, person, party in conn.execute(
            """select e.value, coalesce(d.ballot_name, n.text, o.ballot_label), d.person_id, o.party_id
               from external_id e
               join contest_option o on o.contest_id = e.entity_id
               join candidacy d on d.option_id = o.id
               left join name n on n.entity_id = d.person_id and n.is_preferred and n.lang = 'en'
               where e.scheme = 'us.contest' and e.value ~ '^20[0-2][0-9]-11-[0-9]{2}:NC:[a-z-]+(:[0-9]+:g)?$'
                 and o.party_id is not null"""
        ).fetchall():
            parts = value.split(":")
            words = [
                word
                for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", ballot).lower().split()
                if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if words:
                signature = (
                    int(parts[0][:4]),
                    parts[2],
                    parts[3] if len(parts) > 3 else None,
                    party,
                    words[0][0],
                    slug(words[-1]),
                )
                nominees.setdefault(signature, set()).add(person)
        parties = {code: cs.key("us.party", key) for code, (key, *_) in PARTIES.items()}
        systems = {key: cs.key("us.electoral_system", key) for key in ("two_round_40", "two_round_30")}
        offices = {office: cs.key("us.office", f"{office}:NC") for office, _ in OFFICES.values()}
        bodies = {office: cs.key("us.body", f"NC:{office}") for office in ("house", "senate")}
        if cs.minted & {*parties.values(), *systems.values(), *offices.values(), *bodies.values()}:
            raise ValueError("the parties, systems, offices, and bodies must already exist")
        titles = dict(OFFICES.values())
        elections: dict[date, UUID] = {}
        for day, first in NC_DAYS.items():
            key = f"{day}:NC:{'runoff' if first else 'primary'}"
            election = elections[day] = cs.key("us.election", key)
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=state,
                    kind="runoff" if first else "primary",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, {day.year} North Carolina primary {'runoff ' if first else ''}election",
                )
                cs.add("slug", path=f"us/nc/{day}-{'runoff' if first else 'primary'}", entity_id=election)
        later = {(first, day) for day, first in NC_DAYS.items() if first}
        contests = {
            keys_: rows.sort("votes", descending=True)
            for keys_, rows in lines.group_by("day", "office", "code", "party", "choice", "writein")
            .agg(pl.col("votes").sum())
            .group_by("day", "office", "code", "party")
        }
        held = {(day, office, code, party) for day, office, code, party in contests}
        persons: dict[str, UUID] = {}
        options: dict[tuple[date, str, str | None, str, str], UUID] = {}
        sets: dict[tuple[date, str, str | None, str], UUID] = {}
        made: dict[tuple[date, str, str | None, str], UUID] = {}
        skipped = 0
        for (day, office, code, party), rows in sorted(contests.items(), key=lambda item: item[0][0]):
            assert isinstance(day, date) and isinstance(office, str) and isinstance(party, str)
            assert code is None or isinstance(code, str)
            first = NC_DAYS[day]
            _, word, etype, label = PARTIES[party]
            if code is not None:
                base = f"{day}:NC:{office}:{code}:{etype}" + ("runoff" if first else "")
                taken = re.compile(rf"{re.escape(base)}(f\w*)?")
            else:
                base = f"{day}:NC:{office}:{word}"
                taken = re.compile(re.escape(base))
            if any(taken.fullmatch(value) for value in keys):
                skipped += 1
                continue
            contest = made[(day, office, code, party)] = cs.key("us.contest", base)
            previous = None
            if first is not None:
                previous = made.get((first, office, code, party))
                if previous is None:
                    prior = f"{first}:NC:{office}:{code}:{etype}" if code is not None else f"{first}:NC:{office}:{word}"
                    previous = next(
                        (
                            cs.key("us.contest", value)
                            for value in sorted(keys)
                            if re.fullmatch(rf"{re.escape(prior)}(f\w*)?", value)
                        ),
                        None,
                    )
            kind = "runoff" if first else "primary"
            stage = f"{label} primary" + (" runoff" if first else "")
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="office" if code is None else "body_seats",
                office_id=offices[office] if code is None else None,
                body_id=bodies[office] if code is not None else None,
                district_area_id=cs.area("us.state", "37")
                if code is None
                else cs.area("us.sldl" if office == "house" else "us.sldu", f"NC:{code}"),
                magnitude=1,
                term="full",
                round_number=2 if first else 1,
                previous_round_id=previous,
                electoral_system_id=systems["two_round_40" if day.year < 2018 else "two_round_30"],
                default_result_kind="counted",
                party_scope_id=parties[party],
            )
            if code is None:
                name(contest, f"{day.year} North Carolina {titles[office]} {stage}")
                path = f"us/nc/{day}-{kind}/{office}-{word}"
            else:
                chamber = "House of Representatives" if office == "house" else "Senate"
                name(contest, f"{day.year} North Carolina {chamber} {stage}, District {code}")
                path = f"us/nc/{day}-{kind}/state-{office}-{code}-{slug(stage)}"
            if path in slugs:
                raise ValueError(f"slug {path} is taken")
            slugs.add(path)
            cs.add("slug", path=path, entity_id=contest)
            result_set = sets[(day, office, code, party)] = cs.key(f"{SOURCE}.result_set", f"{base}:precinct")
            threshold = 40 if day.year < 2018 else 30
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[day],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(threshold=threshold))
            total = rows["votes"].sum()
            ranked = rows.filter(~pl.col("writein"))
            matched: list[bool] = []
            for choice, writein, votes in rows.select("choice", "writein", "votes").iter_rows():
                if writein:
                    option = cs.key("us.option", f"{base}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    options[(day, office, code, party, choice)] = option
                    continue
                words = [
                    word
                    for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", choice).lower().split()
                    if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
                ]
                found = nominees.get((day.year, office, code, parties[party], words[0][0], slug(words[-1])), set())
                local = f"NC:{office}:{code or ''}:{party}:{slug(choice)}"
                if len(found) == 1:
                    person = next(iter(found))
                else:
                    person = persons.get(local) or cs.key(f"{SOURCE}.person", local)
                    if person in cs.minted and local not in persons:
                        cs.add("person", id=person)
                        name(person, choice)
                persons[local] = person
                matched.append(len(found) == 1)
                option = cs.key("us.option", f"{base}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties[party],
                    ballot_label=choice,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{base}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=choice,
                    is_independent=False,
                )
                options[(day, office, code, party, choice)] = option
            votes = ranked["votes"].to_list()
            if not votes or (len(votes) > 1 and votes[0] == votes[1]):
                continue
            leader = options[(day, office, code, party, ranked["choice"][0])]
            runoff = any(
                (day, later_day) in later and (later_day, office, code, party) in held for later_day in NC_DAYS
            ) or any(
                re.fullmatch(
                    rf"{later_day}:NC:{office}:" + (f"{code}:{etype}runoff" if code is not None else word),
                    value,
                )
                for later_day in NC_DAYS
                if (day, later_day) in later
                for value in keys
            )
            if first is None and runoff and len(votes) > 1:
                for choice in ranked["choice"][:2]:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[(day, office, code, party, choice)],
                        kind="advanced",
                    )
            elif first is not None or len(votes) == 1 or votes[0] * 100 > threshold * total or matched[0]:
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="won")
        print(f"{len(made)} contests, {skipped} already held")
        frame = lines.join(
            pl.DataFrame(
                [
                    (day, office, code, party, choice, str(option), str(sets[(day, office, code, party)]))
                    for (day, office, code, party, choice), option in options.items()
                ],
                schema={
                    "day": pl.Date,
                    "office": pl.String,
                    "code": pl.String,
                    "party": pl.String,
                    "choice": pl.String,
                    "option_id": pl.String,
                    "result_set_id": pl.String,
                },
                orient="row",
            ),
            on=["day", "office", "code", "party", "choice"],
            nulls_equal=True,
        ).with_columns(
            unit=pl.when(pl.col("precinct").is_not_null()).then(
                pl.concat_str(pl.col("day").cast(pl.String), pl.lit("NC"), "fips", "county", "precinct", separator=":")
            )
        )
        units: dict[str, UUID] = {}
        for day, fips, county, unit in frame.select("day", "fips", "county", "unit").unique().iter_rows():
            if unit is None:
                unit = f"NC:{fips}:county-wide"
                if unit not in units:
                    units[unit] = cs.unit("us.aggregate", unit)
                    if units[unit] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=units[unit],
                            polity_id=state,
                            scheme_code="us.aggregate",
                            code=unit,
                            parent_id=counties[fips][0],
                            area_relation="within",
                            area_id=counties[fips][1],
                            kind="aggregate",
                            valid_from=None,
                        )
                continue
            units[unit] = cs.unit("us.precinct", unit)
            if units[unit] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[unit],
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=unit,
                    parent_id=counties[fips][0],
                    area_relation="within",
                    area_id=counties[fips][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        cs.frame(
            "result_value",
            frame.with_columns(
                flags=pl.when(pl.col("unit").is_null()).then(UNALLOCATED).otherwise(0).cast(pl.Int16),
                unit=pl.coalesce("unit", pl.concat_str(pl.lit("NC"), "fips", pl.lit("county-wide"), separator=":")),
            )
            .with_columns(unit_id=pl.col("unit").replace_strict({key: str(value) for key, value in units.items()}))
            .group_by("result_set_id", "unit_id", "option_id", "flags")
            .agg(value=pl.col("votes").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
