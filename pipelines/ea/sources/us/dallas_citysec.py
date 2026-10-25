import re
import subprocess
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.dallas_citysec"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Dallas, Office of the City Secretary"
TITLE = "History of Mayors and City Councils"
LISTING = "https://dallascityhall.com/government/citysecretary/elections/Pages/historical-data.aspx"
MASTER = "https://citysecretary2.dallascityhall.com/pdf/Elections/ElectMasterList.pdf"
PLACE = "4819000"
FIRST = date(1991, 11, 5)
COUNTY_ONLY = date(2023, 5, 6)
DAY = re.compile(r"(?:\w+day,\s+)?(\w+)\s+(\d{1,2}),\s+(\d{4})")
SEAT = re.compile(
    r"^(?:(Mayor/Place ?(?:No\. ?)?15|Place (?:No\.? ?)?15[/,] ?Mayor|Place (?:No\.? ?)?\d+)\s*:?|(Mayor):)(?=\s|$)"
)
VOTE = re.compile(r"^(.*?\S)\s{2,}([\d,]+)$")
METHOD = (
    "Citywide canvassed totals, across the Dallas, Collin, and Denton county parts of the city, from the City "
    "Secretary's history of mayors and city councils. A seat no candidate won with a majority went to a runoff "
    "between the top two."
)


def when(text: str) -> date | None:
    found = DAY.search(text)
    if found is None:
        return None
    return datetime.strptime(f"{found[1]} {found[2]} {found[3]}", "%B %d %Y").date()


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".html")
    raw.fetch(SOURCE, MASTER, suffix=".pdf")


