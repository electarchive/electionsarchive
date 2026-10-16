import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nj_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RESULTS = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official election results"
FILES = {
    (date(2001, 6, 26), True): "2001/2001-primary-elect-gov-candidate-tallies.pdf",
    (date(2001, 11, 6), False): "2001/2001-general-elect-gov-tallies.pdf",
    (date(2005, 6, 7), True): "2005/2005_primary_official_results-gov.pdf",
    (date(2005, 11, 8), False): "2005/2005_Official_General_Election-Governor_tallies.pdf",
    (date(2009, 6, 2), True): "2009/2009-official-primary-elect-governor-tallies-062909.pdf",
    (date(2009, 11, 3), False): "2009/2009-official-general-election-gov-lt-gov-tallies-120109.pdf",
    (date(2013, 6, 4), True): "2013/2013-official-primary-results-governor.pdf",
    (date(2013, 11, 5), False): "2013/2013-official-general-election-results-governor.pdf",
    (date(2017, 6, 6), True): "2017/2017-official-primary-results-governor-0628.pdf",
    (date(2017, 11, 7), False): "2017/2017-official-general-election-results-governor.pdf",
    (date(2021, 6, 8), True): "2021/2021-official-primary-results-governor.pdf",
    (date(2021, 11, 2), False): "2021/2021-official-general-results-governor.pdf",
    (date(2025, 6, 10), True): "2025/2025-official-primary-results-governor.pdf",
    (date(2025, 11, 4), False): "2025/2025-official-general-results-governor.pdf",
}
COUNTIES = [
    "Atlantic",
    "Bergen",
    "Burlington",
    "Camden",
    "Cape May",
    "Cumberland",
    "Essex",
    "Gloucester",
    "Hudson",
    "Hunterdon",
    "Mercer",
    "Middlesex",
    "Monmouth",
    "Morris",
    "Ocean",
    "Passaic",
    "Salem",
    "Somerset",
    "Sussex",
    "Union",
    "Warren",
]
COUNTY = re.compile(r"\b(" + "|".join(COUNTIES) + r")\b.*?([\d,]+)\s*$", re.IGNORECASE)
CLIPPED = {
    "emocratic": "Democratic",
    "GLASS-STEAGA": "Glass-Steagall Now",
    "PEACE AND FR": "Peace and Freedom",
    "NSA DID 911": "NSA Did 911",
    "REDUCE PROPE": "Reduce Property Taxes",
    "WE THE PEOPL": "We the People",
    "Libertarian Par": "Libertarian Party",
    "SOCIALIST W": "Socialist Workers Party",
    "ONE NEW JERSEY Atla": "One New Jersey",
}
PARTIES = {
    "democratic": "democratic-party",
    "republican": "republican-party",
    "libertarian": "libertarian-party",
    "libertarian party": "libertarian-party",
    "green party": "green-party",
    "the green party": "green-party",
    "constitution": "constitution-party",
    "socialist workers party": "socialist-workers-party",
    "socialist party usa": "socialist-party-usa",
}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, RESULTS + path, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (day, primary), path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, RESULTS + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        columns: tuple[int | None, int, int] | None = None
        current: dict[str, object] | None = None
        votes: dict[str, int] = {}
        start, closed, mate = 0, False, False
        for line in text.splitlines():
            if re.search(r"Official (List|Tallies)|Page \d+ of", line):
                columns = None
                continue
            if re.match(r"\s*Name\b", line) and "County" in line:
                party_column = line.find("Designation") if "Designation" in line else line.find("Party")
                address = line.find("Address") if re.search(r"Name\s{2,}Address", line) else None
                columns = (address, party_column, line.find("County"))
                continue
            if columns is None:
                continue
            address, party_column, county_column = columns
            total = re.match(r"\s*Total\s+([\d,]+)\s*$", line)
            if total and current is not None:
                pooled = current["candidate"] == "Personal Choice"
                if sum(votes.values()) != int(total[1].replace(",", "")) or (len(votes) != 21 and not pooled):
                    raise ValueError(f"{path}: {current['candidate']}'s counties don't sum to the total")
                label = str(current["party"])
                rows.extend(
                    {
                        **current,
                        "party": None if pooled else CLIPPED.get(label, label),
                        "pooled": pooled,
                        "county": county,
                        "votes": count,
                    }
                    for county, count in votes.items()
                )
                current, votes = None, {}
                continue
            first = line[:county_column].strip()
            if first == "Lieutenant Governor":
                mate = True
            elif mate and current is not None and first:
                current["mate"] = re.split(r"\s{2,}", first)[0]
                mate = False
            elif current is None and first and first != "Governor":
                start = next(
                    (
                        position
                        for position in range(party_column - 8, party_column + 2)
                        if 1 < position < len(line) and line[position - 2 : position] == "  " and line[position] != " "
                    ),
                    party_column,
                )
                current = {
                    "day": day,
                    "primary": primary,
                    "candidate": re.sub(r"\s*(\(w\)|\*)", "", line[: address or start]).strip(),
                    "party": line[start:county_column].strip(),
                    "mate": None,
                }
                closed = False
            elif current is not None and not closed and line[start:county_column].strip():
                if line[start - 2 : start] == "  " and line[start] != " ":
                    current["party"] = f"{current['party']} {line[start:county_column].strip()}"
            elif current is not None:
                closed = True
            found = COUNTY.search(line, max(county_column - 4, 0))
            if current is not None and found and found.start() <= county_column + 3:
                votes[found[1].title()] = int(found[2].replace(",", ""))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "governors.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "governors.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's governor generals and primaries by county, 2001–2025",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            if not text.isupper():
                return text
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nj.gov/state/elections/election-information-results.shtml",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, RESULTS + path), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-NJ'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, UUID] = {
            text: unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '34%'"""
            ).fetchall()
        }
        if set(COUNTIES) - set(counties):
            raise ValueError(f"missing counties: {set(COUNTIES) - set(counties)}")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the fptp system and the national parties must exist")
        office = cs.key("us.office", "governor:NJ")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-NJ"), kind="governor")
            name(office, "Governor of New Jersey")
        persons: dict[str, UUID] = {}
        others: dict[str, list[tuple[UUID, set[str]]]] = {}
        for key, option, label in conn.execute(
            """select e.value, o.id, o.ballot_label from external_id e
               join contest c on c.id = e.entity_id join changeset s on s.id = c.changeset_id
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and e.value like '%%:NJ:governor%%' and s.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            others.setdefault(key, []).append((option, set(slug(label or "").split("-"))))

        def person(text: str) -> UUID:
            if slug(text) not in persons:
                persons[slug(text)] = cs.key(f"{SOURCE}.person", f"NJ:{slug(text)}")
                cs.add("person", id=persons[slug(text)])
                name(persons[slug(text)], text)
            return persons[slug(text)]

        for (day, primary, scope), lines in frame.with_columns(
            scope=pl.when("primary").then(pl.col("party")).otherwise(pl.lit(""))
        ).group_by("day", "primary", "scope", maintain_order=True):
            kind = "primary" if primary else "general"
            election_key = f"{day}:NJ" + (":primary" if primary else "")
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-NJ"),
                    parent_id=None if primary or str(day) not in known else cs.key("us.election", str(day)),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} New Jersey {'primary' if primary else 'general election'}")
                cs.add("slug", path=f"us/nj/{day}-{kind}", entity_id=election)
            contest_key = f"{day}:NJ:governor" + (f":{slug(scope)}" if primary else "")
            contest = cs.key("us.contest", contest_key)
            reused = contest_key in others
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    party_scope_id=parties[scope.lower()] if primary else None,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} New Jersey gubernatorial " + (f"{scope} primary" if primary else "election"))
                cs.add(
                    "slug",
                    path=f"us/nj/{day}-{kind}/governor" + (f"-{slug(scope)}" if primary else ""),
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
                is_default=not reused,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for (candidate, label, mate, pooled), cells in lines.group_by(
                "candidate", "party", "mate", "pooled", maintain_order=True
            ):
                if pooled:
                    option = cs.key("us.option", f"{contest_key}:other")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Personal choice",
                    )
                else:
                    surname = [word for word in slug(candidate).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                    matched = next((option for option, words in others.get(contest_key, []) if surname in words), None)
                    party = parties.get((label or "").lower())
                    if matched is None:
                        head = person(title(candidate))
                        matched = cs.key("us.option", f"{contest_key}:{head}")
                        cs.add(
                            "contest_option",
                            id=matched,
                            contest_id=contest,
                            kind="candidate",
                            party_id=party,
                            ballot_label=title(candidate),
                        )
                        for role, text in (("head", candidate), ("running_mate", mate)):
                            if text:
                                cs.add(
                                    "candidacy",
                                    id=cs.key("us.candidacy", f"{contest_key}:{head}:{role}"),
                                    person_id=person(title(text)),
                                    option_id=matched,
                                    role=role,
                                    ballot_name=title(text),
                                    is_independent=party is None,
                                )
                    option = matched
                    totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            if reused:
                continue
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="won" if primary else "elected",
                seats=None,
            )
        return cs.write()
