import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.elstats import FORMER, GAZETTEER, SEARCH, SUFFIXES, TENANTS, TITLE, plain
from ea.store import DATA, connect

SOURCE = "us.elstats_gov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {"CO": 4, "CT": 4, "ID": 4, "NH": 3, "NM": 89, "VT": 3, "VA": 3}
DIVISIONS = {"County": "county", "Locality": "county", "City/Town": "town"}
PSEUDO = {
    "total votes cast",
    "total ballots cast",
    "total votes",
    "registered voters",
    "undervotes",
    "overvotes",
    "blank",
    "blanks",
    "blank votes",
    "void",
    "blank/void",
}
POOLED = {"write-ins", "write-in", "all other votes", "unknown candidate", "scattering", "all others"}
NATIONAL = {
    "democratic": "democratic-party",
    "democrat": "democratic-party",
    "dem": "democratic-party",
    "republican": "republican-party",
    "whig": "whig-party",
    "libertarian": "libertarian-party",
    "green": "green-party",
}
UNAFFILIATED = {
    "independent",
    "unaffiliated",
    "no party",
    "no party affiliation",
    "nonpartisan",
    "petition",
    "petitioning candidate",
    "past labels not allowed in current law",
    "write in",
    "write-in",
    "not registered",
    "general election",
}
ALIASES = {
    ("VA", "elizabethcity"): "hamptoncity",
    ("VA", "norfolk"): "chesapeakecity",
    ("VA", "southnorfolkcity"): "chesapeakecity",
    ("VA", "princessanne"): "virginiabeachcity",
    ("VA", "nansemond"): "suffolkcity",
    ("VA", "warwick"): "newportnewscity",
    ("VA", "warwickcity"): "newportnewscity",
    ("CT", "saybrook"): "deepriver",
    ("CT", "huntington"): "shelton",
    ("CT", "chatham"): "easthampton",
    ("CT", "southlyme"): "oldlyme",
    ("VT", "essexjunction"): "essex",
    ("VT", "salem"): "derby",
    ("VT", "sterling"): "stowe",
    ("VT", "mansfield"): "stowe",
    ("VT", "acton"): "townshend",
    ("VT", "orleans"): "barton",
}
MAJORITY = {"VT": 9999, "CT": 1901}
LEGISLATURE = (
    "No candidate won a majority of the popular vote. Under {state}'s constitution at the time, the legislature chose "
    "the governor."
)


