import html
import re
import subprocess
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.bexar"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Bexar County Elections Department"
TITLE = "Historical election results"
LISTING = "https://elections.bexar.gov/35/Historical"
FILE = "https://elections.bexar.gov/DocumentCenter/View/{}"
FILES = {
    1329: date(2009, 5, 9),
    1364: date(2009, 6, 13),
    1447: date(2011, 5, 14),
    1449: date(2011, 6, 11),
    1476: date(2013, 5, 11),
    1481: date(2013, 6, 15),
    1570: date(2015, 5, 9),
    1566: date(2015, 6, 13),
    1558: date(2017, 5, 6),
    1554: date(2017, 6, 10),
    1437: date(2019, 5, 4),
    1440: date(2019, 6, 8),
    1407: date(2021, 5, 1),
    1406: date(2021, 6, 5),
    1267: date(2023, 5, 6),
    1272: date(2023, 6, 10),
    1226: date(2025, 5, 3),
    1222: date(2025, 6, 7),
}
PLACE = "4865000"
CANDIDATE = re.compile(r"(\d{2}) = (.+?)\s{2,}([\d,]+)(?:\s+(\d*\.\d+))?(?=\s{3,}\d{2} = |\s*$)")
TALLY = re.compile(r"^\s*(.+?)\s{2,}([\d,]+)\s+([\d.]+)%")
OCR = {"Gina ortiz Jones": "Gina Ortiz Jones", "lvalis Meza Gonzalez": "Ivalis Meza Gonzalez"}
METHOD = (
    "City and district totals from the Bexar County Elections Department's reports. A seat no candidate won with a "
    "majority went to a runoff between the top two."
)


def office_of(heading: str) -> str | None:
    text = re.sub(r"\s+", " ", heading.upper().replace("GITY", "CITY"))
    if "CITY OF SAN ANTONIO" not in text or re.search(r"PROP|ISD|SCHOOL|MUD|TRUSTEE|CHARTER|BOND", text):
        return None
    if "MAYOR" in text:
        return "mayor"
    if match := re.search(r"(?:PLACE|DISTRICT)(?: NO\.)? (\d+)", text):
        return f"district-{match[1]}"
    return None


def keyed(text: str) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    office = None
    seen: set[str] = set()
    for line in text.splitlines():
        if line[:1].strip() and not re.match(r"^[\d\s.,]+$", line):
            found = office_of(line)
            office = found if found is not None and found not in seen else None
            if office is not None:
                seen.add(office)
            continue
        if office is None:
            continue
        if line.strip().startswith("---"):
            office = None
            continue
        for _, name, votes, _ in CANDIDATE.findall(line):
            if not re.search(r"OVER VOTES|UNDER VOTES", name):
                rows.append((office, name.strip(), int(votes.replace(",", ""))))
    return rows


def electionware(text: str) -> list[tuple[str, str, int]]:
    totals: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    office = None
    names: list[tuple[str, int, float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("For "):
            office = office_of(stripped)
            names = []
            continue
        if office is None:
            continue
        if stripped.startswith("Total Votes Cast"):
            cast = sum(votes for _, votes, _ in names)
            for name, votes, share in names:
                if cast and abs(100 * votes / cast - share) > 0.02:
                    raise ValueError(f"{office}: {name} {votes} of {cast} is not the printed {share}%")
                if (office, name) not in totals:
                    order.append((office, name))
                totals[(office, name)] = totals.get((office, name), 0) + votes
            office = None
            continue
        if match := TALLY.match(line):
            names.append((OCR.get(match[1].strip(), match[1].strip()), int(match[2].replace(",", "")), float(match[3])))
    return [(office, name, totals[(office, name)]) for office, name in order]


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    for doc in FILES:
        raw.fetch(SOURCE, FILE.format(doc), suffix=".bin")


def parse() -> None:
    rows = []
    for doc, day in FILES.items():
        path = raw.cached(SOURCE, FILE.format(doc)).path
        content = path.read_bytes()
        if content.startswith(b"%PDF"):
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout
            parsed = electionware(text.decode())
        else:
            parsed = keyed(html.unescape(re.sub(r"<[^>]+>", "", content.decode("latin-1"))))
        for office, candidate, votes in parsed:
            rows.append({"day": day, "doc": doc, "office": office, "candidate": candidate, "votes": votes})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.col("office").n_unique().alias("contests"), pl.len()).sort("day"))


