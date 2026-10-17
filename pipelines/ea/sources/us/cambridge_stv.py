import html
import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.cambridge_stv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Cambridge Election Commission"
BASE = "https://www.cambridgema.gov/"
CONTESTS = {
    "2011-11-08:council": (
        "alged:2511000_2011_11_cambridge_MA_City Council_synthetic at-large d_9",
        "election2011/official/ccouncil/Council",
    ),
    "2011-11-08:school": (None, "election2011/official/schoolcomm/School"),
    "2013-11-05:council": (
        "alged:2511000_2013_11_cambridge_MA_City Council_synthetic at-large b_9",
        "election2013/official/Council",
    ),
    "2013-11-05:school": (None, "election2013/official/School"),
    "2015-11-03:council": (
        "alged:2511000_2015_11_cambridge_MA_City Council_synthetic at-large d_9",
        "election2015/ccouncil/15CouncFinal",
    ),
    "2015-11-03:school": (None, "election2015/schoolcommittee/15SchoolFinal"),
    "2017-11-07:council": (
        "alged:2511000_2017_11_cambridge_MA_City Council_synthetic at-large b_9",
        "election2017/Council%20Order",
    ),
    "2017-11-07:school": (None, "election2017/School%20Order"),
    "2019-11-05:council": (
        "alged:2511000_2019_11_cambridge_MA_City Council_synthetic at-large d_9",
        "election2019/official/Council",
    ),
    "2019-11-05:school": (None, "election2019/official/School"),
    "2021-11-02:council": (
        "alged:2511000_2021_11_cambridge_MA_City Council_synthetic at-large b_9",
        "election2021/ccouncil-official/21CouncilFinal",
    ),
    "2021-11-02:school": (None, "election2021/schoolcommittee-official/21SchoolFinal"),
    "2023-11-07:council": (None, "election2023/official/Council"),
    "2023-11-07:school": (None, "election2023/official/School"),
    "2025-11-04:council": (None, "election2025/official/Council"),
    "2025-11-04:school": (None, "election2025/official/School"),
}
OFFICIAL = (
    "Final count by the Cambridge Election Commission using proportional representation, from the ChoicePlus "
    "Pro count-by-count report. Count 1 is the tally of first choices. The quota is calculated by dividing the"
    " number of valid ballots by the number of seats plus one, dropping any fraction, and adding one. "
    "Candidates are elected when they reach the quota. In each subsequent count, the surplus of one elected "
    "candidate is transferred by drawing every nth ballot at random and moving it whole to its next continuing"
    " choice. When no surplus remains, the lowest candidates are eliminated and their ballots move to their "
    "next continuing choices; candidates with fewer than 50 votes are eliminated together. If only as many "
    "candidates remain as there are open seats, they are elected even if they are below the quota. Each "
    "candidate's value is a running total: candidates elected at the quota keep their value after their "
    "surplus moves, and eliminated candidates show 0 in the count that moves their ballots. Write-ins are "
    "summed into one option. Exhausted counts are valid ballots with no continuing choice. Invalid ballots are"
    " excluded from the valid total."
)

RESULTS = "https://www.cambridgema.gov/-/media/Files/electioncommission/electionresults/"
DECLARED = {
    "1999-11-02": (
        "alged:2511000_1999_11_cambridge_MA_City Council_synthetic at-large d_9",
        "https://web.archive.org/web/20000925121520id_/http://www.ci.cambridge.ma.us:80/~Election/99council.pdf",
        [
            "Anthony Galluccio",
            "Kathleen Born",
            "Marjorie Decker",
            "Michael Sullivan",
            "Timothy Toomey",
            "James Braude",
            "Henrietta Davis",
            "Kenneth Reeves",
            "David Maher",
        ],
    ),
    "2001-11-06": (
        "alged:2511000_2001_11_cambridge_MA_City Council_synthetic at-large b_9",
        RESULTS + "2001officialcitycouncilresults.pdf",
        [
            "Anthony Galluccio",
            "Brian Murphy",
            "Henrietta Davis",
            "Michael Sullivan",
            "Marjorie Decker",
            "Denise Simmons",
            "Timothy Toomey",
            "David Maher",
            "Kenneth Reeves",
        ],
    ),
    "2003-11-04": (
        "alged:2511000_2003_11_cambridge_MA_City Council_synthetic at-large d_9",
        RESULTS + "2003citycouncil.pdf",
        [
            "Anthony Galluccio",
            "Henrietta Davis",
            "Michael Sullivan",
            "Marjorie Decker",
            "Brian Murphy",
            "Timothy Toomey",
            "Kenneth Reeves",
            "David Maher",
            "Denise Simmons",
        ],
    ),
    "2009-11-03": (
        "alged:2511000_2009_11_cambridge_MA_City Council_synthetic at-large b_9",
        RESULTS + "2009citycouncilresults.pdf",
        [
            "Henrietta Davis",
            "Denise Simmons",
            "Timothy Toomey",
            "Craig Kelley",
            "David Maher",
            "Kenneth Reeves",
            "Sam Seidel",
            "Marjorie Decker",
            "Leland Cheung",
        ],
    ),
}
DECLARED_NOTE = (
    "Nine candidates elected, listed in order of election from the Cambridge Election Commission's certified "
    "proportional representation count sheet, signed by the Director of the Count and the Auditor. This set records "
    "only the elected candidates; vote counts are not included. The American Local Government Elections Database's "
    "first-choice totals are available as a separate set, but that database marked the nine candidates with the most "
    "first choices as elected, which does not match the certified count."
)


