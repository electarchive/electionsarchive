import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.dc_boe"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://electionresults.dcboe.org"
INFO = HOST + "/electionResults/getElectionInfo/{year}-General-Election"
CITYWIDE = HOST + "/cityWide/getCityWide/{year}-General-Election"
YEARS = range(2012, 2025, 2)
PUBLISHER = "District of Columbia Board of Elections"
TITLE = "Election results"
WARDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8}
CONTESTS = [
    (r"MAYOR OF THE DISTRICT OF COLUMBIA.*", "mayor"),
    (r"CHAIRMAN OF THE COUNCIL.*", "chair"),
    (r"AT ?- ?LARGE MEMBER OF THE COUNCIL.*", "at-large"),
    (r"MEMBER OF THE COUNCIL WARD (\w+)|WARD (\w+) +MEMBER OF THE COUNCIL.*", "ward"),
    (r"ATTORNEY GENERAL.*", "Attorney General"),
    (r"AT ?- ?LARGE MEMBER (?:OF )?(?:THE )?STATE BOARD.*", "At-Large Member of the State Board of Education"),
    (
        r"MEMBER OF THE STATE BOARD OF EDUCATION WARD (\w+)|WARD (\w+) +MEMBER (?:OF THE )?STATE BOARD.*",
        "Member of the State Board of Education",
    ),
    (r"UNITED STATES SENATOR.*", "Shadow United States Senator"),
    (r"UNITED STATES REPRESENTATIVE.*", "Shadow United States Representative"),
]
CODES = r"^(DEM|REP|LIB|STG|IND|SWP|NPN) "
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "STG": "DC:d-c-statehood-green-party",
    "SWP": "socialist-workers-party",
}
PLACE = "1150000"
METHOD = (
    "Totals are the sums of the precinct rows in the District of Columbia Board of Elections' certified results file "
    "and match the citywide totals published by the Board."
)
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def day(year: int) -> date:
    return date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)


def csv(year: int) -> str:
    return json.loads(raw.cached(SOURCE, INFO.format(year=year)).path.read_text())["CsvLink"]


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, INFO.format(year=year), suffix=".json")
        raw.fetch(SOURCE, csv(year), suffix=".csv")
        raw.fetch(SOURCE, CITYWIDE.format(year=year), suffix=".json")


