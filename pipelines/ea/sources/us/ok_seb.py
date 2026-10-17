import html
import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ok_seb"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Official results by county"
SITE = "https://oklahoma.gov/"
FILES = {
    "1994-11-08": "content/dam/ok/en/elections/election-results/1994-election-results/1994-general-results.pdf",
    "1996-11-05:president": "elections/elections-results/election-results/1996-election-results/96gpres.html",
    "1996-11-05:senate": "elections/elections-results/election-results/1996-election-results/96gsen.html",
    **{
        f"{day}:{name}": f"content/dam/ok/en/elections/election-results/{day[:4]}-election-results/"
        f"{day[:4]}-general-election-results/{day[2:4]}{name}.pdf"
        for day, names in (
            ("2000-11-07", ["corp"]),
            ("2002-11-05", ["gov", "ltgov", "sai", "ag", "supt", "labor", "ins", "corp"]),
            ("2004-11-02", ["corp"]),
            ("2006-11-07", ["gov", "ltgov", "sai", "ag", "treas", "supt", "labor", "ins", "corp"]),
            ("2008-11-04", ["corp"]),
        )
        for name in names
    },
}
OFFICES = {
    "GOVERNOR": ("governor", None),
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "Lieutenant Governor"),
    "STATE AUDITOR AND INSPECTOR": ("auditor-and-inspector", "Auditor and Inspector"),
    "ATTORNEY GENERAL": ("attorney-general", "Attorney General"),
    "STATE TREASURER": ("treasurer", "Treasurer"),
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": (
        "superintendent-of-public-instruction",
        "Superintendent of Public Instruction",
    ),
    "COMMISSIONER OF LABOR": ("commissioner-of-labor", "Commissioner of Labor"),
    "INSURANCE COMMISSIONER": ("insurance-commissioner", "Insurance Commissioner"),
    "CORPORATION COMMISSIONER": ("corporation-commissioner", "Corporation Commissioner"),
    "UNITED STATES SENATOR": ("senate:special", None),
}
PARTIES = {"D": "democratic-party", "R": "republican-party"}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=Path(path).suffix)


def parse() -> None:
    rows: list[dict[str, object]] = []
    for key, path in FILES.items():
        document = raw.cached(SOURCE, SITE + path).path
        if path.endswith(".pdf"):
            text = subprocess.run(
                ["pdftotext", "-layout", str(document), "-"], capture_output=True, text=True, check=True
            ).stdout
        else:
            found = re.search(r"<pre[^>]*>(.*?)</pre>", document.read_text(errors="replace"), re.DOTALL)
            if found is None:
                raise ValueError(f"{key}: no table")
            text = "\n".join(
                ["UNITED STATES SENATOR" if key.endswith("senate") else "PRESIDENT", html.unescape(found[1])]
            )
        day = key[:10]
        office: str | None = None
        header: list[str] = []
        candidates: list[tuple[str, str | None]] = []
        sums: list[int] = []
        for line in text.splitlines():
            title = re.sub(r" \(FULL TERM\)$", "", line.strip())
            if (title in OFFICES or title == "PRESIDENT") and office is None:
                office = "president" if title == "PRESIDENT" else OFFICES[title][0]
                if day == "1996-11-05" and office == "senate:special":
                    office = "senate"
                header, candidates = [], []
                continue
            if office is None:
                continue
            if title.startswith("="):
                if not candidates:
                    tokens = [re.split(r"\s{2,}", part.strip()) for part in header[-2:]]
                    if day == "1996-11-05":
                        candidates = [(name, None) for name in tokens[-1][1:-1]]
                    else:
                        if len(tokens[0]) != len(tokens[1]):
                            raise ValueError(f"{day} {office}: header {tokens}")
                        for first, last in zip(tokens[0][:-1], tokens[1][:-1], strict=True):
                            matched = re.fullmatch(r"(.+?) \((\w+)\)", last)
                            if matched is None:
                                raise ValueError(f"{day} {office}: {last}")
                            words = f"{first} {matched[1]}".split()
                            candidates.append(
                                (
                                    " ".join(
                                        "Mc" + word[2:].capitalize() if word.startswith("Mc") else word.capitalize()
                                        for word in words
                                    ),
                                    matched[2],
                                )
                            )
                    sums = [0] * len(candidates)
                continue
            matched = re.fullmatch(r"\s*([A-Za-z][A-Za-z .]*?)(?::)?\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*", line)
            if matched is None:
                if title and not title.startswith("General Election"):
                    header.append(line)
                continue
            values = [int(value.replace(",", "")) for value in matched[2].split()]
            if len(values) != len(candidates) + 1:
                raise ValueError(f"{day} {office}: {line}")
            if matched[1] == "STATE TOTAL":
                if values[:-1] != sums or sum(sums) != values[-1]:
                    raise ValueError(f"{day} {office}: columns sum to {sums}, printed {values}")
                office = None
                continue
            if sum(values[:-1]) != values[-1]:
                raise ValueError(f"{day} {office}: {line}")
            for index, ((name, party), votes) in enumerate(zip(candidates, values, strict=False)):
                sums[index] += votes
                rows.append(
                    {
                        "day": day,
                        "office": office,
                        "county": matched[1],
                        "candidate": name,
                        "party": party,
                        "votes": votes,
                    }
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
            summary="Oklahoma's governor, U.S. Senate, and statewide offices by county 1994, 2002, and 2006, "
            "corporation commissioners in 2000, 2004, and 2008, and president and U.S. Senate by county 1996, "
            "from the State Election Board",
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
            url="https://oklahoma.gov/elections/elections-results.html",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
        if len(counties) != 77:
            raise ValueError("Oklahoma needs 77 counties")
        missing = sorted({county for county in frame["county"] if plain(county) not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, int]] = {}
        for person, text, statewide, known, distance in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':OK:[a-z-]+(:special)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      min(abs(left(x.value, 4)::int - 1995))
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest' and x.value ~ '^\\d{4}.*:OK:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, -distance)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':OK:'")
        )
        titles = {office: title for office, title in OFFICES.values()}
        made: set[UUID] = set()
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:OK:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            if key in contests:
                contest = contests[key]
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s", [contest]
                ).fetchall()
            else:
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s",
                    [contests.get(f"{day}:OK:governor") or contests[f"{day}:OK:president"]],
                ).fetchall()
                office_id = cs.key("us.office", f"{office}:OK")
                if office_id in cs.minted:
                    raise ValueError(f"no office {office}")
                contest = cs.key("us.contest", key)
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
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{day[:4]} Oklahoma {titles[office]} election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ok/{day}-general/{office}", entity_id=contest)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
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
                matches = [option for option, _, text in options if surname(text or "") == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                else:
                    if " " not in candidate:
                        raise ValueError(f"{key}: {candidate} is a surname only and matches no option")
                    known = people.get(plain(candidate)) or people.get(
                        f"{plain(candidate.split()[0])}|{surname(candidate)}", set()
                    )
                    person = (
                        max(known, key=lambda one: ranks.get(one, (False, False, -99)))
                        if known
                        else cs.key(f"{SOURCE}.person", f"OK:{slug(candidate)}")
                    )
                    if person in cs.minted and person not in made:
                        made.add(person)
                        people.setdefault(plain(candidate), set()).add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
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
                        is_independent=party not in parties,
                    )
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
            for name, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[plain(name)],
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
