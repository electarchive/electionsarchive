import re
from collections import defaultdict
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, medsl_state
from ea.sources.us.medsl_state_precincts import FILES, TITLES
from ea.store import DATA, connect

SOURCE = "us.medsl_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TAIL = (
    r"(?:,? -? ?(?P<seat>(?:PLACE|POSITION|SEAT|DEPT\.|DEPARTMENT|COURT|- JUSTICE POSITION) #?\w+|#\d+|\d+|\([A-Z]+\)"
    r"|:? ?TERM COMMENCING [\d/]+|UNEXPIRED [\w\- /,]+|INCUMBENT - PARTIAL TERM ENDING [\d/]+))?"
    r"(?: FULL TERM| 8 YEAR TERMS? \(\d\) POSITIONS?)?"
)
OFFICES = [
    (r"(?:STATE )?GOVERNOR(?: AND (?:LIEUTENANT|LT\.) GOVERNOR|/LIEUTENANT GOVERNOR)?", "Governor"),
    (r"(?:LIEUTENANT|LT\.) GOVERNOR", "Lieutenant Governor"),
    (r"(?:STATE )?ATTORNEY GENERAL", "Attorney General"),
    (r"(?:WASHINGTON STATE - )?SECRETARY OF STATE(?:\|STATEWIDE)?", "Secretary of State"),
    (r"SECRETARY OF THE STATE", "Secretary of the State"),
    (r"SECRETARY OF THE COMMONWEALTH", "Secretary of the Commonwealth"),
    (r"(?:STATE |GENERAL )?TREASURER(?: OF STATE)?", "Treasurer"),
    (r"(?:STATE )?AUDITOR(?: OF (?:STATE|ACCOUNTS|PUBLIC ACCOUNTS))?", "Auditor"),
    (r"AUDITOR GENERAL", "Auditor General"),
    (r"STATE AUDITOR AND INSPECTOR", "Auditor and Inspector"),
    (r"COMPTROLLER", "Comptroller"),
    (r"COMPTROLLER GENERAL", "Comptroller General"),
    (r"(?:STATE )?CONTROLLER", "Controller"),
    (r"CHIEF FINANCIAL OFFICER", "Chief Financial Officer"),
    (r"(?:STATE )?SUPERINTENDENT OF PUBLIC INSTRUCTION", "Superintendent of Public Instruction"),
    (r"STATE SCHOOL SUPERINTENDENT", "School Superintendent"),
    (r"STATE SUPERINTENDENT OF EDUCATION", "Superintendent of Education"),
    (r"COMMISSIONER OF AGRICULTURE|AGRICULTURE COMMISSIONER", "Commissioner of Agriculture"),
    (r"COMMISSIONER OF AGRICULTURE AND INDUSTRIES", "Commissioner of Agriculture and Industries"),
    (r"SECRETARY OF AGRICULTURE", "Secretary of Agriculture"),
    (r"COMMISSIONER OF INSURANCE", "Commissioner of Insurance"),
    (r"INSURANCE COMMISSIONER", "Insurance Commissioner"),
    (r"COMMISSIONER OF LABOR", "Commissioner of Labor"),
    (r"COMMISSIONER OF THE BUREAU OF LABOR AND INDUSTRIES", "Commissioner of the Bureau of Labor and Industries"),
    (r"COMMISSIONER OF PUBLIC LANDS", "Commissioner of Public Lands"),
    (r"(?:PUBLIC )?LANDS? COMMISSIONER", "Land Commissioner"),
    (r"COMMISSIONER OF SCHOOL AND PUBLIC LANDS", "Commissioner of School and Public Lands"),
    (r"COMMISSIONER OF STATE LANDS", "Commissioner of State Lands"),
    (r"CORPORATION COMMISSIONER", "Corporation Commissioner"),
    (r"PUBLIC SERVICE COMMISSIONER(?: - 6 +YEAR TERM)?", "Public Service Commissioner"),
    (r"PUBLIC SERVICE COMMISSION(?:, (?P<seat>(?:PLACE|DISTRICT) \d+)(?: - \w+)?)?", "Public Service Commissioner"),
    (
        r"PRESIDENT, PUBLIC SERVICE COMMISSION|PUBLIC SERVICE COMMISSION PRESIDENT",
        "President of the Public Service Commission",
    ),
    (r"PUBLIC UTILITIES COMMISSIONER", "Public Utilities Commissioner"),
    (r"RAILROAD COMMISSIONER(?: (?P<seat>\d))?", "Railroad Commissioner"),
    (r"TAX COMMISSIONER", "Tax Commissioner"),
    (r"STATE MINE INSPECTOR", "Mine Inspector"),
    (r"CLERK OF THE SUPREME COURT", "Clerk of the Supreme Court"),
    (
        r"(?:MEMBER OF THE )?STATE BOARD OF EDUCATION(?: MEMBER \(AT LARGE\)| 8 YEAR TERMS \(2\) POSITIONS)?",
        "Member of the State Board of Education",
    ),
    (
        r"AT LARGE - BOARD OF EDUCATION|AT-LARGE MEMBER OF THE STATE BOARD OF EDUCATION",
        "At-Large Member of the State Board of Education",
    ),
    (
        r"MEMBER OF THE UNIVERSITY OF MICHIGAN BOARD OF REGENTS|REGENT OF THE UNIVERSITY OF MICHIGAN.*",
        "Regent of the University of Michigan",
    ),
    (
        r"MEMBER OF THE MICHIGAN STATE UNIVERSITY BOARD OF TRUSTEES|TRUSTEE OF MICHIGAN STATE UNIVERSITY.*",
        "Trustee of Michigan State University",
    ),
    (
        r"MEMBER OF THE WAYNE STATE UNIVERSITY BOARD OF GOVERNORS|GOVERNOR OF WAYNE STATE UNIVERSITY.*",
        "Governor of Wayne State University",
    ),
    (r"REGENT OF THE UNIVERSITY OF COLORADO(?:\|0)?", "Regent of the University of Colorado"),
    (r"AT-LARGE TRUSTEE", "Trustee of the Office of Hawaiian Affairs"),
    (
        r"(?P<seat>HAWAII|KAUAI|MOLOKAI) RESIDENT TRUSTEE(?: VACANCY)?|RESIDENT TRUSTEE - (?P<island>MAUI|OAHU)",
        "Trustee of the Office of Hawaiian Affairs",
    ),
    (
        r"(?:STATE )?(?:CHIEF JUSTICE(?: OF THE SUPREME COURT| - SUPREME COURT)|SUPREME COURT CHIEF JUSTICE)" + TAIL,
        "Chief Justice of the Supreme Court",
    ),
    (r"SUPREME COURT OF APPEALS JUSTICE", "Justice of the Supreme Court of Appeals"),
    (
        r"COURT OF CRIMINAL APPEALS PRESIDING JUDGE|CRIMINAL COURT OF APPEALS - PRESIDING JUDGE",
        "Presiding Judge of the Court of Criminal Appeals",
    ),
    (
        r"(?:STATE )?(?:SUPREME COURT(?: ASSOCIATE JUSTICE| JUSTICE| JUDGE)?|(?:ASSOCIATE )?JUSTICE OF (?:THE )?SUPREME"
        r" COURT|ASSOCIATE JUSTICE(?: OF THE SUPREME COURT| - SUPREME COURT)|JUDGE OF THE SUPREME COURT)" + TAIL,
        "Justice of the Supreme Court",
    ),
    (
        r"(?:COURT OF CRIMINAL APPEALS(?: JUDGE| JUSTICE)?|CRIMINAL COURT OF APPEALS)" + TAIL,
        "Judge of the Court of Criminal Appeals",
    ),
    (r"COURT OF CIVIL APPEALS(?: JUDGE)?" + TAIL, "Judge of the Court of Civil Appeals"),
    (r"JUDGE (?:- )?SPECIAL APPEALS(?: AT LARGE)?", "Judge of the Court of Special Appeals"),
    (
        r"(?:COURT OF APPEALS(?: JUDGE| ASSOCIATE JUDGE)?|JUDGE (?:OF )?(?:THE )?COURT OF APPEALS|JUDGE - COURT OF APPEALS)"
        + TAIL,
        "Judge of the Court of Appeals",
    ),
]
EXECUTIVE = {"Governor", "Lieutenant Governor", "Attorney General", "Secretary of State", "Treasurer", "Auditor"}
PLACES = {"AL": "Place", "TX": "Place", "GA": "District", "WA": "Position", "OR": "Position", "NM": "Position"}
SEATS_2016 = {
    ("MI", "Member of the State Board of Education"): 2,
    ("MI", "Regent of the University of Michigan"): 2,
    ("MI", "Trustee of Michigan State University"): 2,
    ("MI", "Governor of Wayne State University"): 2,
    ("AZ", "Corporation Commissioner"): 3,
}
MAJORITY = {"GA"}
RETENTION = r"^(YES|NO)\b|- (YES|NO|UNDERVOTES)$|RETAIN|REJECT|^FOR$|^AGAINST$"
PSEUDO = r"^(OVER|UNDER) ?VOTES?$|^NONE$|^NOT QUALIFIED$|^BLANKS?$|^SPOILED$|^VOID$"
NOTA = "NONE OF THESE CANDIDATES"
LABELS = {
    "DEMOCRATIC": "DEMOCRAT",
    "DEM": "DEMOCRAT",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "PROG": "PROGRESSIVE",
    "NP": "NONPARTISAN",
    "DEMOCRAT-REPUBLICAN": "DEMOCRAT",
    "PROGRESSIVE-DEMOCRAT": "PROGRESSIVE",
    "NO AFFILIATION": "UNAFFILIATED",
    "NO POLITICAL PARTY": "UNAFFILIATED",
    "NO PARTY AFFILIATION": "UNAFFILIATED",
    "INDEPENDENT NOMINATION": "INDEPENDENT",
    "WCP": "WORKING CLASS",
    "US TAXPAYERS": "U.S. TAXPAYERS",
    "UNITED CITIZEN": "UNITED CITIZENS",
    "WORKING FAMILY": "WORKING FAMILIES",
    "UNITED UTAH": "UNITED UTAH",
    "OTHER": "",
    "BOT": "",
}
METHOD = (
    "County totals are the sums of the precinct rows in MEDSL's precinct returns for the year, which compile each "
    "state's certified canvass. The winner shown is the candidate with the most votes in those sums."
)
CLOSE = (
    "The race was within half a percent in these sums, closer than the gaps between MEDSL's compilation and the "
    "certified canvass can be trusted to settle, so no winner is recorded from them."
)
MISSING = "MEDSL's file has no rows for {counties} in this race, so those votes are missing from these totals."