def parse() -> None:
    frames = []
    for year in YEARS:
        frame = pl.read_csv(raw.cached(SOURCE, csv(year)).path, infer_schema=False)
        frame = frame.rename(
            {column: re.sub(r"[^a-z]", "", column.lower()).removesuffix("number") for column in frame.columns}
        ).with_columns(pl.col(pl.String).str.strip_chars())
        published = {
            re.sub(r"\s+", " ", contest["OfficeName"].strip()): {
                entry["Contestant"].strip(): entry["Votes"] for entry in contest["ElectionData"]
            }
            for contest in json.loads(raw.cached(SOURCE, CITYWIDE.format(year=year)).path.read_text())
        }
        rows = []
        for (contest,), lines in frame.with_columns(
            contestname=pl.col("contestname").str.replace_all(r"\s+", " ")
        ).group_by("contestname", maintain_order=True):
            for pattern, office in CONTESTS:
                found = re.fullmatch(pattern, contest)
                if found:
                    break
            else:
                continue
            sums = dict(
                lines.filter(pl.col("precinct").str.contains(r"^\d+$"))
                .group_by("candidate")
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .iter_rows()
            )
            if {name: votes for name, votes in sums.items() if not re.search("OVER|UNDER", name)} != {
                name: votes for name, votes in published[contest].items() if not re.search("OVER|UNDER", name)
            }:
                raise ValueError(f"{year} {contest}: precinct sums differ from the citywide totals")
            ward = next((group for group in found.groups() if group), None)
            for precinct, number, candidate, party, votes in (
                lines.filter(pl.col("precinct").str.contains(r"^\d+$"), ~pl.col("candidate").str.contains("OVER|UNDER"))
                .select("precinct", "ward", "candidate", "party", "votes")
                .iter_rows()
            ):
                code = re.match(CODES, candidate)
                name = re.sub(CODES, "", candidate).strip()
                if sum(letter.islower() for letter in name) * 2 < sum(letter.isupper() for letter in name):
                    name = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), name.title())
                    name = re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), name)
                rows.append(
                    (
                        year,
                        office,
                        WARDS.get(ward or "") if ward else None,
                        name,
                        code[1] if code else party,
                        candidate.upper() == "WRITE-IN",
                        int(precinct),
                        int(number),
                        int(votes),
                    )
                )
        frames.append(
            pl.DataFrame(
                rows,
                schema={
                    "year": pl.Int32,
                    "office": pl.String,
                    "seat": pl.Int32,
                    "candidate": pl.String,
                    "party": pl.String,
                    "writein": pl.Boolean,
                    "precinct": pl.Int32,
                    "ward": pl.Int32,
                    "votes": pl.Int64,
                },
                orient="row",
            )
        )
    frame = pl.concat(frames)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "office", "seat", maintain_order=True)
        .agg(pl.col("candidate").n_unique(), pl.col("precinct").n_unique(), pl.col("votes").sum())
        .rows()
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The District of Columbia's mayor, council, attorney general, shadow delegation, and State Board of "
            "Education 2012–2024 by precinct from the Board of Elections' certified results",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError(f"no {PUBLISHER} source")
        for year in YEARS:
            for url in (INFO.format(year=year), csv(year), CITYWIDE.format(year=year)):
                cs.artifact(raw.cached(SOURCE, url), source)
        keys = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest c on c.id = x.entity_id
                   join changeset s on s.id = x.changeset_id where x.scheme = 'us.contest' and s.pipeline <> %s""",
                [SOURCE],
            )
        }
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        ((county, county_area),) = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = '11001'"
        ).fetchall()
        ((state_area,),) = conn.execute(
            "select area_id from reporting_unit where scheme_code = 'us.state' and code = '11'"
        ).fetchall()
        city = cs.area("us.place", PLACE)
        council = cs.key("us.body", f"DC:{PLACE}:council")
        mayor = cs.key("us.office", f"DC:{PLACE}:mayor")
        ((polity,),) = conn.execute("select polity_id from body where id = %s", [council]).fetchall()
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        sets = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        values: dict[tuple[str, str, str], int] = defaultdict(int)
        minted: dict[str, date] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        districts: set[str] = set()
        minted_districts: set[str] = set()
        persons: set[str] = set()
        for (year,), rows in lines.group_by("year", maintain_order=True):
            when = day(year)
            units: dict[int, str] = {}
            for code in sorted(
                (
                    code
                    for (code,) in conn.execute(
                        """select code from reporting_unit where scheme_code = 'us.precinct'
                           and code like %s and valid_from = %s""",
                        [f"{year}:DC:%", when],
                    ).fetchall()
                ),
                key=len,
                reverse=True,
            ):
                units[int(code.rsplit(":", 1)[1])] = code
            for number in rows["precinct"].unique():
                if number not in units:
                    units[number] = f"{year}:DC:{number}"
                    minted[units[number]] = when
            local = conn.execute(
                """select c.id, c.magnitude, (select text from name where entity_id = c.id and is_preferred limit 1),
                          (select value from external_id where entity_id = c.id and scheme = 'us.contest' limit 1)
                   from contest c join external_id x on x.entity_id = c.election_id
                   where x.scheme = 'us.election' and x.value = %s
                     and not exists (select from external_id k join changeset s on s.id = k.changeset_id
                                     where k.entity_id = c.id and k.scheme = 'us.contest' and s.pipeline = %s)""",
                [f"{when}:DC:{PLACE}", SOURCE],
            ).fetchall()
            for (office, seat), cells in rows.group_by("office", "seat", maintain_order=True):
                municipal = office in ("mayor", "chair", "at-large", "ward")
                magnitude = 2 if office == "at-large" else 1
                if municipal:
                    label = {"mayor": "mayoral", "chair": "At-Large Chair", "at-large": "At-Large"}.get(
                        office, f"At-Large {seat}"
                    )
                    found = [
                        (contest, key)
                        for contest, size, text, key in local
                        if (text.endswith("mayoral election") if office == "mayor" else text.endswith(f", {label}"))
                        and (office == "mayor" or size == magnitude)
                    ]
                    shown = {"chair": "Chair", "at-large": "At-Large"}.get(office, f"Ward {seat}")
                    contest_key = f"{when}:DC:{PLACE}:" + ("mayor" if office == "mayor" else f"council:{slug(shown)}")
                    if found:
                        contest, contest_key = found[0]
                else:
                    shown = f"Seat {seat}" if seat else ""
                    contest_key = f"{when}:DC:{slug(office)}" + (f":{slug(shown)}" if shown else "")
                    contest = cs.key("us.contest", contest_key)
                    found = [(contest, contest_key)] if contest_key in keys else []
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:precinct")
                election = cs.key("us.election", f"{when}:DC")
                if election in cs.minted:
                    raise ValueError(f"no election for {contest_key}")
                options: dict[str, UUID] = {}
                if found:
                    existing = conn.execute(
                        """select o.id, o.kind, o.ballot_label from contest_option o
                           where o.contest_id = %s and o.parent_option_id is null""",
                        [contest],
                    ).fetchall()
                    for row in conn.execute(
                        """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                                  supersedes_id from result_set where contest_id = %s and is_default""",
                        [contest],
                    ).fetchall():
                        if f"{contest_key}:precinct" in sets:
                            continue
                        cs.add(
                            "result_set",
                            **dict(
                                zip(
                                    (
                                        "id",
                                        "election_id",
                                        "contest_id",
                                        "stage",
                                        "kind",
                                        "status",
                                        "source_id",
                                        "as_of",
                                        "completeness",
                                        "supersedes_id",
                                    ),
                                    row,
                                    strict=True,
                                )
                            ),
                            is_default=False,
                        )

                    def plain(text: str) -> str:
                        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()

                    def surname(text: str) -> str:
                        words = [
                            word
                            for word in re.sub(r"[^a-z ]", "", re.sub(r'"[^"]*"|\([^)]*\)', " ", plain(text))).split()
                            if word not in {"jr", "sr", "ii", "iii", "iv"}
                        ]
                        return words[-1] if words else ""

                    for candidate in cells.filter(~pl.col("writein"))["candidate"].unique(maintain_order=True):
                        hits = [
                            option
                            for option, kind, text in existing
                            if kind != "write_in"
                            and surname(text) == surname(candidate)
                            and (
                                len(text.split()) == 1
                                or re.sub(r"[^a-z]", "", plain(text))[:1] == re.sub(r"[^a-z]", "", plain(candidate))[:1]
                            )
                        ]
                        if len(hits) > 1:
                            raise ValueError(f"{contest_key}: {len(hits)} options for {candidate}")
                        if hits:
                            options[candidate] = hits[0]
                    pooled = [option for option, kind, _ in existing if kind == "write_in"]
                    if pooled:
                        options["Write-in"] = pooled[0]
                    unmatched = [option for option, kind, _ in existing if option not in options.values()]
                    if unmatched:
                        print(f"{contest_key}: {len(unmatched)} options on file have no Board row")
                else:
                    if contest_key in keys:
                        raise ValueError(f"{contest_key} is taken")
                    contest = cs.key("us.contest", contest_key)
                    area = state_area
                    if office == "ward" or (not municipal and seat):
                        ward = f"Ward {seat}"
                        district = f"DC:{PLACE}:council:{slug(ward)}"
                        area = cs.area("us.local_district", district)
                        if district not in districts:
                            districts.add(district)
                            name(area, f"{ward}, District of Columbia")
                        if area in cs.minted and district not in minted_districts:
                            minted_districts.add(district)
                            cs.add("area", id=area, scheme_code="us.local_district")
                            cs.add("area_code", area_id=area, code=district, valid_from=None)
                            cs.add("area_parent", area_id=area, parent_id=city, valid_from=None)
                            cs.add(
                                "reporting_unit",
                                id=cs.unit("us.local_district", district),
                                polity_id=polity,
                                scheme_code="us.local_district",
                                code=district,
                                area_relation="equals",
                                area_id=area,
                                kind="standard",
                            )
                        for number in cells["precinct"].unique():
                            memberships.add((cs.unit("us.precinct", units[number]), area, when))
                    elif municipal:
                        area = city
                    office_key = f"{slug(office)}:DC"
                    if not municipal and office_key not in offices:
                        offices.add(office_key)
                        cs.add("office", id=cs.key("us.office", office_key), polity_id=cs.polity("US-DC"), kind="other")
                        name(cs.key("us.office", office_key), f"{office} of the District of Columbia")
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office" if office == "mayor" or not municipal else "body_seats",
                        office_id=mayor
                        if office == "mayor"
                        else None
                        if municipal
                        else cs.key("us.office", office_key),
                        body_id=council if municipal and office != "mayor" else None,
                        district_area_id=area,
                        term="full",
                        magnitude=magnitude,
                        round_number=1,
                        electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                        default_result_kind="counted",
                    )
                    if municipal:
                        name(contest, f"{year} Council of the District of Columbia election, {shown}")
                        path = f"us/dc/{when}-general/council-{slug(shown)}"
                    else:
                        name(
                            contest,
                            f"{year} District of Columbia {office} election" + (f" (Ward {seat})" if seat else ""),
                        )
                        path = f"us/dc/{when}-general/{slug(office)}" + (f"-{slug(shown)}" if shown else "")
                    cs.add("slug", path=path, entity_id=contest)
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
                totals: list[tuple[int, UUID]] = []
                for (candidate, writein), group in (
                    cells.group_by("candidate", "writein")
                    .agg(pl.col("party").drop_nulls().first(), pl.col("votes").sum())
                    .sort("votes", descending=True)
                    .group_by("candidate", "writein", maintain_order=True)
                ):
                    if writein:
                        candidate = "Write-in"
                    if candidate not in options:
                        option_key = f"{contest_key}:" + ("write-ins" if writein else slug(candidate))
                        options[candidate] = cs.key("us.option", option_key)
                        code = group["party"][0]
                        party = cs.key("us.party", PARTIES[code]) if code in PARTIES else None
                        cs.add(
                            "contest_option",
                            id=options[candidate],
                            contest_id=contest,
                            kind="write_in" if writein else "candidate",
                            party_id=party,
                            ballot_label="Write-ins" if writein else candidate,
                        )
                        if not writein:
                            person = cs.key(f"{SOURCE}.person", f"DC:{slug(candidate)}")
                            if person in cs.minted and slug(candidate) not in persons:
                                persons.add(slug(candidate))
                                cs.add("person", id=person)
                                name(person, candidate)
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{option_key}:head"),
                                person_id=person,
                                option_id=options[candidate],
                                role="head",
                                ballot_name=candidate,
                                is_independent=code == "IND",
                            )
                    if not writein:
                        totals.append((int(group["votes"].sum()), options[candidate]))
                for candidate, writein, number, votes in cells.select(
                    "candidate", "writein", "precinct", "votes"
                ).iter_rows():
                    if votes:
                        values[
                            (
                                str(result_set),
                                str(cs.unit("us.precinct", units[number])),
                                str(options["Write-in" if writein else candidate]),
                            )
                        ] += votes
                totals.sort(key=lambda pair: -pair[0])
                total = int(cells["votes"].sum())
                print(contest_key, "on file" if found else "new", total, totals[: magnitude + 1])
                if len(totals) > magnitude and totals[magnitude - 1][0] - totals[magnitude][0] <= total * 0.005:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CLOSE)
                    continue
                for _, option in totals[:magnitude]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for code, when in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code),
                polity_id=cs.polity("US-DC"),
                scheme_code="us.precinct",
                code=code,
                parent_id=county,
                area_relation="within",
                area_id=county_area,
                kind="standard",
                valid_from=when,
                valid_to=when + timedelta(days=1),
            )
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(
                [(str(unit), str(area), when, when + timedelta(days=1)) for unit, area, when in memberships],
                schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                orient="row",
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                [(*cell, value) for cell, value in values.items()],
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        print(f"{len(minted)} new precincts, {len(memberships)} ward memberships")
        return cs.write()
