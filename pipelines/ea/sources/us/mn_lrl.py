import difflib
import html
import itertools
import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mn_lrl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.lrl.mn.gov/archive/sessions/electionresults/{}-g-sec.pdf"
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Minnesota Election Results, by county"
WRITE_IN = ("Write-in", None)
TABLES = {
    "1982-11-02": [
        (
            "senate",
            2,
            3,
            {
                3: ("Dave Durenberger", "IR"),
                4: ("Mark Dayton", "DFL"),
                5: ("Bill Onasch", "SW"),
                6: ("Jeffrey M. Miller", None),
                7: ("Fred G. Hewitt", "LP"),
            },
        ),
        (
            "governor",
            6,
            7,
            {
                0: ("Wheelock Whitney", "IR"),
                1: ("Rudy Perpich", "DFL"),
                2: ("Kathy Wheeler", "SW"),
                3: ("Tom McDonald", None),
                4: ("Franklin H. Haws", "LP"),
            },
        ),
    ],
    "1986-11-04": [
        (
            "governor",
            2,
            3,
            {
                3: ("Rudy Perpich", "DFL"),
                4: ("Cal R. Ludeman", "IR"),
                5: ("Z. Bill Brust", None),
                6: ("Tom Jaax", "SWP"),
                7: ("Joseph A. Rohner III", "LIB"),
            },
        )
    ],
    "1990-11-06": [
        (
            "senate",
            3,
            4,
            {4: ("Paul David Wellstone", "DFL"), 5: ("Rudy Boschwitz", "IR"), 6: ("Russell B. Bentley", "GRP")},
        ),
        (
            "governor",
            5,
            6,
            {
                0: ("Rudy Perpich", "DFL"),
                1: ("Arne Carlson", "IR"),
                2: ("Wendy Lyons", "SWP"),
                3: ("Ross S. Culverhouse", "GRP"),
                4: ("Heartwarrior Chosa", None),
                5: ("Jon Grunseth", "IR"),
            },
        ),
    ],
    "1992-11-03": [
        (
            "president",
            3,
            4,
            {
                4: ("George Bush", "IR"),
                5: ("Bill Clinton", "DFL"),
                6: ("Jack Herer", "GRP"),
                7: ("James Warren", "SWP"),
                8: ("Ross Perot", None),
            },
        ),
        (
            "president",
            5,
            6,
            {
                0: ("John Hagelin", "NLP"),
                1: ("Lenora Fulani", "NAP"),
                2: ("James Gritz", "CP"),
                3: ("Howard Phillips", "MTP"),
                4: ("Lyndon LaRouche", "IER"),
                5: ("Andre Marrou", "LIB"),
                6: WRITE_IN,
            },
        ),
    ],
    "1994-11-08": [
        (
            "senate",
            3,
            4,
            {
                4: ("Rod Grams", "IR"),
                5: ("Ann Wynia", "DFL"),
                6: ("Dean M. Barkley", "IP"),
                7: ("Marea Himelgrin", "SWP"),
                8: ("Stephen Johnson", "NLP"),
                9: ("Candice E. Sjostrom", "GRP"),
            },
        ),
        (
            "governor",
            5,
            6,
            {
                0: ("Arne Carlson", "IR"),
                1: ("John Marty", "DFL"),
                2: ("Will Shetterly", "GRP"),
                3: ("Jon Hillson", "SWP"),
                4: ("Eric Arthur Olson", "LIB"),
                5: ("Leslie Davis", None),
            },
        ),
    ],
    "1996-11-05": [
        (
            "president",
            3,
            4,
            {4: ("Ross Perot", "RP"), 5: ("Bob Dole", "R"), 6: ("Bill Clinton", "DFL"), 7: ("John Hagelin", "NLP")},
        ),
        (
            "president",
            5,
            6,
            {
                0: ("James Harris", "SWP"),
                1: ("Howard Phillips", "UST"),
                2: ("Harry Browne", "LIB"),
                3: ("Dennis Peron", "GRP"),
                4: ("John Birrenbach", "IG"),
                5: ("Jerome White", "SEP"),
                6: ("Ralph Nader", "GPM"),
                7: WRITE_IN,
            },
        ),
        (
            "senate",
            7,
            8,
            {
                0: ("Dean Barkley", "RP"),
                1: ("Rudy Boschwitz", "R"),
                2: ("Paul D. Wellstone", "DFL"),
                3: ("Tim Davis", "GRP"),
                4: ("Howard B. Hanson", None),
                5: ("Steve Johnson", "NLP"),
                6: ("Thomas A. Fiske", "SWP"),
                7: ("Roy Ezra Carlton", "LIB"),
                8: WRITE_IN,
            },
        ),
    ],
    "1998-11-03": [
        (
            "governor",
            3,
            4,
            {
                4: ("Jesse Ventura", "RP"),
                5: ("Norm Coleman", "R"),
                6: ("Hubert H. Humphrey III", "DFL"),
                7: ("Frank Germann", "LIB"),
                8: ("Chris Wright", None),
            },
        ),
        (
            "governor",
            5,
            6,
            {0: ("Thomas A. Fiske", "SWP"), 1: ("Fancy Ray McCloney", None), 2: ("Ken Pentel", "GPM"), 3: WRITE_IN},
        ),
    ],
}
FIXES = {
    ("1992-11-03", 3, "Mahnomen", 8): 483,
    ("1998-11-03", 3, "Beltrami", 5): 5872,
    ("1998-11-03", 3, "Isanti", 6): 2665,
    ("1998-11-03", 3, "Jackson", 6): 1728,
    ("1998-11-03", 3, "Lake", 6): 2472,
}
MISPRINTS = {
    ("1982-11-02", 6, "Becker", 0): 5158,
    ("1982-11-02", 6, "TOTAL", 0): 715796,
    ("1990-11-06", 5, "Aitkin", 1): 2735,
    ("1998-11-03", 3, "Washington", 5): 32565,
}
SHIFTED = {("1990-11-06", 5): {"McLeod": "Martin", "Mahnomen": "McLeod", "Marshall": "Mahnomen", "Martin": "Marshall"}}
MISPRINTED = "The printed canvass shows {} in {} County; this set uses {:,}, which the statewide total and the other tables support."
NOTES = {
    "1982-11-02:MN:governor": MISPRINTED.format("1,158 votes for Wheelock Whitney", "Becker", 5158)
    + " The printed statewide total, 711,796, carries the same slip.",
    "1990-11-06:MN:governor": MISPRINTED.format("2,753 votes for Arne Carlson", "Aitkin", 2735)
    + " The canvass also prints the governor figures for Martin, McLeod, Mahnomen, and Marshall counties one row "
    "out of place; each row is assigned here to the county whose turnout it fits. Jon Grunseth withdrew as the "
    "Independent-Republican nominee on October 29, 1990, and the votes cast for him are kept as printed.",
    "1998-11-03:MN:governor": MISPRINTED.format("32,564 votes for Norm Coleman", "Washington", 32565),
}
PARTIES = {
    "DFL": "MN:dem-farmer-labor",
    "IR": "MN:independent-republican",
    "R": "republican-party",
    "RP": "reform-party",
    "LIB": "libertarian-party",
    "LP": "libertarian-party",
    "SWP": "socialist-workers-party",
    "SW": "socialist-workers-party",
    "NLP": "natural-law-party",
    "GRP": "MN:grassroots",
}
ZEROS = {"a", "o", "O", "D"}


