import html
import io
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.chicago_boe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Board of Election Commissioners for the City of Chicago"
TITLE = "Election results by precinct (Excel download)"
LISTING = "https://chicagoelections.gov/elections/results"
PAGE = "https://chicagoelections.gov/elections/results/{election}"
DOWNLOAD = "https://chicagoelections.gov/elections/results/{election}/download?contest={contest}&ward=&precinct="
ELECTIONS = {
    110: date(2003, 2, 25),
    105: date(2003, 4, 1),
    65: date(2007, 2, 27),
    60: date(2007, 4, 17),
    25: date(2011, 2, 22),
    24: date(2011, 4, 5),
    10: date(2015, 2, 24),
    9: date(2015, 4, 7),
    210: date(2019, 2, 26),
    220: date(2019, 4, 2),
    241: date(2023, 2, 28),
    242: date(2023, 4, 4),
}
OFFICES = re.compile(
    r"^(Mayor|Mayoral|City Clerk|Clerk|City Treasurer|Treasurer|Alderman|Alderperson)\b(?!.*\bCounty\b)",
    re.IGNORECASE,
)
PLACE = "1714000"
FIPS = "17031"
METHOD = (
    "Precinct results from the Chicago Board of Election Commissioners' downloadable results. A seat no candidate won "
    "with a majority went to a runoff between the top two."
)


def contests(election: int) -> list[tuple[str, str]]:
    page = raw.fetch(SOURCE, PAGE.format(election=election), suffix=".html").path.read_text(errors="replace")
    block = re.search(r'name="contest".*?</select>', page, re.DOTALL)
    if block is None:
        raise ValueError(f"election {election}: no contest list")
    return [
        (value, html.unescape(label).strip())
        for value, label in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)', block[0])
        if OFFICES.match(html.unescape(label).strip())
    ]


def fetch() -> None:
    for election in ELECTIONS:
        for contest, _ in contests(election):
            raw.fetch(SOURCE, DOWNLOAD.format(election=election, contest=contest), suffix=".xls")


def parse() -> None:
    rows = []
    for election, day in ELECTIONS.items():
        for contest, label in contests(election):
            ward = re.search(r"(\d+)(?:st|nd|rd|th)\s+Ward", label, re.IGNORECASE)
            office = (
                "council"
                if ward
                else "mayor"
                if label.lower().startswith("mayor")
                else "clerk"
                if "clerk" in label.lower()
                else "treasurer"
            )
            url = DOWNLOAD.format(election=election, contest=contest)
            sheet = pl.read_excel(
                io.BytesIO(raw.cached(SOURCE, url).path.read_bytes()), has_header=False, infer_schema_length=0
            ).rows()
            number = None
            header: list[str] = []
            for line in sheet:
                first = (line[0] or "").strip()
                if match := re.fullmatch(r"Ward (\d+)", first):
                    number = int(match[1])
                elif first == "Precinct":
                    header = [(cell or "").strip() for cell in line]
                elif number is not None and first.isdigit() and header:
                    for i, name in enumerate(header):
                        if i < 2 or not name or name == "%":
                            continue
                        if re.search("[ÃÂ]", name):
                            name = name.encode("cp1252").decode("utf-8")
                        rows.append(
                            {
                                "day": day,
                                "runoff": day.month == 4,
                                "office": office,
                                "ward": int(ward[1]) if ward else None,
                                "url": url,
                                "precinct": f"{number:02d}{int(first):03d}",
                                "candidate": name,
                                "write_in": bool(re.search(r"\(WRITE[- ]IN\)", name, re.IGNORECASE)),
                                "votes": int((line[i] or "0").replace(",", "")),
                            }
                        )
    frame = pl.DataFrame(rows, schema_overrides={"ward": pl.Int64})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(f"{frame.height} rows, {frame.select('day', 'office', 'ward').n_unique()} contests")