def display(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"').replace(" .", ".")).strip()
    if text != text.upper():
        return text
    text = re.sub(r"[A-Z]+", lambda found: found[0] if found[0] in ("II", "III", "IV") else found[0].capitalize(), text)
    return re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), text)


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[\"'“”](\w+)[\"'“”]", " ", plain)
    words = [word for word in re.findall(r"[a-z]+", plain) if word not in ("jr", "sr", "ii", "iii", "iv")]
    return words or [plain]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "San Antonio mayor and City Council 2009-2025 from the Bexar County Elections Department's reports, on "
                "their election days with June runoffs chained to their May first rounds"
            ),
            polity="US-TX",
        )

        def name(entity: UUID, text: str, reason: str = "Renamed to match the Bexar County report") -> None:
            found = conn.execute("select id, text from name where entity_id = %s and is_preferred", [entity]).fetchone()
            if found is not None and found[1] == text:
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason=reason)
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LISTING,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, LISTING), source)
        for doc in FILES:
            cs.artifact(raw.cached(SOURCE, FILE.format(doc)), source)
        system = cs.key("us.electoral_system", "two_round")
        if system in cs.minted:
            raise ValueError("the two-round system must already exist")
        found = conn.execute(
            "select t.polity_id from area_code c join polity_territory t on t.area_id = c.area_id where c.code = %s",
            [PLACE],
        ).fetchone()
        if found is None:
            raise ValueError("no polity for San Antonio")
        polity = found[0]
        current = dict(conn.execute("select entity_id, path from slug where is_current").fetchall())
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/tx/%%'").fetchall()}
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
        people: dict[tuple[str, str], UUID] = {}
        preferred: dict[UUID, str] = {}
        for person, ballot, text in conn.execute(
            """select cy.person_id, coalesce(cy.ballot_name, o.ballot_label), n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where e.polity_id = %s order by e.date_from""",
            [polity],
        ).fetchall():
            words = fold(ballot)
            people[(words[0], words[-1])] = person
            preferred[person] = text
        renamed: set[UUID] = set()
        minted: set[str] = set()
        elections: dict[date, tuple[UUID, str]] = {}
        firsts: dict[tuple[int, str], UUID] = {}
        values: list[tuple[str, str, str, int]] = []
        report: dict[str, int] = {"taken over": 0, "new": 0, "kept alongside": 0, "runoffs chained": 0}
        unmatched: list[str] = []
        differences: list[str] = []

        def place(entity: UUID, path: str) -> str:
            if current.get(entity) == path:
                return path
            base, n = path, 1
            while path in taken:
                n += 1
                path = f"{base}-{n}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=entity)
            current[entity] = path
            return path

        def election(day: date) -> tuple[UUID, str]:
            if day in elections:
                return elections[day]
            kind = "runoff" if day.month == 6 else "local"
            parent = cs.key("us.election", f"{day}:TX")
            if parent in cs.minted:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity("US-TX"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Texas {kind} elections")
                place(parent, f"us/tx/{day}-{kind}")
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = any(%s)",
                [[f"{day:%Y-%m}:TX:{PLACE}", f"{day}:TX:{PLACE}"]],
            ).fetchone()
            entity = found[0] if found else cs.key("us.election", f"{day}:TX:{PLACE}")
            cs.add(
                "election",
                id=entity,
                polity_id=polity,
                parent_id=parent,
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            name(entity, f"{day.year} San Antonio {kind} election")
            elections[day] = (entity, place(entity, f"us/tx/san-antonio/{day}-{kind}"))
            return elections[day]

        def owner(office: str) -> UUID:
            key = "mayor" if office == "mayor" else "council"
            entity = cs.key("us.office" if key == "mayor" else "us.body", f"TX:{PLACE}:{key}")
            if entity in cs.minted and entity not in renamed:
                renamed.add(entity)
                cs.add("office", id=entity, polity_id=polity, kind="other")
                name(entity, "Mayor of San Antonio" if key == "mayor" else "San Antonio City Council")
            return entity

        def title(day: date, office: str, runoff: bool) -> str:
            stage = "runoff" if runoff else "election"
            if office == "mayor":
                return f"{day.year} San Antonio mayoral {stage}"
            return f"{day.year} San Antonio City Council {stage}, District {office.removeprefix('district-')}"

        for (day, office), rows in frame.sort("day", "office").group_by("day", "office", maintain_order=True):
            runoff = day.month == 6
            preliminary = day.year < 2013
            election_id, path = election(day)
            council = office != "mayor"
            number = office.removeprefix("district-")
            key = f"City Council_{number}_1" if council else "Mayor_mayor_1"
            found = conn.execute(
                "select entity_id, value from external_id where scheme = 'us.contest' and value = any(%s)",
                [
                    [
                        f"alged:{PLACE}_{day.year}_{day.month}_san antonio_TX_{key}",
                        f"wikipedia:{day}:TX:{PLACE}:mayor" if not council else "",
                    ]
                ],
            ).fetchone()
            contest_key = found[1] if found else f"bexar:{day}:{office}"
            contest = cs.key("us.contest", contest_key)
            report["taken over" if found else "new"] += 1
            previous = firsts.get((day.year, office)) if runoff else None
            if runoff and previous is not None:
                report["runoffs chained"] += 1
            if not runoff:
                firsts[(day.year, office)] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="body_seats" if council else "office",
                office_id=None if council else owner(office),
                body_id=owner(office) if council else None,
                district_area_id=cs.area("us.local_district", f"48:{PLACE}:council:{office}")
                if council
                else cs.area("us.place", PLACE),
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )
            name(contest, title(day, office, runoff))
            place(contest, f"{path}/{f'council-{office}' if council else 'mayor'}")
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchone()
            kept = preliminary and prior is not None
            if prior is not None and not kept:
                held = dict(zip(columns, prior, strict=True))
                held["election_id"] = election_id
                cs.add("result_set", **held, is_default=False, status="superseded")
            if kept:
                report["kept alongside"] += 1
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="preliminary" if preliminary else "official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=not kept,
                completeness=1,
                supersedes_id=prior[0] if prior is not None and not kept else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            options: list[tuple[list[str], UUID, UUID | None, UUID | None, str]] = []
            for option, label, person, candidacy in conn.execute(
                """select o.id, o.ballot_label, cy.person_id, cy.id from contest_option o
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [contest],
            ).fetchall():
                options.append((fold(label), option, person, candidacy, label))
            before = (
                dict(
                    conn.execute(
                        """select o.ballot_label, sum(v.value)::int from result_value v
                           join result_set r on r.k = v.result_set_k join contest_option o on o.k = v.option_k
                           where r.id = %s group by 1""",
                        [prior[0]],
                    ).fetchall()
                )
                if prior
                else {}
            )
            totals: dict[tuple[str, ...], list] = {}
            for candidate, votes in rows.select("candidate", "votes").iter_rows():
                write_in = bool(re.search(r"write[- ]?in", candidate, re.IGNORECASE))
                group = ("write-in",) if write_in else tuple(fold(display(candidate)))
                entry = totals.setdefault(group, [display(candidate), 0, write_in])
                entry[1] += votes
            ranked: list[tuple[int, UUID]] = []
            chosen: dict[tuple[str, ...], UUID] = {}
            for group, (label, votes, write_in) in sorted(totals.items(), key=lambda item: -item[1][1]):
                if write_in:
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    chosen[group] = option
                    continue
                words = list(group)
                match = next((entry for entry in options if entry[0][-1] == words[-1]), None) or next(
                    (
                        entry
                        for entry in options
                        if entry[0][0] == words[0]
                        and (
                            set(entry[0][1:]) & set(words[1:])
                            or SequenceMatcher(None, entry[0][-1], words[-1]).ratio() >= 0.8
                        )
                    ),
                    None,
                )
                option, person, candidacy = (None, None, None) if match is None else match[1:4]
                if match is not None:
                    options.remove(match)
                    if before and abs(before.get(match[4], 0) - votes) > 0:
                        differences.append(
                            f"{title(day, office, runoff)}: {label} {votes:,} (before {before.get(match[4], 0):,})"
                        )
                person = person or people.get((words[0], words[-1]))
                if person is None:
                    person = cs.key(f"{SOURCE}.person", f"{words[0]}:{words[-1]}")
                    people[(words[0], words[-1])] = person
                    if str(person) not in minted:
                        minted.add(str(person))
                        cs.add("person", id=person)
                        name(person, label)
                elif person not in renamed and set(fold(preferred.get(person, label))) < set(words):
                    renamed.add(person)
                    name(person, label, "The Bexar County report gives the full name")
                option = option or cs.key("us.option", f"{contest_key}:{slug(label)}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=candidacy or cs.key("us.candidacy", f"{contest_key}:{slug(label)}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                )
                chosen[group] = option
                ranked.append((votes, option))
            for _, option, _, _, label in options:
                unmatched.append(f"{title(day, office, runoff)}: {label}")
            total = sum(entry[1] for entry in totals.values())
            decided = runoff or len(ranked) == 1 or ranked[0][0] * 2 > total
            for rank, (_, option) in enumerate(ranked):
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=("elected" if decided else "advanced") if rank < (1 if decided else 2) else "lost",
                )
            unit = (
                cs.unit("us.local_district", f"48:{PLACE}:council:{office}") if council else cs.unit("us.place", PLACE)
            )
            if unit in cs.minted:
                raise ValueError(f"no reporting unit for {office}")
            for group, (_, votes, _) in totals.items():
                values.append((str(result_set), str(unit), str(chosen[group]), votes))
        print(report)
        for line in unmatched:
            print("  option left unmatched:", line)
        for line in differences:
            print("  differs:", line)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema=["result_set_id", "unit_id", "option_id", "value"], orient="row").with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
