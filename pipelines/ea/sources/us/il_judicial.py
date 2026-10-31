import csv
import io
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import leg_canvass
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.il_judicial"
VERSION = "1"
OFFICE = re.compile(r"(\d)(?:ST|ND|RD|TH) (SUPREME|APPELLATE) - (RETAIN )?(.+?)(?: VACANCY)?")
TITLES = {"SUPREME": "Justice of the Supreme Court", "APPELLATE": "Judge of the Appellate Court"}
ORDINALS = ["1st", "2nd", "3rd", "4th", "5th"]
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
TOKEN_2024 = "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Ze1iXEwZay4hPK3eAEa%2f2wcdY8MIeoGR35CWU7WliVnSXlUFjPNH9IqQEvIK0RTUwKYWFeGJqXSz"
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "The judge keeps the seat when Yes reaches three-fifths of the Yes and No votes."
)
NOTE = "County totals from the Illinois State Board of Elections' official vote totals file for the general election."


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def read(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="latin-1")
    delimiter = "\t" if "\t" in text.split("\n", 1)[0] else ","
    table = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    heads = [cell.strip() for cell in table[0]]
    return [dict(zip(heads, [cell.strip() for cell in row], strict=False)) for row in table[1:] if any(row)]


def files() -> dict[int, str]:
    return {**{year: token for year, (token, _) in leg_canvass.IL_FILES.items() if year <= 2014}, 2024: TOKEN_2024}


