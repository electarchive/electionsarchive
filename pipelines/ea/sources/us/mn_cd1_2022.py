import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mn_cd1_2022"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-016"
PRIMARY = date(2022, 5, 24)
GENERAL = date(2022, 8, 9)
FILES = {
    "primary": (
        "https://web.archive.org/web/20250421140608id_/https://electionresultsfiles.sos.mn.gov/20220524/USHouseCty.txt",
        INBOX / "20220524" / "USHouseCty.txt",
        ".txt",
        "text/plain",
    ),
    "general": (
        (
            "https://web.archive.org/web/20240706060806id_/https://ossmedia.sos.state.mn.us/MediaInfo/"
            "Congressional-District-1-Special-Election-Results.xlsx"
        ),
        INBOX / "20220809" / "Congressional-District-1-Special-Election-Results.xlsx",
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}
PUBLISHER = "Minnesota Secretary of State"
TITLES = {
    "primary": "Special primary results by county, U.S. Representative District 1, May 24, 2022",
    "general": "Congressional District 1 Special Election Unofficial Results by Precinct",
}
PARTIES = {
    "R": ("republican", "republican-party", "Republican", 35930),
    "DFL": ("minnesota-democratic-farmer-labor", "MN:minnesota-democratic-farmer-labor-party", "DFL", 18823),
    "LMN": ("legal-marijuana-now", "legal-marijuana-now", "Legal Marijuana Now", 363),
    "GLC": (
        "grassroots-legalize-cannabis",
        "MN:grassroots-legalize-cannabis-party",
        "Grassroots–Legalize Cannabis",
        194,
    ),
}
COLUMNS = {
    "McClellan - GLC": ("Haroun McClellan", 865),
    "Reisdorf - LMN": ("Richard Reisdorf", 1536),
    "Finstad - R": ("Brad Finstad", 59788),
    "Ettinger - DFL": ("Jeff Ettinger", 55155),
    "Write In": ("Write-ins", 535),
}
PRIMARY_NOTE = (
    "County returns from the Secretary of State's results file for the special primary, as archived by the Wayback "
    "Machine on April 21, 2025. Each party's total matches the certified totals the FEC published."
)
GENERAL_NOTE = (
    "Unofficial precinct returns the Secretary of State posted on August 10, 2022, as archived by the Wayback "
    "Machine; county figures are sums of the precinct rows. Every candidate's total matches the certified canvass as "
    'the FEC published it, except that the certified count adds 13 write-in votes for Bob "Again" Carney Jr., which '
    "this file leaves out (117,892 votes certified, 117,879 here)."
)


def fetch() -> None:
    for url, path, suffix, media_type in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type=media_type,
            )


