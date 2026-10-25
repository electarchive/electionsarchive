import re
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ri_local"
VERSION = "1"
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Precinct results files"
BASE = "https://www.ri.gov/election/results"
PARSED = DATA / "parsed" / SOURCE
FILES = [
    (2008, "rigen", "general_election", date(2008, 11, 4)),
    (2008, "ripri", "statewide_primary", date(2008, 9, 9)),
    (2010, "rigen", "general_election", date(2010, 11, 2)),
    (2010, "ripri", "statewide_primary", date(2010, 9, 14)),
    (2012, "rigen", "general_election", date(2012, 11, 6)),
    (2012, "ripri", "statewide_primary", date(2012, 9, 11)),
    (2014, "rigen", "general_election", date(2014, 11, 4)),
    (2014, "ripri", "statewide_primary", date(2014, 9, 9)),
    (2016, "rigen", "general_election", date(2016, 11, 8)),
    (2016, "ripri", "statewide_primary", date(2016, 9, 13)),
    (2018, "rigen", "general_election", date(2018, 11, 6)),
    (2018, "ripri", "statewide_primary", date(2018, 9, 12)),
    (2020, "rigen", "general_election", date(2020, 11, 3)),
    (2020, "ripri", "statewide_primary", date(2020, 9, 8)),
    (2021, "rispecial", "special_election", date(2021, 3, 2)),
    (2022, "rigen", "general_election", date(2022, 11, 8)),
    (2022, "ripri", "statewide_primary", date(2022, 9, 13)),
    (2023, "rigen", "general_election", date(2023, 11, 7)),
    (2023, "rispecial", "special_election", date(2023, 9, 5)),
    (2024, "rigen", "general_election", date(2024, 11, 5)),
    (2024, "ripri", "statewide_primary", date(2024, 9, 10)),
]
STATE = re.compile(
    r"(?i)^(?:[A-Z]{3} )?(?:governor|lieutenant governor|secretary of state|attorney general|general treasurer"
    r"|president|us senator)\b(?! of)|general assembly|in congress|district committee|state committee"
    r"|committeeman|committeewoman|(?:city|town|ward) committee|presidential electors|straight party"
    r"|state question|delegate"
)
CITIES = {"central-falls", "cranston", "east-providence", "newport", "pawtucket", "providence", "warwick", "woonsocket"}
PLACE_COUNTIES = {"cranston": "44007", "pawtucket": "44007", "providence": "44007", "warwick": "44003"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "MOD": "RI:moderate-party",
    "COM": "RI:compassion-party",
    "LIB": "libertarian-party",
    "GRN": "green-party",
}
INDEPENDENT = {"IND"}
BODY = re.compile(r"(?i)council|committee|commission|board|authority|\bdistrict\b")
METHOD = (
    "Totals are from the Rhode Island Board of Elections' precinct results files. The files declare no winners, "
    "so winners of general and municipal elections are the candidates with the most votes for the seats on the ballot."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def url(year: int, prefix: str, folder: str) -> str:
    return f"{BASE}/{year}/{folder}/data/{prefix}{year}l.zip"


def fetch() -> None:
    for year, prefix, folder, _ in FILES:
        raw.fetch(SOURCE, url(year, prefix, folder), suffix=".zip")


def member(archive: zipfile.ZipFile, year: int, prefix: str) -> str:
    return next(
        name for name in archive.namelist() if re.fullmatch(rf"{prefix}{year}l\.(asc|txt)", name, re.IGNORECASE)
    )


def title_case(text: str) -> str:
    small = {"of", "and", "the", "for", "to", "a", "in", "on", "at"}
    words = re.sub(r"\s+", " ", text).strip(" -.,").lower().split(" ")
    return " ".join(w if i and w in small else w.capitalize() for i, w in enumerate(words))


def person(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("*", "")).strip()
    return " ".join(
        re.sub(r"^Mc([a-z])", lambda m: "Mc" + m[1].upper(), w.title())
        if w.isupper() and len(w) > 2 and w not in {"JR.", "SR."} and not re.fullmatch(r"[IVX]+\.?", w)
        else w
        for w in text.split(" ")
    )


def parse() -> None:
    with connect() as conn:
        towns = {
            name: path
            for path, name in conn.execute(
                """select s.path, upper(n.text) from polity p join slug s on s.entity_id = p.id
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where s.path ~ '^us/ri/[a-z-]+$' and p.kind = 'municipality'"""
            ).fetchall()
        }
    names = sorted(towns, key=len, reverse=True)
    rows: list[dict[str, Any]] = []
    unmatched: Counter[str] = Counter()
    for year, prefix, folder, day in FILES:
        with zipfile.ZipFile(raw.cached(SOURCE, url(year, prefix, folder)).path) as archive:
            text = archive.read(member(archive, year, prefix)).decode("latin1")
        primary = day.month == 9
        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            line = line.ljust(263)
            if len(line.rstrip()) > 215:
                party, title, candidate, pname, votes = (
                    line[101:104],
                    line[111:167],
                    line[167:205],
                    line[205:235],
                    line[11:17],
                )
                allowed = line[260:263]
            else:
                party, title, candidate, pname, votes = (
                    line[47:50],
                    line[57:113],
                    line[113:151],
                    line[151:205],
                    line[11:17],
                )
                allowed = line[205:209]
            title = re.sub(r"\s+", " ", title).strip()
            if STATE.search(title):
                continue
            pname = re.sub(r"\s+", " ", pname).strip()
            town = next((n for n in names if pname.upper().startswith(n + " ") or pname.upper() == n), None)
            if town is None:
                unmatched[pname.split(" ")[0]] += 1
                continue
            rows.append(
                {
                    "day": day,
                    "number": line[0:4],
                    "title": title,
                    "party": party.strip().upper(),
                    "candidate": candidate.strip(),
                    "town": towns[town],
                    "tail": pname[len(town) :].strip().upper(),
                    "votes": int(votes),
                    "allowed": int(allowed[:2]),
                    "primary": primary,
                }
            )
    print("rows with no town:", dict(unmatched))
    frame = pl.DataFrame(rows)
    spans = frame.group_by("day", "number", "title").agg(pl.col("town").n_unique().alias("span"))
    print("multi-town contests skipped:")
    for row in spans.filter(pl.col("span") > 1).sort("day", "number").iter_rows(named=True):
        print("  ", row["day"], row["number"], row["title"], row["span"])
    frame = frame.join(
        spans.filter(pl.col("span") == 1).select("day", "number", "title"), on=["day", "number", "title"]
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.col("number").n_unique()).sort("day"))
    print(Counter(frame["party"].to_list()).most_common(30))