def parse() -> None:
    frame = (
        pl.concat(
            [
                medsl.precincts({("statewide", year): raw.cached(source, url).path})
                for year, files in FILES.items()
                for source, url in files
            ],
            how="diagonal_relaxed",
        )
        .with_columns(
            pl.col("precinct", "jurisdiction", "office").str.strip_chars(),
            seat=pl.col("seat").str.strip_chars().str.to_uppercase(),
            county=pl.col("fips").str.slice(0, 5),
            party=pl.col("party")
            .str.to_uppercase()
            .str.strip_chars()
            .str.replace(r"\s*[,/].*$", "")
            .str.replace(r" PARTY$", "")
            .replace(LABELS),
            candidate=pl.when(
                pl.col("candidate").str.contains(r"^[^,]+, [^,]+$")
                & ~pl.col("candidate").str.contains(r", (JR|SR|II|III|IV)\.?$")
            )
            .then(pl.col("candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1"))
            .otherwise("candidate"),
        )
        .with_columns(party=pl.when(pl.col("party") != "").then("party"))
        .filter(~pl.col("candidate").str.contains(PSEUDO), ~pl.col("candidate").is_in(list(medsl_state.DROPPED)))
    )
    race = ["year", "state", "office", "seat"]
    recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select(*race).unique()
    frame = pl.concat(
        [
            frame.filter(pl.col("stage") == "GEN").join(recounted, on=race, how="anti", nulls_equal=True),
            frame.filter(pl.col("stage") == "GEN RECOUNT"),
        ]
    )
    counties = (
        frame.filter(pl.col("county").is_not_null())
        .group_by("year", "state")
        .agg(pl.col("county").n_unique().alias("counties"))
        .group_by("state")
        .agg(pl.col("counties").max())
    )
    named = []
    for office, seat in frame.select("office", "seat").unique().iter_rows():
        for pattern, title in OFFICES:
            if match := re.fullmatch(pattern, office):
                groups = match.groupdict()
                tail = groups.get("seat") or groups.get("island")
                column = None
                if seat and not re.fullmatch(r"STATEWIDE|0+|AT-LARGE|JUSTICE OF THE SUPREME COURT|-?", seat):
                    column = re.sub(r"^STATEWIDE, ", "", seat).split(", ")[-1]
                post = tail or column
                if post and (
                    number := re.fullmatch(r"(?:PLACE|POSITION|SEAT|COURT|- JUSTICE POSITION)? ?#?0*(\d+)", post)
                ):
                    post = number[1]
                named.append((office, seat, title, post))
                break
    frame = frame.join(
        pl.DataFrame(named, schema=["office", "seat", "title", "post"], orient="row"),
        on=["office", "seat"],
        nulls_equal=True,
    ).filter(~pl.col("post").fill_null("").str.starts_with("WARD"))
    contest = ["year", "day", "state", "title", "post", "special"]
    races = (
        frame.filter(pl.col("measure") == 1)
        .group_by(*contest, "candidate", "writein")
        .agg(pl.col("votes").sum(), reach=pl.col("county").filter(pl.col("votes") > 0).n_unique())
        .group_by(contest)
        .agg(
            pl.col("reach").max(),
            retention=pl.col("candidate").filter(~pl.col("writein")).str.contains(RETENTION).all(),
            top=pl.col("votes").filter(~pl.col("writein")).sort(descending=True).head(2).sum() / pl.col("votes").sum(),
        )
        .join(
            frame.group_by(contest).agg(
                pl.col("county").filter(pl.col("votes") > 0).drop_nulls().unique().sort().alias("present"),
                magnitude=pl.col("magnitude").cast(pl.Int16, strict=False).max(),
            ),
            on=contest,
            nulls_equal=True,
        )
        .join(counties, on="state", how="left")
        .with_columns(
            magnitude=pl.coalesce(
                "magnitude",
                pl.struct("state", "title").map_elements(
                    lambda row: SEATS_2016.get((row["state"], row["title"]), 1), return_dtype=pl.Int16
                ),
            )
        )
    )
    statewide = (
        pl.col("counties").is_null()
        | ((pl.col("state") == "DC") & pl.col("post").is_null())
        | (pl.col("reach") >= 0.9 * pl.col("counties"))
    )
    lumped = ~pl.col("title").is_in(list(EXECUTIVE)) & (pl.col("magnitude") == 1) & (pl.col("top") < 0.8)
    for label, rows in (
        ("retention", races.filter(statewide & pl.col("retention"))),
        ("lumped", races.filter(statewide & ~pl.col("retention") & lumped)),
    ):
        print(f"{rows.height} {label} races skipped")
        print(rows.select(*contest).sort("state", "year"))
    races = races.filter(statewide & ~pl.col("retention") & ~lumped)
    frame = (
        frame.join(races.select(*contest, "magnitude", "present"), on=contest, nulls_equal=True)
        .with_columns(
            kind=pl.when(pl.col("kind").is_in(["county", "place"]) & (pl.col("precinct") != ""))
            .then(pl.lit("precinct"))
            .otherwise("kind"),
        )
        .with_columns(
            fips=pl.when(pl.col("kind") == "precinct").then("code"),
            code=pl.when(pl.col("kind") == "precinct")
            .then(pl.concat_str("year", "state", "code", "jurisdiction", "precinct", separator=":"))
            .otherwise("code"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("kind") == "precinct").group_by("code").agg(
        pl.col("state").first(), pl.col("fips").first().alias("county"), pl.col("day").min()
    ).sort("code").write_parquet(PARSED / "units.parquet")
    frame.group_by(
        *contest,
        "magnitude",
        "kind",
        "code",
        "county",
        "candidate",
        "party",
        "writein",
        "measure",
        maintain_order=True,
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "returns.parquet")
    races.select(*contest, "magnitude", "present", "counties").write_parquet(PARSED / "races.parquet")
    print(races.group_by("year").len().sort("year"))


def emit() -> Path:
    returns = pl.read_parquet(PARSED / "returns.parquet").filter(pl.col("measure") == 1)
    races = pl.read_parquet(PARSED / "races.parquet")
    days = sorted({str(day) for day in races["day"].unique()})
    with connect() as conn:
        existing: dict[tuple[str, str], dict[str, list[list[str]]]] = defaultdict(lambda: defaultdict(list))
        for key, label, person in conn.execute(
            """select cx.value, o.ballot_label,
                      (select n.text from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                       where cy.option_id = o.id and cy.role = 'head' limit 1)
               from external_id cx join contest c on c.id = cx.entity_id and c.kind = 'office'
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null and o.kind <> 'write_in'
               where cx.scheme = 'us.contest' and left(cx.value, 10) = any(%s)
                 and cx.value ~ '^[0-9-]{10}:[A-Z]{2}:[a-z]' and cx.value !~ ':(president|senate|house)'""",
            [days],
        ).fetchall():
            existing[(key[:10], key[11:13])][key].append([slug(text) for text in (label, person) if text])
        keys = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.contest'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        counties = dict(conn.execute("select code, id from reporting_unit where scheme_code = 'us.county'").fetchall())
        state_units = {
            abbreviation: (unit, area)
            for abbreviation, unit, area in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide executive, judicial, and board elections 2016–2024 by county from MEDSL's precinct returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())
            return re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), text)

        def party(state: str, label: str | None) -> tuple[UUID | None, bool]:
            if label is None:
                return None, False
            label = medsl_state.EXPANDED.get(label, label)
            if label in medsl_state.INDEPENDENT:
                return None, True
            if label in medsl_state.NATIONAL:
                return cs.key("us.party", medsl_state.NATIONAL[label]), False
            text = title(label.removesuffix(" PARTY")) + " Party"
            candidates = [f"{state}:{slug(text)}", f"{state}:{slug(text.removesuffix(' Party'))}"]
            key = next((key for key in candidates if key in parties_known), candidates[0])
            found = cs.key("us.party", key)
            if found in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=found, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(found, text)
            return found, False

        def surname(text: str) -> str:
            words = [
                word for word in re.sub(r'"[^"]*"', " ", text).split() if word not in {"JR", "SR", "II", "III", "IV"}
            ]
            return re.sub(r"[^a-z]", "", slug(words[-1])) if words else ""

        sources: dict[int, UUID] = {}
        for year, (text, doi) in TITLES.items():
            sources[year] = cs.source(medsl_state.PUBLISHER, text)
            if sources[year] in cs.minted:
                raise ValueError(f"no source {text}")
            for origin, url in FILES[year]:
                cs.artifact(raw.cached(origin, url), sources[year])
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "two_round")}
        values: list[tuple[str, str, str, int]] = []
        persons: set[str] = set()
        mapping: list[tuple[int, str, str, str | None, bool, str]] = []
        contest_columns = ["year", "day", "state", "title", "post", "special"]
        for row in races.sort("state", "year", "title", "post", nulls_last=True).iter_rows(named=True):
            year, day, state, office, post, special = (row[column] for column in contest_columns)
            magnitude = int(row["magnitude"] or 1)
            lines = (
                returns.filter(*(pl.col(column).eq_missing(row[column]) for column in contest_columns))
                .with_columns(
                    candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"),
                    party=pl.when(~pl.col("writein")).then("party"),
                )
                .with_columns(token=pl.col("candidate").str.replace_all(r"[^A-Z0-9]", ""))
                .with_columns(pl.col("candidate").first().over("token"))
            )
            ranked = (
                lines.filter(~pl.col("writein"), pl.col("candidate") != NOTA)
                .group_by("candidate")
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            shown = None
            if post:
                shown = f"{PLACES.get(state, 'Seat')} {post}" if post.isdigit() else title(post.strip("()"))
            contest_key = (
                f"{day}:{state}:{slug(office)}" + (f":{slug(shown)}" if shown else "") + (":special" if special else "")
            )
            leaders = [
                (surname(re.split(r" AND | & |/", candidate)[0]), set(slug(candidate).split("-")))
                for candidate in ranked["candidate"].head(min(2, magnitude + 1))
            ]
            hits = {
                key: sum(
                    any(
                        re.sub(r"[^a-z]", "", re.sub(r"-(jr|sr|ii|iii|iv)$", "", text)).endswith(leader)
                        or f"-{leader}-" in f"-{text}-"
                        or re.sub(r"-(jr|sr|ii|iii|iv)$", "", text).split("-")[-1] in words
                        for names in options
                        for text in names
                    )
                    for leader, words in leaders
                )
                for key, options in existing[(str(day), state)].items()
            }
            matches = [key for key, count in hits.items() if leaders and count == len(leaders)]
            if not matches and hits.get(contest_key, 0) > 0:
                matches = [contest_key]
            if len(matches) > 1:
                matches = [key for key in matches if key.split(":")[2] == slug(office)] or matches
            if len(matches) > 1:
                matches = [max(matches, key=lambda key: len(existing[(str(day), state)][key]))]
            if matches:
                mapping.append((year, state, office, post, special, matches[0]))
                continue
            if contest_key in keys:
                print(f"{contest_key}: taken by a contest with other candidates, skipped")
                continue
            keys.add(contest_key)
            mapping.append((year, state, office, post, special, contest_key))
            election = cs.key("us.election", f"{day}:{state}")
            if election in cs.minted:
                raise ValueError(f"no election {day}:{state}")
            office_key = f"{slug(office)}:{state}"
            office_id = cs.key("us.office", office_key)
            if office_key not in offices_known:
                offices_known.add(office_key)
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(
                    office_id,
                    office
                    if states[state] in office or "University" in office
                    else f"{office} of {'the ' if state == 'DC' else ''}{states[state]}",
                )
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=state_units[state][1],
                term="remainder" if special else "full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["two_round"]
                if state in MAJORITY
                else systems["fptp"]
                if magnitude == 1
                else systems["block"],
                default_result_kind="counted",
            )
            name(
                contest,
                f"{year} {states[state]} {office} {'special election' if special else 'election'}"
                + (f" ({shown})" if shown else ""),
            )
            path = (
                f"us/{state.lower()}/{day}-general/{slug(office)}"
                + (f"-{slug(shown)}" if shown else "")
                + ("-special" if special else "")
            )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            missing = int(row["counties"] or 0) - len(row["present"])
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            if missing > 0:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=MISSING.format(counties=f"{missing} of the state's {row['counties']} counties"),
                )
            totals: list[tuple[int, UUID]] = []
            total = int(lines["votes"].sum())
            for (candidate, pooled), rows in (
                lines.group_by("candidate", "writein", "party", maintain_order=True)
                .agg(pl.col("votes").sum())
                .group_by("candidate", "writein", maintain_order=True)
            ):
                nota = candidate == NOTA
                option_key = (
                    f"{contest_key}:write-ins"
                    if pooled
                    else f"{contest_key}:none-of-these-candidates"
                    if nota
                    else f"{contest_key}:{slug(str(candidate))}"
                )
                root = cs.key("us.option", option_key)
                labels = rows["party"].to_list()
                resolved = [party(state, label) for label in labels] if not (pooled or nota) else [(None, False)]
                ballot = "Write-ins" if pooled else "None of These Candidates" if nota else title(str(candidate))
                cs.add(
                    "contest_option",
                    id=root,
                    contest_id=contest,
                    kind="write_in" if pooled else "none_of_the_above" if nota else "candidate",
                    party_id=resolved[0][0] if len(rows) == 1 else None,
                    ballot_label=ballot,
                )
                if not (pooled or nota):
                    person_key = f"{state}:{slug(str(candidate))}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted and person_key not in persons:
                        persons.add(person_key)
                        cs.add("person", id=person)
                        name(person, ballot)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=ballot,
                        is_independent=all(independent for _, independent in resolved),
                    )
                targets = [root]
                if len(rows) > 1 and not (pooled or nota):
                    targets = []
                    for label, (party_id, _) in zip(labels, resolved, strict=True):
                        target = cs.key("us.option", f"{option_key}:{slug(label or 'no party')}")
                        cs.add(
                            "contest_option",
                            id=target,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="candidate",
                            party_id=party_id,
                            ballot_label=f"{ballot} ({title(label or 'no party')})",
                        )
                        targets.append(target)
                for label, target in zip(labels, targets * len(labels) if len(targets) == 1 else targets, strict=True):
                    cells = lines.filter(
                        pl.col("candidate") == candidate, pl.col("writein") == pooled, pl.col("party").eq_missing(label)
                    )
                    for county, votes in (
                        cells.with_columns(
                            unit=pl.when(pl.col("county").is_in(list(counties))).then("county").otherwise(pl.lit(state))
                        )
                        .group_by("unit")
                        .agg(pl.col("votes").sum())
                        .iter_rows()
                    ):
                        unit = counties[county] if county in counties else state_units[state][0]
                        values.append((str(result_set), str(unit), str(target), int(votes)))
                if not (pooled or nota):
                    totals.append((int(rows["votes"].sum()), root))
            if missing > 0:
                continue
            totals.sort(key=lambda pair: -pair[0])
            if len(totals) > magnitude and totals[magnitude - 1][0] - totals[magnitude][0] <= total * 0.005:
                print(f"{contest_key}: within half a percent, no outcome")
                cs.add("note", entity_id=result_set, kind="caveat", body_md=CLOSE)
                continue
            if state in MAJORITY and totals and totals[0][0] * 2 <= total:
                for _, root in totals[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=root, kind="advanced", seats=None)
                continue
            for _, root in totals[:magnitude]:
                cs.add("outcome", result_set_id=result_set, option_id=root, kind="elected", seats=1)
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            )
            .group_by("result_set_id", "unit_id", "option_id")
            .agg(pl.col("value").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        pl.DataFrame(
            mapping,
            schema={
                "year": pl.Int32,
                "state": pl.String,
                "title": pl.String,
                "post": pl.String,
                "special": pl.Boolean,
                "key": pl.String,
            },
            orient="row",
        ).write_parquet(PARSED / "contests.parquet")
        return cs.write()