def url(state: str) -> str:
    search = {
        "global": {"years": {"from": 1700, "to": 2026}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"id": OFFICES[state]}]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH.format(tenant=TENANTS[state][0]) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    for state in OFFICES:
        raw.fetch(SOURCE, url(state), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{plain(text)}city"] = code
            names.setdefault(plain(text), code)
    with zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in(list(OFFICES)))
    towns: dict[str, dict[str, str]] = {}
    for state, geoid, text in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        names = towns.setdefault(state, {})
        names.setdefault(plain(text), geoid[:5])
        names.setdefault(plain(re.sub(SUFFIXES, "", text.lower())), geoid[:5])
    for (state, text), code in FORMER.items():
        counties.setdefault(state, {})[text] = code
        towns.setdefault(state, {})[text] = code
    for (state, text), current in ALIASES.items():
        names = counties[state] if state == "VA" else towns[state]
        names[text] = names[current]

    frames = []
    for state, office in OFFICES.items():
        division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
        frames.append(
            pl.read_csv(raw.cached(SOURCE, url(state)).path, infer_schema=False)
            .filter(
                (pl.col("office_name") == "Governor")
                & (pl.col("office_id") == str(office))
                & pl.col("division_type").is_in(list(DIVISIONS))
                & (pl.col("division_name") != "State House")
            )
            .select(
                state=pl.lit(state),
                day=pl.col("election_date").str.slice(0, 10).str.to_date(),
                primary=pl.when(pl.col("election_type").str.contains("(?i)primary")).then(
                    pl.col("primary_party").fill_null("")
                ),
                candidate=pl.col("candidate_name").fill_null("").str.strip_chars(),
                party=pl.col("candidate_party_name"),
                winner=pl.col("is_winner") == "true",
                code=pl.when(pl.col("division_type").replace_strict(DIVISIONS) == "county")
                .then(
                    division.str.to_lowercase()
                    .str.replace_all(r"saint ", "st ")
                    .str.replace_all("&", "and")
                    .str.replace_all(r"\b(county|parish|borough)\b", "")
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict(counties[state], default=None)
                )
                .otherwise(
                    pl.coalesce(
                        division.map_elements(plain, return_dtype=pl.String).replace_strict(towns[state], default=None),
                        division.str.to_lowercase()
                        .str.replace_all(SUFFIXES, "")
                        .map_elements(plain, return_dtype=pl.String)
                        .replace_strict(towns[state], default=None),
                    )
                ),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = (
        pl.concat(frames)
        .filter(pl.col("votes").is_not_null() & ~pl.col("candidate").str.to_lowercase().is_in(list(PSEUDO)))
        .with_columns(pooled=pl.col("candidate").str.to_lowercase().is_in(list(POOLED)))
        .with_columns(
            candidate=pl.when("pooled").then(pl.lit("")).otherwise("candidate"),
            party=pl.when("pooled").then(None).otherwise("party"),
        )
    )
    contest = ["state", "day", "primary"]
    empty = frame.group_by(contest).agg(pl.col("votes").sum()).filter(pl.col("votes") == 0)
    print(f"skipped {empty.height} contests the archive lists with no votes: {empty.select(contest).rows()}")
    frame = frame.join(empty, on=contest, how="anti", nulls_equal=True)
    coarse = frame.group_by(contest).agg(coarse=pl.col("code").is_null().any())
    print(
        f"{coarse['coarse'].sum()} of {coarse.height} contests have a division with no current county or town; "
        "they are stored as state totals"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    option = [*contest, "candidate", "pooled"]
    (
        frame.join(coarse, on=contest, nulls_equal=True)
        .with_columns(code=pl.when("coarse").then(None).otherwise("code"))
        .group_by(*option, "code")
        .agg(pl.col("winner").any(), pl.col("votes").sum())
        .join(
            frame.group_by(*option, "party")
            .agg(pl.col("votes").sum())
            .sort("votes", descending=True)
            .group_by(option, maintain_order=True)
            .agg(pl.col("party").drop_nulls().first()),
            on=option,
            nulls_equal=True,
        )
        .sort(*option, "code")
        .write_parquet(PARSED / "governors.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "governors.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor elections from the state archives of Colorado, Connecticut, Idaho, New Hampshire, New Mexico, Vermont, and Virginia, by county or town",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

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
        units: dict[str, UUID] = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county'").fetchall()
        )
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        labels = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        existing: dict[str, list[tuple[UUID, str]]] = {}
        for key, option, label in conn.execute(
            """select e.value, o.id, o.ballot_label from external_id e
               join contest c on c.id = e.entity_id
               join changeset s on s.id = c.changeset_id and s.pipeline <> %s
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and e.value like '%%:governor%%'""",
            [SOURCE],
        ).fetchall():
            existing.setdefault(key, []).append((option, slug(label or "")))
        fptp, two_round = cs.key("us.electoral_system", "fptp"), cs.key("us.electoral_system", "two_round")
        if cs.minted & {fptp, two_round}:
            raise ValueError("the fptp and two-round systems must exist")
        firsts: dict[tuple[str, str], UUID] = {}
        published: dict[UUID, UUID | None] = dict(
            conn.execute(
                """select o.id, coalesce(h.party_id, o.party_id) from contest_option o
                   join changeset c on c.id = o.changeset_id
                   left join lateral (select party_id from contest_option_history h where h.id = o.id
                                      order by h.recorded_from limit 1) h on true
                   where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )

        sources: dict[str, UUID] = {}
        for state in sorted(frame["state"].unique()):
            _, publisher, site = TENANTS[state]
            sources[state] = cs.source(publisher, TITLE)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=publisher,
                title=TITLE,
                url=site,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url(state)), sources[state])

        offices: dict[str, UUID] = {}
        elections: dict[str, UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        parties: dict[str, UUID] = {}

        def party_of(state: str, label: str | None) -> UUID | None:
            text = (label or "").split("/")[0].strip()
            if text.lower() in UNAFFILIATED or not text:
                return None
            if f"{state}:{text}" not in parties:
                key = NATIONAL.get(text.lower()) or next(
                    (
                        key
                        for key in (f"{state}:{slug(text)}", f"{state}:{slug(text)}-party", f"{slug(text)}-party")
                        if key in labels
                    ),
                    f"{state}:{slug(text)}",
                )
                parties[f"{state}:{text}"] = cs.key("us.party", key)
                if key not in labels:
                    labels.add(key)
                    cs.add("party", id=parties[f"{state}:{text}"], kind="party", polity_id=cs.polity(f"US-{state}"))
                    name(parties[f"{state}:{text}"], text)
            return parties[f"{state}:{text}"]

        def person(state: str, text: str) -> UUID:
            if (state, slug(text)) not in persons:
                persons[(state, slug(text))] = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                cs.add("person", id=persons[(state, slug(text))])
                name(persons[(state, slug(text))], text)
            return persons[(state, slug(text))]

        primaries: dict[tuple[str, str], list[date]] = {}
        for state, day, primary in (
            frame.filter(pl.col("primary").is_not_null()).select("state", "day", "primary").unique().iter_rows()
        ):
            primaries.setdefault((state, primary), []).append(day)
        for (state, day, primary), lines in frame.group_by("state", "day", "primary", maintain_order=True):
            unit, area, state_name = states[state]
            others = primaries.get((state, primary), []) if primary is not None else []
            runoff = any(0 < (day - other).days <= 70 for other in others)
            advancing = any(0 < (other - day).days <= 70 for other in others)
            kind = "general" if primary is None else "runoff" if runoff else "primary"
            if state not in offices:
                offices[state] = cs.key("us.office", f"governor:{state}")
                if offices[state] in cs.minted:
                    cs.add("office", id=offices[state], polity_id=cs.polity(f"US-{state}"), kind="governor")
                    name(offices[state], f"Governor of {state_name}")
            election_key = f"{day}:{state}" + ("" if kind == "general" else f":{kind}")
            if election_key not in elections:
                elections[election_key] = cs.key("us.election", election_key)
                if election_key not in known:
                    known.add(election_key)
                    cs.add(
                        "election",
                        id=elections[election_key],
                        polity_id=cs.polity(f"US-{state}"),
                        parent_id=cs.key("us.election", str(day)) if kind == "general" and str(day) in known else None,
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        elections[election_key],
                        f"{day.year} {state_name} "
                        + {"general": "general election", "runoff": "primary runoff", "primary": "primary"}[kind],
                    )
                    cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=elections[election_key])
            election = elections[election_key]
            scope = "" if primary is None else f"-{slug(primary) or 'open'}"
            contest_key = f"{day}:{state}:governor" + scope.replace("-", ":", 1)
            contest = cs.key("us.contest", contest_key)
            if advancing and primary is not None:
                firsts[(state, primary)] = contest
            reused = contest_key in existing
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=offices[state],
                    district_area_id=area,
                    party_scope_id=party_of(state, primary),
                    term="full",
                    magnitude=1,
                    round_number=2 if runoff else 1,
                    previous_round_id=firsts.get((state, primary)) if runoff and primary is not None else None,
                    electoral_system_id=two_round
                    if primary is not None and (runoff or (state == "VA" and 1953 <= day.year <= 1969))
                    else fptp,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{day.year} {state_name} gubernatorial "
                    + (
                        "election"
                        if primary is None
                        else " ".join(word for word in (primary, "primary", "runoff" if runoff else "") if word)
                    ),
                )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{kind}/governor{scope}",
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
                source_id=sources[state],
                is_default=not reused,
                completeness=1,
            )

            options: dict[str, UUID] = {}
            for candidate, pooled, party in (
                lines.select("candidate", "pooled", "party").unique().sort("candidate").iter_rows()
            ):
                if pooled:
                    options[candidate] = cs.key("us.option", f"{contest_key}:other")
                    cs.add(
                        "contest_option",
                        id=options[candidate],
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Other votes",
                    )
                    continue
                head, _, mate = candidate.partition(" and ") if primary is None else (candidate, "", "")
                surname = [
                    word for word in slug(head).split("-") if len(word) > 1 and word not in ("jr", "sr", "ii", "iii")
                ]
                matched = next(
                    (
                        option
                        for option, label in existing.get(contest_key, [])
                        if surname and surname[-1] in label.split("-") and option not in options.values()
                    ),
                    None,
                )
                if matched is not None:
                    options[candidate] = matched
                    continue
                key = cs.key("us.option", f"{contest_key}:{person(state, head)}")
                if key in options.values():
                    options[candidate] = key
                    continue
                options[candidate] = key
                party_id = party_of(state, party)
                cs.add(
                    "contest_option",
                    id=options[candidate],
                    contest_id=contest,
                    kind="candidate",
                    party_id=published.get(options[candidate], party_id),
                    ballot_label=candidate,
                )
                for role, text in (("head", head), ("running_mate", mate)):
                    if text:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{person(state, head)}:{role}"),
                            person_id=person(state, text),
                            option_id=options[candidate],
                            role=role,
                            ballot_name=text,
                            is_independent=party_id is None,
                        )
            totals: dict[str, int] = {}
            cells: dict[tuple[UUID, str | None], int] = {}
            for candidate, code, votes in (
                lines.group_by("candidate", "code").agg(pl.col("votes").sum()).sort("candidate", "code").iter_rows()
            ):
                totals[candidate] = totals.get(candidate, 0) + votes
                cells[(options[candidate], code)] = cells.get((options[candidate], code), 0) + votes
            for (option, code), votes in cells.items() if result_set in cs.minted else ():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit if code is None else units[code],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if reused:
                continue
            won = sorted(set(lines.filter(pl.col("winner") & ~pl.col("pooled"))["candidate"]), key=lambda c: -totals[c])
            if (
                primary is None
                and won
                and day.year <= MAJORITY.get(state, 0)
                and totals[won[0]] * 2 <= sum(totals.values())
            ):
                cs.add("note", entity_id=result_set, kind="caveat", body_md=LEGISLATURE.format(state=state_name))
                continue
            for candidate in won[:2] if advancing else won[:1]:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[candidate],
                    kind="advanced" if advancing else "elected" if primary is None else "won",
                    seats=None,
                )
        return cs.write()
