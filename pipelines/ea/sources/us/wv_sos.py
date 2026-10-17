import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wv_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://apps.sos.wv.gov/elections/results/"
URL = SITE + "readfile.aspx?eid={}&type=StateCountyTotals&format=csv"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Election results by county, 2008–2016"
ELECTIONS = {
    3: (date(2008, 5, 13), "primary"),
    4: (date(2008, 11, 4), "general"),
    6: (date(2010, 11, 2), "general"),
    7: (date(2010, 8, 28), "primary"),
    8: (date(2012, 5, 8), "primary"),
    10: (date(2011, 10, 4), "special"),
    11: (date(2011, 5, 14), "primary"),
    13: (date(2012, 11, 6), "general"),
    14: (date(2014, 5, 13), "primary"),
    21: (date(2014, 11, 4), "general"),
    22: (date(2016, 5, 10), "primary"),
    23: (date(2016, 11, 8), "general"),
}
SPECIALS = {6, 7, 10, 11}
OFFICES = {
    "AA": ("president", None),
    "BA": ("senate", None),
    "BU": ("senate", None),
    "DA": ("governor", "Governor"),
    "EA": ("secretary-of-state", "Secretary of State"),
    "FA": ("auditor", "Auditor"),
    "GA": ("treasurer", "Treasurer"),
    "HA": ("commissioner-of-agriculture", "Commissioner of Agriculture"),
    "IA": ("attorney-general", "Attorney General"),
}
PARTIES = {
    "D": ("democratic-party", "democratic", "Democratic"),
    "R": ("republican-party", "republican", "Republican"),
    "L": ("libertarian-party", "libertarian", "Libertarian"),
    "C": ("constitution-party", "constitution", "Constitution"),
    "M": ("WV:mountain-party", "mountain", "Mountain"),
    "TP": ("american-third-position", "american-third-position", "American Third Position"),
}
OFFICIAL = (
    "The Secretary of State's file has a provisional and an official line for some counties; this set keeps the "
    "official line wherever the file has one."
)


def fetch() -> None:
    for election in ELECTIONS:
        try:
            raw.cached(SOURCE, URL.format(election))
        except LookupError:
            raw.fetch(SOURCE, URL.format(election), suffix=".csv")
            time.sleep(2)


