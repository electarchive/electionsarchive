import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ms_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official election results"
FILES = {
    date(2023, 11, 7): "https://sos.ms.gov/elections/electionresults/2023General/State%20Certified%20Results/"
    "2023%20Statewide%20Recapitulation%20Sheet.pdf",
}
OFFICES = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary Of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "State Auditor": "Auditor",
    "State Treasurer": "Treasurer",
    "Commissioner Of Agriculture & Commerce": "Commissioner of Agriculture and Commerce",
    "Commissioner Of Insurance": "Commissioner of Insurance",
}
PARTIES = {
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Constitution": "constitution-party",
}
CANDIDATE = re.compile(r"^(\S.*?)\s{2,}([A-Z][a-z]+)\s+(\d[\d\s]*)$")


def fetch() -> None:
    for link in FILES.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, link in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        columns: list[str] = []
        placed: list[tuple[int, str]] = []
        extending = False
        office: str | None = None
        heading = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if heading:
                heading = False
                if office is not None and not CANDIDATE.match(line):
                    office = f"{office} {stripped}"
                    continue
            if (
                line.startswith(" ")
                and (extending or re.search(r"\S\s{4,}\S", stripped))
                and not re.search(r"[\d-]", stripped)
            ):
                found_names = [(match.start(), match[0]) for match in re.finditer(r"\S+(?: \S+)*", line)]
                placed = sorted([*placed, *found_names] if extending else found_names)
                columns = [text for _, text in placed]
                extending = True
                continue
            extending = False
            if line.startswith(" ") and "-" in stripped and not re.search(r"\d", stripped):
                parts = stripped.split("-", 1)
                office = parts[1] if parts[0] == "State Of Mississippi" else None
                heading = office is not None
                continue
            found = CANDIDATE.match(line)
            if office is None or found is None or not columns:
                continue
            counts = [int(count) for count in found[3].split()]
            if len(counts) != len(columns):
                raise ValueError(f"{office}: {found[1]} has {len(counts)} counts for {len(columns)} columns")
            for county, count in zip(columns, counts, strict=True):
                if county == "TOTAL":
                    continue
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": county,
                        "votes": count,
                    }
                )
            if columns[-1] == "TOTAL":
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "candidate": found[1],
                        "party": found[2],
                        "county": None,
                        "votes": counts[-1],
                    }
                )
    frame = pl.DataFrame(rows)
    unknown = set(frame["office"]) - set(OFFICES)
    if unknown:
        raise ValueError(f"unknown offices: {unknown}")
    counted = (
        frame.filter(pl.col("county").is_not_null()).group_by("day", "office", "candidate").agg(pl.col("votes").sum())
    )
    printed = frame.filter(pl.col("county").is_null()).select("day", "office", "candidate", total="votes")
    checked = counted.join(printed, on=["day", "office", "candidate"], how="full", coalesce=True)
    if checked.filter(pl.col("votes") != pl.col("total")).height or checked["total"].null_count():
        raise ValueError(
            f"counties don't sum to the printed totals: {checked.filter(pl.col('votes') != pl.col('total'))}"
        )
    counties = frame.filter(pl.col("county").is_not_null()).group_by("day", "office").agg(pl.col("county").n_unique())
    if counties.filter(pl.col("county") != 82).height:
        raise ValueError(f"offices without 82 counties: {counties.filter(pl.col('county') != 82)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("county").is_not_null()).with_columns(
        office=pl.col("office").replace_strict(OFFICES)
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's statewide general elections of 2023 by county",
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
            url="https://www.sos.ms.gov/elections-voting/election-results",
            license="Public record",
        )
        for link in FILES.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-MS'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, UUID] = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '28%'"""
            ).fetchall()
        }
        counties["jeffdavis"] = counties["jeffersondavis"]
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the fptp system and the national parties must exist")
        persons: dict[str, UUID] = {}
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            election_key = f"{day}:MS"
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-MS"),
                    kind="general",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} Mississippi general election")
                cs.add("slug", path=f"us/ms/{day}-general", entity_id=election)
            office_slug = slug(office)
            office_id = cs.key("us.office", f"{office_slug}:MS")
            if f"{office_slug}:MS" not in offices_known:
                offices_known.add(f"{office_slug}:MS")
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity("US-MS"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(office_id, f"{office} of Mississippi")
            contest_key = f"{day}:MS:{office_slug}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Mississippi {office} election")
            cs.add("slug", path=f"us/ms/{day}-general/{office_slug}", entity_id=contest)
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
            totals: dict[UUID, int] = {}
            for (candidate, label), cells in lines.group_by("candidate", "party", maintain_order=True):
                if candidate not in persons:
                    persons[candidate] = cs.key(f"{SOURCE}.person", f"MS:{slug(candidate)}")
                    if persons[candidate] in cs.minted:
                        cs.add("person", id=persons[candidate])
                        name(persons[candidate], candidate)
                party = parties.get(label)
                option = cs.key("us.option", f"{contest_key}:{persons[candidate]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{persons[candidate]}:head"),
                    person_id=persons[candidate],
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=party is None,
                )
                totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[re.sub(r"[^a-z]", "", county.lower())],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            leader = max(totals, key=totals.__getitem__)
            if totals[leader] * 2 > sum(totals.values()):
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="elected", seats=1)
        return cs.write()