def display(text: str) -> str:
    text = re.sub(r"\s*\(WRITE[- ]IN\)\s*$", "", text, flags=re.IGNORECASE).replace("''", '"').strip()
    text = re.sub(r"\s+", " ", text)
    if re.sub("Mc", "", text) != re.sub("Mc", "", text).upper():
        return text
    text = re.sub(r"[A-Z]+", lambda found: found[0] if found[0] in ("II", "III", "IV") else found[0].capitalize(), text)
    return re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), text)


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[\"'](\w+)[\"']", " ", plain)
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
                "Chicago mayor, clerk, treasurer, and aldermen 2003-2023 by precinct from the Board of Election "
                "Commissioners, on their election days with runoffs chained to their first rounds"
            ),
            polity="US-IL",
        )
        cs.scope_units("us.precinct", f"%:IL:{FIPS}:CHICAGO:%")

        def name(
            entity: UUID, text: str, reason: str = "Renamed to match the Board of Election Commissioners' returns"
        ) -> None:
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
        for election in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, PAGE.format(election=election)), source)
        for url in sorted(set(frame["url"])):
            cs.artifact(raw.cached(SOURCE, url), source)
        system = cs.key("us.electoral_system", "two_round")
        if system in cs.minted:
            raise ValueError("the two-round system must already exist")
        found = conn.execute(
            """select t.polity_id, u.id from area_code c join polity_territory t on t.area_id = c.area_id
               cross join reporting_unit u where c.code = %s and u.scheme_code = 'us.county' and u.code = %s""",
            [PLACE, FIPS],
        ).fetchone()
        held = conn.execute(
            "select area_id from reporting_unit where id = %s", [found[1] if found else None]
        ).fetchone()
        if found is None or held is None:
            raise ValueError("Chicago's place or Cook County's unit is missing")
        polity, county = found
        county_area = held[0]
        current = dict(conn.execute("select entity_id, path from slug where is_current").fetchall())
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/il/%%'").fetchall()}
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
        elections: dict[date, tuple[UUID, str]] = {}
        firsts: dict[tuple[int, str, int | None], UUID] = {}
        minted: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        report = {"taken over": 0, "new": 0}

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

        for (day, runoff, office, ward), rows in frame.sort("day", "office", "ward").group_by(
            "day", "runoff", "office", "ward", maintain_order=True
        ):
            kind = "runoff" if runoff else "local"
            if day not in elections:
                parent = cs.key("us.election", f"{day}:IL")
                if parent in cs.minted:
                    cs.add(
                        "election",
                        id=parent,
                        polity_id=cs.polity("US-IL"),
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(parent, f"{day.year} Illinois {kind} elections")
                    place(parent, f"us/il/{day}-{kind}")
                found = conn.execute(
                    "select entity_id from external_id where scheme = 'us.election' and value = %s",
                    [f"{day:%Y-%m}:IL:{PLACE}"],
                ).fetchone()
                election = found[0] if found else cs.key("us.election", f"{day}:IL:{PLACE}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} Chicago {kind} election")
                elections[day] = (election, place(election, f"us/il/chicago/{day}-{kind}"))
            election, path = elections[day]
            title = {
                "mayor": "mayoral",
                "clerk": "city clerk",
                "treasurer": "city treasurer",
                "council": "City Council",
            }[office]
            existing = (
                f"alged:{PLACE}_{day.year}_{day.month}_chicago_IL_"
                + ("Mayor_mayor_1" if office == "mayor" else f"City Council_{ward}_1")
                if office in ("mayor", "council")
                else None
            )
            wiki = f"wikipedia:{day}:IL:{PLACE}:mayor" if office == "mayor" else None
            found = conn.execute(
                "select entity_id, value from external_id where scheme = 'us.contest' and value = any(%s)",
                [[value for value in (existing, wiki) if value]],
            ).fetchone()
            contest_key = found[1] if found else f"chicago_boe:{day}:{office}" + (f":{ward}" if ward else "")
            contest = cs.key("us.contest", contest_key)
            report["taken over" if found else "new"] += 1
            owner = cs.key("us.body" if office == "council" else "us.office", f"IL:{PLACE}:{office}")
            if owner in cs.minted and owner not in renamed:
                renamed.add(owner)
                cs.add("office", id=owner, polity_id=polity, kind="other")
                name(owner, f"Chicago City {office.title()}")
            previous = firsts.get((day.year, office, ward)) if runoff else None
            if not runoff:
                firsts[(day.year, office, ward)] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if office == "council" else "office",
                office_id=None if office == "council" else owner,
                body_id=owner if office == "council" else None,
                district_area_id=cs.area("us.local_district", f"17:{PLACE}:council:district-{ward}")
                if office == "council"
                else cs.area("us.place", PLACE),
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )
            name(
                contest,
                f"{day.year} Chicago {title} "
                + ("runoff" if runoff else "election")
                + (f", District {ward}" if ward else ""),
            )
            place(contest, f"{path}/" + (f"council-district-{ward}" if ward else office))
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchone()
            if prior is not None:
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded")
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
                supersedes_id=prior[0] if prior else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            options: list[tuple[list[str], UUID, UUID | None, UUID | None]] = []
            for option, label, person, candidacy in conn.execute(
                """select o.id, o.ballot_label, cy.person_id, cy.id from contest_option o
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [contest],
            ).fetchall():
                options.append((fold(label), option, person, candidacy))
            totals = (
                rows.group_by("candidate", "write_in", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            chosen: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID]] = []
            for candidate, write_in, votes in totals.iter_rows():
                if write_in:
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    if option not in chosen.values():
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                    chosen[candidate] = option
                    continue
                label = display(candidate)
                words = fold(label)
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
                option, person, candidacy = (None, None, None) if match is None else match[1:]
                if match is not None:
                    options.remove(match)
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
                    name(person, label, "The Board of Election Commissioners' returns give the full name")
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
                chosen[candidate] = option
                ranked.append((votes, option))
            report["unmatched options"] = report.get("unmatched options", 0) + len(options)
            total = sum(votes for votes, _ in ranked) + int(totals.filter(pl.col("write_in"))["votes"].sum())
            decided = runoff or ranked[0][0] * 2 > total
            for rank, (_, option) in enumerate(ranked):
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=("elected" if decided else "advanced") if rank < (1 if decided else 2) else "lost",
                )
            for precinct, candidate, votes in rows.select("precinct", "candidate", "votes").iter_rows():
                code = f"{day}:IL:{FIPS}:CHICAGO:{precinct}"
                unit = cs.unit("us.precinct", code)
                if unit in cs.minted and code not in minted:
                    minted.add(code)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.polity("US-IL"),
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=county,
                        area_relation="within",
                        area_id=county_area,
                        kind="standard",
                        valid_from=day,
                        valid_to=date.fromordinal(day.toordinal() + 1),
                    )
                values.append((str(result_set), str(unit), str(chosen[candidate]), votes))
        print(report)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema=["result_set_id", "unit_id", "option_id", "value"], orient="row")
            .group_by("result_set_id", "unit_id", "option_id")
            .agg(pl.col("value").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
