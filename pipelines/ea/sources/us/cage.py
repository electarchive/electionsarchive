import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.cage"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
RETURNS = f"{ACCESS}/13461690"
GUIDE = f"{ACCESS}/10626650"
PUBLISHER = "Jeremiah Cha, Shiro Kuriwaki, and James M. Snyder Jr."
TITLE = "Candidates in American General Elections"
DAYS = {
    (2007, "LA"): (date(2007, 10, 20), "general"),
    (2011, "LA"): (date(2011, 10, 22), "general"),
    (2011, "WV"): (date(2011, 10, 4), "special"),
    (2015, "LA"): (date(2015, 11, 21), "runoff"),
    (2019, "LA"): (date(2019, 11, 16), "runoff"),
}
DROPPED = {(2007, "LA")}
LEGISLATURE = {(2010, "VT"), (2014, "VT")}
PARTIES = {
    "D": "democratic-party",
    "R": "republican-party",
    "Lbt": "libertarian-party",
    "Grn": "green-party",
    "Const": "constitution-party",
    "DFL": "MN:minnesota-democratic-farmer-labor-party",
}
TRUSTED = {(2008, "MO"), (2012, "MO"), (2016, "MO")}
UNAFFILIATED = {"I", "NPA", "Unaffiliated", "Petition"}
SYSTEMS = {"GA": "two_round", "LA": "two_round"}
SUFFIXES = {"JR.": "Jr.", "SR.": "Sr.", "II": "II", "III": "III", "IV": "IV"}
METHOD = (
    "Votes compiled by Cha, Kuriwaki, and Snyder from official returns. Each candidate shows one party: Democratic, "
    "Republican, Libertarian, Green, Constitution, or Minnesota's DFL. Other party labels, fusion lines, and aggregate "
    "write-ins are not included. Some minor candidates may be missing, which can cause vote shares to run slightly high."
)
RUNOFF = (
    "Louisiana elects its governor in an October open primary where all candidates run on one ballot. If no one wins "
    "a majority, a November runoff is held between the top two candidates. These results are for the runoff; the "
    "October first round is not in the archive."
)
VERMONT = (
    "No candidate won a majority of the popular vote. Under Vermont's constitution, the General Assembly then chose "
    "the governor from the top three candidates and elected {name} in January {year}."
)
NEVADA = "Nevada ballots include “None of These Candidates” in statewide races; these votes do not elect anyone."