def parse() -> None:
    rows: list[dict[str, object]] = []
    for election, (day, stage) in ELECTIONS.items():
        lines = raw.cached(SOURCE, URL.format(election)).path.read_text(encoding="utf-8-sig", errors="replace")
        header, *body = lines.splitlines()
        columns = header.split(",")
        for line in body:
            fields = line.split(",")
            if len(fields) != len(columns):
                raise ValueError(f"{election}: {line}")
            row = dict(zip(columns, fields, strict=True))
            if row["OfficeCode"] not in OFFICES or row["CountyName"] == "" or row["District"] != "0":
                continue
            rows.append(
                {
                    "election": election,
                    "day": day,
                    "stage": stage,
                    "official": row["OfficialResults"] == "Yes",
                    "county": row["CountyName"],
                    "name": re.sub(r"\s+", " ", row["Name"]).strip(),
                    "party": row["PartyCode"],
                    "office": row["OfficeCode"],
                    "votes": int(row["Votes"]),
                }
            )
    frame = pl.DataFrame(rows)
    frame = frame.filter(
        pl.col("official") == pl.col("official").max().over("election", "office", "county")
    ).with_columns(
        primary=pl.when(pl.col("stage") == "primary").then(pl.col("party")).otherwise(pl.lit("")),
    )
    counts = frame.group_by("election", "office", "primary").agg(pl.col("county").n_unique().alias("counties"))
    if (short := counts.filter((pl.col("counties") != 55) & ~pl.col("primary").is_in(["M", "L", "C"]))).height:
        raise ValueError(f"contests without all 55 counties: {short}")
    doubled = frame.group_by("election", "office", "primary", "name", "county").len().filter(pl.col("len") > 1)
    if doubled.height:
        raise ValueError(f"repeated lines: {doubled}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("election", "day", "office", "primary")
        .agg(pl.col("name").n_unique(), pl.col("votes").sum(), pl.col("official").all())
        .sort("day", "office", "primary")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's president, Senate, governor, and statewide executive elections and primaries "
            "2008–2016 by county from the Secretary of State",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", text).replace(",", " ").split()
                if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            return plain(words[-1]) if words else ""

        def full(text: str) -> str:
            return plain(re.sub(r"\([^)]*\)|\"[^\"]*\"|,? (Jr|Sr|III|II)\.?$", " ", text))

        def cased(text: str) -> str:
            if not re.sub(r"\"[^\"]*\"", "", text).isupper():
                return text
            text = re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text.title())
            return re.sub(r"\b(Mc|O')([a-z])", lambda match: match[1] + match[2].upper(), text)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, URL.format(election)), source)
        state = cs.polity("US-WV")
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-WV'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, str] = {
            plain(text): str(unit)
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
        missing = sorted({county for county in frame["county"] if plain(county) not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        known_elections = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")
        }
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ '^20(0[89]|1[0-6]).*:WV:'"
            ).fetchall()
        )
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join polity p on p.id = e.polity_id and p.iso_code = 'US-WV'
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(full(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, (key, _, _) in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        filled = 0
        made: set[UUID] = set()
        for (election, day, stage, office, primary), lines in frame.sort("day", "office", "primary").group_by(
            "election", "day", "stage", "office", "primary", maintain_order=True
        ):
            base, title = OFFICES[office]
            special = election in SPECIALS
            party = PARTIES[primary] if primary else None
            key = f"{day}:WV:{base}" + (":special" if special else "") + (f":{party[1]}" if party else "")
            election_key = f"{day}:WV" if stage == "general" else f"{day}:WV:{stage}"
            election_id = cs.key("us.election", election_key)
            if election_key not in known_elections:
                known_elections.add(election_key)
                cs.add(
                    "election",
                    id=election_id,
                    polity_id=state,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election_id, f"{day:%B} {day.day}, {day.year} West Virginia special primary election")
                cs.add("slug", path=f"us/wv/{day}-{stage}", entity_id=election_id)
            existing = key in contests
            contest = cs.key("us.contest", key)
            if not existing:
                if title is None:
                    raise ValueError(f"{key} must already exist")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=cs.key("us.office", f"{base}:WV"),
                    district_area_id=area,
                    term="remainder" if special else "full",
                    magnitude=1,
                    round_number=1,
                    party_scope_id=parties[primary] if primary else None,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{day.year} West Virginia {title}{' special' if special else ''} {party[2]} primary"
                    if party
                    else f"{day.year} West Virginia {title} {'special election' if special else 'election'}",
                )
                cs.add(
                    "slug",
                    path=f"us/wv/{day}-{stage}/{base}" + (f"-{party[1]}" if party else ""),
                    entity_id=contest,
                )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            options: list[tuple[UUID, str, str]] = []
            outcomes: list[tuple[UUID, str, int | None]] = []
            on_file: dict[UUID, int] = {}
            if existing:
                options = conn.execute(
                    """select id, kind, ballot_label from contest_option
                       where contest_id = %s and kind <> 'others' and parent_option_id is null""",
                    [contest],
                ).fetchall()
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
                outcomes = conn.execute(
                    """select o.option_id, o.kind, o.seats from outcome o
                       join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                    [contest],
                ).fetchall()
                on_file = dict(
                    conn.execute(
                        """select o.id, sum(v.value)::bigint from result_value v
                           join result_set rs on rs.k = v.result_set_k and rs.is_default
                           join contest_option o on o.k = v.option_k
                           where rs.contest_id = %s and v.measure = 1 group by 1""",
                        [contest],
                    ).fetchall()
                )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, code, votes in (
                lines.group_by("name", "party", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                shown = cased(candidate)
                matches = [
                    option
                    for option, kind, text in options
                    if surname(text) == surname(shown) and (kind == "write_in") == (code == "W")
                ]
                if len(matches) > 1:
                    matches = [option for option, _, text in options if full(text) == full(shown)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                else:
                    probes = [full(shown), f"{plain(shown.split()[0])}|{surname(shown)}"]
                    if nickname := re.search(r"[(\"](\w+)[)\"]", shown):
                        probes.append(f"{plain(nickname[1])}|{surname(shown)}")
                    person = next(
                        (next(iter(found)) for probe in probes if len(found := people.get(probe, set())) == 1), None
                    )
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"WV:{slug(shown)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, shown)
                        for probe in probes:
                            people.setdefault(probe, set()).add(person)
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in"
                        if code == "W"
                        else "ticket"
                        if base == "president" and stage == "general"
                        else "candidate",
                        party_id=parties.get(code),
                        ballot_label=shown,
                    )
                    if code != "W":
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=shown,
                            is_independent=code not in PARTIES,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {candidate}")
                if option in on_file and on_file[option] != votes:
                    print(f"{key}: {shown} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if not lines["official"].all():
                cs.add("note", entity_id=result_set, kind="caveat", body_md=OFFICIAL)
            for candidate, county, votes in lines.select("name", "county", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[plain(county)],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                if party is None:
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
                elif base != "president":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
            filled += 1
        print(filled, "contests filled")
        return cs.write()