def fetch() -> None:
    raw.fetch(leg_canvass.SOURCE, leg_canvass.IL_DOWNLOAD.format(TOKEN_2024), suffix=".txt")


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for year, token in files().items():
        day = leg_canvass.day(year)
        for line in read(raw.cached(leg_canvass.SOURCE, leg_canvass.IL_DOWNLOAD.format(token)).path):
            match = OFFICE.fullmatch(re.sub(r"\s+", " ", line["OfficeName"]).strip())
            if match is None:
                continue
            last, first = line["CanLastName"], line["CanFirstName"]
            retention = match[3] is not None
            answer = None
            if retention:
                split = re.fullmatch(r"(.+) - (YES|NO)", last)
                if split is None:
                    raise ValueError(f"{day} {line['OfficeName']}: unexpected retention line {last}")
                last, answer = split[1], split[2].lower()
            found.append(
                {
                    "day": day,
                    "district": int(match[1]),
                    "court": match[2],
                    "retention": retention,
                    "seat": match[4],
                    "candidate": re.sub(r"\s+", " ", f"{first} {last}").strip(),
                    "answer": answer,
                    "party": line.get("PartyAbbrev") or "",
                    "county": line["County"],
                    "votes": int(line["Votes"] or 0),
                }
            )
    return found


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois Supreme Court and Appellate Court general election contests 2000–2014 and 2024 (retention and "
            "vacancy elections) by county, from the State Board of Elections' official vote totals",
            polity="US-IL",
        )
        source = cs.source(leg_canvass.IL_PUBLISHER, leg_canvass.IL_TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=leg_canvass.IL_PUBLISHER,
            title=leg_canvass.IL_TITLE,
            url="https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
            license="Public record",
        )
        for token in files().values():
            cs.artifact(raw.cached(leg_canvass.SOURCE, leg_canvass.IL_DOWNLOAD.format(token)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/il/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")
        retention_system = cs.key("us.electoral_system", "retention")

        def name(entity: UUID, text: str) -> None:
            found = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if found is not None and (found[1] == text or found[2] != SOURCE):
                return
            if found is not None:
                cs.add(
                    "name_drop",
                    name_id=found[0],
                    entity_id=entity,
                    text=found[1],
                    reason="Name restyled from the official all-caps line",
                )
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        names: dict[str, str] = {}
        for row in rows:
            if row["answer"] is None or row["answer"] == "yes":
                first, last = identity(str(row["candidate"]))
                if row["retention"] or first:
                    key = f"IL:{slug(first)}-{slug(last)}"
                    shown = display(str(row["candidate"]).upper())
                    if key not in names or (shown != shown.upper() and len(shown) > len(names[key])):
                        names[key] = shown
        districts: dict[int, UUID] = {}
        for number in range(1, 6):
            code = f"IL:judicial-{number}"
            area = cs.area("us.judicial_district", code)
            if area in cs.minted:
                raise ValueError(f"missing area {code}")
            districts[number] = area
        offices: set[UUID] = set()
        persons: set[str] = set()
        groups: dict[tuple[date, int, str, bool, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            seat = str(row["seat"]) if not row["retention"] else ""
            groups[(row["day"], row["district"], row["court"], row["retention"], seat)].append(row)
        retained_groups: dict[tuple[date, int, str], dict[str, list[dict[str, object]]]] = defaultdict(dict)
        for (day, district, court, retention, seat), group in groups.items():
            if retention:
                by_judge: dict[str, list[dict[str, object]]] = defaultdict(list)
                for row in group:
                    by_judge[str(row["candidate"])].append(row)
                retained_groups[(day, district, court)] = by_judge
        contests: list[tuple[date, int, str, bool, str, str, list[dict[str, object]]]] = []
        for (day, district, court, retention, seat), group in groups.items():
            if not retention:
                contests.append((day, district, court, False, seat, "", group))
        for (day, district, court), by_judge in retained_groups.items():
            for judge, group in by_judge.items():
                contests.append((day, district, court, True, "", judge, group))
        made = 0
        for day, district, court, retention, seat, judge, group in sorted(
            contests, key=lambda item: (item[0], item[2], item[1], item[4], item[5])
        ):
            title = TITLES[court]
            year = day.year
            office_key = f"{slug(title)}:IL"
            office = cs.key("us.office", office_key)
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=cs.polity("US-IL"), kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{title} of Illinois",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            if retention:
                first, last = identity(judge)
                person_key = f"IL:{slug(first)}-{slug(last)}"
                suffix = person_key.split(":")[1]
                label = f"{title} retention: {names[person_key]}"
            else:
                suffix = f"district-{district}-vacancy-of-{slug(seat)}"
                label = f"{title}, {ORDINALS[district - 1]} District ({seat.title()} vacancy)"
            contest_key = f"{day}:IL:{slug(title)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=cs.key("us.election", f"{day}:IL"),
                kind="office",
                office_id=office,
                district_area_id=districts[district],
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=retention_system if retention else fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} Illinois {label}")
            if contest in cs.minted:
                base = f"us/il/{day}-general/{slug(title)}-{suffix}"
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=cs.key("us.election", f"{day}:IL"),
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTE}" if retention else NOTE)
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            parties: dict[str, str] = {}
            for row in group:
                choice = str(row["answer"]) if retention else str(row["candidate"])
                by_choice[choice][str(row["county"])] += int(row["votes"])
                parties[choice] = str(row["party"])
            if retention:
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, names[person_key])
                for position, answer in enumerate(("yes", "no")):
                    options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position + 1,
                        ballot_label=answer.capitalize(),
                    )
                candidacies["yes"] = cs.key("us.candidacy", f"{contest_key}:yes:head")
                cs.add(
                    "candidacy",
                    id=candidacies["yes"],
                    person_id=person,
                    option_id=options["yes"],
                    role="head",
                    ballot_name=names[person_key],
                    is_independent=False,
                )
            else:
                for position, choice in enumerate(by_choice):
                    first, last = identity(choice)
                    person_key = f"IL:{slug(first)}-{slug(last)}"
                    person = cs.key("us.retention.person", person_key)
                    if person_key not in persons:
                        persons.add(person_key)
                        if person in cs.minted:
                            cs.add("person", id=person)
                        name(person, names.get(person_key, display(choice.upper())))
                    party = PARTIES.get(parties[choice])
                    options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                    cs.add(
                        "contest_option",
                        id=options[choice],
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party) if party else None,
                        ballot_position=position + 1,
                        ballot_label=display(choice.upper()),
                    )
                    candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                    cs.add(
                        "candidacy",
                        id=candidacies[choice],
                        person_id=person,
                        option_id=options[choice],
                        role="head",
                        ballot_name=display(choice.upper()),
                        is_independent=parties[choice] == "IND",
                    )
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            if retention:
                yes, no = totals.get("yes", 0), totals.get("no", 0)
                retained = yes >= 0.6 * (yes + no)
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options["yes"],
                    candidacy_id=candidacies["yes"],
                    kind="elected" if retained else "lost",
                    seats=1 if retained else None,
                )
            else:
                winner = max(totals, key=totals.__getitem__)
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[winner],
                    candidacy_id=candidacies[winner],
                    kind="elected",
                    seats=1,
                )
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for choice, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = counties[flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
                    schema={
                        "result_set_id": pl.String,
                        "unit_id": pl.String,
                        "option_id": pl.String,
                        "channel": pl.Int16,
                        "measure": pl.Int16,
                        "count_round": pl.Int16,
                        "flags": pl.Int16,
                        "value": pl.Int64,
                    },
                    orient="row",
                ),
            )
            made += 1
            print(contest_key, totals)
        print(made)
        return cs.write()