def fetch() -> None:
    raw.fetch(SOURCE, RETURNS, suffix=".tab")
    raw.fetch(SOURCE, GUIDE, suffix=".pdf")


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, RETURNS).path, separator="\t", infer_schema_length=None)
        .filter(pl.col("office") == "G")
        .select(
            pl.col("year").cast(pl.Int64),
            "state",
            special=pl.col("type") == "S",
            party=pl.col("party"),
            label=pl.col("party_formal"),
            printed=pl.col("name_snyder"),
            votes=pl.col("candidatevotes").cast(pl.Int64),
            total=pl.col("totalvotes").cast(pl.Int64),
            won=pl.col("won") == 1,
        )
    )
    for (year, state, special), race in frame.group_by("year", "state", "special"):
        if race["won"].sum() != 1:
            raise ValueError(f"{year} {state}: {race['won'].sum()} winners")
        if race["votes"].sum() != race["total"][0]:
            raise ValueError(f"{year} {state}{' special' if special else ''}: the lines don't sum to the total")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("total").sort("year", "state", "special", "votes", descending=[False, False, False, True]).write_parquet(
        PARSED / "governors.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "governors.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governors' general and special elections by state, 2006–2024",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def note(entity: UUID, kind: str, text: str) -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=text)

        def title(text: str) -> str:
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())

        def person_name(printed: str) -> str:
            if printed.startswith("["):
                return printed
            last, _, rest = printed.partition(", ")
            first, _, suffix = rest.partition(", ")
            if suffix not in SUFFIXES and suffix:
                first = f"{first}, {suffix}"
                suffix = ""
            words = [title(first), title(last)] if first else [title(last)]
            text = " ".join(words)
            return (
                f"{text}, {SUFFIXES[suffix]}"
                if suffix in ("JR.", "SR.")
                else f"{text} {SUFFIXES[suffix]}"
                if suffix
                else text
            )

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/DGDRDT",
            license="CC0 1.0",
        )
        for url in (RETURNS, GUIDE):
            cs.artifact(raw.cached(SOURCE, url), source)

        states: dict[str, tuple[UUID, UUID, str]] = {
            abbreviation: (unit, area, text)
            for abbreviation, unit, area, text in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id, n.text from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "two_round")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and two-round systems must exist")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & set(parties.values()):
            raise ValueError(f"missing parties: {[label for label, party in parties.items() if party in cs.minted]}")

        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        official = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e join contest c on c.id = e.entity_id
                   join changeset s on s.id = c.changeset_id
                   where e.scheme = 'us.contest' and e.value like '%%:governor%%' and s.pipeline <> %s""",
                [SOURCE],
            )
        }
        placeholders: dict[str, tuple[UUID, UUID, str]] = {
            value: (contest, election, value)
            for value, contest, election in conn.execute(
                """select e.value, c.id, c.election_id from external_id e join contest c on c.id = e.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join changeset s on s.id = rs.changeset_id
                   where e.scheme = 'us.contest' and e.value like '%%:governor%%' and s.pipeline = 'us.algara'
                     and e.value >= '2006'"""
            )
        }
        done = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }
        offices: dict[str, UUID] = {}
        elections: dict[tuple[date, str, str], UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        for (year, state, special), race in frame.group_by("year", "state", "special", maintain_order=True):
            standard = date(year, 11, 2) + timedelta(days=(1 - date(year, 11, 2).weekday()) % 7)
            day, kind = DAYS.get((year, state), (standard, "general"))
            contest_key = f"{day}:{state}:governor" + (":special" if special else "")
            placeholder = (
                placeholders.get(contest_key)
                or placeholders.get(f"{year}:{state}:governor" + (":special" if special else ""))
                or placeholders.get(f"{day}:{state}:governor")
            )
            if (year, state) in DROPPED or (contest_key in official and placeholder is None):
                continue
            unit, area, state_name = states[state]
            matched: dict[str, UUID] = {}
            if placeholder is not None:
                contest, election, contest_key = placeholder
                if contest_key in done:
                    continue
                on_file = conn.execute(
                    """select o.id, o.ballot_label, coalesce(sum(v.value), 0)::bigint from contest_option o
                       left join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                       left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                       where o.contest_id = %s and o.kind in ('candidate', 'ticket', 'write_in') group by 1, 2""",
                    [contest],
                ).fetchall()
                drifted = []
                for printed, votes in race.select("printed", "votes").iter_rows():
                    surname = slug(re.sub(r",? (Jr|Sr|II|III|IV)\.?$", "", person_name(printed))).split("-")[-1]
                    found = [
                        (option, total)
                        for option, label, total in on_file
                        if slug(re.sub(r",? (Jr|Sr|II|III|IV)\.?$", "", label)).split("-")[-1] == surname
                    ]
                    if len(found) == 1 and found[0][0] not in matched.values():
                        matched[printed] = found[0][0]
                        if abs(votes - found[0][1]) > found[0][1] * 0.02:
                            drifted.append(f"{printed} {votes} against {found[0][1]}")
                if len(set(matched.values())) < len(on_file) or (drifted and (year, state) not in TRUSTED):
                    print(
                        f"{contest_key}: kept Algara's ({len(on_file) - len(set(matched.values()))} unmatched; {drifted})"
                    )
                    continue
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default""",
                    [contest],
                ).fetchall():
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
            elif contest_key in done:
                continue
            else:
                if state not in offices:
                    offices[state] = cs.key("us.office", f"governor:{state}")
                    cs.add("office", id=offices[state], polity_id=cs.polity(f"US-{state}"), kind="governor")
                    name(offices[state], f"Governor of {state_name}")
                election_key = f"{day}:{state}" + ("" if kind == "general" else f":{kind}")
                if (day, state, kind) not in elections:
                    elections[(day, state, kind)] = cs.key("us.election", election_key)
                    if election_key not in known:
                        cs.add(
                            "election",
                            id=elections[(day, state, kind)],
                            polity_id=cs.polity(f"US-{state}"),
                            parent_id=cs.key("us.election", str(day)) if str(day) in known else None,
                            kind=kind,
                            date_from=day,
                            date_to=day,
                            status="held",
                        )
                        name(
                            elections[(day, state, kind)],
                            f"{year} {state_name} {'gubernatorial ' if kind != 'general' else ''}"
                            f"{'general election' if kind == 'general' else f'{kind} election'}",
                        )
                        cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=elections[(day, state, kind)])
                election = elections[(day, state, kind)]

                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=offices[state],
                    district_area_id=area,
                    term="remainder" if special else "full",
                    magnitude=1,
                    round_number=2 if kind == "runoff" else 1,
                    electoral_system_id=systems[SYSTEMS.get(state, "fptp")],
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{year} {state_name} gubernatorial {'special election' if special else 'runoff' if kind == 'runoff' else 'election'}",
                )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{kind}/governor{'-special' if special else ''}",
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
            note(result_set, "methodology", METHOD)
            if kind == "runoff" and placeholder is None:
                note(contest, "caveat", RUNOFF)
            if state == "NV" and placeholder is None:
                note(contest, "context", NEVADA)

            for party, label, printed, votes, won in race.select(
                "party", "label", "printed", "votes", "won"
            ).iter_rows():
                text = person_name(printed)
                if printed == "[NONE OF THESE]":
                    option = cs.key("us.option", f"{contest_key}:none-of-these")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="none_of_the_above",
                        ballot_label="None of These Candidates",
                    )
                elif printed in matched:
                    option = matched[printed]
                else:
                    if (state, slug(text)) not in persons:
                        persons[(state, slug(text))] = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                        cs.add("person", id=persons[(state, slug(text))])
                        name(persons[(state, slug(text))], text)
                    person = persons[(state, slug(text))]
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    main = (label or "").split(",")[0]
                    party_id = parties.get(main) or parties.get(party)
                    write_in = main.startswith("W-I")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if write_in else "candidate",
                        party_id=None if write_in else party_id,
                        ballot_label=text,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=text,
                        is_independent=main in UNAFFILIATED or party == "I",
                    )
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
                if won:
                    if (year, state) in LEGISLATURE:
                        note(result_set, "caveat", VERMONT.format(name=text, year=year + 1))
                        continue
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
        return cs.write()