def fetch() -> None:
    for day in TABLES:
        raw.fetch(SOURCE, URL.format(day), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = sorted(
            (
                text
                for (text,) in conn.execute(
                    """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like '27%'"""
                ).fetchall()
            ),
            key=lambda text: re.sub(r"^st", "saint", re.sub(r"^mc", "mac", re.sub(r"[^a-z]", "", text.lower()))),
        )
    if len(counties) != 87:
        raise ValueError("Minnesota needs 87 counties")

    def plain(text: str) -> str:
        return re.sub(r"[^a-z]", "", text.lower())

    keys = {plain(county): county for county in counties} | {"saintlouis": "St. Louis"}

    def county(text: str) -> str | None:
        if re.fullmatch(r"[a-z]{0,3}tot[a-z]{0,3}", plain(text)):
            return "TOTAL"
        found = difflib.get_close_matches(plain(text), keys, 1, 0.6)
        return keys[found[0]] if found else None

    rows: list[dict[str, object]] = []
    for day, tables in TABLES.items():
        document = raw.cached(SOURCE, URL.format(day)).path
        for office, first, last, columns in tables:
            cells: dict[str, list[str]] = {}
            for number in range(first, last + 1):
                text = subprocess.run(
                    ["pdftotext", "-bbox", "-f", str(number), "-l", str(number), str(document), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                words = [
                    (float(x0), float(y0), float(x1), html.unescape(word))
                    for x0, y0, x1, word in re.findall(
                        r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)<', text
                    )
                ]
                starts = [
                    word
                    for word in words
                    if len(plain(word[3])) >= 3
                    and (county(word[3]) or any(key.startswith(plain(word[3])) for key in keys))
                    and not re.search(r"\d", word[3])
                ]
                margin = min(word[0] for word in starts)
                top = min(word[1] for word in starts if word[0] < margin + 15) - 2
                left = [
                    word for word in words if word[0] < margin + 15 and word[1] > top and re.search("[A-Za-z]", word[3])
                ]
                grown = True
                while grown:
                    grown = False
                    for word in words:
                        if (
                            word not in left
                            and re.fullmatch(r"[A-Za-z.']+", word[3])
                            and any(abs(word[1] - other[1]) < 2 and 0 <= word[0] - other[2] < 6 for other in left)
                        ):
                            left.append(word)
                            grown = True
                lines: list[tuple[float, list[tuple[float, float, float, str]]]] = []
                for word in sorted(left, key=lambda word: word[1]):
                    if lines and abs(word[1] - lines[-1][0]) < 2.5:
                        lines[-1][1].append(word)
                    else:
                        lines.append((word[1], [word]))
                named = [
                    (y, county(" ".join(word[3] for word in group if not re.fullmatch(r"[.·…,]+", word[3]))))
                    for y, group in lines
                ]
                for index in range(1, len(named) - 1):
                    before, after = named[index - 1][1], named[index + 1][1]
                    if (
                        named[index][1] is None
                        and before in counties
                        and after in counties
                        and counties.index(after) - counties.index(before) == 2
                    ):
                        named[index] = (named[index][0], counties[counties.index(before) + 1])
                edge = max(word[2] for y, group in lines for word in group if county(" ".join(w[3] for w in group))) + 2
                named = [(y, found) for y, found in named if found]
                tokens = sorted(
                    (
                        word
                        for word in words
                        if word[0] > edge and word[1] > top and (re.search(r"\d", word[3]) or len(word[3]) <= 3)
                    ),
                    key=lambda word: word[2],
                )
                pending: list[list[tuple[float, float, float, str]]] = []
                for word in tokens:
                    if pending and word[2] - pending[-1][-1][2] < 9:
                        pending[-1].append(word)
                    else:
                        pending.append([word])
                found: list[list[tuple[float, float, float, str]]] = []
                while pending:
                    column = pending.pop(0)
                    heights = sorted(word[1] for word in column)
                    if len(column) > 3 and sum(1 for a, b in itertools.pairwise(heights) if b - a < 2) > 3:
                        across = sorted(column, key=lambda word: word[2])
                        cut = max(range(1, len(across)), key=lambda k: across[k][2] - across[k - 1][2])
                        pending[:0] = [across[:cut], across[cut:]]
                    else:
                        found.append(sorted(column, key=lambda word: word[1]))
                heights = [y for y, _ in named]
                page: dict[str, list[str]] = {name: [] for _, name in named}
                for column in (column for column in found if len(column) > len(named) / 3):
                    shift = column[0][1] - heights[0]
                    placed: dict[str, list[str]] = {}
                    for word in column:
                        index = min(range(len(heights)), key=lambda k: abs(heights[k] + shift - word[1]))
                        if abs(heights[index] + shift - word[1]) <= 3.5:
                            placed.setdefault(named[index][1], []).append(
                                "0" if word[3] in ZEROS else re.sub(r"[.,*·;'\-]", "", word[3])
                            )
                    for name, values in page.items():
                        values.append(" ".join(placed.get(name, ["?"])))
                for name, values in page.items():
                    name = SHIFTED.get((day, first), {}).get(name, name)
                    if name == "TOTAL":
                        cells["TOTAL"] = values
                    else:
                        cells.setdefault(name, []).extend(values)
            missing = [name for name in counties if name not in cells]
            if missing or "TOTAL" not in cells:
                raise ValueError(f"{day} {office} pages {first}–{last}: no row for {missing or 'the total'}")
            if len(cells["TOTAL"]) <= max(columns):
                raise ValueError(f"{day} {office} pages {first}–{last}: the total row has {cells['TOTAL']}")
            for index, (candidate, party) in columns.items():
                total = int(MISPRINTS.get((day, first, "TOTAL", index), cells["TOTAL"][index]))
                values = {
                    name: MISPRINTS.get(
                        (day, first, name, index),
                        FIXES.get((day, first, name, index), cells[name][index] if index < len(cells[name]) else "?"),
                    )
                    for name in counties
                }
                unread = [name for name, value in values.items() if not re.fullmatch(r"\d+", str(value))]
                rest = sum(int(value) for name, value in values.items() if name not in unread)
                if len(unread) == 1 and total - rest >= 0:
                    values[unread[0]] = total - rest
                elif unread or rest != total:
                    raise ValueError(
                        f"{day} {office} pages {first}–{last}, {candidate}: counties sum to {rest} of {total}, "
                        f"unread {[(name, values[name]) for name in unread]}"
                    )
                rows.extend(
                    {
                        "day": day,
                        "office": office,
                        "county": name,
                        "candidate": candidate,
                        "party": party,
                        "votes": int(value),
                    }
                    for name, value in values.items()
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's president, U.S. Senate, and governor by county 1982–1998, "
            "from the Secretary of State's results archived by the Legislative Reference Library",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.lrl.mn.gov/mngov/electionresults",
            license="Public record",
        )
        for day in TABLES:
            cs.artifact(raw.cached(SOURCE, URL.format(day)), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '27%%'"""
            ).fetchall()
        )
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                 and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:MN:[a-z-]+(:special)?$'
               join name n on n.entity_id = cd.person_id and n.is_preferred"""
        ).fetchall():
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & set(parties.values()):
            raise ValueError("the parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MN:'")
        )
        made: set[UUID] = set()
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:MN:{office}"
            contest = contests[key]
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind <> 'others'""",
                [contest],
            ).fetchall()
            columns = (
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
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
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
            for candidate, party, votes in (
                lines.group_by("candidate", maintain_order=True)
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .iter_rows()
            ):
                if candidate == WRITE_IN[0]:
                    generic = [option for option, kind, _ in options if kind == "write_in"]
                    option = generic[0] if len(generic) == 1 else cs.key("us.option", f"{key}:write_in:mn_lrl")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-in"
                        )
                else:
                    matches = [
                        option
                        for option, kind, text in options
                        if kind != "write_in" and surname(text or "") == surname(candidate)
                    ]
                    if len(matches) > 1:
                        raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                    if matches:
                        option = matches[0]
                    else:
                        if office == "president":
                            raise ValueError(f"{key}: {candidate} matches no ticket")
                        known = people.get(plain(candidate)) or people.get(
                            f"{plain(candidate.split()[0])}|{surname(candidate)}", set()
                        )
                        person = (
                            next(iter(known))
                            if len(known) == 1
                            else cs.key(f"{SOURCE}.person", f"MN:{slug(candidate)}")
                        )
                        if person in cs.minted and person not in made:
                            made.add(person)
                            cs.add("person", id=person)
                            cs.add(
                                "name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True
                            )
                        option = cs.key("us.option", f"{key}:{person}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=parties.get(party or ""),
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=party is None,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {candidate}")
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
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
            if key in NOTES:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=NOTES[key])
            for name, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[name],
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
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