def cleaned(title: str, town: str) -> dict[str, Any]:
    name = town.split("/")[-1].replace("-", " ")
    text = re.sub(r"^(?:DEM|REP|MOD|IND|NON)\s+", "", title)
    text = re.sub(r"(?i)^n(?=town council)", "", text)
    text = re.sub(r"(?i)^non-?partisan\s+", "", text)
    number = re.match(r"(?i)^(?:question\s+)?(\d+)[.\-\s]+(?=\S)", text)
    text = re.sub(r"(?i)^(?:question\s+)?\d+[.\-\s]+", "", text)
    text = re.sub(rf"(?i)\s*\b(?:(?:town|city) of )?{re.escape(name)}\b", " ", text)
    text = re.sub(r"(?i)\s*\b(?:town|city) of\s*$", " ", text)
    if " " in name:
        text = re.sub(rf"(?i)\s+{re.escape(name.split(' ')[-1])}\s*$", "", text)
    seat = None
    found = re.search(r"(?i)[\s-]*\b(district|ward|zone)\s+(\w[\w-]*)\s*$", text)
    if found:
        seat = f"{found[1].title()} {found[2]}"
        text = text[: found.start()]
    if re.search(r"(?i)\b(at[- ]large|city[- ]?wide)\b", text):
        seat = "At-Large"
        text = re.sub(r"(?i)[\s-]*\b(at[- ]large|city[- ]?wide)\b[\s-]*", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -.,")
    return {"text": text, "seat": seat, "number": number[1] if number else None}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's municipal elections, 2008–2024",
            polity="US-RI",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.ri.gov/election/results/",
                license="Public record",
            )
        for year, prefix, folder, _ in FILES:
            cs.artifact(raw.cached(SOURCE, url(year, prefix, folder)), source)
        state = cs.polity("US-RI")
        polities = {
            path: (polity, text, scheme, code)
            for path, polity, text, scheme, code in conn.execute(
                """select s.path, p.id, n.text, a.scheme_code, c.code from polity p
                   join slug s on s.entity_id = p.id
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join polity_territory t on t.polity_id = p.id join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id
                   where s.path ~ '^us/ri/[a-z-]+$' and p.kind = 'municipality'"""
            ).fetchall()
        }
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '44%%'"
            ).fetchall()
        }
        county_of = {
            path: PLACE_COUNTIES.get(path.split("/")[-1], code[:5]) for path, (_, _, _, code) in polities.items()
        }
        foreign = {
            table: dict(
                conn.execute(
                    f"""select s.path, s.entity_id from slug s join {table} t on t.id = s.entity_id
                        join changeset c on c.id = t.changeset_id
                        where s.path like 'us/ri/%%' and c.pipeline <> %s""",
                    [SOURCE],
                ).fetchall()
            )
            for table in ("election", "contest")
        }
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code ~ '^[0-9]{4}(-[0-9]{2}-[0-9]{2})?:RI:'"
            ).fetchall()
        )
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum", "two_round")}
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the electoral systems and parties must already exist")

        contests: dict[tuple[Any, ...], dict[str, Any]] = {}
        for (day, number, title), rows in frame.group_by("day", "number", "title", maintain_order=True):
            path = rows["town"][0]
            seen = {p for p in rows["party"].to_list() if p in PARTIES}
            primary = bool(rows["primary"][0])
            if primary and len(seen) > 1:
                continue
            labels = {
                re.sub(r"(?i)^question \d+\s*", "", c).strip().lower() for c in rows["candidate"].unique().to_list()
            }
            contests[(day, number, title)] = {
                "day": day,
                "number": number,
                "path": path,
                "scope": next(iter(seen)) if primary and seen else None,
                "question": bool(labels) and labels <= {"approve", "reject", "yes", "no"},
                "allowed": int(rows["allowed"][0]),
                **cleaned(title, path),
            }
        keys: dict[tuple[Any, ...], str] = {}
        for key, info in contests.items():
            code = polities[info["path"]][3]
            office = "question" if info["question"] else slug(info["text"] or "office")
            value = f"{key[0]}:RI:{code}:{office}:{slug(info['seat'] or '')}:{(info['scope'] or '').lower()}" + (
                f":q{info['number']}" if info["question"] else ""
            )
            while value in keys.values():
                value += f":{key[1]}"
            keys[key] = value
        earlier: dict[tuple[Any, ...], str] = {}
        for key, info in contests.items():
            if not info["question"] and not info["scope"] and key[0].month < 11:
                earlier[(info["path"], slug(info["text"]), info["seat"], key[0].year)] = keys[key]
        elections: dict[tuple[date, str], UUID] = {}
        owners: dict[tuple[str, str], UUID] = {}
        persons: dict[UUID, str] = {}
        minted: dict[str, date] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        order = sorted(contests.items(), key=lambda kv: (kv[0][0], kv[1]["path"], keys[kv[0]]))
        for (day, number, title), row in order:
            path = row["path"]
            polity, place, scheme, code = polities[path]
            city = path.split("/")[-1] in CITIES
            question = row["question"]
            rows = frame.filter((pl.col("day") == day) & (pl.col("number") == number) & (pl.col("title") == title))
            if not question and not row["text"]:
                continue
            primary = bool(rows["primary"][0])
            election_kind = "primary" if primary else "general" if day.month == 11 and day.year % 2 == 0 else "local"
            label = {"general": "general", "local": "municipal", "primary": "primary"}[election_kind]
            text = row["text"] or ""
            canonical = (
                "council"
                if re.fullmatch(r"(?i)(?:town |city )?council(?: members?)?", text)
                else "school-committee"
                if re.fullmatch(r"(?i)school (?:committee|board)", text)
                else slug(text)
            )
            heading = (
                f"{'City' if city else 'Town'} Council"
                if canonical == "council"
                else "School Committee"
                if canonical == "school-committee"
                else title_case(text)
            )
            body = (
                not question
                and bool(BODY.search(text))
                and not re.search(r"(?i)\b(?:clerk|treasurer|moderator|mayor)\b", text)
            )
            if question:
                heading = f"Question {row['number']}" if row["number"] else ""
                heading = f"{heading}: {title_case(text)}" if heading and text else heading or title_case(text)
            office = "question" if question else canonical
            if not question and (path, office) not in owners:
                owner = owners[(path, office)] = cs.key("us.body" if body else "us.office", f"RI:{code}:{office}")
                if owner in cs.minted:
                    if body:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add("office", id=owner, polity_id=polity, kind="mayor" if office == "mayor" else "other")
                    name(owner, f"{place} {heading}" if office != "mayor" else f"Mayor of {place}")
            if (day, path) not in elections and f"{path}/{day}-{election_kind}" in foreign["election"]:
                elections[(day, path)] = foreign["election"][f"{path}/{day}-{election_kind}"]
            if (day, path) not in elections:
                election = elections[(day, path)] = cs.key("us.election", f"{day}:RI:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=election_kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} {label} election")
                cs.add("slug", path=f"{path}/{day}-{election_kind}", entity_id=election)
            election = elections[(day, path)]
            whole = area = cs.area(scheme, code)
            unit = cs.unit(scheme, code)
            seat = row["seat"]
            district = not question and seat is not None and seat != "At-Large"
            if district:
                key = f"44:{code}:{office}:{slug(seat)}"
                area = cs.area("us.local_district", key)
                unit = cs.unit("us.local_district", key)
                if key not in districts:
                    districts.add(key)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=key, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{place} {heading}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=key,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
            contest_key = keys[(day, number, title)]
            contest_path = (
                f"{path}/{day}-{election_kind}/"
                + (
                    f"question-{row['number']}"
                    if question and row["number"]
                    else slug(heading)[:70]
                    if question
                    else office
                )
                + (f"-{slug(seat)}" if seat and not question else "")
                + (f"-{row['scope'].lower()}" if row["scope"] else "")
            )
            while contest_path in paths:
                contest_path += "-2"
            paths.add(contest_path)
            if contest_path in foreign["contest"]:
                continue
            contest = cs.key("us.contest", contest_key)
            magnitude = row["allowed"] or 1
            prev = (
                earlier.get((path, slug(text), seat, day.year))
                if not question and not row["scope"] and day.month == 11
                else None
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if question else "body_seats" if body else "office",
                office_id=None if question or body else owners[(path, office)],
                body_id=owners[(path, office)] if body else None,
                district_area_id=area,
                magnitude=None if question else magnitude,
                round_number=2 if prev else 1,
                previous_round_id=cs.key("us.contest", prev) if prev else None,
                party_scope_id=parties.get(row["scope"]),
                electoral_system_id=systems["referendum"]
                if question
                else systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
                term=None if question else "full",
            )
            if question:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(
                contest,
                f"{day.year} {place}: {heading}"
                if question
                else f"{day.year} mayoral election in {place}"
                if office == "mayor"
                else f"{day.year} {place} {heading} election"
                + (f", {seat}" if seat else "")
                + (f" ({row['scope']} primary)" if row["scope"] else ""),
            )
            cs.add("slug", path=contest_path, entity_id=contest)
            if district:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole="municipality"),
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            totals = rows.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum(), pl.col("party").first())
            ranked = 0
            made: dict[str, UUID] = {}
            yes_votes = no_votes = 0
            if question:
                for candidate, votes in totals.select("candidate", "votes").iter_rows():
                    if re.search(r"(?i)approve|yes", candidate):
                        yes_votes += votes
                    else:
                        no_votes += votes
            for position, (candidate, votes, party) in enumerate(
                totals.sort("votes", descending=True).select("candidate", "votes", "party").iter_rows(), start=1
            ):
                cleaned_name = person(candidate)
                writein = re.search(r"(?i)write-?\s*in", candidate) is not None
                if question:
                    answer = "Approve" if re.search(r"(?i)approve|yes", candidate) else "Reject"
                    option = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "Approve" else 2,
                        ballot_label=answer,
                    )
                    made[candidate] = option
                    if answer == "Approve" and yes_votes > no_votes:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=None)
                    continue
                if writein:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    if option not in made.values():
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                else:
                    ranked += 1
                    who = cs.key(f"{SOURCE}.person", f"{code}:{slug(cleaned_name)}")
                    if who not in persons:
                        persons[who] = cleaned_name
                        cs.add("person", id=who)
                        name(who, cleaned_name)
                    option = cs.key("us.option", f"{contest_key}:{who}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=cleaned_name,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{who}:head"),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=cleaned_name,
                        is_independent=party in INDEPENDENT,
                    )
                    if not primary and ranked <= magnitude:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                made[candidate] = option
            cells: dict[tuple[UUID, UUID], int] = {}
            for candidate, tail, votes in rows.select("candidate", "tail", "votes").iter_rows():
                if not tail.isdigit() and votes == 0:
                    continue
                general = day.month == 11 and day.year % 2 == 0
                place_code = (
                    f"{day.year}:RI:{county_of[path]}:{place.upper()}:{tail}"
                    if general
                    else f"{day}:RI:{county_of[path]}:{place.upper()}:{tail}"
                )
                target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                if place_code not in precincts:
                    minted.setdefault(place_code, day)
                if district:
                    memberships.add((target, area, day))
                cell = (target, made[candidate])
                cells[cell] = cells.get(cell, 0) + votes
            for (target, option), votes in cells.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=target,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for place_code, day in minted.items():
            fips = place_code.split(":")[2]
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=state,
                scheme_code="us.precinct",
                code=place_code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target,
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        return cs.write()