def pages(base: str) -> list[str]:
    start = BASE + base + "%20Round.htm"
    text = raw.cached(SOURCE, start).path.read_text(encoding="latin-1")
    count = max(int(number) for number in re.findall(r"Round(\d+)\.htm", text))
    return [start] + [f"{BASE}{base}%20Round{number}.htm" for number in range(1, count + 1)]


def fetch() -> None:
    for _, base in CONTESTS.values():
        raw.fetch(SOURCE, BASE + base + "%20Round.htm", suffix=".htm")
        for page in pages(base)[1:]:
            raw.fetch(SOURCE, page, suffix=".htm")
    for _, file, _ in DECLARED.values():
        raw.fetch(SOURCE, file, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: dict[str, dict[str, object]] = {}
    for key, (_, base) in CONTESTS.items():
        files = pages(base)

        def read(file: str) -> str:
            content = raw.cached(SOURCE, file).path.read_bytes()
            try:
                return content.decode()
            except UnicodeDecodeError:
                return content.decode("cp1252")

        def clean(text: str) -> str:
            text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
            return re.sub(r"[ \xa0\t]+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()

        def proper(label: str, inverted: bool) -> str:
            parts = [part.strip() for part in label.split(",")]
            if not inverted or len(parts) == 1:
                return label
            return f"{parts[1]} {parts[0]}" if len(parts) == 2 else f"{parts[2]} {parts[0]}, {parts[1]}"

        start = re.sub(r"\n\s*", "\n", clean(re.sub(r"</t[dh]>|</tr>|</p>|</h\d>", "\n", read(files[0]))))
        figures = re.search(
            r"([\d,]+) valid ballots\.\s*Electing (\d+) candidates\.\s*Quota is (\d+) votes\.\s*"
            r"There w(?:ere|as) ([\d,]+) invalid ballots?\.\s*\(([\d,]+) total ballots processed",
            start,
        )
        if figures is None:
            raise ValueError(f"{key}: no ballot figures")
        valid, seats, quota, invalid, processed = (int(group.replace(",", "")) for group in figures.groups())
        if valid + invalid != processed or quota != valid // (seats + 1) + 1:
            raise ValueError(f"{key}: {valid}, {invalid}, {processed}, {quota}")
        names = start[start.index("CANDIDATES") + len("CANDIDATES") :].strip().split("\n")
        names = [name.strip() for name in names[: names.index("Start")] if name.strip()]
        inverted = sum("," in name for name in names) * 2 > len(names)
        rounds: list[dict[str, int]] = []
        status: dict[str, str] = {}
        for file in files[1:]:
            tally: dict[str, int] = {}
            pile = totals = None
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", read(file), flags=re.IGNORECASE | re.DOTALL):
                cells = [
                    clean(cell)
                    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
                ]
                if len(cells) < 3 or not re.fullmatch(r"[+-]?\d+", cells[2]):
                    continue
                label, total = cells[0], int(cells[2])
                if label == "EXHAUSTED PILE:":
                    pile = total
                elif label == "TOTALS:":
                    totals = total
                else:
                    if label.endswith(".") and label not in names:
                        (label,) = [name for name in names if name.startswith(label[:-1])]
                    if label not in names:
                        continue
                    tally[label] = total
                    status[label] = cells[3] if len(cells) > 3 else ""
            if set(tally) != set(names) or pile is None or totals != valid or sum(tally.values()) + pile != valid:
                raise ValueError(f"{key} {file}: {sum(tally.values())} and {pile} against {valid}")
            rounds.append(tally)
        elected = [label for label in names if status[label].startswith("ELECTED")]
        if len(elected) != seats or any(not status[label] for label in names):
            raise ValueError(f"{key}: {elected}")
        contests[key] = {
            "files": files,
            "processed": processed,
            "valid": valid,
            "invalid": invalid,
            "seats": seats,
            "status": {proper(label, inverted): text for label, text in status.items()},
            "rounds": [{proper(label, inverted): votes for label, votes in tally.items()} for tally in rounds],
        }
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cambridge's City Council and School Committee elections, 2011 to 2025, with every count of the "
            "Election Commission's final proportional representation tallies",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr", "iii")]

        county = cs.area("us.county", "25017")
        city = cs.area("us.place", "2511000")
        whole = cs.unit("us.place", "2511000")
        council = cs.key("us.body", "MA:2511000:council")
        if {city, whole, council} & cs.minted:
            raise ValueError("Cambridge and its council must already exist")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        (state,) = conn.execute(
            "select polity_id from polity_territory where area_id = %s", [cs.area("us.state", "25")]
        ).fetchone() or (None,)
        stv = cs.key("us.electoral_system", "cambridge:stv")
        cs.add(
            "electoral_system",
            id=stv,
            polity_id=polity,
            rules={"formula": "stv", "quota": "droop", "surplus": "cincinnati"},
            rules_version=1,
        )
        name(stv, "Cambridge proportional representation")
        school = cs.key("us.body", "MA:2511000:school-committee")
        cs.add("body", id=school, polity_id=polity, kind="council")
        name(school, "Cambridge School Committee")
        restated = {existing for existing, _ in CONTESTS.values() if existing is not None}

        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[city, county]],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, area == city))

        columns = (
            "election_id",
            "kind",
            "office_id",
            "body_id",
            "district_area_id",
            "seat_id",
            "boundary_set_id",
            "magnitude",
            "party_scope_id",
            "round_number",
            "previous_round_id",
            "allocation_parent_id",
            "rules_override",
            "default_result_kind",
            "term",
        )
        for contest, value in conn.execute(
            """select k.id, x.value from contest k join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               where x.value like 'alged:2511000_%%'"""
        ).fetchall():
            if value in restated:
                continue
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            cs.add("contest", id=contest, **dict(zip(columns, row or (), strict=True)), electoral_system_id=stv)
        done: set[UUID] = set()
        for key, (existing, _) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            kind = key[11:]
            record = contests[key]
            rounds: list[dict[str, int]] = record["rounds"]
            election = cs.key("us.election", f"{day}:MA:2511000")
            if election in cs.minted and election not in done:
                done.add(election)
                parent = cs.key("us.election", f"{day}:MA")
                if parent in cs.minted:
                    cs.add(
                        "election",
                        id=parent,
                        polity_id=state,
                        parent_id=None,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(parent, f"{day.year} Massachusetts local elections")
                    cs.add("slug", path=f"us/ma/{day}-local", entity_id=parent)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} Cambridge local election")
                cs.add("slug", path=f"us/ma/cambridge/{day}-local", entity_id=election)
            if existing is None:
                contest = cs.key("us.contest", f"{day}:MA:2511000:{kind}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council if kind == "council" else school,
                    district_area_id=city,
                    magnitude=record["seats"],
                    round_number=1,
                    electoral_system_id=stv,
                    default_result_kind="counted",
                    term="full",
                )
                name(
                    contest,
                    f"{day.year} Cambridge City Council election, At-Large"
                    if kind == "council"
                    else f"{day.year} Cambridge School Committee election",
                )
                cs.add(
                    "slug",
                    path=f"us/ma/cambridge/{day}-local/"
                    + ("council-at-large" if kind == "council" else "school-committee"),
                    entity_id=contest,
                )
            else:
                contest = cs.key("us.contest", existing)
                row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
                if contest in cs.minted or row is None:
                    raise ValueError(f"{key}: {existing} must already exist")
                cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=stv)
                if row[0] != election:
                    raise ValueError(f"{key}: {existing} is in another election")
                previous = cs.key("us.alged.result_set", existing)
                for values in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where id = %s""",
                    [previous],
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
                                values,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )

            seat = "City Council" if kind == "council" else "School Committee"
            title = f"{day:%B} {day.day}, {day.year} Cambridge municipal election: {seat}, final count-by-count report"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=record["files"][0],
                license="Public record",
            )
            for file in record["files"]:
                cs.artifact(raw.cached(SOURCE, file), source)

            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                if label.casefold().startswith("write-in"):
                    if "write-in" in options:
                        options[label] = options["write-in"]
                        continue
                    found = [
                        option
                        for (option,) in conn.execute(
                            "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                        ).fetchall()
                    ]
                    if len(found) > 1:
                        raise ValueError(f"{key}: {len(found)} write-in options")
                    options[label] = found[0] if found else cs.key("us.option", f"{contest}:write_in:scattered")
                    if not found:
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    options["write-in"] = options[label]
                    continue
                words = spell(label)
                if (words[0], words[-1]) in current:
                    options[label] = current[words[0], words[-1]]
                    continue
                near = [
                    option
                    for (_, last), option in current.items()
                    if SequenceMatcher(None, last, words[-1]).ratio() >= 0.8
                ]
                if len(near) == 1:
                    options[label] = near[0]
                    continue
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _ in known}
                local = {person for person, here in known if here}
                if len(local) == 1:
                    matches = local
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    if person in cs.minted and person not in {p for found in people.values() for p, _ in found}:
                        cs.add("person", id=person)
                        name(person, label)
                    people.setdefault((words[0], words[-1]), set()).add((person, True))
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len({options[label] for label in options if not label.casefold().startswith("write-in")}) != len(
                [label for label in options if not label.casefold().startswith("write-in")]
            ):
                raise ValueError(f"{key}: two candidates share an option")

            result = cs.key(f"{SOURCE}.result_set", key)
            if result not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=OFFICIAL)
            values = [
                (None, 3, 0, record["processed"]),
                (None, 5, 0, record["invalid"]),
                (None, 4, 1, record["valid"]),
            ]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, record["valid"] - sum(tally.values())))
                merged: dict[UUID, int] = {}
                for label, votes in tally.items():
                    if any(earlier.get(label) == 0 for earlier in rounds[: number - 1]):
                        continue
                    merged[options[label]] = merged.get(options[label], 0) + votes
                values.extend((option, 1, number, votes) for option, votes in merged.items())
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=whole,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=votes,
                )
            fates: dict[UUID, set[str]] = {}
            for label, text in record["status"].items():
                fates.setdefault(options[label], set()).add(
                    "elected" if text.startswith("ELECTED") else "eliminated" if text.startswith("DEFEATED") else "lost"
                )
            for option, fate in fates.items():
                for outcome in (
                    ("won", "elected") if "elected" in fate else ("lost",) if "lost" in fate else ("eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        for day, (existing, file, winners) in DECLARED.items():
            contest = cs.key("us.contest", existing)
            election = cs.key("us.election", f"{day}:MA:2511000")
            previous = cs.key("us.alged.result_set", existing)
            if {contest, election, previous} & cs.minted:
                raise ValueError(f"{day}: {existing} must already exist")
            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                     supersedes_id from result_set where id = %s""",
                [previous],
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
                            values,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            stamp = date.fromisoformat(day)
            title = (
                f"{stamp:%B} {stamp.day}, {stamp.year} Cambridge municipal election: City Council, certified "
                "proportional representation count"
            )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=file,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, file), source)
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            chosen = []
            for winner in winners:
                words = spell(winner)
                found = [option for (first, last), option in current.items() if last == words[-1]]
                if len(found) > 1:
                    found = [
                        option for (first, last), option in current.items() if (first, last) == (words[0], words[-1])
                    ]
                if len(found) != 1:
                    raise ValueError(f"{day}: no single option for {winner}")
                chosen.append(found[0])
            if len(set(chosen)) != 9:
                raise ValueError(f"{day}: {winners}")
            result = cs.key(f"{SOURCE}.result_set", f"{day}:declared")
            if result not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=0,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=DECLARED_NOTE)
            for option in chosen:
                for outcome in ("won", "elected"):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