def parse() -> None:
    path = raw.cached(SOURCE, MASTER).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    rows = []
    day: date | None = None
    for page in text.split("\f"):
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if day is None and not any(line.endswith("Votes Cast") for line in lines):
            continue
        if not any(line.endswith("Votes Cast") or SEAT.match(line) for line in lines):
            continue
        dates = {}
        for line in lines:
            if match := re.match(r"(Election|Run Off Election|Special Election|Special Runoff Election):\s+(.*)", line):
                dates.setdefault(match[1], when(match[2]))
        if dates:
            titles = " ".join(line for line in lines[:4] if not line.startswith(("Election", "Council-Manager")))
            special = "Special" in titles
            runoff = bool(re.search(r"Run ?Off", titles, re.IGNORECASE))
            if special and runoff:
                day = dates.get("Special Runoff Election") or dates.get("Run Off Election")
            elif special:
                day = dates.get("Special Election") or dates.get("Election")
            elif runoff:
                day = dates.get("Run Off Election")
            else:
                day = dates.get("Election")
            kind = (
                "special runoff" if special and runoff else "special" if special else "runoff" if runoff else "general"
            )
        if day is None or day < FIRST or day == COUNTY_ONLY:
            continue
        seat = None
        for line in lines:
            if found := SEAT.match(line):
                label = found[1] or found[2]
                number = re.findall(r"\d+", label)
                seat = "mayor" if "Mayor" in label or number == ["15"] else f"district-{number[0]}"
                line = line[found.end() :].strip()
            elif re.match(r"^[A-Z][\w ]+:", line) or re.fullmatch(r"\d+", line):
                seat = None
                continue
            if seat is None or not line:
                continue
            if vote := VOTE.match(line):
                rows.append(
                    {
                        "day": day,
                        "kind": kind,
                        "office": seat,
                        "candidate": vote[1],
                        "votes": int(vote[2].replace(",", "")),
                    }
                )
    frame = pl.DataFrame(rows).unique(maintain_order=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day", "kind").agg(pl.col("office").n_unique().alias("contests"), pl.len()).sort("day"))


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
                "Dallas mayor and City Council 1991-2025 from the City Secretary's history of mayors and city councils, "
                "citywide canvassed totals on their election days with runoffs chained to their first rounds"
            ),
            polity="US-TX",
        )

        def name(entity: UUID, text: str, reason: str = "Renamed to match the City Secretary's history") -> None:
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
        cs.artifact(raw.cached(SOURCE, MASTER), source)
        system = cs.key("us.electoral_system", "two_round")
        if system in cs.minted:
            raise ValueError("the two-round system must already exist")
        found = conn.execute(
            "select t.polity_id from area_code c join polity_territory t on t.area_id = c.area_id where c.code = %s",
            [PLACE],
        ).fetchone()
        if found is None:
            raise ValueError("no polity for Dallas")
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
        firsts: dict[str, tuple[UUID, date]] = {}
        last: dict[tuple[int, int], date] = {}
        latest: dict[tuple[int, int, str], date] = {}
        for day, office in frame.select("day", "office").unique().iter_rows():
            last[(day.year, day.month)] = max(day, last.get((day.year, day.month), day))
            latest[(day.year, day.month, office)] = max(day, latest.get((day.year, day.month, office), day))
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

        def election(day: date, kind: str) -> tuple[UUID, str]:
            if day in elections:
                return elections[day]
            kind = {"general": "local", "special runoff": "runoff"}.get(kind, kind)
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
            keys = [f"{day}:TX:{PLACE}"] + ([f"{day:%Y-%m}:TX:{PLACE}"] if last[(day.year, day.month)] == day else [])
            found = conn.execute(
                "select entity_id from external_id where scheme = 'us.election' and value = any(%s)", [keys]
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
            name(entity, f"{day.year} Dallas {kind} election")
            elections[day] = (entity, place(entity, f"us/tx/dallas/{day}-{kind}"))
            return elections[day]

        def owner(office: str) -> UUID:
            key = "mayor" if office == "mayor" else "council"
            entity = cs.key("us.office" if key == "mayor" else "us.body", f"TX:{PLACE}:{key}")
            if entity in cs.minted and entity not in renamed:
                renamed.add(entity)
                cs.add("office", id=entity, polity_id=polity, kind="other")
                name(entity, "Mayor of Dallas" if key == "mayor" else "Dallas City Council")
            return entity

        def title(day: date, office: str, runoff: bool, special: bool = False) -> str:
            stage = ("special " if special else "") + ("runoff" if runoff else "election")
            if office == "mayor":
                return f"{day.year} Dallas mayoral {stage}"
            return f"{day.year} Dallas City Council {stage}, District {office.removeprefix('district-')}"

        for (day, kind, office), rows in frame.sort("day", "office").group_by(
            "day", "kind", "office", maintain_order=True
        ):
            runoff = kind.endswith("runoff")
            election_id, path = election(day, kind)
            council = office != "mayor"
            number = office.removeprefix("district-")
            key = f"City Council_{number}_1" if council else "Mayor_mayor_1"
            keys = [f"wikipedia:{day}:TX:{PLACE}:mayor"] if not council else []
            if latest[(day.year, day.month, office)] == day:
                keys.append(f"alged:{PLACE}_{day.year}_{day.month}_dallas_TX_{key}")
            found = conn.execute(
                "select entity_id, value from external_id where scheme = 'us.contest' and value = any(%s)", [keys]
            ).fetchone()
            contest_key = found[1] if found else f"dallas_citysec:{day}:{office}"
            contest = cs.key("us.contest", contest_key)
            report["taken over" if found else "new"] += 1
            first = firsts.get(office)
            previous = first[0] if runoff and first is not None and (day - first[1]).days <= 75 else None
            if runoff and previous is not None:
                report["runoffs chained"] += 1
            if not runoff:
                firsts[office] = (contest, day)
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
                term="remainder" if kind.startswith("special") else "full",
            )
            name(contest, title(day, office, runoff, kind.startswith("special")))
            place(contest, f"{path}/{f'council-{office}' if council else 'mayor'}")
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchone()
            kept = False
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
                stage="certified",
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
                write_in = bool(re.fullmatch(r"write[- ]?in( candidate)?", candidate, re.IGNORECASE))
                candidate = re.sub(r"\s*\(write[- ]?in\)", "", candidate, flags=re.IGNORECASE)
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
                    name(person, label, "The City Secretary's history gives the full name")
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