def emit() -> Path:
    primary: dict[str, dict[str, dict[str, int]]] = {code: {} for code in PARTIES}
    for line in raw.cached(SOURCE, FILES["primary"][0]).path.read_text(encoding="latin-1").splitlines():
        cells = [cell.strip() for cell in line.split(";")]
        if cells[4] != "U.S. Representative District 1":
            raise ValueError(f"unexpected office {cells[4]}")
        county = f"27{2 * int(cells[1]) - 1:03}"
        primary[cells[10]].setdefault(re.sub(r"\s+", " ", cells[7]), {})[county] = int(cells[13])
    for code, (_, _, _, certified) in PARTIES.items():
        if (found := sum(sum(counties.values()) for counties in primary[code].values())) != certified:
            raise ValueError(f"{code} primary sums to {found}, the FEC certified {certified}")
    sheet = pl.read_excel(
        raw.cached(SOURCE, FILES["general"][0]).path,
        sheet_name="PrecinctResults",
        engine="calamine",
        has_header=False,
        infer_schema_length=0,
    )
    header = sheet.row(1)
    precincts = [
        (f"27{2 * int(row[1]) - 1:03}", re.sub(r"\s+", " ", row[2]).strip(), row)
        for row in sheet.iter_rows()
        if row[1] is not None and row[1] != "County Code"
    ]
    for column, (label, certified) in COLUMNS.items():
        if (found := sum(int(row[header.index(column)]) for _, _, row in precincts)) != certified:
            raise ValueError(f"{label} sums to {found}, the certified canvass has {certified}")

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 1st district U.S. House special primaries of May 24, 2022 by county, and the August 9 "
            "special election by precinct and county from the Secretary of State's unofficial results",
            polity="US-MN",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for stage, title in TITLES.items():
            sources[stage] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[stage],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=FILES[stage][0].split("id_/", 1)[1],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, FILES[stage][0]), sources[stage])
        general = cs.key("us.contest", f"{GENERAL}:MN:house:1:special")
        fptp = cs.key("us.electoral_system", "fptp")
        if cs.minted & {general, fptp}:
            raise ValueError("the special general contest and the fptp system must already exist")
        body, area, boundary = conn.execute(
            "select body_id, district_area_id, boundary_set_id from contest where id = %s", [general]
        ).fetchone()
        known: dict[str, UUID] = {}
        for text, person in conn.execute(
            """select distinct n.text, c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
               join name n on n.entity_id = c.person_id and n.is_preferred
               where ct.district_area_id = %s""",
            [area],
        ).fetchall():
            words = text.split()
            known.setdefault(slug(text), person)
            known.setdefault(slug(f"{words[0]} {words[-1]}"), person)

        election = cs.key("us.election", f"{PRIMARY}:MN:primary")
        if election in cs.minted:
            cs.add(
                "election",
                id=election,
                polity_id=cs.polity("US-MN"),
                parent_id=None,
                kind="primary",
                date_from=PRIMARY,
                date_to=PRIMARY,
                status="held",
            )
            name(election, "May 24, 2022 Minnesota special primary election")
            cs.add("slug", path=f"us/mn/{PRIMARY}-primary", entity_id=election)
        created: set[UUID] = set()
        for code, (suffix, scope, label, _) in PARTIES.items():
            key = f"{PRIMARY}:MN:house:1:special:{suffix}"
            party = cs.key("us.party", scope)
            if party in cs.minted:
                raise ValueError(f"no party {scope}")
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                seat_id=None,
                term="remainder",
                boundary_set_id=boundary,
                party_scope_id=party,
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"2022 U.S. House special election {label} primary in Minnesota's 1st congressional district")
            cs.add("slug", path=f"us/mn/{PRIMARY}-primary/house-1-special-{suffix}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources["primary"],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=PRIMARY_NOTE)
            leader = max(primary[code], key=lambda text: sum(primary[code][text].values()))
            for text, counties in primary[code].items():
                words = text.replace('"', "").split()
                person = known.get(slug(text)) or known.get(slug(f"{words[0]} {words[-1]}"))
                if person is None:
                    found = conn.execute(
                        """select distinct x.entity_id from external_id x
                           join name n on n.entity_id = x.entity_id and n.is_preferred
                           where x.scheme = 'fec.candidate' and x.value ~ '^[HS][0-9]MN' and n.text = %s""",
                        [text],
                    ).fetchall()
                    person = found[0][0] if len(found) == 1 else None
                if person is None:
                    person = cs.key(f"{SOURCE}.person", f"MN:{slug(text)}")
                    if person not in created:
                        created.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                option = cs.key("us.option", f"{key}:{slug(text)}")
                cs.add(
                    "contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=text
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{slug(text)}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=False,
                )
                if text == leader:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won")
                for county, votes in counties.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", county),
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )

        options = dict(
            conn.execute(
                "select ballot_label, id from contest_option where contest_id = %s and parent_option_id is null",
                [general],
            ).fetchall()
        )
        result_set = cs.key(f"{SOURCE}.result_set", f"{GENERAL}:MN:house:1:special")
        cs.add(
            "result_set",
            id=result_set,
            election_id=cs.key("us.election", f"{GENERAL}:MN:special"),
            contest_id=general,
            stage="preliminary",
            kind="counted",
            status="active",
            source_id=sources["general"],
            is_default=True,
            completeness=None,
        )
        cs.add("note", entity_id=result_set, kind="caveat", body_md=GENERAL_NOTE)
        for label, _ in COLUMNS.values():
            if label != "Write-ins":
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[label],
                    kind="elected" if label == "Brad Finstad" else "lost",
                )
        columns = (
            "id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness",
            "supersedes_id",
        )  # fmt: skip
        for row in conn.execute(
            f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s",
            [general, result_set],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
        counties: dict[tuple[str, UUID], int] = {}
        for county, precinct, row in precincts:
            unit = cs.unit("us.precinct", f"{GENERAL}:MN:{county}::{precinct}", GENERAL)
            if unit in cs.minted:
                raise ValueError(f"no precinct unit for {county} {precinct}")
            for column, (label, _) in COLUMNS.items():
                votes = int(row[header.index(column)])
                counties[(county, options[label])] = counties.get((county, options[label]), 0) + votes
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[label],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for (county, option), votes in counties.items():
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=cs.unit("us.county", county),
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
        return cs.write()
