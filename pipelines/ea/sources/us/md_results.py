import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.md_results"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.elections.maryland.gov/elections/archive/"
PUBLISHER = "Maryland State Board of Elections"
TITLE = "Election results archive"
FILES = [
    *(
        f"{year}/results_{year}/{page}.html"
        for year in (1990, 1994, 1998)
        for page in ("gagov", "gacmp", "gaatg", "pagov", "pacmp")
    ),
    *(f"{year}/results_{year}/{page}.html" for year in (1994, 1998) for page in ("gasen", "paatg", "pasen")),
    "1992/results_1992/pregaa.html",
    "1992/results_1992/prepaa.html",
    "1996/results_1996/pregaa.html",
    "2002/results/g_comptroller.html",
    "2002/results/g_attorney_general.html",
    "2006/results/general/office_Comptroller.html",
    "2006/results/general/office_Attorney_General.html",
    *(
        f"{year}/results/general/gen_detail_results_{year}_2_BOT00{page}-.html"
        for year in (2010, 2014)
        for page in (5, 6)
    ),
]
DAYS = {
    (1990, "general"): date(1990, 11, 6),
    (1990, "primary"): date(1990, 9, 11),
    (1992, "general"): date(1992, 11, 3),
    (1992, "primary"): date(1992, 3, 3),
    (1994, "general"): date(1994, 11, 8),
    (1994, "primary"): date(1994, 9, 13),
    (1996, "general"): date(1996, 11, 5),
    (1998, "general"): date(1998, 11, 3),
    (1998, "primary"): date(1998, 9, 15),
    (2002, "general"): date(2002, 11, 5),
    (2006, "general"): date(2006, 11, 7),
    (2010, "general"): date(2010, 11, 2),
    (2014, "general"): date(2014, 11, 4),
}
OFFICES = {
    "President and Vice President of the United States": "president",
    "U.S. Senator": "senate",
    "Governor / Lt. Governor": "governor",
    "Comptroller": "comptroller",
    "Attorney General": "attorney-general",
}
CODES = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Libertarian": "LIB",
    "Natural-Law": "NAT",
    "Reform": "REF",
    "Alliance": "ALL",
    "Taxpayers": "TAX",
    "Independent": "IND",
    "Green": "GRN",
    "Constitution": "CON",
    "Populist": "POP",
    "Other": "OTH",
}


def fetch() -> None:
    for path in FILES:
        raw.fetch(SOURCE, SITE + path, suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()) + ("city" if code == "24510" else ""): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%'"""
            ).fetchall()
        }
    if len(codes) != 24:
        raise ValueError("Maryland needs 24 counties")
    rows: list[dict[str, object]] = []
    for path in FILES:
        year = int(path[:4])
        stage = "primary" if "/pa" in path or "/prep" in path else "general"
        text = raw.cached(SOURCE, SITE + path).path.read_text(errors="replace")
        if year >= 2002:
            office = "comptroller" if re.search("omptroller|005-", path) else "attorney-general"
            header: list[tuple[str, str]] = []
            body: list[list[str]] = []
            for row in re.split(r"<(?:tr|table)[^>]*>", text):
                cells = [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "|", cell)))).strip()
                    for cell in re.findall(r"<t[dh][^>]*>(.*?)(?=<t[dh][\s>]|</?tr[\s>]|</table>|$)", row, re.DOTALL)
                ]
                if len(cells) < 2:
                    continue
                if not cells[0] and not any(re.fullmatch(r"[\d,]+", cell) for cell in cells[1:]):
                    parts = [
                        [
                            part.strip()
                            for part in cell.split("|")
                            if part.strip() and not re.fullmatch(r"\(?\s*Won\s*\)?|N/A", part.strip())
                        ]
                        for cell in cells[1:]
                    ]
                    if not header or body:
                        header, body = [], []
                        for part in parts:
                            name = re.sub(r"\s*\(Won\)$", "", part[0] if part else "").strip()
                            header.append((name, part[1] if len(part) > 1 else ""))
                    else:
                        header = [(name, " ".join(part)) for (name, _), part in zip(header, parts, strict=True)]
                    continue
                if not header:
                    continue
                values = [re.sub(r"\s*\([^)]*\)", "", cell).replace(",", "") for cell in cells[1 : len(header) + 1]]
                if cells[0] != "Totals":
                    body.append([cells[0], *values])
                    continue
                keys = [re.sub(r"[^a-z]", "", line[0].lower()) for line in body]
                if sorted(keys) != sorted(codes):
                    raise ValueError(f"{path}: counties {sorted(set(keys) ^ set(codes))}")
                for column, (name, affiliation) in enumerate(header, 1):
                    counted = [int(line[column]) if line[column].isdigit() else 0 for line in body]
                    if sum(counted) != int(values[column - 1]):
                        raise ValueError(f"{path} {name}: {sum(counted)}, printed {values[column - 1]}")
                    pooled = "Write" in affiliation or "Write-In" in name
                    rows.extend(
                        {
                            "day": DAYS[(year, "general")],
                            "stage": "general",
                            "office": office,
                            "party": None,
                            "code": "OTH" if pooled else CODES.get(affiliation, "OTH"),
                            "candidate": name,
                            "label": f"{name} ({affiliation})" if affiliation else name,
                            "county": codes[key],
                            "votes": value,
                        }
                        for key, value in zip(keys, counted, strict=True)
                    )
                header, body = [], []
            continue
        tables: dict[tuple[str, str | None], list[list[list[str]]]] = {}
        for table in re.findall(r"<table.*?</table>", text, re.DOTALL):
            found = re.search(r"<h2>(.*?)</h2>", table, re.DOTALL)
            heading = re.sub(r"\s+", " ", found[1]).strip() if found else ""
            heading = re.sub(r" Vote For .*", "", heading)
            if heading not in OFFICES or (stage, OFFICES[heading]) == ("primary", "president"):
                continue
            side = re.search(r"<h3>\s*(Democratic|Republican) Candidate", table)
            cells = [
                [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                ]
                for row in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL)
            ]
            cells = [row for row in cells if len(row) > 1 or (row and row[0] not in ("Continued", ""))]
            tables.setdefault((OFFICES[heading], side[1].lower() if side else None), []).append(cells)
        for (office, party), parts in tables.items():
            day = DAYS[(year, stage)]
            used: set[str] = set()
            for cells in parts:
                header = next(row for row in cells if len(row) > 1 and not row[0])
                body = [row for row in cells if len(row) > 1 and row[0] and not row[0].startswith(("Total", "Percent"))]
                (total,) = [row for row in cells if row[0] == "Total"]
                keys = [re.sub(r"[^a-z]", "", row[0].lower()) for row in body]
                if sorted(keys) != sorted(codes):
                    raise ValueError(f"{path} {office}: counties {sorted(set(keys) ^ set(codes))}")
                for column, label in enumerate(header[1:], 1):
                    found = re.fullmatch(r"(.*?)(?: \(([^)]+)\))?", label)
                    assert found is not None
                    names, affiliation = found[1], found[2]
                    if affiliation is not None and affiliation not in CODES:
                        print(f"{path} {office}: {label} has an unknown party")
                    code = CODES.get(affiliation or "")
                    if names.startswith("(Write-in) ") or (stage == "general" and code in used):
                        code = "OTH"
                    used.add(code or "")
                    values = [int(row[column].replace(",", "") or 0) for row in body]
                    if sum(values) != int(total[column].replace(",", "")):
                        raise ValueError(f"{path} {office} {label}: {sum(values)}, printed {total[column]}")
                    rows.extend(
                        {
                            "day": day,
                            "stage": stage,
                            "office": office,
                            "party": party,
                            "code": code,
                            "candidate": names.removeprefix("(Write-in) ").split("/")[0].strip(),
                            "label": label,
                            "county": codes[key],
                            "votes": value,
                        }
                        for key, value in zip(keys, values, strict=True)
                    )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if frame.filter(pl.col("stage") == "primary", pl.col("party").is_null()).height:
        raise ValueError("a primary has no party")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maryland's president, U.S. Senate, governor, comptroller, and attorney general by county 1990–1998, "
            "with their primaries, and its comptroller and attorney general by county 2002–2014, from the State Board "
            "of Elections' archive",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.maryland.gov/elections/archive/index.html",
            license="Public record",
        )
        for path in FILES:
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '24%%'"
            ).fetchall()
        )
        if len(counties) != 24:
            raise ValueError("Maryland needs 24 counties")
        maryland = cs.polity("US-MD")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-MD'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        for person, text, statewide, known, year in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':MD:[a-z-]+(:special|:democratic|:republican)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:MD:|:president$)'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            people.setdefault(f"{plain(text)[:1]}.|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, year)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            "DEM": cs.key("us.party", "democratic-party"),
            "REP": cs.key("us.party", "republican-party"),
            "LIB": cs.key("us.party", "libertarian-party"),
            "NAT": cs.key("us.party", "natural-law-party"),
            "REF": cs.key("us.party", "reform-party"),
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        scopes = {"democratic": parties["DEM"], "republican": parties["REP"]}
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MD:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':MD'")
        )
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        titles = {
            "president": "President",
            "senate": "U.S. Senate",
            "governor": "Governor",
            "comptroller": "Comptroller",
            "attorney-general": "Attorney General",
        }
        made: set[UUID] = set()
        for (day, stage, office, party), lines in frame.sort("day").group_by(
            "day", "stage", "office", "party", maintain_order=True
        ):
            year = day.year
            general = next(
                (
                    value
                    for key, value in sorted(contests.items(), reverse=True)
                    if re.fullmatch(rf"{year}(-1[01]-\d\d)?:MD:{office}", key)
                ),
                None,
            )
            if stage == "general":
                key = f"{day}:MD:{office}"
                election = elections[f"{day}:MD"]
            else:
                key = f"{day}:MD:{office}:{party}"
                found = elections.get(f"{day}:MD:primary")
                if found is None:
                    found = cs.key("us.election", f"{day}:MD:primary")
                    elections[f"{day}:MD:primary"] = found
                    cs.add(
                        "election",
                        id=found,
                        polity_id=maryland,
                        kind="primary",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(found, f"{year} Maryland primary election")
                    cs.add("slug", path=f"us/md/{day}-primary", entity_id=found)
                election = found
            if cs.key(f"{SOURCE}.result_set", key) not in cs.minted:
                continue
            contest = contests.get(key) or (general if stage == "general" else None)
            if contest is None:
                contest = cs.key("us.contest", key)
                contests[key] = contest
                if office == "senate":
                    if general is None:
                        raise ValueError(f"{key}: no general election contest")
                    ((body, seat, area),) = conn.execute(
                        "select body_id, seat_id, district_area_id from contest where id = %s", [general]
                    ).fetchall()
                    office_id = None
                else:
                    body = seat = None
                    area = state
                    office_key = f"{office}:MD"
                    office_id = cs.key("us.office", office_key)
                    if office_key not in offices:
                        offices.add(office_key)
                        cs.add("office", id=office_id, polity_id=maryland, kind="other")
                        name(office_id, f"{titles[office]} of Maryland")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if office == "senate" else "office",
                    office_id=office_id,
                    body_id=body,
                    seat_id=seat,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    party_scope_id=scopes[party] if stage == "primary" else None,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                if stage == "primary":
                    name(
                        contest,
                        f"{year} U.S. Senate {party.title()} primary in Maryland"
                        if office == "senate"
                        else f"{year} Maryland {titles[office]} {party.title()} primary",
                    )
                else:
                    name(contest, f"{year} Maryland {titles[office]} election")
                cs.add("slug", path=f"us/md/{day}-{stage}/{office}" + (f"-{party}" if party else ""), entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
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
            for candidate, code, votes in (
                lines.group_by("candidate", "code", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                if code == "OTH":
                    option = cs.key("us.option", f"{key}:write-in:{slug(candidate)}")
                    if option in cs.minted:
                        cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=candidate)
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
                        first = plain(candidate.split()[0])
                        known = (
                            people.get(plain(candidate))
                            or people.get(f"{first}|{surname(candidate)}")
                            or (people.get(f"{first}.|{surname(candidate)}", set()) if len(first) == 1 else set())
                        )
                        person = (
                            max(
                                known,
                                key=lambda one: (
                                    ranks.get(one, (False, False, []))[0],
                                    ranks.get(one, (False, False, []))[1],
                                    -min(
                                        (abs(other - year) for other in ranks.get(one, (False, False, [year]))[2]),
                                        default=0,
                                    ),
                                ),
                            )
                            if known
                            else cs.key(f"{SOURCE}.person", f"MD:{slug(candidate)}")
                        )
                        if person in cs.minted and person not in made:
                            made.add(person)
                            people.setdefault(plain(candidate), set()).add(person)
                            people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                            cs.add("person", id=person)
                            name(person, candidate)
                        elif person not in made:
                            print(f"{key}: {candidate} is joined to an existing person")
                        option = cs.key("us.option", f"{key}:{person}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=scopes[party] if stage == "primary" else parties.get(code or ""),
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=code == "IND",
                        )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
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
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
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
                if stage == "general":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
                else:
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=None)
        return cs.write()
